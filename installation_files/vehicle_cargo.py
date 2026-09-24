"""
===============================================================================
 VEHICLE CARGO - roof racks, spare wheels, jerry cans that move with the ride
===============================================================================

 Select the loose parts on a vehicle (each item its own mesh) and
 add_cargo(). Each item gets a joint and a control pivoting at its base,
 where it's strapped down, riding the body (or whatever part it was bound
 to: the hood, a trailer). Its mesh is re-bound to that joint.

 bake_cargo() then plays the vehicle's real motion (driven, simulated or
 keyed) through a small spring per item:
   * accelerate and it leans back, brake and it leans forward
   * it leans out in turns, and hops a little over bumps and landings
   * it rattles, a bit when idling and more the faster the vehicle goes

 Tune each item on its {item}_cargo_CTRL:
   stiffness  how tightly it's strapped (the spring's bounce, in Hz)
   damping    0 = wobbles for a long time, 1 = settles at once
   maxLean    the most it leans, in degrees
   rattle     vibration (0 = none)
   bounce     how much it hops over bumps
   physics    KEYABLE 0..1: how much of the bake shows
 The control itself animates on top (a loose strap, a hand-keyed shove).

 Drive Mode and Simulate Physics bake the cargo for you when they finish.

     import vehicle_cargo
     vehicle_cargo.add_cargo(["spareTire", "jerryCan"])
     vehicle_cargo.bake_cargo()          # playback range + the drive's keys
===============================================================================
"""

import json
import math
import random
import re

import maya.cmds as cmds
import maya.api.OpenMaya as om2

from character_rig_builder import (COLOR_IK, create_square_ctrl,
                                   lock_hide_attrs)

TAG = "cargoItem"                   # on the mesh: the item's name
TOP = "C_cargo_GRP"
BODY_JOINT = "C_chassis_BIND_JNT"
_WHEEL_JOINT = re.compile(
    r"_(hub|spoke|spring|tread|leaf|shackle|shockBody|shockRod|axle)_", re.I)
_BAKED = ("bakeRx", "bakeRz", "bakeTy")

# (attr, default, min, max) on each {item}_cargo_CTRL.
SETTINGS = (
    ("stiffness", 2.5, 0.3, 12.0),
    ("damping", 0.2, 0.02, 1.0),
    ("maxLean", 15.0, 0.0, 60.0),
    ("rattle", 0.6, 0.0, 3.0),
    ("bounce", 0.5, 0.0, 2.0),
)


# =============================================================================
# Names and scene queries
# =============================================================================

def clean_name(name):
    name = re.sub(r"[^A-Za-z0-9_]", "_", name.split("|")[-1]).strip("_")
    return name or "cargo"


def nodes(item):
    """(ctrl, offset, sim, joint) names of an item."""
    return ("%s_cargo_CTRL" % item, "%s_cargo_OFFSET" % item,
            "%s_cargo_SIM" % item, "%s_cargo_BIND_JNT" % item)


def list_cargo():
    """{item name: mesh} for every mesh added as cargo."""
    out = {}
    for m in cmds.ls("*." + TAG, o=True, long=True) or []:
        if cmds.nodeType(m) == "transform":
            out[cmds.getAttr(m + "." + TAG)] = m
    return out


def is_built(item):
    return cmds.objExists(nodes(item)[0])


def _skin(mesh):
    for h in cmds.listHistory(mesh, pdo=True) or []:
        if cmds.nodeType(h) == "skinCluster":
            return h
    return None


def _unbind(mesh):
    sc = _skin(mesh)
    if sc:
        cmds.skinCluster(sc, e=True, ub=True)


def _carrier(mesh):
    """The joint an item rides: the vehicle joint its mesh is mostly bound
    to (the body, the hood, a trailer, a part), else the body joint."""
    sc = _skin(mesh)
    if sc:
        import maya.api.OpenMayaAnim as oma2
        sel = om2.MSelectionList()
        sel.add(sc)
        fn = oma2.MFnSkinCluster(sel.getDependNode(0))
        shape = cmds.listRelatives(mesh, s=True, ni=True, f=True)[0]
        path = om2.MSelectionList().add(shape).getDagPath(0)
        comp = om2.MFnSingleIndexedComponent()
        cobj = comp.create(om2.MFn.kMeshVertComponent)
        comp.setCompleteData(om2.MFnMesh(path).numVertices)
        flat, n_inf = fn.getWeights(path, cobj)
        weights = {}
        for k, infl in enumerate(fn.influenceObjects()):
            j = infl.partialPathName()
            if _WHEEL_JOINT.search(j) or j.endswith("_cargo_BIND_JNT"):
                continue
            weights[j] = sum(flat[k::n_inf])
        if weights:
            best = max(weights, key=weights.get)
            if weights[best] > 0:
                return best
    return BODY_JOINT


