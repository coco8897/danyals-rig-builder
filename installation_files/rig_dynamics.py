"""
===============================================================================
 RIG DYNAMICS — secondary motion for tails, ponytails, ears, ropes, legs...
===============================================================================

 Layers nHair dynamics OVER an existing joint chain so it jiggles, swings and
 settles on its own — overlap / follow-through you'd otherwise hand-key.

 The trick that makes it work on an ALREADY-RIGGED chain (an IK/FK leg, a
 driven tail) is that it never fights the rig: a hidden FOLLOW chain tracks
 the rig pose, an nHair curve lags behind it, a hidden DYN chain reads that
 lag, and only the DIFFERENCE (dyn - rig) is added on top of whatever already
 drives each joint. At rest the difference is zero, so the rig is untouched;
 in motion the joints get the overlap. A `dynamics` dial blends it 0..1.

     import rig_dynamics as dyn
     dyn.make_dynamic_chain()              # select the chain root (or all)
     dyn.make_tail_dynamic("C_tail")

 One control per chain (`<name>_dynamics_CTRL`) holds the dials:

     jiggle    0..1   how much overlap is layered on (0 = pure rig pose)
     follow    0..1   how hard the sim snaps back to the rig pose
     stiffness 0..1   resistance to bending
     damping   0..1   how fast the wobble dies down
     drag      0..1   air resistance
     gravity          world pull (shared per nucleus)
     startFrame       frame the sim starts settling from

 Bake the joints (Edit > Keys > Bake Simulation) before export — the dynamic
 nodes don't need to ship, just the motion. Set `dynamics` to 0 to fully
 bypass.
===============================================================================
"""
from __future__ import print_function

import maya.cmds as cmds
import maya.mel as mel

DYN_GRP = "RIG_DYNAMICS_GRP"


# -- chain resolution ---------------------------------------------------------
def _chain_from_root(root):
    chain, cur = [root], root
    while True:
        kids = [k for k in (cmds.listRelatives(cur, c=True, type="joint")
                            or []) if "_corrective_" not in k
                and "_dyn" not in k]
        if not kids:
            break
        cur = kids[0]
        chain.append(cur)
    return chain


def _resolve_chain(joints):
    if isinstance(joints, str):
        joints = [joints]
    if not joints:
        joints = cmds.ls(sl=True, type="joint") or [
            s for s in (cmds.ls(sl=True) or [])
            if cmds.nodeType(s) == "joint"]
    if not joints:
        cmds.warning("[dynamics] select a chain (its root, or all its "
                     "joints) first.")
        return None
    if len(joints) == 1:
        joints = _chain_from_root(joints[0])
    if len(joints) < 3:
        cmds.warning("[dynamics] need at least 3 joints for a dynamic chain "
                     "(got %d)." % len(joints))
        return None
    return joints


def _rot_source(plug):
    """The plug feeding a .rotateX/Y/Z, or None if the channel is free."""
    cons = cmds.listConnections(plug, s=True, d=False, p=True) or []
    return cons[0] if cons else None


def _dup_chain(joints, suffix, parent):
    """A clean FRESH joint chain that mirrors the source chain's shape AND
    orientation frame. Built joint-by-joint rather than `duplicate -po`,
    whose reparented result silently will NOT solve under a spline IK (it
    stays frozen on the curve). Copying the source's local transform +
    jointOrient keeps the frame aligned so the layered rotate-deltas read back
    correctly on the source joints."""
    out = []
    for i, j in enumerate(joints):
        base = j.replace("_BIND_JNT", "").replace("_JNT", "")
        if i:
            cmds.select(out[-1])                       # chain under previous
        else:
            cmds.select(cl=True)
        d = cmds.joint(n="%s%s" % (base, suffix))
        cmds.setAttr("%s.rotateOrder" % d, cmds.getAttr("%s.rotateOrder" % j))
        for at in ("jointOrient", "translate", "rotate"):
            cmds.setAttr("%s.%s" % (d, at),
                         *cmds.getAttr("%s.%s" % (j, at))[0])
        out.append(d)
    if parent:
        cmds.parent(out[0], parent)
    cmds.matchTransform(out[0], joints[0])             # snap root to source
    return out


