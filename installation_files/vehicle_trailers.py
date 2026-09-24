"""
===============================================================================
 VEHICLE TRAILERS - trailers towed behind the vehicle rig
===============================================================================

 Up to 3 trailers in a chain (a road train), each with 1 to 3 axles.

   * add_trailer(axles=2)     drop guides for the next trailer in the chain
   * build_trailers()         build them onto the vehicle rig
   * bake_trailers(start, end)  make the trailers follow the vehicle's
                              animation over a frame range

 Each trailer hangs off a hitch on the vehicle (or on the trailer in front)
 and swings behind it like a real one: its axles roll along behind the
 hitch, so a trailer cuts the inside of a turn and reversing jackknifes it
 (up to maxSwing). Trailer wheels are full vehicle wheels: they spin, have
 suspension, follow the ground mesh and squash against it, and each trailer
 pitches and rolls on the terrain from its hitch.

 Where it runs:
   * Drive Mode moves the trailers every tick as you drive
   * Simulate Physics bakes them after the vehicle (the trailer's weight
     doesn't push the vehicle around: trailers follow)
   * bake_trailers() for a hand-keyed vehicle

 Nodes, per trailer i:
   T{i}_hitch_GRP        on the vehicle (or trailer i-1)
   T{i}_root_GRP         follows the hitch (world space)
     T{i}_swing_AUTO     baked swing / pitch / roll (zxy)
       T{i}_trailer_CTRL your own offset on top
         wheels T{i}L1 T{i}R1 ...  (same rig as the vehicle's wheels)
   T{i}_body_BIND_JNT    skin the trailer body here
   T{i}_hitch_BIND_JNT   skin a tow ball / drawbar here (on the vehicle)

 Guides live in VEHICLE_TRAILER_GUIDES_GRP: T{i}_hitch_GUIDE and one
 T{i}_axle{j}_GUIDE per axle, placed on the LEFT wheel centre (the right
 side mirrors). Each axle guide is a circle, the tyre seen side on: its
 `radius` sets the wheel size, so moving the guide never resizes the wheel
 (Fit Wheels to Selected Tyres sets both from the tyre meshes).
===============================================================================
"""

import contextlib
import math
import types

import maya.cmds as cmds
import maya.api.OpenMaya as om2

from character_rig_builder import (
    SCALE, COLOR_CENTER, make_offset_group, lock_hide_attrs,
    create_square_ctrl, create_diamond_ctrl,
)

GUIDES_GRP = "VEHICLE_TRAILER_GUIDES_GRP"
TOP_GRP = "C_trailers_GRP"
NODE_SET = "C_trailers_SET"
MAX_TRAILERS = 3
MAX_AXLES = 3
SWING_CHANNELS = ("rotateX", "rotateY", "rotateZ")
GLOBAL_SCALE = "C_global_CTRL.globalScale"


# =============================================================================
# Guides
# =============================================================================

def _hitch_guide(i):
    return "T%d_hitch_GUIDE" % i


def _axle_guide(i, j):
    return "T%d_axle%d_GUIDE" % (i, j)


def _top():
    if not cmds.objExists(GUIDES_GRP):
        cmds.group(em=True, n=GUIDES_GRP)
        cmds.setAttr(GUIDES_GRP + ".useOutlinerColor", 1)
        cmds.setAttr(GUIDES_GRP + ".outlinerColor", 0.85, 0.64, 0.25)
    return GUIDES_GRP


def _locator(name, pos, color, size=14.0):
    loc = cmds.spaceLocator(n=name)[0]
    for ax in "XYZ":
        cmds.setAttr("%sShape.localScale%s" % (loc, ax), size)
    cmds.setAttr(loc + "Shape.overrideEnabled", 1)
    cmds.setAttr(loc + "Shape.overrideColor", color)
    cmds.xform(loc, ws=True, t=pos)
    for a in ("rx", "ry", "rz", "sx", "sy", "sz"):
        cmds.setAttr("%s.%s" % (loc, a), l=True, k=False, cb=False)
    cmds.parent(loc, _top())
    return loc


def _wpos(node):
    return tuple(cmds.xform(node, q=True, ws=True, t=True))


RADIUS_ATTR = "radius"


def _guide_size():
    """Locator size for the vehicle in the scene (smaller on a small one)."""
    try:
        import vehicle_rig_builder as vrb
        return vrb.vehicle_size()
    except Exception:
        return 1.0