def _rest_box(mesh):
    """World box of the item as modelled (a bound mesh's Orig shape)."""
    import vehicle_guides
    return cmds.exactWorldBoundingBox(vehicle_guides._rest_shape(mesh))


# =============================================================================
# Add / remove
# =============================================================================

def _top():
    if not cmds.objExists(TOP):
        parent = ("controls_GRP" if cmds.objExists("controls_GRP")
                  else "VEHICLE_RIG_GRP" if cmds.objExists("VEHICLE_RIG_GRP")
                  else None)
        cmds.group(em=True, n=TOP, **({"p": parent} if parent else {}))
    return TOP


def _store(mesh, item, carrier, pivot, height, settings):
    for attr, kind in ((TAG, "string"), ("cargoCarrier", "string"),
                       ("cargoSettings", "string")):
        if not cmds.attributeQuery(attr, node=mesh, exists=True):
            cmds.addAttr(mesh, ln=attr, dt=kind)
    cmds.setAttr(mesh + "." + TAG, item, type="string")
    cmds.setAttr(mesh + ".cargoCarrier", carrier, type="string")
    cmds.setAttr(mesh + ".cargoSettings", json.dumps(
        dict(settings, pivot=list(pivot), height=height)), type="string")


def _stored(mesh):
    try:
        return json.loads(cmds.getAttr(mesh + ".cargoSettings") or "{}")
    except (ValueError, RuntimeError):
        return {}


def add_cargo(meshes=None, **settings):
    """Make each mesh (default: the selection) a cargo item. Settings
    (stiffness, damping, maxLean, rattle, bounce) override the defaults.
    Returns the item names."""
    if not cmds.objExists(BODY_JOINT):
        raise RuntimeError("Build a vehicle rig first.")
    if meshes is None:
        meshes = cmds.ls(sl=True, type="transform") or []
    meshes = [m for m in (cmds.ls(meshes, long=True) or [])
              if cmds.listRelatives(m, s=True, type="mesh", ni=True)]
    import rig_export
    out = []
    with rig_export._rest_pose():            # rig as built: bind there
        for m in meshes:
            known = cmds.attributeQuery(TAG, node=m, exists=True)
            item = (cmds.getAttr(m + "." + TAG) if known
                    else clean_name(m))
            data = _stored(m) if known else {}
            carrier = (cmds.getAttr(m + ".cargoCarrier") if known
                       and cmds.objExists(cmds.getAttr(m + ".cargoCarrier"))
                       else _carrier(m))
            if known and is_built(item):
                remove_cargo(item, forget=False)
            bb = _rest_box(m)
            pivot = data.get("pivot") or [0.5 * (bb[0] + bb[3]), bb[1],
                                          0.5 * (bb[2] + bb[5])]
            height = data.get("height") or max(bb[4] - bb[1], 1e-3)
            base = data.get("base") or [max(0.5 * (bb[3] - bb[0]), 1e-3),
                                        max(0.5 * (bb[5] - bb[2]), 1e-3)]
            size = 2.0 * max(base)
            vals = {a: data.get(a, dv) for a, dv, _lo, _hi in SETTINGS}
            vals.update({k: v for k, v in settings.items() if k in vals})
            _build(item, m, carrier, pivot, height, size, vals, base)
            _store(m, item, carrier, pivot, height, dict(vals, base=base))
            out.append(item)
    return out


