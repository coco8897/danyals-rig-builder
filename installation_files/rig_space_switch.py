"""
===============================================================================
 RIG SPACE SWITCH — parent-space switching for the biped controls
===============================================================================

 Lets an animator change what an IK control / pole vector / head FOLLOWS,
 without rebuilding the rig:

    L/R hand IK   : World (root) / COG / Chest
    L/R foot IK   : World (root) / COG / Hips
    L/R arm pole  : World / Hand (its IK ctrl) / COG
    L/R leg pole  : World / Foot (its IK ctrl) / COG
    head          : Neck (default) / Chest / COG / World

 Mechanism (per control):
   * an enum  `space`  attr is added to the control,
   * one locator per space is created and parent-constrained to that space
     parent,
   * the control's existing OFFSET group is parent-constrained to ALL those
     locators (maintainOffset), and
   * the enum drives the constraint weights through condition nodes so
     exactly one space is active at a time.

 Maya's parentConstraint compensates for the offset's own parent (via
 parentInverseMatrix), so nothing needs reparenting and the default space
 reproduces the rig's original behaviour with no jump at build time.

 Changing the enum in the channel box will POP the control (its offset moves
 to the new parent). Use switch_space()/switch_selected() for a seamless,
 no-jump switch that preserves the control's world pose (and optionally keys
 it on the current frame).
===============================================================================
"""

import maya.cmds as cmds

SPACE_ATTR = "space"
SPACES_TOP = "C_spaceSwitch_GRP"


# =============================================================================
# Config — the standard biped control -> spaces map
# =============================================================================

def biped_config():
    """Return [(ctrl, offset, [(parent, nice_name), ...], default_idx), ...]
    for whichever standard biped controls exist in the scene."""
    cfg = []
    for side in ("L", "R"):
        cfg.append((
            f"{side}_arm_IK_CTRL", f"{side}_arm_IK_OFFSET",
            [("C_global_CTRL", "World"), ("C_cog_CTRL", "COG"),
             ("C_spine_chest_CTRL", "Chest")], 0))
        cfg.append((
            f"{side}_leg_IK_CTRL", f"{side}_leg_IK_OFFSET",
            [("C_global_CTRL", "World"), ("C_cog_CTRL", "COG"),
             ("C_spine_hip_CTRL", "Hips")], 0))
        cfg.append((
            f"{side}_arm_PV_CTRL", f"{side}_arm_PV_OFFSET",
            [("C_global_CTRL", "World"), (f"{side}_arm_IK_CTRL", "Hand"),
             ("C_cog_CTRL", "COG")], 0))
        cfg.append((
            f"{side}_leg_PV_CTRL", f"{side}_leg_PV_OFFSET",
            [("C_global_CTRL", "World"), (f"{side}_leg_IK_CTRL", "Foot"),
             ("C_cog_CTRL", "COG")], 0))
    cfg.append((
        "C_head_CTRL", "C_head_OFFSET",
        [("C_neck_CTRL", "Neck"), ("C_spine_chest_CTRL", "Chest"),
         ("C_cog_CTRL", "COG"), ("C_global_CTRL", "World")], 0))
    cfg += _extra_limb_config()
    return cfg


def _extra_limb_config():
    """Same hand/foot/pole spaces for labelled extra limbs (four-armed,
    centaur...), found by name: an L_/R_ prefix with its own IK ctrl and a
    wrist (arm) or ankle (leg) joint."""
    cfg = []
    for ik in sorted(cmds.ls("L_*_IK_CTRL", "R_*_IK_CTRL",
                             type="transform") or []):
        prefix = ik[:-len("_IK_CTRL")]
        if prefix[2:] in ("arm", "leg") or "_" in prefix[2:]:
            continue                      # base limbs, or not a limb prefix
        if cmds.objExists(f"{prefix}_wrist_BIND_JNT"):
            local, nice, pv_nice = "C_spine_chest_CTRL", "Chest", "Hand"
        elif cmds.objExists(f"{prefix}_ankle_BIND_JNT"):
            local, nice, pv_nice = "C_spine_hip_CTRL", "Hips", "Foot"
        else:
            continue
        cfg.append((ik, f"{prefix}_IK_OFFSET",
                    [("C_global_CTRL", "World"), ("C_cog_CTRL", "COG"),
                     (local, nice)], 0))
        cfg.append((f"{prefix}_PV_CTRL", f"{prefix}_PV_OFFSET",
                    [("C_global_CTRL", "World"), (ik, pv_nice),
                     ("C_cog_CTRL", "COG")], 0))
    return cfg


