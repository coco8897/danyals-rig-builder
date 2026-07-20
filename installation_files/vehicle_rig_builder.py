"""
===============================================================================
 VEHICLE RIG BUILDER  (generic 4-wheeled — sedan / truck / SUV template)
===============================================================================

 Builds a flexible 4-wheeled vehicle rig with toggleable submodules:

   * chassis        — main body ctrl + body BIND joint
   * wheels         — 4 wheels (LF/RF/LB/RB), each with a hub joint,
                      8 tire perimeter joints, auto-spin driven by the
                      chassis translateZ, and a per-wheel tirePressure
                      attribute that squashes the bottom + bulges the
                      sides for soft / flat tire deformation
   * steering       — steering wheel ctrl whose Z rotation drives the
                      front wheels' Y rotation (turning)
   * suspension     — per-wheel suspension ctrl (translate Y) — lets
                      animators dip individual wheels for terrain /
                      landings, plus a master "bodyLean" on the chassis
   * doors          — 4 hinged door ctrls (LF/RF/LB/RB) with an open
                      attribute that rotates them around their hinge
   * hood           — front-hood hinge ctrl that opens up + forward
   * tirePressure   — global "allTirePressure" attribute on the chassis
                      that broadcasts to every wheel; per-wheel attrs
                      stay overridable

 Top node: VEHICLE_RIG_GRP

 Usage in Maya (Python):

     import vehicle_rig_builder
     from importlib import reload; reload(vehicle_rig_builder)
     rig = vehicle_rig_builder.VehicleRig()
     rig.build()

===============================================================================
"""

import math
import maya.cmds as cmds

from character_rig_builder import (
    SCALE,
    COLOR_LEFT, COLOR_RIGHT, COLOR_CENTER, COLOR_IK, COLOR_PV,
    COLOR_BEND, COLOR_SETTINGS,
    make_offset_group, lock_hide_attrs,
    create_circle_ctrl, create_diamond_ctrl, create_cube_ctrl,
    create_square_ctrl, create_gear_ctrl,
    CoreRig,
)


# Ground reference: a locator at world Y=0 by default. Every tire joint
# compares its own world Y to this locator's world Y to decide whether
# it's intersecting the ground and how much to deform. Parent this
# locator to a terrain mesh later for non-flat ground.
GROUND_LOC_NAME = "C_ground_LOC"


# =============================================================================
# VehicleWheel — one wheel with hub, 8 tire joints, auto-spin, pressure SDK
# =============================================================================

