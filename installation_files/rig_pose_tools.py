"""
===============================================================================
 RIG POSE TOOLS — mirror pose L<->R, and IK/FK match-switch
===============================================================================

 Two animator tools for the biped, used from the picker:

   copy_pose_left_to_right()  /  copy_pose_right_to_left()
       Mirror every limb + finger + clavicle ctrl pose across the X=0
       plane so the opposite side matches symmetrically. Uses a geometric
       WORLD-space behaviour-mirror (orientation-independent), so it works
       regardless of how each ctrl's local axes are oriented.

   ikfk_match(prefix)
       Switch an arm or leg between IK and FK while KEEPING the same
       visible pose. Reads the BIND chain (which always holds the current
       pose, whichever mode is active), copies it onto the target mode's
       ctrls, then flips the switch — so the limb doesn't pop.

         prefix = "L_arm" / "R_arm" / "L_leg" / "R_leg"

 All operations are wrapped in undo chunks by the caller (the picker).
===============================================================================
"""

import maya.cmds as cmds
import maya.api.OpenMaya as om


# =============================================================================
# Geometric world-space mirror (behaviour mirror across the YZ / X=0 plane)
# =============================================================================

# Reflection that negates world X. Applied as  M_mirror = R * M * R  gives a
# proper (det +1) "behaviour" mirror: the control ends up on the other side
# AND oriented so it poses symmetrically.
_MIRROR = om.MMatrix((
    (-1.0, 0.0, 0.0, 0.0),
    ( 0.0, 1.0, 0.0, 0.0),
    ( 0.0, 0.0, 1.0, 0.0),
    ( 0.0, 0.0, 0.0, 1.0),
))


def _mirror_world_matrix(mtx):
    """Behaviour-mirror a world MMatrix across the X=0 plane."""
    return _MIRROR * mtx * _MIRROR


def _get_world_matrix(node):
    return om.MMatrix(cmds.xform(node, q=True, ws=True, m=True))


def _set_world_matrix(node, mtx):
    cmds.xform(node, ws=True, m=list(mtx))


def _opp(name):
    """Return the opposite-side counterpart of a ctrl name, or None."""
    if name.startswith("L_"):
        return "R_" + name[2:]
    if name.startswith("R_"):
        return "L_" + name[2:]
    return None


def _settable(node, attr):
    """True if node.attr exists, is settable, and not locked."""
    if not cmds.attributeQuery(attr, node=node, exists=True):
        return False
    plug = f"{node}.{attr}"
    return cmds.getAttr(plug, settable=True)


def _copy_attr(src, dst, attr):
    if _settable(dst, attr) and cmds.attributeQuery(attr, node=src,
                                                     exists=True):
        try:
            cmds.setAttr(f"{dst}.{attr}", cmds.getAttr(f"{src}.{attr}"))
            return True
        except Exception:
            return False
    return False


# =============================================================================
# Ctrl discovery
# =============================================================================

def _rig_root():
    for grp in ("CHARACTER_RIG_GRP",):
        if cmds.objExists(grp):
            return grp
    return None


def _all_left_ctrls():
    """Every L_-prefixed CTRL transform under the biped rig."""
    root = _rig_root()
    if not root:
        return []
    ctrls = cmds.listRelatives(root, ad=True, type="transform") or []
    return [c for c in ctrls
            if c.startswith("L_") and c.endswith("_CTRL")]


# Translate-locked rotation-only ctrls (FK chain ctrls, clavicle, fingers,
# settings) mirror by world rotation only; ctrls with free translate
# (the IK cube, pole vector) mirror by full world matrix. We detect which
# by asking whether translate is settable.
def _has_free_translate(ctrl):
    return any(_settable(ctrl, a) for a in ("translateX", "translateY",
                                             "translateZ"))


# =============================================================================
# Copy pose left <-> right
# =============================================================================

def copy_pose_left_to_right():
    return _copy_pose(src_prefix="L_", dst_prefix="R_")


def copy_pose_right_to_left():
    return _copy_pose(src_prefix="R_", dst_prefix="L_")


def _copy_pose(src_prefix, dst_prefix):
    """Mirror every src-side ctrl pose onto its dst-side counterpart.

    Returns the number of ctrls mirrored.
    """
    root = _rig_root()
    if not root:
        cmds.warning("[pose] No CHARACTER_RIG_GRP — build a biped first.")
        return 0

    ctrls = cmds.listRelatives(root, ad=True, type="transform") or []
    src_ctrls = [c for c in ctrls
                 if c.startswith(src_prefix) and c.endswith("_CTRL")]

    # Settings ctrls just copy attrs straight across (ikFkSwitch, etc.),
    # no geometric mirror. Everything else gets the world-mirror.
    n = 0
    # Apply ROOT-first so chained FK ctrls land correctly (parent before
    # child). Sorting by depth in the hierarchy approximates that.
    def depth(c):
        d, cur = 0, c
        while True:
            p = cmds.listRelatives(cur, p=True)
            if not p:
                return d
            cur, d = p[0], d + 1
    src_ctrls.sort(key=depth)

    for src in src_ctrls:
        dst = src_prefix and (dst_prefix + src[len(src_prefix):])
        if not dst or not cmds.objExists(dst):
            continue

        if "_SETTINGS_CTRL" in src:
            # Copy the unlocked user attrs (ikFkSwitch, autoStretch,
            # bendyVis, finger curls, etc.) verbatim.
            for attr in (cmds.listAttr(src, k=True, u=True) or []):
                _copy_attr(src, dst, attr)
            n += 1
            continue

        src_w = _get_world_matrix(src)
        mir = _mirror_world_matrix(src_w)

        if _has_free_translate(src):
            # IK ctrl / pole vector — full transform mirror.
            _set_world_matrix(dst, mir)
        else:
            # Rotation-only ctrl — apply just the mirrored rotation,
            # leaving its (locked) translate at the offset rest pose.
            tm = om.MTransformationMatrix(mir)
            rot = tm.rotation(asQuaternion=False)   # MEulerRotation, radians
            cmds.xform(dst, ws=True, ro=(
                om.MAngle(rot.x).asDegrees(),
                om.MAngle(rot.y).asDegrees(),
                om.MAngle(rot.z).asDegrees()))
        n += 1

    print(f"[pose] Mirrored {n} ctrl(s) {src_prefix[:-1]} -> "
          f"{dst_prefix[:-1]}.")
    return n


