"""
===============================================================================
 VEHICLE SIM - physics for the vehicle rig: Simulate & Bake
===============================================================================

 tyFlow-style ground reaction for the vehicle rig, in plain Maya Python:

   * the car is a rigid body with mass, momentum and rotational inertia
   * each wheel casts rays at the ground and pushes back with a spring +
     damper, so the body pitches, rolls, squats and dives for real
   * gravity: drive off a ramp and it flies, lands, compresses, rebounds
   * tyres grip up to a friction limit, so fast turns lean the body out

 The car FOLLOWS the path you already have (a WASD drive pass or hand-keyed
 C_global_CTRL translateX/Z + rotateY): the path decides where the car goes
 on the ground, physics decides everything the ground does to it. In the air
 nobody can steer, so a jump keeps its momentum and rejoins the path after
 landing.

     import vehicle_sim
     vehicle_sim.simulate()            # playback range -> baked keys
     vehicle_sim.clear_simulation()    # back to your original path

 What gets keyed (and restored by clear_simulation):
   C_global_CTRL  translateX/Y/Z, rotateX/Y/Z (rotate order set to zxy)
   C_chassis_CTRL.odometer         wheel spin from the real rolling distance
   {wheel}_suspension_AUTO.translateY   per-wheel suspension travel
 The original path lives on C_drivePath_LOC while a simulation is baked.
 Settings are attributes on C_chassis_CTRL (PHYSICS section).
===============================================================================
"""

import math

import maya.cmds as cmds
import maya.api.OpenMaya as om2

import raycast_ground

GLOBAL = "C_global_CTRL"
CHASSIS = "C_chassis_CTRL"
STEER = "C_steering_CTRL"
BODY_OSC = "C_body_OSC"
PATH_LOC = "C_drivePath_LOC"
WHEELS = ("LF", "RF", "LB", "RB")      # a classic car; see wheels()
STEERING_RATIO = -1.0          # matches vehicle_rig_builder.VehicleWheel
ZXY = 2                        # Maya rotateOrder enum: yaw outermost


def wheels():
    """Every wheel on the vehicle rig in the scene, front to back."""
    import vehicle_rig_builder
    return vehicle_rig_builder.scene_wheel_prefixes() or list(WHEELS)

# (attr, default, min, max, tooltip). Written onto C_chassis_CTRL.
SETTINGS = (
    ("simSuspensionHz", 1.8, 0.3, 6.0,
     "Suspension stiffness as a bounce frequency. ~1 soft offroad, ~2 road "
     "car, 3+ race car."),
    ("simDamping", 0.35, 0.0, 2.0,
     "Suspension damping ratio. Low = bouncy, 1 = settles without "
     "overshoot."),
    ("simGrip", 1.1, 0.05, 3.0,
     "Tyre friction. Low = slides like ice, high = sticks and leans hard."),
    ("simCenterOfMass", 0.6, -1.0, 4.0,
     "Centre of mass height above the hubs, in wheel radii. Higher = more "
     "body roll and dive, top-heavy."),
    ("simPathFollow", 1.0, 0.0, 3.0,
     "How tightly the car sticks to your path while its wheels are on the "
     "ground. 0 = coast freely."),
    ("simSubsteps", 8, 1, 40,
     "Physics steps per frame. Raise for very fast cars or stiff springs."),
    ("simStartDrop", 0.0, 0.0, 10000.0,
     "Drop the car from this height at the first frame."),
    ("simPathSmoothing", 2, 0, 30,
     "Input stabilization: frames of smoothing on the path before the car "
     "follows it (irons out keyboard jitter). 0 = follow every wobble."),
    ("simPhysicsBlend", 1.0, 0.0, 1.0,
     "Manual override, KEYABLE. 1 = full physics, 0 = the car sits exactly "
     "on your keyed pose (including keyed height / tilt). Key it to hand "
     "control between you and the simulation during the shot."),
    ("simKeepHeight", False, None, None,
     "Keep your own translateY keys instead of simulated height."),
    ("simKeepPitch", False, None, None,
     "Keep your own rotateX keys instead of simulated pitch."),
    ("simKeepRoll", False, None, None,
     "Keep your own rotateZ keys instead of simulated roll."),
    ("simKeepPath", False, None, None,
     "Keep your exact path (translateX/Z + rotateY): no sliding wide, no "
     "jump drift. Physics still adds height, pitch, roll, suspension."),
)
_KEYABLE = {"simPhysicsBlend"}


# =============================================================================
# Small vector / quaternion helpers (pure Python: Maya 2023 has no numpy)
# =============================================================================

def _add(a, b):
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _sub(a, b):
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _mul(a, s):
    return (a[0] * s, a[1] * s, a[2] * s)


def _dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a, b):
    return (a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0])


def _len(a):
    return math.sqrt(_dot(a, a))


def _norm(a, fallback=(0.0, 0.0, 1.0)):
    n = _len(a)
    return _mul(a, 1.0 / n) if n > 1e-9 else fallback


def _clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