class VehicleWheel(object):
    """A single wheel — antCGi-style spoke tire.

    The tire is built from N spokes radiating out from the hub like a
    bicycle wheel. Each spoke is a 2-joint chain:

        hub  →  spoke_NN_inner_BIND_JNT  (at the rim / inner sidewall)
                  └── spoke_NN_outer_BIND_JNT  (at the tread surface)

    The outer joint is where the rubber meets the road. It compresses
    toward the hub when the spoke tip drops below the ground (squash) and
    can be pushed/pulled by a per-spoke ctrl (manual squash & stretch).
    Because the spoke joints live under a NON-spinning group, the contact
    patch stays anchored at the world bottom no matter how fast the wheel
    spins — only the hub (and the alloy mesh skinned to it) rotates.

    Joint hierarchy:
        {prefix}_hub_BIND_JNT                 (spins — alloy / rim)
        {prefix}_tireRoot_GRP                 (no spin)
          ├── spoke_01_inner_BIND_JNT
          │     └── spoke_01_outer_BIND_JNT   (driven: ground + ctrl)
          ├── spoke_02_inner_BIND_JNT …
          └── … N spokes

    Ctrl hierarchy:
        {prefix}_suspension_OFFSET  (parented to chassis_ctrl)
          └── {prefix}_suspension_CTRL    (translateY = suspension travel)
                └── {prefix}_steering_AUTO  (front wheels only)
                      ├── {prefix}_tireRoot_GRP   (spokes — no spin)
                      │     └── {prefix}_spoke_NN_CTRL × N (manual squash)
                      └── {prefix}_wheel_CTRL → spinAuto → hub_BIND_JNT
    """

    DEFAULT_SPOKE_COUNT = 16
    # Inner spoke joints sit at this fraction of the radius (rim edge).
    INNER_RADIUS_FRACTION = 0.62

    def __init__(self, prefix, position, radius, is_front, is_left,
                 chassis_ctrl, parent_jnt,
                 ctrl_grp, jnt_grp, misc_grp,
                 chassis_drive_attr=None,
                 steering_drive_attr=None,
                 ground_y_attr=None,
                 spoke_count=None,
                 build_spoke_ctrls=True,
                 build_spring=True,
                 spring_top_y=None,
                 spring_top_pos=None):
        """
        Args:
            prefix: "LF", "RF", "LB", "RB".
            position: world (x, y, z) of the hub.
            radius: wheel radius.
            is_front: True for the steered pair.
            is_left: True for left-side wheels.
            chassis_ctrl: the chassis transform — suspension parents here.
            parent_jnt: BIND chain parent (typically chassis_BIND_JNT).
            chassis_drive_attr: the translateZ attr that drives auto-spin.
            steering_drive_attr: the steering attr that drives front wheel
                steering. Pass None or set is_front=False to skip.
            ground_y_attr: the world-Y attribute of the ground reference
                (e.g. "C_ground_LOC_decompose.outputTranslateY"). Outer
                spoke joints compare their own world Y against this to
                decide ground compression. Pass None to skip.
            spoke_count: number of spokes around the tire (default 16).
            build_spoke_ctrls: build a manual squash/stretch ctrl per
                spoke (default True).
            build_spring: build a coil-over spring that compresses /
                extends as the suspension travels (default True).
        """
        self.prefix = prefix
        self.position = position
        self.radius = radius
        self.is_front = is_front
        self.is_left = is_left
        self.chassis_ctrl = chassis_ctrl
        self.parent_jnt = parent_jnt
        self.ctrl_grp = ctrl_grp
        self.jnt_grp = jnt_grp
        self.misc_grp = misc_grp
        self.chassis_drive_attr = chassis_drive_attr
        self.steering_drive_attr = steering_drive_attr
        self.ground_y_attr = ground_y_attr
        self.spoke_count = spoke_count or self.DEFAULT_SPOKE_COUNT
        self.build_spoke_ctrls = build_spoke_ctrls
        self.build_spring = build_spring
        self.spring_top_y = spring_top_y   # world Y of the coil-over top
        self.spring_top_pos = spring_top_pos  # full world XYZ (overrides Y)
        self.color = COLOR_LEFT if is_left else COLOR_RIGHT

        # Outputs
        self.hub_jnt = None
        self.inner_jnts = []        # N inner spoke joints (rim)
        self.outer_jnts = []        # N outer spoke joints (tread, driven)
        self.outer_offset_grps = [] # rest tread positions (ground ref)
        self.spoke_ctrls = []       # N manual squash ctrls (or empty)
        self.suspension_ctrl = None
        self.suspension_offset = None
        self.suspension_auto = None   # auto ground-follow group
        self.pressure_sag_grp = None  # deflation squat (lowers wheel centre)
        self.steering_auto = None
        self.spin_auto = None
        self.wheel_ctrl = None
        self.tire_root_grp = None   # non-spinning parent for spoke joints
        self.spring_jnt = None      # coil-over spring BIND joint
        self.spring_top_grp = None
        self.spring_bottom_grp = None

    def build(self):
        self._build_ctrl_hierarchy()
        self._add_wheel_attrs()
        self._build_hub()
        self._build_spokes()
        self._wire_auto_spin()
        self._wire_steering()
        self._wire_tire_deformation()
        if self.build_spring:
            self._build_suspension_spring()

    # -----------------------------------------------------------------------

    def _build_ctrl_hierarchy(self):
        """Build the ctrl chain. ONE spinning branch carries everything:

            suspension_CTRL → steering_AUTO → spinAuto → wheel_CTRL
                                                            ├── hub_BIND_JNT
                                                            └── tireRoot_GRP
                                                                  └── spokes

        The whole wheel — rim AND tire spokes — spins together under
        spinAuto, so the wheel visibly ROLLS when the car moves forward.
        The contact flat-spot is kept at the world bottom by computing
        each spoke's ground compression as a RADIAL shrink: a spoke is
        pulled toward the hub by however far its (spinning) rest tread
        point drops below the ground. Because the shrink is applied in
        the spoke's own LOCAL radial direction, the spin rotation maps it
        to the correct world-up direction automatically — so the flat
        spot stays planted at the bottom while the tread rolls through it.

        Steering turns this whole branch (rim + tire together) via
        steering_AUTO above the spin.
        """
        x, y, z = self.position

        # Suspension ctrl — animator-facing cube above the wheel.
        self.suspension_ctrl = create_cube_ctrl(
            f"{self.prefix}_suspension_CTRL",
            size=0.5 * SCALE, color=COLOR_BEND,
        )
        cmds.xform(self.suspension_ctrl, ws=True, t=(x, y, z))
        self.suspension_offset = make_offset_group(self.suspension_ctrl)
        cmds.parent(self.suspension_offset, self.chassis_ctrl)
        lock_hide_attrs(self.suspension_ctrl,
                         ["tx", "tz", "rx", "ry", "rz",
                          "sx", "sy", "sz"])

        # Suspension AUTO — the automatic ground-follow lives here, BETWEEN
        # the offset and the ctrl, so:
        #     offset  →  suspension_AUTO  (auto rides the terrain)
        #                  →  suspension_CTRL  (your manual offset on top)
        # The AUTO.translateY is driven by the ground height under this
        # wheel (see assign_ground_mesh / the auto-suspension wiring), so
        # the wheel lifts over bumps and the spring compresses, while the
        # animator can still hand-offset the CTRL on top. Toggle the
        # chassis autoSuspension attr to disable the automatic motion.
        self.suspension_auto = cmds.group(
            em=True, n=f"{self.prefix}_suspension_AUTO")
        cmds.matchTransform(self.suspension_auto, self.suspension_ctrl)
        cmds.parent(self.suspension_auto, self.suspension_offset)
        cmds.parent(self.suspension_ctrl, self.suspension_auto)

        # Pressure SAG — a deflated tyre's centre squats toward the ground.
        # This group lowers the whole wheel (rim + tread) by
        # tireSag * R * (1 - effectiveTirePressure). It sits BELOW the
        # auto-suspension (which seats the full-radius tyre on the terrain)
        # so the squat is NOT cancelled out; the lowered tread then
        # penetrates the ground and the per-spoke contact deformation
        # flattens it into a proper flat-tyre contact patch. At full
        # pressure the sag is 0, so nothing changes. Wired in
        # _wire_tire_deformation (needs effectiveTirePressure).
        self.pressure_sag_grp = cmds.group(
            em=True, n=f"{self.prefix}_pressureSag_GRP")
        cmds.matchTransform(self.pressure_sag_grp, self.suspension_ctrl)
        cmds.parent(self.pressure_sag_grp, self.suspension_ctrl)

        # Steering AUTO — turns the whole wheel (rim + tire). Front only,
        # but always built so the hierarchy is uniform across 4 wheels.
        self.steering_auto = cmds.group(em=True,
                                          n=f"{self.prefix}_steering_AUTO")
        cmds.matchTransform(self.steering_auto, self.pressure_sag_grp)
        cmds.parent(self.steering_auto, self.pressure_sag_grp)

        # Auto-spin group — rotateX driven by chassis translateZ.
        self.spin_auto = cmds.group(em=True,
                                      n=f"{self.prefix}_spinAuto")
        cmds.matchTransform(self.spin_auto, self.steering_auto)
        cmds.parent(self.spin_auto, self.steering_auto)

        # Animator-facing wheel ctrl — circle around the wheel axis.
        # Manual rotation composes on top of auto-spin.
        self.wheel_ctrl = create_circle_ctrl(
            f"{self.prefix}_wheel_CTRL",
            radius=self.radius * 0.12,
            normal=(1, 0, 0), color=self.color,
        )
        cmds.matchTransform(self.wheel_ctrl, self.spin_auto)
        offset = make_offset_group(self.wheel_ctrl)
        cmds.parent(offset, self.spin_auto)
        lock_hide_attrs(self.wheel_ctrl,
                         ["tx", "ty", "tz", "sx", "sy", "sz"])

        # Tire root — SPINS with the wheel (child of wheel_ctrl). The
        # spoke joints + ctrls live here so the tire rolls; the flat
        # spot is handled by radial shrink in _wire_tire_deformation.
        self.tire_root_grp = cmds.group(em=True,
                                          n=f"{self.prefix}_tireRoot_GRP")
        cmds.matchTransform(self.tire_root_grp, self.wheel_ctrl)
        cmds.parent(self.tire_root_grp, self.wheel_ctrl)

    def _build_hub(self):
        """Hub BIND under spinAuto — rotates with the wheel. The alloy /
        rim mesh skins to this so it spins; the tire spokes do not."""
        x, y, z = self.position
        cmds.select(cl=True)
        self.hub_jnt = cmds.joint(n=f"{self.prefix}_hub_BIND_JNT",
                                    p=(x, y, z))
        cmds.setAttr(f"{self.hub_jnt}.jointOrient", 0, 0, 0)
        cmds.setAttr(f"{self.hub_jnt}.radius", 0.4 * SCALE)
        cmds.parent(self.hub_jnt, self.wheel_ctrl)

    def _build_spokes(self):
        """Build N spokes radiating from the hub. Each spoke:
            inner_BIND_JNT (rim)  →  outer_offset_GRP (tread rest)
                                       →  outer_BIND_JNT (driven)
        plus an optional per-spoke ctrl at the tread.

        Everything lives under the NON-spinning tireRoot_GRP, so the
        contact deformation stays at the world bottom regardless of spin.
        The inner joints are world-aligned (identity rotation), so an
        outer joint's local +Y is world +Y — the ground network can drive
        translateY directly.
        """
        x, y, z = self.position
        R = self.radius
        inner_r = R * self.INNER_RADIUS_FRACTION

        for i in range(self.spoke_count):
            nn = f"{i + 1:02d}"
            deg = i * (360.0 / self.spoke_count)
            rad = math.radians(deg)
            # Angle 0 = top (+Y), wrap clockwise into the YZ plane
            # (wheel axis = X). cos→Y, sin→Z.
            dir_y = math.cos(rad)
            dir_z = math.sin(rad)

            # --- inner joint (rim), world-aligned ---
            cmds.select(cl=True)
            inner = cmds.joint(n=f"{self.prefix}_spoke_{nn}_inner_BIND_JNT")
            cmds.setAttr(f"{inner}.radius", 0.2 * SCALE)
            cmds.parent(inner, self.tire_root_grp, relative=True)
            cmds.setAttr(f"{inner}.translate",
                          0, inner_r * dir_y, inner_r * dir_z)
            cmds.setAttr(f"{inner}.jointOrient", 0, 0, 0)
            self.inner_jnts.append(inner)

            # --- outer offset group (tread rest position) ---
            # Local offset from inner to the tread point, in world dirs.
            off_y = (R - inner_r) * dir_y
            off_z = (R - inner_r) * dir_z
            outer_off = cmds.group(
                em=True, n=f"{self.prefix}_spoke_{nn}_outerOffset_GRP")
            cmds.parent(outer_off, inner, relative=True)
            cmds.setAttr(f"{outer_off}.translate", 0, off_y, off_z)
            self.outer_offset_grps.append(outer_off)

            # --- outer joint (tread, driven by ground + ctrl) ---
            cmds.select(cl=True)
            outer = cmds.joint(n=f"{self.prefix}_spoke_{nn}_outer_BIND_JNT")
            cmds.setAttr(f"{outer}.radius", 0.25 * SCALE)
            cmds.parent(outer, outer_off, relative=True)
            cmds.setAttr(f"{outer}.translate", 0, 0, 0)
            cmds.setAttr(f"{outer}.jointOrient", 0, 0, 0)
            self.outer_jnts.append(outer)

            # --- per-spoke manual ctrl (optional) ---
            if self.build_spoke_ctrls:
                ctrl = create_diamond_ctrl(
                    f"{self.prefix}_spoke_{nn}_CTRL",
                    size=0.18 * SCALE, color=self.color)
                # Place at the tread point (same world pos as outer rest).
                cmds.matchTransform(ctrl, outer_off)
                offset = make_offset_group(ctrl)
                cmds.parent(offset, self.tire_root_grp)
                lock_hide_attrs(ctrl, ["rx", "ry", "rz",
                                        "sx", "sy", "sz"])
                self.spoke_ctrls.append(ctrl)
            else:
                self.spoke_ctrls.append(None)

    def _add_wheel_attrs(self):
        """tirePressure (0=flat, 1=full) + wheelRadius (informational)
        + autoFromMaster + the hidden effectiveTirePressure proxy.

        Why the proxy: the tire SDK has to read from SOMETHING that's
        always wired to a value. If we drove tirePressure itself from
        the master broadcast, then turning autoFromMaster off would
        either leave the broadcast feeding 1.0 into the attribute (so
        the animator can't override) or break the connection (so the
        master never re-engages without a rebuild). The clean fix is to
        keep tirePressure freely animatable and have the SDK read from
        effectiveTirePressure, which is blend(tirePressure, master,
        autoFromMaster). When autoFromMaster=1 the master wins; when
        it's 0 the animator's own tirePressure keyframes drive the
        tire shape directly.
        """
        c = self.wheel_ctrl
        cmds.addAttr(c, ln="wheelHeader", at="enum", en="---WHEEL---:",
                      k=True)
        cmds.setAttr(f"{c}.wheelHeader", l=True, cb=True, k=False)
        cmds.addAttr(c, ln="tirePressure", at="double",
                      min=0, max=1, dv=1, k=True)
        cmds.addAttr(c, ln="wheelRadius", at="double",
                      min=0.01, dv=self.radius, k=True)
        cmds.addAttr(c, ln="autoFromMaster", at="bool", dv=True, k=True)
        # tireStiffness scales how much each joint moves up to meet the
        # ground when the tire is fully deflated. 1.0 = joint exactly
        # reaches the ground (perfectly soft). >1 = overshoots (use for
        # exaggerated cartoon flatten). <1 = under-compresses.
        cmds.addAttr(c, ln="tireStiffness", at="double",
                      min=0, max=4, dv=1.0, k=True)
        # How far a FULLY deflated tyre's centre squats toward the ground,
        # as a fraction of the radius. The wheel is lowered by
        # tireSag * R * (1 - effectiveTirePressure) so a flat tyre visibly
        # sags + flattens even on flat ground / while driving. 0 = the old
        # behaviour (only deforms against bumps).
        cmds.addAttr(c, ln="tireSag", at="double",
                      min=0, max=1, dv=0.25, k=True)
        # Hidden proxy that combines per-wheel + master. The deformation
        # network below uses this as its driver instead of tirePressure
        # directly.
        cmds.addAttr(c, ln="effectiveTirePressure", at="double",
                      min=0, max=1, dv=1)
        cmds.setAttr(f"{c}.effectiveTirePressure", k=False, cb=False)

    # -----------------------------------------------------------------------

    def _wire_auto_spin(self):
        """spin_auto.rotateX = chassis_CTRL.translateZ * 360 /
        (2 * pi * wheelRadius). Pure expression — no animation curves
        needed; instantaneous response to the chassis moving forward."""
        if not self.chassis_drive_attr:
            return
        # spinAngle_deg = translateZ * (360 / (2 * pi * R))
        # → multiplyDivide: spin = (1 / circumference) * translateZ
        #   then multDoubleLinear: * 360
        norm_md = cmds.createNode("multiplyDivide",
                                    n=f"{self.prefix}_spinNorm_MD")
        cmds.setAttr(f"{norm_md}.operation", 2)   # divide
        cmds.connectAttr(self.chassis_drive_attr,
                          f"{norm_md}.input1X")
        # Circumference = 2 * pi * radius. Drive it from wheelRadius
        # so the animator can re-tune at runtime if the wheel art
        # changes size.
        circ_md = cmds.createNode("multDoubleLinear",
                                    n=f"{self.prefix}_circumference_MUL")
        cmds.connectAttr(f"{self.wheel_ctrl}.wheelRadius",
                          f"{circ_md}.input1")
        cmds.setAttr(f"{circ_md}.input2", 2.0 * math.pi)
        cmds.connectAttr(f"{circ_md}.output",
                          f"{norm_md}.input2X")
        deg_md = cmds.createNode("multDoubleLinear",
                                   n=f"{self.prefix}_spinDeg_MUL")
        cmds.connectAttr(f"{norm_md}.outputX",
                          f"{deg_md}.input1")
        cmds.setAttr(f"{deg_md}.input2", 360.0)
        cmds.connectAttr(f"{deg_md}.output",
                          f"{self.spin_auto}.rotateX", f=True)

    # Steering ratio. NEGATIVE so the front wheels turn the SAME visual
    # direction the driver turns the wheel: turning the steering wheel
    # clockwise (positive rotateZ from the driver's seat) steers RIGHT
    # (wheels rotateY negative → wheels face -X / right). Both front
    # wheels share this sign so they turn together, never splitting.
    STEERING_RATIO = -1.0

    def _wire_steering(self):
        """steering_AUTO.rotateY = steering_CTRL.rotateZ × STEERING_RATIO.
        Front wheels only. Rim + tire spokes both live under
        steering_AUTO, so they turn together as one wheel."""
        if not self.is_front or not self.steering_drive_attr:
            return
        mul = cmds.createNode("multDoubleLinear",
                                n=f"{self.prefix}_steeringRatio_MUL")
        cmds.connectAttr(self.steering_drive_attr, f"{mul}.input1")
        cmds.setAttr(f"{mul}.input2", self.STEERING_RATIO)
        cmds.connectAttr(f"{mul}.output",
                          f"{self.steering_auto}.rotateY", f=True)

    # -----------------------------------------------------------------------
    # Coil-over spring
    # -----------------------------------------------------------------------

    @staticmethod
    def _make_helix_curve(name, height, radius, turns, color=None):
        """A degree-1 helix curve from y=0 down to y=-height. Parented
        under the spring joint it acts as a visible coil that bunches up
        when the joint scales down."""
        pts = []
        steps = int(turns * 8)
        for i in range(steps + 1):
            t = i / float(steps)
            ang = t * turns * 2.0 * math.pi
            pts.append((radius * math.cos(ang),
                        -t * height,
                        radius * math.sin(ang)))
        crv = cmds.curve(n=name, d=1, p=pts)
        if color is not None:
            for shp in cmds.listRelatives(crv, s=True) or []:
                cmds.setAttr(f"{shp}.overrideEnabled", 1)
                cmds.setAttr(f"{shp}.overrideColor", color)
        return crv

    def _build_suspension_spring(self):
        """A simple coil-over spring above the wheel.

        Top mount is fixed to the chassis (under suspension_OFFSET, which
        does NOT move with suspension travel). Bottom mount rides the
        wheel (under suspension_CTRL, which DOES). The spring joint sits
        at the top, aims down at the bottom mount, and scales along its
        length by the live top→bottom distance ratio — so a coil mesh
        (or the built-in helix indicator) compresses when the wheel
        pushes up and stretches when it drops.

        Test it: move {prefix}_suspension_CTRL.translateY up → the coil
        bunches up; down → it stretches.
        """
        x, y, z = self.position
        R = self.radius
        # Anchor the coil-over ABOVE the tyre so it sits in the wheel
        # arch instead of overlapping the wheel. All offsets are LOCAL,
        # measured up from the hub centre.
        #
        #   tyre top  = hub + R
        #   bot_off   = tyre top + a little (spring bottom clears the tyre)
        #   top_off   = the spring guide Y (where the strut meets the body)
        #               if provided, else a sensible default — but always
        #               floored above the bottom so the spring can never
        #               spawn inside / on the wheel (the rally-tyre bug).
        #
        # Because bot_off scales with R, this stays correct for any wheel
        # size — small road tyres or big rally tyres.
        tyre_top = R
        bot_off = tyre_top + 0.10 * R
        if self.spring_top_y is not None:
            top_off = self.spring_top_y - y
        else:
            top_off = bot_off + 1.0 * R
        min_top = bot_off + 0.5 * R
        if top_off < min_top:
            if self.spring_top_y is not None:
                cmds.warning(
                    f"[{self.prefix}] spring guide Y "
                    f"({self.spring_top_y:.0f}) is at/below the tyre top "
                    f"(~{y + R:.0f}) — raised the spring above the wheel. "
                    f"Move the {self.prefix} spring guide higher for a "
                    f"taller coil-over.")
            top_off = min_top
        if self.spring_top_pos is not None:
            stx, sty, stz = self.spring_top_pos
            bx, by, bz = x, y + bot_off, z
            rest_len = ((stx - bx) ** 2 + (sty - by) ** 2
                        + (stz - bz) ** 2) ** 0.5
        else:
            rest_len = top_off - bot_off

        # Top mount — child of suspension_OFFSET (chassis-fixed).
        top = cmds.group(em=True, n=f"{self.prefix}_springTop_GRP")
        cmds.parent(top, self.suspension_offset, relative=True)
        if self.spring_top_pos is not None:
            # Full guide position — strut can be offset / angled exactly
            # where the rigger placed the spring guide.
            stx, sty, stz = self.spring_top_pos
            cmds.setAttr(f"{top}.translate", stx - x, sty - y, stz - z)
        else:
            cmds.setAttr(f"{top}.translate", 0, top_off, 0)
        self.spring_top_grp = top

        # Bottom mount — child of suspension_CTRL (moves with travel).
        bot = cmds.group(em=True, n=f"{self.prefix}_springBottom_GRP")
        cmds.parent(bot, self.suspension_ctrl, relative=True)
        cmds.setAttr(f"{bot}.translate", 0, bot_off, 0)
        self.spring_bottom_grp = bot

        # Spring joint at the top, aiming -Y at the bottom mount.
        cmds.select(cl=True)
        spring = cmds.joint(n=f"{self.prefix}_spring_BIND_JNT")
        cmds.setAttr(f"{spring}.radius", 0.25 * SCALE)
        cmds.parent(spring, top, relative=True)
        cmds.aimConstraint(bot, spring, aim=(0, -1, 0), u=(0, 0, 1),
                            wut="objectrotation", wuo=top, wu=(0, 0, 1))
        self.spring_jnt = spring

        # Live distance → length ratio → scaleY (the length axis).
        dist = cmds.createNode("distanceBetween",
                                n=f"{self.prefix}_springDist_DB")
        cmds.connectAttr(f"{top}.worldMatrix[0]", f"{dist}.inMatrix1")
        cmds.connectAttr(f"{bot}.worldMatrix[0]", f"{dist}.inMatrix2")
        ratio = cmds.createNode("multiplyDivide",
                                 n=f"{self.prefix}_springRatio_MD")
        cmds.setAttr(f"{ratio}.operation", 2)  # divide
        cmds.connectAttr(f"{dist}.distance", f"{ratio}.input1X")
        cmds.setAttr(f"{ratio}.input2X", rest_len)
        cmds.connectAttr(f"{ratio}.outputX", f"{spring}.scaleY")

        # Visible helix coil — parented under the spring joint so it
        # inherits the scaleY compression. Purely a visual indicator;
        # an artist can skin a real coil mesh to {prefix}_spring_BIND_JNT.
        helix = self._make_helix_curve(
            f"{self.prefix}_springCoil",
            height=rest_len, radius=0.18 * R, turns=6,
            color=COLOR_SETTINGS)
        cmds.parent(helix, spring, relative=True)
        lock_hide_attrs(helix, ["tx", "ty", "tz", "rx", "ry", "rz",
                                 "sx", "sy", "sz", "v"])

    # -----------------------------------------------------------------------
    # Tire pressure deformation
    # -----------------------------------------------------------------------

    def _wire_tire_deformation(self):
        """Rolling-tire ground contact, per spoke = auto + manual ctrl.

        The wheel SPINS, so the deformation can't just translate the
        outer joint in local Y (its local axes rotate with the spin).
        Instead each spoke is compressed RADIALLY — pulled toward the hub
        along its own local radial direction (0, -cos a, -sin a) where
        a = the spoke's mounting angle.

            rest_world_Y = decomposeMatrix(outerOffset_GRP.worldMatrix).Y
            excess       = max(0, ground_Y - rest_world_Y)
            push         = excess × (1 - effectiveTirePressure) × tireStiffness
            outer.tY     = -cos(a) × push   (+ ctrl.tY)
            outer.tZ     = -sin(a) × push   (+ ctrl.tZ)
            outer.tX     =                      ctrl.tX

        Because the shrink is in LOCAL radial space, the wheel's spin
        rotation maps it to world-up automatically: whichever spoke is
        currently at the world bottom is the one whose rest tread point
        is below the ground, so IT compresses straight up — the flat
        spot stays planted while the tread rolls through it.

        outerOffset_GRP spins but is never deformed, so reading its
        world Y is feedback-free.
        """
        ctrl = self.wheel_ctrl
        pressure_factor_attr = None
        if self.ground_y_attr:
            rev = cmds.createNode(
                "reverse", n=f"{self.prefix}_pressureFactor_REV")
            cmds.connectAttr(f"{ctrl}.effectiveTirePressure",
                              f"{rev}.inputX")
            pressure_factor_attr = f"{rev}.outputX"

        # Deflation squat: lower the wheel centre by
        #   sag = R * tireSag * (1 - effectiveTirePressure)
        # so a flat tyre visibly sags + flattens on flat ground (and while
        # driving — the auto-suspension above pressureSag_GRP can't cancel
        # it). The lowered tread penetrates the ground and the per-spoke
        # deformation below flattens the contact patch. sag = 0 at full
        # pressure, so default-pressure rigs are unchanged.
        if pressure_factor_attr and self.pressure_sag_grp:
            sag_amt = cmds.createNode(
                "multDoubleLinear", n=f"{self.prefix}_tireSagAmt_MUL")
            cmds.connectAttr(pressure_factor_attr, f"{sag_amt}.input1")
            cmds.connectAttr(f"{ctrl}.tireSag", f"{sag_amt}.input2")
            sag_neg = cmds.createNode(
                "multDoubleLinear", n=f"{self.prefix}_tireSagDrop_MUL")
            cmds.connectAttr(f"{sag_amt}.output", f"{sag_neg}.input1")
            cmds.setAttr(f"{sag_neg}.input2", -self.radius)   # drop = -R*..
            cmds.connectAttr(f"{sag_neg}.output",
                              f"{self.pressure_sag_grp}.translateY", f=True)

        for i in range(self.spoke_count):
            nn = f"{i + 1:02d}"
            outer = self.outer_jnts[i]
            outer_off = self.outer_offset_grps[i]
            spoke_ctrl = self.spoke_ctrls[i]
            base = f"{self.prefix}_spoke_{nn}"

            deg = i * (360.0 / self.spoke_count)
            rad = math.radians(deg)
            neg_cos = -math.cos(rad)   # local radial-inward Y component
            neg_sin = -math.sin(rad)   # local radial-inward Z component

            push_y_attr = None   # -cos(a) * push
            push_z_attr = None   # -sin(a) * push
            if pressure_factor_attr:
                dm = cmds.createNode("decomposeMatrix",
                                      n=f"{base}_worldY_DM")
                cmds.connectAttr(f"{outer_off}.worldMatrix[0]",
                                  f"{dm}.inputMatrix")

                sub = cmds.createNode("plusMinusAverage",
                                       n=f"{base}_groundDelta_PMA")
                cmds.setAttr(f"{sub}.operation", 2)   # subtract
                cmds.connectAttr(self.ground_y_attr, f"{sub}.input1D[0]")
                cmds.connectAttr(f"{dm}.outputTranslateY",
                                  f"{sub}.input1D[1]")

                cond = cmds.createNode("condition",
                                        n=f"{base}_clamp_COND")
                cmds.setAttr(f"{cond}.operation", 2)  # >
                cmds.connectAttr(f"{sub}.output1D", f"{cond}.firstTerm")
                cmds.setAttr(f"{cond}.secondTerm", 0.0)
                cmds.connectAttr(f"{sub}.output1D",
                                  f"{cond}.colorIfTrueR")
                cmds.setAttr(f"{cond}.colorIfFalseR", 0.0)

                m1 = cmds.createNode("multDoubleLinear",
                                      n=f"{base}_x_pressure_MUL")
                cmds.connectAttr(f"{cond}.outColorR", f"{m1}.input1")
                cmds.connectAttr(pressure_factor_attr, f"{m1}.input2")

                m2 = cmds.createNode("multDoubleLinear",
                                      n=f"{base}_x_stiff_MUL")
                cmds.connectAttr(f"{m1}.output", f"{m2}.input1")
                cmds.connectAttr(f"{ctrl}.tireStiffness", f"{m2}.input2")
                push_attr = f"{m2}.output"

                # Resolve the radial push into local Y and Z components.
                my = cmds.createNode("multDoubleLinear",
                                      n=f"{base}_pushY_MUL")
                cmds.connectAttr(push_attr, f"{my}.input1")
                cmds.setAttr(f"{my}.input2", neg_cos)
                push_y_attr = f"{my}.output"

                mz = cmds.createNode("multDoubleLinear",
                                      n=f"{base}_pushZ_MUL")
                cmds.connectAttr(push_attr, f"{mz}.input1")
                cmds.setAttr(f"{mz}.input2", neg_sin)
                push_z_attr = f"{mz}.output"

            # Combine push + manual ctrl into the outer joint's translate.
            self._combine_into_translate(
                outer, base, push_y_attr, push_z_attr, spoke_ctrl)

    @staticmethod
    def _combine_into_translate(outer, base, push_y_attr, push_z_attr,
                                 spoke_ctrl):
        """Drive outer.translate from ground push (Y,Z) + ctrl delta."""
        def drive(axis, push_attr, ctrl_attr):
            if push_attr and ctrl_attr:
                adl = cmds.createNode(
                    "addDoubleLinear",
                    n=f"{base}_{axis}Total_ADL")
                cmds.connectAttr(push_attr, f"{adl}.input1")
                cmds.connectAttr(ctrl_attr, f"{adl}.input2")
                cmds.connectAttr(f"{adl}.output",
                                  f"{outer}.translate{axis}", f=True)
            elif push_attr:
                cmds.connectAttr(push_attr,
                                  f"{outer}.translate{axis}", f=True)
            elif ctrl_attr:
                cmds.connectAttr(ctrl_attr,
                                  f"{outer}.translate{axis}", f=True)

        cy = f"{spoke_ctrl}.translateY" if spoke_ctrl else None
        cz = f"{spoke_ctrl}.translateZ" if spoke_ctrl else None
        cx = f"{spoke_ctrl}.translateX" if spoke_ctrl else None
        drive("Y", push_y_attr, cy)
        drive("Z", push_z_attr, cz)
        drive("X", None, cx)


