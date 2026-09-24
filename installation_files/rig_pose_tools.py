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

       Stretch carries across: IK -> FK sets the upper/lower FK ctrls'
       `length` channel to the IK stretch. FK -> IK lands the wrist/ankle
       exactly (switching autoStretch on if a lengthened FK limb needs
       it), but IK stretch is one ratio for both segments, so an FK limb
       with unequal upper/lower lengths keeps its hand/foot and lets the
       elbow/knee settle where the IK solve puts it.

         prefix = "L_arm" / "R_arm" / "L_leg" / "R_leg"
                  (or a labelled extra limb, e.g. "L_lowerArm")

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
        _copy_attr(src, dst, LENGTH_ATTR)      # FK bone length, if any
        n += 1

    print(f"[pose] Mirrored {n} ctrl(s) {src_prefix[:-1]} -> "
          f"{dst_prefix[:-1]}.")
    return n


# =============================================================================
# IK / FK match-switch
# =============================================================================

_ARM_PARTS = ("shoulder", "elbow", "wrist")
_LEG_PARTS = ("hip", "knee", "ankle", "ball", "toe")

# Keyable bone-length channel on the upper/lower FK ctrls (1 = rest), added
# by character_rig_builder.add_fk_length. Rigs built before it lack it.
LENGTH_ATTR = "length"

# Tolerance (world units) for "the IK end reached the FK pose".
_REACH_TOL = 1e-3


def _limb_info(prefix):
    """Return (parts, ik_end_part) for an arm/leg prefix, or (None, None)."""
    if prefix.endswith("_arm"):
        return _ARM_PARTS, "wrist"
    if prefix.endswith("_leg"):
        return _LEG_PARTS, "ankle"
    # A quadruped paw / claw leg stands on its wrist or ankle chain but has
    # no biped foot (ball + toe); matching it as an arm or leg would pop.
    if (cmds.objExists(f"{prefix}_toeBend_LOC")
            and not cmds.objExists(f"{prefix}_toe_BIND_JNT")):
        return None, None
    # Labelled extra limbs (L_lowerArm, R_midLeg...): tell arm from leg by
    # which end joint the chain has.
    if cmds.objExists(f"{prefix}_wrist_BIND_JNT"):
        return _ARM_PARTS, "wrist"
    if cmds.objExists(f"{prefix}_ankle_BIND_JNT"):
        return _LEG_PARTS, "ankle"
    return None, None