def _q_mul(a, b):
    aw, ax, ay, az = a
    bw, bx, by, bz = b
    return (aw * bw - ax * bx - ay * by - az * bz,
            aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw)


def _q_norm(q):
    n = math.sqrt(sum(c * c for c in q)) or 1.0
    return tuple(c / n for c in q)


def _q_from_yaw(yaw):
    return (math.cos(yaw * 0.5), 0.0, math.sin(yaw * 0.5), 0.0)


def _q_matrix(q):
    """3x3 rotation matrix, COLUMN-vector convention (v_world = M v)."""
    w, x, y, z = q
    return ((1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)),
            (2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)),
            (2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)))


def _m_apply(m, v):
    return (m[0][0] * v[0] + m[0][1] * v[1] + m[0][2] * v[2],
            m[1][0] * v[0] + m[1][1] * v[1] + m[1][2] * v[2],
            m[2][0] * v[0] + m[2][1] * v[1] + m[2][2] * v[2])


def _m_apply_t(m, v):
    """Transpose (inverse rotation) applied to v."""
    return (m[0][0] * v[0] + m[1][0] * v[1] + m[2][0] * v[2],
            m[0][1] * v[0] + m[1][1] * v[1] + m[2][1] * v[2],
            m[0][2] * v[0] + m[1][2] * v[1] + m[2][2] * v[2])


def _wrap(a):
    return (a + math.pi) % (2.0 * math.pi) - math.pi


# =============================================================================
# Ground: a mesh (ray cast) or a flat plane
# =============================================================================

class Ground(object):
    """Ray queries against the assigned terrain mesh, or a flat plane at
    `flat_y` when there is no mesh."""

    def __init__(self, mesh=None, flat_y=0.0):
        self.flat_y = flat_y
        self.fn = None
        self.accel = None
        if mesh and cmds.objExists(mesh):
            shapes = (cmds.listRelatives(mesh, s=True, ni=True, type="mesh")
                      or ([mesh] if cmds.nodeType(mesh) == "mesh" else []))
            if shapes:
                sel = om2.MSelectionList()
                sel.add(shapes[0])
                self.fn = om2.MFnMesh(sel.getDagPath(0))
                self.accel = self.fn.autoUniformGridParams()

    def ray(self, origin, direction, max_dist):
        """(distance, hit_point, unit_normal) along unit `direction`, or
        None if nothing is hit within max_dist."""
        if self.fn is not None:
            hit = raycast_ground.first_hit(self.fn, origin, direction,
                                           max_dist, self.accel)
            if hit is None:
                return None
            dist, p, face = hit
            n = self.fn.getPolygonNormal(face, om2.MSpace.kWorld)
            normal = _norm((n.x, n.y, n.z), (0.0, 1.0, 0.0))
            if _dot(normal, direction) > 0:        # face points away: flip
                normal = _mul(normal, -1.0)
            return dist, (p.x, p.y, p.z), normal
        if direction[1] > -1e-6:
            return None
        t = (self.flat_y - origin[1]) / direction[1]
        if t < 0 or t > max_dist:
            return None
        return t, _add(origin, _mul(direction, t)), (0.0, 1.0, 0.0)


# =============================================================================
# Rig + settings
# =============================================================================

def has_vehicle():
    return (cmds.objExists(GLOBAL) and cmds.objExists(CHASSIS)
            and bool(wheels()))


def ensure_settings():
    """Add the PHYSICS settings to C_chassis_CTRL (idempotent)."""
    if not cmds.objExists(CHASSIS):
        return
    if not cmds.attributeQuery("physicsHeader", node=CHASSIS, exists=True):
        cmds.addAttr(CHASSIS, ln="physicsHeader", at="enum",
                     en="---PHYSICS---:", k=True)
        cmds.setAttr(CHASSIS + ".physicsHeader", l=True, cb=True, k=False)
    for attr, dv, lo, hi, _tip in SETTINGS:
        if cmds.attributeQuery(attr, node=CHASSIS, exists=True):
            continue
        if isinstance(dv, bool):
            cmds.addAttr(CHASSIS, ln=attr, at="bool", dv=dv)
        else:
            kind = "long" if isinstance(dv, int) else "double"
            cmds.addAttr(CHASSIS, ln=attr, at=kind, dv=dv, min=lo, max=hi)
        if attr in _KEYABLE:
            cmds.setAttr("%s.%s" % (CHASSIS, attr), k=True)
        else:
            cmds.setAttr("%s.%s" % (CHASSIS, attr), cb=True)


def read_settings(overrides=None):
    ensure_settings()
    out = {}
    for attr, dv, _lo, _hi, _tip in SETTINGS:
        out[attr] = (cmds.getAttr("%s.%s" % (CHASSIS, attr))
                     if cmds.objExists(CHASSIS) else dv)
    out.update(overrides or {})
    return out


def _gravity():
    unit = cmds.currentUnit(q=True, linear=True)
    return {"mm": 9810.0, "cm": 981.0, "m": 9.81, "km": 0.00981,
            "in": 386.1, "ft": 32.17, "yd": 10.72}.get(unit, 981.0)