def _build(item, mesh, carrier, pivot, height, size, vals, base):
    ctrl_n, off_n, sim_n, jnt_n = nodes(item)
    ctrl = create_square_ctrl(ctrl_n, size=1.1 * size, normal=(0, 1, 0),
                              color=COLOR_IK)
    off = cmds.group(em=True, n=off_n, p=_top())
    cmds.xform(off, ws=True, t=pivot)
    sim = cmds.group(em=True, n=sim_n, p=off)
    cmds.parent(ctrl, sim, r=True)
    cmds.parentConstraint(carrier, off, mo=True)
    lock_hide_attrs(ctrl, ("sx", "sy", "sz", "v"))
    cmds.addAttr(ctrl, ln="cargoHeader", at="enum", en="---CARGO---:",
                 k=True)
    cmds.setAttr(ctrl + ".cargoHeader", l=True, cb=True, k=False)
    for attr, dv, lo, hi in SETTINGS:
        cmds.addAttr(ctrl, ln=attr, at="double", dv=vals[attr], min=lo,
                     max=hi)
        cmds.setAttr(ctrl + "." + attr, cb=True)
    cmds.addAttr(ctrl, ln="physics", at="double", dv=1.0, min=0.0, max=1.0,
                 k=True)
    cmds.addAttr(ctrl, ln="comHeight", at="double",
                 dv=max(0.5 * height, 1e-3))
    cmds.addAttr(ctrl, ln="baseHalfWidth", at="double", dv=base[0])
    cmds.addAttr(ctrl, ln="baseHalfDepth", at="double", dv=base[1])
    for attr in _BAKED:
        cmds.addAttr(ctrl, ln=attr, at="double", dv=0.0)
    # The bake (on the control, so it travels with the file) times physics.
    md = cmds.createNode("multiplyDivide", n="%s_cargo_MD" % item)
    for attr, axis in zip(_BAKED, "XYZ"):
        cmds.connectAttr(ctrl + "." + attr, "%s.input1%s" % (md, axis))
        cmds.connectAttr(ctrl + ".physics", "%s.input2%s" % (md, axis))
    cmds.connectAttr(md + ".outputX", sim + ".rotateX")
    cmds.connectAttr(md + ".outputY", sim + ".rotateZ")
    cmds.connectAttr(md + ".outputZ", sim + ".translateY")
    cmds.addAttr(md, ln=TAG, dt="string")
    cmds.setAttr(md + "." + TAG, item, type="string")

    cmds.select(cl=True)
    jnt = cmds.joint(n=jnt_n, p=pivot)
    cmds.parent(jnt, carrier)
    cmds.setAttr(jnt + ".jointOrient", 0, 0, 0)
    cmds.setAttr(jnt + ".radius", max(0.1, 0.15 * size))
    cmds.parentConstraint(ctrl, jnt, mo=True)
    _unbind(mesh)
    cmds.skinCluster(jnt, mesh, tsb=True, mi=1,
                     n=clean_name(mesh) + "_cargoSkin")


def remove_cargo(item, forget=True):
    """Undo add_cargo for `item`: its mesh goes back on the joint it rode
    (the body) and the item's nodes are deleted. With `forget` the mesh
    also stops being cargo (so a vehicle rebuild won't bring it back)."""
    mesh = list_cargo().get(item)
    ctrl, off, _sim, jnt = nodes(item)
    carrier = BODY_JOINT
    if mesh:
        c = cmds.getAttr(mesh + ".cargoCarrier")
        carrier = c if cmds.objExists(c) else BODY_JOINT
        _unbind(mesh)
    for n in cmds.ls("%s_cargo_MD" % item) + [jnt, off]:
        if cmds.objExists(n):
            cmds.delete(n)
    if mesh and forget:
        if cmds.objExists(carrier):
            cmds.skinCluster(carrier, mesh, tsb=True, mi=1,
                             n=clean_name(mesh) + "_skinCluster")
        for attr in (TAG, "cargoCarrier", "cargoSettings"):
            if cmds.attributeQuery(attr, node=mesh, exists=True):
                cmds.deleteAttr(mesh + "." + attr)
    return bool(mesh)


def rebuild_cargo():
    """After Build Vehicle Rig: bring every cargo item back (same pivot,
    carrier and settings). Returns the item names."""
    meshes = list(list_cargo().values())
    if not meshes or not cmds.objExists(BODY_JOINT):
        return []
    for m in meshes:           # the old rig's joints went with the old rig
        if not is_built(cmds.getAttr(m + "." + TAG)):
            _unbind(m)
    return add_cargo(meshes)


# =============================================================================
# The bake
# =============================================================================