# =============================================================================
# VehicleDoor / VehicleHood — single-hinge FK ctrls
# =============================================================================

class VehicleHinge(object):
    """A generic hinge-joint module — used for doors and the hood.

    Build args:
        name:        the joint / ctrl base name (e.g. "LF_door", "hood").
        hinge_pos:   world position of the hinge (where the rotation pivots).
        open_axis:   "rotateX" / "rotateY" / "rotateZ".
        open_angle:  degrees the door rotates at openAmount=1
                     (positive or negative).
    """

    def __init__(self, name, hinge_pos, open_axis, open_angle,
                 chassis_ctrl, parent_jnt,
                 ctrl_grp, jnt_grp, color=COLOR_BEND):
        self.name = name
        self.hinge_pos = hinge_pos
        self.open_axis = open_axis
        self.open_angle = open_angle
        self.chassis_ctrl = chassis_ctrl
        self.parent_jnt = parent_jnt
        self.ctrl_grp = ctrl_grp
        self.jnt_grp = jnt_grp
        self.color = color

        self.hinge_jnt = None
        self.ctrl = None

    def build(self):
        # Hinge joint at the hinge position.
        cmds.select(cl=True)
        self.hinge_jnt = cmds.joint(n=f"{self.name}_BIND_JNT",
                                      p=self.hinge_pos)
        cmds.setAttr(f"{self.hinge_jnt}.jointOrient", 0, 0, 0)
        cmds.setAttr(f"{self.hinge_jnt}.radius", 0.4 * SCALE)
        cmds.parent(self.hinge_jnt, self.parent_jnt)

        # Small diamond ctrl at the hinge.
        self.ctrl = create_diamond_ctrl(
            f"{self.name}_CTRL", size=0.5 * SCALE, color=self.color,
        )
        cmds.xform(self.ctrl, ws=True, t=self.hinge_pos)
        offset = make_offset_group(self.ctrl)
        cmds.parent(offset, self.chassis_ctrl)
        cmds.parentConstraint(self.ctrl, self.hinge_jnt, mo=True)
        lock_hide_attrs(self.ctrl,
                         ["tx", "ty", "tz", "sx", "sy", "sz"])

        # openAmount attr — 0 = closed, 1 = fully open.
        cmds.addAttr(self.ctrl, ln="openAmount", at="double",
                      min=0, max=1, dv=0, k=True)
        # Drive the ctrl's own open_axis from openAmount so the hinge
        # joint follows via the parentConstraint.
        mul = cmds.createNode("multDoubleLinear",
                                n=f"{self.name}_openAngle_MUL")
        cmds.connectAttr(f"{self.ctrl}.openAmount", f"{mul}.input1")
        cmds.setAttr(f"{mul}.input2", self.open_angle)
        cmds.connectAttr(f"{mul}.output",
                          f"{self.ctrl}.{self.open_axis}", f=True)