def _plugs_in(plug):
    return cmds.listConnections(plug, s=True, d=False, plugs=True,
                                skipConversionNodes=True) or []


def read_rig(frame):
    """Wheel mount points etc. in the car's own frame, measured at `frame`
    with the rig at rest (no simulation baked)."""
    import vehicle_rig_builder
    cmds.currentTime(frame, edit=True)
    g_m = om2.MMatrix(cmds.xform(GLOBAL, q=True, ws=True, m=True))
    g_t = (g_m[12], g_m[13], g_m[14])
    # The car's own axes (it may be keyed tilted, e.g. a terrain drive).
    rot = tuple(zip(*[_norm((g_m[r], g_m[r + 1], g_m[r + 2]), axis)
                      for r, axis in ((0, (1.0, 0.0, 0.0)),
                                      (4, (0.0, 1.0, 0.0)),
                                      (8, (0.0, 0.0, 1.0)))]))
    scale = cmds.getAttr(GLOBAL + ".globalScale") \
        if cmds.attributeQuery("globalScale", node=GLOBAL, exists=True) \
        else 1.0
    max_comp = cmds.getAttr(CHASSIS + ".maxCompression") * scale
    max_droop = cmds.getAttr(CHASSIS + ".maxDroop") * scale
    wheel_list = []
    for w in wheels():
        pos = cmds.xform("%s_suspension_OFFSET" % w, q=True, ws=True, t=True)
        ratio_node = "%s_steeringRatio_MUL" % w
        steer_scale = (cmds.getAttr(ratio_node + ".input2") / STEERING_RATIO
                       if cmds.objExists(ratio_node) else 0.0)
        radius = vehicle_rig_builder.contact_radius(w) * scale
        wheel_list.append({
            "name": w,
            "mount": _m_apply_t(rot, _sub(tuple(pos), g_t)),
            "radius": radius,
            "steer_scale": steer_scale,
            "max_comp": max_comp,
            "max_droop": max_droop,
        })
    return {"wheels": wheel_list, "scale": scale}


def _sample_path(start, end):
    """Per-frame keyed pose from C_drivePath_LOC plus the steering and the
    physics blend: (x, z, yaw, steer, y, pitch, roll, blend), angles in
    radians."""
    frames = []
    blend_plug = CHASSIS + ".simPhysicsBlend"
    for f in range(start, end + 1):
        steer = (cmds.getAttr(STEER + ".rotateZ", time=f)
                 if cmds.objExists(STEER) else 0.0)
        frames.append((
            cmds.getAttr(PATH_LOC + ".pathTX", time=f),
            cmds.getAttr(PATH_LOC + ".pathTZ", time=f),
            math.radians(cmds.getAttr(PATH_LOC + ".pathRY", time=f)),
            math.radians(steer * STEERING_RATIO),
            cmds.getAttr(PATH_LOC + ".pathTY", time=f),
            math.radians(cmds.getAttr(PATH_LOC + ".pathRX", time=f)),
            math.radians(cmds.getAttr(PATH_LOC + ".pathRZ", time=f)),
            _clamp(cmds.getAttr(blend_plug, time=f), 0.0, 1.0)))
    return frames


def smooth_path(path, radius):
    """Input stabilization: centred moving average over +-radius frames on
    x, z and (unwrapped) yaw. Steering, keyed height / tilt and the blend
    pass through untouched. The ends hold their real values."""
    radius = int(radius)
    if radius <= 0 or len(path) < 3:
        return list(path)
    yaws, prev = [], None
    for p in path:                                # unwrap yaw first
        y = p[2] if prev is None else prev + _wrap(p[2] - prev)
        yaws.append(y)
        prev = y
    out = []
    n = len(path)
    for i, p in enumerate(path):
        r = min(radius, i, n - 1 - i)
        lo, hi = i - r, i + r + 1
        cnt = float(hi - lo)
        sx = sum(path[j][0] for j in range(lo, hi)) / cnt
        sz = sum(path[j][1] for j in range(lo, hi)) / cnt
        sy = sum(yaws[j] for j in range(lo, hi)) / cnt
        out.append((sx, sz, sy) + tuple(p[3:]))
    return out


# =============================================================================
# The simulation (no scene writes: returns per-frame results)
# =============================================================================