def _range():
    lo = cmds.playbackOptions(q=True, min=True)
    hi = cmds.playbackOptions(q=True, max=True)
    for chan in ("translateX", "translateY", "translateZ", "rotateX",
                 "rotateY", "rotateZ"):
        keys = cmds.keyframe("C_global_CTRL." + chan, q=True) or [] \
            if cmds.objExists("C_global_CTRL") else []
        if keys:
            lo, hi = min(lo, min(keys)), max(hi, max(keys))
    return int(math.floor(lo)), int(math.ceil(hi))


def _fps():
    return om2.MTime(1.0, om2.MTime.kSeconds).asUnits(om2.MTime.uiUnit())


def _gravity():
    import vehicle_sim
    return vehicle_sim._gravity()


def _rattle_wave(item, t):
    """Smooth, item-specific vibration in about -1..1: a few quick
    frequencies (3..11 Hz) with random phases."""
    rng = random.Random(item)
    waves = [(rng.uniform(3.0, 11.0), rng.uniform(0.0, 2.0 * math.pi))
             for _ in range(4)]
    return [sum(math.sin(2.0 * math.pi * f * tt + p) for f, p in waves) * 0.5
            for tt in t]


def _soft(x, limit):
    return limit * math.tanh(x / limit) if limit > 1e-6 else 0.0


LEAN_PER_G = 5.0      # degrees a cube-shaped item leans per g at stiffness 2.5


def simulate_item(frames_xf, item, stiffness, damping, max_lean, rattle,
                  bounce, com_height, fps, gravity, half_depth=None,
                  half_width=None):
    """The item's lean and hop per frame from the frame its base rides.

    frames_xf: one 16-float world matrix per frame of the item's base
    (its OFFSET: at the pivot, axes = the vehicle's). A tall, narrow item
    tips further than a low, wide one: the lean per g of acceleration
    scales with its height over the half-width of its base in that
    direction, and with 1 / stiffness. Returns
    [(rotateX deg, rotateZ deg, translateY)] per frame."""
    n = len(frames_xf)
    if n < 3:
        return [(0.0, 0.0, 0.0)] * n
    dt = 1.0 / fps
    pos = [(m[12], m[13], m[14]) for m in frames_xf]
    axes = []
    for m in frames_xf:
        row = []
        for r in (0, 4, 8):
            v = (m[r], m[r + 1], m[r + 2])
            ln = math.sqrt(sum(c * c for c in v)) or 1.0
            row.append((v[0] / ln, v[1] / ln, v[2] / ln))
        axes.append(row)
    # velocity and acceleration of the base (central differences, lightly
    # smoothed so frame-to-frame key jitter isn't read as a jolt)
    vel = [[0.0] * 3 for _ in range(n)]
    acc = [[0.0] * 3 for _ in range(n)]
    for i in range(n):
        a, b = max(i - 1, 0), min(i + 1, n - 1)
        for k in range(3):
            vel[i][k] = (pos[b][k] - pos[a][k]) / ((b - a) * dt)
            if 0 < i < n - 1:
                acc[i][k] = (pos[i + 1][k] - 2.0 * pos[i][k]
                             + pos[i - 1][k]) / (dt * dt)
    smooth = [[0.0] * 3 for _ in range(n)]
    for i in range(n):
        lo, hi = max(i - 1, 0), min(i + 1, n - 1)
        for k in range(3):
            smooth[i][k] = sum(acc[j][k] for j in range(lo, hi + 1)) \
                / float(hi - lo + 1)
    # felt acceleration in the vehicle's axes (gravity is always there)
    local = []
    for i in range(n):
        a = (smooth[i][0], smooth[i][1] + gravity, smooth[i][2])
        local.append(tuple(sum(a[k] * axes[i][r][k] for k in range(3))
                           for r in range(3)))
    speed = [math.sqrt(sum(c * c for c in v)) for v in vel]

    w = 2.0 * math.pi * stiffness
    z = damping
    wv = 2.0 * w                                  # stiffer up and down
    zv = max(z, 0.3)
    l = max(com_height, 1e-3)
    per_g = math.radians(LEAN_PER_G) * 2.5 / max(stiffness, 1e-3) / gravity

    def tip(half):
        return min(4.0, max(0.2, l / max(half or l, 1e-3)))

    pitch_k = per_g * tip(half_depth)          # lean per unit acceleration
    roll_k = per_g * tip(half_width)
    wave_a = _rattle_wave(item + "a", [i * dt for i in range(n)])
    wave_b = _rattle_wave(item + "b", [i * dt for i in range(n)])
    wave_c = _rattle_wave(item + "c", [i * dt for i in range(n)])
    # Rattle is the rack shaking the item, passed straight through (the
    # soft strap spring would filter a fast shake out to nothing).
    lean_amp = math.radians(1.0) * rattle
    hop_amp = 0.02 * l * rattle
    sub = 8
    h = dt / sub
    rx = rz = ty = 0.0
    vx = vz = vy = 0.0
    out = []
    for i in range(n):
        ax_, ay_, az_ = local[i]
        level = 0.15 + 0.85 * min(1.0, speed[i] / gravity)   # ~35 km/h
        for _ in range(sub):
            fx = -w * w * az_ * pitch_k
            fz = w * w * ax_ * roll_k
            fy = -(ay_ - gravity) * bounce
            vx += (fx - 2.0 * z * w * vx - w * w * rx) * h
            vz += (fz - 2.0 * z * w * vz - w * w * rz) * h
            vy += (fy - 2.0 * zv * wv * vy - wv * wv * ty) * h
            rx += vx * h
            rz += vz * h
            ty += vy * h
            if ty < -0.05 * l:                     # it can't sink into
                ty, vy = -0.05 * l, max(vy, 0.0)   # the rack
        lim = math.radians(max_lean)
        shake = lean_amp * level
        out.append((math.degrees(_soft(rx + shake * wave_a[i], lim)),
                    math.degrees(_soft(rz + shake * wave_b[i], lim)),
                    min(max(ty + hop_amp * level * wave_c[i], -0.05 * l),
                        0.3 * l)))
    return out