# =============================================================================
# VehicleRig orchestrator
# =============================================================================

class VehicleRig(object):
    """Builds the full vehicle.

    Modules:
        chassis, wheels (4), steering, suspension, doors (4), hood.
        tirePressure is integrated into wheels.

    Top group: VEHICLE_RIG_GRP

    DEFAULT_POSITIONS units = Maya cm. Vehicle sits with wheels on Y=0
    ground plane, body forward = +Z, left side = +X.
    """

    TOP_GROUP = "VEHICLE_RIG_GRP"

    DEFAULT_POSITIONS = {
        "chassis":      (0, 80, 0),
        "wheelRadius":  40.0,
        # Hub centres (Y = wheelRadius so wheels touch the ground).
        "LF_wheel":     ( 80, 40,  130),
        "RF_wheel":     (-80, 40,  130),
        "LB_wheel":     ( 80, 40, -130),
        "RB_wheel":     (-80, 40, -130),
        # Spring top world-Y per wheel (height of the coil-over). Sits
        # above the tyre top (hub Y 40 + radius 40 = 80).
        "LF_springTopY": 115.0,
        "RF_springTopY": 115.0,
        "LB_springTopY": 115.0,
        "RB_springTopY": 115.0,
        # Door hinges (front edge of each door — opens outward).
        "LF_doorHinge": ( 90, 90,  60),
        "RF_doorHinge": (-90, 90,  60),
        "LB_doorHinge": ( 90, 90,   0),
        "RB_doorHinge": (-90, 90,   0),
        # Hood hinge (front, opens up + forward).
        "hoodHinge":    (  0, 110, 80),
        # Trunk hinge (back, opens up + backward).
        "trunkHinge":   (  0, 110, -80),
        # Steering wheel position (driver-side, biased to left for LHD).
        "steeringWheel": ( 50, 110, 50),
    }

    DEFAULT_MODULES = {"chassis", "wheels", "steering",
                       "suspension", "doors", "hood", "trunk"}

    def __init__(self, positions=None, modules=None,
                 spoke_count=16, build_spoke_ctrls=True,
                 ground_mesh=None):
        import copy
        self.positions = positions or copy.deepcopy(self.DEFAULT_POSITIONS)
        self.modules = set(modules) if modules else set(self.DEFAULT_MODULES)
        # Wheels are mandatory because everything else hangs off them or
        # the chassis they share.
        self.modules.add("chassis")
        self.spoke_count = spoke_count
        self.build_spoke_ctrls = build_spoke_ctrls
        # Optional ground MESH (string name). If given, build() wires
        # per-wheel closestPointOnMesh sampling so tires follow uneven
        # terrain. If None, tires use the flat C_ground_LOC instead.
        self.ground_mesh = ground_mesh

        # Outputs
        self.core = None
        self.chassis_ctrl = None
        self.chassis_jnt = None
        self.body_auto = None      # body shell roll/pitch/bob group
        self.body_osc = None       # drive-loop spring-damper offset group
        self.steering_ctrl = None
        self.wheels = {}          # prefix → VehicleWheel
        self.doors  = {}          # prefix → VehicleHinge
        self.hood = None
        self.trunk = None

    # -----------------------------------------------------------------------

    def build(self):
        if cmds.objExists(self.TOP_GROUP):
            cmds.error(f"{self.TOP_GROUP} already exists. Delete it first.")
            return
        print(f"[VehicleRig] Building modules: {sorted(self.modules)}")

        # 1. Core
        self.core = CoreRig(positions={"cog": self.positions["chassis"]})
        self.core.build()
        self.core.main_grp = cmds.rename("CHARACTER_RIG_GRP", self.TOP_GROUP)
        cg, jg, mg = (self.core.ctrl_grp, self.core.jnt_grp,
                       self.core.misc_grp)

        # 2. Chassis (always)
        self._build_chassis(cg, jg, mg)

        # 2.5 Ground reference locator — sits at world Y=0 by default,
        # acts as the "ground plane" the tires deform against. Parent
        # it to a terrain mesh later for non-flat ground.
        ground_y_attr = self._build_ground_reference()

        # 3. Steering wheel — needs to exist before the wheels so they
        #    can wire to its rotateZ.
        steering_attr = None
        if "steering" in self.modules:
            self._build_steering(cg)
            steering_attr = f"{self.steering_ctrl}.rotateZ"

        # 4. Wheels — auto-spin off (chassis translateZ + odometer),
        #    steering off the steering ctrl, tire pressure per wheel, and
        #    world-aware contact deformation against the ground.
        if "wheels" in self.modules:
            chassis_drive = self._build_spin_driver()
            self._build_wheels(cg, jg, mg,
                                chassis_drive_attr=chassis_drive,
                                steering_drive_attr=steering_attr,
                                ground_y_attr=ground_y_attr)
            # Wire the chassis-level master tirePressure broadcast.
            self._wire_master_tire_pressure()
            # Auto-suspension: each wheel rides the ground on its own.
            if "suspension" in self.modules:
                self._wire_auto_suspension(ground_y_attr)

        # 5. Doors
        if "doors" in self.modules:
            self._build_doors(cg, jg)

        # 6. Hood
        if "hood" in self.modules:
            self._build_hood(cg, jg)

        # 7. Trunk
        if "trunk" in self.modules:
            self._build_trunk(cg, jg)

        # 8. Body reaction — roll / pitch / bob from the wheel travels.
        if ("wheels" in self.modules and "suspension" in self.modules
                and self.body_auto):
            self._wire_body_motion()

        # Lock organisational groups
        for grp in (self.core.main_grp, self.core.ctrl_grp,
                     self.core.jnt_grp):
            lock_hide_attrs(grp, ["tx", "ty", "tz",
                                   "rx", "ry", "rz",
                                   "sx", "sy", "sz"])

        # Hide guide locators if any vehicle guide group is present.
        for guide_grp in ("VEHICLE_RIG_GUIDES_GRP", "RIG_GUIDES_GRP"):
            if cmds.objExists(guide_grp):
                try:
                    cmds.setAttr(f"{guide_grp}.visibility", 0)
                except Exception:
                    pass

        # Per-wheel terrain sampling if a ground mesh was provided.
        if self.ground_mesh and cmds.objExists(self.ground_mesh):
            assign_ground_mesh(self.ground_mesh,
                                spoke_count=self.spoke_count)

        cmds.select(cl=True)
        print(f"[VehicleRig] Done. Top node: {self.TOP_GROUP}")

    def delete(self):
        if cmds.objExists(self.TOP_GROUP):
            cmds.delete(self.TOP_GROUP)

    def assign_ground_mesh(self, mesh):
        """Wire per-wheel closestPointOnMesh terrain sampling so every
        tire follows the surface under it. See module-level
        assign_ground_mesh() for details."""
        return assign_ground_mesh(mesh, spoke_count=self.spoke_count)

    def clear_ground_mesh(self):
        """Revert all wheels to the flat C_ground_LOC."""
        return clear_ground_mesh(spoke_count=self.spoke_count)

    # -----------------------------------------------------------------------
    # Sub-builders
    # -----------------------------------------------------------------------

    def _build_chassis(self, cg, jg, mg):
        # Body ctrl — square outline above the body. Animator translates +
        # rotates this to drive the vehicle.
        chassis_pos = self.positions["chassis"]
        self.chassis_ctrl = create_square_ctrl(
            "C_chassis_CTRL", size=3.0 * SCALE,
            normal=(0, 1, 0), color=COLOR_CENTER,
        )
        cmds.xform(self.chassis_ctrl, ws=True, t=chassis_pos)
        offset = make_offset_group(self.chassis_ctrl)
        cmds.parent(offset, self.core.cog_ctrl)
        lock_hide_attrs(self.chassis_ctrl, ["sx", "sy", "sz", "v"])

        # ---- Body AUTO ----
        # The BODY SHELL (chassis BIND joint, doors, hood, trunk, steering)
        # parents under this group, NOT directly under the chassis ctrl.
        # The wheels parent under the chassis ctrl OUTSIDE this group, so
        # the body can ROLL / PITCH / BOB from the wheel heights while the
        # wheels stay planted on the terrain — that's the secondary motion
        # that makes an off-road shot look alive. The pivot is at the
        # chassis centre so the body tilts about its own middle.
        # C_body_OSC sits ABOVE C_body_AUTO. The node network drives
        # body_AUTO to the INSTANT terrain tilt; the live-drive tool
        # keyframes body_OSC with the spring-damper LAG offset, so the
        # body overshoots + settles like real mass on its suspension.
        # When not driving, body_OSC stays at zero, so manual scrubbing
        # still shows the clean instant tilt.
        self.body_osc = cmds.group(em=True, n="C_body_OSC")
        cmds.matchTransform(self.body_osc, self.chassis_ctrl)
        cmds.parent(self.body_osc, self.chassis_ctrl)

        self.body_auto = cmds.group(em=True, n="C_body_AUTO")
        cmds.matchTransform(self.body_auto, self.body_osc)
        cmds.parent(self.body_auto, self.body_osc)

        # Body BIND joint at chassis position — rides the body AUTO so the
        # skinned body mesh tilts with the shell.
        cmds.select(cl=True)
        self.chassis_jnt = cmds.joint(n="C_chassis_BIND_JNT",
                                        p=chassis_pos)
        cmds.setAttr(f"{self.chassis_jnt}.jointOrient", 0, 0, 0)
        cmds.setAttr(f"{self.chassis_jnt}.radius", 0.8 * SCALE)
        cmds.parent(self.chassis_jnt, self.core.root_bind_jnt)
        cmds.parentConstraint(self.body_auto, self.chassis_jnt,
                                mo=True)

        # Master tire pressure on the chassis — broadcasts to all wheels.
        cmds.addAttr(self.chassis_ctrl, ln="vehicleHeader", at="enum",
                      en="---VEHICLE---:", k=True)
        cmds.setAttr(f"{self.chassis_ctrl}.vehicleHeader",
                      l=True, cb=True, k=False)
        cmds.addAttr(self.chassis_ctrl, ln="allTirePressure",
                      at="double", min=0, max=1, dv=1, k=True)
        # Odometer — total distance driven. Drives wheel spin so the
        # wheels roll by how far the car has TRAVELLED (not its world Z).
        # The live-drive tool accumulates this; manual users can ignore
        # it (translateZ still spins the wheels for straight-line tests).
        cmds.addAttr(self.chassis_ctrl, ln="odometer", at="double",
                      dv=0, k=True)
        # Auto-suspension — each wheel rides the terrain on its own. When
        # ON (default), wheels lift over bumps + drop into dips and the
        # springs pump; the animator's manual suspension_CTRL still layers
        # on top. Travel is clamped to maxCompression (up) / maxDroop
        # (down) so big bumps don't fling the wheel through the body.
        cmds.addAttr(self.chassis_ctrl, ln="suspensionHeader", at="enum",
                      en="--SUSPENSION--:", k=True)
        cmds.setAttr(f"{self.chassis_ctrl}.suspensionHeader",
                      l=True, cb=True, k=False)
        cmds.addAttr(self.chassis_ctrl, ln="autoSuspension", at="double",
                      min=0, max=1, dv=1, k=True)
        radius = self.positions.get("wheelRadius", 40.0)
        cmds.addAttr(self.chassis_ctrl, ln="maxCompression", at="double",
                      min=0, dv=0.5 * radius, k=True)
        cmds.addAttr(self.chassis_ctrl, ln="maxDroop", at="double",
                      min=0, dv=0.5 * radius, k=True)
        # Body reaction — the shell rolls / pitches / bobs from the four
        # wheel heights so an off-road shot looks alive (the body leans
        # into a dip, pitches over a crest, bobs with the terrain) while
        # the wheels stay planted. autoBodyMotion = master on/off; the
        # amounts scale each component (set to taste, 0 = off).
        cmds.addAttr(self.chassis_ctrl, ln="bodyHeader", at="enum",
                      en="----BODY----:", k=True)
        cmds.setAttr(f"{self.chassis_ctrl}.bodyHeader",
                      l=True, cb=True, k=False)
        cmds.addAttr(self.chassis_ctrl, ln="autoBodyMotion", at="double",
                      min=0, max=1, dv=1, k=True)
        # Gain per axis. NO min clamp on purpose — a negative value flips
        # that axis, so animators can invert roll / pitch / bob to taste
        # (e.g. exaggerate a forward brake-dive, or reverse a lean).
        cmds.addAttr(self.chassis_ctrl, ln="bodyRoll", at="double",
                      dv=0.6, k=True)
        cmds.addAttr(self.chassis_ctrl, ln="bodyPitch", at="double",
                      dv=0.5, k=True)
        cmds.addAttr(self.chassis_ctrl, ln="bodyBob", at="double",
                      dv=0.5, k=True)

    def _build_spin_driver(self):
        """Return an attr = (chassis.translateZ + chassis.odometer) that
        drives every wheel's auto-spin. translateZ keeps simple straight-
        line testing working; odometer lets the drive tool spin the
        wheels while the car curves freely around the world."""
        adl = cmds.createNode("addDoubleLinear",
                               n="C_chassis_spinDriver_ADL")
        cmds.connectAttr(f"{self.chassis_ctrl}.translateZ",
                          f"{adl}.input1")
        cmds.connectAttr(f"{self.chassis_ctrl}.odometer",
                          f"{adl}.input2")
        return f"{adl}.output"

    def _build_steering(self, cg):
        pos = self.positions["steeringWheel"]
        self.steering_ctrl = create_circle_ctrl(
            "C_steering_CTRL", radius=0.8 * SCALE,
            normal=(0, 0, 1), color=COLOR_IK,
        )
        cmds.xform(self.steering_ctrl, ws=True, t=pos)
        offset = make_offset_group(self.steering_ctrl)
        # Steering rides the body shell so it tilts with the cabin.
        cmds.parent(offset, self.body_auto or self.chassis_ctrl)
        # Steering wheel only spins around the dash Z axis. Lock the rest.
        lock_hide_attrs(self.steering_ctrl,
                         ["tx", "ty", "tz", "rx", "ry",
                          "sx", "sy", "sz"])

    def _build_ground_reference(self):
        """Locator at world Y=0 by default. Tires deform against
        whichever Y this sits at. Lives at the top of VEHICLE_RIG_GRP
        (NOT under cog) so moving the chassis doesn't move the ground.
        Returns the world-Y attr to feed into VehicleWheel.
        """
        loc = cmds.spaceLocator(n=GROUND_LOC_NAME)[0]
        cmds.parent(loc, self.core.main_grp)
        cmds.xform(loc, ws=True, t=(0, 0, 0))
        for ax in ("X", "Y", "Z"):
            cmds.setAttr(f"{loc}Shape.localScale{ax}", 30.0)
        cmds.setAttr(f"{loc}Shape.overrideEnabled", 1)
        cmds.setAttr(f"{loc}Shape.overrideColor", 17)   # yellow
        # decomposeMatrix lets the ground locator be parented to a
        # terrain mesh later without rewiring — the world Y still
        # flows through correctly.
        dm = cmds.createNode("decomposeMatrix",
                                n=f"{GROUND_LOC_NAME}_decompose")
        cmds.connectAttr(f"{loc}.worldMatrix[0]", f"{dm}.inputMatrix")
        return f"{dm}.outputTranslateY"

    # Footprint sample offsets along the wheel's travel (local Z), as a
    # fraction of the tyre radius. front / center / back. The non-zero
    # offsets let the LEADING edge of the tyre touch a bump before the hub
    # reaches it, so the wheel climbs a curb gradually (over ~footprint
    # length) instead of snapping straight up.
    FOOT_OFFSETS = (("Front", 0.7), ("Center", 0.0), ("Back", -0.7))

    def _wire_auto_suspension(self, ground_y_attr):
        """Drive each wheel's suspension_AUTO.translateY from a 3-point
        CONTACT-PATCH sample, so the wheel rolls over bumps on a smooth
        circular arc instead of teleporting to the ground height.

        A tyre of radius R resting on terrain has its CENTRE height, at a
        footprint point that is dx ahead/behind the hub, equal to:

            centre_lift(dx) = terrain_Y(dx) + sqrt(R^2 - dx^2)

        The wheel rests on the HIGHEST of the footprint points, so:

            travel_raw = max over {front, centre, back} of centre_lift(dx)
                         - R                       (R = rest on flat ground)
            travel     = clamp(travel_raw, -maxDroop, +maxCompression)
            AUTO.tY    = travel * autoSuspension

        The sqrt(R^2 - dx^2) term is the geometry of the round tyre: the
        front sample only lifts the wheel once a bump is tall enough /
        close enough to actually touch the circle there, and the lift
        ramps in smoothly. On flat terrain the centre point wins (its
        sqrt term = R, the offsets' terms are smaller), so travel = 0.

        Each footprint point has a stable locator group whose world
        position feeds a per-point closestPointOnMesh (wired by
        assign_ground_mesh). Until a mesh is assigned all points read the
        flat C_ground_LOC.
        """
        chassis = self.chassis_ctrl
        import math
        for prefix, wheel in self.wheels.items():
            auto = wheel.suspension_auto
            offset_grp = wheel.suspension_offset
            R = wheel.radius
            if not auto:
                continue

            contrib_attrs = []
            for tag, frac in self.FOOT_OFFSETS:
                dx = frac * R
                geo = math.sqrt(max(0.0, R * R - dx * dx))   # sqrt(R^2-dx^2)

                # Footprint locator group — stable point at the wheel,
                # offset fore/aft. Its worldMatrix feeds the CPOM later.
                foot = cmds.group(
                    em=True, n=f"{prefix}_foot{tag}_GRP")
                cmds.parent(foot, offset_grp, relative=True)
                cmds.setAttr(f"{foot}.translate", 0, 0, dx)
                for a in ("tx", "ty", "tz", "rx", "ry", "rz",
                           "sx", "sy", "sz"):
                    cmds.setAttr(f"{foot}.{a}", l=True, k=False, cb=False)

                # Ground-Y source for this point (passthrough the terrain
                # sampler can override) PLUS the circular geometry term.
                #   contribution = terrain_Y + sqrt(R^2 - dx^2)
                src = cmds.createNode(
                    "addDoubleLinear",
                    n=f"{prefix}_foot{tag}Src_ADL")
                cmds.connectAttr(ground_y_attr, f"{src}.input1")
                cmds.setAttr(f"{src}.input2", geo)
                contrib_attrs.append(f"{src}.output")

            # max(front, centre, back) via two condition nodes.
            def _max_node(a, b, name):
                c = cmds.createNode("condition", n=name)
                cmds.setAttr(f"{c}.operation", 2)   # greater than
                cmds.connectAttr(a, f"{c}.firstTerm")
                cmds.connectAttr(b, f"{c}.secondTerm")
                cmds.connectAttr(a, f"{c}.colorIfTrueR")
                cmds.connectAttr(b, f"{c}.colorIfFalseR")
                return f"{c}.outColorR"

            m1 = _max_node(contrib_attrs[0], contrib_attrs[1],
                           f"{prefix}_suspMax1_COND")
            m2 = _max_node(m1, contrib_attrs[2],
                           f"{prefix}_suspMax2_COND")

            # travel_raw = max_contribution - R
            sub = cmds.createNode(
                "plusMinusAverage", n=f"{prefix}_suspTravel_PMA")
            cmds.setAttr(f"{sub}.operation", 2)   # subtract
            cmds.connectAttr(m2, f"{sub}.input1D[0]")
            cmds.setAttr(f"{sub}.input1D[1]", R)

            # clamp to [-maxDroop, +maxCompression]
            clamp = cmds.createNode("clamp", n=f"{prefix}_suspClamp_CL")
            cmds.connectAttr(f"{sub}.output1D", f"{clamp}.inputR")
            cmds.connectAttr(f"{chassis}.maxCompression", f"{clamp}.maxR")
            neg = cmds.createNode("multDoubleLinear",
                                   n=f"{prefix}_suspNegDroop_MDL")
            cmds.connectAttr(f"{chassis}.maxDroop", f"{neg}.input1")
            cmds.setAttr(f"{neg}.input2", -1.0)
            cmds.connectAttr(f"{neg}.output", f"{clamp}.minR")

            # * autoSuspension master toggle
            gate = cmds.createNode("multDoubleLinear",
                                    n=f"{prefix}_suspGate_MDL")
            cmds.connectAttr(f"{clamp}.outputR", f"{gate}.input1")
            cmds.connectAttr(f"{chassis}.autoSuspension", f"{gate}.input2")
            cmds.connectAttr(f"{gate}.output",
                              f"{auto}.translateY", f=True)

    def _wire_body_motion(self):
        """Drive C_body_AUTO roll / pitch / bob from the four wheels'
        suspension travel, so the body shell reacts to the terrain while
        the wheels stay planted (the off-road secondary motion).

            travel_W = W_suspension_AUTO.translateY   (+ up over a bump)

            roll  (rotateZ) = (avg(L) - avg(R)) * bodyRoll  * autoBodyMotion
            pitch (rotateX) = (avg(B) - avg(F)) * bodyPitch * autoBodyMotion
            bob   (translateY) = avg(all 4)     * bodyBob   * autoBodyMotion

        Roll: left wheels higher than right -> body leans to the right
        (rotateZ negative side drops) — reads as the body settling into
        the low side. Pitch: front wheels higher (driving uphill) -> nose
        pitches UP (back-minus-front, because +rotateX tips the front
        down). Bob: all four up -> body lifts with the terrain.
        Any gain can be set negative to invert that axis.

        Lifting a wheel raises that corner, so the body tilts AWAY from the
        lift toward the low corner — exactly how a real chassis settles.
        """
        chassis = self.chassis_ctrl
        body = self.body_auto
        travel = {}
        for p in ("LF", "RF", "LB", "RB"):
            w = self.wheels.get(p)
            if not w or not w.suspension_auto:
                return   # need all four to compute the averages
            travel[p] = f"{w.suspension_auto}.translateY"

        def avg2(a, b, name):
            n = cmds.createNode("plusMinusAverage", n=name)
            cmds.setAttr(f"{n}.operation", 3)   # average
            cmds.connectAttr(travel[a], f"{n}.input1D[0]")
            cmds.connectAttr(travel[b], f"{n}.input1D[1]")
            return f"{n}.output1D"

        def avg4(name):
            n = cmds.createNode("plusMinusAverage", n=name)
            cmds.setAttr(f"{n}.operation", 3)   # average
            for i, p in enumerate(("LF", "RF", "LB", "RB")):
                cmds.connectAttr(travel[p], f"{n}.input1D[{i}]")
            return f"{n}.output1D"

        def diff(a_attr, b_attr, name):
            n = cmds.createNode("plusMinusAverage", n=name)
            cmds.setAttr(f"{n}.operation", 2)   # subtract
            cmds.connectAttr(a_attr, f"{n}.input1D[0]")
            cmds.connectAttr(b_attr, f"{n}.input1D[1]")
            return f"{n}.output1D"

        def scale_gate(src, amount_attr, name):
            # src * bodyXxx * autoBodyMotion
            m1 = cmds.createNode("multDoubleLinear", n=f"{name}_AMT")
            cmds.connectAttr(src, f"{m1}.input1")
            cmds.connectAttr(f"{chassis}.{amount_attr}", f"{m1}.input2")
            m2 = cmds.createNode("multDoubleLinear", n=f"{name}_GATE")
            cmds.connectAttr(f"{m1}.output", f"{m2}.input1")
            cmds.connectAttr(f"{chassis}.autoBodyMotion", f"{m2}.input2")
            return f"{m2}.output"

        # ---- roll: (avg L - avg R) -> rotateZ ----
        avgL = avg2("LF", "LB", "C_body_avgL_PMA")
        avgR = avg2("RF", "RB", "C_body_avgR_PMA")
        roll_src = diff(avgL, avgR, "C_body_rollDelta_PMA")
        cmds.connectAttr(scale_gate(roll_src, "bodyRoll",
                                    "C_body_roll"),
                          f"{body}.rotateZ", f=True)

        # ---- pitch: (avg B - avg F) -> rotateX ----
        # NOTE ordering: in Maya +rotateX tips the +Z (front) DOWN, so to
        # make an uphill grade (front wheels riding higher) read as a
        # nose-UP body we drive rotateX from (back - front). Front high ->
        # avgB < avgF -> negative rotateX -> nose lifts. (avgF - avgB here
        # would dive the nose forward going uphill — the old bug.)
        avgF = avg2("LF", "RF", "C_body_avgF_PMA")
        avgB = avg2("LB", "RB", "C_body_avgB_PMA")
        pitch_src = diff(avgB, avgF, "C_body_pitchDelta_PMA")
        cmds.connectAttr(scale_gate(pitch_src, "bodyPitch",
                                    "C_body_pitch"),
                          f"{body}.rotateX", f=True)

        # ---- bob: avg all 4 -> translateY ----
        bob_src = avg4("C_body_avgAll_PMA")
        cmds.connectAttr(scale_gate(bob_src, "bodyBob", "C_body_bob"),
                          f"{body}.translateY", f=True)

    def _build_wheels(self, cg, jg, mg,
                       chassis_drive_attr, steering_drive_attr,
                       ground_y_attr=None):
        default_radius = self.positions.get("wheelRadius", 40.0)
        layout = [
            ("LF", self.positions["LF_wheel"], True,  True),
            ("RF", self.positions["RF_wheel"], True,  False),
            ("LB", self.positions["LB_wheel"], False, True),
            ("RB", self.positions["RB_wheel"], False, False),
        ]
        for prefix, pos, is_front, is_left in layout:
            # Per-wheel radius. If the positions dict carries an explicit
            # "{prefix}_wheelRadius" use it; otherwise derive it from the
            # hub centre's height above the ground (Y), which is what a
            # rim-centre guide naturally gives. Falls back to the global
            # default if the centre is at/below the ground.
            radius = self.positions.get(f"{prefix}_wheelRadius")
            if radius is None:
                radius = pos[1] if pos[1] > 1.0 else default_radius
            # Spring top world-Y (height of the coil-over). Guide-driven.
            spring_top_y = self.positions.get(f"{prefix}_springTopY")
            spring_top_pos = self.positions.get(f"{prefix}_springTop")
            wheel = VehicleWheel(
                prefix=prefix,
                position=pos,
                radius=radius,
                is_front=is_front,
                is_left=is_left,
                chassis_ctrl=self.chassis_ctrl,
                parent_jnt=self.chassis_jnt,
                ctrl_grp=cg, jnt_grp=jg, misc_grp=mg,
                chassis_drive_attr=chassis_drive_attr,
                steering_drive_attr=steering_drive_attr,
                ground_y_attr=ground_y_attr,
                spoke_count=self.spoke_count,
                build_spoke_ctrls=self.build_spoke_ctrls,
                build_spring=("suspension" in self.modules),
                spring_top_y=spring_top_y,
                spring_top_pos=spring_top_pos,
            )
            wheel.build()
            self.wheels[prefix] = wheel

    def _wire_master_tire_pressure(self):
        """Wire chassis.allTirePressure → wheel.effectiveTirePressure
        via a blend that respects wheel.autoFromMaster.

            blender = 0 (autoFromMaster OFF) → output = input[0]
                                              = wheel.tirePressure
            blender = 1 (autoFromMaster ON)  → output = input[1]
                                              = chassis.allTirePressure

        Result: when ON the master broadcasts; when OFF the per-wheel
        tirePressure keyframes drive the tire shape directly. The
        animator-facing tirePressure attr is never overwritten by an
        incoming connection — they can keyframe it any time.
        """
        master = f"{self.chassis_ctrl}.allTirePressure"
        for wheel in self.wheels.values():
            ctrl = wheel.wheel_ctrl
            blend = cmds.createNode(
                "blendTwoAttr",
                n=f"{wheel.prefix}_pressureSrc_BLEND",
            )
            cmds.connectAttr(f"{ctrl}.tirePressure", f"{blend}.input[0]")
            cmds.connectAttr(master,                  f"{blend}.input[1]")
            cmds.connectAttr(f"{ctrl}.autoFromMaster",
                              f"{blend}.attributesBlender")
            cmds.connectAttr(f"{blend}.output",
                              f"{ctrl}.effectiveTirePressure", f=True)

    def _build_doors(self, cg, jg):
        # 4 doors, hinge at the front edge of each door so it swings open
        # outward and forward when the openAmount goes up.
        door_specs = [
            # (name, hinge_pos_key, open_axis, open_angle, color)
            ("LF_door", "LF_doorHinge", "rotateY",  60.0, COLOR_LEFT),
            ("RF_door", "RF_doorHinge", "rotateY", -60.0, COLOR_RIGHT),
            ("LB_door", "LB_doorHinge", "rotateY",  60.0, COLOR_LEFT),
            ("RB_door", "RB_doorHinge", "rotateY", -60.0, COLOR_RIGHT),
        ]
        for name, pos_key, axis, angle, color in door_specs:
            hinge = VehicleHinge(
                name=name,
                hinge_pos=self.positions[pos_key],
                open_axis=axis,
                open_angle=angle,
                # Doors ride the body shell so they tilt with the cabin.
                chassis_ctrl=self.body_auto or self.chassis_ctrl,
                parent_jnt=self.chassis_jnt,
                ctrl_grp=cg, jnt_grp=jg,
                color=color,
            )
            hinge.build()
            self.doors[name] = hinge

    def _build_hood(self, cg, jg):
        # Hood hinges at the back edge, opens UP (rotateX positive →
        # front edge lifts toward sky).
        self.hood = VehicleHinge(
            name="hood",
            hinge_pos=self.positions["hoodHinge"],
            open_axis="rotateX",
            open_angle=-70.0,   # negative because forward = +Z, so the
                                # front-of-hood lifts up = -X rotation
                                # in our world (Y up, +Z forward).
            chassis_ctrl=self.body_auto or self.chassis_ctrl,
            parent_jnt=self.chassis_jnt,
            ctrl_grp=cg, jnt_grp=jg,
            color=COLOR_CENTER,
        )
        self.hood.build()

    def _build_trunk(self, cg, jg):
        # Trunk hinges at the front edge of the trunk lid (toward the
        # cabin) and opens UP + backward. With forward = +Z, the back of
        # the lid lifting up is a +X rotation.
        self.trunk = VehicleHinge(
            name="trunk",
            hinge_pos=self.positions["trunkHinge"],
            open_axis="rotateX",
            open_angle=70.0,
            chassis_ctrl=self.body_auto or self.chassis_ctrl,
            parent_jnt=self.chassis_jnt,
            ctrl_grp=cg, jnt_grp=jg,
            color=COLOR_CENTER,
        )
        self.trunk.build()


