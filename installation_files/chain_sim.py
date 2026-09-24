"""
===============================================================================
 CHAIN SIM - simulate and bake follow-through on tails
===============================================================================

 Like the vehicle's Simulate & Bake, for tails: animate the character however
 you like (hand keys, Walk Mode, Fly Mode), then Simulate & Bake. The tail
 swings when the body speeds up, slows down and turns, then settles, and the
 result is written as keys, so it scrubs, renders and exports exactly.

     import chain_sim
     chain_sim.find_chains()           # {"C_tail": [joints root..tip], ...}
     chain_sim.bake("C_tail")          # the timeline range
     chain_sim.clear("C_tail")         # remove the baked motion
     chain_sim.remove("C_tail")        # take the physics layer off the rig

 How it works
   * Non-destructive: a physics layer sits between the rig and each tail
     joint. The rig's own rotation still drives the joint, and a small
     baked offset is added on top, blended by `physics` (0..1, keyable) on
     the tail's SETTINGS control. At 0 the tail is exactly the animation.
   * The simulation: each joint is a damped angular spring that bends away
     from its animated angle when its pivot accelerates (speeding up,
     slowing down, turning) and springs back. Bends add up down the tail,
     so the tip swings furthest. It's a bake, so the whole animation is
     known: accelerations are measured over neighbouring frames and spread
     evenly, which keeps the motion smooth.
   * Dials, on the SETTINGS control, read at bake time:
        physStiffness   how fast it springs back (wobbles per second)
        physDamping     how quickly the wobble dies away (0..1)
        physSwing       how far it swings
        physWhip        how much looser the tip is than the base (0..1)
   * Works on any tail built by the rig builder: biped tails, quadruped
     and raptor tails, extra tails and custom chains (IK or FK mode).
===============================================================================
"""

import math
import re

import maya.cmds as cmds
import maya.api.OpenMaya as om2

PHYS_GRP = "CHAIN_PHYSICS_GRP"

DEFAULTS = {
    "physStiffness": 2.5,
    "physDamping": 0.35,
    "physSwing": 0.5,
    "physWhip": 0.6,
}
_RANGES = {
    "physStiffness": (0.2, 20.0),
    "physDamping": (0.0, 1.0),
    "physSwing": (0.0, 5.0),
    "physWhip": (0.0, 1.0),
}


# =============================================================================
# Finding tails
# =============================================================================

def find_chains():
    """Tail-style chains built by the rig builder, {prefix: [root .. tip]}:
    joints `<prefix>_01_BIND_JNT` .. `_NN_` plus `<prefix>Tip_BIND_JNT`, with
    a `<prefix>_SETTINGS_CTRL`."""
    chains = {}
    for settings in sorted(cmds.ls("*_SETTINGS_CTRL", type="transform") or []):
        prefix = settings[:-len("_SETTINGS_CTRL")]
        pat = re.compile(r"^%s_(\d+)_BIND_JNT$" % re.escape(prefix))
        numbered = []
        for j in cmds.ls("%s_*_BIND_JNT" % prefix, type="joint") or []:
            m = pat.match(j)
            if m:
                numbered.append((int(m.group(1)), j))
        joints = [j for _, j in sorted(numbered)]
        tip = "%sTip_BIND_JNT" % prefix
        if cmds.objExists(tip):
            joints.append(tip)
        if len(joints) < 3:
            continue
        linked = all((cmds.listRelatives(child, p=True) or [None])[0] == par
                     for par, child in zip(joints, joints[1:]))
        if linked:
            chains[prefix] = joints
    return chains


def settings_ctrl(prefix):
    return "%s_SETTINGS_CTRL" % prefix


def ensure_settings(prefix):
    """The physics dials on the tail's SETTINGS control."""
    ctrl = settings_ctrl(prefix)
    if not cmds.objExists(ctrl):
        raise RuntimeError("%s has no settings control." % prefix)
    if not cmds.attributeQuery("physicsDivider", node=ctrl, exists=True):
        cmds.addAttr(ctrl, ln="physicsDivider", nn="PHYSICS", at="enum",
                     en="--------:", k=True)
        cmds.setAttr("%s.physicsDivider" % ctrl, l=True, cb=True, k=False)
    if not cmds.attributeQuery("physics", node=ctrl, exists=True):
        cmds.addAttr(ctrl, ln="physics", at="double", min=0, max=1, dv=1,
                     k=True)
    for attr, dv in DEFAULTS.items():
        if not cmds.attributeQuery(attr, node=ctrl, exists=True):
            lo, hi = _RANGES[attr]
            cmds.addAttr(ctrl, ln=attr, at="double", min=lo, max=hi, dv=dv)
            cmds.setAttr("%s.%s" % (ctrl, attr), cb=True)
    return ctrl


