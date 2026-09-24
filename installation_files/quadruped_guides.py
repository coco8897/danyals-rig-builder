"""
===============================================================================
 QUADRUPED GUIDES — locator placement for quadruped_rig_builder
===============================================================================

 Three starting animals, each with a foot type per leg pair:

   Animal       front feet   back feet
   Horse        hoof         hoof
   Cat / Dog    paw          paw
   Raptor       arm          claw

   hoof   horse, deer, cow, goat: stands on the tip of the hoof with the
          fetlock raised behind it.
   paw    cat, dog, wolf, lion: stands on the toe pads with the wrist /
          ankle raised. Four toes.
   claw   raptor, dinosaur, big bird-like feet: a long raised ankle bone,
          three toes forward (the inner one carries the raised sickle claw)
          and a small dewclaw.
   arm    front limbs held off the ground (raptor arms), three clawed
          fingers.

 The foot types mix freely (a griffin: claw front, paw back).

 Workflow:
   1. QuadGuideSystem().build("cat")   -> spawns locators at cat defaults
   2. Drag locators to fit the character mesh (moving a paw, ball or wrist
      guide brings its toes / fingers along)
   3. QuadGuideSystem().mirror_left_to_right()  -> copy L legs to R
   4. positions = QuadGuideSystem().read_positions()
   5. QuadrupedRig(positions=positions).build()

 Changing feet on guides you already placed keeps the body, shoulder and
 hip guides and swaps only the leg below them:
   QuadGuideSystem().set_feet(front="paw")

 Save / load (the animal and feet are saved too):
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


# =============================================================================
# Anatomy
# =============================================================================

ANIMALS = ("horse", "cat", "raptor")
ANIMAL_LABELS = {"horse": "Horse", "cat": "Cat / Dog", "raptor": "Raptor"}

FOOT_TYPES = ("hoof", "paw", "claw", "arm")
FOOT_LABELS = {"hoof": "Hoof", "paw": "Paw", "claw": "Claw (raptor)",
               "arm": "Arm (off the ground)"}
FRONT_FEET = ("hoof", "paw", "claw", "arm")
BACK_FEET = ("hoof", "paw", "claw")

# Joints down each limb. The last one is the tip leaf (no control).
LEG_SLOTS = {
    ("front", "hoof"): ("scapula", "shoulder", "elbow", "knee",
                        "fetlock", "hoof", "hoofTip"),
    ("back", "hoof"):  ("hip", "stifle", "hock", "fetlock", "hoof",
                        "hoofTip"),
    ("front", "paw"):  ("scapula", "shoulder", "elbow", "wrist", "paw",
                        "toeTip"),
    ("back", "paw"):   ("hip", "knee", "ankle", "paw", "toeTip"),
    ("front", "claw"): ("scapula", "shoulder", "elbow", "wrist", "ball",
                        "toeTip"),
    ("back", "claw"):  ("hip", "knee", "ankle", "ball", "toeTip"),
    ("front", "arm"):  ("scapula", "shoulder", "elbow", "wrist", "handTip"),
}

# Toes / fingers per foot type, inner to outer. On a claw foot digit1 is the
# dewclaw and digit2 the sickle claw.
DIGITS = {
    "hoof": (),
    "paw":  ("digit2", "digit3", "digit4", "digit5"),
    "claw": ("digit1", "digit2", "digit3", "digit4"),
    "arm":  ("digit1", "digit2", "digit3"),
}
DIGIT_PARTS = ("_01", "_02", "Tip")

# The guides a leg keeps when its foot type changes.
ANCHORS = {"front": ("scapula", "shoulder"), "back": ("hip",)}


def anchor_slot(kind):
    return "shoulder" if kind == "front" else "hip"


def foot_slot(kind, foot):
    """The joint the toes / fingers grow from (paw, ball, wrist, hoof)."""
    return LEG_SLOTS[(kind, foot)][-2]


def limb_prefix(side, kind, foot):
    """Rig name prefix: L_frontLeg / L_backLeg, or L_frontArm for arms (so
    Walk Mode swings them like arms instead of stepping on them)."""
    return f"{side}_frontArm" if foot == "arm" else f"{side}_{kind}Leg"


# Leg shapes for generated legs: offsets from the shoulder (front) or hip
# (back) in units of that joint's height above the ground, x outward.
# (A horse template uses its own hand-placed legs instead.)
SHAPES = {
    ("front", "hoof"): {"elbow": (.017, -.267, -.1),
                        "knee": (.025, -.583, -.033),
                        "fetlock": (.025, -.817, -.083),
                        "hoof": (.025, -.967, -.058),
                        "hoofTip": (.025, -.992, .017)},
    ("back", "hoof"):  {"stifle": (.053, -.333, .12),
                        "hock": (.06, -.633, -.067),
                        "fetlock": (.06, -.84, .013),
                        "hoof": (.06, -.973, 0.0),
                        "hoofTip": (.06, -.993, .06)},
    ("front", "paw"):  {"elbow": (.02, -.40, -.10),
                        "wrist": (.02, -.86, -.02),
                        "paw": (.02, -.955, .05),
                        "toeTip": (.02, -.995, .16)},
    ("back", "paw"):   {"knee": (.04, -.36, .16),
                        "ankle": (.05, -.70, -.12),
                        "paw": (.05, -.955, 0.0),
                        "toeTip": (.05, -.995, .11)},
    ("front", "claw"): {"elbow": (.04, -.40, -.10),
                        "wrist": (.05, -.84, 0.0),
                        "ball": (.05, -.955, .05),
                        "toeTip": (.05, -.985, .21)},
    ("back", "claw"):  {"knee": (.04, -.32, .22),
                        "ankle": (.06, -.74, -.07),
                        "ball": (.06, -.965, .02),
                        "toeTip": (.06, -.995, .22)},
    ("front", "arm"):  {"elbow": (.05, -.20, -.08),
                        "wrist": (.07, -.30, .08),
                        "handTip": (.08, -.33, .19)},
}

# Toe / finger (base, middle, tip) offsets from the foot joint, same units.
_CLAW_DIGITS = {
    "digit1": ((-.02, .06, -.035), (-.026, .04, -.015), (-.03, .022, .005)),
    "digit2": ((-.02, -.012, .015), (-.03, -.02, .07), (-.036, .03, .105)),
    "digit3": ((0.0, -.012, .02), (0.0, -.025, .11), (0.0, -.03, .20)),
    "digit4": ((.02, -.012, .015), (.03, -.025, .09), (.04, -.03, .17)),
}
_PAW_DIGITS = {
    "digit2": ((-.022, -.012, .025), (-.03, -.03, .065), (-.034, -.04, .10)),
    "digit3": ((-.008, -.01, .035), (-.01, -.03, .08), (-.011, -.04, .115)),
    "digit4": ((.008, -.01, .035), (.01, -.03, .08), (.011, -.04, .115)),
    "digit5": ((.022, -.012, .025), (.03, -.03, .065), (.034, -.04, .10)),
}
DIGIT_SHAPES = {
    ("front", "paw"): _PAW_DIGITS,
    ("back", "paw"): _PAW_DIGITS,
    ("front", "claw"): {d: tuple(tuple(v * 0.8 for v in p) for p in pts)
                        for d, pts in _CLAW_DIGITS.items()},
    ("back", "claw"): _CLAW_DIGITS,
    ("front", "arm"): {
        "digit1": ((-.015, -.01, .06), (-.02, -.025, .09),
                   (-.022, -.045, .11)),
        "digit2": ((0.0, -.02, .11), (0.0, -.04, .16), (0.0, -.07, .19)),
        "digit3": ((.012, -.025, .10), (.018, -.045, .14),
                   (.02, -.07, .165)),
    },
}


# =============================================================================
# Starting animals. +Z faces forward, Y up, ground at Y=0. Left side only;
# the right side mirrors.
# =============================================================================

TEMPLATES = {
    "horse": {
        "feet": ("hoof", "hoof"),
        "body": {
            "C_quad_cog":        (0, 110,   0),
            # Spine (croup <-> withers)
            "C_quad_spineHip":   (0, 150, -60),
            "C_quad_spineChest": (0, 158,  60),
            # Neck + head
            "C_quad_neck":       (0, 160,  68),
            "C_quad_head":       (0, 195, 100),
            "C_quad_headTip":    (0, 172, 128),
            # Tail (7 segments + tip)
            "C_quad_tail_01":    (0, 148,  -66),
            "C_quad_tail_02":    (0, 142,  -76),
            "C_quad_tail_03":    (0, 134,  -86),
            "C_quad_tail_04":    (0, 124,  -95),
            "C_quad_tail_05":    (0, 113, -103),
            "C_quad_tail_06":    (0, 102, -110),
            "C_quad_tail_07":    (0,  92, -116),
            "C_quad_tailTip":    (0,  84, -121),
            # Face (jaw / mouth, tongue, eyes, ears)
            "C_quad_jaw":        (0, 184, 106),
            "C_quad_jawTip":     (0, 166, 125),
            "L_quad_eye":        (9, 187, 110),
            "C_quad_eyesLookAt": (0, 187, 165),
            "C_quad_tongue01":   (0, 175, 112),
            "C_quad_tongue02":   (0, 173, 118),
            "C_quad_tongue03":   (0, 171, 124),
            "L_quad_ear":        (7, 205,  96),
        },
        "front": {"scapula": (15, 160, 52), "shoulder": (22, 120, 62)},
        "back": {"hip": (16, 150, -54)},
        "legs": {
            ("front", "hoof"): {
                "scapula":  (15, 160,  52),
                "shoulder": (22, 120,  62),
                "elbow":    (24,  88,  50),
                "knee":     (25,  50,  58),
                "fetlock":  (25,  22,  52),
                "hoof":     (25,   4,  55),
                "hoofTip":  (25,   1,  64),
            },
            ("back", "hoof"): {
                "hip":      (16, 150, -54),
                "stifle":   (24, 100, -36),
                "hock":     (25,  55, -64),
                "fetlock":  (25,  24, -52),
                "hoof":     (25,   4, -54),
                "hoofTip":  (25,   1, -45),
            },
        },
    },

    # A big cat / large dog: long flexible back, long tail.
    "cat": {
        "feet": ("paw", "paw"),
        "body": {
            "C_quad_cog":        (0,  70,   0),
            "C_quad_spineHip":   (0,  85, -45),
            "C_quad_spineChest": (0,  88,  40),
            "C_quad_neck":       (0,  90,  48),
            "C_quad_head":       (0, 108,  72),
            "C_quad_headTip":    (0, 100,  98),
            "C_quad_tail_01":    (0,  84,  -58),
            "C_quad_tail_02":    (0,  82,  -70),
            "C_quad_tail_03":    (0,  79,  -82),
            "C_quad_tail_04":    (0,  75,  -94),
            "C_quad_tail_05":    (0,  70, -105),
            "C_quad_tail_06":    (0,  64, -115),
            "C_quad_tail_07":    (0,  58, -124),
            "C_quad_tailTip":    (0,  53, -132),
            "C_quad_jaw":        (0, 100,  78),
            "C_quad_jawTip":     (0,  92,  96),
            "L_quad_eye":        (6, 107,  90),
            "C_quad_eyesLookAt": (0, 107, 140),
            "C_quad_tongue01":   (0,  95,  82),
            "C_quad_tongue02":   (0,  94,  88),
            "C_quad_tongue03":   (0,  93,  94),
            "L_quad_ear":        (7, 118,  74),
        },
        "front": {"scapula": (10, 92, 38), "shoulder": (13, 68, 46)},
        "back": {"hip": (12, 80, -48)},
        "legs": {},
    },

    # A big dromaeosaur standing on its back legs: level body balanced over
    # the hips, long stiff tail, S-curved neck, folded clawed arms.
    "raptor": {
        "feet": ("arm", "claw"),
        "body": {
            "C_quad_cog":        (0, 112,  -5),
            "C_quad_spineHip":   (0, 118, -15),
            "C_quad_spineChest": (0, 116,  45),
            "C_quad_neck":       (0, 120,  58),
            "C_quad_head":       (0, 150,  88),
            "C_quad_headTip":    (0, 146, 125),
            "C_quad_tail_01":    (0, 116,  -30),
            "C_quad_tail_02":    (0, 115,  -55),
            "C_quad_tail_03":    (0, 113,  -80),
            "C_quad_tail_04":    (0, 110, -105),
            "C_quad_tail_05":    (0, 106, -130),
            "C_quad_tail_06":    (0, 101, -155),
            "C_quad_tail_07":    (0,  96, -180),
            "C_quad_tailTip":    (0,  90, -205),
            "C_quad_jaw":        (0, 145,  95),
            "C_quad_jawTip":     (0, 136, 124),
            "L_quad_eye":        (7, 154, 100),
            "C_quad_eyesLookAt": (0, 154, 170),
            "C_quad_tongue01":   (0, 141, 102),
            "C_quad_tongue02":   (0, 140, 110),
            "C_quad_tongue03":   (0, 139, 118),
            "L_quad_ear":        (7, 150,  92),
        },
        "front": {"scapula": (8, 124, 42), "shoulder": (12, 105, 55)},
        "back": {"hip": (12, 112, -12)},
        "legs": {},
    },
}


def template_feet(animal):
    front, back = TEMPLATES[animal]["feet"]
    return {"front": front, "back": back}


def _check_feet(front, back):
    if front not in FRONT_FEET:
        raise ValueError(f"Front feet must be one of {FRONT_FEET}, "
                         f"not {front!r}.")
    if back not in BACK_FEET:
        raise ValueError(f"Back feet must be one of {BACK_FEET}, "
                         f"not {back!r}.")


def leg_positions(kind, foot, anchors, animal=None):
    """Left-side world positions of one leg: {slot: pos, "digits": {digit:
    [base, mid, tip]}}. `anchors` holds the shoulder (+ scapula) or hip.

    An animal's own hand-placed leg is used when its anchors haven't moved,
    otherwise the leg is grown down from the anchor with SHAPES."""
    anchors = {k: tuple(v) for k, v in anchors.items()}
    tmpl = TEMPLATES.get(animal) if animal else None
    explicit = tmpl["legs"].get((kind, foot)) if tmpl else None
    if explicit and all(
            max(abs(a - b) for a, b in zip(anchors[s], explicit[s])) < 1e-3
            for s in anchors if s in explicit):
        pos = {k: tuple(v) for k, v in explicit.items()}
        pos.update(anchors)
    else:
        a = anchors[anchor_slot(kind)]
        h = a[1] if a[1] > 1e-3 else 100.0
        out = -1.0 if a[0] < 0 else 1.0
        pos = dict(anchors)
        for slot, (dx, dy, dz) in SHAPES[(kind, foot)].items():
            pos[slot] = (a[0] + out * dx * h, a[1] + dy * h, a[2] + dz * h)
    shapes = DIGIT_SHAPES.get((kind, foot))
    if shapes:
        a = anchors[anchor_slot(kind)]
        h = a[1] if a[1] > 1e-3 else 100.0
        out = -1.0 if a[0] < 0 else 1.0
        f = pos[foot_slot(kind, foot)]
        pos["digits"] = {
            d: [(f[0] + out * dx * h, f[1] + dy * h, f[2] + dz * h)
                for dx, dy, dz in shapes[d]]
            for d in DIGITS[foot] if d in shapes}
    return pos


def _mirror(p):
    return (-p[0], p[1], p[2])


def _mirror_leg(leg):
    out = {k: _mirror(v) for k, v in leg.items() if k != "digits"}
    if "digits" in leg:
        out["digits"] = {d: [_mirror(p) for p in pts]
                         for d, pts in leg["digits"].items()}
    return out


def default_positions(animal="horse", front=None, back=None):
    """The full positions dict QuadrupedRig builds from, for an animal (and
    optional foot types), without any guides in the scene."""
    tmpl = TEMPLATES[animal]
    feet = template_feet(animal)
    feet["front"] = front or feet["front"]
    feet["back"] = back or feet["back"]
    _check_feet(feet["front"], feet["back"])
    body = dict(tmpl["body"])
    for name in list(body):
        if name.startswith("L_"):
            body["R_" + name[2:]] = _mirror(body[name])
    legs = {}
    for kind in ("front", "back"):
        leg = leg_positions(kind, feet[kind], tmpl[kind], animal)
        legs[f"L_{kind}Leg"] = leg
        legs[f"R_{kind}Leg"] = _mirror_leg(leg)
    return _positions_dict(body.__getitem__, legs, feet, animal)


def _positions_dict(p, legs, feet, animal):
    """Assemble QuadrupedRig's schema from a body-guide lookup + legs."""
    pos = {
        "core":  {"cog": p("C_quad_cog")},
        "spine": {"hip": p("C_quad_spineHip"),
                  "chest": p("C_quad_spineChest")},
        "neck": {"neck": p("C_quad_neck"), "head": p("C_quad_head"),
                 "head_tip": p("C_quad_headTip")},
        "tail": {**{"tail_%02d" % i: p("C_quad_tail_%02d" % i)
                    for i in range(1, 8)},
                 "tip": p("C_quad_tailTip")},
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
        "feet": dict(feet),
        "animal": animal,
    }
    pos.update(legs)
    return pos


