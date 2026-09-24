"""
===============================================================================
 VEHICLE CRASH - walls stop the car, and the body dents where it hits
===============================================================================

 Tag any meshes as crash OBSTACLES (walls, poles, barriers, parked cars):

   * Drive Mode: the car hits them instead of driving through. The crumple
     zone soaks up the hit, so a fast car crushes deeper and stops over a
     longer distance; a glancing hit knocks the car sideways and spins it.
   * Simulate Physics: the same in 3D, and after a real hit the driver lets
     go of the path (crashStopsPath), so the wreck doesn't keep pushing.
   * Crash damage: the body, hood, doors, trunk (every mesh bound to the
     vehicle, wheels excluded) dent where they touched. Metal around the
     hit is dragged along (crashSpread) and buckles (crashCrumple).

 Drive Mode and Simulate Physics bake the damage for you when they finish.
 After hand-keying a crash, run bake_damage() (the "Bake Crash Damage"
 button). The dents are blendShape targets IN FRONT of the skin (a standard
 Maya node, no plugin: the file opens anywhere), keyed so each dent grows
 at the moment of impact. C_chassis_CTRL.crashDamage scales every dent:
 0 = showroom, 1 = as crashed.

     import vehicle_crash
     vehicle_crash.add_obstacles(["wall", "pole"])
     vehicle_crash.bake_damage()        # animated range -> keyed dents
     vehicle_crash.clear_damage()

 The ground counts too (crashGround): rollovers, hard landings and ramp
 scrapes dent the car in Simulate Physics and in the damage bake. Drive
 Mode keeps the car on the terrain, so there it only hits obstacles.

 Soft obstacles (add_obstacles(soft=True): another car, a fence) dent
 too, and the two crumple zones share the crush so they meet in the middle.

 Moving obstacles: Simulate Physics follows keyed (rigid) obstacles, so a
 wrecking ball or a keyed car shoves and dents even a parked vehicle. Drive
 Mode treats obstacles as standing still; the damage bake follows them
 all. Normals should face out of the obstacle; a single plane works from
 whichever side the car starts on.
 Settings: C_chassis_CTRL > CRASH.
===============================================================================
"""

import math
import re

import maya.cmds as cmds
import maya.api.OpenMaya as om2
import maya.api.OpenMayaAnim as oma2

import raycast_ground
import vehicle_sim

GLOBAL = "C_global_CTRL"
CHASSIS = "C_chassis_CTRL"
RIG_TOP = "VEHICLE_RIG_GRP"
OBSTACLE_SET = "VEHICLE_CRASH_OBSTACLES_SET"
TAG = "vehicleCrash"                  # bool attr on our blendShape nodes
SOFT = "crashSoft"                    # bool attr: this obstacle dents too
CHUNK = 4                             # frames of damage per blendShape target
FRICTION = 0.35                       # scraping along a wall
BEAM = 0.35                           # least share of the front a hit loads

# (attr, default, min, max, tooltip). Written onto C_chassis_CTRL.
SETTINGS = (
    ("crashDamage", 1.0, 0.0, 1.0,
     "KEYABLE. Scales every baked dent: 0 = showroom, 1 = as crashed."),
    ("crashStrength", 12.0, 1.0, 200.0,
     "How hard the crumple zone pushes back, as the g-force a full-width "
     "hit stops the car with. Lower = softer car: crushes deeper and stops "
     "over a longer distance."),
    ("crashMaxCrush", 0.2, 0.01, 0.6,
     "Deepest crush, as a fraction of the car's length. Past this the car "
     "is solid (engine block, chassis) and stops dead."),
    ("crashSpread", 0.12, 0.0, 0.5,
     "How far a dent drags the metal around it, as a fraction of the car's "
     "length. 0 = only what touched moves."),
    ("crashCrumple", 0.5, 0.0, 2.0,
     "Wrinkles and buckles in the dented metal. 0 = smooth dents."),
    ("crashBounce", 0.1, 0.0, 1.0,
     "How much the car bounces back off what it hit."),
    ("crashStopsPath", True, None, None,
     "Simulate Physics: after a hit the driver lets go of the path and "
     "brakes, instead of pushing on through the obstacle."),
    ("crashGround", True, None, None,
     "The ground dents the car too: a rollover onto the roof, a hard "
     "landing that bottoms out, a nose-first landing, scraping a ramp. "
     "Simulate Physics lands the body on the ground instead of letting it "
     "sink through. Metal that already touches the ground at rest (a low "
     "lip, mudflaps) is left alone."),
)
_KEYABLE = {"crashDamage"}

# Running gear, not body panels: wheels, tyres, springs, treads, and the
# leaf spring / shackle / shock / solid axle parts.
_WHEEL_JOINT = re.compile(
    r"_(hub|spoke|spring|tread|leaf|shackle|shockBody|shockRod|axle)_", re.I)


# =============================================================================
# Settings
# =============================================================================

def ensure_settings():
    """Add the CRASH settings to C_chassis_CTRL (idempotent)."""
    if not cmds.objExists(CHASSIS):
        return
    if not cmds.attributeQuery("crashHeader", node=CHASSIS, exists=True):
        cmds.addAttr(CHASSIS, ln="crashHeader", at="enum",
                     en="---CRASH---:", k=True)
        cmds.setAttr(CHASSIS + ".crashHeader", l=True, cb=True, k=False)
    for attr, dv, lo, hi, _tip in SETTINGS:
        if cmds.attributeQuery(attr, node=CHASSIS, exists=True):
            continue
        if isinstance(dv, bool):
            cmds.addAttr(CHASSIS, ln=attr, at="bool", dv=dv)
        else:
            cmds.addAttr(CHASSIS, ln=attr, at="double", dv=dv, min=lo, max=hi)
        plug = "%s.%s" % (CHASSIS, attr)
        if attr in _KEYABLE:
            cmds.setAttr(plug, k=True)
        else:
            cmds.setAttr(plug, cb=True)


def read_settings(overrides=None):
    ensure_settings()
    out = {}
    for attr, dv, _lo, _hi, _tip in SETTINGS:
        out[attr] = (cmds.getAttr("%s.%s" % (CHASSIS, attr))
                     if cmds.objExists(CHASSIS) else dv)
    out.update(overrides or {})
    return out


# =============================================================================
# Scene: obstacles and the meshes that dent
# =============================================================================

def _mesh_shape(node):
    if not node or not cmds.objExists(node):
        return None
    if cmds.nodeType(node) == "mesh":
        return node
    shapes = cmds.listRelatives(node, s=True, ni=True, type="mesh",
                                f=True) or []
    return shapes[0] if shapes else None


def _transform(node):
    if cmds.nodeType(node) == "mesh":
        return cmds.listRelatives(node, p=True, f=True)[0]
    return cmds.ls(node, long=True)[0]


def _skin_cluster(shape):
    for h in cmds.listHistory(shape, pdo=True) or []:
        if cmds.nodeType(h) == "skinCluster":
            return h
    return None


def _long(names):
    return set(cmds.ls(names, long=True) or []) if names else set()


def obstacles():
    """Mesh transforms tagged as crash obstacles (long names)."""
    if not cmds.objExists(OBSTACLE_SET):
        return []
    out = []
    for n in cmds.sets(OBSTACLE_SET, q=True) or []:
        if _mesh_shape(n):
            out.append(_transform(n))
    return sorted(set(out))


def _ground_mesh():
    try:
        import vehicle_rig_builder
        g = vehicle_rig_builder.assigned_ground_mesh()
    except Exception:
        g = None
    return _transform(g) if g and cmds.objExists(g) else None


def soft_obstacles():
    """Obstacles that dent themselves (another car, a fence): long names."""
    return [n for n in obstacles()
            if cmds.attributeQuery(SOFT, node=n, exists=True)
            and cmds.getAttr(n + "." + SOFT)]


def add_obstacles(nodes=None, soft=False):
    """Tag meshes (default: the selection) as crash obstacles. `soft` ones
    dent where the car hits them, and the two crumple zones share the
    crush. The ground mesh and the car's own meshes are refused. Returns
    what was added."""
    if nodes is None:
        nodes = cmds.ls(sl=True, type="transform") or []
    nodes = [_transform(n) for n in nodes if _mesh_shape(n)]
    ground = _ground_mesh()
    own = _long(panel_meshes(exclude_obstacles=False))
    added = []
    for n in nodes:
        short = n.split("|")[-1]
        if n == ground:
            cmds.warning("[crash] '%s' is the ground mesh: it already dents "
                         "the car (crashGround on C_chassis_CTRL)." % short)
            continue
        if n in own:
            cmds.warning("[crash] '%s' is part of the vehicle." % short)
            continue
        added.append(n)
    if not added:
        return []
    if not cmds.objExists(OBSTACLE_SET):
        cmds.sets(em=True, n=OBSTACLE_SET)
    cmds.sets(added, add=OBSTACLE_SET)
    for n in added:
        if not cmds.attributeQuery(SOFT, node=n, exists=True):
            cmds.addAttr(n, ln=SOFT, at="bool", dv=False)
        cmds.setAttr(n + "." + SOFT, bool(soft))
    return [n.split("|")[-1] for n in added]


def remove_obstacles(nodes=None):
    """Untag meshes (default: the selection). Returns what was removed."""
    if not cmds.objExists(OBSTACLE_SET):
        return []
    if nodes is None:
        nodes = cmds.ls(sl=True, type="transform") or []
    current = set(obstacles())
    gone = [_transform(n) for n in nodes if cmds.objExists(n)]
    gone = [n for n in gone if n in current]
    if gone:
        cmds.sets(gone, remove=OBSTACLE_SET)
    return [n.split("|")[-1] for n in gone]


def panel_meshes(exclude_obstacles=True):
    """Every mesh that rides on the vehicle's body and can dent: skinned to
    at least one vehicle joint that isn't a wheel (hub, spoke, spring,
    tread), or parented under the vehicle rig. Tyres, rims and tread links
    are left out. Long names."""
    skip = set(obstacles()) if exclude_obstacles else set()
    ground = _ground_mesh()
    out = []
    for shape in cmds.ls(type="mesh", ni=True, long=True) or []:
        tr = cmds.listRelatives(shape, p=True, f=True)[0]
        if tr in skip or tr == ground or "|C_treadLinks_GRP|" in tr + "|":
            continue
        sc = _skin_cluster(shape)
        if sc:
            infl = cmds.ls(cmds.skinCluster(sc, q=True, inf=True) or [],
                           long=True) or []
            body = [j for j in infl
                    if not _WHEEL_JOINT.search(j.split("|")[-1])
                    and ("|%s|" % RIG_TOP in j or "|C_trailers_GRP|" in j)]
            if body:
                out.append(tr)
        elif "|%s|" % RIG_TOP in tr:
            out.append(tr)
    return sorted(set(out))


