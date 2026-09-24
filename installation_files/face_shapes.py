"""
===============================================================================
 FACE SHAPES - 52 expression dials on one control (ARKit / MediaPipe names)
===============================================================================

 One control, `C_faceShapes_CTRL`, with a 0..1 dial for each of the 52
 standard face-tracking shapes (the set iPhone ARKit, Live Link Face and
 Google MediaPipe all output): eyeBlinkLeft, jawOpen, mouthSmileLeft,
 browInnerUp ... Each dial drives the face rig that's already there, and adds
 on top of anything an animator poses by hand. They're useful for keyframing
 on their own, and they're what live face capture plugs into.

     import face_shapes
     face_shapes.build()          # add (or rebuild) the dials on a built face
     face_shapes.remove()         # take them off again
     face_shapes.SHAPES           # the 52 names, in ARKit order

 What each group drives
   eyes    blink (blink dial), wide / squint (upper / lower lid controls,
           cheek raise), look up / down / in / out (the eye aim controls)
   jaw     open (jaw rotation), forward (jawThrust), left / right (jawSide)
   mouth   close (zip), pucker (pucker), and per side smile, frown, dimple,
           stretch, press, upper lip up, lower lip down, plus funnel, mouth
           left / right, roll and shrug of each lip (the lip master controls)
   brows   inner up, outer up, down (raise / furrow)
   cheeks  puff, squint (puff / cheekRaise)
   nose    sneer (nostril control + a little upper lip)
   tongue  out (the tongue base control)
 Parts the face doesn't have are skipped (a face without the Advanced Face
 lips gets no lip shapes); build() returns what was and wasn't wired.

 Also added here (the face rig upgrade):
   lidFollow  on L/R_blink_CTRL (0..1): the lids follow the eyes when they
              look up and down, like a real eye.
 and the Advanced Face repair for faces built earlier (working mouth corner
 controls, one smile that lifts the cheeks, dead channels hidden).

 Left / Right are the character's own left and right (as in ARKit).
===============================================================================
"""

import json
import math

import maya.cmds as cmds

CTRL = "C_faceShapes_CTRL"
OFFSET = "C_faceShapes_OFFSET"
NODE_SET = "FACE_SHAPES_SET"

SHAPES = (
    "eyeBlinkLeft", "eyeLookDownLeft", "eyeLookInLeft", "eyeLookOutLeft",
    "eyeLookUpLeft", "eyeSquintLeft", "eyeWideLeft",
    "eyeBlinkRight", "eyeLookDownRight", "eyeLookInRight", "eyeLookOutRight",
    "eyeLookUpRight", "eyeSquintRight", "eyeWideRight",
    "jawForward", "jawLeft", "jawRight", "jawOpen",
    "mouthClose", "mouthFunnel", "mouthPucker", "mouthLeft", "mouthRight",
    "mouthSmileLeft", "mouthSmileRight", "mouthFrownLeft", "mouthFrownRight",
    "mouthDimpleLeft", "mouthDimpleRight", "mouthStretchLeft",
    "mouthStretchRight", "mouthRollLower", "mouthRollUpper",
    "mouthShrugLower", "mouthShrugUpper", "mouthPressLeft", "mouthPressRight",
    "mouthLowerDownLeft", "mouthLowerDownRight", "mouthUpperUpLeft",
    "mouthUpperUpRight",
    "browDownLeft", "browDownRight", "browInnerUp", "browOuterUpLeft",
    "browOuterUpRight",
    "cheekPuff", "cheekSquintLeft", "cheekSquintRight",
    "noseSneerLeft", "noseSneerRight",
    "tongueOut",
)

_GROUPS = (("EYES", "eye"), ("JAW", "jaw"), ("MOUTH", "mouth"),
           ("BROWS", "brow"), ("CHEEKS", "cheek"), ("NOSE", "nose"),
           ("TONGUE", "tongue"))

SIDES = (("L", "Left"), ("R", "Right"))

# How far the eyes turn at a full look dial (degrees).
LOOK_UP, LOOK_DOWN, LOOK_SIDE = 25.0, 25.0, 30.0
JAW_OPEN = 28.0


# =============================================================================
# Small helpers
# =============================================================================

def _exists(node, attr=None):
    if not cmds.objExists(node):
        return False
    return attr is None or cmds.attributeQuery(attr, node=node, exists=True)


def _wp(node):
    return cmds.xform(node, q=True, ws=True, t=True)


def _dist(a, b):
    return math.dist(a, b)


def _smooth(t):
    t = max(0.0, min(1.0, t))
    return t * t * (3.0 - 2.0 * t)


