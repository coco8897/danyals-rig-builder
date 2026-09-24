"""
===============================================================================
 RIG EXPORT — game-skeleton helpers + FBX export for Unreal / Unity
===============================================================================

 Exporting never changes the working rig. Both exports build a temporary,
 clean skeleton at the world root with the same bone names (an optional
 ground `root` bone, then C_root_BIND_JNT and every deform joint), bake or
 hold it, write the FBX and delete it. The file holds real joints only: no
 controls, groups or constraints.

   export_rig_fbx(filepath, include_mesh=True, include_bendy=False,
                  ground_root=True)
       The skeleton in the rest pose, plus a copy of every skinned mesh
       bound to it with the same weights.

   export_animation_fbx(filepath, start, end, include_bendy=False,
                        ground_root=True, in_place=False, take=None)
       The bones animated over a frame range (one take). With
       ground_root the `root` bone carries the travel (root motion);
       in_place keeps the character at the origin.

   add_clip / list_clips / remove_clip / export_clips
       Named frame ranges saved with the scene, exported one file each.

 Use the same ground_root setting for a rig and its animations so the
 skeletons match in the engine.

 Older helper, still available (not needed for exporting):

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

 Everything is rig-type-agnostic — they work the same on the
 biped CharacterRig, the QuadrupedRig, or the BirdRig as long as the
 standard CoreRig built `C_root_BIND_JNT` as the root.
===============================================================================
"""

import json
import os
import re

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
        # only the curve-driven detail joints: straight off the curve, or
        # through a lid seal / tweak control (all world positions)
        src = (cmds.listConnections(f"{j}.translate", s=True, d=False,
                                    p=True) or [None])[0]
        if not src:
            continue
        try:
            import advanced_face
            curve_driven = advanced_face._base_source(j)[1] is not None
        except ImportError:
            curve_driven = cmds.objectType(src.split(".")[0]) == \
                "pointOnCurveInfo"
        if not curve_driven:
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
        cmds.connectAttr(src, f"{pmm}.inPoint")
        cmds.connectAttr(f"{tgt}.worldInverseMatrix[0]", f"{pmm}.inMatrix")
        cmds.disconnectAttr(src, f"{j}.translate")
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
# 2. The export skeleton
# =============================================================================
# Exports never touch the working rig. A temporary, clean copy of the
# deform skeleton is built at the world root with the SAME bone names, each
# bone following its rig joint through a matrix link, then baked, exported
# and deleted. So the file holds only real joints (no controls, groups or
# constraints), animation keys live on the bones alone, and the rig keeps
# working afterwards.

GROUND_ROOT = "root"
CLIPS_NODE = "DRB_exportClips"
_TR = ("translateX", "translateY", "translateZ",
       "rotateX", "rotateY", "rotateZ", "scaleX", "scaleY", "scaleZ")


def _long(node):
    found = cmds.ls(node, l=True) or []
    return found[0] if found else None


def _in_rig(node):
    """True for nodes inside a rig's own groups (ribbon surfaces etc.)."""
    top = (_long(node) or "").split("|")
    return len(top) > 1 and top[1].endswith("_RIG_GRP")


def _character_skins():
    """(mesh transform, skinCluster) for every polygon mesh the user skinned
    (the rig's own skinned ribbon surfaces don't count)."""
    out = []
    for sc in cmds.ls(type="skinCluster") or []:
        for shape in cmds.skinCluster(sc, q=True, g=True) or []:
            if cmds.nodeType(shape) != "mesh" or _in_rig(shape):
                continue
            xf = cmds.listRelatives(shape, p=True, f=True)
            if xf:
                out.append((xf[0], sc))
    return out


def _skin_influences():
    """Every joint the character's skinned meshes deform with."""
    out = set()
    for _mesh, sc in _character_skins():
        for inf in cmds.skinCluster(sc, q=True, inf=True) or []:
            if cmds.objectType(inf) == "joint":
                out.add(_long(inf))
    return out


