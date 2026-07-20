"""
===============================================================================
 RIG CORRECTIVES — pose-reader-driven helper joints (lightweight PSD)
===============================================================================

 Joints + linear-blend skinning collapse at extreme angles (the candy-wrapper
 shoulder, the pinched elbow / knee). This adds CORRECTIVE helper joints that
 a POSE READER fires as a limb flexes, so you can paint volume back in that
 only shows up when it's needed.

 Reader: the flex MAGNITUDE of a bind joint = the length of its local
 rotation (sqrt(rx^2+ry^2+rz^2)), remapped start..end degrees -> 0..1. It
 fires no matter WHICH way the joint bends, which is what you want for
 volume preservation. No blendshapes, no plugins, no per-frame sim — pure
 dependency graph, so it scrubs and exports cleanly.

     import rig_correctives as rc
     rc.build_biped_correctives()          # shoulders/elbows/hips/knees
     # or one at a time:
     rc.add_corrective("L_arm_elbow_BIND_JNT", "L_elbow",
                       move=(0, 1.5, -1.0), start=15, end=110)

 Paint mesh weights onto the *_corrective_BIND_JNT joints (they're named
 _BIND_JNT so the skinning tools pick them up automatically).
===============================================================================
"""
from __future__ import print_function

import maya.cmds as cmds


def _mdl(name, a, b):
    n = cmds.createNode("multDoubleLinear", n=name)
    for slot, v in (("input1", a), ("input2", b)):
        if isinstance(v, str):
            cmds.connectAttr(v, "%s.%s" % (n, slot))
        else:
            cmds.setAttr("%s.%s" % (n, slot), v)
    return n + ".output"


def add_reader(bind_jnt, name, start=15.0, end=110.0):
    """Build a flex-magnitude pose reader on `bind_jnt`. Returns the 0..1
    output plug (0 at <= `start` deg of bend, 1 at >= `end`)."""
    if not cmds.objExists(bind_jnt):
        cmds.warning("[correctives] %s not found." % bind_jnt)
        return None
    # magnitude = sqrt(rx^2 + ry^2 + rz^2) of the joint's LOCAL rotation
    sq = cmds.createNode("multiplyDivide", n="%s_rdrSq_MLT" % name)
    cmds.setAttr("%s.operation" % sq, 3)             # power
    for ax in "XYZ":
        cmds.connectAttr("%s.rotate%s" % (bind_jnt, ax), "%s.input1%s"
                         % (sq, ax))
        cmds.setAttr("%s.input2%s" % (sq, ax), 2.0)
    summ = cmds.createNode("plusMinusAverage", n="%s_rdrSum_PMA" % name)
    for i, ax in enumerate("XYZ"):
        cmds.connectAttr("%s.output%s" % (sq, ax),
                         "%s.input1D[%d]" % (summ, i))
    root = cmds.createNode("multiplyDivide", n="%s_rdrMag_MLT" % name)
    cmds.setAttr("%s.operation" % root, 3)
    cmds.connectAttr("%s.output1D" % summ, "%s.input1X" % root)
    cmds.setAttr("%s.input2X" % root, 0.5)           # sqrt
    # remap start..end deg -> 0..1
    rmv = cmds.createNode("remapValue", n="%s_reader_RMV" % name)
    cmds.connectAttr("%s.outputX" % root, "%s.inputValue" % rmv)
    cmds.setAttr("%s.inputMin" % rmv, start)
    cmds.setAttr("%s.inputMax" % rmv, end)
    return "%s.outValue" % rmv


def _seg_length(bind_jnt):
    """Distance from a bind joint to its first (non-corrective) child — i.e.
    the length of the bone hanging off it. Used to size correctives to the
    limb so they read the same on a tiny test rig and a 170-unit character."""
    kids = [k for k in (cmds.listRelatives(bind_jnt, c=True, type="joint")
                        or []) if "_corrective_" not in k]
    if not kids:
        return None, 0.0
    p0 = cmds.xform(bind_jnt, q=True, ws=True, t=True)
    p1 = cmds.xform(kids[0], q=True, ws=True, t=True)
    return kids[0], sum((a - b) ** 2 for a, b in zip(p0, p1)) ** 0.5