def damage_meshes():
    """Everything that can dent: the car's panels plus the obstacles marked
    as denting too."""
    return panel_meshes() + soft_obstacles()


def ground_source():
    """(ground mesh or None, flat floor height) the car drives on."""
    flat = (cmds.xform("C_ground_LOC", q=True, ws=True, t=True)[1]
            if cmds.objExists("C_ground_LOC") else 0.0)
    return _ground_mesh(), flat


def ground_on(settings=None):
    return bool(read_settings(settings)["crashGround"])


def active(settings=None):
    """True when there's anything to crash into: obstacles, or the ground
    (crashGround) with car meshes to dent."""
    if obstacles():
        return True
    return (cmds.objExists(CHASSIS) and ground_on(settings)
            and bool(panel_meshes()))


# =============================================================================
# Small affine math: a matrix is a 12-tuple, 3 rows + translation, applied to
# row vectors like Maya (p' = p * M).
# =============================================================================

def _m12(m):
    """12-tuple from an MMatrix or a flat 16-list."""
    return (m[0], m[1], m[2], m[4], m[5], m[6], m[8], m[9], m[10],
            m[12], m[13], m[14])


def _xf(m, p):
    x, y, z = p
    return (x * m[0] + y * m[3] + z * m[6] + m[9],
            x * m[1] + y * m[4] + z * m[7] + m[10],
            x * m[2] + y * m[5] + z * m[8] + m[11])


def _xv(m, v):
    x, y, z = v
    return (x * m[0] + y * m[3] + z * m[6],
            x * m[1] + y * m[4] + z * m[7],
            x * m[2] + y * m[5] + z * m[8])


def _inv(m):
    a, b, c, d, e, f, g, h, i = m[:9]
    co = (e * i - f * h, c * h - b * i, b * f - c * e,
          f * g - d * i, a * i - c * g, c * d - a * f,
          d * h - e * g, b * g - a * h, a * e - b * d)
    det = a * co[0] + b * co[3] + c * co[6]
    if abs(det) < 1e-12:
        det = 1e-12
    r = tuple(x / det for x in co)
    t = _xv(r, (-m[9], -m[10], -m[11]))
    return r + t


def _blend(mats, weights):
    out = [0.0] * 12
    for m, w in zip(mats, weights):
        for k in range(12):
            out[k] += m[k] * w
    return tuple(out)


def _scale(m):
    return math.sqrt(m[0] * m[0] + m[1] * m[1] + m[2] * m[2]) or 1.0


def _box_corners(lo, hi):
    return [(x, y, z) for x in (lo[0], hi[0]) for y in (lo[1], hi[1])
            for z in (lo[2], hi[2])]


def _box_of(points):
    xs, ys, zs = zip(*points)
    return (min(xs), min(ys), min(zs)), (max(xs), max(ys), max(zs))


def _overlap(alo, ahi, blo, bhi, pad=0.0):
    return all(alo[k] - pad <= bhi[k] and blo[k] - pad <= ahi[k]
               for k in range(3))


def _inside(p, lo, hi, pad=0.0):
    return (lo[0] - pad <= p[0] <= hi[0] + pad
            and lo[1] - pad <= p[1] <= hi[1] + pad
            and lo[2] - pad <= p[2] <= hi[2] + pad)


def _len3(v):
    return math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])


# =============================================================================
# Crumple noise (smooth value noise, pure Python)
# =============================================================================

def _hash(i, j, k):
    h = ((i * 73856093) ^ (j * 19349663) ^ (k * 83492791)) & 0xffffffff
    h = ((h ^ (h >> 15)) * 2246822519) & 0xffffffff
    h ^= h >> 13
    return (h & 0xffff) / 32767.5 - 1.0


def _noise(x, y, z):
    ix, iy, iz = int(math.floor(x)), int(math.floor(y)), int(math.floor(z))
    fx, fy, fz = x - ix, y - iy, z - iz
    u = fx * fx * (3.0 - 2.0 * fx)
    v = fy * fy * (3.0 - 2.0 * fy)
    w = fz * fz * (3.0 - 2.0 * fz)

    def lerp(a, b, t):
        return a + (b - a) * t

    x00 = lerp(_hash(ix, iy, iz), _hash(ix + 1, iy, iz), u)
    x10 = lerp(_hash(ix, iy + 1, iz), _hash(ix + 1, iy + 1, iz), u)
    x01 = lerp(_hash(ix, iy, iz + 1), _hash(ix + 1, iy, iz + 1), u)
    x11 = lerp(_hash(ix, iy + 1, iz + 1), _hash(ix + 1, iy + 1, iz + 1), u)
    return lerp(lerp(x00, x10, v), lerp(x01, x11, v), w)


def crumple_noise(p, freq):
    """Two octaves of smooth noise in about -1..1."""
    x, y, z = p[0] * freq, p[1] * freq, p[2] * freq
    n = _noise(x, y, z) + 0.5 * _noise(2.17 * x + 17.3, 2.17 * y - 4.1,
                                       2.17 * z + 9.7)
    return max(-1.0, min(1.0, n / 1.1))


# =============================================================================
# Obstacles: closest-point queries in world space
# =============================================================================

def _dag(name):
    sel = om2.MSelectionList()
    sel.add(name)
    return sel.getDagPath(0)


def _is_deforming(shape):
    for h in cmds.listHistory(shape, pdo=True) or []:
        if cmds.nodeType(h) in ("skinCluster", "blendShape", "cluster",
                                "ffd", "wire", "deltaMush", "nonLinear",
                                "softMod", "wrap", "polySmoothFace"):
            return True
    return False


