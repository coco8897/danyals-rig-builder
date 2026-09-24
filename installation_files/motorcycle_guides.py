"""
===============================================================================
 MOTORCYCLE GUIDES: place seven locators, build the bike
===============================================================================

 A motorcycle is a single-track vehicle: one wheel in front on a fork that
 steers, one wheel behind on a swingarm, and the whole thing leans into a
 corner. Every bike is a different shape, so place these locators on your
 model and build from them.

 Locators (all on the centre line, X = 0):
   C_frame          the bike's centre of mass / root. The frame BIND
                      joint sits here and the animator drives it.
   C_frontWheel     front axle centre. Its circle is the tyre (radius).
   C_backWheel      back axle centre. Its own radius, so a fat rear
                      tyre stays fat.
   C_steeringHead   top of the steering head, where the fork meets the
                      frame. The line from here to the front axle IS the
                      steering axis: its lean back is the bike's rake.
   C_handlebar      the handlebar centre. The steering ctrl is drawn
                      here and the bars turn with the fork.
   C_swingarmPivot  where the swingarm bolts to the frame. The arm
                      swings from here and aims at the rear axle.
   C_shockTop       the rear shock's top mount on the frame. The coil
                      runs from here down to the rear wheel.

 Workflow:
     import motorcycle_guides
     from importlib import reload; reload(motorcycle_guides)
     gs = motorcycle_guides.MotorcycleGuideSystem()
     gs.build()                      # spawn the locators at defaults
     gs.fit_to_meshes(cmds.ls(sl=True))   # or drag them yourself
     import motorcycle_rig_builder
     motorcycle_rig_builder.MotorcycleRig(
         positions=gs.read_positions()).build()
===============================================================================
"""

import json
import maya.cmds as cmds

import vehicle_guides
import rig_guides


GUIDES_GRP_NAME = "MOTORCYCLE_RIG_GUIDES_GRP"
GUIDE_SUFFIX = "_GUIDE"
GUIDE_SCALE = 5.0

COLOR_CENTER = 17   # yellow
COLOR_WHEEL  = 18   # cyan
COLOR_MOVING = 21   # orange: the parts that steer / swing
RADIUS_ATTR = vehicle_guides.RADIUS_ATTR

WHEEL_GUIDES = ("C_frontWheel", "C_backWheel")

# Order = the order they are created (and listed in the panel).
DEFAULT_GUIDES = (
    ("C_frame", (0, 70, 0), COLOR_CENTER,
     "The bike's centre / root. Everything hangs off here and the "
     "animator drives the bike from this control."),
    ("C_frontWheel", (0, 32, 72), COLOR_WHEEL,
     "FRONT axle centre. The circle is the tyre: set its Radius in the "
     "channel box so the wheel rolls at the right rate."),
    ("C_backWheel", (0, 32, -63), COLOR_WHEEL,
     "BACK axle centre, with its own Radius (rear tyres are usually "
     "fatter)."),
    ("C_steeringHead", (0, 100, 45), COLOR_MOVING,
     "Top of the STEERING HEAD, where the fork meets the frame. The "
     "line from here down to the front axle is the steering axis, so "
     "its lean back sets the bike's rake."),
    ("C_handlebar", (0, 112, 34), COLOR_MOVING,
     "HANDLEBAR centre. The steering control is drawn here; turning it "
     "turns the fork, the front wheel and the bars."),
    ("C_swingarmPivot", (0, 52, -6), COLOR_MOVING,
     "SWINGARM pivot on the frame. The arm swings from here and always "
     "aims at the rear axle."),
    ("C_shockTop", (0, 90, -26), COLOR_MOVING,
     "Rear SHOCK top mount on the frame. The coil runs from here down "
     "to the rear wheel and compresses as it travels."),
)

DEFAULT_RADIUS = {"C_frontWheel": 32.0, "C_backWheel": 32.0}


def guide_names():
    return [g[0] for g in DEFAULT_GUIDES]


def has_guides():
    return cmds.objExists(GUIDES_GRP_NAME)


