"""
===============================================================================
 BIRD RIG BUILDER  (raptor template)
===============================================================================

Builds a feathered bird-of-prey rig — eagle / hawk proportions. Reuses the
orientation-agnostic biped modules (CoreRig, SpineRig, NeckHeadRig, FaceRig)
from character_rig_builder.py and adds bird-specific limbs and a fanned tail:

  * BirdWingRig — humerus / radius / manus 3-joint RP IK with FK switch,
                  pole vector, stretchy IK, AND fanned primary + secondary
                  feather rows per wing driven by a wingSpread macro attr.
  * BirdLegRig  — avian leg: femur / tibiotarsus / tarsometatarsus IK chain
                  (the visible "knee" is the ankle on a real bird), reverse-
                  foot ankle roll, four toes (three forward + one hallux at
                  the back) each with a talon tip + per-toe / global curl SDK.
  * TailFanRig  — small tail base + N fanned tail-feather joints, driven by
                  a tailSpread macro attribute on the tail-base settings ctrl.

Usage in Maya (Python):
    import bird_rig_builder
    from importlib import reload
    reload(bird_rig_builder)

    rig = bird_rig_builder.BirdRig()
    rig.build()

Top node: BIRD_RIG_GRP
===============================================================================
"""

import math
import maya.cmds as cmds

# Reuse helpers, constants and re-usable modules from the biped builder.
from character_rig_builder import (
    SCALE,
    COLOR_LEFT, COLOR_RIGHT, COLOR_CENTER, COLOR_IK, COLOR_PV,
    COLOR_BEND, COLOR_SETTINGS,
    make_offset_group, lock_hide_attrs, get_pole_vector_position,
    create_circle_ctrl, create_diamond_ctrl, create_cube_ctrl,
    create_square_ctrl, create_gear_ctrl, create_foot_ctrl,
    CoreRig, SpineRig, NeckHeadRig, FaceRig,
)


# =============================================================================
# BIRD WING RIG  (one wing — IK/FK + per-feather controls)
# =============================================================================