class Obstacles(object):
    """The crash obstacles, ready for 'is this point inside, how deep, which
    way out' queries. refresh() follows obstacles that move or deform.
    add_ground() adds the ground as one more (self.ground is its index)."""

    def __init__(self, names=None):
        self.items = []
        self.ground = None
        for n in (obstacles() if names is None else names):
            shape = _mesh_shape(n)
            if not shape:
                continue
            self.items.append({"name": n.split("|")[-1], "path": _dag(shape),
                               "sign": 1.0, "m": None, "group": n,
                               "deforming": _is_deforming(shape)})
        self.refresh(force=True)

    def __len__(self):
        return len(self.items)

    def add_mesh(self, mesh, group, panel=None, fixed=True):
        """One more obstacle: a mesh that is itself a dent panel (the car,
        or another car). `group` keeps a panel from denting on its own
        body; `panel` is its index in the bake's panel list, which shares
        the crush between the two crumple zones."""
        shape = _mesh_shape(mesh)
        if not shape:
            return None
        it = {"name": mesh.split("|")[-1], "path": _dag(shape), "sign": 1.0,
              "m": None, "deforming": True, "group": group, "panel": panel,
              "fixed": fixed}
        self.items.append(it)
        self._update(it)
        return len(self.items) - 1

    def add_ground(self, mesh=None, flat_y=0.0):
        """The ground: the assigned ground mesh, or a flat floor at flat_y.
        Returns its index."""
        shape = _mesh_shape(mesh) if mesh else None
        if shape:
            it = {"name": mesh.split("|")[-1], "path": _dag(shape),
                  "sign": 1.0, "m": None, "deforming": False, "ground": True}
            self.items.append(it)
            self._update(it)
            self._height_grid(it)
        else:
            big = 1e7
            self.items.append({
                "name": "ground", "kind": "plane", "ground": True,
                "y": flat_y, "sign": 1.0, "deforming": False, "m": None,
                "lo": (-big, flat_y, -big), "hi": (big, flat_y, big),
                "flat": True, "thickness": 0.0, "feature": 0.0})
        self.ground = len(self.items) - 1
        return self.ground

    def refresh(self, force=False):
        for it in self.items:
            if it.get("kind") == "plane":
                continue
            if it["deforming"] and it["m"] is not None and not force:
                # Deforming (a skinned car): re-measure cheaply now and
                # rebuild the closest-point tree only if it gets queried.
                it["stale"] = True
                self._box(it, it["path"].inclusiveMatrix())
                continue
            if (not force and it["m"] is not None
                    and it["path"].inclusiveMatrix().isEquivalent(it["m"],
                                                                  1e-9)):
                it["m_prev"] = it["m"]            # stood still this frame
                continue
            self._update(it)

    def _ensure(self, it):
        if it.get("stale"):
            self._update(it)

    def _box(self, it, m):
        obb = om2.MFnDagNode(it["path"]).boundingBox
        bb = om2.MBoundingBox(obb)
        bb.transformUsing(m)
        axes = [_len3(_m12(m)[r:r + 3]) for r in (0, 3, 6)]
        ext = sorted((obb.max[k] - obb.min[k]) * axes[k] for k in range(3))
        it.update(olo=(obb.min.x, obb.min.y, obb.min.z),
                  ohi=(obb.max.x, obb.max.y, obb.max.z),
                  lo=(bb.min.x, bb.min.y, bb.min.z),
                  hi=(bb.max.x, bb.max.y, bb.max.z),
                  thickness=ext[0], feature=ext[1],
                  flat=ext[0] <= 1e-4 * max(ext[2], 1e-9))

    def _update(self, it):
        m = it["path"].inclusiveMatrix()
        it["m_prev"] = it["m"] if it.get("m") is not None else m
        it["accel"] = None
        mi = om2.MMeshIntersector()
        mi.create(it["path"].node(), m)
        fn = om2.MFnMesh(it["path"])
        self._box(it, m)
        it.update(m=m, nt=m.inverse().transpose(), mi=mi, fn=fn,
                  vn=fn.getVertexNormals(True, om2.MSpace.kObject),
                  inv=_m12(m.inverse()), stale=False)

    def _height_grid(self, it, cells=200):
        """Highest ground in each cell of an XZ grid, so points clearly above
        the ground skip the closest-point query."""
        pts = it["fn"].getPoints(om2.MSpace.kWorld)
        _counts, idx = it["fn"].getTriangles()
        lo, hi = it["lo"], it["hi"]
        size = max(hi[0] - lo[0], hi[2] - lo[2], 1e-6) / float(cells)
        grid = {}
        for k in range(0, len(idx), 3):
            a, b, c = pts[idx[k]], pts[idx[k + 1]], pts[idx[k + 2]]
            top = max(a.y, b.y, c.y)
            i0 = int(math.floor((min(a.x, b.x, c.x) - lo[0]) / size))
            i1 = int(math.floor((max(a.x, b.x, c.x) - lo[0]) / size))
            j0 = int(math.floor((min(a.z, b.z, c.z) - lo[2]) / size))
            j1 = int(math.floor((max(a.z, b.z, c.z) - lo[2]) / size))
            for i in range(i0, i1 + 1):
                for j in range(j0, j1 + 1):
                    if grid.get((i, j), -1e30) < top:
                        grid[(i, j)] = top
        it["hgrid"] = (grid, size)

    def exit_along(self, i, p, d, max_dist):
        """Where a ray from p (inside obstacle i) along unit d comes out:
        (distance, outward normal there, how square the ray meets it:
        1 = straight through the face, 0 = along it), or None."""
        it = self.items[i]
        if it.get("kind") == "plane":
            if d[1] <= 1e-6:
                return None
            t = (it["y"] - p[1]) / d[1]
            return (t, (0.0, 1.0, 0.0), d[1]) if 0.0 <= t <= max_dist \
                else None
        self._ensure(it)
        if it.get("accel") is None:
            it["accel"] = it["fn"].autoUniformGridParams()
        hit = raycast_ground.first_hit(it["fn"], p, d, max_dist, it["accel"])
        if hit is None:
            return None
        dist, _pt, face = hit
        nn = it["fn"].getPolygonNormal(face, om2.MSpace.kWorld)
        ln = nn.length() or 1.0
        nn = (nn.x / ln * it["sign"], nn.y / ln * it["sign"],
              nn.z / ln * it["sign"])
        return dist, nn, d[0] * nn[0] + d[1] * nn[1] + d[2] * nn[2]

    def motion(self, i, p, panels=None):
        """How far the obstacle's surface at world point p moved since the
        last frame (0 for the ground and anything standing still)."""
        it = self.items[i]
        if it.get("ground") or it.get("m") is None:
            return (0.0, 0.0, 0.0)
        pi = it.get("panel")
        if pi is not None and panels is not None:
            return panels[pi].motion_at(p)
        prev = it.get("m_prev")
        if prev is None or prev.isEquivalent(it["m"], 1e-9):
            return (0.0, 0.0, 0.0)
        was = om2.MPoint(p[0], p[1], p[2]) * it["m"].inverse() * prev
        return (p[0] - was.x, p[1] - was.y, p[2] - was.z)

    def ground_top(self, i, lo, hi):
        """Highest ground under the XZ footprint [lo, hi] (-inf off it)."""
        it = self.items[i]
        if it.get("kind") == "plane":
            return it["y"]
        if "hgrid" not in it:
            return it["hi"][1]
        grid, size = it["hgrid"]
        glo = it["lo"]
        i0 = int(math.floor((lo[0] - glo[0]) / size))
        i1 = int(math.floor((hi[0] - glo[0]) / size))
        j0 = int(math.floor((lo[2] - glo[2]) / size))
        j1 = int(math.floor((hi[2] - glo[2]) / size))
        if (i1 - i0 + 1) * (j1 - j0 + 1) > 4 * len(grid):
            tops = [v for (i, j), v in grid.items()
                    if i0 <= i <= i1 and j0 <= j <= j1]
        else:
            tops = [grid[(i, j)] for i in range(i0, i1 + 1)
                    for j in range(j0, j1 + 1) if (i, j) in grid]
        return max(tops) if tops else -1e30

    def near(self, lo, hi, pad=0.0):
        """Indices of the obstacles whose world box overlaps [lo, hi]. The
        ground only counts when the box dips below the highest ground
        under it."""
        out = []
        for i, it in enumerate(self.items):
            if it.get("ground"):
                if lo[1] - pad <= self.ground_top(i, lo, hi):
                    out.append(i)
            elif _overlap(lo, hi, it["lo"], it["hi"], pad):
                out.append(i)
        return out

    def box(self, i, depth, clip=None):
        """Obstacle i's world box, grown by `depth` for a plane (whose
        inside is the space behind it) and down for the ground, cut to
        `clip` (lo, hi) when given."""
        it = self.items[i]
        lo, hi = it["lo"], it["hi"]
        if it.get("ground"):
            lo = (lo[0], lo[1] - depth, lo[2])
        elif it["flat"]:
            lo = tuple(x - depth for x in lo)
            hi = tuple(x + depth for x in hi)
        if clip:
            lo = tuple(max(a, b) for a, b in zip(lo, clip[0]))
            hi = tuple(min(a, b) for a, b in zip(hi, clip[1]))
        return lo, hi

    def smallest_feature(self):
        """The thinnest obstacle's width (its middle bounding-box side): the
        crash hull samples the car finer than this so nothing slips
        between its points. The ground doesn't count."""
        sizes = [it["feature"] for it in self.items
                 if it["feature"] > 0 and not it.get("ground")]
        return min(sizes) if sizes else 0.0

    def thinnest(self):
        """The thinnest wall (smallest bounding-box side, planes and the
        ground ignored): a point must not jump through one in one step."""
        sizes = [it["thickness"] for it in self.items
                 if it["thickness"] > 1e-6 and not it.get("ground")]
        return min(sizes) if sizes else 0.0

    def _closest(self, it, p):
        """(closest point, face normal, smooth normal) in world space. The
        face normal says which way is out; the smooth normal (the vertex
        normals blended across the triangle) decides inside or outside, as
        the face normal of the wrong face comes back at edges and
        corners."""
        q = om2.MPoint(p[0], p[1], p[2])
        moved = it.get("delta")               # placed by move() (the sim)
        if moved is not None:
            q = q * moved[0]
        pom = it["mi"].getClosestPoint(q)
        c = om2.MPoint(pom.point) * it["m"]
        n = om2.MVector(pom.normal) * it["nt"]
        tri = it["fn"].getPolygonTriangleVertices(pom.face, pom.triangle)
        u, v = pom.barycentricCoords
        vn = it["vn"]
        s = (om2.MVector(vn[tri[0]]) * u + om2.MVector(vn[tri[1]]) * v
             + om2.MVector(vn[tri[2]]) * (1.0 - u - v)) * it["nt"]
        if moved is not None:
            c, n, s = c * moved[1], n * moved[1], s * moved[1]
        ln = n.length()
        if ln < 1e-12:
            return None
        n = n * (it["sign"] / ln)
        s = s * it["sign"]
        return (c.x, c.y, c.z), (n.x, n.y, n.z), (s.x, s.y, s.z)

    def move(self, i, m, m_next=None, dt=1.0):
        """Place rigid obstacle i at world matrix m without rebuilding its
        closest-point tree (queries are carried through the move), and
        give it a velocity from where it will be `dt` seconds later."""
        it = self.items[i]
        it["delta"] = (m.inverse() * it["m"], it["m"].inverse() * m)
        it["mt"] = m
        it["inv"] = _m12(m.inverse())
        self._box(it, m)
        it["m_next"], it["dt_next"] = m_next, dt

    def velocity(self, i, p):
        """World velocity of obstacle i's surface at p (units per second),
        for an obstacle move()d along its animation; 0 otherwise."""
        it = self.items[i]
        if it.get("m_next") is None:
            return (0.0, 0.0, 0.0)
        q = om2.MPoint(p[0], p[1], p[2]) * it["mt"].inverse() * it["m_next"]
        k = 1.0 / it["dt_next"]
        return ((q.x - p[0]) * k, (q.y - p[1]) * k, (q.z - p[2]) * k)

    def orient(self, outside_point):
        """Make every obstacle's 'outside' the side `outside_point` (the
        car) is on: fixes planes and flipped normals."""
        for it in self.items:
            if it.get("kind") == "plane" or it.get("fixed"):
                continue                # modelled normals: leave them
            it["sign"] = 1.0
            hit = self._closest(it, outside_point)
            if hit is None:
                continue
            c, _n, s = hit
            d = sum((outside_point[k] - c[k]) * s[k] for k in range(3))
            if d < 0:
                it["sign"] = -1.0

    def query(self, p, ids, max_depth):
        """(depth, unit normal out of the obstacle, index) for the shallowest
        obstacle point p is inside of, or None. Deeper than max_depth
        counts as having gone through."""
        best = None
        for i in ids:
            it = self.items[i]
            if it.get("kind") == "plane":
                depth = it["y"] - p[1]
                if 0.0 < depth <= max_depth and (best is None
                                                 or depth < best[0]):
                    best = (depth, (0.0, 1.0, 0.0), i)
                continue
            if "hgrid" in it:
                grid, size = it["hgrid"]
                key = (int(math.floor((p[0] - it["lo"][0]) / size)),
                       int(math.floor((p[2] - it["lo"][2]) / size)))
                if p[1] > grid.get(key, -1e30):
                    continue                  # above the ground here
                pad = max_depth
            else:
                # A plane has no thickness: behind it counts, to max_depth.
                pad = max_depth if it["flat"] else 1e-6
            if not _inside(p, it["lo"], it["hi"], pad):
                continue
            self._ensure(it)
            if not _inside(_xf(it["inv"], p), it["olo"], it["ohi"], pad):
                continue                      # outside its own (tight) box
            hit = self._closest(it, p)
            if hit is None:
                continue
            c, n, s = hit
            d = (p[0] - c[0], p[1] - c[1], p[2] - c[2])
            if d[0] * s[0] + d[1] * s[1] + d[2] * s[2] >= 0.0:
                continue                              # outside
            depth = _len3(d)
            if depth > max_depth:
                continue
            if best is None or depth < best[0]:
                best = (depth, n, i)
        return best


# =============================================================================
# The car's collision hull (points spread evenly over its skin)
# =============================================================================

def car_frame():
    """(origin, x axis, y axis, z axis) of C_global_CTRL in world space,
    axes normalised: the frame hull points are stored in."""
    m = cmds.xform(GLOBAL, q=True, ws=True, m=True)
    axes = []
    for r in (0, 4, 8):
        v = (m[r], m[r + 1], m[r + 2])
        ln = _len3(v) or 1.0
        axes.append((v[0] / ln, v[1] / ln, v[2] / ln))
    return (m[12], m[13], m[14]), axes[0], axes[1], axes[2]


