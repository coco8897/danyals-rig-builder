"""
===============================================================================
 RIG CREATURE - extra arms, legs, tails and custom chains on the biped
===============================================================================

 The artist-facing side of CharacterRig(extra_limbs=...):

   * add_limb() / add_preset()   drop GUIDE locators for an extra limb,
                                 copied from the biped's own guides
   * add_limb("chain", ...)      a custom joint chain with FK, IK or FK+IK
   * chain_from_joints()         the same, from joints you drew yourself
   * attach=...                  hang any of them off ANY guide or joint
                                 (spine 2, tail 3, another limb's knee...)
   * read_extra_limbs()          turn the guides into the extra_limbs specs
                                 Build Rig passes to CharacterRig
   * picker_layout()             button layout for the picker's Creature tab

 Guides live under their own CREATURE_GUIDES_GRP (NOT inside RIG_GUIDES_GRP,
 whose refresh pass deletes any locator it doesn't recognise). One group per
 limb, e.g. lowerArm_LIMB_GUIDES, carries the limb's settings as attributes,
 so a saved scene remembers everything and the UI can list it back.

 With side "LR" only the LEFT guides are created; the right side is mirrored
 at build time, exactly like the biped's symmetric mode.

     import rig_creature
     rig_creature.add_preset("Four-Armed")
     rig_creature.add_limb("tail", "tail2", offset=(10, 0, 0))
     rig_creature.add_limb("chain", "cape", side="C", joints=6,
                           controls="fk", attach="C_spine_03_GUIDE")
===============================================================================
"""

import re

import maya.cmds as cmds

import rig_guides

CREATURE_GUIDES_GRP = "CREATURE_GUIDES_GRP"
LIMB_GRP_SUFFIX = "_LIMB_GUIDES"
GUIDE_SUFFIX = rig_guides.GUIDE_SUFFIX

KINDS = ("arm", "leg", "tail", "chain")
PARENTS = ("chest", "pelvis", "cog", "head")
SIDES = ("LR", "L", "R", "C")
CONTROLS = ("fkik", "fk", "ik")
CUSTOM = "custom"

# Guide slots per limb type -> the biped guide each one is copied from
# (left side; tails are centre).
ARM_SLOTS = (("clavicle", "L_clavicle"), ("shoulder", "L_shoulder"),
             ("elbow", "L_elbow"), ("wrist", "L_wrist"))
LEG_SLOTS = (("hip", "L_hip"), ("knee", "L_knee"), ("ankle", "L_ankle"),
             ("ball", "L_ball"), ("toe", "L_toe"), ("toeTip", "L_toeTip"))
TAIL_COUNT = 7
TAIL_SLOTS = tuple(("tail_%02d" % i, "C_tail_%02d" % i)
                   for i in range(1, TAIL_COUNT + 1)) + (("tip", "C_tailTip"),)
CHAIN_SPACING = 6.0

# Where a chain starts when it hangs off one of the named parents.
_PARENT_GUIDE = {"chest": "C_chest", "pelvis": "C_pelvis", "cog": "C_root",
                 "head": "C_head"}

# Biped guides whose joint isn't simply <guide>_BIND_JNT.
_GUIDE_JOINT_SPECIAL = {"C_face": "C_head_BIND_JNT",
                        "C_eyesLookAt": "C_head_BIND_JNT",
                        "C_headTip": "C_head_tip_BIND_JNT"}
_BASE_ARM = ("shoulder", "elbow", "wrist")
_BASE_LEG = ("hip", "knee", "ankle", "ball", "toe", "toeTip")

