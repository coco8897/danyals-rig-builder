"""
===============================================================================
 FACE BIND - skin a face that works, even on ONE mesh (head + body)
===============================================================================

 Select the character's meshes (or nothing: every mesh in the 'geo' group
 near the head) and run:

     import face_bind
     face_bind.smart_bind()

 Each mesh is recognised and bound the way that part should move:

   whole body / head mesh   keeps its body skin (makes one first if it has
                            none, or only has face joints), then a FACE LAYER
                            is painted on top, only around the face:
                              jaw     the lower face below the lip line, in
                                      front of the ears, down to under the
                                      chin (soft edges into the head / neck)
                              lips    split by the lip seam: a vert below it
                                      goes to the LOWER lip joints only, so
                                      the mouth can open
                              lids    same, split by the lash line, blended
                                      lash row -> crease row
                              brows, cheeks, nose, nostrils, ears: soft
                                      falloff around each joint
                            The rest of the body is not touched.
   eyeballs                 100 % on the eye joint (never bend in a blink)
   eyelashes                ride the lash row of their lid
   brows (separate mesh)    the brow joints of that side
   upper / lower teeth      the teeth joints (or head / jaw)
   tongue                   along the tongue chain
   inner mouth              head above the lip line, jaw below
   hair / hat               100 % head
   anything else            left alone (shoes, nails ... the body skin's job)

 Running it again re-does the face layer from scratch (the old face weights
 go back to the head first), so it's safe to repeat after moving a joint.
===============================================================================
"""

import math
import re

import maya.cmds as cmds
import maya.api.OpenMaya as om
import maya.api.OpenMayaAnim as oma

HEAD = "C_head_BIND_JNT"
JAW = "C_jaw_BIND_JNT"

# joints that belong to the face (moved back to the head before a re-layer)
_DETAIL_RE = re.compile(
    r"^(\w+_)?(lid|lip|eyelid|brow|cheek|nose|nostril|ear|tongue|jaw|"
    r"upperTeeth|lowerTeeth|teeth|eye)", re.I)
_BODY_KEEP = {HEAD, "C_head_tip_BIND_JNT"}

MAX_INFLUENCES = 6

KINDS = ("body", "head", "eye", "lashes", "brows", "teeth_upper",
         "teeth_lower", "tongue", "mouth", "hair", "skip")


# =============================================================================
# Small maths
# =============================================================================

def _wp(n):
    return om.MVector(cmds.xform(n, q=True, ws=True, t=True))


def _smooth(e0, e1, x):
    """0 at e0 -> 1 at e1 (either order), smoothstep."""
    if e1 == e0:
        return 1.0 if x >= e1 else 0.0
    t = max(0.0, min(1.0, (x - e0) / (e1 - e0)))
    return t * t * (3.0 - 2.0 * t)


def _falloff(d, r0, r1):
    """1 inside r0, 0 outside r1."""
    return 1.0 - _smooth(r0, r1, d)


def _seg_closest(p, a, b):
    ab = b - a
    ll = ab * ab
    t = 0.0 if ll < 1e-12 else max(0.0, min(1.0, ((p - a) * ab) / ll))
    q = a + ab * t
    return (p - q).length(), t


def _label(graph, upper, lower, limit):
    """Multi-source shortest paths over the mesh edges from the upper and
    lower edge of an opening (their shared corner points excluded).
    {vertex: (is_upper, distance)} for vertices within `limit`."""
    import heapq
    heap = []
    for items, is_up in ((upper[1:-1], True), (lower[1:-1], False)):
        for v, _ in items:
            heap.append((0.0, v, is_up))
    heapq.heapify(heap)
    out = {}
    while heap:
        d, v, is_up = heapq.heappop(heap)
        if v in out:
            continue
        out[v] = (is_up, d)
        for n, ln in graph[v]:
            nd = d + ln
            if n not in out and nd <= limit:
                heapq.heappush(heap, (nd, n, is_up))
    return out


def _poly_closest(p, pts):
    """(distance, segment index, t) to a polyline."""
    best = (1e30, 0, 0.0)
    for i in range(len(pts) - 1):
        d, t = _seg_closest(p, pts[i], pts[i + 1])
        if d < best[0]:
            best = (d, i, t)
    if len(pts) == 1:
        best = ((p - pts[0]).length(), 0, 0.0)
    return best


def _row(pattern):
    """BIND joints matching pattern, ordered along the row by name number."""
    js = cmds.ls(pattern, type="joint") or []

    def num(j):
        m = re.search(r"_(\d+)_BIND_JNT$", j)
        return int(m.group(1)) if m else 0
    return sorted(js, key=num)


# =============================================================================
# The face, measured
# =============================================================================