def _dir_to_frame(frame, d):
    _o, ax, ay, az = frame
    return (d[0] * ax[0] + d[1] * ax[1] + d[2] * ax[2],
            d[0] * ay[0] + d[1] * ay[1] + d[2] * ay[2],
            d[0] * az[0] + d[1] * az[1] + d[2] * az[2])


def _to_frame(frame, p):
    o = frame[0]
    return _dir_to_frame(frame, (p[0] - o[0], p[1] - o[1], p[2] - o[2]))


def _from_frame(frame, p):
    o, ax, ay, az = frame
    return (o[0] + p[0] * ax[0] + p[1] * ay[0] + p[2] * az[0],
            o[1] + p[0] * ax[1] + p[1] * ay[1] + p[2] * az[1],
            o[2] + p[0] * ax[2] + p[1] * ay[2] + p[2] * az[2])


def _world_points(mesh):
    shape = _mesh_shape(mesh)
    if not shape:
        return []
    pts = om2.MFnMesh(_dag(shape)).getPoints(om2.MSpace.kWorld)
    return [(p.x, p.y, p.z) for p in pts]


def _world_triangles(mesh):
    shape = _mesh_shape(mesh)
    if not shape:
        return []
    _counts, idx = om2.MFnMesh(_dag(shape)).getTriangles()
    return list(idx)


def _wheel_box(frame):
    """A box around the wheels (car-frame corners), for a car with no
    meshes bound yet."""
    import vehicle_rig_builder
    hubs, radius = [], 1.0
    scale = (cmds.getAttr(GLOBAL + ".globalScale")
             if cmds.attributeQuery("globalScale", node=GLOBAL, exists=True)
             else 1.0)
    for p in vehicle_rig_builder.scene_wheel_prefixes():
        j = "%s_hub_BIND_JNT" % p
        if cmds.objExists(j):
            hubs.append(_to_frame(frame, cmds.xform(j, q=True, ws=True,
                                                    t=True)))
            if cmds.objExists("%s_wheel_CTRL" % p):
                radius = vehicle_rig_builder.contact_radius(p) * scale
    if not hubs:
        return None
    lo, hi = _box_of(hubs)
    return ((lo[0] - 0.35 * radius, lo[1] - 0.2 * radius, lo[2] - 1.4 * radius),
            (hi[0] + 0.35 * radius, hi[1] + 2.0 * radius, hi[2] + 1.4 * radius))


def _box_triangles(lo, hi):
    c = _box_corners(lo, hi)           # index = 4 x + 2 y + z
    quads = ((0, 1, 3, 2), (4, 6, 7, 5), (0, 4, 5, 1), (2, 3, 7, 6),
             (0, 2, 6, 4), (1, 5, 7, 3))
    tris = []
    for a, b, cc, d in quads:
        tris += [(c[a], c[b], c[cc]), (c[a], c[cc], c[d])]
    return tris


class Hull(object):
    """Collision points spread evenly over the car's skin, `spacing` apart,
    in the car frame (car_frame(): C_global's position and axes, world
    units)."""

    def __init__(self, points, spacing):
        self.points = points
        self.spacing = spacing
        self.area = spacing * spacing
        self.lo, self.hi = _box_of(points)
        self.centre = tuple(0.5 * (self.lo[k] + self.hi[k]) for k in range(3))
        self.width = self.hi[0] - self.lo[0]
        self.height = self.hi[1] - self.lo[1]
        self.length = self.hi[2] - self.lo[2]
        self.gcell = 2.0 * spacing
        self.grid = {}
        for i, p in enumerate(points):
            self.grid.setdefault(self._key(p), []).append(i)

    def _key(self, p):
        g = self.gcell
        return (int(math.floor(p[0] / g)), int(math.floor(p[1] / g)),
                int(math.floor(p[2] / g)))

    @staticmethod
    def extent(meshes=None):
        """(lo, hi) of the car's meshes in the car frame."""
        frame = car_frame()
        pts = []
        for m in (panel_meshes() if meshes is None else meshes):
            pts.extend(_to_frame(frame, p) for p in _world_points(m))
        if pts:
            return _box_of(pts)
        return _wheel_box(frame)

    @classmethod
    def from_scene(cls, meshes=None, spacing=None):
        """Sample the car's meshes (or a box round the wheels) every
        `spacing` (default: a 14th of the car's length)."""
        frame = car_frame()
        tris = []
        for m in (panel_meshes() if meshes is None else meshes):
            pts = [_to_frame(frame, p) for p in _world_points(m)]
            idx = _world_triangles(m)
            tris += [(pts[idx[k]], pts[idx[k + 1]], pts[idx[k + 2]])
                     for k in range(0, len(idx), 3)]
        if not tris:
            box = _wheel_box(frame)
            if box is None:
                raise RuntimeError("No vehicle to build a crash hull from.")
            tris = _box_triangles(*box)
        lo, hi = _box_of([p for t in tris for p in t])
        if not spacing:
            spacing = max(hi[k] - lo[k] for k in range(3)) / 14.0
        spacing = max(spacing, 1e-4)
        step = 0.5 * spacing
        best = {}

        def keep(p):
            key = (int(math.floor(p[0] / spacing)),
                   int(math.floor(p[1] / spacing)),
                   int(math.floor(p[2] / spacing)))
            d2 = sum(((key[k] + 0.5) * spacing - p[k]) ** 2 for k in range(3))
            if key not in best or d2 < best[key][0]:
                best[key] = (d2, p)

        for a, b, c in tris:
            keep(a)
            edge = max(_len3((b[0] - a[0], b[1] - a[1], b[2] - a[2])),
                       _len3((c[0] - a[0], c[1] - a[1], c[2] - a[2])),
                       _len3((c[0] - b[0], c[1] - b[1], c[2] - b[2])))
            n = int(math.ceil(edge / step))
            if n < 2:
                continue
            for i in range(n + 1):
                for j in range(n + 1 - i):
                    u, w = i / float(n), j / float(n)
                    keep(tuple(a[k] + (b[k] - a[k]) * u + (c[k] - a[k]) * w
                               for k in range(3)))
        return cls([v[1] for v in best.values()], spacing)

    def near(self, lo, hi, pad):
        """Indices of the points inside the car-frame box [lo, hi] + pad."""
        k0 = self._key(tuple(x - pad for x in lo))
        k1 = self._key(tuple(x + pad for x in hi))
        out = []
        for x in range(k0[0], k1[0] + 1):
            for y in range(k0[1], k1[1] + 1):
                for z in range(k0[2], k1[2] + 1):
                    out.extend(self.grid.get((x, y, z), ()))
        return out

    def world_box(self, frame):
        return _box_of([_from_frame(frame, c)
                        for c in _box_corners(self.lo, self.hi)])


class _Collider(object):
    """What the 2D drive and the 3D sim share: the obstacles, the hull and
    each hull point's dent (car frame). A point that crushed stays where
    the metal went, so it keeps pressing on what it hit (a pole can't slip
    through the car once it has touched it)."""

    def __init__(self, obstacle_names=None, settings=None, hull=None,
                 ground=None):
        """ground: (mesh or None, flat height) to hit the ground too."""
        names = obstacles() if obstacle_names is None else obstacle_names
        self.obs = Obstacles(names)
        if ground is not None:
            self.obs.add_ground(*ground)
        self.active = bool(len(self.obs))
        self.impacts = []            # (tick, approach speed)
        self.touching = False
        if not self.active:
            return
        s = read_settings(settings)
        self.settings = s
        if hull is None:
            lo, hi = Hull.extent() or ((0, 0, 0), (1, 1, 1))
            size = max(hi[k] - lo[k] for k in range(3))
            spacing = size / 14.0
            thin = self.obs.smallest_feature()
            if thin > 0:
                spacing = min(spacing, 0.6 * thin)
            hull = Hull.from_scene(spacing=max(spacing, size / 60.0))
        self.hull = h = hull
        g = vehicle_sim._gravity()
        # Plastic crush: each hull point yields at the same pressure, so a
        # full-width hit (width x height of contact) stops the car at
        # crashStrength g and a pole (a few points) crushes much deeper.
        self.yield_acc = (s["crashStrength"] * g * h.area
                          / max(h.width * h.height, 1e-6))
        self.max_crush = s["crashMaxCrush"] * h.length
        self.bounce = s["crashBounce"]
        self.tol = 1e-3 * h.length
        self.tol_ground = 5e-3 * h.length   # a light scrape isn't a dent
        self.offset = [(0.0, 0.0, 0.0)] * len(h.points)
        self.crush = [0.0] * len(h.points)
        frame = car_frame()
        self.obs.orient(_from_frame(frame, h.centre))
        # Skin already in the ground at rest (a low lip, mudflaps) never
        # dents on it.
        self.immune = set()
        g = self.obs.ground
        if g is not None:
            self.immune = {i for i, pl in enumerate(h.points)
                           if self.obs.query(_from_frame(frame, pl), [g],
                                             1e9)}

    def _contacts(self, frame, depth_limit, planar=False):
        """[(point index, world point, normal)] + depths for the dented
        hull points that are inside an obstacle."""
        lo, hi = self.hull.world_box(frame)
        ids = self.obs.near(lo, hi, depth_limit)
        if not ids:
            return []
        g = self.obs.ground
        solid = [o for o in ids if o != g]
        pad = depth_limit + self.max_crush
        clip = (tuple(x - pad for x in lo), tuple(x + pad for x in hi))
        out = []
        seen = set()
        for oi in ids:
            local = [_to_frame(frame, c) for c in _box_corners(
                *self.obs.box(oi, depth_limit, clip))]
            olo, ohi = _box_of(local)
            for i in self.hull.near(olo, ohi, self.max_crush
                                    + self.hull.spacing):
                if i in seen:
                    continue
                seen.add(i)
                pl, off = self.hull.points[i], self.offset[i]
                p = _from_frame(frame, (pl[0] + off[0], pl[1] + off[1],
                                        pl[2] + off[2]))
                hit = self.obs.query(p, solid if i in self.immune else ids,
                                     depth_limit)
                if hit is None:
                    continue
                depth, n, o = hit
                if depth <= (self.tol_ground if o == g else self.tol):
                    continue
                if planar:
                    if abs(n[1]) > 0.7:           # floors and ceilings
                        continue
                    ln = math.hypot(n[0], n[2])
                    n = (n[0] / ln, 0.0, n[2] / ln)
                out.append((i, p, n, depth, o))
        return out

    def _cap(self, i, dt):
        """Most impulse point i takes this tick before the metal gives."""
        if self.crush[i] >= self.max_crush - 1e-6:
            return float("inf")                    # solid: engine, chassis
        return self.yield_acc * dt

    def _beam(self, caps):
        """The bumper beam and frame rails spread a narrow hit (a pole, a
        corner) over at least BEAM of the car's front, so a small contact
        still pushes back with that share of the full-width force."""
        soft = [c for c in caps if c != float("inf")]
        if not soft:
            return caps
        h = self.hull
        full = max(h.width * h.height / h.area, 1.0)
        scale = BEAM * full / len(soft)
        if scale <= 1.0:
            return caps
        return [c * scale if c != float("inf") else c for c in caps]

    def _settle(self, frame, contacts, impulses, caps, approach, dt,
                velocities):
        """Dent the hull and return how far each contact is still inside (to
        be pushed out). Where the car kept going (the impulse hit the yield
        cap) the metal follows it in. Where it stopped within this tick it
        crushed as far as it takes to stop from the approach speed at the
        crumple zone's deceleration (v^2 / 2a), not the whole distance the
        tick happened to overshoot.
        The metal is pushed back against the way that bit of the car was
        travelling (so it stays in front of a pole and wraps round it),
        unless it only grazed the obstacle (then out along the normal)."""
        a_total = sum(c / dt for c in caps if c != float("inf"))
        stop = (approach * approach / (2.0 * a_total)) if a_total > 0 else 0.0
        residual = []
        for (i, _p, n, depth, _o), j, cap, vel in zip(contacts, impulses,
                                                      caps, velocities):
            if cap == float("inf"):
                residual.append(depth)
                continue
            want = depth if j >= cap * 0.999 else min(depth, stop)
            d, dist = n, want
            speed = _len3(vel)
            if speed > 1e-6:
                back = (-vel[0] / speed, -vel[1] / speed, -vel[2] / speed)
                facing = back[0] * n[0] + back[1] * n[1] + back[2] * n[2]
                if facing > 0.35:
                    d, dist = back, want / facing
            dl = _dir_to_frame(frame, d)
            nl = _dir_to_frame(frame, n)
            old = self.offset[i]
            new = [old[k] + dl[k] * dist for k in range(3)]
            ln = _len3(new)
            if ln > self.max_crush:
                new = [c * self.max_crush / ln for c in new]
            moved = sum((new[k] - old[k]) * nl[k] for k in range(3))
            self.offset[i] = tuple(new)
            self.crush[i] = min(ln, self.max_crush)
            residual.append(max(0.0, depth - moved))
        return residual

    @staticmethod
    def _push_out(contacts, residual):
        d = [0.0, 0.0, 0.0]
        for _ in range(4):
            for c, r in zip(contacts, residual):
                if r <= 0:
                    continue
                n = c[2]
                pen = r - (d[0] * n[0] + d[1] * n[1] + d[2] * n[2])
                if pen > 0:
                    d = [d[k] + n[k] * pen for k in range(3)]
        return tuple(d)

    def _note_impact(self, tick, speed):
        if speed > 0.05 * self.hull.length and not self.touching:
            self.impacts.append((tick, speed))


