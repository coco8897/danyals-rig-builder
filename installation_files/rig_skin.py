"""
===============================================================================
 RIG SKIN — character skinning toolkit (bind / mirror / copy / save / load)
===============================================================================

 The build tools make great skeletons; this bridges to a usable character:

   bind_mesh(mesh)              smooth-bind a mesh to all _BIND_JNT joints
   mirror_weights(mesh)         mirror skin weights L<->R across X (uses joint
                                side labels so L_* <-> R_* map correctly)
   copy_weights(src, dst)       transfer weights between two meshes
   save_weights(mesh, folder)   write the skin weights to disk (deformerWeights)
   load_weights(mesh, folder)   read them back onto a (re-bound) mesh

 Save -> rebuild the rig -> bind -> load is the round-trip that lets you
 iterate on a rig without re-painting weights every time.
===============================================================================
"""

import os

import maya.cmds as cmds


# =============================================================================
# Joints + skin cluster discovery
# =============================================================================

def bind_joints(include_bendy=True):
    """Every _BIND_JNT joint — the set a character mesh should skin to."""
    js = [j for j in (cmds.ls(type="joint") or [])
          if j.endswith("_BIND_JNT")]
    if not include_bendy:
        js = [j for j in js if "_bendy_" not in j]
    return js


def skin_cluster(mesh):
    """The skinCluster deforming `mesh`, or None."""
    shapes = cmds.listRelatives(mesh, s=True, ni=True) or [mesh]
    for shp in shapes:
        scs = cmds.ls(cmds.listHistory(shp) or [], type="skinCluster")
        if scs:
            return scs[0]
    return None


# =============================================================================
# Joint side labels (so mirror maps L_* <-> R_* correctly)
# =============================================================================

def label_joints(joints=None):
    """Set Maya side/type labels on the BIND joints from their L_/R_ names so
    copySkinWeights mirrorMode can pair them. L_x_BIND_JNT and R_x_BIND_JNT
    get otherType 'x_BIND_JNT' and side Left/Right; centre joints get None.
    Returns the number of joints labelled."""
    joints = joints if joints is not None else bind_joints()
    n = 0
    for j in joints:
        if not cmds.objExists(j):
            continue
        if j.startswith("L_"):
            side, base = 1, j[2:]
        elif j.startswith("R_"):
            side, base = 2, j[2:]
        else:
            side, base = 0, j[2:] if j.startswith("C_") else j
        try:
            cmds.setAttr(f"{j}.side", side)
            cmds.setAttr(f"{j}.type", 18)            # 18 = "Other"
            cmds.setAttr(f"{j}.otherType", base, type="string")
            n += 1
        except Exception:
            pass
    return n


# =============================================================================
# Bind
# =============================================================================

def bind_mesh(mesh, joints=None, max_influences=4, dropoff=4.0):
    """Smooth-bind `mesh` to the BIND joints (replacing any existing skin).
    Returns the new skinCluster."""
    if not cmds.objExists(mesh):
        cmds.warning(f"[skin] '{mesh}' not found.")
        return None
    joints = joints if joints is not None else bind_joints()
    if not joints:
        cmds.warning("[skin] No _BIND_JNT joints — build a rig first.")
        return None
    old = skin_cluster(mesh)
    if old:
        cmds.skinCluster(old, e=True, ub=True)   # unbind cleanly
    label_joints(joints)
    sc = cmds.skinCluster(
        joints, mesh, tsb=True, mi=max_influences, dr=dropoff,
        rui=False, n=f"{mesh}_skinCluster")[0]
    print(f"[skin] Bound '{mesh}' to {len(joints)} joints (mi={max_influences}).")
    return sc


# =============================================================================
# Mirror / copy
# =============================================================================

def mirror_weights(mesh, left_to_right=True):
    """Mirror skin weights across X. left_to_right copies the +X (L) weights
    onto the -X (R) side. Joints are labelled first so L_* map to R_*."""
    sc = skin_cluster(mesh)
    if not sc:
        cmds.warning(f"[skin] '{mesh}' has no skinCluster — bind it first.")
        return False
    label_joints()
    cmds.copySkinWeights(
        ss=sc, ds=sc, mirrorMode="YZ", mirrorInverse=not left_to_right,
        surfaceAssociation="closestComponent",
        influenceAssociation=["label", "oneToOne", "closestJoint"])
    print(f"[skin] Mirrored weights on '{mesh}' "
          f"({'L->R' if left_to_right else 'R->L'}).")
    return True