class BirdWingRig(object):
    """One bird wing with a 3-joint RP IK chain and a row of feather ctrls.

    Joint chain (BIND, mirrored as FK and IK):
        shoulder (humerus root) → elbow (humerus tip / radius root)
        → wrist (radius tip / manus root) → wingTip (leaf bone for manus tip)

    The wingTip joint is a leaf — it's NOT included in the IK chain. The IK
    solver runs shoulder → wrist (3 joints, RP solver). wingTip rotation is
    inherited from wrist, and the primaries fan out from along the manus.

    Settings ctrl attrs (`{prefix}_SETTINGS_CTRL`):
        ikFkSwitch       — 0 = IK, 1 = FK  (matches arm/leg convention)
        autoStretch      — 0/1, stretchy IK enable
        wingSpread       — 0 = folded along the wing, 1 = fully fanned
        primarySpread    — additive on top of wingSpread for primaries
        secondarySpread  — additive on top of wingSpread for secondaries
        featherVis       — bool, visibility of feather ctrls
    """

    DEFAULT_PRIMARY_COUNT   = 6   # primaries fan from the manus (wrist → tip)
    DEFAULT_SECONDARY_COUNT = 6   # secondaries fan from the radius (elbow → wrist)

    # When wingSpread=1, the i-th feather rotates this many degrees outward
    # from the wing bone. Per-feather angle = base_angle + i * step. The
    # outer feathers (toward the wingtip) spread MORE than the inner ones —
    # this matches the way a real bird's primaries flare out the most.
    PRIMARY_MIN_SPREAD   = 10.0
    PRIMARY_MAX_SPREAD   = 55.0
    SECONDARY_MIN_SPREAD = 8.0
    SECONDARY_MAX_SPREAD = 35.0

    def __init__(self, side, positions=None,
                 primary_count=None, secondary_count=None,
                 parent_ctrl=None, parent_jnt=None,
                 ctrl_grp=None, jnt_grp=None, misc_grp=None):
        assert side in ("L", "R")
        self.side = side
        self.prefix = f"{side}_wing"
        mult = 1 if side == "L" else -1
        # The elbow is intentionally OFF the shoulder-wrist line in both
        # Y (gull-wing up bend) and Z (slight forward sweep). This gives
        # the 3-joint RP IK a clear bend plane — without it the chain is
        # ~collinear and the elbow refuses to bend when the IK ctrl moves
        # (the solver can't pick a bend direction on a straight chain).
        self.positions = positions or {
            "shoulder": (  6 * mult,  80,  10),
            "elbow":    ( 50 * mult,  86,   3),   # +6 Y, +Z above the line
            "wrist":    ( 90 * mult,  82,  -5),
            "wingTip":  (125 * mult,  80, -12),
        }
        self.primary_count   = primary_count   or self.DEFAULT_PRIMARY_COUNT
        self.secondary_count = secondary_count or self.DEFAULT_SECONDARY_COUNT
        self.parent_ctrl = parent_ctrl
        self.parent_jnt  = parent_jnt
        self.ctrl_grp = ctrl_grp
        self.jnt_grp  = jnt_grp
        self.misc_grp = misc_grp
        self.color = COLOR_LEFT if side == "L" else COLOR_RIGHT

        # Outputs
        # bind_jnts: [shoulder, elbow, wrist, wingTip]  — wingTip is the leaf
        # fk_jnts / ik_jnts: parallel chains of length 3 (no wingTip clone)
        self.bind_jnts = []
        self.fk_jnts   = []
        self.ik_jnts   = []
        self.fk_ctrls, self.fk_offsets = [], []
        self.ik_ctrl = self.ik_offset = None
        self.pv_ctrl = self.pv_offset = None
        self.settings_ctrl = None
        self.ik_handle = None
        # Feather lists — each entry is dict(jnt=..., ctrl=..., auto=...).
        self.primary_feathers   = []
        self.secondary_feathers = []

    # -----------------------------------------------------------------------

    def build(self):
        self._create_joint_chains()
        self._create_fk()
        self._create_ik()
        self._create_settings_ctrl()
        self._create_stretchy_ik()
        self._create_ikfk_blend()
        self._create_visibility_sdk()
        self._create_feathers()
        cmds.setAttr(f"{self.ik_jnts[0]}.v", 0)
        cmds.setAttr(f"{self.fk_jnts[0]}.v", 0)

    # -----------------------------------------------------------------------
    # Joint chains
    # -----------------------------------------------------------------------

    def _create_joint_chains(self):
        # BIND: shoulder → elbow → wrist → wingTip (leaf)
        cmds.select(cl=True)
        for part in ("shoulder", "elbow", "wrist", "wingTip"):
            j = cmds.joint(n=f"{self.prefix}_{part}_BIND_JNT",
                           p=self.positions[part])
            self.bind_jnts.append(j)
        cmds.joint(self.bind_jnts[0], e=True, oj="xyz", sao="yup",
                   ch=True, zso=True)
        cmds.setAttr(f"{self.bind_jnts[-1]}.jointOrient", 0, 0, 0)
        cmds.parent(self.bind_jnts[0], self.parent_jnt)

        # FK and IK chains — only the first three joints (no wingTip clone).
        # The wingTip rides on the wrist BIND joint's child transform, so no
        # parallel needed; IK solves shoulder → wrist.
        self.fk_jnts = self._duplicate_chain(self.bind_jnts[:3], "FK")
        self.ik_jnts = self._duplicate_chain(self.bind_jnts[:3], "IK")

    def _duplicate_chain(self, source, suffix):
        new_chain = []
        cmds.select(cl=True)
        for jnt in source:
            pos = cmds.xform(jnt, q=True, ws=True, t=True)
            new_name = jnt.replace("_BIND_", f"_{suffix}_")
            new_chain.append(cmds.joint(n=new_name, p=pos))
        cmds.joint(new_chain[0], e=True, oj="xyz", sao="yup",
                   ch=True, zso=True)
        cmds.setAttr(f"{new_chain[-1]}.jointOrient", 0, 0, 0)
        # Same parent as the BIND chain — required so FK/IK/BIND share
        # an identical parent space and the orientConstraint blend works.
        cmds.parent(new_chain[0], self.parent_jnt)
        return new_chain

    # -----------------------------------------------------------------------
    # FK
    # -----------------------------------------------------------------------

    def _create_fk(self):
        for i, jnt in enumerate(self.fk_jnts):
            ctrl = create_circle_ctrl(
                jnt.replace("_JNT", "_CTRL"), radius=1.0 * SCALE,
                normal=(1, 0, 0), color=self.color,
            )
            cmds.matchTransform(ctrl, jnt)
            offset = make_offset_group(ctrl)
            if i > 0:
                cmds.parent(offset, self.fk_ctrls[i - 1])
            else:
                cmds.parent(offset, self.parent_ctrl)
            cmds.parentConstraint(ctrl, jnt, mo=True)
            lock_hide_attrs(ctrl, ["tx", "ty", "tz",
                                   "sx", "sy", "sz", "v"])
            self.fk_ctrls.append(ctrl)
            self.fk_offsets.append(offset)

    # -----------------------------------------------------------------------
    # IK
    # -----------------------------------------------------------------------

    def _create_ik(self):
        self.ik_handle = cmds.ikHandle(
            sj=self.ik_jnts[0], ee=self.ik_jnts[-1],
            sol="ikRPsolver", n=f"{self.prefix}_ikHandle",
        )[0]
        cmds.setAttr(f"{self.ik_handle}.v", 0)

        self.ik_ctrl = create_cube_ctrl(
            f"{self.prefix}_IK_CTRL", size=1.5 * SCALE, color=COLOR_IK,
        )
        cmds.matchTransform(self.ik_ctrl, self.ik_jnts[-1])
        self.ik_offset = make_offset_group(self.ik_ctrl)
        cmds.parent(self.ik_offset, self.ctrl_grp)
        cmds.parent(self.ik_handle, self.ik_ctrl)
        cmds.orientConstraint(self.ik_ctrl, self.ik_jnts[-1], mo=True)

        pv_pos = get_pole_vector_position(self.ik_jnts[0],
                                           self.ik_jnts[1],
                                           self.ik_jnts[2],
                                           distance=5.0 * SCALE)
        self.pv_ctrl = create_diamond_ctrl(
            f"{self.prefix}_PV_CTRL", size=0.6 * SCALE, color=COLOR_PV,
        )
        cmds.xform(self.pv_ctrl, ws=True, t=pv_pos)
        self.pv_offset = make_offset_group(self.pv_ctrl)
        cmds.parent(self.pv_offset, self.ctrl_grp)
        cmds.poleVectorConstraint(self.pv_ctrl, self.ik_handle)

        lock_hide_attrs(self.ik_ctrl, ["sx", "sy", "sz", "v"])
        lock_hide_attrs(self.pv_ctrl, ["rx", "ry", "rz",
                                        "sx", "sy", "sz", "v"])

    # -----------------------------------------------------------------------
    # Settings ctrl
    # -----------------------------------------------------------------------

    def _create_settings_ctrl(self):
        self.settings_ctrl = create_gear_ctrl(
            f"{self.prefix}_SETTINGS_CTRL", size=0.5 * SCALE,
            color=COLOR_SETTINGS,
        )
        # Place above the wrist by a few units in Y so it's easy to grab.
        cmds.matchTransform(self.settings_ctrl, self.bind_jnts[2],
                             pos=True, rot=False)
        cmds.move(0, 1.5 * SCALE, 0, self.settings_ctrl, r=True, ws=True)
        offset = make_offset_group(self.settings_ctrl)
        cmds.parent(offset, self.ctrl_grp)
        cmds.parentConstraint(self.bind_jnts[2], offset, mo=True)

        cmds.addAttr(self.settings_ctrl, ln="ikFkSwitch", at="double",
                     min=0, max=1, dv=0, k=True)
        cmds.addAttr(self.settings_ctrl, ln="autoStretch", at="double",
                     min=0, max=1, dv=1, k=True)
        # Feather header (display-only enum divider).
        cmds.addAttr(self.settings_ctrl, ln="feathers", at="enum",
                     en="---------:", k=True)
        cmds.setAttr(f"{self.settings_ctrl}.feathers",
                     l=True, cb=True, k=False)
        cmds.addAttr(self.settings_ctrl, ln="wingSpread", at="double",
                     min=0, max=1, dv=0, k=True)
        cmds.addAttr(self.settings_ctrl, ln="primarySpread", at="double",
                     min=-1, max=1, dv=0, k=True)
        cmds.addAttr(self.settings_ctrl, ln="secondarySpread", at="double",
                     min=-1, max=1, dv=0, k=True)
        cmds.addAttr(self.settings_ctrl, ln="featherVis", at="bool",
                     dv=True, k=True)

        lock_hide_attrs(self.settings_ctrl,
                        ["tx", "ty", "tz", "rx", "ry", "rz",
                         "sx", "sy", "sz", "v"])

    # -----------------------------------------------------------------------
    # Stretchy IK  (identical math to ArmRig._create_stretchy_ik)
    # -----------------------------------------------------------------------

    def _create_stretchy_ik(self):
        elbow_t = cmds.getAttr(f"{self.ik_jnts[1]}.translateX")
        wrist_t = cmds.getAttr(f"{self.ik_jnts[2]}.translateX")

        parent_pos = cmds.xform(self.parent_jnt, q=True, ws=True, t=True)
        ctrl_pos   = cmds.xform(self.ik_ctrl,    q=True, ws=True, t=True)
        rest_length = math.sqrt(sum((p - c) ** 2
                                     for p, c in zip(parent_pos, ctrl_pos)))

        dist = cmds.createNode("distanceBetween",
                                n=f"{self.prefix}_stretch_DIST")
        cmds.connectAttr(f"{self.parent_jnt}.worldMatrix[0]",
                          f"{dist}.inMatrix1")
        cmds.connectAttr(f"{self.ik_ctrl}.worldMatrix[0]",
                          f"{dist}.inMatrix2")

        norm_dist = cmds.createNode("multiplyDivide",
                                     n=f"{self.prefix}_stretch_NORM")
        cmds.setAttr(f"{norm_dist}.operation", 2)
        cmds.connectAttr(f"{dist}.distance", f"{norm_dist}.input1X")
        if cmds.objExists("C_global_CTRL.globalScale"):
            cmds.connectAttr("C_global_CTRL.globalScale",
                              f"{norm_dist}.input2X")
        else:
            cmds.setAttr(f"{norm_dist}.input2X", 1.0)

        ratio = cmds.createNode("multiplyDivide",
                                 n=f"{self.prefix}_stretch_RATIO")
        cmds.setAttr(f"{ratio}.operation", 2)
        cmds.connectAttr(f"{norm_dist}.outputX", f"{ratio}.input1X")
        cmds.setAttr(f"{ratio}.input2X", rest_length)

        cond = cmds.createNode("condition",
                                n=f"{self.prefix}_stretch_COND")
        cmds.setAttr(f"{cond}.operation", 2)
        cmds.connectAttr(f"{ratio}.outputX", f"{cond}.firstTerm")
        cmds.setAttr(f"{cond}.secondTerm", 1.0)
        cmds.connectAttr(f"{ratio}.outputX", f"{cond}.colorIfTrueR")
        cmds.setAttr(f"{cond}.colorIfFalseR", 1.0)

        blend = cmds.createNode("blendTwoAttr",
                                 n=f"{self.prefix}_stretch_BLEND")
        cmds.setAttr(f"{blend}.input[0]", 1.0)
        cmds.connectAttr(f"{cond}.outColorR", f"{blend}.input[1]")
        cmds.connectAttr(f"{self.settings_ctrl}.autoStretch",
                          f"{blend}.attributesBlender")

        for i, t in enumerate((elbow_t, wrist_t), start=1):
            m = cmds.createNode("multDoubleLinear",
                                 n=f"{self.prefix}_stretch_{i}_MULT")
            cmds.connectAttr(f"{blend}.output", f"{m}.input1")
            cmds.setAttr(f"{m}.input2", t)
            cmds.connectAttr(f"{m}.output",
                              f"{self.ik_jnts[i]}.translateX", f=True)

    # -----------------------------------------------------------------------
    # IK / FK blend
    # -----------------------------------------------------------------------

    def _create_ikfk_blend(self):
        rev = cmds.createNode("reverse", n=f"{self.prefix}_ikfk_REV")
        cmds.connectAttr(f"{self.settings_ctrl}.ikFkSwitch", f"{rev}.inputX")

        # Only the 3 IK/FK joints participate in the blend; the leaf wingTip
        # rides on bind_jnts[2] (wrist) by simple parent-child.
        for i in range(3):
            bind = self.bind_jnts[i]
            fk   = self.fk_jnts[i]
            ik   = self.ik_jnts[i]

            oc = cmds.orientConstraint(fk, ik, bind, mo=True)[0]
            cmds.setAttr(f"{oc}.interpType", 2)  # shortest path
            weights = cmds.orientConstraint(oc, q=True, wal=True)
            cmds.connectAttr(f"{self.settings_ctrl}.ikFkSwitch",
                              f"{oc}.{weights[0]}")
            cmds.connectAttr(f"{rev}.outputX", f"{oc}.{weights[1]}")

            if i > 0:
                tx_blend = cmds.createNode("blendTwoAttr",
                                            n=f"{bind}_tx_BLEND")
                cmds.connectAttr(f"{fk}.translateX",
                                  f"{tx_blend}.input[0]")
                cmds.connectAttr(f"{ik}.translateX",
                                  f"{tx_blend}.input[1]")
                cmds.connectAttr(f"{rev}.outputX",
                                  f"{tx_blend}.attributesBlender")
                cmds.connectAttr(f"{tx_blend}.output",
                                  f"{bind}.translateX", f=True)

    # -----------------------------------------------------------------------
    # Visibility SDK
    # -----------------------------------------------------------------------

    def _create_visibility_sdk(self):
        driver = f"{self.settings_ctrl}.ikFkSwitch"
        for ctrl in (self.ik_ctrl, self.pv_ctrl):
            cmds.setAttr(f"{ctrl}.v", l=False)
            cmds.setDrivenKeyframe(f"{ctrl}.v", cd=driver, dv=0.0, v=1,
                                    itt="linear", ott="step")
            cmds.setDrivenKeyframe(f"{ctrl}.v", cd=driver, dv=0.999, v=1,
                                    itt="linear", ott="step")
            cmds.setDrivenKeyframe(f"{ctrl}.v", cd=driver, dv=1.0, v=0,
                                    itt="linear", ott="step")
        for ctrl in self.fk_ctrls:
            cmds.setAttr(f"{ctrl}.v", l=False)
            cmds.setDrivenKeyframe(f"{ctrl}.v", cd=driver, dv=0.0, v=0,
                                    itt="linear", ott="step")
            cmds.setDrivenKeyframe(f"{ctrl}.v", cd=driver, dv=0.001, v=1,
                                    itt="linear", ott="step")
            cmds.setDrivenKeyframe(f"{ctrl}.v", cd=driver, dv=1.0, v=1,
                                    itt="linear", ott="step")

    # -----------------------------------------------------------------------
    # Feathers
    # -----------------------------------------------------------------------

    def _create_feathers(self):
        """Build primary + secondary feather rows.

        Primaries fan out from the manus (between wrist and wingTip).
        Secondaries fan out from the radius (between elbow and wrist).

        Each feather is a small BIND joint child of the corresponding wing
        BIND joint (wrist for primaries, elbow for secondaries) so it
        follows the IK/FK blend automatically. A small FK ctrl above an
        AUTO group lets the animator manually pose individual feathers.
        SDK drives AUTO.rotateY from wingSpread + per-row spread:

            AUTO.rotateY = (wingSpread + rowSpread) × per-feather angle

        At wingSpread=0 and rowSpread=0 the feather AUTO is at 0° (folded
        along the wing); at wingSpread=1 the i-th feather is rotated by
        an angle that ramps from MIN_SPREAD at the inner feather up to
        MAX_SPREAD at the outermost — matching how a real bird flares its
        primaries the most.
        """
        # Primaries: along the manus (wrist → wingTip), child of wrist BIND.
        self.primary_feathers = self._build_feather_row(
            row_name="primary",
            count=self.primary_count,
            start_jnt=self.bind_jnts[2],   # wrist
            end_jnt=self.bind_jnts[3],     # wingTip
            parent_jnt=self.bind_jnts[2],  # wrist  (follows IK/FK blend)
            parent_ctrl=self.fk_ctrls[2],  # wrist FK ctrl  (animator-visible parent)
            min_angle=self.PRIMARY_MIN_SPREAD,
            max_angle=self.PRIMARY_MAX_SPREAD,
            row_attr="primarySpread",
        )
        # Secondaries: along the radius (elbow → wrist), child of elbow BIND.
        self.secondary_feathers = self._build_feather_row(
            row_name="secondary",
            count=self.secondary_count,
            start_jnt=self.bind_jnts[1],   # elbow
            end_jnt=self.bind_jnts[2],     # wrist
            parent_jnt=self.bind_jnts[1],  # elbow
            parent_ctrl=self.fk_ctrls[1],  # elbow FK ctrl
            min_angle=self.SECONDARY_MIN_SPREAD,
            max_angle=self.SECONDARY_MAX_SPREAD,
            row_attr="secondarySpread",
        )

    def _build_feather_row(self, row_name, count, start_jnt, end_jnt,
                            parent_jnt, parent_ctrl,
                            min_angle, max_angle, row_attr):
        """Build one row of N feathers along the segment start_jnt → end_jnt.

        Returns a list of dict(jnt=..., ctrl=..., auto=...).
        """
        side_mult = 1 if self.side == "L" else -1
        start_pos = cmds.xform(start_jnt, q=True, ws=True, t=True)
        end_pos   = cmds.xform(end_jnt,   q=True, ws=True, t=True)
        # Direction along the bone (used to space feathers).
        bone_vec = tuple(e - s for s, e in zip(start_pos, end_pos))

        # Feather visibility container — parented to the parent BIND joint
        # so all the feather ctrls stay in one outliner group per row.
        row_grp = cmds.group(em=True,
                              n=f"{self.prefix}_{row_name}_feathers_GRP")
        cmds.matchTransform(row_grp, parent_ctrl)
        cmds.parent(row_grp, parent_ctrl)

        # Drive feather row visibility from settings_ctrl.featherVis.
        cmds.connectAttr(f"{self.settings_ctrl}.featherVis",
                          f"{row_grp}.v", f=True)

        out = []
        for i in range(count):
            # Spacing 0.1 → 0.9 along the bone so feathers don't crowd the joints.
            t = 0.1 + (0.8 * (i / float(count - 1))) if count > 1 else 0.5
            pos = tuple(s + t * v for s, v in zip(start_pos, bone_vec))

            # ---- BIND feather joint ----
            cmds.select(cl=True)
            slot = f"{row_name}{i + 1:02d}"
            jnt = cmds.joint(n=f"{self.prefix}_{slot}_BIND_JNT", p=pos)
            cmds.setAttr(f"{jnt}.jointOrient", 0, 0, 0)
            cmds.setAttr(f"{jnt}.radius", 0.25 * SCALE)
            cmds.parent(jnt, parent_jnt)

            # ---- FK ctrl + AUTO group above ----
            ctrl = create_square_ctrl(
                f"{self.prefix}_{slot}_CTRL",
                size=0.5 * SCALE, normal=(0, 1, 0), color=COLOR_BEND,
            )
            cmds.matchTransform(ctrl, jnt, pos=True, rot=False)
            offset = make_offset_group(ctrl)
            auto = cmds.group(em=True,
                               n=f"{self.prefix}_{slot}_AUTO")
            cmds.matchTransform(auto, jnt)
            cmds.parent(auto, offset)
            cmds.parent(ctrl, auto)
            cmds.parent(offset, row_grp)

            cmds.parentConstraint(ctrl, jnt, mo=True)
            lock_hide_attrs(ctrl, ["tx", "ty", "tz",
                                    "sx", "sy", "sz", "v"])

            # ---- SDK: AUTO.rotateY = (wingSpread + rowSpread) × per-feather angle
            # The angle ramps from min_angle (inner feather) to max_angle (outer).
            angle_norm = (i / float(count - 1)) if count > 1 else 0.5
            per_angle = (min_angle
                         + angle_norm * (max_angle - min_angle))
            # Flip sign for the right side so feathers spread outward
            # (mirrored), not into the body.
            per_angle *= side_mult

            sum_node = cmds.createNode(
                "addDoubleLinear",
                n=f"{self.prefix}_{slot}_spreadSum_ADL")
            cmds.connectAttr(f"{self.settings_ctrl}.wingSpread",
                              f"{sum_node}.input1")
            cmds.connectAttr(f"{self.settings_ctrl}.{row_attr}",
                              f"{sum_node}.input2")

            mul = cmds.createNode(
                "multDoubleLinear",
                n=f"{self.prefix}_{slot}_spread_MUL")
            cmds.connectAttr(f"{sum_node}.output", f"{mul}.input1")
            cmds.setAttr(f"{mul}.input2", per_angle)
            cmds.connectAttr(f"{mul}.output", f"{auto}.rotateY", f=True)

            out.append({"jnt": jnt, "ctrl": ctrl, "auto": auto})

        return out