def export_joint_plan(include_bendy=False):
    """The bones to export, parents first: [(rig joint long name, bone name,
    parent rig joint long name or None)].

    Each bone's parent is its nearest exported ancestor in the rig; a joint
    with none takes the joint it's constrained to (the face lids follow the
    head), else C_root_BIND_JNT. Joints the skin uses are always included,
    so a mesh bound to ribbon joints keeps its weights."""
    root = _long(ROOT_BIND)
    if not root:
        return []
    live = {_long(j) for j in _exportable_bind_joints(include_bendy)}
    live |= _skin_influences()
    live.add(root)
    live.discard(None)
    # Props in the same scene export on their own (prop_rig.export_fbx).
    live = {j for j in live
            if not cmds.attributeQuery("propRig", node=j, exists=True)}

    def game_parent(j):
        if j == root:
            return None
        up = cmds.listRelatives(j, p=True, f=True)
        while up:
            if up[0] in live:
                return up[0]
            up = cmds.listRelatives(up[0], p=True, f=True)
        for ctype in ("parentConstraint", "orientConstraint",
                      "pointConstraint"):
            for c in set(cmds.listConnections(j, s=True, d=False,
                                              type=ctype) or []):
                query = getattr(cmds, ctype)
                for t in query(c, q=True, tl=True) or []:
                    tl = _long(t)
                    if tl in live and tl != j:
                        return tl
        return root

    parents = {j: game_parent(j) for j in live}
    for j in live:                                   # no loops
        seen, cur = set(), j
        while cur is not None:
            if cur in seen:
                parents[j] = root if j != root else None
                break
            seen.add(cur)
            cur = parents.get(cur)
    order, placed = [], set()

    def place(j):
        if j in placed:
            return
        if parents[j] is not None:
            place(parents[j])
        placed.add(j)
        order.append(j)

    place(root)
    for j in sorted(live):
        place(j)
    return [(j, j.split("|")[-1], parents[j]) for j in order]


class _ExportSkeleton(object):
    """Temporary world-level copy of the deform skeleton that follows the
    rig. With root motion, an optional ground `root` bone follows
    C_global_CTRL; in place, everything is measured in the character's own
    space instead, so the character stays at the origin."""

    def __init__(self, plan, ground_root=True, in_place=False):
        self.plan = plan
        self.ground_root = ground_root
        self.in_place = in_place
        self.bones = {}          # rig joint long name -> bone long name
        self.top = None
        self.utility = []

    def build(self):
        g = "C_global_CTRL" if cmds.objExists("C_global_CTRL") else None
        if self.ground_root:
            self.top = _long(cmds.createNode("joint", n=GROUND_ROOT))
            cmds.setAttr(self.top + ".segmentScaleCompensate", 0)
            if g and not self.in_place:
                self._drive(self.top, g, None)
        for live, name, parent in self.plan:
            par = self.bones.get(parent) if parent else self.top
            node = (cmds.createNode("joint", n=name, p=par) if par
                    else cmds.createNode("joint", n=name))
            node = _long(node)
            if self.top is None:
                self.top = node
            self.bones[live] = node
            self._drive(node, live, par)
        return self

    def _drive(self, bone, live, parent_bone):
        cmds.setAttr(bone + ".segmentScaleCompensate", 0)
        mm = cmds.createNode("multMatrix", n="drbExport_MM")
        dm = cmds.createNode("decomposeMatrix", n="drbExport_DM")
        self.utility += [mm, dm]
        cmds.connectAttr(live + ".worldMatrix[0]", mm + ".matrixIn[0]")
        if self.in_place and cmds.objExists("C_global_CTRL"):
            cmds.connectAttr("C_global_CTRL.worldInverseMatrix[0]",
                             mm + ".matrixIn[1]")
        if parent_bone:
            cmds.connectAttr(parent_bone + ".worldInverseMatrix[0]",
                             mm + ".matrixIn[2]")
        cmds.connectAttr(mm + ".matrixSum", dm + ".inputMatrix")
        for out, attr in (("outputTranslate", "translate"),
                          ("outputRotate", "rotate"),
                          ("outputScale", "scale")):
            cmds.connectAttr("%s.%s" % (dm, out), "%s.%s" % (bone, attr))

    def all_bones(self):
        bones = list(self.bones.values())
        if self.top not in bones:
            bones.insert(0, self.top)
        return bones

    def bake(self, start, end, step=1.0):
        bones = self.all_bones()
        cmds.bakeResults(bones, time=(start, end), simulation=True,
                         sampleBy=step, disableImplicitControl=True,
                         preserveOutsideKeys=False, sparseAnimCurveBake=False,
                         minimizeRotation=True, attribute=list(_TR))
        curves = cmds.listConnections(
            ["%s.rotate%s" % (b, a) for b in bones for a in "XYZ"],
            s=True, d=False, type="animCurve") or []
        if curves:
            cmds.filterCurve(curves, filter="euler")
        self._drop_utility()

    def freeze(self):
        """Hold the current pose as plain values (a static skeleton)."""
        values = {b: [cmds.getAttr("%s.%s" % (b, a)) for a in _TR]
                  for b in self.all_bones()}
        self._drop_utility()
        for b, vals in values.items():
            for a, v in zip(_TR, vals):
                cmds.setAttr("%s.%s" % (b, a), v)

    def _drop_utility(self):
        for n in self.utility:
            if cmds.objExists(n):
                cmds.delete(n)
        self.utility = []

    def delete(self):
        self._drop_utility()
        if self.top and cmds.objExists(self.top):
            cmds.delete(self.top)