def world_safe():
    return None


# -- node helpers -------------------------------------------------------------
def _pma(name, a, b, op=1):
    """plusMinusAverage of two inputs (op 1=sum, 2=subtract). a/b are plugs
    or constants. Returns the .output1D plug."""
    n = cmds.createNode("plusMinusAverage", n=name)
    cmds.setAttr("%s.operation" % n, op)
    for i, v in enumerate((a, b)):
        slot = "%s.input1D[%d]" % (n, i)
        if isinstance(v, str):
            cmds.connectAttr(v, slot)
        else:
            cmds.setAttr(slot, v)
    return "%s.output1D" % n


def _mdl(name, a, b):
    n = cmds.createNode("multDoubleLinear", n=name)
    for slot, v in (("input1", a), ("input2", b)):
        if isinstance(v, str):
            cmds.connectAttr(v, "%s.%s" % (n, slot))
        else:
            cmds.setAttr("%s.%s" % (n, slot), v)
    return "%s.output" % n


def _output_curve(before_curves):
    new = [c for c in cmds.ls(type="nurbsCurve") if c not in before_curves]
    for shp in new:
        par = (cmds.listRelatives(shp, p=True) or [None])[0]
        gp = (cmds.listRelatives(par, p=True) or [None])[0] if par else None
        if gp and "OutputCurves" in gp:
            return par, gp
    for shp in new:
        if not cmds.getAttr(shp + ".intermediateObject"):
            return (cmds.listRelatives(shp, p=True) or [None])[0], None
    return None, None