# One-click creatures. `modules_off` = biped modules the preset turns off in
# the UI (a centaur's tail belongs on the horse body, not the human pelvis).
PRESETS = {
    "Four-Armed": {
        "about": "A second pair of arms under the first, with their own "
                 "clavicles and fingers.",
        "limbs": [
            {"type": "arm", "label": "lowerArm", "offset": (0, -22, 0),
             "clavicle": True, "fingers": True},
        ],
    },
    "Winged": {
        "about": "A pair of 3-joint wings (shoulder, elbow, wrist) on the "
                 "upper back, each with a shrug clavicle.",
        "limbs": [
            {"type": "arm", "label": "wing", "offset": (0, 12, -14),
             "clavicle": True},
        ],
    },
    "Centaur": {
        "about": "Hind legs and a tail set back on the horse body. Move the "
                 "guides to fit your model.",
        "limbs": [
            {"type": "leg", "label": "hindLeg", "offset": (0, 0, -60)},
            {"type": "tail", "label": "horseTail", "offset": (0, 0, -55)},
        ],
        "modules_off": ["tail"],
    },
    "Six-Legged": {
        "about": "Two extra leg pairs behind the first (insect / spider "
                 "style). Auto-walk alternates the pairs.",
        "limbs": [
            {"type": "leg", "label": "midLeg", "offset": (0, 0, -25)},
            {"type": "leg", "label": "hindLeg", "offset": (0, 0, -50)},
        ],
    },
    "Dragon": {
        "about": "Four legs and wings. RE-POSES the body guides: body "
                 "horizontal (facing +Z), the biped arms become the wings "
                 "with the fingers as long wing spars, the legs become the "
                 "hind legs, a frontLeg pair is added on the chest, and the "
                 "neck becomes a 5-joint IK neck: drag C_headIK_CTRL and the "
                 "whole neck follows (C_neck_SETTINGS_CTRL.ikFkSwitch swaps "
                 "it for the FK chain). Keep "
                 "Arms + Fingers + Tail on. Set walkArmSwing to 0 on the "
                 "global control before using auto-walk.",
        # Left-side / centre biped guide positions (right side mirrors).
        "guides": {
            "C_root": (0, 100, -10), "C_pelvis": (0, 100, -40),
            "C_spine_01": (0, 102, -20), "C_spine_02": (0, 104, 0),
            "C_spine_03": (0, 104, 20), "C_chest": (0, 102, 40),
            "C_neck": (0, 115, 60), "C_neck_02": (0, 122, 67),
            "C_neck_03": (0, 128, 73), "C_neck_04": (0, 132, 79),
            "C_neck_05": (0, 134, 84), "C_head": (0, 135, 90),
            "C_headTip": (0, 135, 115),
            "L_hip": (14, 95, -40), "L_knee": (16, 60, -25),
            "L_ankle": (16, 20, -45), "L_ball": (16, 3, -35),
            "L_toe": (16, 3, -25), "L_toeTip": (16, 3, -20),
            "L_clavicle": (5, 112, 35), "L_shoulder": (18, 118, 35),
            "L_elbow": (55, 135, 25), "L_wrist": (90, 128, 30),
            "C_tail_01": (0, 95, -65), "C_tail_02": (0, 92, -80),
            "C_tail_03": (0, 89, -95), "C_tail_04": (0, 86, -110),
            "C_tail_05": (0, 83, -125), "C_tail_06": (0, 80, -140),
            "C_tail_07": (0, 77, -155), "C_tailTip": (0, 75, -170),
        },
        # A dragon's neck: 5 joints on an IK spline, with a head control.
        "neck": {"segments": 5, "ik": True},
        # Wing membrane spars: the finger guides fan back from the wrist.
        "wing_spars": {"wrist": "L_wrist", "length": 90, "thumb_length": 60,
                       "fan_start": 10, "fan_step": 22, "drop": 10},
        "limbs": [
            {"type": "leg", "label": "frontLeg", "parent": "chest",
             "positions": {"hip": (16, 95, 38), "knee": (16, 60, 50),
                           "ankle": (16, 20, 35), "ball": (16, 3, 45),
                           "toe": (16, 3, 55), "toeTip": (16, 3, 60)}},
        ],
    },
}

_FINGER_SLOTS = {
    "thumb": ("Meta", "Prox", "Dist", "Tip"),
    "index": ("Meta", "Prox", "Mid", "Dist", "Tip"),
    "middle": ("Meta", "Prox", "Mid", "Dist", "Tip"),
    "ring": ("Meta", "Prox", "Mid", "Dist", "Tip"),
    "pinky": ("Meta", "Prox", "Mid", "Dist", "Tip"),
}


def _preset_guide_targets(preset):
    """{guide name: position} a body-posing preset (Dragon) applies: its
    listed guides, the wing spars, and every face / head guide carried along
    with the head so the face stays on it. Right side mirrored."""
    import math
    targets = dict(preset.get("guides") or {})
    spars = preset.get("wing_spars")
    if spars:
        wx, wy, wz = targets.get(spars["wrist"]) or _base_pos(spars["wrist"])
        for i, (finger, slots) in enumerate(_FINGER_SLOTS.items()):
            ang = math.radians(spars["fan_start"] + i * spars["fan_step"])
            dx, dz = math.cos(ang), -math.sin(ang)
            length = spars["thumb_length" if finger == "thumb" else "length"]
            for k, slot in enumerate(slots):
                t = (k + 1) / float(len(slots))
                targets["L_%s%s" % (finger, slot)] = (
                    wx + dx * length * t, wy - spars["drop"] * t,
                    wz + dz * length * t)
    if "C_head" in targets:
        hx, hy, hz = _base_pos("C_head")
        nx, ny, nz = targets["C_head"]
        for g, grp in rig_guides.GUIDE_GROUP_OF.items():
            if grp in ("FACE_GUIDES", "HEAD_GUIDES") and g not in targets:
                x, y, z = _base_pos(g)
                targets[g] = (x + nx - hx, y + ny - hy, z + nz - hz)
    for g in list(targets):
        if g.startswith("L_"):
            x, y, z = targets[g]
            targets.setdefault("R_" + g[2:], (-x, y, z))
    return targets


