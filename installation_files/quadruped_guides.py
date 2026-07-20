"""
===============================================================================
 QUADRUPED GUIDES — locator placement for quadruped_rig_builder (horse)
===============================================================================

 Workflow:
   1. QuadGuideSystem().build()  -> spawns colored locators at horse defaults
   2. Drag locators to fit the character mesh
   3. QuadGuideSystem().mirror_left_to_right()  -> copy L legs to R
   4. positions = QuadGuideSystem().read_positions()
   5. QuadrupedRig(positions=positions).build()

 Save / load:
   QuadGuideSystem().save_to_json("C:/path/horse.json")
   QuadGuideSystem().load_from_json("C:/path/horse.json")
===============================================================================
"""

import json
import maya.cmds as cmds


GUIDES_GRP_NAME = "QUAD_RIG_GUIDES_GRP"
GUIDE_SUFFIX = "_GUIDE"
GUIDE_SCALE = 2.0

COLOR_CENTER = 17  # yellow
COLOR_LEFT   = 18  # cyan
COLOR_RIGHT  = 20  # pink

# Default horse guide positions. +Z faces forward, Y up, ground at Y=0.
# Mirrors the proportions baked into QuadrupedRig.DEFAULT_POSITIONS.
DEFAULT_GUIDES = {
    # ---- Core ----
    "C_quad_cog":         {"pos": (0, 110,   0), "color": COLOR_CENTER},

    # ---- Spine (croup ↔ withers) ----
    "C_quad_spineHip":    {"pos": (0, 150, -60), "color": COLOR_CENTER},
    "C_quad_spineChest":  {"pos": (0, 158,  60), "color": COLOR_CENTER},

    # ---- Neck + head ----
    "C_quad_neck":        {"pos": (0, 160,  68), "color": COLOR_CENTER},
    "C_quad_head":        {"pos": (0, 195, 100), "color": COLOR_CENTER},
    "C_quad_headTip":     {"pos": (0, 172, 128), "color": COLOR_CENTER},

    # ---- Left front leg ----
    "L_front_scapula":    {"pos": (15, 160,  52), "color": COLOR_LEFT},
    "L_front_shoulder":   {"pos": (22, 120,  62), "color": COLOR_LEFT},
    "L_front_elbow":      {"pos": (24,  88,  50), "color": COLOR_LEFT},
    "L_front_knee":       {"pos": (25,  50,  58), "color": COLOR_LEFT},
    "L_front_fetlock":    {"pos": (25,  22,  52), "color": COLOR_LEFT},
    "L_front_hoof":       {"pos": (25,   4,  55), "color": COLOR_LEFT},
    "L_front_hoofTip":    {"pos": (25,   1,  64), "color": COLOR_LEFT},

    # ---- Right front leg ----
    "R_front_scapula":    {"pos": (-15, 160,  52), "color": COLOR_RIGHT},
    "R_front_shoulder":   {"pos": (-22, 120,  62), "color": COLOR_RIGHT},
    "R_front_elbow":      {"pos": (-24,  88,  50), "color": COLOR_RIGHT},
    "R_front_knee":       {"pos": (-25,  50,  58), "color": COLOR_RIGHT},
    "R_front_fetlock":    {"pos": (-25,  22,  52), "color": COLOR_RIGHT},
    "R_front_hoof":       {"pos": (-25,   4,  55), "color": COLOR_RIGHT},
    "R_front_hoofTip":    {"pos": (-25,   1,  64), "color": COLOR_RIGHT},

    # ---- Left back leg ----
    "L_back_hip":         {"pos": (16, 150, -54), "color": COLOR_LEFT},
    "L_back_stifle":      {"pos": (24, 100, -36), "color": COLOR_LEFT},
    "L_back_hock":        {"pos": (25,  55, -64), "color": COLOR_LEFT},
    "L_back_fetlock":     {"pos": (25,  24, -52), "color": COLOR_LEFT},
    "L_back_hoof":        {"pos": (25,   4, -54), "color": COLOR_LEFT},
    "L_back_hoofTip":     {"pos": (25,   1, -45), "color": COLOR_LEFT},

    # ---- Right back leg ----
    "R_back_hip":         {"pos": (-16, 150, -54), "color": COLOR_RIGHT},
    "R_back_stifle":      {"pos": (-24, 100, -36), "color": COLOR_RIGHT},
    "R_back_hock":        {"pos": (-25,  55, -64), "color": COLOR_RIGHT},
    "R_back_fetlock":     {"pos": (-25,  24, -52), "color": COLOR_RIGHT},
    "R_back_hoof":        {"pos": (-25,   4, -54), "color": COLOR_RIGHT},
    "R_back_hoofTip":     {"pos": (-25,   1, -45), "color": COLOR_RIGHT},

    # ---- Tail (7 segments + tip) ----
    "C_quad_tail_01":     {"pos": (0, 148,  -66), "color": COLOR_CENTER},
    "C_quad_tail_02":     {"pos": (0, 142,  -76), "color": COLOR_CENTER},
    "C_quad_tail_03":     {"pos": (0, 134,  -86), "color": COLOR_CENTER},
    "C_quad_tail_04":     {"pos": (0, 124,  -95), "color": COLOR_CENTER},
    "C_quad_tail_05":     {"pos": (0, 113, -103), "color": COLOR_CENTER},
    "C_quad_tail_06":     {"pos": (0, 102, -110), "color": COLOR_CENTER},
    "C_quad_tail_07":     {"pos": (0,  92, -116), "color": COLOR_CENTER},
    "C_quad_tailTip":     {"pos": (0,  84, -121), "color": COLOR_CENTER},

    # ---- Face (jaw / mouth, tongue, eyes, ears) ----
    "C_quad_jaw":         {"pos": ( 0, 184, 106), "color": COLOR_CENTER},
    "C_quad_jawTip":      {"pos": ( 0, 166, 125), "color": COLOR_CENTER},
    "L_quad_eye":         {"pos": ( 9, 187, 110), "color": COLOR_LEFT},
    "R_quad_eye":         {"pos": (-9, 187, 110), "color": COLOR_RIGHT},
    "C_quad_eyesLookAt":  {"pos": ( 0, 187, 165), "color": COLOR_CENTER},
    "C_quad_tongue01":    {"pos": ( 0, 175, 112), "color": COLOR_CENTER},
    "C_quad_tongue02":    {"pos": ( 0, 173, 118), "color": COLOR_CENTER},
    "C_quad_tongue03":    {"pos": ( 0, 171, 124), "color": COLOR_CENTER},
    "L_quad_ear":         {"pos": ( 7, 205,  96), "color": COLOR_LEFT},
    "R_quad_ear":         {"pos": (-7, 205,  96), "color": COLOR_RIGHT},
}