class DriveCrash(_Collider):
    """Crash response for Drive Mode (the flat, keyboard-driven car)."""

    def __init__(self, obstacle_names=None, settings=None, max_speed=900.0):
        super(DriveCrash, self).__init__(obstacle_names, settings)
        self.max_speed = max_speed
        if self.active:
            h = self.hull
            self.inertia = (h.length ** 2 + h.width ** 2) / 12.0

    def substeps(self, state, dt):
        """How many pieces to cut a tick into near an obstacle, so no part
        of the car jumps through a thin wall or pole in one step."""
        if not self.active:
            return 1
        h = self.hull
        v = (math.hypot(state["speed"], state.get("v_lat", 0.0))
             + abs(state.get("yaw_rate", 0.0)) * 0.5 * h.length)
        travel = v * dt
        lo, hi = h.world_box(car_frame())
        if not self.obs.near(lo, hi, travel):
            return 1
        limit = min(x for x in (h.spacing, self.obs.thinnest() or h.spacing)
                    if x > 0) * 0.4
        return max(1, min(8, int(math.ceil(travel / limit))))

    def resolve(self, state, dt, tick=0):
        """Stop the car at the obstacles, after vehicle_drive.apply_state()
        posed it for this tick. Edits `state` (x, z, speed, v_lat, yaw_rate,
        grip) and returns True if it changed."""
        if not self.active:
            return False
        frame = car_frame()
        v_abs = math.hypot(state["speed"], state.get("v_lat", 0.0))
        contacts = self._contacts(frame, 2.0 * v_abs * dt
                                  + 2.0 * self.hull.spacing, planar=True)
        if not contacts:
            self.touching = False
            return False

        heading = state["heading_rad"]
        fwd = (math.sin(heading), math.cos(heading))
        side = (math.cos(heading), -math.sin(heading))
        speed, v_lat = state["speed"], state.get("v_lat", 0.0)
        va = (fwd[0] * speed + side[0] * v_lat, fwd[1] * speed + side[1] * v_lat)
        wy = -state.get("yaw_rate", 0.0)        # heading -= yaw_rate * dt
        com3 = _from_frame(frame, self.hull.centre)
        com = (com3[0], com3[2])
        ra = (state["x"] - com[0], state["z"] - com[1])
        # velocity of the centre: v_a = v_c + w x r_a, w x r = (w rz, -w rx)
        vx, vz = va[0] - wy * ra[1], va[1] + wy * ra[0]
        v0 = (vx, vz)
        inertia = self.inertia

        rows, caps, jn, jt, vels = [], [], [], [], []
        approach = 0.0
        for i, p, n, _d, _o in contacts:
            r = (p[0] - com[0], p[2] - com[1])
            vels.append((vx + wy * r[1], 0.0, vz - wy * r[0]))
            nx, nz = n[0], n[2]
            tx, tz = -nz, nx
            rn = r[1] * nx - r[0] * nz
            rt = r[1] * tx - r[0] * tz
            vn0 = (vx + wy * r[1]) * nx + (vz - wy * r[0]) * nz
            approach = max(approach, -vn0)
            rows.append((r, nx, nz, tx, tz, rn, rt,
                         1.0 + rn * rn / inertia, 1.0 + rt * rt / inertia,
                         -self.bounce * vn0 if vn0 < -1e-3 else 0.0))
            caps.append(self._cap(i, dt))
            jn.append(0.0)
            jt.append(0.0)
        caps = self._beam(caps)
        for _ in range(10):
            for k, (r, nx, nz, tx, tz, rn, rt, kn, kt, target) in \
                    enumerate(rows):
                vn = (vx + wy * r[1]) * nx + (vz - wy * r[0]) * nz
                new = min(max(jn[k] + (target - vn) / kn, 0.0), caps[k])
                d = new - jn[k]
                jn[k] = new
                vx += nx * d
                vz += nz * d
                wy += rn * d / inertia
                vt = (vx + wy * r[1]) * tx + (vz - wy * r[0]) * tz
                lim = FRICTION * jn[k]
                new = min(max(jt[k] - vt / kt, -lim), lim)
                d = new - jt[k]
                jt[k] = new
                vx += tx * d
                vz += tz * d
                wy += rt * d / inertia

        residual = self._settle(frame, contacts, jn, caps, approach, dt,
                                vels)
        push = self._push_out(contacts, residual)
        self._note_impact(tick, approach)
        self.touching = True

        va = (vx + wy * ra[1], vz - wy * ra[0])
        state["x"] += push[0]
        state["z"] += push[2]
        state["speed"] = va[0] * fwd[0] + va[1] * fwd[1]
        state["v_lat"] = va[0] * side[0] + va[1] * side[1]
        state["yaw_rate"] = -wy
        # A hard knock breaks the tyres loose for a moment (so the car can
        # slide and spin off the hit) and lifts your foot off the pedal.
        dv = math.hypot(vx - v0[0], vz - v0[1])
        severity = min(1.0, dv / max(0.35 * self.max_speed, 1.0))
        if severity > 0.05:
            state["grip"] = min(state.get("grip", 1.0), 1.0 - 0.8 * severity)
            state["throttle"] = state.get("throttle", 0.0) * (1.0 - severity)
        return True


