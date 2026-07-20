"""
===============================================================================
 RIG EXPORT — game-skeleton helpers + FBX export for Unreal / Unity
===============================================================================

 Three entry points:

   organize_for_game_export()
       Reparents every BIND joint chain that is currently a top-level child
       of joints_GRP (e.g. C_pelvis_BIND_JNT, C_chest_BIND_JNT) so it sits
       UNDER C_root_BIND_JNT. Result: a single clean joint tree rooted at
       C_root_BIND_JNT that engines can ingest as one skeleton asset.

       Joints driven by follicles (the spine / arm / leg ribbon bendy
       joints) are LEFT IN PLACE — they need their follicle parent for the
       ribbon deformation to work. They can still be skinned to; for game
       export you typically pick whether to include them by checkbox.

       Visual rig keeps working after this because every BIND joint is
       driven by a constraint on the joint itself, not by parent-space
       inheritance. Reparenting BIND joints does not break the rig.

   export_rig_fbx(filepath, include_mesh=True, include_bendy=False)
       Selects the BIND skeleton starting at C_root_BIND_JNT (+ optionally
       the geo group) and exports an FBX with skinning info. No animation.
       Use this to push the asset into Unreal / Unity for the first time.

   export_animation_fbx(filepath, start=None, end=None,
                         include_bendy=False)
       Bakes every BIND joint's animation over the time range, selects the
       BIND skeleton, and exports an FBX with animation only. Use this to
       export takes / cycles into the engine.

 All three functions are rig-type-agnostic — they work the same on the
 biped CharacterRig, the QuadrupedRig, or the BirdRig as long as the
 standard CoreRig built `C_root_BIND_JNT` as the root.
===============================================================================
"""

import maya.cmds as cmds
import maya.mel as mel


ROOT_BIND = "C_root_BIND_JNT"


# =============================================================================
# Helpers
# =============================================================================

def _all_bind_joints():
    """Return every node whose name ends in '_BIND_JNT'."""
    return [j for j in (cmds.ls(type="joint") or [])
             if j.endswith("_BIND_JNT")]


def _is_bendy(jnt):
    """Bendy / ribbon joints live under follicles and shouldn't be moved
    in the skeleton hierarchy — moving them out of the follicle breaks
    the ribbon drive."""
    if "_bendy_" in jnt:
        return True
    parent = cmds.listRelatives(jnt, p=True) or []
    if parent and cmds.objectType(parent[0]) == "transform":
        # Follicles are transforms with a follicle-shape child.
        shapes = cmds.listRelatives(parent[0], s=True) or []
        for s in shapes:
            if cmds.objectType(s) == "follicle":
                return True
    return False


# Node types that output WORLD-space positions into a joint's translate.
# A joint fed by one of these has local coordinates that are really world
# coordinates, so reparenting it under a normal (inheriting) parent
# re-multiplies them by the new parent matrix and the joint flies away.
_WORLD_DRIVER_TYPES = (
    "pointOnCurveInfo", "motionPath",
    "pointOnSurfaceInfo", "closestPointOnSurface",
    "follicle",
)


def _is_world_space_driven(jnt):
    """True if 'jnt' must NOT be reparented for game export because its
    local transform is really a world-space value.

    Two signals, either is sufficient:
      1. Its immediate parent has inheritsTransform = 0 (the explicit
         "treat my children's local coords as world coords" flag — used
         by the heavy-face detail groups and ribbon follicle groups).
      2. Its translate (or tx/ty/tz) is fed by a world-space node such
         as pointOnCurveInfo (the heavy-face eyelid / lip detail joints).

    This is what stops 'Make Game Skeleton' from yanking the curve-driven
    heavy-face joints up off the root — the green-spike bug.
    """
    # 1. Parent group with inheritsTransform = 0.
    parents = cmds.listRelatives(jnt, p=True) or []
    if parents:
        par = parents[0]
        if cmds.attributeQuery("inheritsTransform", node=par,
                                exists=True):
            try:
                if cmds.getAttr(f"{par}.inheritsTransform") == 0:
                    return True
            except Exception:
                pass

    # 2. translate driven by a world-space node.
    for plug in (f"{jnt}.translate", f"{jnt}.translateX",
                 f"{jnt}.translateY", f"{jnt}.translateZ"):
        srcs = cmds.listConnections(plug, s=True, d=False) or []
        for s in srcs:
            try:
                if cmds.objectType(s) in _WORLD_DRIVER_TYPES:
                    return True
            except Exception:
                pass
    return False