# =============================================================================
# BIRD LEG RIG  (one leg — avian anatomy: femur / tibiotarsus / tarsometatarsus)
# =============================================================================

class BirdLegRig(object):
    """One bird leg with avian anatomy and four toes.

    Joint chain (BIND, mirrored as FK and IK):
        femur (true hip — usually hidden up in body feathers)
        → tibiotarsus (the "drumstick" — the topmost VISIBLE leg segment)
        → tarsometatarsus (what most people call the "shin" on a bird —
          this is actually fused foot bones, and the joint above it is the
          true ankle, NOT a knee, which is why birds appear to bend
          "backwards" at the leg)
        → foot (ball of the foot, where the toes meet)
        → footTip (leaf bone in front of the foot — toes pivot from here)

    The IK chain is the first 3 joints (femur → tibiotarsus → tarsometatarsus).
    The ankle joint (between tarsometatarsus and foot) drives the foot roll
    via a reverse-foot setup similar to the biped LegRig.

    Toes (4 per foot, standard avian):
        digit2, digit3, digit4 — three forward-facing toes
        hallux                  — single backward-facing toe

    Each toe is a 2-segment FK chain (proximal + distal) + a talon tip leaf.
    Per-toe `{toe}Curl` attributes on the settings ctrl curl that toe;
    `taloneCurl` (global) curls all four together for a gripping pose.
    """

    TOE_NAMES = ["digit2", "digit3", "digit4", "hallux"]

    def __init__(self, side, positions=None,
                 parent_ctrl=None, parent_jnt=None,
                 ctrl_grp=None, jnt_grp=None, misc_grp=None):
        assert side in ("L", "R")
        self.side = side
        self.prefix = f"{side}_birdLeg"
        mult = 1 if side == "L" else -1
        self.positions = positions or self._default_positions(mult)
        self.parent_ctrl = parent_ctrl
        self.parent_jnt  = parent_jnt
        self.ctrl_grp = ctrl_grp
        self.jnt_grp  = jnt_grp
        self.misc_grp = misc_grp
        self.color = COLOR_LEFT if side == "L" else COLOR_RIGHT

        # Storage
        self.bind_jnts = []     # [femur, tibiotarsus, tarsometatarsus, foot, footTip]
        self.fk_jnts   = []     # first 3 only (FK clone)
        self.ik_jnts   = []     # first 3 only (IK clone)
        self.fk_ctrls = []
        self.ik_ctrl = self.pv_ctrl = self.settings_ctrl = None
        self.ik_handle_main = None    # femur → tarsometatarsus
        self.ik_handle_ankle = None   # tarsometatarsus → foot (SC)
        self.ik_handle_foot  = None   # foot → footTip (SC)
        # Toes — dict[toe_name] -> dict(jnts=[...], ctrls=[...], autos=[...])
        self.toes = {}

    @staticmethod
    def _default_positions(mult):
        # Raptor-ish defaults — eagle scale. Femur is angled back+down so
        # it tucks into the body; tibiotarsus drops down from the hip;
        # tarsometatarsus is the visible "shin"; foot is at ankle level.
        return {
            "femur":           ( 6 * mult, 55, -10),
            "tibiotarsus":     ( 8 * mult, 35,  -4),
            "tarsometatarsus": (10 * mult, 18,  -8),
            "foot":            (10 * mult,  4,  -4),
            "footTip":         (10 * mult,  0,   6),
            # Toes — each is base + tip in world coords. The base is the
            # metatarsal-phalangeal joint (where the toe meets the foot).
            "digit2": {
                "base": (16 * mult, 2,  6),
                "tip":  (20 * mult, 0, 14),
            },
            "digit3": {
                "base": (10 * mult, 2,  6),
                "tip":  (10 * mult, 0, 18),
            },
            "digit4": {
                "base": ( 4 * mult, 2,  6),
                "tip":  ( 0 * mult, 0, 14),
            },
            "hallux": {
                "base": (10 * mult, 2, -2),
                "tip":  (10 * mult, 0, -8),
            },
        }

    # -----------------------------------------------------------------------

    def build(self):
        self._create_joint_chains()
        self._create_fk()
        self._create_ik()
        self._create_reverse_foot()
        self._create_settings_ctrl()
        self._create_ikfk_blend()
        self._create_visibility_sdk()
        self._create_toes()
        cmds.setAttr(f"{self.ik_jnts[0]}.v", 0)
        cmds.setAttr(f"{self.fk_jnts[0]}.v", 0)

    # -----------------------------------------------------------------------

    _MAIN_SLOTS = ["femur", "tibiotarsus", "tarsometatarsus", "foot", "footTip"]

    def _create_joint_chains(self):
        cmds.select(cl=True)
        for part in self._MAIN_SLOTS:
            j = cmds.joint(n=f"{self.prefix}_{part}_BIND_JNT",
                           p=self.positions[part])
            self.bind_jnts.append(j)
        cmds.joint(self.bind_jnts[0], e=True, oj="xyz", sao="yup",
                    ch=True, zso=True)
        cmds.setAttr(f"{self.bind_jnts[-1]}.jointOrient", 0, 0, 0)
        cmds.parent(self.bind_jnts[0], self.parent_jnt)

        # FK + IK chains are the first 3 (femur → tibiotarsus → tarsometatarsus).
        # The foot joint is driven by the reverse-foot setup, not by an
        # FK/IK blend — same as the biped LegRig ankle.
        self.fk_jnts = self._duplicate_chain(self.bind_jnts[:3], "FK")
        self.ik_jnts = self._duplicate_chain(self.bind_jnts[:3], "IK")

    def _duplicate_chain(self, source, suffix):
        new_chain = []
        cmds.select(cl=True)
        for jnt in source:
            pos = cmds.xform(jnt, q=True, ws=True, t=True)
            new_name = jnt.replace("_BIND_", f"_{suffix}_")
            new_chain.append(cmds.joint(n=new_name, p=pos))
        cmds.joint(new_chain[0], e=True, oj="xyz", sao="yup",
                    ch=True, zso=True)
        cmds.setAttr(f"{new_chain[-1]}.jointOrient", 0, 0, 0)
        cmds.parent(new_chain[0], self.parent_jnt)
        return new_chain

    # -----------------------------------------------------------------------

    def _create_fk(self):
        for i, jnt in enumerate(self.fk_jnts):
            ctrl = create_circle_ctrl(
                jnt.replace("_JNT", "_CTRL"), radius=1.0 * SCALE,
                normal=(1, 0, 0), color=self.color,
            )
            cmds.matchTransform(ctrl, jnt)
            offset = make_offset_group(ctrl)
            if i > 0:
                cmds.parent(offset, self.fk_ctrls[i - 1])
            else:
                cmds.parent(offset, self.parent_ctrl)
            cmds.parentConstraint(ctrl, jnt, mo=True)
            lock_hide_attrs(ctrl, ["tx", "ty", "tz",
                                    "sx", "sy", "sz", "v"])
            self.fk_ctrls.append(ctrl)

    # -----------------------------------------------------------------------

    def _create_ik(self):
        # Main IK: femur → tarsometatarsus (3-joint RP).
        self.ik_handle_main = cmds.ikHandle(
            sj=self.ik_jnts[0], ee=self.ik_jnts[-1],
            sol="ikRPsolver", n=f"{self.prefix}_ikMainHandle",
        )[0]
        cmds.setAttr(f"{self.ik_handle_main}.v", 0)

        # IK foot ctrl — boot-shaped flat curve at the foot position.
        self.ik_ctrl = create_foot_ctrl(
            f"{self.prefix}_IK_CTRL", size=1.5 * SCALE, color=COLOR_IK,
        )
        cmds.matchTransform(self.ik_ctrl, self.bind_jnts[3],
                             pos=True, rot=False)
        self.ik_offset = make_offset_group(self.ik_ctrl)
        cmds.parent(self.ik_offset, self.ctrl_grp)

        # Pole vector at the knee/ankle bend.
        pv_pos = get_pole_vector_position(self.ik_jnts[0],
                                           self.ik_jnts[1],
                                           self.ik_jnts[2],
                                           distance=5.0 * SCALE)
        self.pv_ctrl = create_diamond_ctrl(
            f"{self.prefix}_PV_CTRL", size=0.6 * SCALE, color=COLOR_PV,
        )
        cmds.xform(self.pv_ctrl, ws=True, t=pv_pos)
        self.pv_offset = make_offset_group(self.pv_ctrl)
        cmds.parent(self.pv_offset, self.ctrl_grp)
        cmds.poleVectorConstraint(self.pv_ctrl, self.ik_handle_main)

        lock_hide_attrs(self.ik_ctrl, ["sx", "sy", "sz", "v"])
        lock_hide_attrs(self.pv_ctrl, ["rx", "ry", "rz",
                                         "sx", "sy", "sz", "v"])

    # -----------------------------------------------------------------------

    def _create_reverse_foot(self):
        """Simple reverse-foot for the bird's ankle roll.

        Two SC IK handles:
            ankle:  tarsometatarsus_BIND → foot_BIND     (drives foot pose)
            foot :  foot_BIND            → footTip_BIND  (drives toe pivot)

        A small chain of "reverse pivots" — heel, footTip, ankle — sits
        under the IK ctrl. Animator-facing attrs on the IK ctrl:
            footRoll  — heel (negative) ↔ ball/toe roll (positive)
            heelTwist — twists heel pivot around Y
            tipTwist  — twists footTip pivot around Y

        The reverse-foot puts the IK handle for the main leg under the
        ankle pivot so when the foot rolls, the main IK chain follows
        automatically.
        """
        # SC IK handles — direct connections (BIND → BIND), no clone chain.
        # They follow the IK_handle_main once we re-parent everything.
        self.ik_handle_ankle = cmds.ikHandle(
            sj=self.bind_jnts[2], ee=self.bind_jnts[3],
            sol="ikSCsolver", n=f"{self.prefix}_ikAnkleHandle",
        )[0]
        self.ik_handle_foot = cmds.ikHandle(
            sj=self.bind_jnts[3], ee=self.bind_jnts[4],
            sol="ikSCsolver", n=f"{self.prefix}_ikFootHandle",
        )[0]
        for h in (self.ik_handle_ankle, self.ik_handle_foot):
            cmds.setAttr(f"{h}.v", 0)

        # Pivot locators — simple empty transforms.
        heel_pos = (cmds.getAttr(f"{self.bind_jnts[3]}.tx"),
                     0.0,
                     cmds.getAttr(f"{self.bind_jnts[3]}.tz") - 4.0)
        # Use world positions instead:
        foot_world = cmds.xform(self.bind_jnts[3], q=True, ws=True, t=True)
        tip_world  = cmds.xform(self.bind_jnts[4], q=True, ws=True, t=True)
        heel_world = (foot_world[0], 0.0, foot_world[2] - 4.0)

        heel_piv = cmds.group(em=True, n=f"{self.prefix}_heelPivot_LOC")
        cmds.xform(heel_piv, ws=True, t=heel_world)
        tip_piv = cmds.group(em=True, n=f"{self.prefix}_tipPivot_LOC")
        cmds.xform(tip_piv, ws=True, t=tip_world)
        ankle_piv = cmds.group(em=True, n=f"{self.prefix}_anklePivot_LOC")
        cmds.xform(ankle_piv, ws=True, t=foot_world)

        # Reverse-foot chain: ik_ctrl → heel_piv → tip_piv → ankle_piv
        cmds.parent(heel_piv, self.ik_ctrl)
        cmds.parent(tip_piv, heel_piv)
        cmds.parent(ankle_piv, tip_piv)

        # Park the IK handles + ankle handle under the appropriate pivots:
        # - The toe SC handle parents under the tip_piv so it stays at the
        #   foot's leading edge.
        # - The ankle SC handle parents under the ankle_piv.
        # - The MAIN leg IK handle ALSO parents under the ankle_piv so the
        #   whole leg solves to wherever the ankle pivot lands.
        cmds.parent(self.ik_handle_foot, tip_piv)
        cmds.parent(self.ik_handle_ankle, ankle_piv)
        cmds.parent(self.ik_handle_main, ankle_piv)

        # ---- Foot attrs on the IK ctrl ----
        cmds.addAttr(self.ik_ctrl, ln="footRoll", at="double",
                      min=-45, max=90, dv=0, k=True)
        cmds.addAttr(self.ik_ctrl, ln="heelTwist", at="double",
                      min=-90, max=90, dv=0, k=True)
        cmds.addAttr(self.ik_ctrl, ln="tipTwist", at="double",
                      min=-90, max=90, dv=0, k=True)

        # footRoll < 0 → heel rotates back; > 0 → tip pivot rolls forward.
        cond_pos = cmds.createNode("condition",
                                    n=f"{self.prefix}_rollPos_COND")
        cmds.setAttr(f"{cond_pos}.operation", 2)  # greater-than
        cmds.connectAttr(f"{self.ik_ctrl}.footRoll", f"{cond_pos}.firstTerm")
        cmds.setAttr(f"{cond_pos}.secondTerm", 0)
        cmds.connectAttr(f"{self.ik_ctrl}.footRoll", f"{cond_pos}.colorIfTrueR")
        cmds.setAttr(f"{cond_pos}.colorIfFalseR", 0)

        cond_neg = cmds.createNode("condition",
                                    n=f"{self.prefix}_rollNeg_COND")
        cmds.setAttr(f"{cond_neg}.operation", 4)  # less-than
        cmds.connectAttr(f"{self.ik_ctrl}.footRoll", f"{cond_neg}.firstTerm")
        cmds.setAttr(f"{cond_neg}.secondTerm", 0)
        cmds.connectAttr(f"{self.ik_ctrl}.footRoll", f"{cond_neg}.colorIfTrueR")
        cmds.setAttr(f"{cond_neg}.colorIfFalseR", 0)

        cmds.connectAttr(f"{cond_pos}.outColorR", f"{tip_piv}.rotateX")
        cmds.connectAttr(f"{cond_neg}.outColorR", f"{heel_piv}.rotateX")
        cmds.connectAttr(f"{self.ik_ctrl}.heelTwist", f"{heel_piv}.rotateY")
        cmds.connectAttr(f"{self.ik_ctrl}.tipTwist",  f"{tip_piv}.rotateY")

    # -----------------------------------------------------------------------

    def _create_settings_ctrl(self):
        self.settings_ctrl = create_gear_ctrl(
            f"{self.prefix}_SETTINGS_CTRL", size=0.5 * SCALE,
            color=COLOR_SETTINGS,
        )
        cmds.matchTransform(self.settings_ctrl, self.bind_jnts[2],
                             pos=True, rot=False)
        cmds.move(0, 0, 2.0 * SCALE, self.settings_ctrl, r=True, ws=True)
        offset = make_offset_group(self.settings_ctrl)
        cmds.parent(offset, self.ctrl_grp)
        cmds.parentConstraint(self.bind_jnts[2], offset, mo=True)

        cmds.addAttr(self.settings_ctrl, ln="ikFkSwitch", at="double",
                      min=0, max=1, dv=0, k=True)
        # Talon header
        cmds.addAttr(self.settings_ctrl, ln="talons", at="enum",
                      en="---------:", k=True)
        cmds.setAttr(f"{self.settings_ctrl}.talons",
                      l=True, cb=True, k=False)
        cmds.addAttr(self.settings_ctrl, ln="talonCurl", at="double",
                      min=-2, max=10, dv=0, k=True)
        for toe in self.TOE_NAMES:
            cmds.addAttr(self.settings_ctrl, ln=f"{toe}Curl", at="double",
                          min=-2, max=10, dv=0, k=True)

        lock_hide_attrs(self.settings_ctrl,
                         ["tx", "ty", "tz", "rx", "ry", "rz",
                          "sx", "sy", "sz", "v"])

    # -----------------------------------------------------------------------

    def _create_ikfk_blend(self):
        rev = cmds.createNode("reverse", n=f"{self.prefix}_ikfk_REV")
        cmds.connectAttr(f"{self.settings_ctrl}.ikFkSwitch", f"{rev}.inputX")

        for i in range(3):
            bind = self.bind_jnts[i]
            fk   = self.fk_jnts[i]
            ik   = self.ik_jnts[i]

            oc = cmds.orientConstraint(fk, ik, bind, mo=True)[0]
            cmds.setAttr(f"{oc}.interpType", 2)
            weights = cmds.orientConstraint(oc, q=True, wal=True)
            cmds.connectAttr(f"{self.settings_ctrl}.ikFkSwitch",
                              f"{oc}.{weights[0]}")
            cmds.connectAttr(f"{rev}.outputX", f"{oc}.{weights[1]}")

            if i > 0:
                tx_blend = cmds.createNode("blendTwoAttr",
                                            n=f"{bind}_tx_BLEND")
                cmds.connectAttr(f"{fk}.translateX",
                                  f"{tx_blend}.input[0]")
                cmds.connectAttr(f"{ik}.translateX",
                                  f"{tx_blend}.input[1]")
                cmds.connectAttr(f"{rev}.outputX",
                                  f"{tx_blend}.attributesBlender")
                cmds.connectAttr(f"{tx_blend}.output",
                                  f"{bind}.translateX", f=True)

    # -----------------------------------------------------------------------

    def _create_visibility_sdk(self):
        driver = f"{self.settings_ctrl}.ikFkSwitch"
        for ctrl in (self.ik_ctrl, self.pv_ctrl):
            cmds.setAttr(f"{ctrl}.v", l=False)
            cmds.setDrivenKeyframe(f"{ctrl}.v", cd=driver, dv=0.0, v=1,
                                    itt="linear", ott="step")
            cmds.setDrivenKeyframe(f"{ctrl}.v", cd=driver, dv=0.999, v=1,
                                    itt="linear", ott="step")
            cmds.setDrivenKeyframe(f"{ctrl}.v", cd=driver, dv=1.0, v=0,
                                    itt="linear", ott="step")
        for ctrl in self.fk_ctrls:
            cmds.setAttr(f"{ctrl}.v", l=False)
            cmds.setDrivenKeyframe(f"{ctrl}.v", cd=driver, dv=0.0, v=0,
                                    itt="linear", ott="step")
            cmds.setDrivenKeyframe(f"{ctrl}.v", cd=driver, dv=0.001, v=1,
                                    itt="linear", ott="step")
            cmds.setDrivenKeyframe(f"{ctrl}.v", cd=driver, dv=1.0, v=1,
                                    itt="linear", ott="step")

    # -----------------------------------------------------------------------
    # Toes (4 per foot — digit2, digit3, digit4, hallux)
    # -----------------------------------------------------------------------

    # Per-segment curl weight in degrees per curl unit. Negative because
    # the toe's local +Z is "out of the foot", and curling around to grab
    # rotates the segment toward the body (negative around local Z).
    _TOE_CURL_WEIGHTS = {
        "prox": -3.0,   # proximal phalanx
        "dist": -4.0,   # distal phalanx (talon)
    }

    def _create_toes(self):
        """Build the 4 toes. Each toe is a 2-joint FK chain (prox + dist)
        + a talon-tip leaf joint."""
        foot_bind = self.bind_jnts[3]    # foot ball
        cap = lambda s: s[0].upper() + s[1:]
        slots = ["prox", "dist"]

        for toe in self.TOE_NAMES:
            toe_pos = self.positions.get(toe)
            if not toe_pos:
                continue
            base = toe_pos["base"]
            tip  = toe_pos["tip"]
            # Mid-point between base and tip = the distal joint position.
            mid  = tuple((b + t) * 0.5 for b, t in zip(base, tip))

            # ---- BIND chain: prox → dist → talon tip ----
            cmds.select(cl=True)
            prox_jnt = cmds.joint(
                n=f"{self.prefix}_{toe}Prox_BIND_JNT", p=base,
            )
            dist_jnt = cmds.joint(
                n=f"{self.prefix}_{toe}Dist_BIND_JNT", p=mid,
            )
            tip_jnt = cmds.joint(
                n=f"{self.prefix}_{toe}Tip_BIND_JNT", p=tip,
            )
            cmds.joint(prox_jnt, e=True, oj="xyz", sao="yup",
                        ch=True, zso=True)
            cmds.setAttr(f"{tip_jnt}.jointOrient", 0, 0, 0)
            for j in (prox_jnt, dist_jnt, tip_jnt):
                cmds.setAttr(f"{j}.radius", 0.2 * SCALE)
            cmds.parent(prox_jnt, foot_bind)

            # ---- FK ctrls (one per BIND joint, chained) ----
            bind_jnts = [prox_jnt, dist_jnt]
            ctrls = []
            autos = []
            prev_ctrl = self.ik_ctrl   # foot IK ctrl — toes follow the foot
            for slot, bind in zip(slots, bind_jnts):
                ctrl = create_circle_ctrl(
                    f"{self.prefix}_{toe}{cap(slot)}_FK_CTRL",
                    radius=0.18 * SCALE, normal=(1, 0, 0),
                    color=self.color,
                )
                cmds.matchTransform(ctrl, bind)
                offset = make_offset_group(ctrl)
                auto = cmds.group(em=True,
                                   n=ctrl.replace("_CTRL", "_AUTO"))
                cmds.matchTransform(auto, bind)
                cmds.parent(auto, offset)
                cmds.parent(ctrl, auto)
                cmds.parent(offset, prev_ctrl)
                cmds.parentConstraint(ctrl, bind, mo=True)
                lock_hide_attrs(ctrl, ["tx", "ty", "tz",
                                        "sx", "sy", "sz", "v"])
                ctrls.append(ctrl)
                autos.append(auto)
                prev_ctrl = ctrl

            # ---- SDK: AUTO.rotateZ = (talonCurl + perToeCurl) × weight
            settings = self.settings_ctrl
            for slot, ctrl, auto in zip(slots, ctrls, autos):
                weight = self._TOE_CURL_WEIGHTS.get(slot, 0.0)
                if abs(weight) < 1e-6:
                    continue
                sum_node = cmds.createNode(
                    "plusMinusAverage",
                    n=f"{self.prefix}_{toe}{cap(slot)}_curlSum_PMA")
                cmds.connectAttr(f"{settings}.talonCurl",
                                  f"{sum_node}.input1D[0]")
                cmds.connectAttr(f"{settings}.{toe}Curl",
                                  f"{sum_node}.input1D[1]")
                mul = cmds.createNode(
                    "multDoubleLinear",
                    n=f"{self.prefix}_{toe}{cap(slot)}_curl_MUL")
                cmds.connectAttr(f"{sum_node}.output1D",
                                  f"{mul}.input1")
                cmds.setAttr(f"{mul}.input2", weight)
                cmds.connectAttr(f"{mul}.output", f"{auto}.rotateZ", f=True)

            self.toes[toe] = {
                "jnts":  bind_jnts + [tip_jnt],
                "ctrls": ctrls,
                "autos": autos,
            }