def run_simulation(rig, path, ground, settings, fps, gravity=981.0,
                   crash=None):
    """Simulate the car along `path` (one (x, z, yaw, steer) per frame).
    `crash` (a vehicle_crash.SimCrash) makes obstacles stop the car.

    Returns a list, one per frame, of dicts:
        origin (x, y, z)  car pivot = C_global_CTRL position
        matrix            3x3 rotation (column vectors)
        yaw_target        path yaw (radians), for Euler unwrapping
        travel [4]        suspension travel per wheel, world units
        airborne          True if no wheel touches the ground
        speed             forward speed along the car
    """
    wheels = rig["wheels"]
    n_w = len(wheels)
    sub = max(1, int(settings["simSubsteps"]))
    dt = 1.0 / fps
    h = dt / sub
    g = gravity

    radius = sum(w["radius"] for w in wheels) / n_w
    xs = [w["mount"][0] for w in wheels]
    zs = [w["mount"][2] for w in wheels]
    mount_y = sum(w["mount"][1] for w in wheels) / n_w
    com = (sum(xs) / n_w, mount_y + settings["simCenterOfMass"] * radius,
           sum(zs) / n_w)
    # Box inertia per unit mass, scaled up a little for a solid feel.
    width = (max(xs) - min(xs)) + radius
    length = (max(zs) - min(zs)) + 2.0 * radius
    height = 2.2 * radius
    inertia = (1.3 * (height ** 2 + length ** 2) / 12.0,
               1.3 * (width ** 2 + length ** 2) / 12.0,
               1.3 * (width ** 2 + height ** 2) / 12.0)

    omega = 2.0 * math.pi * settings["simSuspensionHz"]
    k = omega * omega / n_w                     # per wheel, unit total mass
    c = 2.0 * settings["simDamping"] * omega / n_w
    preload = g / (omega * omega)               # rest pose = equilibrium
    grip = settings["simGrip"]
    slip_speed = max(1.0, 1.25 * radius)        # sideways speed at full grip
    follow = settings["simPathFollow"]
    kp_pos = (2.5 * follow) ** 2
    kd_pos = 2.0 * 0.9 * 2.5 * follow
    kp_yaw = (6.0 * follow) ** 2
    kd_yaw = 2.0 * 0.8 * 6.0 * follow

    def lerp_i(a, b, i, frac):
        return (a[i] + (b[i] - a[i]) * frac) if len(a) > i else None

    def target_at(fi, frac):
        a = path[min(fi, len(path) - 1)]
        b = path[min(fi + 1, len(path) - 1)]
        pos = (a[0] + (b[0] - a[0]) * frac, a[1] + (b[1] - a[1]) * frac)
        yaw = a[2] + _wrap(b[2] - a[2]) * frac
        steer = a[3] + (b[3] - a[3]) * frac
        # Manual override: keyed height / pitch / roll and the blend.
        hold = (lerp_i(a, b, 4, frac) or 0.0,
                lerp_i(a, b, 5, frac) or 0.0,
                lerp_i(a, b, 6, frac) or 0.0,
                1.0 - (lerp_i(a, b, 7, frac) if len(a) > 7 else 1.0))
        p0 = path[max(fi - 1, 0)]
        p1 = path[min(fi + 1, len(path) - 1)]
        span = (min(fi + 1, len(path) - 1) - max(fi - 1, 0)) * dt or dt
        vel = ((p1[0] - p0[0]) / span, (p1[1] - p0[1]) / span)
        yaw_rate = _wrap(p1[2] - p0[2]) / span
        # Path acceleration (feed-forward), from the second difference.
        pm = path[min(fi, len(path) - 1)]
        if 0 < fi < len(path) - 1:
            acc = ((p1[0] - 2.0 * pm[0] + p0[0]) / (dt * dt),
                   (p1[1] - 2.0 * pm[1] + p0[1]) / (dt * dt))
        else:
            acc = (0.0, 0.0)
        return pos, yaw, steer, vel, yaw_rate, acc, hold

    # ---- initial state: on the ground at the path start, then settle ----
    x0, z0, yaw0 = path[0][0], path[0][1], path[0][2]
    q = _q_from_yaw(yaw0)
    rot = _q_matrix(q)
    top = 1.0e5
    hit = ground.ray((x0, top, z0), (0.0, -1.0, 0.0), 2.0 * top)
    ground_y = hit[1][1] if hit else ground.flat_y
    origin = (x0, ground_y - (mount_y - radius) + settings["simStartDrop"],
              z0)
    p = _add(origin, _m_apply(rot, com))
    v = (0.0, 0.0, 0.0)
    w = (0.0, 0.0, 0.0)
    prev_x = [None] * n_w
    travel = [0.0] * n_w
    crashed = False               # after a real hit the driver lets go

    def step(target, dt_h):
        nonlocal p, v, w, q, rot, crashed
        pos_t, yaw_t, steer_t, vel_t, yaw_rate_t, acc_t, hold_t = target
        up = _m_apply(rot, (0.0, 1.0, 0.0))
        body_fwd = _m_apply(rot, (0.0, 0.0, 1.0))
        origin_w = _sub(p, _m_apply(rot, com))
        force = (0.0, -g, 0.0)
        torque = (0.0, 0.0, 0.0)
        contacts = []
        for i, wh in enumerate(wheels):
            R = wh["radius"]
            mount_w = _add(origin_w, _m_apply(rot, wh["mount"]))
            yaw_w = steer_t * wh["steer_scale"]
            wf = _m_apply(rot, (math.sin(yaw_w), 0.0, math.cos(yaw_w)))
            lift = wh["max_comp"] + 1.5 * R
            best = None
            for frac in (0.7, 0.0, -0.7):
                dx = frac * R
                src = _add(_add(mount_w, _mul(wf, dx)), _mul(up, lift))
                reach = lift + R + wh["max_droop"] + 0.5 * R
                hit = ground.ray(src, _mul(up, -1.0), reach)
                if not hit:
                    continue
                comp = math.sqrt(max(0.0, R * R - dx * dx)) - (hit[0] - lift)
                if best is None or comp > best[0]:
                    best = (comp, hit[1], hit[2])
            if best is None or best[0] <= -wh["max_droop"]:
                travel[i] = -wh["max_droop"]
                prev_x[i] = None
                continue
            comp, cp, normal = best
            comp_rate = 0.0 if prev_x[i] is None else (comp - prev_x[i]) / dt_h
            prev_x[i] = comp
            travel[i] = min(comp, wh["max_comp"])
            fs = k * (comp + preload) + c * comp_rate
            if comp > wh["max_comp"]:                 # bump stop
                fs += 12.0 * k * (comp - wh["max_comp"]) + 2.0 * c * comp_rate
            fs = max(0.0, fs)
            r = _sub(cp, p)
            # Rolling direction: where the path wants this contact to go
            # (so path turns never fight the tyres), else the wheel heading.
            # (0, yaw_rate, 0) x r = (yaw_rate * r.z, 0, -yaw_rate * r.x)
            vel_des = (vel_t[0] + yaw_rate_t * (cp[2] - origin_w[2]), 0.0,
                       vel_t[1] - yaw_rate_t * (cp[0] - origin_w[0]))
            roll_dir = vel_des if _len(vel_des) > 5.0 and not crashed else wf
            fdir = _norm(_sub(roll_dir, _mul(normal, _dot(roll_dir, normal))),
                         _norm(_sub(wf, _mul(normal, _dot(wf, normal)))))
            if _dot(fdir, body_fwd) < 0:
                fdir = _mul(fdir, -1.0)
            sdir = _cross(normal, fdir)
            vc = _add(v, _cross(w, r))
            v_lat = _dot(vc, sdir)
            v_long = _dot(vc, fdir)
            limit = grip * fs
            f_lat = -limit * _clamp(v_lat / slip_speed, -1.0, 1.0)
            # Rolling resistance; a crashed car brakes.
            f_long = -(0.4 if crashed else 0.015) * fs * _clamp(
                v_long / slip_speed, -1.0, 1.0)
            force = _add(force, _mul(up, fs))
            torque = _add(torque, _cross(r, _mul(up, fs)))
            contacts.append([r, fdir, sdir, f_long, f_lat, limit])

        n_c = len(contacts)
        if n_c and follow > 0 and not crashed:
            v_origin = _add(v, _cross(w, _sub(origin_w, p)))
            acc = (acc_t[0] + kp_pos * (pos_t[0] - origin_w[0])
                   + kd_pos * (vel_t[0] - v_origin[0]),
                   0.0,
                   acc_t[1] + kp_pos * (pos_t[1] - origin_w[2])
                   + kd_pos * (vel_t[1] - v_origin[2]))
            cap = 1.5 * grip * g
            mag = _len(acc)
            if mag > cap:
                acc = _mul(acc, cap / mag)
            share = _mul(acc, 1.0 / n_c)
            for ct in contacts:
                ct[3] += _dot(share, ct[1])
                ct[4] += _dot(share, ct[2])
            heading = math.atan2(body_fwd[0], body_fwd[2])
            yaw_acc = (kp_yaw * _wrap(yaw_t - heading)
                       + kd_yaw * (yaw_rate_t - w[1]))
            torque = _add(torque, (0.0, inertia[1] * yaw_acc * n_c / n_w,
                                   0.0))
        for r, fdir, sdir, f_long, f_lat, limit in contacts:
            mag = math.hypot(f_long, f_lat)
            if mag > limit > 0:
                f_long *= limit / mag
                f_lat *= limit / mag
            f = _add(_mul(fdir, f_long), _mul(sdir, f_lat))
            force = _add(force, f)
            torque = _add(torque, _cross(r, f))

        # ---- manual override: pull the body onto the keyed pose ----
        # hold = 1 - physics blend. Works in the air too, so a keyed stunt
        # stays keyed; as the blend rises the car is released into physics
        # from exactly where your keys left it (no pop).
        hold = hold_t[3]
        if hold > 1e-4:
            key_y, key_pitch, key_roll = hold_t[0], hold_t[1], hold_t[2]
            v_origin = _add(v, _cross(w, _sub(origin_w, p)))
            kp, kd = 144.0, 24.0
            force = _add(force, _mul((
                kp * (pos_t[0] - origin_w[0]) + kd * (vel_t[0] - v_origin[0]),
                g + kp * (key_y - origin_w[1]) - kd * v_origin[1],
                kp * (pos_t[1] - origin_w[2]) + kd * (vel_t[1] - v_origin[2])),
                hold))
            q_key = _q_mul(_q_mul(_q_from_yaw(yaw_t),
                                  (math.cos(key_pitch * 0.5),
                                   math.sin(key_pitch * 0.5), 0.0, 0.0)),
                           (math.cos(key_roll * 0.5), 0.0, 0.0,
                            math.sin(key_roll * 0.5)))
            q_err = _q_mul(q_key, (q[0], -q[1], -q[2], -q[3]))
            if q_err[0] < 0:
                q_err = tuple(-c for c in q_err)
            err = (2.0 * q_err[1], 2.0 * q_err[2], 2.0 * q_err[3])
            i_avg = sum(inertia) / 3.0
            torque = _add(torque, _mul(_sub(_mul(err, kp), _mul(w, kd)),
                                       hold * i_avg))

        # ---- integrate (semi-implicit Euler) ----
        force = _add(force, _mul(v, -0.02))
        v = _add(v, _mul(force, dt_h))
        local_t = _m_apply_t(rot, torque)
        local_a = (local_t[0] / inertia[0], local_t[1] / inertia[1],
                   local_t[2] / inertia[2])
        w = _add(w, _mul(_m_apply(rot, local_a), dt_h))
        w = _mul(w, max(0.0, 1.0 - 0.4 * dt_h))
        push = (0.0, 0.0, 0.0)
        if crash is not None:
            # Obstacles: the crumple zone soaks up the hit (vehicle_crash).
            v, w, push, hit = crash.resolve(p, v, w, rot, com, inertia, dt_h)
            if crash.stops_path and hit > crash.stop_speed:
                crashed = True
        p = _add(_add(p, _mul(v, dt_h)), push)
        q = _q_norm(_add4(q, _mul4(_q_mul((0.0,) + w, q), 0.5 * dt_h)))
        rot = _q_matrix(q)
        return n_c

    # Settle on the springs before the shot starts (path frozen at frame 0).
    rest = target_at(0, 0.0)
    rest = (rest[0], rest[1], rest[2], (0.0, 0.0), 0.0, (0.0, 0.0), rest[6])
    if crash is not None:
        crash.set_time(0, 0.0)            # animated obstacles: first frame
    for _ in range(int(0.6 * fps) * sub if not settings["simStartDrop"]
                   else 0):
        step(rest, h)
    if not settings["simStartDrop"]:
        t0 = target_at(0, 0.0)
        v = (t0[3][0], 0.0, t0[3][1])

    out = []
    for fi in range(len(path)):
        grounded = 0
        if fi > 0:
            for s in range(sub):
                if crash is not None:
                    crash.set_time(fi - 1, (s + 1) / float(sub))
                grounded = max(grounded,
                               step(target_at(fi - 1, (s + 1) / float(sub)),
                                    h))
        else:
            grounded = sum(1 for x in prev_x if x is not None)
            if settings["simStartDrop"]:
                grounded = 0
        origin_w = _sub(p, _m_apply(rot, com))
        fwd = _m_apply(rot, (0.0, 0.0, 1.0))
        out.append({
            "origin": origin_w, "matrix": rot, "yaw_target": path[fi][2],
            "travel": list(travel), "airborne": grounded == 0,
            "speed": _dot(v, fwd),
        })
    return out