def _exportable_bind_joints(include_bendy=False):
    """Filter BIND joints for game export.

    Bendy joints are excluded by default — they're driven by follicles
    and there are too many of them for typical real-time rigs anyway.
    The toggle exists for users who want the deformation detail in
    high-end cinematic exports.
    """
    out = []
    for j in _all_bind_joints():
        if not include_bendy and _is_bendy(j):
            continue
        out.append(j)
    return out


def _ensure_fbx_loaded():
    """Make sure the FBX plugin is loaded before we touch its commands."""
    if not cmds.pluginInfo("fbxmaya", q=True, l=True):
        try:
            cmds.loadPlugin("fbxmaya")
        except RuntimeError as e:
            cmds.error(f"Could not load fbxmaya plugin: {e}")
            return False
    return True


# =============================================================================
# 1. Organize for game export
# =============================================================================

def face_joints_under_head(verbose=True):
    """Parent the advanced-face detail joints (the pointOnCurveInfo-driven
    lid / lip joints) into the deform hierarchy for the game skeleton, so the
    'single joint hierarchy' INCLUDES them and they export.

    They're world-space driven (their .translate is a world position from a
    pointOnCurveInfo), which is why a naive reparent shoots them off. So we
    insert a world->local conversion (pointMatrixMult against the deform
    target's worldInverseMatrix) and parent each under its deform target —
    the head for lids / upper lip, the jaw for the lower lip (read from each
    joint's existing orientConstraint). The joint then still rides the curve
    AND sits under the head/jaw, so it exports cleanly. Non-destructive: the
    live face keeps working. Idempotent.

    Returns the number of face joints moved.
    """
    moved = 0
    faces = set()
    for pat in ("*_lid*_BIND_JNT", "*_lip*_BIND_JNT"):
        faces.update(cmds.ls(pat, type="joint") or [])
    for j in sorted(faces):
        # only the PCI-driven detail joints
        pci = None
        for s in (cmds.listConnections(f"{j}.translate", s=True, d=False)
                  or []):
            if cmds.objectType(s) == "pointOnCurveInfo":
                pci = s
                break
        if not pci:
            continue
        # deform target = the joint's orientConstraint target (head or jaw)
        tgt = None
        oc = cmds.listConnections(j, s=True, d=False, type="orientConstraint")
        if oc:
            tl = cmds.orientConstraint(oc[0], q=True, tl=True) or []
            tgt = tl[0] if tl else None
        if not tgt or not cmds.objExists(tgt):
            tgt = "C_head_BIND_JNT"
        if not cmds.objExists(tgt):
            continue
        if (cmds.listRelatives(j, p=True) or [None])[0] == tgt:
            continue                                # already converted
        # world (PCI) -> target-local
        pmm = cmds.createNode("pointMatrixMult", n=f"{j}_toLocal_PMM")
        cmds.connectAttr(f"{pci}.position", f"{pmm}.inPoint")
        cmds.connectAttr(f"{tgt}.worldInverseMatrix[0]", f"{pmm}.inMatrix")
        cmds.disconnectAttr(f"{pci}.position", f"{j}.translate")
        # drop the orient/scale constraints — the joint now INHERITS the
        # target's rotation + scale by being its child.
        for ctyp in ("orientConstraint", "scaleConstraint"):
            for c in (cmds.listConnections(j, s=True, d=False, type=ctyp)
                      or []):
                if cmds.objExists(c):
                    cmds.delete(c)
        cmds.parent(j, tgt)
        cmds.connectAttr(f"{pmm}.output", f"{j}.translate", f=True)
        for ax in "XYZ":
            cmds.setAttr(f"{j}.rotate{ax}", 0)
            cmds.setAttr(f"{j}.jointOrient{ax}", 0)
            cmds.setAttr(f"{j}.scale{ax}", 1)
        moved += 1
    if verbose and moved:
        print(f"[rig_export] Parented {moved} advanced-face joint(s) under "
              f"the head / jaw for the game skeleton (world->local converted; "
              f"the live face still works).")
    return moved