def read_settings(prefix, overrides=None):
    ctrl = ensure_settings(prefix)
    out = {a: cmds.getAttr("%s.%s" % (ctrl, a)) for a in DEFAULTS}
    for k, v in (overrides or {}).items():
        if k in out:
            cmds.setAttr("%s.%s" % (ctrl, k), v)
            out[k] = v
    return out


# =============================================================================
# The physics layer on the rig
# =============================================================================

def _phys_node(joint):
    return joint.replace("_BIND_JNT", "") + "_PHYS"


def _rot_source(plug):
    cons = cmds.listConnections(plug, s=True, d=False, p=True) or []
    return cons[0] if cons else None


def is_installed(prefix):
    joints = find_chains().get(prefix) or []
    return bool(joints) and all(cmds.objExists(_phys_node(j)) for j in joints)


def install(prefix):
    """Put the physics layer between the rig and each tail joint:

        rig rotation (constraint output) -> composeMatrix  \
                                                             multMatrix ->
        PHYS.rotate x physics            -> composeMatrix  /
            decomposeMatrix -> joint.rotate

    so the joint turns by the rig's rotation, then by the baked offset (in
    the joint's own frame). The PHYS transforms hold the keys."""
    joints = find_chains().get(prefix)
    if not joints:
        raise RuntimeError("No tail chain named %s." % prefix)
    ctrl = ensure_settings(prefix)
    if is_installed(prefix):
        return [_phys_node(j) for j in joints]
    if not cmds.objExists(PHYS_GRP):
        grp = cmds.group(em=True, n=PHYS_GRP)
        for parent in ("misc_GRP",):
            if cmds.objExists(parent):
                cmds.parent(grp, parent)
                break
        cmds.setAttr("%s.visibility" % PHYS_GRP, 0)
    nodes = []
    for j in joints:
        name = _phys_node(j)
        phys = cmds.createNode("transform", n=name, p=PHYS_GRP)
        for ch in ("translate", "scale"):
            for ax in "XYZ":
                cmds.setAttr("%s.%s%s" % (phys, ch, ax), l=True, k=False)
        md = cmds.createNode("multiplyDivide", n=name + "_WEIGHT_MD")
        cmds.connectAttr(phys + ".rotate", md + ".input1")
        for ax in "XYZ":
            cmds.connectAttr(ctrl + ".physics", "%s.input2%s" % (md, ax))
        offset = cmds.createNode("composeMatrix", n=name + "_OFFSET_CM")
        cmds.connectAttr(md + ".output", offset + ".inputRotate")
        base = cmds.createNode("composeMatrix", n=name + "_RIG_CM")
        cmds.connectAttr(j + ".rotateOrder", base + ".inputRotateOrder")
        for ax in "XYZ":
            plug = "%s.rotate%s" % (j, ax)
            src = _rot_source(plug)
            cmds.addAttr(phys, ln="rigSource" + ax, dt="string")
            if src:
                cmds.disconnectAttr(src, plug)
                cmds.connectAttr(src, "%s.inputRotate%s" % (base, ax))
                cmds.setAttr("%s.rigSource%s" % (phys, ax), src,
                             type="string")
            else:
                cmds.setAttr("%s.inputRotate%s" % (base, ax),
                             cmds.getAttr(plug))
        mult = cmds.createNode("multMatrix", n=name + "_MM")
        cmds.connectAttr(base + ".outputMatrix", mult + ".matrixIn[0]")
        cmds.connectAttr(offset + ".outputMatrix", mult + ".matrixIn[1]")
        dm = cmds.createNode("decomposeMatrix", n=name + "_DM")
        cmds.connectAttr(mult + ".matrixSum", dm + ".inputMatrix")
        cmds.connectAttr(j + ".rotateOrder", dm + ".inputRotateOrder")
        for ax in "XYZ":
            cmds.connectAttr("%s.outputRotate%s" % (dm, ax),
                             "%s.rotate%s" % (j, ax), f=True)
        nodes.append(phys)
    return nodes


