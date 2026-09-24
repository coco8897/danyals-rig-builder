"""
===============================================================================
 QUADRUPED RIG BUILDER  (horse, cat / dog, raptor)
===============================================================================

 Builds a four-legged character rig. Reuses the orientation-agnostic biped
 modules (CoreRig, SpineRig, NeckHeadRig, TailRig) from
 character_rig_builder.py and adds a dedicated QuadLegRig for the limbs.

 Each leg pair has a foot type (see quadruped_guides for the anatomy):
   * hoof: horse. Front: scapula (FK) + shoulder/elbow/knee (3-joint RP
     IK) + fetlock/hoof. Back: hip/stifle/hock + fetlock/hoof.
     Reverse-foot hoof roll.
   * paw: cat / dog. Stands on the toe pads: shoulder/elbow/wrist or
     hip/knee/ankle IK, then paw + toe tip, with a digitigrade
     reverse foot (heel / ball / toe roll, toe bend, bank) and four
     toes that curl and spread.
   * claw: raptor. Same foot as the paw on a long raised ankle bone, with
     three toes, a dewclaw and a sickle claw you can raise or strike.
   * arm: front limbs off the ground (raptor arms): an IK hand control
     that rides the chest, and three clawed fingers.

 Usage in Maya (Python):
     import quadruped_rig_builder
     from importlib import reload
     reload(quadruped_rig_builder)
     quadruped_rig_builder.QuadrupedRig().build()                 # horse
     quadruped_rig_builder.QuadrupedRig(animal="cat").build()
     quadruped_rig_builder.QuadrupedRig(
         animal="horse", feet={"front": "paw", "back": "claw"}).build()

 Top node: QUADRUPED_RIG_GRP
===============================================================================
"""

import math
import maya.cmds as cmds

# Reuse helpers, constants and modules from the biped builder. Both files
# live in the same Maya scripts folder, so this import resolves cleanly.
from character_rig_builder import (
    SCALE,
    COLOR_LEFT, COLOR_RIGHT, COLOR_CENTER, COLOR_IK, COLOR_PV,
    COLOR_BEND, COLOR_SETTINGS,
    make_offset_group, lock_hide_attrs, get_pole_vector_position,
    create_circle_ctrl, create_cube_ctrl, create_diamond_ctrl,
    create_foot_ctrl, create_gear_ctrl,
    CoreRig, SpineRig, NeckHeadRig, TailRig, FaceRig,
)
import quadruped_guides as qg


# =============================================================================
# QUAD LEG RIG  (one limb, front or back)
# =============================================================================

def _layout(leg_type, foot):
    """Slots (joints with controls), the tip leaf, the 3-joint IK chain and
    the FK-only joints above it, for one leg type + foot."""
    names = list(qg.LEG_SLOTS[(leg_type, foot)])
    slots, tip = names[:-1], names[-1]
    top = ["scapula"] if leg_type == "front" else []
    ik = tuple(slots[len(top):len(top) + 3])
    layout = {"slots": slots, "tip": tip, "ik": ik, "fk_only_top": top}
    if foot == "hoof":
        layout["rev"] = (ik[2], "fetlock", "hoof")
    return layout