def copy_weights(src_mesh, dst_mesh):
    """Copy skin weights from src_mesh onto dst_mesh (closest point). dst is
    bound to the same joints first if it isn't already."""
    ssc = skin_cluster(src_mesh)
    if not ssc:
        cmds.warning(f"[skin] source '{src_mesh}' has no skinCluster.")
        return False
    dsc = skin_cluster(dst_mesh)
    if not dsc:
        infs = cmds.skinCluster(ssc, q=True, inf=True)
        dsc = bind_mesh(dst_mesh, joints=infs)
        if not dsc:
            return False
    cmds.copySkinWeights(
        ss=ssc, ds=dsc, noMirror=True,
        surfaceAssociation="closestPoint",
        influenceAssociation=["label", "name", "closestJoint"])
    print(f"[skin] Copied weights '{src_mesh}' -> '{dst_mesh}'.")
    return True


# =============================================================================
# Save / load (deformerWeights XML round-trip)
# =============================================================================

def save_weights(mesh, folder, name=None):
    """Export `mesh` skin weights to <folder>/<name>.xml. Returns the path."""
    sc = skin_cluster(mesh)
    if not sc:
        cmds.warning(f"[skin] '{mesh}' has no skinCluster.")
        return None
    if not os.path.isdir(folder):
        os.makedirs(folder)
    name = name or mesh
    fname = f"{name}.xml"
    cmds.deformerWeights(fname, path=folder, ex=True, deformer=sc,
                         format="XML", vertexConnections=True)
    print(f"[skin] Saved weights '{mesh}' -> {os.path.join(folder, fname)}")
    return os.path.join(folder, fname)


def load_weights(mesh, folder, name=None, method="index"):
    """Import skin weights from <folder>/<name>.xml onto `mesh`. The mesh
    must already be bound with matching influences (bind_mesh first)."""
    sc = skin_cluster(mesh)
    if not sc:
        cmds.warning(f"[skin] '{mesh}' has no skinCluster — bind it first.")
        return False
    name = name or mesh
    fname = f"{name}.xml"
    if not os.path.isfile(os.path.join(folder, fname)):
        cmds.warning(f"[skin] No weight file {os.path.join(folder, fname)}.")
        return False
    cmds.deformerWeights(fname, path=folder, im=True, deformer=sc,
                         method=method)
    cmds.skinCluster(sc, e=True, forceNormalizeWeights=True)
    print(f"[skin] Loaded weights onto '{mesh}'.")
    return True


# =============================================================================
# Gradient (bone-falloff) skinning
# =============================================================================
#
#  Lays smooth LINEAR weights along a bone chain: each bone's MIDDLE is fully
#  weighted to that bone's joint, ramping to a 50/50 blend at each joint — so
#  along a shoulder->elbow->wrist arm the weight reads 0,25,50,100,50,25,0...
#  per bone. It's the clean tubular base you then smooth.
#
#    gradient_skin_auto(mesh)            -> the whole BIND skeleton, one click
#    gradient_skin_chain(mesh, joints)   -> just an ordered chain (+ verts)
#
#  Both work on the whole mesh or a vertex selection, and bind first if needed.

def _vdist(a, b):
    return sum((a[k] - b[k]) ** 2 for k in range(3)) ** 0.5


def _wpos(node):
    return cmds.xform(node, q=True, ws=True, t=True)


def _order_chain(joints):
    """Order joints root -> tip by their depth in the DAG (so a selected chain
    comes out in hierarchy order regardless of pick order)."""
    return sorted((j for j in joints if cmds.objExists(j)),
                  key=lambda j: len(cmds.ls(j, l=True)[0].split("|")))


def _chain_bones(joints_ordered):
    """Bones of an ordered chain: each {a, b, pa} = segment a->b driven by a,
    with pa = the previous joint to blend with at the a-end (None at the root)."""
    out = []
    for i in range(len(joints_ordered) - 1):
        out.append({"a": joints_ordered[i], "b": joints_ordered[i + 1],
                    "pa": joints_ordered[i - 1] if i > 0 else None})
    return out


def _skeleton_bones(joints):
    """Every parent->child BIND bone in the skeleton: {a, b, pa} = a->b driven
    by joint a, pa = a's bind parent (blend at the a-end). Handles branches."""
    jset = set(joints)
    out = []
    for j in joints:
        pj = cmds.listRelatives(j, p=True, type="joint") or []
        pj = pj[0] if pj and pj[0] in jset else None
        for c in (cmds.listRelatives(j, c=True, type="joint") or []):
            if c in jset:
                out.append({"a": j, "b": c, "pa": pj})
    return out


def _bone_weights(p, bones, geo):
    """Gradient weights for a point p: find its CLOSEST bone, then along that
    bone give the bone-midpoint-full / joint-50-50 falloff (blend with the
    previous bone in the proximal half, the next bone in the distal half)."""
    best_d, best_i, best_t = 1e30, 0, 0.0
    for i, (Pa, ab, L2) in enumerate(geo):
        t = sum((p[k] - Pa[k]) * ab[k] for k in range(3)) / L2
        t = 0.0 if t < 0.0 else 1.0 if t > 1.0 else t
        cp = [Pa[k] + t * ab[k] for k in range(3)]
        d = sum((p[k] - cp[k]) ** 2 for k in range(3))
        if d < best_d:
            best_d, best_i, best_t = d, i, t
    bone, t = bones[best_i], best_t
    a, b, pa = bone["a"], bone["b"], bone["pa"]
    w = {}
    if t < 0.5:
        if pa:                                  # proximal half: blend a <- pa
            w[a] = 0.5 + t
            w[pa] = w.get(pa, 0.0) + 0.5 - t
        else:                                   # root bone: full a
            w[a] = 1.0
    else:                                       # distal half: blend a -> b
        w[a] = 1.5 - t
        w[b] = w.get(b, 0.0) + t - 0.5
    return w