class _rest_pose(object):
    """Every control at zero (the pose the rig was built and bound in) while
    the block runs; the animation comes back afterwards."""

    def __enter__(self):
        self.held = []
        for ctrl in cmds.ls("*_CTRL", type="transform") or []:
            for a in ("translateX", "translateY", "translateZ",
                      "rotateX", "rotateY", "rotateZ"):
                plug = "%s.%s" % (ctrl, a)
                try:
                    if (cmds.getAttr(plug, lock=True)
                            or not cmds.getAttr(plug, se=True)):
                        continue
                    self.held.append((plug, cmds.getAttr(plug)))
                    cmds.setAttr(plug, 0.0)
                except (RuntimeError, ValueError):
                    pass
        # A baked tail swing (chain_sim) or cargo shake (vehicle_cargo) is
        # animation too: switch it off.
        dials = []
        for ctrl in (cmds.ls("*_SETTINGS_CTRL", "*_cargo_CTRL",
                             type="transform") or []):
            if cmds.attributeQuery("physics", node=ctrl, exists=True):
                dials.append(ctrl + ".physics")
        # So are the face shape dials (face_shapes).
        if cmds.objExists("C_faceShapes_CTRL"):
            dials += ["C_faceShapes_CTRL." + a for a in cmds.listAttr(
                "C_faceShapes_CTRL", ud=True, k=True) or []]
        # And a car's crash dents (vehicle_crash): export the car undamaged.
        if cmds.objExists("C_chassis_CTRL") and cmds.attributeQuery(
                "crashDamage", node="C_chassis_CTRL", exists=True):
            dials.append("C_chassis_CTRL.crashDamage")
        for plug in dials:
            try:
                if (not cmds.getAttr(plug, lock=True)
                        and cmds.getAttr(plug, se=True)):
                    self.held.append((plug, cmds.getAttr(plug)))
                    cmds.setAttr(plug, 0.0)
            except (RuntimeError, ValueError):
                pass
        return self

    def __exit__(self, *exc):
        for plug, value in self.held:
            if not cmds.keyframe(plug, q=True, keyframeCount=True):
                try:
                    cmds.setAttr(plug, value)
                except RuntimeError:
                    pass
        cmds.currentTime(cmds.currentTime(q=True), edit=True)
        return False


def _skinned_meshes(bone_map):
    """(mesh transform, skinCluster) pairs deformed by the exported joints."""
    return [(mesh, sc) for mesh, sc in _character_skins()
            if {_long(i) for i in cmds.skinCluster(sc, q=True, inf=True)
                or []} & set(bone_map)]


def _copy_skinned_mesh(mesh, sc, bone_map):
    """A world-level duplicate of `mesh` skinned to the export bones with
    the same weights. Returns the duplicate."""
    import maya.api.OpenMaya as om2
    import maya.api.OpenMayaAnim as oma2
    short = mesh.split("|")[-1]
    dup = _long(cmds.duplicate(mesh, rr=True)[0])
    for shp in cmds.listRelatives(dup, s=True, f=True) or []:
        if cmds.getAttr(shp + ".intermediateObject"):
            cmds.delete(shp)
    for a in _TR + ("visibility",):
        cmds.setAttr("%s.%s" % (dup, a), lock=False)
    if cmds.listRelatives(dup, p=True):
        dup = _long(cmds.parent(dup, w=True)[0])
    dup = _long(cmds.rename(dup, short))

    def fn_for(node):
        sel = om2.MSelectionList()
        sel.add(node)
        return oma2.MFnSkinCluster(sel.getDependNode(0))

    def shape_path(node):
        shapes = cmds.listRelatives(node, s=True, f=True, ni=True) or [node]
        sel = om2.MSelectionList()
        sel.add(shapes[0])
        return sel.getDagPath(0)

    live_fn = fn_for(sc)
    live_infs = [p.fullPathName() for p in live_fn.influenceObjects()]
    bones = [bone_map[i] for i in live_infs]
    new_sc = cmds.skinCluster(bones, dup, toSelectedBones=True,
                              maximumInfluences=cmds.skinCluster(
                                  sc, q=True, mi=True) or 4,
                              obeyMaxInfluences=False,
                              n=short + "_export_skinCluster")[0]
    new_fn = fn_for(new_sc)
    new_order = [p.fullPathName() for p in new_fn.influenceObjects()]
    live_path = shape_path(mesh)
    new_path = shape_path(dup)
    comp = om2.MFnSingleIndexedComponent()
    comp_obj = comp.create(om2.MFn.kMeshVertComponent)
    comp.setCompleteData(om2.MFnMesh(live_path).numVertices)
    weights, _count = live_fn.getWeights(live_path, comp_obj)
    indices = om2.MIntArray([new_order.index(b) for b in bones])
    new_fn.setWeights(new_path, comp_obj, indices, weights, False)
    return dup