def clear_bake(items=None):
    """Remove the baked cargo motion (hand keys on the controls stay)."""
    n = 0
    for item in (items or list(list_cargo())):
        ctrl = nodes(item)[0]
        if not cmds.objExists(ctrl):
            continue
        for attr in _BAKED:
            plug = "%s.%s" % (ctrl, attr)
            if cmds.keyframe(plug, q=True, keyframeCount=True):
                cmds.cutKey(plug, clear=True)
                n += 1
            cmds.setAttr(plug, 0.0)
    return n


def bake_cargo(start=None, end=None, items=None):
    """Bake every cargo item's lean, hop and rattle over [start, end]
    (default: the playback range plus every keyed frame of the vehicle).
    Returns the items baked."""
    items = [i for i in (items or list(list_cargo())) if is_built(i)]
    if not items:
        return []
    lo, hi = _range()
    start = lo if start is None else int(start)
    end = hi if end is None else int(end)
    if end - start < 2:
        return []
    clear_bake(items)
    fps, g = _fps(), _gravity()
    frames = list(range(start, end + 1))
    for item in items:
        ctrl, off, _sim, _jnt = nodes(item)
        mats = [cmds.getAttr(off + ".worldMatrix[0]", time=f)
                for f in frames]
        res = simulate_item(
            mats, item, *[cmds.getAttr("%s.%s" % (ctrl, a))
                          for a, _dv, _lo, _hi in SETTINGS],
            com_height=cmds.getAttr(ctrl + ".comHeight"), fps=fps,
            gravity=g, half_width=cmds.getAttr(ctrl + ".baseHalfWidth"),
            half_depth=cmds.getAttr(ctrl + ".baseHalfDepth"))
        for k, attr in enumerate(_BAKED):
            plug = "%s.%s" % (ctrl, attr)
            for f, r in zip(frames, res):
                cmds.setKeyframe(plug, t=f, v=r[k])
        mesh = list_cargo().get(item)
        if mesh:                    # keep the tuning for a rebuild
            data = _stored(mesh)
            data.update({a: cmds.getAttr("%s.%s" % (ctrl, a))
                         for a, _dv, _lo, _hi in SETTINGS})
            cmds.setAttr(mesh + ".cargoSettings", json.dumps(data),
                         type="string")
    print("[cargo] Baked %d item(s) over frames %d-%d." % (len(items), start,
                                                          end))
    return items