# =============================================================================
# IK / FK match-switch
# =============================================================================

_ARM_PARTS = ("shoulder", "elbow", "wrist")
_LEG_PARTS = ("hip", "knee", "ankle", "ball", "toe")


def _limb_info(prefix):
    """Return (parts, ik_end_part) for an arm/leg prefix, or (None, None)."""
    if prefix.endswith("_arm"):
        return _ARM_PARTS, "wrist"
    if prefix.endswith("_leg"):
        return _LEG_PARTS, "ankle"
    return None, None


def ikfk_match(prefix):
    """Switch a limb between IK and FK keeping the same world pose.

    prefix: 'L_arm' / 'R_arm' / 'L_leg' / 'R_leg'.
    Returns the new mode string ('IK' or 'FK'), or None on failure.
    """
    parts, ik_end = _limb_info(prefix)
    if not parts:
        cmds.warning(f"[ikfk] '{prefix}' is not an arm/leg prefix.")
        return None
    settings = f"{prefix}_SETTINGS_CTRL"
    if not cmds.objExists(settings):
        cmds.warning(f"[ikfk] {settings} not found.")
        return None

    switch_attr = f"{settings}.ikFkSwitch"
    cur = cmds.getAttr(switch_attr)        # 0 = IK, 1 = FK
    going_to_fk = cur < 0.5

    bind = {p: f"{prefix}_{p}_BIND_JNT" for p in parts}
    if not all(cmds.objExists(b) for b in bind.values()):
        cmds.warning(f"[ikfk] missing BIND joints for {prefix}.")
        return None

    if going_to_fk:
        # IK -> FK: capture each BIND joint's world rotation (the current
        # IK pose), then drive the FK ctrls to reproduce it, root first.
        targets = {p: cmds.xform(bind[p], q=True, ws=True, ro=True)
                   for p in parts}
        cmds.setAttr(switch_attr, 1)       # flip first so FK ctrls drive
        for p in parts:
            fk = f"{prefix}_{p}_FK_CTRL"
            if cmds.objExists(fk) and p in targets:
                cmds.xform(fk, ws=True, ro=targets[p])
        _key_if_autokey([f"{prefix}_{p}_FK_CTRL" for p in parts]
                        + [switch_attr])
        return "FK"
    else:
        # FK -> IK: place the IK ctrl at the end BIND joint and the pole
        # vector behind the mid joint, then flip to IK.
        ik_ctrl = f"{prefix}_IK_CTRL"
        pv_ctrl = f"{prefix}_PV_CTRL"
        end_bind = bind[ik_end]
        end_mtx = _get_world_matrix(end_bind)
        if cmds.objExists(ik_ctrl):
            _set_world_matrix(ik_ctrl, end_mtx)
        # Pole vector from the first three joints of the chain.
        if cmds.objExists(pv_ctrl):
            pv = _pole_position(bind[parts[0]], bind[parts[1]],
                                bind[parts[2]])
            cmds.xform(pv_ctrl, ws=True, t=pv)
        cmds.setAttr(switch_attr, 0)
        _key_if_autokey([ik_ctrl, pv_ctrl, switch_attr])
        return "IK"


def _pole_position(start, mid, end, distance=None):
    """Pole-vector position: project mid onto start->end, push outward."""
    s = om.MVector(*cmds.xform(start, q=True, ws=True, t=True))
    m = om.MVector(*cmds.xform(mid,   q=True, ws=True, t=True))
    e = om.MVector(*cmds.xform(end,   q=True, ws=True, t=True))
    sw = e - s
    sm = m - s
    if sw.length() < 1e-5:
        return (m.x, m.y, m.z)
    proj = (sm * sw) / sw.length()
    closest = s + sw.normal() * proj
    pv_dir = m - closest
    if pv_dir.length() < 1e-5:
        pv_dir = om.MVector(0, 0, -1)
    if distance is None:
        # Push out by the limb's own length so the PV sits clear.
        distance = (m - s).length()
    pv = m + pv_dir.normal() * distance
    return (pv.x, pv.y, pv.z)


def _key_if_autokey(plugs):
    """Set keys on the given plugs only if auto-key is on (so a match
    during animation records the switch; a static pose-fix doesn't)."""
    if not cmds.autoKeyframe(q=True, state=True):
        return
    for pl in plugs:
        if not pl:
            continue
        node = pl.split(".")[0]
        if cmds.objExists(node):
            try:
                cmds.setKeyframe(pl)
            except Exception:
                pass
