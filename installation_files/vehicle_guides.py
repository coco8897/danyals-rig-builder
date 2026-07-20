"""
===============================================================================
 VEHICLE GUIDES — per-car locator placement (left + center only)
===============================================================================

 Every car is different, so place the guide locators to match your model,
 then Build From Guides. You only place the LEFT side and CENTER locators —
 the right side is mirrored automatically on build (cars are symmetric).

 Locators:
   Center (single):
     C_chassis      — body centre / root
     C_steering     — steering wheel (driver side)
     C_hood         — hood hinge (front)
     C_trunk        — trunk hinge (back)
   Left (mirrored to right on build):
     L_frontWheel   — front rim centre  (its height = front tyre radius)
     L_backWheel    — back rim centre   (its height = back tyre radius)
     L_frontSpring  — front coil-over top (sets spring height)
     L_backSpring   — back coil-over top
     L_frontDoor    — front door hinge
     L_backDoor     — back door hinge

 Workflow:
     import vehicle_guides
     from importlib import reload; reload(vehicle_guides)
     gs = vehicle_guides.VehicleGuideSystem()
     gs.build()                       # spawn locators at defaults
     # ... drag the L + C locators to fit your car ...
     positions = gs.read_positions()  # mirrors L -> R automatically
     import vehicle_rig_builder
     vehicle_rig_builder.VehicleRig(positions=positions).build()
===============================================================================
"""

import json
import maya.cmds as cmds


GUIDES_GRP_NAME = "VEHICLE_RIG_GUIDES_GRP"
GUIDE_SUFFIX = "_GUIDE"
GUIDE_SCALE = 8.0          # vehicles are big — larger locators read better

COLOR_CENTER = 17  # yellow
COLOR_LEFT   = 18  # cyan


# name -> dict(pos, color, note). Only LEFT + CENTER locators exist;
# the right side is synthesised by read_positions() (negate X).
DEFAULT_GUIDES = {
    # ---- Center ----
    "C_chassis":     {"pos": (0,  80,   0), "color": COLOR_CENTER,
                       "note": "Body centre / root. The whole car drives "
                               "from here."},
    "C_steering":    {"pos": (40, 110,  50), "color": COLOR_CENTER,
                       "note": "Steering wheel. Place on the driver side; "
                               "rotating it steers the front wheels."},
    "C_hood":        {"pos": (0, 110,  80), "color": COLOR_CENTER,
                       "note": "Hood hinge — at the FRONT edge of the "
                               "engine bay. The hood lifts up + forward."},
    "C_trunk":       {"pos": (0, 110, -80), "color": COLOR_CENTER,
                       "note": "Trunk hinge — at the BACK edge of the boot "
                               "lid. The trunk lifts up + backward."},

    # ---- Left (mirrored to right on build) ----
    "L_frontWheel":  {"pos": (80, 40,  130), "color": COLOR_LEFT,
                       "note": "Front-left rim CENTRE. Its height above the "
                               "ground sets the front tyre radius."},
    "L_backWheel":   {"pos": (80, 40, -130), "color": COLOR_LEFT,
                       "note": "Back-left rim CENTRE. Its height sets the "
                               "back tyre radius (can differ from front)."},
    "L_frontSpring": {"pos": (80, 115,  130), "color": COLOR_LEFT,
                       "note": "Front-left coil-over TOP. Sets the spring "
                               "height — place it where the strut mounts "
                               "to the body. Keep it ABOVE the tyre top."},
    "L_backSpring":  {"pos": (80, 115, -130), "color": COLOR_LEFT,
                       "note": "Back-left coil-over top. Keep it above the "
                               "tyre top."},
    "L_frontDoor":   {"pos": (90, 90,   60), "color": COLOR_LEFT,
                       "note": "Front-left door HINGE — at the front edge "
                               "of the door, where it pivots open."},
    "L_backDoor":    {"pos": (90, 90,    0), "color": COLOR_LEFT,
                       "note": "Back-left door hinge."},
}


