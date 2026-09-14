"""
===============================================================================
 RIG POSE LIBRARY — save / apply / mirror poses (and clips) on disk
===============================================================================

 Captures the keyable values of every rig control to a JSON file so poses
 survive scene + rebuild, and can be re-applied to the whole rig or just the
 selected controls. Also flips a pose left<->right (a true world-matrix swap,
 reusing rig_pose_tools' behaviour-mirror).

   save_pose("t_pose")                 -> ~/danyal_rig_poses/t_pose.json
   list_poses()                        -> ["t_pose", ...]
   apply_pose_file("t_pose")           -> whole rig
   apply_pose_file("wave", selected_only=True)
   apply_pose_file("wave", mirror=True)  -> applied, flipped L<->R
   flip_pose_lr()                      -> swap current L/R pose in place

 Works on any built rig (biped / quadruped / bird / vehicle) — controls are
 discovered as the _CTRL transforms under the known rig roots.
===============================================================================
"""

import json
import os

import maya.cmds as cmds
import rig_pose_tools as rpt   # reuse _opp / _settable / world-mirror helpers

RIG_ROOTS = ("CHARACTER_RIG_GRP", "QUADRUPED_RIG_GRP",
             "BIRD_RIG_GRP", "VEHICLE_RIG_GRP")

POSE_DIR = os.path.join(os.path.expanduser("~"), "danyal_rig_poses")


# =============================================================================
# Control discovery + pose read/apply
# =============================================================================

def _roots():
    return [g for g in RIG_ROOTS if cmds.objExists(g)]


def all_controls():
    """Every _CTRL transform under any built rig root."""
    out = []
    for root in _roots():
        for c in (cmds.listRelatives(root, ad=True, type="transform") or []):
            if c.endswith("_CTRL"):
                out.append(c)
    return out


def read_pose(controls=None):
    """{ctrl: {attr: value}} for the keyable, unlocked, numeric attrs of
    each control."""
    controls = controls if controls is not None else all_controls()
    data = {}
    for c in controls:
        if not cmds.objExists(c):
            continue
        attrs = {}
        for a in (cmds.listAttr(c, k=True, u=True) or []):
            try:
                v = cmds.getAttr(f"{c}.{a}")
            except Exception:
                continue
            if isinstance(v, bool):
                attrs[a] = int(v)
            elif isinstance(v, (int, float)):
                attrs[a] = v
        if attrs:
            data[c] = attrs
    return data


def apply_pose(data, selected_only=False):
    """Set the stored attr values back onto the controls. If selected_only,
    only controls that are currently selected are touched. Returns the
    number of attributes set."""
    targets = None
    if selected_only:
        targets = set(cmds.ls(sl=True) or [])
        if not targets:
            cmds.warning("[pose] selected_only: nothing selected.")
            return 0
    n = 0
    for c, attrs in data.items():
        if targets is not None and c not in targets:
            continue
        if not cmds.objExists(c):
            continue
        for a, v in attrs.items():
            if rpt._settable(c, a):
                try:
                    cmds.setAttr(f"{c}.{a}", v)
                    n += 1
                except Exception:
                    pass
    return n


# =============================================================================
# Disk
# =============================================================================

def _path(name, folder=None):
    folder = folder or POSE_DIR
    if name.endswith(".json"):
        return name if os.path.isabs(name) else os.path.join(folder, name)
    return os.path.join(folder, name + ".json")


def save_pose(name, folder=None, controls=None, thumbnail=False):
    """Write the current pose to <folder>/<name>.json. Optionally playblast
    a single-frame thumbnail beside it. Returns the json path."""
    folder = folder or POSE_DIR
    if not os.path.isdir(folder):
        os.makedirs(folder)
    data = read_pose(controls)
    path = _path(name, folder)
    with open(path, "w") as f:
        json.dump(data, f, indent=1)
    if thumbnail:
        _save_thumbnail(path[:-5] + ".png")
    print(f"[pose] Saved '{name}' ({len(data)} controls) -> {path}")
    return path


def load_pose(name, folder=None):
    with open(_path(name, folder)) as f:
        return json.load(f)


def list_poses(folder=None):
    folder = folder or POSE_DIR
    if not os.path.isdir(folder):
        return []
    return sorted(f[:-5] for f in os.listdir(folder) if f.endswith(".json"))


