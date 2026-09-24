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
     L_frontWheel   — front rim centre; its circle is the tyre (radius attr)
     L_backWheel    — back rim centre; own radius (big / small wheels)
     L_frontSpring  — front coil-over top (sets spring height)
     L_backSpring   — back coil-over top
     L_frontDoor    — front door hinge
     L_backDoor     — back door hinge
   Tank wheels (tracked, any layout): fit_track_wheels(meshes) or
   add_track_wheel() makes one L_trackWheelNN guide per wheel, each with
   its own radius and a role (Road wheel / Roller / Gear); the tread's
   thickness sits on the group (treadThickness).
   Springs (per axle, set_suspension): a leaf-spring axle adds
     L_<axle>LeafFront / LeafSeat / LeafRear — the leaf's eyes + middle
     L_<axle>ShacklePin                    — the shackle's frame pin
     L_<axle>ShockTop / ShockBottom        — the shock absorber mounts

 Workflow:
     import vehicle_guides
     from importlib import reload; reload(vehicle_guides)
     gs = vehicle_guides.VehicleGuideSystem()
     gs.build()                       # spawn locators at defaults
     gs.fit_wheels(["tyre_FL", "tyre_BL"])   # or place + set radius
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
RADIUS_ATTR  = "radius"   # a wheel guide's tyre radius


def is_wheel_guide(name):
    return name.startswith("L_") and "Wheel" in name


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
                       "note": "Front-left rim CENTRE. The circle is the "
                               "tyre: set its Radius in the channel box."},
    "L_backWheel":   {"pos": (80, 40, -130), "color": COLOR_LEFT,
                       "note": "Back-left rim CENTRE. Its own Radius, so "
                               "back tyres can be bigger or smaller."},
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


def _mid_guides(axles):
    """Extra LEFT guides for the middle axles of a 6 / 8-wheeler, spaced
    evenly between the front and back defaults."""
    out = {}
    n = max(2, min(4, int(axles))) - 1
    fw, bw = DEFAULT_GUIDES["L_frontWheel"]["pos"], \
        DEFAULT_GUIDES["L_backWheel"]["pos"]
    fs, bs = DEFAULT_GUIDES["L_frontSpring"]["pos"], \
        DEFAULT_GUIDES["L_backSpring"]["pos"]
    for i in range(1, n):
        t = i / float(n)
        lerp = lambda a, b: tuple(x + (y - x) * t for x, y in zip(a, b))
        out["L_midWheel%d" % i] = {
            "pos": lerp(fw, bw), "color": COLOR_LEFT,
            "note": "Middle axle %d, left rim CENTRE (front to back). The "
                    "circle is the tyre: set its Radius." % i}
        out["L_midSpring%d" % i] = {
            "pos": lerp(fs, bs), "color": COLOR_LEFT,
            "note": "Middle axle %d, left coil-over top." % i}
    return out


ALL_MID_GUIDES = _mid_guides(4)

# ---- Springs, per axle: "coil" (a coil-over from the spring guide),
# "leaf" (a leaf spring + shackle + shock, six guides) or "none"; and a
# solid axle (a beam tying the two wheels) or independent wheels.
SUSPENSION_TYPES = ("coil", "leaf", "none")
COLOR_SPRING = 21  # orange
# axle key (guide names) -> (rig axle, wheel guide, coil-over guide)
AXLES = (("front", "F", "L_frontWheel", "L_frontSpring"),
         ("mid1", "M1", "L_midWheel1", "L_midSpring1"),
         ("mid2", "M2", "L_midWheel2", "L_midSpring2"),
         ("back", "B", "L_backWheel", "L_backSpring"))
LEAF_PARTS = ("leafFront", "leafSeat", "leafRear", "shacklePin",
              "shockTop", "shockBottom")
LEAF_NOTES = {
    "leafFront": "Leaf spring FRONT eye: where the leaf bolts to the "
                 "frame hanger.",
    "leafSeat": "Leaf spring SEAT: the middle of the leaf, where it sits "
                "on the axle (the U-bolts).",
    "leafRear": "Leaf spring REAR eye: where the leaf hangs from the "
                "shackle.",
    "shacklePin": "Shackle PIN on the frame, above the rear eye. The "
                  "shackle swings from here so the leaf can flatten and "
                  "arch.",
    "shockTop": "Shock absorber TOP mount (on the frame).",
    "shockBottom": "Shock absorber BOTTOM mount (on the axle).",
}


# ---- Tank wheels: a free layout for tracked vehicles. Any number of
# wheels, each with its own radius and a role: a Road wheel rides the
# ground (the tread runs under it), a Roller carries the tread over it, a
# Gear just spins inside the loop.
TRACK_WHEEL = "L_trackWheel"
TRACK_ROLES = ("road", "roller", "gear")         # = vehicle_rig_builder's
TRACK_ROLE_LABELS = ("Road wheel", "Roller", "Gear")
TRACK_ROLE_COLORS = {"road": 21, "roller": 18, "gear": 3}
TRACK_NOTE = ("Tank wheel: put it on the wheel's centre and set its Radius "
              "(the circle is the wheel). Role: Road wheel = on the ground, "
              "the tread runs under it; Roller = the tread wraps over it; "
              "Gear = just spins inside the loop (spinReverse turns it the "
              "other way).")
