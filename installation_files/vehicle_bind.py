"""
===============================================================================
 VEHICLE BIND — one-click skinning of car parts to their bones
===============================================================================

 Makes binding a car model painless: select the mesh, pick the part type,
 hit bind. The tool figures out WHICH instance (LF / RF / LB / RB, or the
 single body / hood / trunk) by finding the joint nearest the mesh, then
 skins the mesh to the right bone(s).

   Body     → C_chassis_BIND_JNT                 (rigid, weight 1)
   Tire     → {corner}_hub + all spoke joints    (so air-pressure +
              contact deformation actually move the rubber)
   Rim      → {corner}_hub_BIND_JNT              (spins, rigid). A mesh
              holding several wheels (both sides in one mesh, or all of
              them) binds each piece to its own nearest hub.
   Door     → {corner}_door_BIND_JNT            (hinge, rigid)
   Hood     → hood_BIND_JNT                       (rigid)
   Trunk    → trunk_BIND_JNT                      (rigid)
   Spring   → {corner}_spring_BIND_JNT           (rigid)
   Leaf Spring → {corner}_leaf_01..07_BIND_JNT   (smooth: it bends)
   Shackle  → {corner}_shackle_BIND_JNT          (rigid, swings)
   Shock Body / Shock Rod → {corner}_shockBody / shockRod_BIND_JNT
   Axle     → {axle}_axle_BIND_JNT, a solid axle's beam (rigid)
   Tread    → the tread link joints of a tank: a one-piece modelled tread
              (either side, or both in one mesh) flows round the wheels
   (Rim also finds a tank's rollers and gears.)
   Trailer  → T{n}_body_BIND_JNT of the nearest trailer (rigid)
   Hitch    → T{n}_hitch_BIND_JNT, a tow ball / drawbar on the vehicle
   (Tire / Rim / Spring find trailer wheels too.)

 Usage:
     import vehicle_bind
     from importlib import reload; reload(vehicle_bind)
     # select the LF tyre mesh, then:
     vehicle_bind.bind_part("Tire")     # auto-detects it's the LF tyre
===============================================================================
"""

import maya.cmds as cmds


WHEEL_CORNERS = ("LF", "RF", "LB", "RB")


def _corners():
    """Wheel corners on the rig in the scene (6 / 8-wheelers included)."""
    try:
        import vehicle_rig_builder
        return vehicle_rig_builder.all_wheel_prefixes() or WHEEL_CORNERS
    except Exception:
        return WHEEL_CORNERS

# Part type -> how to resolve the joints to bind to.
#   "anchor"   = a representative joint used to pick the nearest instance
#   "rigid"    = bind to a single joint at weight 1
#   "tire"     = bind to the hub + all spokes of the matched corner
PART_TYPES = (
    "Body", "Tire", "Rim", "Door", "Hood", "Trunk", "Spring", "Trailer",
    "Hitch", "Leaf Spring", "Shackle", "Shock Body", "Shock Rod", "Axle",
    "Tread", "Fork", "Handlebar", "Swingarm",
)
# Motorcycle parts -> the one joint each rides. The frame binds as Body.
_BIKE_PARTS = {
    "Handlebar": "C_handlebar_BIND_JNT",
    "Swingarm": "C_swingarm_BIND_JNT",
}
# Leaf spring / shock parts -> the joint of the nearest corner.
_CORNER_PARTS = {
    "Shackle": "{c}_shackle_BIND_JNT",
    "Shock Body": "{c}_shockBody_BIND_JNT",
    "Shock Rod": "{c}_shockRod_BIND_JNT",
}


def _hub_prefixes():
    """Every wheel with a hub joint: the vehicle's, the trailers', and a
    tank's rollers and gears."""
    try:
        import vehicle_rig_builder
        return (vehicle_rig_builder.all_wheel_prefixes()
                + vehicle_rig_builder.hull_wheel_prefixes())
    except Exception:
        return list(WHEEL_CORNERS)