def organize_for_game_export(verbose=True, face_under_head=True):
    """Reparent loose BIND chains under C_root_BIND_JNT.

    A BIND joint is considered "loose" if its immediate parent is not
    another BIND joint AND it isn't a follicle-driven bendy joint. For a
    fresh CharacterRig build that means C_pelvis_BIND_JNT and
    C_chest_BIND_JNT (both currently siblings of C_root_BIND_JNT under
    joints_GRP) get reparented under the root.

    Returns the number of joints reparented.
    """
    if not cmds.objExists(ROOT_BIND):
        cmds.warning(f"No {ROOT_BIND} found — build a rig first.")
        return 0

    # First fold the advanced-face joints into the head/jaw deform hierarchy
    # (world->local converted) so they ride in the single skeleton + export.
    if face_under_head:
        face_joints_under_head(verbose=verbose)

    reparented = 0
    skipped_world = 0
    for j in _all_bind_joints():
        if j == ROOT_BIND:
            continue
        if _is_bendy(j):
            # Stays under its follicle — don't move.
            continue
        # Curve-driven / world-space joints (heavy-face eyelid + lip
        # detail joints) must NOT be reparented — their translate holds
        # world coords interpreted under an inheritsTransform=0 group.
        # Reparenting them under the root re-multiplies those coords by
        # the root matrix and they shoot upward (the green-spike bug).
        if _is_world_space_driven(j):
            skipped_world += 1
            continue
        parents = cmds.listRelatives(j, p=True) or []
        parent = parents[0] if parents else None
        # If parent IS already a BIND joint we leave it alone — the
        # parent-child chain is already correct (arm under clavicle,
        # leg under hip, etc.). Only reparent the top-level orphans.
        if parent and parent.endswith("_BIND_JNT"):
            continue
        try:
            cmds.parent(j, ROOT_BIND)
            reparented += 1
        except RuntimeError:
            # Already a child, or cycle — skip silently.
            pass

    if verbose:
        print(f"[rig_export] Reparented {reparented} BIND joint(s) under "
              f"{ROOT_BIND}. Skeleton ready for game export.")
        if skipped_world:
            print(f"[rig_export] Left {skipped_world} curve-driven "
                  f"heavy-face joint(s) in place (they're world-space "
                  f"driven and would break if reparented). For a game "
                  f"engine these are typically replaced by blendshapes "
                  f"or baked separately.")
    return reparented


# =============================================================================
# 2. FBX export — rig (skeleton + skinning, no animation)
# =============================================================================

def export_rig_fbx(filepath, include_mesh=True, include_bendy=False,
                    organize_first=True):
    """Export the BIND skeleton (+ optionally the skinned mesh) to FBX.

    Args:
        filepath: where to save the .fbx file (absolute path).
        include_mesh: include the geometry group if found.
        include_bendy: include ribbon-bendy joints (off by default).
        organize_first: auto-run organize_for_game_export() so loose
            anchor chains end up under the root before exporting.

    Returns the filepath on success, None on failure.
    """
    if not cmds.objExists(ROOT_BIND):
        cmds.warning(f"No {ROOT_BIND} found — build a rig first.")
        return None
    if not _ensure_fbx_loaded():
        return None

    if organize_first:
        organize_for_game_export(verbose=False)

    # Build the selection: root + every exportable BIND joint + mesh.
    to_export = [ROOT_BIND]
    to_export += [j for j in _exportable_bind_joints(include_bendy)
                   if j != ROOT_BIND]
    # Mesh — use the same lookup logic as CoreRig._find_geo_group.
    geo_grp = None
    if include_mesh:
        for name in ("geo", "Geo", "GEO", "geometry",
                      "Geometry", "GEOMETRY", "meshes", "MESHES"):
            if cmds.objExists(name):
                geo_grp = name
                break
        if not geo_grp:
            # Any top-level transform with "geo" in its name.
            for top in (cmds.ls(assemblies=True) or []):
                if "geo" in top.lower():
                    geo_grp = top
                    break
        if geo_grp:
            to_export.append(geo_grp)

    cmds.select(to_export, r=True)

    # FBX options — push the static-rig flavor.
    mel.eval('FBXResetExport;')
    mel.eval('FBXExportSmoothingGroups -v true;')
    mel.eval('FBXExportInputConnections -v false;')
    mel.eval('FBXExportShapes -v true;')
    mel.eval('FBXExportSkins -v true;')
    mel.eval('FBXExportConstraints -v false;')
    mel.eval('FBXExportEmbeddedTextures -v false;')
    mel.eval('FBXExportAnimationOnly -v false;')
    mel.eval('FBXExportBakeComplexAnimation -v false;')
    mel.eval('FBXExportInAscii -v false;')
    mel.eval('FBXExportFileVersion -v "FBX201800";')

    # Export selected.
    safe_path = filepath.replace("\\", "/")
    try:
        cmds.file(safe_path, force=True, options="v=0;",
                   type="FBX export", pr=True, es=True)
    except RuntimeError as e:
        cmds.warning(f"FBX export failed: {e}")
        return None

    print(f"[rig_export] Rig exported to {filepath}")
    return filepath