def ikfk_match(prefix):
    """Switch a limb between IK and FK keeping the same world pose.

    prefix: 'L_arm' / 'R_arm' / 'L_leg' / 'R_leg', or a labelled extra
    limb ('L_lowerArm'). Stretch carries across (see the module docstring).
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
        # IK pose) and the stretch on the two IK segments, then drive the
        # FK ctrls to reproduce both, root first.
        targets = {p: cmds.xform(bind[p], q=True, ws=True, ro=True)
                   for p in parts}
        lengths = _fk_length_targets(prefix, parts, bind)
        cmds.setAttr(switch_attr, 1)       # flip first so FK ctrls drive
        for plug, value in lengths.items():
            cmds.setAttr(plug, value)
        for p in parts:
            fk = f"{prefix}_{p}_FK_CTRL"
            if cmds.objExists(fk) and p in targets:
                cmds.xform(fk, ws=True, ro=targets[p])
        _key_if_autokey([f"{prefix}_{p}_FK_CTRL" for p in parts]
                        + list(lengths) + [switch_attr])
        return "FK"
    else:
        # FK -> IK: place the IK ctrl so the IK chain end lands on the end
        # BIND joint, and the pole vector behind the mid joint, then flip.
        ik_ctrl = f"{prefix}_IK_CTRL"
        pv_ctrl = f"{prefix}_PV_CTRL"
        end_bind = bind[ik_end]
        if cmds.objExists(ik_ctrl):
            foot = _foot_ctrl_matrix(prefix, ik_ctrl, bind) \
                if ik_end == "ankle" else None
            # Arm: the IK ctrl sits ON the wrist (built there, orient-
            # constrained with no offset), so copy the wrist matrix. Leg:
            # the foot ctrl is at the ground with the ankle under it via the
            # reverse-foot locators, so solve for where the ctrl must go.
            _set_world_matrix(ik_ctrl,
                              foot if foot is not None
                              else _get_world_matrix(end_bind))
        # Pole vector from the first three joints of the chain.
        if cmds.objExists(pv_ctrl):
            pv = _pole_position(bind[parts[0]], bind[parts[1]],
                                bind[parts[2]])
            cmds.xform(pv_ctrl, ws=True, t=pv)
        keys = [ik_ctrl, pv_ctrl, switch_attr]
        # End joint first; a leg adds ball + toe so the foot settles too.
        end_parts = parts[parts.index(ik_end):]
        ik_jnts = [f"{prefix}_{p}_IK_JNT" for p in end_parts]
        targets = [bind[p] for p in end_parts]
        stretch_attr = f"{settings}.autoStretch"
        if cmds.objExists(ik_ctrl) and all(cmds.objExists(j)
                                           for j in ik_jnts):
            miss = _settle_ik_end(ik_ctrl, ik_jnts, targets)
            # A lengthened FK limb is only reachable by a stretching chain.
            if (miss > _REACH_TOL
                    and _settable(settings, "autoStretch")
                    and cmds.getAttr(stretch_attr) < 1.0):
                cmds.setAttr(stretch_attr, 1.0)
                keys.append(stretch_attr)
                print(f"[ikfk] {prefix}: turned autoStretch on so the IK "
                      f"chain can reach the lengthened FK pose.")
                miss = _settle_ik_end(ik_ctrl, ik_jnts, targets)
            if miss > 0.01:
                cmds.warning(
                    f"[ikfk] {prefix}: IK can't reach the FK pose, the "
                    f"{ik_end} is off by {miss:.3f} (FK limb longer than "
                    f"the IK stretch allows).")
        cmds.setAttr(switch_attr, 0)
        _key_if_autokey(keys)
        return "IK"


def _wpos(node):
    return om.MVector(*cmds.xform(node, q=True, ws=True, t=True))


def _dist(a, b):
    return (_wpos(a) - _wpos(b)).length()


def _settle_ik_end(ik_ctrl, ik_jnts, targets, iterations=12):
    """Move the IK ctrl until the IK joints land on their BIND targets.

    ik_jnts / targets: parallel node lists, END joint first. With three
    (leg: ankle, ball, toe) the ctrl takes the rigid move carrying the IK
    foot triangle onto the BIND one, otherwise it just translates by the
    end joint's gap.

    Current rigs measure stretch from an anchor on the hip / shoulder, so
    the IK end already sits on its target and this returns after at most
    one step. Rigs built before that measured from the pelvis (or chest),
    so a stretched leg's ankle trails its foot ctrl and the foot tilts
    toward the ctrl's locators. The IK pose that made the FK pose had the
    ctrl sitting out ahead in exactly that way, so repeating the correction
    converges back to it. Stops as soon as a step doesn't help (pose out of
    reach). Returns the end joint's miss.
    """
    # Judge by end + ball only: the toe can legitimately stay off (an FK
    # toe bend IK can't make), but those two are always reachable together.
    def err():
        return max(_dist(a, b) for a, b in zip(ik_jnts[:2], targets[:2]))

    best = err()
    for _ in range(iterations):
        if best <= _REACH_TOL:
            break
        before = _get_world_matrix(ik_ctrl)
        cur = want = None
        if len(ik_jnts) == 3:
            cur = _frame(*[_wpos(n) for n in ik_jnts])
            want = _frame(*[_wpos(n) for n in targets])
        if cur is not None and want is not None:
            _set_world_matrix(ik_ctrl, before * cur.inverse() * want)
        else:
            gap = _wpos(targets[0]) - _wpos(ik_jnts[0])
            t = cmds.xform(ik_ctrl, q=True, ws=True, t=True)
            cmds.xform(ik_ctrl, ws=True,
                       t=(t[0] + gap.x, t[1] + gap.y, t[2] + gap.z))
        now = err()
        if now >= best - 1e-6:
            _set_world_matrix(ik_ctrl, before)
            break
        best = now
    return _dist(ik_jnts[0], targets[0])


def _fk_length_targets(prefix, parts, bind):
    """{FK ctrl.length plug: value} that makes the FK upper/lower segments
    as long as the BIND chain is right now (i.e. carries the IK stretch).

    Stretchy IK lengthens the IK joints' translateX and the BIND tx blend
    passes it on; FK lengthens the same bones through `length` on the
    parent FK ctrl. The FK joint's tx is proportional to that length, so
    the new value is  length * bindTx / fkTx.  Rigs without the channel
    can't hold stretch in FK: warn instead of silently drifting.
    """
    out, missing = {}, []
    for i in (1, 2):
        child = parts[i]
        owner = f"{prefix}_{parts[i - 1]}_FK_CTRL"
        fk_jnt = f"{prefix}_{child}_FK_JNT"
        if not (cmds.objExists(owner) and cmds.objExists(fk_jnt)):
            continue
        bind_tx = cmds.getAttr(f"{bind[child]}.translateX")
        fk_tx = cmds.getAttr(f"{fk_jnt}.translateX")
        if abs(fk_tx) < 1e-6:
            continue
        has = cmds.attributeQuery(LENGTH_ATTR, node=owner, exists=True)
        cur = cmds.getAttr(f"{owner}.{LENGTH_ATTR}") if has else 1.0
        want = cur * bind_tx / fk_tx
        if not has or not _settable(owner, LENGTH_ATTR):
            if abs(want - 1.0) > 1e-3:
                missing.append(owner)
            continue
        out[f"{owner}.{LENGTH_ATTR}"] = want
    if missing:
        cmds.warning(
            f"[ikfk] {prefix} is stretched but {', '.join(missing)} has no "
            f"'{LENGTH_ATTR}' channel, so FK can't hold the stretch and the "
            f"limb will shorten. Rebuild the rig to get FK length.")
    return out


def _frame(o, a, b):
    """Orthonormal world frame at point o: X toward a, Z normal to the
    o-a-b plane. None when the three points are (nearly) collinear."""
    x = a - o
    z = x ^ (b - o)
    if x.length() < 1e-6 or z.length() < 1e-6 * x.length():
        return None
    x, z = x.normal(), z.normal()
    y = z ^ x
    return om.MMatrix(((x.x, x.y, x.z, 0.0), (y.x, y.y, y.z, 0.0),
                       (z.x, z.y, z.z, 0.0), (o.x, o.y, o.z, 1.0)))


def _foot_ctrl_matrix(prefix, ik_ctrl, bind):
    """World matrix for a leg's foot IK ctrl that puts the reverse-foot
    ankle / ball / toe locators (which the three IK handles hang from) on
    the ankle / ball / toe BIND joints. The locators ride rigidly under the
    ctrl (current roll attrs included), so the ctrl takes the same rigid
    move that carries the locator triangle onto the joint triangle.
    None if this isn't a reverse-foot leg.
    """
    locs = [f"{prefix}_{n}_LOC" for n in ("ankle", "ballIK", "toe")]
    jnts = [bind.get(p) for p in ("ankle", "ball", "toe")]
    if not all(n and cmds.objExists(n) for n in locs + jnts):
        return None

    def pts(nodes):
        return [om.MVector(*cmds.xform(n, q=True, ws=True, t=True))
                for n in nodes]

    ctrl_m = _get_world_matrix(ik_ctrl)
    cur, want = _frame(*pts(locs)), _frame(*pts(jnts))
    if cur is None or want is None:
        # Degenerate foot: keep the ctrl's orientation, just move it so the
        # ankle locator lands on the ankle joint.
        delta = pts(jnts)[0] - pts(locs)[0]
        m = [ctrl_m.getElement(r, c) for r in range(4) for c in range(4)]
        m[12] += delta.x
        m[13] += delta.y
        m[14] += delta.z
        return om.MMatrix(m)
    # Rigid (scale-free) delta, so a globalScale'd ctrl keeps its scale.
    return ctrl_m * cur.inverse() * want


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
