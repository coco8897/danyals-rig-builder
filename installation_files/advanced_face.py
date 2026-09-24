"""
===============================================================================
 ADVANCED FACE — mesh-conforming heavy face rig (AdvancedSkeleton-style)
===============================================================================

 The simple locator face (in character_rig_builder.FaceRig) is unchanged.
 THIS module is a separate, optional, high-detail face that conforms to
 your actual mesh topology — you select a real edge loop and the rig lays
 a row of detail joints exactly on that loop, then drives them with a
 NURBS curve + a handful of master cluster controls.

 Why it's more accurate than the old "heavy mode": the old one placed a
 fixed number of joints on a guide curve that only *approximated* the eye.
 This one fits the curve THROUGH your selected loop vertices, so the
 joints hug the real geometry.

 Regions: eyelids (upper+lower, per eye) and lips (upper+lower).

 Workflow (per feature):
   1. Select the WHOLE eye loop (or whole mouth loop) on the mesh
      (right-click → Edge → double-click an edge to select the closed loop)
   2. Click Fit Eye L / Fit Eye R / Fit Mouth — the tool auto-splits the
      loop into the UPPER and LOWER halves for you (no more fitting each
      lid/lip separately)
   3. Build → joints + curves + master ctrls under ADV_FACE_GRP

 API:
     import advanced_face
     af = advanced_face.AdvancedFace(head_joint="C_head_BIND_JNT",
                                     jaw_joint="C_jaw_BIND_JNT")
     # (select the WHOLE left-eye loop in the viewport, then:)
     af.fit_eye("L")        # auto-splits into L_lidUpper + L_lidLower
     af.fit_eye_outer("L")  # OPTIONAL second loop: the lid crease — gives
     af.fit_eye("R")        #   two joint rows per lid for clean skinning
     af.fit_mouth()         # auto-splits into C_lipUpper + C_lipLower
     af.build()
     # (the per-lid fit_region("L_lidUpper") still works if you want it)
===============================================================================
"""

import json
import math
import re
import maya.cmds as cmds
import maya.api.OpenMaya as om2

try:
    from character_rig_builder import (SCALE, create_square_ctrl,
                                        create_circle_ctrl)
except Exception:
    SCALE = 10.0

    def create_square_ctrl(name, size=1.0, normal=(0, 0, 1), color=None):
        s = size * 0.5
        pts = [(-s, 0, -s), (s, 0, -s), (s, 0, s), (-s, 0, s), (-s, 0, -s)]
        return cmds.curve(n=name, d=1, p=pts, k=list(range(len(pts))))

    def create_circle_ctrl(name, radius=1.0, normal=(1, 0, 0), color=None):
        return cmds.circle(n=name, nr=normal, r=radius, ch=False)[0]


TOP_GROUP = "ADV_FACE_GRP"

COLOR_L = 6    # blue
COLOR_R = 13   # red
COLOR_C = 17   # yellow

# Region definitions: name -> dict(...).
#   joints  = number of detail joints conformed along the loop
#   master  = number of master ctrls spread along the curve. Each master
#             owns one DRIVER joint that the curve is skinned to, so the
#             ctrl reshapes the curve through a clean joint hierarchy
#             (no fragile cluster-under-ctrl double-transform).
#   kind    = "lid" (gets the blink driver) or "lip" (gets the mouth ctrls)
#   side    = "L" / "R" / "C" — used to find the eye centre for blink
REGIONS = {}
for _s, _col in (("L", COLOR_L), ("R", COLOR_R)):
    for _p in ("Upper", "Lower"):
        # Inner row = the lash line (gets the blink). Outer row = the lid
        # CREASE — an optional second loop that follows the blink softly,
        # giving two joint rows per lid for clean skinning (AS-style).
        # 7 masters (not 5) keeps the closed-lid curve smooth — too few and
        # the blink seam ripples between the sparse control points.
        REGIONS[f"{_s}_lid{_p}"] = {
            "joints": 9, "master": 7, "color": _col,
            "kind": "lid", "side": _s, "pair": _p.lower()}
        REGIONS[f"{_s}_lid{_p}Outer"] = {
            "joints": 9, "master": 7, "color": _col,
            "kind": "lidOuter", "side": _s, "pair": _p.lower()}
for _p in ("Upper", "Lower"):
    REGIONS[f"C_lip{_p}"] = {
        "joints": 11, "master": 5, "color": COLOR_C,
        "kind": "lip", "side": "C", "pair": _p.lower()}


# =============================================================================
# Edge-loop reading + ordering
# =============================================================================

def selected_loop_points():
    """Return the world positions of the currently-selected edge loop's
    vertices, ORDERED along the loop. Accepts an edge / vertex / face
    selection; converts to vertices and walks the connectivity so the
    points come out in loop order (not Maya's arbitrary selection order).

    Returns a list of (vert_name, (x, y, z)) tuples, or [] on failure.
    """
    sel = cmds.ls(sl=True, flatten=True) or []
    if not sel:
        cmds.warning("[advFace] Select the region's edge loop first "
                     "(right-click mesh → Edge → double-click a loop edge).")
        return []

    # Convert selection to a set of vertices.
    verts = cmds.polyListComponentConversion(sel, tv=True)
    verts = cmds.filterExpand(verts, sm=31) or []   # 31 = poly vertex
    if len(verts) < 3:
        cmds.warning(f"[advFace] Need at least 3 loop verts; got "
                     f"{len(verts)}. Select the FULL loop.")
        return []

    vert_set = set(verts)

    # Build adjacency WITHIN the selected set, using edge connectivity.
    # For each vert, its neighbours = verts it shares a selected-set edge
    # with. polyListComponentConversion(vert, te=True) gives incident
    # edges; convert those back to verts and keep ones in our set.
    adj = {v: set() for v in verts}
    for v in verts:
        edges = cmds.polyListComponentConversion(v, te=True) or []
        edges = cmds.filterExpand(edges, sm=32) or []   # 32 = poly edge
        for e in edges:
            evs = cmds.polyListComponentConversion(e, tv=True)
            evs = cmds.filterExpand(evs, sm=31) or []
            for ev in evs:
                if ev != v and ev in vert_set:
                    adj[v].add(ev)

    # Walk the chain. Endpoints (degree 1) start an open arc; a closed
    # loop has all degree 2 — start anywhere.
    ends = [v for v in verts if len(adj[v]) == 1]
    if ends:
        start = ends[0]            # open arc (e.g. a lid anchored corner-to-corner)
    else:
        start = verts[0]           # closed loop

    ordered = [start]
    visited = {start}
    cur = start
    while True:
        nxts = [n for n in adj[cur] if n not in visited]
        if not nxts:
            break
        # If a vert branches, take the nearest unvisited (keeps the walk
        # on the main loop on slightly messy topology).
        cur_pos = cmds.xform(cur, q=True, ws=True, t=True)
        nxts.sort(key=lambda n: _dist(cur_pos,
                                       cmds.xform(n, q=True, ws=True, t=True)))
        cur = nxts[0]
        ordered.append(cur)
        visited.add(cur)

    return [(v, tuple(cmds.xform(v, q=True, ws=True, t=True)))
            for v in ordered]


def _dist(a, b):
    return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5


# =============================================================================
# Small shared helpers (used by blink / mouth / region builders)
# =============================================================================

def _world_pos(node):
    """World-space translation of a node."""
    return cmds.xform(node, q=True, ws=True, t=True)


def _dedupe(pts, tol=1e-3):
    """Drop consecutive near-identical points. Doubled verts (common at
    mesh seams / mouth corners) put a CUSP in the fitted EP curve — the
    rest pose still looks fine, but the skinned curve kinks on that side
    the moment the jaw or blink deforms it."""
    if not pts:
        return pts
    out = [pts[0]]
    for p in pts[1:]:
        if _dist(p, out[-1]) > tol:
            out.append(p)
    return out


# --- small vector ops (for the eyeball-orbit blink) ------------------------

def _v_sub(a, b):
    return [a[k] - b[k] for k in range(3)]


def _v_add(a, b):
    return [a[k] + b[k] for k in range(3)]


def _v_scale(a, s):
    return [a[k] * s for k in range(3)]


def _v_dot(a, b):
    return sum(a[k] * b[k] for k in range(3))


def _v_cross(a, b):
    return [a[1] * b[2] - a[2] * b[1],
            a[2] * b[0] - a[0] * b[2],
            a[0] * b[1] - a[1] * b[0]]


def _v_norm(a):
    ln = _dist(a, (0, 0, 0)) or 1.0
    return [a[k] / ln for k in range(3)]


def _rotate_about_axis(p, pivot, axis, ang):
    """Rotate point p about `axis` (unit) through `pivot` by `ang` radians
    (Rodrigues). Distance from the pivot is preserved, so a point on the
    eyeball sphere stays on the sphere — that's what makes the lid sweep
    OVER the eyeball instead of cutting across it."""
    v = _v_sub(p, pivot)
    c, s = math.cos(ang), math.sin(ang)
    rot = _v_add(_v_add(_v_scale(v, c), _v_scale(_v_cross(axis, v), s)),
                 _v_scale(axis, _v_dot(axis, v) * (1.0 - c)))
    return _v_add(pivot, rot)


def _signed_angle(v1, v2, axis):
    """Signed angle (radians) from v1 to v2 measured about `axis` (unit),
    using only the components perpendicular to the axis."""
    a = _v_sub(v1, _v_scale(axis, _v_dot(axis, v1)))
    b = _v_sub(v2, _v_scale(axis, _v_dot(axis, v2)))
    if _dist(a, (0, 0, 0)) < 1e-9 or _dist(b, (0, 0, 0)) < 1e-9:
        return 0.0
    return math.atan2(_v_dot(_v_cross(a, b), axis), _v_dot(a, b))


def _loc_axis(loc, row):
    """World direction of locator `loc`'s local axis (row 0=X,1=Y,2=Z)."""
    m = cmds.xform(loc, q=True, ws=True, matrix=True)
    return _v_norm([m[row * 4], m[row * 4 + 1], m[row * 4 + 2]])


def _nearest_on_curve(crv, p):
    """World point on `crv` nearest `p` (transient nearestPointOnCurve)."""
    npoc = cmds.createNode("nearestPointOnCurve")
    cmds.connectAttr(f"{crv}.worldSpace[0]", f"{npoc}.inputCurve")
    cmds.setAttr(f"{npoc}.inPosition", *p)
    pos = list(cmds.getAttr(f"{npoc}.position")[0])
    cmds.delete(npoc)
    return pos


# Upper vs lower controls get a DISTINCT shape + colour so a dense eye /
# mouth isn't a soup of identical squares: UPPER = square + the side colour,
# LOWER = circle + a contrasting shade.
_LOWER_COLOR = {COLOR_L: 18, COLOR_R: 20, COLOR_C: 24}


def _pair_color(base, is_upper):
    return base if is_upper else _LOWER_COLOR.get(base, base)


def _pair_ctrl(name, size, is_upper, color):
    """Square ctrl for an UPPER row, circle for a LOWER row."""
    if is_upper:
        return create_square_ctrl(name, size=size, normal=(0, 0, 1),
                                  color=color)
    return create_circle_ctrl(name, radius=0.55 * size, normal=(0, 0, 1),
                              color=color)


def _offset_shape_fwd(ctrl, dist):
    """Float a control's CURVE SHAPE forward (+Z world) by `dist` WITHOUT
    moving its transform/pivot — the clickable curve floats OUTSIDE the mesh
    (easy to select) while the control still drives from its on-surface
    position. The face is built facing +Z, so +Z is 'out of the face'."""
    if dist and dist > 1e-6 and cmds.objExists(ctrl):
        try:
            cmds.move(0, 0, dist, "%s.cv[*]" % ctrl, relative=True,
                      worldSpace=True)
        except Exception:
            pass


def _centroid(points):
    n = float(len(points)) or 1.0
    return [sum(p[k] for p in points) / n for k in range(3)]


def _pair_index(i, n_from, n_to):
    """Index in an n_to row at the same parametric spot as i in an n_from
    row (both rows run corner -> corner)."""
    if n_from <= 1 or n_to <= 1:
        return 0
    return max(0, min(n_to - 1,
                      int(round(i * (n_to - 1) / float(n_from - 1)))))


def _corner_t(i, n):
    """0.0 at either end of a row (the corners) -> 1.0 at its middle."""
    half = ((n - 1) / 2.0) or 1.0
    return min(i, n - 1 - i) / half


def _lock_attrs(node, attrs=("tx", "ty", "tz", "rx", "ry", "rz",
                             "sx", "sy", "sz", "v")):
    for a in attrs:
        try:
            cmds.setAttr(f"{node}.{a}", l=True, k=False, cb=False)
        except Exception:
            pass


def _ensure_attr(node, ln, lo, hi, dv):
    if not cmds.attributeQuery(ln, node=node, exists=True):
        cmds.addAttr(node, ln=ln, at="double", min=lo, max=hi, dv=dv, k=True)


def _sdk(plug, driver, keys):
    """setDrivenKeyframe `plug` with linear (driverValue, value) keys."""
    for dv, v in keys:
        cmds.setDrivenKeyframe(plug, currentDriver=driver, driverValue=dv,
                               value=v, itt="linear", ott="linear")


def split_loop(ordered_pts):
    """Split a CLOSED loop of ordered world points into its UPPER and LOWER
    arcs — a whole EYE loop into upper/lower lid, or a MOUTH loop into
    upper/lower lip.

    The two corners (eye canthi / mouth corners) are the furthest-apart
    points on the loop. They split the ring into two arcs; the arc with the
    higher average Y is the upper. Both returned arcs SHARE the two corner
    points (so the upper + lower curves meet at the corners) and run in the
    same direction (corner A -> corner B).

    Returns (upper_pts, lower_pts).
    """
    n = len(ordered_pts)
    # Two corners = the furthest-apart pair of loop points.
    ci, cj, best = 0, 0, -1.0
    for a in range(n):
        pa = ordered_pts[a]
        for b in range(a + 1, n):
            d = _dist(pa, ordered_pts[b])
            if d > best:
                best, ci, cj = d, a, b
    # Two arcs between the corners, both oriented A(ci) -> B(cj), each
    # INCLUDING both corner points.
    arc1 = ordered_pts[ci:cj + 1]
    arc2 = (ordered_pts[cj:] + ordered_pts[:ci + 1])[::-1]

    def avg_y(arc):
        return sum(p[1] for p in arc) / float(len(arc))

    return (arc1, arc2) if avg_y(arc1) >= avg_y(arc2) else (arc2, arc1)


# =============================================================================
# Advanced Face
# =============================================================================