class Face(object):
    """Landmarks of the built face in a head frame (x = character's left,
    y = up, z = forward)."""

    def __init__(self):
        if not cmds.objExists(HEAD):
            raise RuntimeError("No C_head_BIND_JNT: build the rig first.")
        self.head = _wp(HEAD)
        eyes = [j for j in ("L_eye_BIND_JNT", "R_eye_BIND_JNT")
                if cmds.objExists(j)]
        if len(eyes) == 2:
            x = _wp(eyes[0]) - _wp(eyes[1])
            self.spacing = x.length()
            x = x.normal()
        else:
            x, self.spacing = om.MVector(1, 0, 0), 6.0
        y = om.MVector(0, 1, 0)
        y = (y - x * (y * x)).normal()
        z = (x ^ y).normal()
        self.axes = (x, y, z)
        self.S = self.spacing                     # the unit for every radius

        L = self.local
        self.jaw = L(_wp(JAW)) if cmds.objExists(JAW) else None
        chin = next((j for j in ("C_jawTip_BIND_JNT", "C_chin_BIND_JNT")
                     if cmds.objExists(j)), None)
        self.chin = L(_wp(chin)) if chin else None
        self.lip_up = [(j, L(_wp(j))) for j in _row("C_lipUpper_*_BIND_JNT")]
        self.lip_lo = [(j, L(_wp(j))) for j in _row("C_lipLower_*_BIND_JNT")]
        for row in (self.lip_up, self.lip_lo):
            row.sort(key=lambda jp: jp[1].x)
        if self.lip_up and self.lip_lo:
            allp = [p for _, p in self.lip_up + self.lip_lo]
            self.mouth = sum(allp, om.MVector()) / len(allp)
            xs = [p.x for p in allp]
            self.mouth_w = max(1e-3, max(xs) - min(xs))
        else:
            self.mouth = None
            self.mouth_w = self.S
        if self.chin is None and self.mouth is not None:
            self.chin = self.mouth + om.MVector(0, -0.8 * self.mouth_w,
                                                -0.1 * self.mouth_w)
        self.eyes = {}
        for s in ("L", "R"):
            rows = {}
            for key, pat in (("up", "%s_lidUpper_*_BIND_JNT"),
                             ("lo", "%s_lidLower_*_BIND_JNT"),
                             ("up_out", "%s_lidUpperOuter_*_BIND_JNT"),
                             ("lo_out", "%s_lidLowerOuter_*_BIND_JNT")):
                row = [(j, L(_wp(j))) for j in _row(pat % s)]
                row.sort(key=lambda jp: jp[1].x)
                rows[key] = row
            if rows["up"] and rows["lo"]:
                eye = "%s_eye_BIND_JNT" % s
                pts = [p for _, p in rows["up"] + rows["lo"]]
                rows["centre"] = sum(pts, om.MVector()) / len(pts)
                xs = [p.x for p in pts]
                rows["width"] = max(1e-3, max(xs) - min(xs))
                rows["ball"] = L(_wp(eye)) if cmds.objExists(eye) else None
                self.eyes[s] = rows
        self.points = []            # (joint, local pos, r0, r1)
        for pat, r0, r1 in (("?_brow*_BIND_JNT", 0.18, 0.7),
                            ("?_cheek*_BIND_JNT", 0.12, 0.6),
                            ("C_nose*_BIND_JNT", 0.08, 0.35),
                            ("?_nostril*_BIND_JNT", 0.05, 0.22),
                            ("?_ear*_BIND_JNT", 0.15, 0.4)):
            for j in cmds.ls(pat, type="joint") or []:
                self.points.append((j, L(_wp(j)), r0 * self.S, r1 * self.S))

    def local(self, p):
        d = p - self.head
        x, y, z = self.axes
        return om.MVector(d * x, d * y, d * z)

    # ---- lines ---------------------------------------------------------------
    @staticmethod
    def _row_y(row, x):
        """Height of a joint row at local x (clamped to its ends)."""
        if x <= row[0][1].x:
            return row[0][1].y, row[0][1].z
        if x >= row[-1][1].x:
            return row[-1][1].y, row[-1][1].z
        for i in range(len(row) - 1):
            a, b = row[i][1], row[i + 1][1]
            if a.x <= x <= b.x:
                t = (x - a.x) / max(1e-9, b.x - a.x)
                return a.y + (b.y - a.y) * t, a.z + (b.z - a.z) * t
        return row[-1][1].y, row[-1][1].z

    def seam(self, x):
        """Lip seam height / depth at x: the middle of the mesh's mouth
        opening when it has one (the exact split between the lips), else
        between the two joint rows."""
        up, lo = (self.mouth_hole if self.mouth_hole
                  else (self.lip_up, self.lip_lo))
        uy, uz = self._row_y(up, x)
        ly, lz = self._row_y(lo, x)
        return 0.5 * (uy + ly), 0.5 * (uz + lz)

    def lid_seam(self, side, x):
        e = self.eyes[side]
        up, lo = e.get("hole") or (e["up"], e["lo"])
        return 0.5 * (self._row_y(up, x)[0] + self._row_y(lo, x)[0])

    mouth_hole = None
    labels = None

    def use_mesh(self, mesh):
        """Read the mesh's mouth / eye openings (border loops). Every vertex
        near one is labelled UPPER or LOWER by which edge of the opening it
        reaches first ALONG THE SURFACE (not by height): an upper lip's
        inner wall that hangs below the lower lip's edge is still upper."""
        self.mouth_hole, self.labels = None, {}
        for e in self.eyes.values():
            e.pop("hole", None)
        try:
            dag = _shape_path(mesh)
        except RuntimeError:
            return
        pts = _bind_points(mesh)
        nv = len(pts)
        graph = [[] for _ in range(nv)]
        border = {}
        it = om.MItMeshEdge(dag)
        while not it.isDone():
            a, b = it.vertexId(0), it.vertexId(1)
            ln = (pts[a] - pts[b]).length()
            graph[a].append((b, ln))
            graph[b].append((a, ln))
            if it.onBoundary():
                border.setdefault(a, []).append(b)
                border.setdefault(b, []).append(a)
            it.next()
        seen = set()
        for start in border:
            if start in seen or len(border[start]) != 2:
                continue
            loop, prev, cur = [start], None, start
            seen.add(start)
            while True:
                nxt = [n for n in border[cur] if n != prev]
                if not nxt or nxt[0] == start or nxt[0] in seen:
                    break
                prev, cur = cur, nxt[0]
                loop.append(cur)
                seen.add(cur)
            if len(loop) < 6 or len(loop) > 400:
                continue
            items = [(i, self.local(om.MVector(pts[i]))) for i in loop]
            centre = sum((p for _, p in items), om.MVector()) / len(items)
            split = self._split_loop(items)
            if split is None:
                continue
            up, lo = split
            chains = ([(None, p) for _, p in up], [(None, p) for _, p in lo])
            if self.mouth is not None and self.mouth_hole is None and \
                    (centre - self.mouth).length() < 0.4 * self.mouth_w:
                self.mouth_hole = chains
                self.labels["mouth"] = _label(graph, up, lo,
                                              1.2 * self.mouth_w)
                continue
            for s, e in self.eyes.items():
                if "hole" not in e and \
                        (centre - e["centre"]).length() < 0.4 * e["width"]:
                    e["hole"] = chains
                    self.labels[s] = _label(graph, up, lo, 1.0 * e["width"])
                    break

    @staticmethod
    def _split_loop(items):
        """A closed loop [(vertex, pos)] -> (upper, lower), each sorted by x,
        split at the loop's left- and right-most points (both ends shared)."""
        n = len(items)
        i0 = min(range(n), key=lambda i: items[i][1].x)
        i1 = max(range(n), key=lambda i: items[i][1].x)
        if i0 == i1:
            return None
        a = [items[(i0 + k) % n] for k in range(((i1 - i0) % n) + 1)]
        b = [items[(i1 + k) % n] for k in range(((i0 - i1) % n) + 1)]
        if len(a) < 3 or len(b) < 3:
            return None
        ya = sum(p.y for _, p in a) / len(a)
        yb = sum(p.y for _, p in b) / len(b)
        up, lo = (a, b) if ya >= yb else (b, a)
        return (sorted(up, key=lambda t: t[1].x),
                sorted(lo, key=lambda t: t[1].x))

    def label(self, key, v):
        """(is_upper, surface distance to the opening) or None."""
        if v is None or not self.labels:
            return None
        return (self.labels.get(key) or {}).get(v)

    # ---- the jaw -----------------------------------------------------------------
    def jaw_weight(self, p, gate_front=True, v=None):
        if self.jaw is None or self.mouth is None:
            return 0.0
        S = self.S
        corner = self.lip_up[-1][1] if p.x >= 0 else self.lip_up[0][1]
        seam_y, seam_z = self.seam(p.x)
        # the jaw line: the lip seam at the front rising to the jaw pivot at
        # the back (the lower cheek / jowl go with the jaw, the upper don't)
        span = max(1e-3, corner.z - self.jaw.z)
        t = max(0.0, min(1.0, (corner.z - p.z) / span))
        line = seam_y + (self.jaw.y - 0.15 * S - seam_y) * t
        # between the corners the lips own the seam: nothing above it goes
        # with the jaw. Out on the cheeks the edge is wide and soft, so the
        # open jaw doesn't crease a line across the face.
        out = _smooth(abs(corner.x), abs(corner.x) + 0.8 * S, abs(p.x))
        side = max(out, t)
        below = 0.3 * S + 0.3 * S * side
        above = 0.55 * S * side
        if not gate_front:              # inside the mouth: roof / floor
            below, above = 0.25 * S, 0.0
        w = _smooth(line + above, line - below, p.y)
        lab = self.label("mouth", v)
        if lab is not None:
            # right by the lips the surface label decides; further out the
            # height rule takes over
            k = _smooth(0.25 * self.mouth_w, 0.7 * self.mouth_w, lab[1])
            w = (0.0 if lab[0] else 1.0) * (1.0 - k) + w * k
        if w <= 0.0:
            return 0.0
        if gate_front:
            w *= _smooth(self.jaw.z - 0.25 * S, self.jaw.z + 0.35 * S, p.z)
        # between the ears
        ears = [pt for j, pt, _, _ in self.points if "_ear" in j]
        half = max(abs(e.x) for e in ears) if ears else 1.35 * S
        w *= _falloff(abs(p.x), 0.85 * half, 1.1 * half)
        # under the chin the skin fades into the neck
        if self.chin is not None and gate_front:
            g_z = self.jaw.z + 0.15 * S
            g_y = self.chin.y + 0.35 * (self.jaw.y - self.chin.y)
            dz = self.chin.z - g_z
            u = max(0.0, min(1.0, (self.chin.z - p.z) / dz)) if abs(dz) > 1e-6 \
                else 0.0
            under = self.chin.y + (g_y - self.chin.y) * u
            w *= _smooth(under - 0.35 * S, under + 0.05 * S, p.y)
        return w

    # ---- features -----------------------------------------------------------
    def _row_weights(self, row, p):
        pts = [pt for _, pt in row]
        d, i, t = _poly_closest(p, pts)
        if len(row) == 1:
            return d, {row[0][0]: 1.0}
        return d, {row[i][0]: 1.0 - t, row[i + 1][0]: t}

    def lip_layer(self, p, v=None):
        if self.mouth is None:
            return 0.0, {}
        W = self.mouth_w
        lab = self.label("mouth", v)
        if lab is not None:
            upper = lab[0]
        else:
            upper = p.y >= self.seam(p.x)[0]
        row = self.lip_up if upper else self.lip_lo
        d, ws = self._row_weights(row, p)
        return _falloff(d, 0.05 * W, 0.34 * W), ws

    def lid_layer(self, p, v=None):
        best = (0.0, {})
        for s, e in self.eyes.items():
            E = e["width"]
            if (p - e["centre"]).length() > 1.2 * E:
                continue
            lab = self.label(s, v)
            upper = lab[0] if lab is not None else \
                p.y >= self.lid_seam(s, p.x)
            lash = e["up"] if upper else e["lo"]
            crease = e["up_out"] if upper else e["lo_out"]
            dl, wl = self._row_weights(lash, p)
            ws = dict(wl)
            d = dl
            if crease:
                dc, wc = self._row_weights(crease, p)
                # 0 on the lash line. With the mesh's eye opening known,
                # measure from the opening itself (along the surface): the
                # edge verts are then 100 % lash and close with the lid,
                # even when the lash joints sit a hair off the edge.
                d_open = lab[1] if lab is not None else dl
                a = d_open / max(1e-9, d_open + dc)
                ws = {j: v * (1.0 - a) for j, v in wl.items()}
                for j, v in wc.items():
                    ws[j] = ws.get(j, 0.0) + v * a
                d = min(dl, dc)
            s_ = _falloff(d, 0.04 * E, 0.3 * E)
            # never reach behind the eyeball's centre (the socket / skull)
            if e["ball"] is not None:
                s_ *= _smooth(e["ball"].z - 0.1 * E, e["ball"].z + 0.15 * E,
                              p.z)
            if lab is not None:
                # the rim of the opening is ALL lid, or the head's share
                # holds the edge open on a full blink
                s_ = max(s_, _falloff(lab[1], 0.06 * E, 0.3 * E))
            if s_ > best[0]:
                best = (s_, ws)
        return best

    def point_layers(self, p):
        out = []
        for j, pt, r0, r1 in self.points:
            s_ = _falloff((p - pt).length(), r0, r1)
            if s_ > 0.0:
                out.append((s_, {j: 1.0}))
        return out

    def detail(self, p, v=None):
        """(strength 0..1, {joint: weight}) of the face features at p
        (v: the vertex id, for the surface labels)."""
        lip, lid = self.lip_layer(p, v), self.lid_layer(p, v)
        # lids and lips own their skin: brows / cheeks / nose fade out there
        keep = (1.0 - max(lip[0], lid[0])) ** 2
        layers = [lip, lid] + [(s_ * keep, w)
                               for s_, w in self.point_layers(p)]
        layers = [(s_, w) for s_, w in layers if s_ > 1e-4]
        if not layers:
            return 0.0, {}
        strength = max(s_ for s_, _ in layers)
        total = sum(s_ * s_ for s_, _ in layers)
        out = {}
        for s_, ws in layers:
            k = s_ * s_ / total
            for j, v in ws.items():
                out[j] = out.get(j, 0.0) + v * k
        return strength, out