class VehicleGuideSystem(object):

    def __init__(self):
        self.guides_grp = None
        self.guides = {}

    # -----------------------------------------------------------------------

    def exists(self):
        return cmds.objExists(GUIDES_GRP_NAME)

    def build(self):
        if self.exists():
            cmds.warning(f"{GUIDES_GRP_NAME} already exists. Delete it "
                         f"first or call reset_to_defaults().")
            self._refresh_handles()
            return
        self.guides_grp = cmds.group(em=True, n=GUIDES_GRP_NAME)
        cmds.setAttr(f"{self.guides_grp}.useOutlinerColor", 1)
        cmds.setAttr(f"{self.guides_grp}.outlinerColor", 0.4, 0.9, 1.0)
        for name, info in DEFAULT_GUIDES.items():
            self._create_guide(name, info["pos"], info["color"],
                                info.get("note", ""))
        cmds.select(cl=True)
        print(f"[VehicleGuideSystem] Created {len(DEFAULT_GUIDES)} guides "
              f"(left + center). Right side is mirrored on build.")

    def delete(self):
        if self.exists():
            cmds.delete(GUIDES_GRP_NAME)
            self.guides = {}
            print(f"[VehicleGuideSystem] Deleted {GUIDES_GRP_NAME}.")

    def reset_to_defaults(self):
        if not self.exists():
            cmds.warning("No vehicle guides to reset.")
            return
        self._refresh_handles()
        for name, info in DEFAULT_GUIDES.items():
            loc = self.guides.get(name)
            if loc and cmds.objExists(loc):
                cmds.xform(loc, ws=True, t=info["pos"])
        print("[VehicleGuideSystem] Reset all guides to defaults.")

    # -----------------------------------------------------------------------

    def read_positions(self):
        """Return a VehicleRig positions dict. The right side and the
        4-wheel / 4-door / per-wheel-spring entries are SYNTHESISED from
        the left + center locators by mirroring X."""
        if not self.exists():
            cmds.error(f"{GUIDES_GRP_NAME} does not exist. "
                       f"Run VehicleGuideSystem().build() first.")
            return {}
        self._refresh_handles()
        p = self._pos

        def mir(name):
            x, y, z = p(name)
            return (-x, y, z)

        lf_w = p("L_frontWheel")
        lb_w = p("L_backWheel")
        lf_s = p("L_frontSpring")
        lb_s = p("L_backSpring")
        lf_d = p("L_frontDoor")
        lb_d = p("L_backDoor")

        return {
            "chassis":      p("C_chassis"),
            "steeringWheel": p("C_steering"),
            "hoodHinge":    p("C_hood"),
            "trunkHinge":   p("C_trunk"),

            # Wheels (front + back, L mirrored to R).
            "LF_wheel": lf_w,            "RF_wheel": (-lf_w[0], lf_w[1], lf_w[2]),
            "LB_wheel": lb_w,            "RB_wheel": (-lb_w[0], lb_w[1], lb_w[2]),
            # Per-wheel radius derived from the rim-centre height.
            "LF_wheelRadius": lf_w[1],   "RF_wheelRadius": lf_w[1],
            "LB_wheelRadius": lb_w[1],   "RB_wheelRadius": lb_w[1],
            # Spring top per wheel — full XYZ so the strut can be
            # offset / angled exactly where the guide was placed.
            # (springTopY kept too as a height-only fallback.)
            "LF_springTop": lf_s,
            "RF_springTop": (-lf_s[0], lf_s[1], lf_s[2]),
            "LB_springTop": lb_s,
            "RB_springTop": (-lb_s[0], lb_s[1], lb_s[2]),
            "LF_springTopY": lf_s[1],    "RF_springTopY": lf_s[1],
            "LB_springTopY": lb_s[1],    "RB_springTopY": lb_s[1],
            # Doors.
            "LF_doorHinge": lf_d,        "RF_doorHinge": (-lf_d[0], lf_d[1], lf_d[2]),
            "LB_doorHinge": lb_d,        "RB_doorHinge": (-lb_d[0], lb_d[1], lb_d[2]),
        }

    # -----------------------------------------------------------------------

    def save_to_json(self, filepath):
        if not self.exists():
            cmds.error("No vehicle guides to save.")
            return
        self._refresh_handles()
        data = {name: list(self._pos(name)) for name in self.guides
                 if cmds.objExists(self.guides[name])}
        with open(filepath, "w") as f:
            json.dump(data, f, indent=2)
        print(f"[VehicleGuideSystem] Saved {len(data)} guides to "
              f"{filepath}")

    def load_from_json(self, filepath):
        with open(filepath, "r") as f:
            data = json.load(f)
        if not self.exists():
            self.build()
        else:
            self._refresh_handles()
        for name, pos in data.items():
            loc = self.guides.get(name)
            if loc and cmds.objExists(loc):
                cmds.xform(loc, ws=True, t=tuple(pos))
        print(f"[VehicleGuideSystem] Loaded {len(data)} guides from "
              f"{filepath}")

    # -----------------------------------------------------------------------
    # Internals
    # -----------------------------------------------------------------------

    def _create_guide(self, name, pos, color, note=""):
        loc_name = name + GUIDE_SUFFIX
        loc = cmds.spaceLocator(n=loc_name)[0]
        for axis in ("X", "Y", "Z"):
            cmds.setAttr(f"{loc}Shape.localScale{axis}", GUIDE_SCALE)
        cmds.xform(loc, ws=True, t=pos)
        cmds.setAttr(f"{loc}Shape.overrideEnabled", 1)
        cmds.setAttr(f"{loc}Shape.overrideColor", color)
        # Plain-English note in the Attribute Editor.
        if note:
            cmds.addAttr(loc, ln="notes", dt="string")
            cmds.setAttr(f"{loc}.notes", note, type="string")
        # Lock rotate / scale — guides are positions only.
        for a in ("rx", "ry", "rz", "sx", "sy", "sz"):
            cmds.setAttr(f"{loc}.{a}", l=True, k=False, cb=False)
        cmds.parent(loc, self.guides_grp)
        self.guides[name] = loc

    def _refresh_handles(self):
        if cmds.objExists(GUIDES_GRP_NAME):
            self.guides_grp = GUIDES_GRP_NAME
        self.guides = {}
        for name in DEFAULT_GUIDES:
            loc = name + GUIDE_SUFFIX
            if cmds.objExists(loc):
                self.guides[name] = loc

    def _pos(self, name):
        loc = self.guides.get(name)
        if not loc or not cmds.objExists(loc):
            cmds.error(f"Missing vehicle guide locator: "
                       f"{name}{GUIDE_SUFFIX}")
        return tuple(cmds.xform(loc, q=True, ws=True, t=True))