def remove(prefix):
    """Take the physics layer off: the rig drives the tail directly again."""
    joints = find_chains().get(prefix) or []
    removed = 0
    for j in joints:
        phys = _phys_node(j)
        if not cmds.objExists(phys):
            continue
        for ax in "XYZ":
            plug = "%s.rotate%s" % (j, ax)
            src = cmds.getAttr("%s.rigSource%s" % (phys, ax))
            for s in cmds.listConnections(plug, s=True, d=False, p=True) or []:
                cmds.disconnectAttr(s, plug)
            if src and cmds.objExists(src.split(".")[0]):
                cmds.connectAttr(src, plug, f=True)
        extra = [phys + suffix for suffix in ("_WEIGHT_MD", "_OFFSET_CM",
                                              "_RIG_CM", "_MM", "_DM")]
        for rot in "XYZ":
            cmds.cutKey("%s.rotate%s" % (phys, rot), clear=True)
        cmds.delete([n for n in extra if cmds.objExists(n)] + [phys])
        removed += 1
    if cmds.objExists(PHYS_GRP) and not cmds.listRelatives(PHYS_GRP, c=True):
        cmds.delete(PHYS_GRP)
    return removed


def clear(prefix):
    """Remove the baked tail motion (the layer stays, offsets go to zero)."""
    joints = find_chains().get(prefix) or []
    n = 0
    for j in joints:
        phys = _phys_node(j)
        if not cmds.objExists(phys):
            continue
        for ax in "XYZ":
            plug = "%s.rotate%s" % (phys, ax)
            if cmds.keyframe(plug, q=True, keyframeCount=True):
                cmds.cutKey(plug, clear=True)
                n += 1
            cmds.setAttr(plug, 0.0)
    return n


def is_baked(prefix):
    joints = find_chains().get(prefix) or []
    return any(cmds.keyframe(_phys_node(j) + ".rotateX", q=True,
                             keyframeCount=True)
               for j in joints if cmds.objExists(_phys_node(j)))


# =============================================================================
# The simulation (pure maths, no scene)
# =============================================================================

def simulate(targets, fps=24.0, stiffness=2.5, damping=0.35, swing=0.5,
             whip=0.6, substeps=2):
    """Tail positions per frame: `targets` is [[MVector per joint] per
    frame] of the animation, the result has the same shape with the tail
    swinging. Each bone gets a bend (a rotation vector) off its animated
    direction, a damped angular spring at `stiffness` wobbles per second
    (the tip `whip` looser), pushed the other way by its pivot's
    acceleration across the bone. Bends accumulate down the tail like FK and
    bone lengths never change."""
    n = len(targets[0])
    frames = len(targets)
    lengths = [(targets[0][i + 1] - targets[0][i]).length()
               for i in range(n - 1)]
    levers = [max(1e-3, 0.5 * sum(lengths[i:])) for i in range(n - 1)]
    raw = []
    for f in range(frames):
        a = targets[max(0, f - 1)]
        b = targets[min(frames - 1, f + 1)]
        c = targets[f]
        raw.append([(b[i] - c[i] * 2.0 + a[i]) * (fps * fps)
                    for i in range(n)])
    acc = []
    for f in range(frames):
        lo, hi = max(0, f - 1), min(frames - 1, f + 1)
        acc.append([(raw[lo][i] + raw[f][i] * 2.0 + raw[hi][i]) * 0.25
                    for i in range(n)])
    h = 1.0 / fps / substeps
    bend = [om2.MVector() for _ in range(n - 1)]
    spin = [om2.MVector() for _ in range(n - 1)]
    out = [[om2.MVector(v) for v in targets[0]]]
    for f in range(1, frames):
        a, b = targets[f - 1], targets[f]
        pos = None
        for k in range(1, substeps + 1):
            u = k / float(substeps)
            t = [a[i] + (b[i] - a[i]) * u for i in range(n)]
            pos = [om2.MVector(t[0])]
            carry = om2.MQuaternion()
            for i in range(n - 1):
                bone = t[i + 1] - t[i]
                rig_dir = (bone.normal() if bone.length() > 1e-9
                           else om2.MVector(0.0, 0.0, -1.0))
                frac = i / float(max(1, n - 2))
                w = 2.0 * math.pi * stiffness * (1.0 - 0.5 * whip * frac)
                accel = acc[f - 1][i] + (acc[f][i] - acc[f - 1][i]) * u
                accel = accel - rig_dir * (accel * rig_dir)
                torque = (rig_dir ^ accel) * (-swing / levers[i])
                spin[i] += (torque - bend[i] * (w * w)
                            - spin[i] * (2.0 * damping * w)) * h
                bend[i] += spin[i] * h
                bend[i] -= rig_dir * (bend[i] * rig_dir)
                direction = rig_dir.rotateBy(carry)
                angle = bend[i].length()
                if angle > 1e-9:
                    own = om2.MQuaternion(angle, bend[i].normal())
                    direction = direction.rotateBy(own)
                    carry = carry * own
                pos.append(pos[i] + direction * lengths[i])
        out.append(pos)
    return out