# =============================================================================
# Helpers
# =============================================================================

def _chain_slots(count):
    return tuple(("tail_%02d" % i, None) for i in range(1, count + 1)) + \
        (("tip", None),)


def _slots(kind, joints=None):
    if kind == "chain":
        return _chain_slots(joints or 1)
    return {"arm": ARM_SLOTS, "leg": LEG_SLOTS, "tail": TAIL_SLOTS}[kind]


def _guide_name(kind, label, side, slot):
    if kind in ("tail", "chain"):
        if slot == "tip":
            return "%s_%sTip%s" % (side, label, GUIDE_SUFFIX)
        return "%s_%s_%s%s" % (side, label, slot[len("tail_"):], GUIDE_SUFFIX)
    return "%s_%s_%s%s" % (side, label, slot, GUIDE_SUFFIX)


def _limb_grp(label):
    return label + LIMB_GRP_SUFFIX


def _base_pos(guide):
    """World position of a biped guide: the live locator if the guides are in
    the scene, otherwise its default."""
    loc = guide + GUIDE_SUFFIX
    if cmds.objExists(loc):
        return tuple(cmds.xform(loc, q=True, ws=True, t=True))
    return tuple(rig_guides.DEFAULT_GUIDES[guide]["pos"])


def _top_grp():
    if not cmds.objExists(CREATURE_GUIDES_GRP):
        cmds.group(em=True, n=CREATURE_GUIDES_GRP)
        cmds.setAttr(CREATURE_GUIDES_GRP + ".useOutlinerColor", 1)
        cmds.setAttr(CREATURE_GUIDES_GRP + ".outlinerColor", 0.85, 0.64, 0.25)
    return CREATURE_GUIDES_GRP


def _set_str(node, attr, value):
    if not cmds.attributeQuery(attr, node=node, exists=True):
        cmds.addAttr(node, ln=attr, dt="string")
    cmds.setAttr("%s.%s" % (node, attr), str(value), type="string")


def _get_str(node, attr, default=""):
    if cmds.attributeQuery(attr, node=node, exists=True):
        return cmds.getAttr("%s.%s" % (node, attr)) or default
    return default


def _validate(specs):
    """Reuse the builder's own rules so the UI rejects exactly what Build
    Rig would."""
    import character_rig_builder as crb
    crb.CharacterRig(extra_limbs=specs)._validate_extra_limbs()


def _short(node):
    return node.split("|")[-1]


# =============================================================================
# Custom attach: any guide or joint
# =============================================================================

def resolve_attach(target):
    """Turn a picked guide locator or joint into the builder's joint parent.

    Biped guides map to the joint they build (L_knee_GUIDE ->
    L_leg_knee_BIND_JNT, C_tail_03_GUIDE -> C_tail_03_BIND_JNT). Spine guides
    sit between ribbon joints, so they resolve to the nearest spine joint.
    Creature guides map the same way (L_hindLeg_knee_GUIDE ->
    L_hindLeg_knee_BIND_JNT). A joint is used as-is. Raises ValueError for
    anything else."""
    if not target:
        raise ValueError("pick a guide locator or a joint to attach to")
    name = _short(str(target))
    if name.endswith(GUIDE_SUFFIX):
        base = name[:-len(GUIDE_SUFFIX)]
        if base in _GUIDE_JOINT_SPECIAL:
            return _GUIDE_JOINT_SPECIAL[base]
        if base in rig_guides.DEFAULT_GUIDES:
            if re.match(r"^C_spine_\d\d$", base):
                return {"joint": "C_spine_*_BIND_JNT", "near": _base_pos(base)}
            m = re.match(r"^([LR])_(\w+)$", base)
            if m and m.group(2) in _BASE_ARM:
                return "%s_arm_%s_BIND_JNT" % m.groups()
            if m and m.group(2) in _BASE_LEG:
                return "%s_leg_%s_BIND_JNT" % m.groups()
            m = re.match(r"^C_tongue(\d\d)$", base)
            if m:
                return "C_tongue_%s_BIND_JNT" % m.group(1)
            return base + "_BIND_JNT"
        if re.match(r"^[LRC]_[A-Za-z][A-Za-z0-9]*_\w+$", base) or \
                re.match(r"^[LRC]_[A-Za-z][A-Za-z0-9]*Tip$", base):
            return base + "_BIND_JNT"            # a creature guide
        raise ValueError("%r isn't a rig guide" % name)
    if cmds.objExists(name) and cmds.nodeType(name) == "joint":
        return name
    if name.endswith("_JNT"):
        return name                              # a joint the build will make
    raise ValueError("%r isn't a guide locator or a joint. Select a guide "
                     "(e.g. C_spine_02_GUIDE) or a joint." % name)


