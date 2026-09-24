"""
===============================================================================
 BIRD GUIDES — locator placement for bird_rig_builder (raptor)
===============================================================================

 Workflow:
   1. BirdGuideSystem().build()  -> spawns colored locators at raptor defaults
   2. Drag locators to fit the character mesh
   3. BirdGuideSystem().mirror_left_to_right()  -> copy L wing/leg to R
   4. positions = BirdGuideSystem().read_positions()
   5. BirdRig(positions=positions).build()

 Save / load:
   BirdGuideSystem().save_to_json("C:/path/eagle.json")
   BirdGuideSystem().load_from_json("C:/path/eagle.json")
===============================================================================
"""

import json
import maya.cmds as cmds


GUIDES_GRP_NAME = "BIRD_RIG_GUIDES_GRP"
GUIDE_SUFFIX = "_GUIDE"
GUIDE_SCALE = 1.5

COLOR_CENTER = 17  # yellow
COLOR_LEFT   = 18  # cyan
COLOR_RIGHT  = 20  # pink


# Default raptor guide positions. +Z faces forward, Y up, ground at Y=0.
# Mirrors the proportions baked into BirdRig.DEFAULT_POSITIONS.
DEFAULT_GUIDES = {
    # ---- Core ----
    "C_bird_cog":             {"pos": (0, 75,   0), "color": COLOR_CENTER},

    # ---- Spine ----
    "C_bird_spineHip":        {"pos": (0, 70, -25), "color": COLOR_CENTER},
    "C_bird_spineChest":      {"pos": (0, 80,  20), "color": COLOR_CENTER},

    # ---- Neck + head ----
    "C_bird_neck":            {"pos": (0,  90, 25), "color": COLOR_CENTER},
    "C_bird_head":            {"pos": (0, 105, 35), "color": COLOR_CENTER},
    "C_bird_headTip":         {"pos": (0, 105, 55), "color": COLOR_CENTER},

    # ---- Face (lower beak, eyes, tongue) ----
    "C_bird_jaw":             {"pos": ( 0, 100, 40), "color": COLOR_CENTER},
    "C_bird_jawTip":          {"pos": ( 0,  97, 52), "color": COLOR_CENTER},
    "L_bird_eye":             {"pos": ( 5, 108, 38), "color": COLOR_LEFT},
    "R_bird_eye":             {"pos": (-5, 108, 38), "color": COLOR_RIGHT},
    "C_bird_eyesLookAt":      {"pos": ( 0, 108, 80), "color": COLOR_CENTER},
    "C_bird_tongue01":        {"pos": ( 0,  99, 42), "color": COLOR_CENTER},
    "C_bird_tongue02":        {"pos": ( 0,  99, 46), "color": COLOR_CENTER},
    "C_bird_tongue03":        {"pos": ( 0,  99, 50), "color": COLOR_CENTER},

    # ---- Left wing ----
    # The elbow is intentionally lifted (Y+6) and offset forward from
    # the shoulder-wrist line so the RP IK has a clear bend plane.
    # If the elbow sits ON the line the IK can't decide bend direction
    # and the elbow joint refuses to follow the IK ctrl ("sticks").
    "L_wing_shoulder":        {"pos": (  6, 80,  10), "color": COLOR_LEFT},
    "L_wing_elbow":           {"pos": ( 50, 86,   3), "color": COLOR_LEFT},
    "L_wing_wrist":           {"pos": ( 90, 82,  -5), "color": COLOR_LEFT},
    "L_wing_wingTip":         {"pos": (125, 80, -12), "color": COLOR_LEFT},

    # ---- Right wing ----
    "R_wing_shoulder":        {"pos": (  -6, 80,  10), "color": COLOR_RIGHT},
    "R_wing_elbow":           {"pos": ( -50, 86,   3), "color": COLOR_RIGHT},
    "R_wing_wrist":           {"pos": ( -90, 82,  -5), "color": COLOR_RIGHT},
    "R_wing_wingTip":         {"pos": (-125, 80, -12), "color": COLOR_RIGHT},

    # ---- Left leg (femur / tibiotarsus / tarsometatarsus / foot / footTip) ----
    "L_leg_femur":            {"pos": ( 6, 55, -10), "color": COLOR_LEFT},
    "L_leg_tibiotarsus":      {"pos": ( 8, 35,  -4), "color": COLOR_LEFT},
    "L_leg_tarsometatarsus":  {"pos": (10, 18,  -8), "color": COLOR_LEFT},
    "L_leg_foot":             {"pos": (10,  4,  -4), "color": COLOR_LEFT},
    "L_leg_footTip":          {"pos": (10,  0,   6), "color": COLOR_LEFT},
    # Left toes — base + tip per toe (3 forward + hallux at back)
    "L_leg_digit2_base":      {"pos": (16, 2,  6), "color": COLOR_LEFT},
    "L_leg_digit2_tip":       {"pos": (20, 0, 14), "color": COLOR_LEFT},
    "L_leg_digit3_base":      {"pos": (10, 2,  6), "color": COLOR_LEFT},
    "L_leg_digit3_tip":       {"pos": (10, 0, 18), "color": COLOR_LEFT},
    "L_leg_digit4_base":      {"pos": ( 4, 2,  6), "color": COLOR_LEFT},
    "L_leg_digit4_tip":       {"pos": ( 0, 0, 14), "color": COLOR_LEFT},
    "L_leg_hallux_base":      {"pos": (10, 2, -2), "color": COLOR_LEFT},
    "L_leg_hallux_tip":       {"pos": (10, 0, -8), "color": COLOR_LEFT},

    # ---- Right leg ----
    "R_leg_femur":            {"pos": ( -6, 55, -10), "color": COLOR_RIGHT},
    "R_leg_tibiotarsus":      {"pos": ( -8, 35,  -4), "color": COLOR_RIGHT},
    "R_leg_tarsometatarsus":  {"pos": (-10, 18,  -8), "color": COLOR_RIGHT},
    "R_leg_foot":             {"pos": (-10,  4,  -4), "color": COLOR_RIGHT},
    "R_leg_footTip":          {"pos": (-10,  0,   6), "color": COLOR_RIGHT},
    "R_leg_digit2_base":      {"pos": (-16, 2,  6), "color": COLOR_RIGHT},
    "R_leg_digit2_tip":       {"pos": (-20, 0, 14), "color": COLOR_RIGHT},
    "R_leg_digit3_base":      {"pos": (-10, 2,  6), "color": COLOR_RIGHT},
    "R_leg_digit3_tip":       {"pos": (-10, 0, 18), "color": COLOR_RIGHT},
    "R_leg_digit4_base":      {"pos": ( -4, 2,  6), "color": COLOR_RIGHT},
    "R_leg_digit4_tip":       {"pos": (  0, 0, 14), "color": COLOR_RIGHT},
    "R_leg_hallux_base":      {"pos": (-10, 2, -2), "color": COLOR_RIGHT},
    "R_leg_hallux_tip":       {"pos": (-10, 0, -8), "color": COLOR_RIGHT},

    # ---- Tail (base + central fan-tip) ----
    "C_bird_tailBase":        {"pos": (0, 65, -35), "color": COLOR_CENTER},
    "C_bird_tailFanTip":      {"pos": (0, 55, -70), "color": COLOR_CENTER},
}