# =============================================================================
# Mesh helpers
# =============================================================================

def _shape_path(mesh):
    sel = om.MSelectionList()
    sel.add(mesh)
    dag = sel.getDagPath(0)
    dag.extendToShape()
    if dag.node().hasFn(om.MFn.kMesh) and om.MFnDagNode(dag).isIntermediateObject:
        shapes = cmds.listRelatives(mesh, s=True, ni=True, f=True) or []
        sel = om.MSelectionList()
        sel.add(shapes[0])
        dag = sel.getDagPath(0)
    return dag


def _skin(mesh):
    for s in cmds.listRelatives(mesh, s=True, ni=True, f=True) or [mesh]:
        scs = cmds.ls(cmds.listHistory(s) or [], type="skinCluster")
        if scs:
            return scs[0]
    return None


def _bind_points(mesh):
    """Rest (bind pose) positions: the skin's input if skinned."""
    sc = _skin(mesh)
    dag = _shape_path(mesh)
    pts = om.MFnMesh(dag).getPoints(om.MSpace.kWorld)
    if not sc:
        return pts
    # the orig shape holds the undeformed points (in object space)
    orig = [s for s in cmds.listRelatives(mesh, s=True, f=True) or []
            if cmds.getAttr(s + ".intermediateObject")
            and cmds.listConnections(s + ".worldMesh", d=True, s=False)]
    if orig:
        sel = om.MSelectionList()
        sel.add(orig[0])
        o = om.MFnMesh(sel.getDagPath(0)).getPoints(om.MSpace.kObject)
        wm = om.MMatrix(cmds.getAttr(mesh + ".worldMatrix[0]"))
        return [om.MPoint(p) * wm for p in o]
    return pts


