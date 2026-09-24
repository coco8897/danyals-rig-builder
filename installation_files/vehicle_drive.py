"""
===============================================================================
 VEHICLE DRIVE — live WASD driving for the vehicle rig
===============================================================================

 Drive the car around the viewport with the keyboard, like a game. Every
 frame is keyframed, so when you stop you have the animation — wheels
 spinning, front wheels steering, and (if a ground mesh is assigned)
 tires deforming against the surface, all baked in.

   W / S   — accelerate / brake + reverse
   A / D   — steer left / right
   Esc     — stop driving

 Usage:
     import vehicle_drive
     from importlib import reload; reload(vehicle_drive)
     vehicle_drive.show()          # opens the Drive panel; click it, then WASD

 How it works:
   * C_global_CTRL carries the car's WORLD position (translateX/Z) + heading
     (rotateY).
   * C_chassis_CTRL.odometer accumulates distance driven → drives wheel spin
     (so wheels roll by how far you've travelled, even around curves).
   * C_steering_CTRL.rotateZ is set from the steer angle → front wheels turn.
   * Each tick sets a keyframe on those channels and advances the timeline.

 NOTE: Maya only sends keys to the focused window, so the little Drive panel
 must stay focused while you drive (click it once if WASD stops responding).
===============================================================================
"""

import contextlib
import math
import maya.cmds as cmds
import maya.api.OpenMaya as om2
import maya.OpenMayaUI as omui
try:
    from PySide2 import QtCore, QtWidgets
    from shiboken2 import wrapInstance
except ImportError:                                  # Maya 2025+ (Qt6)
    from PySide6 import QtCore, QtWidgets
    from shiboken6 import wrapInstance

import vehicle_rig_builder
import raycast_ground
import vehicle_sim
import vehicle_trailers
import vehicle_crash
import vehicle_cargo
from importlib import reload as _reload
_reload(raycast_ground)
_reload(vehicle_sim)
_reload(vehicle_trailers)
_reload(vehicle_crash)
_reload(vehicle_cargo)


@contextlib.contextmanager
def _undo_chunk():
    cmds.undoInfo(openChunk=True)
    try:
        yield
    finally:
        cmds.undoInfo(closeChunk=True)


WINDOW_OBJECT_NAME = "DanyalVehicleDriveWindow"

DRIVE_ROOT   = "C_global_CTRL"
ODO_ATTR     = "C_chassis_CTRL.odometer"
STEER_CTRL   = "C_steering_CTRL"
LEAN_ATTR    = "C_chassis_CTRL.lean"   # motorcycles only
SLIP_ATTR    = "C_chassis_CTRL.tyreSlip"   # 0 = gripping, 1 = let go
BODY_AUTO    = "C_body_AUTO"   # instant terrain tilt (node-driven)
BODY_OSC     = "C_body_OSC"    # drive-loop spring-damper LAG offset
# The three body channels we oscillate: (auto attr, osc attr).
_BODY_CHANS = ("rotateX", "rotateZ", "translateY")


def has_body_osc():
    return cmds.objExists(BODY_OSC) and cmds.objExists(BODY_AUTO)


def ensure_slip_attr():
    """Give the rig somewhere to record how much the tyres are sliding, so
    the tyre-track baker knows where to put marks. Added on demand, so
    rigs built before this still get it."""
    if not cmds.objExists("C_chassis_CTRL"):
        return False
    if not cmds.attributeQuery("tyreSlip", node="C_chassis_CTRL",
                               exists=True):
        cmds.addAttr("C_chassis_CTRL", ln="tyreSlip", at="double",
                     min=0, max=1, dv=0, k=True)
    return True


def slip_amount(state, params=None):
    """How much the tyres are sliding this tick, 0 to 1. Grip lost to the
    handbrake counts, and so does the car travelling sideways."""
    p = dict(DEFAULT_PARAMS, **(params or {}))
    grip = state.get("grip", 1.0)
    speed = abs(state.get("speed", 0.0))
    lat = abs(state.get("v_lat", 0.0))
    sideways = lat / max(speed, lat, 1.0)
    return _clamp(max(1.0 - grip, sideways), 0.0, 1.0)


def has_lean():
    """True if the rig is a bike: it has a lean to drive."""
    return (cmds.objExists("C_chassis_CTRL")
            and cmds.attributeQuery("lean", node="C_chassis_CTRL",
                                    exists=True))


# Default handling parameters (Maya units, SCALE=10 → 1 unit ≈ 1 cm).
DEFAULT_PARAMS = {
    "accel":       260.0,   # units/sec^2 throttle
    "brake":       420.0,   # units/sec^2 brake / reverse push
    "friction":    160.0,   # units/sec^2 coast-down decay
    "max_speed":   900.0,   # units/sec forward cap
    "max_reverse": 320.0,   # units/sec reverse cap
    "max_steer":    35.0,   # degrees of steering lock
    "steer_speed":  90.0,   # degrees/sec the steer angle eases toward target
    "wheelbase":   260.0,   # front-to-back axle distance (bicycle model)
    "handbrake_decel": 380.0,  # units/sec^2: rear wheels locked (not a full stop)
    "drift_mult":   2.4,    # how hard the free-rear car rotates into a drift
    # Grip model. 1 = tyres fully planted (the rear axle rolls, never
    # slides), 0 = rear broken loose. The handbrake drops rear grip, the
    # car keeps its momentum and slides, and grip comes back gradually.
    "drift_grip":   0.12,   # rear grip while the handbrake is held
    "grip_loss":    0.08,   # seconds for the rear to break loose
    "grip_return":  0.45,   # seconds for grip to come back after release
    "slide_friction": 240.0,  # units/sec^2 sideways slow-down while sliding
    # Input smoothing (so keyboard driving doesn't look digital).
    "throttle_response": 3.5,  # 1/sec: how fast the pedal reaches full
    "steer_falloff": 0.5,   # steering lock left at top speed (0.5 = half)
    # Tanks (tracked vehicles) skid-steer: A / D turn the hull at this rate
    # (degrees/sec at full lock), even standing still.
    "skid_steer":   False,
    "tank_turn_rate": 45.0,
    # Motorcycles lean into a corner instead of rolling on their springs.
    # The angle where gravity and the corner balance is atan(v * yaw / g),
    # which is why a bike leans further the faster and tighter it goes.
    "lean":          False,   # set from the rig when the pass begins
    "max_lean":      45.0,    # degrees: about where a road tyre gives up
    "lean_response": 0.18,    # seconds to settle into (and out of) a lean
    "gravity":      981.0,    # units/sec^2 (SCALE 10 -> 1 unit = 1 cm)
    # Body oscillation (spring-damper on the body shell). The body lags
    # its instant terrain tilt and overshoots/settles like real mass.
    "body_stiffness": 55.0,  # spring constant — higher = snappier, less lag
    "body_damping":   7.0,   # damping — lower = more bouncy overshoot
    "body_osc":       1.0,   # master 0..1 amount of the oscillation
    # On a ground mesh the hull rises and tilts with the terrain; this is
    # the seconds it takes to settle onto a new slope (bottomed-out wheels
    # always lift it at once).
    "terrain_smooth": 0.08,
}


