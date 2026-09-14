"""
===============================================================================
 CHARACTER RIG BUILDER - Maya 2023 (Python 3)
===============================================================================

 A complete biped rigging system in one Python file.

 Modules:
   * CoreRig         - world (global) + COG controls, global scale
   * SpineRig        - ribbon spine (3 ctrls + bendy follicles + pelvis/chest)
   * NeckHeadRig     - FK neck + head + head-tip ctrls
   * ClavicleRig     - per-side FK clavicle
   * ArmRig          - shoulder/elbow/wrist with IK/FK switch, stretchy IK,
                       set-driven-key visibility, ribbon bendy per segment
   * LegRig          - hip/knee/ankle/ball/toe with IK/FK switch, stretchy IK,
                       reverse-foot pivots, foot roll/bank/wiggle attributes,
                       ribbon bendy upper + lower
   * CharacterRig    - orchestrator, parents modules together

 Naming convention:
   {side}_{module}_{part}_{layer}_{type}
   e.g. L_arm_elbow_BIND_JNT, R_leg_knee_FK_CTRL, C_spine_03_BIND_JNT

 Sides: L (left, blue), R (right, red), C (center, yellow)
 Layers: BIND (skin to these), FK (hidden driver), IK (hidden driver), DRV
         (ribbon drivers), CTRL (animator-facing)

 Usage in Maya Script Editor (Python tab):

    import sys
    sys.path.append(r"/path/to/folder/containing/this/file")
    import character_rig_builder
    from importlib import reload; reload(character_rig_builder)

    rig = character_rig_builder.CharacterRig()
    rig.build()

    # Tear it down:
    rig.delete()

 Skin your geometry to:
   * Spine bendy joints     (C_spine_NN_BIND_JNT)
   * pelvis + chest anchors (C_pelvis_BIND_JNT, C_chest_BIND_JNT)
   * Neck/head              (C_neck_BIND_JNT, C_head_BIND_JNT)
   * Clavicles              ({side}_clavicle_BIND_JNT)
   * Arm BIND joints + arm bendy joints
   * Leg BIND joints + leg bendy joints

 Do NOT skin to FK/IK driver chains or the DRV joints (those are hidden).

===============================================================================
"""

import math
import re
import maya.cmds as cmds
import maya.api.OpenMaya as om


# =============================================================================
# SECTION 1: UTILITY FUNCTIONS
# =============================================================================

# Color indices (Maya draw-override palette)
COLOR_LEFT       = 6    # blue
COLOR_RIGHT      = 13   # red
COLOR_CENTER     = 17   # yellow
COLOR_IK         = 14   # green
COLOR_PV         = 20   # pink
COLOR_BEND       = 22   # pale yellow
COLOR_GLOBAL     = 17   # yellow
COLOR_COG        = 21   # light red/pink
COLOR_SETTINGS   = 17   # yellow

# Global rig scale (1 unit = 1 cm, human ~170 cm tall).
# Multiply every control size and offset distance by this value.
SCALE = 10.0


def make_offset_group(node, suffix="OFFSET"):
    """Insert a zeroed transform above 'node', preserving its world matrix."""
    parent = cmds.listRelatives(node, p=True)
    if "_CTRL" in node:
        grp_name = node.replace("_CTRL", f"_{suffix}")
    else:
        grp_name = f"{node}_{suffix}"
    grp = cmds.group(em=True, n=grp_name)
    cmds.matchTransform(grp, node)
    cmds.parent(node, grp)
    if parent:
        cmds.parent(grp, parent[0])
    return grp


def set_ctrl_color(ctrl, color_idx):
    for shp in cmds.listRelatives(ctrl, s=True) or []:
        cmds.setAttr(f"{shp}.overrideEnabled", 1)
        cmds.setAttr(f"{shp}.overrideColor", color_idx)


def create_circle_ctrl(name, radius=1.0, normal=(1, 0, 0), color=None):
    ctrl = cmds.circle(n=name, nr=normal, r=radius, ch=False)[0]
    if color is not None:
        set_ctrl_color(ctrl, color)
    return ctrl


def create_cube_ctrl(name, size=1.0, color=None):
    s = size * 0.5
    pts = [
        (-s, -s, -s), ( s, -s, -s), ( s,  s, -s), (-s,  s, -s), (-s, -s, -s),
        (-s, -s,  s), ( s, -s,  s), ( s, -s, -s), ( s, -s,  s),
        ( s,  s,  s), ( s,  s, -s), ( s,  s,  s),
        (-s,  s,  s), (-s,  s, -s), (-s,  s,  s), (-s, -s,  s),
    ]
    ctrl = cmds.curve(n=name, d=1, p=pts, k=list(range(len(pts))))
    if color is not None:
        set_ctrl_color(ctrl, color)
    return ctrl


def create_diamond_ctrl(name, size=1.0, color=None):
    s = size
    pts = [
        ( 0,  s,  0), ( s,  0,  0), ( 0,  0, -s), (-s,  0,  0), ( 0,  s,  0),
        ( 0,  0,  s), ( s,  0,  0), ( 0,  0, -s), ( 0, -s,  0), ( 0,  0,  s),
        (-s,  0,  0), ( 0, -s,  0),
    ]
    ctrl = cmds.curve(n=name, d=1, p=pts, k=list(range(len(pts))))
    if color is not None:
        set_ctrl_color(ctrl, color)
    return ctrl


def create_square_ctrl(name, size=1.0, normal=(0, 1, 0), color=None):
    """Flat square in the plane perpendicular to 'normal'."""
    s = size * 0.5
    if normal == (0, 1, 0):
        pts = [(-s, 0, -s), ( s, 0, -s), ( s, 0,  s),
               (-s, 0,  s), (-s, 0, -s)]
    elif normal == (1, 0, 0):
        pts = [(0, -s, -s), (0,  s, -s), (0,  s,  s),
               (0, -s,  s), (0, -s, -s)]
    else:
        pts = [(-s, -s, 0), ( s, -s, 0), ( s,  s, 0),
               (-s,  s, 0), (-s, -s, 0)]
    ctrl = cmds.curve(n=name, d=1, p=pts, k=list(range(len(pts))))
    if color is not None:
        set_ctrl_color(ctrl, color)
    return ctrl


def create_global_ctrl(name, size=4.0, color=None):
    """Big arrow-square at the floor — the world transform."""
    s = size
    h = s * 0.25
    pts = [
        (-s, 0, -s + h), (-s + h, 0, -s + h), (-s + h, 0, -s),
        ( s - h, 0, -s), ( s - h, 0, -s + h), ( s, 0, -s + h),
        ( s, 0,  s - h), ( s - h, 0,  s - h), ( s - h, 0,  s),
        # forward arrow:
        ( h * 1.2, 0, s), ( h * 1.2, 0,  s + h * 1.5), ( h * 2.0, 0, s + h * 1.5),
        ( 0, 0, s + h * 3.0),
        (-h * 2.0, 0, s + h * 1.5), (-h * 1.2, 0, s + h * 1.5), (-h * 1.2, 0, s),
        (-s + h, 0,  s), (-s + h, 0,  s - h), (-s, 0,  s - h),
        (-s, 0, -s + h),
    ]
    ctrl = cmds.curve(n=name, d=1, p=pts, k=list(range(len(pts))))
    if color is not None:
        set_ctrl_color(ctrl, color)
    return ctrl


def create_gear_ctrl(name, size=0.5, color=None):
    pts = []
    for i in range(17):
        ang = math.radians(i * 22.5)
        r = size if i % 2 == 0 else size * 0.55
        pts.append((math.cos(ang) * r, 0.0, math.sin(ang) * r))
    ctrl = cmds.curve(n=name, d=1, p=pts, k=list(range(len(pts))))
    if color is not None:
        set_ctrl_color(ctrl, color)
    return ctrl


def create_clavicle_ctrl(name, size=1.0, color=None):
    """Sideways triangle pointing away from spine."""
    s = size
    pts = [( 0, 0, 0), ( s, s * 0.5, 0), ( s, -s * 0.5, 0), ( 0, 0, 0)]
    ctrl = cmds.curve(n=name, d=1, p=pts, k=list(range(len(pts))))
    if color is not None:
        set_ctrl_color(ctrl, color)
    return ctrl


def create_foot_ctrl(name, size=1.0, color=None):
    """Boot-shaped flat curve, pointing along +Z."""
    s = size
    pts = [
        (-s * 0.6, 0, -s * 0.7),
        ( s * 0.6, 0, -s * 0.7),
        ( s * 0.6, 0,  s * 1.3),
        (-s * 0.6, 0,  s * 1.3),
        (-s * 0.6, 0, -s * 0.7),
    ]
    ctrl = cmds.curve(n=name, d=1, p=pts, k=list(range(len(pts))))
    if color is not None:
        set_ctrl_color(ctrl, color)
    return ctrl


def lock_hide_attrs(node, attrs):
    for a in attrs:
        try:
            cmds.setAttr(f"{node}.{a}", l=True, k=False, cb=False)
        except Exception:
            pass


def add_fk_length(ctrl, child_offset, name):
    """Give an FK ctrl a keyable `length` (1 = rest) that slides the next FK
    ctrl's offset down the bone. Stretchy IK lengthens the IK joints'
    translateX; FK has no other way to lengthen (ctrl translate + scale are
    locked), so without this an IK -> FK match can't hold a stretched limb.
    The FK joint follows its ctrl, and the BIND tx blend already reads FK tx.
    """
    cmds.addAttr(ctrl, ln="length", at="double", min=0.01, dv=1.0, k=True)
    rest = cmds.getAttr(f"{child_offset}.translate")[0]
    md = cmds.createNode("multiplyDivide", n=name)
    cmds.setAttr(f"{md}.input1", *rest)
    for ax in "XYZ":
        cmds.connectAttr(f"{ctrl}.length", f"{md}.input2{ax}")
    cmds.connectAttr(f"{md}.output", f"{child_offset}.translate", f=True)
    return md


def make_stretch_anchor(chain_root, parent_jnt, name):
    """Empty transform ON an IK chain's root joint (shoulder / hip), parented
    under the chain's parent joint, for stretchy IK to measure from.

    parent_jnt itself is only a valid start point when it sits on the root:
    a pelvis is not the hip and a chest is not the shoulder, so its distance
    to the IK target changes by a different ratio than root -> target does
    and the IK end misses (legs, clavicle-less extra arms). The root joint
    can't be used either, the IK solver writes its rotate (a cycle). The
    root's translate is fixed under parent_jnt, so this rides exactly on it
    while depending only on parent_jnt.
    """
    anchor = cmds.createNode("transform", n=name, p=parent_jnt, ss=True)
    cmds.xform(anchor, ws=True,
               t=cmds.xform(chain_root, q=True, ws=True, t=True))
    return anchor


def get_pole_vector_position(start, mid, end, distance=5.0):
    """Project mid onto line start->end, push outward through mid."""
    s = om.MVector(*cmds.xform(start, q=True, ws=True, t=True))
    m = om.MVector(*cmds.xform(mid,   q=True, ws=True, t=True))
    e = om.MVector(*cmds.xform(end,   q=True, ws=True, t=True))

    sw = e - s
    sm = m - s
    proj_len = (sm * sw) / sw.length()
    closest = s + sw.normal() * proj_len
    pv_dir = (m - closest)
    if pv_dir.length() < 1e-5:
        pv_dir = om.MVector(0, 0, -1)
    pv_dir = pv_dir.normal()
    pv_pos = m + pv_dir * distance
    return (pv_pos.x, pv_pos.y, pv_pos.z)


def make_pv_guide_line(mid_joint, pv_ctrl, name, parent_grp=None):
    """Template guide line from an IK mid joint (elbow / knee) to its pole
    vector ctrl, AdvancedSkeleton-style — so the animator can see at a
    glance which PV the limb aims at. Both ends follow live (worldMatrix →
    curve CVs), it's unselectable (template display), and it inherits the
    PV ctrl's IK/FK visibility."""
    crv = cmds.curve(d=1, p=[(0, 0, 0), (0, 0, 1)], n=name)
    shp = cmds.listRelatives(crv, s=True)[0]
    cmds.setAttr(f"{crv}.inheritsTransform", 0)
    cmds.setAttr(f"{shp}.overrideEnabled", 1)
    cmds.setAttr(f"{shp}.overrideDisplayType", 1)   # template = grey, no pick
    for idx, node in ((0, mid_joint), (1, pv_ctrl)):
        dm = cmds.createNode("decomposeMatrix", n=f"{name}_{idx}_DM")
        cmds.connectAttr(f"{node}.worldMatrix[0]", f"{dm}.inputMatrix")
        cmds.connectAttr(f"{dm}.outputTranslate",
                         f"{shp}.controlPoints[{idx}]")
    try:
        # PV ctrl vis is SDK-driven by the IK/FK switch — piggyback on it
        # so the line shows only in IK mode.
        cmds.connectAttr(f"{pv_ctrl}.visibility", f"{crv}.v")
    except Exception:
        pass
    if parent_grp and cmds.objExists(parent_grp):
        cmds.parent(crv, parent_grp)
    return crv


def create_follicle(surface, u, v, name):
    fol_shape = cmds.createNode("follicle", n=f"{name}Shape")
    fol_xform = cmds.listRelatives(fol_shape, p=True)[0]
    fol_xform = cmds.rename(fol_xform, name)

    surface_shape = cmds.listRelatives(surface, s=True, ni=True)[0]
    cmds.connectAttr(f"{surface_shape}.local", f"{fol_shape}.inputSurface")
    cmds.connectAttr(f"{surface_shape}.worldMatrix[0]",
                     f"{fol_shape}.inputWorldMatrix")
    cmds.connectAttr(f"{fol_shape}.outTranslate", f"{fol_xform}.translate")
    cmds.connectAttr(f"{fol_shape}.outRotate", f"{fol_xform}.rotate")

    cmds.setAttr(f"{fol_shape}.parameterU", u)
    cmds.setAttr(f"{fol_shape}.parameterV", v)
    return fol_xform


def build_ribbon_bendy(start_jnt, end_jnt, label, prefix,
                       bendy_count, misc_grp, color):
    """
    Build one bendy segment connecting two bind joints:
      - NURBS plane aligned along the bone
      - 3 driver joints (start, mid, end), skinned to ribbon
      - mid driver lives under a BEND_CTRL inside an auto group constrained
        between start/end
      - N follicles each carrying a bendy bind joint

    Returns dict: { ribbon, bend_ctrl, bendy_jnts (list) }
    """
    seg_prefix = f"{prefix}_{label}"
    start_pos = cmds.xform(start_jnt, q=True, ws=True, t=True)
    end_pos   = cmds.xform(end_jnt,   q=True, ws=True, t=True)
    mid_pos   = tuple((s + e) * 0.5 for s, e in zip(start_pos, end_pos))
    seg_dist  = math.sqrt(sum((e - s) ** 2
                              for s, e in zip(start_pos, end_pos)))

    # --- Ribbon --------------------------------------------------------------
    ribbon = cmds.nurbsPlane(
        n=f"{seg_prefix}_ribbon",
        ax=(0, 1, 0), w=seg_dist, lr=0.15,
        d=3, u=bendy_count, v=1, ch=False,
    )[0]
    cmds.xform(ribbon, ws=True, t=mid_pos)
    aim_tmp = cmds.aimConstraint(
        end_jnt, ribbon,
        aim=(1, 0, 0), u=(0, 1, 0),
        wut="objectrotation", wuo=start_jnt, wu=(0, 1, 0),
    )
    cmds.delete(aim_tmp)
    cmds.parent(ribbon, misc_grp)
    # CRITICAL: inheritsTransform=0 on the ribbon so that when the rig moves
    # via global/cog, the ribbon's worldMatrix does NOT pick up that motion.
    # Without this, the skinCluster (driven by drivers under cog) deforms
    # the CVs in world by +delta, AND the ribbon's parent matrix ALSO offsets
    # by +delta → CVs end up at +2*delta in world → follicles output 2x →
    # bendy joints translate twice. With inheritsTransform=0 the deformer is
    # the sole source of motion → exactly one translation.
    cmds.setAttr(f"{ribbon}.inheritsTransform", 0)

    # --- Auto group between start/end ---------------------------------------
    mid_auto = cmds.group(em=True, n=f"{seg_prefix}_mid_AUTO")
    cmds.matchTransform(mid_auto, ribbon, pos=False, rot=True)
    cmds.xform(mid_auto, ws=True, t=mid_pos)
    cmds.parent(mid_auto, misc_grp)
    cmds.pointConstraint(start_jnt, end_jnt, mid_auto, mo=False)
    cmds.aimConstraint(
        end_jnt, mid_auto,
        aim=(1, 0, 0), u=(0, 1, 0),
        wut="objectrotation", wuo=start_jnt, wu=(0, 1, 0),
        mo=False,
    )

    # --- Bend ctrl -----------------------------------------------------------
    bend_ctrl = create_circle_ctrl(
        f"{seg_prefix}_BEND_CTRL", radius=0.6 * SCALE,
        normal=(1, 0, 0), color=color,
    )
    cmds.matchTransform(bend_ctrl, mid_auto)
    bend_offset = make_offset_group(bend_ctrl)
    cmds.parent(bend_offset, mid_auto)
    lock_hide_attrs(bend_ctrl, ["sx", "sy", "sz", "v"])

    # --- Driver joints -------------------------------------------------------
    cmds.select(cl=True)
    start_drv = cmds.joint(n=f"{seg_prefix}_start_DRV_JNT", p=start_pos)
    cmds.select(cl=True)
    end_drv   = cmds.joint(n=f"{seg_prefix}_end_DRV_JNT",   p=end_pos)
    cmds.select(cl=True)
    mid_drv   = cmds.joint(n=f"{seg_prefix}_mid_DRV_JNT")
    cmds.matchTransform(mid_drv, bend_ctrl)
    cmds.parent(mid_drv, bend_ctrl)
    cmds.setAttr(f"{mid_drv}.translate", 0, 0, 0)
    cmds.setAttr(f"{mid_drv}.rotate",    0, 0, 0)
    cmds.setAttr(f"{mid_drv}.jointOrient", 0, 0, 0)

    cmds.parentConstraint(start_jnt, start_drv, mo=False)
    cmds.parentConstraint(end_jnt,   end_drv,   mo=False)

    drv_grp = cmds.group(em=True, n=f"{seg_prefix}_drivers_GRP", p=misc_grp)
    cmds.parent([start_drv, end_drv], drv_grp)

    # --- Skin ribbon to drivers ---------------------------------------------
    cmds.skinCluster(
        [start_drv, mid_drv, end_drv], ribbon,
        tsb=True, mi=2, dr=4.0,
        n=f"{seg_prefix}_ribbon_skinCluster",
    )

    # --- Follicles + bendy bind joints --------------------------------------
    fol_grp = cmds.group(em=True, n=f"{seg_prefix}_follicles_GRP", p=misc_grp)
    cmds.setAttr(f"{fol_grp}.inheritsTransform", 0)

    bendy_jnts = []
    for i in range(bendy_count):
        u_param = (i + 0.5) / float(bendy_count)
        fol = create_follicle(ribbon, u_param, 0.5,
                              f"{seg_prefix}_fol_{i + 1:02d}")
        cmds.parent(fol, fol_grp)
        cmds.select(cl=True)
        bjnt = cmds.joint(n=f"{seg_prefix}_bendy_{i + 1:02d}_JNT")
        cmds.parent(bjnt, fol)
        cmds.setAttr(f"{bjnt}.translate",   0, 0, 0)
        cmds.setAttr(f"{bjnt}.rotate",      0, 0, 0)
        cmds.setAttr(f"{bjnt}.jointOrient", 0, 0, 0)
        cmds.setAttr(f"{bjnt}.radius", 0.4)
        bendy_jnts.append(bjnt)

    # Scale-constrain bendy joints to globalScale so meshes skinned to
    # them deform uniformly when the rig is scaled. Follicle xforms only
    # write translate/rotate, so without this the joints' scale stays
    # at 1 even at globalScale=6, leaving vertex offsets un-scaled.
    if cmds.objExists("C_global_CTRL"):
        for bjnt in bendy_jnts:
            cmds.scaleConstraint("C_global_CTRL", bjnt, mo=True)

    return {"ribbon": ribbon, "bend_ctrl": bend_ctrl,
            "bendy_jnts": bendy_jnts}


# =============================================================================
# SECTION 2: CORE RIG (global + cog)
# =============================================================================

class CoreRig(object):

    def __init__(self, positions=None):
        self.positions = positions or {"cog": (0, 100, 0)}
        self.main_grp = self.ctrl_grp = self.jnt_grp = self.misc_grp = None
        self.global_ctrl = self.cog_ctrl = None
        self.root_bind_jnt = None  # fallback parent when spine is disabled

    def build(self):
        # Hierarchy: CHARACTER_RIG_GRP -> C_global_CTRL -> {controls,joints,misc}_GRP
        # Putting all sub-groups under global_ctrl means globalScale propagates
        # to controls AND joints AND ribbons via parent inheritance — no fragile
        # multi-attribute connections needed.
        self.main_grp = cmds.group(em=True, n="CHARACTER_RIG_GRP")

        self.global_ctrl = create_global_ctrl("C_global_CTRL", size=4.0 * SCALE,
                                              color=COLOR_GLOBAL)
        cmds.parent(self.global_ctrl, self.main_grp)

        # Visible "built with" tag on the global ctrl (AS-style passive
        # credit): anyone opening a rig file made with the tool sees where it
        # came from. Locked, non-keyable, harmless to animation/export.
        try:
            try:
                from rig_telemetry import TOOL_VERSION as _v
            except Exception:
                _v = ""
            if not cmds.attributeQuery("builtWith", node=self.global_ctrl,
                                       exists=True):
                cmds.addAttr(self.global_ctrl, ln="builtWith", dt="string")
            cmds.setAttr(f"{self.global_ctrl}.builtWith",
                         ("Danyal's Rig Builder" + (f" v{_v}" if _v else "")),
                         type="string")
            cmds.setAttr(f"{self.global_ctrl}.builtWith", l=True,
                         channelBox=True)
        except Exception as e:
            cmds.warning("builtWith tag skipped: %r" % (e,))

        self.ctrl_grp = cmds.group(em=True, n="controls_GRP", p=self.global_ctrl)
        self.jnt_grp  = cmds.group(em=True, n="joints_GRP",   p=self.global_ctrl)
        self.misc_grp = cmds.group(em=True, n="misc_GRP",     p=self.global_ctrl)
        # misc_GRP inherits transform now (so it scales with global). The
        # follicle sub-group inside still has inheritsTransform=0, so follicles
        # don't double-transform.
        cmds.setAttr(f"{self.misc_grp}.v", 0)

        # COG ctrl — positioned from C_root guide (or default Y=100)
        self.cog_ctrl = create_circle_ctrl(
            "C_cog_CTRL", radius=2.0 * SCALE, normal=(0, 1, 0), color=COLOR_COG,
        )
        cmds.xform(self.cog_ctrl, ws=True, t=self.positions["cog"])
        cog_offset = make_offset_group(self.cog_ctrl)
        cmds.parent(cog_offset, self.ctrl_grp)

        # Re-parent jnt_grp + misc_grp UNDER cog_ctrl so they follow COG
        # translations / rotations (not just global). Otherwise: when the
        # animator moves COG to position the body, the BIND chain follows
        # (via parent constraints to ctrls under cog) but heavy face curves
        # under misc_grp don't move — PCI keeps outputting the old world
        # positions → heavy face joints stay at original world position
        # while head joint moves → mesh stretches between them.
        # Same applies to spine ribbons under misc_grp.
        # Absolute parenting preserves world positions of the groups (they
        # both compensate via their local translate to keep world identity).
        cmds.parent(self.jnt_grp, self.cog_ctrl)
        cmds.parent(self.misc_grp, self.cog_ctrl)

        # globalScale attribute drives global_ctrl's scale uniformly.
        cmds.addAttr(self.global_ctrl, ln="globalScale", at="double",
                     min=0.01, dv=1.0, k=True)
        for ax in ("scaleX", "scaleY", "scaleZ"):
            cmds.connectAttr(f"{self.global_ctrl}.globalScale",
                             f"{self.global_ctrl}.{ax}", f=True)

        # ---- Visibility master attrs on the root ctrl --------------------
        # Production defaults: ctrls ON, joints OFF, mesh ON. Animator can
        # flip joints on for debugging without touching the outliner.
        cmds.addAttr(self.global_ctrl, ln="ctrlVis",  at="bool",
                     dv=True,  k=True)
        cmds.addAttr(self.global_ctrl, ln="jointVis", at="bool",
                     dv=False, k=True)
        cmds.addAttr(self.global_ctrl, ln="meshVis",  at="bool",
                     dv=True,  k=True)
        cmds.connectAttr(f"{self.global_ctrl}.ctrlVis",
                         f"{self.ctrl_grp}.v", f=True)
        cmds.connectAttr(f"{self.global_ctrl}.jointVis",
                         f"{self.jnt_grp}.v", f=True)
        # meshVis is wired opportunistically at build time — see
        # _wire_mesh_vis(). If the mesh isn't in the scene yet (build
        # before import), the attr stays unconnected and the
        # `rig_ui.show()` panel exposes a "Wire Mesh Visibility" button
        # that calls the same helper after the mesh lands.
        self._wire_mesh_vis()

        lock_hide_attrs(self.global_ctrl,
                        ["scaleX", "scaleY", "scaleZ", "v"])
        lock_hide_attrs(self.cog_ctrl, ["sx", "sy", "sz", "v"])

        # Root BIND joint — always created. Other modules use it as a
        # fallback parent when spine is disabled (head-only rigs, no-spine
        # creatures, etc.). Follows the COG ctrl.
        cmds.select(cl=True)
        self.root_bind_jnt = cmds.joint(
            n="C_root_BIND_JNT", p=self.positions["cog"],
        )
        cmds.setAttr(f"{self.root_bind_jnt}.jointOrient", 0, 0, 0)
        cmds.setAttr(f"{self.root_bind_jnt}.radius", 0.6 * SCALE)
        cmds.parent(self.root_bind_jnt, self.jnt_grp)
        cmds.parentConstraint(self.cog_ctrl, self.root_bind_jnt, mo=False)

    def delete(self):
        if cmds.objExists("CHARACTER_RIG_GRP"):
            cmds.delete("CHARACTER_RIG_GRP")

    # ------------------------------------------------------------------------
    # Mesh visibility wiring — called at build time and exposed to the UI
    # so the connection can be (re-)made after the mesh is imported.
    # ------------------------------------------------------------------------

    GEO_GROUP_CANDIDATES = ("geo", "Geo", "GEO", "geometry",
                             "Geometry", "GEOMETRY", "meshes", "MESHES")

    def _find_geo_group(self):
        """Return the geo group name, or None. Same lookup logic as the
        UI's Select All Geo button: exact names first, then any top-level
        transform with 'geo' substring (case-insensitive)."""
        for name in self.GEO_GROUP_CANDIDATES:
            if cmds.objExists(name):
                return name
        for top in (cmds.ls(assemblies=True) or []):
            if "geo" in top.lower():
                return top
        return None

    def _wire_mesh_vis(self):
        """Connect global_ctrl.meshVis to the geo group's visibility.

        Silent no-op if there's no geo group in the scene yet or the
        connection already exists. Safe to call multiple times."""
        if not self.global_ctrl:
            return False
        grp = self._find_geo_group()
        if not grp:
            return False
        dst = f"{grp}.v"
        src = f"{self.global_ctrl}.meshVis"
        # Skip if already wired (avoid double-connection error)
        cur = cmds.listConnections(dst, s=True, d=False, plugs=True) or []
        if src in cur:
            return True
        try:
            cmds.connectAttr(src, dst, f=True)
            return True
        except Exception as e:
            cmds.warning(f"Could not wire meshVis -> {dst}: {e}")
            return False


# =============================================================================
# SECTION 3: SPINE RIG (ribbon-based)
# =============================================================================