class SimCrash(_Collider):
    """Crash response inside vehicle_sim.run_simulation (the 3D rigid
    body). Hull points are in the car frame = the sim's body frame."""

    def __init__(self, obstacle_names=None, settings=None, hull=None,
                 ground=None, start=None, end=None, fps=24.0):
        super(SimCrash, self).__init__(obstacle_names, settings, hull,
                                       ground)
        self.stops_path = bool(self.active
                               and self.settings["crashStopsPath"])
        self.stop_speed = (0.15 * self.hull.length) if self.active else 0.0
        self.tick = 0
        self.fps = fps
        self.anim = {}
        if self.active and start is not None and end is not None:
            self._sample(int(start), int(end))

    def _sample(self, start, end):
        """Record where every animated obstacle is on each frame, so the
        simulation (which doesn't step Maya's timeline) can move them: a
        swinging wrecking ball or a keyed car hits the vehicle."""
        for i, it in enumerate(self.obs.items):
            if (it.get("kind") == "plane" or it.get("ground")
                    or it.get("deforming")):
                continue
            shape = it["path"].fullPathName()
            mats = [om2.MMatrix(cmds.getAttr(shape + ".worldMatrix[0]",
                                             time=f))
                    for f in range(start, end + 1)]
            if all(m.isEquivalent(mats[0], 1e-6) for m in mats):
                continue
            self.anim[i] = [om2.MTransformationMatrix(m) for m in mats]

    def _at(self, tms, t):
        """Obstacle matrix at fractional frame t (0 = first sampled)."""
        t = min(max(t, 0.0), len(tms) - 1.0)
        i = min(int(t), len(tms) - 2) if len(tms) > 1 else 0
        f = t - i
        a, b = tms[i], tms[min(i + 1, len(tms) - 1)]
        tr = a.translation(om2.MSpace.kWorld) * (1.0 - f) \
            + b.translation(om2.MSpace.kWorld) * f
        q = om2.MQuaternion.slerp(a.rotation(asQuaternion=True),
                                  b.rotation(asQuaternion=True), f)
        sa, sb = a.scale(om2.MSpace.kWorld), b.scale(om2.MSpace.kWorld)
        out = om2.MTransformationMatrix()
        out.setScale([sa[k] * (1.0 - f) + sb[k] * f for k in range(3)],
                     om2.MSpace.kWorld)
        out.setRotation(q)
        out.setTranslation(tr, om2.MSpace.kWorld)
        return out.asMatrix()

    def set_time(self, frame, frac=0.0):
        """Put the animated obstacles where they are at frame index
        `frame` + `frac` of the simulated range."""
        t = frame + frac
        step = 0.25
        for i, tms in self.anim.items():
            self.obs.move(i, self._at(tms, t), self._at(tms, t + step),
                          step / self.fps)

    def resolve(self, p, v, w, rot, com, inertia, dt):
        """Contact impulses for one sim substep. p = centre of mass, v / w =
        velocities, rot = the sim's 3x3 (column vectors), com = centre of
        mass in the body frame, inertia = body-frame diagonal.
        Returns (v, w, position correction, approach speed into an
        obstacle; the ground doesn't count, so a hard landing isn't a
        crash)."""
        self.tick += 1
        if not self.active:
            return v, w, (0.0, 0.0, 0.0), 0.0
        vs = vehicle_sim
        origin = vs._sub(p, vs._m_apply(rot, com))
        frame = (origin, (rot[0][0], rot[1][0], rot[2][0]),
                 (rot[0][1], rot[1][1], rot[2][1]),
                 (rot[0][2], rot[1][2], rot[2][2]))
        contacts = self._contacts(frame, 2.0 * vs._len(v) * dt
                                  + 2.0 * self.hull.spacing)
        if not contacts:
            self.touching = False
            return v, w, (0.0, 0.0, 0.0), 0.0

        def inv_i(x):
            loc = vs._m_apply_t(rot, x)
            return vs._m_apply(rot, (loc[0] / inertia[0], loc[1] / inertia[1],
                                     loc[2] / inertia[2]))

        rows, caps, jn, jt, vels = [], [], [], [], []
        approach = hit = 0.0
        for i, pt, n, _d, o in contacts:
            r = vs._sub(pt, p)
            vo = self.obs.velocity(o, pt)          # a moving obstacle
            vels.append(vs._sub(vs._add(v, vs._cross(w, r)), vo))
            rxn = vs._cross(r, n)
            kn = 1.0 + vs._dot(n, vs._cross(inv_i(rxn), r))
            vn0 = vs._dot(vels[-1], n)
            approach = max(approach, -vn0)
            if o != self.obs.ground:
                hit = max(hit, -vn0)
            rows.append((r, n, rxn, kn,
                         -self.bounce * vn0 if vn0 < -1e-3 else 0.0, vo))
            caps.append(self._cap(i, dt))
            jn.append(0.0)
            jt.append(0.0)
        caps = self._beam(caps)
        for _ in range(8):
            for k, (r, n, rxn, kn, target, vo) in enumerate(rows):
                vc = vs._sub(vs._add(v, vs._cross(w, r)), vo)
                new = min(max(jn[k] + (target - vs._dot(vc, n)) / kn, 0.0),
                          caps[k])
                d = new - jn[k]
                jn[k] = new
                v = vs._add(v, vs._mul(n, d))
                w = vs._add(w, vs._mul(inv_i(rxn), d))
                vc = vs._sub(vs._add(v, vs._cross(w, r)), vo)
                vt = vs._sub(vc, vs._mul(n, vs._dot(vc, n)))
                vt_len = vs._len(vt)
                if vt_len < 1e-6:
                    continue
                t = vs._mul(vt, 1.0 / vt_len)
                rxt = vs._cross(r, t)
                kt = 1.0 + vs._dot(t, vs._cross(inv_i(rxt), r))
                lim = FRICTION * jn[k]
                new = min(max(jt[k] - vt_len / kt, -lim), lim)
                d = new - jt[k]
                jt[k] = new
                v = vs._add(v, vs._mul(t, d))
                w = vs._add(w, vs._mul(inv_i(rxt), d))
        residual = self._settle(frame, contacts, jn, caps, approach, dt,
                                vels)
        push = self._push_out(contacts, residual)
        self._note_impact(self.tick, approach)
        self.touching = True
        return v, w, push, hit


# =============================================================================
# Damage: dents stored as blendShape targets in front of the skin
# =============================================================================

class Panel(object):
    """One mesh that can dent. Its rest points are the blendShape's space
    (in front of the skin), and each point reaches the world through its
    skin influences (or the mesh's own transform when it isn't skinned)."""

    def __init__(self, mesh, group=None):
        self.mesh = mesh
        self.group = group or mesh
        self.name = mesh.split("|")[-1]
        self.shape = _mesh_shape(mesh)
        self.sc = _skin_cluster(self.shape)
        out_path = _dag(self.shape)
        rest_mesh = om2.MFnMesh(out_path)
        if self.sc:
            sel = om2.MSelectionList()
            sel.add(self.sc)
            fn = oma2.MFnSkinCluster(sel.getDependNode(0))
            # The points going INTO the skin (the Orig shape plus tweaks and
            # anything else upstream), the space the dents are added in.
            # Not getInputGeometry(): that's the bind-time original.
            try:
                idx = fn.indexForOutputShape(out_path.node())
                plug = om2.MFnDependencyNode(sel.getDependNode(0)).findPlug(
                    "input", False).elementByLogicalIndex(idx).child(0)
                rest_mesh = om2.MFnMesh(plug.asMObject())
            except RuntimeError:
                inputs = fn.getInputGeometry()
                if len(inputs):
                    rest_mesh = om2.MFnMesh(inputs[0])
            paths = fn.influenceObjects()
            self.infl = list(paths)
            geo = om2.MMatrix(cmds.getAttr(self.sc + ".geomMatrix"))
            self.pre = []
            for path in paths:
                idx = fn.indexForInfluenceObject(path)
                bpm = om2.MMatrix(cmds.getAttr(
                    "%s.bindPreMatrix[%d]" % (self.sc, idx)))
                self.pre.append(geo * bpm)
            rest_xf = _m12(geo)
        else:
            self.infl = None
            self.pre = None
            rest_xf = _m12(out_path.inclusiveMatrix())
        pts = rest_mesh.getPoints(om2.MSpace.kObject)
        self.points = [(p.x, p.y, p.z) for p in pts]
        nrm = rest_mesh.getVertexNormals(True, om2.MSpace.kObject)
        self.normals = [(n.x, n.y, n.z) for n in nrm]
        self.rest = [_xf(rest_xf, p) for p in self.points]
        n_v = len(self.points)
        # Group the points by what moves them: one influence (fast path)
        # or a blend of several.
        self.groups = {}
        self.blend = {}
        self.vgroup = {}
        if self.sc:
            comp = om2.MFnSingleIndexedComponent()
            cobj = comp.create(om2.MFn.kMeshVertComponent)
            comp.setCompleteData(n_v)
            flat, n_inf = fn.getWeights(out_path, cobj)
            for v in range(n_v):
                row = [(k, flat[v * n_inf + k]) for k in range(n_inf)
                       if flat[v * n_inf + k] > 1e-5]
                if len(row) == 1 or (row and row[0][1] > 0.9995):
                    self.groups.setdefault(row[0][0], []).append(v)
                    self.vgroup[v] = row[0][0]
                elif row:
                    tot = sum(w for _, w in row)
                    self.blend[v] = [(k, w / tot) for k, w in row]
        else:
            self.groups[0] = list(range(n_v))
            self.vgroup = dict.fromkeys(range(n_v), 0)
        self.push = {}            # vertex -> [x, y, z] dent in rest space
        self.contact = set()      # vertices that ever touched an obstacle
        self.ground_immune = set()  # in the ground at rest: never dent
        self._noise = {}
        self.grid = None

    # ---- setup -------------------------------------------------------------

    def prepare(self, cell, max_crush, crumple, freq):
        self.cell = cell
        self.max_crush = max_crush
        self.crumple = crumple
        self.freq = freq
        self.grid = {}
        for k, verts in self.groups.items():
            g = self.grid.setdefault(k, {})
            for v in verts:
                p = self.points[v]
                key = (int(math.floor(p[0] / cell)),
                       int(math.floor(p[1] / cell)),
                       int(math.floor(p[2] / cell)))
                g.setdefault(key, []).append(v)
        self.lo, self.hi = _box_of(self.points)

    def update(self):
        """Read this frame's matrices (call after the time changed); the
        last frame's are kept for motion()."""
        prev = getattr(self, "mats", None)
        if self.sc:
            self.mats = [_m12(pre * path.inclusiveMatrix())
                         for pre, path in zip(self.pre, self.infl)]
        else:
            self.mats = [_m12(_dag(self.shape).inclusiveMatrix())]
        self.invs = [_inv(m) for m in self.mats]
        self.scale = _scale(self.mats[0])
        self.prev = prev or self.mats

    def rewind(self):
        """Back to the last update's matrices (before stepping between two
        frames)."""
        self.mats = self.prev
        self.invs = [_inv(m) for m in self.mats]

    # ---- the dent ----------------------------------------------------------

    def offset(self, v):
        """The vertex's total dent in rest space: the push plus the
        crumple buckle along its normal."""
        push = self.push.get(v)
        if push is None:
            return (0.0, 0.0, 0.0)
        mag = _len3(push)
        if not self.crumple or mag < 1e-9:
            return tuple(push)
        n = self._noise.get(v)
        if n is None:
            n = self._noise[v] = crumple_noise(self.rest[v], self.freq)
        # Metal that touched folds inward, away from what it hit; the metal
        # around it is squeezed and buckles out (a hood tents up).
        amt = self.crumple * mag * (-abs(n) if v in self.contact
                                    else 0.45 + 0.55 * n)
        nx, ny, nz = self.normals[v]
        return (push[0] + nx * amt, push[1] + ny * amt, push[2] + nz * amt)

    def _matrix(self, v, mats=None):
        mats = mats or self.mats
        if v in self.blend:
            row = self.blend[v]
            return _blend([mats[k] for k, _ in row], [w for _, w in row])
        return mats[self.vgroup.get(v, 0)]

    def motion_at(self, w):
        """How far this panel's body moved at world point w since the last
        frame (its main part: the first skin influence)."""
        here = _xf(self.invs[0], w)
        was = _xf(self.prev[0], here)
        return (w[0] - was[0], w[1] - was[1], w[2] - was[2])

    def motion(self, v):
        """How far the vertex's undented spot moved since the last frame
        (world)."""
        p = self.points[v]
        a = _xf(self._matrix(v, self.prev), p)
        b = _xf(self._matrix(v), p)
        return (b[0] - a[0], b[1] - a[1], b[2] - a[2])

    def world(self, v, m=None):
        m = m or self._matrix(v)
        o = self.offset(v)
        p = self.points[v]
        return _xf(m, (p[0] + o[0], p[1] + o[1], p[2] + o[2]))

    def world_box(self, pad):
        corners = _box_corners(self.lo, self.hi)
        pts = []
        for m in self.mats:
            pts.extend(_xf(m, c) for c in corners)
        lo, hi = _box_of(pts)
        return (tuple(x - pad for x in lo), tuple(x + pad for x in hi))

    def find(self, lo, hi):
        """(vertex, matrix, world position) of every dented vertex inside
        the world box [lo, hi]."""
        out = []
        pad = self.max_crush * (1.0 + self.crumple) / self.scale + self.cell
        corners = _box_corners(lo, hi)
        for k, grid in self.grid.items():
            m, inv = self.mats[k], self.invs[k]
            llo, lhi = _box_of([_xf(inv, c) for c in corners])
            c0 = [int(math.floor((llo[a] - pad) / self.cell)) for a in range(3)]
            c1 = [int(math.floor((lhi[a] + pad) / self.cell)) for a in range(3)]
            if (c1[0] - c0[0] + 1) * (c1[1] - c0[1] + 1) * \
                    (c1[2] - c0[2] + 1) > 4 * len(grid):
                keys = [key for key in grid
                        if all(c0[a] <= key[a] <= c1[a] for a in range(3))]
            else:
                keys = [(x, y, z) for x in range(c0[0], c1[0] + 1)
                        for y in range(c0[1], c1[1] + 1)
                        for z in range(c0[2], c1[2] + 1) if (x, y, z) in grid]
            for key in keys:
                for v in grid[key]:
                    w = self.world(v, m)
                    if _inside(w, lo, hi):
                        out.append((v, m, w))
        for v in self.blend:
            m = self._matrix(v)
            w = self.world(v, m)
            if _inside(w, lo, hi):
                out.append((v, m, w))
        return out

    def add(self, v, m, inc_world):
        """Push vertex v by a world-space increment (clamped to the deepest
        crush). Returns how far it actually moved, in world units."""
        inv = _inv(m)
        inc = _xv(inv, inc_world)
        old = self.push.get(v, [0.0, 0.0, 0.0])
        new = [old[0] + inc[0], old[1] + inc[1], old[2] + inc[2]]
        limit = self.max_crush / self.scale
        ln = _len3(new)
        if ln > limit:
            new = [c * limit / ln for c in new]
        moved = _len3((new[0] - old[0], new[1] - old[1], new[2] - old[2]))
        if moved > 1e-9:
            self.push[v] = new
        return moved * self.scale