TOE_NAMES = ("digit2", "digit3", "digit4", "hallux")


class BirdGuideSystem(object):

    def __init__(self):
        self.guides_grp = None
        self.guides = {}    # name -> locator transform

    # -----------------------------------------------------------------------

    def exists(self):
        return cmds.objExists(GUIDES_GRP_NAME)

    def fit_to_meshes(self, meshes):
        """Size the guides to the selected model (rig_guides
        .fit_group_to_meshes)."""
        import rig_guides
        return rig_guides.fit_group_to_meshes(GUIDES_GRP_NAME, meshes)

    def build(self):
        if self.exists():
            cmds.warning(f"{GUIDES_GRP_NAME} already exists. "
                         f"Delete it first or call reset_to_defaults().")
            self._refresh_handles()
            return
        self.guides_grp = cmds.group(em=True, n=GUIDES_GRP_NAME)
        for name, info in DEFAULT_GUIDES.items():
            self._create_guide(name, info["pos"], info["color"])
        cmds.select(cl=True)
        print(f"[BirdGuideSystem] Created {len(DEFAULT_GUIDES)} bird "
              f"guides under {GUIDES_GRP_NAME}.")

    def delete(self):
        if self.exists():
            cmds.delete(GUIDES_GRP_NAME)
            self.guides = {}
            print(f"[BirdGuideSystem] Deleted {GUIDES_GRP_NAME}.")

    def reset_to_defaults(self):
        if not self.exists():
            cmds.warning("No bird guides to reset.")
            return
        self._refresh_handles()
        for name, info in DEFAULT_GUIDES.items():
            loc = self.guides.get(name)
            if loc and cmds.objExists(loc):
                cmds.xform(loc, ws=True, t=info["pos"])
        print("[BirdGuideSystem] Reset all guides to raptor defaults.")

    # -----------------------------------------------------------------------

    def mirror_left_to_right(self):
        """Copy every L_* guide's world position to R_* with X negated."""
        if not self.exists():
            cmds.warning("No bird guides to mirror.")
            return
        self._refresh_handles()
        mirrored = 0
        for name, loc in self.guides.items():
            if not name.startswith("L_"):
                continue
            r_name = "R_" + name[2:]
            r_loc = self.guides.get(r_name)
            if not r_loc or not cmds.objExists(r_loc):
                continue
            x, y, z = cmds.xform(loc, q=True, ws=True, t=True)
            cmds.xform(r_loc, ws=True, t=(-x, y, z))
            mirrored += 1
        print(f"[BirdGuideSystem] Mirrored {mirrored} L -> R guides.")

    # -----------------------------------------------------------------------

    def read_positions(self):
        """Return a positions dict matching BirdRig's schema."""
        if not self.exists():
            cmds.error(f"{GUIDES_GRP_NAME} does not exist. "
                       f"Run BirdGuideSystem().build() first.")
            return {}
        self._refresh_handles()
        p = self._pos

        def leg(side):
            out = {
                "femur":           p(f"{side}_leg_femur"),
                "tibiotarsus":     p(f"{side}_leg_tibiotarsus"),
                "tarsometatarsus": p(f"{side}_leg_tarsometatarsus"),
                "foot":            p(f"{side}_leg_foot"),
                "footTip":         p(f"{side}_leg_footTip"),
            }
            for toe in TOE_NAMES:
                out[toe] = {
                    "base": p(f"{side}_leg_{toe}_base"),
                    "tip":  p(f"{side}_leg_{toe}_tip"),
                }
            return out

        def wing(side):
            return {
                "shoulder": p(f"{side}_wing_shoulder"),
                "elbow":    p(f"{side}_wing_elbow"),
                "wrist":    p(f"{side}_wing_wrist"),
                "wingTip":  p(f"{side}_wing_wingTip"),
            }

        return {
            "core":  {"cog": p("C_bird_cog")},
            "spine": {
                "hip":   p("C_bird_spineHip"),
                "chest": p("C_bird_spineChest"),
            },
            "neck": {
                "neck":     p("C_bird_neck"),
                "head":     p("C_bird_head"),
                "head_tip": p("C_bird_headTip"),
            },
            "face": {
                "jaw":        p("C_bird_jaw"),
                "jawTip":     p("C_bird_jawTip"),
                "L_eye":      p("L_bird_eye"),
                "R_eye":      p("R_bird_eye"),
                "eyesLookAt": p("C_bird_eyesLookAt"),
                "tongue01":   p("C_bird_tongue01"),
                "tongue02":   p("C_bird_tongue02"),
                "tongue03":   p("C_bird_tongue03"),
            },
            "L_wing": wing("L"),
            "R_wing": wing("R"),
            "L_leg":  leg("L"),
            "R_leg":  leg("R"),
            "tail": {
                "base":   p("C_bird_tailBase"),
                "fanTip": p("C_bird_tailFanTip"),
            },
        }

    # -----------------------------------------------------------------------

    def save_to_json(self, filepath):
        if not self.exists():
            cmds.error("No bird guides to save.")
            return
        self._refresh_handles()
        data = {name: list(self._pos(name)) for name in self.guides
                 if cmds.objExists(self.guides[name])}
        with open(filepath, "w") as f:
            json.dump(data, f, indent=2)
        print(f"[BirdGuideSystem] Saved {len(data)} guide positions to "
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
        print(f"[BirdGuideSystem] Loaded {len(data)} guide positions "
              f"from {filepath}")

    # -----------------------------------------------------------------------
    # Internals
    # -----------------------------------------------------------------------

    def _create_guide(self, name, pos, color):
        loc_name = name + GUIDE_SUFFIX
        loc = cmds.spaceLocator(n=loc_name)[0]
        for axis in ("X", "Y", "Z"):
            cmds.setAttr(f"{loc}Shape.localScale{axis}", GUIDE_SCALE)
        cmds.xform(loc, ws=True, t=pos)
        cmds.setAttr(f"{loc}Shape.overrideEnabled", 1)
        cmds.setAttr(f"{loc}Shape.overrideColor", color)
        for a in ("rx", "ry", "rz", "sx", "sy", "sz"):
            cmds.setAttr(f"{loc}.{a}", l=True, k=False, cb=False)
        cmds.parent(loc, self.guides_grp)
        self.guides[name] = loc

    def _refresh_handles(self):
        self.guides = {}
        for name in DEFAULT_GUIDES:
            loc = name + GUIDE_SUFFIX
            if cmds.objExists(loc):
                self.guides[name] = loc

    def _pos(self, name):
        loc = self.guides.get(name)
        if not loc or not cmds.objExists(loc):
            cmds.error(f"Missing bird guide locator: "
                       f"{name}{GUIDE_SUFFIX}")
        return tuple(cmds.xform(loc, q=True, ws=True, t=True))