def _axle_locator(i, j, pos, radius):
    """An axle guide: a locator on the left wheel centre with a `radius`
    and a circle of that radius (the tyre)."""
    import vehicle_guides
    loc = _locator(_axle_guide(i, j), pos, 18, size=10.0 * _guide_size())
    vehicle_guides.add_tyre_circle(loc, radius)
    set_axle_radius(loc, radius)
    return loc


def axle_guides():
    """Every trailer axle guide in the scene, trailer by trailer."""
    return [_axle_guide(i, j) for i in range(1, MAX_TRAILERS + 1)
            for j in range(1, MAX_AXLES + 1)
            if cmds.objExists(_axle_guide(i, j))]


def _world_scale(node):
    m = cmds.xform(node, q=True, ws=True, m=True)
    return math.sqrt(sum(v * v for v in m[0:3])) or 1.0


def axle_radius(guide):
    """An axle guide's wheel radius in world units (its circle). A guide
    from before 1.0.3 has no radius: its height above the ground was the
    radius."""
    if cmds.attributeQuery(RADIUS_ATTR, node=guide, exists=True):
        return cmds.getAttr(guide + "." + RADIUS_ATTR) * _world_scale(guide)
    return _wpos(guide)[1]


def set_axle_radius(guide, radius):
    """Set an axle guide's wheel radius (world units)."""
    if not cmds.attributeQuery(RADIUS_ATTR, node=guide, exists=True):
        import vehicle_guides
        vehicle_guides.add_tyre_circle(guide, radius)
    cmds.setAttr(guide + "." + RADIUS_ATTR,
                 max(1e-3, float(radius) / _world_scale(guide)))


def upgrade_axle_guides():
    """Axle guides from before 1.0.3 sized the wheels from their height:
    give them a circle and a radius equal to that height, so nothing
    changes size. Returns how many were upgraded."""
    n = 0
    for guide in axle_guides():
        if not cmds.attributeQuery(RADIUS_ATTR, node=guide, exists=True):
            set_axle_radius(guide, _wpos(guide)[1])
            n += 1
    return n


def ground_gaps(tol=0.1):
    """[(axle guide, how far its tyre bottom is off the ground)] for the
    trailer wheels that won't sit on the ground (y 0) at rest."""
    out = []
    for guide in axle_guides():
        r = axle_radius(guide)
        gap = _wpos(guide)[1] - r
        if abs(gap) > max(0.5 * _guide_size(), tol * r):
            out.append((guide, gap))
    return out


def list_trailers():
    """Trailer specs from the guides, front of the chain first:
    [{"index", "hitch": (x, y, z), "axles": [left wheel centre, ...],
    "radii": [each axle's wheel radius]}] (world units)."""
    out = []
    for i in range(1, MAX_TRAILERS + 1):
        if not cmds.objExists(_hitch_guide(i)):
            break
        axles, radii = [], []
        for j in range(1, MAX_AXLES + 1):
            if not cmds.objExists(_axle_guide(i, j)):
                break
            axles.append(_wpos(_axle_guide(i, j)))
            radii.append(axle_radius(_axle_guide(i, j)))
        out.append({"index": i, "hitch": _wpos(_hitch_guide(i)),
                    "axles": axles, "radii": radii})
    return out


def delete_guides():
    """Remove every trailer guide."""
    if cmds.objExists(GUIDES_GRP):
        cmds.delete(GUIDES_GRP)


def load_guides(specs):
    """Replace the trailer guides with ones made from specs (as
    list_trailers returns them; "radii" optional)."""
    delete_guides()
    for s in specs:
        i = s["index"]
        _locator(_hitch_guide(i), tuple(s["hitch"]), 17,
                 size=14.0 * _guide_size())
        radii = s.get("radii") or [a[1] for a in s["axles"]]
        for j, (a, r) in enumerate(zip(s["axles"], radii), 1):
            _axle_locator(i, j, tuple(a), r)
    cmds.select(cl=True)