# =============================================================================
# Build
# =============================================================================

def _spaces_top():
    """The hidden group that holds every control's space locators."""
    if not cmds.objExists(SPACES_TOP):
        grp = cmds.group(em=True, n=SPACES_TOP)
        for parent in ("misc_GRP", "CHARACTER_RIG_GRP"):
            if cmds.objExists(parent):
                cmds.parent(grp, parent)
                break
        cmds.setAttr(f"{grp}.visibility", 0)
    return SPACES_TOP


def has_space_switch(ctrl):
    return (cmds.objExists(ctrl)
            and cmds.attributeQuery(SPACE_ATTR, node=ctrl, exists=True)
            and cmds.objExists(f"{ctrl}_SPACES_GRP"))


def add_space_switch(ctrl, offset, parents, default=0, attr=SPACE_ATTR):
    """Add a parent-space switch to `ctrl` (driving its `offset` group).

    parents : list of (parent_node, nice_name). Missing parents are skipped.
    Returns True if the switch was built (needs >= 2 valid spaces).
    """
    if not (cmds.objExists(ctrl) and cmds.objExists(offset)):
        return False
    if has_space_switch(ctrl):
        return True   # idempotent
    parents = [(p, n) for (p, n) in parents if cmds.objExists(p)]
    if len(parents) < 2:
        return False

    names = ":".join(n for _, n in parents)
    if cmds.attributeQuery(attr, node=ctrl, exists=True):
        # Re-create as the right enum if it somehow exists as another type.
        try:
            cmds.deleteAttr(f"{ctrl}.{attr}")
        except Exception:
            pass
    cmds.addAttr(ctrl, ln=attr, at="enum", en=names, k=True)

    grp = cmds.group(em=True, n=f"{ctrl}_SPACES_GRP")
    cmds.matchTransform(grp, offset)
    cmds.parent(grp, _spaces_top())

    locs = []
    for parent, nice in parents:
        loc = cmds.spaceLocator(n=f"{ctrl}_{nice}Space_LOC")[0]
        cmds.matchTransform(loc, offset)
        cmds.parent(loc, grp)
        cmds.parentConstraint(parent, loc, mo=True)
        for s in ("X", "Y", "Z"):
            cmds.setAttr(f"{loc}.localScale{s}", 0.001)
        locs.append(loc)

    pc = cmds.parentConstraint(*(locs + [offset]), mo=True)[0]
    weights = cmds.parentConstraint(pc, q=True, weightAliasList=True)
    for i, w in enumerate(weights):
        cond = cmds.createNode("condition", n=f"{ctrl}_space{i}_COND")
        cmds.connectAttr(f"{ctrl}.{attr}", f"{cond}.firstTerm")
        cmds.setAttr(f"{cond}.secondTerm", i)
        cmds.setAttr(f"{cond}.operation", 0)      # equal
        cmds.setAttr(f"{cond}.colorIfTrueR", 1)
        cmds.setAttr(f"{cond}.colorIfFalseR", 0)
        cmds.connectAttr(f"{cond}.outColorR", f"{pc}.{w}")

    cmds.setAttr(f"{ctrl}.{attr}", default)
    return True


