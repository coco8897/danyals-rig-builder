"""
===============================================================================
 VEHICLE PARTS - controllable joints on the vehicle rig
===============================================================================

 Turrets, gun barrels, excavator arms, crane booms, tipper beds: any hinged
 part that rides the vehicle.

   * add_preset("Tank Turret")    drop PART GUIDES for a preset
   * add_hinge(name, ...)         drop a guide for your own hinge
   * build_parts()                build the parts on the vehicle rig

 Each hinge becomes a control (C_{name}_part_CTRL) that rotates about ONE
 axis inside its limits, driving a BIND joint (C_{name}_part_BIND_JNT).
 Parts hang off the vehicle body (so they ride the suspension and the
 physics) or off another part (a stick on a boom, a barrel on a turret).

   * Aim: a turret + barrel can track a target locator. Blend it with the
     aimAtTarget attribute on the part control (0 = hand animated, 1 = aims).
   * Pistons: hydraulic rams between two parts, a cylinder + rod pair that
     stays connected and slides as the arm moves.

 Part guides live in VEHICLE_PARTS_GUIDES_GRP (saved with the scene), so
 rebuilding the vehicle and clicking Build Parts gives the same parts back.
===============================================================================
"""

import re

import maya.cmds as cmds

from character_rig_builder import (
    SCALE, COLOR_IK, COLOR_SETTINGS, make_offset_group, lock_hide_attrs,
    create_circle_ctrl, create_diamond_ctrl,
)

GUIDES_GRP = "VEHICLE_PARTS_GUIDES_GRP"
PARTS_TAG = "vehiclePart"
BODY = "body"
AXES = ("x", "y", "z")

# name, kind ("hinge" | "piston"), guide positions and settings.
PRESETS = {
    "Tank Turret": {
        "about": "Turret that turns on the hull and a barrel that elevates, "
                 "both able to aim at a target locator.",
        "parts": [
            {"name": "turret", "kind": "hinge", "pos": (0, 130, -10),
             "axis": "y", "parent": BODY, "aim": True},
            {"name": "barrel", "kind": "hinge", "pos": (0, 150, 45),
             "axis": "x", "parent": "turret", "min": -25, "max": 10,
             "aim": True, "tip": (0, 150, 260)},
        ],
    },
    "Excavator Arm": {
        "about": "Swinging cab, boom, stick and bucket, with hydraulic rams "
                 "that stay connected.",
        "parts": [
            {"name": "cab", "kind": "hinge", "pos": (0, 110, 0), "axis": "y",
             "parent": BODY},
            {"name": "boom", "kind": "hinge", "pos": (30, 140, 60),
             "axis": "x", "parent": "cab", "min": -60, "max": 30},
            {"name": "stick", "kind": "hinge", "pos": (30, 330, 240),
             "axis": "x", "parent": "boom", "min": -40, "max": 100},
            {"name": "bucket", "kind": "hinge", "pos": (30, 180, 400),
             "axis": "x", "parent": "stick", "min": -80, "max": 80,
             "tip": (30, 110, 450)},
            {"name": "boomRam", "kind": "piston", "pos": (30, 110, 20),
             "end": (30, 230, 130), "parent": "cab", "end_parent": "boom"},
            {"name": "stickRam", "kind": "piston", "pos": (30, 300, 140),
             "end": (30, 360, 230), "parent": "boom", "end_parent": "stick"},
            {"name": "bucketRam", "kind": "piston", "pos": (30, 300, 260),
             "end": (30, 200, 380), "parent": "stick",
             "end_parent": "bucket"},
        ],
    },
    "Tipper Bed": {
        "about": "Dump-truck bed hinged at the back.",
        "parts": [
            {"name": "bed", "kind": "hinge", "pos": (0, 120, -150),
             "axis": "x", "parent": BODY, "min": -55, "max": 0,
             "tip": (0, 120, 100)},
        ],
    },
}


# =============================================================================
# Guides
# =============================================================================

def _guide(name, suffix=""):
    return "%s_part%s_GUIDE" % (name, suffix)