class SpineRig(object):

    def __init__(self, positions=None, bendy_count=7, fk_chain_count=3,
                 spine_type="ribbon",
                 parent_ctrl=None, parent_grp=None,
                 ctrl_grp=None, jnt_grp=None, misc_grp=None):
        self.positions = positions or {
            "hip":   (0, 100.0, 0),
            "chest": (0, 140.0, 0),
        }
        self.bendy_count = bendy_count
        # Number of FK ctrls in the chain between hip and chest. Rotations
        # propagate up the chain (hip → FK_01 → FK_02 → ... → chest), so
        # rotating hip ALSO rotates the chest position (proper FK behavior).
        # Set to 0 for the old sibling-ctrl behavior where hip and chest
        # rotate independently.
        self.fk_chain_count = fk_chain_count
        self.parent_ctrl = parent_ctrl   # cog_ctrl
        self.parent_grp = parent_grp
        self.ctrl_grp = ctrl_grp
        self.jnt_grp = jnt_grp
        self.misc_grp = misc_grp

        # Outputs
        self.hip_ctrl = self.mid_ctrl = self.chest_ctrl = None
        self.hip_offset = self.chest_offset = self.mid_auto = None
        self.fk_ctrls = []  # FK chain ctrls (between hip and chest)
        self.pelvis_bind_jnt = None
        self.chest_bind_jnt = None
        self.spine_bendy_jnts = []
        self.spine_bendy_follicles = []  # parallel to spine_bendy_jnts
        self.bendy_grp = None            # free parent for the bendy BINDs
        self.fk_drv_jnts = []            # FK chain DRV joints (rotation source)
        self.fk_drv_grp = None
        self.bendy_constraints = []      # per-bendy {pc, weight_aliases, mults}
        self.ik_fk_reverse = None        # reverse node for (1 - switch)
        self.chest_fk_constraint = None  # (pc, alias) — weight = switch
        self.settings_ctrl = None

    def build(self):
        self._build_anchor_joints()
        self._build_controls()
        self._build_ribbon()
        self._build_fk_chain()
        self._build_settings_ctrl()

    # ------------------------------------------------------------------------

    def _build_anchor_joints(self):
        cmds.select(cl=True)
        self.pelvis_bind_jnt = cmds.joint(n="C_pelvis_BIND_JNT",
                                          p=self.positions["hip"])
        cmds.select(cl=True)
        self.chest_bind_jnt = cmds.joint(n="C_chest_BIND_JNT",
                                         p=self.positions["chest"])
        for j in (self.pelvis_bind_jnt, self.chest_bind_jnt):
            cmds.setAttr(f"{j}.jointOrient", 0, 0, 0)
            cmds.setAttr(f"{j}.radius", 1.2)
            cmds.parent(j, self.jnt_grp)

    def _build_controls(self):
        # Hip ctrl — bottom of spine, also drives pelvis BIND
        self.hip_ctrl = create_circle_ctrl(
            "C_spine_hip_CTRL", radius=2.5 * SCALE, normal=(0, 1, 0),
            color=COLOR_CENTER,
        )
        cmds.xform(self.hip_ctrl, ws=True, t=self.positions["hip"])
        self.hip_offset = make_offset_group(self.hip_ctrl)
        cmds.parent(self.hip_offset, self.parent_ctrl)

        # Chest ctrl — top of spine, drives chest BIND. Its offset will be
        # parented under the TOP of the FK chain so rotations propagate.
        self.chest_ctrl = create_circle_ctrl(
            "C_spine_chest_CTRL", radius=2.2 * SCALE, normal=(0, 1, 0),
            color=COLOR_CENTER,
        )
        cmds.xform(self.chest_ctrl, ws=True, t=self.positions["chest"])
        self.chest_offset = make_offset_group(self.chest_ctrl)

        # ===== FK Chain =====
        # Build a chain of FK ctrls between hip and chest. Each is a child
        # of the previous, so rotations propagate up the chain. Chest is
        # parented to the TOP of the chain → rotating hip rotates entire
        # chain AND chest (classic FK spine behavior).
        # FK ctrls are rotation-only (translates locked).
        hip_pos   = self.positions["hip"]
        chest_pos = self.positions["chest"]
        prev_ctrl = self.hip_ctrl   # bottom of chain = hip

        for i in range(self.fk_chain_count):
            fk_pos = self._fk_position(i, hip_pos, chest_pos)
            fk_ctrl = create_circle_ctrl(
                f"C_spine_FK_{i + 1:02d}_CTRL",
                radius=1.8 * SCALE, normal=(0, 1, 0),
                color=COLOR_CENTER,
            )
            cmds.xform(fk_ctrl, ws=True, t=fk_pos)
            fk_offset = make_offset_group(fk_ctrl)
            cmds.parent(fk_offset, prev_ctrl)   # chain to previous
            # FK ctrls are rotation-only — lock translate so animators
            # don't accidentally drift segments.
            lock_hide_attrs(fk_ctrl,
                            ["tx", "ty", "tz", "sx", "sy", "sz", "v"])
            self.fk_ctrls.append(fk_ctrl)
            prev_ctrl = fk_ctrl

        # Parent chest_offset DIRECTLY to parent_ctrl (cog) — NOT under the
        # FK chain. This decouples the chest from FK ctrl rotations so that
        # in pure-IK mode the animator can rotate FK ctrls without dragging
        # the chest (and therefore the ribbon, and therefore the bendy
        # joints). In FK mode, _build_fk_chain reattaches the chest to the
        # top of the FK_DRV chain via a parentConstraint whose weight is
        # driven by IK_FK_Switch — so chest follows the FK chain only when
        # the animator chooses FK.
        cmds.parent(self.chest_offset, self.parent_ctrl)

        # Mid auto group + mid ctrl (auto-blended midpoint between hip+chest)
        self.mid_auto = cmds.group(em=True, n="C_spine_mid_AUTO")
        mid_pos = tuple((self.positions["hip"][i]
                         + self.positions["chest"][i]) * 0.5
                        for i in range(3))
        cmds.xform(self.mid_auto, ws=True, t=mid_pos)
        cmds.parent(self.mid_auto, self.parent_ctrl)
        cmds.pointConstraint(self.hip_ctrl, self.chest_ctrl,
                             self.mid_auto, mo=False)
        cmds.orientConstraint(self.hip_ctrl, self.chest_ctrl,
                              self.mid_auto, mo=False)

        self.mid_ctrl = create_circle_ctrl(
            "C_spine_mid_CTRL", radius=1.6 * SCALE, normal=(0, 1, 0),
            color=COLOR_BEND,
        )
        cmds.matchTransform(self.mid_ctrl, self.mid_auto)
        mid_offset = make_offset_group(self.mid_ctrl)
        cmds.parent(mid_offset, self.mid_auto)

        # Drive the pelvis/chest bind joints
        cmds.parentConstraint(self.hip_ctrl, self.pelvis_bind_jnt, mo=False)
        cmds.parentConstraint(self.chest_ctrl, self.chest_bind_jnt, mo=False)

        lock_hide_attrs(self.hip_ctrl,   ["sx", "sy", "sz", "v"])
        lock_hide_attrs(self.chest_ctrl, ["sx", "sy", "sz", "v"])
        lock_hide_attrs(self.mid_ctrl,   ["sx", "sy", "sz", "v"])

    def _build_ribbon(self):
        """Build a vertical ribbon along the spine driven by 3 joints."""
        hip_pos   = self.positions["hip"]
        chest_pos = self.positions["chest"]
        mid_pos   = tuple((hip_pos[i] + chest_pos[i]) * 0.5 for i in range(3))
        seg_dist  = math.sqrt(sum((c - h) ** 2
                                  for h, c in zip(hip_pos, chest_pos)))

        # Ribbon (length along X locally, aim it up via aim constraint)
        ribbon = cmds.nurbsPlane(
            n="C_spine_ribbon",
            ax=(0, 1, 0), w=seg_dist, lr=0.15,
            d=3, u=self.bendy_count, v=1, ch=False,
        )[0]
        cmds.xform(ribbon, ws=True, t=mid_pos)
        # Aim from hip to chest with X as primary
        aim_tmp = cmds.aimConstraint(
            self.chest_bind_jnt, ribbon,
            aim=(1, 0, 0), u=(0, 0, 1),
            wut="objectrotation", wuo=self.pelvis_bind_jnt, wu=(0, 0, 1),
        )
        cmds.delete(aim_tmp)
        cmds.parent(ribbon, self.misc_grp)
        # See note on build_ribbon_bendy — without this the skin deformation
        # and the parent inheritance both shift the CVs, doubling the world
        # translation seen on the bendy joints.
        cmds.setAttr(f"{ribbon}.inheritsTransform", 0)

        # Driver joints at hip, mid, chest
        cmds.select(cl=True)
        hip_drv = cmds.joint(n="C_spine_hip_DRV_JNT", p=hip_pos)
        cmds.select(cl=True)
        chest_drv = cmds.joint(n="C_spine_chest_DRV_JNT", p=chest_pos)
        cmds.select(cl=True)
        mid_drv = cmds.joint(n="C_spine_mid_DRV_JNT")
        cmds.matchTransform(mid_drv, self.mid_ctrl)

        # Constrain drivers to ctrls
        cmds.parentConstraint(self.hip_ctrl, hip_drv, mo=False)
        cmds.parentConstraint(self.chest_ctrl, chest_drv, mo=False)
        cmds.parentConstraint(self.mid_ctrl, mid_drv, mo=False)

        drv_grp = cmds.group(em=True, n="C_spine_drivers_GRP", p=self.misc_grp)
        cmds.parent([hip_drv, mid_drv, chest_drv], drv_grp)

        # Skin ribbon
        cmds.skinCluster(
            [hip_drv, mid_drv, chest_drv], ribbon,
            tsb=True, mi=2, dr=4.0,
            n="C_spine_ribbon_skinCluster",
        )

        # Follicles + bendy bind joints
        # Follicles live under misc_grp (deformer territory).
        # Bendy BIND joints live under jnt_grp (BIND territory) as FREE
        # transforms — NOT children of the follicles. They get a
        # parentConstraint(follicle, bjnt) at IK weight, and a second
        # parentConstraint(FK_DRV, bjnt) at FK weight, wired in
        # _build_fk_chain(). That way the IK_FK_Switch can blend between
        # ribbon-driven (IK) and FK-chain-driven (FK) without re-parenting.
        fol_grp = cmds.group(em=True, n="C_spine_follicles_GRP",
                             p=self.misc_grp)
        cmds.setAttr(f"{fol_grp}.inheritsTransform", 0)

        self.bendy_grp = cmds.group(em=True, n="C_spine_bendy_BIND_GRP",
                                     p=self.jnt_grp)
        # inheritsTransform=0 so the parentConstraints (which deliver world
        # positions) compose correctly with the cog/global moves — same
        # reasoning as the follicle parent.
        cmds.setAttr(f"{self.bendy_grp}.inheritsTransform", 0)

        for i in range(self.bendy_count):
            u_param = (i + 0.5) / float(self.bendy_count)
            fol = create_follicle(ribbon, u_param, 0.5,
                                  f"C_spine_fol_{i + 1:02d}")
            cmds.parent(fol, fol_grp)
            cmds.select(cl=True)
            bjnt = cmds.joint(n=f"C_spine_{i + 1:02d}_BIND_JNT")
            cmds.parent(bjnt, self.bendy_grp)
            cmds.setAttr(f"{bjnt}.translate",   0, 0, 0)
            cmds.setAttr(f"{bjnt}.rotate",      0, 0, 0)
            cmds.setAttr(f"{bjnt}.jointOrient", 0, 0, 0)
            cmds.setAttr(f"{bjnt}.radius", 0.5)
            self.spine_bendy_jnts.append(bjnt)
            self.spine_bendy_follicles.append(fol)

        # Scale-constrain spine bendy joints to globalScale (same reason
        # as build_ribbon_bendy — follicles only write translate/rotate).
        if cmds.objExists("C_global_CTRL"):
            for bjnt in self.spine_bendy_jnts:
                cmds.scaleConstraint("C_global_CTRL", bjnt, mo=True)

    def _fk_position(self, i, hip_pos, chest_pos):
        """Return the world position for the i-th FK spine ctrl / DRV joint.

        If the positions dict was given an explicit `fk_NN` key (1-based
        index) — written by rig_guides.read_positions() from per-spine
        guide locators — use that as-is. This lets the rigger author a
        curved spine by dragging the intermediate spine guides.

        If no override is present, fall back to linear interpolation
        between hip and chest, exactly like before.
        """
        key = f"fk_{i + 1:02d}"
        override = self.positions.get(key)
        if override is not None:
            return tuple(override)
        t = (i + 1) / float(self.fk_chain_count + 1)
        return tuple(hip_pos[k] + (chest_pos[k] - hip_pos[k]) * t
                      for k in range(3))

    def _build_fk_chain(self):
        """Build an FK driver chain that the bendy BIND joints can switch to.

        Architecture:
          - FK_DRV chain has `fk_chain_count` driver joints (default 3) at
            the same world positions as the FK ctrls between hip and chest.
            Each is parent-constrained to its corresponding FK ctrl, so the
            chain follows whatever pose the FK ctrls strike.
          - "Virtual" endpoint targets are hip_ctrl (at u=0) and chest_ctrl
            (at u=1). The bendy joints reuse those instead of dedicated
            duplicate joints — keeps joint count to the FK ctrl count the
            user requested while still hitting the spine endpoints.
          - Every bendy BIND gets ONE parentConstraint with THREE targets:
                target 0 = its follicle           (IK source, weight = 1-switch)
                target 1 = lower FK chain target  (FK source, weight = switch * w_lo)
                target 2 = upper FK chain target  (FK source, weight = switch * w_hi)
            where w_lo / w_hi linearly interpolate the FK chain at this
            bendy joint's u parameter.
          - IK_FK_Switch attr lives on settings_ctrl; visibility of mid_ctrl
            and FK ctrls is gated by the switch (mid hides in FK mode, FK
            ctrls hide in IK mode).
        """
        if not self.spine_bendy_jnts:
            return
        hip_pos   = self.positions["hip"]
        chest_pos = self.positions["chest"]

        # ---- FK_DRV joints (one per FK ctrl, hierarchically chained) ------
        self.fk_drv_grp = cmds.group(em=True, n="C_spine_FK_DRV_GRP",
                                       p=self.jnt_grp)
        prev = None
        for i, fk_ctrl in enumerate(self.fk_ctrls):
            pos = self._fk_position(i, hip_pos, chest_pos)
            cmds.select(cl=True)
            drv = cmds.joint(n=f"C_spine_FK_{i + 1:02d}_DRV_JNT", p=pos)
            cmds.setAttr(f"{drv}.jointOrient", 0, 0, 0)
            cmds.setAttr(f"{drv}.radius", 0.4)
            if prev is None:
                cmds.parent(drv, self.fk_drv_grp)
            else:
                cmds.parent(drv, prev)
            cmds.parentConstraint(fk_ctrl, drv, mo=False)
            self.fk_drv_jnts.append(drv)
            prev = drv

        # ---- Virtual FK target sequence at u = 0, 1/(K+1), ..., 1 ---------
        # K = number of FK_DRV joints. The targets are:
        #   index 0: hip_ctrl at u=0
        #   index 1..K: fk_drv_jnts at u=1/(K+1)..K/(K+1)
        #   index K+1: chest_ctrl at u=1
        K = len(self.fk_drv_jnts)
        fk_targets = [self.hip_ctrl] + list(self.fk_drv_jnts) + [self.chest_ctrl]
        fk_target_us = [i / float(K + 1) for i in range(K + 2)]

        # ---- Reverse node — outputX = 1 - switch (IK weight when switch=FK) -
        self.ik_fk_reverse = cmds.createNode("reverse",
                                              n="C_spine_IK_FK_REV")

        # ---- chest_offset follows FK chain top only when in FK mode -------
        # mo=True captures the rest offset so that at weight=1 the chest
        # lands at its bind world position when the FK chain is at rest.
        # Weight defaults to 0 (= IK mode); we wire it to IK_FK_Switch in
        # _build_settings_ctrl.
        if self.chest_offset and self.fk_drv_jnts:
            pc = cmds.parentConstraint(
                self.fk_drv_jnts[-1], self.chest_offset, mo=True,
            )[0]
            aliases = cmds.parentConstraint(pc, q=True,
                                              weightAliasList=True)
            cmds.setAttr(f"{pc}.{aliases[0]}", 0)
            self.chest_fk_constraint = (pc, aliases[0])

        # ---- Wire each bendy joint --------------------------------------
        for i, (bjnt, fol) in enumerate(zip(self.spine_bendy_jnts,
                                              self.spine_bendy_follicles)):
            u = (i + 0.5) / float(self.bendy_count)
            # Find FK target indices that bracket this u
            lo_idx = 0
            while lo_idx < len(fk_target_us) - 1 and fk_target_us[lo_idx + 1] <= u:
                lo_idx += 1
            hi_idx = min(lo_idx + 1, len(fk_target_us) - 1)
            if hi_idx == lo_idx:
                w_hi = 0.0
            else:
                w_hi = (u - fk_target_us[lo_idx]) / \
                       (fk_target_us[hi_idx] - fk_target_us[lo_idx])
            w_lo = 1.0 - w_hi

            # CRITICAL: snap bendy joint to follicle pose BEFORE making
            # the constraint, so mo=True captures the correct rest. The
            # follicle's rotation aligns with the ribbon tangent (for a
            # vertical spine that's a ~90° twist around Z); the FK_DRV
            # chain at rest has identity rotation. Without pre-snap +
            # mo=True the bendy joints would jerk to identity rotation
            # when the switch flips to FK, and the mesh bound at the IK
            # rotation would twist and collapse (waist crush bug).
            cmds.matchTransform(bjnt, fol)

            # Single parentConstraint with 3 targets, mo=True. Maya stores
            # an independent rest-offset per target — at rest, every
            # target's contribution evaluates to the bendy's snapped pose
            # (so blending across the switch doesn't move it). When an
            # FK ctrl rotates, only its delta from the rest pose flows
            # through to the bendy joint.
            pc = cmds.parentConstraint(
                fol, fk_targets[lo_idx], fk_targets[hi_idx],
                bjnt, mo=True,
            )[0]
            aliases = cmds.parentConstraint(pc, q=True,
                                              weightAliasList=True)
            # aliases[0] = follicle (IK), [1] = fk_lo, [2] = fk_hi

            # IK weight: directly from reverse.outputX (= 1 - switch)
            # When switch=0, IK weight=1 (full ribbon). When switch=1, IK=0.

            # FK weights: switch * w_lo, switch * w_hi (so they sum to switch).
            # Use multDoubleLinear nodes for the position blend.
            mult_lo = cmds.createNode("multDoubleLinear",
                                       n=f"C_spine_{i + 1:02d}_FKlo_MULT")
            mult_hi = cmds.createNode("multDoubleLinear",
                                       n=f"C_spine_{i + 1:02d}_FKhi_MULT")
            cmds.setAttr(f"{mult_lo}.input2", w_lo)
            cmds.setAttr(f"{mult_hi}.input2", w_hi)
            cmds.connectAttr(f"{mult_lo}.output", f"{pc}.{aliases[1]}", f=True)
            cmds.connectAttr(f"{mult_hi}.output", f"{pc}.{aliases[2]}", f=True)

            self.bendy_constraints.append({
                "pc": pc, "aliases": aliases,
                "mult_lo": mult_lo, "mult_hi": mult_hi,
                "w_lo": w_lo, "w_hi": w_hi,
            })

    def _build_settings_ctrl(self):
        self.settings_ctrl = create_gear_ctrl(
            "C_spine_SETTINGS_CTRL", size=0.5 * SCALE, color=COLOR_SETTINGS,
        )
        cmds.matchTransform(self.settings_ctrl, self.chest_bind_jnt)
        cmds.move(2.5 * SCALE, 0, 0, self.settings_ctrl, r=True, ws=True)
        offset = make_offset_group(self.settings_ctrl)
        cmds.parent(offset, self.parent_ctrl)
        cmds.parentConstraint(self.chest_bind_jnt, offset, mo=True)

        cmds.addAttr(self.settings_ctrl, ln="bendyVis", at="bool",
                     dv=True, k=True)
        for j in self.spine_bendy_jnts:
            cmds.connectAttr(f"{self.settings_ctrl}.bendyVis",
                             f"{j}.v", f=True)

        # ---- IK / FK switch -------------------------------------------------
        # 0 = pure IK ribbon (mid_ctrl visible, FK ctrls hidden, bendy joints
        #     follow the follicles)
        # 1 = pure FK chain (FK ctrls visible, mid_ctrl hidden, bendy joints
        #     follow the FK_DRV chain interpolated between hip and chest)
        # Intermediate values blend smoothly.
        if self.bendy_constraints and self.ik_fk_reverse:
            cmds.addAttr(self.settings_ctrl, ln="IK_FK_Switch",
                         at="double", min=0.0, max=1.0, dv=0.0, k=True)
            cmds.connectAttr(f"{self.settings_ctrl}.IK_FK_Switch",
                             f"{self.ik_fk_reverse}.inputX", f=True)
            # Wire all per-bendy constraints
            for con in self.bendy_constraints:
                # IK weight (follicle target) = (1 - switch) from reverse
                cmds.connectAttr(f"{self.ik_fk_reverse}.outputX",
                                 f"{con['pc']}.{con['aliases'][0]}", f=True)
                # FK weight inputs: switch -> mult.input1 (mult.input2 holds
                # the static positional weight; output = switch * w)
                cmds.connectAttr(f"{self.settings_ctrl}.IK_FK_Switch",
                                 f"{con['mult_lo']}.input1", f=True)
                cmds.connectAttr(f"{self.settings_ctrl}.IK_FK_Switch",
                                 f"{con['mult_hi']}.input1", f=True)

            # chest_offset's constraint to FK chain top — weight = switch
            if self.chest_fk_constraint:
                pc, alias = self.chest_fk_constraint
                cmds.connectAttr(f"{self.settings_ctrl}.IK_FK_Switch",
                                 f"{pc}.{alias}", f=True)

            # Visibility: clean threshold using condition nodes so the bool
            # vis attr doesn't get fed a float (Maya would complain).
            # mid_ctrl visible while switch < 0.5  (IK mode)
            cond_ik = cmds.createNode("condition", n="C_spine_IK_vis_COND")
            cmds.setAttr(f"{cond_ik}.operation", 4)   # 4 = Less Than
            cmds.setAttr(f"{cond_ik}.secondTerm", 0.5)
            cmds.setAttr(f"{cond_ik}.colorIfTrueR", 1)
            cmds.setAttr(f"{cond_ik}.colorIfFalseR", 0)
            cmds.connectAttr(f"{self.settings_ctrl}.IK_FK_Switch",
                             f"{cond_ik}.firstTerm", f=True)
            # FK ctrls visible while switch >= 0.5  (FK mode)
            cond_fk = cmds.createNode("condition", n="C_spine_FK_vis_COND")
            cmds.setAttr(f"{cond_fk}.operation", 3)   # 3 = Greater Or Equal
            cmds.setAttr(f"{cond_fk}.secondTerm", 0.5)
            cmds.setAttr(f"{cond_fk}.colorIfTrueR", 1)
            cmds.setAttr(f"{cond_fk}.colorIfFalseR", 0)
            cmds.connectAttr(f"{self.settings_ctrl}.IK_FK_Switch",
                             f"{cond_fk}.firstTerm", f=True)

            # mid_ctrl vis is currently driven by nothing; connect via the
            # SHAPE's vis (transform vis was previously locked-hidden via
            # lock_hide_attrs([... "v"]) so we drive shape visibility
            # which Maya allows). Same approach for FK ctrls.
            mid_shape = cmds.listRelatives(self.mid_ctrl, s=True)
            if mid_shape:
                cmds.setAttr(f"{mid_shape[0]}.v", l=False)
                cmds.connectAttr(f"{cond_ik}.outColorR",
                                 f"{mid_shape[0]}.v", f=True)
            for fk in self.fk_ctrls:
                fk_shape = cmds.listRelatives(fk, s=True)
                if fk_shape:
                    cmds.setAttr(f"{fk_shape[0]}.v", l=False)
                    cmds.connectAttr(f"{cond_fk}.outColorR",
                                     f"{fk_shape[0]}.v", f=True)

        lock_hide_attrs(self.settings_ctrl,
                        ["tx", "ty", "tz", "rx", "ry", "rz",
                         "sx", "sy", "sz", "v"])


# =============================================================================
# SECTION 4: NECK + HEAD (FK)
# =============================================================================

class NeckHeadRig(object):

    def __init__(self, positions=None,
                 parent_ctrl=None, parent_jnt=None,
                 ctrl_grp=None, jnt_grp=None):
        self.positions = positions or {
            "neck":     (0, 148.0, 0),
            "head":     (0, 158.0, 0),
            "head_tip": (0, 170.0, 0),
        }
        self.parent_ctrl = parent_ctrl  # chest_ctrl
        self.parent_jnt = parent_jnt    # chest_bind_jnt
        self.ctrl_grp = ctrl_grp
        self.jnt_grp = jnt_grp
        self.bind_jnts = []
        self.fk_ctrls = []

    def build(self):
        # Bind joints (3: neck, head, head_tip)
        cmds.select(cl=True)
        for part in ("neck", "head", "head_tip"):
            j = cmds.joint(n=f"C_{part}_BIND_JNT", p=self.positions[part])
            self.bind_jnts.append(j)
        cmds.joint(self.bind_jnts[0], e=True, oj="xyz", sao="yup",
                   ch=True, zso=True)
        cmds.setAttr(f"{self.bind_jnts[-1]}.jointOrient", 0, 0, 0)
        cmds.parent(self.bind_jnts[0], self.parent_jnt)

        # FK ctrls for neck and head.
        # Use POSITION-ONLY matchTransform so the ctrls have identity world
        # rotation. This is critical: any face sub-ctrls (lids/lips/etc) that
        # parent under head_CTRL will inherit its world rotation. If head_CTRL
        # has the chain's X-up orient baked into rotation, face SDK that moves
        # things in local-Y ends up moving them in a tilted/wrong axis,
        # collapsing X to zero. The parentConstraint with mo=True absorbs
        # the offset cleanly so animator rotation still drives the joint.
        # The circle normal=(0,1,0) gives a horizontal halo around the head.
        for i, part in enumerate(("neck", "head")):
            ctrl = create_circle_ctrl(
                f"C_{part}_CTRL", radius=1.2 * SCALE,
                normal=(0, 1, 0), color=COLOR_CENTER,
            )
            cmds.matchTransform(ctrl, self.bind_jnts[i], pos=True, rot=False)
            offset = make_offset_group(ctrl)
            if i == 0:
                cmds.parent(offset, self.parent_ctrl)
            else:
                cmds.parent(offset, self.fk_ctrls[-1])
            cmds.parentConstraint(ctrl, self.bind_jnts[i], mo=True)
            lock_hide_attrs(ctrl, ["tx", "ty", "tz",
                                   "sx", "sy", "sz", "v"])
            self.fk_ctrls.append(ctrl)


# =============================================================================
# SECTION 5: CLAVICLE (FK)
# =============================================================================

class ClavicleRig(object):

    def __init__(self, side, positions=None,
                 parent_ctrl=None, parent_jnt=None,
                 ctrl_grp=None, jnt_grp=None, label=None):
        # `label` (optional): builds a SECOND instance with unique names, e.g.
        # label="lowerArm" -> L_lowerArm_*. Unset = the classic names, so
        # existing rigs, poses, pickers and space switches are untouched.
        self.side = side
        self.label = label
        self.base = (f"{side}_{label}_clavicle" if label
                     else f"{side}_clavicle")
        mult = 1 if side == "L" else -1
        self.positions = positions or {
            "clavicle":      ( 5.0 * mult, 140.0, 2.0),
            "clavicle_tip":  (22.0 * mult, 138.0, 0.0),
        }
        self.parent_ctrl = parent_ctrl
        self.parent_jnt = parent_jnt
        self.ctrl_grp = ctrl_grp
        self.jnt_grp = jnt_grp
        self.color = COLOR_LEFT if side == "L" else COLOR_RIGHT
        self.bind_jnt = None
        self.tip_jnt = None
        self.ctrl = None

    def build(self):
        cmds.select(cl=True)
        self.bind_jnt = cmds.joint(
            n=f"{self.base}_BIND_JNT",
            p=self.positions["clavicle"],
        )
        self.tip_jnt = cmds.joint(
            n=f"{self.base}_tip_BIND_JNT",
            p=self.positions["clavicle_tip"],
        )
        cmds.joint(self.bind_jnt, e=True, oj="xyz", sao="yup",
                   ch=True, zso=True)
        cmds.setAttr(f"{self.tip_jnt}.jointOrient", 0, 0, 0)
        cmds.parent(self.bind_jnt, self.parent_jnt)

        # Ctrl shape pointing outward
        self.ctrl = create_clavicle_ctrl(
            f"{self.base}_CTRL",
            size=1.5 * SCALE * (1 if self.side == "L" else -1), color=self.color,
        )
        cmds.matchTransform(self.ctrl, self.bind_jnt, pos=True, rot=False)
        offset = make_offset_group(self.ctrl)
        cmds.parent(offset, self.parent_ctrl)
        cmds.parentConstraint(self.ctrl, self.bind_jnt, mo=True)
        lock_hide_attrs(self.ctrl, ["tx", "ty", "tz",
                                    "sx", "sy", "sz", "v"])


# =============================================================================
# SECTION 6: ARM RIG (IK/FK + stretch + bendy)
# =============================================================================