def _fbx_options(skins, animated, start=0, end=0, step=1):
    mel.eval('FBXResetExport;')
    mel.eval('FBXExportSmoothingGroups -v true;')
    mel.eval('FBXExportInputConnections -v false;')
    mel.eval('FBXExportConstraints -v false;')
    mel.eval('FBXExportCameras -v false;')
    mel.eval('FBXExportLights -v false;')
    mel.eval('FBXExportSkins -v %s;' % ("true" if skins else "false"))
    mel.eval('FBXExportShapes -v %s;' % ("true" if skins else "false"))
    mel.eval('FBXExportEmbeddedTextures -v false;')
    mel.eval('FBXExportAnimationOnly -v false;')
    mel.eval('FBXExportUpAxis y;')
    mel.eval('FBXExportInAscii -v false;')
    mel.eval('FBXExportFileVersion -v "FBX201800";')
    if animated:
        mel.eval('FBXExportBakeComplexAnimation -v true;')
        mel.eval('FBXExportBakeComplexStart -v %d;' % int(start))
        mel.eval('FBXExportBakeComplexEnd -v %d;' % int(end))
        mel.eval('FBXExportBakeComplexStep -v %d;' % max(1, int(round(step))))
        # One take per file (engines name the animation after the file;
        # extra named takes would import as extra animations).
        mel.eval('FBXExportSplitAnimationIntoTakes -c;')
    else:
        mel.eval('FBXExportBakeComplexAnimation -v false;')


def _write_fbx(filepath, nodes):
    folder = os.path.dirname(filepath)
    if folder and not os.path.isdir(folder):
        os.makedirs(folder)
    if os.path.isfile(filepath):
        os.remove(filepath)
    cmds.select(nodes, r=True)
    safe = filepath.replace("\\", "/")
    mel.eval('FBXExport -f "%s" -s;' % safe)
    return os.path.isfile(filepath)


class _keep_scene_state(object):
    def __enter__(self):
        self.sel = cmds.ls(sl=True, l=True) or []
        self.time = cmds.currentTime(q=True)
        return self

    def __exit__(self, *exc):
        cmds.currentTime(self.time, edit=True)
        live = [n for n in self.sel if cmds.objExists(n)]
        if live:
            cmds.select(live, r=True)
        else:
            cmds.select(cl=True)
        return False


# =============================================================================
# 3. FBX export — rig (skeleton + skinned mesh)
# =============================================================================

def export_rig_fbx(filepath, include_mesh=True, include_bendy=False,
                   organize_first=False, ground_root=True):
    """Export the skeleton (+ the skinned meshes) to FBX, in the rest pose.

    The rig is left exactly as it was. Every mesh skinned to the skeleton
    is exported as a copy bound to the export bones with the same weights.

    Args:
        include_mesh: include the skinned meshes.
        include_bendy: include ribbon bendy joints (joints the skin uses
            are always included).
        organize_first: also run Make Game Skeleton on the rig (not needed
            for exporting; kept for older scripts).
        ground_root: add a `root` bone at the ground above the skeleton
            (use the same setting for the rig and its animations).

    Returns the filepath on success, None on failure.
    """
    if not cmds.objExists(ROOT_BIND):
        cmds.warning(f"No {ROOT_BIND} found — build a rig first.")
        return None
    if not _ensure_fbx_loaded():
        return None
    if organize_first:
        organize_for_game_export(verbose=False)
    plan = export_joint_plan(include_bendy)
    skel = None
    copies = []
    with _keep_scene_state():
        try:
            with _rest_pose():
                skel = _ExportSkeleton(plan, ground_root=ground_root).build()
                skel.freeze()
                if include_mesh:
                    for mesh, sc in _skinned_meshes(skel.bones):
                        copies.append(_copy_skinned_mesh(mesh, sc, skel.bones))
            _fbx_options(skins=bool(copies), animated=False)
            if not _write_fbx(filepath, [skel.top] + copies):
                cmds.warning("FBX export failed: nothing written.")
                return None
        except RuntimeError as e:
            cmds.warning(f"FBX export failed: {e}")
            return None
        finally:
            for c in copies:
                if cmds.objExists(c):
                    cmds.delete(c)
            if skel:
                skel.delete()
    print(f"[rig_export] Rig exported to {filepath} ({len(plan)} bones, "
          f"{len(copies)} skinned mesh(es)).")
    return filepath