def attach_label(target):
    """Short readable name for a picked attach target, for the UI."""
    name = _short(str(target or ""))
    for suffix in (GUIDE_SUFFIX, "_BIND_JNT"):
        if name.endswith(suffix):
            return name[:-len(suffix)]
    return name


def _attach_pos(parent, attach):
    """Where a new chain should start: the picked target, or the named
    parent's guide."""
    if attach:
        name = _short(attach)
        if cmds.objExists(name):
            return tuple(cmds.xform(name, q=True, ws=True, t=True))
        res = resolve_attach(attach)
        if isinstance(res, dict):
            return tuple(res["near"])
        if cmds.objExists(res):
            return tuple(cmds.xform(res, q=True, ws=True, t=True))
    return _base_pos(_PARENT_GUIDE.get(parent or "chest", "C_chest"))


def selected_attach():
    """The one selected guide or joint, validated, for the Custom attach
    option. Raises ValueError with a readable message otherwise."""
    sel = cmds.ls(sl=True, type="transform") or []
    sel += [j for j in (cmds.ls(sl=True, type="joint") or []) if j not in sel]
    if len(sel) != 1:
        raise ValueError("select exactly ONE guide locator or joint to attach "
                         "to (you have %d selected)" % len(sel))
    resolve_attach(sel[0])
    return _short(sel[0])


# =============================================================================
# Guides
# =============================================================================

def list_limbs():
    """Every extra limb in the scene as a settings dict (no positions), in
    the order they were added. Custom-attached limbs carry parent "custom"
    and the picked target in "attach"."""
    if not cmds.objExists(CREATURE_GUIDES_GRP):
        return []
    out = []
    for grp in cmds.listRelatives(CREATURE_GUIDES_GRP, c=True,
                                  type="transform") or []:
        if not grp.endswith(LIMB_GRP_SUFFIX):
            continue
        spec = {
            "type": _get_str(grp, "limbType"),
            "label": _get_str(grp, "limbLabel"),
            "side": _get_str(grp, "limbSide", "LR"),
            "parent": _get_str(grp, "limbParent"),
        }
        if spec["type"] == "tail":
            spec.pop("side")
        if spec["parent"] == CUSTOM:
            spec["attach"] = _get_str(grp, "limbAttach")
        if spec["type"] == "arm":
            spec["clavicle"] = _get_str(grp, "limbClavicle") == "1"
            spec["fingers"] = _get_str(grp, "limbFingers") == "1"
        if spec["type"] == "chain":
            spec["joints"] = int(_get_str(grp, "limbJoints", "1") or 1)
            spec["controls"] = _get_str(grp, "limbControls", "fkik")
        out.append(spec)
    return out


def _attached_to(spec):
    """Label token a BUILD spec attaches to ('' if none): the name right
    after the side prefix of its attach joint."""
    par = spec.get("parent")
    name = par.get("joint", "") if isinstance(par, dict) else str(par or "")
    m = re.match(r"^[LRC]_([A-Za-z][A-Za-z0-9]*)", name)
    return m.group(1).lower() if m and name.endswith("_JNT") else ""


def _build_order(limbs):
    """Build specs in an order the builder accepts: anything attached to
    another creature limb comes after it, otherwise creation order.
    (Removing and re-adding a limb would otherwise put its dependents
    first.)"""
    labels = {l["label"].lower() for l in limbs}
    out, placed, pending = [], set(), list(limbs)
    while pending:
        progress = False
        for limb in list(pending):
            dep = _attached_to(limb)
            if dep not in labels or dep in placed or \
                    dep == limb["label"].lower():
                out.append(limb)
                placed.add(limb["label"].lower())
                pending.remove(limb)
                progress = True
        if not progress:              # a cycle: let the validator explain
            out += pending
            break
    return out


def _build_spec(limb):
    """A list_limbs() entry -> a spec the builder validates (no positions)."""
    spec = {k: v for k, v in limb.items()
            if k in ("type", "label", "side", "joints", "controls",
                     "clavicle", "fingers")}
    if limb.get("parent") == CUSTOM:
        spec["parent"] = resolve_attach(limb.get("attach"))
    elif limb.get("parent"):
        spec["parent"] = limb["parent"]
    return spec