# The wheel guides a free track layout replaces (hidden while it's in use).
CAR_WHEEL_GUIDES = ("L_frontWheel", "L_backWheel", "L_midWheel1",
                    "L_midWheel2", "L_frontSpring", "L_backSpring",
                    "L_midSpring1", "L_midSpring2")


def add_tyre_circle(loc, radius, color=COLOR_LEFT):
    """Give a wheel guide a `radius` attr and a circle of that radius (the
    tyre, seen side on): a unit circle child scaled by the radius, drawn
    but not selectable, so clicking always picks the guide."""
    if not cmds.attributeQuery(RADIUS_ATTR, node=loc, exists=True):
        cmds.addAttr(loc, ln=RADIUS_ATTR, at="double", min=1e-3,
                     dv=max(1e-3, radius), k=True)
        cmds.setAttr(f"{loc}.{RADIUS_ATTR}", max(1e-3, radius))
    tyre = loc + "_tyre"
    if cmds.objExists(tyre):
        return
    tyre = cmds.circle(nr=(1, 0, 0), r=1.0, s=16, ch=False, n=tyre)[0]
    cmds.parent(tyre, loc, r=True)
    for a in "XYZ":
        cmds.connectAttr(f"{loc}.{RADIUS_ATTR}", f"{tyre}.scale{a}")
    for a in ("tx", "ty", "tz", "rx", "ry", "rz", "v"):
        cmds.setAttr(f"{tyre}.{a}", l=True, k=False)
    cmds.setAttr(f"{tyre}.overrideEnabled", 1)
    cmds.setAttr(f"{tyre}.overrideDisplayType", 2)      # reference
    shape = cmds.listRelatives(tyre, s=True)[0]
    cmds.setAttr(f"{shape}.overrideEnabled", 1)
    cmds.setAttr(f"{shape}.overrideColor", color)


def wheel_centres(meshes):
    """The wheels in some tyre meshes: [(|x|, y, z, radius)], one per
    wheel. A mesh can be one tyre, a tyre with its rim, or both tyres of
    an axle: round pieces on the same centre are one wheel (the biggest
    sizes it); the left and right tyre of a pair are one wheel too."""
    parts = []
    for mesh in meshes:
        found = []
        for lo, hi, _p in _shell_boxes(mesh):
            sx, sy, sz = (hi[k] - lo[k] for k in range(3))
            big = max(sy, sz)
            if big > 1e-6 and 0.8 <= sy / max(sz, 1e-9) <= 1.25 \
                    and sx <= 1.2 * big:
                found.append((abs(0.5 * (lo[0] + hi[0])),
                              0.5 * (lo[1] + hi[1]), 0.5 * (lo[2] + hi[2]),
                              0.5 * sy))
        if not found:                      # not round: the whole mesh
            bb = cmds.exactWorldBoundingBox(_rest_shape(mesh))
            found = [(abs(0.5 * (bb[0] + bb[3])), 0.5 * (bb[1] + bb[4]),
                      0.5 * (bb[2] + bb[5]), 0.5 * (bb[4] - bb[1]))]
        parts += found
    parts.sort(key=lambda p: -p[3])
    groups = []                     # [[same-size twins]] per wheel
    for p in parts:
        for g in groups:
            x, y, z, r = g[0]
            if (y - p[1]) ** 2 + (z - p[2]) ** 2 < (0.5 * r) ** 2:
                if p[3] >= 0.8 * r:          # its twin (the other side)
                    g.append(p)
                break                        # a smaller piece inside: skip
        else:
            groups.append([p])
    return [tuple(sum(p[k] for p in g) / len(g) for k in range(4))
            for g in groups]


def track_wheel_name(i):
    return "%s%02d" % (TRACK_WHEEL, i)