# -- main ---------------------------------------------------------------------
def make_dynamic_chain(joints=None, name=None, dynamics=None, follow=0.05,
                       stiffness=0.15, damping=0.2, drag=0.05, gravity=9.8):
    """Layer nHair dynamics over an existing joint chain. `joints` = ordered
    root..tip, a single root, or None for the selection. Returns a dict of the
    nodes created."""
    joints = _resolve_chain(joints)
    if not joints:
        return None
    root, tip = joints[0], joints[-1]
    parent = (cmds.listRelatives(root, p=True) or [None])[0]
    if name is None:
        name = root.replace("_BIND_JNT", "").replace("_JNT", "")
        name = name.rsplit("_", 1)[0] if name[-1:].isdigit() else name
        name = name or "dynChain"

    # is the chain already driven by the rig? (constraints / connections)
    driven = any(_rot_source("%s.rotate%s" % (j, ax))
                 for j in joints[1:] for ax in "XYZ")
    if dynamics is None:
        dynamics = 0.5 if driven else 1.0

    pts = [cmds.xform(j, q=True, ws=True, t=True) for j in joints]
    deg = 3 if len(pts) >= 4 else 2

    grp = DYN_GRP if cmds.objExists(DYN_GRP) else cmds.group(
        em=True, n=DYN_GRP)

    # FOLLOW chain = the rig pose, jiggle-free (drives the start curve so the
    # whole setup travels with the character). Reads each joint's rig SOURCE,
    # never the joint itself, so layering jiggle back on can't cause a cycle.
    fchain = _dup_chain(joints, "_dynFollow_JNT", grp)
    for fj, sj in list(zip(fchain, joints))[1:]:         # root tracked below
        for ax in "XYZ":
            src = _rot_source("%s.rotate%s" % (sj, ax))
            if src:
                cmds.connectAttr(src, "%s.rotate%s" % (fj, ax), f=True)
    cmds.parentConstraint(root, fchain[0], mo=False)     # FOLLOW tracks rig

    # start curve, skinned to FOLLOW so it tracks the rig
    in_crv = cmds.curve(d=deg, p=pts, n="%s_dynIn_CRV" % name)
    cmds.skinCluster(fchain, in_crv, tsb=True, mi=2,
                     n="%s_dynIn_SKIN" % name)

    before_hs = set(cmds.ls(type="hairSystem"))
    before_fol = set(cmds.ls(type="follicle"))
    before_nuc = set(cmds.ls(type="nucleus"))
    before_crv = set(cmds.ls(type="nurbsCurve"))
    cmds.select(in_crv)
    mel.eval('makeCurvesDynamic 2 { "1", "0", "1", "1", "0"};')

    hs_shape = next(iter(set(cmds.ls(type="hairSystem")) - before_hs), None)
    fol_shape = next(iter(set(cmds.ls(type="follicle")) - before_fol), None)
    nucleus = next(iter(set(cmds.ls(type="nucleus")) - before_nuc),
                   (cmds.ls(type="nucleus") or [None])[0])
    out_crv, out_grp = _output_curve(before_crv)
    if not (hs_shape and fol_shape and out_crv):
        cmds.warning("[dynamics] nHair setup failed — is the nHair plugin "
                     "loaded?")
        return None
    hs = cmds.rename(cmds.listRelatives(hs_shape, p=True)[0],
                     "%s_hairSystem" % name)
    hs_shape = cmds.listRelatives(hs, s=True)[0]
    fol = cmds.rename(cmds.listRelatives(fol_shape, p=True)[0],
                      "%s_follicle" % name)
    fol_shape = cmds.listRelatives(fol, s=True)[0]
    cmds.setAttr("%s.pointLock" % fol_shape, 1)          # base
    cmds.setAttr("%s.startCurveAttract" % hs_shape, follow)
    cmds.setAttr("%s.stiffness" % hs_shape, stiffness)
    cmds.setAttr("%s.damp" % hs_shape, damping)
    cmds.setAttr("%s.drag" % hs_shape, drag)
    if nucleus:
        cmds.setAttr("%s.gravity" % nucleus, gravity)

    # DYN chain rides the SOLVED (lagging) curve via a spline IK
    dynj = _dup_chain(joints, "_dynOut_JNT", grp)
    cmds.pointConstraint(root, dynj[0], mo=False)        # anchor; IK rotates
    ik = cmds.ikHandle(sj=dynj[0], ee=dynj[-1], sol="ikSplineSolver",
                       c=out_crv, ccv=False, pcv=False,
                       n="%s_dyn_IKH" % name)[0]
    cmds.parent(ik, grp)

    # the animator control + dials
    ctrl = cmds.circle(n="%s_dynamics_CTRL" % name, nr=(0, 1, 0),
                       r=_chain_radius(pts), ch=False)[0]
    cmds.matchTransform(ctrl, root, pos=True, rot=False)
    _color(ctrl, 17)
    cmds.addAttr(ctrl, ln="SECONDARY", at="enum", en="____:", k=False)
    cmds.setAttr("%s.SECONDARY" % ctrl, cb=True)
    _drive(ctrl, "jiggle", None, dynamics, 0, 1)         # blend, wired below
    _drive(ctrl, "follow",   "%s.startCurveAttract" % hs_shape, follow, 0, 1)
    _drive(ctrl, "stiffness", "%s.stiffness" % hs_shape, stiffness, 0, 1)
    _drive(ctrl, "damping",  "%s.damp" % hs_shape, damping, 0, 1)
    _drive(ctrl, "drag",     "%s.drag" % hs_shape, drag, 0, 1)
    if nucleus:
        _drive(ctrl, "gravity", "%s.gravity" % nucleus, gravity, 0, None)
        _drive(ctrl, "startFrame", "%s.startFrame" % nucleus,
               cmds.getAttr("%s.startFrame" % nucleus), None, None)
    if parent:
        cmds.parentConstraint(root, ctrl, mo=True)
    amount = "%s.jiggle" % ctrl

    # rest-zero: sample DYN-vs-FOLLOW at the start frame so the layered offset
    # is EXACTLY zero at rest (no matter how the spline IK decomposes the
    # rotation), then layer amount * (live difference - rest difference) on
    # top of whatever already drives each joint below the root.
    if nucleus:
        cmds.currentTime(cmds.getAttr("%s.startFrame" % nucleus))
    layered = 0
    for fj, dj, sj in list(zip(fchain, dynj, joints))[1:]:
        for ax in "XYZ":
            d_plug = "%s.rotate%s" % (dj, ax)
            f_plug = "%s.rotate%s" % (fj, ax)
            rest = cmds.getAttr(d_plug) - cmds.getAttr(f_plug)
            diff = _pma("%s_%s%s_diff" % (name, _short(sj), ax),
                        d_plug, f_plug, op=2)
            zeroed = _pma("%s_%s%s_zero" % (name, _short(sj), ax),
                          diff, rest, op=2)
            scaled = _mdl("%s_%s%s_amt" % (name, _short(sj), ax),
                          zeroed, amount)
            base = _rot_source("%s.rotate%s" % (sj, ax))
            if base:
                cmds.disconnectAttr(base, "%s.rotate%s" % (sj, ax))
            else:
                base = cmds.getAttr("%s.rotate%s" % (sj, ax))
            summed = _pma("%s_%s%s_sum" % (name, _short(sj), ax),
                          base, scaled, op=1)
            cmds.connectAttr(summed, "%s.rotate%s" % (sj, ax), f=True)
            layered += 1

    # tuck the plumbing away
    if out_grp and (cmds.listRelatives(out_grp, p=True) or [None])[0] != grp:
        cmds.parent(out_grp, grp)
    for n in (in_crv, hs, fol):
        if cmds.objExists(n) and (cmds.listRelatives(n, p=True)
                                  or [None])[0] != grp:
            cmds.parent(n, grp)
    cmds.setAttr("%s.visibility" % grp, 0)

    cmds.select(ctrl)
    print("[dynamics] '%s' is %s dynamic (%d joints, %d channels layered). "
          "Dial 'jiggle' on %s; play to settle, then bake the joints "
          "before export." % (name, "now" if not driven else "now (overlay "
          "on the rig)", len(joints), layered, ctrl))
    return {"control": ctrl, "ikHandle": ik, "hairSystem": hs,
            "hairShape": hs_shape, "follicle": fol, "nucleus": nucleus,
            "inCurve": in_crv, "outCurve": out_crv, "followChain": fchain,
            "dynChain": dynj, "joints": joints, "driven": driven}