def _shells(mesh):
    """The mesh's connected pieces: [(vertex ids, world centre)]."""
    import maya.api.OpenMaya as om2
    shape = (cmds.listRelatives(mesh, s=True, ni=True, f=True) or [mesh])[0]
    sel = om2.MSelectionList()
    sel.add(shape)
    fn = om2.MFnMesh(sel.getDagPath(0))
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
        groups.setdefault(root(v), []).append(v)
    out = []
    for verts in groups.values():
        lo = [min(pts[v][k] for v in verts) for k in range(3)]
        hi = [max(pts[v][k] for v in verts) for k in range(3)]
        out.append((verts, tuple(0.5 * (a + b) for a, b in zip(lo, hi))))
    return out, pts


def _set_weights(mesh, sc, joints, weight_of):
    """Write skin weights in one go: weight_of(vertex) -> {joint: w}."""
    import maya.api.OpenMaya as om2
    import maya.api.OpenMayaAnim as oma2
    sel = om2.MSelectionList()
    sel.add(sc)
    fn = oma2.MFnSkinCluster(sel.getDependNode(0))
    shape = (cmds.listRelatives(mesh, s=True, ni=True, f=True) or [mesh])[0]
    path = om2.MSelectionList().add(shape).getDagPath(0)
    order = [p.partialPathName() for p in fn.influenceObjects()]
    index = {j: k for k, j in enumerate(order)}
    n = om2.MFnMesh(path).numVertices
    flat = om2.MDoubleArray(n * len(order), 0.0)
    for v in range(n):
        for j, w in weight_of(v).items():
            flat[v * len(order) + index[j]] = w
    comp = om2.MFnSingleIndexedComponent()
    cobj = comp.create(om2.MFn.kMeshVertComponent)
    comp.setCompleteData(n)
    fn.setWeights(path, cobj, om2.MIntArray(list(range(len(order)))), flat,
                  normalize=False)


def _rim_pieces(mesh):
    """Each piece of a rim mesh -> the hub joint nearest it:
    ({vertex: hub}, [hubs]). One hub for an ordinary single wheel."""
    hubs = [p + "_hub_BIND_JNT" for p in _hub_prefixes()
            if cmds.objExists(p + "_hub_BIND_JNT")]
    if not hubs:
        return {}, []
    at = {h: _jpos(h) for h in hubs}
    shells, _pts = _shells(mesh)
    owner = {}
    for verts, centre in shells:
        hub = min(hubs, key=lambda h: _dist(centre, at[h]))
        for v in verts:
            owner[v] = hub
    return owner, sorted(set(owner.values()))


def _tread_weights(mesh):
    """Each vertex of a modelled tread -> its two nearest tread link joints
    on its own side (inverse-distance blend): ({vertex: {joint: w}},
    joints). Measured where the links are now (bind at rest)."""
    links = {s: sorted(cmds.ls("%s_tread_*_BIND_JNT" % s, type="joint")
                       or []) for s in ("L", "R")}
    if not (links["L"] or links["R"]):
        return {}, []
    at = {j: _jpos(j) for s in links for j in links[s]}
    _shells_out, pts = _shells(mesh)
    weights = {}
    used = set()
    for v, p in enumerate(pts):
        side = "L" if p[0] >= 0 else "R"
        pool = links[side] or links["L" if side == "R" else "R"]
        near = sorted(pool, key=lambda j: _dist(p, at[j]))[:2]
        d = [max(1e-6, _dist(p, at[j])) for j in near]
        inv = [1.0 / (x * x) for x in d]
        total = sum(inv)
        weights[v] = {j: w / total for j, w in zip(near, inv)}
        used.update(near)
    return weights, sorted(used)


def _mesh_center(mesh):
    """World-space bounding-box center of a mesh transform."""
    bb = cmds.exactWorldBoundingBox(mesh)
    return ((bb[0] + bb[3]) * 0.5,
            (bb[1] + bb[4]) * 0.5,
            (bb[2] + bb[5]) * 0.5)


def _jpos(joint):
    return cmds.xform(joint, q=True, ws=True, t=True)