def step_oscillator(osc, target, dt, stiffness, damping):
    """Advance one critically-tunable spring-damper toward `target`.

    osc = dict(value, vel). Returns the new osc dict.
        accel = stiffness*(target - value) - damping*vel
    Sub-steps internally so it stays stable at low fps / high stiffness.
    """
    value = osc["value"]
    vel = osc["vel"]
    # Sub-step for stability (semi-implicit Euler).
    steps = max(1, int(dt * 120) + 1)
    h = dt / steps
    for _ in range(steps):
        accel = stiffness * (target - value) - damping * vel
        vel += accel * h
        value += vel * h
    return {"value": value, "vel": vel}


def _clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


# =============================================================================
# Terrain: sit the whole vehicle on the ground mesh while driving
# =============================================================================
# The suspension alone can only lift a wheel by maxCompression. On a real
# hill the HULL has to rise and tilt, or the wheels sink into the slope.
# Each tick the drive fits the hull (C_global_CTRL translateY, rotateX,
# rotateZ in zxy order) to the ground under every wheel, then lets the
# suspension soak up what's left.

def hub_height(ty, pitch_deg, roll_deg, mount):
    """World Y of a wheel mount (x, y, z in the car's frame) for a hull at
    height ty with zxy rotateX = pitch and rotateZ = roll."""
    x, y, z = mount
    p, r = math.radians(pitch_deg), math.radians(roll_deg)
    return ty + (x * math.sin(r) + y * math.cos(r)) * math.cos(p) \
        - z * math.sin(p)


def fit_hull(mounts, need, max_comp, max_droop, previous=None, blend=1.0):
    """Hull pose that rests the wheels on the terrain.

    mounts:    [(x, y, z)] wheel mounts in the car's frame (world units)
    need:      the world Y each hub must reach to touch the ground
    previous:  last tick's (ty, pitch, roll); the fit eases toward the new
               pose by `blend` (0..1) so small bumps stay in the suspension
    Returns (ty, pitch_deg, roll_deg). No wheel is left needing more than
    max_comp of compression, and the hull never floats with every wheel
    past max_droop."""
    # Least squares for need_i ~ ty + y_i + x_i * sin(roll) - z_i * sin(pitch)
    ata = [[0.0] * 3 for _ in range(3)]
    atb = [0.0] * 3
    for (x, y, z), n in zip(mounts, need):
        row = (1.0, x, -z)
        for i in range(3):
            atb[i] += row[i] * (n - y)
            for j in range(3):
                ata[i][j] += row[i] * row[j]
    for i in range(3):
        ata[i][i] += 1e-6                    # a tank's wheels can be in a line
    sol = _solve3(ata, atb)
    limit = math.sin(math.radians(40.0))
    ty = sol[0]
    pitch = math.degrees(math.asin(_clamp(sol[2], -limit, limit)))
    roll = math.degrees(math.asin(_clamp(sol[1], -limit, limit)))
    if previous is not None:
        ty = previous[0] + (ty - previous[0]) * blend
        pitch = previous[1] + (pitch - previous[1]) * blend
        roll = previous[2] + (roll - previous[2]) * blend
    # Hard limits: bottomed-out wheels lift the hull, a floating hull drops.
    excess = [n - hub_height(ty, pitch, roll, m) for m, n in zip(mounts, need)]
    worst = max(excess)
    if worst > max_comp:
        ty += worst - max_comp
    elif worst < -max_droop:
        ty += worst + max_droop
    return ty, pitch, roll


def read_terrain_rig():
    """What the terrain fit needs from the rig, or None if this rig's
    suspension doesn't account for a lifted hull (built before v1.0.3:
    rebuild it). Each wheel: its mount in C_global_CTRL's frame and the
    constants of its suspension network, all in world units."""
    if not cmds.objExists(DRIVE_ROOT):
        return None
    m = cmds.xform(DRIVE_ROOT, q=True, ws=True, m=True)
    origin = m[12:15]
    axes = []
    for r in (0, 4, 8):
        a = m[r:r + 3]
        n = math.sqrt(sum(c * c for c in a)) or 1.0
        axes.append([c / n for c in a])
    wheel_list = []
    for p in vehicle_rig_builder.scene_wheel_prefixes():
        travel = f"{p}_suspTravel_PMA"
        if not (cmds.objExists(f"{p}_suspMount_DM")
                and cmds.objExists(f"{p}_suspension_OFFSET")):
            return None
        pos = cmds.xform(f"{p}_suspension_OFFSET", q=True, ws=True, t=True)
        d = [a - b for a, b in zip(pos, origin)]
        feet = {}
        for tag in raycast_ground.FOOT_TAGS:
            src = f"{p}_foot{tag}Src_ADL"
            if cmds.objExists(src):
                feet[tag] = cmds.getAttr(src + ".input2")
        wheel_list.append({
            "prefix": p,
            "mount": tuple(sum(dc * ac for dc, ac in zip(d, ax))
                           for ax in axes),
            "feet": feet,
            # hub sits on the ground when max(ground + foot) - R + restY
            "offset": (-cmds.getAttr(travel + ".input1D[1]")
                       - cmds.getAttr(travel + ".input1D[3]")),
        })
    if not wheel_list:
        return None
    # The feet and offset above read in world units; the travel limits are
    # rig units (the suspension clamps its local travel with them).
    chassis = "C_chassis_CTRL"
    scale = vehicle_rig_builder.rig_scale()
    return {"wheels": wheel_list,
            "max_comp": cmds.getAttr(chassis + ".maxCompression") * scale,
            "max_droop": cmds.getAttr(chassis + ".maxDroop") * scale}


def terrain_need(terrain, ground_y):
    """World Y each hub must reach, from {(prefix, tag): ground Y}."""
    need = []
    for w in terrain["wheels"]:
        best = max((ground_y[(w["prefix"], tag)] + geo
                    for tag, geo in w["feet"].items()
                    if (w["prefix"], tag) in ground_y), default=None)
        need.append(w["offset"] + (best if best is not None else 0.0))
    return need


def apply_terrain(terrain, ground_y, hull, dt, params=None, key=True):
    """One drive tick of the terrain fit: pose (and key) the hull from the
    sampled ground. Returns the new hull (ty, pitch, roll)."""
    smooth = (params or DEFAULT_PARAMS).get("terrain_smooth", 0.08)
    blend = 1.0 - math.exp(-dt / max(smooth, 1e-3))
    hull = fit_hull([w["mount"] for w in terrain["wheels"]],
                    terrain_need(terrain, ground_y),
                    terrain["max_comp"], terrain["max_droop"],
                    previous=hull, blend=blend)
    plugs = [f"{DRIVE_ROOT}.{c}" for c in ("translateY", "rotateX", "rotateZ")]
    for plug, value in zip(plugs, hull):
        cmds.setAttr(plug, value)
    if key:
        cmds.setKeyframe(plugs)
    return hull


