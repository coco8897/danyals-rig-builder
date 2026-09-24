"""
===============================================================================
 PROP RIG - a sword, a gun, a chair or a box, rigged in a click
===============================================================================

 Three joints in a chain, the way game engines like props:

   {name}_root_BIND_JNT     where the prop sits in the world (placement)
   {name}_move_BIND_JNT     the prop itself: the mesh is bound here
   {name}_attach_BIND_JNT   the socket: the point that goes in a hand (the
                            grip) or where something else attaches. Rotate
                            its guide to aim the socket.

 Two controls: {name}_root_CTRL (a flat ring round the base: placement) and
 {name}_move_CTRL (a box round the prop: animate this one).

 Everything is prefixed with the prop's name, so any number of props live in
 one scene, next to a character or a vehicle.

 Attach to a hand: select the hand joint or control and attach(): the prop's
 attach point snaps into the hand and follows it. {name}_move_CTRL.space
 picks Root or Attached; rig_space_switch.switch_space() switches without a
 jump (pick up / put down in an animation).

     import prop_rig
     prop_rig.create_guides("sword", ["swordGeo"])   # place the 3 guides
     prop_rig.build("sword")                         # rig + bind the mesh
     prop_rig.attach("sword", "L_wrist_BIND_JNT")
     prop_rig.export_fbx("sword", "D:/sword.fbx")
===============================================================================
"""

import re

import maya.cmds as cmds

from character_rig_builder import (
    COLOR_CENTER, COLOR_IK, COLOR_LEFT,
    create_circle_ctrl, create_cube_ctrl, lock_hide_attrs, set_ctrl_color,
)

GUIDES_TOP = "PROP_GUIDES_GRP"
PARTS = ("root", "move", "attach")
TAG = "propRig"                  # string attr on a prop's joints and groups
SPACES = "Root:Attached"
_COLORS = {"root": COLOR_CENTER, "move": COLOR_LEFT, "attach": COLOR_IK}


# =============================================================================
# Names
# =============================================================================

def clean_name(name):
    """A safe prop name: letters, digits and _ only."""
    name = re.sub(r"[^A-Za-z0-9_]", "_", (name or "").strip()).strip("_")
    if not name:
        name = "prop"
    if name[0].isdigit():
        name = "prop_" + name
    return name


def guide(name, part):
    return "%s_%s_GUIDE" % (name, part)


def guides_grp(name):
    return "%s_PROP_GUIDES_GRP" % name


def rig_grp(name):
    return "%s_PROP_RIG_GRP" % name


def joint(name, part):
    return "%s_%s_BIND_JNT" % (name, part)


def ctrl(name, part):
    return "%s_%s_CTRL" % (name, part)


def offset(name, part):
    return "%s_%s_OFFSET" % (name, part)


def list_props():
    """Every prop in the scene (guides or rig), by name."""
    out = set()
    for grp in (cmds.ls("*_PROP_GUIDES_GRP", "*_PROP_RIG_GRP",
                        type="transform") or []):
        if cmds.attributeQuery(TAG, node=grp, exists=True):
            out.add(cmds.getAttr(grp + "." + TAG))
    return sorted(out)


def has_guides(name):
    return all(cmds.objExists(guide(name, p)) for p in PARTS)


def is_built(name):
    return cmds.objExists(rig_grp(name))


def _tag(node, name):
    if not cmds.attributeQuery(TAG, node=node, exists=True):
        cmds.addAttr(node, ln=TAG, dt="string")
    cmds.setAttr(node + "." + TAG, name, type="string")


def _meshes(nodes):
    out = []
    for n in nodes or []:
        if not cmds.objExists(n):
            continue
        if cmds.nodeType(n) == "mesh":
            n = cmds.listRelatives(n, p=True)[0]
        if cmds.listRelatives(n, s=True, type="mesh", ni=True):
            out.append(n)
    return out


def _selected_meshes():
    return _meshes(cmds.ls(sl=True, type="transform") or [])


def _box(meshes):
    """World box of the meshes' visible shapes (a transform's own box also
    counts a skinned mesh's hidden Orig shape)."""
    bb = None
    for m in meshes:
        shapes = cmds.listRelatives(m, s=True, ni=True, type="mesh",
                                    f=True) or [m]
        b = cmds.exactWorldBoundingBox(shapes)
        bb = b if bb is None else [min(bb[0], b[0]), min(bb[1], b[1]),
                                   min(bb[2], b[2]), max(bb[3], b[3]),
                                   max(bb[4], b[4]), max(bb[5], b[5])]
    return bb