def add_limb(kind, label, side="LR", parent=None, offset=(0, 0, 0),
             clavicle=False, fingers=False, joints=5, controls="fkik",
             attach=None, points=None, positions=None):
    """Create guide locators for one extra limb and return its guide group.

    Arms / legs / tails are copied from the biped's guides and moved by
    `offset` (or placed exactly at `positions`, {slot: (x, y, z)} for the
    side the guides are made on). A "chain" gets `joints` guides plus a tip,
    starting at what it attaches to and running outward (or exactly at
    `points`, a list of joints+1 world positions). `attach` = any guide or
    joint name to hang the limb from instead of `parent`. Raises ValueError
    for a bad or duplicate spec (same rules as Build Rig)."""
    import character_rig_builder as crb
    if kind == "tail":
        side = "C"
    if points is not None:
        points = [tuple(p) for p in points]
        joints = len(points) - 1
    spec = {"type": kind, "label": label}
    if kind != "tail":
        spec["side"] = side
    if kind == "chain":
        spec["joints"] = joints
        spec["controls"] = controls
    if attach:
        spec["parent"] = resolve_attach(attach)
    elif parent:
        spec["parent"] = parent
    if offset is not None:
        spec["offset"] = tuple(offset)
    _validate(_build_order([_build_spec(l) for l in list_limbs()] + [spec]))
    parent = CUSTOM if attach else (
        parent or crb.CharacterRig._EXTRA_DEFAULT_PARENT[kind])

    grp = cmds.group(em=True, n=_limb_grp(label), p=_top_grp())
    for attr, val in (("limbType", kind), ("limbLabel", label),
                      ("limbSide", side), ("limbParent", parent),
                      ("limbAttach", _short(attach) if attach else ""),
                      ("limbClavicle", int(bool(clavicle))),
                      ("limbFingers", int(bool(fingers))),
                      ("limbJoints", joints if kind == "chain" else ""),
                      ("limbControls", controls if kind == "chain" else "")):
        _set_str(grp, attr, val)

    ox, oy, oz = offset or (0, 0, 0)
    sd = {"LR": "L", "L": "L", "R": "R", "C": "C"}[side]
    color = {"L": rig_guides.COLOR_LEFT, "R": rig_guides.COLOR_RIGHT,
             "C": rig_guides.COLOR_CENTER}[sd]
    placed = []
    if kind == "chain":
        if points is None:
            start = _attach_pos(parent if parent != CUSTOM else None, attach)
            step = {"L": (CHAIN_SPACING, 0, 0), "R": (-CHAIN_SPACING, 0, 0),
                    "C": (0, 0, -CHAIN_SPACING)}[sd]
            points = [(start[0] + ox + step[0] * (i + 1),
                       start[1] + oy + step[1] * (i + 1),
                       start[2] + oz + step[2] * (i + 1))
                      for i in range(joints + 1)]
        placed = list(zip([s for s, _ in _chain_slots(joints)], points))
    else:
        mx = -1 if sd == "R" else 1
        for slot, base in _slots(kind):
            if slot == "clavicle" and not clavicle:
                continue
            if positions and slot in positions:
                placed.append((slot, tuple(positions[slot])))
                continue
            bx, by, bz = _base_pos(base)
            placed.append((slot, ((bx + ox) * mx, by + oy, bz + oz)))

    for slot, pos in placed:
        loc = cmds.spaceLocator(n=_guide_name(kind, label, sd, slot))[0]
        for ax in "XYZ":
            cmds.setAttr("%sShape.localScale%s" % (loc, ax),
                         rig_guides.GUIDE_SCALE)
        cmds.setAttr(loc + "Shape.overrideEnabled", 1)
        cmds.setAttr(loc + "Shape.overrideColor", color)
        for a in ("rx", "ry", "rz", "sx", "sy", "sz"):
            cmds.setAttr("%s.%s" % (loc, a), l=True, k=False, cb=False)
        cmds.xform(loc, ws=True, t=pos)
        cmds.parent(loc, grp)
        _set_str(loc, rig_guides.CREATURE_TAG, label)
    if is_ez_mode():
        _link_limb(list_limbs()[-1])
    cmds.select(cl=True)
    print("[creature] added %s %r guides (%s%s)"
          % (kind, label, side, ", on " + attach_label(attach)
             if attach else ""))
    return grp


