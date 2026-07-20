"""
===============================================================================
 QUADRUPED RIG BUILDER  (horse template)
===============================================================================

 Builds a four-legged character rig. Reuses the orientation-agnostic biped
 modules (CoreRig, SpineRig, NeckHeadRig, TailRig) from
 character_rig_builder.py and adds a dedicated QuadLegRig for the four legs.

 Anatomy:
   * Front legs  — scapula (FK) + shoulder/elbow/knee (3-joint RP IK)
                   + fetlock/hoof (reverse-foot hoof roll)
   * Back legs   — hip/stifle/hock (3-joint RP IK, backward-bending hock)
                   + fetlock/hoof (reverse-foot hoof roll)

 Usage in Maya (Python):
     import quadruped_rig_builder
     from importlib import reload
     reload(quadruped_rig_builder)
     rig = quadruped_rig_builder.QuadrupedRig()
     rig.build()

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
    create_circle_ctrl, create_diamond_ctrl, create_foot_ctrl,
    create_gear_ctrl,
    CoreRig, SpineRig, NeckHeadRig, TailRig, FaceRig,
)


# =============================================================================
# QUAD LEG RIG  (one leg — front or back)
# =============================================================================

class QuadLegRig(object):
    """One quadruped leg.

    leg_type 'front':
        scapula → shoulder → elbow → knee → fetlock → hoof  (+ hoofTip)
        scapula is FK-only. shoulder/elbow/knee form a 3-joint RP IK.
        knee/fetlock/hoof form the reverse-foot (hoof roll) section.

    leg_type 'back':
        hip → stifle → hock → fetlock → hoof  (+ hoofTip)
        hip/stifle/hock form a 3-joint RP IK (hock bends backward via
        guide placement). hock/fetlock/hoof form the reverse-foot section.

    Each leg has an IK/FK switch (0 = IK, 1 = FK — matches the biped
    convention), a pole vector, and a hoof reverse-foot setup with
    heel / hoof / fetlock roll attributes on the IK ctrl.
    """

    LAYOUT = {
        "front": {
            "slots":       ["scapula", "shoulder", "elbow",
                            "knee", "fetlock", "hoof"],
            "ik":          ("shoulder", "elbow", "knee"),
            "rev":         ("knee", "fetlock", "hoof"),
            "fk_only_top": ["scapula"],   # FK-only joints above the IK chain
        },
        "back": {
            "slots":       ["hip", "stifle", "hock", "fetlock", "hoof"],
            "ik":          ("hip", "stifle", "hock"),
            "rev":         ("hock", "fetlock", "hoof"),
            "fk_only_top": [],
        },
    }

    def __init__(self, side, leg_type, positions,
                 parent_ctrl, parent_jnt,
                 ctrl_grp, jnt_grp, misc_grp):
        assert side in ("L", "R")
        assert leg_type in ("front", "back")
        self.side = side
        self.leg_type = leg_type
        self.prefix = f"{side}_{leg_type}Leg"
        self.layout = self.LAYOUT[leg_type]
        self.slot_idx = {s: i for i, s in enumerate(self.layout["slots"])}
        self.positions = positions or {}
        self.parent_ctrl = parent_ctrl
        self.parent_jnt = parent_jnt
        self.ctrl_grp = ctrl_grp
        self.jnt_grp = jnt_grp
        self.misc_grp = misc_grp
        self.color = COLOR_LEFT if side == "L" else COLOR_RIGHT

        # Storage
        self.bind_jnts = []     # includes the hoofTip leaf joint at [-1]
        self.fk_jnts = []
        self.ik_jnts = []
        self.fk_ctrls = []          # one per slot (no ctrl on hoofTip)
        self.switchable_fk_ctrls = []   # FK ctrls hidden in IK mode
        self.ik_ctrl = self.pv_ctrl = self.settings_ctrl = None
        self.ik_handle_main = None      # RP IK over the 3-joint chain
        self.ik_handle_fetlock = None   # SC: ik-end → fetlock
        self.ik_handle_hoof = None      # SC: fetlock → hoof
        self.foot_locators = {}

    # ------------------------------------------------------------------------

    def build(self):
        self._create_joint_chains()
        self._create_fk()
        self._create_ik()
        self._create_reverse_foot()
        self._create_settings_ctrl()
        self._create_ikfk_blend()
        self._create_visibility_sdk()
        # Hide the FK + IK driver chains; only BIND stays visible.
        cmds.setAttr(f"{self.fk_jnts[0]}.v", 0)
        cmds.setAttr(f"{self.ik_jnts[0]}.v", 0)

    # ------------------------------------------------------------------------

    def _create_joint_chains(self):
        slots = self.layout["slots"]
        cmds.select(cl=True)
        for slot in slots:
            j = cmds.joint(n=f"{self.prefix}_{slot}_BIND_JNT",
                           p=self.positions[slot])
            self.bind_jnts.append(j)
        tip = cmds.joint(n=f"{self.prefix}_hoofTip_BIND_JNT",
                         p=self.positions["hoofTip"])
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
        """One FK ctrl per BIND joint (excluding hoofTip), chained. The
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

    def _create_ik(self):
        ik_slots = self.layout["ik"]
        rev = self.layout["rev"]
        s0 = self.slot_idx[ik_slots[0]]
        s1 = self.slot_idx[ik_slots[1]]
        s2 = self.slot_idx[ik_slots[2]]
        fet = self.slot_idx[rev[1]]
        hf = self.slot_idx[rev[2]]

        # RP IK over the 3-joint upper chain (shoulder/elbow/knee or
        # hip/stifle/hock).
        self.ik_handle_main = cmds.ikHandle(
            sj=self.ik_jnts[s0], ee=self.ik_jnts[s2],
            sol="ikRPsolver", n=f"{self.prefix}_ikHandle_main",
        )[0]
        # SC IK: ik-chain-end → fetlock, then fetlock → hoof.
        self.ik_handle_fetlock = cmds.ikHandle(
            sj=self.ik_jnts[s2], ee=self.ik_jnts[fet],
            sol="ikSCsolver", n=f"{self.prefix}_ikHandle_fetlock",
        )[0]
        self.ik_handle_hoof = cmds.ikHandle(
            sj=self.ik_jnts[fet], ee=self.ik_jnts[hf],
            sol="ikSCsolver", n=f"{self.prefix}_ikHandle_hoof",
        )[0]
        for h in (self.ik_handle_main, self.ik_handle_fetlock,
                  self.ik_handle_hoof):
            cmds.setAttr(f"{h}.v", 0)

        # IK ctrl — a foot-shaped ctrl on the ground at the hoof.
        hoof_pos = self.positions[rev[2]]
        ctrl_pos = (hoof_pos[0], 0.0, hoof_pos[2])
        self.ik_ctrl = create_foot_ctrl(
            f"{self.prefix}_IK_CTRL", size=1.6 * SCALE, color=COLOR_IK,
        )
        cmds.xform(self.ik_ctrl, ws=True, t=ctrl_pos)
        ik_offset = make_offset_group(self.ik_ctrl)
        cmds.parent(ik_offset, self.ctrl_grp)
        lock_hide_attrs(self.ik_ctrl, ["sx", "sy", "sz", "v"])

        # Pole vector — auto-placed in the bend plane of the IK chain.
        pv_pos = get_pole_vector_position(
            self.ik_jnts[s0], self.ik_jnts[s1], self.ik_jnts[s2],
            distance=8.0 * SCALE,
        )
        self.pv_ctrl = create_diamond_ctrl(
            f"{self.prefix}_PV_CTRL", size=0.7 * SCALE, color=COLOR_PV,
        )
        cmds.xform(self.pv_ctrl, ws=True, t=pv_pos)
        pv_offset = make_offset_group(self.pv_ctrl)
        cmds.parent(pv_offset, self.ctrl_grp)
        cmds.poleVectorConstraint(self.pv_ctrl, self.ik_handle_main)
        lock_hide_attrs(self.pv_ctrl, ["rx", "ry", "rz",
                                       "sx", "sy", "sz", "v"])

    # ------------------------------------------------------------------------

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

        def mk_loc(name, pos):
            loc = cmds.spaceLocator(n=name)[0]
            cmds.xform(loc, ws=True, t=pos)
            cmds.setAttr(f"{loc}Shape.visibility", 0)
            return loc

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

    # ------------------------------------------------------------------------

    def _create_settings_ctrl(self):
        self.settings_ctrl = create_gear_ctrl(
            f"{self.prefix}_SETTINGS_CTRL", size=0.5 * SCALE,
            color=COLOR_SETTINGS,
        )
        # Park the settings ctrl beside the fetlock.
        fet_jnt = self.bind_jnts[self.slot_idx[self.layout["rev"][1]]]
        cmds.matchTransform(self.settings_ctrl, fet_jnt,
                            pos=True, rot=False)
        cmds.move(2.5 * SCALE * (1 if self.side == "L" else -1), 0, 0,
                  self.settings_ctrl, r=True, ws=True)
        offset = make_offset_group(self.settings_ctrl)
        cmds.parent(offset, self.ctrl_grp)
        cmds.parentConstraint(fet_jnt, offset, mo=True)

        # 0 = IK (default), 1 = FK.
        cmds.addAttr(self.settings_ctrl, ln="ikFkSwitch", at="double",
                     min=0, max=1, dv=0, k=True)
        lock_hide_attrs(self.settings_ctrl,
                        ["tx", "ty", "tz", "rx", "ry", "rz",
                         "sx", "sy", "sz", "v"])

    # ------------------------------------------------------------------------

    def _create_ikfk_blend(self):
        """Blend the BIND chain between FK and IK.

        Joints from the first IK slot down to the hoofTip get a
        world-space orientConstraint blend. FK-only joints above the IK
        chain (the scapula) are simply parent-constrained to their FK
        ctrl — they don't switch.
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


# =============================================================================
# QUADRUPED RIG  (orchestrator — horse)
# =============================================================================

class QuadrupedRig(object):
    """Builds a full four-legged rig: core, horizontal ribbon spine,
    neck + head, four QuadLegRig legs, and a tail.

    Pass `positions` (a nested dict, same idea as CharacterRig) to drive
    proportions, or leave it None to use the baked-in horse defaults.
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
                 bendy_count=7, spine_fk_count=3, tail_joint_count=7):
        self.positions = positions or self._default_positions()
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
    def _default_positions(cls):
        """Deep-copy the baked-in defaults and mirror the L legs to R."""
        import copy
        pos = copy.deepcopy(cls.DEFAULT_POSITIONS)
        for side_key in ("frontLeg", "backLeg"):
            l = pos[f"L_{side_key}"]
            r = {}
            for slot, p in l.items():
                r[slot] = (-p[0], p[1], p[2])   # mirror X
            pos[f"R_{side_key}"] = r
        return pos

    # ------------------------------------------------------------------------

    def build(self):
        if cmds.objExists(self.TOP_GROUP):
            cmds.error(f"{self.TOP_GROUP} already exists. Delete it first.")
            return
        print(f"[QuadrupedRig] Building modules: {sorted(self.modules)}")

        # 1. Core — reuse CoreRig, then rename its top group.
        self.core = CoreRig(positions=self.positions.get("core"))
        self.core.build()
        cmds.rename("CHARACTER_RIG_GRP", self.TOP_GROUP)
        cg, jg, mg = (self.core.ctrl_grp, self.core.jnt_grp,
                      self.core.misc_grp)

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
            head_jnt  = self.neck.bind_jnts[1]   # C_head_BIND_JNT
            head_ctrl = self.neck.fk_ctrls[1]    # C_head_CTRL
            self.face = FaceRig(
                positions=self.positions.get("face"),
                parent_ctrl=head_ctrl, parent_jnt=head_jnt,
                ctrl_grp=cg, jnt_grp=jg, misc_grp=mg,
                submodules={"jaw", "eyes", "tongue", "ears"},
            )
            self.face.build()

        # 4. Front legs — parented to the withers end of the spine.
        if "frontLegs" in self.modules:
            self.L_front = QuadLegRig(
                "L", "front", self.positions.get("L_frontLeg"),
                parent_ctrl=chest_ctrl, parent_jnt=chest_jnt,
                ctrl_grp=cg, jnt_grp=jg, misc_grp=mg,
            )
            self.R_front = QuadLegRig(
                "R", "front", self.positions.get("R_frontLeg"),
                parent_ctrl=chest_ctrl, parent_jnt=chest_jnt,
                ctrl_grp=cg, jnt_grp=jg, misc_grp=mg,
            )
            self.L_front.build()
            self.R_front.build()

        # 5. Back legs — parented to the croup end of the spine.
        if "backLegs" in self.modules:
            self.L_back = QuadLegRig(
                "L", "back", self.positions.get("L_backLeg"),
                parent_ctrl=pelvis_ctrl, parent_jnt=pelvis_jnt,
                ctrl_grp=cg, jnt_grp=jg, misc_grp=mg,
            )
            self.R_back = QuadLegRig(
                "R", "back", self.positions.get("R_backLeg"),
                parent_ctrl=pelvis_ctrl, parent_jnt=pelvis_jnt,
                ctrl_grp=cg, jnt_grp=jg, misc_grp=mg,
            )
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