def _solve3(a, b):
    """Gaussian elimination for a 3x3 system."""
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for c in range(3):
        piv = max(range(c, 3), key=lambda r: abs(m[r][c]))
        m[c], m[piv] = m[piv], m[c]
        if abs(m[c][c]) < 1e-12:
            return [0.0, 0.0, 0.0]
        for r in range(3):
            if r != c:
                f = m[r][c] / m[c][c]
                m[r] = [vr - f * vc for vr, vc in zip(m[r], m[c])]
    return [m[i][3] / m[i][i] for i in range(3)]


def step_drive(state, keys, dt, params=None):
    """Pure driving integrator — no Maya calls, fully testable.

    state: dict(speed, heading_rad, x, z, odometer, steer_deg), plus the
        optional carried-over v_lat, grip, yaw_rate, throttle.
        x, z are the REAR-AXLE position. With the tyres planted the car
        pivots about the rear axle like a real car (the rear wheels roll,
        the front wheels steer, the back never slides). The handbrake
        breaks the rear loose: the car keeps its momentum, the back slides
        out (v_lat) and grip returns gradually after release.
        Distances (x, z, odometer, speed) are WORLD units; apply_state
        converts the odometer to rig units for C_chassis_CTRL.
    keys:  set of held keys among {'w','a','s','d','space'}
    dt:    seconds since last tick
    Returns the new state dict.
    """
    p = dict(DEFAULT_PARAMS, **(params or {}))
    speed = state["speed"]                    # along the car (rear axle)
    heading = state["heading_rad"]
    x, z = state["x"], state["z"]
    odo = state["odometer"]
    steer = state["steer_deg"]
    v_lat = state.get("v_lat", 0.0)           # sideways slide of the rear
    grip = state.get("grip", 1.0)
    throttle = state.get("throttle", 0.0)
    handbrake = "space" in keys

    # ---- pedal smoothing: throttle / brake ease in instead of snapping ----
    want = 0.0 if handbrake else (1.0 if "w" in keys else
                                  -1.0 if "s" in keys else 0.0)
    throttle += (want - throttle) * min(1.0, p["throttle_response"] * dt)
    if abs(want) > 0 and abs(throttle) < 0.25:
        throttle = 0.25 * want                # instant initial bite

    # ---- rear grip: the handbrake breaks the rear loose ----
    target_grip = p["drift_grip"] if handbrake else 1.0
    tau = p["grip_loss"] if target_grip < grip else p["grip_return"]
    grip += (target_grip - grip) * (1.0 - math.exp(-dt / max(tau, 1e-4)))

    # ---- longitudinal: pedal, brake, handbrake, coasting ----
    if handbrake:
        d = p["handbrake_decel"] * dt
        speed = max(0.0, speed - d) if speed > 0 else min(0.0, speed + d)
    elif throttle > 0.01 and speed >= -1.0:
        speed += p["accel"] * throttle * (0.4 + 0.6 * grip) * dt
    elif throttle > 0.01:                     # W while rolling backwards
        speed = min(0.0, speed + p["brake"] * throttle * dt)
    elif throttle < -0.01:
        speed += p["brake"] * throttle * dt   # brake, then reverse
    else:
        decay = p["friction"] * dt
        speed = max(0.0, speed - decay) if speed > 0 else \
            min(0.0, speed + decay)
    speed = _clamp(speed, -p["max_reverse"], p["max_speed"])

    # ---- steering: eases toward the key, less lock at high speed ----
    # Sign convention: D = steer RIGHT = positive steer; A = LEFT = negative.
    # Positive steer feeds C_steering_CTRL.rotateZ, which (through the
    # rig's STEERING_RATIO = -1) points the front wheels to the RIGHT —
    # matching the direction the car curves below.
    fast = _clamp(abs(speed) / max(p["max_speed"], 1.0), 0.0, 1.0)
    lock = p["max_steer"] * (1.0 - (1.0 - p["steer_falloff"]) * fast)
    if handbrake:
        lock = p["max_steer"]                 # full lock to flick a drift
    step = p["steer_speed"] * dt
    if "d" in keys:
        steer = min(lock, steer + step)
    elif "a" in keys:
        steer = max(-lock, steer - step)
    else:
        steer = max(0.0, steer - step) if steer > 0 else \
            min(0.0, steer + step)

    # ---- yaw: the steered front pulls the car round ----
    # With full grip the car follows the clean bicycle arc about the rear
    # axle (heading rate = speed * tan(steer) / wheelbase). With the rear
    # loose the front drags the nose round harder (drift_mult) and the
    # rotation carries momentum instead of snapping to the arc. Rate is
    # NEGATIVE-heading for positive steer, so D (right) curves toward -X.
    slide_speed = math.hypot(speed, v_lat)
    if p["skid_steer"]:
        # Tracks turn the hull directly (counter-rotating on the spot).
        kin_rate = (steer / max(p["max_steer"], 1e-3)
                    * math.radians(p["tank_turn_rate"]))
    else:
        kin_rate = speed * math.tan(math.radians(steer)) / p["wheelbase"]
    rate = state.get("yaw_rate", kin_rate)
    drift_rate = kin_rate * p["drift_mult"]
    if slide_speed < 1e-3:
        drift_rate = 0.0
    want_rate = kin_rate + (drift_rate - kin_rate) * (1.0 - grip)
    yaw_tau = 0.02 + 0.3 * (1.0 - grip)       # planted = instant, loose = lazy
    rate += (want_rate - rate) * (1.0 - math.exp(-dt / yaw_tau))

    # ---- move: momentum in world space, then tyres take sideways speed ----
    fwd = (math.sin(heading), math.cos(heading))
    side = (math.cos(heading), -math.sin(heading))
    vx = fwd[0] * speed + side[0] * v_lat
    vz = fwd[1] * speed + side[1] * v_lat
    x += vx * dt
    z += vz * dt
    odo += speed * dt
    heading -= rate * dt

    # The car turned under its own momentum: re-express the velocity in the
    # new heading, then let the tyres kill the sideways part. Planted tyres
    # turn that sideways speed into forward speed (a clean corner keeps its
    # pace); sliding tyres scrub it off as friction.
    fwd = (math.sin(heading), math.cos(heading))
    side = (math.cos(heading), -math.sin(heading))
    new_long = vx * fwd[0] + vz * fwd[1]
    new_lat = vx * side[0] + vz * side[1]
    kept = new_lat * math.exp(-dt / (0.01 + 0.9 * (1.0 - grip)))
    removed = new_lat - kept
    if abs(kept) > 0:                         # sliding friction
        scrub = p["slide_friction"] * (1.0 - grip) * dt
        kept = max(0.0, kept - scrub) if kept > 0 else min(0.0, kept + scrub)
    sign = 1.0 if new_long >= 0 else -1.0
    speed = sign * math.sqrt(new_long * new_long + grip * removed * removed)
    v_lat = kept
    if not handbrake and grip > 0.98:
        v_lat = 0.0                            # fully planted: no creep
    speed = _clamp(speed, -p["max_reverse"], p["max_speed"])

    # ---- lean: a bike tips into the corner it is turning ----
    # tan(lean) = v * yaw_rate / g is the angle where the corner's push and
    # gravity balance through the tyres. Positive rate = turning right (the
    # heading falls), which is a lean to the right, so the sign carries
    # straight through to C_chassis_CTRL.lean.
    lean = state.get("lean_deg", 0.0)
    if p["lean"]:
        want_lean = _clamp(
            math.degrees(math.atan2(speed * rate, max(p["gravity"], 1e-3))),
            -p["max_lean"], p["max_lean"])
        lean += (want_lean - lean) * (
            1.0 - math.exp(-dt / max(p["lean_response"], 1e-4)))

    return {"speed": speed, "heading_rad": heading, "x": x, "z": z,
            "odometer": odo, "steer_deg": steer, "v_lat": v_lat,
            "grip": grip, "yaw_rate": rate, "throttle": throttle,
            "lean_deg": lean}