@contextlib.contextmanager
def rest_pose():
    """Hold the vehicle at its rest pose (whatever frame you're on) while
    measuring or building, then put the animation back."""
    held = []
    for node in ("C_global_CTRL", "C_cog_CTRL", "C_chassis_CTRL"):
        if not cmds.objExists(node):
            continue
        for ch in ("tx", "ty", "tz", "rx", "ry", "rz"):
            plug = "%s.%s" % (node, ch)
            if cmds.getAttr(plug, lock=True) or not cmds.getAttr(plug, se=True):
                continue
            held.append((plug, cmds.getAttr(plug)))
            cmds.setAttr(plug, 0.0)
    try:
        yield
    finally:
        for plug, value in held:
            if not cmds.keyframe(plug, q=True, keyframeCount=True):
                cmds.setAttr(plug, value)
        cmds.currentTime(cmds.currentTime(q=True), edit=True)


def _scale_is_held():
    """True if C_global_CTRL.globalScale isn't 1 and can't be set to 1 for
    a build (locked or driven)."""
    import vehicle_rig_builder as vrb
    return vrb.rig_scale() != 1.0 and (
        cmds.getAttr(GLOBAL_SCALE, lock=True)
        or not cmds.getAttr(GLOBAL_SCALE, se=True))


@contextlib.contextmanager
def _unit_scale():
    """Hold C_global_CTRL.globalScale at 1 while building onto the rig (the
    build measures in world space, like the vehicle's own build), then put
    it back. Yields the scale it was at."""
    import vehicle_rig_builder as vrb
    scale = vrb.rig_scale()
    if scale != 1.0:
        cmds.setAttr(GLOBAL_SCALE, 1.0)
    try:
        yield scale
    finally:
        if scale != 1.0:
            cmds.setAttr(GLOBAL_SCALE, scale)


def _vehicle_rear():
    """(half track, wheel radius, back-axle z, hub height) of the vehicle
    at rest in world units, or car defaults."""
    try:
        import vehicle_rig_builder as vrb
        wheels = vrb.scene_wheel_prefixes()
    except Exception:
        wheels = []
    if not wheels:
        return 80.0, 40.0, -130.0, 40.0
    back = wheels[-1]
    x, y, z = _wpos(back + "_hub_BIND_JNT")
    radius = vrb.contact_radius(back) * vrb.rig_scale()
    return abs(x), radius, z, y


def add_trailer(axles=2):
    """Guides for the next trailer in the chain, behind the vehicle or the
    last trailer. Returns the trailer number."""
    axles = int(axles)
    if not 1 <= axles <= MAX_AXLES:
        raise ValueError("a trailer has 1 to %d axles" % MAX_AXLES)
    specs = list_trailers()
    if len(specs) >= MAX_TRAILERS:
        raise ValueError("up to %d trailers" % MAX_TRAILERS)
    i = len(specs) + 1
    upgrade_axle_guides()
    with rest_pose():
        half, radius, back_z, hub_y = _vehicle_rear()
    if specs:
        last = specs[-1]
        radius = last["radii"][-1] if last["axles"] else radius
        half = abs(last["axles"][-1][0]) if last["axles"] else half
        hitch = (0.0, last["hitch"][1],
                 (last["axles"][-1][2] if last["axles"] else last["hitch"][2])
                 - 2.4 * radius)
    else:
        hitch = (0.0, hub_y + 0.2 * radius, back_z - 2.2 * radius)
    _locator(_hitch_guide(i), hitch, 17, size=14.0 * _guide_size())
    first = hitch[2] - 6.0 * radius
    for j in range(axles):
        _axle_locator(i, j + 1, (half, radius, first - j * 2.6 * radius),
                      radius)
    cmds.select(cl=True)
    return i


def remove_last_trailer():
    """Delete the guides of the last trailer in the chain. Returns its
    number, or 0."""
    specs = list_trailers()
    if not specs:
        return 0
    i = specs[-1]["index"]
    for node in [_hitch_guide(i)] + [_axle_guide(i, j)
                                     for j in range(1, MAX_AXLES + 1)]:
        if cmds.objExists(node):
            cmds.delete(node)
    return i


def validate(specs):
    for s in specs:
        if not s["axles"]:
            raise ValueError("trailer %d has no axle guides" % s["index"])
        if any(a[2] >= s["hitch"][2] for a in s["axles"]):
            raise ValueError("trailer %d: every axle must be BEHIND its hitch"
                             " (lower Z)" % s["index"])
        if any(abs(a[0]) < 1.0 for a in s["axles"]):
            raise ValueError("trailer %d: put the axle guides on the LEFT "
                             "wheel centre (X away from 0)" % s["index"])