def _add4(a, b):
    return tuple(x + y for x, y in zip(a, b))


def _mul4(a, s):
    return tuple(x * s for x in a)


# =============================================================================
# Capture / bake / clear
# =============================================================================

_PATH_ATTRS = (("pathTX", "translateX"), ("pathTZ", "translateZ"),
               ("pathRY", "rotateY"), ("pathTY", "translateY"),
               ("pathRX", "rotateX"), ("pathRZ", "rotateZ"))
_BODY_CHANS = ("rotateX", "rotateZ", "translateY")


def is_baked():
    return (cmds.objExists(PATH_LOC) and cmds.attributeQuery(
        "simBaked", node=PATH_LOC, exists=True)
        and cmds.getAttr(PATH_LOC + ".simBaked"))


def _copy_channel(src, dst):
    """Copy animation (or the static value) from plug src to plug dst."""
    s_node, s_attr = src.split(".", 1)
    d_node, d_attr = dst.split(".", 1)
    if cmds.keyframe(src, q=True, keyframeCount=True):
        cmds.cutKey(dst, clear=True)
        cmds.copyKey(s_node, at=s_attr)
        cmds.pasteKey(d_node, at=d_attr, option="replaceCompletely")
    else:
        if cmds.keyframe(dst, q=True, keyframeCount=True):
            cmds.cutKey(dst, clear=True)
        cmds.setAttr(dst, cmds.getAttr(src))