# =============================================================================
# Per-wheel terrain ground sampling
# =============================================================================

def _connect_if_needed(src, dst):
    """connectAttr only if dst isn't already driven by src — avoids the
    'already connected' warning spam on re-assign."""
    cur = cmds.listConnections(dst, s=True, d=False, plugs=True) or []
    if src in cur:
        return
    for c in cur:
        cmds.disconnectAttr(c, dst)
    cmds.connectAttr(src, dst, f=True)


def _resolve_mesh(mesh):
    """Return (mesh_shape, mesh_transform) for a mesh name, or (None, None)."""
    if not cmds.objExists(mesh):
        cmds.warning(f"[vehicle] Ground mesh '{mesh}' does not exist.")
        return None, None
    if cmds.objectType(mesh) == "mesh":
        xform = (cmds.listRelatives(mesh, p=True) or [None])[0]
        return mesh, xform
    shapes = cmds.listRelatives(mesh, s=True, ni=True, type="mesh") or []
    if not shapes:
        cmds.warning(f"[vehicle] '{mesh}' has no mesh shape — pick a "
                     f"polygon mesh transform.")
        return None, None
    return shapes[0], mesh


def assign_ground_mesh(mesh, wheel_prefixes=("LF", "RF", "LB", "RB"),
                       spoke_count=16):
    """Wire per-wheel (actually per-SPOKE) terrain sampling.

    For each spoke, a closestPointOnMesh node queries the ground mesh at
    the spoke's own world position and returns the surface Y there. That
    Y replaces the flat C_ground_LOC value as the spoke's ground
    reference, so each tire — and each spoke within a tire — flattens
    against the actual surface beneath it. Park the car on a slope and
    the front + back tires deform differently; drive across hills and
    each tire follows its own contact patch.

    Correct closestPointOnMesh wiring (verified):
        inMesh      <- groundShape.outMesh        (LOCAL mesh)
        inputMatrix <- groundTransform.worldMatrix (to world)
        inPosition  <- spoke world position
        positionY    = surface Y under the spoke

    Reading the spoke position from the static outerOffset_GRP (never
    deformed) keeps it feedback-free. Idempotent — re-running re-points
    the same closestPointOnMesh nodes at the new mesh.

    Returns the number of spokes rewired.
    """
    mesh_shape, mesh_xform = _resolve_mesh(mesh)
    if not mesh_shape:
        return 0

    rewired = 0
    for prefix in wheel_prefixes:
        for i in range(spoke_count):
            nn = f"{i + 1:02d}"
            base = f"{prefix}_spoke_{nn}"
            dm = f"{base}_worldY_DM"
            pma = f"{base}_groundDelta_PMA"
            if not (cmds.objExists(dm) and cmds.objExists(pma)):
                continue
            cpom = f"{base}_groundCPOM"
            if not cmds.objExists(cpom):
                cpom = cmds.createNode("closestPointOnMesh", n=cpom)
            _connect_if_needed(f"{mesh_shape}.outMesh", f"{cpom}.inMesh")
            _connect_if_needed(f"{mesh_xform}.worldMatrix[0]",
                                f"{cpom}.inputMatrix")
            # Query at the spoke's world position (reuse its DM).
            _connect_if_needed(f"{dm}.outputTranslate",
                                f"{cpom}.inPosition")
            # Swap the ground source on input1D[0] (ground_Y term).
            _connect_if_needed(f"{cpom}.positionY",
                                f"{pma}.input1D[0]")
            rewired += 1

        # ---- Auto-suspension 3-point footprint sampling for this wheel ----
        # Each footprint point (Front / Center / Back) samples the terrain
        # at its own world XZ so the contact-patch math can roll the wheel
        # over curbs smoothly.
        for tag in ("Front", "Center", "Back"):
            src = f"{prefix}_foot{tag}Src_ADL"
            foot_grp = f"{prefix}_foot{tag}_GRP"
            if not (cmds.objExists(src) and cmds.objExists(foot_grp)):
                continue
            scpom = f"{prefix}_foot{tag}CPOM"
            if not cmds.objExists(scpom):
                scpom = cmds.createNode("closestPointOnMesh", n=scpom)
            sdm = f"{prefix}_foot{tag}Pos_DM"
            if not cmds.objExists(sdm):
                sdm = cmds.createNode("decomposeMatrix", n=sdm)
            _connect_if_needed(f"{foot_grp}.worldMatrix[0]",
                                f"{sdm}.inputMatrix")
            _connect_if_needed(f"{mesh_shape}.outMesh", f"{scpom}.inMesh")
            _connect_if_needed(f"{mesh_xform}.worldMatrix[0]",
                                f"{scpom}.inputMatrix")
            _connect_if_needed(f"{sdm}.outputTranslate",
                                f"{scpom}.inPosition")
            # Redirect the footprint ground source from the flat locator to
            # this point's CPOM (input1 of the passthrough ADL; input2 is
            # the fixed sqrt(R^2-dx^2) geometry term — leave it alone).
            _connect_if_needed(f"{scpom}.positionY", f"{src}.input1")

    # Remember the assigned ground mesh on the chassis so the drive tool
    # can raycast against it (the live drive uses a true downward ray,
    # which avoids the closestPointOnMesh "snap to obstacle flank" bug).
    if cmds.objExists("C_chassis_CTRL"):
        if not cmds.attributeQuery("groundMesh", node="C_chassis_CTRL",
                                    exists=True):
            cmds.addAttr("C_chassis_CTRL", ln="groundMesh", dt="string")
        cmds.setAttr("C_chassis_CTRL.groundMesh", mesh_xform,
                     type="string")

    print(f"[vehicle] Ground mesh '{mesh_xform}' assigned — {rewired} "
          f"spoke(s) + {len(wheel_prefixes)}x3 footprint sample(s) now "
          f"follow terrain per-wheel.")
    return rewired