# =============================================================================
# Bake
# =============================================================================

def _rotation(m):
    return om2.MTransformationMatrix(om2.MMatrix(m)).rotation(
        asQuaternion=True).asMatrix()


def _point(m):
    return om2.MVector(m[12], m[13], m[14])


def _scene_fps():
    return om2.MTime(1.0, om2.MTime.kSeconds).asUnits(om2.MTime.uiUnit())


def bake(prefix, start=None, end=None, settings=None, fps=None):
    """Simulate the tail over [start, end] (default: the playback range) and
    key the result on its physics layer. Re-running replaces the old bake.
    Returns a summary dict."""
    joints = find_chains().get(prefix)
    if not joints:
        raise RuntimeError("No tail chain named %s in the scene." % prefix)
    start = int(cmds.playbackOptions(q=True, min=True)) if start is None \
        else int(start)
    end = int(cmds.playbackOptions(q=True, max=True)) if end is None \
        else int(end)
    if end - start < 2:
        raise ValueError("Bake at least 3 frames.")
    fps = fps or _scene_fps()
    params = read_settings(prefix, settings)
    install(prefix)
    clear(prefix)                   # sample the pure animation
    phys = [_phys_node(j) for j in joints]
    host = (cmds.listRelatives(joints[0], p=True) or [None])[0]

    now = cmds.currentTime(q=True)
    frames = list(range(start, end + 1))
    worlds, hosts = [], []
    for f in frames:
        cmds.currentTime(f)
        worlds.append([om2.MMatrix(cmds.getAttr(j + ".worldMatrix"))
                       for j in joints])
        hosts.append(om2.MMatrix(cmds.getAttr(host + ".worldMatrix"))
                     if host else om2.MMatrix())
    targets = [[_point(m) for m in row] for row in worlds]
    sim = simulate(targets, fps=fps, stiffness=params["physStiffness"],
                   damping=params["physDamping"], swing=params["physSwing"],
                   whip=params["physWhip"])

    # Joint frames: local rotation = rotateAxis * rotate * jointOrient.
    axes = [om2.MEulerRotation(*[math.radians(v) for v in
                                 cmds.getAttr(j + ".rotateAxis")[0]]).asMatrix()
            for j in joints]
    orients = [om2.MEulerRotation(*[math.radians(v) for v in
                                    cmds.getAttr(j + ".jointOrient")[0]])
               .asMatrix() for j in joints]
    columns = [[[], [], []] for _ in joints]
    n = len(joints)
    worst = 0.0
    prev = [om2.MEulerRotation() for _ in joints]
    for fi in range(len(frames)):
        world, t, p = worlds[fi], targets[fi], sim[fi]
        parent = _rotation(hosts[fi])
        delta = om2.MMatrix()
        for i in range(n):
            if i < n - 1:
                rig_bone, sim_bone = t[i + 1] - t[i], p[i + 1] - p[i]
                if rig_bone.length() > 1e-9 and sim_bone.length() > 1e-9:
                    delta = om2.MQuaternion(rig_bone, sim_bone).asMatrix()
                else:
                    delta = om2.MMatrix()
            rig_rot = _rotation(world[i])
            want = rig_rot * delta
            inv_parent = parent.inverse()
            inv_axis = axes[i].inverse()
            inv_orient = orients[i].inverse()
            rig_local = inv_axis * rig_rot * inv_parent * inv_orient
            want_local = inv_axis * want * inv_parent * inv_orient
            offset = rig_local.inverse() * want_local
            e = om2.MTransformationMatrix(offset).rotation()
            e = e.closestSolution(prev[i])
            prev[i] = e
            for ax, v in enumerate((e.x, e.y, e.z)):
                columns[i][ax].append(math.degrees(v))
            parent = want
        worst = max(worst, (p[-1] - t[-1]).length())

    for i, node in enumerate(phys):
        for ax, name in enumerate("XYZ"):
            plug = "%s.rotate%s" % (node, name)
            cmds.cutKey(plug, clear=True)
            for f, v in zip(frames, columns[i][ax]):
                cmds.setKeyframe(plug, t=f, v=v)
    cmds.currentTime(now)
    summary = {"prefix": prefix, "frames": len(frames),
               "max_tip_swing": worst, "settings": params}
    print("[chain_sim] Baked %s: %d frames, tip swings up to %.1f units. "
          "Dial it with %s.physics." % (prefix, len(frames), worst,
                                        settings_ctrl(prefix)))
    return summary