def _register(*nodes):
    nodes = [n for n in nodes if n and cmds.objExists(n)]
    if not nodes:
        return
    if not cmds.objExists(NODE_SET):
        cmds.sets(em=True, n=NODE_SET)
    cmds.sets(nodes, add=NODE_SET)


def _is_proxy(plug):
    node, attr = plug.split(".", 1)
    if attr not in (cmds.listAttr(node, userDefined=True) or []):
        return False                       # built-in attrs are never proxies
    try:
        return bool(cmds.addAttr(plug, q=True, usedAsProxy=True))
    except Exception:
        return False


def _shape(name):
    return "%s.%s" % (CTRL, name)


def _drive(plug, shape, value):
    """Add `value` to `plug` at shape = 1 (a linear driven key, summed with
    anything else driving it)."""
    if abs(value) < 1e-9:
        return
    cmds.setDrivenKeyframe(plug, cd=_shape(shape), dv=0.0, v=0.0,
                           itt="linear", ott="linear")
    cmds.setDrivenKeyframe(plug, cd=_shape(shape), dv=1.0, v=value,
                           itt="linear", ott="linear")


def _sum_node(src_plug, lo=None, hi=None):
    """Put a sum between an attribute and everything it drives, so shapes can
    add to it while the attribute keeps working. Returns the sum node (made
    once, reused after)."""
    node, attr = src_plug.split(".", 1)
    name = "%s_%s_shapes_PMA" % (node, attr)
    if cmds.objExists(name):
        return name
    dests = [d for d in cmds.listConnections(src_plug, s=False, d=True,
                                             p=True) or []
             if not _is_proxy(d)]
    if not dests:
        return None
    pma = cmds.createNode("plusMinusAverage", n=name)
    cmds.connectAttr(src_plug, pma + ".input1D[0]")
    out = pma + ".output1D"
    made = [pma]
    if lo is not None and hi is not None:
        cl = cmds.createNode("clamp", n=name.replace("_PMA", "_CLAMP"))
        cmds.setAttr(cl + ".minR", lo)
        cmds.setAttr(cl + ".maxR", hi)
        cmds.connectAttr(out, cl + ".inputR")
        out = cl + ".outputR"
        made.append(cl)
    cmds.addAttr(pma, ln="shapeSource", dt="string")
    cmds.setAttr(pma + ".shapeSource", src_plug, type="string")
    cmds.addAttr(pma, ln="shapeDests", dt="string")
    cmds.setAttr(pma + ".shapeDests", json.dumps([out] + dests),
                 type="string")
    for d in dests:
        cmds.disconnectAttr(src_plug, d)
        cmds.connectAttr(out, d, f=True)
    _register(*made)
    return pma


def _add_to(src_plug, driver_plug, weight, lo=None, hi=None):
    """src_plug's consumers get + driver_plug * weight."""
    node, attr = src_plug.split(".", 1)
    if not _exists(node, attr.split(".")[0]):
        return False
    if lo is None and cmds.attributeQuery(attr, node=node, minExists=True) \
            and cmds.attributeQuery(attr, node=node, maxExists=True):
        lo = cmds.attributeQuery(attr, node=node, minimum=True)[0]
        hi = cmds.attributeQuery(attr, node=node, maximum=True)[0]
    pma = _sum_node(src_plug, lo, hi)
    if not pma:
        return False
    idx = cmds.getAttr(pma + ".input1D", size=True)
    used = cmds.getAttr(pma + ".input1D", multiIndices=True) or []
    idx = (max(used) + 1) if used else idx
    mdl = cmds.createNode("multDoubleLinear",
                          n="%s_%s_shape_MDL" % (node, attr))
    cmds.connectAttr(driver_plug, mdl + ".input1")
    cmds.setAttr(mdl + ".input2", weight)
    cmds.connectAttr(mdl + ".output", "%s.input1D[%d]" % (pma, idx))
    _register(mdl)
    return True


def _shapes_group(ctrl):
    """A group between a control and its parent that the shapes move, so the
    control (and whatever it constrains) rides along."""
    grp = (ctrl[:-len("_CTRL")] if ctrl.endswith("_CTRL") else ctrl) \
        + "_SHAPES"
    if cmds.objExists(grp):
        return grp
    parent = (cmds.listRelatives(ctrl, p=True) or [None])[0]
    grp = cmds.group(em=True, n=grp)
    if parent:
        cmds.parent(grp, parent, r=True)     # identity in the parent's space
    cmds.parent(ctrl, grp, r=True)           # the ctrl keeps its own values
    _register(grp)
    return grp