class ArmRig(object):

    def __init__(self, side="L", positions=None, bendy_count=5,
                 parent_ctrl=None, parent_jnt=None,
                 ctrl_grp=None, jnt_grp=None, misc_grp=None,
                 finger_positions=None, label=None):
        assert side in ("L", "R")
        # `label` (optional): builds a SECOND instance with unique names, e.g.
        # label="lowerArm" -> L_lowerArm_*. Unset = the classic names, so
        # existing rigs, poses, pickers and space switches are untouched.
        self.side = side
        self.label = label
        self.prefix = f"{side}_{label}" if label else f"{side}_arm"
        self.bendy_count = bendy_count
        mult = 1 if side == "L" else -1
        self.positions = positions or {
            "shoulder": (22.0 * mult, 138.0,   0.0),
            "elbow":    (48.0 * mult, 138.0,  -4.0),  # Z offset keeps PV behind elbow
            "wrist":    (72.0 * mult, 138.0,   0.0),
        }
        self.parent_ctrl = parent_ctrl   # clavicle_ctrl
        self.parent_jnt = parent_jnt     # clavicle_bind_jnt
        self.ctrl_grp = ctrl_grp
        self.jnt_grp = jnt_grp
        self.misc_grp = misc_grp
        self.color = COLOR_LEFT if side == "L" else COLOR_RIGHT
        self.finger_positions = finger_positions   # dict or None

        self.bind_jnts, self.fk_jnts, self.ik_jnts = [], [], []
        self.fk_ctrls, self.fk_offsets = [], []
        self.ik_ctrl = self.ik_offset = None
        self.pv_ctrl = self.pv_offset = None
        self.settings_ctrl = None
        self.ik_handle = None
        self.bendy_jnts_upper, self.bendy_jnts_lower = [], []
        self.weapon_ctrl = None   # attachment point for weapons / props
        self.finger_jnts  = {}    # finger_name -> [BIND joints incl tip]
        self.finger_ctrls = {}    # finger_name -> [FK ctrls]

    def build(self):
        self._create_joint_chains()
        self._create_fk()
        self._create_ik()
        self._create_settings_ctrl()
        self._create_stretchy_ik()
        self._create_ikfk_blend()
        self._create_visibility_sdk()
        self._create_bendy()
        self._create_weapon_ctrl()
        self._create_fingers()
        cmds.setAttr(f"{self.ik_jnts[0]}.v", 0)
        cmds.setAttr(f"{self.fk_jnts[0]}.v", 0)

    def _create_weapon_ctrl(self):
        """Weapon attachment ctrl at the wrist.

        Parented to the wrist BIND joint so it tracks the actual wrist
        pose in both IK and FK modes. Animators parent-constrain (or
        directly parent) any weapon prop to this ctrl, then use its
        translate/rotate channels to dial in the grip pose without
        touching the prop itself. The ctrl shape is a small diamond so
        it reads clearly in the viewport without obscuring the hand.
        """
        wrist_bind = self.bind_jnts[2]
        ctrl_name = f"{self.prefix}_weapon_CTRL"

        ctrl = create_diamond_ctrl(ctrl_name, size=0.4 * SCALE,
                                    color=COLOR_BEND)
        cmds.matchTransform(ctrl, wrist_bind, pos=True, rot=False)
        offset = make_offset_group(ctrl)
        # Parent the offset under the wrist BIND joint so it follows
        # regardless of IK/FK switch. The BIND joint itself is driven by
        # the IK/FK blend, so anything childed under it inherits the
        # actual runtime wrist pose.
        cmds.parent(offset, wrist_bind)
        # Lock scale only — animator needs translate + rotate to position
        # weapons in the grip. Visibility stays unlocked in case it needs
        # to be toggled from the global root attrs.
        lock_hide_attrs(ctrl, ["sx", "sy", "sz"])
        self.weapon_ctrl = ctrl

    # ------------------------------------------------------------------------
    # Fingers — 5 per hand, parented under the wrist
    # ------------------------------------------------------------------------

    # Per-joint curl weight (degrees per curl unit). Negative because the
    # finger's local +Z is "out of the palm" and curling the fist rotates
    # the finger bone toward -Y world, which is negative around local Z.
    _FINGER_CURL_WEIGHTS = {
        "thumb":  {"meta": -1.5, "prox": -2.5, "dist": -2.0},
        "index":  {"meta": -2.5, "prox": -3.5, "mid": -4.0, "dist": -3.0},
        "middle": {"meta": -2.5, "prox": -3.5, "mid": -4.0, "dist": -3.0},
        "ring":   {"meta": -2.5, "prox": -3.5, "mid": -4.0, "dist": -3.0},
        "pinky":  {"meta": -2.5, "prox": -3.5, "mid": -4.0, "dist": -3.0},
    }
    # Spread weight (degrees per spread unit, applied to META joint Y)
    _FINGER_SPREAD_WEIGHTS = {
        "thumb":   3.0,
        "index":   1.5,
        "middle":  0.0,
        "ring":   -1.5,
        "pinky":  -3.0,
    }
    # BIND joint order per finger (tip is added separately, orient-only).
    _FINGER_JOINT_ORDER = {
        "thumb":  ["meta", "prox", "dist"],
        "index":  ["meta", "prox", "mid", "dist"],
        "middle": ["meta", "prox", "mid", "dist"],
        "ring":   ["meta", "prox", "mid", "dist"],
        "pinky":  ["meta", "prox", "mid", "dist"],
    }

    def _create_fingers(self):
        """Build 5 fingers per hand: thumb, index, middle, ring, pinky.

        Each finger:
          - BIND joint chain (meta → prox → [mid] → dist → tip) under wrist
          - FK ctrl per BIND joint (excluding tip), chained under a
            wrist-follow group that tracks wrist_BIND in both IK and FK
          - AUTO group above each ctrl for SDK input (so animator can
            still manually rotate the ctrl on top of the curl SDK)

        The wrist-follow group is parent-constrained to wrist_BIND_JNT
        instead of being a child of wrist_FK_CTRL — the FK ctrl doesn't
        follow the IK chain in IK mode, so fingers parented under it
        would stay frozen at the rest wrist pose. The follow group is
        under ctrl_grp so fingers don't get hidden by the root's
        jointVis attribute, only by ctrlVis.

        Master attrs on `{prefix}_SETTINGS_CTRL`:
          - `fistCurl`  — curls all 5 fingers proportionally
          - `spread`    — fans the fingers apart at the meta joints
          - `{finger}Curl` — per-finger override (additive with fistCurl)

        SDK wiring (per joint): AUTO.rotateZ = (fistCurl + fingerCurl)
        × joint weight. Spread goes on the META joint AUTO.rotateY.
        """
        if not self.finger_positions:
            return

        wrist_bind = self.bind_jnts[2]
        settings   = self.settings_ctrl

        # Master attributes on the settings ctrl
        if not cmds.attributeQuery("fingerCtrls", node=settings, exists=True):
            cmds.addAttr(settings, ln="fingerCtrls", at="enum",
                         en="---------:", k=True)
            cmds.setAttr(f"{settings}.fingerCtrls", l=True, cb=True, k=False)
        cmds.addAttr(settings, ln="fistCurl", at="double",
                     min=-2, max=10, dv=0, k=True)
        cmds.addAttr(settings, ln="spread", at="double",
                     min=-5, max=10, dv=0, k=True)
        for fname in ("thumb", "index", "middle", "ring", "pinky"):
            cmds.addAttr(settings, ln=f"{fname}Curl", at="double",
                         min=-2, max=10, dv=0, k=True)

        # Wrist-follow group — finger ctrls parent here. Tracks wrist_BIND
        # in both IK and FK modes.
        follow = cmds.group(em=True, n=f"{self.prefix}_wristFingerFollow_GRP")
        cmds.matchTransform(follow, wrist_bind)
        cmds.parent(follow, self.ctrl_grp)
        cmds.parentConstraint(wrist_bind, follow, mo=True)
        self.finger_follow_grp = follow

        for finger_name, positions in self.finger_positions.items():
            if finger_name not in self._FINGER_JOINT_ORDER:
                continue
            slots = self._FINGER_JOINT_ORDER[finger_name]
            missing = [s for s in slots + ["tip"] if s not in positions]
            if missing:
                cmds.warning(f"{self.side} {finger_name} missing joint "
                             f"positions: {missing}; skipping.")
                continue
            self._build_one_finger(finger_name, slots, positions,
                                    wrist_bind, follow, settings)

    def _build_one_finger(self, finger_name, slots, positions,
                           wrist_bind, finger_parent, settings):
        """Build joints + ctrls + SDK for a single finger."""
        side = self.side
        # finger roots are side-built (L_indexProx...), so a labelled arm
        # needs its own token: L_lowerArm_indexProx...
        fside = f"{side}_{self.label}" if self.label else side
        cap = lambda s: s[0].upper() + s[1:]

        # ---- BIND chain (meta → ... → dist → tip) ----
        cmds.select(cl=True)
        bind_jnts = []
        for slot in slots:
            j = cmds.joint(
                n=f"{fside}_{finger_name}{cap(slot)}_BIND_JNT",
                p=positions[slot],
            )
            bind_jnts.append(j)
        tip_jnt = cmds.joint(
            n=f"{fside}_{finger_name}Tip_BIND_JNT",
            p=positions["tip"],
        )
        # Auto-orient the chain: X aims down the bone, Y up.
        cmds.joint(bind_jnts[0], e=True, oj="xyz", sao="yup",
                    ch=True, zso=True)
        cmds.setAttr(f"{tip_jnt}.jointOrient", 0, 0, 0)
        for j in bind_jnts + [tip_jnt]:
            cmds.setAttr(f"{j}.radius", 0.2 * SCALE)
        cmds.parent(bind_jnts[0], wrist_bind)

        # ---- FK ctrls per BIND joint (chained) ----
        # Each ctrl gets an AUTO group above it for SDK input. Hierarchy:
        #     parent (finger_parent on meta, prev ctrl on others)
        #       └── this_offset (positioned at bind)
        #             └── this_AUTO  (SDK writes rotateZ / rotateY here)
        #                   └── this_CTRL  (animator manual rotation)
        # The CTRL parent-constrains the BIND joint. The first finger
        # ctrl parents under the wrist-follow group (which follows
        # wrist_BIND in both IK and FK modes); subsequent ctrls chain
        # under the previous finger ctrl.
        fk_ctrls = []
        prev_ctrl = finger_parent
        for slot, bind in zip(slots, bind_jnts):
            ctrl = create_circle_ctrl(
                f"{fside}_{finger_name}{cap(slot)}_FK_CTRL",
                radius=0.2 * SCALE, normal=(1, 0, 0),
                color=self.color,
            )
            cmds.matchTransform(ctrl, bind)
            offset = make_offset_group(ctrl)
            # Insert AUTO between offset and ctrl
            auto = cmds.group(em=True,
                               n=ctrl.replace("_CTRL", "_AUTO"))
            cmds.matchTransform(auto, bind)
            cmds.parent(auto, offset)
            cmds.parent(ctrl, auto)
            # Chain under previous ctrl
            cmds.parent(offset, prev_ctrl)
            cmds.parentConstraint(ctrl, bind, mo=True)
            lock_hide_attrs(ctrl, ["tx", "ty", "tz",
                                    "sx", "sy", "sz", "v"])
            fk_ctrls.append((slot, ctrl, auto))
            prev_ctrl = ctrl

        # ---- SDK: AUTO.rotateZ = (fistCurl + fingerCurl) * jointWeight ----
        curl_weights = self._FINGER_CURL_WEIGHTS.get(finger_name, {})
        finger_curl_attr = f"{settings}.{finger_name}Curl"
        fist_curl_attr   = f"{settings}.fistCurl"
        for slot, ctrl, auto in fk_ctrls:
            weight = curl_weights.get(slot, 0.0)
            if abs(weight) < 1e-6:
                continue
            sum_node = cmds.createNode(
                "plusMinusAverage",
                n=f"{fside}_{finger_name}{cap(slot)}_curlSum_PMA")
            cmds.connectAttr(fist_curl_attr,   f"{sum_node}.input1D[0]")
            cmds.connectAttr(finger_curl_attr, f"{sum_node}.input1D[1]")
            mul = cmds.createNode(
                "multDoubleLinear",
                n=f"{fside}_{finger_name}{cap(slot)}_curl_MUL")
            cmds.connectAttr(f"{sum_node}.output1D", f"{mul}.input1")
            cmds.setAttr(f"{mul}.input2", weight)
            cmds.connectAttr(f"{mul}.output", f"{auto}.rotateZ")

        # ---- Spread on META joint AUTO.rotateY ----
        spread_weight = self._FINGER_SPREAD_WEIGHTS.get(finger_name, 0.0)
        if abs(spread_weight) > 1e-6 and fk_ctrls:
            meta_auto = fk_ctrls[0][2]
            spread_mul = cmds.createNode(
                "multDoubleLinear",
                n=f"{fside}_{finger_name}_spread_MUL")
            cmds.connectAttr(f"{settings}.spread",
                              f"{spread_mul}.input1")
            cmds.setAttr(f"{spread_mul}.input2", spread_weight)
            cmds.connectAttr(f"{spread_mul}.output",
                              f"{meta_auto}.rotateY")

        self.finger_jnts[finger_name]  = bind_jnts + [tip_jnt]
        self.finger_ctrls[finger_name] = [c[1] for c in fk_ctrls]

    def _create_joint_chains(self):
        cmds.select(cl=True)
        for part in ("shoulder", "elbow", "wrist"):
            j = cmds.joint(n=f"{self.prefix}_{part}_BIND_JNT",
                           p=self.positions[part])
            self.bind_jnts.append(j)
        cmds.joint(self.bind_jnts[0], e=True, oj="xyz", sao="yup",
                   ch=True, zso=True)
        cmds.setAttr(f"{self.bind_jnts[-1]}.jointOrient", 0, 0, 0)
        cmds.parent(self.bind_jnts[0], self.parent_jnt)

        self.fk_jnts = self._duplicate_chain(self.bind_jnts, "FK")
        self.ik_jnts = self._duplicate_chain(self.bind_jnts, "IK")

    def _duplicate_chain(self, source, suffix):
        new_chain = []
        cmds.select(cl=True)
        for jnt in source:
            pos = cmds.xform(jnt, q=True, ws=True, t=True)
            new_name = jnt.replace("_BIND_", f"_{suffix}_")
            new_chain.append(cmds.joint(n=new_name, p=pos))
        cmds.joint(new_chain[0], e=True, oj="xyz", sao="yup",
                   ch=True, zso=True)
        cmds.setAttr(f"{new_chain[-1]}.jointOrient", 0, 0, 0)
        # Parent FK/IK chains to the SAME upstream joint as BIND so that
        # all three chains share an identical parent space. This guarantees
        # equal joint orients across chains so the IK/FK blend works
        # without parent-space distortion.
        cmds.parent(new_chain[0], self.parent_jnt)
        return new_chain

    def _create_fk(self):
        for i, jnt in enumerate(self.fk_jnts):
            ctrl = create_circle_ctrl(
                jnt.replace("_JNT", "_CTRL"), radius=1.2 * SCALE,
                normal=(1, 0, 0), color=self.color,
            )
            cmds.matchTransform(ctrl, jnt)
            offset = make_offset_group(ctrl)
            if i > 0:
                cmds.parent(offset, self.fk_ctrls[i - 1])
            else:
                cmds.parent(offset, self.parent_ctrl)
            cmds.parentConstraint(ctrl, jnt, mo=True)
            lock_hide_attrs(ctrl, ["tx", "ty", "tz",
                                   "sx", "sy", "sz", "v"])
            self.fk_ctrls.append(ctrl)
            self.fk_offsets.append(offset)
        # Upper/lower arm length, so FK can hold what stretchy IK does.
        for i, part in ((1, "shoulder"), (2, "elbow")):
            add_fk_length(self.fk_ctrls[i - 1], self.fk_offsets[i],
                          f"{self.prefix}_{part}_fkLength_MD")

    def _create_ik(self):
        self.ik_handle = cmds.ikHandle(
            sj=self.ik_jnts[0], ee=self.ik_jnts[-1],
            sol="ikRPsolver", n=f"{self.prefix}_ikHandle",
        )[0]
        cmds.setAttr(f"{self.ik_handle}.v", 0)

        self.ik_ctrl = create_cube_ctrl(
            f"{self.prefix}_IK_CTRL", size=1.5 * SCALE, color=COLOR_IK,
        )
        cmds.matchTransform(self.ik_ctrl, self.ik_jnts[-1])
        self.ik_offset = make_offset_group(self.ik_ctrl)
        cmds.parent(self.ik_offset, self.ctrl_grp)
        cmds.parent(self.ik_handle, self.ik_ctrl)
        cmds.orientConstraint(self.ik_ctrl, self.ik_jnts[-1], mo=True)

        pv_pos = get_pole_vector_position(self.ik_jnts[0],
                                          self.ik_jnts[1],
                                          self.ik_jnts[2],
                                          distance=5.0 * SCALE)
        self.pv_ctrl = create_diamond_ctrl(
            f"{self.prefix}_PV_CTRL", size=0.6 * SCALE, color=COLOR_PV,
        )
        cmds.xform(self.pv_ctrl, ws=True, t=pv_pos)
        self.pv_offset = make_offset_group(self.pv_ctrl)
        cmds.parent(self.pv_offset, self.ctrl_grp)
        cmds.poleVectorConstraint(self.pv_ctrl, self.ik_handle)
        # Lives under ctrl_grp, NOT misc_grp — misc is hidden (v=0), which
        # would make the guide line invisible.
        make_pv_guide_line(self.ik_jnts[1], self.pv_ctrl,
                           f"{self.prefix}_PV_LINE", self.ctrl_grp)

        lock_hide_attrs(self.ik_ctrl, ["sx", "sy", "sz", "v"])
        lock_hide_attrs(self.pv_ctrl, ["rx", "ry", "rz",
                                       "sx", "sy", "sz", "v"])

    def _create_settings_ctrl(self):
        self.settings_ctrl = create_gear_ctrl(
            f"{self.prefix}_SETTINGS_CTRL", size=0.5 * SCALE, color=COLOR_SETTINGS,
        )
        cmds.matchTransform(self.settings_ctrl, self.bind_jnts[-1],
                            pos=True, rot=False)
        cmds.move(0, 2.0 * SCALE, 0, self.settings_ctrl, r=True, ws=True)
        offset = make_offset_group(self.settings_ctrl)
        cmds.parent(offset, self.ctrl_grp)
        cmds.parentConstraint(self.bind_jnts[-1], offset, mo=True)

        # Convention: 0 = IK mode (IK chain drives BIND, IK ctrls visible),
        #             1 = FK mode (FK chain drives BIND, FK ctrls visible).
        # Default = 0 (IK) since most animators set IK as the working pose
        # for limb planting; flip to 1 to enter pure FK.
        cmds.addAttr(self.settings_ctrl, ln="ikFkSwitch", at="double",
                     min=0, max=1, dv=0, k=True)
        cmds.addAttr(self.settings_ctrl, ln="autoStretch", at="double",
                     min=0, max=1, dv=1, k=True)
        cmds.addAttr(self.settings_ctrl, ln="bendyVis", at="bool",
                     dv=True, k=True)
        lock_hide_attrs(self.settings_ctrl,
                        ["tx", "ty", "tz", "rx", "ry", "rz",
                         "sx", "sy", "sz", "v"])

    def _create_stretchy_ik(self):
        elbow_t = cmds.getAttr(f"{self.ik_jnts[1]}.translateX")
        wrist_t = cmds.getAttr(f"{self.ik_jnts[2]}.translateX")

        # Measure from an anchor on the shoulder (see make_stretch_anchor).
        # With a clavicle, parent_jnt is its tip, already on the shoulder;
        # a clavicle-less extra arm hangs off the chest, which is not.
        # Not ik_jnts[0]: the IK solver writes its rotate, which would feed
        # back into this distance, into the stretch, and into
        # ik_jnts[1..2].translateX, re-triggering the solver.
        anchor = make_stretch_anchor(self.ik_jnts[0], self.parent_jnt,
                                     f"{self.prefix}_stretch_ANCHOR")

        # Rest length = ACTUAL world distance from the anchor to ik_ctrl at
        # build time, so ratio is exactly 1.0 in the rest pose.
        anchor_pos = cmds.xform(anchor,       q=True, ws=True, t=True)
        ctrl_pos   = cmds.xform(self.ik_ctrl, q=True, ws=True, t=True)
        rest_length = math.sqrt(sum((p - c) ** 2
                                    for p, c in zip(anchor_pos, ctrl_pos)))

        dist = cmds.createNode("distanceBetween",
                               n=f"{self.prefix}_stretch_DIST")
        cmds.connectAttr(f"{anchor}.worldMatrix[0]", f"{dist}.inMatrix1")
        cmds.connectAttr(f"{self.ik_ctrl}.worldMatrix[0]",
                         f"{dist}.inMatrix2")

        # CRITICAL: normalize distance by globalScale before computing
        # the stretch ratio. Without this normalization the stretchy IK
        # double-applies the scale — distance = N * rest_length at scale
        # N, ratio = N, joint.local.tx = N * t, then parent.scale = N
        # also applies → joint world tx = N² * t → limbs explode at
        # globalScale > ~2. With this, ratio stays at 1.0 at rest for
        # any uniform scale, and only rises when the animator moves the
        # IK ctrl beyond its rest distance.
        norm_dist = cmds.createNode("multiplyDivide",
                                     n=f"{self.prefix}_stretch_NORM")
        cmds.setAttr(f"{norm_dist}.operation", 2)  # divide
        cmds.connectAttr(f"{dist}.distance", f"{norm_dist}.input1X")
        if cmds.objExists("C_global_CTRL.globalScale"):
            cmds.connectAttr("C_global_CTRL.globalScale",
                             f"{norm_dist}.input2X")
        else:
            cmds.setAttr(f"{norm_dist}.input2X", 1.0)

        ratio = cmds.createNode("multiplyDivide",
                                n=f"{self.prefix}_stretch_RATIO")
        cmds.setAttr(f"{ratio}.operation", 2)
        cmds.connectAttr(f"{norm_dist}.outputX", f"{ratio}.input1X")
        cmds.setAttr(f"{ratio}.input2X", rest_length)

        cond = cmds.createNode("condition",
                               n=f"{self.prefix}_stretch_COND")
        cmds.setAttr(f"{cond}.operation", 2)
        cmds.connectAttr(f"{ratio}.outputX", f"{cond}.firstTerm")
        cmds.setAttr(f"{cond}.secondTerm", 1.0)
        cmds.connectAttr(f"{ratio}.outputX", f"{cond}.colorIfTrueR")
        cmds.setAttr(f"{cond}.colorIfFalseR", 1.0)

        blend = cmds.createNode("blendTwoAttr",
                                n=f"{self.prefix}_stretch_BLEND")
        cmds.setAttr(f"{blend}.input[0]", 1.0)
        cmds.connectAttr(f"{cond}.outColorR", f"{blend}.input[1]")
        cmds.connectAttr(f"{self.settings_ctrl}.autoStretch",
                         f"{blend}.attributesBlender")

        for i, t in enumerate((elbow_t, wrist_t), start=1):
            m = cmds.createNode("multDoubleLinear",
                                n=f"{self.prefix}_stretch_{i}_MULT")
            cmds.connectAttr(f"{blend}.output", f"{m}.input1")
            cmds.setAttr(f"{m}.input2", t)
            cmds.connectAttr(f"{m}.output",
                             f"{self.ik_jnts[i]}.translateX", f=True)

    def _create_ikfk_blend(self):
        """
        Blend BIND chain between FK and IK using world-space orientConstraint
        (immune to BIND vs FK/IK parent-space differences) plus a targeted
        translateX blend on non-root joints for stretchy-IK propagation.

        We DO NOT blend full translate via pairBlend — doing so corrupts the
        root joint's position whenever the BIND chain and the FK/IK chains
        live under different parents (clavicle vs joints_GRP).
        """
        rev = cmds.createNode("reverse", n=f"{self.prefix}_ikfk_REV")
        cmds.connectAttr(f"{self.settings_ctrl}.ikFkSwitch", f"{rev}.inputX")

        for i, bind in enumerate(self.bind_jnts):
            fk = self.fk_jnts[i]
            ik = self.ik_jnts[i]

            # Rotation: world-space blend via orientConstraint
            oc = cmds.orientConstraint(fk, ik, bind, mo=True)[0]
            cmds.setAttr(f"{oc}.interpType", 2)  # shortest path
            weights = cmds.orientConstraint(oc, q=True, wal=True)
            # weights[0] = FK target, weights[1] = IK target.
            # FK weight = switch directly (1 at FK mode, 0 at IK mode).
            # IK weight = 1 - switch via the reverse node.
            cmds.connectAttr(f"{self.settings_ctrl}.ikFkSwitch",
                             f"{oc}.{weights[0]}")
            cmds.connectAttr(f"{rev}.outputX", f"{oc}.{weights[1]}")

            # TranslateX (bone length) blend — only for non-root joints,
            # so stretchy IK propagates from IK chain to BIND chain.
            if i > 0:
                tx_blend = cmds.createNode("blendTwoAttr",
                                           n=f"{bind}_tx_BLEND")
                cmds.connectAttr(f"{fk}.translateX", f"{tx_blend}.input[0]")
                cmds.connectAttr(f"{ik}.translateX", f"{tx_blend}.input[1]")
                # blender=0 picks input[0]=FK.tx, blender=1 picks input[1]=IK.tx.
                # We want IK when switch=0 and FK when switch=1, so blender
                # is the REVERSE of the switch.
                cmds.connectAttr(f"{rev}.outputX",
                                 f"{tx_blend}.attributesBlender")
                cmds.connectAttr(f"{tx_blend}.output",
                                 f"{bind}.translateX", f=True)

    def _create_visibility_sdk(self):
        # Convention: switch=0 → IK mode (IK + PV ctrls visible, FK hidden)
        #             switch=1 → FK mode (FK chain visible, IK hidden)
        driver = f"{self.settings_ctrl}.ikFkSwitch"
        for ctrl in (self.ik_ctrl, self.pv_ctrl):
            cmds.setAttr(f"{ctrl}.v", l=False)
            # IK visible at switch=0, hidden once switch crosses to FK.
            cmds.setDrivenKeyframe(f"{ctrl}.v", cd=driver, dv=0.0, v=1,
                                   itt="linear", ott="step")
            cmds.setDrivenKeyframe(f"{ctrl}.v", cd=driver, dv=0.999, v=1,
                                   itt="linear", ott="step")
            cmds.setDrivenKeyframe(f"{ctrl}.v", cd=driver, dv=1.0, v=0,
                                   itt="linear", ott="step")
        for ctrl in self.fk_ctrls:
            cmds.setAttr(f"{ctrl}.v", l=False)
            # FK hidden at switch=0, visible once switch leaves IK.
            cmds.setDrivenKeyframe(f"{ctrl}.v", cd=driver, dv=0.0, v=0,
                                   itt="linear", ott="step")
            cmds.setDrivenKeyframe(f"{ctrl}.v", cd=driver, dv=0.001, v=1,
                                   itt="linear", ott="step")
            cmds.setDrivenKeyframe(f"{ctrl}.v", cd=driver, dv=1.0, v=1,
                                   itt="linear", ott="step")

    def _create_bendy(self):
        upper = build_ribbon_bendy(
            self.bind_jnts[0], self.bind_jnts[1], "upper",
            self.prefix, self.bendy_count, self.misc_grp, COLOR_BEND,
        )
        lower = build_ribbon_bendy(
            self.bind_jnts[1], self.bind_jnts[2], "lower",
            self.prefix, self.bendy_count, self.misc_grp, COLOR_BEND,
        )
        self.bendy_jnts_upper = upper["bendy_jnts"]
        self.bendy_jnts_lower = lower["bendy_jnts"]
        for j in self.bendy_jnts_upper + self.bendy_jnts_lower:
            cmds.connectAttr(f"{self.settings_ctrl}.bendyVis",
                             f"{j}.v", f=True)


# =============================================================================
# SECTION 7: LEG RIG (IK/FK + reverse foot + stretch + bendy)
# =============================================================================