# =============================================================================
# Build
# =============================================================================

def has_trailers():
    return cmds.objExists("T1_trailer_CTRL")


def trailer_count():
    n = 0
    while cmds.objExists("T%d_trailer_CTRL" % (n + 1)):
        n += 1
    return n


def delete_trailers():
    """Remove every built trailer (guides stay)."""
    if not cmds.objExists(NODE_SET):
        return 0
    members = cmds.sets(NODE_SET, q=True) or []
    alive = [m for m in members if cmds.objExists(m)]
    # DAG children go with their parents; delete what's still there.
    for m in alive:
        if cmds.objExists(m):
            try:
                cmds.delete(m)
            except RuntimeError:
                pass
    if cmds.objExists(NODE_SET):
        cmds.delete(NODE_SET)
    return len(alive)


def build_trailers(specs=None):
    """Build the trailers from their guides onto the vehicle rig, replacing
    any built before. Returns the trailer numbers."""
    import vehicle_rig_builder as vrb
    if not cmds.objExists("C_chassis_CTRL"):
        raise RuntimeError("Build a vehicle rig first.")
    if specs is None:
        upgrade_axle_guides()
        specs = list_trailers()
    validate(specs)
    if _scale_is_held():
        raise RuntimeError("C_global_CTRL.globalScale is locked or driven: "
                           "set it to 1 to build trailers.")
    delete_trailers()
    if not specs:
        return []
    before = set(cmds.ls())
    tow_wheels = vrb.scene_wheel_prefixes()
    sample = tow_wheels[0] if tow_wheels else None
    spokes = len(cmds.ls("%s_spoke_*_inner_BIND_JNT" % sample) or []) or 16
    spoke_ctrls = bool(sample and cmds.objExists(sample + "_spoke_01_CTRL"))
    springs = bool(sample and cmds.objExists(sample + "_spring_BIND_JNT"))
    spin = ("C_chassis_spinDriver_ADL.output"
            if cmds.objExists("C_chassis_spinDriver_ADL")
            else "C_chassis_CTRL.odometer")
    ground_y = "%s_decompose.outputTranslateY" % vrb.GROUND_LOC_NAME
    groups = ("controls_GRP", "joints_GRP", "misc_GRP")

    with rest_pose(), _unit_scale() as scale, vrb.sized_controls():
        # The guides sit round the car as you see it (at its globalScale);
        # the rig is built at scale 1, so bring them into rig units.
        specs = [dict(s, hitch=tuple(c / scale for c in s["hitch"]),
                      axles=[tuple(c / scale for c in a) for a in s["axles"]],
                      radii=[r / scale for r in (s.get("radii") or [
                          a[1] for a in s["axles"]])])
                 for s in specs]
        top = cmds.group(em=True, n=TOP_GRP, p="VEHICLE_RIG_GRP")
        cmds.setAttr(top + ".inheritsTransform", 0)
        tow_ctrl, tow_jnt = "C_chassis_CTRL", "C_chassis_BIND_JNT"
        root_jnt = ("C_root_BIND_JNT" if cmds.objExists("C_root_BIND_JNT")
                    else tow_jnt)
        wheels = {}
        for s in specs:
            i = s["index"]
            hx, hy, hz = s["hitch"]
            length = hz - sum(a[2] for a in s["axles"]) / len(s["axles"])
            half = max(abs(a[0]) for a in s["axles"])

            hitch = cmds.group(em=True, n="T%d_hitch_GRP" % i)
            cmds.xform(hitch, ws=True, t=s["hitch"])
            cmds.parent(hitch, tow_ctrl)
            cmds.select(cl=True)
            hitch_jnt = cmds.joint(n="T%d_hitch_BIND_JNT" % i, p=s["hitch"])
            cmds.setAttr(hitch_jnt + ".radius", 0.4 * SCALE)
            cmds.parent(hitch_jnt, tow_jnt)

            root = cmds.group(em=True, n="T%d_root_GRP" % i, p=top)
            dm = cmds.createNode("decomposeMatrix", n="T%d_hitch_DM" % i)
            cmds.connectAttr(hitch + ".worldMatrix[0]", dm + ".inputMatrix")
            for out, inp in (("outputTranslate", "translate"),
                             ("outputRotate", "rotate"),
                             ("outputScale", "scale")):
                cmds.connectAttr("%s.%s" % (dm, out), "%s.%s" % (root, inp))
            swing = cmds.group(em=True, n="T%d_swing_AUTO" % i, p=root)
            cmds.setAttr(swing + ".rotateOrder", 2)              # zxy

            ctrl = create_square_ctrl("T%d_trailer_CTRL" % i,
                                      size=2.6 * half, color=COLOR_CENTER)
            cmds.scale(1.0, 1.0, max(length + 1.5 * s["radii"][-1],
                                     2.0 * half) / (2.6 * half),
                       ctrl + ".cv[*]", r=True)
            cmds.move(0, 0, -0.5 * length, ctrl + ".cv[*]", r=True)
            cmds.xform(ctrl, ws=True, t=s["hitch"])
            offset = make_offset_group(ctrl)
            cmds.parent(offset, swing)
            cmds.setAttr(ctrl + ".rotateOrder", 2)
            lock_hide_attrs(ctrl, ["tx", "ty", "tz", "sx", "sy", "sz", "v"])
            cmds.addAttr(ctrl, ln="trailerHeader", at="enum",
                         en="---TRAILER---:", k=True)
            cmds.setAttr(ctrl + ".trailerHeader", l=True, cb=True, k=False)
            cmds.addAttr(ctrl, ln="maxSwing", at="double", min=0, max=175,
                         dv=80, k=True)
            cmds.addAttr(ctrl, ln="trailerLength", at="double", dv=length)
            cmds.setAttr(ctrl + ".trailerLength", cb=True)
            ball = create_diamond_ctrl("T%d_hitchPoint_CRV" % i,
                                       size=0.25 * SCALE, color=COLOR_CENTER)
            for shp in cmds.listRelatives(ball, s=True) or []:
                cmds.parent(shp, ctrl, r=True, s=True)
            cmds.delete(ball)

            cmds.select(cl=True)
            body_jnt = cmds.joint(n="T%d_body_BIND_JNT" % i, p=s["hitch"])
            cmds.setAttr(body_jnt + ".radius", 0.7 * SCALE)
            cmds.parent(body_jnt, root_jnt)
            cmds.setAttr(body_jnt + ".jointOrient", 0, 0, 0)
            cmds.parentConstraint(ctrl, body_jnt, mo=True)

            trailer_wheels = {}
            for j, (ax, ay, az) in enumerate(s["axles"], 1):
                radius = s["radii"][j - 1]
                for side, sign in (("L", 1.0), ("R", -1.0)):
                    prefix = "T%d%s%d" % (i, side, j)
                    wheel = vrb.VehicleWheel(
                        prefix=prefix, position=(sign * abs(ax), ay, az),
                        radius=radius, is_front=False, is_left=side == "L",
                        chassis_ctrl=ctrl, parent_jnt=body_jnt,
                        ctrl_grp=groups[0], jnt_grp=groups[1],
                        misc_grp=groups[2], chassis_drive_attr=spin,
                        steering_drive_attr=None, ground_y_attr=ground_y,
                        spoke_count=spokes, build_spoke_ctrls=spoke_ctrls,
                        build_spring=springs)
                    wheel.build()
                    trailer_wheels[prefix] = wheel
            wheels.update(trailer_wheels)
            tow_ctrl, tow_jnt = ctrl, body_jnt

        shim = types.SimpleNamespace(
            wheels=wheels, chassis_ctrl="C_chassis_CTRL",
            FOOT_OFFSETS=vrb.VehicleRig.FOOT_OFFSETS)
        vrb.VehicleRig._wire_master_tire_pressure(shim)
        if cmds.attributeQuery("maxCompression", node="C_chassis_CTRL",
                               exists=True):
            vrb.VehicleRig._wire_auto_suspension(shim, ground_y)

    mesh = vrb.assigned_ground_mesh()
    if mesh:
        vrb.assign_ground_mesh(mesh, wheel_prefixes=list(wheels),
                               spoke_count=spokes)
    made = [n for n in set(cmds.ls()) - before if cmds.objExists(n)]
    cmds.select(cl=True)
    cmds.sets(made, n=NODE_SET)
    cmds.select(cl=True)
    print("[vehicle_trailers] Built %d trailer(s), %d wheels."
          % (len(specs), len(wheels)))
    return [s["index"] for s in specs]