# =============================================================================
# Guides
# =============================================================================

def create_guides(name, meshes=None):
    """Three guides for prop `name`, placed round `meshes` (default: the
    selected meshes; none = a 100 unit prop at the origin): root at the
    bottom, move in the middle, attach a quarter of the way up. Moving
    the root guide brings the others. Returns the prop's name."""
    name = clean_name(name)
    if has_guides(name):
        cmds.warning("[prop] '%s' already has guides." % name)
        return name
    if meshes is None:
        meshes = _selected_meshes()
    meshes = _meshes(meshes)
    bb = _box(meshes) or [-50.0, 0.0, -50.0, 50.0, 100.0, 50.0]
    cx, cz = 0.5 * (bb[0] + bb[3]), 0.5 * (bb[2] + bb[5])
    cy, h = 0.5 * (bb[1] + bb[4]), bb[4] - bb[1]
    size = max(bb[3] - bb[0], bb[4] - bb[1], bb[5] - bb[2], 1e-3)
    if not cmds.objExists(GUIDES_TOP):
        cmds.group(em=True, n=GUIDES_TOP)
    grp = cmds.group(em=True, n=guides_grp(name), p=GUIDES_TOP)
    _tag(grp, name)
    cmds.addAttr(grp, ln="propSize", at="double", dv=size)
    cmds.addAttr(grp, ln="propMeshes", dt="string")
    cmds.setAttr(grp + ".propMeshes", " ".join(
        cmds.ls(meshes, long=True) or []), type="string")
    where = {"root": (cx, bb[1], cz), "move": (cx, cy, cz),
             "attach": (cx, cy + 0.25 * h, cz)}
    parent = grp
    for part in PARTS:
        loc = cmds.spaceLocator(n=guide(name, part))[0]
        k = {"root": 0.35, "move": 0.25, "attach": 0.15}[part] * size
        for a in "XYZ":
            cmds.setAttr("%s.localScale%s" % (loc, a), k)
        set_ctrl_color(loc, _COLORS[part])
        cmds.parent(loc, parent)
        cmds.xform(loc, ws=True, t=where[part])
        parent = loc
    cmds.select(guide(name, "attach"))
    return name


def delete_guides(name):
    grp = guides_grp(name)
    if cmds.objExists(grp):
        cmds.delete(grp)
    if cmds.objExists(GUIDES_TOP) and not cmds.listRelatives(GUIDES_TOP,
                                                              c=True):
        cmds.delete(GUIDES_TOP)


# =============================================================================
# Build
# =============================================================================

def _world(node):
    """World position and rotation (no scale) of a guide."""
    t = cmds.xform(node, q=True, ws=True, t=True)
    r = cmds.xform(node, q=True, ws=True, ro=True)
    return t, r


def _bound_meshes(name):
    """Meshes skinned to any of the prop's joints."""
    out = []
    for part in PARTS:
        j = joint(name, part)
        if not cmds.objExists(j):
            continue
        for sc in set(cmds.listConnections(j, type="skinCluster") or []):
            for shape in cmds.skinCluster(sc, q=True, g=True) or []:
                xf = cmds.listRelatives(shape, p=True, f=True)
                if xf and xf[0] not in out:
                    out.append(xf[0])
    return out


def _unbind(mesh):
    for h in cmds.listHistory(mesh, pdo=True) or []:
        if cmds.nodeType(h) == "skinCluster":
            cmds.skinCluster(h, e=True, ub=True)
            return True
    return False