def _drive_world(grp, shape, vec):
    """Move a shapes group by a WORLD vector at shape = 1 (converted into the
    group's parent space, so tilted controls move the right way)."""
    import maya.api.OpenMaya as om2
    pim = om2.MMatrix(cmds.getAttr(grp + ".parentInverseMatrix[0]"))
    local = om2.MVector(*vec) * pim
    for axis, v in zip("XYZ", (local.x, local.y, local.z)):
        _drive("%s.translate%s" % (grp, axis), shape, v)


# =============================================================================
# Build
# =============================================================================

def has_face():
    return any(_exists(n) for n in ("C_jaw_CTRL", "C_mouth_CTRL",
                                    "L_blink_CTRL", "L_browInner_CTRL"))


def exists():
    return cmds.objExists(CTRL)


def build():
    """Add the 52 shape dials to the face in the scene (rebuilding them if
    they're already there). Returns {"wired": [...], "skipped": [...]}."""
    if not has_face():
        raise RuntimeError("No face rig in the scene. Build a rig with a face "
                           "(and the Advanced Face for lids and lips) first.")
    if exists():
        remove()
    try:
        import advanced_face
        advanced_face.repair_face()
    except Exception as exc:                     # pragma: no cover
        cmds.warning("[faceShapes] corner repair skipped: %s" % exc)

    _make_control()
    wired = set()
    wired |= _wire_eyes()
    wired |= _wire_jaw()
    wired |= _wire_mouth()
    wired |= _wire_brows()
    wired |= _wire_cheeks()
    wired |= _wire_nose()
    wired |= _wire_tongue()
    _wire_lid_follow()
    skipped = [s for s in SHAPES if s not in wired]
    for s in skipped:                  # a dial that would do nothing: hide it
        cmds.setAttr(_shape(s), k=False, cb=False)
    cmds.select(cl=True)
    print("[faceShapes] %d of %d shape dials wired on %s%s" % (
        len(wired), len(SHAPES), CTRL,
        (" (no rig part for: %s)" % ", ".join(skipped)) if skipped else ""))
    return {"wired": [s for s in SHAPES if s in wired], "skipped": skipped}


def _face_frame():
    """Head position and a face size to scale everything by."""
    head = _wp("C_head_BIND_JNT") if _exists("C_head_BIND_JNT") else \
        [0.0, 0.0, 0.0]
    size = None
    if _exists("L_eye_BIND_JNT") and _exists("R_eye_BIND_JNT"):
        size = 2.2 * _dist(_wp("L_eye_BIND_JNT"), _wp("R_eye_BIND_JNT"))
    elif _exists("C_jaw_BIND_JNT"):
        size = 1.5 * _dist(head, _wp("C_jaw_BIND_JNT"))
    return head, size or 20.0


def _make_control():
    head, size = _face_frame()
    ctrl = cmds.curve(n=CTRL, d=1, p=[(-1, 0.6, 0), (1, 0.6, 0), (1, -0.6, 0),
                                       (-1, -0.6, 0), (-1, 0.6, 0)],
                      k=[0, 1, 2, 3, 4])
    cmds.scale(0.18 * size, 0.18 * size, 0.18 * size, ctrl + ".cv[*]")
    shape = cmds.listRelatives(ctrl, s=True)[0]
    cmds.setAttr(shape + ".overrideEnabled", 1)
    cmds.setAttr(shape + ".overrideColor", 17)
    off = cmds.group(ctrl, n=OFFSET)
    cmds.xform(off, ws=True, t=(head[0] + 0.9 * size, head[1] + 0.35 * size,
                                head[2] + 0.4 * size))
    parent = next((p for p in ("ADV_FACE_controls_GRP", "C_head_CTRL")
                   if cmds.objExists(p)), None)
    if parent:
        cmds.parent(off, parent)
    for a in ("tx", "ty", "tz", "rx", "ry", "rz", "sx", "sy", "sz", "v"):
        cmds.setAttr("%s.%s" % (ctrl, a), l=True, k=False, cb=False)
    for label, prefix in _GROUPS:
        cmds.addAttr(ctrl, ln="shapes" + label.title(), nn=label,
                     at="enum", en="----------:", k=True)
        cmds.setAttr("%s.shapes%s" % (ctrl, label.title()), l=True, cb=True,
                     k=False)
        for s in SHAPES:
            if s.startswith(prefix):
                cmds.addAttr(ctrl, ln=s, at="double", min=0, max=1, dv=0,
                             k=True)
    _register(off, ctrl)


# ---- eyes ----------------------------------------------------------------------