def delete_pose(name, folder=None):
    p = _path(name, folder)
    removed = False
    for f in (p, p[:-5] + ".png"):
        if os.path.isfile(f):
            os.remove(f)
            removed = True
    return removed


def apply_pose_file(name, folder=None, selected_only=False, mirror=False):
    """Load + apply a saved pose. mirror=True applies it flipped L<->R."""
    data = load_pose(name, folder)
    n = apply_pose(data, selected_only=selected_only)
    if mirror and not selected_only:
        flip_pose_lr()
    print(f"[pose] Applied '{name}' ({n} attrs"
          + (", mirrored" if mirror else "") + ").")
    return n


def _save_thumbnail(png_path):
    """Best-effort single-frame playblast thumbnail (no-op if headless)."""
    try:
        cmds.playblast(frame=cmds.currentTime(q=True), format="image",
                       completeFilename=png_path, widthHeight=(160, 160),
                       showOrnaments=False, viewer=False, percent=100,
                       compression="png", quality=70)
    except Exception:
        pass


# =============================================================================
# Mirror / flip  (true world-matrix swap, reusing rig_pose_tools)
# =============================================================================

def _apply_world_mirror(ctrl, world_mtx):
    """Place ctrl at the behaviour-mirror of world_mtx. Rotation-only ctrls
    (no free translate) take just the mirrored rotation."""
    mir = rpt._mirror_world_matrix(world_mtx)
    if rpt._has_free_translate(ctrl):
        rpt._set_world_matrix(ctrl, mir)
    else:
        # keep its (locked) translate, take mirrored world rotation only
        cur = rpt._get_world_matrix(ctrl)
        import maya.api.OpenMaya as om
        tm_cur = om.MTransformationMatrix(cur)
        tm_mir = om.MTransformationMatrix(mir)
        tm_cur.setRotation(tm_mir.rotation())
        rpt._set_world_matrix(ctrl, tm_cur.asMatrix())


def flip_pose_lr():
    """Swap the LEFT and RIGHT control poses across X=0 (a true mirror swap),
    and mirror the CENTER controls in place. Settings controls swap their
    unlocked attrs L<->R. Returns the number of controls changed."""
    ctrls = all_controls()
    if not ctrls:
        cmds.warning("[pose] No rig controls found.")
        return 0

    # Snapshot first so the swap isn't order-dependent.
    snap = {c: rpt._get_world_matrix(c) for c in ctrls
            if "_SETTINGS_CTRL" not in c}

    n = 0
    done = set()
    for c in ctrls:
        if c in done:
            continue
        if "_SETTINGS_CTRL" in c:
            opp = rpt._opp(c)
            if opp and cmds.objExists(opp):
                la = cmds.listAttr(c, k=True, u=True) or []
                vals_c = {a: cmds.getAttr(f"{c}.{a}") for a in la
                          if rpt._settable(c, a)}
                lb = cmds.listAttr(opp, k=True, u=True) or []
                vals_o = {a: cmds.getAttr(f"{opp}.{a}") for a in lb
                          if rpt._settable(opp, a)}
                for a, v in vals_o.items():
                    if rpt._settable(c, a):
                        cmds.setAttr(f"{c}.{a}", v)
                for a, v in vals_c.items():
                    if rpt._settable(opp, a):
                        cmds.setAttr(f"{opp}.{a}", v)
                done.update((c, opp))
                n += 2
            continue

        opp = rpt._opp(c)
        if opp and cmds.objExists(opp) and opp in snap:
            # swap: c <- mirror(opp), opp <- mirror(c)
            _apply_world_mirror(c, snap[opp])
            _apply_world_mirror(opp, snap[c])
            la = rpt.LENGTH_ATTR                  # FK bone length, if any
            if rpt._settable(c, la) and rpt._settable(opp, la):
                lc, lo = cmds.getAttr(f"{c}.{la}"), cmds.getAttr(f"{opp}.{la}")
                cmds.setAttr(f"{c}.{la}", lo)
                cmds.setAttr(f"{opp}.{la}", lc)
            done.update((c, opp))
            n += 2
        elif opp is None:
            # center control — mirror in place
            _apply_world_mirror(c, snap[c])
            done.add(c)
            n += 1
    print(f"[pose] Flipped {n} controls L<->R.")
    return n