class LegRig(object):

    def __init__(self, side="L", positions=None, bendy_count=5,
                 parent_ctrl=None, parent_jnt=None,
                 ctrl_grp=None, jnt_grp=None, misc_grp=None, label=None):
        assert side in ("L", "R")
        # `label` (optional): builds a SECOND instance with unique names, e.g.
        # label="lowerArm" -> L_lowerArm_*. Unset = the classic names, so
        # existing rigs, poses, pickers and space switches are untouched.
        self.side = side
        self.label = label
        self.prefix = f"{side}_{label}" if label else f"{side}_leg"
        self.bendy_count = bendy_count
        mult = 1 if side == "L" else -1
        self.positions = positions or {
            "hip":    (12.0 * mult, 97.0,   0.0),
            "knee":   (12.0 * mult, 52.0,   3.0),  # slight +Z for PV
            "ankle":  (12.0 * mult, 10.0,   0.0),
            "ball":   (12.0 * mult,  3.0,  10.0),
            "toe":    (12.0 * mult,  3.0,  20.0),
            "toeTip": (12.0 * mult,  3.0,  25.0),  # leaf joint at foot tip
        }
        self.parent_ctrl = parent_ctrl   # hip ctrl from spine
        self.parent_jnt = parent_jnt     # pelvis bind jnt
        self.ctrl_grp = ctrl_grp
        self.jnt_grp = jnt_grp
        self.misc_grp = misc_grp
        self.color = COLOR_LEFT if side == "L" else COLOR_RIGHT

        # Storage
        self.bind_jnts, self.fk_jnts, self.ik_jnts = [], [], []
        self.fk_ctrls = []
        self.ik_ctrl = self.pv_ctrl = self.settings_ctrl = None
        self.ik_handle_main = None     # hip -> ankle
        self.ik_handle_ball = None     # ankle -> ball
        self.ik_handle_toe = None      # ball -> toe
        self.foot_locators = {}
        self.bendy_jnts_upper, self.bendy_jnts_lower = [], []
        self.toeTip_jnt = None    # leaf BIND for toe-curl skinning
        self.toeTip_ctrl = None   # small FK ctrl for toe-curl rotation

    def build(self):
        self._create_joint_chains()
        self._create_fk()
        self._create_ik()
        self._create_reverse_foot()
        self._create_settings_ctrl()
        self._create_stretchy_ik()
        self._create_ikfk_blend()
        self._create_visibility_sdk()
        self._create_bendy()
        self._create_toe_tip()
        cmds.setAttr(f"{self.ik_jnts[0]}.v", 0)
        cmds.setAttr(f"{self.fk_jnts[0]}.v", 0)

    def _create_toe_tip(self):
        """Leaf BIND joint at the very front of the foot (toes tip).

        Lives as a child of the existing toe BIND joint. Not part of the
        IK or FK chain — it's a free skinning point for the front of the
        foot mesh. A small FK ctrl drives its rotation so animators can
        curl the toes (e.g. during a tip-toe pose) on top of the foot
        roll IK behavior.

        Skipped silently if the positions dict doesn't include a
        'toeTip' key (e.g. an older positions dict).
        """
        pos = self.positions.get("toeTip")
        if pos is None:
            return
        toe_bind = self.bind_jnts[4]   # toe BIND joint

        # BIND joint — child of toe so it inherits toe's pose and rotates
        # with whatever the toe joint does (IK foot roll, FK chain etc).
        cmds.select(cl=True)
        self.toeTip_jnt = cmds.joint(n=f"{self.prefix}_toeTip_BIND_JNT",
                                       p=pos)
        cmds.setAttr(f"{self.toeTip_jnt}.jointOrient", 0, 0, 0)
        cmds.setAttr(f"{self.toeTip_jnt}.radius", 0.4 * SCALE)
        cmds.parent(self.toeTip_jnt, toe_bind)

        # Small FK ctrl for toe-curl rotation. Parented under the IK
        # ctrl so it stays accessible whether the leg is in IK or FK
        # mode, but the constraint targets the BIND joint so it follows
        # the actual runtime toe pose.
        self.toeTip_ctrl = create_circle_ctrl(
            f"{self.prefix}_toeTip_CTRL", radius=0.35 * SCALE,
            normal=(1, 0, 0), color=self.color,
        )
        cmds.matchTransform(self.toeTip_ctrl, self.toeTip_jnt,
                              pos=True, rot=False)
        offset = make_offset_group(self.toeTip_ctrl)
        cmds.parent(offset, self.ctrl_grp)
        # Constrain ctrl offset to the toe BIND so the ctrl tracks the
        # foot pose; the ctrl's own rotation then layers on as a delta.
        cmds.parentConstraint(toe_bind, offset, mo=True)
        cmds.orientConstraint(self.toeTip_ctrl, self.toeTip_jnt, mo=True)
        lock_hide_attrs(self.toeTip_ctrl, ["tx", "ty", "tz",
                                              "sx", "sy", "sz"])

    def _create_joint_chains(self):
        cmds.select(cl=True)
        for part in ("hip", "knee", "ankle", "ball", "toe"):
            j = cmds.joint(n=f"{self.prefix}_{part}_BIND_JNT",
                           p=self.positions[part])
            self.bind_jnts.append(j)
        cmds.joint(self.bind_jnts[0], e=True, oj="xyz", sao="yup",
                   ch=True, zso=True)
        cmds.setAttr(f"{self.bind_jnts[-1]}.jointOrient", 0, 0, 0)
        cmds.parent(self.bind_jnts[0], self.parent_jnt)

        self.fk_jnts = self._duplicate_chain(self.bind_jnts, "FK")
        self.ik_jnts = self._duplicate_chain(self.bind_jnts, "IK")

    def _duplicate_chain(self, source, suffix):
        new_chain = []
        cmds.select(cl=True)
        for jnt in source:
            pos = cmds.xform(jnt, q=True, ws=True, t=True)
            new_name = jnt.replace("_BIND_", f"_{suffix}_")
            new_chain.append(cmds.joint(n=new_name, p=pos))
        cmds.joint(new_chain[0], e=True, oj="xyz", sao="yup",
                   ch=True, zso=True)
        cmds.setAttr(f"{new_chain[-1]}.jointOrient", 0, 0, 0)
        # Parent FK/IK chains to the SAME upstream joint as BIND so that
        # all three chains share an identical parent space. This guarantees
        # equal joint orients across chains so the IK/FK blend works
        # without parent-space distortion.
        cmds.parent(new_chain[0], self.parent_jnt)
        return new_chain

    def _create_fk(self):
        # Full FK chain: hip, knee, ankle, ball, toe — every joint gets
        # its own ctrl so animators can pose the foot in FK mode too.
        # Without ball/toe FK ctrls, the only way to roll the foot or
        # curl the toes was via the reverse-foot IK attrs — which don't
        # apply when the leg is switched to FK. With this change, an
        # FK-mode tip-toe pose is just hip + knee + ankle + ball + toe
        # rotations on five chained ctrls.
        #
        # Radius scales down for the smaller anatomical joints so the
        # ctrls don't visually overlap each other on a small foot.
        radii = (1.2, 1.2, 1.2, 0.7, 0.5)   # hip, knee, ankle, ball, toe
        for i in range(5):
            jnt = self.fk_jnts[i]
            ctrl = create_circle_ctrl(
                jnt.replace("_JNT", "_CTRL"), radius=radii[i] * SCALE,
                normal=(1, 0, 0), color=self.color,
            )
            cmds.matchTransform(ctrl, jnt)
            offset = make_offset_group(ctrl)
            if i > 0:
                cmds.parent(offset, self.fk_ctrls[i - 1])
            else:
                cmds.parent(offset, self.parent_ctrl)
            cmds.parentConstraint(ctrl, jnt, mo=True)
            lock_hide_attrs(ctrl, ["tx", "ty", "tz",
                                   "sx", "sy", "sz", "v"])
            self.fk_ctrls.append(ctrl)
        # Thigh/shin length: the two segments stretchy IK lengthens.
        for i, part in ((1, "hip"), (2, "knee")):
            offset = cmds.listRelatives(self.fk_ctrls[i], p=True)[0]
            add_fk_length(self.fk_ctrls[i - 1], offset,
                          f"{self.prefix}_{part}_fkLength_MD")

    def _create_ik(self):
        # Three IK handles
        self.ik_handle_main = cmds.ikHandle(
            sj=self.ik_jnts[0], ee=self.ik_jnts[2],
            sol="ikRPsolver", n=f"{self.prefix}_ikHandle_main",
        )[0]
        self.ik_handle_ball = cmds.ikHandle(
            sj=self.ik_jnts[2], ee=self.ik_jnts[3],
            sol="ikSCsolver", n=f"{self.prefix}_ikHandle_ball",
        )[0]
        self.ik_handle_toe = cmds.ikHandle(
            sj=self.ik_jnts[3], ee=self.ik_jnts[4],
            sol="ikSCsolver", n=f"{self.prefix}_ikHandle_toe",
        )[0]
        for h in (self.ik_handle_main, self.ik_handle_ball,
                  self.ik_handle_toe):
            cmds.setAttr(f"{h}.v", 0)

        # IK ctrl (boot at ankle ground)
        ankle_pos = self.positions["ankle"]
        ctrl_pos = (ankle_pos[0], 0.0, self.positions["ball"][2] * 0.3)
        self.ik_ctrl = create_foot_ctrl(
            f"{self.prefix}_IK_CTRL", size=1.5 * SCALE, color=COLOR_IK,
        )
        cmds.xform(self.ik_ctrl, ws=True, t=ctrl_pos)
        offset = make_offset_group(self.ik_ctrl)
        cmds.parent(offset, self.ctrl_grp)

        # Pole vector
        pv_pos = get_pole_vector_position(self.ik_jnts[0],
                                          self.ik_jnts[1],
                                          self.ik_jnts[2],
                                          distance=6.0 * SCALE)
        self.pv_ctrl = create_diamond_ctrl(
            f"{self.prefix}_PV_CTRL", size=0.6 * SCALE, color=COLOR_PV,
        )
        cmds.xform(self.pv_ctrl, ws=True, t=pv_pos)
        pv_offset = make_offset_group(self.pv_ctrl)
        cmds.parent(pv_offset, self.ctrl_grp)
        cmds.poleVectorConstraint(self.pv_ctrl, self.ik_handle_main)
        # ctrl_grp, not the hidden misc_grp (see ArmRig note).
        make_pv_guide_line(self.ik_jnts[1], self.pv_ctrl,
                           f"{self.prefix}_PV_LINE", self.ctrl_grp)

        lock_hide_attrs(self.ik_ctrl, ["sx", "sy", "sz", "v"])
        lock_hide_attrs(self.pv_ctrl, ["rx", "ry", "rz",
                                       "sx", "sy", "sz", "v"])

    def _create_reverse_foot(self):
        """
        Locator hierarchy under IK ctrl:
            heel_LOC
              └── toeTip_LOC
                    └── ball_LOC
                          ├── ankle_LOC   (parents main IK handle)
                          ├── ballIK_LOC  (parents ball IK handle)
                          └── toe_LOC     (parents toe IK handle)
        """
        ankle_pos = self.positions["ankle"]
        ball_pos  = self.positions["ball"]
        toe_pos   = self.positions["toe"]
        heel_pos  = (ankle_pos[0], 0.0, ankle_pos[2] - 5.0 * SCALE)
        toetip_pos = (toe_pos[0], 0.0, toe_pos[2] + 3.0 * SCALE)

        # Make locators
        def mk_loc(name, pos):
            loc = cmds.spaceLocator(n=name)[0]
            cmds.xform(loc, ws=True, t=pos)
            cmds.setAttr(f"{loc}Shape.visibility", 0)
            return loc

        heel = mk_loc(f"{self.prefix}_heel_LOC", heel_pos)
        toetip = mk_loc(f"{self.prefix}_toeTip_LOC", toetip_pos)
        ball = mk_loc(f"{self.prefix}_ball_LOC", ball_pos)
        ankle = mk_loc(f"{self.prefix}_ankle_LOC", ankle_pos)
        ballik = mk_loc(f"{self.prefix}_ballIK_LOC", ball_pos)
        toe = mk_loc(f"{self.prefix}_toe_LOC", toe_pos)

        self.foot_locators = {"heel": heel, "toeTip": toetip, "ball": ball,
                              "ankle": ankle, "ballIK": ballik, "toe": toe}

        cmds.parent(toetip, heel)
        cmds.parent(ball, toetip)
        cmds.parent(ankle, ball)
        cmds.parent(ballik, ball)
        cmds.parent(toe, ball)

        cmds.parent(self.ik_handle_main, ankle)
        cmds.parent(self.ik_handle_ball, ballik)
        cmds.parent(self.ik_handle_toe, toe)

        cmds.parent(heel, self.ik_ctrl)

        # Add foot roll attributes
        cmds.addAttr(self.ik_ctrl, ln="heelRoll", at="double", dv=0, k=True)
        cmds.addAttr(self.ik_ctrl, ln="ballRoll", at="double", dv=0, k=True)
        cmds.addAttr(self.ik_ctrl, ln="toeRoll",  at="double", dv=0, k=True)
        cmds.addAttr(self.ik_ctrl, ln="toeWiggle", at="double", dv=0, k=True)
        # toeCurl — SDK-driven slider (0..10) that curls the toe joint via
        # the reverse foot. Composed with toeWiggle (fine 1:1 degree
        # control) through a plusMinusAverage so both attrs can drive
        # the same toe_LOC.rotateX without conflict. Default curve:
        #   toeCurl=0   → 0°    (rest)
        #   toeCurl=10  → -45°  (full curl down)
        #   toeCurl=-10 → +30°  (toes flex up)
        # Riggers can re-tune the curve in the Graph Editor on the
        # animCurveUU node (look for the SDK output attr below).
        cmds.addAttr(self.ik_ctrl, ln="toeCurl", at="double",
                     min=-10, max=10, dv=0, k=True)
        cmds.addAttr(self.ik_ctrl, ln="footBank", at="double", dv=0, k=True)

        # Hidden output attr — SDK writes here, then summed with toeWiggle
        cmds.addAttr(self.ik_ctrl, ln="toeCurlSDKOut", at="double", dv=0)
        cmds.setAttr(f"{self.ik_ctrl}.toeCurlSDKOut", k=False, cb=False)

        # Connect rolls (X-axis rotations on locators)
        cmds.connectAttr(f"{self.ik_ctrl}.heelRoll", f"{heel}.rotateX")
        cmds.connectAttr(f"{self.ik_ctrl}.ballRoll", f"{ball}.rotateX")
        cmds.connectAttr(f"{self.ik_ctrl}.toeRoll",  f"{toetip}.rotateX")

        # toeCurl SDK keyframes — drives the hidden toeCurlSDKOut attr,
        # which feeds into the pma below alongside the direct toeWiggle
        cmds.setDrivenKeyframe(f"{self.ik_ctrl}.toeCurlSDKOut",
                                cd=f"{self.ik_ctrl}.toeCurl",
                                dv=-10.0, v=30.0,
                                itt="auto", ott="auto")
        cmds.setDrivenKeyframe(f"{self.ik_ctrl}.toeCurlSDKOut",
                                cd=f"{self.ik_ctrl}.toeCurl",
                                dv=0.0, v=0.0,
                                itt="auto", ott="auto")
        cmds.setDrivenKeyframe(f"{self.ik_ctrl}.toeCurlSDKOut",
                                cd=f"{self.ik_ctrl}.toeCurl",
                                dv=10.0, v=-45.0,
                                itt="auto", ott="auto")

        # Sum toeWiggle (direct) + toeCurl SDK output → toe IK joint rotateZ.
        #
        # The pivot question: rotating the toe_LOC (or any locator at the
        # toe position) doesn't move the IK handle because the handle sits
        # AT that pivot, so the SC solver does nothing — the original
        # toeWiggle was non-functional. Rotating a pivot at the BALL
        # makes the IK solver work but pivots the curl at the ball
        # (whole front of foot tilts) which isn't anatomical toe curl.
        #
        # Correct: directly write to the toe IK joint's local rotateZ.
        # With the chain's auto-orient (X aims down the bone, Y up), the
        # joint's local Z is the perpendicular curl axis. The BIND chain
        # follows via the IK/FK orient constraint, so the toe BIND
        # rotates locally and the toeTip BIND (child of toe BIND)
        # orbits around the TOE position — true toe-joint-pivoted curl.
        toe_pma = cmds.createNode("plusMinusAverage",
                                    n=f"{self.prefix}_toeRot_PMA")
        cmds.connectAttr(f"{self.ik_ctrl}.toeWiggle",
                         f"{toe_pma}.input1D[0]")
        cmds.connectAttr(f"{self.ik_ctrl}.toeCurlSDKOut",
                         f"{toe_pma}.input1D[1]")
        cmds.connectAttr(f"{toe_pma}.output1D",
                         f"{self.ik_jnts[4]}.rotateZ")

        # Bank: positive bank -> outside of foot lifts (different per side)
        # Use heel.rotateZ for positive bank, toetip.rotateZ for negative
        # Simple: bank to heel.rotateZ scaled by side
        bank_mult = cmds.createNode(
            "multDoubleLinear", n=f"{self.prefix}_bank_MULT")
        cmds.connectAttr(f"{self.ik_ctrl}.footBank", f"{bank_mult}.input1")
        cmds.setAttr(f"{bank_mult}.input2",
                     1.0 if self.side == "L" else -1.0)
        cmds.connectAttr(f"{bank_mult}.output", f"{heel}.rotateZ")

        # ===== Smart "roll" attribute (Advanced Skeleton style) =====
        # One attribute that automates the heel → ball → toe progression:
        #   roll  <  0                       → heel rocks back  (heel_LOC)
        #   0    ≤ roll ≤ rollStartAngle     → ball roll        (ball_LOC)
        #   roll >  rollStartAngle           → toe roll  on top  (toetip_LOC)
        # rollEndAngle is informational — it documents the angle at which
        # full toe roll is expected. The contributions are SUMMED with the
        # existing heelRoll/ballRoll/toeRoll attrs via PMAs, so animators
        # can use either the smart `roll` slider or the individual rolls
        # (or both, additively) — nothing breaks if you're used to the
        # old workflow.
        cmds.addAttr(self.ik_ctrl, ln="roll", at="double", dv=0, k=True)
        cmds.addAttr(self.ik_ctrl, ln="rollStartAngle",
                     at="double", min=0, dv=30, k=True)
        cmds.addAttr(self.ik_ctrl, ln="rollEndAngle",
                     at="double", min=0, dv=60, k=True)

        # ----- contributions from `roll` -----
        # heel contribution = max(0, -roll)
        neg_roll = cmds.createNode("multDoubleLinear",
                                    n=f"{self.prefix}_negRoll_MULT")
        cmds.connectAttr(f"{self.ik_ctrl}.roll", f"{neg_roll}.input1")
        cmds.setAttr(f"{neg_roll}.input2", -1.0)
        heel_clamp = cmds.createNode("clamp",
                                      n=f"{self.prefix}_heelFromRoll_CLAMP")
        cmds.connectAttr(f"{neg_roll}.output", f"{heel_clamp}.inputR")
        cmds.setAttr(f"{heel_clamp}.minR", 0.0)
        cmds.setAttr(f"{heel_clamp}.maxR", 9999.0)

        # ball contribution = clamp(roll, 0, rollStartAngle)
        ball_clamp = cmds.createNode("clamp",
                                      n=f"{self.prefix}_ballFromRoll_CLAMP")
        cmds.connectAttr(f"{self.ik_ctrl}.roll", f"{ball_clamp}.inputR")
        cmds.setAttr(f"{ball_clamp}.minR", 0.0)
        cmds.connectAttr(f"{self.ik_ctrl}.rollStartAngle",
                         f"{ball_clamp}.maxR")

        # toe contribution = max(0, roll - rollStartAngle)
        toe_sub = cmds.createNode("plusMinusAverage",
                                   n=f"{self.prefix}_toeFromRoll_SUB")
        cmds.setAttr(f"{toe_sub}.operation", 2)  # subtract
        cmds.connectAttr(f"{self.ik_ctrl}.roll", f"{toe_sub}.input1D[0]")
        cmds.connectAttr(f"{self.ik_ctrl}.rollStartAngle",
                         f"{toe_sub}.input1D[1]")
        toe_clamp = cmds.createNode("clamp",
                                     n=f"{self.prefix}_toeFromRoll_CLAMP")
        cmds.connectAttr(f"{toe_sub}.output1D", f"{toe_clamp}.inputR")
        cmds.setAttr(f"{toe_clamp}.minR", 0.0)
        cmds.setAttr(f"{toe_clamp}.maxR", 9999.0)

        # ----- replace direct connections with PMA-summed versions -----
        # heel:   heelRoll attr + heel-from-roll  →  heel_LOC.rotateX
        cmds.disconnectAttr(f"{self.ik_ctrl}.heelRoll",
                            f"{heel}.rotateX")
        heel_sum = cmds.createNode("plusMinusAverage",
                                    n=f"{self.prefix}_heelSum_PMA")
        cmds.connectAttr(f"{self.ik_ctrl}.heelRoll",
                         f"{heel_sum}.input1D[0]")
        cmds.connectAttr(f"{heel_clamp}.outputR",
                         f"{heel_sum}.input1D[1]")
        cmds.connectAttr(f"{heel_sum}.output1D", f"{heel}.rotateX")

        # ball:   ballRoll attr + ball-from-roll  →  ball_LOC.rotateX
        cmds.disconnectAttr(f"{self.ik_ctrl}.ballRoll",
                            f"{ball}.rotateX")
        ball_sum = cmds.createNode("plusMinusAverage",
                                    n=f"{self.prefix}_ballSum_PMA")
        cmds.connectAttr(f"{self.ik_ctrl}.ballRoll",
                         f"{ball_sum}.input1D[0]")
        cmds.connectAttr(f"{ball_clamp}.outputR",
                         f"{ball_sum}.input1D[1]")
        cmds.connectAttr(f"{ball_sum}.output1D", f"{ball}.rotateX")

        # toe:    toeRoll attr + toe-from-roll  →  toetip_LOC.rotateX
        cmds.disconnectAttr(f"{self.ik_ctrl}.toeRoll",
                            f"{toetip}.rotateX")
        toe_sum = cmds.createNode("plusMinusAverage",
                                   n=f"{self.prefix}_toeSum_PMA")
        cmds.connectAttr(f"{self.ik_ctrl}.toeRoll",
                         f"{toe_sum}.input1D[0]")
        cmds.connectAttr(f"{toe_clamp}.outputR",
                         f"{toe_sum}.input1D[1]")
        cmds.connectAttr(f"{toe_sum}.output1D", f"{toetip}.rotateX")

    def _create_settings_ctrl(self):
        self.settings_ctrl = create_gear_ctrl(
            f"{self.prefix}_SETTINGS_CTRL", size=0.5 * SCALE, color=COLOR_SETTINGS,
        )
        cmds.matchTransform(self.settings_ctrl, self.bind_jnts[2],
                            pos=True, rot=False)
        cmds.move(2.5 * SCALE * (1 if self.side == "L" else -1), 0, 0,
                  self.settings_ctrl, r=True, ws=True)
        offset = make_offset_group(self.settings_ctrl)
        cmds.parent(offset, self.ctrl_grp)
        cmds.parentConstraint(self.bind_jnts[2], offset, mo=True)

        # Convention: 0 = IK mode (IK chain drives BIND, IK ctrls visible),
        #             1 = FK mode (FK chain drives BIND, FK ctrls visible).
        # Default = 0 (IK) since most animators set IK as the working pose
        # for limb planting; flip to 1 to enter pure FK.
        cmds.addAttr(self.settings_ctrl, ln="ikFkSwitch", at="double",
                     min=0, max=1, dv=0, k=True)
        cmds.addAttr(self.settings_ctrl, ln="autoStretch", at="double",
                     min=0, max=1, dv=1, k=True)
        cmds.addAttr(self.settings_ctrl, ln="bendyVis", at="bool",
                     dv=True, k=True)
        lock_hide_attrs(self.settings_ctrl,
                        ["tx", "ty", "tz", "rx", "ry", "rz",
                         "sx", "sy", "sz", "v"])

    def _create_stretchy_ik(self):
        knee_t = cmds.getAttr(f"{self.ik_jnts[1]}.translateX")
        ankle_t = cmds.getAttr(f"{self.ik_jnts[2]}.translateX")

        # Measure from an anchor on the hip, not from parent_jnt (pelvis):
        # the pelvis sits on the spine centreline, so pelvis -> ankle grows
        # by a different ratio than hip -> ankle and the IK ankle missed its
        # locator (see make_stretch_anchor). Not ik_jnts[0] either: the IK
        # solver writes its rotate, which would feed back into this
        # distance, into the stretch, and into ik_jnts[1..2].translateX,
        # re-triggering the solver.
        ankle_loc = self.foot_locators["ankle"]
        anchor = make_stretch_anchor(self.ik_jnts[0], self.parent_jnt,
                                     f"{self.prefix}_stretch_ANCHOR")

        # Rest length = ACTUAL world distance from the hip anchor to the
        # ankle locator at build time, so ratio is exactly 1.0 at rest.
        anchor_pos = cmds.xform(anchor,    q=True, ws=True, t=True)
        ankle_pos  = cmds.xform(ankle_loc, q=True, ws=True, t=True)
        rest_length = math.sqrt(sum((p - a) ** 2
                                    for p, a in zip(anchor_pos, ankle_pos)))

        dist = cmds.createNode("distanceBetween",
                               n=f"{self.prefix}_stretch_DIST")
        cmds.connectAttr(f"{anchor}.worldMatrix[0]", f"{dist}.inMatrix1")
        cmds.connectAttr(f"{ankle_loc}.worldMatrix[0]",
                         f"{dist}.inMatrix2")

        # CRITICAL: normalize distance by globalScale before computing
        # the stretch ratio. Without this normalization the stretchy IK
        # double-applies the scale — distance = N * rest_length at scale
        # N, ratio = N, joint.local.tx = N * t, then parent.scale = N
        # also applies → joint world tx = N² * t → limbs explode at
        # globalScale > ~2. With this, ratio stays at 1.0 at rest for
        # any uniform scale, and only rises when the animator moves the
        # IK ctrl beyond its rest distance.
        norm_dist = cmds.createNode("multiplyDivide",
                                     n=f"{self.prefix}_stretch_NORM")
        cmds.setAttr(f"{norm_dist}.operation", 2)  # divide
        cmds.connectAttr(f"{dist}.distance", f"{norm_dist}.input1X")
        if cmds.objExists("C_global_CTRL.globalScale"):
            cmds.connectAttr("C_global_CTRL.globalScale",
                             f"{norm_dist}.input2X")
        else:
            cmds.setAttr(f"{norm_dist}.input2X", 1.0)

        ratio = cmds.createNode("multiplyDivide",
                                n=f"{self.prefix}_stretch_RATIO")
        cmds.setAttr(f"{ratio}.operation", 2)
        cmds.connectAttr(f"{norm_dist}.outputX", f"{ratio}.input1X")
        cmds.setAttr(f"{ratio}.input2X", rest_length)

        cond = cmds.createNode("condition",
                               n=f"{self.prefix}_stretch_COND")
        cmds.setAttr(f"{cond}.operation", 2)
        cmds.connectAttr(f"{ratio}.outputX", f"{cond}.firstTerm")
        cmds.setAttr(f"{cond}.secondTerm", 1.0)
        cmds.connectAttr(f"{ratio}.outputX", f"{cond}.colorIfTrueR")
        cmds.setAttr(f"{cond}.colorIfFalseR", 1.0)

        blend = cmds.createNode("blendTwoAttr",
                                n=f"{self.prefix}_stretch_BLEND")
        cmds.setAttr(f"{blend}.input[0]", 1.0)
        cmds.connectAttr(f"{cond}.outColorR", f"{blend}.input[1]")
        cmds.connectAttr(f"{self.settings_ctrl}.autoStretch",
                         f"{blend}.attributesBlender")

        for i, t in ((1, knee_t), (2, ankle_t)):
            m = cmds.createNode(
                "multDoubleLinear",
                n=f"{self.prefix}_stretch_{i}_MULT",
            )
            cmds.connectAttr(f"{blend}.output", f"{m}.input1")
            cmds.setAttr(f"{m}.input2", t)
            cmds.connectAttr(f"{m}.output",
                             f"{self.ik_jnts[i]}.translateX", f=True)

    def _create_ikfk_blend(self):
        """
        World-space rotation blending via orientConstraint + targeted
        translateX blend on non-root joints. See ArmRig._create_ikfk_blend
        for the rationale (pairBlend on full translate displaces the root
        joint when BIND and FK/IK chains have different parents).
        """
        rev = cmds.createNode("reverse", n=f"{self.prefix}_ikfk_REV")
        cmds.connectAttr(f"{self.settings_ctrl}.ikFkSwitch", f"{rev}.inputX")

        for i, bind in enumerate(self.bind_jnts):
            fk = self.fk_jnts[i]
            ik = self.ik_jnts[i]

            oc = cmds.orientConstraint(fk, ik, bind, mo=True)[0]
            cmds.setAttr(f"{oc}.interpType", 2)
            weights = cmds.orientConstraint(oc, q=True, wal=True)
            # weights[0] = FK target, weights[1] = IK target.
            # FK weight = switch directly (1 at FK mode, 0 at IK mode).
            # IK weight = 1 - switch via the reverse node.
            cmds.connectAttr(f"{self.settings_ctrl}.ikFkSwitch",
                             f"{oc}.{weights[0]}")
            cmds.connectAttr(f"{rev}.outputX", f"{oc}.{weights[1]}")

            if i > 0:
                tx_blend = cmds.createNode("blendTwoAttr",
                                           n=f"{bind}_tx_BLEND")
                cmds.connectAttr(f"{fk}.translateX", f"{tx_blend}.input[0]")
                cmds.connectAttr(f"{ik}.translateX", f"{tx_blend}.input[1]")
                # blender=0 picks input[0]=FK.tx, blender=1 picks input[1]=IK.tx.
                # We want IK when switch=0 and FK when switch=1, so blender
                # is the REVERSE of the switch.
                cmds.connectAttr(f"{rev}.outputX",
                                 f"{tx_blend}.attributesBlender")
                cmds.connectAttr(f"{tx_blend}.output",
                                 f"{bind}.translateX", f=True)

    def _create_visibility_sdk(self):
        # Convention: switch=0 → IK mode (IK + PV ctrls visible, FK hidden)
        #             switch=1 → FK mode (FK chain visible, IK hidden)
        driver = f"{self.settings_ctrl}.ikFkSwitch"
        for ctrl in (self.ik_ctrl, self.pv_ctrl):
            cmds.setAttr(f"{ctrl}.v", l=False)
            # IK visible at switch=0, hidden once switch crosses to FK.
            cmds.setDrivenKeyframe(f"{ctrl}.v", cd=driver, dv=0.0, v=1,
                                   itt="linear", ott="step")
            cmds.setDrivenKeyframe(f"{ctrl}.v", cd=driver, dv=0.999, v=1,
                                   itt="linear", ott="step")
            cmds.setDrivenKeyframe(f"{ctrl}.v", cd=driver, dv=1.0, v=0,
                                   itt="linear", ott="step")
        for ctrl in self.fk_ctrls:
            cmds.setAttr(f"{ctrl}.v", l=False)
            # FK hidden at switch=0, visible once switch leaves IK.
            cmds.setDrivenKeyframe(f"{ctrl}.v", cd=driver, dv=0.0, v=0,
                                   itt="linear", ott="step")
            cmds.setDrivenKeyframe(f"{ctrl}.v", cd=driver, dv=0.001, v=1,
                                   itt="linear", ott="step")
            cmds.setDrivenKeyframe(f"{ctrl}.v", cd=driver, dv=1.0, v=1,
                                   itt="linear", ott="step")

    def _create_bendy(self):
        upper = build_ribbon_bendy(
            self.bind_jnts[0], self.bind_jnts[1], "upper",
            self.prefix, self.bendy_count, self.misc_grp, COLOR_BEND,
        )
        lower = build_ribbon_bendy(
            self.bind_jnts[1], self.bind_jnts[2], "lower",
            self.prefix, self.bendy_count, self.misc_grp, COLOR_BEND,
        )
        self.bendy_jnts_upper = upper["bendy_jnts"]
        self.bendy_jnts_lower = lower["bendy_jnts"]
        for j in self.bendy_jnts_upper + self.bendy_jnts_lower:
            cmds.connectAttr(f"{self.settings_ctrl}.bendyVis",
                             f"{j}.v", f=True)


# =============================================================================
# SECTION 7A: TAIL RIG (IK spline + FK chain + curl/wag SDK)
# =============================================================================