def measure_geometry():
    """Read the car's wheelbase + rear-axle offset from the actual rig
    hubs, in the car's own forward direction (works at any pose).

    Returns (wheelbase, rear_to_center) where rear_to_center is the
    forward distance from the rear axle to C_global_CTRL's pivot (the
    point the manipulator + body sit at). Falls back to the defaults if
    the hubs aren't found.
    """
    wb_default = DEFAULT_PARAMS["wheelbase"]
    rtc_default = wb_default / 2.0
    bike = ("CF_hub_BIND_JNT", "CB_hub_BIND_JNT")
    hubs = ("LF_hub_BIND_JNT", "RF_hub_BIND_JNT",
            "LB_hub_BIND_JNT", "RB_hub_BIND_JNT")
    single_track = all(cmds.objExists(h) for h in bike)
    if not single_track and not all(cmds.objExists(h) for h in hubs):
        return wb_default, rtc_default
    h = math.radians(cmds.getAttr(f"{DRIVE_ROOT}.rotateY"))
    fwd = (math.sin(h), math.cos(h))
    cx, _, cz = cmds.xform(DRIVE_ROOT, q=True, ws=True, t=True)

    def fwd_dist(node):
        x, _, z = cmds.xform(node, q=True, ws=True, t=True)
        return (x - cx) * fwd[0] + (z - cz) * fwd[1]

    if single_track:
        # A bike is the bicycle model made literal: one steered wheel in
        # front, one driven wheel behind, and it pivots about the back one.
        front, back = fwd_dist(bike[0]), fwd_dist(bike[1])
        wheelbase = abs(front - back)
        return ((wheelbase, -back) if wheelbase > 1.0
                else (wb_default, rtc_default))

    front = 0.5 * (fwd_dist("LF_hub_BIND_JNT") + fwd_dist("RF_hub_BIND_JNT"))
    # The car pivots about the middle of its UNsteered axles (one on a car,
    # the rear bogie on a 6 / 8-wheel truck).
    fixed = [p for p in vehicle_rig_builder.scene_wheel_prefixes()
             if not cmds.objExists(f"{p}_steeringRatio_MUL")
             and cmds.objExists(f"{p}_hub_BIND_JNT")]
    if fixed:
        back = sum(fwd_dist(f"{p}_hub_BIND_JNT") for p in fixed) / len(fixed)
    else:
        back = 0.5 * (fwd_dist("LB_hub_BIND_JNT")
                      + fwd_dist("RB_hub_BIND_JNT"))
    wheelbase = abs(front - back)
    if wheelbase < 1.0:
        return wb_default, rtc_default
    rear_to_center = -back   # forward distance from rear axle to centre
    return wheelbase, rear_to_center


def read_state_from_scene(rear_to_center=None):
    """Seed the drive state from the car's current pose. The tracked
    x,z is the REAR AXLE, derived from C_global's centre."""
    if rear_to_center is None:
        rear_to_center = DEFAULT_PARAMS["wheelbase"] / 2.0
    cx = cmds.getAttr(f"{DRIVE_ROOT}.translateX")
    cz = cmds.getAttr(f"{DRIVE_ROOT}.translateZ")
    heading = math.radians(cmds.getAttr(f"{DRIVE_ROOT}.rotateY"))
    # rear axle = centre - forward * rear_to_center
    rx = cx - math.sin(heading) * rear_to_center
    rz = cz - math.cos(heading) * rear_to_center
    # The rig's odometer is in rig units (the wheels divide it by their
    # local radius); the drive integrates world distance.
    odo = cmds.getAttr(ODO_ATTR) * vehicle_rig_builder.rig_scale()
    steer = (cmds.getAttr(f"{STEER_CTRL}.rotateZ")
             if cmds.objExists(STEER_CTRL) else 0.0)
    lean = cmds.getAttr(LEAN_ATTR) if has_lean() else 0.0
    return {"speed": 0.0, "heading_rad": heading, "x": rx, "z": rz,
            "odometer": odo, "steer_deg": steer, "lean_deg": lean}


def apply_state(state, key_frame=True, rear_to_center=None):
    """Push the drive state onto the rig and (optionally) keyframe it.
    The state's x,z is the rear axle; C_global's centre is computed as
    rear_axle + forward * rear_to_center so the body sits correctly while
    the motion pivots about the rear axle (no slide)."""
    if rear_to_center is None:
        rear_to_center = DEFAULT_PARAMS["wheelbase"] / 2.0
    heading = state["heading_rad"]
    cx = state["x"] + math.sin(heading) * rear_to_center
    cz = state["z"] + math.cos(heading) * rear_to_center
    cmds.setAttr(f"{DRIVE_ROOT}.translateX", cx)
    cmds.setAttr(f"{DRIVE_ROOT}.translateZ", cz)
    cmds.setAttr(f"{DRIVE_ROOT}.rotateY", math.degrees(heading))
    # World distance -> rig units, so a scaled-up car's bigger wheels roll
    # (and a tank's treads run) at the right rate.
    cmds.setAttr(ODO_ATTR,
                 state["odometer"] / vehicle_rig_builder.rig_scale())
    if cmds.objExists(STEER_CTRL):
        cmds.setAttr(f"{STEER_CTRL}.rotateZ", state["steer_deg"])
    lean = "lean_deg" in state and has_lean()
    if lean:
        cmds.setAttr(LEAN_ATTR, state["lean_deg"])
    slip = cmds.objExists(SLIP_ATTR)
    if slip:
        cmds.setAttr(SLIP_ATTR, slip_amount(state))
    if key_frame:
        cmds.setKeyframe([f"{DRIVE_ROOT}.translateX",
                          f"{DRIVE_ROOT}.translateZ",
                          f"{DRIVE_ROOT}.rotateY",
                          ODO_ATTR])
        if cmds.objExists(STEER_CTRL):
            cmds.setKeyframe(f"{STEER_CTRL}.rotateZ")
        if lean:
            cmds.setKeyframe(LEAN_ATTR)
        if slip:
            cmds.setKeyframe(SLIP_ATTR)


def can_drive():
    """True if a drivable vehicle rig is in the scene."""
    return (cmds.objExists(DRIVE_ROOT)
            and cmds.attributeQuery("odometer",
                                     node="C_chassis_CTRL", exists=True))


