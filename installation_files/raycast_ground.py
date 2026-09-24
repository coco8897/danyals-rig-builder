"""
===============================================================================
 RAYCAST GROUND — true straight-down ground sampling for the vehicle
===============================================================================

 closestPointOnMesh (used for the live terrain follow) returns the point
 on the mesh CLOSEST IN 3D — not the ground directly below the wheel. Near
 an obstacle, the obstacle's flank becomes closer than the flat ground
 under the wheel, so the wheel "snaps" up onto the side of the bump BEFORE
 it's actually over it — and choppily, as the closest face jumps around.

 This module samples the ground with a real DOWNWARD RAYCAST: from high
 above each footprint point, straight down, take the first hit. That's the
 ground genuinely beneath the wheel — no premature lift, no chop.

 Raycasts need MFnMesh (can't be a live node connection), so the drive
 tool calls sample_footprints() every frame and writes the result into
 each footprint's ground-source node, then keyframes the suspension.
 For a NON-driven (static / hand-keyed) setup, bake_over_range() bakes
 the raycast result across a frame range.

 Wiring assumption (matches vehicle_rig_builder):
   Each footprint has:
     {prefix}_foot{Tag}_GRP        — world position to sample under
     {prefix}_foot{Tag}Src_ADL     — .input1 = ground-Y feeding the susp.
===============================================================================
"""

import maya.cmds as cmds
import maya.api.OpenMaya as om2


WHEEL_PREFIXES = ("LF", "RF", "LB", "RB")   # classic car; see _prefixes()


def _prefixes(prefixes=None):
    """The wheels to sample: the ones given, else every wheel on the rig
    (6 / 8-wheelers and tanks included)."""
    if prefixes:
        return prefixes
    import vehicle_rig_builder
    return vehicle_rig_builder.all_wheel_prefixes() or WHEEL_PREFIXES
FOOT_TAGS = ("Front", "Center", "Back")
RAY_HEIGHT = 100000.0   # start the ray well above any plausible terrain


def _mesh_fn(mesh):
    """Return an MFnMesh for a mesh transform or shape, or None."""
    if not cmds.objExists(mesh):
        return None
    shapes = (mesh if cmds.objectType(mesh) == "mesh"
              else (cmds.listRelatives(mesh, s=True, ni=True,
                                       type="mesh") or [None])[0])
    if not shapes:
        return None
    sel = om2.MSelectionList()
    sel.add(shapes)
    return om2.MFnMesh(sel.getDagPath(0))


def clear_channel_keys(plug):
    """Remove the animCurve(s) on a plug WITHOUT deleting the host node.

    Two Maya quirks make this fiddly, both of which delete the host utility
    node (e.g. the footprint addDoubleLinear) and silently break terrain
    following:
      * cmds.cutKey(plug, clear=True) deletes the node along with the keys.
      * cmds.delete(animCurve) while it's still connected cascades to the
        node it drives.
    So we DISCONNECT each animCurve first, then delete it. That leaves the
    host node intact with a free (re-connectable) input plug.

    Returns the number of animCurves removed.
    """
    nodes = cmds.listConnections(plug, s=True, d=False,
                                 type="animCurve") or []
    if not nodes:
        return 0
    srcs = cmds.listConnections(plug, s=True, d=False, plugs=True,
                                type="animCurve") or []
    for sp in srcs:
        try:
            cmds.disconnectAttr(sp, plug)
        except Exception:
            pass
    for ac in nodes:
        if cmds.objExists(ac):
            cmds.delete(ac)
    return len(nodes)


def first_hit(mesh_fn, origin, direction, max_dist, accel=None):
    """Nearest hit of a ray on a mesh: (distance, MFloatPoint, face) or None.

    Uses allIntersections, not closestIntersection. In Maya 2023
    closestIntersection with an acceleration grid misses about half of the
    rays that start above a terrain, and without one it can return a
    farther face than the nearest on bumpy meshes (and is very slow on
    dense ones). allIntersections with the grid is exact and fast."""
    if accel is None:
        accel = mesh_fn.autoUniformGridParams()
    hits = mesh_fn.allIntersections(
        om2.MFloatPoint(*origin), om2.MFloatVector(*direction),
        om2.MSpace.kWorld, float(max_dist), False,
        accelParams=accel, sortHits=True)
    if not hits or not len(hits[1]):
        return None
    return float(hits[1][0]), hits[0][0], int(hits[2][0])


def raycast_down(mesh_fn, x, z, default=0.0, accel=None):
    """First downward hit Y at world (x, z), or `default` if the ray
    misses the mesh entirely (off the edge of the terrain)."""
    hit = first_hit(mesh_fn, (x, RAY_HEIGHT, z), (0.0, -1.0, 0.0),
                    2.0 * RAY_HEIGHT, accel)
    return default if hit is None else hit[1].y


def _footprint_nodes(prefix, tag):
    grp = f"{prefix}_foot{tag}_GRP"
    src = f"{prefix}_foot{tag}Src_ADL"
    if cmds.objExists(grp) and cmds.objExists(src):
        return grp, src
    return None, None