def _shell_boxes(mesh):
    """World boxes of a mesh's connected pieces (its rest shape if it's
    skinned): [(min xyz, max xyz, points)]."""
    import maya.api.OpenMaya as om2
    shape = _rest_shape(mesh)
    sel = om2.MSelectionList()
    sel.add(shape)
    path = sel.getDagPath(0)
    fn = om2.MFnMesh(path)
    pts = fn.getPoints(om2.MSpace.kWorld)
    counts, conn = fn.getVertices()
    parent = list(range(len(pts)))

    def root(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    i = 0
    for c in counts:
        face = conn[i:i + c]
        i += c
        r0 = root(face[0])
        for v in face[1:]:
            r = root(v)
            if r != r0:
                parent[r] = r0
    groups = {}
    for v in range(len(pts)):
        groups.setdefault(root(v), []).append(pts[v])
    out = []
    for g in groups.values():
        lo = tuple(min(p[k] for p in g) for k in range(3))
        hi = tuple(max(p[k] for p in g) for k in range(3))
        out.append((lo, hi, g))
    return out


def measure_track(meshes):
    """Read a tank's wheels (and tread) off its meshes, left side: every
    round, flat piece is a wheel part (pieces on the same centre, like a
    disc and its hub nut, or the left and right wheel of a pair in one
    mesh, are one wheel); the mesh made of long pieces is the tread.
    Returns {"wheels": [{"pos", "radius", "role", "reverse"}] front to
    back, "tread_bottom": lowest point of the tread or None, "box": world
    box of everything}. Roles are guessed: the lowest wheels are road
    wheels, the ones a rubber band round them all would touch are
    rollers, the rest gears."""
    import vehicle_rig_builder as vrb
    parts, tread, box = [], None, None
    for mesh in meshes:
        pieces = _shell_boxes(mesh)
        round_ = []
        for lo, hi, pts in pieces:
            sx, sy, sz = (hi[k] - lo[k] for k in range(3))
            big = max(sy, sz)
            if big > 1e-6 and 0.8 <= sy / max(sz, 1e-9) <= 1.25 \
                    and sx <= 1.2 * big:
                round_.append((lo, hi))
        for lo, hi, _p in pieces:
            box = (tuple(lo) + tuple(hi) if box is None else
                   tuple(min(box[k], lo[k]) for k in range(3))
                   + tuple(max(box[3 + k], hi[k]) for k in range(3)))
        if len(round_) * 2 < len(pieces):
            # Mostly long pieces: the tread (the one reaching lowest wins).
            bottom = min(p[1] for _lo, _hi, pts in pieces for p in pts
                         if p[0] >= 0) if any(
                p[0] >= 0 for _lo, _hi, pts in pieces for p in pts) else \
                min(lo[1] for lo, _hi, _p in pieces)
            if tread is None or bottom < tread:
                tread = bottom
            continue
        for lo, hi in round_:
            c = [0.5 * (lo[k] + hi[k]) for k in range(3)]
            r = 0.5 * max(hi[1] - lo[1], hi[2] - lo[2])
            parts.append((abs(c[0]), c[1], c[2], r))
    # Pieces on the same centre are one wheel: the biggest one sizes it.
    parts.sort(key=lambda p: -p[3])
    wheels = []
    for x, y, z, r in parts:
        for w in wheels:
            if (w["pos"][1] - y) ** 2 + (w["pos"][2] - z) ** 2 < \
                    (0.5 * w["radius"]) ** 2:
                break
        else:
            wheels.append({"pos": (x, y, z), "radius": r})
    wheels.sort(key=lambda w: -w["pos"][2])
    if wheels:
        bottoms = [w["pos"][1] - w["radius"] for w in wheels]
        tops = [w["pos"][1] + w["radius"] for w in wheels]
        low, high = min(bottoms), max(tops)
        cut = low + 0.2 * (high - low)
        circles = [(w["pos"][2], w["pos"][1], w["radius"]) for w in wheels]
        on_hull = set(vrb.circles_on_hull(circles))
        for i, (w, b) in enumerate(zip(wheels, bottoms)):
            w["role"] = ("road" if b <= cut else
                         "roller" if i in on_hull else "gear")
            w["reverse"] = w["role"] == "gear"
    return {"wheels": wheels, "tread_bottom": tread, "box": box}


def spring_guide(key, part):
    """The guide for one leaf / shock point of an axle ("front" ...)."""
    return "L_%s%s%s" % (key, part[0].upper(), part[1:])


ALL_SPRING_GUIDES = [spring_guide(a[0], p) for a in AXLES
                     for p in LEAF_PARTS]


def _rest_shape(mesh):
    """The tyre as modelled: a skinned (already rigged) tyre's Orig shape,
    not its rig-deformed one; otherwise its visible shape."""
    shapes = cmds.listRelatives(mesh, s=True, ni=True, f=True) or [mesh]
    for h in cmds.listHistory(shapes[0]) or []:
        if (cmds.nodeType(h) == "mesh"
                and cmds.getAttr(h + ".intermediateObject")):
            return h
    return shapes[0]


def ground_gaps(positions, ground_y=0.0, tol=0.1):
    """[(wheel, how far its tyre bottom is off the ground)] for the wheels
    in a read_positions() dict whose tyre won't sit on the ground at rest
    (negative = pushed into it, it squashes; positive = floating), by more
    than `tol` of its radius (a centimetre of contact patch is normal)."""
    out = []
    track = positions.get("trackWheels")
    if track:
        # A tank: its tread lies on one line under the road wheels (see
        # vehicle_rig_builder.track_ground); that line should be on the
        # ground, and any road wheel well above it floats.
        import vehicle_rig_builder
        t = positions.get("treadThickness", 0.0) or 0.0
        road = [(i, w) for i, w in enumerate(track, 1)
                if w.get("role", "road") == "road"]
        if not road:
            return out
        level, contacts = vehicle_rig_builder.track_ground(
            [(w["pos"][1], w["radius"]) for _i, w in road], t)
        r_mid = sorted(w["radius"] for _i, w in road)[len(road) // 2]
        gap = level - 0.5 * t - ground_y
        if abs(gap) > tol * r_mid:
            out.append(("tread", gap))
        for (i, w), c in zip(road, contacts):
            own = w["pos"][1] - c - ground_y
            if abs(own - gap) > 1e-6 and abs(own) > tol * w["radius"]:
                out.append(("track wheel %02d" % i, own))
        return out
    for key in sorted(positions):
        if not key.endswith("_wheel") or not key.startswith("L"):
            continue
        prefix = key[:-len("_wheel")]
        r = positions.get(prefix + "_wheelRadius")
        if r is None:
            continue
        gap = positions[key][1] - r - ground_y
        if abs(gap) > max(0.5, tol * r):
            out.append((prefix, gap))
    return out


class VehicleGuideSystem(object):

    def __init__(self):
        self.guides_grp = None
        self.guides = {}

    # -----------------------------------------------------------------------

    def exists(self):
        return cmds.objExists(GUIDES_GRP_NAME)

    def build(self, axles=2):
        """axles 2 = a car (the classic guides); 3 / 4 add left guides for
        the middle axles of a 6 / 8-wheeler."""
        if self.exists():
            self._refresh_handles()
            upgraded = self.upgrade_wheel_guides()
            added = self.set_axles(axles, remove=False)
            if not (added or upgraded):
                cmds.warning(f"{GUIDES_GRP_NAME} already exists. Delete it "
                             f"first or call reset_to_defaults().")
            return
        self.guides_grp = cmds.group(em=True, n=GUIDES_GRP_NAME)
        cmds.setAttr(f"{self.guides_grp}.useOutlinerColor", 1)
        cmds.setAttr(f"{self.guides_grp}.outlinerColor", 0.4, 0.9, 1.0)
        for name, info in list(DEFAULT_GUIDES.items()) + \
                list(_mid_guides(axles).items()):
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
        for name, info in list(DEFAULT_GUIDES.items()) + \
                list(ALL_MID_GUIDES.items()):
            loc = self.guides.get(name)
            if loc and cmds.objExists(loc):
                cmds.xform(loc, ws=True, t=info["pos"])
                if is_wheel_guide(name):
                    self._set_radius(name, info["pos"][1])
        for key, _, wheel, _ in AXLES:
            if wheel in self.guides and self.suspension(key) == "leaf":
                for name, pos in self._leaf_defaults(key).items():
                    if name in self.guides:
                        cmds.xform(self.guides[name], ws=True, t=pos)
        print("[VehicleGuideSystem] Reset all guides to defaults.")

    # -----------------------------------------------------------------------
    # Wheels: every axle its own size, placed and sized independently
    # -----------------------------------------------------------------------

    def wheel_guides(self):
        """Wheel guide names in the scene, front to back."""
        self._refresh_handles()
        order = ["L_frontWheel", "L_midWheel1", "L_midWheel2", "L_backWheel"]
        return [n for n in order if n in self.guides]

    def _world_scale(self, name):
        """How much a guide is scaled in the world (by a scaled guide
        group): its circle is radius x this."""
        m = cmds.xform(self.guides[name], q=True, ws=True, m=True)
        return sum(v * v for v in m[0:3]) ** 0.5 or 1.0

    def radius(self, name):
        """A wheel guide's tyre radius (world units, so a scaled guide
        group scales it too)."""
        loc = self.guides[name]
        if cmds.attributeQuery(RADIUS_ATTR, node=loc, exists=True):
            return cmds.getAttr(f"{loc}.{RADIUS_ATTR}") * \
                self._world_scale(name)
        return self._pos(name)[1]          # before 1.0.3: its height

    def _set_radius(self, name, value):
        """Set a wheel guide's radius in world units."""
        loc = self.guides[name]
        if cmds.attributeQuery(RADIUS_ATTR, node=loc, exists=True):
            v = max(1e-3, float(value) / self._world_scale(name))
            if v < (cmds.attributeQuery(RADIUS_ATTR, node=loc, min=True)
                    or [0.0])[0]:
                cmds.addAttr(f"{loc}.{RADIUS_ATTR}", e=True, min=1e-3)
            cmds.setAttr(f"{loc}.{RADIUS_ATTR}", v)

    def _add_tyre_circle(self, loc, radius):
        """Give a wheel guide a `radius` attr and a circle of that radius
        (the tyre, seen side on)."""
        add_tyre_circle(loc, radius)

    def upgrade_wheel_guides(self):
        """Wheel guides from before 1.0.3 got their tyre size from their
        height. Give them a circle and a radius equal to that height, so
        nothing changes size. Returns how many were upgraded."""
        n = 0
        for name in self.wheel_guides():
            loc = self.guides[name]
            if not cmds.attributeQuery(RADIUS_ATTR, node=loc, exists=True):
                self._add_tyre_circle(loc, self._pos(name)[1])
                n += 1
        if n:
            print(f"[VehicleGuideSystem] {n} wheel guide(s) now have a "
                  f"tyre circle and a Radius (kept at their old size).")
        return n

    def set_axles(self, axles, remove=True):
        """Match the middle-axle guides to `axles` (2 = a car, 3 / 4 = a
        6 / 8-wheeler): add the missing ones, and with `remove` delete the
        ones past the count. Returns how many changed."""
        self._refresh_handles()
        want = _mid_guides(axles)
        changed = 0
        for name, info in want.items():
            if name not in self.guides:
                self._create_guide(name, info["pos"], info["color"],
                                   info.get("note", ""))
                changed += 1
        if remove:
            for name in list(ALL_MID_GUIDES):
                if name not in want and name in self.guides:
                    cmds.delete(self.guides.pop(name))
                    changed += 1
        for key, _, wheel, _ in AXLES:          # spring guides follow
            if key.startswith("mid"):
                self.set_suspension(key)
        if changed:
            print(f"[VehicleGuideSystem] Middle-axle guides set for "
                  f"{axles} axles.")
        return changed

    def fit_wheels(self, meshes):
        """Snap wheel guides onto tyre meshes: each wheel found in them
        (wheel_centres: a tyre, a tyre and rim, or both tyres of an axle
        in one mesh) moves the nearest wheel guide, the car's or a
        trailer's axle guide, to its centre and sizes it to it. Left and
        right tyres count as one. Returns the guides fitted."""
        self._refresh_handles()
        try:
            import vehicle_trailers
            trailer = vehicle_trailers.axle_guides()
        except Exception:
            vehicle_trailers, trailer = None, []
        names = self.wheel_guides()
        pos = {n: self._pos(n) for n in names}
        for g in trailer:
            pos[g] = tuple(cmds.xform(g, q=True, ws=True, t=True))
        if not pos:
            return []
        fits = {}
        for x, y, z, r in wheel_centres(meshes):
            best = min(pos, key=lambda n: sum(
                (a - b) ** 2 for a, b in zip(pos[n], (x, y, z))))
            fits.setdefault(best, []).append(((x, y, z), r))
        for name, got in fits.items():     # several tyres: average
            k = 1.0 / len(got)
            c = [sum(g[0][i] for g in got) * k for i in range(3)]
            r = sum(g[1] for g in got) * k
            if name in self.guides:
                cmds.xform(self.guides[name], ws=True, t=c)
                self._set_radius(name, r)
            else:
                cmds.xform(name, ws=True, t=c)
                vehicle_trailers.set_axle_radius(name, r)
        return sorted(fits)

    def any_radius(self, name):
        """The radius of a car wheel guide or a trailer axle guide."""
        if name in self.guides:
            return self.radius(name)
        import vehicle_trailers
        return vehicle_trailers.axle_radius(name)

    # -----------------------------------------------------------------------
    # Tank wheels: a free layout (any number, each its own size and role)
    # -----------------------------------------------------------------------

    def track_wheels(self):
        """The track wheel guides, in order (L_trackWheel01 ...)."""
        self._refresh_handles()
        return sorted(n for n in self.guides if n.startswith(TRACK_WHEEL))

    def add_track_wheel(self, pos, radius, role="road", reverse=None):
        """A new track wheel guide at `pos` (world, left side) with its
        radius, role (road / roller / gear) and spin direction. Returns
        its name."""
        if not self.exists():
            self.build()
        taken = self.track_wheels()
        i = 1
        while track_wheel_name(i) in taken:
            i += 1
        name = track_wheel_name(i)
        self._create_guide(name, (abs(pos[0]), pos[1], pos[2]), COLOR_LEFT,
                           TRACK_NOTE)
        loc = self.guides[name]
        cmds.addAttr(loc, ln="role", at="enum",
                     en=":".join(TRACK_ROLE_LABELS), k=True)
        cmds.addAttr(loc, ln="spinReverse", at="bool", k=True)
        self._set_radius(name, radius)
        self.set_track_role(name, role, reverse)
        self._hide_car_wheels(True)
        return name

    def remove_track_wheel(self, name):
        self._refresh_handles()
        if name in self.guides:
            cmds.delete(self.guides.pop(name))
        if not self.track_wheels():
            self._hide_car_wheels(False)

    def clear_track_wheels(self):
        for name in self.track_wheels():
            self.remove_track_wheel(name)

    def track_role(self, name):
        loc = self.guides[name]
        if not cmds.attributeQuery("role", node=loc, exists=True):
            return "road"
        return TRACK_ROLES[cmds.getAttr(loc + ".role")]

    def track_reverse(self, name):
        loc = self.guides[name]
        return bool(cmds.attributeQuery("spinReverse", node=loc, exists=True)
                    and cmds.getAttr(loc + ".spinReverse"))

    def set_track_role(self, name, role=None, reverse=None):
        """Set a track wheel's role and / or spin direction (None keeps it;
        a new gear spins the other way by default). Colours its circle."""
        self._refresh_handles()
        loc = self.guides[name]
        if role is not None:
            if role not in TRACK_ROLES:
                cmds.error(f"Unknown wheel role '{role}': use "
                           f"{', '.join(TRACK_ROLES)}.")
            cmds.setAttr(loc + ".role", TRACK_ROLES.index(role))
            if reverse is None:
                reverse = role == "gear"
        if reverse is not None:
            cmds.setAttr(loc + ".spinReverse", bool(reverse))
        tyre = loc + "_tyre"
        if cmds.objExists(tyre):
            shape = cmds.listRelatives(tyre, s=True)[0]
            cmds.setAttr(shape + ".overrideColor",
                         TRACK_ROLE_COLORS[self.track_role(name)])

    def tread_thickness(self):
        grp = GUIDES_GRP_NAME
        if cmds.objExists(grp) and cmds.attributeQuery(
                "treadThickness", node=grp, exists=True):
            return cmds.getAttr(grp + ".treadThickness")
        return 0.0

    def set_tread_thickness(self, value):
        """How thick the tread is under the road wheels (they ride that
        far above the ground)."""
        grp = GUIDES_GRP_NAME
        if not cmds.objExists(grp):
            return
        if not cmds.attributeQuery("treadThickness", node=grp, exists=True):
            cmds.addAttr(grp, ln="treadThickness", at="double", min=0.0,
                         k=True)
        cmds.setAttr(grp + ".treadThickness", max(0.0, float(value)))

    def _hide_car_wheels(self, hide):
        """A free track layout replaces the car's wheel and spring guides:
        hide them while it's in use."""
        for name in CAR_WHEEL_GUIDES:
            loc = self.guides.get(name)
            if loc and cmds.objExists(loc):
                cmds.setAttr(loc + ".visibility", 0 if hide else 1)

    def fit_to_model(self, box):
        """Scale and move every guide (not the track wheels) so the car the
        guides describe matches a model's world box (min xyz + max xyz):
        its wheels span the model's length and sit on its lowest point.
        Returns the scale (1.0 = nothing to do)."""
        self._refresh_handles()
        front, back = self._pos("L_frontWheel"), self._pos("L_backWheel")
        rf, rb = self.radius("L_frontWheel"), self.radius("L_backWheel")
        length = (front[2] + rf) - (back[2] - rb)
        model = box[5] - box[2]
        if length <= 1e-6 or model <= 1e-6:
            return 1.0
        s = model / length
        g = (0.0, 0.0, 0.5 * ((front[2] + rf) + (back[2] - rb)))
        m = (0.0, box[1], 0.5 * (box[2] + box[5]))
        if abs(s - 1.0) < 0.02 and sum((a - b) ** 2 for a, b in
                                       zip(g, m)) ** 0.5 < 0.02 * model:
            return 1.0
        for name, loc in self.guides.items():
            if name.startswith(TRACK_WHEEL):
                continue
            p = self._pos(name)
            cmds.xform(loc, ws=True, t=[m[k] + s * (p[k] - g[k])
                                        for k in range(3)])
            if cmds.attributeQuery(RADIUS_ATTR, node=loc, exists=True):
                self._set_radius(name, self.radius(name) * s)
            shape = cmds.listRelatives(loc, s=True, type="locator") or []
            for sh in shape:
                for a in "XYZ":
                    cmds.setAttr(f"{sh}.localScale{a}",
                                 cmds.getAttr(f"{sh}.localScale{a}") * s)
        return s

    def fit_track_wheels(self, meshes):
        """Make the track wheel guides from a tank's meshes: select its
        wheel meshes (either side, or both sides in one mesh) and its
        tread. One guide per wheel, on its centre and sized to it, with
        its role guessed (road / roller / gear); the tread thickness is
        measured; the rest of the guides are scaled to the tank and the
        chassis guide moves to its middle. Returns a summary dict."""
        if not self.exists():
            self.build()
        found = measure_track(meshes)
        wheels = found["wheels"]
        if not wheels:
            cmds.warning("No wheels found: select the tank's wheel meshes "
                         "(and its tread).")
            return {"wheels": [], "thickness": self.tread_thickness(),
                    "scale": 1.0}
        box = found["box"]
        scale = self.fit_to_model(box)
        cmds.xform(self.guides["C_chassis"], ws=True,
                   t=(0.0, 0.5 * (box[1] + box[4]), 0.5 * (box[2] + box[5])))
        self.clear_track_wheels()
        for w in wheels:
            self.add_track_wheel(w["pos"], w["radius"], w["role"],
                                 w["reverse"])
        road = sorted(w["pos"][1] - w["radius"] for w in wheels
                      if w["role"] == "road")
        thickness = self.tread_thickness()
        if found["tread_bottom"] is not None and road:
            thickness = max(0.0, road[len(road) // 2] - found["tread_bottom"])
            self.set_tread_thickness(thickness)
        return {"wheels": [(n, self.track_role(n), self.radius(n))
                           for n in self.track_wheels()],
                "thickness": thickness, "scale": scale}

    # -----------------------------------------------------------------------
    # Springs: per axle type (coil / leaf / none) and solid axle
    # -----------------------------------------------------------------------

    def _setting(self, attr, kind, default):
        grp = GUIDES_GRP_NAME
        if not cmds.objExists(grp):
            return default
        if not cmds.attributeQuery(attr, node=grp, exists=True):
            return default
        return cmds.getAttr(f"{grp}.{attr}") or default

    def suspension(self, key):
        """An axle's spring type: "coil", "leaf" or "none". key = "front",
        "mid1", "mid2" or "back"."""
        kind = self._setting(f"susp_{key}", "string", "coil")
        return kind if kind in SUSPENSION_TYPES else "coil"

    def solid_axle(self, key):
        """True if the axle is a solid beam (both wheels tied together)."""
        return bool(self._setting(f"solid_{key}", "bool", False))

    def _leaf_defaults(self, key):
        """{guide: default position} of an axle's leaf + shock guides,
        placed from its wheel guide and tyre radius."""
        import vehicle_rig_builder
        wheel = [a[2] for a in AXLES if a[0] == key][0]
        pts = vehicle_rig_builder.default_leaf_positions(
            self._pos(wheel), self.radius(wheel))
        return {spring_guide(key, p): pts[p] for p in LEAF_PARTS}

    def set_suspension(self, key, kind=None, solid=None):
        """Set an axle's spring type ("coil", "leaf", "none") and / or
        whether it is a solid axle; None keeps the current one. A leaf
        axle gets six guides placed from its wheel (move them onto the
        model's leaf, shackle and shock); switching away deletes them.
        The coil-over guide shows only on a coil axle. Returns the type."""
        if key not in [a[0] for a in AXLES]:
            cmds.error(f"Unknown axle '{key}': use front, mid1, mid2 or "
                       f"back.")
        if kind is not None and kind not in SUSPENSION_TYPES:
            cmds.error(f"Unknown spring type '{kind}': use "
                       f"{', '.join(SUSPENSION_TYPES)}.")
        if not self.exists():
            return kind or "coil"
        self._refresh_handles()
        grp = GUIDES_GRP_NAME
        if kind is not None:
            if not cmds.attributeQuery(f"susp_{key}", node=grp, exists=True):
                cmds.addAttr(grp, ln=f"susp_{key}", dt="string")
            cmds.setAttr(f"{grp}.susp_{key}", kind, type="string")
        if solid is not None:
            if not cmds.attributeQuery(f"solid_{key}", node=grp,
                                       exists=True):
                cmds.addAttr(grp, ln=f"solid_{key}", at="bool")
            cmds.setAttr(f"{grp}.solid_{key}", bool(solid))
        kind = self.suspension(key)
        _, _, wheel, coil = [a for a in AXLES if a[0] == key][0]
        on_axle = wheel in self.guides
        if kind == "leaf" and on_axle:
            for name, pos in self._leaf_defaults(key).items():
                if name not in self.guides:
                    part = name[len("L_" + key):]
                    part = part[0].lower() + part[1:]
                    self._create_guide(name, pos, COLOR_SPRING,
                                       LEAF_NOTES.get(part, ""))
        else:
            for part in LEAF_PARTS:
                name = spring_guide(key, part)
                if name in self.guides:
                    cmds.delete(self.guides.pop(name))
        if coil in self.guides:
            cmds.setAttr(f"{self.guides[coil]}.visibility",
                         1 if kind == "coil" else 0)
        return kind

    def spring_guides(self, key):
        """The leaf / shock guides an axle has in the scene."""
        self._refresh_handles()
        return [spring_guide(key, p) for p in LEAF_PARTS
                if spring_guide(key, p) in self.guides]

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

        positions = {
            "chassis":      p("C_chassis"),
            "steeringWheel": p("C_steering"),
            "hoodHinge":    p("C_hood"),
            "trunkHinge":   p("C_trunk"),

            # Wheels (front + back, L mirrored to R).
            "LF_wheel": lf_w,            "RF_wheel": (-lf_w[0], lf_w[1], lf_w[2]),
            "LB_wheel": lb_w,            "RB_wheel": (-lb_w[0], lb_w[1], lb_w[2]),
            # Per-axle tyre radius (the wheel guide's circle).
            "LF_wheelRadius": self.radius("L_frontWheel"),
            "RF_wheelRadius": self.radius("L_frontWheel"),
            "LB_wheelRadius": self.radius("L_backWheel"),
            "RB_wheelRadius": self.radius("L_backWheel"),
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
        # Middle axles (6 / 8-wheelers), when their guides exist.
        for i in (1, 2):
            if f"L_midWheel{i}" not in self.guides:
                continue
            w = p(f"L_midWheel{i}")
            positions[f"LM{i}_wheel"] = w
            positions[f"RM{i}_wheel"] = (-w[0], w[1], w[2])
            positions[f"LM{i}_wheelRadius"] = self.radius(f"L_midWheel{i}")
            positions[f"RM{i}_wheelRadius"] = self.radius(f"L_midWheel{i}")
            if f"L_midSpring{i}" in self.guides:
                s = p(f"L_midSpring{i}")
                positions[f"LM{i}_springTop"] = s
                positions[f"RM{i}_springTop"] = (-s[0], s[1], s[2])
                positions[f"LM{i}_springTopY"] = s[1]
                positions[f"RM{i}_springTopY"] = s[1]
        # Springs: per axle type + solid axle; a leaf axle's points.
        for key, axle, wheel, _ in AXLES:
            if wheel not in self.guides:
                continue
            kind = self.suspension(key)
            positions[f"{axle}_suspension"] = kind
            positions[f"{axle}_solidAxle"] = self.solid_axle(key)
            if kind != "leaf":
                continue
            for part in LEAF_PARTS:
                name = spring_guide(key, part)
                if name in self.guides:
                    v = p(name)
                    positions[f"L{axle}_{part}"] = v
                    positions[f"R{axle}_{part}"] = (-v[0], v[1], v[2])
        # A tank's free wheel layout (left side; the builder mirrors it and
        # uses it for a tracked vehicle).
        track = self.track_wheels()
        if track:
            positions["trackWheels"] = [
                {"pos": p(n), "radius": self.radius(n),
                 "role": self.track_role(n), "reverse": self.track_reverse(n)}
                for n in track]
            positions["treadThickness"] = self.tread_thickness()
        return positions

    def axle_count(self):
        """Axles implied by the guides in the scene (2, 3 or 4)."""
        self._refresh_handles()
        return 2 + sum(1 for i in (1, 2) if f"L_midWheel{i}" in self.guides)

    # -----------------------------------------------------------------------

    def save_to_json(self, filepath):
        if not self.exists():
            cmds.error("No vehicle guides to save.")
            return
        self._refresh_handles()
        data = {name: list(self._pos(name)) for name in self.guides
                 if cmds.objExists(self.guides[name])}
        for name in self.wheel_guides():
            data[name + ".radius"] = self.radius(name)
        for key, _, wheel, _ in AXLES:
            if wheel in self.guides:
                data[f"{key}.suspension"] = self.suspension(key)
                data[f"{key}.solidAxle"] = self.solid_axle(key)
        for name in self.track_wheels():
            data[name + ".radius"] = self.radius(name)
            data[name + ".role"] = self.track_role(name)
            data[name + ".reverse"] = self.track_reverse(name)
        if self.track_wheels():
            data["_.treadThickness"] = self.tread_thickness()
        try:
            import vehicle_trailers
            trailers = vehicle_trailers.list_trailers()
        except Exception:
            trailers = []
        if trailers:
            data["_.trailers"] = trailers
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
        # As many axles as the file has (a 6 / 8-wheeler's middle axles),
        # so every saved guide has a locator to land on.
        self.set_axles(2 + sum(1 for i in (1, 2)
                               if "L_midWheel%d" % i in data))
        # Track wheels as saved (the file's set replaces the scene's).
        self.clear_track_wheels()
        for name in sorted(k for k in data if k.startswith(TRACK_WHEEL)
                           and "." not in k):
            self.add_track_wheel(data[name], data.get(name + ".radius", 1.0),
                                 data.get(name + ".role", "road"),
                                 data.get(name + ".reverse"))
        if "_.treadThickness" in data:
            self.set_tread_thickness(data["_.treadThickness"])
        if "_.trailers" in data:
            import vehicle_trailers
            vehicle_trailers.load_guides(data["_.trailers"])
        # Spring types first, so a leaf axle's guides exist to be placed.
        for key, _, wheel, _ in AXLES:
            if wheel in self.guides and f"{key}.suspension" in data:
                self.set_suspension(key, data[f"{key}.suspension"],
                                    data.get(f"{key}.solidAxle", False))
        for name, pos in data.items():
            if "." in name:
                continue
            loc = self.guides.get(name)
            if loc and cmds.objExists(loc):
                cmds.xform(loc, ws=True, t=tuple(pos))
        self.upgrade_wheel_guides()
        for name in self.wheel_guides():
            if name + ".radius" in data:
                self._set_radius(name, data[name + ".radius"])
            elif name in data:
                self._set_radius(name, data[name][1])   # old file: height
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
        if is_wheel_guide(name):
            self._add_tyre_circle(loc, pos[1])

    def _refresh_handles(self):
        if cmds.objExists(GUIDES_GRP_NAME):
            self.guides_grp = GUIDES_GRP_NAME
        self.guides = {}
        for name in (list(DEFAULT_GUIDES) + list(ALL_MID_GUIDES)
                     + ALL_SPRING_GUIDES):
            loc = name + GUIDE_SUFFIX
            if cmds.objExists(loc):
                self.guides[name] = loc
        for loc in cmds.ls(TRACK_WHEEL + "*" + GUIDE_SUFFIX,
                           type="transform") or []:
            self.guides[loc[:-len(GUIDE_SUFFIX)]] = loc

    def _pos(self, name):
        loc = self.guides.get(name)
        if not loc or not cmds.objExists(loc):
            cmds.error(f"Missing vehicle guide locator: "
                       f"{name}{GUIDE_SUFFIX}")
        return tuple(cmds.xform(loc, q=True, ws=True, t=True))