def build(name):
    """Build (or rebuild) prop `name` from its guides and bind its meshes to
    the move joint. Returns the rig group."""
    name = clean_name(name)
    if not has_guides(name):
        raise RuntimeError("No guides for prop '%s': create them first."
                           % name)
    gg = guides_grp(name)
    size = cmds.getAttr(gg + ".propSize") if cmds.attributeQuery(
        "propSize", node=gg, exists=True) else 100.0
    stored = (cmds.getAttr(gg + ".propMeshes") or "").split() \
        if cmds.attributeQuery("propMeshes", node=gg, exists=True) else []
    rebind = _bound_meshes(name) or [m for m in stored if cmds.objExists(m)]
    was_attached = attach_target(name)
    if is_built(name):
        delete_rig(name)

    top = cmds.group(em=True, n=rig_grp(name))
    _tag(top, name)
    try:
        import rig_telemetry
        cmds.addAttr(top, ln="builtWith", dt="string")
        cmds.setAttr(top + ".builtWith", "Danyal's Rig Builder v%s"
                     % rig_telemetry.TOOL_VERSION, type="string")
        cmds.setAttr(top + ".builtWith", l=True)
    except Exception:
        pass
    ctrls = cmds.group(em=True, n="%s_ctrls_GRP" % name, p=top)
    joints = cmds.group(em=True, n="%s_joints_GRP" % name, p=top)

    # ---- joints: root > move > attach ----
    cmds.select(cl=True)
    parent = joints
    for part in PARTS:
        t, r = _world(guide(name, part))
        cmds.select(cl=True)
        j = cmds.joint(n=joint(name, part))
        cmds.parent(j, parent)
        cmds.xform(j, ws=True, t=t, ro=r)
        cmds.makeIdentity(j, apply=True, r=True)     # rotation -> orient
        cmds.setAttr(j + ".segmentScaleCompensate", 0)
        cmds.setAttr(j + ".radius", 0.04 * size)
        _tag(j, name)
        parent = j

    # ---- controls ----
    t, r = _world(guide(name, "root"))
    root = create_circle_ctrl(ctrl(name, "root"), radius=0.6 * size,
                              normal=(0, 1, 0), color=COLOR_CENTER)
    root_off = cmds.group(em=True, n=offset(name, "root"), p=ctrls)
    cmds.xform(root_off, ws=True, t=t, ro=r)
    cmds.parent(root, root_off, r=True)
    t, r = _world(guide(name, "move"))
    move = create_cube_ctrl(ctrl(name, "move"), size=1.0, color=COLOR_LEFT)
    move_off = cmds.group(em=True, n=offset(name, "move"), p=root)
    cmds.xform(move_off, ws=True, t=t, ro=r)
    cmds.parent(move, move_off, r=True)
    # The box hugs the prop (its meshes, else a cube a third of its size).
    meshes = [m for m in rebind if cmds.objExists(m)]
    bb = _box(meshes)
    if bb:
        lo = cmds.xform(move, q=True, ws=True, t=True)
        pad = 0.08 * size
        cmds.scale((bb[3] - bb[0]) + pad, (bb[4] - bb[1]) + pad,
                   (bb[5] - bb[2]) + pad, move + ".cv[*]", ws=True,
                   p=lo)
        cmds.move(0.5 * (bb[0] + bb[3]) - lo[0], 0.5 * (bb[1] + bb[4])
                  - lo[1], 0.5 * (bb[2] + bb[5]) - lo[2], move + ".cv[*]",
                  r=True, ws=True)
    else:
        cmds.scale(0.35 * size, 0.35 * size, 0.35 * size, move + ".cv[*]")
    for c in (root, move):
        _tag(c, name)
    lock_hide_attrs(move, ("scaleX", "scaleY", "scaleZ", "visibility"))
    lock_hide_attrs(root, ("visibility",))
    # Uniform scale on the root ring: one `propScale` dial.
    cmds.addAttr(root, ln="propScale", at="double", dv=1.0, min=0.001,
                 k=True)
    for a in "XYZ":
        cmds.connectAttr(root + ".propScale", "%s.scale%s" % (root, a))
    lock_hide_attrs(root, ("scaleX", "scaleY", "scaleZ"))

    # ---- drive the joints ----
    cmds.parentConstraint(root, joint(name, "root"), mo=True)
    cmds.scaleConstraint(root, joint(name, "root"), mo=True)
    cmds.parentConstraint(move, joint(name, "move"), mo=True)
    cmds.setAttr(joints + ".visibility", 1)

    for m in rebind:
        if cmds.objExists(m):
            bind(name, [m])
    if was_attached and cmds.objExists(was_attached):
        attach(name, was_attached)
    cmds.select(move)
    print("[prop] Built '%s'%s." % (name, " and bound %s" % ", ".join(
        m.split("|")[-1] for m in rebind) if rebind else ""))
    return top