def chain_from_joints(root, label, side="C", controls="fkik", parent=None,
                      attach=None):
    """Custom chain guides placed exactly on a joint chain you drew: `root`
    and its first child, grandchild... down to the end. If no parent/attach
    is given and the drawn root sits under another joint, the chain attaches
    to that joint. Your drawn joints are left untouched (delete them once
    the guides look right)."""
    root = _short(root)
    if not (cmds.objExists(root) and cmds.nodeType(root) == "joint"):
        raise ValueError("select the ROOT joint of the chain you drew")
    chain, cur = [root], root
    while True:
        kids = cmds.listRelatives(cur, c=True, type="joint") or []
        if not kids:
            break
        cur = kids[0]
        chain.append(cur)
    if len(chain) < 2:
        raise ValueError("%r has no child joints. Draw at least 2 joints "
                         "(the last one is the tip)" % root)
    pts = [tuple(cmds.xform(j, q=True, ws=True, t=True)) for j in chain]
    if not attach and not parent:
        up = cmds.listRelatives(root, p=True, type="joint")
        if up:
            attach = _short(up[0])
    return add_limb("chain", label, side=side, parent=parent, offset=None,
                    controls=controls, attach=attach, points=pts)


def add_preset(name):
    """Add every limb of a PRESETS entry. Returns the labels added. Nothing is
    created if any limb would clash."""
    preset = PRESETS[name]
    _validate(_build_order(
        [_build_spec(l) for l in list_limbs()]
        + [{k: v for k, v in limb.items() if k != "positions"}
           for limb in preset["limbs"]]))
    if preset.get("guides"):
        gs = rig_guides.GuideSystem()
        if not gs.exists():
            gs.build()
        gs._refresh_handles()
        neck = preset.get("neck") or {}
        if neck:
            # Make the neck's joints first: their guides are placed below.
            gs.set_neck_segments(neck.get("segments", 1))
            gs.set_neck_ik(neck.get("ik", False))
            gs._refresh_handles()
        targets = _preset_guide_targets(preset)
        gs._set_world_positions({gs.guides[g]: p for g, p in targets.items()
                                 if g in gs.guides})
    labels = []
    for limb in preset["limbs"]:
        add_limb(limb["type"], limb["label"], side=limb.get("side", "LR"),
                 parent=limb.get("parent"), offset=limb.get("offset"),
                 clavicle=limb.get("clavicle", False),
                 fingers=limb.get("fingers", False),
                 positions=limb.get("positions"))
        labels.append(limb["label"])
    return labels


def limb_guides(label):
    """A limb's guide locators, wherever EZ mode has linked them."""
    out = []
    for node in cmds.ls("*." + rig_guides.CREATURE_TAG, o=True) or []:
        if cmds.getAttr(node + "." + rig_guides.CREATURE_TAG) == label:
            out.append(_short(node))
    return out


def select_limb(label):
    guides = limb_guides(label)
    if guides:
        cmds.select(guides, r=True)
    return guides


# =============================================================================
# EZ / Free guide mode
# =============================================================================

def is_ez_mode():
    return rig_guides.GuideSystem().is_ez_mode()


def _limb_links(limb):
    """[(child_locator, parent_locator)] that EZ mode makes for one limb,
    root link last: each joint under the previous one, and the limb's first
    guide under the guide it attaches to (when that guide is visible)."""
    kind, label = limb["type"], limb["label"]
    side = limb.get("side", "LR")
    sd = "C" if kind == "tail" else {"LR": "L"}.get(side, side)
    slots = [s for s, _ in _slots(kind, limb.get("joints"))]
    if kind == "arm" and not limb.get("clavicle"):
        slots.remove("clavicle")
    names = [_guide_name(kind, label, sd, s) for s in slots]
    names = [n for n in names if cmds.objExists(n)]
    links = list(zip(names[1:], names[:-1]))
    if not names:
        return links
    if limb.get("parent") == CUSTOM:
        target = _short(limb.get("attach") or "")
        target = target if target.endswith(GUIDE_SUFFIX) else ""
    else:
        target = _PARENT_GUIDE.get(limb.get("parent"), "") + GUIDE_SUFFIX
    if target and cmds.objExists(target) and \
            cmds.getAttr(target + ".visibility"):
        links.append((names[0], target))
    return links


def _link_limb(limb):
    for child, parent in _limb_links(limb):
        if (cmds.listRelatives(child, p=True) or [None])[0] != parent:
            cmds.parent(child, parent)


def _unlink_limb(label):
    grp = _limb_grp(label)
    if not cmds.objExists(grp):
        return
    for loc in limb_guides(label):
        if (cmds.listRelatives(loc, p=True) or [None])[0] != grp:
            cmds.parent(loc, grp)


def set_guide_mode(ez):
    """EZ (True): body guides AND creature guides linked like a skeleton,
    so moving a parent brings its children. Free (False): every guide moves
    on its own. Nothing moves in world space either way."""
    gs = rig_guides.GuideSystem()
    limbs = list_limbs()
    if not ez:
        for limb in limbs:                 # unhook from body guides first
            _unlink_limb(limb["label"])
    if gs.exists():
        gs.set_ez_mode(ez)
    if ez:
        for limb in limbs:                 # creation order: attach targets
            _link_limb(limb)               # on earlier limbs already exist
    return ez