def is_detail_joint(j):
    base = j.split("|")[-1]
    return bool(_DETAIL_RE.match(base)) and base not in _BODY_KEEP


def rig_joints():
    roots = [g for g in ("CHARACTER_RIG_GRP", "QUADRUPED_RIG_GRP",
                         "BIRD_RIG_GRP") if cmds.objExists(g)]
    js = []
    for r in roots:
        js += [j for j in cmds.listRelatives(r, ad=True, type="joint") or []
               if j.endswith("_BIND_JNT")]
    return sorted(set(js))


def body_joints():
    return [j for j in rig_joints() if not is_detail_joint(j)]


def face_joints():
    return sorted({j for j in cmds.ls("*_BIND_JNT", type="joint") or []
                   if is_detail_joint(j)})


def _bbox(mesh):
    return cmds.exactWorldBoundingBox(mesh, ignoreInvisible=False)


def classify(mesh, face=None):
    """What a mesh is, from its name first, then where and how big it is."""
    face = face or Face()
    name = mesh.split("|")[-1].lower()
    bb = _bbox(mesh)
    lo, hi = om.MVector(bb[0], bb[1], bb[2]), om.MVector(bb[3], bb[4], bb[5])
    centre = (lo + hi) * 0.5
    size = (hi - lo).length()
    S = face.S
    neck_y = _wp("C_neck_BIND_JNT").y if cmds.objExists(
        "C_neck_BIND_JNT") else face.head.y - 1.5 * S
    near_head = (centre - face.head).length() < 3.5 * S
    if "lash" in name:
        return "lashes"
    if "brow" in name:
        return "brows"
    if re.search(r"tooth|teeth|gum", name):
        if re.search(r"low|bot|lwr|under|jaw", name):
            return "teeth_lower"
        if re.search(r"up|top", name):
            return "teeth_upper"
        mouth_y = face.mouth.y + face.head.y if face.mouth is not None \
            else face.head.y
        return "teeth_lower" if centre.y < mouth_y else "teeth_upper"
    if "tongue" in name:
        return "tongue"
    if re.search(r"mouth|palate|cavity|oral", name):
        return "mouth"
    if re.search(r"hair|hat|helmet|cap\b|beanie|scalp", name) and near_head:
        return "hair"
    for s in ("L", "R"):
        eye = "%s_eye_BIND_JNT" % s
        if cmds.objExists(eye) and re.search(r"eye|iris|cornea|pupil|ball",
                                             name):
            if (centre - _wp(eye)).length() < 0.6 * S and size < 2.2 * S:
                return "eye"
    if lo.y < neck_y - 1.0 * S and hi.y > face.head.y:
        return "body"
    if near_head and hi.y > face.head.y and lo.y > neck_y - 1.0 * S \
            and size > 2.0 * S:
        return "head"
    return "skip"