def bind(name, meshes=None):
    """Skin meshes (default: the selection) rigidly to the prop's move
    joint. Returns the skinClusters."""
    name = clean_name(name)
    j = joint(name, "move")
    if not cmds.objExists(j):
        raise RuntimeError("Build prop '%s' first." % name)
    meshes = _meshes(meshes) if meshes is not None else _selected_meshes()
    out = []
    for m in meshes:
        _unbind(m)
        short = m.split("|")[-1]
        out.append(cmds.skinCluster(j, m, tsb=True, mi=1,
                                    n=short + "_propSkin")[0])
    gg = guides_grp(name)
    if out and cmds.objExists(gg):
        have = (cmds.getAttr(gg + ".propMeshes") or "").split()
        for m in cmds.ls(meshes, long=True) or []:
            if m not in have:
                have.append(m)
        cmds.setAttr(gg + ".propMeshes", " ".join(have), type="string")
    return out


def delete_rig(name):
    """Delete prop `name`'s rig (its meshes are unbound back to their
    modelled shape; the guides stay)."""
    name = clean_name(name)
    detach(name)
    for m in _bound_meshes(name):
        _unbind(m)
    if cmds.objExists(rig_grp(name)):
        cmds.delete(rig_grp(name))


# =============================================================================
# Attach to a hand (or anything)
# =============================================================================

def attach_target(name):
    """What prop `name` is attached to, or None."""
    c = ctrl(name, "move")
    if cmds.objExists(c) and cmds.attributeQuery("attachTarget", node=c,
                                                 exists=True):
        return cmds.getAttr(c + ".attachTarget") or None
    return None


def attach(name, target, snap=True):
    """Make prop `name` follow `target` (a hand joint or control). With
    `snap`, the prop moves so its attach joint sits on the target (a grip
    in the hand), keeping its orientation. {name}_move_CTRL gets a `space`
    enum (Root / Attached) and is left Attached; animate on top of it."""
    name = clean_name(name)
    move, move_off = ctrl(name, "move"), offset(name, "move")
    if not cmds.objExists(move):
        raise RuntimeError("Build prop '%s' first." % name)
    if not cmds.objExists(target):
        raise RuntimeError("'%s' doesn't exist." % target)
    detach(name)
    rest_t = cmds.getAttr(move_off + ".translate")[0]
    rest_r = cmds.getAttr(move_off + ".rotate")[0]
    for attr, val in (("restTranslate", rest_t), ("restRotate", rest_r)):
        cmds.addAttr(move_off, ln=attr, at="double3")
        for a in "XYZ":
            cmds.addAttr(move_off, ln=attr + a, at="double", p=attr)
        cmds.setAttr(move_off + "." + attr, *val, type="double3")

    grp = cmds.group(em=True, n="%s_attach_GRP" % name, p=rig_grp(name))
    cmds.setAttr(grp + ".visibility", 0)
    root_space = cmds.spaceLocator(n="%s_rootSpace_LOC" % name)[0]
    held_space = cmds.spaceLocator(n="%s_attachedSpace_LOC" % name)[0]
    for loc in (root_space, held_space):
        cmds.parent(loc, grp)
        cmds.matchTransform(loc, move_off)
    if snap:
        grip = cmds.xform(joint(name, "attach"), q=True, ws=True, t=True)
        goal = cmds.xform(target, q=True, ws=True, t=True)
        cmds.move(goal[0] - grip[0], goal[1] - grip[1], goal[2] - grip[2],
                  held_space, r=True, ws=True)
    cmds.parentConstraint(ctrl(name, "root"), root_space, mo=True)
    cmds.parentConstraint(target, held_space, mo=True)
    pc = cmds.parentConstraint(root_space, held_space, move_off, mo=False,
                               n="%s_space_PC" % name)[0]
    cmds.addAttr(move, ln="space", at="enum", en=SPACES, k=True)
    cmds.addAttr(move, ln="attachTarget", dt="string")
    cmds.setAttr(move + ".attachTarget", target, type="string")
    for i, w in enumerate(cmds.parentConstraint(pc, q=True,
                                                weightAliasList=True)):
        cond = cmds.createNode("condition", n="%s_space%d_COND" % (name, i))
        cmds.connectAttr(move + ".space", cond + ".firstTerm")
        cmds.setAttr(cond + ".secondTerm", i)
        cmds.setAttr(cond + ".colorIfTrueR", 1)
        cmds.setAttr(cond + ".colorIfFalseR", 0)
        cmds.connectAttr(cond + ".outColorR", "%s.%s" % (pc, w))
        cmds.sets(cond, add=_nodes_set(name))
    cmds.setAttr(move + ".space", 1)
    return pc