# =============================================================================
# TAIL FAN RIG  (small tail base + N fanned tail-feather joints)
# =============================================================================

class TailFanRig(object):
    """A bird's tail — a central tail-base ctrl + N fanned tail feathers.

    Joint structure:
        C_tailBase_BIND_JNT             (small base joint at the rump)
            └── C_tailFeather_01..NN_BIND_JNT   (fanned outward, each
                                                  a direct child of base)

    Settings ctrl attrs (`C_tailFan_SETTINGS_CTRL`):
        tailSpread   — 0 = closed (feathers stacked), 1 = fully spread fan
        tailLift     — rotates all feather AUTOs around X (sweep up/down)
        featherVis   — bool, visibility of feather ctrls

    Each feather has an FK ctrl + AUTO group. SDK on AUTO.rotateY drives
    the spread; AUTO.rotateX driven by tailLift.
    """

    DEFAULT_FEATHER_COUNT = 7
    # Half-angle in degrees of the fully-spread fan. At spread=1 the
    # outermost feathers reach ±MAX_SPREAD degrees off centre.
    MAX_SPREAD = 60.0

    def __init__(self, positions=None, feather_count=None,
                 parent_ctrl=None, parent_jnt=None,
                 ctrl_grp=None, jnt_grp=None, misc_grp=None):
        self.feather_count = feather_count or self.DEFAULT_FEATHER_COUNT
        self.positions = positions or {
            "base":     (0,  50, -40),
            "fanTip":   (0,  35, -75),   # centre feather tip — used for spread radius
        }
        self.parent_ctrl = parent_ctrl
        self.parent_jnt  = parent_jnt
        self.ctrl_grp = ctrl_grp
        self.jnt_grp  = jnt_grp
        self.misc_grp = misc_grp

        # Outputs
        self.base_jnt = None
        self.base_ctrl = None
        self.feather_jnts  = []
        self.feather_ctrls = []
        self.feather_autos = []
        self.settings_ctrl = None

    # -----------------------------------------------------------------------

    def build(self):
        self._build_base()
        self._build_feathers()
        self._build_settings_ctrl()
        self._wire_spread_sdk()

    # -----------------------------------------------------------------------

    def _build_base(self):
        cmds.select(cl=True)
        self.base_jnt = cmds.joint(
            n="C_tailBase_BIND_JNT", p=self.positions["base"],
        )
        cmds.setAttr(f"{self.base_jnt}.jointOrient", 0, 0, 0)
        cmds.setAttr(f"{self.base_jnt}.radius", 0.5 * SCALE)
        cmds.parent(self.base_jnt, self.parent_jnt)

        self.base_ctrl = create_circle_ctrl(
            "C_tailBase_CTRL", radius=0.8 * SCALE,
            normal=(1, 0, 0), color=COLOR_CENTER,
        )
        cmds.matchTransform(self.base_ctrl, self.base_jnt,
                             pos=True, rot=False)
        offset = make_offset_group(self.base_ctrl)
        cmds.parent(offset, self.parent_ctrl)
        cmds.parentConstraint(self.base_ctrl, self.base_jnt, mo=True)
        lock_hide_attrs(self.base_ctrl, ["sx", "sy", "sz", "v"])

    def _build_feathers(self):
        """Build feather_count joints fanning out from base toward fanTip.

        At rest (spread=0) all feathers sit at the same world position
        as the centre feather — i.e. stacked along the central axis. The
        SDK rotates each AUTO group around Y so the fan opens out.
        """
        base_pos = self.positions["base"]
        tip_pos  = self.positions["fanTip"]
        # Central feather direction (base → tip).
        for i in range(self.feather_count):
            slot = f"feather_{i + 1:02d}"
            # All feathers placed at the centre-feather tip initially.
            # Spread is purely rotational, so per-feather translation is
            # the same — visual fan comes from rotation about the base.
            cmds.select(cl=True)
            jnt = cmds.joint(
                n=f"C_tail{slot}_BIND_JNT", p=tip_pos,
            )
            cmds.setAttr(f"{jnt}.jointOrient", 0, 0, 0)
            cmds.setAttr(f"{jnt}.radius", 0.3 * SCALE)
            cmds.parent(jnt, self.base_jnt)
            self.feather_jnts.append(jnt)

            # FK ctrl + AUTO above
            ctrl = create_square_ctrl(
                f"C_tail{slot}_CTRL", size=0.4 * SCALE,
                normal=(0, 1, 0), color=COLOR_BEND,
            )
            cmds.matchTransform(ctrl, jnt, pos=True, rot=False)
            offset = make_offset_group(ctrl)
            auto = cmds.group(em=True,
                               n=f"C_tail{slot}_AUTO")
            # Pivot of AUTO should be at the base so rotateY rotates the
            # feather around the base, not around its own tip.
            cmds.matchTransform(auto, self.base_jnt)
            cmds.parent(auto, offset)
            cmds.parent(ctrl, auto)
            cmds.parent(offset, self.base_ctrl)
            cmds.parentConstraint(ctrl, jnt, mo=True)
            # NOTE: visibility is intentionally LEFT UNLOCKED here so the
            # settings ctrl's featherVis attr can connect to each feather
            # ctrl's .v. Translate / scale stay locked.
            lock_hide_attrs(ctrl, ["tx", "ty", "tz",
                                    "sx", "sy", "sz"])
            self.feather_ctrls.append(ctrl)
            self.feather_autos.append(auto)

    def _build_settings_ctrl(self):
        self.settings_ctrl = create_gear_ctrl(
            "C_tailFan_SETTINGS_CTRL", size=0.4 * SCALE,
            color=COLOR_SETTINGS,
        )
        cmds.matchTransform(self.settings_ctrl, self.base_jnt,
                             pos=True, rot=False)
        cmds.move(0, 1.5 * SCALE, 0, self.settings_ctrl, r=True, ws=True)
        offset = make_offset_group(self.settings_ctrl)
        cmds.parent(offset, self.ctrl_grp)
        cmds.parentConstraint(self.base_jnt, offset, mo=True)

        cmds.addAttr(self.settings_ctrl, ln="tailSpread", at="double",
                      min=0, max=1, dv=0.5, k=True)
        cmds.addAttr(self.settings_ctrl, ln="tailLift", at="double",
                      min=-45, max=45, dv=0, k=True)
        cmds.addAttr(self.settings_ctrl, ln="featherVis", at="bool",
                      dv=True, k=True)

        lock_hide_attrs(self.settings_ctrl,
                         ["tx", "ty", "tz", "rx", "ry", "rz",
                          "sx", "sy", "sz", "v"])

        # Wire featherVis to each feather ctrl.
        for ctrl in self.feather_ctrls:
            cmds.connectAttr(f"{self.settings_ctrl}.featherVis",
                              f"{ctrl}.v", f=True)

    def _wire_spread_sdk(self):
        """Drive each feather AUTO.rotateY from tailSpread.

        Per-feather angle = (i - centre) / (centre) × MAX_SPREAD, so the
        outermost feathers rotate ±MAX_SPREAD when spread=1 and the
        centre feather stays at 0.
        """
        n = self.feather_count
        centre = (n - 1) / 2.0
        for i, auto in enumerate(self.feather_autos):
            # Normalized offset from the central feather: -1 .. +1.
            offset_norm = (i - centre) / centre if centre > 0 else 0.0
            per_angle = offset_norm * self.MAX_SPREAD

            mul = cmds.createNode(
                "multDoubleLinear",
                n=f"C_tail_feather_{i + 1:02d}_spread_MUL",
            )
            cmds.connectAttr(f"{self.settings_ctrl}.tailSpread",
                              f"{mul}.input1")
            cmds.setAttr(f"{mul}.input2", per_angle)
            cmds.connectAttr(f"{mul}.output", f"{auto}.rotateY", f=True)

            # tailLift directly drives rotateX on each AUTO (uniform lift).
            cmds.connectAttr(f"{self.settings_ctrl}.tailLift",
                              f"{auto}.rotateX", f=True)