def _ensure_path_loc():
    if not cmds.objExists(PATH_LOC):
        loc = cmds.spaceLocator(n=PATH_LOC)[0]
        top = "VEHICLE_RIG_GRP"
        if cmds.objExists(top):
            cmds.parent(loc, top)
        cmds.setAttr(loc + ".visibility", 0)
        for attr, _ in _PATH_ATTRS:
            cmds.addAttr(loc, ln=attr, at="double", k=True)
        cmds.addAttr(loc, ln="pathOdometer", at="double", k=True)
        for chan in _BODY_CHANS:
            cmds.addAttr(loc, ln="osc_" + chan, at="double", k=True)
        cmds.addAttr(loc, ln="savedRotateOrder", at="long", dv=0)
        cmds.addAttr(loc, ln="savedBodyMotion", at="double", dv=1)
        cmds.addAttr(loc, ln="simBaked", at="bool", dv=False)
    return PATH_LOC


def capture_path():
    """Store the car's current path (keys or static values) on
    C_drivePath_LOC. Skipped while a simulation is baked, so re-simulating
    always starts from your original path."""
    _ensure_path_loc()
    if is_baked():
        return False
    for attr, chan in _PATH_ATTRS:
        _copy_channel("%s.%s" % (GLOBAL, chan), "%s.%s" % (PATH_LOC, attr))
    _copy_channel(CHASSIS + ".odometer", PATH_LOC + ".pathOdometer")
    if cmds.objExists(BODY_OSC):
        for chan in _BODY_CHANS:
            _copy_channel("%s.%s" % (BODY_OSC, chan),
                          "%s.osc_%s" % (PATH_LOC, chan))
    cmds.setAttr(PATH_LOC + ".savedRotateOrder",
                 cmds.getAttr(GLOBAL + ".rotateOrder"))
    if cmds.attributeQuery("autoBodyMotion", node=CHASSIS, exists=True):
        cmds.setAttr(PATH_LOC + ".savedBodyMotion",
                     cmds.getAttr(CHASSIS + ".autoBodyMotion"))
    return True