# =============================================================================
# 4. FBX export — animation
# =============================================================================

def export_animation_fbx(filepath, start=None, end=None,
                         include_bendy=False, organize_first=False,
                         bake_step=1.0, ground_root=True, in_place=False,
                         take=None):
    """Export the animation over [start, end] (default: playback range) as
    an FBX of the skeleton's bones, animated. Nothing else goes in the file.

    The rig is left exactly as it was: no keys are added to it and nothing
    is re-parented or disconnected.

    Args:
        ground_root: a `root` bone at the ground carries the character's
            travel (root motion). Match the setting used for the rig export.
        in_place: remove the travel so the character stays at the origin.
        take: the clip name, for the log (the file name names the
            animation in the engine).

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
    if end <= start:
        cmds.warning("Animation export needs a range of at least 2 frames.")
        return None
    if organize_first:
        organize_for_game_export(verbose=False)
    take = take or os.path.splitext(os.path.basename(filepath))[0]
    plan = export_joint_plan(include_bendy)
    skel = None
    with _keep_scene_state():
        try:
            skel = _ExportSkeleton(plan, ground_root=ground_root,
                                   in_place=in_place).build()
            skel.bake(start, end, bake_step)
            _fbx_options(skins=False, animated=True, start=start, end=end,
                         step=bake_step)
            if not _write_fbx(filepath, [skel.top]):
                cmds.warning("FBX export failed: nothing written.")
                return None
        except RuntimeError as e:
            cmds.warning(f"FBX export failed: {e}")
            return None
        finally:
            if skel:
                skel.delete()
    print(f"[rig_export] Animation '{take}' ({int(start)}-{int(end)}, "
          f"{len(plan)} bones) exported to {filepath}")
    return filepath


# =============================================================================
# 5. Clips: named frame ranges, exported in one go
# =============================================================================

def list_clips():
    """[{"name", "start", "end"}] saved with the scene."""
    if not cmds.objExists(CLIPS_NODE):
        return []
    try:
        return json.loads(cmds.getAttr(CLIPS_NODE + ".clips") or "[]")
    except ValueError:
        return []


def _save_clips(clips):
    if not cmds.objExists(CLIPS_NODE):
        cmds.createNode("network", n=CLIPS_NODE)
        cmds.addAttr(CLIPS_NODE, ln="clips", dt="string")
    cmds.setAttr(CLIPS_NODE + ".clips", json.dumps(clips), type="string")


def add_clip(name, start, end):
    """Save (or update) a named clip."""
    name = str(name).strip()
    if not re.match(r"^[A-Za-z0-9][A-Za-z0-9_\-]*$", name):
        raise ValueError("clip names use letters, digits, _ and - "
                         "(the name becomes the file name)")
    if end <= start:
        raise ValueError("a clip needs an end frame after its start")
    clips = [c for c in list_clips() if c["name"] != name]
    clips.append({"name": name, "start": float(start), "end": float(end)})
    clips.sort(key=lambda c: c["start"])
    _save_clips(clips)
    return clips


def remove_clip(name):
    clips = [c for c in list_clips() if c["name"] != name]
    _save_clips(clips)
    return clips


def export_clips(folder, include_bendy=False, ground_root=True,
                 in_place=False, clips=None):
    """Export every clip to `folder` as <clip name>.fbx. Returns the files."""
    written = []
    for clip in (list_clips() if clips is None else clips):
        path = os.path.join(folder, clip["name"] + ".fbx")
        if export_animation_fbx(path, clip["start"], clip["end"],
                                include_bendy=include_bendy,
                                ground_root=ground_root, in_place=in_place,
                                take=clip["name"]):
            written.append(path)
    return written