def _apply_gradient(mesh, sc, bones, verts):
    """Compute + batch-set the gradient weights (OpenMaya, fast) for the given
    bones onto `verts` (vertex indices) of `mesh`/`sc`. verts=None => all."""
    import maya.api.OpenMaya as om
    import maya.api.OpenMayaAnim as oma
    geo = []
    for bo in bones:
        Pa, Pb = _wpos(bo["a"]), _wpos(bo["b"])
        ab = [Pb[k] - Pa[k] for k in range(3)]
        L2 = sum(c * c for c in ab) or 1e-9
        geo.append((Pa, ab, L2))

    msel = om.MSelectionList()
    msel.add(mesh)
    mdag = msel.getDagPath(0)
    mdag.extendToShape()
    pts = om.MFnMesh(mdag).getPoints(om.MSpace.kWorld)
    target = list(verts) if verts else list(range(len(pts)))
    if not target:
        return 0

    ssel = om.MSelectionList()
    ssel.add(sc)
    mfn = oma.MFnSkinCluster(ssel.getDependNode(0))
    infs = mfn.influenceObjects()
    idx = {infs[i].partialPathName(): i for i in range(len(infs))}
    # also map full path tails so L_x_BIND_JNT resolves whatever the name form
    n = len(infs)

    comp = om.MFnSingleIndexedComponent()
    cobj = comp.create(om.MFn.kMeshVertComponent)
    comp.addElements(target)
    weights = om.MDoubleArray(len(target) * n, 0.0)
    for row, vi in enumerate(target):
        p = (pts[vi].x, pts[vi].y, pts[vi].z)
        for j, wv in _bone_weights(p, bones, geo).items():
            ji = idx.get(j)
            if ji is None:
                ji = idx.get(j.split("|")[-1])
            if ji is not None:
                weights[row * n + ji] = wv
    inf_arr = om.MIntArray(list(range(n)))
    mfn.setWeights(mdag, cobj, inf_arr, weights, True)
    return len(target)


def _ensure_bound(mesh, joints):
    """Return the mesh's skinCluster, binding to `joints` first if needed and
    making sure every joint in `joints` is an influence."""
    sc = skin_cluster(mesh)
    if not sc:
        sc = bind_mesh(mesh, joints=joints)
        return sc
    have = set(cmds.skinCluster(sc, q=True, inf=True) or [])
    add = [j for j in joints if cmds.objExists(j) and j not in have]
    if add:
        cmds.skinCluster(sc, e=True, ai=add, lw=True, wt=0.0)
    return sc


def gradient_skin_chain(mesh, joints, verts=None):
    """Gradient-skin `mesh` along the ORDERED joint chain `joints` (root->tip):
    each bone's middle = 100% its joint, ramping to 50/50 at the joints. Binds
    to the chain if the mesh isn't skinned. verts = a list of vertex indices to
    limit it to (else the whole mesh)."""
    chain = _order_chain(joints)
    if len(chain) < 2:
        cmds.warning("[skin] gradient_skin_chain needs >= 2 joints.")
        return False
    bones = _chain_bones(chain)
    sc = _ensure_bound(mesh, chain)
    if not sc:
        return False
    label_joints(chain)
    n = _apply_gradient(mesh, sc, bones, verts)
    print(f"[skin] Gradient-skinned {n} verts of '{mesh}' along "
          f"{len(chain)} joints ({chain[0]} -> {chain[-1]}).")
    return True


def gradient_skin_auto(mesh, joints=None, verts=None):
    """One-click gradient skin of `mesh` to the WHOLE BIND skeleton: every
    vertex takes the bone-falloff of its closest bone (mid-bone full, joints
    50/50). Binds to all BIND joints first if needed. verts limits it to a
    vertex selection."""
    joints = joints if joints is not None else bind_joints()
    bones = _skeleton_bones(joints)
    if not bones:
        cmds.warning("[skin] No BIND bones found — build a rig first.")
        return False
    sc = _ensure_bound(mesh, joints)
    if not sc:
        return False
    label_joints(joints)
    n = _apply_gradient(mesh, sc, bones, verts)
    print(f"[skin] Gradient-skinned {n} verts of '{mesh}' to "
          f"{len(bones)} bones (whole skeleton).")
    return True