def assigned_ground_mesh():
    """Return the ground mesh assigned to this vehicle, or None."""
    if (cmds.objExists("C_chassis_CTRL")
            and cmds.attributeQuery("groundMesh", node="C_chassis_CTRL",
                                     exists=True)):
        m = cmds.getAttr("C_chassis_CTRL.groundMesh")
        if m and cmds.objExists(m):
            return m
    return None


def repair_body_motion():
    """Idempotently bring an ALREADY-BUILT rig up to the current body-motion
    behaviour, so saved scenes don't need rebuilding:

      1. Drop the min=0 clamp on bodyRoll / bodyPitch / bodyBob so each gain
         can be set negative (to invert an axis).
      2. Flip the pitch delta to (back - front) so driving UPHILL pitches
         the nose UP instead of diving it forward.

    Safe to run repeatedly and on freshly built rigs (it detects which
    pitch ordering is wired and only corrects the old one).

    Returns a short report dict.
    """
    report = {"clamps_freed": 0, "pitch_flipped": False}
    ctrl = "C_chassis_CTRL"
    if not cmds.objExists(ctrl):
        cmds.warning("[vehicle] No C_chassis_CTRL — build a vehicle first.")
        return report

    # 1) Free the gain clamps.
    for a in ("bodyRoll", "bodyPitch", "bodyBob"):
        if cmds.attributeQuery(a, node=ctrl, exists=True):
            if cmds.attributeQuery(a, node=ctrl, minExists=True):
                cmds.addAttr(f"{ctrl}.{a}", e=True, hasMinValue=False)
                report["clamps_freed"] += 1

    # 2) Flip the pitch delta if it's still (front - back). The subtract
    #    node output = input1D[0] - input1D[1]; the OLD wiring fed
    #    avgF -> [0], avgB -> [1]. We want avgB -> [0], avgF -> [1].
    pma = "C_body_pitchDelta_PMA"
    if cmds.objExists(pma):
        s0 = (cmds.listConnections(f"{pma}.input1D[0]", s=True, d=False)
              or [None])[0]
        if s0 == "C_body_avgF_PMA":
            in0 = cmds.listConnections(f"{pma}.input1D[0]", s=True, d=False,
                                       plugs=True)[0]
            in1 = cmds.listConnections(f"{pma}.input1D[1]", s=True, d=False,
                                       plugs=True)[0]
            cmds.connectAttr(in1, f"{pma}.input1D[0]", f=True)
            cmds.connectAttr(in0, f"{pma}.input1D[1]", f=True)
            report["pitch_flipped"] = True

    print(f"[vehicle] repair_body_motion: freed {report['clamps_freed']} "
          f"clamp(s), pitch {'flipped to nose-up' if report['pitch_flipped'] else 'already correct'}.")
    return report