def _lid_opening(s):
    up = sorted(cmds.ls("%s_lidUpper_master_*_CTRL" % s, type="transform")
                or [])
    lo = sorted(cmds.ls("%s_lidLower_master_*_CTRL" % s, type="transform")
                or [])
    if up and lo:
        return max(0.05, _dist(_wp(up[len(up) // 2]), _wp(lo[len(lo) // 2])))
    return None


def _wire_eyes():
    wired = set()
    for s, side in SIDES:
        blink = next((("%s.blink" % n) for n in ("%s_blink_CTRL" % s,
                                                 "%s_eye_aim_CTRL" % s)
                      if _exists(n, "blink") and not _is_proxy(
                          "%s.blink" % n)), None)
        if blink and _add_to(blink, _shape("eyeBlink" + side), 1.0, 0.0,
                             1.0):
            wired.add("eyeBlink" + side)
        opening = _lid_opening(s)
        upper, lower = "%s_upperLid_CTRL" % s, "%s_lowerLid_CTRL" % s
        if opening and _exists(upper) and _exists(lower):
            if (_add_to(upper + ".translateY", _shape("eyeWide" + side),
                        0.4 * opening, -1e6, 1e6)
                    and _add_to(lower + ".translateY",
                                _shape("eyeWide" + side), -0.12 * opening,
                                -1e6, 1e6)):
                wired.add("eyeWide" + side)
            if (_add_to(lower + ".translateY", _shape("eyeSquint" + side),
                        0.4 * opening, -1e6, 1e6)
                    and _add_to(upper + ".translateY",
                                _shape("eyeSquint" + side), -0.15 * opening,
                                -1e6, 1e6)):
                if _exists("%s_cheek_CTRL" % s, "cheekRaise"):
                    _add_to("%s_cheek_CTRL.cheekRaise" % s,
                            _shape("eyeSquint" + side), 0.25)
                wired.add("eyeSquint" + side)
        aim = "%s_eye_aim_CTRL" % s
        eye = "%s_eye_BIND_JNT" % s
        if _exists(aim) and _exists(eye):
            # The look-at control's target, and the head-forward target the
            # eye aims at when lookAt is off: both move, so the dials work
            # either way.
            straight = "%s_eye_straight_TGT" % s
            for target in (aim, straight):
                if not _exists(target):
                    continue
                grp = _shapes_group(target)
                d = max(1e-3, _dist(_wp(target), _wp(eye)))
                out = 1.0 if _wp(eye)[0] >= 0.0 else -1.0
                up = d * math.tan(math.radians(LOOK_UP))
                down = d * math.tan(math.radians(LOOK_DOWN))
                across = d * math.tan(math.radians(LOOK_SIDE))
                _drive_world(grp, "eyeLookUp" + side, (0.0, up, 0.0))
                _drive_world(grp, "eyeLookDown" + side, (0.0, -down, 0.0))
                _drive_world(grp, "eyeLookOut" + side,
                             (out * across, 0.0, 0.0))
                _drive_world(grp, "eyeLookIn" + side,
                             (-out * across, 0.0, 0.0))
            wired |= {"eyeLook%s%s" % (k, side)
                      for k in ("Up", "Down", "In", "Out")}
    return wired


def _wire_lid_follow():
    """lidFollow (0..1) on each blink control: as the eye looks up or down,
    the lids travel with it. Measured from the eye joint's real aim (so it
    works for the look-at control, the look dials and capture alike)."""
    import maya.api.OpenMaya as om2
    for s, _ in SIDES:
        blink, eye = "%s_blink_CTRL" % s, "%s_eye_BIND_JNT" % s
        upper, lower = "%s_upperLid_CTRL" % s, "%s_lowerLid_CTRL" % s
        head = "C_head_BIND_JNT"
        opening = _lid_opening(s)
        if not (opening and all(_exists(n) for n in (blink, eye, upper,
                                                     lower, head))):
            continue
        aims = cmds.listConnections(eye, type="aimConstraint", s=True,
                                    d=False) or []
        if aims:
            aim_vec = cmds.getAttr(aims[0] + ".aimVector")[0]
        else:
            aim_vec = (1.0, 0.0, 0.0)
        if not _exists(blink, "lidFollow"):
            cmds.addAttr(blink, ln="lidFollow", at="double", min=0, max=1,
                         dv=0.6, k=True)
        # Head up, in the head joint's own space (world up at rest).
        hm = om2.MMatrix(cmds.getAttr(head + ".worldMatrix"))
        up_local = om2.MVector(0.0, 1.0, 0.0) * hm.inverse()
        fwd = cmds.createNode("vectorProduct", n="%s_lidFollowFwd_VP" % s)
        cmds.setAttr(fwd + ".operation", 3)
        cmds.setAttr(fwd + ".input1", *aim_vec)
        cmds.setAttr(fwd + ".normalizeOutput", 1)
        cmds.connectAttr(eye + ".worldMatrix[0]", fwd + ".matrix")
        upv = cmds.createNode("vectorProduct", n="%s_lidFollowUp_VP" % s)
        cmds.setAttr(upv + ".operation", 3)
        cmds.setAttr(upv + ".input1", up_local.x, up_local.y, up_local.z)
        cmds.setAttr(upv + ".normalizeOutput", 1)
        cmds.connectAttr(head + ".worldMatrix[0]", upv + ".matrix")
        dot = cmds.createNode("vectorProduct", n="%s_lidFollowDot_VP" % s)
        cmds.setAttr(dot + ".operation", 1)
        cmds.connectAttr(fwd + ".output", dot + ".input1")
        cmds.connectAttr(upv + ".output", dot + ".input2")
        # The rest gaze: toward where the look-at control sits at zero (so an
        # eye already posed while this is built doesn't shift the lids).
        held = []
        for ctrl in ("%s_eye_aim_CTRL" % s, "C_eyes_lookAt_CTRL"):
            for a in ("tx", "ty", "tz"):
                plug = "%s.%s" % (ctrl, a)
                if _exists(ctrl) and cmds.getAttr(plug, se=True):
                    held.append((plug, cmds.getAttr(plug)))
                    cmds.setAttr(plug, 0.0)
        rest = cmds.getAttr(dot + ".outputX")
        for plug, value in held:
            cmds.setAttr(plug, value)
        delta = cmds.createNode("plusMinusAverage",
                                n="%s_lidFollowDelta_PMA" % s)
        cmds.setAttr(delta + ".operation", 2)
        cmds.connectAttr(dot + ".outputX", delta + ".input1D[0]")
        cmds.setAttr(delta + ".input1D[1]", rest)
        amount = cmds.createNode("multDoubleLinear",
                                 n="%s_lidFollow_MDL" % s)
        cmds.connectAttr(delta + ".output1D", amount + ".input1")
        cmds.connectAttr(blink + ".lidFollow", amount + ".input2")
        # Fades out as the eye closes, so a blink while looking down still
        # meets in the middle.
        try:
            import advanced_face
            ease = advanced_face.blink_ease(s)
        except ImportError:
            ease = None
        src = (cmds.listConnections(ease + ".inputValue", s=True, d=False,
                                    p=True) or [None])[0] if ease else None
        open_ = cmds.createNode("reverse", n="%s_lidFollowOpen_REV" % s)
        cmds.connectAttr(src or (blink + ".blink"), open_ + ".inputX")
        faded = cmds.createNode("multDoubleLinear",
                                n="%s_lidFollowFade_MDL" % s)
        cmds.connectAttr(amount + ".output", faded + ".input1")
        cmds.connectAttr(open_ + ".outputX", faded + ".input2")
        _register(fwd, upv, dot, delta, amount, open_, faded)
        _add_to(upper + ".translateY", faded + ".output", 1.1 * opening,
                -1e6, 1e6)
        _add_to(lower + ".translateY", faded + ".output", 0.6 * opening,
                -1e6, 1e6)


# ---- jaw --------------------------------------------------------------------------

def _wire_jaw():
    wired = set()
    jaw, auto = "C_jaw_CTRL", "C_jaw_AUTO"
    if _exists(auto):
        _drive(auto + ".rotateX", "jawOpen", JAW_OPEN)
        wired.add("jawOpen")
    if _exists(jaw, "jawThrust") and _add_to(jaw + ".jawThrust",
                                            _shape("jawForward"), 1.0):
        wired.add("jawForward")
    if _exists(jaw, "jawSide"):
        if _add_to(jaw + ".jawSide", _shape("jawLeft"), 1.0):
            wired.add("jawLeft")
        if _add_to(jaw + ".jawSide", _shape("jawRight"), -1.0):
            wired.add("jawRight")
    return wired


# ---- mouth ------------------------------------------------------------------------

def _lip_masters():
    import advanced_face as af
    upper = sorted(cmds.ls("C_lipUpper_master_*_CTRL", type="transform")
                   or [])
    lower = sorted(cmds.ls("C_lipLower_master_*_CTRL", type="transform")
                   or [])
    if len(upper) < 3 or len(lower) < 3:
        return None
    pts = [_wp(c) for c in upper + lower]
    cx = sum(p[0] for p in pts) / len(pts)
    xs = [p[0] for p in pts]
    width = (max(xs) - min(xs)) or 1.0
    info = []
    for row, is_upper in ((upper, True), (lower, False)):
        n = len(row)
        for i, mc in enumerate(row):
            auto = mc.replace("_CTRL", "_AUTO")
            if not cmds.objExists(auto):
                continue
            x = _wp(mc)[0]
            t = af._corner_t(i, n)                 # 0 corner .. 1 centre
            side = (x - cx) / (0.5 * width)        # -1 right .. +1 left
            info.append({
                "auto": auto, "upper": is_upper, "t": t,
                "out": 1.0 if x >= cx else -1.0, "x": x - cx,
                "wide": math.cos(0.5 * math.pi * t),
                "left": _smooth(0.5 + side / 0.7 * 0.5),
            })
    return {"masters": info, "width": width}


def _wire_mouth():
    wired = set()
    mouth = "C_mouth_CTRL"
    if _exists(mouth, "zip") and _add_to(mouth + ".zip", _shape("mouthClose"),
                                        1.0, 0.0, 1.0):
        wired.add("mouthClose")
    if _exists(mouth, "pucker") and _add_to(mouth + ".pucker",
                                           _shape("mouthPucker"), 1.0,
                                           -1.0, 1.0):
        wired.add("mouthPucker")
    lips = _lip_masters()
    if not lips:
        return wired
    w = lips["width"]
    A = 0.25 * w                       # the smile's corner travel
    for m in lips["masters"]:
        auto, t, out, wide = m["auto"], m["t"], m["out"], m["wide"]
        up = m["upper"]
        lift = 0.15 + 0.85 * (wide ** 0.55)
        mid = 0.3 + 0.7 * t
        for side, weight in (("Left", m["left"]), ("Right", 1.0 - m["left"])):
            if weight < 1e-3:
                continue
            k = weight
            # smile: corners up + out, the whole lip bows (like `smile`)
            _drive(auto + ".translateX", "mouthSmile" + side,
                   k * 0.6 * A * wide * out)
            _drive(auto + ".translateY", "mouthSmile" + side, k * A * lift)
            # frown: corners down, pulled in a touch
            _drive(auto + ".translateY", "mouthFrown" + side,
                   -k * 0.9 * A * (0.1 + 0.9 * wide ** 0.7))
            _drive(auto + ".translateX", "mouthFrown" + side,
                   -k * 0.2 * A * wide * out)
            # stretch: corners out and down, the lips thin
            _drive(auto + ".translateX", "mouthStretch" + side,
                   k * 0.45 * A * wide * out)
            _drive(auto + ".translateY", "mouthStretch" + side,
                   -k * 0.35 * A * wide)
            # dimple: the corner tucks back into the cheek
            _drive(auto + ".translateZ", "mouthDimple" + side,
                   -k * 0.35 * A * wide)
            _drive(auto + ".translateX", "mouthDimple" + side,
                   k * 0.15 * A * wide * out)
            # press: the lips squeeze together
            _drive(auto + ".translateY", "mouthPress" + side,
                   k * (-1.0 if up else 1.0) * 0.14 * A * mid)
            _drive(auto + ".translateZ", "mouthPress" + side,
                   -k * 0.06 * A * mid)
            # upper lip up / lower lip down
            if up:
                _drive(auto + ".translateY", "mouthUpperUp" + side,
                       k * 0.5 * A * mid)
            else:
                _drive(auto + ".translateY", "mouthLowerDown" + side,
                       -k * 0.5 * A * mid)
        # mouth shifted to its left / right
        _drive(auto + ".translateX", "mouthLeft", 0.4 * A)
        _drive(auto + ".translateX", "mouthRight", -0.4 * A)
        # funnel: an open 'O', lips pushed forward
        _drive(auto + ".translateX", "mouthFunnel", -m["x"] * 0.25)
        _drive(auto + ".translateZ", "mouthFunnel", 0.22 * w * mid)
        _drive(auto + ".translateY", "mouthFunnel",
               (1.0 if up else -1.0) * 0.14 * A * t)
        # roll each lip in, and shrug each lip up (chin / upper lip raise)
        if up:
            _drive(auto + ".translateZ", "mouthRollUpper", -0.16 * w * mid)
            _drive(auto + ".translateY", "mouthRollUpper", -0.06 * w * mid)
            _drive(auto + ".translateY", "mouthShrugUpper", 0.25 * A * mid)
            _drive(auto + ".translateZ", "mouthShrugUpper", 0.1 * A * t)
        else:
            _drive(auto + ".translateZ", "mouthRollLower", -0.16 * w * mid)
            _drive(auto + ".translateY", "mouthRollLower", 0.06 * w * mid)
            _drive(auto + ".translateY", "mouthShrugLower", 0.4 * A * mid)
            _drive(auto + ".translateZ", "mouthShrugLower", 0.15 * A * t)
    for s, side in SIDES:              # a smile bunches the cheek up a little
        if _exists("%s_cheek_CTRL" % s, "cheekRaise"):
            _add_to("%s_cheek_CTRL.cheekRaise" % s,
                    _shape("mouthSmile" + side), 0.2)
    wired |= {"mouthSmileLeft", "mouthSmileRight", "mouthFrownLeft",
              "mouthFrownRight", "mouthStretchLeft", "mouthStretchRight",
              "mouthDimpleLeft", "mouthDimpleRight", "mouthPressLeft",
              "mouthPressRight", "mouthUpperUpLeft", "mouthUpperUpRight",
              "mouthLowerDownLeft", "mouthLowerDownRight", "mouthLeft",
              "mouthRight", "mouthFunnel", "mouthRollUpper",
              "mouthRollLower", "mouthShrugUpper", "mouthShrugLower"}
    return wired


# ---- brows, cheeks, nose, tongue ------------------------------------------------

def _wire_brows():
    wired = set()
    for s, side in SIDES:
        inner, mid, outer = ("%s_brow%s_CTRL" % (s, p)
                             for p in ("Inner", "Mid", "Outer"))
        # The brow controls' own raise range is generous (a cartoon reach),
        # so a full dial uses part of it.
        if _exists(inner, "raise") and _add_to(inner + ".raise",
                                              _shape("browInnerUp"), 0.7):
            wired.add("browInnerUp")
        down = False
        for ctrl, amt in ((inner, -0.4), (mid, -0.4), (outer, -0.35)):
            if _exists(ctrl, "raise"):
                down |= _add_to(ctrl + ".raise", _shape("browDown" + side),
                                amt)
        if _exists(inner, "furrow"):
            down |= _add_to(inner + ".furrow", _shape("browDown" + side), 0.5)
        if down:
            wired.add("browDown" + side)
        if _exists(outer, "raise") and _add_to(outer + ".raise",
                                              _shape("browOuterUp" + side),
                                              0.7):
            if _exists(mid, "raise"):
                _add_to(mid + ".raise", _shape("browOuterUp" + side), 0.25)
            wired.add("browOuterUp" + side)
    return wired


def _wire_cheeks():
    wired = set()
    for s, side in SIDES:
        cheek = "%s_cheek_CTRL" % s
        if _exists(cheek, "puff") and _add_to(cheek + ".puff",
                                             _shape("cheekPuff"), 1.0):
            wired.add("cheekPuff")
        if _exists(cheek, "cheekRaise") and _add_to(
                cheek + ".cheekRaise", _shape("cheekSquint" + side), 0.6):
            wired.add("cheekSquint" + side)
    return wired


def _wire_nose():
    wired = set()
    head, size = _face_frame()
    lips = _lip_masters()
    for s, side in SIDES:
        nostril = "%s_nostril_CTRL" % s
        if not _exists(nostril):
            continue
        grp = _shapes_group(nostril)
        out = 1.0 if _wp(nostril)[0] >= 0.0 else -1.0
        _drive_world(grp, "noseSneer" + side,
                     (0.02 * size * out, 0.05 * size, 0.0))
        if _exists("%s_cheek_CTRL" % s, "cheekRaise"):
            _add_to("%s_cheek_CTRL.cheekRaise" % s,
                    _shape("noseSneer" + side), 0.25)
        if lips:
            A = 0.25 * lips["width"]
            for m in lips["masters"]:
                wgt = m["left"] if side == "Left" else 1.0 - m["left"]
                if m["upper"] and wgt > 1e-3:
                    _drive(m["auto"] + ".translateY", "noseSneer" + side,
                           wgt * 0.25 * A * (0.3 + 0.7 * m["t"]))
        wired.add("noseSneer" + side)
    return wired


def _wire_tongue():
    base = "C_tongue_01_CTRL"
    tip = next((j for j in ("C_tongue_03_BIND_JNT", "C_tongue_02_BIND_JNT")
                if _exists(j)), None)
    if not (_exists(base) and _exists("C_tongue_01_BIND_JNT") and tip):
        return set()
    length = _dist(_wp("C_tongue_01_BIND_JNT"), _wp(tip))
    grp = _shapes_group(base)
    _drive_world(grp, "tongueOut", (0.0, -0.15 * length, 1.1 * length))
    return {"tongueOut"}


# =============================================================================
# Remove
# =============================================================================

def remove():
    """Take the shape dials off: every sum is unwound back to its original
    connections, driven keys and helper nodes are deleted, controls moved
    back under their original parents."""
    if not cmds.objExists(NODE_SET) and not exists():
        return 0
    # Dials to zero first, so everything they drove settles back at rest
    # before the drivers go (a deleted driven key leaves its last value).
    reset()
    curves, driven = set(), []
    if exists():
        for s in SHAPES:
            for c in cmds.listConnections(_shape(s), s=False, d=True) or []:
                if cmds.nodeType(c).startswith("animCurve"):
                    curves.add(c)
                    driven += cmds.listConnections(c + ".output", s=False,
                                                   d=True, p=True) or []
    for plug in driven:
        try:
            cmds.getAttr(plug)
        except Exception:
            pass
    members = cmds.sets(NODE_SET, q=True) if cmds.objExists(NODE_SET) else []
    members = members or []
    # Unwind sums.
    for node in members:
        if not cmds.objExists(node) or not _exists(node, "shapeDests"):
            continue
        src = cmds.getAttr(node + ".shapeSource")
        dests = json.loads(cmds.getAttr(node + ".shapeDests"))
        out, dests = dests[0], dests[1:]
        for d in dests:
            if not cmds.objExists(d.split(".")[0]):
                continue
            for s_ in cmds.listConnections(d, s=True, d=False, p=True) or []:
                cmds.disconnectAttr(s_, d)
            if cmds.objExists(src.split(".")[0]):
                cmds.connectAttr(src, d, f=True)
    # Controls back out of their shapes groups.
    for node in members:
        if cmds.objExists(node) and node.endswith("_SHAPES"):
            kids = cmds.listRelatives(node, c=True, type="transform") or []
            parent = (cmds.listRelatives(node, p=True) or [None])[0]
            for kid in kids:
                if parent:
                    cmds.parent(kid, parent)
                else:
                    cmds.parent(kid, world=True)
    for blink in ("L_blink_CTRL", "R_blink_CTRL"):
        if _exists(blink, "lidFollow"):
            cmds.deleteAttr(blink + ".lidFollow")
    doomed = [c for c in curves if cmds.objExists(c)]
    doomed += [n for n in members if cmds.objExists(n)]
    if doomed:
        cmds.delete(doomed)
    if cmds.objExists(NODE_SET):
        cmds.delete(NODE_SET)
    return len(doomed)


def reset():
    """All 52 dials back to 0."""
    set_values({s: 0.0 for s in SHAPES})


# Mouth shapes for lip sync, made from the dials (both sides even).
def _both(name, v):
    return {name + "Left": v, name + "Right": v}


VISEMES = {
    "rest": {},
    "AI": dict(jawOpen=0.45, **_both("mouthLowerDown", 0.3),
               **_both("mouthUpperUp", 0.15), **_both("mouthStretch", 0.15)),
    "E": dict(jawOpen=0.2, **_both("mouthStretch", 0.45),
              **_both("mouthSmile", 0.2), **_both("mouthUpperUp", 0.15),
              **_both("mouthLowerDown", 0.2)),
    "O": dict(jawOpen=0.35, mouthFunnel=0.7, mouthPucker=0.2),
    "U": dict(jawOpen=0.1, mouthPucker=0.85, mouthFunnel=0.35),
    "MBP": dict(mouthClose=0.2, mouthRollUpper=0.15, mouthRollLower=0.15,
                **_both("mouthPress", 0.6)),
    "FV": dict(jawOpen=0.05, mouthRollLower=0.6, **_both("mouthUpperUp", 0.25)),
    "L": dict(jawOpen=0.3, **_both("mouthLowerDown", 0.2),
              **_both("mouthUpperUp", 0.1)),
    "etc": dict(jawOpen=0.15, **_both("mouthStretch", 0.25),
                **_both("mouthUpperUp", 0.2), **_both("mouthLowerDown", 0.15)),
}
VISEME_ORDER = ("rest", "AI", "E", "O", "U", "MBP", "FV", "L", "etc")


def apply_viseme(name, key=False):
    """Set the mouth dials to a lip-sync shape (the other mouth / jaw dials
    go to 0 first; eyes and brows are left alone)."""
    if name not in VISEMES:
        raise ValueError("unknown viseme %r, have: %s"
                         % (name, ", ".join(VISEME_ORDER)))
    vals = {s: 0.0 for s in SHAPES if s.startswith(("mouth", "jaw"))}
    vals.update(VISEMES[name])
    return set_values(vals, key=key)


def values():
    """{shape: value} for every dial (empty if there are none)."""
    if not exists():
        return {}
    return {s: cmds.getAttr(_shape(s)) for s in SHAPES}


def set_values(vals, key=False):
    """Set dials from {shape: value} (unknown names are ignored; values are
    clamped to 0..1). key=True also sets a key on each. Returns how many."""
    if not exists():
        return 0
    n = 0
    for s, v in (vals or {}).items():
        if s not in SHAPES:
            continue
        plug = _shape(s)
        if cmds.getAttr(plug, lock=True) or not cmds.getAttr(plug, se=True):
            continue
        cmds.setAttr(plug, max(0.0, min(1.0, float(v))))
        if key:
            cmds.setKeyframe(plug)
        n += 1
    return n
