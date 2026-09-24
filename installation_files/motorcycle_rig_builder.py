"""
===============================================================================
 MOTORCYCLE RIG BUILDER: a single-track vehicle that leans
===============================================================================

 A bike is a vehicle, so it is built on the same bones as the car rig:
 the same C_global_CTRL, the same C_chassis_CTRL with its odometer, the
 same wheels (hub, spoke tyre, auto-spin, pressure, ground contact) and
 the same C_steering_CTRL. That means everything that already works on a
 car works here too: Drive Mode (WASD), terrain, the ground raycast,
 crash, cargo, export and the bind tools.

 What is different is the shape of it:

   * TWO wheels, on the centre line: CF (front) and CB (back).
   * The front wheel hangs off a FORK that steers about the steering-head
     axis, so it turns and its suspension slides along the fork, not
     straight up.
   * The back wheel sits on a SWINGARM that pivots on the frame and aims
     at the rear axle, with a coil-over shock from the frame down to it.
   * The whole bike LEANS. C_lean_AUTO sits above the frame with its
     pivot on the ground line, so leaning tips the bike about its tyres
     instead of sliding them sideways. Drive Mode leans it into a corner
     automatically (C_chassis_CTRL.lean), and the attribute is keyable
     so an animator can take it over.

 Hierarchy:

     C_chassis_CTRL
       └── C_lean_AUTO                     (lean, pivot on the ground)
             ├── C_body_OSC → C_body_AUTO  (the frame shell: pitch + bob)
             ├── C_steeringHead_GRP        (raked back = the steer axis)
             │     ├── C_steering_GRP > C_steering_CTRL   (the bars)
             │     └── C_fork_steer_AUTO   (turned by C_steering_CTRL)
             │           └── CF_suspension_OFFSET ...     (front wheel)
             └── C_rearMount_GRP
                   └── CB_suspension_OFFSET ...        (back wheel)

 Usage in Maya (Python):

     import motorcycle_guides, motorcycle_rig_builder
     gs = motorcycle_guides.MotorcycleGuideSystem()
     gs.build()                       # place them on your model
     motorcycle_rig_builder.MotorcycleRig(
         positions=gs.read_positions()).build()

===============================================================================
"""

import copy
import math
import maya.cmds as cmds

import vehicle_rig_builder as vrb
from vehicle_rig_builder import VehicleRig, VehicleWheel
from character_rig_builder import (
    SCALE,
    COLOR_CENTER, COLOR_IK,
    make_offset_group, lock_hide_attrs,
    create_circle_ctrl,
    CoreRig,
)


# How far a bike is allowed to lean, in degrees. Real road bikes run out
# of tyre at about 50; a MotoGP rider gets past 60.
MAX_LEAN = 60.0