def _dist(a, b):
    return sum((x - y) ** 2 for x, y in zip(a, b)) ** 0.5


def _nearest_corner(mesh_center, anchor_pattern, corners=None):
    """Return the wheel corner (LF/RF/LB/RB) whose anchor joint is
    closest to the mesh center. anchor_pattern uses {c} for the corner,
    e.g. '{c}_hub_BIND_JNT'."""
    best_c, best_d = None, 1e18
    for c in (corners or _corners()):
        jnt = anchor_pattern.format(c=c)
        if not cmds.objExists(jnt):
            continue
        d = _dist(mesh_center, _jpos(jnt))
        if d < best_d:
            best_d, best_c = d, c
    return best_c


def resolve_joints(mesh, part_type):
    """Return the list of joints to bind `mesh` to for `part_type`, with
    the matched corner auto-detected. Returns (joints, label) or
    ([], reason)."""
    if not cmds.objExists(mesh):
        return [], f"'{mesh}' does not exist."
    center = _mesh_center(mesh)

    if part_type == "Body":
        j = "C_chassis_BIND_JNT"
        return ([j], "Body") if cmds.objExists(j) else \
            ([], "No C_chassis_BIND_JNT — build a vehicle first.")

    if part_type == "Hood":
        j = "hood_BIND_JNT"
        return ([j], "Hood") if cmds.objExists(j) else \
            ([], "No hood_BIND_JNT.")

    if part_type == "Trunk":
        j = "trunk_BIND_JNT"
        return ([j], "Trunk") if cmds.objExists(j) else \
            ([], "No trunk_BIND_JNT.")

    if part_type in ("Tire", "Rim"):
        c = _nearest_corner(center, "{c}_hub_BIND_JNT", _hub_prefixes())
        if not c:
            return [], "No wheel hubs found — build a vehicle first."
        if part_type == "Rim":
            owner, hubs = _rim_pieces(mesh)
            if len(hubs) > 1:
                return hubs, f"Rim ({len(hubs)} wheels, one per piece)"
            return [f"{c}_hub_BIND_JNT"], f"{c} Rim"
        if not cmds.objExists(f"{c}_spoke_01_inner_BIND_JNT"):
            return [f"{c}_hub_BIND_JNT"], f"{c} Tire (a roller: rigid)"
        # Tire: hub + every spoke joint of that corner.
        jnts = [f"{c}_hub_BIND_JNT"]
        jnts += sorted(cmds.ls(f"{c}_spoke_*_BIND_JNT") or [])
        return jnts, f"{c} Tire ({len(jnts)} joints)"

    if part_type == "Door":
        c = _nearest_corner(center, "{c}_door_BIND_JNT")
        if not c:
            return [], "No door joints found."
        return [f"{c}_door_BIND_JNT"], f"{c} Door"

    if part_type == "Spring":
        c = _nearest_corner(center, "{c}_spring_BIND_JNT")
        if not c:
            return [], "No spring joints found."
        return [f"{c}_spring_BIND_JNT"], f"{c} Spring"

    if part_type == "Leaf Spring":
        c = _nearest_corner(center, "{c}_leaf_04_BIND_JNT")
        if not c:
            return [], ("No leaf spring joints: set an axle's spring to "
                        "Leaf spring and rebuild.")
        jnts = sorted(cmds.ls(f"{c}_leaf_*_BIND_JNT", type="joint") or [])
        return jnts, f"{c} Leaf Spring ({len(jnts)} joints)"

    if part_type in _CORNER_PARTS:
        c = _nearest_corner(center, _CORNER_PARTS[part_type])
        if not c:
            return [], (f"No {part_type.lower()} joints: set an axle's "
                        f"spring to Leaf spring and rebuild.")
        return [_CORNER_PARTS[part_type].format(c=c)], f"{c} {part_type}"

    if part_type == "Axle":
        joints = cmds.ls("*_axle_BIND_JNT", type="joint") or []
        if not joints:
            return [], ("No solid axles: tick Solid axle on an axle and "
                        "rebuild.")
        j = min(joints, key=lambda n: _dist(center, _jpos(n)))
        return [j], f"{j.split('_')[0]} Axle"

    if part_type == "Tread":
        _w, joints = _tread_weights(mesh)
        if not joints:
            return [], ("No tread links: build a tracked vehicle first.")
        return joints, f"Tread ({len(joints)} link joints)"

    if part_type == "Fork":
        jnts = [j for j in ("C_fork_BIND_JNT", "C_forkLower_BIND_JNT")
                if cmds.objExists(j)]
        if not jnts:
            return [], "No fork joints: build a motorcycle rig first."
        # One joint per vertex, so the lower tube slides inside the upper
        # instead of stretching between them.
        return jnts, "Fork (upper + lower tubes)"

    if part_type in _BIKE_PARTS:
        j = _BIKE_PARTS[part_type]
        if not cmds.objExists(j):
            return [], (f"No {j}: build a motorcycle rig first.")
        return [j], part_type

    if part_type in ("Trailer", "Hitch"):
        pattern = ("T*_body_BIND_JNT" if part_type == "Trailer"
                   else "T*_hitch_BIND_JNT")
        joints = cmds.ls(pattern, type="joint") or []
        if not joints:
            return [], "No trailers found: build trailers first."
        j = min(joints, key=lambda n: _dist(center, _jpos(n)))
        return [j], f"{j.split('_')[0]} {part_type}"

    return [], f"Unknown part type {part_type!r}."