class AdvancedFace(object):
    """Builds (or rebuilds) the mesh-conforming heavy face from fitted
    region loops.

    fit_region() snapshots the ordered loop points for a region. build()
    consumes the snapshots and constructs the joints / curves / ctrls.
    Snapshots persist on a hidden network node so the window can be closed
    and reopened, and so build() can run later.
    """

    def __init__(self, head_joint="C_head_BIND_JNT",
                 jaw_joint="C_jaw_BIND_JNT",
                 ctrl_grp=None, jnt_grp=None, misc_grp=None,
                 lid_joints=None, lip_joints=None, remove_standard=True,
                 blink_height=0.1, flip_curvature=False, eye_symmetry=True):
        self.head_joint = head_joint
        self.jaw_joint = jaw_joint
        # ESCAPE HATCH: if a particular mesh still arcs the lids INWARD after
        # build (eyeball locator mis-placed, etc.), set this True and rebuild —
        # it negates the blink "forward" so the sweep bulges out over the eye.
        self.flip_curvature = flip_curvature
        # EYE SYMMETRY: True => ONE eyeball ball (L_eyeball_LOC) drives both
        # eyes (the right mirrors it across x=0). False => an asymmetric face;
        # each eye gets its OWN ball to place independently.
        self.eye_symmetry = eye_symmetry
        # Where the lids meet on a blink: 0 = at the lower lash line (lower
        # lid frozen), 1 = at the upper. Real-life blink ~0.05-0.15 — the
        # UPPER lid does nearly all the work, the lower barely moves.
        self.blink_height = blink_height
        self.ctrl_grp = ctrl_grp
        self.jnt_grp = jnt_grp
        self.misc_grp = misc_grp
        # DEPRECATED (kept for API compat): detail joints are now placed
        # ONE PER FITTED LOOP VERTEX — exactly on the vertex — so weight
        # painting maps 1:1 to the mesh edge loop. Want more joints?
        # Select a denser loop.
        self.lid_joints = lid_joints
        self.lip_joints = lip_joints
        # On build, delete the STANDARD face's eyelid + lip joints so they
        # don't collide / double up with the mesh-conforming ones.
        self.remove_standard = remove_standard
        # region -> list of (x,y,z) ordered loop points
        self.fits = {}
        self.detail_jnts = {}     # region -> [joints]
        self.master_ctrls = {}    # region -> [ctrls]
        self._loop_len = {}       # region -> fitted loop arc length

    # -----------------------------------------------------------------------
    # Fitting
    # -----------------------------------------------------------------------

    def fit_region(self, region):
        """Snapshot the selected edge loop for `region`. Returns the
        number of loop points captured, or 0 on failure."""
        if region not in REGIONS:
            cmds.warning(f"[advFace] unknown region {region!r}.")
            return 0
        pts = selected_loop_points()
        if not pts:
            return 0
        self.fits[region] = _dedupe([p for (_v, p) in pts])
        n = len(self.fits[region])
        print(f"[advFace] Fitted {region}: {n} loop points captured.")
        self._save_fits()
        return n

    def fit_eye(self, side):
        """Fit a WHOLE eye in one go: select the entire eye edge loop and
        this auto-splits it into upper + lower lid. Much faster than fitting
        each lid separately. Also drops the {side}_eyeball_LOC right away so
        you can move/scale it to match the eyeball BEFORE building — the
        blink curves over it. Returns total points captured, or 0."""
        n = self._fit_split(selected_loop_points(),
                            f"{side}_lidUpper", f"{side}_lidLower",
                            label=f"{side} eye")
        if n:
            self._ensure_eyeball_loc(side)
        return n

    def _ensure_eyeball_loc(self, side):
        """Drop the eyeball SPHERE — a VISIBLE guide ball — at FIT time so
        there's no guessing where the eye centre goes.

          L_eyeball_LOC   ONE yellow NURBS sphere. Drag its CENTRE to the LEFT
                          eyeball centre (INSIDE the head, behind the lids) and
                          scale it to the eyeball; the blink curves the lids
                          OVER it. We work in SYMMETRY, so this ONE ball drives
                          BOTH eyes — the right eye mirrors it across the centre
                          line (x=0). (Asymmetric eyes later: hand-place an
                          R_eyeball_LOC and the right eye uses that instead.)

        An existing one (incl. a locator from an older build, or one you placed)
        is left untouched — delete it to regenerate as a sphere.
        """
        # SYMMETRY SOURCE: with eye_symmetry ON, one ball — the LEFT — whenever
        # the left eye is fitted. OFF (asymmetric face) => this side's own ball.
        sym = getattr(self, "eye_symmetry", True)
        src = ("L" if sym and self.fits.get("L_lidUpper")
               and self.fits.get("L_lidLower") else side)
        up = self.fits.get(f"{src}_lidUpper", [])
        lo = self.fits.get(f"{src}_lidLower", [])
        if not up or not lo:
            return None
        eye = f"{src}_eyeball_LOC"
        if not cmds.objExists(eye):
            if src == "R" and cmds.objExists("L_eyeball_LOC"):
                # breaking symmetry: seed the right ball at the MIRROR of the
                # left so it starts matched, then nudge it for the asymmetry.
                lx, ly, lz = _world_pos("L_eyeball_LOC")
                C = [-lx, ly, lz]
            else:
                eye_jnt = f"{src}_eye_BIND_JNT"
                C = (_world_pos(eye_jnt) if cmds.objExists(eye_jnt)
                     else list(_centroid(up + lo)))
            r = max(sum(_dist(p, C) for p in (up + lo))
                    / float(len(up + lo)), 1e-3)
            s = cmds.sphere(n=eye, r=r, ch=False)[0]
            cmds.xform(s, ws=True, t=C)
            # an obvious, non-rendering GUIDE ball (yellow wireframe)
            shp = (cmds.listRelatives(s, s=True) or [s])[0]
            for at, v in (("overrideEnabled", 1), ("overrideColor", 17),
                          ("castsShadows", 0), ("receiveShadows", 0),
                          ("primaryVisibility", 0), ("visibleInReflections", 0),
                          ("visibleInRefractions", 0)):
                try:
                    cmds.setAttr(f"{shp}.{at}", v)
                except Exception:
                    pass
            drives = ("ONE ball drives BOTH eyes (mirrored)" if sym
                      else "asymmetric: this eye's OWN ball")
            print(f"[advFace] {eye} eyeball SPHERE created — drag its CENTRE "
                  f"into the eye socket (behind the lids) + scale to the "
                  f"eyeball, then Build. {drives}.")
        # symmetry => ONE ball: clear a stray right-side ball so it can't fight
        # the mirror. (eye_symmetry OFF keeps both balls for asymmetric eyes.)
        if sym and src == "L" and cmds.objExists("R_eyeball_LOC"):
            try:
                cmds.delete("R_eyeball_LOC")
            except Exception:
                pass
        return eye

    def _eyeball_center(self, side):
        """World-space eyeball centre for a side. With eye_symmetry ON the
        single L_eyeball_LOC drives both eyes — the right mirrors it across the
        centre line (x=0). A hand-placed {side}_eyeball_LOC always wins; OFF,
        each eye uses strictly its own ball."""
        loc = f"{side}_eyeball_LOC"
        if cmds.objExists(loc):
            return list(_world_pos(loc))
        if getattr(self, "eye_symmetry", True):
            other = "L" if side == "R" else "R"
            oloc = f"{other}_eyeball_LOC"
            if cmds.objExists(oloc):
                x, y, z = _world_pos(oloc)
                return [-x, y, z]
        return None

    def fit_eye_outer(self, side):
        """OPTIONAL second eye loop — the lid CREASE, one loop further out
        than the lash line. Select the whole outer loop and it auto-splits
        into upper + lower OUTER lid rows. With both loops fitted each lid
        gets TWO conformed joint rows (lash line + crease, AS-style), which
        makes eyelid skinning far cleaner — the crease row follows the blink
        at the blink ctrl's `lidFollow` factor."""
        return self._fit_split(selected_loop_points(),
                               f"{side}_lidUpperOuter",
                               f"{side}_lidLowerOuter",
                               label=f"{side} eye outer")

    def fit_mouth(self):
        """Fit the WHOLE mouth in one go: select the entire lip edge loop and
        this auto-splits it into upper + lower lip. Returns total points
        captured, or 0."""
        return self._fit_split(selected_loop_points(),
                               "C_lipUpper", "C_lipLower", label="mouth")

    def _fit_split(self, pts, upper_region, lower_region, label):
        """Shared whole-loop fit: split the selected closed loop into an
        upper + lower arc and snapshot BOTH regions from one selection."""
        if not pts:
            return 0
        loop = _dedupe([p for (_v, p) in pts])
        if len(loop) < 6:
            cmds.warning(f"[advFace] Select the FULL {label} loop "
                         f"(>= 6 verts); got {len(loop)}.")
            return 0
        upper, lower = split_loop(loop)
        if len(upper) < 3 or len(lower) < 3:
            cmds.warning(
                f"[advFace] {label}: couldn't split the loop into two halves "
                f"(upper {len(upper)}, lower {len(lower)}). Make sure you "
                f"selected the FULL closed loop.")
            return 0
        self.fits[upper_region] = upper
        self.fits[lower_region] = lower
        self._save_fits()
        print(f"[advFace] Fitted {label}: {len(upper)} upper + {len(lower)} "
              f"lower joints from one loop selection.")
        return len(upper) + len(lower)

    def fitted_regions(self):
        return sorted(self.fits.keys())

    # -----------------------------------------------------------------------
    # Build
    # -----------------------------------------------------------------------

    # Standard-rig ctrls that must be at REST while the face builds. The
    # jaw-follow / head-attach constraints capture the CURRENT pose as
    # their maintained offset — building with the jaw open bakes that open
    # pose in as "rest", and zeroing the jaw afterwards shears the lower
    # lip off the face (displacement scaled by each master's jaw weight).
    _NEUTRAL_CTRLS = ("C_jaw_CTRL", "C_head_CTRL", "C_neck_CTRL")

    def _neutralize_pose(self):
        """Zero the jaw/head/neck ctrls' TRS; return values to restore."""
        stored = []
        for c in self._NEUTRAL_CTRLS:
            if not cmds.objExists(c):
                continue
            for at in ("rotate", "translate"):
                for ax in "XYZ":
                    plug = f"{c}.{at}{ax}"
                    try:
                        v = cmds.getAttr(plug)
                        if abs(v) > 1e-6 and cmds.getAttr(plug,
                                                          settable=True):
                            cmds.setAttr(plug, 0.0)
                            stored.append((plug, v))
                    except Exception:
                        pass
        return stored

    def _mirror_eye_fits(self):
        """SYMMETRIC eyes: mirror the LEFT eye's fitted lid loops onto the
        RIGHT (across X) so BOTH eyes build from one fit — the Fit Eye R
        buttons are hidden while symmetry is on, so without this the right eye
        would get no joints / controls."""
        if not getattr(self, "eye_symmetry", True):
            return
        for pair in ("lidUpper", "lidLower", "lidUpperOuter", "lidLowerOuter"):
            L, R = f"L_{pair}", f"R_{pair}"
            if self.fits.get(L) and not self.fits.get(R):
                # negate X + reverse so the right loop keeps corner->corner
                # order (mirroring flips handedness).
                self.fits[R] = [(-p[0], p[1], p[2])
                                for p in reversed(self.fits[L])]

    def build(self):
        if not self.fits:
            self._load_fits()
        if not self.fits:
            cmds.warning("[advFace] No regions fitted yet. Select a loop "
                         "and click a Fit button first.")
            return None
        self._mirror_eye_fits()
        # The face shape dials (face_shapes) are wired into the controls this
        # rebuild replaces: take them off first, put them back after.
        shapes = None
        try:
            import face_shapes
            if face_shapes.exists():
                shapes = face_shapes.values()
                face_shapes.remove()
        except ImportError:
            face_shapes = None
        # Build at NEUTRAL pose no matter how the rig is currently posed,
        # then restore the animator's pose afterwards.
        stored = self._neutralize_pose()
        try:
            result = self._build_neutral()
            if shapes is not None:
                face_shapes.build()
                face_shapes.set_values(shapes)
            return result
        finally:
            for plug, v in stored:
                try:
                    cmds.setAttr(plug, v)
                except Exception:
                    pass
            if stored:
                print(f"[advFace] Built at neutral pose ({len(stored)} "
                      f"posed channel(s) temporarily zeroed + restored).")

    def _build_neutral(self):
        # The previous build's utility nodes (remaps, blends, driven keys,
        # curve infos) aren't under TOP_GROUP: delete them with it.
        if cmds.objExists(NODES_SET):
            old = cmds.sets(NODES_SET, q=True) or []
            cmds.delete(NODES_SET)
            for n in old:              # one by one: deleting one (a skin,
                if cmds.objExists(n):  # say) can take others with it
                    try:
                        cmds.delete(n)
                    except (RuntimeError, ValueError):
                        pass
        before = set(cmds.ls())
        try:
            return self._build_neutral_inner()
        finally:
            fresh = [n for n in cmds.ls() if n not in before
                     and not cmds.ls(n, dag=True) and "_smileCheek_" not in n
                     and cmds.nodeType(n) != "objectSet"]
            if fresh:
                cmds.sets(fresh, n=NODES_SET, empty=False)

    def _build_neutral_inner(self):
        if cmds.objExists(TOP_GROUP):
            # Rescue the user-adjustable blink locators before deleting the
            # old build (they get parented under the controls group, so a
            # rebuild would otherwise wipe the user's centre/radius/aim
            # tweaks).
            for s_ in ("L", "R"):
                for suf_ in ("_eyeball_LOC", "_upperLid_LOC"):
                    lc_ = f"{s_}{suf_}"
                    if (cmds.objExists(lc_)
                            and cmds.listRelatives(lc_, p=True)):
                        try:
                            cmds.parent(lc_, world=True)
                        except Exception:
                            pass
            # Remove any detail joints relocated OUT of TOP_GROUP by a prior
            # 'Make Game Skeleton' (folded under the head/jaw) — otherwise the
            # rebuild's fresh joints name-clash with the leftover ones.

            def _in_top(n):
                cur = n
                for _ in range(60):
                    par = (cmds.listRelatives(cur, p=True) or [None])[0]
                    if par is None:
                        return False
                    if par == TOP_GROUP:
                        return True
                    cur = par
                return False
            stray = []
            for _region in REGIONS:
                for _j in (cmds.ls(f"{_region}_*_BIND_JNT", type="joint")
                           or []):
                    if not _in_top(_j):
                        stray.append(_j)
            if stray:
                cmds.delete(stray)
            cmds.delete(TOP_GROUP)

        top = cmds.group(em=True, n=TOP_GROUP)

        def _grp(name, inherits=True, visible=True):
            g = cmds.group(em=True, n=name, p=top)
            if not inherits:
                cmds.setAttr(f"{g}.inheritsTransform", 0)
            if not visible:
                cmds.setAttr(f"{g}.v", 0)
            return g

        # Detail joints + curves read WORLD positions (via PCI), so their
        # groups must not inherit transforms. Driver joints stay hidden.
        jnt_grp = _grp("ADV_FACE_joints_GRP", inherits=False)
        crv_grp = _grp("ADV_FACE_curves_GRP", inherits=False)
        ctl_grp = _grp("ADV_FACE_controls_GRP")
        drv_grp = _grp("ADV_FACE_drivers_GRP", visible=False)

        for region in sorted(self.fits.keys()):
            self._build_region(region, jnt_grp, crv_grp, ctl_grp, drv_grp)

        # Blink: for each eye whose BOTH lids are fitted, wire a blink
        # control that closes the upper lid down to the lower lid.
        for side in ("L", "R"):
            if (f"{side}_lidUpper" in self.fits
                    and f"{side}_lidLower" in self.fits):
                self._build_blink(side, ctl_grp)
                add_lid_seal(side)
            # Optional crease rows: follow the inner-lid blink softly.
            self._wire_outer_lids(side)

        # Mouth: smile / frown + zippy lips (when both lips are fitted).
        if "C_lipUpper" in self.fits and "C_lipLower" in self.fits:
            self._build_mouth_controls(ctl_grp)

        # ---- FOLLOW THE HEAD ----
        # The detail joints get their TRANSLATION from the curves (PCI) and
        # their rotation from the head (orient constraint). The curves are
        # driven by the master controls, so to make the whole face travel +
        # turn with the head we constrain the controls group to the head
        # joint (masters -> drivers -> curve re-skins -> PCI joints all ride
        # along, no double transform). Without this the face floats in place
        # while the head moves away.
        if self.head_joint and cmds.objExists(self.head_joint):
            attach_to_head(self.head_joint)
        else:
            cmds.warning(
                f"[advFace] Head joint '{self.head_joint}' not found — the "
                f"face will NOT follow the head. Set a valid Head joint and "
                f"rebuild (or run advanced_face.attach_to_head()).")

        # ---- REPLACE the standard eyelids/lips ----
        # The advanced face supersedes the standard joint-based lids/lips, so
        # delete those (only the regions we actually built) to avoid two sets
        # of joints on the same mesh. Other standard face parts stay.
        if self.remove_standard:
            built = set(self.fits.keys())
            has_lids = any(r.endswith(("lidUpper", "lidLower")) for r in built)
            has_lips = any(r.endswith(("lipUpper", "lipLower")) for r in built)
            remove_standard_lids_lips(eyelids=has_lids, lips=has_lips)
            if has_lips:
                link_smile_to_cheeks()

        # Let the eye look-at controls blink the advanced lids (same attr).
        self._link_blink_to_lookat()

        # The eyeball guide balls have done their job (shaped the curvature) —
        # hide them so they don't clutter the built face. Re-show (or use Snap
        # Eyeball) + rebuild to re-tune.
        for loc in ("L_eyeball_LOC", "R_eyeball_LOC"):
            if cmds.objExists(loc):
                try:
                    cmds.setAttr(f"{loc}.visibility", 0)
                except Exception:
                    pass

        cmds.select(cl=True)
        print(f"[advFace] Built advanced face: "
              f"{', '.join(sorted(self.fits.keys()))}. Top: {TOP_GROUP}")
        return top

    def _build_blink(self, side, ctl_grp):
        """Blink + manual lid controls for one eye.

        ROBUST CLOSE: on a blink each non-corner lid master LERPS toward its
        index-paired master on the OTHER lid (so the eye ALWAYS shuts, in
        any head orientation), plus a forward CURVATURE bulge (radial from
        {side}_eyeball_LOC) that peaks mid-blink so the lid arcs over the
        eyeball. The shared corners stay pinned.

        Controls:
          {side}_blink_CTRL    `blink` (0 open -> 1 closed) + live
                               `blinkHeight` (0 lids meet at the lower lash,
                               1 at the upper).
          {side}_upperLid_CTRL / {side}_lowerLid_CTRL — grab + TRANSLATE to
                               pose that whole lid by hand (wide eyes,
                               squint, asymmetry). Composes with the blink.
        Move {side}_eyeball_LOC to change the curvature; rebuild.
        """
        upper = self.master_ctrls.get(f"{side}_lidUpper", [])
        lower = self.master_ctrls.get(f"{side}_lidLower", [])
        if len(upper) < 3 or len(lower) < 3:
            return

        self._ensure_eyeball_loc(side)
        # ONE ball drives both eyes: left uses L_eyeball_LOC, right MIRRORS it.
        C = self._eyeball_center(side)
        if C is None:
            C = list(_centroid([_world_pos(c) for c in upper + lower]))
        # park this side's OWN ball (only the left has one) under the control
        # group so it follows the head; the mirrored right eye has no ball.
        eye_loc = f"{side}_eyeball_LOC"
        if cmds.objExists(eye_loc) and \
                (cmds.listRelatives(eye_loc, p=True) or [None])[0] != ctl_grp:
            try:
                cmds.parent(eye_loc, ctl_grp)
            except Exception:
                pass
        ec = _centroid([_world_pos(c) for c in upper + lower])
        gap = _dist(list(_centroid([_world_pos(c) for c in upper])),
                    list(_centroid([_world_pos(c) for c in lower]))) or SCALE
        # Radius FLOOR: the eyeball is roughly 0.4x the eye WIDTH (canthus to
        # canthus). We floor the arc's pivot depth to that so the lids NEVER
        # over-bulge when the guide sphere is dropped too shallow, and the arc
        # is always visible on a narrowly-opened eye where the gap is tiny.
        span = _dist(_world_pos(upper[0]), _world_pos(upper[-1]))
        # Minimum pivot depth so the lids never over-bulge when the ball is
        # dropped shallow, and the arc stays visible on a narrowly-opened eye.
        # The ball's PLACEMENT (its centre depth, below) is what really shapes
        # the curve — a correctly-sized ball pushed to the eyeball centre rides
        # a wider sphere automatically, so we don't fold its drawn size in here
        # (that would let a giant guide ball flatten the sweep to nothing).
        r_floor = max(0.42 * span, 0.6 * gap, 0.3)

        eye_len = (self._loop_len.get(f"{side}_lidUpper", 0.0)
                   + self._loop_len.get(f"{side}_lidLower", 0.0)) or SCALE
        color = COLOR_L if side == "L" else COLOR_R
        blink_ctrl = create_circle_ctrl(
            f"{side}_blink_CTRL", radius=max(0.1, 0.12 * eye_len),
            normal=(0, 0, 1), color=color)
        boff = cmds.group(blink_ctrl, n=f"{side}_blink_OFFSET")
        # Sit the blink ctrl above AND well in FRONT of the eye (+Z) so it
        # floats outside the mesh and is easy to select. It's a pure attr
        # holder (the SDK reads its .blink), so moving it is free.
        cmds.xform(boff, ws=True,
                   t=(ec[0], ec[1] + 0.45 * eye_len, ec[2] + 0.4 * eye_len))
        cmds.parent(boff, ctl_grp)
        bh_dv = max(0.0, min(1.0, float(self.blink_height)))
        _ensure_attr(blink_ctrl, "blink", 0, 1, 0.0)
        _ensure_attr(blink_ctrl, "blinkHeight", 0, 1, bh_dv)
        _lock_attrs(blink_ctrl)

        # --- MANUAL LID CONTROLS: translate to pose the whole lid -----------
        # A LID group is inserted above each non-corner master's AUTO and
        # driven by the lid control's translate (the corners stay pinned).
        # AUTO is still where the blink writes, so they compose cleanly.
        def _lid_control(name, masters, is_upper):
            cen = _centroid([_world_pos(c) for c in masters])
            ctrl = _pair_ctrl(name, max(0.06, 0.09 * eye_len), is_upper,
                              _pair_color(color, is_upper))
            off = cmds.group(ctrl, n=f"{name}_OFFSET")
            cmds.xform(off, ws=True, t=(cen[0], cen[1], cen[2]))
            cmds.parent(off, ctl_grp)
            _lock_attrs(ctrl, attrs=("rx", "ry", "rz",
                                     "sx", "sy", "sz", "v"))
            nn = len(masters)
            for j, mc in enumerate(masters):
                if j in (0, nn - 1):
                    continue                       # corner -> pinned
                auto = mc.replace("_CTRL", "_AUTO")
                if not cmds.objExists(auto):
                    continue
                par = (cmds.listRelatives(auto, p=True) or [None])[0]
                lid = cmds.group(em=True, n=mc.replace("_CTRL", "_LID"))
                if par:
                    cmds.parent(lid, par)
                cmds.parent(auto, lid, relative=True)
                for ax in "XYZ":
                    cmds.connectAttr(f"{ctrl}.translate{ax}",
                                     f"{lid}.translate{ax}")
            return ctrl

        _lid_control(f"{side}_upperLid_CTRL", upper, True)
        _lid_control(f"{side}_lowerLid_CTRL", lower, False)

        # --- LIVE blink network --------------------------------------------
        # AUTO.translate = drive * (toward the other lid) + arc * bulge
        #   upperDrive = blink * (1 - blinkHeight);  lowerDrive = blink * bh
        #   arc = 4*blink*(1-blink)  -> peaks mid-blink, 0 at both ends
        ease = cmds.createNode("remapValue", n=f"{side}_blinkEase_RMV")
        cmds.connectAttr(f"{blink_ctrl}.blink", f"{ease}.inputValue")
        for idx in (0, 1):
            cmds.setAttr(f"{ease}.value[{idx}].value_Position", float(idx))
            cmds.setAttr(f"{ease}.value[{idx}].value_FloatValue", float(idx))
            cmds.setAttr(f"{ease}.value[{idx}].value_Interp", 2)
        bk = f"{ease}.outValue"
        revbh = cmds.createNode("reverse", n=f"{side}_blinkHt_REV")
        cmds.connectAttr(f"{blink_ctrl}.blinkHeight", f"{revbh}.inputX")
        up_drive = cmds.createNode("multDoubleLinear",
                                   n=f"{side}_blinkUpDrive_MDL")
        cmds.connectAttr(bk, f"{up_drive}.input1")
        cmds.connectAttr(f"{revbh}.outputX", f"{up_drive}.input2")
        lo_drive = cmds.createNode("multDoubleLinear",
                                   n=f"{side}_blinkLoDrive_MDL")
        cmds.connectAttr(bk, f"{lo_drive}.input1")
        cmds.connectAttr(f"{blink_ctrl}.blinkHeight", f"{lo_drive}.input2")
        rev_b = cmds.createNode("reverse", n=f"{side}_blinkArc_REV")
        cmds.connectAttr(bk, f"{rev_b}.inputX")
        arc_m = cmds.createNode("multDoubleLinear", n=f"{side}_blinkArc1_MDL")
        cmds.connectAttr(bk, f"{arc_m}.input1")
        cmds.connectAttr(f"{rev_b}.outputX", f"{arc_m}.input2")
        arc = cmds.createNode("multDoubleLinear", n=f"{side}_blinkArc2_MDL")
        cmds.connectAttr(f"{arc_m}.output", f"{arc}.input1")
        cmds.setAttr(f"{arc}.input2", 4.0)
        arc_plug = f"{arc}.output"

        # ===== SPHERICAL-ARC BLINK (the way Advanced Skeleton does it) ======
        # Each non-corner master is SET-DRIVEN along the EYE-SPHERE ARC: its
        # AUTO.translate is keyed at blink fractions 0/.25/.5/.75/1 to the
        # position it reaches by ROTATING about the eye centre toward the other
        # lid (Rodrigues — distance from the centre is preserved, so it stays
        # ON the sphere). The samples give a SMOOTH curve that bulges FORWARD
        # over the eyeball (rotating about a centre BEHIND the lids arcs them
        # OUT), and the end (drive=1) lands the lid ON the other lid so the eye
        # SEALS, the upper MERGING onto the lower. blinkHeight stays LIVE via
        # the up/lo drive (how far along the arc each lid travels), so the meet
        # point slides with no rebuild. It drives AUTO.translate (a SUM, like
        # the AS PMA) and never reparents — so it CANNOT double-transform.
        c_lf, c_rt = _world_pos(upper[0]), _world_pos(upper[-1])
        up_mid = _centroid([_world_pos(c) for c in upper[1:-1]] or [c_lf])
        lo_mid = _centroid([_world_pos(c) for c in lower[1:-1]] or [c_lf])
        canthi = _v_norm(_v_sub(c_rt, c_lf))
        nrm = _v_cross(_v_sub(c_rt, c_lf), _v_sub(list(up_mid), list(lo_mid)))
        fwd = _v_norm(nrm) if _dist(nrm, (0, 0, 0)) > 1e-6 else [0.0, 0.0, 1.0]
        # FORWARD (out of the face) = the lid-plane NORMAL, signed OUT using
        # the eyeball ball (it sits BEHIND the lids). Crucially the DIRECTION
        # comes from the NORMAL, not the raw ball vector — so the pivot lands
        # squarely BEHIND the lids even if the ball is a little off-centre or
        # low. (Using the raw ball vector could tilt the pivot BETWEEN the lids,
        # and the close then swings the upper lid up-and-OVER instead of
        # shutting — exactly the "blink won't close" failure.)
        fwd = _v_norm(nrm) if _dist(nrm, (0, 0, 0)) > 1e-6 else [0.0, 0.0, 1.0]
        out_ref = _v_sub(list(ec), list(C))          # points OUT if ball behind
        if _dist(out_ref, (0, 0, 0)) < 0.15 * r_floor:   # ball ~on the lid plane
            for cand in (self.head_joint, f"{side}_eye_BIND_JNT"):
                if cand and cmds.objExists(cand):
                    out_ref = _v_sub(list(ec), _world_pos(cand))
                    break
        if _v_dot(fwd, out_ref) < 0:
            fwd = _v_scale(fwd, -1.0)
        # last-resort manual override (tick "Flip eye curvature" + rebuild):
        if getattr(self, "flip_curvature", False):
            fwd = _v_scale(fwd, -1.0)
        # PIVOT = behind the lid plane by how far the ball sits back (floored to
        # r_floor) — the lid sweeps OVER the eyeball and SHUTS, never a flip.
        pivot_dist = max(_v_dot(out_ref, fwd), r_floor)
        center = _v_sub(list(ec), _v_scale(fwd, pivot_dist))

        up_crv, lo_crv = f"{side}_lidUpper_CRV", f"{side}_lidLower_CRV"

        def _curve_pt(crv, u):
            try:
                return list(cmds.pointOnCurve(crv, pr=u, p=True, top=True))
            except Exception:
                return None

        samples = (0.0, 0.25, 0.5, 0.75, 1.0)
        for masters, drive_plug, other_crv in (
                (upper, f"{up_drive}.output", lo_crv),
                (lower, f"{lo_drive}.output", up_crv)):
            n = len(masters)
            have = cmds.objExists(other_crv)
            for i, mc in enumerate(masters):
                if i in (0, n - 1):                # corners (canthi) pinned
                    continue
                auto = mc.replace("_CTRL", "_AUTO")
                P = _world_pos(mc)
                u = i / float(n - 1)
                tgt = (_curve_pt(other_crv, u) if have else None) or list(P)
                full_ang = _signed_angle(_v_sub(P, center),
                                         _v_sub(tgt, center), canthi)
                for t in samples:
                    rp = _rotate_about_axis(P, center, canthi, t * full_ang)
                    d = _v_sub(rp, P)
                    for k, ax in enumerate("XYZ"):
                        cmds.setDrivenKeyframe(
                            f"{auto}.translate{ax}", currentDriver=drive_plug,
                            driverValue=t, value=d[k],
                            inTangentType="spline", outTangentType="spline")

        print(f"[advFace] Wired {side} blink (eye-sphere SDK arc — AS-style "
              f"spherical sweep + merge, live blinkHeight, no reparent).")

    def _wire_outer_lids(self, side):
        """Make the OUTER (crease) lid rows follow the inner-lid blink.

        Each outer master's AUTO copies its index-paired inner master's AUTO
        translate, scaled by a live `lidFollow` attr on the blink ctrl
        (0 = crease static, 1 = full blink travel; ~0.4 = a soft crease
        follow, like skin sliding over the orbit). The inner corner masters
        don't move on blink, so the outer corners stay pinned too. Animators
        can still hand-pose the outer masters via their own CTRLs."""
        blink_ctrl = f"{side}_blink_CTRL"
        if not cmds.objExists(blink_ctrl):
            return
        wired = 0
        for pair in ("Upper", "Lower"):
            inner = self.master_ctrls.get(f"{side}_lid{pair}", [])
            outer = self.master_ctrls.get(f"{side}_lid{pair}Outer", [])
            if not inner or not outer:
                continue
            if not wired:
                _ensure_attr(blink_ctrl, "lidFollow", 0.0, 1.0, 0.4)
            nI, nO = len(inner), len(outer)
            for j, oc in enumerate(outer):
                i_auto = inner[_pair_index(j, nO, nI)].replace("_CTRL",
                                                               "_AUTO")
                o_auto = oc.replace("_CTRL", "_AUTO")
                for ax in "XYZ":
                    m = cmds.createNode("multDoubleLinear",
                                        n=f"{o_auto}_fl{ax}_MDL")
                    cmds.connectAttr(f"{i_auto}.translate{ax}", f"{m}.input1")
                    cmds.connectAttr(f"{blink_ctrl}.lidFollow", f"{m}.input2")
                    cmds.connectAttr(f"{m}.output",
                                     f"{o_auto}.translate{ax}", f=True)
                wired += 1
        if wired:
            print(f"[advFace] {side} crease rows follow the blink "
                  f"({wired} outer masters, live lidFollow).")

    def _link_blink_to_lookat(self):
        """Make the eye look-at controls blink the ADVANCED lids.

        The standard rig already puts a `blink` attr on each
        `{side}_eye_aim_CTRL` (it used to close the standard lids, now
        deleted). We replace it with a PROXY of `{side}_blink_CTRL.blink` —
        literally the SAME attribute surfaced on both controls — so you can
        blink straight from the eye look-at handle you're already holding,
        and `L/R_blink_CTRL` keeps working too. Same for `blinkHeight`."""
        for side in ("L", "R"):
            aim = f"{side}_eye_aim_CTRL"
            blink = f"{side}_blink_CTRL"
            if not (cmds.objExists(aim) and cmds.objExists(blink)):
                continue
            for attr in ("blink", "blinkHeight"):
                if not cmds.attributeQuery(attr, node=blink, exists=True):
                    continue
                # drop the orphaned standard attr (drove the deleted lids),
                # then re-add it as a live proxy of the advanced blink ctrl.
                if cmds.attributeQuery(attr, node=aim, exists=True):
                    try:
                        cmds.setAttr(f"{aim}.{attr}", lock=False)
                        cmds.deleteAttr(f"{aim}.{attr}")
                    except Exception:
                        pass
                try:
                    cmds.addAttr(aim, ln=attr, proxy=f"{blink}.{attr}")
                except Exception as e:
                    cmds.warning(f"[advFace] couldn't proxy {attr} onto "
                                 f"{aim}: {e}")
            print(f"[advFace] {aim} now blinks the advanced lids "
                  f"(blink / blinkHeight shared with {blink}).")

    def _build_mouth_controls(self, ctl_grp):
        """Smile / frown + zippy (sticky) lips for the mouth.

        Adds C_mouth_CTRL with two attrs and two corner controls:
          smile (-1..1)  corners up + out (smile) / down + in (frown), with a
                         BROAD lift so the whole lip bows into a smile curve.
          pucker(-1..1)  +1 kiss: mouth narrows + pouts FORWARD into an O;
                         -1 spread: mouth widens + pulls back.
          lipRoll(-1..1) +1 lips roll OUT (push forward + evert, fuller pout,
                         no narrowing); -1 lips tuck IN (back + press, the
                         'lips disappear' look).
          zip   (0..1)   upper + lower lips seal to the midline, CORNER-FIRST
                         (unzips from the corners), like a real lip seal.
          L/R_mouthCorner_CTRL  direct corner posing (drives the shared corner
                         masters of both lips).
        smile/zip write the masters' AUTO groups (additive blendWeighted); the
        corner controls write the OFFSET groups — so they all compose with the
        animator's per-master CTRL.
        """
        upper = self.master_ctrls.get("C_lipUpper", [])
        lower = self.master_ctrls.get("C_lipLower", [])
        if len(upper) < 3 or len(lower) < 3:
            return

        allp = [_world_pos(c) for c in upper + lower]
        centre = _centroid(allp)
        xs = [p[0] for p in allp]
        width = (max(xs) - min(xs)) or 1.0
        nU, nL = len(upper), len(lower)

        # --- C_mouth_CTRL (smile + zip attrs) ---
        mouth = create_circle_ctrl("C_mouth_CTRL",
                                   radius=max(0.1, 0.12 * width),
                                   normal=(0, 0, 1), color=COLOR_C)
        # Position the OFFSET, not the ctrl — the ctrl stays zeroed.
        moff = cmds.group(mouth, n="C_mouth_OFFSET")
        # Below the mouth AND forward (+Z) so it floats outside the mesh.
        cmds.xform(moff, ws=True,
                   t=(centre[0], centre[1] - 0.45 * width,
                      centre[2] + 0.3 * width))
        cmds.parent(moff, ctl_grp)
        _ensure_attr(mouth, "smile", -1.0, 1.0, 0.0)
        _ensure_attr(mouth, "zip", 0.0, 1.0, 0.0)
        # pucker: +1 kiss (mouth narrows + pouts FORWARD into an O), -1 spread
        # (mouth widens + pulls back). Composes with smile via blendWeighted.
        _ensure_attr(mouth, "pucker", -1.0, 1.0, 0.0)
        # lipRoll: +1 lips roll OUT (push forward + evert, a fuller pout, NO
        # narrowing), -1 lips roll/tuck IN (pull back + press together, the
        # 'lips disappear' look). Pure in/out, independent of pucker.
        _ensure_attr(mouth, "lipRoll", -1.0, 1.0, 0.0)
        _lock_attrs(mouth)

        # --- SMILE / FROWN + PUCKER -------------------------------------------
        # SMILE: corners up + out, but with a BROAD lift falloff so the WHOLE
        # lip line bows into a smile CURVE (not just two yanked corners); a
        # frown at -1. PUCKER: narrow + pout forward / spread + pull back.
        smile_amt = 0.25 * width
        fwd_amt = 0.18 * width        # forward pout at full pucker
        roll_z = 0.16 * width         # in/out push at full lipRoll
        roll_y = 0.06 * width         # subtle evert(out) / press(in)
        for masters in (upper, lower):
            n = len(masters)
            for i, mc in enumerate(masters):
                t = _corner_t(i, n)                   # 0 corner -> 1 centre
                wide = math.cos(0.5 * math.pi * t)    # spread: corners most
                # LIFT broadened (^0.55) AND floored at 0.15 so the centre
                # rises a little too — the WHOLE lip bows into one smile curve
                # instead of pinching only at the corners.
                lift = 0.15 + 0.85 * (wide ** 0.55)
                px = _world_pos(mc)
                out = 1.0 if px[0] >= centre[0] else -1.0
                dx, dy = smile_amt * wide * out * 0.6, smile_amt * lift
                auto = mc.replace("_CTRL", "_AUTO")
                _sdk(f"{auto}.translateX", f"{mouth}.smile",
                     [(-1.0, -dx), (0.0, 0.0), (1.0, dx)])
                _sdk(f"{auto}.translateY", f"{mouth}.smile",
                     [(-1.0, -dy), (0.0, 0.0), (1.0, dy)])

                # PUCKER: narrow toward the centre (corners move most), pout
                # the centre FORWARD the most. -1 mirrors it (spread + back).
                narrow = -(px[0] - centre[0]) * 0.4
                fwd = fwd_amt * (0.35 + 0.65 * t)
                _sdk(f"{auto}.translateX", f"{mouth}.pucker",
                     [(-1.0, -narrow), (0.0, 0.0), (1.0, narrow)])
                _sdk(f"{auto}.translateZ", f"{mouth}.pucker",
                     [(-1.0, -fwd), (0.0, 0.0), (1.0, fwd)])

                # LIP ROLL: +1 lips push OUT (forward) + evert (upper rolls up,
                # lower down); -1 lips tuck IN (back) + press together. Centre
                # rolls most, corners least; no narrowing (that's pucker).
                y_sign = 1.0 if masters is upper else -1.0
                rprof = 0.3 + 0.7 * t
                rz, ry = roll_z * rprof, roll_y * rprof * y_sign
                _sdk(f"{auto}.translateZ", f"{mouth}.lipRoll",
                     [(-1.0, -rz), (0.0, 0.0), (1.0, rz)])
                _sdk(f"{auto}.translateY", f"{mouth}.lipRoll",
                     [(-1.0, -ry), (0.0, 0.0), (1.0, ry)])

        # (zip no longer writes SDK deltas onto the AUTO groups — it drives
        # the seal-target constraint blend below. One system, no stacking.)

        # --- corner controls (direct posing of the shared corners) ---
        _make_mouth_corners(upper, lower, centre, width, ctl_grp,
                            retire_standard=self.remove_standard)

        # --- JAW FOLLOW + STICKY SEAL TARGETS ------------------------------
        # One SEAL target per lip pair, parked at the pair MIDPOINT and
        # riding the HALF-JAW line (static 50/50 jaw+anchor constraint,
        # shortest interp). `zip` blends each lip master's jaw-follow
        # constraint ONTO its seal target — upper and lower land exactly
        # together, corner-first, with the jaw open or closed. ONE system:
        # no SDK deltas stacked on constraint blends (the old double
        # translation), and the rest gap closes because the seal target IS
        # the pair midpoint.
        #
        # Jaw-follow itself: each LOWER master rides the jaw with a weight
        # that peaks mid-lip and fades to 0 at the corners (`jawFollow`
        # dials it). Constraints always include the anchor target because
        # Maya NORMALIZES weights (a lone jaw target at w=0.5 acts as 1).
        jaw = self.jaw_joint
        has_jaw = bool(jaw and cmds.objExists(jaw))
        if has_jaw:
            _ensure_attr(mouth, "jawFollow", 0.0, 1.0, 1.0)

        def _sticky_plug(mc, t):
            """0..1 sticky activation: corner-first remap of zip."""
            start = t * 0.6
            rm = cmds.createNode("remapValue",
                                 n=mc.replace("_CTRL", "_sticky_RMV"))
            cmds.connectAttr(f"{mouth}.zip", f"{rm}.inputValue")
            cmds.setAttr(f"{rm}.inputMin", start)
            cmds.setAttr(f"{rm}.inputMax", min(1.0, start + 0.4))
            return f"{rm}.outValue"

        def _mdl(name, in1, in2):
            """multDoubleLinear output; inputs are plugs or constants."""
            m = cmds.createNode("multDoubleLinear", n=name)
            for slot, v in (("input1", in1), ("input2", in2)):
                if isinstance(v, str):
                    cmds.connectAttr(v, f"{m}.{slot}")
                else:
                    cmds.setAttr(f"{m}.{slot}", v)
            return f"{m}.output"

        def _rev(name, plug):
            rv = cmds.createNode("reverse", n=name)
            cmds.connectAttr(plug, f"{rv}.inputX")
            return f"{rv}.outputX"

        def _wire_jaw_follow(mc, targets, weights):
            """Insert a follow group above mc's AUTO, constrained to
            `targets` with `weights` (plugs or constants).

            CRITICAL: the group sits AT THE MASTER, not at the origin —
            parentConstraint blends fractional weights in the constrained
            node's own frame, and a group at the origin puts the lip on a
            head-height lever arm (hundreds of units of fly-off at any
            partial weight). The master's rest position moves UP from the
            OFFSET into this group; AUTO keeps local zero (relative
            reparent) so the smile SDK stays valid.
            """
            auto = mc.replace("_CTRL", "_AUTO")
            if not cmds.objExists(auto):
                return
            rest = cmds.xform(mc, q=True, ws=True, t=True)
            par = (cmds.listRelatives(auto, p=True) or [None])[0]
            jf = cmds.group(em=True, n=mc.replace("_CTRL", "_JAWFOLLOW"))
            if par:
                cmds.parent(jf, par)
            cmds.xform(jf, ws=True, t=rest)        # jf carries the rest
            cmds.parent(auto, jf, relative=True)   # AUTO stays local 0
            # The OFFSET no longer carries the rest position (jf does).
            off = mc.replace("_CTRL", "_OFFSET")
            srcs = cmds.listConnections(f"{off}.translate",
                                        s=True, d=False) or []
            if srcs:
                # Corner master: a PMA feeds the offset (rest + corner
                # ctrl) — zero ITS rest term instead.
                cmds.setAttr(f"{srcs[0]}.input3D[0]", 0.0, 0.0, 0.0)
            else:
                cmds.setAttr(f"{off}.translate", 0.0, 0.0, 0.0)
            pc = cmds.parentConstraint(*targets, jf, mo=True)[0]
            # SHORTEST rotation interpolation — 'average' can blend the
            # jaw rotation the long way round (-335 instead of +25 deg)
            # and flip the group at fractional weights.
            cmds.setAttr(f"{pc}.interpType", 2)
            # The SEAL target (always LAST) must have NO maintained offset:
            # maintainOffset makes every target hold the lip at REST, which
            # would let full seal weight do nothing. Zeroing its offset
            # makes w_seal=1 land the lip exactly ON the seal target.
            last = len(targets) - 1
            cmds.setAttr(f"{pc}.target[{last}].targetOffsetTranslate",
                         0.0, 0.0, 0.0)
            cmds.setAttr(f"{pc}.target[{last}].targetOffsetRotate",
                         0.0, 0.0, 0.0)
            aliases = cmds.parentConstraint(pc, q=True,
                                            weightAliasList=True)
            for alias, w in zip(aliases, weights):
                if isinstance(w, str):
                    cmds.connectAttr(w, f"{pc}.{alias}")
                else:
                    cmds.setAttr(f"{pc}.{alias}", w)

        # Pair the rows by index (both run corner -> corner; REGIONS gives
        # both lips the same master count).
        n_pairs = min(nU, nL)
        for i in range(n_pairs):
            uc = upper[i]
            lc = lower[_pair_index(i, nU, nL)]
            U, L = _world_pos(uc), _world_pos(lc)

            # SEAL target at the pair midpoint, riding the half-jaw line.
            seal = cmds.group(em=True, n=f"C_lipSeal_{i + 1:02d}_GRP")
            cmds.parent(seal, ctl_grp)
            cmds.xform(seal, ws=True,
                       t=[(U[k] + L[k]) * 0.5 for k in range(3)])
            if has_jaw:
                sc_ = cmds.parentConstraint(jaw, ctl_grp, seal,
                                            mo=True)[0]
                cmds.setAttr(f"{sc_}.interpType", 2)   # static 50/50

            t = _corner_t(i, n_pairs)

            # REAL mouth-open weights. The old corner_t falloff (corner 0,
            # quarter 0.5, centre 1) stretched the lower lip into a V down
            # the chin when the jaw opened. A real mouth: the LOWER lip
            # rides the jaw almost RIGIDLY, the UPPER lip stays put, and
            # the SHARED corners travel HALF on BOTH lips (they're owned
            # 50/50 by head + jaw) — so the corner never tears and the
            # open mouth reads as a lip, not a hammock.
            blend = math.sin(0.5 * math.pi * min(1.0, 2.0 * t))
            row_specs = ((uc, 0.5 * (1.0 - blend)),     # upper: 0.5 -> 0
                         (lc, 0.5 + 0.5 * blend))       # lower: 0.5 -> 1

            for mc, jaw_f in row_specs:
                s = _sticky_plug(mc, t)
                rs = _rev(mc.replace("_CTRL", "_sticky_REV"), s)
                if has_jaw and jaw_f > 1e-4:
                    gate = _mdl(mc.replace("_CTRL", "_jawGate_MDL"),
                                f"{mouth}.jawFollow", jaw_f)
                    w_jaw = _mdl(mc.replace("_CTRL", "_jawWOpen_MDL"),
                                 gate, rs)
                    w_anchor = _mdl(
                        mc.replace("_CTRL", "_jawWAnchor_MDL"),
                        _rev(mc.replace("_CTRL", "_jawGate_REV"), gate),
                        rs)
                    _wire_jaw_follow(mc, (jaw, ctl_grp, seal),
                                     (w_jaw, w_anchor, s))
                else:
                    _wire_jaw_follow(mc, (ctl_grp, seal), (rs, s))

        print("[advFace] Wired mouth: smile / frown (arc) + pucker / spread "
              "+ lipRoll (out/in) + corners + jaw-follow + seal-target "
              "sticky lips.")

    def _build_region(self, region, jnt_grp, crv_grp, ctl_grp, drv_grp):
        """Build one region with the SKINNED-CURVE mechanism:

            master_CTRL  →  AUTO group  →  driver_JNT  ──skin──►  CURVE
                                                                    │
                                                          pointOnCurveInfo
                                                                    ▼
                                                          detail BIND joints

        The curve is SKINNED to a handful of driver joints (one per master
        ctrl). Moving a ctrl moves its driver joint, which deforms the
        curve via the skinCluster, which slides the detail joints via PCI.
        This is rock-solid — no clusters parented under controls (which
        double-transform the curve off the mesh, the bug you saw).
        """
        spec = REGIONS[region]
        pts = self.fits[region]
        if len(pts) < 3:
            cmds.warning(f"[advFace] {region} has < 3 points; skipped.")
            return

        # Size ctrls + joints from the FITTED loop, not the global SCALE —
        # so they sit proportionally on any head (no giant squares
        # swallowing a small eye).
        loop_len = sum(_dist(pts[i], pts[i + 1])
                       for i in range(len(pts) - 1))
        self._loop_len[region] = loop_len

        # --- NURBS curve through the loop points (cubic EP curve where we
        # have enough points; cubic is smoother than degree-2 — less
        # overshoot when the lid/lip deforms). Still interpolates every
        # vertex, so the per-vertex joints stay exactly on the loop. ---
        crv_deg = 3 if len(pts) >= 4 else 2
        crv = cmds.curve(n=f"{region}_CRV", d=crv_deg, ep=pts)
        cmds.parent(crv, crv_grp)
        cmds.setAttr(f"{crv}.inheritsTransform", 0)

        n_cv = cmds.getAttr(f"{crv}.spans") + cmds.getAttr(f"{crv}.degree")
        n_master = max(2, min(spec["master"], n_cv))

        # --- driver joints + master ctrls (one per master) ---
        driver_jnts = []
        ctrls = []
        for m in range(n_master):
            # CV index this master is responsible for (evenly spread).
            cv = int(round(m * (n_cv - 1) / float(max(1, n_master - 1))))
            cv = max(0, min(n_cv - 1, cv))
            cv_pos = cmds.pointPosition(f"{crv}.cv[{cv}]", w=True)

            # Driver joint at the CV — the curve will be skinned to these.
            cmds.select(cl=True)
            drv = cmds.joint(n=f"{region}_drv_{m + 1:02d}_JNT", p=cv_pos)
            cmds.setAttr(f"{drv}.radius", max(0.05, 0.004 * loop_len))
            driver_jnts.append(drv)

            # Master ctrl with an AUTO group above it. The CTRL is where
            # the animator works; the AUTO is where the automation (blink /
            # smile / zip) writes, so they compose cleanly.
            # ZEROED ctrl: the OFFSET group carries the rest position so
            # the ctrl itself sits at translate (0,0,0). A ctrl that
            # "rests" at its world position teleports to the origin the
            # moment an animator zeroes it — dragging the whole skinned
            # lip/lid curve with it. UPPER row = square + side colour,
            # LOWER row = circle + a contrasting shade (so upper vs lower
            # read apart at a glance instead of a square soup).
            is_upper = spec["pair"] == "upper"
            ctrl = _pair_ctrl(
                f"{region}_master_{m + 1:02d}_CTRL",
                max(0.05, 0.28 * loop_len / n_master),
                is_upper, _pair_color(spec["color"], is_upper))
            offset = cmds.group(ctrl,
                                n=f"{region}_master_{m + 1:02d}_OFFSET")
            cmds.xform(offset, ws=True, t=cv_pos)
            auto = cmds.group(offset,
                              n=f"{region}_master_{m + 1:02d}_AUTO")
            cmds.xform(auto, ws=True, piv=cv_pos)
            cmds.parent(auto, ctl_grp)
            # Ctrl drives its driver joint.
            cmds.parentConstraint(ctrl, drv, mo=False)
            # Float the SHAPE forward so the lid / lip master sits OUTSIDE the
            # mesh and is easy to grab (transform stays on the loop, so the
            # driver joint + skinned curve are unaffected).
            _offset_shape_fwd(ctrl, 0.10 * loop_len)
            # Scale never reached the curve (a parentConstraint carries no
            # scale): hide the dead channels.
            _lock_attrs(ctrl, ("sx", "sy", "sz"))
            ctrls.append(ctrl)

        # Parent all driver joints under the driver group AFTER creation
        # (they were created at root by the joint command chain).
        for drv in driver_jnts:
            cmds.parent(drv, drv_grp)

        # --- skin the curve to the driver joints ---
        sc = cmds.skinCluster(driver_jnts, crv, tsb=True, mi=2, dr=4.0,
                              n=f"{region}_skin")[0]
        # Pin the END CV at each corner to the corner driver so the canthus
        # can't drift. The masters are bunched toward the corners (see the
        # CV distribution above), so the closure resolves finely right next
        # to the pin and the near-corner joints still reach the seam.
        for c, drv in ((0, driver_jnts[0]), (n_cv - 1, driver_jnts[-1])):
            if 0 <= c < n_cv:
                try:
                    cmds.skinPercent(sc, f"{crv}.cv[{c}]",
                                     transformValue=[(drv, 1.0)])
                except Exception:
                    pass

        self.master_ctrls[region] = ctrls

        # --- detail BIND joints: ONE PER FITTED LOOP VERTEX, parked
        # exactly ON the vertex (its curve parameter), so weight painting
        # maps 1:1 to the mesh edge loop. Counts follow the loop you
        # selected — not a spinner.
        npoc = cmds.createNode("nearestPointOnCurve")
        cmds.connectAttr(f"{crv}.worldSpace[0]", f"{npoc}.inputCurve")
        params = []
        for p in pts:
            cmds.setAttr(f"{npoc}.inPosition", *p)
            params.append(cmds.getAttr(f"{npoc}.parameter"))
        cmds.delete(npoc)

        orient_target = (self.jaw_joint
                         if (region == "C_lipLower"
                             and cmds.objExists(self.jaw_joint))
                         else self.head_joint)
        # ONE joint per fitted loop vertex — EVERY vert of this region's
        # arc, corners included. (split_loop shares the two corners with the
        # other arc, so the corner verts end up with a joint from each arc —
        # that's intentional: each lip/lid loop matches its own joint count
        # 1:1, which is what you weight-paint against.)
        jnts = []
        for i, prm in enumerate(params):
            cmds.select(cl=True)
            j = cmds.joint(n=f"{region}_{i + 1:02d}_BIND_JNT")
            cmds.setAttr(f"{j}.radius", max(0.03, 0.008 * loop_len))
            cmds.parent(j, jnt_grp)
            pci = cmds.createNode("pointOnCurveInfo",
                                   n=f"{region}_{i + 1:02d}_PCI")
            cmds.connectAttr(f"{crv}.worldSpace[0]", f"{pci}.inputCurve")
            cmds.setAttr(f"{pci}.parameter", prm)
            cmds.setAttr(f"{pci}.turnOnPercentage", 0)
            cmds.connectAttr(f"{pci}.position", f"{j}.translate")
            if orient_target and cmds.objExists(orient_target):
                cmds.orientConstraint(orient_target, j, mo=True)
                cmds.scaleConstraint(orient_target, j, mo=True)
            jnts.append(j)
        self.detail_jnts[region] = jnts

    def _eye_centre(self, side):
        """Best-guess eye-centre world position for blink pivot: the
        average of this side's fitted lid points (upper + lower)."""
        all_pts = []
        for reg in (f"{side}_lidUpper", f"{side}_lidLower"):
            all_pts.extend(self.fits.get(reg, []))
        return tuple(_centroid(all_pts)) if all_pts else None

    # -----------------------------------------------------------------------
    # Fit persistence (on a hidden network node so it survives reopen)
    # -----------------------------------------------------------------------

    _FIT_NODE = "ADV_FACE_fits"

    def _save_fits(self):
        if not cmds.objExists(self._FIT_NODE):
            cmds.createNode("network", n=self._FIT_NODE)
        if not cmds.attributeQuery("fits", node=self._FIT_NODE,
                                    exists=True):
            cmds.addAttr(self._FIT_NODE, ln="fits", dt="string")
        cmds.setAttr(f"{self._FIT_NODE}.fits", json.dumps(self.fits),
                     type="string")

    def _load_fits(self):
        if (cmds.objExists(self._FIT_NODE)
                and cmds.attributeQuery("fits", node=self._FIT_NODE,
                                         exists=True)):
            raw = cmds.getAttr(f"{self._FIT_NODE}.fits") or "{}"
            try:
                data = json.loads(raw)
                self.fits = {k: [tuple(p) for p in v]
                             for k, v in data.items()}
            except Exception:
                pass

    def delete(self):
        try:
            import face_shapes
            face_shapes.remove()
        except ImportError:
            pass
        for n in (TOP_GROUP, self._FIT_NODE):
            if cmds.objExists(n):
                cmds.delete(n)
        self.fits = {}