def _write_weights(mesh, sc, rows, verts=None):
    """rows: [{joint: weight}] per vertex (all verts, or `verts`)."""
    infl = [p.partialPathName() for p in oma.MFnSkinCluster(
        _depend(sc)).influenceObjects()]
    need = sorted({j for r in rows for j in r} - set(infl))
    if need:
        cmds.skinCluster(sc, e=True, ai=need, lw=True, wt=0.0)
        for j in need:
            cmds.setAttr(j + ".liw", 0)
    fn = oma.MFnSkinCluster(_depend(sc))
    infl = [p.partialPathName() for p in fn.influenceObjects()]
    idx = {j: i for i, j in enumerate(infl)}
    n = len(infl)
    dag = _shape_path(mesh)
    comp = om.MFnSingleIndexedComponent()
    cobj = comp.create(om.MFn.kMeshVertComponent)
    ids = list(range(len(rows))) if verts is None else list(verts)
    comp.addElements(ids)
    arr = om.MDoubleArray(len(ids) * n, 0.0)
    for r, row in enumerate(rows):
        for j, v in row.items():
            arr[r * n + idx[j]] = v
    for j in infl:                     # a locked influence would block us
        if cmds.getAttr(j + ".liw"):
            cmds.setAttr(j + ".liw", 0)
    fn.setWeights(dag, cobj, om.MIntArray(list(range(n))), arr, False)
    return len(ids)


def _depend(node):
    sel = om.MSelectionList()
    sel.add(node)
    return sel.getDependNode(0)