# The exact channels a drive pass keyframes. clear_drive_animation()
# wipes keys on these (and only these) so you can redo the drive cleanly.
def _drive_channels():
    chans = [f"{DRIVE_ROOT}.translateX",
             f"{DRIVE_ROOT}.translateZ",
             f"{DRIVE_ROOT}.rotateY",
             ODO_ATTR]
    if cmds.objExists(STEER_CTRL):
        chans.append(f"{STEER_CTRL}.rotateZ")
    if has_lean():
        chans.append(LEAN_ATTR)
    if cmds.objExists(SLIP_ATTR):
        chans.append(SLIP_ATTR)
    # Hull height + tilt, keyed only by a drive over a ground mesh.
    for c in ("translateY", "rotateX", "rotateZ"):
        plug = f"{DRIVE_ROOT}.{c}"
        if (cmds.objExists(DRIVE_ROOT)
                and cmds.keyframe(plug, q=True, keyframeCount=True)):
            chans.append(plug)
    # Trailer swing / pitch / roll.
    chans.extend(vehicle_trailers.swing_plugs())
    # Body oscillation offset channels (keyed by the drive loop).
    if cmds.objExists(BODY_OSC):
        for c in _BODY_CHANS:
            chans.append(f"{BODY_OSC}.{c}")
    # Ground-raycast footprint sources (keyed by the drive loop when a
    # terrain mesh is assigned).
    for p in raycast_ground._prefixes():
        for t in raycast_ground.FOOT_TAGS:
            src = f"{p}_foot{t}Src_ADL"
            if cmds.objExists(src):
                chans.append(f"{src}.input1")
    return chans


def _footprint_source_channels():
    """The ground-sampling input1 channels the drive loop keyframes when a
    terrain mesh is assigned. These are INFRASTRUCTURE, not car motion."""
    out = set()
    for p in raycast_ground._prefixes():
        for t in raycast_ground.FOOT_TAGS:
            src = f"{p}_foot{t}Src_ADL"
            if cmds.objExists(src):
                out.add(f"{src}.input1")
    return out


def _restore_ground_sources():
    """Re-establish the live ground hookup on the footprint sources after a
    drive clear. The drive loop disconnects the closestPointOnMesh feeds at
    start and keyframes input1 directly; if we just cut those keys the
    sources go dead and terrain stops being detected until a scene reload.

    If a terrain mesh is still assigned, re-wire the per-wheel sampling;
    otherwise reconnect the flat C_ground_LOC so the suspension still reads
    a real ground value (Y=0) instead of a frozen number.
    """
    mesh = vehicle_rig_builder.assigned_ground_mesh()
    if mesh and cmds.objExists(mesh):
        vehicle_rig_builder.assign_ground_mesh(mesh)
    else:
        # No terrain assigned — make the footprint sources read the flat
        # ground locator again (clear_bake reconnects it).
        raycast_ground.clear_bake(reconnect_flat=True)


def clear_drive_animation(reset_pose=True):
    """Delete every keyframe a drive pass laid down, so you can re-drive
    from scratch. Optionally zero the MOTION channels back to the start
    pose.

    Only touches the drive channels (global translateX/Z + rotateY,
    odometer, steering rotateZ, body-OSC offsets, ground footprint
    samples) — your manual posing on other ctrls is left alone.

    Importantly, the ground footprint-sample channels are NOT zeroed: their
    keys are cut and then their live terrain hookup is restored, so the
    ground stays detectable after a clear (no scene reload needed).

    Returns the number of channels cleared.
    """
    if not can_drive():
        cmds.warning("[drive] No drivable vehicle rig in scene.")
        return 0
    # A baked physics pass sits on top of the drive keys: take it off first
    # so the drive channels (and the suspension network) are the originals.
    vehicle_sim.clear_simulation()
    vehicle_crash.clear_damage()          # the dents came from that drive
    vehicle_cargo.clear_bake()            # so did the cargo shake
    ground_chans = _footprint_source_channels()
    n = 0
    for ch in _drive_channels():
        node, attr = ch.split(".", 1)
        if not cmds.objExists(node):
            continue
        is_ground = ch in ground_chans
        # Remove all animation on this channel.
        if cmds.keyframe(ch, q=True, keyframeCount=True) > 0:
            if is_ground:
                # cutKey(clear=True) would DELETE the footprint
                # addDoubleLinear node and kill terrain following; delete
                # the animCurve directly so the node survives.
                raycast_ground.clear_channel_keys(ch)
            else:
                cmds.cutKey(ch, clear=True)
            n += 1
        # Reset MOTION channels to the start pose — but never the ground
        # sources (those get their live connection restored below, and a
        # static 0 would silently disable terrain following).
        if reset_pose and not is_ground:
            try:
                cmds.setAttr(ch, 0)
            except Exception:
                pass
    # Reconnect terrain sampling so clearing a drive never loses the ground.
    if ground_chans:
        _restore_ground_sources()
    print(f"[drive] Cleared drive animation on {n} channel(s)"
          + (" and reset the car to start pose." if reset_pose else "."))
    return n


# =============================================================================
# Record now, key on stop
# =============================================================================
# Keying every channel and stepping the timeline on every tick makes Maya
# re-evaluate the whole rig and rebuild its evaluation graph each frame (a
# 6-wheel tank with two trailers took ~60 ms a tick, over the 42 ms a 24 fps
# drive has). So a drive only POSES the rig while you steer, remembers the
# values, and writes every key in one batch when you stop.

class DriveRecorder(object):
    """Channel values captured tick by tick, keyed from `start` on bake()."""

    def __init__(self, plugs, start):
        self.plugs = [p for p in dict.fromkeys(plugs)
                      if cmds.objExists(p.split(".", 1)[0])]
        self.start = int(start)
        self.rows = []

    def capture(self):
        self.rows.append([cmds.getAttr(p) for p in self.plugs])

    @property
    def end(self):
        return self.start + len(self.rows) - 1

    def bake(self):
        """Key every captured frame, replacing keys already in that range.
        Returns the number of frames keyed."""
        if not self.rows:
            return 0
        start, end = self.start, self.end
        frames = range(start, end + 1)
        for i, plug in enumerate(self.plugs):
            _clear_key_range(plug, start, end)
            for f, row in zip(frames, self.rows):
                cmds.setKeyframe(plug, t=f, v=row[i])
        return len(self.rows)


def _clear_key_range(plug, start, end):
    times = cmds.keyframe(plug, q=True) or []
    if not times:
        return
    if all(start <= t <= end for t in times):
        # Emptying a curve with cutKey can delete the node it drives (the
        # ground footprint sources): detach the curve first.
        raycast_ground.clear_channel_keys(plug)
    else:
        cmds.cutKey(plug, time=(start, end), option="keys")


def pause_cached_playback():
    """Turn Cached Playback off for a drive (keying keeps throwing its cache
    away). Returns whether it was on, for resume_cached_playback()."""
    try:
        was_on = bool(cmds.evaluator(name="cache", q=True, en=True))
        if was_on:
            cmds.evaluator(name="cache", en=False)
        return was_on
    except Exception:
        return False


def resume_cached_playback(was_on):
    if was_on:
        try:
            cmds.evaluator(name="cache", en=True)
        except Exception:
            pass