def repair_tire_sag(wheel_prefixes=("LF", "RF", "LB", "RB")):
    """Idempotently add the tyre deflation-squat to an ALREADY-BUILT rig so
    a flat tyre visibly sags + flattens on flat ground / while driving,
    without rebuilding from guides.

    Per wheel, if it isn't already present:
      * insert {prefix}_pressureSag_GRP between suspension_CTRL and
        steering_AUTO (preserving world position),
      * add the tireSag attr (dv 0.25) to the wheel ctrl,
      * wire  pressureSag_GRP.translateY = -R * tireSag *
              (1 - effectiveTirePressure).

    Skips any wheel that already has the sag group, so it's safe to run on
    new rigs or repeatedly. Returns the number of wheels upgraded.
    """
    upgraded = 0
    for prefix in wheel_prefixes:
        sag_grp = f"{prefix}_pressureSag_GRP"
        susp_ctrl = f"{prefix}_suspension_CTRL"
        steer = f"{prefix}_steering_AUTO"
        wheel_ctrl = f"{prefix}_wheel_CTRL"
        if cmds.objExists(sag_grp):
            continue   # already upgraded
        if not (cmds.objExists(susp_ctrl) and cmds.objExists(steer)
                and cmds.objExists(wheel_ctrl)):
            continue
        # Only reparent if steering_AUTO is actually under suspension_CTRL
        # (the pre-fix layout); otherwise leave the hierarchy alone.
        parent = (cmds.listRelatives(steer, p=True) or [None])[0]
        if parent != susp_ctrl:
            continue
        try:
            grp = cmds.group(em=True, n=sag_grp)
            cmds.matchTransform(grp, susp_ctrl)
            cmds.parent(grp, susp_ctrl)
            cmds.parent(steer, grp)   # preserves world position

            if not cmds.attributeQuery("tireSag", node=wheel_ctrl,
                                       exists=True):
                cmds.addAttr(wheel_ctrl, ln="tireSag", at="double",
                             min=0, max=1, dv=0.25, k=True)

            R = 40.0
            if cmds.attributeQuery("wheelRadius", node=wheel_ctrl,
                                   exists=True):
                R = cmds.getAttr(f"{wheel_ctrl}.wheelRadius")

            # Reuse the deformation's reverse node (1 - pressure) if present.
            rev = f"{prefix}_pressureFactor_REV"
            if not cmds.objExists(rev):
                rev = cmds.createNode("reverse", n=rev)
                cmds.connectAttr(f"{wheel_ctrl}.effectiveTirePressure",
                                 f"{rev}.inputX")
            sag_amt = cmds.createNode("multDoubleLinear",
                                      n=f"{prefix}_tireSagAmt_MUL")
            cmds.connectAttr(f"{rev}.outputX", f"{sag_amt}.input1")
            cmds.connectAttr(f"{wheel_ctrl}.tireSag", f"{sag_amt}.input2")
            sag_neg = cmds.createNode("multDoubleLinear",
                                      n=f"{prefix}_tireSagDrop_MUL")
            cmds.connectAttr(f"{sag_amt}.output", f"{sag_neg}.input1")
            cmds.setAttr(f"{sag_neg}.input2", -R)
            cmds.connectAttr(f"{sag_neg}.output", f"{sag_grp}.translateY",
                             f=True)
            upgraded += 1
        except Exception as exc:
            cmds.warning(f"[vehicle] repair_tire_sag: {prefix} skipped "
                         f"({exc}).")
    print(f"[vehicle] repair_tire_sag: upgraded {upgraded} wheel(s).")
    return upgraded