class MotorcycleRig(VehicleRig):
    """A two-wheeled vehicle rig: fork, swingarm, lean.

    Top group: VEHICLE_RIG_GRP (same as the car: one vehicle per scene).
    """

    TOP_GROUP = "VEHICLE_RIG_GRP"

    DEFAULT_POSITIONS = {
        "chassis":       (0,  70,   0),    # frame centre / root
        "frontWheel":    (0,  32,  72),    # front axle
        "backWheel":     (0,  32, -63),    # back axle
        "frontRadius":   32.0,
        "backRadius":    32.0,
        # Suspension travel defaults come off this (see _build_chassis).
        "wheelRadius":   32.0,
        "steeringHead":  (0, 100,  45),    # top of the steering head
        "handlebar":     (0, 112,  34),    # bar centre
        "swingarmPivot": (0,  52,  -6),    # arm pivot on the frame
        "shockTop":      (0,  90, -26),    # rear shock top mount
    }

    DEFAULT_MODULES = {"chassis", "wheels", "steering", "suspension"}

    def __init__(self, positions=None, modules=None,
                 spoke_count=16, build_spoke_ctrls=True, ground_mesh=None):
        VehicleRig.__init__(
            self,
            positions=positions or copy.deepcopy(self.DEFAULT_POSITIONS),
            modules=modules, spoke_count=spoke_count,
            build_spoke_ctrls=build_spoke_ctrls, ground_mesh=ground_mesh,
            axles=2, tracked=False, steer_axles=1)
        # Outputs particular to a bike.
        self.lean_grp = None
        self.head_grp = None
        self.fork_steer = None
        self.rear_mount = None
        self.fork_jnt = None
        self.fork_lower_jnt = None
        self.handlebar_jnt = None
        self.swingarm_jnt = None

    # -----------------------------------------------------------------------

    def rake(self):
        """The steering head's lean back from vertical, in degrees: the
        angle of the line from the steering head down to the front axle.
        A cruiser is around 30, a sportbike around 23."""
        hx, hy, hz = self.positions["steeringHead"]
        ax, ay, az = self.positions["frontWheel"]
        drop = hy - ay
        if drop <= 1e-6:
            return 0.0
        return math.degrees(math.atan2(az - hz, drop))

    def wheelbase(self):
        return abs(self.positions["frontWheel"][2]
                   - self.positions["backWheel"][2])

    def _size_factor(self):
        """The bike's size relative to the default car: front tyre nose to
        back tyre tail over REFERENCE_LENGTH. The default bike is 0.59, so
        its controls are drawn bike-sized, not car-sized."""
        f = self.positions["frontWheel"]
        b = self.positions["backWheel"]
        fr = self.positions.get("frontRadius", 32.0)
        br = self.positions.get("backRadius", 32.0)
        length = (f[2] + fr) - (b[2] - br)
        if length <= 1e-6:
            return 1.0
        return max(0.001, length / vrb.REFERENCE_LENGTH)

    # -----------------------------------------------------------------------

    def _build_impl(self):
        print("[MotorcycleRig] Building: wheelbase %.0f, rake %.0f deg, "
              "size %.2f." % (self.wheelbase(), self.rake(), self.size))

        # 1. Core: global + COG, same as every other rig.
        self.core = CoreRig(positions={"cog": self.positions["chassis"]})
        self.core.build()
        self.core.main_grp = cmds.rename("CHARACTER_RIG_GRP", self.TOP_GROUP)
        cg, jg, mg = (self.core.ctrl_grp, self.core.jnt_grp,
                      self.core.misc_grp)

        # 2. Chassis: the car's, so the odometer, tyre pressure, auto
        #    suspension and body attrs are all the ones the tools expect.
        self._build_chassis(cg, jg, mg)
        self._add_bike_attrs()

        # 3. Lean: between the chassis ctrl and everything else, pivoting
        #    on the ground line so the tyres stay put when the bike tips.
        self._build_lean()

        # 4. Ground reference + spin driver.
        ground_y_attr = self._build_ground_reference()
        chassis_drive = self._build_spin_driver()

        # 5. Fork and swingarm mounts, then the two wheels on them.
        self._build_fork()
        self._build_wheels(cg, jg, mg,
                           chassis_drive_attr=chassis_drive,
                           ground_y_attr=ground_y_attr)
        self._wire_master_tire_pressure()
        if "suspension" in self.modules:
            self._wire_auto_suspension(ground_y_attr)
            self._rake_front_travel()
            self._lean_contact_comp()

        # 6. The parts that are visibly bolted to the wheels.
        self._build_fork_joints()
        self._build_swingarm()

        # 7. Frame pitch + bob from the two wheels (no roll: it leans).
        if self.body_auto:
            self._wire_body_motion()

        for grp in (self.core.main_grp, self.core.ctrl_grp,
                    self.core.jnt_grp):
            lock_hide_attrs(grp, ["tx", "ty", "tz", "rx", "ry", "rz",
                                  "sx", "sy", "sz"])
        for guide_grp in ("MOTORCYCLE_RIG_GUIDES_GRP",
                          "VEHICLE_RIG_GUIDES_GRP", "RIG_GUIDES_GRP"):
            if cmds.objExists(guide_grp):
                try:
                    cmds.setAttr(f"{guide_grp}.visibility", 0)
                except Exception:
                    pass

        if self.ground_mesh and cmds.objExists(self.ground_mesh):
            vrb.assign_ground_mesh(self.ground_mesh,
                                   spoke_count=self.spoke_count)

        cmds.select(cl=True)
        print(f"[MotorcycleRig] Done. Top node: {self.TOP_GROUP}")

    # -----------------------------------------------------------------------
    # Lean
    # -----------------------------------------------------------------------

    def _add_bike_attrs(self):
        """The chassis attributes a bike has and a car doesn't."""
        c = self.chassis_ctrl
        cmds.addAttr(c, ln="vehicleKind", dt="string")
        cmds.setAttr(f"{c}.vehicleKind", "motorcycle", type="string")
        cmds.addAttr(c, ln="leanHeader", at="enum", en="----LEAN----:",
                     k=True)
        cmds.setAttr(f"{c}.leanHeader", l=True, cb=True, k=False)
        # Positive = lean RIGHT, the way Drive Mode's D key turns.
        cmds.addAttr(c, ln="lean", at="double", min=-MAX_LEAN, max=MAX_LEAN,
                     dv=0, k=True)
        # How much of the lean the drive tool computes actually shows.
        cmds.addAttr(c, ln="leanAmount", at="double", min=0, max=2, dv=1,
                     k=True)
        cmds.addAttr(c, ln="rake", at="double", dv=self.rake())
        cmds.setAttr(f"{c}.rake", cb=True)

    def _build_lean(self):
        """C_lean_AUTO: an empty group whose pivot sits on the GROUND at
        the bike's centre line, so rotating it tips the bike over its
        tyres (leaning about the frame centre would slide them out).
        Everything below the chassis ctrl moves under here."""
        self.lean_grp = cmds.group(em=True, n="C_lean_AUTO")
        cmds.parent(self.lean_grp, self.chassis_ctrl)     # keeps the pivot
        cmds.setAttr(f"{self.lean_grp}.rotateOrder", 2)      # zxy
        # lean * leanAmount -> rotateZ
        mul = cmds.createNode("multDoubleLinear", n="C_lean_MDL")
        cmds.connectAttr(f"{self.chassis_ctrl}.lean", f"{mul}.input1")
        cmds.connectAttr(f"{self.chassis_ctrl}.leanAmount", f"{mul}.input2")
        cmds.connectAttr(f"{mul}.output", f"{self.lean_grp}.rotateZ")
        for a in ("tx", "ty", "tz", "rx", "ry", "rz", "sx", "sy", "sz"):
            cmds.setAttr(f"{self.lean_grp}.{a}", l=True, k=False, cb=False)
        # The body shell rides the lean.
        cmds.parent(self.body_osc, self.lean_grp)

    # -----------------------------------------------------------------------
    # Fork
    # -----------------------------------------------------------------------

    def _build_fork(self):
        """The steering head (raked back), the group the steering turns,
        and the handlebar control that turns it.

        The head group's local Y runs UP the steering axis, so the fork
        steers about its own rotateY and the front wheel's suspension
        slides along the fork, not straight up (_rake_front_travel keeps
        the tyre on the ground while it does).
        """
        rake = self.rake()
        head = self.positions["steeringHead"]

        self.head_grp = cmds.group(em=True, n="C_steeringHead_GRP")
        cmds.xform(self.head_grp, ws=True, t=head)
        cmds.setAttr(f"{self.head_grp}.rotateX", -rake)
        cmds.parent(self.head_grp, self.lean_grp)

        self.fork_steer = cmds.group(em=True, n="C_fork_steer_AUTO",
                                     p=self.head_grp)
        cmds.setAttr(f"{self.fork_steer}.translate", 0, 0, 0)
        cmds.setAttr(f"{self.fork_steer}.rotate", 0, 0, 0)

        if "steering" not in self.modules:
            return

        # ---- handlebar ctrl ----
        # Its local Z points DOWN the steering axis, so rotateZ turns it
        # exactly the way the fork turns, and rotateZ is the channel
        # Drive Mode already steers with. It hangs off the STEERING HEAD,
        # not off the fork it drives: riding the fork as well would turn
        # the drawn bars twice as far as the wheel.
        bar_grp = cmds.group(em=True, n="C_steering_GRP")
        cmds.xform(bar_grp, ws=True, t=self.positions["handlebar"])
        cmds.setAttr(f"{bar_grp}.rotateX", 90.0 - rake)
        self.steering_ctrl = create_circle_ctrl(
            "C_steering_CTRL", radius=0.9 * SCALE, normal=(0, 0, 1),
            color=COLOR_IK)
        bar = cmds.curve(d=1, n="C_handlebar_CRV", p=[
            (-3.0 * SCALE, 0, -0.6 * SCALE),
            (-3.0 * SCALE, 0, 0),
            (3.0 * SCALE, 0, 0),
            (3.0 * SCALE, 0, -0.6 * SCALE)])
        for shp in cmds.listRelatives(bar, s=True) or []:
            cmds.parent(shp, self.steering_ctrl, r=True, s=True)
            cmds.setAttr(f"{shp}.overrideEnabled", 1)
            cmds.setAttr(f"{shp}.overrideColor", COLOR_IK)
        cmds.delete(bar)
        cmds.matchTransform(self.steering_ctrl, bar_grp)
        offset = make_offset_group(self.steering_ctrl)
        cmds.parent(offset, bar_grp)
        cmds.parent(bar_grp, self.head_grp)
        lock_hide_attrs(self.steering_ctrl,
                        ["tx", "ty", "tz", "rx", "ry", "sx", "sy", "sz"])

        # ---- steering -> fork ----
        # Same ratio as the car, so D (right) points the front wheel right.
        mul = cmds.createNode("multDoubleLinear", n="C_forkSteer_MDL")
        cmds.connectAttr(f"{self.steering_ctrl}.rotateZ", f"{mul}.input1")
        cmds.setAttr(f"{mul}.input2", VehicleWheel.STEERING_RATIO)
        cmds.connectAttr(f"{mul}.output", f"{self.fork_steer}.rotateY")

    # -----------------------------------------------------------------------
    # Wheels
    # -----------------------------------------------------------------------

    def _build_wheels(self, cg, jg, mg, chassis_drive_attr=None,
                      ground_y_attr=None, **_kw):
        """CF on the fork, CB on a frame mount at the swingarm's axle.

        Both hang UNDER the lean group but OUTSIDE the body shell, so the
        frame can pitch and bob on its suspension while the tyres stay
        planted (and so the body motion can't feed back into the
        suspension that drives it).
        """
        self.rear_mount = cmds.group(em=True, n="C_rearMount_GRP")
        cmds.parent(self.rear_mount, self.lean_grp)

        specs = (
            ("CF", self.positions["frontWheel"],
             self.positions.get("frontRadius", 32.0), self.fork_steer,
             None),
            ("CB", self.positions["backWheel"],
             self.positions.get("backRadius", 32.0), self.rear_mount,
             self.positions.get("shockTop")),
        )
        for prefix, pos, radius, parent, spring_top in specs:
            wheel = VehicleWheel(
                prefix=prefix, position=pos, radius=radius,
                is_front=False, is_left=True,
                chassis_ctrl=parent, parent_jnt=self.chassis_jnt,
                ctrl_grp=cg, jnt_grp=jg, misc_grp=mg,
                chassis_drive_attr=chassis_drive_attr,
                steering_drive_attr=None,      # the FORK steers, not the hub
                ground_y_attr=ground_y_attr,
                spoke_count=self.spoke_count,
                build_spoke_ctrls=self.build_spoke_ctrls,
                build_spring=spring_top is not None,
                spring_top_pos=spring_top)
            wheel.build()
            self.wheels[prefix] = wheel
        # The front wheel assembly squares up to the FORK (its offset group
        # is built world-aligned), so its suspension slides along the fork
        # tubes the way a telescopic fork actually works. Tilting a wheel
        # about its own spin axis shows nothing: it is round.
        front = self.wheels["CF"].suspension_offset
        if front:
            cmds.setAttr(f"{front}.rotate", 0, 0, 0)

    def _rake_front_travel(self):
        """The front wheel slides along the FORK, which leans back by the
        rake, so a travel of t only lifts the wheel by t*cos(rake). Divide
        the travel by cos(rake) and the tyre stays exactly on the ground
        at any rake."""
        wheel = self.wheels.get("CF")
        if not wheel or not wheel.suspension_auto:
            return
        plug = f"{wheel.suspension_auto}.translateY"
        src = cmds.listConnections(plug, p=True, s=True, d=False)
        if not src:
            return
        c = math.cos(math.radians(self.rake()))
        mul = cmds.createNode("multDoubleLinear", n="CF_forkTravel_MDL")
        cmds.connectAttr(src[0], f"{mul}.input1")
        cmds.setAttr(f"{mul}.input2", 1.0 / max(1e-3, c))
        cmds.connectAttr(f"{mul}.output", plug, f=True)

    def _lean_contact_comp(self):
        """A leaned wheel sits LOWER: a tyre of radius R tipped by an angle
        meets the ground R*cos(lean) below its hub, not R. Without this the
        auto-suspension keeps pushing the hub up to R and the bike floats
        on its tyres in a hard lean (9 cm at 45 degrees on a 32 cm wheel).

        cos(lean) comes off a helper group turned by the same lean: its
        local matrix times (0,1,0) is the leaned up-axis, whose Y IS the
        cosine. No expression, so it evaluates like the rest of the rig.
        """
        helper = cmds.group(em=True, n="C_leanCos_GRP",
                            p=self.core.misc_grp)
        cmds.connectAttr("C_lean_MDL.output", f"{helper}.rotateZ")
        vp = cmds.createNode("vectorProduct", n="C_leanCos_VP")
        cmds.setAttr(f"{vp}.operation", 3)        # vector matrix product
        cmds.setAttr(f"{vp}.input1", 0, 1, 0)
        cmds.setAttr(f"{vp}.normalizeOutput", 0)
        cmds.connectAttr(f"{helper}.matrix", f"{vp}.matrix")
        # Each footprint's "how high the hub sits when this point is on the
        # ground" term, sqrt(R^2 - dx^2), is a straight-up distance: tip
        # the wheel and it shrinks by cos(lean).
        for prefix in self.wheels:
            for tag, _frac in self.FOOT_OFFSETS:
                geo = f"{prefix}_foot{tag}Geo_MDL"
                if not cmds.objExists(geo):
                    continue
                mul = cmds.createNode("multDoubleLinear",
                                      n=f"{prefix}_foot{tag}Lean_MDL")
                cmds.setAttr(f"{mul}.input1",
                             cmds.getAttr(f"{geo}.input1"))
                cmds.connectAttr(f"{vp}.outputY", f"{mul}.input2")
                cmds.connectAttr(f"{mul}.output", f"{geo}.input1", f=True)

    # -----------------------------------------------------------------------
    # Fork tubes, handlebar, swingarm, as BIND joints
    # -----------------------------------------------------------------------

    def _joint(self, name, pos, parent, radius=0.4):
        cmds.select(cl=True)
        jnt = cmds.joint(n=name, p=pos)
        cmds.setAttr(f"{jnt}.jointOrient", 0, 0, 0)
        cmds.setAttr(f"{jnt}.radius", radius * SCALE)
        cmds.parent(jnt, parent)
        return jnt

    def _build_fork_joints(self):
        """Upper fork tube (turns with the steering) and lower tube (slides
        with the wheel), plus the handlebar joint that rides the fork."""
        head = self.positions["steeringHead"]
        self.fork_jnt = self._joint("C_fork_BIND_JNT", head,
                                    self.chassis_jnt, 0.5)
        cmds.parentConstraint(self.fork_steer, self.fork_jnt, mo=True)
        axle = self.positions["frontWheel"]
        self.fork_lower_jnt = self._joint("C_forkLower_BIND_JNT", axle,
                                          self.fork_jnt, 0.4)
        hub = self.wheels["CF"].hub_jnt
        if hub:
            cmds.pointConstraint(hub, self.fork_lower_jnt, mo=False)
        if "steering" in self.modules:
            self.handlebar_jnt = self._joint(
                "C_handlebar_BIND_JNT", self.positions["handlebar"],
                self.fork_jnt, 0.35)

    def _build_swingarm(self):
        """The swingarm pivots on the frame and always aims at the rear
        axle, so it swings up and down as the back wheel travels."""
        pivot = self.positions["swingarmPivot"]
        self.swingarm_jnt = self._joint("C_swingarm_BIND_JNT", pivot,
                                        self.chassis_jnt, 0.5)
        hub = self.wheels["CB"].hub_jnt
        if not hub:
            return
        cmds.aimConstraint(hub, self.swingarm_jnt,
                           aimVector=(0, 0, -1), upVector=(0, 1, 0),
                           worldUpType="objectrotation",
                           worldUpVector=(0, 1, 0),
                           worldUpObject=self.body_auto or self.chassis_ctrl,
                           n="C_swingarm_AIM")

    # -----------------------------------------------------------------------
    # Frame reaction
    # -----------------------------------------------------------------------

    def _wire_body_motion(self):
        """Pitch and bob the frame from the two wheels' travel. No roll:
        a bike doesn't roll on its suspension, it LEANS (C_lean_AUTO).

            pitch (rotateX) = (back - front) * bodyPitch * autoBodyMotion
            bob   (tY)      = avg(front, back) * bodyBob * autoBodyMotion

        Front wheel riding high (going uphill, or the front hitting a
        bump) pitches the nose up, exactly like the car.
        """
        front = self.wheels.get("CF")
        back = self.wheels.get("CB")
        if not (front and back
                and front.suspension_auto and back.suspension_auto):
            return
        b = f"{back.suspension_auto}.translateY"
        chassis, body = self.chassis_ctrl, self.body_auto
        # The front travels along the raked fork, so only cos(rake) of it
        # is height, or a flat lift would pitch the frame.
        vert = cmds.createNode("multDoubleLinear", n="C_body_frontVert_MDL")
        cmds.connectAttr(f"{front.suspension_auto}.translateY",
                         f"{vert}.input1")
        cmds.setAttr(f"{vert}.input2", math.cos(math.radians(self.rake())))
        f = f"{vert}.output"

        def gate(src, amount_attr, name):
            m1 = cmds.createNode("multDoubleLinear", n=f"{name}_AMT")
            cmds.connectAttr(src, f"{m1}.input1")
            cmds.connectAttr(f"{chassis}.{amount_attr}", f"{m1}.input2")
            m2 = cmds.createNode("multDoubleLinear", n=f"{name}_GATE")
            cmds.connectAttr(f"{m1}.output", f"{m2}.input1")
            cmds.connectAttr(f"{chassis}.autoBodyMotion", f"{m2}.input2")
            return f"{m2}.output"

        pitch = cmds.createNode("plusMinusAverage",
                                n="C_body_pitchDelta_PMA")
        cmds.setAttr(f"{pitch}.operation", 2)          # subtract
        cmds.connectAttr(b, f"{pitch}.input1D[0]")
        cmds.connectAttr(f, f"{pitch}.input1D[1]")
        cmds.connectAttr(gate(f"{pitch}.output1D", "bodyPitch",
                              "C_body_pitch"), f"{body}.rotateX", f=True)

        bob = cmds.createNode("plusMinusAverage", n="C_body_avgAll_PMA")
        cmds.setAttr(f"{bob}.operation", 3)            # average
        cmds.connectAttr(f, f"{bob}.input1D[0]")
        cmds.connectAttr(b, f"{bob}.input1D[1]")
        cmds.connectAttr(gate(f"{bob}.output1D", "bodyBob", "C_body_bob"),
                         f"{body}.translateY", f=True)


# =============================================================================
# Convenience
# =============================================================================

def build_from_guides(spoke_count=16, build_spoke_ctrls=True,
                      ground_mesh=None):
    """Build a motorcycle from MOTORCYCLE_RIG_GUIDES_GRP."""
    import motorcycle_guides
    gs = motorcycle_guides.MotorcycleGuideSystem()
    if not gs.exists():
        cmds.error("No motorcycle guides in the scene. Create Guides first.")
        return None
    rig = MotorcycleRig(positions=gs.read_positions(),
                        spoke_count=spoke_count,
                        build_spoke_ctrls=build_spoke_ctrls,
                        ground_mesh=ground_mesh)
    rig.build()
    return rig