def damage_nodes():
    """Every crash-damage blendShape in the scene."""
    return [n for n in cmds.ls(type="blendShape") or []
            if cmds.attributeQuery(TAG, node=n, exists=True)]


def has_damage():
    return bool(damage_nodes())


def clear_damage():
    """Delete every baked dent. Returns how many blendShapes went."""
    nodes = damage_nodes()
    for bs in nodes:
        curves = cmds.listConnections(bs + ".weight", s=True, d=False,
                                      type="animCurve") or []
        if curves:
            cmds.delete(curves)
        cmds.delete(bs)
    return len(nodes)


def _anim_range():
    lo = cmds.playbackOptions(q=True, min=True)
    hi = cmds.playbackOptions(q=True, max=True)
    for chan in ("translateX", "translateY", "translateZ",
                 "rotateX", "rotateY", "rotateZ"):
        keys = cmds.keyframe("%s.%s" % (GLOBAL, chan), q=True) or []
        if keys:
            lo, hi = min(lo, min(keys)), max(hi, max(keys))
    return int(math.floor(lo)), int(math.ceil(hi))


def _car_length(panels):
    frame = car_frame()
    ext = [0.0, 0.0]
    for panel in panels:
        pts = [_to_frame(frame, p) for p in _world_points(panel.mesh)]
        if pts:
            lo, hi = _box_of(pts)
            ext[0] = max(ext[0], hi[0] - lo[0])
            ext[1] = max(ext[1], hi[2] - lo[2])
    return max(ext) or 1.0


def _new_blendshape(panel):
    bs = cmds.blendShape(panel.mesh, frontOfChain=True,
                         n=re.sub(r"\W", "_", panel.name) + "_crash_BS")[0]
    cmds.addAttr(bs, ln=TAG, at="bool", dv=True)
    if cmds.objExists(CHASSIS) and cmds.attributeQuery(
            "crashDamage", node=CHASSIS, exists=True):
        cmds.connectAttr(CHASSIS + ".crashDamage", bs + ".envelope", f=True)
    return bs


def _write_target(bs, index, name, items, keys):
    base = "%s.inputTarget[0].inputTargetGroup[%d].inputTargetItem[6000]" % (
        bs, index)
    cmds.setAttr(base + ".inputPointsTarget", len(items),
                 *[(d[0], d[1], d[2], 1.0) for _, d in items],
                 type="pointArray")
    cmds.setAttr(base + ".inputComponentsTarget", len(items),
                 *["vtx[%d]" % v for v, _ in items], type="componentList")
    plug = "%s.weight[%d]" % (bs, index)
    cmds.setAttr(plug, 0.0)
    try:
        cmds.aliasAttr(name, plug)
    except RuntimeError:
        pass
    for f, val in keys:
        cmds.setKeyframe(plug, t=f, v=val)
    cmds.keyTangent(plug, itt="linear", ott="linear")


def bake_damage(start=None, end=None, meshes=None, settings=None,
                progress=None):
    """Walk the animation and dent every panel mesh where it went into an
    obstacle: the car's meshes, and any obstacle marked as denting too (a
    parked car). Where two dentable things meet, they share the crush, so
    their crumple zones end up against each other. Replaces any damage
    baked before. Default range: the playback range plus every frame
    C_global_CTRL is keyed on. `progress(fraction)` is called as it goes.
    Returns a summary dict."""
    clear_damage()
    summary = {"dents": 0, "panels": [], "contact_frames": 0,
               "first_hit": None}
    names = obstacles()
    s = read_settings(settings)
    ground = s["crashGround"]
    if not (names or ground) or not cmds.objExists(GLOBAL):
        return summary
    lo_f, hi_f = _anim_range()
    start = lo_f if start is None else int(start)
    end = hi_f if end is None else int(end)
    panel_names = panel_meshes() if meshes is None else meshes
    if not panel_names:
        cmds.warning("[crash] No car meshes to dent: bind the body to the "
                     "vehicle rig first (Bind Selected Mesh to Part).")
        return summary
    now = cmds.currentTime(q=True)
    suspended = False
    try:
        try:
            cmds.refresh(suspend=True)
            suspended = True
        except Exception:
            pass
        cmds.currentTime(start, edit=True, update=True)
        panels = [Panel(m, group="car") for m in panel_names]
        car_panels = len(panels)
        obs = Obstacles(names)
        soft = [n for n in soft_obstacles() if n not in panel_names]
        for n in soft:                  # it dents as well as being hit
            panels.append(Panel(n))
        for oi, it in enumerate(obs.items):
            for pi in range(car_panels, len(panels)):
                if it.get("group") == panels[pi].group:
                    it["panel"] = pi
        if soft:                        # ... and the car is its obstacle
            for pi in range(car_panels):
                obs.add_mesh(panels[pi].mesh, "car", pi)
        if ground:
            obs.add_ground(*ground_source())
        length = _car_length(panels[:car_panels])
        max_crush = s["crashMaxCrush"] * length
        radius = s["crashSpread"] * length
        depth_limit = 1.5 * max_crush + 0.05 * length
        tol = 1e-3 * length          # float noise, not a dent
        for panel in panels:
            panel.prepare(max(length / 12.0, 1e-3), max_crush,
                          0.35 * s["crashCrumple"], 1.0 / (0.06 * length))
            panel.update()
        centre = cmds.xform("C_chassis_BIND_JNT", q=True, ws=True, t=True) \
            if cmds.objExists("C_chassis_BIND_JNT") else \
            cmds.xform(GLOBAL, q=True, ws=True, t=True)
        obs.orient(tuple(centre))
        if obs.ground is not None:
            # Metal already in the ground at rest never dents on it.
            for panel in panels:
                box = obs.box(obs.ground, depth_limit,
                              panel.world_box(depth_limit))
                panel.ground_immune = {
                    v for v, _m, w in panel.find(*box)
                    if obs.query(w, [obs.ground], 1e9)}

        chunk = None
        chunks = []
        err_tol = 2.0 * tol
        total = max(1, end - start + 1)
        for fi, f in enumerate(range(start, end + 1)):
            if progress and fi % 10 == 0:
                progress(fi / float(total))
            sub_moved, sub_snap = None, {}
            if fi:
                cmds.currentTime(f, edit=True, update=True)
                obs.refresh()
                for panel in panels:
                    panel.update()
                steps = _substeps(panels, obs)
                if steps > 1:
                    # Fast past a thin obstacle: dent in between the frames
                    # too, or the metal jumps through it.
                    for panel in panels:
                        panel.rewind()
                    for k in range(1, steps):
                        cmds.currentTime(f - 1 + k / float(steps), edit=True,
                                         update=True)
                        obs.refresh()
                        for panel in panels:
                            panel.update()
                        r = _dent_frame(panels, obs, max_crush, radius,
                                        depth_limit, tol)
                        if r is not None:
                            sub_moved = (sub_moved or 0.0) + r[0]
                            for key, before in r[1].items():
                                sub_snap.setdefault(key, before)
                    cmds.currentTime(f, edit=True, update=True)
                    obs.refresh()
                    for panel in panels:
                        panel.update()
            moved = _dent_frame(panels, obs, max_crush, radius, depth_limit,
                                tol)
            if sub_moved is not None:
                if moved is None:
                    moved = (0.0, {})
                snap = dict(moved[1])
                snap.update(sub_snap)             # the earliest "before"
                moved = (moved[0] + sub_moved, snap)
            if moved is None or moved[0] <= tol:
                if chunk:
                    chunks.append(chunk)
                    chunk = None
                continue
            amount, snap = moved
            summary["contact_frames"] += 1
            if summary["first_hit"] is None:
                summary["first_hit"] = f
            if chunk is not None and _chunk_error(
                    panels, chunk, snap, amount) > err_tol:
                # One blend weight can't grow this frame's dents along with
                # the chunk's (the contact moved, a tumble): start a new one.
                chunks.append(chunk)
                chunk = None
            if chunk is None:
                chunk = {"first": f, "frames": [], "before": {}, "hist": []}
            for key, before in snap.items():
                chunk["before"].setdefault(key, before)
            chunk["frames"].append((f, amount))
            chunk["hist"].append({key: panels[key[0]].offset(key[1])
                                  for key in chunk["before"]})
            if len(chunk["frames"]) >= CHUNK:
                chunks.append(chunk)
                chunk = None
        if chunk:
            chunks.append(chunk)

        # ---- write the targets ----
        blendshapes = {}
        for chunk in chunks:
            tot = sum(a for _, a in chunk["frames"]) or 1.0
            keys, acc = [(chunk["first"] - 1, 0.0)], 0.0
            for f, a in chunk["frames"]:
                acc += a
                keys.append((f, min(1.0, acc / tot)))
            by_panel = {}
            last = chunk["hist"][-1]
            for (pi, v), before in chunk["before"].items():
                after = last[(pi, v)]
                d = (after[0] - before[0], after[1] - before[1],
                     after[2] - before[2])
                if _len3(d) > 1e-7:
                    by_panel.setdefault(pi, []).append((v, d))
            for pi, items in sorted(by_panel.items()):
                panel = panels[pi]
                bs = blendshapes.get(pi)
                if bs is None:
                    bs = blendshapes[pi] = _new_blendshape(panel)
                used = cmds.getAttr(bs + ".weight", mi=True) or []
                index = max(used) + 1 if used else 0
                name = ("crash_f%d" % chunk["first"]).replace("-", "m")
                _write_target(bs, index, name, sorted(items), keys)
                summary["dents"] += 1
        summary["panels"] = sorted(panels[pi].name for pi in blendshapes)
    finally:
        cmds.currentTime(now, edit=True, update=True)
        if suspended:
            cmds.refresh(suspend=False)
    if progress:
        progress(1.0)
    print("[crash] Baked %d dent(s) on %s over frames %d-%d."
          % (summary["dents"], ", ".join(summary["panels"]) or "nothing",
             start, end))
    return summary