def _short(j):
    return j.replace("_BIND_JNT", "").replace("_JNT", "").rsplit("_", 1)[-1] \
        or j


def _chain_radius(pts):
    span = max((sum((a - b) ** 2 for a, b in zip(pts[0], pts[-1]))) ** 0.5,
               1.0)
    return max(span * 0.12, 0.5)


def _drive(ctrl, attr, target, dv, lo, hi):
    kw = {"ln": attr, "at": "double", "k": True, "dv": dv}
    if lo is not None:
        kw["min"] = lo
    if hi is not None:
        kw["max"] = hi
    cmds.addAttr(ctrl, **kw)
    if target:
        cmds.connectAttr("%s.%s" % (ctrl, attr), target, f=True)


def _color(node, idx):
    for shp in cmds.listRelatives(node, s=True) or [node]:
        cmds.setAttr("%s.overrideEnabled" % shp, 1)
        cmds.setAttr("%s.overrideColor" % shp, idx)


def make_tail_dynamic(prefix="C_tail"):
    js = cmds.ls("%s_*_BIND_JNT" % prefix, type="joint")
    if not js:
        cmds.warning("[dynamics] no joints match %s_*_BIND_JNT." % prefix)
        return None

    def _idx(j):
        digits = "".join(ch for ch in j.replace("_BIND_JNT", "")
                         if ch.isdigit())
        return int(digits) if digits else 0
    return make_dynamic_chain(sorted(js, key=_idx), name=prefix)