def _read_weights(mesh, sc):
    fn = oma.MFnSkinCluster(_depend(sc))
    infl = [p.partialPathName() for p in fn.influenceObjects()]
    dag = _shape_path(mesh)
    comp = om.MFnSingleIndexedComponent()
    cobj = comp.create(om.MFn.kMeshVertComponent)
    count = om.MFnMesh(dag).numVertices
    comp.setCompleteData(count)
    w, n = fn.getWeights(dag, cobj)
    rows = []
    for v in range(count):
        base = v * n
        rows.append({infl[i]: w[base + i] for i in range(n)
                     if w[base + i] > 1e-6})
    return rows


def _prune(row, limit=MAX_INFLUENCES):
    items = sorted(((v, j) for j, v in row.items() if v > 1e-5),
                   reverse=True)[:limit]
    s = sum(v for v, _ in items) or 1.0
    return {j: v / s for v, j in items}


def _new_skin(mesh, joints, geodesic=True, mi=4):
    """Smooth bind to joints: Geodesic Voxel when there's a GPU context, else
    closest distance."""
    old = _skin(mesh)
    if old:
        cmds.skinCluster(old, e=True, ub=True)
    short = mesh.split("|")[-1]
    if geodesic:
        try:
            sc = cmds.skinCluster(joints, mesh, toSelectedBones=True,
                                  bindMethod=3, skinMethod=0,
                                  maximumInfluences=mi, obeyMaxInfluences=True,
                                  normalizeWeights=1, weightDistribution=1,
                                  name=short + "_skinCluster")[0]
            try:
                cmds.geomBind(sc, bindMethod=3, falloff=0.2,
                              maxInfluences=mi,
                              geodesicVoxelParams=(256, True))
                return sc, "geodesic voxel"
            except RuntimeError:
                cmds.skinCluster(sc, e=True, ub=True)
        except RuntimeError:
            pass
    sc = cmds.skinCluster(joints, mesh, toSelectedBones=True, bindMethod=0,
                          maximumInfluences=mi, obeyMaxInfluences=False,
                          dropoffRate=4.0, normalizeWeights=1,
                          name=short + "_skinCluster")[0]
    return sc, "closest distance"


def _copy_skin(source, mesh):
    """Bind mesh to source's joints and copy its weights by closest point
    (a separate brow / beard mesh then moves exactly with the skin under
    it)."""
    src = _skin(source)
    old = _skin(mesh)
    if old:
        cmds.skinCluster(old, e=True, ub=True)
    infl = cmds.skinCluster(src, q=True, inf=True)
    dst = cmds.skinCluster(infl, mesh, toSelectedBones=True, bindMethod=0,
                           maximumInfluences=MAX_INFLUENCES,
                           normalizeWeights=1,
                           name=mesh.split("|")[-1] + "_skinCluster")[0]
    cmds.copySkinWeights(ss=src, ds=dst, noMirror=True,
                         surfaceAssociation="closestPoint",
                         influenceAssociation=["oneToOne", "name"],
                         normalize=True)
    try:
        cmds.skinCluster(dst, e=True, removeUnusedInfluence=True)
    except RuntimeError:
        pass
    return dst


def _rigid(mesh, rows_fn, joints):
    """(Re)bind a small face part to joints and set weights per vertex."""
    joints = sorted({j for j in joints if cmds.objExists(j)} | {HEAD})
    old = _skin(mesh)
    pts = _bind_points(mesh) if old else om.MFnMesh(
        _shape_path(mesh)).getPoints(om.MSpace.kWorld)
    if old:
        cmds.skinCluster(old, e=True, ub=True)
    sc = cmds.skinCluster(joints, mesh, toSelectedBones=True, bindMethod=0,
                          maximumInfluences=4, obeyMaxInfluences=False,
                          normalizeWeights=1,
                          name=mesh.split("|")[-1] + "_skinCluster")[0]
    rows = [_prune(rows_fn(om.MVector(p)), 4) for p in pts]
    _write_weights(mesh, sc, rows)
    return sc


# =============================================================================
# Binding each kind
# =============================================================================

def face_layer(mesh, face=None):
    """Re-do the face part of an already skinned body / head mesh. Returns
    the number of vertices with face weights."""
    face = face or Face()
    sc = _skin(mesh)
    if not sc:
        raise RuntimeError("%s has no skin yet." % mesh)
    pts = _bind_points(mesh)
    face.use_mesh(mesh)
    old = _read_weights(mesh, sc)
    head_ok = cmds.objExists(HEAD)
    rows, touched = [], 0
    for v, p in enumerate(pts):
        row = dict(old[v])
        # 1. any old face weight goes back to the body joints that vertex
        #    already has (or the head), so this can be run again
        moved = 0.0
        for j in list(row):
            if is_detail_joint(j):
                moved += row.pop(j)
        if moved:
            rest = sum(row.values())
            if rest > 1e-6:
                row = {j: x * (rest + moved) / rest for j, x in row.items()}
            elif head_ok:
                row[HEAD] = moved
        lp = face.local(om.MVector(p))
        # 2. the jaw takes the lower face
        wj = face.jaw_weight(lp, v=v)
        if wj > 0.0:
            row = {j: x * (1.0 - wj) for j, x in row.items()}
            row[JAW] = row.get(JAW, 0.0) + wj
        # 3. lips, lids, brows, cheeks, nose, ears on top
        s_, det = face.detail(lp, v)
        if s_ > 0.0:
            row = {j: x * (1.0 - s_) for j, x in row.items()}
            for j, x in det.items():
                row[j] = row.get(j, 0.0) + x * s_
        if wj > 0.0 or s_ > 0.0:
            touched += 1
        rows.append(_prune(row) if row else {HEAD: 1.0})
    _write_weights(mesh, sc, rows)
    return touched