def dependents(label):
    """Labels of limbs attached to one of `label`'s guides or joints."""
    tok = str(label).lower()
    out = []
    for limb in list_limbs():
        m = re.match(r"^[LRC]_([A-Za-z][A-Za-z0-9]*)",
                     attach_label(limb.get("attach")))
        if m and m.group(1).lower() == tok and limb["label"] != label:
            out.append(limb["label"])
    return out


def remove_limb(label):
    grp = _limb_grp(label)
    if not cmds.objExists(grp):
        return False
    mine = set(limb_guides(label))
    # Other limbs' guides linked under this one (EZ mode) go home first.
    for loc in mine:
        for kid in cmds.listRelatives(loc, c=True, type="transform") or []:
            if kid in mine or not cmds.attributeQuery(
                    rig_guides.CREATURE_TAG, node=kid, exists=True):
                continue
            home = _limb_grp(cmds.getAttr(kid + "." + rig_guides.CREATURE_TAG))
            cmds.parent(kid, home if cmds.objExists(home) else _top_grp())
    tops = [loc for loc in mine
            if (cmds.listRelatives(loc, p=True) or [None])[0] not in mine]
    if tops:
        cmds.delete(tops)
    cmds.delete(grp)
    if not (cmds.listRelatives(CREATURE_GUIDES_GRP, c=True) or []):
        cmds.delete(CREATURE_GUIDES_GRP)
    return True


def delete_all():
    if cmds.objExists(CREATURE_GUIDES_GRP):
        cmds.delete(CREATURE_GUIDES_GRP)


def read_extra_limbs():
    """The extra_limbs list for CharacterRig, with positions read from the
    creature guides. Empty list if there are none."""
    specs = []
    for limb in list_limbs():
        kind, label = limb["type"], limb["label"]
        side = limb.get("side", "LR")
        sd = "C" if kind == "tail" else {"LR": "L"}.get(side, side)

        def p(slot):
            loc = _guide_name(kind, label, sd, slot)
            if not cmds.objExists(loc):
                raise ValueError("creature limb %r is missing its guide %s. "
                                 "Remove the limb and add it again."
                                 % (label, loc))
            return tuple(cmds.xform(loc, q=True, ws=True, t=True))

        spec = _build_spec(limb)
        spec["positions"] = {slot: p(slot)
                             for slot, _ in _slots(kind, limb.get("joints"))
                             if slot != "clavicle"}
        if kind == "arm" and limb.get("clavicle"):
            spec["clavicle_positions"] = {"clavicle": p("clavicle"),
                                          "clavicle_tip": p("shoulder")}
        specs.append(spec)
    return _build_order(specs)


# =============================================================================
# Picker
# =============================================================================

def scene_extra_limbs():
    """Extra limbs present on the BUILT rig, found by name:
    [(kind, label, sides)] with sides like "LR", "L" or "C". Chains on the
    left / right report as "chain", centre ones as "tail" (same layout)."""
    found = {}
    for ik in cmds.ls("L_*_IK_CTRL", "R_*_IK_CTRL", type="transform") or []:
        prefix = ik[:-len("_IK_CTRL")]
        label = prefix[2:]
        if label in ("arm", "leg") or "_" in label:
            continue
        if cmds.objExists(prefix + "_wrist_BIND_JNT"):
            kind = "arm"
        elif cmds.objExists(prefix + "_ankle_BIND_JNT"):
            kind = "leg"
        else:
            continue
        found.setdefault((kind, label), set()).add(prefix[0])
    for st in cmds.ls("*_SETTINGS_CTRL", type="transform") or []:
        m = re.match(r"^([LRC])_([A-Za-z][A-Za-z0-9]*)_SETTINGS_CTRL$", st)
        if not m:
            continue
        sd, label = m.groups()
        if (sd, label) == ("C", "tail"):
            continue
        if cmds.objExists("%s_%s_01_FK_CTRL" % (sd, label)):
            found.setdefault(("chain", label), set()).add(sd)
    out = []
    for (kind, label), sides in found.items():
        if kind == "chain" and sides == {"C"}:
            kind = "tail"
        out.append((kind, label, "".join(s for s in "LRC" if s in sides)))
    order = {"arm": 0, "leg": 1, "chain": 2, "tail": 3}
    return sorted(out, key=lambda t: (order[t[0]], t[1]))