# =============================================================================
# 3. FBX export — animation only (bake then export)
# =============================================================================

def export_animation_fbx(filepath, start=None, end=None,
                          include_bendy=False, organize_first=True,
                          bake_step=1.0):
    """Bake animation onto BIND joints and export as FBX.

    Args:
        filepath: where to save the .fbx file.
        start, end: time range; defaults to the playback range.
        include_bendy: include ribbon-bendy joints in the bake (off by
            default — large overhead and most engines don't want them).
        organize_first: run organize_for_game_export() first.
        bake_step: bake sample step in frames (1.0 = every frame).

    Returns the filepath on success, None on failure.
    """
    if not cmds.objExists(ROOT_BIND):
        cmds.warning(f"No {ROOT_BIND} found — build a rig first.")
        return None
    if not _ensure_fbx_loaded():
        return None

    if start is None:
        start = cmds.playbackOptions(q=True, min=True)
    if end is None:
        end = cmds.playbackOptions(q=True, max=True)

    if organize_first:
        organize_for_game_export(verbose=False)

    bind_joints = _exportable_bind_joints(include_bendy)
    if not bind_joints:
        cmds.warning("No BIND joints found to bake.")
        return None

    # Bake all attributes (translate, rotate, scale) onto BIND joints.
    # disableImplicitControl=True severs the constraint feed during bake
    # so the curves we just baked stay as the only animation source.
    cmds.bakeResults(
        bind_joints,
        simulation=True,
        time=(start, end),
        sampleBy=bake_step,
        oversamplingRate=1,
        disableImplicitControl=True,
        preserveOutsideKeys=True,
        sparseAnimCurveBake=False,
        removeBakedAttributeFromLayer=False,
        removeBakedAnimFromLayer=False,
        bakeOnOverrideLayer=False,
        minimizeRotation=True,
        controlPoints=False,
        shape=False,
    )

    cmds.select([ROOT_BIND] + bind_joints, r=True)

    mel.eval('FBXResetExport;')
    mel.eval('FBXExportSmoothingGroups -v true;')
    mel.eval('FBXExportInputConnections -v false;')
    mel.eval('FBXExportShapes -v false;')
    mel.eval('FBXExportSkins -v false;')
    mel.eval('FBXExportConstraints -v false;')
    mel.eval('FBXExportAnimationOnly -v true;')
    mel.eval('FBXExportBakeComplexAnimation -v true;')
    mel.eval(f'FBXExportBakeComplexStart -v {int(start)};')
    mel.eval(f'FBXExportBakeComplexEnd -v {int(end)};')
    mel.eval(f'FBXExportBakeComplexStep -v {int(round(bake_step))};')
    mel.eval('FBXExportInAscii -v false;')
    mel.eval('FBXExportFileVersion -v "FBX201800";')

    safe_path = filepath.replace("\\", "/")
    try:
        cmds.file(safe_path, force=True, options="v=0;",
                   type="FBX export", pr=True, es=True)
    except RuntimeError as e:
        cmds.warning(f"FBX export failed: {e}")
        return None

    print(f"[rig_export] Animation ({int(start)}-{int(end)}) "
          f"exported to {filepath}")
    return filepath