def _needs_body_skin(mesh):
    sc = _skin(mesh)
    if not sc:
        return True
    infl = cmds.skinCluster(sc, q=True, inf=True) or []
    body = [j for j in infl if not is_detail_joint(j)
            and j not in (HEAD, "C_neck_BIND_JNT")]
    rig_body = [j for j in body_joints() if j not in (HEAD,
                                                     "C_neck_BIND_JNT")]
    # skinned only to face / head joints while the rig has a body: the old
    # face-only bind. Start the body skin again.
    return bool(rig_body) and not body or not any(
        not is_detail_joint(j) for j in infl)


class _neutral_face(object):
    """Every control at rest while binding (blink 0, smile 0, jaw shut,
    shape dials 0 ...): the joints are measured where the mesh was modelled,
    not wherever the last pose or capture take left them."""

    def __enter__(self):
        self.held = []
        for c in cmds.ls("*_CTRL", type="transform") or []:
            ud = []
            for a in cmds.listAttr(c, ud=True, k=True) or []:
                try:
                    if cmds.getAttr(f"{c}.{a}", type=True) in (
                            "double", "float", "doubleLinear", "doubleAngle",
                            "long", "short", "bool", "enum"):
                        ud.append(a)
                except (RuntimeError, ValueError):
                    pass
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


def smart_bind(meshes=None, geodesic=True):
    """Bind the face the right way for each mesh (measured with the rig at
    rest, whatever pose it's in). Returns {mesh: note}."""
    try:
        import advanced_face
        rest = advanced_face.neutral_pose()
    except ImportError:
        rest = _neutral_face()
    with rest:
        report = _smart_bind(meshes, geodesic)
    return report


def fit_lid_overlap(mesh, face=None, margin=0.03):
    # margin: extra closing as a fraction of the eye's width (a straight
    # side-on measure misses the curve of the lid near the corners)
    """How far past each other the lids must close so the MESH's eye
    opening shuts (its edge can sit a little outside the lash joints).
    Measured on a full blink and written to L/R_blink_CTRL.lidOverlap.
    Returns {side: overlap}."""
    face = face or Face()
    face.use_mesh(mesh)
    dag = _shape_path(mesh)
    rest = _bind_points(mesh)
    out = {}
    for s in ("L", "R"):
        ctrl = "%s_blink_CTRL" % s
        lab = face.labels.get(s)
        if not lab or not cmds.objExists(ctrl) or not cmds.attributeQuery(
                "lidOverlap", node=ctrl, exists=True):
            continue
        up = sorted((v for v, (u, d) in lab.items() if u and d == 0),
                    key=lambda v: rest[v].x)
        lo = sorted((v for v, (u, d) in lab.items() if not u and d == 0),
                    key=lambda v: rest[v].x)
        if len(up) < 3 or len(lo) < 3:
            continue
        cmds.setAttr(ctrl + ".lidOverlap", 0.0)
        lap = 0.0
        extra = margin * face.eyes[s]["width"] if s in face.eyes else margin
        for _ in range(2):
            cmds.setAttr(ctrl + ".blink", 1.0)
            cur = om.MFnMesh(dag).getPoints(om.MSpace.kWorld)
            cmds.setAttr(ctrl + ".blink", 0.0)
            gaps = []
            lower = [(cur[v].x, cur[v].y) for v in lo]
            for v in up[1:-1]:
                x, y = cur[v].x, cur[v].y
                for (x0, y0), (x1, y1) in zip(lower, lower[1:]):
                    if min(x0, x1) <= x <= max(x0, x1) and x1 != x0:
                        gaps.append(y - (y0 + (y1 - y0) * (x - x0) / (x1 - x0)))
                        break
            if not gaps:
                break
            gaps.sort()
            worst = gaps[int(0.95 * (len(gaps) - 1))]  # the whole lid shut
            if worst <= 0.0:
                break
            lap += worst + extra
            cmds.setAttr(ctrl + ".lidOverlap", lap)
        out[s] = round(lap, 4)
    return out