_ARM_ROWS = (
    (("_clavicle_CTRL", "CLV", 44, None), ("_shoulder_FK_CTRL", "SH", 40, None),
     ("_elbow_FK_CTRL", "EL", 40, None), ("_wrist_FK_CTRL", "WR", 40, None)),
    (("_IK_CTRL", "{nice} IK", 92, "IK"), ("_PV_CTRL", "PV", 30, "PV"),
     ("_SETTINGS_CTRL", "SET", 36, "S"), ("_weapon_CTRL", "WPN", 36, "B")),
    (("_thumbProx_FK_CTRL", "th", 38, None), ("_indexProx_FK_CTRL", "idx", 38, None),
     ("_middleProx_FK_CTRL", "mid", 38, None), ("_ringProx_FK_CTRL", "ring", 38, None),
     ("_pinkyProx_FK_CTRL", "pky", 38, None)),
)
_LEG_ROWS = (
    (("_hip_FK_CTRL", "HIP", 48, None), ("_knee_FK_CTRL", "KNEE", 48, None),
     ("_ankle_FK_CTRL", "ANK", 48, None), ("_ball_FK_CTRL", "BALL", 44, None)),
    (("_IK_CTRL", "{nice} IK", 92, "IK"), ("_PV_CTRL", "PV", 30, "PV"),
     ("_SETTINGS_CTRL", "SET", 36, "S"), ("_toeTip_CTRL", "TOES", 40, None)),
)
_ROW_H, _GAP = 20, 4


def _chain_mode(prefix):
    """'fk' / 'ik' for a chain whose switch is locked to one mode, else
    'fkik'."""
    plug = prefix + "_SETTINGS_CTRL.ikFkSwitch"
    try:
        if cmds.getAttr(plug, lock=True):
            return "fk" if cmds.getAttr(plug) > 0.5 else "ik"
    except (RuntimeError, ValueError):
        pass
    return "fkik"


def picker_layout(limbs=None, width=460):
    """Buttons for the picker's Creature tab.

    Returns (entries, height, ikfk_prefixes). Entries use the picker's
    (ctrl, label, x, y, w, h, color) format; only controls that exist (and,
    on FK-only / IK-only chains, only the mode in use) are included."""
    if limbs is None:
        limbs = scene_extra_limbs()
    entries, prefixes = [], []
    col_w = (width - 30) // 2
    y = 10

    def flow(row_specs, prefix, x0, y0, side_color, nice):
        x, placed = x0, 0
        for suffix, text, w, color in row_specs:
            ctrl = prefix + suffix
            if not cmds.objExists(ctrl):
                continue
            entries.append((ctrl, text.format(nice=nice), x, y0, w, _ROW_H,
                            color or side_color))
            x += w + _GAP
            placed += 1
        return placed

    for kind, label, sides in limbs:
        nice = label[:9]
        if kind in ("tail", "chain"):
            block_h = 0
            for sd in sides:
                pre = "%s_%s" % (sd, label)
                x0 = 10 if sd in "LC" else 20 + col_w
                right = (width - 10) if sd == "C" else (x0 + col_w)
                mode = _chain_mode(pre)
                yy = y
                head = [("_SETTINGS_CTRL", "{nice} S", 92, "S")]
                if mode != "fk":
                    head += [("_root_IK_CTRL", "ROOT", 36, "IK"),
                             ("_mid_IK_CTRL", "MID", 36, "IK"),
                             ("_tip_IK_CTRL", "TIP", 36, "IK")]
                label_nice = nice if sd == "C" else "%s %s" % (sd, nice)
                if flow(head, pre, x0, yy, sd, label_nice):
                    yy += _ROW_H + _GAP
                if mode != "ik":
                    fks = sorted(c for c in cmds.ls("%s_*_FK_CTRL" % pre,
                                                    type="transform") or []
                                 if re.match(r"^%s_\d\d_FK_CTRL$" % pre, c))
                    x = x0
                    for ctrl in fks:
                        if x + 36 > right:
                            x, yy = x0, yy + _ROW_H + _GAP
                        entries.append((ctrl, ctrl[len(pre) + 1:len(pre) + 3],
                                        x, yy, 36, _ROW_H, sd))
                        x += 40
                    if fks:
                        yy += _ROW_H + _GAP
                block_h = max(block_h, yy - y)
            y += block_h + 10
            continue

        rows = _ARM_ROWS if kind == "arm" else _LEG_ROWS
        block_h = 0
        for sd in sides:
            x0 = 10 if sd == "L" else 20 + col_w
            yy = y
            for row in rows:
                if flow(row, "%s_%s" % (sd, label), x0, yy, sd,
                        "%s %s" % (sd, nice)):
                    yy += _ROW_H + _GAP
            block_h = max(block_h, yy - y)
            prefixes.append("%s_%s" % (sd, label))
        y += block_h + 10
    return entries, max(y, 60), prefixes