def scene_fps():
    return om2.MTime(1.0, om2.MTime.kSeconds).asUnits(om2.MTime.uiUnit())


# =============================================================================
# Interactive Qt drive session
# =============================================================================

def _maya_main_window():
    ptr = omui.MQtUtil.mainWindow()
    return wrapInstance(int(ptr), QtWidgets.QWidget)


_KEY_MAP = {
    QtCore.Qt.Key_W: "w", QtCore.Qt.Key_A: "a",
    QtCore.Qt.Key_S: "s", QtCore.Qt.Key_D: "d",
    QtCore.Qt.Key_Up: "w", QtCore.Qt.Key_Left: "a",
    QtCore.Qt.Key_Down: "s", QtCore.Qt.Key_Right: "d",
    QtCore.Qt.Key_Space: "space",   # handbrake
}


class DrivePass(object):
    """A drive pass without the window: set it up, tick it once per frame
    with the keys held, finish to write the keys. DriveSession (the WASD
    panel) is this plus a keyboard and a timer; scripts and tests can use it
    directly:

        p = DrivePass()
        p._begin()
        for keys in ({"w"}, {"w"}, {"w", "d"}):
            p.held = set(keys)
            p._tick_once()
        p._finish()
    """

    def __init__(self, fps=None, params=None):
        self.fps = fps or scene_fps()
        self.params = dict(params or DEFAULT_PARAMS)
        self.rear_to_center = self.params["wheelbase"] / 2.0
        self.held = set()
        self.state = None
        self.recorder = None
        self.crash = None
        self.damage = None

    def _begin(self):
        """Set up a drive pass from the current frame: read the car, the
        ground, the trailers, and start recording."""
        self.cache_was_on = pause_cached_playback()
        # Measure this car's actual wheelbase + rear-axle offset so the
        # turn radius and the no-slide pivot are correct for any model.
        wb, rtc = measure_geometry()
        self.params["wheelbase"] = wb
        self.rear_to_center = rtc
        # Tanks skid-steer about their centre.
        self.params["skid_steer"] = vehicle_rig_builder.is_tracked()
        if self.params["skid_steer"]:
            self.rear_to_center = 0.0
        # Bikes lean into their corners.
        self.params["lean"] = vehicle_rig_builder.is_motorcycle()
        # Record how much the tyres slide, so tracks can be baked from it.
        ensure_slip_attr()
        self.state = read_state_from_scene(self.rear_to_center)
        # Ground raycast — if a terrain mesh is assigned, sample it with a
        # true DOWNWARD ray each frame (avoids the closestPointOnMesh
        # "snap onto the obstacle flank" premature-lift bug). Grab the
        # MFnMesh once at the start of the pass.
        self.ground_fn = None
        self.ground_src_plugs = []
        gmesh = vehicle_rig_builder.assigned_ground_mesh()
        if gmesh and raycast_ground.has_footprints():
            self.ground_fn = raycast_ground._mesh_fn(gmesh)
            # Disconnect the CPOM nodes so the raycast can set the values.
            raycast_ground.prepare_for_raycast()
            for p in raycast_ground._prefixes():
                for t in raycast_ground.FOOT_TAGS:
                    src = f"{p}_foot{t}Src_ADL"
                    if cmds.objExists(src):
                        self.ground_src_plugs.append(f"{src}.input1")
        # Terrain fit: the hull rises and tilts with the ground.
        self.terrain = read_terrain_rig() if self.ground_fn else None
        self.hull = None
        if self.terrain:
            cmds.setAttr(f"{DRIVE_ROOT}.rotateOrder", vehicle_sim.ZXY)
            self.hull = tuple(cmds.getAttr(f"{DRIVE_ROOT}.{c}")
                              for c in ("translateY", "rotateX", "rotateZ"))
        elif self.ground_fn:
            cmds.warning("This vehicle rig was built before terrain driving: "
                         "rebuild it so the hull climbs hills instead of "
                         "the wheels sinking in.")
        # Trailers swing along behind.
        self.trailers = (vehicle_trailers.TrailerFollower()
                         if vehicle_trailers.has_trailers() else None)
        # Body oscillators — one spring-damper per body channel, seeded
        # at the current instant target so they don't jump on the first
        # frame.
        self.body_osc = {}
        if has_body_osc():
            for chan in _BODY_CHANS:
                t = cmds.getAttr(f"{BODY_AUTO}.{chan}")
                self.body_osc[chan] = {"value": t, "vel": 0.0}
        # Crash obstacles stop the car; the old dents belonged to the old
        # motion and are baked again when the pass ends.
        vehicle_crash.clear_damage()
        self.damage = None
        self.crash = None
        if vehicle_crash.obstacles():
            self.crash = vehicle_crash.DriveCrash(
                max_speed=self.params["max_speed"])
        # Everything a tick sets, captured per tick and keyed on stop.
        plugs = [f"{DRIVE_ROOT}.translateX", f"{DRIVE_ROOT}.translateZ",
                 f"{DRIVE_ROOT}.rotateY", ODO_ATTR]
        if cmds.objExists(STEER_CTRL):
            plugs.append(f"{STEER_CTRL}.rotateZ")
        if self.params["lean"] and has_lean():
            plugs.append(LEAN_ATTR)
        if cmds.objExists(SLIP_ATTR):
            plugs.append(SLIP_ATTR)
        if self.terrain:
            plugs += [f"{DRIVE_ROOT}.{c}"
                      for c in ("translateY", "rotateX", "rotateZ")]
        plugs += vehicle_trailers.swing_plugs() + self.ground_src_plugs
        if self.body_osc:
            plugs += [f"{BODY_OSC}.{c}" for c in _BODY_CHANS]
        self._start_frame = int(round(cmds.currentTime(q=True)))
        self.recorder = DriveRecorder(plugs, self._start_frame)

    def _tick_once(self):
        """One frame of driving: move, sit on the ground, pull the trailers,
        bounce the body, and remember the pose (no keys yet)."""
        dt = 1.0 / self.fps
        # Near a crash obstacle a fast tick is cut into pieces so the car
        # can't jump through a thin wall; it stops at it instead.
        steps = self.crash.substeps(self.state, dt) if self.crash else 1
        for _ in range(steps):
            self.state = step_drive(self.state, self.held, dt / steps,
                                    self.params)
            apply_state(self.state, key_frame=False,
                        rear_to_center=self.rear_to_center)
            if self.crash is not None and self.crash.resolve(
                    self.state, dt / steps, len(self.recorder.rows)):
                apply_state(self.state, key_frame=False,
                            rear_to_center=self.rear_to_center)
        # Ground raycast: sample the terrain straight DOWN under each
        # footprint (now that the car has moved this frame). True downward
        # ray, so a wheel only lifts once it's actually over an obstacle.
        self._tick_ground_raycast()
        # Body oscillation: must run AFTER the suspension updated, so the
        # node network has C_body_AUTO at the new instant tilt to lag.
        self._tick_body_osc(dt)
        self.recorder.capture()

    def _finish(self):
        """Key the recorded drive and move the timeline to its last frame.
        Returns (first frame, last frame, frames keyed)."""
        rec = self.recorder
        n = rec.bake() if rec else 0
        # Dent the car wherever the drive (this pass and any before it)
        # pushed it into an obstacle.
        if n and vehicle_crash.obstacles():
            self.damage = vehicle_crash.bake_damage()
        # Roof racks and loose parts ride the new motion.
        if n and vehicle_cargo.list_cargo():
            vehicle_cargo.bake_cargo()
        resume_cached_playback(getattr(self, "cache_was_on", False))
        if n:
            cmds.currentTime(rec.end, edit=True)
        return (rec.start if rec else 0, rec.end if rec else 0, n)

    def _tick_ground_raycast(self):
        """Raycast each footprint straight down onto the assigned terrain
        and set the suspension ground sources, sit the hull on the terrain
        and pull the trailers. Without a ground mesh only the trailers move."""
        if not self.ground_fn:
            if self.trailers:
                self.trailers.step(key=False)
            return
        ground_y = {}
        raycast_ground.sample_footprints(
            self.ground_fn, prefixes=vehicle_rig_builder.scene_wheel_prefixes(),
            out=ground_y)
        if self.terrain:
            self.hull = apply_terrain(self.terrain, ground_y, self.hull,
                                      1.0 / self.fps, self.params, key=False)
        # Trailers follow the vehicle's new pose, then their wheels sample
        # the ground where the trailers now are.
        if self.trailers:
            self.trailers.step(key=False)
            trailer_wheels = vehicle_rig_builder.trailer_wheel_prefixes()
            if trailer_wheels:
                raycast_ground.sample_footprints(self.ground_fn,
                                                 prefixes=trailer_wheels)

    def _tick_body_osc(self, dt):
        """Advance the body spring-dampers toward the live instant tilt and
        write the LAG offset onto C_body_OSC."""
        if not self.body_osc or not has_body_osc():
            return
        amount = self.params.get("body_osc", 1.0)
        stiff = self.params.get("body_stiffness", 55.0)
        damp = self.params.get("body_damping", 7.0)
        for chan in _BODY_CHANS:
            target = cmds.getAttr(f"{BODY_AUTO}.{chan}")
            osc = step_oscillator(self.body_osc[chan], target, dt,
                                  stiff, damp)
            self.body_osc[chan] = osc
            # OSC offset = (damped - target) * amount, so the final shown
            # tilt = target + offset = lerp(target, damped, amount).
            offset = (osc["value"] - target) * amount
            cmds.setAttr(f"{BODY_OSC}.{chan}", offset)