class QuadGuideSystem(object):

    def __init__(self):
        self.guides_grp = None
        self.guides = {}    # name -> locator transform

    # -----------------------------------------------------------------------

    def exists(self):
        return cmds.objExists(GUIDES_GRP_NAME)

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
        print(f"[QuadGuideSystem] Created {len(DEFAULT_GUIDES)} horse "
              f"guides under {GUIDES_GRP_NAME}.")

    def delete(self):
        if self.exists():
            cmds.delete(GUIDES_GRP_NAME)
            self.guides = {}
            print(f"[QuadGuideSystem] Deleted {GUIDES_GRP_NAME}.")

    def reset_to_defaults(self):
        if not self.exists():
            cmds.warning("No quadruped guides to reset.")
            return
        self._refresh_handles()
        for name, info in DEFAULT_GUIDES.items():
            loc = self.guides.get(name)
            if loc and cmds.objExists(loc):
                cmds.xform(loc, ws=True, t=info["pos"])
        print("[QuadGuideSystem] Reset all guides to horse defaults.")

    # -----------------------------------------------------------------------

    def mirror_left_to_right(self):
        """Copy every L_* guide's world position to R_* with X negated."""
        if not self.exists():
            cmds.warning("No quadruped guides to mirror.")
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
        print(f"[QuadGuideSystem] Mirrored {mirrored} L -> R guides.")

    # -----------------------------------------------------------------------

    def read_positions(self):
        """Return a positions dict matching QuadrupedRig's schema."""
        if not self.exists():
            cmds.error(f"{GUIDES_GRP_NAME} does not exist. "
                       f"Run QuadGuideSystem().build() first.")
            return {}
        self._refresh_handles()
        p = self._pos

        def leg(side, kind):
            # kind = "front" or "back"
            if kind == "front":
                slots = ("scapula", "shoulder", "elbow", "knee",
                         "fetlock", "hoof", "hoofTip")
            else:
                slots = ("hip", "stifle", "hock",
                         "fetlock", "hoof", "hoofTip")
            return {s: p(f"{side}_{kind}_{s}") for s in slots}

        return {
            "core":  {"cog": p("C_quad_cog")},
            "spine": {
                "hip":   p("C_quad_spineHip"),
                "chest": p("C_quad_spineChest"),
            },
            "neck": {
                "neck":     p("C_quad_neck"),
                "head":     p("C_quad_head"),
                "head_tip": p("C_quad_headTip"),
            },
            "L_frontLeg": leg("L", "front"),
            "R_frontLeg": leg("R", "front"),
            "L_backLeg":  leg("L", "back"),
            "R_backLeg":  leg("R", "back"),
            "tail": {
                "tail_01": p("C_quad_tail_01"),
                "tail_02": p("C_quad_tail_02"),
                "tail_03": p("C_quad_tail_03"),
                "tail_04": p("C_quad_tail_04"),
                "tail_05": p("C_quad_tail_05"),
                "tail_06": p("C_quad_tail_06"),
                "tail_07": p("C_quad_tail_07"),
                "tip":     p("C_quad_tailTip"),
            },
            "face": {
                "jaw":        p("C_quad_jaw"),
                "jawTip":     p("C_quad_jawTip"),
                "L_eye":      p("L_quad_eye"),
                "R_eye":      p("R_quad_eye"),
                "eyesLookAt": p("C_quad_eyesLookAt"),
                "tongue01":   p("C_quad_tongue01"),
                "tongue02":   p("C_quad_tongue02"),
                "tongue03":   p("C_quad_tongue03"),
                "L_ear":      p("L_quad_ear"),
                "R_ear":      p("R_quad_ear"),
            },
        }

    # -----------------------------------------------------------------------

    def save_to_json(self, filepath):
        if not self.exists():
            cmds.error("No quadruped guides to save.")
            return
        self._refresh_handles()
        data = {name: list(self._pos(name)) for name in self.guides
                if cmds.objExists(self.guides[name])}
        with open(filepath, "w") as f:
            json.dump(data, f, indent=2)
        print(f"[QuadGuideSystem] Saved {len(data)} guide positions to "
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
        print(f"[QuadGuideSystem] Loaded {len(data)} guide positions "
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
            cmds.error(f"Missing quadruped guide locator: "
                       f"{name}{GUIDE_SUFFIX}")
        return tuple(cmds.xform(loc, q=True, ws=True, t=True))