def _smart_bind(meshes=None, geodesic=True):
    face = Face()
    if not meshes:
        meshes = [m for m in cmds.ls(sl=True, type="transform", l=True) or []
                  if cmds.listRelatives(m, s=True, type="mesh", ni=True)]
    if not meshes and cmds.objExists("geo"):
        meshes = [cmds.listRelatives(s, p=True, f=True)[0] for s in
                  cmds.listRelatives("geo", ad=True, type="mesh", f=True)
                  or [] if not cmds.getAttr(s + ".intermediateObject")]
    if not meshes:
        raise RuntimeError("Select the character's meshes (or put them in a "
                           "'geo' group).")
    report = {}
    head = HEAD
    eye_j = {s: "%s_eye_BIND_JNT" % s for s in ("L", "R")}
    up_teeth = "C_upperTeeth_BIND_JNT" if cmds.objExists(
        "C_upperTeeth_BIND_JNT") else head
    lo_teeth = "C_lowerTeeth_BIND_JNT" if cmds.objExists(
        "C_lowerTeeth_BIND_JNT") else (JAW if cmds.objExists(JAW) else head)
    tongue = _row("C_tongue_*_BIND_JNT")
    kinds = {m: classify(m, face) for m in meshes}
    # the body / head first: separate brows copy their skin afterwards
    order = sorted(meshes, key=lambda m: kinds[m] not in ("body", "head"))
    skin_source = None
    for mesh in order:
        kind = kinds[mesh]
        short = mesh.split("|")[-1]
        if kind in ("body", "head"):
            note = ""
            if _needs_body_skin(mesh):
                joints = body_joints() if kind == "body" else [
                    j for j in body_joints()
                    if re.match(r"C_(neck|head)", j)] or [head]
                if head not in joints:
                    joints.append(head)
                sc, how = _new_skin(mesh, joints, geodesic)
                note = "body skin (%s, %d joints) + " % (how, len(joints))
            n = face_layer(mesh, face)
            report[short] = "%sface layer on %d verts" % (note, n)
            try:
                laps = fit_lid_overlap(mesh, face)
                if any(laps.values()):
                    report[short] += ", lids close %s past each other" % \
                        ", ".join("%s %.2f" % kv for kv in sorted(laps.items()))
            except RuntimeError:
                pass
            skin_source = skin_source or mesh
        elif kind == "eye":
            c = om.MVector([sum(v) / 2.0 for v in zip(
                _bbox(mesh)[:3], _bbox(mesh)[3:])])
            side = "L" if (c - _wp(eye_j["L"])).length() < \
                (c - _wp(eye_j["R"])).length() else "R"
            _rigid(mesh, lambda p, j=eye_j[side]: {j: 1.0}, [eye_j[side]])
            report[short] = "eyeball: 100 % " + eye_j[side]
        elif kind == "lashes":
            def lash(p):
                lp = face.local(p)
                side = "L" if lp.x >= 0 else "R"
                e = face.eyes.get(side)
                if not e:
                    return {head: 1.0}
                row = e["up"] if lp.y >= face.lid_seam(side, lp.x) \
                    else e["lo"]
                return face._row_weights(row, lp)[1]
            joints = [j for e in face.eyes.values() for r in ("up", "lo")
                      for j, _ in e[r]]
            _rigid(mesh, lash, joints)
            report[short] = "lashes: ride the lash rows"
        elif kind == "brows" and skin_source:
            _copy_skin(skin_source, mesh)
            report[short] = "brows: copied from %s's skin (move with the " \
                "forehead)" % skin_source.split("|")[-1]
        elif kind == "brows":
            brows = [(j, pt) for j, pt, _, _ in face.points if "_brow" in j]

            def brow(p):
                lp = face.local(p)
                side = [(j, pt) for j, pt in brows
                        if (pt.x >= 0) == (lp.x >= 0)] or brows
                if not side:
                    return {head: 1.0}
                ds = sorted(((lp - pt).length(), j) for j, pt in side)[:2]
                w = {j: 1.0 / (d + 0.05 * face.S) ** 2 for d, j in ds}
                s = sum(w.values())
                return {j: v / s for j, v in w.items()}
            _rigid(mesh, brow, [j for j, _ in brows])
            report[short] = "brows: brow joints"
        elif kind in ("teeth_upper", "teeth_lower"):
            j = up_teeth if kind == "teeth_upper" else lo_teeth
            _rigid(mesh, lambda p, j=j: {j: 1.0}, [j])
            report[short] = "%s: 100 %% %s" % (kind.replace("_", " "), j)
        elif kind == "tongue" and tongue:
            pos = [(j, face.local(_wp(j))) for j in tongue]

            def tng(p):
                return face._row_weights(pos, face.local(p))[1]
            _rigid(mesh, tng, tongue)
            report[short] = "tongue: along the tongue chain"
        elif kind == "mouth":
            def inner(p):
                w = face.jaw_weight(face.local(p), gate_front=False)
                return {JAW: w, head: 1.0 - w} if cmds.objExists(JAW) \
                    else {head: 1.0}
            _rigid(mesh, inner, [JAW])
            report[short] = "inner mouth: head above the lips, jaw below"
        elif kind == "hair":
            _rigid(mesh, lambda p: {head: 1.0}, [head])
            report[short] = "hair: 100 % head"
        else:
            report[short] = "skipped (not a face part)"
    for k, v in report.items():
        print("[faceBind] %-16s %s" % (k, v))
    return report