# =============================================================================
# Following: the trailer swings behind its hitch
# =============================================================================

def _axes(node):
    """Unit X, Y, Z axes of a node's world matrix (world units, no scale)."""
    m = cmds.xform(node, q=True, ws=True, m=True)
    out = []
    for r in (0, 4, 8):
        v = m[r:r + 3]
        n = math.sqrt(sum(c * c for c in v)) or 1.0
        out.append([c / n for c in v])
    return out


def _wrap(a):
    return (a + math.pi) % (2.0 * math.pi) - math.pi


def _mat_mul(a, b):
    return [[sum(a[r][k] * b[k][c] for k in range(3)) for c in range(3)]
            for r in range(3)]


def _rot_y(a):
    c, s = math.cos(a), math.sin(a)
    return [[c, 0.0, s], [0.0, 1.0, 0.0], [-s, 0.0, c]]


def _rot_x(a):
    c, s = math.cos(a), math.sin(a)
    return [[1.0, 0.0, 0.0], [0.0, c, -s], [0.0, s, c]]


def _rot_z(a):
    c, s = math.cos(a), math.sin(a)
    return [[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]]


def fit_tilt(mounts, need, hitch_y, max_comp, max_droop):
    """Pitch and roll (degrees, zxy rotateX / rotateZ) of a trailer pivoting
    on its hitch so its wheels rest on the ground.

    mounts: wheel mounts (x, y, z) in the trailer's frame, from the hitch
    need:   world Y each hub must reach to touch the ground
    No wheel is left needing more than max_comp of compression, and the
    trailer never hangs with every wheel past max_droop."""
    # need_i - hitch_y - y_i ~ x_i * sin(roll) - z_i * sin(pitch)
    sxx = sxz = szz = sxr = szr = 0.0
    rhs = []
    for (x, y, z), n in zip(mounts, need):
        r = n - hitch_y - y
        rhs.append(r)
        sxx += x * x
        sxz += x * z
        szz += z * z
        sxr += x * r
        szr += z * r
    det = sxx * szz - sxz * sxz
    if abs(det) < 1e-9:
        a = sxr / sxx if sxx > 1e-9 else 0.0
        b = -szr / szz if szz > 1e-9 else 0.0
    else:
        # [sxx -sxz; -sxz szz] [a b] = [sxr, -szr]
        a = (sxr * szz - sxz * szr) / det
        b = (-sxx * szr + sxz * sxr) / det
    limit = math.sin(math.radians(45.0))
    a = max(-limit, min(limit, a))
    b = max(-limit, min(limit, b))
    for _ in range(4):
        res = [r - x * a + z * b for (x, _y, z), r in zip(mounts, rhs)]
        k = max(range(len(res)), key=lambda i: res[i])
        z = mounts[k][2]
        if abs(z) < 1e-6:
            break
        if res[k] > max_comp:
            b += (max_comp - res[k]) / z
        elif res[k] < -max_droop:
            b += (-max_droop - res[k]) / z
        else:
            break
        b = max(-limit, min(limit, b))
    return math.degrees(math.asin(b)), math.degrees(math.asin(a))


