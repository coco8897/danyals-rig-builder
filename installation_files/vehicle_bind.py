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
   Rim      → {corner}_hub_BIND_JNT              (spins, rigid)
   Door     → {corner}_door_BIND_JNT            (hinge, rigid)
   Hood     → hood_BIND_JNT                       (rigid)
   Trunk    → trunk_BIND_JNT                      (rigid)
   Spring   → {corner}_spring_BIND_JNT           (rigid)

 Usage:
     import vehicle_bind
     from importlib import reload; reload(vehicle_bind)
     # select the LF tyre mesh, then:
     vehicle_bind.bind_part("Tire")     # auto-detects it's the LF tyre
===============================================================================
"""

import maya.cmds as cmds


WHEEL_CORNERS = ("LF", "RF", "LB", "RB")

# Part type -> how to resolve the joints to bind to.
#   "anchor"   = a representative joint used to pick the nearest instance
#   "rigid"    = bind to a single joint at weight 1
#   "tire"     = bind to the hub + all spokes of the matched corner
PART_TYPES = (
    "Body", "Tire", "Rim", "Door", "Hood", "Trunk", "Spring",
)


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


def _nearest_corner(mesh_center, anchor_pattern):
    """Return the wheel corner (LF/RF/LB/RB) whose anchor joint is
    closest to the mesh center. anchor_pattern uses {c} for the corner,
    e.g. '{c}_hub_BIND_JNT'."""
    best_c, best_d = None, 1e18
    for c in WHEEL_CORNERS:
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
        c = _nearest_corner(center, "{c}_hub_BIND_JNT")
        if not c:
            return [], "No wheel hubs found — build a vehicle first."
        if part_type == "Rim":
            return [f"{c}_hub_BIND_JNT"], f"{c} Rim"
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
        mi=(4 if part_type == "Tire" else 1),   # tyre blends across spokes
        dr=4.0, n=f"{mesh}_skinCluster",
    )[0]

    print(f"[vehicleBind] Bound '{mesh}' to {label} "
          f"({len(joints)} joint(s)). skinCluster: {sc}")
    return sc