def _top():
    if not cmds.objExists(GUIDES_GRP):
        cmds.group(em=True, n=GUIDES_GRP)
        cmds.setAttr(GUIDES_GRP + ".useOutlinerColor", 1)
        cmds.setAttr(GUIDES_GRP + ".outlinerColor", 0.85, 0.64, 0.25)
    return GUIDES_GRP


def _set(node, attr, value):
    if not cmds.attributeQuery(attr, node=node, exists=True):
        cmds.addAttr(node, ln=attr, dt="string")
    cmds.setAttr("%s.%s" % (node, attr), str(value), type="string")


def _get(node, attr, default=""):
    if cmds.attributeQuery(attr, node=node, exists=True):
        return cmds.getAttr("%s.%s" % (node, attr)) or default
    return default


def _locator(name, pos, color, parent):
    loc = cmds.spaceLocator(n=name)[0]
    for ax in "XYZ":
        cmds.setAttr("%sShape.localScale%s" % (loc, ax), 12.0)
    cmds.setAttr(loc + "Shape.overrideEnabled", 1)
    cmds.setAttr(loc + "Shape.overrideColor", color)
    cmds.xform(loc, ws=True, t=pos)
    for a in ("rx", "ry", "rz", "sx", "sy", "sz"):
        cmds.setAttr("%s.%s" % (loc, a), l=True, k=False, cb=False)
    cmds.parent(loc, parent)
    return loc


def list_parts():
    """Part specs from the guides, in the order they were added."""
    if not cmds.objExists(GUIDES_GRP):
        return []
    out = []
    for loc in cmds.listRelatives(GUIDES_GRP, c=True, type="transform") or []:
        if not loc.endswith("_part_GUIDE"):
            continue
        spec = {"name": _get(loc, "partName"), "kind": _get(loc, "partKind"),
                "parent": _get(loc, "partParent", BODY),
                "pos": tuple(cmds.xform(loc, q=True, ws=True, t=True))}
        if spec["kind"] == "hinge":
            spec["axis"] = _get(loc, "partAxis", "y")
            lo, hi = _get(loc, "partMin"), _get(loc, "partMax")
            spec["min"] = float(lo) if lo not in ("", "None") else None
            spec["max"] = float(hi) if hi not in ("", "None") else None
            spec["aim"] = _get(loc, "partAim") == "1"
            tip = _guide(spec["name"], "Tip")
            if cmds.objExists(tip):
                spec["tip"] = tuple(cmds.xform(tip, q=True, ws=True, t=True))
        else:
            spec["end_parent"] = _get(loc, "partEndParent", BODY)
            spec["end"] = tuple(cmds.xform(_guide(spec["name"], "End"),
                                           q=True, ws=True, t=True))
        out.append(spec)
    return out


def validate(specs):
    """Raise ValueError for a bad set of part specs (names, parents, axis,
    limits)."""
    seen = set()
    for s in specs:
        name = s.get("name", "")
        if not re.match(r"^[A-Za-z][A-Za-z0-9]*$", str(name)):
            raise ValueError("part name must be letters/digits starting with "
                             "a letter, got %r" % (name,))
        if name.lower() in seen:
            raise ValueError("part name %r is used twice" % name)
        if name.lower() in (BODY, "chassis", "global", "steering"):
            raise ValueError("part name %r is reserved" % name)
        for key in ("parent", "end_parent"):
            par = s.get(key)
            if par is None or par == BODY:
                continue
            if par.lower() not in seen:
                raise ValueError("part %r: %s %r must be a part added "
                                 "before it (or 'body')" % (name, key, par))
        if s.get("kind", "hinge") == "hinge":
            if s.get("axis", "y") not in AXES:
                raise ValueError("part %r: axis must be x, y or z" % name)
            lo, hi = s.get("min"), s.get("max")
            if lo is not None and hi is not None and lo > hi:
                raise ValueError("part %r: min limit is above max" % name)
        elif s.get("kind") == "piston":
            if s.get("end") is None:
                raise ValueError("piston %r needs an end position" % name)
        else:
            raise ValueError("part %r: kind must be hinge or piston" % name)
        seen.add(name.lower())