class DriveSession(QtWidgets.QDialog, DrivePass):
    """Small focused panel that captures WASD and drives the car."""

    def __init__(self, fps=30, params=None, parent=None):
        super(DriveSession, self).__init__(parent or _maya_main_window())
        self.setObjectName(WINDOW_OBJECT_NAME)
        self.setWindowTitle("Drive Mode")
        self.setWindowFlags(self.windowFlags() | QtCore.Qt.Window
                            | QtCore.Qt.WindowStaysOnTopHint)
        self.setFocusPolicy(QtCore.Qt.StrongFocus)
        self.setMinimumWidth(260)

        self.fps = fps
        self.params = dict(params or DEFAULT_PARAMS)
        self.rear_to_center = self.params["wheelbase"] / 2.0
        self.held = set()
        self.state = None
        self.recorder = None
        self.crash = None
        self.damage = None
        self.driving = False

        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(int(1000.0 / fps))
        self.timer.timeout.connect(self._tick)

        self._build_ui()

    # -----------------------------------------------------------------------

    def _build_ui(self):
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(8)

        bike = vehicle_rig_builder.is_motorcycle()
        title = QtWidgets.QLabel(
            "🏍  RIDE MODE" if bike else "🚗  DRIVE MODE")
        title.setStyleSheet("QLabel { font-size: 14pt; font-weight: bold; "
                            "color: #cfe0ff; }")
        lay.addWidget(title)

        help_lbl = QtWidgets.QLabel(
            "W / S   — accelerate / brake + reverse\n"
            + ("A / D   — steer left / right; the bike leans into it\n"
               if bike else "A / D   — steer left / right\n")
            + "Space — handbrake (drift / quick stop)\n"
            "Esc     — stop driving\n\n"
            "Keep THIS panel focused (click it if keys stop responding).")
        help_lbl.setStyleSheet("QLabel { color: #ccc; }")
        lay.addWidget(help_lbl)

        # ---- Tuning (adjust the feel live; this is where 'drift' lives) ----
        tune = QtWidgets.QGroupBox("Handling")
        grid = QtWidgets.QGridLayout(tune)
        grid.setSpacing(4)
        self._tune_spins = {}

        def add_spin(row, label, key, lo, hi, step, tip):
            grid.addWidget(QtWidgets.QLabel(label), row, 0)
            sp = QtWidgets.QDoubleSpinBox()
            sp.setRange(lo, hi)
            sp.setSingleStep(step)
            sp.setValue(self.params[key])
            sp.setToolTip(tip)
            sp.setFocusPolicy(QtCore.Qt.ClickFocus)
            sp.valueChanged.connect(
                lambda v, k=key: self.params.__setitem__(k, v))
            grid.addWidget(sp, row, 1)
            self._tune_spins[key] = sp

        add_spin(0, "Drift", "drift_mult", 1.0, 6.0, 0.1,
                 "Handbrake turn multiplier — higher = wilder drifts.")
        add_spin(1, "Top speed", "max_speed", 100.0, 4000.0, 50.0,
                 "Maximum forward speed (units/sec).")
        add_spin(2, "Accel", "accel", 50.0, 1500.0, 20.0,
                 "How hard the throttle pulls (units/sec^2).")
        add_spin(3, "Steer lock", "max_steer", 10.0, 60.0, 1.0,
                 "Maximum steering angle in degrees.")
        add_spin(7, "Drift grip", "drift_grip", 0.0, 1.0, 0.02,
                 "Rear grip left while the handbrake is held. Lower = the back "
                 "slides out further.")
        add_spin(8, "Grip return", "grip_return", 0.05, 3.0, 0.05,
                 "Seconds for the tyres to grip again after you let go of "
                 "the handbrake. Longer = a lazier catch.")
        add_spin(9, "Pedal response", "throttle_response", 0.5, 20.0, 0.5,
                 "How fast the throttle and brake ease in. Higher = snappier.")
        add_spin(4, "Body bounce", "body_osc", 0.0, 1.0, 0.05,
                 "How much the body lags + overshoots the terrain tilt. "
                 "0 = instant (no bounce), 1 = full spring-damper.")
        add_spin(5, "Body settle", "body_damping", 1.0, 20.0, 0.5,
                 "Damping. LOWER = bouncier / more overshoot, "
                 "HIGHER = settles faster with less wobble.")
        add_spin(6, "Body stiffness", "body_stiffness", 10.0, 150.0, 5.0,
                 "Spring stiffness. Higher = snappier, follows the "
                 "terrain tilt more tightly with less lag.")
        if bike:
            add_spin(10, "Lean limit", "max_lean", 0.0, 60.0, 1.0,
                     "How far the bike is allowed to lean, in degrees. A "
                     "road bike runs out of tyre around 45.")
            add_spin(11, "Lean settle", "lean_response", 0.02, 1.0, 0.02,
                     "Seconds to tip into (and back out of) a lean. Higher "
                     "= lazier, more weight to it.")
        lay.addWidget(tune)

        self.status = QtWidgets.QLabel("Ready — press Start, then WASD.")
        self.status.setStyleSheet("QLabel { color: #8f8; "
                                  "border-top: 1px solid #444; padding-top: 6px; }")
        lay.addWidget(self.status)

        row = QtWidgets.QHBoxLayout()
        self.btn_start = QtWidgets.QPushButton("Start Driving")
        self.btn_start.setStyleSheet("QPushButton { font-weight: bold; "
                                     "padding: 6px; }")
        self.btn_start.clicked.connect(self.start_driving)
        self.btn_stop = QtWidgets.QPushButton("Stop (Esc)")
        self.btn_stop.clicked.connect(self.stop_driving)
        self.btn_stop.setEnabled(False)
        # No keyboard focus on the buttons — so Space (handbrake) goes to
        # the dialog's key handler, not a button "click".
        self.btn_start.setFocusPolicy(QtCore.Qt.NoFocus)
        self.btn_stop.setFocusPolicy(QtCore.Qt.NoFocus)
        row.addWidget(self.btn_start)
        row.addWidget(self.btn_stop)
        lay.addLayout(row)

        # Clear the baked drive animation so you can take another pass.
        self.btn_clear = QtWidgets.QPushButton("Clear Drive Animation")
        self.btn_clear.setToolTip(
            "Delete the keyframes from the last drive pass (car position,\n"
            "heading, odometer, steering) and reset the car to its start\n"
            "pose, so you can re-drive from scratch. Other manual posing\n"
            "is left untouched.")
        self.btn_clear.setFocusPolicy(QtCore.Qt.NoFocus)
        self.btn_clear.clicked.connect(self._on_clear)
        lay.addWidget(self.btn_clear)

        # Physics pass over what you just drove (jumps, landings, body roll).
        self.btn_sim = QtWidgets.QPushButton("Simulate Physics on This Drive")
        self.btn_sim.setToolTip(
            "Re-plays your drive as a physics simulation: real suspension,\n"
            "body roll and dive, jumps and landings on the ground mesh.\n"
            "Bakes over the playback range (set it to cover your drive);\n"
            "Clear Drive Animation removes it\n"
            "along with the drive. Tune on C_chassis_CTRL > PHYSICS.")
        self.btn_sim.setFocusPolicy(QtCore.Qt.NoFocus)
        self.btn_sim.clicked.connect(self._on_simulate)
        lay.addWidget(self.btn_sim)

    # -----------------------------------------------------------------------

    def start_driving(self):
        if not can_drive():
            cmds.warning("No drivable vehicle rig found. Build a vehicle "
                         "rig first.")
            return
        if vehicle_sim.clear_simulation():
            cmds.warning("Removed the baked physics so you drive the original "
                         "path. Simulate Physics again when you're done.")
        # Open an undo chunk so the whole drive is one undo step.
        cmds.undoInfo(openChunk=True)
        self._begin()
        self.held.clear()
        self.driving = True
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.status.setText("DRIVING (recording): WASD. Esc to stop.")
        self.timer.start()
        self.setFocus()
        self.activateWindow()
        self.raise_()

    def stop_driving(self):
        if not self.driving:
            return
        self.driving = False
        self.timer.stop()
        self.held.clear()
        self.status.setText("Writing keys...")
        QtWidgets.QApplication.processEvents()
        try:
            start, end, n = self._finish()
        finally:
            try:
                cmds.undoInfo(closeChunk=True)
            except Exception:
                pass
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        dents = (self.damage or {}).get("dents", 0)
        self.status.setText(
            (f"Stopped. Keyed frames {start} to {end}"
             + (f", {dents} crash dent(s) baked" if dents else "")
             + ". Press play.") if n else "Stopped. Nothing driven.")

    def _on_simulate(self):
        if self.driving:
            self.stop_driving()
        start = int(cmds.playbackOptions(q=True, min=True))
        end = int(cmds.playbackOptions(q=True, max=True))
        try:
            with _undo_chunk():
                s = vehicle_sim.simulate(start, end)
        except (RuntimeError, ValueError) as e:
            cmds.warning(f"Simulation failed: {e}")
            return
        self.status.setText(f"Physics baked, frames {start} to {end}"
                            + (f", {s['airborne_frames']} in the air"
                               if s["airborne_frames"] else "")
                            + (f", {s['dents']} crash dent(s)"
                               if s.get("dents") else "")
                            + ". Press play.")

    def _on_clear(self):
        if self.driving:
            self.stop_driving()
        with _undo_chunk():
            n = clear_drive_animation(reset_pose=True)
        # Rewind to the start so the next pass begins clean.
        try:
            cmds.currentTime(cmds.playbackOptions(q=True, min=True),
                             edit=True)
        except Exception:
            pass
        self.status.setText(f"Cleared drive animation ({n} channels). "
                            f"Ready for another pass.")

    # -----------------------------------------------------------------------

    def _tick(self):
        if not self.driving or self.state is None:
            return
        self._tick_once()
        spd = self.state["speed"]
        hit = ""
        if self.crash is not None and self.crash.impacts:
            tick, impact = self.crash.impacts[-1]
            if len(self.recorder.rows) - tick < 2 * self.fps:
                hit = f"   CRASH at {impact:.0f}/s"
        self.status.setText(f"REC frame {self.recorder.end}   speed {spd:7.1f}"
                            f"   steer {self.state['steer_deg']:5.1f}°" + hit)

    # ---- key capture ----

    def keyPressEvent(self, event):
        if event.isAutoRepeat():
            return
        if event.key() == QtCore.Qt.Key_Escape:
            self.stop_driving()
            return
        k = _KEY_MAP.get(event.key())
        if k:
            self.held.add(k)
            event.accept()
            return
        super(DriveSession, self).keyPressEvent(event)

    def keyReleaseEvent(self, event):
        if event.isAutoRepeat():
            return
        k = _KEY_MAP.get(event.key())
        if k:
            self.held.discard(k)
            event.accept()
            return
        super(DriveSession, self).keyReleaseEvent(event)

    def closeEvent(self, event):
        self.stop_driving()
        super(DriveSession, self).closeEvent(event)


_drive_window = None


def show():
    """Open the Drive Mode panel."""
    global _drive_window
    for w in QtWidgets.QApplication.topLevelWidgets():
        if w.objectName() == WINDOW_OBJECT_NAME:
            try:
                w.close()
                w.deleteLater()
            except Exception:
                pass
    if not can_drive():
        cmds.warning("No drivable vehicle rig in scene. Build a vehicle "
                     "rig first (it needs C_global_CTRL + odometer).")
    _drive_window = DriveSession(fps=scene_fps())
    _drive_window.show()
    _drive_window.raise_()
    _drive_window.setFocus()
    return _drive_window