class QuadLegRig(object):
    """One quadruped limb.

    Hoof legs (the horse):
        front: scapula → shoulder → elbow → knee → fetlock → hoof (+ hoofTip)
        back:  hip → stifle → hock → fetlock → hoof (+ hoofTip)
        A 3-joint RP IK, then a hoof reverse foot with heel / hoof / fetlock
        roll attributes on the IK ctrl.

    Paw / claw legs (cat, dog, raptor), standing on their toes:
        front: scapula → shoulder → elbow → wrist → paw|ball (+ toeTip)
        back:  hip → knee → ankle → paw|ball (+ toeTip)
        A 3-joint RP IK, then a digitigrade reverse foot (see
        _create_toe_foot), plus toe chains under the paw / ball.

    Arms (front limbs off the ground):
        scapula → shoulder → elbow → wrist (+ handTip), IK hand ctrl on the
        wrist that rides the chest, plus finger chains under the wrist.

    Each limb has an IK/FK switch (0 = IK, 1 = FK — matches the biped
    convention) and a pole vector.
    """

    LAYOUTS = {key: _layout(*key) for key in qg.LEG_SLOTS}
    # The horse layouts under their old keys, for older scripts.
    LAYOUT = {"front": LAYOUTS[("front", "hoof")],
              "back": LAYOUTS[("back", "hoof")]}

    def __init__(self, side, leg_type, positions,
                 parent_ctrl, parent_jnt,
                 ctrl_grp, jnt_grp, misc_grp, foot="hoof", toes=True):
        assert side in ("L", "R")
        assert leg_type in ("front", "back")
        if (leg_type, foot) not in self.LAYOUTS:
            raise ValueError(f"{leg_type} legs can't have {foot!r} feet.")
        self.side = side
        self.leg_type = leg_type
        self.foot = foot
        self.toes = toes
        self.prefix = qg.limb_prefix(side, leg_type, foot)
        self.layout = self.LAYOUTS[(leg_type, foot)]
        self.slot_idx = {s: i for i, s in enumerate(self.layout["slots"])}
        self.positions = positions or {}
        missing = [s for s in self.layout["slots"] + [self.layout["tip"]]
                   if s not in self.positions]
        if missing:
            raise ValueError(f"{self.prefix} ({foot}) is missing positions "
                             f"for {missing}.")
        self.parent_ctrl = parent_ctrl
        self.parent_jnt = parent_jnt
        self.ctrl_grp = ctrl_grp
        self.jnt_grp = jnt_grp
        self.misc_grp = misc_grp
        self.color = COLOR_LEFT if side == "L" else COLOR_RIGHT

        # Storage
        self.bind_jnts = []     # includes the tip leaf joint at [-1]
        self.fk_jnts = []
        self.ik_jnts = []
        self.fk_ctrls = []          # one per slot (no ctrl on the tip)
        self.switchable_fk_ctrls = []   # FK ctrls hidden in IK mode
        self.ik_ctrl = self.pv_ctrl = self.settings_ctrl = None
        self.ik_handle_main = None      # RP IK over the 3-joint chain
        self.ik_handle_fetlock = None   # hoof, SC: ik-end → fetlock
        self.ik_handle_hoof = None      # hoof, SC: fetlock → hoof
        self.ik_handle_ball = None      # paw / claw, SC: ik-end → ball
        self.ik_handle_toe = None       # paw / claw, SC: ball → toe tip
        self.foot_locators = {}
        self.digits = {}    # digit -> {"jnts": [...], "ctrls": [...]}

    # ------------------------------------------------------------------------

    def build(self):
        self._create_joint_chains()
        self._create_fk()
        self._create_ik()
        if self.foot == "hoof":
            self._create_reverse_foot()
        elif self.foot in ("paw", "claw"):
            self._create_toe_foot()
        self._create_settings_ctrl()
        self._create_ikfk_blend()
        self._create_visibility_sdk()
        if self.toes:
            self._create_digits()
        # Hide the FK + IK driver chains; only BIND stays visible.
        cmds.setAttr(f"{self.fk_jnts[0]}.v", 0)
        cmds.setAttr(f"{self.ik_jnts[0]}.v", 0)

    # ------------------------------------------------------------------------

    def _create_joint_chains(self):
        slots = self.layout["slots"]
        tip_slot = self.layout["tip"]
        cmds.select(cl=True)
        for slot in slots:
            j = cmds.joint(n=f"{self.prefix}_{slot}_BIND_JNT",
                           p=self.positions[slot])
            self.bind_jnts.append(j)
        tip = cmds.joint(n=f"{self.prefix}_{tip_slot}_BIND_JNT",
                         p=self.positions[tip_slot])
        self.bind_jnts.append(tip)

        cmds.joint(self.bind_jnts[0], e=True, oj="xyz", sao="yup",
                   ch=True, zso=True)
        cmds.setAttr(f"{tip}.jointOrient", 0, 0, 0)
        for j in self.bind_jnts:
            cmds.setAttr(f"{j}.radius", 0.8 * SCALE)
        cmds.parent(self.bind_jnts[0], self.parent_jnt)

        self.fk_jnts = self._dup_chain(self.bind_jnts, "FK")
        self.ik_jnts = self._dup_chain(self.bind_jnts, "IK")

    def _dup_chain(self, source, suffix):
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

    # ------------------------------------------------------------------------

    def _create_fk(self):
        """One FK ctrl per BIND joint (excluding the tip), chained. The
        scapula ctrl (front leg) is flagged always-visible — it should be
        poseable whether the leg is in IK or FK mode."""
        slots = self.layout["slots"]
        fk_only_top = set(self.layout["fk_only_top"])
        prev = self.parent_ctrl
        for i, slot in enumerate(slots):
            fk_jnt = self.fk_jnts[i]
            ctrl = create_circle_ctrl(
                f"{self.prefix}_{slot}_FK_CTRL",
                radius=1.0 * SCALE, normal=(1, 0, 0), color=self.color,
            )
            cmds.matchTransform(ctrl, fk_jnt)
            offset = make_offset_group(ctrl)
            cmds.parent(offset, prev)
            cmds.parentConstraint(ctrl, fk_jnt, mo=True)
            lock_hide_attrs(ctrl, ["tx", "ty", "tz",
                                   "sx", "sy", "sz", "v"])
            self.fk_ctrls.append(ctrl)
            if slot not in fk_only_top:
                self.switchable_fk_ctrls.append(ctrl)
            prev = ctrl

    # ------------------------------------------------------------------------

    def _ground(self):
        """The floor under a paw / claw: y = 0 for a foot standing on it, or
        the lowest foot joint's height for a leg built up in the air."""
        end = self.positions[self.layout["ik"][2]]
        low = min(self.positions[s][1]
                  for s in (self.layout["slots"][-1], self.layout["tip"]))
        return 0.0 if abs(low) <= end[1] - low else low

    def _create_ik(self):
        ik_slots = self.layout["ik"]
        s0 = self.slot_idx[ik_slots[0]]
        s1 = self.slot_idx[ik_slots[1]]
        s2 = self.slot_idx[ik_slots[2]]

        # RP IK over the 3-joint upper chain (shoulder/elbow/knee|wrist or
        # hip/stifle|knee/hock|ankle).
        self.ik_handle_main = cmds.ikHandle(
            sj=self.ik_jnts[s0], ee=self.ik_jnts[s2],
            sol="ikRPsolver", n=f"{self.prefix}_ikHandle_main",
        )[0]
        handles = [self.ik_handle_main]
        if self.foot == "hoof":
            rev = self.layout["rev"]
            fet = self.slot_idx[rev[1]]
            hf = self.slot_idx[rev[2]]
            # SC IK: ik-chain-end → fetlock, then fetlock → hoof.
            self.ik_handle_fetlock = cmds.ikHandle(
                sj=self.ik_jnts[s2], ee=self.ik_jnts[fet],
                sol="ikSCsolver", n=f"{self.prefix}_ikHandle_fetlock",
            )[0]
            self.ik_handle_hoof = cmds.ikHandle(
                sj=self.ik_jnts[fet], ee=self.ik_jnts[hf],
                sol="ikSCsolver", n=f"{self.prefix}_ikHandle_hoof",
            )[0]
            handles += [self.ik_handle_fetlock, self.ik_handle_hoof]
        elif self.foot in ("paw", "claw"):
            ball = s2 + 1
            # SC IK: ankle|wrist → paw|ball, then paw|ball → toe tip.
            self.ik_handle_ball = cmds.ikHandle(
                sj=self.ik_jnts[s2], ee=self.ik_jnts[ball],
                sol="ikSCsolver", n=f"{self.prefix}_ikHandle_ball",
            )[0]
            self.ik_handle_toe = cmds.ikHandle(
                sj=self.ik_jnts[ball], ee=self.ik_jnts[ball + 1],
                sol="ikSCsolver", n=f"{self.prefix}_ikHandle_toe",
            )[0]
            handles += [self.ik_handle_ball, self.ik_handle_toe]
        for h in handles:
            cmds.setAttr(f"{h}.v", 0)

        if self.foot == "arm":
            # The hand ctrl sits ON the wrist with the wrist's orientation
            # (like the biped arm) and rides the chest.
            self.ik_ctrl = create_cube_ctrl(
                f"{self.prefix}_IK_CTRL", size=0.8 * SCALE, color=COLOR_IK,
            )
            cmds.matchTransform(self.ik_ctrl, self.ik_jnts[s2])
            ik_offset = make_offset_group(self.ik_ctrl)
            cmds.parent(ik_offset, self.parent_ctrl)
            cmds.parent(self.ik_handle_main, self.ik_ctrl)
            cmds.orientConstraint(self.ik_ctrl, self.ik_jnts[s2], mo=True)
        else:
            # A foot-shaped ctrl on the ground at the hoof / paw / ball.
            if self.foot == "hoof":
                foot_pos = self.positions[self.layout["rev"][2]]
                ground, size = 0.0, 1.6 * SCALE
            else:
                foot_pos = self.positions[self.layout["slots"][-1]]
                ground = self._ground()
                top = self.positions[ik_slots[0]][1] - ground
                size = 1.6 * SCALE * max(0.35, min(1.0, top / 150.0))
            self.ik_ctrl = create_foot_ctrl(
                f"{self.prefix}_IK_CTRL", size=size, color=COLOR_IK,
            )
            cmds.xform(self.ik_ctrl, ws=True,
                       t=(foot_pos[0], ground, foot_pos[2]))
            ik_offset = make_offset_group(self.ik_ctrl)
            cmds.parent(ik_offset, self.ctrl_grp)
        lock_hide_attrs(self.ik_ctrl, ["sx", "sy", "sz", "v"])

        # Pole vector — auto-placed in the bend plane of the IK chain.
        pv_pos = get_pole_vector_position(
            self.ik_jnts[s0], self.ik_jnts[s1], self.ik_jnts[s2],
            distance=(4.0 if self.foot == "arm" else 8.0) * SCALE,
        )
        self.pv_ctrl = create_diamond_ctrl(
            f"{self.prefix}_PV_CTRL", size=0.7 * SCALE, color=COLOR_PV,
        )
        cmds.xform(self.pv_ctrl, ws=True, t=pv_pos)
        pv_offset = make_offset_group(self.pv_ctrl)
        cmds.parent(pv_offset, self.parent_ctrl if self.foot == "arm"
                    else self.ctrl_grp)
        cmds.poleVectorConstraint(self.pv_ctrl, self.ik_handle_main)
        lock_hide_attrs(self.pv_ctrl, ["rx", "ry", "rz",
                                       "sx", "sy", "sz", "v"])

    # ------------------------------------------------------------------------

    def _mk_loc(self, name, pos):
        loc = cmds.spaceLocator(n=name)[0]
        cmds.xform(loc, ws=True, t=pos)
        cmds.setAttr(f"{loc}Shape.visibility", 0)
        return loc

    def _create_reverse_foot(self):
        """Hoof reverse-foot locator hierarchy:

            heel_LOC
              └── hoofTip_LOC
                    └── fetlock_LOC
                          ├── legIK_LOC      (parents the RP IK handle)
                          ├── fetlockIK_LOC  (parents the fetlock SC handle)
                          └── hoofIK_LOC     (parents the hoof SC handle)

        Roll attrs on the IK ctrl drive heel / hoof / fetlock rotation.
        """
        rev = self.layout["rev"]
        ik_end_pos  = self.positions[rev[0]]    # knee / hock
        fetlock_pos = self.positions[rev[1]]
        hoof_pos    = self.positions[rev[2]]
        hooftip_pos = self.positions["hoofTip"]
        heel_pos = (hoof_pos[0], 0.0, hoof_pos[2] - 8.0)

        mk_loc = self._mk_loc
        heel    = mk_loc(f"{self.prefix}_heel_LOC", heel_pos)
        hooftip = mk_loc(f"{self.prefix}_hoofTip_LOC", hooftip_pos)
        fetlock = mk_loc(f"{self.prefix}_fetlock_LOC", fetlock_pos)
        legik   = mk_loc(f"{self.prefix}_legIK_LOC", ik_end_pos)
        fetik   = mk_loc(f"{self.prefix}_fetlockIK_LOC", fetlock_pos)
        hoofik  = mk_loc(f"{self.prefix}_hoofIK_LOC", hoof_pos)

        self.foot_locators = {
            "heel": heel, "hoofTip": hooftip, "fetlock": fetlock,
            "legIK": legik, "fetlockIK": fetik, "hoofIK": hoofik,
        }

        cmds.parent(hooftip, heel)
        cmds.parent(fetlock, hooftip)
        cmds.parent(legik, fetlock)
        cmds.parent(fetik, fetlock)
        cmds.parent(hoofik, fetlock)

        cmds.parent(self.ik_handle_main, legik)
        cmds.parent(self.ik_handle_fetlock, fetik)
        cmds.parent(self.ik_handle_hoof, hoofik)

        cmds.parent(heel, self.ik_ctrl)

        # Roll attributes (X-axis rotations on the locators).
        cmds.addAttr(self.ik_ctrl, ln="heelRoll", at="double", dv=0, k=True)
        cmds.addAttr(self.ik_ctrl, ln="hoofRoll", at="double", dv=0, k=True)
        cmds.addAttr(self.ik_ctrl, ln="fetlockRoll", at="double",
                     dv=0, k=True)
        cmds.connectAttr(f"{self.ik_ctrl}.heelRoll", f"{heel}.rotateX")
        cmds.connectAttr(f"{self.ik_ctrl}.hoofRoll", f"{hooftip}.rotateX")
        cmds.connectAttr(f"{self.ik_ctrl}.fetlockRoll",
                         f"{fetlock}.rotateX")

    def _create_toe_foot(self):
        """Reverse foot for a paw or a clawed foot, standing on its toes:

            heel_LOC            back of the pad, on the ground    heelRoll
              └── toeTip_LOC       tips of the toes, on the ground   toeRoll
                    ├── ball_LOC      the paw / ball joint           ballRoll
                    │     ├── ankle_LOC    (the RP IK handle)
                    │     └── ballIK_LOC   (ankle → ball SC handle)
                    └── toeBend_LOC   also at the ball               toeBend
                          (ball → toe tip SC handle)

        ballRoll lifts the wrist / ankle while the toes stay planted,
        toeRoll stands the foot up on its toe tips, heelRoll rocks back on
        the pad, toeBend lifts the toes, footBank tips it sideways. `roll`
        runs heel → ball → toe in one slider like the biped foot (Walk Mode
        uses it for the toe-off).
        """
        end_slot = self.layout["ik"][2]
        ball_slot = self.layout["slots"][-1]
        end_pos = self.positions[end_slot]
        ball_pos = self.positions[ball_slot]
        tip_pos = self.positions[self.layout["tip"]]
        ground = self._ground()
        dx, dz = tip_pos[0] - ball_pos[0], tip_pos[2] - ball_pos[2]
        length = math.hypot(dx, dz)
        hx, hz = (dx / length, dz / length) if length > 1e-6 else (0.0, 1.0)
        back = 0.5 * length
        heel_pos = (ball_pos[0] - hx * back, ground, ball_pos[2] - hz * back)
        toetip_pos = (tip_pos[0], ground, tip_pos[2])

        mk_loc = self._mk_loc
        heel = mk_loc(f"{self.prefix}_heel_LOC", heel_pos)
        toetip = mk_loc(f"{self.prefix}_toeTip_LOC", toetip_pos)
        ball = mk_loc(f"{self.prefix}_ball_LOC", ball_pos)
        ankle = mk_loc(f"{self.prefix}_ankle_LOC", end_pos)
        ballik = mk_loc(f"{self.prefix}_ballIK_LOC", ball_pos)
        toebend = mk_loc(f"{self.prefix}_toeBend_LOC", ball_pos)
        self.foot_locators = {"heel": heel, "toeTip": toetip, "ball": ball,
                              "ankle": ankle, "ballIK": ballik,
                              "toeBend": toebend}

        cmds.parent(toetip, heel)
        cmds.parent(ball, toetip)
        cmds.parent(ankle, ball)
        cmds.parent(ballik, ball)
        cmds.parent(toebend, toetip)
        cmds.parent(self.ik_handle_main, ankle)
        cmds.parent(self.ik_handle_ball, ballik)
        cmds.parent(self.ik_handle_toe, toebend)
        cmds.parent(heel, self.ik_ctrl)

        c = self.ik_ctrl
        cmds.addAttr(c, ln="roll", at="double", dv=0, k=True)
        cmds.addAttr(c, ln="rollStartAngle", at="double", min=0, dv=25,
                     k=True)
        for attr in ("heelRoll", "ballRoll", "toeRoll", "toeBend",
                     "footBank"):
            cmds.addAttr(c, ln=attr, at="double", dv=0, k=True)

        def node(kind, name):
            return cmds.createNode(kind, n=f"{self.prefix}_{name}")

        # From `roll`: heel = max(0, -roll), ball = clamp(roll, 0, start),
        # toe = max(0, roll - start).
        neg = node("multDoubleLinear", "negRoll_MULT")
        cmds.connectAttr(f"{c}.roll", f"{neg}.input1")
        cmds.setAttr(f"{neg}.input2", -1.0)
        clamp = node("clamp", "roll_CLAMP")
        cmds.connectAttr(f"{neg}.output", f"{clamp}.inputR")
        cmds.connectAttr(f"{c}.roll", f"{clamp}.inputG")
        sub = node("plusMinusAverage", "toeFromRoll_SUB")
        cmds.setAttr(f"{sub}.operation", 2)
        cmds.connectAttr(f"{c}.roll", f"{sub}.input1D[0]")
        cmds.connectAttr(f"{c}.rollStartAngle", f"{sub}.input1D[1]")
        cmds.connectAttr(f"{sub}.output1D", f"{clamp}.inputB")
        cmds.connectAttr(f"{c}.rollStartAngle", f"{clamp}.maxG")
        for ch in ("R", "B"):
            cmds.setAttr(f"{clamp}.max{ch}", 9999.0)

        # heel: the whole foot is IN FRONT of this pivot, and +rotateX
        # swings +Z points down, so a positive heel roll rotates -X for the
        # toes to rise. (ball / toe tip pivot with the heel behind them.)
        heel_sum = node("plusMinusAverage", "heelSum_PMA")
        cmds.setAttr(f"{heel_sum}.operation", 2)
        cmds.setAttr(f"{heel_sum}.input1D[0]", 0.0)
        cmds.connectAttr(f"{c}.heelRoll", f"{heel_sum}.input1D[1]")
        cmds.connectAttr(f"{clamp}.outputR", f"{heel_sum}.input1D[2]")
        cmds.connectAttr(f"{heel_sum}.output1D", f"{heel}.rotateX")

        for attr, out, loc in (("ballRoll", "outputG", ball),
                               ("toeRoll", "outputB", toetip)):
            total = node("plusMinusAverage", f"{attr}Sum_PMA")
            cmds.connectAttr(f"{c}.{attr}", f"{total}.input1D[0]")
            cmds.connectAttr(f"{clamp}.{out}", f"{total}.input1D[1]")
            cmds.connectAttr(f"{total}.output1D", f"{loc}.rotateX")

        # toeBend: positive lifts the toes (they're in front of the pivot).
        bend = node("multDoubleLinear", "toeBend_MULT")
        cmds.connectAttr(f"{c}.toeBend", f"{bend}.input1")
        cmds.setAttr(f"{bend}.input2", -1.0)
        cmds.connectAttr(f"{bend}.output", f"{toebend}.rotateX")

        # footBank: positive lifts the outside of the foot on either side.
        bank = node("multDoubleLinear", "bank_MULT")
        cmds.connectAttr(f"{c}.footBank", f"{bank}.input1")
        cmds.setAttr(f"{bank}.input2", 1.0 if self.side == "L" else -1.0)
        cmds.connectAttr(f"{bank}.output", f"{heel}.rotateZ")

    # ------------------------------------------------------------------------

    def _create_settings_ctrl(self):
        self.settings_ctrl = create_gear_ctrl(
            f"{self.prefix}_SETTINGS_CTRL", size=0.5 * SCALE,
            color=COLOR_SETTINGS,
        )
        # Park the settings ctrl beside the fetlock (hoof) or the wrist /
        # ankle (paws, claws, arms).
        anchor = (self.layout["rev"][1] if self.foot == "hoof"
                  else self.layout["ik"][2])
        anchor_jnt = self.bind_jnts[self.slot_idx[anchor]]
        cmds.matchTransform(self.settings_ctrl, anchor_jnt,
                            pos=True, rot=False)
        cmds.move(2.5 * SCALE * (1 if self.side == "L" else -1), 0, 0,
                  self.settings_ctrl, r=True, ws=True)
        offset = make_offset_group(self.settings_ctrl)
        cmds.parent(offset, self.ctrl_grp)
        cmds.parentConstraint(anchor_jnt, offset, mo=True)

        # 0 = IK (default), 1 = FK.
        cmds.addAttr(self.settings_ctrl, ln="ikFkSwitch", at="double",
                     min=0, max=1, dv=0, k=True)
        lock_hide_attrs(self.settings_ctrl,
                        ["tx", "ty", "tz", "rx", "ry", "rz",
                         "sx", "sy", "sz", "v"])

    # ------------------------------------------------------------------------

    def _create_ikfk_blend(self):
        """Blend the BIND chain between FK and IK.

        Joints from the first IK slot down to the tip get a world-space
        orientConstraint blend. FK-only joints above the IK chain (the
        scapula) are simply parent-constrained to their FK ctrl — they
        don't switch.
        """
        # FK-only joints above the IK chain (scapula).
        for slot in self.layout["fk_only_top"]:
            idx = self.slot_idx[slot]
            cmds.parentConstraint(self.fk_ctrls[idx],
                                  self.bind_jnts[idx], mo=True)

        rev_node = cmds.createNode("reverse",
                                   n=f"{self.prefix}_ikfk_REV")
        cmds.connectAttr(f"{self.settings_ctrl}.ikFkSwitch",
                         f"{rev_node}.inputX")

        start_idx = self.slot_idx[self.layout["ik"][0]]
        for i in range(start_idx, len(self.bind_jnts)):
            bind = self.bind_jnts[i]
            fk = self.fk_jnts[i]
            ik = self.ik_jnts[i]
            oc = cmds.orientConstraint(fk, ik, bind, mo=True)[0]
            cmds.setAttr(f"{oc}.interpType", 2)   # shortest path
            weights = cmds.orientConstraint(oc, q=True, wal=True)
            # weights[0] = FK target, weights[1] = IK target.
            cmds.connectAttr(f"{self.settings_ctrl}.ikFkSwitch",
                             f"{oc}.{weights[0]}")
            cmds.connectAttr(f"{rev_node}.outputX",
                             f"{oc}.{weights[1]}")

    # ------------------------------------------------------------------------

    def _create_visibility_sdk(self):
        """IK ctrl + PV visible at switch=0; switchable FK ctrls visible
        at switch=1. The scapula ctrl is left always-visible."""
        driver = f"{self.settings_ctrl}.ikFkSwitch"
        for ctrl in (self.ik_ctrl, self.pv_ctrl):
            cmds.setAttr(f"{ctrl}.v", l=False)
            cmds.setDrivenKeyframe(f"{ctrl}.v", cd=driver, dv=0.0, v=1,
                                   itt="linear", ott="step")
            cmds.setDrivenKeyframe(f"{ctrl}.v", cd=driver, dv=0.999, v=1,
                                   itt="linear", ott="step")
            cmds.setDrivenKeyframe(f"{ctrl}.v", cd=driver, dv=1.0, v=0,
                                   itt="linear", ott="step")
        for ctrl in self.switchable_fk_ctrls:
            cmds.setAttr(f"{ctrl}.v", l=False)
            cmds.setDrivenKeyframe(f"{ctrl}.v", cd=driver, dv=0.0, v=0,
                                   itt="linear", ott="step")
            cmds.setDrivenKeyframe(f"{ctrl}.v", cd=driver, dv=0.001, v=1,
                                   itt="linear", ott="step")
            cmds.setDrivenKeyframe(f"{ctrl}.v", cd=driver, dv=1.0, v=1,
                                   itt="linear", ott="step")

    # ------------------------------------------------------------------------

    def _create_digits(self):
        """Toes (paw, claw) or fingers (arm): each a base → middle → tip
        chain under the paw / ball / wrist joint, with an FK ctrl on the
        base and middle joints. They follow the foot in IK and FK.

        Dials on the SETTINGS ctrl, added to each ctrl's AUTO group so hand
        posing composes on top:
            toeCurl / fingerCurl     +10 curls down, -10 flexes up
            toeSpread / fingerSpread +10 fans them out
            sickleClaw (claw feet)   +10 raises the sickle claw, -10 strikes
        """
        digits = self.positions.get("digits") or {}
        names = [d for d in qg.DIGITS[self.foot] if d in digits]
        if not names:
            return
        foot_bind = self.bind_jnts[len(self.layout["slots"]) - 1]
        foot_pos = self.positions[self.layout["slots"][-1]]
        out = 1.0 if self.side == "L" else -1.0
        word = "finger" if self.foot == "arm" else "toe"
        settings = self.settings_ctrl
        cmds.addAttr(settings, ln=f"{word}Curl", at="double",
                     min=-10, max=10, dv=0, k=True)
        cmds.addAttr(settings, ln=f"{word}Spread", at="double",
                     min=-10, max=10, dv=0, k=True)
        sickle = self.foot == "claw" and "digit2" in names
        if sickle:
            cmds.addAttr(settings, ln="sickleClaw", at="double",
                         min=-10, max=10, dv=0, k=True)

        grp = cmds.group(em=True, n=f"{self.prefix}_digits_GRP")
        cmds.parent(grp, self.ctrl_grp)
        cmds.parentConstraint(foot_bind, grp, mo=True)

        # How far out from the middle each digit sits (-1 inner .. +1 outer).
        lateral = {d: (digits[d][0][0] - foot_pos[0]) * out for d in names}
        widest = max(abs(v) for v in lateral.values()) or 1.0
        top = self.positions[self.layout["ik"][0]][1]
        radius = 0.25 * SCALE * max(0.35, min(1.0, top / 150.0))

        for d in names:
            base, mid, tip = digits[d]
            cmds.select(cl=True)
            jnts = [cmds.joint(n=f"{self.prefix}_{d}_01_BIND_JNT", p=base),
                    cmds.joint(n=f"{self.prefix}_{d}_02_BIND_JNT", p=mid),
                    cmds.joint(n=f"{self.prefix}_{d}Tip_BIND_JNT", p=tip)]
            cmds.joint(jnts[0], e=True, oj="xyz", sao="yup", ch=True,
                       zso=True)
            cmds.setAttr(f"{jnts[-1]}.jointOrient", 0, 0, 0)
            for j in jnts:
                cmds.setAttr(f"{j}.radius", 0.3 * SCALE)
            jnts[0] = cmds.parent(jnts[0], foot_bind)[0]

            ctrls, prev = [], grp
            for i, part in enumerate(("01", "02")):
                bind = f"{self.prefix}_{d}_{part}_BIND_JNT"
                ctrl = create_circle_ctrl(
                    f"{self.prefix}_{d}_{part}_FK_CTRL", radius=radius,
                    normal=(1, 0, 0), color=self.color)
                cmds.matchTransform(ctrl, bind)
                offset = make_offset_group(ctrl)
                auto = cmds.group(em=True, n=ctrl.replace("_CTRL", "_AUTO"))
                cmds.matchTransform(auto, bind)
                cmds.parent(auto, offset)
                cmds.parent(ctrl, auto)
                cmds.parent(offset, prev)
                cmds.parentConstraint(ctrl, bind, mo=True)
                lock_hide_attrs(ctrl, ["tx", "ty", "tz",
                                       "sx", "sy", "sz", "v"])

                # Curl bends about the joint's local Z (+Z lifts the tip,
                # so a positive curl is negative).
                total = cmds.createNode(
                    "plusMinusAverage", n=f"{self.prefix}_{d}_{part}_curl_PMA")
                curl = cmds.createNode(
                    "multDoubleLinear", n=f"{self.prefix}_{d}_{part}_curl_MUL")
                cmds.connectAttr(f"{settings}.{word}Curl", f"{curl}.input1")
                cmds.setAttr(f"{curl}.input2", -3.0 if i == 0 else -5.0)
                cmds.connectAttr(f"{curl}.output", f"{total}.input1D[0]")
                if sickle and d == "digit2" and i == 1:
                    claw = cmds.createNode(
                        "multDoubleLinear", n=f"{self.prefix}_sickleClaw_MUL")
                    cmds.connectAttr(f"{settings}.sickleClaw",
                                     f"{claw}.input1")
                    cmds.setAttr(f"{claw}.input2", 6.0)
                    cmds.connectAttr(f"{claw}.output", f"{total}.input1D[1]")
                cmds.connectAttr(f"{total}.output1D", f"{auto}.rotateZ")

                # Spread turns the base joint about its local Y (about up):
                # +Y swings the tip toward world +X, the outside of a left
                # foot.
                if i == 0 and abs(lateral[d]) > 1e-6:
                    spread = cmds.createNode(
                        "multDoubleLinear", n=f"{self.prefix}_{d}_spread_MUL")
                    cmds.connectAttr(f"{settings}.{word}Spread",
                                     f"{spread}.input1")
                    cmds.setAttr(f"{spread}.input2",
                                 1.5 * lateral[d] / widest * out)
                    cmds.connectAttr(f"{spread}.output", f"{auto}.rotateY")
                ctrls.append(ctrl)
                prev = ctrl
            self.digits[d] = {"jnts": jnts, "ctrls": ctrls}