def add_hinge(name, pos, axis="y", parent=BODY, min_angle=None,
              max_angle=None, aim=False, tip=None):
    """Guide for one hinge part. Returns the guide locator."""
    spec = {"name": name, "kind": "hinge", "pos": tuple(pos), "axis": axis,
            "parent": parent, "min": min_angle, "max": max_angle,
            "aim": aim, "tip": tip}
    validate(list_parts() + [spec])
    return _make_guides(spec)


def add_piston(name, pos, end, parent=BODY, end_parent=BODY):
    """Guides for a hydraulic ram from `pos` (on `parent`) to `end` (on
    `end_parent`). Returns the base guide."""
    spec = {"name": name, "kind": "piston", "pos": tuple(pos),
            "end": tuple(end), "parent": parent, "end_parent": end_parent}
    validate(list_parts() + [spec])
    return _make_guides(spec)


def _make_guides(spec):
    loc = _locator(_guide(spec["name"]), spec["pos"], 17, _top())
    _set(loc, "partName", spec["name"])
    _set(loc, "partKind", spec["kind"])
    _set(loc, "partParent", spec.get("parent") or BODY)
    if spec["kind"] == "hinge":
        _set(loc, "partAxis", spec.get("axis", "y"))
        _set(loc, "partMin", spec.get("min"))
        _set(loc, "partMax", spec.get("max"))
        _set(loc, "partAim", int(bool(spec.get("aim"))))
        if spec.get("tip") is not None:
            _locator(_guide(spec["name"], "Tip"), spec["tip"], 18, _top())
    else:
        _set(loc, "partEndParent", spec.get("end_parent") or BODY)
        _locator(_guide(spec["name"], "End"), spec["end"], 18, _top())
    cmds.select(cl=True)
    return loc


def add_preset(name):
    """Guides for every part of a PRESETS entry (all or nothing)."""
    parts = [dict(p) for p in PRESETS[name]["parts"]]
    validate(list_parts() + parts)
    for p in parts:
        _make_guides(p)
    return [p["name"] for p in parts]


def remove_part(name):
    """Delete a part's guides. Parts that hang off it are removed too."""
    specs = list_parts()
    doomed = {name.lower()}
    for s in specs:                                   # dependents, in order
        if (s["parent"].lower() in doomed
                or str(s.get("end_parent", "")).lower() in doomed):
            doomed.add(s["name"].lower())
    removed = []
    for s in specs:
        if s["name"].lower() in doomed:
            for suffix in ("", "Tip", "End"):
                g = _guide(s["name"], suffix)
                if cmds.objExists(g):
                    cmds.delete(g)
            removed.append(s["name"])
    return removed


# =============================================================================
# Build
# =============================================================================

def _body_parents():
    ctrl = "C_body_AUTO" if cmds.objExists("C_body_AUTO") else "C_chassis_CTRL"
    return ctrl, "C_chassis_BIND_JNT"


def _part_nodes(name):
    return "C_%s_part_CTRL" % name, "C_%s_part_BIND_JNT" % name


def _tag(node, name):
    if not cmds.attributeQuery(PARTS_TAG, node=node, exists=True):
        cmds.addAttr(node, ln=PARTS_TAG, dt="string")
    cmds.setAttr("%s.%s" % (node, PARTS_TAG), name, type="string")


def delete_parts():
    """Remove every built part (guides stay)."""
    nodes = cmds.ls("*." + PARTS_TAG, o=True) or []
    tops = [n for n in nodes if cmds.objExists(n) and not any(
        (cmds.listRelatives(n, p=True) or [None])[0] == o for o in nodes)]
    for n in tops:
        if cmds.objExists(n):
            cmds.delete(n)
    return len(tops)


def build_parts(specs=None):
    """Build the parts from their guides onto the vehicle rig. Replaces any
    parts already built. Returns the part names. Controls and joints are
    drawn at the vehicle's size."""
    import vehicle_rig_builder
    with vehicle_rig_builder.sized_controls():
        return _build_parts(specs)