def _nodes_set(name):
    s = "%s_propNodes_SET" % name
    if not cmds.objExists(s):
        s = cmds.sets(em=True, n=s)
    return s


def detach(name):
    """Stop prop `name` following its target: back on its root ring,
    exactly where it was built."""
    name = clean_name(name)
    move, move_off = ctrl(name, "move"), offset(name, "move")
    if not cmds.objExists(move):
        return False
    had = attach_target(name) is not None
    pc = "%s_space_PC" % name
    if cmds.objExists(pc):
        cmds.delete(pc)
    for n in ("%s_attach_GRP" % name, "%s_propNodes_SET" % name):
        if cmds.objExists(n) and n.endswith("_SET"):
            members = cmds.sets(n, q=True) or []
            if members:
                cmds.delete(members)     # an emptied set goes with them
        if cmds.objExists(n):
            cmds.delete(n)
    for attr in ("space", "attachTarget"):
        if cmds.attributeQuery(attr, node=move, exists=True):
            cmds.cutKey(move, at=attr, clear=True)
            cmds.deleteAttr(move + "." + attr)
    for attr, chan in (("restTranslate", "translate"),
                       ("restRotate", "rotate")):
        if cmds.attributeQuery(attr, node=move_off, exists=True):
            val = cmds.getAttr("%s.%s" % (move_off, attr))[0]
            cmds.setAttr("%s.%s" % (move_off, chan), *val)
            cmds.deleteAttr("%s.%s" % (move_off, attr))
    return had


def switch(name, attached, key=True):
    """Pick the prop up (attached=True) or put it down, without it jumping,
    keyed on the current frame."""
    import rig_space_switch
    move = ctrl(name, "move")
    if not cmds.attributeQuery("space", node=move, exists=True):
        cmds.warning("[prop] '%s' isn't attached to anything." % name)
        return False
    return rig_space_switch.switch_space(move, 1 if attached else 0, key=key)


# =============================================================================
# Game export
# =============================================================================

def is_prop_joint(node):
    return cmds.objExists(node) and cmds.attributeQuery(TAG, node=node,
                                                        exists=True) \
        and cmds.nodeType(node) == "joint"


def export_fbx(name, filepath, animation=False, start=None, end=None,
               include_mesh=True):
    """Prop `name` to FBX: its 3 joints (root > move > attach) plus its
    skinned meshes in the rest pose, or with `animation` the joints baked
    over [start, end] (default: the playback range)."""
    import rig_export as rx
    name = clean_name(name)
    js = [cmds.ls(joint(name, p), long=True) for p in PARTS]
    if not all(js):
        cmds.warning("[prop] Build prop '%s' first." % name)
        return None
    js = [j[0] for j in js]
    if not rx._ensure_fbx_loaded():
        return None
    plan = [(js[0], js[0].split("|")[-1], None),
            (js[1], js[1].split("|")[-1], js[0]),
            (js[2], js[2].split("|")[-1], js[1])]
    if start is None:
        start = cmds.playbackOptions(q=True, min=True)
    if end is None:
        end = cmds.playbackOptions(q=True, max=True)
    skel, copies = None, []
    with rx._keep_scene_state():
        try:
            if animation:
                skel = rx._ExportSkeleton(plan, ground_root=False).build()
                skel.bake(start, end)
                rx._fbx_options(skins=False, animated=True, start=start,
                                end=end)
                nodes = [skel.top]
            else:
                with rx._rest_pose():
                    skel = rx._ExportSkeleton(plan, ground_root=False).build()
                    skel.freeze()
                    if include_mesh:
                        for mesh, sc in rx._skinned_meshes(skel.bones):
                            copies.append(rx._copy_skinned_mesh(
                                mesh, sc, skel.bones))
                rx._fbx_options(skins=bool(copies), animated=False)
                nodes = [skel.top] + copies
            if not rx._write_fbx(filepath, nodes):
                cmds.warning("[prop] FBX export failed: nothing written.")
                return None
        except RuntimeError as e:
            cmds.warning("[prop] FBX export failed: %s" % e)
            return None
        finally:
            for c in copies:
                if cmds.objExists(c):
                    cmds.delete(c)
            if skel:
                skel.delete()
    print("[prop] '%s' exported to %s%s." % (
        name, filepath, " (animation %d-%d)" % (start, end)
        if animation else ""))
    return filepath