def build_biped_spaces():
    """Add space switches to every standard biped control present. Idempotent.
    Returns the number of controls switched."""
    n = 0
    for ctrl, offset, parents, default in biped_config():
        if add_space_switch(ctrl, offset, parents, default):
            n += 1
    print(f"[space] Added space switches to {n} control(s).")
    return n


# =============================================================================
# Seamless switching (animation time)
# =============================================================================

def space_names(ctrl, attr=SPACE_ATTR):
    """The enum labels for a control's space attr, or []."""
    if not cmds.attributeQuery(attr, node=ctrl, exists=True):
        return []
    en = cmds.attributeQuery(attr, node=ctrl, listEnum=True) or [""]
    return en[0].split(":") if en[0] else []


def switch_space(ctrl, index, attr=SPACE_ATTR, key=False):
    """Switch `ctrl` to space `index` WITHOUT it jumping — the control's
    world position + orientation are preserved across the switch.

    index may be an int or a space name. If `key`, keys the control + the
    space attr on the current frame (so the switch is clean in animation).
    """
    if not cmds.attributeQuery(attr, node=ctrl, exists=True):
        cmds.warning(f"[space] {ctrl} has no '{attr}' attr.")
        return False
    if isinstance(index, str):
        names = space_names(ctrl, attr)
        if index not in names:
            cmds.warning(f"[space] {ctrl}: no space '{index}'.")
            return False
        index = names.index(index)

    t = cmds.xform(ctrl, q=True, ws=True, t=True)
    r = cmds.xform(ctrl, q=True, ws=True, ro=True)
    if key:
        # Key the OLD pose one frame back so the switch frame is a clean cut.
        cmds.setKeyframe(f"{ctrl}.{attr}")
    cmds.setAttr(f"{ctrl}.{attr}", index)
    cmds.xform(ctrl, ws=True, t=t)
    try:
        cmds.xform(ctrl, ws=True, ro=r)
    except Exception:
        pass   # translate-only controls (pole vectors) lock rotation
    if key:
        cmds.setKeyframe(ctrl)
        cmds.setKeyframe(f"{ctrl}.{attr}")
    return True


def switch_selected(index, key=True):
    """Seamlessly switch every selected control that has a space attr."""
    sel = cmds.ls(sl=True) or []
    done = 0
    for ctrl in sel:
        if cmds.attributeQuery(SPACE_ATTR, node=ctrl, exists=True):
            if switch_space(ctrl, index, key=key):
                done += 1
    if not done:
        cmds.warning("[space] No selected control has a space switch.")
    return done


def switch_selected_to(name, key=True):
    """Seamlessly switch every selected control that HAS a space called
    `name` (e.g. 'World', 'COG') to that space. Controls without that space
    are skipped, so you can multi-select hands + feet and send them all to
    World in one click. Returns the count switched."""
    sel = cmds.ls(sl=True) or []
    done = 0
    for ctrl in sel:
        if name in space_names(ctrl):
            if switch_space(ctrl, name, key=key):
                done += 1
    if not done:
        cmds.warning(f"[space] No selected control has a '{name}' space.")
    return done


def remove_space_switch(ctrl, attr=SPACE_ATTR):
    """Strip a control's space switch (for rebuilds / cleanup)."""
    grp = f"{ctrl}_SPACES_GRP"
    # delete constraint on the offset
    offset = ctrl.replace("_CTRL", "_OFFSET")
    if cmds.objExists(offset):
        for c in (cmds.listRelatives(offset, type="parentConstraint") or []):
            cmds.delete(c)
    if cmds.objExists(grp):
        cmds.delete(grp)
    for cond in (cmds.ls(f"{ctrl}_space*_COND") or []):
        if cmds.objExists(cond):
            cmds.delete(cond)
    if cmds.attributeQuery(attr, node=ctrl, exists=True):
        try:
            cmds.deleteAttr(f"{ctrl}.{attr}")
        except Exception:
            pass