def _build_parts(specs=None):
    if not cmds.objExists("C_chassis_CTRL"):
        raise RuntimeError("Build a vehicle rig first.")
    specs = list_parts() if specs is None else specs
    validate(specs)
    delete_parts()
    body_ctrl, body_jnt = _body_parents()
    ctrl_grp = "controls_GRP" if cmds.objExists("controls_GRP") else None
    built = {}
    aim_target = None

    for s in specs:
        if s["kind"] != "hinge":
            continue
        name = s["name"]
        p_ctrl, p_jnt = ((body_ctrl, body_jnt) if s["parent"] == BODY
                         else built[s["parent"]])
        ctrl_name, jnt_name = _part_nodes(name)
        size = 0.9 * SCALE
        normal = {"x": (1, 0, 0), "y": (0, 1, 0), "z": (0, 0, 1)}[s["axis"]]
        ctrl = create_circle_ctrl(ctrl_name, radius=size, normal=normal,
                                  color=COLOR_IK)
        cmds.xform(ctrl, ws=True, t=s["pos"])
        offset = make_offset_group(ctrl)
        auto = cmds.group(em=True, n="C_%s_part_AUTO" % name)
        cmds.matchTransform(auto, ctrl)
        cmds.parent(auto, offset)
        cmds.parent(ctrl, auto)
        cmds.parent(offset, p_ctrl)
        _tag(offset, name)
        keep = "r" + s["axis"]
        lock_hide_attrs(ctrl, [a for a in ("tx", "ty", "tz", "rx", "ry",
                                           "rz", "sx", "sy", "sz", "v")
                               if a != keep])
        lo, hi = s.get("min"), s.get("max")
        if lo is not None or hi is not None:
            flag = {"x": "rx", "y": "ry", "z": "rz"}[s["axis"]]
            cmds.transformLimits(
                ctrl, **{flag: (lo if lo is not None else -360.0,
                                hi if hi is not None else 360.0),
                         "e" + flag: (lo is not None, hi is not None)})

        cmds.select(cl=True)
        jnt = cmds.joint(n=jnt_name, p=s["pos"])
        cmds.setAttr(jnt + ".radius", 0.5 * SCALE)
        cmds.parent(jnt, p_jnt)
        cmds.setAttr(jnt + ".jointOrient", 0, 0, 0)
        _tag(jnt, name)
        if s.get("tip") is not None:
            cmds.select(cl=True)
            tip = cmds.joint(n="C_%s_partTip_JNT" % name, p=s["tip"])
            cmds.parent(tip, jnt)
            cmds.setAttr(tip + ".jointOrient", 0, 0, 0)
        cmds.parentConstraint(ctrl, jnt, mo=True)
        built[name] = (ctrl, jnt)

        if s.get("aim"):
            aim_target = aim_target or _aim_target(s)
            _wire_aim(name, s["axis"], auto, offset, aim_target, p_ctrl)

    for s in specs:
        if s["kind"] == "piston":
            _build_piston(s, built, body_ctrl, body_jnt, ctrl_grp)

    cmds.select(cl=True)
    print("[vehicle_parts] Built %d part(s): %s"
          % (len(specs), ", ".join(s["name"] for s in specs)))
    return [s["name"] for s in specs]


def _aim_target(spec):
    loc = "C_partAim_LOC"
    if not cmds.objExists(loc):
        loc = cmds.spaceLocator(n=loc)[0]
        x, y, z = spec.get("tip") or spec["pos"]
        cmds.xform(loc, ws=True, t=(x, y, z + 600.0))
        for ax in "XYZ":
            cmds.setAttr("%sShape.localScale%s" % (loc, ax), 25.0)
        cmds.setAttr(loc + "Shape.overrideEnabled", 1)
        cmds.setAttr(loc + "Shape.overrideColor", COLOR_SETTINGS)
        if cmds.objExists("VEHICLE_RIG_GRP"):
            cmds.parent(loc, "VEHICLE_RIG_GRP")
        _tag(loc, "aim")
    return loc