# =============================================================================
# QUADRUPED RIG  (orchestrator)
# =============================================================================

class QuadrupedRig(object):
    """Builds a full four-legged rig: core, horizontal ribbon spine,
    neck + head, four QuadLegRig limbs, and a tail.

    Pass `positions` (a nested dict, as QuadGuideSystem.read_positions()
    returns) to drive proportions, or leave it None to use an animal's
    defaults: animal = "horse" (default), "cat" or "raptor". `feet`
    ({"front": ..., "back": ...}) overrides the foot types; `toes=False`
    skips the toe / finger chains.
    """

    TOP_GROUP = "QUADRUPED_RIG_GRP"

    # ---- Baked-in horse default proportions (Maya units, SCALE=10) -------
    # +Z faces forward, Y up, ground at Y=0.
    DEFAULT_POSITIONS = {
        "core":  {"cog": (0, 110, 0)},
        "spine": {
            "hip":   (0, 150, -60),   # croup  (rear of the back)
            "chest": (0, 158,  60),   # withers (front of the back)
        },
        "neck": {
            "neck":     (0, 160,  68),
            "head":     (0, 195, 100),
            "head_tip": (0, 172, 128),
        },
        "L_frontLeg": {
            "scapula":  (15, 160,  52),
            "shoulder": (22, 120,  62),
            "elbow":    (24,  88,  50),
            "knee":     (25,  50,  58),
            "fetlock":  (25,  22,  52),
            "hoof":     (25,   4,  55),
            "hoofTip":  (25,   1,  64),
        },
        "L_backLeg": {
            "hip":      (16, 150, -54),
            "stifle":   (24, 100, -36),
            "hock":     (25,  55, -64),
            "fetlock":  (25,  24, -52),
            "hoof":     (25,   4, -54),
            "hoofTip":  (25,   1, -45),
        },
        "tail": {
            "tail_01": (0, 148,  -66),
            "tail_02": (0, 142,  -76),
            "tail_03": (0, 134,  -86),
            "tail_04": (0, 124,  -95),
            "tail_05": (0, 113, -103),
            "tail_06": (0, 102, -110),
            "tail_07": (0,  92, -116),
            "tip":     (0,  84, -121),
        },
        # Face — jaw (mouth open/close), tongue, eyes, ears. Positions
        # sit on the horse head (poll ~Y195/Z100, muzzle ~Y172/Z128).
        "face": {
            "jaw":        (  0, 184, 106),   # jaw hinge
            "jawTip":     (  0, 166, 125),   # chin / front of lower jaw
            "L_eye":      (  9, 187, 110),
            "R_eye":      ( -9, 187, 110),
            "eyesLookAt": (  0, 187, 165),   # look-at target, forward
            "tongue01":   (  0, 175, 112),   # tongue base (in the mouth)
            "tongue02":   (  0, 173, 118),
            "tongue03":   (  0, 171, 124),   # tongue tip
            "L_ear":      (  7, 205,  96),
            "R_ear":      ( -7, 205,  96),
        },
    }

    DEFAULT_MODULES = {"spine", "neck", "frontLegs", "backLegs",
                       "tail", "face"}

    def __init__(self, positions=None, modules=None,
                 bendy_count=7, spine_fk_count=3, tail_joint_count=7,
                 animal=None, feet=None, toes=True):
        feet = dict(feet or {})
        if positions is None:
            positions = self._default_positions(
                animal or "horse", feet.get("front"), feet.get("back"))
        self.positions = positions
        given = positions.get("feet") or {}
        self.feet = {k: feet.get(k) or given.get(k) or "hoof"
                     for k in ("front", "back")}
        self.animal = animal or positions.get("animal") or "horse"
        self.toes = toes
        self.modules = set(modules) if modules else set(self.DEFAULT_MODULES)
        self.bendy_count = bendy_count
        self.spine_fk_count = spine_fk_count
        self.tail_joint_count = tail_joint_count

        self.core = None
        self.spine = None
        self.neck = None
        self.face = None
        self.L_front = self.R_front = None
        self.L_back = self.R_back = None
        self.tail = None

    # ------------------------------------------------------------------------

    @classmethod
    def _default_positions(cls, animal="horse", front=None, back=None):
        """An animal's default positions (both sides), with its feet or the
        ones given."""
        if animal == "horse" and front in (None, "hoof") \
                and back in (None, "hoof"):
            import copy
            pos = copy.deepcopy(cls.DEFAULT_POSITIONS)
            for side_key in ("frontLeg", "backLeg"):
                l = pos[f"L_{side_key}"]
                pos[f"R_{side_key}"] = {slot: (-p[0], p[1], p[2])
                                        for slot, p in l.items()}
            pos["feet"] = {"front": "hoof", "back": "hoof"}
            pos["animal"] = "horse"
            return pos
        return qg.default_positions(animal, front, back)

    # ------------------------------------------------------------------------

    def _size_factor(self):
        """Guides scaled as a unit should build a rig scaled to match:
        joints follow the guides already, but control sizes come from the
        module SCALE. Measure the hip -> chest span against the default
        guides; a default-size rig returns 1.0 (nothing changes)."""
        try:
            sp = (self.positions or {}).get("spine") or {}
            hip, chest = sp.get("hip"), sp.get("chest")
            dfl = self.DEFAULT_POSITIONS["spine"]
            if hip and chest:
                cur = sum((a - b) ** 2
                          for a, b in zip(hip, chest)) ** 0.5
                base = sum((a - b) ** 2 for a, b in
                           zip(dfl["hip"], dfl["chest"])) ** 0.5
                if base > 1e-4 and cur > 1e-4:
                    return cur / base
        except Exception:
            pass
        return 1.0

    def build(self):
        if cmds.objExists(self.TOP_GROUP):
            cmds.error(f"{self.TOP_GROUP} already exists. Delete it first.")
            return
        # Controls and joints are drawn at the guides' size (1.0 = the
        # default horse: nothing changes). CoreRig draws the global and COG
        # controls from the character builder's own SCALE, so set both.
        import character_rig_builder as _crb
        global SCALE
        _base, _core = SCALE, _crb.SCALE
        factor = self._size_factor()
        SCALE, _crb.SCALE = _base * factor, _core * factor
        if abs(factor - 1.0) > 0.02:
            print(f"[QuadrupedRig] Guides are ~{factor:.2f}x the default "
                  f"size: scaling controls + joints to match.")
        try:
            return self._build_impl()
        finally:
            SCALE, _crb.SCALE = _base, _core

    def _build_impl(self):
        print(f"[QuadrupedRig] Building modules: {sorted(self.modules)} "
              f"({self.feet['front']} front, {self.feet['back']} back)")

        # 1. Core — reuse CoreRig, then rename its top group.
        self.core = CoreRig(positions=self.positions.get("core"))
        self.core.build()
        cmds.rename("CHARACTER_RIG_GRP", self.TOP_GROUP)
        cg, jg, mg = (self.core.ctrl_grp, self.core.jnt_grp,
                      self.core.misc_grp)
        for attr, value in (("animal", self.animal),
                            ("frontFeet", self.feet["front"]),
                            ("backFeet", self.feet["back"])):
            cmds.addAttr(self.TOP_GROUP, ln=attr, dt="string")
            cmds.setAttr(f"{self.TOP_GROUP}.{attr}", value, type="string")

        # 2. Spine — horizontal ribbon (withers ↔ croup).
        self.spine = SpineRig(
            positions=self.positions.get("spine"),
            bendy_count=self.bendy_count,
            fk_chain_count=self.spine_fk_count,
            parent_ctrl=self.core.cog_ctrl,
            ctrl_grp=cg, jnt_grp=jg, misc_grp=mg,
        )
        self.spine.build()

        chest_ctrl  = self.spine.chest_ctrl       # withers end
        chest_jnt   = self.spine.chest_bind_jnt
        pelvis_ctrl = self.spine.hip_ctrl         # croup end
        pelvis_jnt  = self.spine.pelvis_bind_jnt

        # 3. Neck + head — parented to the withers.
        if "neck" in self.modules:
            self.neck = NeckHeadRig(
                positions=self.positions.get("neck"),
                parent_ctrl=chest_ctrl, parent_jnt=chest_jnt,
                ctrl_grp=cg, jnt_grp=jg,
            )
            self.neck.build()

        # 3.5 Face — jaw (mouth open/close), eyes, tongue, ears.
        # Reuses the biped FaceRig with just those four submodules.
        # Parents to the head joint + head ctrl from the neck module.
        if "face" in self.modules and self.neck:
            head_jnt  = self.neck.head_jnt       # C_head_BIND_JNT
            head_ctrl = self.neck.head_ctrl      # C_head_CTRL
            self.face = FaceRig(
                positions=self.positions.get("face"),
                parent_ctrl=head_ctrl, parent_jnt=head_jnt,
                ctrl_grp=cg, jnt_grp=jg, misc_grp=mg,
                submodules={"jaw", "eyes", "tongue", "ears"},
            )
            self.face.build()

        # 4. Front legs (or arms) — parented to the withers end of the spine.
        if "frontLegs" in self.modules:
            self.L_front, self.R_front = (
                QuadLegRig(
                    side, "front", self.positions.get(f"{side}_frontLeg"),
                    parent_ctrl=chest_ctrl, parent_jnt=chest_jnt,
                    ctrl_grp=cg, jnt_grp=jg, misc_grp=mg,
                    foot=self.feet["front"], toes=self.toes,
                ) for side in ("L", "R"))
            self.L_front.build()
            self.R_front.build()

        # 5. Back legs — parented to the croup end of the spine.
        if "backLegs" in self.modules:
            self.L_back, self.R_back = (
                QuadLegRig(
                    side, "back", self.positions.get(f"{side}_backLeg"),
                    parent_ctrl=pelvis_ctrl, parent_jnt=pelvis_jnt,
                    ctrl_grp=cg, jnt_grp=jg, misc_grp=mg,
                    foot=self.feet["back"], toes=self.toes,
                ) for side in ("L", "R"))
            self.L_back.build()
            self.R_back.build()

        # 6. Tail — parented to the croup.
        if "tail" in self.modules:
            self.tail = TailRig(
                positions=self.positions.get("tail"),
                joint_count=self.tail_joint_count,
                parent_ctrl=pelvis_ctrl, parent_jnt=pelvis_jnt,
                ctrl_grp=cg, jnt_grp=jg, misc_grp=mg,
            )
            self.tail.build()

        # 7. Lock the organisational groups.
        for grp in (self.core.main_grp, self.core.ctrl_grp,
                    self.core.jnt_grp):
            lock_hide_attrs(grp, ["tx", "ty", "tz", "rx", "ry", "rz",
                                  "sx", "sy", "sz"])

        # Hide guide locators if a quadruped guide group is present.
        for guide_grp in ("QUAD_RIG_GUIDES_GRP", "RIG_GUIDES_GRP"):
            if cmds.objExists(guide_grp):
                try:
                    cmds.setAttr(f"{guide_grp}.visibility", 0)
                except Exception:
                    pass

        cmds.select(cl=True)
        print(f"[QuadrupedRig] Done. Top node: {self.TOP_GROUP}")

    # ------------------------------------------------------------------------

    def delete(self):
        if cmds.objExists(self.TOP_GROUP):
            cmds.delete(self.TOP_GROUP)