def read_trailer_rig():
    """What the follower needs from each built trailer, in world units (the
    hitch and ground it's fitted to are world positions)."""
    import raycast_ground
    import vehicle_rig_builder as vrb
    scale = vrb.rig_scale()
    out = []
    for i in range(1, trailer_count() + 1):
        ctrl = "T%d_trailer_CTRL" % i
        origin = _wpos(ctrl)
        axes = _axes(ctrl)
        wheel_list = []
        for p in _trailer_prefixes(i):
            pos = _wpos(p + "_suspension_OFFSET")
            d = [a - b for a, b in zip(pos, origin)]
            travel = p + "_suspTravel_PMA"
            feet = {}
            for tag in raycast_ground.FOOT_TAGS:
                src = "%s_foot%sSrc_ADL" % (p, tag)
                if cmds.objExists(src):
                    feet[tag] = cmds.getAttr(src + ".input2")
            has_net = cmds.objExists(travel)
            wheel_list.append({
                "prefix": p,
                "mount": tuple(sum(dc * ac for dc, ac in zip(d, ax))
                               for ax in axes),
                "radius": vrb.contact_radius(p) * scale,
                "feet": feet,
                "offset": ((-cmds.getAttr(travel + ".input1D[1]")
                            - cmds.getAttr(travel + ".input1D[3]"))
                           if has_net and cmds.objExists(p + "_suspMount_DM")
                           else 0.0),
            })
        out.append({"index": i, "ctrl": ctrl,
                    "length": cmds.getAttr(ctrl + ".trailerLength") * scale,
                    "wheels": wheel_list})
    return out