def _wire_aim(name, axis, auto, offset, target, parent_space):
    """AUTO.rotate<axis> = aimAtTarget * (angle that points the part's +Z at
    the target, measured in the part's parent space)."""
    # (A single-axis aimConstraint flips its answer once the target is more
    # than 90 degrees round, so the angle is measured directly instead: the
    # target's position in the part's own space, flattened onto the plane
    # the part turns in, and the signed angle from the part's rest forward.)
    ctrl = "C_%s_part_CTRL" % name
    cmds.addAttr(ctrl, ln="aimAtTarget", at="double", min=0, max=1, dv=0,
                 k=True)
    mm = cmds.createNode("multMatrix", n="C_%s_aimSpace_MM" % name)
    cmds.connectAttr(target + ".worldMatrix[0]", mm + ".matrixIn[0]")
    cmds.connectAttr(offset + ".worldInverseMatrix[0]", mm + ".matrixIn[1]")
    dm = cmds.createNode("decomposeMatrix", n="C_%s_aimSpace_DM" % name)
    cmds.connectAttr(mm + ".matrixSum", dm + ".inputMatrix")
    flat = cmds.createNode("multiplyDivide", n="C_%s_aimFlat_MD" % name)
    cmds.connectAttr(dm + ".outputTranslate", flat + ".input1")
    cmds.setAttr(flat + ".input2", *{"x": (0, 1, 1), "y": (1, 0, 1),
                                     "z": (1, 1, 0)}[axis])
    ang = cmds.createNode("angleBetween", n="C_%s_aim_AB" % name)
    cmds.setAttr(ang + ".vector1", *((0, 1, 0) if axis == "z" else (0, 0, 1)))
    cmds.connectAttr(flat + ".output", ang + ".vector2")
    blend = cmds.createNode("blendTwoAttr", n="C_%s_aim_BLEND" % name)
    for node in (mm, dm, flat, ang, blend):
        _tag(node, name)                     # so delete_parts cleans up
    cmds.setAttr(blend + ".input[0]", 0.0)
    cmds.connectAttr("%s.euler%s" % (ang, axis.upper()), blend + ".input[1]")
    cmds.connectAttr(ctrl + ".aimAtTarget", blend + ".attributesBlender")
    cmds.connectAttr(blend + ".output", "%s.rotate%s" % (auto, axis.upper()))


def _build_piston(spec, built, body_ctrl, body_jnt, ctrl_grp):
    """Cylinder joint on the base part aiming at the end, rod joint on the
    end part aiming back: the ram telescopes as the parts move."""
    name = spec["name"]

    def owner(par):
        return (body_ctrl, body_jnt) if par == BODY else built[par]

    base_ctrl, base_jnt = owner(spec["parent"])
    end_ctrl, end_jnt = owner(spec["end_parent"])
    anchors = []
    for tag, pos, parent in (("base", spec["pos"], base_jnt),
                             ("end", spec["end"], end_jnt)):
        a = cmds.group(em=True, n="C_%s_%sAnchor_GRP" % (name, tag))
        cmds.xform(a, ws=True, t=pos)
        cmds.parent(a, parent)
        _tag(a, name)
        anchors.append(a)
    joints = []
    for tag, pos, parent in (("cylinder", spec["pos"], anchors[0]),
                             ("rod", spec["end"], anchors[1])):
        cmds.select(cl=True)
        j = cmds.joint(n="C_%s_%s_BIND_JNT" % (name, tag), p=pos)
        cmds.setAttr(j + ".radius", 0.35 * SCALE)
        cmds.parent(j, parent)
        joints.append(j)
    cyl, rod = joints
    cmds.aimConstraint(anchors[1], cyl, aimVector=(0, 0, 1),
                       upVector=(1, 0, 0), worldUpType="objectrotation",
                       worldUpVector=(1, 0, 0), worldUpObject=base_jnt)
    cmds.aimConstraint(anchors[0], rod, aimVector=(0, 0, -1),
                       upVector=(1, 0, 0), worldUpType="objectrotation",
                       worldUpVector=(1, 0, 0), worldUpObject=base_jnt)