def _set_channel_keys(plug, frames, values):
    cmds.cutKey(plug, clear=True)
    for f, val in zip(frames, values):
        cmds.setKeyframe(plug, t=f, v=val)


def _euler_zxy(matrix, prev=None, yaw_hint=None):
    m = matrix
    mm = om2.MMatrix((m[0][0], m[1][0], m[2][0], 0.0,
                      m[0][1], m[1][1], m[2][1], 0.0,
                      m[0][2], m[1][2], m[2][2], 0.0,
                      0.0, 0.0, 0.0, 1.0))
    e = om2.MTransformationMatrix(mm).rotation().reorder(
        om2.MEulerRotation.kZXY)
    if prev is not None:
        e = e.closestSolution(prev)
    elif yaw_hint is not None:
        turns = round((yaw_hint - e.y) / (2.0 * math.pi))
        e = om2.MEulerRotation(e.x, e.y + turns * 2.0 * math.pi, e.z,
                               om2.MEulerRotation.kZXY)
    return e


def simulate(start=None, end=None, settings=None, fps=None):
    """Simulate the vehicle over [start, end] (default: playback range) and
    bake the result to keys. Re-running starts again from the original
    path. Returns a summary dict."""
    if not has_vehicle():
        raise RuntimeError("No vehicle rig in the scene. Build one first.")
    import vehicle_rig_builder
    start = int(cmds.playbackOptions(q=True, min=True)) if start is None \
        else int(start)
    end = int(cmds.playbackOptions(q=True, max=True)) if end is None \
        else int(end)
    if end <= start:
        raise ValueError("Simulation range must be at least 2 frames.")
    if fps is None:
        fps = om2.MTime(1.0, om2.MTime.kSeconds).asUnits(
            om2.MTime.uiUnit())
    if is_baked():
        clear_simulation()
    settings = read_settings(settings)
    capture_path()

    # Rest state for measuring: rotate order + no body-motion network tilt.
    cmds.setAttr(GLOBAL + ".rotateOrder", ZXY)
    rig = read_rig(start)
    mesh = vehicle_rig_builder.assigned_ground_mesh()
    flat_y = (cmds.xform("C_ground_LOC", q=True, ws=True, t=True)[1]
              if cmds.objExists("C_ground_LOC") else 0.0)
    ground = Ground(mesh, flat_y)
    raw = _sample_path(start, end)
    path = smooth_path(raw, settings["simPathSmoothing"])
    frames = list(range(start, end + 1))
    import vehicle_crash
    vehicle_crash.clear_damage()
    crash = None
    if vehicle_crash.active():
        # Walls stop the car; with crashGround the body lands on the
        # ground too (rollovers, bottoming out) instead of sinking in.
        cmds.currentTime(start, edit=True)
        crash = vehicle_crash.SimCrash(
            ground=((mesh, flat_y) if vehicle_crash.ground_on() else None),
            start=start, end=end, fps=fps)
    result = run_simulation(rig, path, ground, settings, fps, _gravity(),
                            crash=crash)

    # ---- bake: physics, blended with your keys where you asked ----
    cols = {c: [] for c in ("tx", "ty", "tz", "rx", "ry", "rz")}
    prev = None
    keep_path = settings["simKeepPath"]
    for r, key in zip(result, raw):
        e = _euler_zxy(r["matrix"], prev, r["yaw_target"])
        prev = e
        sim = {"tx": r["origin"][0], "ty": r["origin"][1],
               "tz": r["origin"][2], "rx": math.degrees(e.x),
               "ry": math.degrees(e.y), "rz": math.degrees(e.z)}
        key_ry = math.degrees(key[2])
        key_ry += 360.0 * round((sim["ry"] - key_ry) / 360.0)   # same turn
        keyed = {"tx": key[0], "ty": key[4], "tz": key[1],
                 "rx": math.degrees(key[5]), "ry": key_ry,
                 "rz": math.degrees(key[6])}
        hold = 1.0 - key[7]
        for c in cols:
            forced = ((c == "ty" and settings["simKeepHeight"])
                      or (c == "rx" and settings["simKeepPitch"])
                      or (c == "rz" and settings["simKeepRoll"])
                      or (c in ("tx", "tz", "ry") and keep_path))
            w = 1.0 if forced else hold
            cols[c].append(sim[c] + (keyed[c] - sim[c]) * w)
    for key, chan in (("tx", "translateX"), ("ty", "translateY"),
                      ("tz", "translateZ"), ("rx", "rotateX"),
                      ("ry", "rotateY"), ("rz", "rotateZ")):
        _set_channel_keys("%s.%s" % (GLOBAL, chan), frames, cols[key])

    odo0 = cmds.getAttr(PATH_LOC + ".pathOdometer", time=start)
    odo, dist = [], odo0
    for i, r in enumerate(result):
        if i:
            dist += r["speed"] / fps / rig["scale"]
        odo.append(dist)
    _set_channel_keys(CHASSIS + ".odometer", frames, odo)

    scale = rig["scale"]
    for i, wname in enumerate(wheels()):
        plug = "%s_suspension_AUTO.translateY" % wname
        for src in _plugs_in(plug):
            cmds.disconnectAttr(src, plug)
        _set_channel_keys(plug, frames,
                          [r["travel"][i] / scale for r in result])

    if cmds.attributeQuery("autoBodyMotion", node=CHASSIS, exists=True):
        cmds.setAttr(CHASSIS + ".autoBodyMotion", 0)
    if cmds.objExists(BODY_OSC):
        for chan in _BODY_CHANS:
            plug = "%s.%s" % (BODY_OSC, chan)
            cmds.cutKey(plug, clear=True)
            cmds.setAttr(plug, 0)
    cmds.setAttr(PATH_LOC + ".simBaked", True)
    cmds.currentTime(start, edit=True)
    import vehicle_trailers
    if vehicle_trailers.has_trailers():
        vehicle_trailers.bake_trailers(start, end)     # follow the new motion

    airborne = sum(1 for r in result if r["airborne"])
    summary = {"frames": len(result), "airborne_frames": airborne,
               "max_height": max(cols["ty"]) - min(cols["ty"]),
               "ground": mesh or "flat", "impacts": 0, "dents": 0}
    if crash is not None:
        summary["impacts"] = len(crash.impacts)
        summary["dents"] = vehicle_crash.bake_damage()["dents"]
    import vehicle_cargo
    summary["cargo"] = (len(vehicle_cargo.bake_cargo(start, end))
                        if vehicle_cargo.list_cargo() else 0)
    print("[vehicle_sim] Baked %d frames (%d airborne) on %s ground."
          % (len(result), airborne, summary["ground"]))
    return summary