def clear_ground_mesh(wheel_prefixes=("LF", "RF", "LB", "RB"),
                      spoke_count=16):
    """Revert every spoke's ground source back to the flat C_ground_LOC.
    Leaves the closestPointOnMesh nodes in place (disconnected) so a
    later assign_ground_mesh re-uses them. Returns spokes reverted."""
    loc_y = f"{GROUND_LOC_NAME}_decompose.outputTranslateY"
    if not cmds.objExists(f"{GROUND_LOC_NAME}_decompose"):
        cmds.warning("[vehicle] No C_ground_LOC decompose found.")
        return 0
    reverted = 0
    for prefix in wheel_prefixes:
        for i in range(spoke_count):
            nn = f"{i + 1:02d}"
            pma = f"{prefix}_spoke_{nn}_groundDelta_PMA"
            if not cmds.objExists(pma):
                continue
            for e in (cmds.listConnections(f"{pma}.input1D[0]",
                                            s=True, d=False,
                                            plugs=True) or []):
                cmds.disconnectAttr(e, f"{pma}.input1D[0]")
            cmds.connectAttr(loc_y, f"{pma}.input1D[0]", f=True)
            reverted += 1
        # Revert the 3 footprint ground sources back to the flat locator.
        for tag in ("Front", "Center", "Back"):
            src = f"{prefix}_foot{tag}Src_ADL"
            if not cmds.objExists(src):
                continue
            for e in (cmds.listConnections(f"{src}.input1",
                                            s=True, d=False,
                                            plugs=True) or []):
                cmds.disconnectAttr(e, f"{src}.input1")
            cmds.connectAttr(loc_y, f"{src}.input1", f=True)
    print(f"[vehicle] Reverted {reverted} spoke(s) + suspensions to the "
          f"flat C_ground_LOC.")
    return reverted