class MotorcycleGuideSystem(object):
    """The seven locators a motorcycle rig is built from."""

    def __init__(self):
        self.guides_grp = None
        self.guides = {}
        self._refresh_handles()

    # -----------------------------------------------------------------------

    def exists(self):
        return cmds.objExists(GUIDES_GRP_NAME)

    def build(self):
        if self.exists():
            self._refresh_handles()
            cmds.warning(f"{GUIDES_GRP_NAME} already exists. Delete it "
                         f"first or call reset_to_defaults().")
            return
        self.guides_grp = cmds.group(em=True, n=GUIDES_GRP_NAME)
        cmds.setAttr(f"{self.guides_grp}.useOutlinerColor", 1)
        cmds.setAttr(f"{self.guides_grp}.outlinerColor", 1.0, 0.7, 0.3)
        for name, pos, color, note in DEFAULT_GUIDES:
            self._create_guide(name, pos, color, note)
        cmds.select(cl=True)
        print(f"[MotorcycleGuideSystem] Created {len(DEFAULT_GUIDES)} "
              f"guides. Place them on your bike, then build.")

    def delete(self):
        if self.exists():
            cmds.delete(GUIDES_GRP_NAME)
            self.guides = {}
            print(f"[MotorcycleGuideSystem] Deleted {GUIDES_GRP_NAME}.")

    def reset_to_defaults(self):
        if not self.exists():
            cmds.warning("No motorcycle guides to reset.")
            return
        self._refresh_handles()
        for a in ("tx", "ty", "tz", "rx", "ry", "rz", "sx", "sy", "sz"):
            try:
                cmds.setAttr(f"{GUIDES_GRP_NAME}.{a}",
                             0.0 if a[0] != "s" else 1.0)
            except Exception:
                pass
        for name, pos, _color, _note in DEFAULT_GUIDES:
            loc = self.guides.get(name)
            if loc and cmds.objExists(loc):
                cmds.xform(loc, ws=True, t=pos)
                if name in DEFAULT_RADIUS:
                    self.set_radius(name, DEFAULT_RADIUS[name])
        print("[MotorcycleGuideSystem] Reset all guides to defaults.")

    # -----------------------------------------------------------------------
    # Wheels
    # -----------------------------------------------------------------------

    def _world_scale(self, name):
        """How much the guide group scales this guide in the world: its
        circle is radius x this."""
        m = cmds.xform(self.guides[name], q=True, ws=True, m=True)
        return max(1e-6, (m[0] ** 2 + m[1] ** 2 + m[2] ** 2) ** 0.5)

    def radius(self, name):
        """A wheel guide's tyre radius in WORLD units."""
        self._refresh_handles()
        loc = self.guides.get(name)
        if not loc or not cmds.attributeQuery(RADIUS_ATTR, node=loc,
                                              exists=True):
            return DEFAULT_RADIUS.get(name, 32.0)
        return cmds.getAttr(f"{loc}.{RADIUS_ATTR}") * self._world_scale(name)

    def set_radius(self, name, radius):
        """Set a wheel guide's tyre radius (world units)."""
        self._refresh_handles()
        loc = self.guides.get(name)
        if not loc:
            return
        vehicle_guides.add_tyre_circle(loc, 1.0, COLOR_WHEEL)
        cmds.setAttr(f"{loc}.{RADIUS_ATTR}",
                     max(1e-3, float(radius) / self._world_scale(name)))

    # -----------------------------------------------------------------------
    # Fitting to a model
    # -----------------------------------------------------------------------

    def fit_wheels(self, meshes):
        """Snap the guides to the wheels found in some meshes: the two
        round shells become the front and back wheel (position AND
        radius), and every other guide moves with them, keeping its place
        on the bike. Returns 2 when it fitted them, 0 when it couldn't
        find two wheels."""
        if not self.exists():
            self.build()
        self._refresh_handles()
        found = vehicle_guides.wheel_centres(meshes)
        if len(found) < 2:
            return 0
        # The two extremes front to back are the wheels; anything the
        # selection picked up between them (the frame, a tank, a fairing)
        # sits between the axles and is passed over.
        found.sort(key=lambda w: w[2])          # back to front by Z
        back, front = found[0], found[-1]
        old_f = self._pos("C_frontWheel")
        old_b = self._pos("C_backWheel")
        new_f = (0.0, front[1], front[2])
        new_b = (0.0, back[1], back[2])
        old_span = max(1e-6, abs(old_f[2] - old_b[2]))
        scale = abs(new_f[2] - new_b[2]) / old_span
        old_mid = [(a + b) * 0.5 for a, b in zip(old_f, old_b)]
        new_mid = [(a + b) * 0.5 for a, b in zip(new_f, new_b)]
        # Everything that isn't a wheel keeps its place on the bike: the
        # same offset from the middle of the wheelbase, scaled by how
        # much the wheelbase changed.
        for name in self.guides:
            if name in WHEEL_GUIDES:
                continue
            p = self._pos(name)
            cmds.xform(self.guides[name], ws=True, t=(
                0.0,
                new_mid[1] + (p[1] - old_mid[1]) * scale,
                new_mid[2] + (p[2] - old_mid[2]) * scale))
        cmds.xform(self.guides["C_frontWheel"], ws=True, t=new_f)
        cmds.xform(self.guides["C_backWheel"], ws=True, t=new_b)
        self.set_radius("C_frontWheel", front[3])
        self.set_radius("C_backWheel", back[3])
        print("[MotorcycleGuideSystem] Fitted the guides to the front and "
              "back wheels (wheelbase %.1f, of %d round pieces found)."
              % (abs(new_f[2] - new_b[2]), len(found)))
        return 2

    def fit_to_meshes(self, meshes):
        """Fit the guides to a bike model: size the whole set to the
        model's box, then snap the wheels if they can be found. Returns
        True if anything was fitted."""
        if not meshes:
            return False
        if not self.exists():
            self.build()
        self._refresh_handles()
        box = rig_guides.mesh_box(meshes)
        if not box:
            return False
        rig_guides.fit_group_to_meshes(GUIDES_GRP_NAME, meshes)
        self.fit_wheels(meshes)
        return True

    # -----------------------------------------------------------------------
    # Reading
    # -----------------------------------------------------------------------

    def read_positions(self):
        """A MotorcycleRig positions dict from the locators."""
        if not self.exists():
            cmds.error(f"{GUIDES_GRP_NAME} does not exist. Run "
                       f"MotorcycleGuideSystem().build() first.")
            return {}
        self._refresh_handles()
        p = self._pos
        return {
            "chassis":       p("C_frame"),
            "frontWheel":    p("C_frontWheel"),
            "backWheel":     p("C_backWheel"),
            "frontRadius":   self.radius("C_frontWheel"),
            "backRadius":    self.radius("C_backWheel"),
            "wheelRadius":   self.radius("C_frontWheel"),
            "steeringHead":  p("C_steeringHead"),
            "handlebar":     p("C_handlebar"),
            "swingarmPivot": p("C_swingarmPivot"),
            "shockTop":      p("C_shockTop"),
        }

    # -----------------------------------------------------------------------
    # Save / load
    # -----------------------------------------------------------------------

    def save_to_json(self, filepath):
        if not self.exists():
            cmds.error("No motorcycle guides to save.")
            return
        self._refresh_handles()
        data = {name: list(self._pos(name)) for name in self.guides}
        for name in WHEEL_GUIDES:
            if name in self.guides:
                data[name + ".radius"] = self.radius(name)
        with open(filepath, "w") as f:
            json.dump(data, f, indent=2)
        print(f"[MotorcycleGuideSystem] Saved guides to {filepath}")
        return filepath

    def load_from_json(self, filepath):
        with open(filepath, "r") as f:
            data = json.load(f)
        if not self.exists():
            self.build()
        self._refresh_handles()
        for name, value in data.items():
            if name.endswith(".radius"):
                continue
            loc = self.guides.get(name)
            if loc and cmds.objExists(loc):
                cmds.xform(loc, ws=True, t=tuple(value))
        for name in WHEEL_GUIDES:
            if name + ".radius" in data and name in self.guides:
                self.set_radius(name, data[name + ".radius"])
        print(f"[MotorcycleGuideSystem] Loaded guides from {filepath}")
        return filepath

    # -----------------------------------------------------------------------

    def _create_guide(self, name, pos, color, note=""):
        loc = cmds.spaceLocator(n=name + GUIDE_SUFFIX)[0]
        for axis in ("X", "Y", "Z"):
            cmds.setAttr(f"{loc}Shape.localScale{axis}", GUIDE_SCALE)
        cmds.xform(loc, ws=True, t=pos)
        cmds.setAttr(f"{loc}Shape.overrideEnabled", 1)
        cmds.setAttr(f"{loc}Shape.overrideColor", color)
        if note:
            cmds.addAttr(loc, ln="notes", dt="string")
            cmds.setAttr(f"{loc}.notes", note, type="string")
        for a in ("rx", "ry", "rz", "sx", "sy", "sz"):
            cmds.setAttr(f"{loc}.{a}", l=True, k=False, cb=False)
        cmds.parent(loc, self.guides_grp)
        self.guides[name] = loc
        if name in DEFAULT_RADIUS:
            vehicle_guides.add_tyre_circle(loc, DEFAULT_RADIUS[name],
                                           COLOR_WHEEL)

    def _refresh_handles(self):
        if cmds.objExists(GUIDES_GRP_NAME):
            self.guides_grp = GUIDES_GRP_NAME
        self.guides = {}
        for name in guide_names():
            loc = name + GUIDE_SUFFIX
            if cmds.objExists(loc):
                self.guides[name] = loc

    def _pos(self, name):
        loc = self.guides.get(name)
        if not loc or not cmds.objExists(loc):
            cmds.error(f"Missing motorcycle guide locator: "
                       f"{name}{GUIDE_SUFFIX}")
        return tuple(cmds.xform(loc, q=True, ws=True, t=True))