def clear_simulation():
    """Remove a baked simulation and put the original path back. Crash
    damage belonged to the simulated motion, so it goes too (bake it again
    with vehicle_crash.bake_damage for the original path)."""
    if not is_baked():
        return False
    import vehicle_crash
    import vehicle_cargo
    vehicle_crash.clear_damage()
    vehicle_cargo.clear_bake()
    for attr, chan in _PATH_ATTRS:
        _copy_channel("%s.%s" % (PATH_LOC, attr), "%s.%s" % (GLOBAL, chan))
    _copy_channel(PATH_LOC + ".pathOdometer", CHASSIS + ".odometer")
    cmds.setAttr(GLOBAL + ".rotateOrder",
                 cmds.getAttr(PATH_LOC + ".savedRotateOrder"))
    for wname in wheels():
        plug = "%s_suspension_AUTO.translateY" % wname
        cmds.cutKey(plug, clear=True)
        gate = "%s_suspGate_MDL.output" % wname
        if cmds.objExists(gate.split(".")[0]) and not _plugs_in(plug):
            cmds.connectAttr(gate, plug, f=True)
        elif not _plugs_in(plug):
            cmds.setAttr(plug, 0)
    if cmds.attributeQuery("autoBodyMotion", node=CHASSIS, exists=True):
        cmds.setAttr(CHASSIS + ".autoBodyMotion",
                     cmds.getAttr(PATH_LOC + ".savedBodyMotion"))
    if cmds.objExists(BODY_OSC):
        for chan in _BODY_CHANS:
            _copy_channel("%s.osc_%s" % (PATH_LOC, chan),
                          "%s.%s" % (BODY_OSC, chan))
    cmds.setAttr(PATH_LOC + ".simBaked", False)
    import vehicle_trailers
    vehicle_trailers.rebake()             # trailers follow the original path
    print("[vehicle_sim] Simulation cleared, original path restored.")
    return True