def bind_part(part_type, mesh=None):
    """Skin the selected (or given) mesh to the resolved part joints.

    Returns the skinCluster name on success, or None.
    """
    if mesh is None:
        sel = cmds.ls(sl=True, type="transform") or []
        meshes = [s for s in sel
                  if cmds.listRelatives(s, s=True, type="mesh", ni=True)]
        if not meshes:
            cmds.warning("[vehicleBind] Select a polygon mesh first.")
            return None
        mesh = meshes[0]

    if part_type == "Tread":
        # Bind where the links are at rest (no drive, no suspension).
        import rig_export
        with rig_export._rest_pose():
            odo = "C_chassis_CTRL.odometer"
            held = None
            if cmds.objExists("C_chassis_CTRL") and cmds.attributeQuery(
                    "odometer", node="C_chassis_CTRL", exists=True):
                held = cmds.getAttr(odo)
                cmds.setAttr(odo, 0.0)
            try:
                return _bind_part(part_type, mesh)
            finally:
                if held is not None and not cmds.keyframe(
                        odo, q=True, keyframeCount=True):
                    cmds.setAttr(odo, held)
    return _bind_part(part_type, mesh)


def _bind_part(part_type, mesh):
    joints, label = resolve_joints(mesh, part_type)
    if not joints:
        cmds.warning(f"[vehicleBind] {label}")
        return None

    # Remove any existing skinCluster so re-binding is clean.
    existing = [h for h in (cmds.listHistory(mesh) or [])
                if cmds.nodeType(h) == "skinCluster"]
    if existing:
        try:
            cmds.skinCluster(existing[0], e=True, ub=True)
        except Exception:
            pass

    sc = cmds.skinCluster(
        joints, mesh, tsb=True,
        # A tyre blends across spokes, a leaf along its length.
        mi=(4 if part_type == "Tire" else 2 if part_type in (
            "Leaf Spring", "Tread") else 1),
        dr=4.0, n=f"{mesh}_skinCluster",
    )[0]

    if part_type == "Tread":
        weights, _j = _tread_weights(mesh)
        _set_weights(mesh, sc, joints, lambda v: weights[v])
    elif part_type == "Rim" and len(joints) > 1:
        owner, _h = _rim_pieces(mesh)
        _set_weights(mesh, sc, joints, lambda v: {owner[v]: 1.0})

    print(f"[vehicleBind] Bound '{mesh}' to {label} "
          f"({len(joints)} joint(s)). skinCluster: {sc}")
    return sc