def add_corrective(bind_jnt, name, move=(0.0, 1.0, 0.0), rotate=(0, 0, 0),
                   start=15.0, end=110.0, reach=0.6, push=0.22):
    """Add a corrective helper CHAIN under `bind_jnt`, driven 0..1 as the
    joint flexes from `start` to `end` degrees.

    The corrective is a real 2-joint bone that runs `reach` x the limb
    segment DOWN the limb (so it covers the muscle, not just a point), and at
    full flex it pushes out by `push` x the segment in the `move` direction
    plus `rotate` degrees. Sizing to the segment keeps it proportional on any
    rig scale. Returns the corrective root joint (`*_corrective_BIND_JNT`)."""
    if not cmds.objExists(bind_jnt):
        cmds.warning("[correctives] %s not found." % bind_jnt)
        return None
    child, seg = _seg_length(bind_jnt)
    if seg < 1e-4:
        seg = max(1.0, cmds.getAttr("%s.radius" % bind_jnt) * 4.0)
    reader = add_reader(bind_jnt, name, start, end)

    # root corrective at the bind joint, sharing its orientation
    cmds.select(cl=True)
    cj = cmds.joint(n="%s_corrective_BIND_JNT" % name)
    cmds.setAttr("%s.radius" % cj,
                 max(0.2, cmds.getAttr("%s.radius" % bind_jnt) * 0.9))
    cmds.matchTransform(cj, bind_jnt)
    cmds.parent(cj, bind_jnt)
    for ax in "XYZ":
        cmds.setAttr("%s.translate%s" % (cj, ax), 0)
        cmds.setAttr("%s.jointOrient%s" % (cj, ax), 0)

    # a TIP child gives the corrective real length: run it `reach` of the way
    # down the bone toward the child joint (where the muscle volume lives)
    tip = cmds.joint(n="%s_correctiveTip_JNT" % name)
    cmds.setAttr("%s.radius" % tip, cmds.getAttr("%s.radius" % cj))
    if child:
        p0 = cmds.xform(bind_jnt, q=True, ws=True, t=True)
        p1 = cmds.xform(child, q=True, ws=True, t=True)
        tgt = [p0[i] + (p1[i] - p0[i]) * reach for i in range(3)]
        cmds.xform(tip, ws=True, t=tgt)
    else:
        cmds.setAttr("%s.translateX" % tip, seg * reach)

    # drive the root's offset off the reader, scaled to the segment length
    for k, ax in enumerate("XYZ"):
        if abs(move[k]) > 1e-6:
            cmds.connectAttr(_mdl("%s_corT%s_MDL" % (name, ax), reader,
                                  move[k] * push * seg),
                             "%s.translate%s" % (cj, ax), f=True)
        if abs(rotate[k]) > 1e-6:
            cmds.connectAttr(_mdl("%s_corR%s_MDL" % (name, ax), reader,
                                  rotate[k]), "%s.rotate%s" % (cj, ax),
                             f=True)
    cmds.select(cl=True)
    return cj


# Default corrective set for a biped: (joint, name, move_dir, start, end).
# move_dir is a UNIT-ish push direction in the joint's local frame; the
# magnitude is auto-scaled to the limb segment inside add_corrective.
_BIPED_CORRECTIVES = [
    ("{s}_arm_shoulder_BIND_JNT", "{s}_shoulder", (0.0, 1.0, 0.0), 20, 95),
    ("{s}_arm_elbow_BIND_JNT",    "{s}_elbow",    (1.0, 0.0, 0.0), 15, 120),
    ("{s}_leg_hip_BIND_JNT",      "{s}_hip",      (0.0, 1.0, 0.6), 20, 95),
    ("{s}_leg_knee_BIND_JNT",     "{s}_knee",     (0.0, 0.0, -1.0), 15, 120),
]


def build_biped_correctives(sides=("L", "R")):
    """Add the standard shoulder / elbow / hip / knee correctives to a built
    biped. Returns the list of corrective joints created."""
    made = []
    for s in sides:
        for jtmpl, ntmpl, move, st, en in _BIPED_CORRECTIVES:
            j = jtmpl.format(s=s)
            if cmds.objExists(j):
                cj = add_corrective(j, ntmpl.format(s=s), move=move,
                                    start=st, end=en)
                if cj:
                    made.append(cj)
    cmds.select(cl=True)
    print("[correctives] added %d corrective joints (%s). Paint weights "
          "onto the *_corrective_BIND_JNT joints." % (len(made),
          ", ".join(made[:4]) + (" ..." if len(made) > 4 else "")))
    return made