# =============================================================================
# Head follow (repair an existing build without rebuilding)
# =============================================================================

CONTROLS_GROUP = "ADV_FACE_controls_GRP"
NODES_SET = "ADV_FACE_NODES_SET"


def diagnose_blink(side="L"):
    """Print what the blink is actually doing on YOUR model, so a bad eye can
    be pinpointed instead of guessed. Run after a build:

        import advanced_face; advanced_face.diagnose_blink("L")

    Reports: whether the curvature bulge points OUT of the face (+ its size),
    how completely the lid seals at blink=1, and the key joint positions."""
    bc = f"{side}_blink_CTRL"
    if not cmds.objExists(bc):
        print(f"[diagnose] no {bc} — build the advanced face first.")
        return
    ujs = sorted(cmds.ls(f"{side}_lidUpper_*_BIND_JNT") or [],
                 key=lambda j: cmds.xform(j, q=True, ws=True, t=True)[0])
    lo_crv = f"{side}_lidLower_CRV"
    bh0 = cmds.getAttr(f"{bc}.blink")

    def wp(n):
        return cmds.xform(n, q=True, ws=True, t=True)
    print(f"=== BLINK DIAGNOSTIC — {side} eye ===")
    for n in (f"{side}_eyeball_LOC", f"{side}_eye_BIND_JNT",
              "C_head_BIND_JNT"):
        if cmds.objExists(n):
            print(f"  {n:<22} {[round(v, 1) for v in wp(n)]}")
    if cmds.objExists(f"{side}_eyeball_LOC"):
        print(f"  eyeball loc scale      "
              f"{cmds.getAttr(f'{side}_eyeball_LOC.localScaleX'):.2f}  "
              f"(bigger = more curvature)")
    # curvature: does a mid upper-lid joint move OUT of the face mid-blink?
    if ujs:
        mid = ujs[len(ujs) // 2]
        cmds.setAttr(f"{bc}.blink", 0.0)
        p0 = wp(mid)
        cmds.setAttr(f"{bc}.blink", 0.5)
        p5 = wp(mid)
        d = [round(p5[k] - p0[k], 2) for k in range(3)]
        print(f"  mid-blink bulge delta   {d}  "
              f"(should point OUT of the face, not into it)")
    # closure: worst gap from each upper joint to the lower lid line
    if ujs and cmds.objExists(lo_crv):
        cmds.setAttr(f"{bc}.blink", 1.0)
        worst = max(_dist(wp(u), _nearest_on_curve(lo_crv, wp(u)))
                    for u in ujs)
        print(f"  blink=1 worst seal gap  {worst:.3f}  (0 = fully closed)")
    cmds.setAttr(f"{bc}.blink", bh0)
    print("=== if the bulge delta points INTO the face or the seal gap is "
          "large, paste this whole block back. ===")


class neutral_pose(object):
    """Every control at rest for the duration (blink 0, smile 0, jaw shut,
    shape dials 0 ...), then the pose comes back. Anything measured inside is
    measured where the face was built and modelled, not wherever the last
    pose or capture take left it."""

    _NUMERIC = ("double", "float", "doubleLinear", "doubleAngle", "long",
                "short", "bool", "enum")

    def __enter__(self):
        self.held = []
        def numeric(plug):
            try:
                return cmds.getAttr(plug, type=True) in self._NUMERIC
            except (RuntimeError, ValueError):
                return False
        for c in cmds.ls("*_CTRL", type="transform") or []:
            ud = [a for a in (cmds.listAttr(c, ud=True, k=True) or [])
                  if numeric(f"{c}.{a}")]
            for a in ("translateX", "translateY", "translateZ",
                      "rotateX", "rotateY", "rotateZ") + tuple(ud):
                plug = f"{c}.{a}"
                try:
                    if cmds.getAttr(plug, lock=True) or \
                            not cmds.getAttr(plug, se=True):
                        continue
                    dv = cmds.attributeQuery(a, node=c, listDefault=True)[0] \
                        if a in ud else 0.0
                    v = cmds.getAttr(plug)
                    if abs(v - dv) > 1e-9:
                        self.held.append((plug, v))
                        cmds.setAttr(plug, dv)
                except (RuntimeError, ValueError, TypeError):
                    pass
        return self

    def __exit__(self, *exc):
        for plug, v in reversed(self.held):
            try:
                cmds.setAttr(plug, v)
            except RuntimeError:
                pass
        return False


def _lash_row(side, which):
    """The lash-line detail joints of one lid, in row order."""
    pat = re.compile(r"^%s_lid%s_(\d+)_BIND_JNT$" % (side, which))
    js = [j for j in cmds.ls(f"{side}_lid{which}_*_BIND_JNT", type="joint")
          or [] if pat.match(j)]
    return sorted(js, key=lambda j: int(pat.match(j).group(1)))


def _base_source(joint):
    """The plug giving a detail joint its CURVE point, looking through a
    tweak PMA (input3D[0]) and a lid seal: (plug, pointOnCurveInfo)."""
    plug = (cmds.listConnections(f"{joint}.translate", s=True, d=False,
                                 p=True) or [None])[0]
    for _ in range(4):
        if not plug:
            return None, None
        node = plug.split(".")[0]
        kind = cmds.objectType(node)
        if kind == "pointOnCurveInfo":
            return plug, node
        if kind == "plusMinusAverage":
            plug = (cmds.listConnections(f"{node}.input3D[0]", s=True,
                                         d=False, p=True) or [None])[0]
        elif kind == "blendColors":
            if node.endswith("_seal_BC"):
                return plug, (cmds.listConnections(f"{node}.color2", s=True,
                                                   d=False) or [None])[0]
            return None, None
        else:
            return None, None
    return None, None


def blink_ease(side):
    """The remapValue that actually eases this side's blink: followed from
    the blink control (through a face-shapes sum if there is one), not by
    name, because a rebuild can leave a dead L_blinkEase_RMV behind and call
    the live one L_blinkEase_RMV3."""
    frontier = [f"{side}_blink_CTRL.blink"]
    if not cmds.objExists(f"{side}_blink_CTRL"):
        return None
    seen = set()
    for _ in range(5):
        nxt = []
        for plug in frontier:
            for d in cmds.listConnections(plug, s=False, d=True, p=True) or []:
                node = d.split(".")[0]
                if node in seen:
                    continue
                seen.add(node)
                kind = cmds.nodeType(node)
                if kind == "remapValue" and "blinkEase" in node:
                    return node
                outs = {"plusMinusAverage": ("output1D",),
                        "clamp": ("outputR",),
                        "unitConversion": ("output",),
                        "multDoubleLinear": ("output",)}.get(kind, ())
                nxt += [f"{node}.{o}" for o in outs]
        frontier = nxt
    return None


def _curve_param_across(curve_plug, point, axis0, axis1, samples=240):
    """Fraction (0..1) along a curve whose point sits straight ACROSS from
    `point`: the same position along the eye's corner-to-corner axis."""
    ax = [b - a for a, b in zip(axis0, axis1)]
    ll = sum(v * v for v in ax) or 1.0

    def along(p):
        return sum((p[k] - axis0[k]) * ax[k] for k in range(3)) / ll
    want = along(point)
    tmp = cmds.createNode("pointOnCurveInfo")
    try:
        cmds.connectAttr(curve_plug, f"{tmp}.inputCurve")
        cmds.setAttr(f"{tmp}.turnOnPercentage", 1)
        best = (1e30, 0.0)
        for i in range(samples + 1):
            f = i / float(samples)
            cmds.setAttr(f"{tmp}.parameter", f)
            d = abs(along(cmds.getAttr(f"{tmp}.position")[0]) - want)
            if d < best[0]:
                best = (d, f)
        return best[1]
    finally:
        cmds.delete(tmp)


def add_lid_seal(side=None, start=0.7):
    with neutral_pose():
        return _add_lid_seal(side, start)


def _add_lid_seal(side=None, start=0.7):
    """Close the blink EXACTLY. Each lid is a curve through its master
    controls: the masters meet their partners, but between them the upper
    and lower curves bend differently, so a detail joint here and there
    stayed a little open (or went a little through). Over the last part of
    the blink (from `start` of the eased blink to 1) every upper lash joint
    and the point straight across on the lower lid are pulled to the same
    point, so the whole lash line seals. Where they meet follows
    blinkHeight (0: on the lower lid, which then stays put; 1: on the
    upper), as far from the eyeball centre as the outer of the two lids, so
    a seal never sinks a lid into the eye. lidOverlap (on the
    blink control) closes the lids that much PAST each other, for a mesh
    whose eye opening sits a little outside the lash joints. Below `start`
    nothing changes.
    Works on built rigs; safe to run again. Returns joints sealed."""
    made = 0
    for s in ([side] if side else ["L", "R"]):
        ease = blink_ease(s)
        up, lo = _lash_row(s, "Upper"), _lash_row(s, "Lower")
        if not ease or len(up) < 3 or len(lo) < 3:
            continue
        rm = f"{s}_lidSeal_RMV"
        if cmds.objExists(rm) and cmds.listConnections(
                f"{rm}.inputValue", s=True, d=False) != [ease]:
            cmds.delete(rm)                    # listening to a dead blink
        if not cmds.objExists(rm):
            rm = cmds.createNode("remapValue", n=rm)
            cmds.connectAttr(f"{ease}.outValue", f"{rm}.inputValue")
            for idx in (0, 1):
                cmds.setAttr(f"{rm}.value[{idx}].value_Position", float(idx))
                cmds.setAttr(f"{rm}.value[{idx}].value_FloatValue",
                             float(idx))
                cmds.setAttr(f"{rm}.value[{idx}].value_Interp", 2)
        cmds.setAttr(f"{rm}.inputMin", float(start))
        cmds.setAttr(f"{rm}.inputMax", 1.0)
        blink_ctrl = f"{s}_blink_CTRL"
        if not cmds.attributeQuery("lidOverlap", node=blink_ctrl,
                                   exists=True):
            cmds.addAttr(blink_ctrl, ln="lidOverlap", at="double", min=0.0,
                         dv=0.0, k=True)
        if not cmds.attributeQuery("blinkHeight", node=blink_ctrl,
                                   exists=True):
            _ensure_attr(blink_ctrl, "blinkHeight", 0, 1, 0.1)
        bh = f"{blink_ctrl}.blinkHeight"
        rev = f"{s}_lidSealHt_REV"
        if not cmds.objExists(rev):
            rev = cmds.createNode("reverse", n=rev)
            cmds.connectAttr(bh, f"{rev}.inputX")
        # the upper lid closes (1 - blinkHeight) of the overlap, the lower
        # blinkHeight of it: blinkHeight 0 keeps the lower lid still
        laps = {}
        for key, share in (("Up", f"{rev}.outputX"), ("Lo", bh)):
            md = f"{s}_lidOverlap{key}_MDL"
            if not cmds.objExists(md):
                md = cmds.createNode("multDoubleLinear", n=md)
                cmds.connectAttr(f"{blink_ctrl}.lidOverlap", f"{md}.input1")
                cmds.connectAttr(share, f"{md}.input2")
            laps[key] = f"{md}.output"
        for old in (f"{s}_lidOverlapHalf_MDL",):
            if cmds.objExists(old):
                cmds.delete(old)
        head = next((h for h in ("C_head_BIND_JNT",) if cmds.objExists(h)),
                    None)
        pci = {j: _base_source(j)[1] for j in up + lo}
        if not (pci.get(up[1]) and pci.get(lo[1])):
            continue
        curves = {}
        for key, row in (("up", up), ("lo", lo)):
            src = cmds.listConnections(f"{pci[row[1]]}.inputCurve", s=True,
                                       d=False, p=True)
            curves[key] = src[0] if src else None
        if not (curves["up"] and curves["lo"]):
            continue
        # the eye's corner-to-corner axis (at rest), to find "straight across"
        c0 = cmds.xform(up[0], q=True, ws=True, t=True)
        c1 = cmds.xform(up[-1], q=True, ws=True, t=True)
        # the eyeball's centre, live (it rides the head)
        centre = next((n for n in (f"{s}_eyeball_LOC", f"{s}_eye_BIND_JNT")
                       if cmds.objExists(n)), None)
        dm = f"{s}_lidSeal_centre_DM"
        if centre and not cmds.objExists(dm):
            dm = cmds.createNode("decomposeMatrix", n=dm)
            cmds.connectAttr(f"{centre}.worldMatrix[0]", f"{dm}.inputMatrix")
        ctr = f"{dm}.outputTranslate" if centre else None
        for row, other_curve, is_up in ((up, curves["lo"], True),
                                        (lo, curves["up"], False)):
            n = len(row)
            for i, j in enumerate(row):
                if i in (0, n - 1):
                    continue                   # the corners are shared
                bc = j.replace("_BIND_JNT", "_seal_BC")
                avg = j.replace("_BIND_JNT", "_sealAvg_PMA")
                across = j.replace("_BIND_JNT", "_sealAcross_PCI")
                if cmds.objExists(bc):
                    if cmds.listConnections(f"{bc}.output", s=False, d=True) \
                            and cmds.listConnections(f"{bc}.blender", s=True,
                                                     d=False) == [rm] \
                            and (cmds.objExists(
                                j.replace("_BIND_JNT", "_sealOut_PMA"))
                                 or not ctr) \
                            and cmds.objExists(
                                j.replace("_BIND_JNT", "_sealLap_PMA")) \
                            and cmds.objExists(
                                j.replace("_BIND_JNT", "_sealMix_BC")):
                        continue               # already sealed, and live
                    _unseal(j)
                p_self = pci.get(j)
                if not p_self:
                    continue
                dests = [d for d in cmds.listConnections(
                    f"{p_self}.position", s=False, d=True, p=True) or []
                    if not d.split(".")[0].endswith(_SEAL_SUFFIXES)]
                frac = _curve_param_across(
                    other_curve, cmds.getAttr(f"{p_self}.position")[0],
                    c0, c1)
                across = cmds.createNode("pointOnCurveInfo", n=across)
                cmds.connectAttr(other_curve, f"{across}.inputCurve")
                cmds.setAttr(f"{across}.turnOnPercentage", 1)
                cmds.setAttr(f"{across}.parameter", frac)
                upper = f"{p_self}.position" if is_up else \
                    f"{across}.position"
                lower = f"{across}.position" if is_up else \
                    f"{p_self}.position"
                mix = cmds.createNode(
                    "blendColors", n=j.replace("_BIND_JNT", "_sealMix_BC"))
                cmds.connectAttr(upper, f"{mix}.color1")
                cmds.connectAttr(lower, f"{mix}.color2")
                cmds.connectAttr(bh, f"{mix}.blender")
                meet = f"{mix}.output"
                if ctr:
                    meet = _outer_shell(j, meet, upper, lower, ctr, bh)
                # lidOverlap: close PAST the meeting point, toward the other
                # lid (the mesh's opening edge can sit a little outside the
                # lash joints; this closes it)
                a_ = cmds.getAttr(f"{across}.position")[0]
                o_ = cmds.getAttr(f"{p_self}.position")[0]
                d_ = om2.MVector(*[x - y for x, y in zip(a_, o_)])
                if d_.length() > 1e-6:
                    d_.normalize()
                meet = _seal_overlap(j, meet, d_,
                                     laps["Up" if is_up else "Lo"], head)
                bc = cmds.createNode("blendColors", n=bc)
                cmds.connectAttr(meet, f"{bc}.color1")
                cmds.connectAttr(f"{p_self}.position", f"{bc}.color2")
                cmds.connectAttr(f"{rm}.outValue", f"{bc}.blender")
                for d in dests:
                    cmds.connectAttr(f"{bc}.output", d, f=True)
                made += 1
    if made:
        print(f"[advFace] Lid seal: {made} lash joints now close exactly on "
              f"a full blink.")
    return made


_SEAL_SUFFIXES = ("_seal_BC", "_sealAvg_PMA", "_sealMix_BC", "_sealR_BC",
                  "_sealAcross_PCI",
                  "_sealVec_PMA", "_sealRself_DB", "_sealRacross_DB",
                  "_sealR_CND", "_sealLen_DB", "_sealScale_MD", "_sealDir_MD",
                  "_sealOut_PMA", "_sealDir_VP", "_sealLapAmt_MD",
                  "_sealLap_PMA")


def _seal_overlap(joint, meet, direction, amount, head):
    """meet + direction * amount, the direction held in the head's frame so
    it turns with the head. Returns the output plug."""
    base = joint.replace("_BIND_JNT", "")
    if head:
        hm = om2.MMatrix(cmds.getAttr(f"{head}.worldMatrix[0]"))
        local = direction * hm.inverse()
        vp = cmds.createNode("vectorProduct", n=base + "_sealDir_VP")
        cmds.setAttr(f"{vp}.operation", 3)
        cmds.setAttr(f"{vp}.input1", local.x, local.y, local.z)
        cmds.connectAttr(f"{head}.worldMatrix[0]", f"{vp}.matrix")
        vec = f"{vp}.output"
    else:
        vp = cmds.createNode("plusMinusAverage", n=base + "_sealDir_VP")
        cmds.setAttr(f"{vp}.input3D[0]", direction.x, direction.y,
                     direction.z)
        vec = f"{vp}.output3D"
    md = cmds.createNode("multiplyDivide", n=base + "_sealLapAmt_MD")
    cmds.connectAttr(vec, f"{md}.input1")
    for ax in "XYZ":
        cmds.connectAttr(amount, f"{md}.input2{ax}")
    out = cmds.createNode("plusMinusAverage", n=base + "_sealLap_PMA")
    cmds.connectAttr(meet, f"{out}.input3D[0]")
    cmds.connectAttr(f"{md}.output", f"{out}.input3D[1]")
    return f"{out}.output3D"


def _outer_shell(joint, mid, upper, lower, centre, bh):
    """`mid` pushed out from the eyeball centre to the OUTER of the two
    lids (a straight-line blend between two points on a ball dips inside
    it): centre + (mid - centre) * max(|upper-c|, |lower-c|) / |mid-c|.
    Returns the output plug."""
    a, b = upper, lower
    base = joint.replace("_BIND_JNT", "")
    vec = cmds.createNode("plusMinusAverage", n=base + "_sealVec_PMA")
    cmds.setAttr(f"{vec}.operation", 2)                       # mid - c
    cmds.connectAttr(mid, f"{vec}.input3D[0]")
    cmds.connectAttr(centre, f"{vec}.input3D[1]")
    dists = []
    for plug, tag in ((a, "Rself"), (b, "Racross"), (mid, "Len")):
        db = cmds.createNode("distanceBetween", n=f"{base}_seal{tag}_DB")
        cmds.connectAttr(plug, f"{db}.point1")
        cmds.connectAttr(centre, f"{db}.point2")
        dists.append(f"{db}.distance")
    # the OUTER of the two lids: going inside either one lets the eyeball
    # show through the lid skin
    cnd = cmds.createNode("condition", n=base + "_sealR_CND")
    cmds.setAttr(f"{cnd}.operation", 2)                       # greater than
    cmds.connectAttr(dists[0], f"{cnd}.firstTerm")
    cmds.connectAttr(dists[1], f"{cnd}.secondTerm")
    cmds.connectAttr(dists[0], f"{cnd}.colorIfTrueR")
    cmds.connectAttr(dists[1], f"{cnd}.colorIfFalseR")
    sc = cmds.createNode("multiplyDivide", n=base + "_sealScale_MD")
    cmds.setAttr(f"{sc}.operation", 2)                        # r / |mid-c|
    cmds.connectAttr(f"{cnd}.outColorR", f"{sc}.input1X")
    cmds.connectAttr(dists[2], f"{sc}.input2X")
    dv = cmds.createNode("multiplyDivide", n=base + "_sealDir_MD")
    cmds.connectAttr(f"{vec}.output3D", f"{dv}.input1")
    for ax in "XYZ":
        cmds.connectAttr(f"{sc}.outputX", f"{dv}.input2{ax}")
    out = cmds.createNode("plusMinusAverage", n=base + "_sealOut_PMA")
    cmds.connectAttr(centre, f"{out}.input3D[0]")
    cmds.connectAttr(f"{dv}.output", f"{out}.input3D[1]")
    return f"{out}.output3D"


def _unseal(joint):
    """Take a lid seal off one joint (its consumers go back to the curve)."""
    bc = joint.replace("_BIND_JNT", "_seal_BC")
    if not cmds.objExists(bc):
        return
    src = cmds.listConnections(f"{bc}.color2", s=True, d=False, p=True)
    for d in cmds.listConnections(f"{bc}.output", s=False, d=True,
                                  p=True) or []:
        cmds.disconnectAttr(f"{bc}.output", d)
        if src:
            cmds.connectAttr(src[0], d, f=True)
    cmds.delete([n for n in (joint.replace("_BIND_JNT", suf)
                             for suf in _SEAL_SUFFIXES) if cmds.objExists(n)])


def repair_face():
    """Bring a face built with an older version up to date: working mouth
    corners, one smile, lid seal. Safe to run again."""
    wired = repair_mouth_corners()
    sealed = add_lid_seal()
    return {"corners": wired, "sealed": sealed}


def add_tertiary_controls(head_joint="C_head_BIND_JNT"):
    """Add a small TWEAK control on every advanced-face detail joint, so any
    single lid / lip joint can be nudged BY HAND on top of the blink to
    perfect the close (AS-style fine control).

    Each tweak ADDS its offset to the joint on top of the live curve drive:
    joint.translate = curve(PCI) + (tweak.translate rotated by the head), via
    a vectorProduct so the nudge rides head rotation correctly. The control
    itself RIDES the joint's live curve point (so it travels WITH the lid on a
    blink instead of staying behind). The tweaks sit under a hidden
    `ADV_FACE_tweaks_GRP`; a `showTweaks` toggle on the blink / mouth controls
    reveals them. Idempotent — re-running upgrades existing tweaks (adds the
    follow) without disturbing any nudge you've dialled. Returns the controls
    created."""
    head = head_joint if cmds.objExists(head_joint) else None

    def _curve_driven(j_):
        """True if the joint rides a curve point — DIRECTLY (un-tweaked),
        through a lid seal, or through a tweak PMA (already tweaked, so a
        re-run can still find + upgrade it)."""
        return _base_source(j_)[1] is not None

    joints = []
    for pat in ("*_lid*_BIND_JNT", "*_lip*_BIND_JNT"):
        for j in (cmds.ls(pat, type="joint") or []):
            if _curve_driven(j):
                joints.append(j)
    if not joints:
        cmds.warning("[advFace] no curve-driven detail joints found — build "
                     "the advanced face first.")
        return []
    grp = "ADV_FACE_tweaks_GRP"
    if not cmds.objExists(grp):
        grp = cmds.group(em=True, n=grp)
        cmds.setAttr(f"{grp}.visibility", 0)
        if cmds.objExists(CONTROLS_GROUP):
            cmds.parent(grp, CONTROLS_GROUP)
    # Sit the group EXACTLY at the controls' frame (parent-constrained to the
    # head). Zeroing its local TRS makes every tweak OFFSET inherit the HEAD
    # orientation, so a tweak's local nudge maps the same way the joint offset
    # does (vectorProduct by head.worldMatrix) — control + joint stay glued.
    for at in ("translate", "rotate"):
        try:
            cmds.setAttr(f"{grp}.{at}", 0, 0, 0)
        except Exception:
            pass

    made = []
    for j in sorted(joints):
        tw = j.replace("_BIND_JNT", "") + "_tweak_CTRL"
        # the joint's BASE point: the curve point, or the lid seal on it
        base, pci = _base_source(j)
        if not pci:
            continue
        off = tw.replace("_CTRL", "_OFFSET")
        if not cmds.objExists(tw):
            rad = max(0.08, cmds.getAttr(f"{j}.radius") * 1.6)
            ctrl = create_circle_ctrl(tw, radius=rad, normal=(0, 0, 1),
                                      color=24)
            off = cmds.group(ctrl, n=off)
            cmds.parent(off, grp)
            _lock_attrs(ctrl, attrs=("rx", "ry", "rz", "sx", "sy", "sz", "v"))
            # head-relative offset so the nudge rides head rotation
            if head:
                vp = cmds.createNode("vectorProduct", n=f"{tw}_VP")
                cmds.setAttr(f"{vp}.operation", 3)         # vector x matrix
                cmds.connectAttr(f"{ctrl}.translate", f"{vp}.input1")
                # the control's OWN frame (its parent): a nudge moves the
                # joint exactly the way the control moves in the viewport
                # (the head joint's axes are rotated on a biped, so using
                # them pushed an 'up' nudge sideways)
                cmds.connectAttr(f"{ctrl}.parentMatrix[0]", f"{vp}.matrix")
                off_plug = f"{vp}.output"
            else:
                off_plug = f"{ctrl}.translate"
            # joint.translate = curve(PCI) + offset
            pma = cmds.createNode("plusMinusAverage", n=f"{tw}_PMA")
            cmds.connectAttr(base, f"{pma}.input3D[0]")
            cmds.connectAttr(off_plug, f"{pma}.input3D[1]")
            cmds.disconnectAttr(base, f"{j}.translate")
            cmds.connectAttr(f"{pma}.output3D", f"{j}.translate", f=True)
            made.append(ctrl)
        # upgrade tweaks made before the frame fix
        vp_ = f"{tw}_VP"
        if cmds.objExists(vp_) and cmds.objExists(tw) and cmds.listConnections(
                f"{vp_}.matrix", s=True, d=False, p=True) != [
                f"{tw}.parentMatrix"]:
            cmds.connectAttr(f"{tw}.parentMatrix[0]", f"{vp_}.matrix", f=True)
        # FOLLOW: drive the OFFSET to the joint's LIVE curve point so the
        # control travels WITH the lid on a blink (it used to stay behind).
        # The OFFSET lives in the head-following group, so convert the world
        # curve point into that group's space (pointMatrixMult by its inverse).
        fpmm = f"{tw}_followPMM"
        if not cmds.objExists(fpmm) and cmds.objExists(off):
            fpmm = cmds.createNode("pointMatrixMult", n=fpmm)
            cmds.connectAttr(base, f"{fpmm}.inPoint")
            cmds.connectAttr(f"{grp}.worldInverseMatrix[0]", f"{fpmm}.inMatrix")
            cmds.connectAttr(f"{fpmm}.output", f"{off}.translate", f=True)
            for r in "XYZ":
                try:
                    cmds.setAttr(f"{off}.rotate{r}", 0)
                except Exception:
                    pass

    # showTweaks toggle on the blink + mouth controls (hidden by default)
    drivers = [c for c in (cmds.ls("?_blink_CTRL") or []) if cmds.objExists(c)]
    if cmds.objExists("C_mouth_CTRL"):
        drivers.append("C_mouth_CTRL")
    if drivers:
        main = drivers[0]
        if not cmds.attributeQuery("showTweaks", node=main, exists=True):
            cmds.addAttr(main, ln="showTweaks", at="bool", k=True, dv=0)
        try:
            cmds.connectAttr(f"{main}.showTweaks", f"{grp}.visibility", f=True)
        except Exception:
            pass
        for d in drivers[1:]:
            if not cmds.attributeQuery("showTweaks", node=d, exists=True):
                try:
                    cmds.addAttr(d, ln="showTweaks",
                                 proxy=f"{main}.showTweaks")
                except Exception:
                    pass
    cmds.select(cl=True)
    print(f"[advFace] Added {len(made)} per-joint tweak control(s) (hidden — "
          f"turn on 'showTweaks' on a blink / mouth ctrl to reveal them, then "
          f"nudge any lid/lip joint by hand).")
    return made


def attach_to_head(head_joint="C_head_BIND_JNT"):
    """Make an ALREADY-BUILT advanced face follow the head.

    The detail joints are pointOnCurveInfo-driven (their .translate is a live
    connection to the curve), so they CANNOT be moved by parenting them or
    the group under the head — that connection wins. The only thing that
    moves them is making the CONTROLS follow the head, which re-drives the
    curve. This:
       * undoes any manual re-parenting of ADV_FACE_GRP (a no-op that just
         clutters), putting it back at the world root,
       * (re)constrains the controls group to the head joint.

    Use it to repair a face built before the follow fix, or after a manual
    parent attempt. Returns True on success.
    """
    if not cmds.objExists(CONTROLS_GROUP):
        cmds.warning("[advFace] No advanced face found — build it first.")
        return False
    if not cmds.objExists(head_joint):
        cmds.warning(f"[advFace] Head joint '{head_joint}' not found.")
        return False
    # Undo a manual parent of the top group (harmless but tidy).
    if cmds.objExists(TOP_GROUP):
        if cmds.listRelatives(TOP_GROUP, p=True):
            cmds.parent(TOP_GROUP, world=True)
    # Replace any existing follow constraint with a clean one.
    for c in (cmds.listRelatives(CONTROLS_GROUP, type="parentConstraint")
              or []):
        cmds.delete(c)
    cmds.parentConstraint(head_joint, CONTROLS_GROUP, mo=True)
    print(f"[advFace] Advanced face attached to '{head_joint}' — it now "
          f"follows the head.")
    return True


# =============================================================================
# Replace the standard face's eyelids + lips
# =============================================================================

# The STANDARD (joint-based) face builds these eyelid + lip joints/ctrls.
# The mesh-conforming advanced face REPLACES them, so on build we delete
# them to avoid two sets of lid/lip joints fighting over the same mesh.
_STD_EYELID_NAMES = [
    f"{s}_eyelid{ud}{part}"
    for s in ("L", "R") for ud in ("Upper", "Lower")
    for part in ("Inner", "Mid", "Outer")
]
_STD_LIP_NAMES = [
    "C_upperLip", "L_upperLipMid", "R_upperLipMid",
    "C_lowerLip", "L_lowerLipMid", "R_lowerLipMid",
]


def _under_top(node):
    path = (cmds.ls(node, long=True) or [""])[0]
    return ("|%s|" % TOP_GROUP) in path


def _make_mouth_corners(upper, lower, centre, width, ctl_grp,
                        retire_standard=True):
    """L / R mouth corner controls that pose the shared corner masters of
    both lips. The standard face has its own `*_mouthCorner_CTRL` (moving a
    standard corner joint the face bind never uses): it's retired first, or
    its name kept the lip corner control from being made at all and dragging
    a corner did nothing. Returns the number of corners wired."""
    nU, nL = len(upper), len(lower)
    wired = 0
    for idx in (0, nU - 1):
        cpos = _world_pos(upper[idx])
        sd = "L" if cpos[0] >= centre[0] else "R"
        name = f"{sd}_mouthCorner_CTRL"
        if cmds.objExists(name) and not _under_top(name) and retire_standard:
            jnt = f"{sd}_mouthCorner_BIND_JNT"
            skinned = cmds.objExists(jnt) and bool(cmds.listConnections(
                f"{jnt}.worldMatrix", type="skinCluster", s=False, d=True))
            for suffix in ("_AUTO", "_OFFSET", "_CTRL", "_BIND_JNT"):
                node = f"{sd}_mouthCorner{suffix}"
                if not cmds.objExists(node):
                    continue
                if skinned:
                    # A mesh is already bound to it: keep it, out of the way.
                    cmds.rename(node, f"{sd}_mouthCornerStd{suffix}")
                else:
                    cmds.delete(node)
        if cmds.objExists(name):
            continue   # degenerate loop (both ends on one side), or kept
        cc = create_square_ctrl(name, size=max(0.05, 0.08 * width),
                                normal=(0, 0, 1),
                                color=(COLOR_L if sd == "L" else COLOR_R))
        # Position the OFFSET group (not the ctrl) so the ctrl sits at
        # zero — its translate is what drives the corner masters.
        coff = cmds.group(cc, n=f"{sd}_mouthCorner_OFFSET")
        cmds.xform(coff, ws=True, t=cpos)
        cmds.parent(coff, ctl_grp)
        _offset_shape_fwd(cc, 0.2 * width)   # float the corner ctrl out
        _lock_attrs(cc, ("rx", "ry", "rz", "sx", "sy", "sz"))
        for m in (upper[idx], lower[_pair_index(idx, nU, nL)]):
            off = m.replace("_CTRL", "_OFFSET")
            if not cmds.objExists(off):
                continue
            src = cmds.listConnections(f"{off}.translate", s=True, d=False,
                                       p=True) or []
            if src:
                # Already summed (a rebuilt corner): feed the existing PMA.
                node = src[0].split(".")[0]
                if cmds.nodeType(node) == "plusMinusAverage":
                    cmds.connectAttr(f"{cc}.translate", f"{node}.input3D[1]",
                                     f=True)
                continue
            # The OFFSET carries the master's REST position (or zero once
            # the jaw-follow group took it over), so the corner ctrl must
            # ADD to it (a direct connect would teleport the master).
            rest = cmds.getAttr(f"{off}.translate")[0]
            pma = cmds.createNode(
                "plusMinusAverage", n=m.replace("_CTRL", "_corner_PMA"))
            cmds.setAttr(f"{pma}.input3D[0]", *rest)
            cmds.connectAttr(f"{cc}.translate", f"{pma}.input3D[1]")
            cmds.connectAttr(f"{pma}.output3D", f"{off}.translate", f=True)
        wired += 1
    return wired


def link_smile_to_cheeks(mouth="C_mouth_CTRL", jaw="C_jaw_CTRL"):
    """ONE smile. The standard face's `C_jaw_CTRL.smile` lifted the cheeks
    and its corner joints, but once the advanced lips replace the standard
    mouth, jaw.smile only moved the cheeks while `C_mouth_CTRL.smile` only
    moved the lips. Hand the cheek lift to the mouth smile, then hide the
    jaw's leftover smile and lipsSeal dials (lipsSeal did nothing on the
    advanced lips; zip is the seal). Returns the cheek links moved.

    The jaw smile runs 0..10 and the mouth smile -1..1, so the mouth smile
    goes through a x10 remap (clamped at 0: a frown doesn't lift cheeks)."""
    if not (cmds.objExists(mouth) and cmds.objExists(jaw)
            and cmds.attributeQuery("smile", node=mouth, exists=True)):
        return 0
    moved = 0
    mdl, cl = f"{mouth}_smileCheek_MDL", f"{mouth}_smileCheek_CLAMP"
    dests = []
    if cmds.attributeQuery("smile", node=jaw, exists=True):
        dests = [d for d in cmds.listConnections(f"{jaw}.smile", s=False,
                                                 d=True, p=True) or []
                 if cmds.nodeType(d.split(".")[0]).startswith("animCurve")]
    linked = cmds.listConnections(f"{cl}.outputR", s=False, d=True,
                                  p=True) or [] if cmds.objExists(cl) else []
    if dests or linked:
        if not cmds.objExists(mdl):
            mdl = cmds.createNode("multDoubleLinear", n=mdl)
            cmds.setAttr(f"{mdl}.input2", 10.0)
        # (re)connect: a rebuilt mouth control is a new node
        if cmds.listConnections(f"{mdl}.input1", s=True, d=False,
                                p=True) != [f"{mouth}.smile"]:
            cmds.connectAttr(f"{mouth}.smile", f"{mdl}.input1", f=True)
        if not cmds.objExists(cl):
            cl = cmds.createNode("clamp", n=cl)
            cmds.setAttr(f"{cl}.maxR", 10.0)
        if not cmds.listConnections(f"{cl}.inputR", s=True, d=False):
            cmds.connectAttr(f"{mdl}.output", f"{cl}.inputR", f=True)
        for dest in dests:
            cmds.disconnectAttr(f"{jaw}.smile", dest)
            cmds.connectAttr(f"{cl}.outputR", dest, f=True)
            moved += 1
    for attr in ("smile", "lipsSeal"):
        if cmds.attributeQuery(attr, node=jaw, exists=True):
            try:
                cmds.setAttr(f"{jaw}.{attr}", 0)
                cmds.setAttr(f"{jaw}.{attr}", k=False, cb=False)
            except Exception:
                pass
    return moved


def repair_mouth_corners():
    """For faces built before the corner fix: give the advanced lips working
    L / R corner controls, join the smile to the cheeks and hide the dead
    master scale channels. Safe to run again. Returns corners wired."""
    upper = [c for c in sorted(cmds.ls("C_lipUpper_master_*_CTRL",
                                       type="transform") or [])]
    lower = [c for c in sorted(cmds.ls("C_lipLower_master_*_CTRL",
                                       type="transform") or [])]
    wired = 0
    ctl_grp = "ADV_FACE_controls_GRP"
    if len(upper) >= 3 and len(lower) >= 3 and cmds.objExists(ctl_grp):
        allp = [_world_pos(c) for c in upper + lower]
        centre = _centroid(allp)
        xs = [q[0] for q in allp]
        width = (max(xs) - min(xs)) or 1.0
        wired = _make_mouth_corners(upper, lower, centre, width, ctl_grp)
        link_smile_to_cheeks()
    for ctrl in cmds.ls("*_lid*_master_*_CTRL", "*_lip*_master_*_CTRL",
                        type="transform") or []:
        _lock_attrs(ctrl, ("sx", "sy", "sz"))
    return wired


def remove_standard_lids_lips(eyelids=True, lips=True):
    """Delete the STANDARD face's eyelid and/or lip joints + their controls,
    so the mesh-conforming advanced face (which replaces them) doesn't
    collide / double up. The rest of the standard face — jaw, eyes, brow,
    cheeks, nose, tongue, teeth, ears — is left untouched.

    Returns the number of nodes deleted.
    """
    names = []
    if eyelids:
        names += _STD_EYELID_NAMES
    if lips:
        names += _STD_LIP_NAMES
    deleted = 0
    for name in names:
        # Delete the control's top group first (cascades OFFSET + CTRL),
        # then the BIND joint (a separate hierarchy under the head).
        for suffix in ("_AUTO", "_OFFSET", "_CTRL", "_BIND_JNT"):
            node = f"{name}{suffix}"
            if cmds.objExists(node):
                try:
                    cmds.delete(node)
                    deleted += 1
                except Exception:
                    pass
    if deleted:
        print(f"[advFace] Removed {deleted} standard eyelid/lip node(s) — "
              f"replaced by the advanced face.")
    return deleted


# =============================================================================
# One-click face bind (select the face mesh -> bind it to the face joints)
# =============================================================================

# Face-region BIND joints only (never arms/legs): the advanced-face detail
# joints + the head / jaw / neck / eye / brow / cheek / nose / ear / tongue
# anchors + any leftover standard eyelid/lip joints.
_FACE_JOINT_PATTERNS = [
    "*_lid*_BIND_JNT", "*_lip*_BIND_JNT",          # advanced face detail
    "C_head_BIND_JNT", "C_neck*_BIND_JNT", "C_jaw*_BIND_JNT",
    "?_eye_BIND_JNT", "C_eye*_BIND_JNT",
    "?_brow*_BIND_JNT", "?_cheek*_BIND_JNT", "C_nose*_BIND_JNT",
    "?_ear*_BIND_JNT", "C_tongue*_BIND_JNT",
    "*_eyelid*_BIND_JNT",                          # standard (if no adv face)
]


def face_bind_joints():
    """Every face-region BIND joint a face mesh should skin to."""
    js = set()
    for pat in _FACE_JOINT_PATTERNS:
        for j in cmds.ls(pat, type="joint") or []:
            js.add(j)
    return sorted(js)


def _selected_meshes():
    out = []
    for s in cmds.ls(sl=True, transforms=True) or []:
        if cmds.listRelatives(s, type="mesh", ni=True):
            out.append(s)
    # also accept a directly-selected mesh shape
    for s in cmds.ls(sl=True, type="mesh", ni=True) or []:
        par = (cmds.listRelatives(s, p=True) or [None])[0]
        if par and par not in out:
            out.append(par)
    return out


def eyelid_bind_joints():
    """Just the EYELID joints (both lids' lash + crease rows) + the eye and
    head anchors — for a clean isolated eyelid bind."""
    js = set()
    for pat in ("*_lid*_BIND_JNT", "*_eyelid*_BIND_JNT",
                "?_eye_BIND_JNT", "C_head_BIND_JNT"):
        for j in cmds.ls(pat, type="joint") or []:
            js.add(j)
    return sorted(js)


def _mesh_skincluster(m):
    """The skinCluster already deforming mesh `m`, or None."""
    for s in (cmds.listRelatives(m, s=True, ni=True) or [m]):
        scs = cmds.ls(cmds.listHistory(s) or [], type="skinCluster")
        if scs:
            return scs[0]
    return None


def _reweight_region(mesh, sc, target_joints, m_inf=4):
    """NON-DESTRUCTIVE bind for a COMBINED mesh (e.g. one head+body geo). Adds
    `target_joints` to the EXISTING skinCluster and reweights ONLY the verts in
    that region — the ones closer to a target joint than to any OTHER joint —
    to the nearest target joints (inverse-distance). The rest of the mesh (the
    body) keeps its weights untouched. Returns the # of verts reweighted."""
    import maya.api.OpenMaya as om
    import maya.api.OpenMayaAnim as oma
    tset = set(target_joints)
    tgt = [j for j in target_joints if cmds.objExists(j)]
    if not tgt:
        return 0
    other = [j for j in (cmds.ls(type="joint") or [])
             if j.endswith("_BIND_JNT") and j not in tset]
    # add the target joints as influences (keeps the existing body weights)
    have = set(cmds.skinCluster(sc, q=True, inf=True) or [])
    add = [j for j in tgt if j not in have]
    if add:
        cmds.skinCluster(sc, e=True, ai=add, lw=True, wt=0.0)
    tpos = [(_world_pos(j), j) for j in tgt]
    opos = [_world_pos(j) for j in other]

    msel = om.MSelectionList()
    msel.add(mesh)
    mdag = msel.getDagPath(0)
    mdag.extendToShape()
    pts = om.MFnMesh(mdag).getPoints(om.MSpace.kWorld)
    ssel = om.MSelectionList()
    ssel.add(sc)
    mfn = oma.MFnSkinCluster(ssel.getDependNode(0))
    infs = mfn.influenceObjects()
    idx = {infs[i].partialPathName(): i for i in range(len(infs))}
    n = len(infs)

    def d2(p, q):
        return (p[0] - q[0]) ** 2 + (p[1] - q[1]) ** 2 + (p[2] - q[2]) ** 2

    region, region_w = [], {}
    for vi in range(len(pts)):
        p = (pts[vi].x, pts[vi].y, pts[vi].z)
        td = [(d2(p, tp), j) for tp, j in tpos]
        nf = min(d for d, _ in td)
        nb = min((d2(p, op) for op in opos), default=1e30)
        if nf > nb:
            continue                     # body region -> leave it alone
        td.sort()
        ws = [(j, 1.0 / (d + 1e-9)) for d, j in td[:m_inf]]
        s = sum(w for _, w in ws) or 1.0
        region_w[vi] = {j: w / s for j, w in ws}
        region.append(vi)
    if not region:
        return 0

    comp = om.MFnSingleIndexedComponent()
    cobj = comp.create(om.MFn.kMeshVertComponent)
    comp.addElements(region)
    weights = om.MDoubleArray(len(region) * n, 0.0)
    for row, vi in enumerate(region):
        for j, w in region_w[vi].items():
            ji = idx.get(j)
            if ji is None:
                ji = idx.get(j.split("|")[-1])
            if ji is not None:
                weights[row * n + ji] = w
    mfn.setWeights(mdag, cobj, om.MIntArray(list(range(n))), weights, True)
    return len(region)


def _bind_meshes(meshes, jnts, max_influences, what):
    """Bind each mesh to `jnts`. If a mesh is ALREADY skinned (e.g. a single
    head+body geo already auto-skinned to the body), this is NON-DESTRUCTIVE:
    it adds the face joints + reweights only the face region, keeping the body
    skin. A fresh mesh is smooth-bound outright. Returns the skinClusters."""
    if not meshes:
        cmds.warning(f"[advFace] Select your {what} mesh first, then bind.")
        return []
    if not jnts:
        cmds.warning("[advFace] No joints found — build the face first.")
        return []
    made = []
    for m in meshes:
        if not cmds.objExists(m):
            continue
        sc = _mesh_skincluster(m)
        if sc:
            n = _reweight_region(m, sc, jnts, m_inf=max_influences)
            print(f"[advFace] {what}: re-weighted {n} {what}-region verts of "
                  f"'{m}' to {len(jnts)} joints, KEPT the existing (body) skin "
                  f"— combined mesh safe.")
            made.append(sc)
            continue
        shp = (cmds.listRelatives(m, s=True, ni=True) or [m])[0]
        for old in cmds.ls(cmds.listHistory(shp) or [], type="skinCluster"):
            cmds.skinCluster(old, e=True, ub=True)
        sc = cmds.skinCluster(jnts, m, tsb=True, mi=max_influences,
                              dr=4.0, rui=False, n=f"{m}_skinCluster")[0]
        if sc:
            made.append(sc)
            print(f"[advFace] Bound '{m}' to {len(jnts)} {what} joints "
                  f"(fresh) — drive the controls to test, then weight-paint.")
    return made


def _resolve_meshes(mesh):
    return ([mesh] if isinstance(mesh, str)
            else list(mesh) if mesh else _selected_meshes())


def bind_face_mesh(mesh=None, max_influences=4):
    """Smooth-bind the WHOLE face mesh to every face joint in ONE step.

    Select your face geo and call with no args (or pass a mesh name/list).
    Any existing skin is replaced; closest-point starting weights mean the
    blink + mouth drive the mesh immediately — then refine with weight
    paint. Returns the skinClusters created.
    """
    return _bind_meshes(_resolve_meshes(mesh), face_bind_joints(),
                        max_influences, "face")


def bind_eyelids(mesh=None, max_influences=3):
    """Smooth-bind ONLY the eyelid mesh to ONLY the eyelid joints (+ eye and
    head anchors) — a clean isolated blink test, no lip/jaw influence to
    fight. Select the eyelid mesh and call with no args."""
    return _bind_meshes(_resolve_meshes(mesh), eyelid_bind_joints(),
                        max_influences, "eyelid")


def _fitted_eye(side):
    """(loop centre, canthus-to-canthus width) of the fitted eye loop, or
    (None, None) — the reliable 'where + how big is the eye' on the model."""
    try:
        f = AdvancedFace()
        f._load_fits()
        for up_r in (f"{side}_lidUpper", "L_lidUpper", "R_lidUpper"):
            up = f.fits.get(up_r)
            if up and len(up) >= 2:
                lo = f.fits.get(up_r.replace("Upper", "Lower")) or []
                allp = list(up) + list(lo)
                ctr = [sum(p[k] for p in allp) / len(allp) for k in range(3)]
                return ctr, _dist(up[0], up[-1])
    except Exception:
        pass
    return None, None


def snap_eyeball_to_mesh(mesh=None, side=None, mirror=False):
    """Drop / move the eyeball BALL onto a selected EYEBALL MESH (or its eye-
    area faces/verts). Centres + scales {side}_eyeball_LOC to the selection and
    un-hides it. Two safety nets so a wrong / oversized / mis-placed pick can't
    fling the ball off into space: the radius is CLAMPED to the fitted eye
    loop, and if the selection sits FAR from the fitted eye the ball drops at
    the eye CENTRE instead. Nudge if it's slightly off, then Build. side=None
    infers L/R from X; mirror forces that side's X (symmetric -> LEFT ball)."""
    # A COMPONENT selection (eye-area faces/verts/edges) wins — it lets you
    # isolate the eye on a one-piece head; else use the selected mesh object.
    comps = cmds.filterExpand(cmds.ls(sl=True, fl=True) or [],
                              sm=(31, 32, 34)) or []
    if mesh is None and comps:
        bb = cmds.exactWorldBoundingBox(comps)
        what = comps[0].split(".")[0]
    else:
        meshes = _resolve_meshes(mesh)
        what = meshes[0] if meshes else None
        if not what or not cmds.objExists(what):
            cmds.warning("[advFace] Select your eyeball MESH (or its eye-area "
                         "faces) first, then snap.")
            return None
        bb = cmds.exactWorldBoundingBox(what)
    C = [(bb[0] + bb[3]) * 0.5, (bb[1] + bb[4]) * 0.5, (bb[2] + bb[5]) * 0.5]
    r = max(bb[3] - bb[0], bb[4] - bb[1], bb[5] - bb[2]) * 0.5 or 1e-3
    if side is None:
        side = "L" if C[0] >= 0 else "R"

    # Where the eye actually IS + how big — fitted loop centre, then the eye
    # joint, then the eye-to-head distance.
    anchor, eye_w = _fitted_eye(side)
    eye_jnt = f"{side}_eye_BIND_JNT"
    if anchor is None and cmds.objExists(eye_jnt):
        anchor = _world_pos(eye_jnt)
    if eye_w is None and anchor is not None \
            and cmds.objExists("C_head_BIND_JNT"):
        eye_w = _dist(anchor, _world_pos("C_head_BIND_JNT"))

    # SANITY (fixes "ball up in the air"): if the pick sits way off the eye,
    # it was the wrong / a floating mesh — drop the ball at the eye centre (the
    # eye joint if we have one, else the fitted loop centre) instead of in
    # space. (Also handles picking the OTHER eye in symmetric mode.)
    if anchor is not None and eye_w and _dist(C, anchor) > 1.5 * eye_w:
        C = (_world_pos(eye_jnt) if cmds.objExists(eye_jnt) else list(anchor))
        cmds.warning("[advFace] selection was far from the fitted eye — placed "
                     "the ball at the eye centre. Nudge it if needed, then "
                     "Build.")

    # SIZE clamp: an eyeball is ~0.4x the eye width, so cap at 0.6x the loop.
    if eye_w:
        r = min(r, 0.6 * eye_w)
    if mirror:
        C[0] = abs(C[0]) if side == "L" else -abs(C[0])
    eye = f"{side}_eyeball_LOC"
    if cmds.objExists(eye):
        try:
            cmds.delete(eye)
        except Exception:
            pass
    s = cmds.sphere(n=eye, r=r, ch=False)[0]
    cmds.xform(s, ws=True, t=C)
    shp = (cmds.listRelatives(s, s=True) or [s])[0]
    for at, v in (("overrideEnabled", 1), ("overrideColor", 17),
                  ("castsShadows", 0), ("receiveShadows", 0),
                  ("primaryVisibility", 0), ("visibleInReflections", 0),
                  ("visibleInRefractions", 0)):
        try:
            cmds.setAttr(f"{shp}.{at}", v)
        except Exception:
            pass
    cmds.select(cl=True)
    print(f"[advFace] {eye} snapped to '{what}' (centre "
          f"{[round(x, 1) for x in C]}, radius {r:.2f}). Nudge if needed, "
          f"then Build.")
    return eye