class TailRig(object):
    """Tail rig with switchable IK spline and FK chain.

    Structure:
        Three parallel joint chains (BIND, FK, IK), all under jnt_grp,
        all parented to the host (pelvis by default). BIND is what the
        mesh skins to. FK chain follows the FK ctrls. IK chain follows
        an ikSplineSolver running through a NURBS curve that's deformed
        by 3 cluster ctrls (root / mid / tip).

    Settings ctrl attrs:
        ikFkSwitch  — 0 = IK spline, 1 = FK chain (matches arm/leg convention)
        curl        — linear progression Z rotation per joint (weights
                      ramp from root → tip so the tail curls smoothly,
                      most curl at the tip)
        wag         — linear progression Y rotation per joint (same ramp;
                      animator keyframes wag back and forth for true motion)

    Curl / wag drive an AUTO group above each FK ctrl, so animators can
    still manually rotate any FK ctrl on top of the SDK. The SDK only
    visibly affects the BIND chain when in FK mode (BIND follows FK).
    In IK mode, the spline ctrls do all the work and curl/wag silently
    drive the (hidden) FK ctrls in case the animator wants to switch.
    """

    def __init__(self, positions=None, joint_count=7,
                 parent_ctrl=None, parent_jnt=None,
                 ctrl_grp=None, jnt_grp=None, misc_grp=None, label=None,
                 side="C", controls="fkik", ik_follow=False):
        # `label` (optional): builds a SECOND instance with unique names, e.g.
        # label="tail2" -> C_tail2_*. Unset = the classic names, so
        # existing rigs, poses, pickers and space switches are untouched.
        # Only NODE NAMES take the label. Position keys stay tail_01..NN
        # because they come straight from the guide system.
        self.name = label or "tail"
        # `side` + `controls` turn the tail into a generic custom chain
        # (capes, antennae, tentacles): side "L"/"R" names it L_/R_,
        # controls "fk" / "ik" lock the IK/FK switch to that one mode and
        # hide the other set of ctrls. Defaults = the classic C_ tail.
        if side not in ("C", "L", "R"):
            raise ValueError("TailRig side must be C, L or R, got %r" % side)
        if controls not in ("fkik", "fk", "ik"):
            raise ValueError("TailRig controls must be fkik, fk or ik, got %r"
                             % controls)
        self.side = side
        self.controls = controls
        # ik_follow: IK spline ctrls ride along with parent_ctrl (a chain on
        # the head turns with the head). False = the classic tail, whose IK
        # ctrls live in world space under ctrl_grp.
        self.ik_follow = ik_follow
        self.joint_count = joint_count
        # Default positions: arc backward and downward from pelvis area.
        if positions is None:
            positions = {}
            for i in range(joint_count):
                t = i / float(joint_count - 1) if joint_count > 1 else 0
                positions[f"tail_{i + 1:02d}"] = (
                    0.0,
                    98.0 - 18.0 * t,
                    -5.0 - 28.0 * t,
                )
            positions["tip"] = (0.0, 75.0, -35.0)
        self.positions = positions
        self.parent_ctrl = parent_ctrl   # cog_ctrl or hip_ctrl
        self.parent_jnt = parent_jnt     # pelvis_BIND or root_BIND
        self.ctrl_grp = ctrl_grp
        self.jnt_grp = jnt_grp
        self.misc_grp = misc_grp

        # Outputs
        self.bind_jnts = []
        self.fk_jnts   = []
        self.ik_jnts   = []
        self.fk_ctrls  = []          # list of FK ctrls (excludes tip)
        self.fk_autos  = []          # parallel to fk_ctrls — SDK target
        self.cluster_ctrls = []      # 3 spline cluster ctrls
        self.settings_ctrl = None
        self.curve = None
        self.ik_handle = None

    # -----------------------------------------------------------------------

    def build(self):
        self._create_joint_chains()
        self._create_fk()
        self._create_ik_spline()
        self._create_settings_ctrl()
        self._create_ikfk_blend()
        self._create_visibility_sdk()
        self._create_curl_wag_sdk()
        self._apply_control_mode()
        # Hide the FK + IK driver chains; BIND chain stays visible.
        cmds.setAttr(f"{self.fk_jnts[0]}.v", 0)
        cmds.setAttr(f"{self.ik_jnts[0]}.v", 0)

    # -----------------------------------------------------------------------

    def _ordered_slots(self):
        """Return BIND-chain joint slot names in chain order, excluding tip."""
        return [f"tail_{i + 1:02d}" for i in range(self.joint_count)]

    def _create_joint_chains(self):
        slots = self._ordered_slots()

        # ---- BIND chain ----
        cmds.select(cl=True)
        for slot in slots:
            j = cmds.joint(n=f"{self.side}_{self._nm(slot)}_BIND_JNT", p=self.positions[slot])
            self.bind_jnts.append(j)
        tip_jnt = cmds.joint(n=f"{self.side}_{self.name}Tip_BIND_JNT",
                              p=self.positions["tip"])
        self.bind_jnts.append(tip_jnt)
        # Auto-orient: X aims down the chain (tail forward direction),
        # Y up. zso for sister joint orient on the chain root.
        cmds.joint(self.bind_jnts[0], e=True, oj="xyz", sao="yup",
                    ch=True, zso=True)
        cmds.setAttr(f"{tip_jnt}.jointOrient", 0, 0, 0)
        for j in self.bind_jnts:
            cmds.setAttr(f"{j}.radius", 0.5 * SCALE)
        cmds.parent(self.bind_jnts[0], self.parent_jnt)

        # ---- FK + IK driver chains ----
        self.fk_jnts = self._duplicate_chain(self.bind_jnts, "FK")
        self.ik_jnts = self._duplicate_chain(self.bind_jnts, "IK")

    def _nm(self, slot):
        """Position key -> node-name token: tail_03 -> tail_03 (classic) or
        tail2_03 when labelled."""
        return self.name + slot[len("tail"):]

    def _duplicate_chain(self, source, suffix):
        new_chain = []
        cmds.select(cl=True)
        for jnt in source:
            pos = cmds.xform(jnt, q=True, ws=True, t=True)
            new_name = jnt.replace("_BIND_", f"_{suffix}_")
            new_chain.append(cmds.joint(n=new_name, p=pos))
        cmds.joint(new_chain[0], e=True, oj="xyz", sao="yup",
                    ch=True, zso=True)
        cmds.setAttr(f"{new_chain[-1]}.jointOrient", 0, 0, 0)
        cmds.parent(new_chain[0], self.parent_jnt)
        return new_chain

    def _create_fk(self):
        """FK ctrl per BIND joint (excluding tip). Each ctrl has an AUTO
        group above it that the curl/wag SDK writes to."""
        slots = self._ordered_slots()
        prev_ctrl = self.parent_ctrl
        for i, slot in enumerate(slots):
            fk_jnt = self.fk_jnts[i]
            ctrl_name = f"{self.side}_{self._nm(slot)}_FK_CTRL"
            ctrl = create_circle_ctrl(
                ctrl_name, radius=0.7 * SCALE,
                normal=(1, 0, 0), color={"L": COLOR_LEFT,
                                         "R": COLOR_RIGHT}.get(self.side,
                                                               COLOR_CENTER),
            )
            cmds.matchTransform(ctrl, fk_jnt)
            offset = make_offset_group(ctrl)
            # Insert AUTO group between offset and ctrl
            auto = cmds.group(em=True, n=ctrl_name.replace("_CTRL", "_AUTO"))
            cmds.matchTransform(auto, fk_jnt)
            cmds.parent(auto, offset)
            cmds.parent(ctrl, auto)
            cmds.parent(offset, prev_ctrl)
            cmds.parentConstraint(ctrl, fk_jnt, mo=True)
            lock_hide_attrs(ctrl, ["tx", "ty", "tz",
                                    "sx", "sy", "sz", "v"])
            self.fk_ctrls.append(ctrl)
            self.fk_autos.append(auto)
            prev_ctrl = ctrl

    def _create_ik_spline(self):
        """Build a NURBS curve through the tail rest positions and run an
        ikSplineSolver from tail_01 to tip_IK. Cluster the CVs into 3
        groups (root / mid / tip) and parent each cluster handle under a
        master ctrl so the animator can pose the spline."""
        slots = self._ordered_slots()
        pts = [self.positions[s] for s in slots] + [self.positions["tip"]]

        self.curve = cmds.curve(ep=pts, d=3, n=f"{self.side}_{self.name}_CRV")
        cmds.parent(self.curve, self.misc_grp)
        # Same anti-double-translate trick as the ribbons: the curve's
        # parent inherits cog/global scale, but the cluster handles
        # driving its CVs are also under cog/global, so without
        # inheritsTransform=0 the cluster deltas would land twice on
        # the CV positions and the spline IK would track the wrong
        # world targets.
        cmds.setAttr(f"{self.curve}.inheritsTransform", 0)

        # ikSplineSolver from tail_01_IK to tip_IK
        self.ik_handle = cmds.ikHandle(
            sj=self.ik_jnts[0], ee=self.ik_jnts[-1],
            sol="ikSplineSolver", curve=self.curve,
            createCurve=False, parentCurve=False,
            n=f"{self.side}_{self.name}_ikHandle",
        )[0]
        cmds.setAttr(f"{self.ik_handle}.v", 0)
        cmds.parent(self.ik_handle, self.misc_grp)

        # Cluster the CVs into 3 ctrl groups. With degree-3 EP curve
        # passing through 8 points, cmds.curve produces 10 CVs. Split:
        #   root cluster: CVs 0-2  (first quarter)
        #   mid  cluster: CVs 3-6  (middle half)
        #   tip  cluster: CVs 7-9  (last quarter)
        n_cvs = cmds.getAttr(f"{self.curve}.spans") \
                + cmds.getAttr(f"{self.curve}.degree")
        third = max(1, n_cvs // 3)
        cv_groups = [
            ("root", list(range(0, third))),
            ("mid",  list(range(third, n_cvs - third))),
            ("tip",  list(range(n_cvs - third, n_cvs))),
        ]
        cluster_positions = {
            "root": self.positions["tail_01"],
            "mid":  self.positions[slots[len(slots) // 2]],
            "tip":  self.positions["tip"],
        }
        for label, cv_indices in cv_groups:
            if not cv_indices:
                continue
            cv_specs = [f"{self.curve}.cv[{i}]" for i in cv_indices]
            cluster, handle = cmds.cluster(
                cv_specs, n=f"{self.side}_{self.name}_{label}_CLUS")
            cmds.hide(handle)
            cmds.parent(handle, self.misc_grp)

            ctrl = create_diamond_ctrl(
                f"{self.side}_{self.name}_{label}_IK_CTRL",
                size=0.8 * SCALE, color=COLOR_IK,
            )
            cmds.xform(ctrl, ws=True, t=cluster_positions[label])
            offset = make_offset_group(ctrl)
            cmds.parent(offset, self.parent_ctrl if self.ik_follow
                        else self.ctrl_grp)
            # Drive the cluster handle from the ctrl
            cmds.parentConstraint(ctrl, handle, mo=True)
            lock_hide_attrs(ctrl, ["sx", "sy", "sz", "v"])
            self.cluster_ctrls.append(ctrl)

    def _create_settings_ctrl(self):
        self.settings_ctrl = create_gear_ctrl(
            f"{self.side}_{self.name}_SETTINGS_CTRL", size=0.5 * SCALE,
            color=COLOR_SETTINGS,
        )
        cmds.matchTransform(self.settings_ctrl, self.bind_jnts[0])
        cmds.move(2.5 * SCALE, 0, 0, self.settings_ctrl, r=True, ws=True)
        offset = make_offset_group(self.settings_ctrl)
        cmds.parent(offset, self.ctrl_grp)
        cmds.parentConstraint(self.bind_jnts[0], offset, mo=True)

        # 0 = IK spline (default), 1 = FK chain. Same convention as
        # arms / legs.
        cmds.addAttr(self.settings_ctrl, ln="ikFkSwitch",
                     at="double", min=0, max=1, dv=0, k=True)
        cmds.addAttr(self.settings_ctrl, ln="curl",
                     at="double", dv=0, k=True)
        cmds.addAttr(self.settings_ctrl, ln="wag",
                     at="double", dv=0, k=True)
        lock_hide_attrs(self.settings_ctrl,
                        ["tx", "ty", "tz", "rx", "ry", "rz",
                         "sx", "sy", "sz", "v"])

    def _create_ikfk_blend(self):
        """Blend BIND chain rotations between FK and IK chains. Same
        orientConstraint pattern as arms / legs."""
        rev = cmds.createNode("reverse", n=f"{self.side}_{self.name}_ikfk_REV")
        cmds.connectAttr(f"{self.settings_ctrl}.ikFkSwitch",
                          f"{rev}.inputX")

        for i, bind in enumerate(self.bind_jnts):
            fk = self.fk_jnts[i]
            ik = self.ik_jnts[i]
            oc = cmds.orientConstraint(fk, ik, bind, mo=True)[0]
            cmds.setAttr(f"{oc}.interpType", 2)  # shortest path
            weights = cmds.orientConstraint(oc, q=True, wal=True)
            # weights[0] = FK weight, weights[1] = IK weight.
            cmds.connectAttr(f"{self.settings_ctrl}.ikFkSwitch",
                              f"{oc}.{weights[0]}")
            cmds.connectAttr(f"{rev}.outputX",
                              f"{oc}.{weights[1]}")

    def _create_visibility_sdk(self):
        """FK ctrls visible only at switch=1, cluster ctrls visible only
        at switch=0. Same SDK pattern as arms / legs."""
        driver = f"{self.settings_ctrl}.ikFkSwitch"
        for ctrl in self.cluster_ctrls:
            cmds.setAttr(f"{ctrl}.v", l=False)
            cmds.setDrivenKeyframe(f"{ctrl}.v", cd=driver, dv=0.0, v=1,
                                    itt="linear", ott="step")
            cmds.setDrivenKeyframe(f"{ctrl}.v", cd=driver, dv=0.999, v=1,
                                    itt="linear", ott="step")
            cmds.setDrivenKeyframe(f"{ctrl}.v", cd=driver, dv=1.0, v=0,
                                    itt="linear", ott="step")
        for ctrl in self.fk_ctrls:
            cmds.setAttr(f"{ctrl}.v", l=False)
            cmds.setDrivenKeyframe(f"{ctrl}.v", cd=driver, dv=0.0, v=0,
                                    itt="linear", ott="step")
            cmds.setDrivenKeyframe(f"{ctrl}.v", cd=driver, dv=0.001, v=1,
                                    itt="linear", ott="step")
            cmds.setDrivenKeyframe(f"{ctrl}.v", cd=driver, dv=1.0, v=1,
                                    itt="linear", ott="step")

    def _apply_control_mode(self):
        """controls="fk" / "ik": pin the switch to that mode and lock it, so
        only one set of ctrls ever shows. The visibility SDK already hides
        the other set. Curl / wag only drive the FK chain, so an IK-only
        chain hides them too."""
        if self.controls == "fkik":
            return
        plug = f"{self.settings_ctrl}.ikFkSwitch"
        cmds.setAttr(plug, 1 if self.controls == "fk" else 0)
        cmds.setAttr(plug, lock=True, keyable=False, channelBox=False)
        if self.controls == "ik":
            for attr in ("curl", "wag"):
                cmds.setAttr(f"{self.settings_ctrl}.{attr}",
                             lock=True, keyable=False, channelBox=False)

    def _create_curl_wag_sdk(self):
        """Linear progression: each joint gets curl × (i+1)/N degrees on
        AUTO.rotateZ, and wag × (i+1)/N on AUTO.rotateY. Total curl at
        max value gets distributed across all joints with weights
        ramping from root → tip, so the tail forms a smooth arc instead
        of a sharp bend at the root."""
        n = len(self.fk_autos)
        if n == 0:
            return
        for i, auto in enumerate(self.fk_autos):
            # Weight ramps from 0.3 at root to 1.0 at tip — roots get a
            # gentle base curl while the tip gets the full motion.
            weight = 0.3 + 0.7 * (i / float(n - 1) if n > 1 else 0)
            # curl → rotateZ
            mc = cmds.createNode("multDoubleLinear",
                                  n=f"{self.side}_{self.name}_{i + 1:02d}_curl_MUL")
            cmds.connectAttr(f"{self.settings_ctrl}.curl",
                              f"{mc}.input1")
            cmds.setAttr(f"{mc}.input2", weight)
            cmds.connectAttr(f"{mc}.output", f"{auto}.rotateZ")
            # wag → rotateY
            mw = cmds.createNode("multDoubleLinear",
                                  n=f"{self.side}_{self.name}_{i + 1:02d}_wag_MUL")
            cmds.connectAttr(f"{self.settings_ctrl}.wag",
                              f"{mw}.input1")
            cmds.setAttr(f"{mw}.input2", weight)
            cmds.connectAttr(f"{mw}.output", f"{auto}.rotateY")


# =============================================================================
# SECTION 7B: FACE RIG (jaw + eyes + eyelids + brow + mouth + tongue)
# =============================================================================

class FaceRig(object):
    """Joint-based face rig. All sub-modules are toggleable via `submodules`.

    Submodule names: "jaw", "eyes", "eyelids", "brow", "mouth", "tongue".

    All face joints parent under self.parent_jnt (head BIND), all FK ctrls
    parent under self.parent_ctrl (head ctrl). The eye look-at master ctrl
    is the exception — it lives in world space (under ctrl_grp) so the
    character can track world targets.
    """

    DEFAULT_SUBMODULES = {"jaw", "eyes", "eyelids", "brow",
                          "mouth", "lips", "tongue", "cheeks", "nose",
                          "teeth", "ears"}

    def __init__(self, positions=None, parent_ctrl=None, parent_jnt=None,
                 ctrl_grp=None, jnt_grp=None, misc_grp=None,
                 submodules=None, heavy_mode=False,
                 heavy_lid_joints_per_arc=16,
                 heavy_lip_joints_per_curve=16):
        self.positions   = positions or {}
        self.parent_ctrl = parent_ctrl   # head_CTRL
        self.parent_jnt  = parent_jnt    # C_head_BIND_JNT
        self.ctrl_grp    = ctrl_grp
        self.jnt_grp     = jnt_grp
        self.misc_grp    = misc_grp      # holds NURBS curves & cluster handles
        self.submodules  = (set(submodules) if submodules
                            else set(self.DEFAULT_SUBMODULES))
        self.heavy_mode  = heavy_mode    # True = curve-driven 32-joint rigs
        # Configurable joint counts per eyelid arc / lip curve. Default 16
        # gives 32 lid joints per eye + 32 lip joints. Adjust to match the
        # actual mesh loop topology (~loop_count / 2 per arc).
        self.heavy_lid_joints_per_arc = heavy_lid_joints_per_arc
        self.heavy_lip_joints_per_curve = heavy_lip_joints_per_curve

        # Storage
        self.jaw_jnt = self.jaw_ctrl = self.jaw_auto = None
        self.L_eye_jnt = self.R_eye_jnt = None
        self.L_eye_aim_ctrl = self.R_eye_aim_ctrl = None
        self.eyes_lookat_ctrl = None
        self.eyelid_jnts  = {}    # name -> jnt (12 joints)
        self.eyelid_ctrls = {}
        self.eyelid_autos = {}    # AUTO group above each lid ctrl (SDK target)
        self.brow_jnts    = {}
        self.brow_ctrls   = {}
        self.brow_autos   = {}    # AUTO groups for brow raise / furrow SDK
        self.mouth_jnts   = {}
        self.mouth_ctrls  = {}
        self.mouth_autos  = {}    # AUTO groups for mouth corners (smile SDK)
        self.lip_jnts     = {}    # upper + lower lip joints
        self.lip_ctrls    = {}
        self.lip_autos    = {}    # AUTO groups for lips (lipsSeal SDK)
        # Sticky lips constraint info: ctrl_name -> (parentConstraint,
        # jaw_target_weight_attr). Heavy mode only. Setup happens in
        # _setup_sticky_lips_sdk which connects (1 - stickyLips) to each
        # weight so lower lips can "unstick" from jaw smoothly.
        self.sticky_constraint_weights = {}
        self.cheek_jnts   = {}
        self.cheek_ctrls  = {}
        self.cheek_autos  = {}    # for smile cascade
        self.nose_jnts    = {}
        self.nose_ctrls   = {}
        self.tongue_jnts  = []
        self.tongue_ctrls = []
        # Teeth — upper attaches to head joint, lower attaches to jaw joint
        # (or head if jaw isn't built).
        self.upperTeeth_jnt  = self.upperTeeth_ctrl = None
        self.lowerTeeth_jnt  = self.lowerTeeth_ctrl = None
        # Ears — single FK joint + ctrl per side, both children of head.
        self.L_ear_jnt = self.R_ear_jnt = None
        self.L_ear_ctrl = self.R_ear_ctrl = None

    # ------------------------------------------------------------------------

    def build(self):
        if "jaw"     in self.submodules: self._build_jaw()
        if "eyes"    in self.submodules: self._build_eyes()
        if "eyelids" in self.submodules:
            if self.heavy_mode: self._build_eyelids_heavy()
            else:               self._build_eyelids()
        if "brow"    in self.submodules: self._build_brow()
        # Standard mouth corners only when NOT in heavy mode — the heavy lip
        # rig provides its own corner master ctrls that drive the curves.
        if "mouth"   in self.submodules and not self.heavy_mode:
            self._build_mouth()
        if "lips"    in self.submodules:
            if self.heavy_mode: self._build_lips_heavy()
            else:               self._build_lips()
        if "cheeks"  in self.submodules: self._build_cheeks()
        if "nose"    in self.submodules: self._build_nose()
        if "tongue"  in self.submodules: self._build_tongue()
        if "teeth"   in self.submodules: self._build_teeth()
        if "ears"    in self.submodules: self._build_ears()
        # Layer SDK-driven automation on top of the built joints/ctrls.
        self._build_automation()

    # ------------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------------

    def _make_simple_face_joint(self, name, pos, ctrl_size, color,
                                 parent_jnt=None, parent_ctrl=None,
                                 ctrl_normal=(0, 0, 1), add_auto=False):
        """Create one BIND joint + a small square FK ctrl that drives it.

        If add_auto=True, an extra AUTO group is inserted above the ctrl's
        OFFSET group. This AUTO group is where SDK-driven automation
        (blink, smile, lipsSeal etc.) writes. The CTRL itself stays at
        zero translate so the animator can still layer manual offsets
        on top of the automation. v is left unlocked so the visibility
        can be driven from a master attribute.

        Returns (jnt, ctrl, auto_or_offset).
        """
        parent_jnt  = parent_jnt  or self.parent_jnt
        parent_ctrl = parent_ctrl or self.parent_ctrl

        cmds.select(cl=True)
        jnt = cmds.joint(n=f"{name}_BIND_JNT", p=pos)
        cmds.setAttr(f"{jnt}.jointOrient", 0, 0, 0)
        cmds.setAttr(f"{jnt}.radius", 0.3 * SCALE)
        cmds.parent(jnt, parent_jnt)

        ctrl = create_square_ctrl(f"{name}_CTRL", size=ctrl_size,
                                  normal=ctrl_normal, color=color)
        # POSITION-ONLY matchTransform so the ctrl is world-aligned. The joint
        # keeps its proper orient (inherited from head); the parentConstraint
        # below captures the rotational offset and the animator gets clean
        # world-axis manipulators on the face ctrls. CRUCIAL for the SDK to
        # move lids/lips in world Y instead of some tilted local axis.
        cmds.matchTransform(ctrl, jnt, pos=True, rot=False)
        offset = make_offset_group(ctrl)

        if add_auto:
            # New AUTO hierarchy:
            #   parent_ctrl
            #     └── AUTO            (local = 0,0,0  — pure SDK target)
            #           └── OFFSET    (local = world offset of the joint)
            #                 └── CTRL (local = 0,0,0 — animator additive)
            #
            # Old version put the world offset on AUTO, but then SDK that
            # writes auto.translateY=0 collapses the joint to parent_ctrl's
            # position. Putting the offset on OFFSET instead means SDK
            # writes are purely deltas added on top of the rest pose.
            auto = cmds.group(em=True, n=f"{name}_AUTO")
            # Relative parent → auto.local stays at (0,0,0,identity).
            cmds.parent(auto, parent_ctrl, r=True)
            # Absolute parent of offset under auto → world preserved on offset.
            cmds.parent(offset, auto)
        else:
            cmds.parent(offset, parent_ctrl)
            auto = offset  # return offset when no auto needed

        cmds.parentConstraint(ctrl, jnt, mo=True)
        # Lock scale only — leave v unlocked so master ctrls can drive vis.
        lock_hide_attrs(ctrl, ["sx", "sy", "sz"])
        return jnt, ctrl, auto

    # ------------------------------------------------------------------------
    # Submodules
    # ------------------------------------------------------------------------

    def _build_jaw(self):
        jaw_pos = self.positions["jaw"]
        tip_pos = self.positions["jawTip"]

        # Jaw + jawTip joints (chain so jaw orient aims at tip)
        cmds.select(cl=True)
        self.jaw_jnt = cmds.joint(n="C_jaw_BIND_JNT", p=jaw_pos)
        jaw_tip_jnt = cmds.joint(n="C_jawTip_BIND_JNT", p=tip_pos)
        cmds.joint(self.jaw_jnt, e=True, oj="xyz", sao="yup",
                   ch=True, zso=True)
        cmds.setAttr(f"{jaw_tip_jnt}.jointOrient", 0, 0, 0)
        cmds.setAttr(f"{self.jaw_jnt}.radius", 0.4 * SCALE)
        cmds.setAttr(f"{jaw_tip_jnt}.radius", 0.3 * SCALE)
        cmds.parent(self.jaw_jnt, self.parent_jnt)

        # FK ctrl — POSITION-ONLY matchTransform so the ctrl is world-aligned.
        # The jaw joint keeps its proper orient (aimed at jawTip) for skinning;
        # parentConstraint absorbs the rotational offset. This is what lets
        # the lower lip SDK move things in world Y instead of jaw-local Y.
        self.jaw_ctrl = create_circle_ctrl(
            "C_jaw_CTRL", radius=0.7 * SCALE, normal=(1, 0, 0),
            color=COLOR_CENTER,
        )
        cmds.matchTransform(self.jaw_ctrl, self.jaw_jnt, pos=True, rot=False)
        offset = make_offset_group(self.jaw_ctrl)
        cmds.parent(offset, self.parent_ctrl)
        # AUTO group between OFFSET and CTRL — drives jaw SIDE-SLIDE + THRUST
        # from attrs, so the jaw can chew / jut without disturbing the
        # animator's open/close on the ctrl (and the lips follow via the jaw).
        self.jaw_auto = cmds.group(em=True, n="C_jaw_AUTO")
        cmds.parent(self.jaw_auto, offset, r=True)
        cmds.parent(self.jaw_ctrl, self.jaw_auto, r=True)
        cmds.parentConstraint(self.jaw_ctrl, self.jaw_jnt, mo=True)
        for ln in ("jawSide", "jawThrust"):
            if not cmds.attributeQuery(ln, node=self.jaw_ctrl, exists=True):
                cmds.addAttr(self.jaw_ctrl, ln=ln, at="double",
                             min=-1, max=1, dv=0, k=True)
        for ax, attr, amt in (("X", "jawSide", 0.15 * SCALE),
                              ("Z", "jawThrust", 0.22 * SCALE)):
            drv = f"{self.jaw_ctrl}.{attr}"
            for dv, v in ((-1, -amt), (0, 0.0), (1, amt)):
                cmds.setDrivenKeyframe(f"{self.jaw_auto}.translate{ax}",
                                       cd=drv, dv=dv, v=v)
        # Float the jaw ctrl SHAPE down to the chin + forward so it sits
        # OUTSIDE the mesh and is easy to grab. Only the curve CVs move —
        # the transform/pivot stays at the hinge, so the joint + the
        # parentConstraint are untouched.
        jaw_shape_off = [tip_pos[i] - jaw_pos[i] for i in range(3)]
        jaw_shape_off[2] += 0.6 * SCALE
        cmds.move(jaw_shape_off[0], jaw_shape_off[1], jaw_shape_off[2],
                  f"{self.jaw_ctrl}.cv[*]", relative=True, worldSpace=True)
        # Leave v unlocked — showLipCtrls attr drives sub-ctrl visibility.
        lock_hide_attrs(self.jaw_ctrl, ["sx", "sy", "sz"])

    def _build_eyes(self):
        # ---- BIND joints ----
        for side in ("L", "R"):
            pos = self.positions[f"{side}_eye"]
            cmds.select(cl=True)
            jnt = cmds.joint(n=f"{side}_eye_BIND_JNT", p=pos)
            cmds.setAttr(f"{jnt}.jointOrient", 0, 0, 0)
            cmds.setAttr(f"{jnt}.radius", 0.4 * SCALE)
            cmds.parent(jnt, self.parent_jnt)
            if side == "L":
                self.L_eye_jnt = jnt
            else:
                self.R_eye_jnt = jnt

        # ---- Master look-at ctrl in WORLD SPACE (smaller, more visible) ----
        lookat_pos = self.positions["eyesLookAt"]
        self.eyes_lookat_ctrl = create_square_ctrl(
            "C_eyes_lookAt_CTRL", size=0.5 * SCALE,
            normal=(0, 0, 1), color=COLOR_CENTER,
        )
        cmds.xform(self.eyes_lookat_ctrl, ws=True, t=lookat_pos)
        lookat_offset = make_offset_group(self.eyes_lookat_ctrl)
        cmds.parent(lookat_offset, self.ctrl_grp)
        # Leave v unlocked — face automation drives sub-ctrl visibility from
        # an attribute on this ctrl.
        lock_hide_attrs(self.eyes_lookat_ctrl,
                        ["rx", "ry", "rz", "sx", "sy", "sz"])

        # `lookAt` attr — 1.0 (default) = eyes track the lookAt ctrl,
        # 0.0 = eyes look straight forward from the head (ignore lookAt).
        # Wired via a reverse node into the aimConstraint weights below
        # so animators can disable look-at without dragging the ctrl out
        # of the way or breaking constraints.
        cmds.addAttr(self.eyes_lookat_ctrl, ln="lookAt", at="double",
                     min=0.0, max=1.0, dv=1.0, k=True)
        lookat_rev = cmds.createNode("reverse", n="C_eyes_lookAt_REV")
        cmds.connectAttr(f"{self.eyes_lookat_ctrl}.lookAt",
                         f"{lookat_rev}.inputX", f=True)

        # ---- Per-eye aim ctrls under the master (smaller circles) ----
        for side, eye_jnt in (("L", self.L_eye_jnt), ("R", self.R_eye_jnt)):
            color = COLOR_LEFT if side == "L" else COLOR_RIGHT
            eye_pos = self.positions[f"{side}_eye"]
            # Place per-eye ctrl at the eye's X/Y but at the look-at Z
            ctrl_pos = (eye_pos[0], eye_pos[1], lookat_pos[2])
            ctrl = create_circle_ctrl(
                f"{side}_eye_aim_CTRL", radius=0.15 * SCALE,
                normal=(0, 0, 1), color=color,
            )
            cmds.xform(ctrl, ws=True, t=ctrl_pos)
            offset = make_offset_group(ctrl)
            cmds.parent(offset, self.eyes_lookat_ctrl)

            # "Straight ahead" target — a hidden transform parented to the
            # head joint, positioned directly in front of the eye in HEAD
            # local space. When the head rotates, this target rotates with
            # it, so aiming at it = eyes look in head-forward direction.
            # Distance forward matches the lookAt ctrl's Z so the aim
            # vector magnitudes are comparable when blending weights.
            straight_tgt = cmds.group(em=True,
                                       n=f"{side}_eye_straight_TGT")
            cmds.xform(straight_tgt, ws=True, t=ctrl_pos)
            cmds.parent(straight_tgt, self.parent_jnt)
            cmds.setAttr(f"{straight_tgt}.v", 0)

            # Two-target aimConstraint — weights blend between tracking
            # the per-eye ctrl (= lookAt ctrl chain) and the head-local
            # straight-ahead target.
            aim = cmds.aimConstraint(
                ctrl, straight_tgt, eye_jnt,
                aim=(0, 0, 1), u=(0, 1, 0),
                wut="objectrotation", wuo=self.parent_jnt, wu=(0, 1, 0),
                mo=False,
            )[0]
            aliases = cmds.aimConstraint(aim, q=True,
                                          weightAliasList=True)
            # aliases[0] = per-eye ctrl (lookAt source)
            # aliases[1] = straight_tgt (head-forward source)
            cmds.connectAttr(f"{self.eyes_lookat_ctrl}.lookAt",
                             f"{aim}.{aliases[0]}", f=True)
            cmds.connectAttr(f"{lookat_rev}.outputX",
                             f"{aim}.{aliases[1]}", f=True)

            # Leave v unlocked — blink SDK adds an attr here.
            lock_hide_attrs(ctrl, ["rx", "ry", "rz", "sx", "sy", "sz"])
            if side == "L":
                self.L_eye_aim_ctrl = ctrl
            else:
                self.R_eye_aim_ctrl = ctrl

    def _build_eyelids(self):
        """12 lid joints (3 per upper/lower per eye). Each gets its own
        small FK ctrl and an AUTO group above it so blink SDK can close the
        lids while still leaving the per-ctrl translate available to the
        animator for asymmetric shapes / squints."""
        names = (
            "L_eyelidUpperInner", "L_eyelidUpperMid", "L_eyelidUpperOuter",
            "L_eyelidLowerInner", "L_eyelidLowerMid", "L_eyelidLowerOuter",
            "R_eyelidUpperInner", "R_eyelidUpperMid", "R_eyelidUpperOuter",
            "R_eyelidLowerInner", "R_eyelidLowerMid", "R_eyelidLowerOuter",
        )
        for name in names:
            color = COLOR_LEFT if name.startswith("L") else COLOR_RIGHT
            jnt, ctrl, auto = self._make_simple_face_joint(
                name, self.positions[name],
                ctrl_size=0.2 * SCALE, color=color, add_auto=True,
            )
            self.eyelid_jnts[name]  = jnt
            self.eyelid_ctrls[name] = ctrl
            self.eyelid_autos[name] = auto

    # ========================================================================
    # HEAVY (curve-driven) eyelid + lip rigs — production-quality face setup
    # ========================================================================

    # NOTE: these are class-level defaults; the actual counts used at build
    # time come from self.heavy_lid_joints_per_arc / heavy_lip_joints_per_curve
    # which the UI lets the user adjust to match the mesh loop topology.
    HEAVY_LID_JOINTS_PER_ARC = 16   # default if instance attr isn't set
    HEAVY_LIP_JOINTS_PER_CURVE = 16

    def _attach_joints_to_curve(self, curve, n_joints, base_name, parent_grp,
                                 orient_target=None):
        """Create n_joints BIND joints driven by pointOnCurveInfo nodes.

        Joints sit under parent_grp (must have inheritsTransform=0 so the
        world-space positions from PCI are interpreted correctly).

        orient_target: joint to orient + scale constrain each created joint
            to. CRITICAL for skin deformation when global moves/rotates/
            scales the rig — PCI only drives translate, so without this
            the joints stay at identity rotation/scale while the BIND
            chain rotates/scales with global, causing mesh distortion at
            vertices weighted to both. Pass the head_jnt for lids and
            upper lip, jaw_jnt for lower lip."""
        joints = []
        for i in range(n_joints):
            u = (i + 0.5) / float(n_joints)
            cmds.select(cl=True)
            jnt = cmds.joint(n=f"{base_name}_{i + 1:02d}_BIND_JNT")
            cmds.setAttr(f"{jnt}.radius", 0.15 * SCALE)
            cmds.parent(jnt, parent_grp)

            pci = cmds.createNode("pointOnCurveInfo",
                                  n=f"{base_name}_{i + 1:02d}_PCI")
            cmds.connectAttr(f"{curve}.worldSpace[0]",
                             f"{pci}.inputCurve")
            cmds.setAttr(f"{pci}.parameter", u)
            cmds.setAttr(f"{pci}.turnOnPercentage", 1)  # use 0-1 range
            cmds.connectAttr(f"{pci}.position", f"{jnt}.translate")

            if orient_target and cmds.objExists(orient_target):
                # Orient + scale constraint with mo=True. CRITICAL:
                # mo=False snaps the joint to the target's ABSOLUTE rotation
                # at creation (so the bind pose has joint=head.worldRotation
                # including head's joint orient — and any tiny rotation drift
                # at runtime gets amplified as mesh distortion).
                # mo=True captures the OFFSET at creation, so the joint stays
                # at its current (identity) rotation and only inherits the
                # DELTA when head rotates. Same for scale. Skin deformation
                # then behaves correctly for translation-only moves.
                cmds.orientConstraint(orient_target, jnt, mo=True)
                cmds.scaleConstraint(orient_target, jnt, mo=True)

            joints.append(jnt)
        return joints

    def _make_cluster_ctrl(self, curve, cv_spec, ctrl_name, world_pos,
                            color, ctrl_size, parent_ctrl,
                            rotation_pivot_world=None):
        """Create a cluster on a single CV + a master ctrl that drives it.

        rotation_pivot_world:
            If provided, the AUTO group is placed at this world position
            (and parented to parent_ctrl with absolute mode). SDK on
            AUTO.rotateXYZ then orbits the cluster ctrl around that pivot.
            Used for eye blink — pivot = eyeball center, so the lid clusters
            curve over the eye sphere instead of translating in a flat line.

            If None (default), AUTO is at local (0,0,0) under parent_ctrl
            and SDK on AUTO.translateXYZ produces simple additive offsets.

        Returns (ctrl, auto_group) for SDK targeting."""
        # Master ctrl + offset
        ctrl = create_square_ctrl(ctrl_name, size=ctrl_size,
                                  normal=(0, 0, 1), color=color)
        cmds.xform(ctrl, ws=True, t=world_pos)
        offset = make_offset_group(ctrl)
        auto = cmds.group(em=True, n=ctrl_name.replace("_CTRL", "_AUTO"))

        if rotation_pivot_world is None:
            # Standard: AUTO at (0,0,0) local → translation-based SDK.
            cmds.parent(auto, parent_ctrl, r=True)
        else:
            # Rotation pivot: AUTO at given world pos → rotateXYZ orbits
            # the cluster ctrl around that point.
            cmds.xform(auto, ws=True, t=rotation_pivot_world)
            cmds.parent(auto, parent_ctrl)  # absolute, world preserved

        cmds.parent(offset, auto)
        lock_hide_attrs(ctrl, ["sx", "sy", "sz"])

        # Cluster on the CV(s). cv_spec can be "0", "1:2", "3" etc — supports
        # both single CV and ranges. Range clusters are used when the curve
        # was built with ep= and middle EPs map to multiple CVs.
        cluster_name = ctrl_name.replace("_CTRL", "_CLUS")
        cluster, handle = cmds.cluster(f"{curve}.cv[{cv_spec}]",
                                       n=cluster_name)
        cmds.parent(handle, ctrl)
        cmds.hide(handle)
        return ctrl, auto

    def _build_eyelids_heavy(self):
        """Curve-driven eyelid rig. Per eye: 2 NURBS arcs (upper + lower)
        through the 3 eyelid guide positions, 16 detail joints attached
        per arc (32 per eye), 3 master cluster ctrls per arc (6 per eye)
        driving the curve CVs. Blink SDK runs on the cluster ctrls."""
        for side in ("L", "R"):
            self._build_one_heavy_eyelid(side)

    def _build_one_heavy_eyelid(self, side):
        color = COLOR_LEFT if side == "L" else COLOR_RIGHT
        # Detail-joint parent group with inheritsTransform=0 — PCI gives
        # world positions, so the parent must not apply transforms.
        detail_grp = cmds.group(em=True,
                                n=f"{side}_lid_detail_GRP", p=self.jnt_grp)
        cmds.setAttr(f"{detail_grp}.inheritsTransform", 0)

        # Eyeball world position — used as the rotation pivot for the lid
        # master ctrls. When blink SDK drives AUTO.rotateX, each cluster
        # ctrl orbits around the eyeball, so the curve (and the 16 detail
        # joints attached to it) curves over the eye sphere — natural blink
        # instead of straight translation.
        eye_pos = self.positions.get(f"{side}_eye", (0, 162, 9))

        for lid_name, point_keys in [
            ("Upper", ("Inner", "Mid", "Outer")),
            ("Lower", ("Inner", "Mid", "Outer")),
        ]:
            points = [self.positions[f"{side}_eyelid{lid_name}{p}"]
                      for p in point_keys]
            # Degree-2 NURBS curve with EDIT POINTS so the curve actually
            # passes through every guide. With p= (CVs) the middle point
            # would just pull on the curve, not lie on it — detail joints
            # would drift away from the middle guide. With ep= the curve
            # has 4 CVs (3 EPs + degree-1 = 4) and passes through all 3
            # guide positions exactly.
            crv = cmds.curve(ep=points, d=2,
                             n=f"{side}_lid{lid_name}_CRV")
            cmds.parent(crv, self.misc_grp)
            # Same double-translation fix as the body ribbons: the clusters
            # driving the curve CVs are parented to head_CTRL (which inherits
            # cog/global moves), and without inheritsTransform=0 here the
            # curve's own worldMatrix ALSO inherits, doubling the offset.
            cmds.setAttr(f"{crv}.inheritsTransform", 0)

            # Detail BIND joints attached to the curve. Orient+scale
            # constrained to head_jnt so they follow global/head rotation
            # and scale (PCI only drives translate).
            jnts = self._attach_joints_to_curve(
                crv, self.heavy_lid_joints_per_arc,
                f"{side}_lid{lid_name}", detail_grp,
                orient_target=self.parent_jnt,
            )
            for j in jnts:
                self.eyelid_jnts[j] = j

            # Master cluster ctrls — 3 per arc. With ep curve (4 CVs from
            # 3 EPs), CV mapping is:
            #   Inner (EP0) -> cv[0]
            #   Mid   (EP1) -> cv[1:2]  (two middle CVs shape the through-point)
            #   Outer (EP2) -> cv[3]
            cv_specs = ("0", "1:2", "3")
            for cv_spec, part, pos in zip(cv_specs, point_keys, points):
                ctrl_name = f"{side}_lid{lid_name}{part}_CTRL"
                ctrl, auto = self._make_cluster_ctrl(
                    crv, cv_spec, ctrl_name, pos,
                    color=color, ctrl_size=0.22 * SCALE,
                    parent_ctrl=self.parent_ctrl,
                )
                self.eyelid_ctrls[ctrl_name] = ctrl
                self.eyelid_autos[ctrl_name] = auto

    def _build_lips_heavy(self):
        """Curve-driven lip rig with 32 detail joints (16 per curve), 10
        cluster master ctrls (5 per curve), shared corner ctrls between
        upper + lower lips.

        Lower lip cluster ctrls are parented under head_CTRL with a
        JAW_FOLLOW group inserted between them. The JAW_FOLLOW group is
        parent-constrained to jaw_CTRL with weight = (1 - stickyLips).
        When stickyLips=0 (default), the weight=1 so lowers follow the
        jaw normally (mouth opens). When stickyLips=1, weight=0 so lowers
        stay at their rest world position (lips stuck to upper)."""
        # Anchor points for each curve — uses existing lip + corner guides.
        upper_pts = [
            self.positions["L_mouthCorner"],
            self.positions["L_upperLipMid"],
            self.positions["C_upperLip"],
            self.positions["R_upperLipMid"],
            self.positions["R_mouthCorner"],
        ]
        lower_pts = [
            self.positions["L_mouthCorner"],
            self.positions["L_lowerLipMid"],
            self.positions["C_lowerLip"],
            self.positions["R_lowerLipMid"],
            self.positions["R_mouthCorner"],
        ]

        # Detail-joint parent (inheritsTransform=0)
        detail_grp = cmds.group(em=True,
                                n="C_lips_detail_GRP", p=self.jnt_grp)
        cmds.setAttr(f"{detail_grp}.inheritsTransform", 0)

        # Build both curves with EDIT POINTS so the curve passes through
        # every guide. With p= the middle CVs would just pull the curve
        # toward them — detail joints sampled along the curve would NOT
        # match the lip mid / center guide positions. With ep= the curve
        # has 7 CVs (5 EPs + degree 3 - 1 = 7) and goes through all 5 EPs.
        crv_upper = cmds.curve(ep=upper_pts, d=3, n="C_upperLip_CRV")
        crv_lower = cmds.curve(ep=lower_pts, d=3, n="C_lowerLip_CRV")
        cmds.parent([crv_upper, crv_lower], self.misc_grp)
        # Double-translation fix — clusters on these curves are driven by
        # ctrls that inherit cog/global motion; if the curves themselves
        # also inherit, every motion lands twice on the PCI-driven joints.
        cmds.setAttr(f"{crv_upper}.inheritsTransform", 0)
        cmds.setAttr(f"{crv_lower}.inheritsTransform", 0)

        # Detail joints. Upper lip joints follow head rotation/scale,
        # lower lip joints follow jaw (so when jaw rotates, lower lip
        # rotation aligns) — falls back to head if no jaw built.
        upper_jnts = self._attach_joints_to_curve(
            crv_upper, self.heavy_lip_joints_per_curve,
            "C_upperLip", detail_grp,
            orient_target=self.parent_jnt,
        )
        lower_jnts = self._attach_joints_to_curve(
            crv_lower, self.heavy_lip_joints_per_curve,
            "C_lowerLip", detail_grp,
            orient_target=self.jaw_jnt or self.parent_jnt,
        )
        for j in upper_jnts + lower_jnts:
            self.lip_jnts[j] = j

        # 5 master cluster ctrls per curve. With ep curve (7 CVs from 5 EPs),
        # CV mapping is:
        #   LCorner (EP0) -> cv[0]
        #   LMid    (EP1) -> cv[1:2]  (middle CVs shape the through-point)
        #   Center  (EP2) -> cv[3]
        #   RMid    (EP3) -> cv[4:5]
        #   RCorner (EP4) -> cv[6]
        ctrl_data = [
            # (cv_spec, point_index, part_name, color_side)
            ("0",   0, "LCorner", "L"),
            ("1:2", 1, "LMid",    "L"),
            ("3",   2, "Center",  "C"),
            ("4:5", 3, "RMid",    "R"),
            ("6",   4, "RCorner", "R"),
        ]

        for label, crv, points in [
            ("upper", crv_upper, upper_pts),
            ("lower", crv_lower, lower_pts),
        ]:
            is_lower = (label == "lower")
            for cv_spec, pt_i, part, side in ctrl_data:
                color = (COLOR_CENTER if side == "C"
                         else COLOR_LEFT if side == "L"
                         else COLOR_RIGHT)
                ctrl_name = f"C_{label}Lip_{part}_CTRL"

                # Both upper and lower parent to head_CTRL initially.
                # For lower we'll insert a JAW_FOLLOW group between.
                ctrl, auto = self._make_cluster_ctrl(
                    crv, cv_spec, ctrl_name, points[pt_i],
                    color=color, ctrl_size=0.18 * SCALE,
                    parent_ctrl=self.parent_ctrl,
                )

                if is_lower and self.jaw_ctrl:
                    self._insert_jaw_follow_for_sticky(
                        ctrl_name, auto, points[pt_i],
                    )

                self.lip_ctrls[ctrl_name] = ctrl
                self.lip_autos[ctrl_name] = auto
                # Mouth corner registration for smile SDK
                if part in ("LCorner", "RCorner") and label == "upper":
                    corner_name = f"{side}_mouthCorner"
                    self.mouth_ctrls[corner_name] = ctrl
                    self.mouth_autos[corner_name] = auto

    def _insert_jaw_follow_for_sticky(self, ctrl_name, auto, world_pos):
        """Insert a JAW_FOLLOW group between head_CTRL and the given AUTO
        group so a parentConstraint to jaw_CTRL can blend the lower lip
        between "follow jaw" and "stay at rest" via stickyLips weight.

        CRITICAL: after re-parenting, we zero BOTH the AUTO.translate AND
        its OFFSET child's translate. Here's why:

        When AUTO is re-parented from head_CTRL to JAW_FOLLOW (which is at
        world_pos), absolute parenting preserves AUTO's world position. But
        AUTO was at head_CTRL's world, so AUTO.local becomes non-zero (the
        offset from world_pos back to head_CTRL.world). The corresponding
        OFFSET.local also has the COMPLEMENTARY non-zero translate that
        cancels out and puts the cluster ctrl at world_pos.

        Problem: when SDK fires (e.g., lipsSeal at value 0) and writes
        AUTO.translateY = 0, it overwrites the non-zero rest value. The
        complementary OFFSET.translate is no longer cancelled → the cluster
        ctrl shifts → cluster deforms the curve CV down by ~6.5 units →
        lower lips end up "very low" (this was the bug).

        Fix: zero both AUTO.translate and OFFSET.translate after re-parent.
        Now both are at (0,0,0), so AUTO is at jaw_follow.world (= world_pos)
        and OFFSET puts CTRL at the same place. SDK can write deltas to
        AUTO.translate cleanly without breaking the rest pose."""
        auto_parent = cmds.listRelatives(auto, p=True)[0]   # head_CTRL

        jf_name = ctrl_name.replace("_CTRL", "_JAWFOLLOW")
        jaw_follow = cmds.group(em=True, n=jf_name)
        cmds.xform(jaw_follow, ws=True, t=world_pos)
        cmds.parent(jaw_follow, auto_parent)   # under head_CTRL

        # Re-parent AUTO under jaw_follow (absolute → world preserved).
        cmds.parent(auto, jaw_follow)

        # Zero AUTO and OFFSET locals to give SDK a clean (0,0,0) rest.
        cmds.setAttr(f"{auto}.translate", 0, 0, 0)
        cmds.setAttr(f"{auto}.rotate",    0, 0, 0)
        offset_name = ctrl_name.replace("_CTRL", "_OFFSET")
        if cmds.objExists(offset_name):
            cmds.setAttr(f"{offset_name}.translate", 0, 0, 0)
            cmds.setAttr(f"{offset_name}.rotate",    0, 0, 0)

        # Two-target parentConstraint for SMOOTH interpolation.
        # Single-target + weight blending tends to be step-y for rotation;
        # two targets with crossfade weights blend linearly between them.
        #   jaw_target: at world_pos, parented to jaw_CTRL   → follows jaw
        #   head_target: at world_pos, parented to head_CTRL → stays in head
        # weights:
        #   stickyLips=0 → jaw=1, head=0 → fully follows jaw (mouth opens)
        #   stickyLips=1 → jaw=0, head=1 → stuck in head space (lips sealed)
        #   stickyLips=0.4 → jaw=0.6, head=0.4 → linear blend
        jaw_tgt = cmds.group(em=True,
                             n=ctrl_name.replace("_CTRL", "_jawTGT"))
        cmds.xform(jaw_tgt, ws=True, t=world_pos)
        cmds.parent(jaw_tgt, self.jaw_ctrl)
        cmds.hide(jaw_tgt)

        head_tgt = cmds.group(em=True,
                              n=ctrl_name.replace("_CTRL", "_headTGT"))
        cmds.xform(head_tgt, ws=True, t=world_pos)
        cmds.parent(head_tgt, auto_parent)   # head_CTRL
        cmds.hide(head_tgt)

        pc = cmds.parentConstraint(jaw_tgt, head_tgt, jaw_follow,
                                   mo=True)[0]
        cmds.setAttr(f"{pc}.interpType", 2)   # shortest rotation path
        weights = cmds.parentConstraint(pc, q=True, weightAliasList=True)
        # weights[0] = jaw_tgt weight, weights[1] = head_tgt weight
        self.sticky_constraint_weights[ctrl_name] = (
            pc, weights[0], weights[1],
        )

    def _build_brow(self):
        for name in ("L_browInner", "L_browMid", "L_browOuter",
                     "R_browInner", "R_browMid", "R_browOuter"):
            color = COLOR_LEFT if name.startswith("L") else COLOR_RIGHT
            jnt, ctrl, auto = self._make_simple_face_joint(
                name, self.positions[name],
                ctrl_size=0.18 * SCALE, color=color, add_auto=True,
            )
            self.brow_jnts[name]  = jnt
            self.brow_ctrls[name] = ctrl
            self.brow_autos[name] = auto

    def _setup_brow_sdk(self):
        """Brow expression dials on each brow ctrl:
            * raise   (-1..1, every brow point) — up (surprise) / down.
            * furrow  (0..1, the INNER brows only) — squeeze toward the centre
              + down (anger / concentration, the '11' between the brows).
        Both write the brow AUTO group so they layer (blendWeighted) with the
        animator's own ctrl offset. Inner-brow raise + furrow together give the
        worried / sad shape (inner up) vs the angry shape (inner down + in)."""
        if not self.brow_ctrls:
            return
        RAISE_Y = 0.25 * SCALE
        FURROW_X = 0.12 * SCALE      # inward squeeze
        FURROW_Y = 0.15 * SCALE      # downward pull
        for name, ctrl in self.brow_ctrls.items():
            auto = self.brow_autos.get(name)
            if not auto or not cmds.objExists(ctrl):
                continue
            if not cmds.attributeQuery("raise", node=ctrl, exists=True):
                cmds.addAttr(ctrl, ln="raise", at="double",
                             min=-1, max=1, dv=0, k=True)
            for dv, v in ((-1, -RAISE_Y), (0, 0.0), (1, RAISE_Y)):
                cmds.setDrivenKeyframe(f"{auto}.translateY",
                                       cd=f"{ctrl}.raise", dv=dv, v=v)
            if "Inner" in name:
                if not cmds.attributeQuery("furrow", node=ctrl, exists=True):
                    cmds.addAttr(ctrl, ln="furrow", at="double",
                                 min=0, max=1, dv=0, k=True)
                sign = -1 if name.startswith("L") else 1   # toward centre x=0
                cmds.setDrivenKeyframe(f"{auto}.translateX",
                                       cd=f"{ctrl}.furrow", dv=0, v=0)
                cmds.setDrivenKeyframe(f"{auto}.translateX",
                                       cd=f"{ctrl}.furrow", dv=1,
                                       v=FURROW_X * sign)
                cmds.setDrivenKeyframe(f"{auto}.translateY",
                                       cd=f"{ctrl}.furrow", dv=0, v=0)
                cmds.setDrivenKeyframe(f"{auto}.translateY",
                                       cd=f"{ctrl}.furrow", dv=1, v=-FURROW_Y)

    def _build_mouth(self):
        for name in ("L_mouthCorner", "R_mouthCorner"):
            color = COLOR_LEFT if name.startswith("L") else COLOR_RIGHT
            jnt, ctrl, auto = self._make_simple_face_joint(
                name, self.positions[name],
                ctrl_size=0.3 * SCALE, color=color, add_auto=True,
            )
            self.mouth_jnts[name]  = jnt
            self.mouth_ctrls[name] = ctrl
            self.mouth_autos[name] = auto

    def _build_lips(self):
        """Upper + lower lip joints. Lower lip joints parent under the jaw
        joint (if it exists) so they follow mouth open / close automatically.
        AUTO groups above each ctrl let lipsSeal SDK pull the lips together."""
        upper_names = ("C_upperLip", "L_upperLipMid", "R_upperLipMid")
        lower_names = ("C_lowerLip", "L_lowerLipMid", "R_lowerLipMid")

        # Upper lip → parented to head (doesn't move with jaw)
        for name in upper_names:
            color = (COLOR_CENTER if name.startswith("C")
                     else COLOR_LEFT if name.startswith("L") else COLOR_RIGHT)
            jnt, ctrl, auto = self._make_simple_face_joint(
                name, self.positions[name],
                ctrl_size=0.2 * SCALE, color=color, add_auto=True,
            )
            self.lip_jnts[name]  = jnt
            self.lip_ctrls[name] = ctrl
            self.lip_autos[name] = auto

        # Lower lip → parented to jaw if present, else head
        lower_jnt_parent  = self.jaw_jnt  or self.parent_jnt
        lower_ctrl_parent = self.jaw_ctrl or self.parent_ctrl
        for name in lower_names:
            color = (COLOR_CENTER if name.startswith("C")
                     else COLOR_LEFT if name.startswith("L") else COLOR_RIGHT)
            jnt, ctrl, auto = self._make_simple_face_joint(
                name, self.positions[name],
                ctrl_size=0.2 * SCALE, color=color, add_auto=True,
                parent_jnt=lower_jnt_parent,
                parent_ctrl=lower_ctrl_parent,
            )
            self.lip_jnts[name]  = jnt
            self.lip_ctrls[name] = ctrl
            self.lip_autos[name] = auto

    def _build_cheeks(self):
        for name in ("L_cheek", "R_cheek"):
            color = COLOR_LEFT if name.startswith("L") else COLOR_RIGHT
            jnt, ctrl, auto = self._make_simple_face_joint(
                name, self.positions[name],
                ctrl_size=0.2 * SCALE, color=color, add_auto=True,
            )
            self.cheek_jnts[name]  = jnt
            self.cheek_ctrls[name] = ctrl
            self.cheek_autos[name] = auto

    def _build_nose(self):
        # Nose tip
        jnt, ctrl, _ = self._make_simple_face_joint(
            "C_noseTip", self.positions["C_noseTip"],
            ctrl_size=0.18 * SCALE, color=COLOR_CENTER,
        )
        self.nose_jnts["C_noseTip"]  = jnt
        self.nose_ctrls["C_noseTip"] = ctrl
        # Nostrils (for flare)
        for name in ("L_nostril", "R_nostril"):
            color = COLOR_LEFT if name.startswith("L") else COLOR_RIGHT
            jnt, ctrl, _ = self._make_simple_face_joint(
                name, self.positions[name],
                ctrl_size=0.12 * SCALE, color=color,
            )
            self.nose_jnts[name]  = jnt
            self.nose_ctrls[name] = ctrl

    # ------------------------------------------------------------------------
    # Automation — SDK-driven blink / smile / lipsSeal + sub-ctrl visibility
    # ------------------------------------------------------------------------

    def _build_automation(self):
        """Wire up driver attrs and SDK so animators get one-click expressions:
            * blink     (per-eye aim ctrl)        -> closes lids
              - standard: translation toward midline
              - heavy:    rotateX around eyeball (curves over sphere)
            * smile     (jaw ctrl, -1..1)         -> mouth corners cascade
            * lipsSeal  (jaw ctrl, 0..1)          -> lips meet at midpoint
            * stickyLips (jaw ctrl, 0..1, heavy)  -> lower lips stay at upper
                                                     rest pos as jaw opens
            * showLidCtrls / showLipCtrls         -> hide/show granular ctrls
        """
        if "eyes" in self.submodules and "eyelids" in self.submodules:
            self._setup_blink_sdk()
            self._setup_lid_visibility_toggle()
        if "jaw" in self.submodules and "mouth" in self.submodules:
            self._setup_smile_sdk()
        if "jaw" in self.submodules and "lips" in self.submodules:
            self._setup_lips_seal_sdk()
            if self.heavy_mode:
                self._setup_sticky_lips_sdk()
        if "jaw" in self.submodules:
            self._setup_lip_visibility_toggle()
        if "cheeks" in self.submodules:
            self._setup_cheek_sdk()
        if "brow" in self.submodules:
            self._setup_brow_sdk()

    def _setup_sticky_lips_sdk(self):
        """Heavy lip rig only. Jaw ctrl gets a 'stickyLips' attr (0..1).
        Each lower lip cluster has a two-target parentConstraint
        (jaw_target + head_target). This wires the crossfade weights:
            stickyLips=0   -> jaw=1, head=0 -> follows jaw normally
            stickyLips=1   -> jaw=0, head=1 -> stays in head space (stuck)
            stickyLips=0.4 -> jaw=0.6, head=0.4 -> linear blend
        Smooth interpolation across the full 0..1 range — animator can
        keyframe sticky-then-release for wet-lip unsticking effects."""
        if not self.jaw_ctrl or not self.sticky_constraint_weights:
            return
        if not cmds.attributeQuery("stickyLips", node=self.jaw_ctrl,
                                   exists=True):
            cmds.addAttr(self.jaw_ctrl, ln="stickyLips", at="double",
                         min=0, max=1, dv=0, k=True)

        rev = cmds.createNode("reverse", n="C_stickyLips_REV")
        cmds.connectAttr(f"{self.jaw_ctrl}.stickyLips", f"{rev}.inputX")

        for ctrl_name, weights_info in self.sticky_constraint_weights.items():
            pc, jaw_w_attr, head_w_attr = weights_info
            # jaw target weight = 1 - stickyLips (follow jaw when sticky=0)
            cmds.connectAttr(f"{rev}.outputX",
                             f"{pc}.{jaw_w_attr}", f=True)
            # head target weight = stickyLips (stuck to head when sticky=1)
            cmds.connectAttr(f"{self.jaw_ctrl}.stickyLips",
                             f"{pc}.{head_w_attr}", f=True)

    def _setup_blink_sdk(self):
        """Each per-eye aim ctrl gets a 'blink' attr (0..1) that closes its
        side's lids automatically.

        Standard (12-joint) mode: SDK on translateY — lids translate up/down
            in flat motion.
        Heavy (32-joint) mode: SDK on rotateX — AUTO groups sit at the
            eyeball center, so rotation orbits the cluster ctrls around the
            eye sphere. Lid curves wrap over the eye surface naturally
            instead of moving in a straight line."""
        eye_ctrls = {"L": self.L_eye_aim_ctrl, "R": self.R_eye_aim_ctrl}

        for side, eye_ctrl in eye_ctrls.items():
            if not eye_ctrl:
                continue
            if not cmds.attributeQuery("blink", node=eye_ctrl, exists=True):
                cmds.addAttr(eye_ctrl, ln="blink", at="double",
                             min=0, max=1, dv=0, k=True)
            driver = f"{eye_ctrl}.blink"

            if self.heavy_mode:
                # Translation-based blink — same approach as standard mode
                # but driving the 6 master cluster ctrls. Upper lid Y moves
                # 70% toward midline, lower lid 30% — natural blink anatomy.
                # The curve deforms via the clusters, all 16 detail joints
                # per arc follow along the curve.
                for part in ("Inner", "Mid", "Outer"):
                    upper_pos  = self.positions.get(
                        f"{side}_eyelidUpper{part}")
                    lower_pos  = self.positions.get(
                        f"{side}_eyelidLower{part}")
                    upper_auto = self.eyelid_autos.get(
                        f"{side}_lidUpper{part}_CTRL")
                    lower_auto = self.eyelid_autos.get(
                        f"{side}_lidLower{part}_CTRL")
                    if not (upper_pos and lower_pos
                            and upper_auto and lower_auto):
                        continue
                    # Use FULL gap (upper.y - lower.y), not half.
                    # Upper closes 70% of full gap downward, lower closes
                    # 30% upward → they MEET at midline (100% closure).
                    # The old "* 0.7 on (mid - upper)" only covered half the
                    # gap because (mid - upper) is already half the distance.
                    full_gap = upper_pos[1] - lower_pos[1]
                    upper_dy = -full_gap * 0.7
                    lower_dy =  full_gap * 0.3
                    cmds.setDrivenKeyframe(f"{upper_auto}.translateY",
                                           cd=driver, dv=0, v=0)
                    cmds.setDrivenKeyframe(f"{upper_auto}.translateY",
                                           cd=driver, dv=1, v=upper_dy)
                    cmds.setDrivenKeyframe(f"{lower_auto}.translateY",
                                           cd=driver, dv=0, v=0)
                    cmds.setDrivenKeyframe(f"{lower_auto}.translateY",
                                           cd=driver, dv=1, v=lower_dy)
            else:
                # Standard mode: translation SDK toward midline.
                for part in ("Inner", "Mid", "Outer"):
                    upper_pos  = self.positions.get(
                        f"{side}_eyelidUpper{part}")
                    lower_pos  = self.positions.get(
                        f"{side}_eyelidLower{part}")
                    upper_auto = self.eyelid_autos.get(
                        f"{side}_eyelidUpper{part}")
                    lower_auto = self.eyelid_autos.get(
                        f"{side}_eyelidLower{part}")
                    if not (upper_pos and lower_pos
                            and upper_auto and lower_auto):
                        continue
                    # Use FULL gap (upper.y - lower.y), not half.
                    # Upper closes 70% of full gap downward, lower closes
                    # 30% upward → they MEET at midline (100% closure).
                    # The old "* 0.7 on (mid - upper)" only covered half the
                    # gap because (mid - upper) is already half the distance.
                    full_gap = upper_pos[1] - lower_pos[1]
                    upper_dy = -full_gap * 0.7
                    lower_dy =  full_gap * 0.3
                    cmds.setDrivenKeyframe(f"{upper_auto}.translateY",
                                           cd=driver, dv=0, v=0)
                    cmds.setDrivenKeyframe(f"{upper_auto}.translateY",
                                           cd=driver, dv=1, v=upper_dy)
                    cmds.setDrivenKeyframe(f"{lower_auto}.translateY",
                                           cd=driver, dv=0, v=0)
                    cmds.setDrivenKeyframe(f"{lower_auto}.translateY",
                                           cd=driver, dv=1, v=lower_dy)

    def _setup_smile_sdk(self):
        """Jaw ctrl gets a 'smile' attr (0..10). At 10 the whole mouth area
        cascades upward into a big smile shape:
            * Mouth corners: full Y, slight inward X
            * Lip mids:      half Y (lips form a smile curve)
            * Cheeks:        quarter Y, slight inward X (cheek puff)

        Range is 0..10 (not -1..1) per user spec — pure smile, no frown.
        Amplitudes are ~3-4x bigger than the previous version so the smile
        actually reads on the mesh."""
        if not self.jaw_ctrl or not self.mouth_autos:
            return
        # If smile attr exists from a prior build with old range, recreate it
        if cmds.attributeQuery("smile", node=self.jaw_ctrl, exists=True):
            cmds.deleteAttr(f"{self.jaw_ctrl}.smile")
        cmds.addAttr(self.jaw_ctrl, ln="smile", at="double",
                     min=0, max=10, dv=0, k=True)
        driver = f"{self.jaw_ctrl}.smile"

        SMILE_Y = 0.5 * SCALE    # vertical travel at smile=10  (was 0.15)
        SMILE_X = 0.2 * SCALE    # inward squeeze at smile=10   (was 0.06)

        def _sdk_y(auto, amount):
            cmds.setDrivenKeyframe(f"{auto}.translateY",
                                   cd=driver, dv=0,  v=0)
            cmds.setDrivenKeyframe(f"{auto}.translateY",
                                   cd=driver, dv=10, v=amount)

        def _sdk_x(auto, amount_at_smile):
            cmds.setDrivenKeyframe(f"{auto}.translateX",
                                   cd=driver, dv=0,  v=0)
            cmds.setDrivenKeyframe(f"{auto}.translateX",
                                   cd=driver, dv=10, v=amount_at_smile)

        # Mouth corners: full lift, slight inward squeeze
        for name, auto in self.mouth_autos.items():
            _sdk_y(auto, SMILE_Y)
            sign = -1 if name.startswith("L") else 1
            _sdk_x(auto, SMILE_X * sign)

        # Lip mids — names differ between standard and heavy modes
        if self.heavy_mode:
            lip_mid_names = (
                "C_upperLip_LMid_CTRL", "C_upperLip_RMid_CTRL",
                "C_lowerLip_LMid_CTRL", "C_lowerLip_RMid_CTRL",
            )
        else:
            lip_mid_names = (
                "L_upperLipMid", "R_upperLipMid",
                "L_lowerLipMid", "R_lowerLipMid",
            )
        for name in lip_mid_names:
            auto = self.lip_autos.get(name)
            if auto:
                _sdk_y(auto, SMILE_Y * 0.5)

        # Cheeks: quarter lift + slight inward squeeze (cheek puff)
        for name, auto in self.cheek_autos.items():
            _sdk_y(auto, SMILE_Y * 0.25)
            sign = -1 if name.startswith("L") else 1
            _sdk_x(auto, SMILE_X * 0.4 * sign)

    def _setup_cheek_sdk(self):
        """Each cheek ctrl gets its own `puff` and `cheekRaise` (0..1):
            * puff       — the cheek balloons OUT: forward (+Z) and laterally
                           (the 'blowing' / chipmunk shape).
            * cheekRaise — the cheek lifts UP toward the eye — the orbicularis
                           'apple' that pops on a squint or a big smile.
        Both write the cheek AUTO group, so they layer additively
        (blendWeighted) on top of the jaw smile cascade AND the animator's own
        ctrl offset — turn the dial, the cheek joint moves, nothing fights."""
        if not self.cheek_ctrls:
            return
        PUFF_Z = 0.30 * SCALE      # forward balloon at puff=1
        PUFF_X = 0.18 * SCALE      # lateral balloon at puff=1
        RAISE_Y = 0.30 * SCALE     # cheek-raise lift at cheekRaise=1
        for name, ctrl in self.cheek_ctrls.items():
            auto = self.cheek_autos.get(name)
            if not auto or not cmds.objExists(ctrl):
                continue
            for ln in ("puff", "cheekRaise"):
                if not cmds.attributeQuery(ln, node=ctrl, exists=True):
                    cmds.addAttr(ctrl, ln=ln, at="double", min=0, max=1,
                                 dv=0, k=True)
            sign = 1 if name.startswith("L") else -1   # L cheek = +X
            cmds.setDrivenKeyframe(f"{auto}.translateZ",
                                   cd=f"{ctrl}.puff", dv=0, v=0)
            cmds.setDrivenKeyframe(f"{auto}.translateZ",
                                   cd=f"{ctrl}.puff", dv=1, v=PUFF_Z)
            cmds.setDrivenKeyframe(f"{auto}.translateX",
                                   cd=f"{ctrl}.puff", dv=0, v=0)
            cmds.setDrivenKeyframe(f"{auto}.translateX",
                                   cd=f"{ctrl}.puff", dv=1, v=PUFF_X * sign)
            cmds.setDrivenKeyframe(f"{auto}.translateY",
                                   cd=f"{ctrl}.cheekRaise", dv=0, v=0)
            cmds.setDrivenKeyframe(f"{auto}.translateY",
                                   cd=f"{ctrl}.cheekRaise", dv=1, v=RAISE_Y)

    def _setup_lips_seal_sdk(self):
        """Jaw ctrl gets a 'lipsSeal' attr (0..1). Each upper/lower lip pair
        meets at midpoint when seal=1 — basic zippy-lip behaviour.

        Standard rig has 3 pairs (center + 2 mids).
        Heavy rig has 5 pairs (corners + mids + center) driving curves —
        produces a full curve-based zip across the entire lip line.

        For true left/right zippy (zipL/zipR independent attrs), expose
        per-pair attrs in a later iteration."""
        if not self.jaw_ctrl or not self.lip_autos:
            return
        if not cmds.attributeQuery("lipsSeal", node=self.jaw_ctrl,
                                   exists=True):
            cmds.addAttr(self.jaw_ctrl, ln="lipsSeal", at="double",
                         min=0, max=1, dv=0, k=True)
        driver = f"{self.jaw_ctrl}.lipsSeal"

        if self.heavy_mode:
            # 5 master cluster pairs across the curve.
            # (upper_ctrl_name, lower_ctrl_name, source_pos_key_pair)
            pairs = [
                ("C_upperLip_LCorner_CTRL", "C_lowerLip_LCorner_CTRL",
                 ("L_mouthCorner", "L_mouthCorner")),
                ("C_upperLip_LMid_CTRL",    "C_lowerLip_LMid_CTRL",
                 ("L_upperLipMid", "L_lowerLipMid")),
                ("C_upperLip_Center_CTRL",  "C_lowerLip_Center_CTRL",
                 ("C_upperLip", "C_lowerLip")),
                ("C_upperLip_RMid_CTRL",    "C_lowerLip_RMid_CTRL",
                 ("R_upperLipMid", "R_lowerLipMid")),
                ("C_upperLip_RCorner_CTRL", "C_lowerLip_RCorner_CTRL",
                 ("R_mouthCorner", "R_mouthCorner")),
            ]
        else:
            # Standard: 3 pairs of single lip joint ctrls.
            pairs = [
                ("C_upperLip",    "C_lowerLip",
                 ("C_upperLip", "C_lowerLip")),
                ("L_upperLipMid", "L_lowerLipMid",
                 ("L_upperLipMid", "L_lowerLipMid")),
                ("R_upperLipMid", "R_lowerLipMid",
                 ("R_upperLipMid", "R_lowerLipMid")),
            ]

        for upper_name, lower_name, (u_key, l_key) in pairs:
            upper_pos  = self.positions.get(u_key)
            lower_pos  = self.positions.get(l_key)
            upper_auto = self.lip_autos.get(upper_name)
            lower_auto = self.lip_autos.get(lower_name)
            if not (upper_pos and lower_pos
                    and upper_auto and lower_auto):
                continue
            mid_y = (upper_pos[1] + lower_pos[1]) * 0.5
            upper_dy = (mid_y - upper_pos[1]) * 0.5
            lower_dy = (mid_y - lower_pos[1]) * 0.5

            cmds.setDrivenKeyframe(f"{upper_auto}.translateY",
                                   cd=driver, dv=0, v=0)
            cmds.setDrivenKeyframe(f"{upper_auto}.translateY",
                                   cd=driver, dv=1, v=upper_dy)
            cmds.setDrivenKeyframe(f"{lower_auto}.translateY",
                                   cd=driver, dv=0, v=0)
            cmds.setDrivenKeyframe(f"{lower_auto}.translateY",
                                   cd=driver, dv=1, v=lower_dy)

    def _setup_lid_visibility_toggle(self):
        """Master eye look-at ctrl gets a 'showLidCtrls' bool. When 0, the
        12 lid ctrls are hidden — animator drives blink via the SDK. Set
        to 1 to expose them for granular shape work."""
        if not self.eyes_lookat_ctrl or not self.eyelid_ctrls:
            return
        attr = "showLidCtrls"
        if not cmds.attributeQuery(attr, node=self.eyes_lookat_ctrl,
                                   exists=True):
            cmds.addAttr(self.eyes_lookat_ctrl, ln=attr, at="bool",
                         dv=0, k=True)
        for ctrl in self.eyelid_ctrls.values():
            cmds.setAttr(f"{ctrl}.v", l=False)
            cmds.connectAttr(f"{self.eyes_lookat_ctrl}.{attr}",
                             f"{ctrl}.v", f=True)

    def _setup_lip_visibility_toggle(self):
        """Jaw ctrl gets a 'showLipCtrls' bool. Hides mouth corners + lip
        ctrls by default so animator works with smile / lipsSeal attrs."""
        if not self.jaw_ctrl:
            return
        sub_ctrls = (list(self.mouth_ctrls.values()) +
                     list(self.lip_ctrls.values()))
        if not sub_ctrls:
            return
        attr = "showLipCtrls"
        if not cmds.attributeQuery(attr, node=self.jaw_ctrl, exists=True):
            cmds.addAttr(self.jaw_ctrl, ln=attr, at="bool", dv=0, k=True)
        for ctrl in sub_ctrls:
            cmds.setAttr(f"{ctrl}.v", l=False)
            cmds.connectAttr(f"{self.jaw_ctrl}.{attr}",
                             f"{ctrl}.v", f=True)

    # ------------------------------------------------------------------------

    def _build_tongue(self):
        # FK chain (3 joints), parented to jaw if present, else head
        tongue_root_parent = self.jaw_jnt or self.parent_jnt
        tongue_ctrl_parent = self.jaw_ctrl or self.parent_ctrl

        positions = [self.positions[f"tongue0{i}"] for i in (1, 2, 3)]

        cmds.select(cl=True)
        for i, pos in enumerate(positions, start=1):
            j = cmds.joint(n=f"C_tongue_0{i}_BIND_JNT", p=pos)
            self.tongue_jnts.append(j)
        cmds.joint(self.tongue_jnts[0], e=True, oj="xyz", sao="yup",
                   ch=True, zso=True)
        cmds.setAttr(f"{self.tongue_jnts[-1]}.jointOrient", 0, 0, 0)
        for j in self.tongue_jnts:
            cmds.setAttr(f"{j}.radius", 0.25 * SCALE)
        cmds.parent(self.tongue_jnts[0], tongue_root_parent)

        # FK ctrls
        for i, jnt in enumerate(self.tongue_jnts):
            ctrl = create_circle_ctrl(
                jnt.replace("_BIND_JNT", "_CTRL"),
                radius=0.3 * SCALE, normal=(1, 0, 0), color=COLOR_CENTER,
            )
            cmds.matchTransform(ctrl, jnt)
            offset = make_offset_group(ctrl)
            if i == 0:
                cmds.parent(offset, tongue_ctrl_parent)
            else:
                cmds.parent(offset, self.tongue_ctrls[-1])
            cmds.parentConstraint(ctrl, jnt, mo=True)
            lock_hide_attrs(ctrl, ["tx", "ty", "tz",
                                    "sx", "sy", "sz", "v"])
            self.tongue_ctrls.append(ctrl)

    # ------------------------------------------------------------------------
    # Teeth — upper follows head, lower follows jaw
    # ------------------------------------------------------------------------

    def _build_teeth(self):
        """One BIND joint + one FK ctrl per row of teeth. The upper row
        parents to the head joint so it stays put when the jaw opens;
        the lower row parents to the jaw joint (if built) so it follows
        the jaw — exactly how real teeth behave."""
        upper_pos = self.positions.get("upperTeeth")
        lower_pos = self.positions.get("lowerTeeth")
        if upper_pos is None or lower_pos is None:
            cmds.warning("FaceRig: teeth submodule requested but "
                         "positions['upperTeeth' / 'lowerTeeth'] missing.")
            return

        # Upper teeth → head
        cmds.select(cl=True)
        self.upperTeeth_jnt = cmds.joint(n="C_upperTeeth_BIND_JNT",
                                          p=upper_pos)
        cmds.setAttr(f"{self.upperTeeth_jnt}.jointOrient", 0, 0, 0)
        cmds.setAttr(f"{self.upperTeeth_jnt}.radius", 0.3 * SCALE)
        cmds.parent(self.upperTeeth_jnt, self.parent_jnt)

        self.upperTeeth_ctrl = create_square_ctrl(
            "C_upperTeeth_CTRL", size=0.25 * SCALE,
            normal=(0, 1, 0), color=COLOR_CENTER,
        )
        cmds.matchTransform(self.upperTeeth_ctrl,
                             self.upperTeeth_jnt, pos=True, rot=False)
        u_offset = make_offset_group(self.upperTeeth_ctrl)
        cmds.parent(u_offset, self.parent_ctrl)
        cmds.parentConstraint(self.upperTeeth_ctrl,
                               self.upperTeeth_jnt, mo=True)
        lock_hide_attrs(self.upperTeeth_ctrl, ["sx", "sy", "sz"])

        # Lower teeth → jaw if available, else head
        lower_jnt_parent  = self.jaw_jnt  or self.parent_jnt
        lower_ctrl_parent = self.jaw_ctrl or self.parent_ctrl

        cmds.select(cl=True)
        self.lowerTeeth_jnt = cmds.joint(n="C_lowerTeeth_BIND_JNT",
                                          p=lower_pos)
        cmds.setAttr(f"{self.lowerTeeth_jnt}.jointOrient", 0, 0, 0)
        cmds.setAttr(f"{self.lowerTeeth_jnt}.radius", 0.3 * SCALE)
        cmds.parent(self.lowerTeeth_jnt, lower_jnt_parent)

        self.lowerTeeth_ctrl = create_square_ctrl(
            "C_lowerTeeth_CTRL", size=0.25 * SCALE,
            normal=(0, 1, 0), color=COLOR_CENTER,
        )
        cmds.matchTransform(self.lowerTeeth_ctrl,
                             self.lowerTeeth_jnt, pos=True, rot=False)
        l_offset = make_offset_group(self.lowerTeeth_ctrl)
        cmds.parent(l_offset, lower_ctrl_parent)
        cmds.parentConstraint(self.lowerTeeth_ctrl,
                               self.lowerTeeth_jnt, mo=True)
        lock_hide_attrs(self.lowerTeeth_ctrl, ["sx", "sy", "sz"])

    # ------------------------------------------------------------------------
    # Ears — single FK joint per side, parented to head
    # ------------------------------------------------------------------------

    def _build_ears(self):
        """One BIND joint per ear at the ear root, driven by an FK ctrl.
        Animator can rotate the ear via the ctrl (flop, perk, twitch)."""
        for side in ("L", "R"):
            pos_key = f"{side}_ear"
            pos = self.positions.get(pos_key)
            if pos is None:
                cmds.warning(f"FaceRig: ears submodule requested but "
                             f"positions['{pos_key}'] missing.")
                continue
            color = COLOR_LEFT if side == "L" else COLOR_RIGHT

            cmds.select(cl=True)
            jnt = cmds.joint(n=f"{side}_ear_BIND_JNT", p=pos)
            cmds.setAttr(f"{jnt}.jointOrient", 0, 0, 0)
            cmds.setAttr(f"{jnt}.radius", 0.35 * SCALE)
            cmds.parent(jnt, self.parent_jnt)

            ctrl = create_circle_ctrl(
                f"{side}_ear_CTRL", radius=0.35 * SCALE,
                normal=(1, 0, 0), color=color,
            )
            cmds.matchTransform(ctrl, jnt, pos=True, rot=False)
            offset = make_offset_group(ctrl)
            cmds.parent(offset, self.parent_ctrl)
            cmds.parentConstraint(ctrl, jnt, mo=True)
            lock_hide_attrs(ctrl, ["tx", "ty", "tz",
                                    "sx", "sy", "sz"])

            if side == "L":
                self.L_ear_jnt, self.L_ear_ctrl = jnt, ctrl
            else:
                self.R_ear_jnt, self.R_ear_ctrl = jnt, ctrl


# =============================================================================
# SECTION 8: CHARACTER RIG (orchestrator)
# =============================================================================

class CharacterRig(object):
    """Builds and connects all rig modules into a full biped.

    Args:
        positions: nested dict from a GuideSystem.read_positions() call.
            Schema:
              {"spine":      {"hip": (x,y,z), "chest": (x,y,z)},
               "neck":       {"neck": ..., "head": ..., "head_tip": ...},
               "L_clavicle": {"clavicle": ..., "clavicle_tip": ...},
               "R_clavicle": {...},
               "L_arm":      {"shoulder": ..., "elbow": ..., "wrist": ...},
               "R_arm":      {...},
               "L_leg":      {"hip": ..., "knee": ..., "ankle": ...,
                              "ball": ..., "toe": ...},
               "R_leg":      {...}}
            Pass None to use the script's hardcoded T-pose defaults.

        modules: set of module names to build. Defaults to all body modules.
            Available: "spine", "neck", "clavicles", "arms", "legs".
            ("spine" is mandatory if neck/clavicles/arms/legs are enabled.)

        bendy_count: number of bendy joints in the spine ribbon.
    """

    DEFAULT_MODULES = {"spine", "neck", "clavicles", "arms", "legs",
                       "face", "fingers", "tail"}

    def __init__(self, positions=None, modules=None, bendy_count=7,
                 face_submodules=None, face_heavy=False,
                 spine_fk_count=3,
                 face_lid_joints_per_arc=16,
                 face_lip_joints_per_curve=16,
                 extra_limbs=None):
        positions = positions or {}
        self.core_positions       = positions.get("core")
        self.spine_positions      = positions.get("spine")
        self.neck_positions       = positions.get("neck")
        self.L_clavicle_positions = positions.get("L_clavicle")
        self.R_clavicle_positions = positions.get("R_clavicle")
        self.L_arm_positions      = positions.get("L_arm")
        self.R_arm_positions      = positions.get("R_arm")
        self.L_leg_positions      = positions.get("L_leg")
        self.R_leg_positions      = positions.get("R_leg")
        self.face_positions       = positions.get("face")
        self.L_finger_positions   = positions.get("L_fingers")
        self.R_finger_positions   = positions.get("R_fingers")
        self.tail_positions       = positions.get("tail")

        self.modules = set(modules) if modules else set(self.DEFAULT_MODULES)
        self.bendy_count = bendy_count
        self.face_submodules = face_submodules  # None = use FaceRig default
        self.face_heavy = face_heavy            # True = curve-driven 32-jnt
        self.spine_fk_count = spine_fk_count    # FK ctrls between hip+chest
        self.face_lid_joints_per_arc = face_lid_joints_per_arc
        self.face_lip_joints_per_curve = face_lip_joints_per_curve
        # Fantasy-creature limbs built on top of the biped (four arms, extra
        # legs, a second tail...). See _build_extra_limbs for the spec format.
        # Empty = a normal biped, byte-identical to before.
        self.extra_limbs = list(extra_limbs or [])
        self.extra_rigs = {}

        # Module dependencies:
        # - Spine is OPTIONAL. When absent, upper-body modules (neck/clavicles/
        #   arms) and legs fall back to C_root_BIND_JNT / C_cog_CTRL.
        # - Face requires neck (it needs the head joint to parent to).
        if "face" in self.modules:
            self.modules.add("neck")
        # - Arms require clavicles (need clavicle_tip joint to parent to).
        if "arms" in self.modules:
            self.modules.add("clavicles")
        # - Fingers require arms.
        if "fingers" in self.modules:
            self.modules.add("arms")
        # - Tail attaches to pelvis (from spine) or root_BIND (fallback).
        #   No dependency forced — works standalone.

        self.core = None
        self.spine = None
        self.neck = None
        self.L_clav = self.R_clav = None
        self.L_arm = self.R_arm = None
        self.L_leg = self.R_leg = None
        self.face = None
        self.tail = None

    def _size_factor(self):
        """A guide rig SCALED as a unit (in RIG_GUIDES_GRP) should build a
        proportionally-sized rig — joints AND controls. Joints already follow
        the (world-space) guide positions, but the control sizing uses the
        module SCALE constant, so without this a scaled-up rig gets tiny
        controls. Measure the hip->chest span against the default guides and
        return the ratio; a default-size rig returns 1.0 (nothing changes)."""
        try:
            import rig_guides as _rg
            sp = self.spine_positions or {}
            hip, chest = sp.get("hip"), sp.get("chest")
            if hip and chest:
                dh = _rg.DEFAULT_GUIDES["C_pelvis"]["pos"]
                dc = _rg.DEFAULT_GUIDES["C_chest"]["pos"]
                cur = sum((a - b) ** 2 for a, b in zip(hip, chest)) ** 0.5
                dfl = sum((a - b) ** 2 for a, b in zip(dh, dc)) ** 0.5
                if dfl > 1e-4 and cur > 1e-4:
                    return cur / dfl
        except Exception:
            pass
        return 1.0

    def build(self):
        if cmds.objExists("CHARACTER_RIG_GRP"):
            cmds.error("Character rig already exists. Delete it first.")
            return
        # Check extra-limb specs BEFORE creating anything, so a typo can't
        # leave a half-built rig in the scene.
        self._validate_extra_limbs()
        # Auto-size controls to the (possibly scaled) guides. SCALE is a module
        # global every sub-module reads, so set it for this build and restore
        # it after — a default-size rig is unaffected (factor 1.0).
        global SCALE
        _base_scale = SCALE
        factor = self._size_factor()
        SCALE = _base_scale * factor
        if abs(factor - 1.0) > 0.02:
            print(f"[CharacterRig] Guides are ~{factor:.2f}x the default size "
                  f"— scaling controls + joints to match.")
        try:
            return self._build_impl()
        finally:
            SCALE = _base_scale

    def _build_impl(self):
        print(f"[CharacterRig] Building modules: {sorted(self.modules)}")

        # 1. Core (always)
        self.core = CoreRig(positions=self.core_positions)
        self.core.build()
        cg, jg, mg = (self.core.ctrl_grp, self.core.jnt_grp,
                      self.core.misc_grp)

        # 2. Spine (OPTIONAL)
        if "spine" in self.modules:
            self.spine = SpineRig(
                positions=self.spine_positions,
                bendy_count=self.bendy_count,
                fk_chain_count=self.spine_fk_count,
                parent_ctrl=self.core.cog_ctrl,
                ctrl_grp=cg, jnt_grp=jg, misc_grp=mg,
            )
            self.spine.build()

        # Resolve fallback parents — when spine is absent, modules attach
        # to the core (cog ctrl + root BIND joint).
        torso_ctrl  = self.spine.chest_ctrl     if self.spine else self.core.cog_ctrl
        torso_jnt   = self.spine.chest_bind_jnt if self.spine else self.core.root_bind_jnt
        pelvis_ctrl = self.spine.hip_ctrl       if self.spine else self.core.cog_ctrl
        pelvis_jnt  = self.spine.pelvis_bind_jnt if self.spine else self.core.root_bind_jnt

        # 3. Neck (under chest, or under cog if no spine)
        if "neck" in self.modules:
            self.neck = NeckHeadRig(
                positions=self.neck_positions,
                parent_ctrl=torso_ctrl,
                parent_jnt=torso_jnt,
                ctrl_grp=cg, jnt_grp=jg,
            )
            self.neck.build()

        # 4. Clavicles (required if arms enabled — enforced in __init__)
        if "clavicles" in self.modules:
            self.L_clav = ClavicleRig(
                side="L", positions=self.L_clavicle_positions,
                parent_ctrl=torso_ctrl,
                parent_jnt=torso_jnt,
                ctrl_grp=cg, jnt_grp=jg,
            )
            self.R_clav = ClavicleRig(
                side="R", positions=self.R_clavicle_positions,
                parent_ctrl=torso_ctrl,
                parent_jnt=torso_jnt,
                ctrl_grp=cg, jnt_grp=jg,
            )
            self.L_clav.build()
            self.R_clav.build()

        # 5. Arms (+ fingers under each wrist if "fingers" module enabled)
        if "arms" in self.modules and self.L_clav and self.R_clav:
            build_fingers = "fingers" in self.modules
            self.L_arm = ArmRig(
                side="L", positions=self.L_arm_positions,
                parent_ctrl=self.L_clav.ctrl, parent_jnt=self.L_clav.tip_jnt,
                ctrl_grp=cg, jnt_grp=jg, misc_grp=mg,
                finger_positions=(self.L_finger_positions
                                   if build_fingers else None),
            )
            self.R_arm = ArmRig(
                side="R", positions=self.R_arm_positions,
                parent_ctrl=self.R_clav.ctrl, parent_jnt=self.R_clav.tip_jnt,
                ctrl_grp=cg, jnt_grp=jg, misc_grp=mg,
                finger_positions=(self.R_finger_positions
                                   if build_fingers else None),
            )
            self.L_arm.build()
            self.R_arm.build()

        # 6. Legs (under hip ctrl, or under cog if no spine)
        if "legs" in self.modules:
            self.L_leg = LegRig(
                side="L", positions=self.L_leg_positions,
                parent_ctrl=pelvis_ctrl,
                parent_jnt=pelvis_jnt,
                ctrl_grp=cg, jnt_grp=jg, misc_grp=mg,
            )
            self.R_leg = LegRig(
                side="R", positions=self.R_leg_positions,
                parent_ctrl=pelvis_ctrl,
                parent_jnt=pelvis_jnt,
                ctrl_grp=cg, jnt_grp=jg, misc_grp=mg,
            )
            self.L_leg.build()
            self.R_leg.build()

        # 6.5 Tail (attaches to pelvis or cog/root if no spine)
        if "tail" in self.modules:
            self.tail = TailRig(
                positions=self.tail_positions,
                parent_ctrl=pelvis_ctrl,
                parent_jnt=pelvis_jnt,
                ctrl_grp=cg, jnt_grp=jg, misc_grp=mg,
            )
            self.tail.build()

        # 7. Face (parents to head joint + head ctrl)
        if "face" in self.modules and self.neck:
            head_jnt  = self.neck.bind_jnts[1]   # C_head_BIND_JNT
            head_ctrl = self.neck.fk_ctrls[1]    # C_head_CTRL
            self.face = FaceRig(
                positions=self.face_positions,
                parent_ctrl=head_ctrl,
                parent_jnt=head_jnt,
                ctrl_grp=cg, jnt_grp=jg, misc_grp=mg,
                submodules=self.face_submodules,
                heavy_mode=self.face_heavy,
                heavy_lid_joints_per_arc=self.face_lid_joints_per_arc,
                heavy_lip_joints_per_curve=self.face_lip_joints_per_curve,
            )
            self.face.build()

        # 7.5 Extra limbs + custom chains. No-op unless extra_limbs given.
        # After the face, so a limb can attach to ANY joint (jaw, brow...).
        if self.extra_limbs:
            neck = getattr(self, "neck", None)
            self._build_extra_limbs(cg, jg, mg, parents={
                "chest":  (torso_ctrl, torso_jnt),
                "pelvis": (pelvis_ctrl, pelvis_jnt),
                "cog":    (self.core.cog_ctrl, self.core.root_bind_jnt),
                "head":   ((neck.fk_ctrls[1], neck.bind_jnts[1]) if neck
                           else (torso_ctrl, torso_jnt)),
            })

        # Procedural auto-walk on the root ctrl (needs both legs).
        if self.L_leg and self.R_leg:
            self._build_auto_walk()

        # Lock the top organizational groups (not global_ctrl — that drives
        # the rig's globalScale via its own scaleX/Y/Z connection).
        for grp in (self.core.main_grp, self.core.ctrl_grp,
                    self.core.jnt_grp):
            lock_hide_attrs(grp,
                            ["tx", "ty", "tz", "rx", "ry", "rz",
                             "sx", "sy", "sz"])

        # Joint radius pass — make all joints visible at this scale.
        # BIND joints big, FK/IK/DRV joints smaller, bendy joints smaller still.
        all_joints = cmds.listRelatives(self.core.main_grp,
                                        ad=True, type="joint") or []
        for j in all_joints:
            if "_FK_" in j or "_IK_" in j or "_DRV_" in j:
                cmds.setAttr(f"{j}.radius", 0.4 * SCALE)
            elif "_bendy_" in j or "_spine_0" in j:
                cmds.setAttr(f"{j}.radius", 0.5 * SCALE)
            else:  # BIND anchors (pelvis, chest, neck, head, clavicle, limb)
                cmds.setAttr(f"{j}.radius", 0.8 * SCALE)

        # Hide the guide locators once the rig is built — they're only
        # useful during placement, and leaving them visible clutters
        # the viewport. The guides group stays in the scene (so the
        # rigger can re-show, mirror, save, or rebuild) — only its
        # visibility is toggled off. Done by name lookup so we don't
        # need a reference to the GuideSystem instance.
        for guide_grp in ("RIG_GUIDES_GRP", "GUIDES_GRP"):
            if cmds.objExists(guide_grp):
                try:
                    cmds.setAttr(f"{guide_grp}.visibility", 0)
                except Exception:
                    pass

        cmds.select(cl=True)
        print("[CharacterRig] Done. Top node: CHARACTER_RIG_GRP")

    # ------------------------------------------------------------------
    # Extra limbs (fantasy creatures)
    # ------------------------------------------------------------------
    _EXTRA_DEFAULT_PARENT = {"arm": "chest", "leg": "pelvis", "tail": "pelvis",
                             "chain": "chest"}
    _CHAIN_CONTROLS = ("fkik", "fk", "ik")
    # Name tokens the classic biped + face already use (compared lowercase).
    # An extra limb reusing one would collide with, or be picked up by name
    # as, a base rig node. Labels starting "lid"/"lip" are blocked too, since
    # the face export pass grabs *_lid* / *_lip* joints.
    _RESERVED_LABELS = {
        "arm", "leg", "tail", "clavicle", "thumb", "index", "middle", "ring",
        "pinky", "spine", "neck", "head", "chest", "pelvis", "hip", "hips",
        "cog", "root", "global", "autowalk", "jaw", "eye", "eyes", "eyeball",
        "brow", "browinner", "browmid", "browouter", "cheek", "ear", "nose",
        "nostril", "tongue", "teeth", "upperteeth", "lowerteeth", "mouth",
        "mouthcorner", "upperlip", "lowerlip", "upperlipmid", "lowerlipmid",
        "eyelid", "face", "foot", "hand", "space", "spaceswitch"}

    _EXTRA_PARENTS = ("chest", "pelvis", "cog", "head")

    @staticmethod
    def _attach_joint_name(par):
        """The joint name (or wildcard pattern) a custom attach targets, or
        None if `par` isn't a joint-style parent. Accepted forms:
            "C_tail_02_BIND_JNT"
            {"joint": "C_spine_*_BIND_JNT", "near": (x, y, z)}
        (the second picks the matching joint nearest `near`, for guides that
        sit between ribbon joints)."""
        if isinstance(par, str) and par.endswith("_JNT"):
            return par
        if (isinstance(par, dict) and isinstance(par.get("joint"), str)
                and par["joint"].endswith("_JNT")):
            return par["joint"]
        return None

    def _resolve_attach(self, par, side, label, cg):
        """(ctrl_parent, joint_parent) for a joint-style attach on `side`.
        An L_ joint on a mirrored R limb becomes its R_ twin. The ctrls hang
        under a small group in the controls hierarchy that follows the
        joint, so they stay visible and move with whatever drives it."""
        name = self._attach_joint_name(par)
        near = par.get("near") if isinstance(par, dict) else None
        if side == "R" and name.startswith("L_"):
            name = "R_" + name[2:]
            if near:
                near = (-near[0], near[1], near[2])
        found = [j for j in (cmds.ls(name, type="joint") or [])
                 if "|" not in j]
        if not found:
            raise ValueError("extra limb %r: attach joint %r isn't in the rig. "
                             "Is its module (tail, face, fingers...) turned "
                             "on?" % (label, name))
        if len(found) > 1 or "*" in name:
            ref = near or cmds.xform(found[0], q=True, ws=True, t=True)
            found.sort(key=lambda j: sum(
                (a - b) ** 2 for a, b in
                zip(cmds.xform(j, q=True, ws=True, t=True), ref)))
        jnt = found[0]
        grp = cmds.group(em=True, n="%s_%s_attach_GRP" % (side, label))
        cmds.matchTransform(grp, jnt)
        cmds.parent(grp, cg)
        cmds.parentConstraint(jnt, grp, mo=True)
        return grp, jnt

    def _validate_extra_limbs(self):
        """Raise ValueError for any bad extra-limb spec. Runs before the
        build creates a single node."""
        import re
        seen = set()
        for i, spec in enumerate(self.extra_limbs):
            if not isinstance(spec, dict):
                raise ValueError("extra_limbs[%d] must be a dict, got %r"
                                 % (i, spec))
            kind = spec.get("type")
            label = spec.get("label")
            if kind not in self._EXTRA_DEFAULT_PARENT:
                raise ValueError('extra limb type must be "arm", "leg", '
                                 '"tail" or "chain", got %r' % (kind,))
            if not label or not re.match(r"^[A-Za-z][A-Za-z0-9]*$",
                                         str(label)):
                raise ValueError("extra limb label must be letters/digits "
                                 "starting with a letter, got %r" % (label,))
            low = str(label).lower()
            if (low in self._RESERVED_LABELS or low.startswith("eyelid")
                    or low.startswith(("lid", "lip"))):
                raise ValueError("extra limb label %r clashes with a name the "
                                 "base rig or face already uses, pick another "
                                 "(e.g. %r)" % (label, "extra" + label[:1]
                                                .upper() + label[1:]))
            if low in seen:
                raise ValueError("extra limb label %r is used twice, labels "
                                 "must be unique" % label)
            par = spec.get("parent", self._EXTRA_DEFAULT_PARENT[kind])
            is_pair = (isinstance(par, (list, tuple)) and len(par) == 2
                       and all(isinstance(n, str) for n in par))
            jnt = self._attach_joint_name(par)
            if not is_pair and jnt is None and par not in self._EXTRA_PARENTS:
                raise ValueError("extra limb %r: unknown parent %r (use %s, a "
                                 "joint name like 'C_spine_04_BIND_JNT', or a "
                                 "(ctrl, joint) pair)"
                                 % (label, par, ", ".join(self._EXTRA_PARENTS)))
            if is_pair:
                for node in par:
                    if not cmds.objExists(node):
                        raise ValueError("extra limb %r: parent node %r does "
                                         "not exist" % (label, node))
            if jnt is not None:
                # A joint on an extra limb must come from one listed EARLIER
                # (limbs build in order). Base-rig joints are checked once
                # the base rig exists, before any extra limb is built.
                m = re.match(r"^[LRC]_([A-Za-z][A-Za-z0-9]*)", jnt)
                tok = (m.group(1).lower() if m else "")
                later = {str(s.get("label", "")).lower()
                         for s in self.extra_limbs[i + 1:]
                         if isinstance(s, dict)}
                if tok == low or tok in later:
                    raise ValueError("extra limb %r: can't attach to %r, that "
                                     "limb is %s. Attach to a limb listed "
                                     "before it." % (label, jnt, "itself"
                                                     if tok == low else
                                                     "built after it"))
            seen.add(low)
            sides_ok = (("C", "L", "R", "LR") if kind == "chain"
                        else ("L", "R", "LR"))
            if kind != "tail" and spec.get("side", "LR") not in sides_ok:
                raise ValueError('extra limb %r: side must be %s, got %r'
                                 % (label, " / ".join(sides_ok),
                                    spec.get("side")))
            if kind == "chain":
                controls = spec.get("controls", "fkik")
                if controls not in self._CHAIN_CONTROLS:
                    raise ValueError('chain %r: controls must be "fkik", "fk" '
                                     'or "ik", got %r' % (label, controls))
                n = spec.get("joints")
                pos = spec.get("positions")
                if pos is not None:
                    n = len([k for k in pos if str(k).startswith("tail_")])
                if not isinstance(n, int) or n < 1:
                    raise ValueError("chain %r: needs a joint count of 1 or "
                                     "more (\"joints\"), got %r" % (label, n))
                if controls != "fk" and n < 2:
                    raise ValueError("chain %r: IK needs at least 2 joints, "
                                     "use controls \"fk\" for a single joint"
                                     % label)
            off = spec.get("offset")
            if off is not None and not (
                    isinstance(off, (list, tuple)) and len(off) == 3
                    and all(isinstance(c, (int, float)) for c in off)):
                raise ValueError("extra limb %r: offset must be (x, y, z), got "
                                 "%r" % (label, off))

    def _build_extra_limbs(self, cg, jg, mg, parents):
        """Build each `extra_limbs` spec on top of the base biped.

        A spec is a dict:
          type       "arm" | "leg" | "tail" | "chain"                required
          label      unique name token, letters/digits, e.g. "lowerArm"
                     -> nodes L_lowerArm_* / R_lowerArm_*            required
          side       "LR" mirrored pair (default) | "L" | "R"
                     (chains also "C" for one centre chain)
          parent     "chest" | "pelvis" | "cog" | "head", a (ctrl, joint)
                     pair, or ANY joint: "C_tail_02_BIND_JNT", or
                     {"joint": "C_spine_*_BIND_JNT", "near": (x, y, z)}.
                     An L_ joint on a mirrored limb's R side becomes R_.
                     Default: chest for arms and chains, pelvis otherwise.
          joints     chains: number of joints (plus a tip)
          controls   chains: "fkik" (switchable, default) | "fk" | "ik"
          positions  that limb's positions. With side "LR" they are for the
                     LEFT and the right is mirrored in X. Omitted = copy this
                     rig's own arm / leg / tail layout.
          offset     (x, y, z) added to those positions (x mirrored on R)
          fingers    True: give an extra arm fingers                  arms
          clavicle   True: give an extra arm its own clavicle/shrug   arms
          clavicle_positions / finger_positions   optional, LEFT side like
                     `positions`. Omitted = the base clavicle / fingers moved
                     along with this arm's shoulder / wrist.

        Four-armed creature, second pair 25 units lower:
            CharacterRig(positions=pos, extra_limbs=[
                {"type": "arm", "label": "lowerArm", "offset": (0, -25, 0),
                 "clavicle": True, "fingers": True}])
        """
        import copy

        def _is_pt(v):
            return (isinstance(v, (list, tuple)) and len(v) == 3
                    and all(isinstance(c, (int, float)) for c in v))

        def _map(d, fn):
            if d is None:
                return None
            out = {}
            for k, v in d.items():
                if isinstance(v, dict):
                    out[k] = _map(v, fn)
                elif _is_pt(v):
                    out[k] = fn(v)
                else:
                    out[k] = copy.deepcopy(v)
            return out

        def _shift(d, o):
            return _map(d, lambda v: (v[0] + o[0], v[1] + o[1], v[2] + o[2]))

        def _mirror(d):
            return _map(d, lambda v: (-v[0], v[1], v[2]))

        # Base-rig attach joints must exist before ANY extra limb is built
        # (joints on earlier extra limbs are made along the way).
        extra_tokens = {str(s["label"]).lower() for s in self.extra_limbs}
        for spec in self.extra_limbs:
            name = self._attach_joint_name(spec.get("parent"))
            if not name:
                continue
            m = re.match(r"^[LRC]_([A-Za-z][A-Za-z0-9]*)", name)
            if m and m.group(1).lower() in extra_tokens:
                continue
            if not cmds.ls(name, type="joint"):
                raise ValueError("extra limb %r: attach joint %r isn't in the "
                                 "rig. Is its module (tail, face, fingers...) "
                                 "turned on?" % (spec["label"], name))

        for spec in self.extra_limbs:            # validated in build()
            kind = spec["type"]
            label = spec["label"]
            par = spec.get("parent", self._EXTRA_DEFAULT_PARENT[kind])
            custom = self._attach_joint_name(par) is not None

            def attach(sd):
                if custom:
                    return self._resolve_attach(par, sd, label, cg)
                if isinstance(par, (list, tuple)):
                    return tuple(par)
                return parents[par]

            p_ctrl, p_jnt = (None, None) if custom else attach("C")
            off = spec.get("offset")
            explicit = spec.get("positions")
            built = {"type": kind}

            if kind == "chain":
                controls = spec.get("controls", "fkik")
                sides = spec.get("side", "LR")
                for sd in (("L", "R") if sides == "LR" else (sides,)):
                    if explicit is not None:
                        pos = copy.deepcopy(explicit)
                        if sides == "LR" and sd == "R":
                            pos = _mirror(pos)
                        count = len([k for k in pos
                                     if str(k).startswith("tail_")])
                    else:
                        count = spec["joints"]
                        pos = TailRig(joint_count=count).positions
                        if sd == "R":
                            pos = _mirror(pos)
                    if off:
                        o = off if sd != "R" else (-off[0], off[1], off[2])
                        pos = _shift(pos, o)
                    c_ctrl, c_jnt = attach(sd)
                    rig = TailRig(positions=pos, joint_count=count,
                                  parent_ctrl=c_ctrl, parent_jnt=c_jnt,
                                  ctrl_grp=cg, jnt_grp=jg, misc_grp=mg,
                                  label=label, side=sd, controls=controls,
                                  ik_follow=True)
                    rig.build()
                    built[sd] = rig
                self.extra_rigs[label] = built
                print("[CharacterRig] chain %r built (%s, %s controls)"
                      % (label, "+".join(k for k in built if k != "type"),
                         controls))
                continue

            if kind == "tail":
                if custom:
                    p_ctrl, p_jnt = attach("C")
                pos = copy.deepcopy(explicit if explicit is not None else
                                    (self.tail_positions or TailRig().positions))
                if off:
                    pos = _shift(pos, off)
                count = len([k for k in pos if str(k).startswith("tail_")])
                rig = TailRig(positions=pos, joint_count=count,
                              parent_ctrl=p_ctrl, parent_jnt=p_jnt,
                              ctrl_grp=cg, jnt_grp=jg, misc_grp=mg,
                              label=label)
                rig.build()
                built["C"] = rig
                self.extra_rigs[label] = built
                print("[CharacterRig] extra tail %r built" % label)
                continue

            sides = spec.get("side", "LR")
            for sd in (("L", "R") if sides == "LR" else (sides,)):
                if custom:
                    p_ctrl, p_jnt = attach(sd)
                o = None
                if off:
                    o = off if sd == "L" else (-off[0], off[1], off[2])

                if explicit is not None:
                    pos = copy.deepcopy(explicit)
                    if sides == "LR" and sd == "R":
                        pos = _mirror(pos)
                else:
                    base = getattr(self, "%s_%s_positions" % (sd, kind))
                    mod = ArmRig if kind == "arm" else LegRig
                    pos = (copy.deepcopy(base) if base
                           else mod(side=sd).positions)
                if o:
                    pos = _shift(pos, o)

                if kind == "leg":
                    rig = LegRig(side=sd, positions=pos,
                                 parent_ctrl=p_ctrl, parent_jnt=p_jnt,
                                 ctrl_grp=cg, jnt_grp=jg, misc_grp=mg,
                                 label=label)
                    rig.build()
                    built[sd] = rig
                    continue

                # Clavicle + fingers ride along with wherever THIS arm's
                # shoulder / wrist ended up (offset or moved guides), unless
                # the spec gives their positions outright.
                abase = (getattr(self, "%s_arm_positions" % sd)
                         or ArmRig(side=sd).positions)

                def _delta(key):
                    return tuple(a - b for a, b in zip(pos[key], abase[key]))

                def _given(key):
                    d = spec.get(key)
                    if d is None:
                        return None
                    d = copy.deepcopy(d)
                    return _mirror(d) if sides == "LR" and sd == "R" else d

                arm_ctrl, arm_jnt = p_ctrl, p_jnt
                if spec.get("clavicle"):
                    cpos = _given("clavicle_positions")
                    if cpos is None:
                        cbase = getattr(self, "%s_clavicle_positions" % sd)
                        cpos = _shift(copy.deepcopy(cbase) if cbase
                                      else ClavicleRig(side=sd).positions,
                                      _delta("shoulder"))
                    cpos["clavicle_tip"] = tuple(pos["shoulder"])
                    clav = ClavicleRig(side=sd, positions=cpos,
                                       parent_ctrl=p_ctrl, parent_jnt=p_jnt,
                                       ctrl_grp=cg, jnt_grp=jg, label=label)
                    clav.build()
                    built["%s_clavicle" % sd] = clav
                    arm_ctrl, arm_jnt = clav.ctrl, clav.tip_jnt

                fpos = None
                if spec.get("fingers"):
                    fbase = getattr(self, "%s_finger_positions" % sd)
                    fpos = _given("finger_positions")
                    if fpos is None and fbase:
                        fpos = _shift(copy.deepcopy(fbase), _delta("wrist"))
                    if fpos is None:
                        cmds.warning("extra arm %r: this rig has no finger "
                                     "guide positions, so it is built "
                                     "without fingers" % label)

                rig = ArmRig(side=sd, positions=pos,
                             parent_ctrl=arm_ctrl, parent_jnt=arm_jnt,
                             ctrl_grp=cg, jnt_grp=jg, misc_grp=mg,
                             finger_positions=fpos, label=label)
                rig.build()
                built[sd] = rig

            self.extra_rigs[label] = built
            print("[CharacterRig] extra %s %r built (%s)"
                  % (kind, label, "+".join(k for k in built if k != "type")))

    def delete(self):
        if cmds.objExists("CHARACTER_RIG_GRP"):
            cmds.delete("CHARACTER_RIG_GRP")
            print("[CharacterRig] Deleted CHARACTER_RIG_GRP")

    # ------------------------------------------------------------------------
    # Auto-walk — procedural walk cycle driven by the root ctrl
    # ------------------------------------------------------------------------

    @staticmethod
    def _sdk_walk(driven_attr, driver, keys, tangent):
        """Set-driven-key a normalized walk-cycle curve and make it loop.

        `keys` is a list of (driverValue, value) pairs spanning one cycle
        (driver 0.0 → 1.0). The resulting animCurve gets cycle infinity
        on both ends, so as the driver keeps climbing past 1.0 the curve
        repeats forever — that's what turns a steadily-increasing root
        translation into an endless walk."""
        for dv, val in keys:
            cmds.setDrivenKeyframe(driven_attr, currentDriver=driver,
                                   driverValue=dv, value=val,
                                   itt=tangent, ott=tangent)
        crvs = cmds.listConnections(driven_attr, s=True, d=False,
                                    type="animCurve") or []
        if crvs:
            # setInfinity resolves anim curves from a node + attribute —
            # passing the anim-curve node itself finds nothing and the
            # curve just holds its last value past phase 1.0. Split the
            # driven plug back into node + attribute so the cycle
            # actually loops as the root keeps moving forward.
            node, attr = driven_attr.split(".", 1)
            cmds.setInfinity(node, attribute=attr,
                             preInfinite="cycle", postInfinite="cycle")

    def _build_auto_walk(self):
        """Procedural walk cycle on the root ctrl (C_global_CTRL).

        Turn on `autoWalk` and translate the root forward (+Z): the feet
        step procedurally and the body bobs. The cycle phase is
        rootZ / walkStride, so the walk speed exactly tracks how fast
        the root moves — move it slowly, slow walk; fast, fast walk;
        stop, the feet stop.

        How a planted foot stays put: during the contact part of the
        cycle the foot's AUTO.translateZ slides backward at a rate that
        exactly cancels the root's forward travel, so the foot looks
        glued to the ground while the body passes over it. During swing
        the foot lifts (Y arc) and snaps forward to the next plant.

        NOTE: the legs must be in IK mode (the default) — auto-walk
        drives the foot IK ctrls. `autoWalk` = 0 leaves everything at
        rest, so manual animation is unaffected when it's off.
        """
        g = self.core.global_ctrl
        cog = self.core.cog_ctrl

        # ---- attributes on the root ctrl ----
        cmds.addAttr(g, ln="autoWalk", at="double",
                     min=0, max=1, dv=0, k=True)
        cmds.addAttr(g, ln="walkStride", at="double",
                     min=1, dv=35, k=True)
        cmds.addAttr(g, ln="walkStepHeight", at="double",
                     min=0, dv=10, k=True)
        cmds.addAttr(g, ln="walkBodyBob", at="double",
                     min=0, dv=3, k=True)
        # Hidden attrs that hold the normalized SDK cycle outputs.
        for a in ("walkNrmZL", "walkNrmYL", "walkNrmZR",
                  "walkNrmYR", "walkNrmBob"):
            cmds.addAttr(g, ln=a, at="double", dv=0)
            cmds.setAttr(f"{g}.{a}", k=False, cb=False)

        # ---- phase = rootZ / walkStride ;  phaseR = phase + 0.5 ----
        md_phase = cmds.createNode("multiplyDivide",
                                   n="C_autoWalk_phase_MD")
        cmds.setAttr(f"{md_phase}.operation", 2)   # divide
        cmds.connectAttr(f"{g}.translateZ", f"{md_phase}.input1X")
        cmds.connectAttr(f"{g}.walkStride", f"{md_phase}.input2X")
        phase = f"{md_phase}.outputX"

        adl_pr = cmds.createNode("addDoubleLinear",
                                 n="C_autoWalk_phaseR_ADL")
        cmds.connectAttr(phase, f"{adl_pr}.input1")
        cmds.setAttr(f"{adl_pr}.input2", 0.5)
        phase_r = f"{adl_pr}.output"

        # ---- normalized walk-cycle SDK curves ----
        # Z slide: +0.3 (planted, forward) → -0.3 (planted, back; end of
        # contact at phase 0.6) → +0.3 (swung forward by phase 1.0). The
        # straight -1.0 slope through contact cancels the body's forward
        # travel. LINEAR tangents keep that slope exact (no slipping).
        z_keys = [(0.0, 0.3), (0.6, -0.3), (1.0, 0.3)]
        # Y lift: flat on the ground through contact, arcs up mid-swing.
        y_keys = [(0.0, 0.0), (0.6, 0.0), (0.8, 1.0), (1.0, 0.0)]
        # Body bob: dips once per footfall — twice per full cycle.
        bob_keys = [(0.0, 0.0), (0.25, -1.0), (0.5, 0.0),
                    (0.75, -1.0), (1.0, 0.0)]

        self._sdk_walk(f"{g}.walkNrmZL", phase,   z_keys,   "linear")
        self._sdk_walk(f"{g}.walkNrmYL", phase,   y_keys,   "spline")
        self._sdk_walk(f"{g}.walkNrmZR", phase_r, z_keys,   "linear")
        self._sdk_walk(f"{g}.walkNrmYR", phase_r, y_keys,   "spline")
        self._sdk_walk(f"{g}.walkNrmBob", phase,  bob_keys, "spline")

        # ---- combined scale factors (amount = setting × autoWalk) ----
        def amount(node_name, setting_attr):
            mdl = cmds.createNode("multDoubleLinear", n=node_name)
            cmds.connectAttr(f"{g}.{setting_attr}", f"{mdl}.input1")
            cmds.connectAttr(f"{g}.autoWalk", f"{mdl}.input2")
            return f"{mdl}.output"

        stride_amt = amount("C_autoWalk_strideAmt_MDL", "walkStride")
        height_amt = amount("C_autoWalk_heightAmt_MDL", "walkStepHeight")
        bob_amt    = amount("C_autoWalk_bobAmt_MDL", "walkBodyBob")

        # ---- insert AUTO groups + wire the cycle in ----
        def insert_auto(ctrl):
            """Insert a *_walkAUTO group between a ctrl and its offset so
            the procedural cycle composes UNDER the animator's ctrl.
            The ctrl (foot, COG) is world-aligned, so the AUTO is too."""
            offset = cmds.listRelatives(ctrl, p=True)[0]
            auto = cmds.group(em=True, n=ctrl.replace("_CTRL", "_walkAUTO"))
            cmds.matchTransform(auto, ctrl)
            cmds.parent(auto, offset)
            cmds.parent(ctrl, auto)
            return auto

        def insert_world_auto(ctrl):
            """Insert a WORLD-ALIGNED *_walkAUTO group ABOVE the ctrl's
            offset. Used for the arm IK ctrls — those are joint-oriented
            (matchTransform copied the wrist orient), so a naive AUTO
            would swing the hand along the wrong axis. A world-aligned
            group parented under the world-aligned controls_GRP gives a
            clean world +Z = forward translation channel."""
            offset = cmds.listRelatives(ctrl, p=True)[0]
            grandparent = cmds.listRelatives(offset, p=True)
            auto = cmds.group(em=True, n=ctrl.replace("_CTRL", "_walkAUTO"))
            # Left at world origin, identity rotation = world-aligned.
            if grandparent:
                cmds.parent(auto, grandparent[0])
            cmds.parent(offset, auto)
            return auto

        def mul(name, a, b):
            mdl = cmds.createNode("multDoubleLinear", n=name)
            cmds.connectAttr(a, f"{mdl}.input1")
            cmds.connectAttr(b, f"{mdl}.input2")
            return f"{mdl}.output"

        def wire_foot(leg, nrm_z, nrm_y):
            auto = insert_auto(leg.ik_ctrl)
            cmds.connectAttr(
                mul(f"{leg.prefix}_walkZ_MDL", f"{g}.{nrm_z}", stride_amt),
                f"{auto}.translateZ")
            cmds.connectAttr(
                mul(f"{leg.prefix}_walkY_MDL", f"{g}.{nrm_y}", height_amt),
                f"{auto}.translateY")

        wire_foot(self.L_leg, "walkNrmZL", "walkNrmYL")
        wire_foot(self.R_leg, "walkNrmZR", "walkNrmYR")

        # Extra leg pairs (centaur, spider...) alternate: the 1st extra pair
        # steps opposite the base legs (diagonal trot), the 2nd matches them,
        # and so on, so the feet never all lift together.
        extra_legs = [r for r in self.extra_rigs.values()
                      if r.get("type") == "leg"]
        for i, rigs in enumerate(extra_legs, 1):
            swap = i % 2 == 1
            for sd, leg in ((s, rigs[s]) for s in ("L", "R") if s in rigs):
                left = (sd == "L") != swap
                wire_foot(leg, "walkNrmZL" if left else "walkNrmZR",
                          "walkNrmYL" if left else "walkNrmYR")

        # Body bob on the COG (feet stay grounded — they're not under it).
        cog_auto = insert_auto(cog)
        cmds.connectAttr(
            mul("C_autoWalk_bob_MDL", f"{g}.walkNrmBob", bob_amt),
            f"{cog_auto}.translateY")

        # ---- arm swing (IK hand ctrls, contralateral to the legs) ----
        # Arms swing opposite to the legs: when the left leg steps
        # forward the RIGHT arm swings forward. The hand IK ctrl just
        # oscillates forward/back in world Z (no contact cancellation —
        # hands swing free) and the IK solver bends the elbow.
        # Arms must be in IK mode (the default) for this to show.
        if self.L_arm and self.R_arm:
            cmds.addAttr(g, ln="walkArmSwing", at="double",
                         min=0, dv=15, k=True)
            for a in ("walkNrmArmL", "walkNrmArmR"):
                cmds.addAttr(g, ln=a, at="double", dv=0)
                cmds.setAttr(f"{g}.{a}", k=False, cb=False)
            # Smooth sine-like swing: hand forward at phase 0, back at 0.5.
            arm_keys = [(0.0, 1.0), (0.5, -1.0), (1.0, 1.0)]
            # L arm uses phaseR (swings with the right leg); R arm uses
            # phase (swings with the left leg) — proper contralateral gait.
            self._sdk_walk(f"{g}.walkNrmArmL", phase_r, arm_keys, "spline")
            self._sdk_walk(f"{g}.walkNrmArmR", phase,   arm_keys, "spline")
            arm_amt = amount("C_autoWalk_armAmt_MDL", "walkArmSwing")
            arm_pairs = [(self.L_arm, "walkNrmArmL"),
                         (self.R_arm, "walkNrmArmR")]
            # Extra arms swing with the base arm on their side.
            for rigs in self.extra_rigs.values():
                if rigs.get("type") == "arm":
                    arm_pairs += [(rigs[s], "walkNrmArm" + s)
                                  for s in ("L", "R") if s in rigs]
            for arm, nrm in arm_pairs:
                auto = insert_world_auto(arm.ik_ctrl)
                cmds.connectAttr(
                    mul(f"{arm.prefix}_walkArm_MDL",
                        f"{g}.{nrm}", arm_amt),
                    f"{auto}.translateZ")

        print("[CharacterRig] Auto-walk wired to C_global_CTRL "
              "(autoWalk / walkStride / walkStepHeight / walkBodyBob / "
              "walkArmSwing).")

    # Standard-mode face joint names that heavy mode REPLACES with
    # curve-driven joints (`L_lidUpper_01_BIND_JNT`, etc). When heavy mode
    # is active the diagnostic skips these silently instead of reporting
    # them as MISSING (which was confusing — they're absent by design).
    _HEAVY_REPLACED_JOINTS = frozenset((
        "L_eyelidUpperInner_BIND_JNT", "L_eyelidUpperMid_BIND_JNT",
        "L_eyelidUpperOuter_BIND_JNT", "L_eyelidLowerInner_BIND_JNT",
        "L_eyelidLowerMid_BIND_JNT",   "L_eyelidLowerOuter_BIND_JNT",
        "R_eyelidUpperInner_BIND_JNT", "R_eyelidUpperMid_BIND_JNT",
        "R_eyelidUpperOuter_BIND_JNT", "R_eyelidLowerInner_BIND_JNT",
        "R_eyelidLowerMid_BIND_JNT",   "R_eyelidLowerOuter_BIND_JNT",
        "L_mouthCorner_BIND_JNT",      "R_mouthCorner_BIND_JNT",
        "C_upperLip_BIND_JNT",
        "L_upperLipMid_BIND_JNT",      "R_upperLipMid_BIND_JNT",
        "C_lowerLip_BIND_JNT",
        "L_lowerLipMid_BIND_JNT",      "R_lowerLipMid_BIND_JNT",
    ))

    def diagnose(self):
        """Print every BIND joint's world position alongside its source guide.
        Use this when joints look misaligned to confirm whether the build
        consumed the guide values correctly.
        """
        print("\n[CharacterRig] Diagnostic — BIND joint world positions:")

        # Detect heavy mode by checking for curve-driven joint names
        heavy_active = bool(cmds.ls("L_lidUpper_01_BIND_JNT")
                            or cmds.ls("R_lidUpper_01_BIND_JNT")
                            or cmds.ls("C_upperLip_01_BIND_JNT"))
        if heavy_active:
            print("  (heavy face rig detected — standard-mode eyelid/lip "
                  "joints are replaced; see [heavy face joints] section)")

        def _check(joint_name, guide_name=None):
            # Skip standard-mode joints that heavy mode intentionally
            # replaces — reporting them as MISSING is misleading.
            if heavy_active and joint_name in self._HEAVY_REPLACED_JOINTS:
                return
            if not cmds.objExists(joint_name):
                print(f"  {joint_name:40s}  MISSING")
                return
            jpos = cmds.xform(joint_name, q=True, ws=True, t=True)
            jpos_str = f"({jpos[0]:7.2f}, {jpos[1]:7.2f}, {jpos[2]:7.2f})"
            if guide_name and cmds.objExists(guide_name):
                gpos = cmds.xform(guide_name, q=True, ws=True, t=True)
                delta = [j - g for j, g in zip(jpos, gpos)]
                ok = all(abs(d) < 0.01 for d in delta)
                marker = "OK " if ok else "OFF"
                print(f"  {joint_name:40s}  {jpos_str}   "
                      f"vs guide {guide_name:24s}  delta=({delta[0]:+.2f}, "
                      f"{delta[1]:+.2f}, {delta[2]:+.2f})  [{marker}]")
            else:
                print(f"  {joint_name:40s}  {jpos_str}")

        pairs = [
            ("C_pelvis_BIND_JNT",        "C_pelvis_GUIDE"),
            ("C_chest_BIND_JNT",         "C_chest_GUIDE"),
            ("C_neck_BIND_JNT",          "C_neck_GUIDE"),
            ("C_head_BIND_JNT",          "C_head_GUIDE"),
            ("L_clavicle_BIND_JNT",      "L_clavicle_GUIDE"),
            ("R_clavicle_BIND_JNT",      "R_clavicle_GUIDE"),
            ("L_arm_shoulder_BIND_JNT",  "L_shoulder_GUIDE"),
            ("L_arm_elbow_BIND_JNT",     "L_elbow_GUIDE"),
            ("L_arm_wrist_BIND_JNT",     "L_wrist_GUIDE"),
            ("R_arm_shoulder_BIND_JNT",  "R_shoulder_GUIDE"),
            ("R_arm_elbow_BIND_JNT",     "R_elbow_GUIDE"),
            ("R_arm_wrist_BIND_JNT",     "R_wrist_GUIDE"),
            ("L_leg_hip_BIND_JNT",       "L_hip_GUIDE"),
            ("L_leg_knee_BIND_JNT",      "L_knee_GUIDE"),
            ("L_leg_ankle_BIND_JNT",     "L_ankle_GUIDE"),
            ("L_leg_ball_BIND_JNT",      "L_ball_GUIDE"),
            ("L_leg_toe_BIND_JNT",       "L_toe_GUIDE"),
            ("R_leg_hip_BIND_JNT",       "R_hip_GUIDE"),
            ("R_leg_knee_BIND_JNT",      "R_knee_GUIDE"),
            ("R_leg_ankle_BIND_JNT",     "R_ankle_GUIDE"),
            ("R_leg_ball_BIND_JNT",      "R_ball_GUIDE"),
            ("R_leg_toe_BIND_JNT",       "R_toe_GUIDE"),
            # Core root (fallback parent when spine is disabled)
            ("C_root_BIND_JNT",          None),
            # Face — jaw + eyes
            ("C_jaw_BIND_JNT",           "C_jaw_GUIDE"),
            ("L_eye_BIND_JNT",           "L_eye_GUIDE"),
            ("R_eye_BIND_JNT",           "R_eye_GUIDE"),
            # Detailed eyelids (12 joints)
            ("L_eyelidUpperInner_BIND_JNT", "L_eyelidUpperInner_GUIDE"),
            ("L_eyelidUpperMid_BIND_JNT",   "L_eyelidUpperMid_GUIDE"),
            ("L_eyelidUpperOuter_BIND_JNT", "L_eyelidUpperOuter_GUIDE"),
            ("L_eyelidLowerInner_BIND_JNT", "L_eyelidLowerInner_GUIDE"),
            ("L_eyelidLowerMid_BIND_JNT",   "L_eyelidLowerMid_GUIDE"),
            ("L_eyelidLowerOuter_BIND_JNT", "L_eyelidLowerOuter_GUIDE"),
            ("R_eyelidUpperInner_BIND_JNT", "R_eyelidUpperInner_GUIDE"),
            ("R_eyelidUpperMid_BIND_JNT",   "R_eyelidUpperMid_GUIDE"),
            ("R_eyelidUpperOuter_BIND_JNT", "R_eyelidUpperOuter_GUIDE"),
            ("R_eyelidLowerInner_BIND_JNT", "R_eyelidLowerInner_GUIDE"),
            ("R_eyelidLowerMid_BIND_JNT",   "R_eyelidLowerMid_GUIDE"),
            ("R_eyelidLowerOuter_BIND_JNT", "R_eyelidLowerOuter_GUIDE"),
            # Brow
            ("L_browInner_BIND_JNT",     "L_browInner_GUIDE"),
            ("L_browMid_BIND_JNT",       "L_browMid_GUIDE"),
            ("L_browOuter_BIND_JNT",     "L_browOuter_GUIDE"),
            ("R_browInner_BIND_JNT",     "R_browInner_GUIDE"),
            ("R_browMid_BIND_JNT",       "R_browMid_GUIDE"),
            ("R_browOuter_BIND_JNT",     "R_browOuter_GUIDE"),
            # Mouth + Lips
            ("L_mouthCorner_BIND_JNT",   "L_mouthCorner_GUIDE"),
            ("R_mouthCorner_BIND_JNT",   "R_mouthCorner_GUIDE"),
            ("C_upperLip_BIND_JNT",      "C_upperLip_GUIDE"),
            ("L_upperLipMid_BIND_JNT",   "L_upperLipMid_GUIDE"),
            ("R_upperLipMid_BIND_JNT",   "R_upperLipMid_GUIDE"),
            ("C_lowerLip_BIND_JNT",      "C_lowerLip_GUIDE"),
            ("L_lowerLipMid_BIND_JNT",   "L_lowerLipMid_GUIDE"),
            ("R_lowerLipMid_BIND_JNT",   "R_lowerLipMid_GUIDE"),
            # Cheeks
            ("L_cheek_BIND_JNT",         "L_cheek_GUIDE"),
            ("R_cheek_BIND_JNT",         "R_cheek_GUIDE"),
            # Nose
            ("C_noseTip_BIND_JNT",       "C_noseTip_GUIDE"),
            ("L_nostril_BIND_JNT",       "L_nostril_GUIDE"),
            ("R_nostril_BIND_JNT",       "R_nostril_GUIDE"),
            # Tongue
            ("C_tongue_01_BIND_JNT",     "C_tongue01_GUIDE"),
            ("C_tongue_02_BIND_JNT",     "C_tongue02_GUIDE"),
            ("C_tongue_03_BIND_JNT",     "C_tongue03_GUIDE"),
        ]
        for jnt, guide in pairs:
            _check(jnt, guide)

        # ---- Heavy face joints (curve-driven, no 1:1 guide mapping) ----
        # In heavy mode, eyelid + lip joints don't match the standard names
        # above (they're sampled along curves). Report counts here so the
        # rigger can see they were built correctly.
        heavy_lid_jnts = (cmds.ls("*_lidUpper_*_BIND_JNT") or []) + \
                        (cmds.ls("*_lidLower_*_BIND_JNT") or [])
        heavy_lip_jnts = (cmds.ls("C_upperLip_*_BIND_JNT") or []) + \
                        (cmds.ls("C_lowerLip_*_BIND_JNT") or [])
        # Filter to ones with a purely-numeric token (curve detail joints
        # are named e.g. C_upperLip_01_BIND_JNT — the "01" is the digit
        # token). Previous filter checked the wrong split index.
        heavy_lip_jnts = [j for j in heavy_lip_jnts
                          if any(t.isdigit() for t in j.split("_"))]

        if heavy_lid_jnts or heavy_lip_jnts:
            print()
            print(f"  [heavy face joints]")
            print(f"    eyelid curve-driven joints: {len(heavy_lid_jnts)} "
                  f"(L+R, expect 64)")
            print(f"    lip curve-driven joints:    {len(heavy_lip_jnts)} "
                  f"(upper+lower, expect 32)")
            if heavy_lid_jnts:
                sample = heavy_lid_jnts[0]
                y = cmds.xform(sample, q=True, ws=True, t=True)[1]
                ok = "OK " if 150 < y < 175 else "OFF"
                print(f"    sanity: {sample} Y={y:.2f}  "
                      f"(expect ~160-165)  [{ok}]")
            if heavy_lip_jnts:
                # Check a center lip joint specifically
                center_lips = [j for j in heavy_lip_jnts
                               if "lowerLip" in j or "upperLip" in j]
                if center_lips:
                    sample = center_lips[0]
                    pos = cmds.xform(sample, q=True, ws=True, t=True)
                    ok = "OK " if 148 < pos[1] < 155 else "OFF"
                    print(f"    sanity: {sample} Y={pos[1]:.2f}  "
                          f"(expect ~150-153)  [{ok}]")
        print()


# =============================================================================
# Run directly
# =============================================================================

if __name__ == "__main__":
    rig = CharacterRig()
    rig.build()