def _trailer_prefixes(i):
    import vehicle_rig_builder as vrb
    return [p for p in vrb.trailer_wheel_prefixes()
            if p.startswith("T%d" % i) and p[len("T%d" % i)] in "LR"]


class TrailerFollower(object):
    """Moves the trailers one frame at a time. Call step() after the vehicle
    is posed on the current frame (Drive Mode does, bake_trailers does)."""

    def __init__(self):
        import vehicle_rig_builder as vrb
        import raycast_ground
        self.trailers = read_trailer_rig()
        self.state = [None] * len(self.trailers)     # (hitch, axle) XZ
        self.prev_euler = [None] * len(self.trailers)
        mesh = vrb.assigned_ground_mesh()
        self.mesh_fn = raycast_ground._mesh_fn(mesh) if mesh else None
        self.accel = (self.mesh_fn.autoUniformGridParams()
                      if self.mesh_fn else None)
        self.flat_y = (_wpos(vrb.GROUND_LOC_NAME)[1]
                       if cmds.objExists(vrb.GROUND_LOC_NAME) else 0.0)
        # Travel limits in world units, like the rest of the fit.
        chassis = "C_chassis_CTRL"
        scale = vrb.rig_scale()
        self.max_comp = scale * (
            cmds.getAttr(chassis + ".maxCompression")
            if cmds.attributeQuery("maxCompression", node=chassis,
                                   exists=True) else 20.0)
        self.max_droop = scale * (
            cmds.getAttr(chassis + ".maxDroop")
            if cmds.attributeQuery("maxDroop", node=chassis,
                                   exists=True) else 20.0)

    def ground(self, x, z):
        import raycast_ground
        if self.mesh_fn is None:
            return self.flat_y
        return raycast_ground.raycast_down(self.mesh_fn, x, z,
                                           default=self.flat_y,
                                           accel=self.accel)

    def step(self, key=True):
        """Pose (and key) every trailer on the current frame."""
        for k, t in enumerate(self.trailers):
            i = t["index"]
            swing = "T%d_swing_AUTO" % i
            hitch = _wpos("T%d_hitch_GRP" % i)
            root_axes = _axes("T%d_root_GRP" % i)
            tow_fwd = root_axes[2]
            tow_heading = math.atan2(tow_fwd[0], tow_fwd[2])
            length = max(t["length"], 1e-3)
            hx, hz = hitch[0], hitch[2]
            if self.state[k] is None:
                fwd = _axes(swing)[2]
                heading = math.atan2(fwd[0], fwd[2])
                ax, az = (hx - length * math.sin(heading),
                          hz - length * math.cos(heading))
            else:
                (px, pz), (ax, az) = self.state[k]
                moved = math.hypot(hx - px, hz - pz)
                steps = max(1, int(math.ceil(moved / (0.05 * length))))
                for s in range(1, steps + 1):
                    sx = px + (hx - px) * s / steps
                    sz = pz + (hz - pz) * s / steps
                    dx, dz = ax - sx, az - sz
                    d = math.hypot(dx, dz)
                    if d < 1e-9:
                        dx, dz, d = -tow_fwd[0], -tow_fwd[2], 1.0
                    ax, az = sx + dx / d * length, sz + dz / d * length
                heading = math.atan2(hx - ax, hz - az)
            # A trailer can only swing so far before it hits the vehicle.
            max_swing = math.radians(cmds.getAttr(t["ctrl"] + ".maxSwing"))
            rel = _wrap(heading - tow_heading)
            if abs(rel) > max_swing:
                heading = tow_heading + math.copysign(max_swing, rel)
                ax = hx - length * math.sin(heading)
                az = hz - length * math.cos(heading)
            self.state[k] = ((hx, hz), (ax, az))

            pitch, roll = self._tilt(t, hitch, heading)
            world = _mat_mul(_mat_mul(_rot_y(heading),
                                      _rot_x(math.radians(pitch))),
                             _rot_z(math.radians(roll)))
            root = [[root_axes[c][r] for c in range(3)] for r in range(3)]
            local = _mat_mul([[root[c][r] for c in range(3)]
                              for r in range(3)], world)
            import vehicle_sim
            e = vehicle_sim._euler_zxy(local, self.prev_euler[k])
            self.prev_euler[k] = e
            plugs = ["%s.%s" % (swing, c) for c in SWING_CHANNELS]
            for plug, value in zip(plugs, (e.x, e.y, e.z)):
                cmds.setAttr(plug, math.degrees(value))
            if key:
                cmds.setKeyframe(plugs)

    def _tilt(self, trailer, hitch, heading):
        sin_h, cos_h = math.sin(heading), math.cos(heading)
        mounts, need = [], []
        for w in trailer["wheels"]:
            x, y, z = w["mount"]
            wx = hitch[0] + x * cos_h + z * sin_h
            wz = hitch[2] - x * sin_h + z * cos_h
            best = None
            for tag, geo in w["feet"].items():
                dx = {"Front": 0.7, "Center": 0.0, "Back": -0.7}[tag] \
                    * w["radius"]
                gy = self.ground(wx + dx * sin_h, wz + dx * cos_h) + geo
                best = gy if best is None else max(best, gy)
            if best is None:
                best = self.ground(wx, wz) + w["radius"]
            mounts.append(w["mount"])
            need.append(best + w["offset"])
        if not mounts:
            return 0.0, 0.0
        return fit_tilt(mounts, need, hitch[1], self.max_comp, self.max_droop)