def prepare_for_raycast(prefixes=None):
    """Disconnect anything driving the footprint ground sources (the
    closestPointOnMesh nodes from assign_ground_mesh) so the raycast can
    set the values directly. Idempotent. Returns disconnected count.

    Call once before sampling (the drive loop does this on start). The
    CPOM nodes are left in the scene; clear_ground_mesh / assign re-wire
    them, so flipping back to the node-based follow still works.
    """
    n = 0
    for prefix in _prefixes(prefixes):
        for tag in FOOT_TAGS:
            _grp, src = _footprint_nodes(prefix, tag)
            if not src:
                continue
            plug = f"{src}.input1"
            for c in (cmds.listConnections(plug, s=True, d=False,
                                            plugs=True) or []):
                cmds.disconnectAttr(c, plug)
                n += 1
    return n


def sample_footprints(mesh_fn, prefixes=None,
                      default_ground=0.0, disconnect=False, out=None):
    """Raycast every footprint straight down through `mesh_fn` and write
    the hit Y into that footprint's ground-source node. Call once per
    frame from the drive loop (after the car has moved that frame).

    disconnect: if True, break a CPOM/animCurve feeding input1 before
        setting it. The drive loop / bake call prepare_for_raycast()
        ONCE up front instead (passing disconnect=False here), so a
        keyframed input1 keeps its single animCurve across frames
        instead of being severed every frame.
    out: optional dict, filled with {(prefix, tag): ground Y}.

    Returns the number of footprints sampled.
    """
    if mesh_fn is None:
        return 0
    n = 0
    accel = mesh_fn.autoUniformGridParams()
    for prefix in _prefixes(prefixes):
        for tag in FOOT_TAGS:
            grp, src = _footprint_nodes(prefix, tag)
            if not grp:
                continue
            plug = f"{src}.input1"
            if disconnect:
                for c in (cmds.listConnections(plug, s=True, d=False,
                                                plugs=True) or []):
                    if cmds.nodeType(c.split(".")[0]) != "animCurveTU":
                        cmds.disconnectAttr(c, plug)
            x, _y, z = cmds.xform(grp, q=True, ws=True, t=True)
            gy = raycast_down(mesh_fn, x, z, default=default_ground,
                              accel=accel)
            cmds.setAttr(plug, gy)
            if out is not None:
                out[(prefix, tag)] = gy
            n += 1
    return n


def has_footprints():
    """True if the vehicle has the raycast-able footprint groups. Asks the
    rig which wheels it has, so a motorcycle (CF / CB) counts as much as a
    car's front-left corner."""
    return any(cmds.objExists("%s_footCenter_GRP" % p)
               for p in _prefixes())


# =============================================================================
# Static bake (for hand-keyed / non-driven use)
# =============================================================================

def bake_over_range(mesh, start=None, end=None,
                    prefixes=None):
    """Bake the downward-raycast ground follow onto the suspension across
    a frame range — for animators who keyframe the car by hand rather
    than live-driving. Keys each footprint's ground-source node so the
    suspension reacts to the terrain straight below each wheel.

    Returns the number of frames baked.
    """
    mesh_fn = _mesh_fn(mesh)
    if mesh_fn is None:
        cmds.warning(f"[raycast] '{mesh}' has no mesh shape.")
        return 0
    if start is None:
        start = cmds.playbackOptions(q=True, min=True)
    if end is None:
        end = cmds.playbackOptions(q=True, max=True)
    start, end = int(start), int(end)

    prefixes = _prefixes(prefixes)
    prepare_for_raycast(prefixes)
    srcs = []
    for prefix in _prefixes(prefixes):
        for tag in FOOT_TAGS:
            _grp, src = _footprint_nodes(prefix, tag)
            if src:
                srcs.append(f"{src}.input1")

    cmds.undoInfo(openChunk=True)
    try:
        for f in range(start, end + 1):
            cmds.currentTime(f, edit=True)
            sample_footprints(mesh_fn, prefixes)
            cmds.setKeyframe(srcs)
    finally:
        cmds.undoInfo(closeChunk=True)
    print(f"[raycast] Baked downward-raycast ground follow "
          f"{start}-{end} ({len(srcs)} footprint channels).")
    return end - start + 1


def clear_bake(prefixes=None, reconnect_flat=True):
    """Remove raycast bake keys from the footprint sources, then leave each
    source connected to the flat ground locator (or a static 0) so the
    suspension network stays intact and the ground is still detectable.

    Uses clear_channel_keys (deletes the animCurve) rather than cutKey,
    because cutKey(clear=True) would delete the footprint addDoubleLinear
    node itself and break terrain following.

    Returns the number of footprint channels cleared.
    """
    flat_y = None
    if reconnect_flat:
        dec = "C_ground_LOC_decompose"
        if cmds.objExists(dec):
            flat_y = f"{dec}.outputTranslateY"
    n = 0
    for prefix in _prefixes(prefixes):
        for tag in FOOT_TAGS:
            _grp, src = _footprint_nodes(prefix, tag)
            if not src:
                continue
            plug = f"{src}.input1"
            if clear_channel_keys(plug) > 0:
                n += 1
            # Keep the node alive + meaningful: reconnect the flat ground
            # (so the suspension reads Y=0) instead of leaving it dangling.
            if cmds.objExists(src):
                if flat_y:
                    try:
                        cmds.connectAttr(flat_y, plug, f=True)
                    except Exception:
                        cmds.setAttr(plug, 0.0)
                else:
                    cmds.setAttr(plug, 0.0)
    print(f"[raycast] Cleared raycast bake on {n} footprint channels.")
    return n