# =============================================================================
# BIRD RIG ORCHESTRATOR
# =============================================================================

class BirdRig(object):
    """Builds the full raptor bird rig.

    Modules:
        core, spine, neck, face (jaw/eyes/tongue), wings (L+R),
        legs (L+R), tail

    Top group: BIRD_RIG_GRP
    """

    TOP_GROUP = "BIRD_RIG_GRP"

    # ---- Baked-in raptor (eagle / hawk) defaults -------------------------
    # +Z faces forward, Y up, ground at Y=0. Body horizontal, neck arcs
    # up and forward from the shoulders.
    DEFAULT_POSITIONS = {
        "core":  {"cog": (0, 75, 0)},
        "spine": {
            "hip":   (0, 70, -25),    # pelvis end of the spine
            "chest": (0, 80,  20),    # shoulder end
        },
        "neck": {
            "neck":     (0, 90, 25),
            "head":     (0, 105, 35),
            "head_tip": (0, 105, 55),   # beak tip
        },
        # Face — jaw is the lower beak, eyes look forward.
        "face": {
            "jaw":         (0, 100, 40),
            "jawTip":      (0,  97, 52),
            "L_eye":       (5, 108, 38),
            "R_eye":      (-5, 108, 38),
            "eyesLookAt":  (0, 108, 80),
            "tongue01":    (0,  99, 42),
            "tongue02":    (0,  99, 46),
            "tongue03":    (0,  99, 50),
        },
        "L_wing": {
            # Elbow lifted (Y+6) and slightly forward of the
            # shoulder→wrist line — gives the IK chain a clear gull-wing
            # bend plane so the elbow knows which way to bend. A flat
            # chain (all joints on one line) leaves the RP IK solver
            # unable to decide bend direction and the elbow "sticks".
            "shoulder": (  6,  80,  10),
            "elbow":    ( 50,  86,   3),
            "wrist":    ( 90,  82,  -5),
            "wingTip":  (125,  80, -12),
        },
        "L_leg": {
            "femur":           ( 6, 55, -10),
            "tibiotarsus":     ( 8, 35,  -4),
            "tarsometatarsus": (10, 18,  -8),
            "foot":            (10,  4,  -4),
            "footTip":         (10,  0,   6),
            "digit2": {"base": (16, 2,  6), "tip": (20, 0, 14)},
            "digit3": {"base": (10, 2,  6), "tip": (10, 0, 18)},
            "digit4": {"base": ( 4, 2,  6), "tip": ( 0, 0, 14)},
            "hallux": {"base": (10, 2, -2), "tip": (10, 0, -8)},
        },
        "tail": {
            "base":   (0, 65, -35),
            "fanTip": (0, 55, -70),
        },
    }

    DEFAULT_MODULES = {"spine", "neck", "face", "wings", "legs", "tail"}

    def __init__(self, positions=None, modules=None, bendy_count=5,
                 spine_fk_count=2,
                 primary_count=None, secondary_count=None,
                 tail_feather_count=None):
        self.positions = positions or self._default_positions()
        self.modules = set(modules) if modules else set(self.DEFAULT_MODULES)
        self.bendy_count = bendy_count
        self.spine_fk_count = spine_fk_count
        self.primary_count = primary_count
        self.secondary_count = secondary_count
        self.tail_feather_count = tail_feather_count

        # Module dependencies
        # - Face requires neck (needs the head joint).
        if "face" in self.modules:
            self.modules.add("neck")

        self.core = None
        self.spine = None
        self.neck = None
        self.face = None
        self.L_wing = self.R_wing = None
        self.L_leg = self.R_leg = None
        self.tail = None

    # -----------------------------------------------------------------------

    @classmethod
    def _default_positions(cls):
        """Deep-copy defaults and mirror L wing/leg to R."""
        import copy
        pos = copy.deepcopy(cls.DEFAULT_POSITIONS)
        for limb in ("wing", "leg"):
            l_key = f"L_{limb}"
            r_key = f"R_{limb}"
            r = {}
            for slot, p in pos[l_key].items():
                # Nested dicts (toe definitions) need recursive mirroring.
                if isinstance(p, dict):
                    r[slot] = {k: (-v[0], v[1], v[2]) for k, v in p.items()}
                else:
                    r[slot] = (-p[0], p[1], p[2])
            pos[r_key] = r
        return pos

    # -----------------------------------------------------------------------

    def build(self):
        if cmds.objExists(self.TOP_GROUP):
            cmds.error(f"{self.TOP_GROUP} already exists. Delete it first.")
            return
        print(f"[BirdRig] Building modules: {sorted(self.modules)}")

        # 1. Core — reuse CoreRig, then rename the top group.
        # cmds.rename invalidates the previous string handle, so we
        # write the new name back onto self.core.main_grp so later
        # lookups (listRelatives, lock_hide_attrs) still resolve.
        self.core = CoreRig(positions=self.positions.get("core"))
        self.core.build()
        self.core.main_grp = cmds.rename("CHARACTER_RIG_GRP", self.TOP_GROUP)
        cg, jg, mg = (self.core.ctrl_grp, self.core.jnt_grp,
                       self.core.misc_grp)

        # 2. Spine — horizontal ribbon (hip → chest).
        if "spine" in self.modules:
            self.spine = SpineRig(
                positions=self.positions.get("spine"),
                bendy_count=self.bendy_count,
                fk_chain_count=self.spine_fk_count,
                parent_ctrl=self.core.cog_ctrl,
                ctrl_grp=cg, jnt_grp=jg, misc_grp=mg,
            )
            self.spine.build()

        chest_ctrl  = self.spine.chest_ctrl       if self.spine else self.core.cog_ctrl
        chest_jnt   = self.spine.chest_bind_jnt   if self.spine else self.core.root_bind_jnt
        pelvis_ctrl = self.spine.hip_ctrl         if self.spine else self.core.cog_ctrl
        pelvis_jnt  = self.spine.pelvis_bind_jnt  if self.spine else self.core.root_bind_jnt

        # 3. Neck + head — attaches to the chest end of the spine.
        if "neck" in self.modules:
            self.neck = NeckHeadRig(
                positions=self.positions.get("neck"),
                parent_ctrl=chest_ctrl, parent_jnt=chest_jnt,
                ctrl_grp=cg, jnt_grp=jg,
            )
            self.neck.build()

        # 4. Face — jaw (lower beak), eyes, tongue. Reuse FaceRig.
        if "face" in self.modules and self.neck:
            head_jnt  = self.neck.bind_jnts[1]   # C_head_BIND_JNT
            head_ctrl = self.neck.fk_ctrls[1]    # C_head_CTRL
            self.face = FaceRig(
                positions=self.positions.get("face"),
                parent_ctrl=head_ctrl, parent_jnt=head_jnt,
                ctrl_grp=cg, jnt_grp=jg, misc_grp=mg,
                submodules={"jaw", "eyes", "tongue"},
            )
            self.face.build()

        # 5. Wings — parented to the chest (shoulder area).
        if "wings" in self.modules:
            self.L_wing = BirdWingRig(
                side="L",
                positions=self.positions.get("L_wing"),
                primary_count=self.primary_count,
                secondary_count=self.secondary_count,
                parent_ctrl=chest_ctrl, parent_jnt=chest_jnt,
                ctrl_grp=cg, jnt_grp=jg, misc_grp=mg,
            )
            self.R_wing = BirdWingRig(
                side="R",
                positions=self.positions.get("R_wing"),
                primary_count=self.primary_count,
                secondary_count=self.secondary_count,
                parent_ctrl=chest_ctrl, parent_jnt=chest_jnt,
                ctrl_grp=cg, jnt_grp=jg, misc_grp=mg,
            )
            self.L_wing.build()
            self.R_wing.build()

        # 6. Legs — parented to the pelvis end of the spine.
        if "legs" in self.modules:
            self.L_leg = BirdLegRig(
                side="L",
                positions=self.positions.get("L_leg"),
                parent_ctrl=pelvis_ctrl, parent_jnt=pelvis_jnt,
                ctrl_grp=cg, jnt_grp=jg, misc_grp=mg,
            )
            self.R_leg = BirdLegRig(
                side="R",
                positions=self.positions.get("R_leg"),
                parent_ctrl=pelvis_ctrl, parent_jnt=pelvis_jnt,
                ctrl_grp=cg, jnt_grp=jg, misc_grp=mg,
            )
            self.L_leg.build()
            self.R_leg.build()

        # 7. Tail — fanned tail feathers from the pelvis.
        if "tail" in self.modules:
            self.tail = TailFanRig(
                positions=self.positions.get("tail"),
                feather_count=self.tail_feather_count,
                parent_ctrl=pelvis_ctrl, parent_jnt=pelvis_jnt,
                ctrl_grp=cg, jnt_grp=jg, misc_grp=mg,
            )
            self.tail.build()

        # Joint radius pass — make joints readable at this scale.
        all_joints = cmds.listRelatives(self.core.main_grp,
                                         ad=True, type="joint") or []
        for j in all_joints:
            if "_FK_" in j or "_IK_" in j or "_DRV_" in j:
                cmds.setAttr(f"{j}.radius", 0.4 * SCALE)
            elif "_feather" in j or "_bendy_" in j or "_spine_0" in j:
                cmds.setAttr(f"{j}.radius", 0.3 * SCALE)
            elif "Tip_BIND" in j or "digit" in j or "hallux" in j:
                cmds.setAttr(f"{j}.radius", 0.25 * SCALE)
            else:
                cmds.setAttr(f"{j}.radius", 0.7 * SCALE)

        # Lock organisational groups.
        for grp in (self.core.main_grp, self.core.ctrl_grp,
                     self.core.jnt_grp):
            lock_hide_attrs(grp, ["tx", "ty", "tz", "rx", "ry", "rz",
                                   "sx", "sy", "sz"])

        # Hide guide locators if a bird guide group is present.
        for guide_grp in ("BIRD_RIG_GUIDES_GRP", "RIG_GUIDES_GRP"):
            if cmds.objExists(guide_grp):
                try:
                    cmds.setAttr(f"{guide_grp}.visibility", 0)
                except Exception:
                    pass

        cmds.select(cl=True)
        print(f"[BirdRig] Done. Top node: {self.TOP_GROUP}")

    # -----------------------------------------------------------------------

    def delete(self):
        if cmds.objExists(self.TOP_GROUP):
            cmds.delete(self.TOP_GROUP)