def _color(name):
    return {"L": COLOR_LEFT, "R": COLOR_RIGHT}.get(name[0], COLOR_CENTER)


def _leg_guides(side, kind, foot, leg):
    """(guide name, world position, parent guide or None) for one leg, each
    parent before its children. Toes / fingers and the tip hang under the
    foot joint's guide so the foot moves as one piece."""
    slots = LEG_SLOTS[(kind, foot)]
    fj = f"{side}_{kind}_{slots[-2]}"
    nested = bool(DIGITS[foot])
    out = []
    for slot in slots:
        parent = fj if nested and slot == slots[-1] else None
        out.append((f"{side}_{kind}_{slot}", tuple(leg[slot]), parent))
    for d in DIGITS[foot]:
        pts = (leg.get("digits") or {}).get(d)
        if pts:
            for part, p in zip(DIGIT_PARTS, pts):
                out.append((f"{side}_{kind}_{d}{part}", tuple(p), fj))
    return out


def leg_guide_names(side, kind, foot):
    """Every guide name a leg of this foot type has."""
    slots = LEG_SLOTS[(kind, foot)]
    return ([f"{side}_{kind}_{s}" for s in slots]
            + [f"{side}_{kind}_{d}{part}" for d in DIGITS[foot]
               for part in DIGIT_PARTS])