def swing_plugs():
    return ["T%d_swing_AUTO.%s" % (i, c)
            for i in range(1, trailer_count() + 1) for c in SWING_CHANNELS]


def bake_trailers(start=None, end=None):
    """Make the trailers follow the vehicle's animation from start to end
    (default: playback range). Trailers start lined up the way they are on
    the start frame. Returns the frames baked."""
    if not has_trailers():
        return 0
    start = int(cmds.playbackOptions(q=True, min=True)) if start is None \
        else int(start)
    end = int(cmds.playbackOptions(q=True, max=True)) if end is None \
        else int(end)
    cmds.currentTime(start, edit=True)
    follower = TrailerFollower()
    follower.step(key=False)         # start lined up as they are right now
    for plug in swing_plugs():
        cmds.cutKey(plug, clear=True)
    import raycast_ground
    import vehicle_rig_builder as vrb
    # On a ground mesh the trailer wheels sample the ground with the same
    # straight-down rays the trailer was fitted with, keyed per frame
    # (the live closestPointOnMesh reads slopes differently).
    feet = []
    if follower.mesh_fn is not None:
        prefixes = vrb.trailer_wheel_prefixes()
        raycast_ground.prepare_for_raycast(prefixes)
        feet = ["%s_foot%sSrc_ADL.input1" % (p, t) for p in prefixes
                for t in raycast_ground.FOOT_TAGS
                if cmds.objExists("%s_foot%sSrc_ADL" % (p, t))]
        for plug in feet:
            raycast_ground.clear_channel_keys(plug)
    for f in range(start, end + 1):
        cmds.currentTime(f, edit=True)
        follower.step(key=True)
        if feet:
            raycast_ground.sample_footprints(follower.mesh_fn,
                                             prefixes=prefixes)
            cmds.setKeyframe(feet)
    cmds.currentTime(start, edit=True)
    print("[vehicle_trailers] Baked %d trailer(s) over frames %d-%d."
          % (len(follower.trailers), start, end))
    return end - start + 1


def rebake():
    """Re-bake trailers over the frames they're already keyed on (after the
    vehicle's animation changed underneath them)."""
    if not has_trailers():
        return 0
    frames = cmds.keyframe("T1_swing_AUTO.rotateY", q=True) or []
    if not frames:
        return 0
    return bake_trailers(int(min(frames)), int(max(frames)))


def clear_trailer_animation():
    """Remove the trailers' follow keys and line them up again."""
    n = 0
    for plug in swing_plugs():
        if cmds.keyframe(plug, q=True, keyframeCount=True):
            cmds.cutKey(plug, clear=True)
            n += 1
        cmds.setAttr(plug, 0.0)
    return n