def _substeps(panels, obs):
    """Pieces to cut the last frame into: enough that no part of the car
    moved more than 0.4 x the thinnest obstacle near it in one piece."""
    thin = obs.thinnest()
    if thin <= 0:
        return 1
    travel = 0.0
    for panel in panels:
        corners = _box_corners(panel.lo, panel.hi)
        for m, q in zip(panel.mats, panel.prev):
            for c in corners:
                a, b = _xf(q, c), _xf(m, c)
                travel = max(travel, _len3((b[0] - a[0], b[1] - a[1],
                                            b[2] - a[2])))
    if travel <= 0.4 * thin:
        return 1
    if not any(obs.near(*panel.world_box(travel)) for panel in panels):
        return 1
    return min(8, int(math.ceil(travel / (0.4 * thin))))


def _chunk_error(panels, chunk, snap, amount):
    """How far off one blend weight per frame would be if this frame's dents
    joined `chunk` (world-ish units, the worst vertex on the worst frame).
    The weight on frame k is the share of the chunk's movement done by
    then, so dents that move across the car within a chunk come out
    wrong."""
    before = dict(chunk["before"])
    for key, b in snap.items():
        before.setdefault(key, b)
    end = {key: panels[key[0]].offset(key[1]) for key in before}
    cum = sum(a for _, a in chunk["frames"]) + amount
    worst, acc = 0.0, 0.0
    for (_f, a), hist in zip(chunk["frames"], chunk["hist"]):
        acc += a
        c = acc / cum
        for key, b in before.items():
            e, h = end[key], hist.get(key, b)
            err = _len3((b[0] + c * (e[0] - b[0]) - h[0],
                         b[1] + c * (e[1] - b[1]) - h[1],
                         b[2] + c * (e[2] - b[2]) - h[2]))
            if err > worst:
                worst = err
    return worst


def _dent_frame(panels, obs, max_crush, radius, depth_limit, tol=0.0):
    """Dent at the current time (the panels already update()d). Returns
    None when nothing is near an obstacle, else (world distance moved,
    {(panel, vertex): offset before this step} for every vertex it moved)."""
    near = []
    for pi, panel in enumerate(panels):
        lo, hi = panel.world_box(0.0)
        ids = obs.near(lo, hi, depth_limit)
        if ids:
            near.append((pi, ids, (lo, hi)))
    if not near:
        return None

    # ---- what went inside an obstacle, and how far out it must come ----
    # The metal goes back out the way it came in (relative to what it hit):
    # it stays in front of a pole and wraps it, and a nose edge is pushed
    # back by the other car's nose instead of slipping over its bonnet. A
    # glancing scrape (it came in through a face at a shallow angle) is
    # pushed straight out instead.
    contacts = {}
    g = obs.ground
    for pi, ids, (plo, phi) in near:
        panel = panels[pi]
        clip = (tuple(x - depth_limit for x in plo),
                tuple(x + depth_limit for x in phi))
        for oi in ids:
            it = obs.items[oi]
            if it.get("group") == panel.group:
                continue                  # its own body
            # Two crumple zones meeting share the crush, so they end up
            # against each other instead of each flattening to the other's
            # undamaged shape.
            share = 0.5 if it.get("panel") is not None else 1.0
            immune = panel.ground_immune if oi == g else ()
            for v, m, w in panel.find(*obs.box(oi, depth_limit, clip)):
                if v in immune:
                    continue
                hit = obs.query(w, [oi], depth_limit)
                if hit is None:
                    continue
                depth, n, _ = hit
                if depth <= (5.0 * tol if oi == g else tol):
                    continue
                mv = panel.motion(v)
                om = obs.motion(oi, w, panels)
                rel = (mv[0] - om[0], mv[1] - om[1], mv[2] - om[2])
                d, dist = n, depth
                speed = _len3(rel)
                if speed > 1e-6:
                    back = (-rel[0] / speed, -rel[1] / speed,
                            -rel[2] / speed)
                    facing = sum(back[k] * n[k] for k in range(3))
                    if it.get("ground"):
                        if facing > 0.35:
                            d, dist = back, min(depth / facing, 3.0 * depth)
                    else:
                        # It came in through the side that's closer behind
                        # it than ahead of it (moving out again after a
                        # bounce, the way back is the long way through).
                        ex = obs.exit_along(oi, w, back, depth_limit)
                        if ex is not None and ex[2] > 0.35:
                            ahead = obs.exit_along(
                                oi, w, (-back[0], -back[1], -back[2]),
                                ex[0])
                            if ahead is None:
                                d, dist = back, ex[0]
                if share < 1.0:
                    # Only its share of the overlap its UNdented spot has
                    # with the other body, however many frames it takes.
                    w0 = _xf(m, panel.points[v])
                    if d is n:
                        hit0 = obs.query(w0, [oi], 2.0 * depth_limit)
                        full = hit0[0] if hit0 else dist
                    else:
                        ex0 = obs.exit_along(oi, w0, d, 4.0 * dist
                                             + 2.0 * depth_limit)
                        full = ex0[0] if ex0 else dist
                    dist -= (1.0 - share) * full
                    if dist <= tol:
                        continue
                old = contacts.get((pi, v))
                if old is None or dist < old[2]:
                    contacts[(pi, v)] = (m, w, dist, d)
    if not contacts:
        return 0.0, {}
    pushes = {key: (c[3][0] * c[2], c[3][1] * c[2], c[3][2] * c[2])
              for key, c in contacts.items()}

    snap = {}

    def remember(pi, v):
        if (pi, v) not in snap:
            snap[(pi, v)] = panels[pi].offset(v)

    # ---- sources for the spread: contacts clustered on a coarse grid ----
    sources = {}
    cell = max(radius * 0.5, 1e-6)
    for (pi, v), (m, w, depth, n) in contacts.items():
        inc = pushes[(pi, v)]
        key = (int(math.floor(w[0] / cell)), int(math.floor(w[1] / cell)),
               int(math.floor(w[2] / cell)))
        s = sources.setdefault(key, [0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0])
        for k in range(3):
            s[k] += w[k]
            s[3 + k] += inc[k]
        s[6] += 1

    moved = 0.0
    if radius > 0.0:
        src = [((s[0] / s[6], s[1] / s[6], s[2] / s[6]),
                (s[3] / s[6], s[4] / s[6], s[5] / s[6])) for s in
               sources.values()]
        lookup = {}
        for sp in src:
            key = tuple(int(math.floor(sp[0][k] / radius)) for k in range(3))
            lookup.setdefault(key, []).append(sp)
        slo, shi = _box_of([sp[0] for sp in src])
        slo = tuple(x - radius for x in slo)
        shi = tuple(x + radius for x in shi)
        r2 = radius * radius
        for pi, panel in enumerate(panels):
            plo, phi = panel.world_box(0.0)
            if not _overlap(slo, shi, plo, phi):
                continue
            for v, m, w in panel.find(slo, shi):
                if (pi, v) in contacts:
                    continue
                key = tuple(int(math.floor(w[k] / radius)) for k in range(3))
                num = [0.0, 0.0, 0.0]
                den = 0.0
                for dx in (-1, 0, 1):
                    for dy in (-1, 0, 1):
                        for dz in (-1, 0, 1):
                            for sp, inc in lookup.get(
                                    (key[0] + dx, key[1] + dy, key[2] + dz),
                                    ()):
                                d2 = ((w[0] - sp[0]) ** 2 + (w[1] - sp[1]) ** 2
                                      + (w[2] - sp[2]) ** 2)
                                if d2 >= r2:
                                    continue
                                t = 1.0 - d2 / r2
                                f = t * t
                                wt = f ** 4
                                num[0] += wt * f * inc[0]
                                num[1] += wt * f * inc[1]
                                num[2] += wt * f * inc[2]
                                den += wt
                if den <= 0.0:
                    continue
                inc = (num[0] / den, num[1] / den, num[2] / den)
                if _len3(inc) < 1e-7:
                    continue
                remember(pi, v)
                moved += panel.add(v, m, inc)

    for (pi, v), (m, w, depth, n) in contacts.items():
        remember(pi, v)
        panel = panels[pi]
        panel.contact.add(v)
        moved += panel.add(v, m, pushes[(pi, v)])
    return moved, snap