# Kept for older scripts: the horse guides as {name: {"pos", "color"}}.
DEFAULT_GUIDES = {}
for _name, _pos in TEMPLATES["horse"]["body"].items():
    DEFAULT_GUIDES[_name] = {"pos": _pos, "color": _color(_name)}
    if _name.startswith("L_"):
        DEFAULT_GUIDES["R_" + _name[2:]] = {"pos": _mirror(_pos),
                                            "color": COLOR_RIGHT}
for _kind in ("front", "back"):
    _leg = TEMPLATES["horse"]["legs"][(_kind, "hoof")]
    for _side, _l in (("L", _leg), ("R", _mirror_leg(_leg))):
        for _name, _pos, _parent in _leg_guides(_side, _kind, "hoof", _l):
            DEFAULT_GUIDES[_name] = {"pos": _pos, "color": _color(_name)}


# =============================================================================
# Guide locators in the scene
# =============================================================================

class QuadGuideSystem(object):

    def __init__(self):
        self.guides_grp = None
        self.guides = {}    # name -> locator transform

    # -----------------------------------------------------------------------

    def exists(self):
        return cmds.objExists(GUIDES_GRP_NAME)

    def animal(self):
        """The animal the guides were made from ("horse" for old guides)."""
        return self._stored("animal", "horse")

    def feet(self):
        """{"front": foot type, "back": foot type} of the guides."""
        return {"front": self._stored("frontFeet", "hoof"),
                "back": self._stored("backFeet", "hoof")}

    def fit_to_meshes(self, meshes):
        """Size the guides to the selected model (rig_guides
        .fit_group_to_meshes)."""
        import rig_guides
        return rig_guides.fit_group_to_meshes(GUIDES_GRP_NAME, meshes)

    def build(self, animal="horse", front=None, back=None):
        if animal not in TEMPLATES:
            raise ValueError(f"Animal must be one of {ANIMALS}, "
                             f"not {animal!r}.")
        feet = template_feet(animal)
        feet["front"] = front or feet["front"]
        feet["back"] = back or feet["back"]
        _check_feet(feet["front"], feet["back"])
        if self.exists():
            self._refresh_handles()
            if self.animal() == animal and self.feet() != feet:
                self.set_feet(feet["front"], feet["back"])
                return
            cmds.warning(f"{GUIDES_GRP_NAME} already exists. "
                         f"Delete it first or call reset_to_defaults().")
            return
        self.guides_grp = cmds.group(em=True, n=GUIDES_GRP_NAME)
        self.guides = {}
        self._store(animal, feet)
        tmpl = TEMPLATES[animal]
        for name, pos in tmpl["body"].items():
            self._create_guide(name, pos)
            if name.startswith("L_"):
                self._create_guide("R_" + name[2:], _mirror(pos))
        for kind in ("front", "back"):
            leg = leg_positions(kind, feet[kind], tmpl[kind], animal)
            self._create_leg(kind, feet[kind], leg, _mirror_leg(leg))
        cmds.select(cl=True)
        print(f"[QuadGuideSystem] Created {len(self.guides)} "
              f"{ANIMAL_LABELS[animal]} guides ({feet['front']} front, "
              f"{feet['back']} back) under {GUIDES_GRP_NAME}.")

    def set_feet(self, front=None, back=None):
        """Swap the foot type of the front and / or back legs. The body and
        the scapula, shoulder and hip guides stay where they are; the leg
        below them is regrown for the new foot."""
        if not self.exists():
            cmds.warning("No quadruped guides to change.")
            return
        self._refresh_handles()
        cur = self.feet()
        new = {"front": front or cur["front"], "back": back or cur["back"]}
        _check_feet(new["front"], new["back"])
        animal = self.animal()
        for kind in ("front", "back"):
            if new[kind] == cur[kind]:
                continue
            legs = []
            for side in ("L", "R"):
                anchors = {}
                for slot in ANCHORS[kind]:
                    name = f"{side}_{kind}_{slot}"
                    if name in self.guides:
                        p = self._pos(name)
                    else:
                        p = TEMPLATES[animal][kind][slot]
                        p = p if side == "L" else _mirror(p)
                    anchors[slot] = p if side == "L" else _mirror(p)
                leg = leg_positions(kind, new[kind], anchors, animal)
                legs.append(leg if side == "L" else _mirror_leg(leg))
            old = [n for side in ("L", "R")
                   for n in leg_guide_names(side, kind, cur[kind])]
            for n in old:                    # a paw takes its toes along
                if n in self.guides and cmds.objExists(self.guides[n]):
                    cmds.delete(self.guides[n])
            self._refresh_handles()
            self._create_leg(kind, new[kind], legs[0], legs[1])
            print(f"[QuadGuideSystem] {kind.capitalize()} legs are now "
                  f"{new[kind]}.")
        self._store(animal, new)
        cmds.select(cl=True)

    def delete(self):
        if self.exists():
            cmds.delete(GUIDES_GRP_NAME)
            self.guides = {}
            print(f"[QuadGuideSystem] Deleted {GUIDES_GRP_NAME}.")

    def reset_to_defaults(self, animal=None, front=None, back=None):
        """Put every guide back at the animal's defaults. A different animal
        (or feet) rebuilds the guides for it."""
        if not self.exists():
            cmds.warning("No quadruped guides to reset.")
            return
        cur_animal = self.animal()
        animal = animal or cur_animal
        feet = template_feet(animal) if animal != cur_animal else self.feet()
        feet["front"] = front or feet["front"]
        feet["back"] = back or feet["back"]
        if animal != cur_animal or feet != self.feet():
            self.delete()
            self.build(animal, feet["front"], feet["back"])
            return
        self._refresh_handles()
        data = self._default_guide_positions(animal, feet)
        for name in self._by_depth():
            if name in data:
                cmds.xform(self.guides[name], ws=True, t=data[name])
        print(f"[QuadGuideSystem] Reset all guides to "
              f"{ANIMAL_LABELS[animal]} defaults.")

    # -----------------------------------------------------------------------

    def mirror_left_to_right(self):
        """Copy every L_* guide's world position to R_* with X negated."""
        if not self.exists():
            cmds.warning("No quadruped guides to mirror.")
            return
        self._refresh_handles()
        mirrored = 0
        for name in self._by_depth():        # a paw before its toes
            if not name.startswith("L_"):
                continue
            r_loc = self.guides.get("R_" + name[2:])
            if not r_loc or not cmds.objExists(r_loc):
                continue
            x, y, z = cmds.xform(self.guides[name], q=True, ws=True, t=True)
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
        feet = self.feet()

        def leg(side, kind):
            foot = feet[kind]
            out = {s: p(f"{side}_{kind}_{s}") for s in LEG_SLOTS[(kind, foot)]}
            digits = {}
            for d in DIGITS[foot]:
                names = [f"{side}_{kind}_{d}{part}" for part in DIGIT_PARTS]
                if all(n in self.guides for n in names):
                    digits[d] = [p(n) for n in names]
            if digits:
                out["digits"] = digits
            return out

        legs = {f"{side}_{kind}Leg": leg(side, kind)
                for side in ("L", "R") for kind in ("front", "back")}
        return _positions_dict(p, legs, feet, self.animal())

    # -----------------------------------------------------------------------

    def save_to_json(self, filepath):
        if not self.exists():
            cmds.error("No quadruped guides to save.")
            return
        self._refresh_handles()
        data = {"_animal": self.animal(), "_feet": self.feet()}
        data.update({name: list(self._pos(name)) for name in self._by_depth()})
        with open(filepath, "w") as f:
            json.dump(data, f, indent=2)
        print(f"[QuadGuideSystem] Saved {len(data) - 2} guide positions to "
              f"{filepath}")

    def load_from_json(self, filepath):
        with open(filepath, "r") as f:
            data = json.load(f)
        animal = data.pop("_animal", "horse")
        animal = animal if animal in TEMPLATES else "horse"
        feet = data.pop("_feet", None) or {"front": "hoof", "back": "hoof"}
        if not self.exists():
            self.build(animal, feet.get("front"), feet.get("back"))
        else:
            if self.feet() != feet:
                self.set_feet(feet.get("front"), feet.get("back"))
            self._store(animal, self.feet())
        self._refresh_handles()
        for name in self._by_depth():
            if name in data:
                cmds.xform(self.guides[name], ws=True, t=tuple(data[name]))
        print(f"[QuadGuideSystem] Loaded {len(data)} guide positions "
              f"from {filepath}")

    # -----------------------------------------------------------------------
    # Internals
    # -----------------------------------------------------------------------

    def _create_guide(self, name, pos, parent=None):
        loc_name = name + GUIDE_SUFFIX
        loc = cmds.spaceLocator(n=loc_name)[0]
        for axis in ("X", "Y", "Z"):
            cmds.setAttr(f"{loc}Shape.localScale{axis}",
                         GUIDE_SCALE if "digit" not in name
                         else GUIDE_SCALE * 0.4)
        cmds.xform(loc, ws=True, t=pos)
        cmds.setAttr(f"{loc}Shape.overrideEnabled", 1)
        cmds.setAttr(f"{loc}Shape.overrideColor", _color(name))
        for a in ("rx", "ry", "rz", "sx", "sy", "sz"):
            cmds.setAttr(f"{loc}.{a}", l=True, k=False, cb=False)
        parent_loc = self.guides.get(parent) if parent else None
        loc = cmds.parent(loc, parent_loc or GUIDES_GRP_NAME)[0]
        self.guides[name] = loc
        return loc

    def _create_leg(self, kind, foot, left, right):
        for side, leg in (("L", left), ("R", right)):
            for name, pos, parent in _leg_guides(side, kind, foot, leg):
                if name in self.guides and cmds.objExists(self.guides[name]):
                    cmds.xform(self.guides[name], ws=True, t=pos)
                    continue
                self._create_guide(name, pos, parent)

    def _store(self, animal, feet):
        for attr, value in (("animal", animal),
                            ("frontFeet", feet["front"]),
                            ("backFeet", feet["back"])):
            if not cmds.attributeQuery(attr, node=GUIDES_GRP_NAME,
                                       exists=True):
                cmds.addAttr(GUIDES_GRP_NAME, ln=attr, dt="string")
            cmds.setAttr(f"{GUIDES_GRP_NAME}.{attr}", value, type="string")

    def _stored(self, attr, default):
        if (self.exists() and cmds.attributeQuery(
                attr, node=GUIDES_GRP_NAME, exists=True)):
            return cmds.getAttr(f"{GUIDES_GRP_NAME}.{attr}") or default
        return default

    def _default_guide_positions(self, animal, feet):
        tmpl = TEMPLATES[animal]
        data = {}
        for name, pos in tmpl["body"].items():
            data[name] = pos
            if name.startswith("L_"):
                data["R_" + name[2:]] = _mirror(pos)
        for kind in ("front", "back"):
            leg = leg_positions(kind, feet[kind], tmpl[kind], animal)
            for side, l in (("L", leg), ("R", _mirror_leg(leg))):
                for name, pos, _ in _leg_guides(side, kind, feet[kind], l):
                    data[name] = pos
        return data

    def _refresh_handles(self):
        self.guides = {}
        self._depth = {}
        if not self.exists():
            return
        for path in cmds.listRelatives(GUIDES_GRP_NAME, ad=True,
                                       type="transform", fullPath=True) or []:
            short = path.rsplit("|", 1)[-1]
            if short.endswith(GUIDE_SUFFIX):
                name = short[:-len(GUIDE_SUFFIX)]
                self.guides[name] = short
                self._depth[name] = path.count("|")

    def _by_depth(self):
        """Guide names, parents before children."""
        depth = getattr(self, "_depth", {})
        return sorted(self.guides, key=lambda n: depth.get(n, 0))

    def _pos(self, name):
        loc = self.guides.get(name)
        if not loc or not cmds.objExists(loc):
            cmds.error(f"Missing quadruped guide locator: "
                       f"{name}{GUIDE_SUFFIX}")
        return tuple(cmds.xform(loc, q=True, ws=True, t=True))

