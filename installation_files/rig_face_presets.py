"""
===============================================================================
 RIG FACE PRESETS — one-click expressions (smile / angry / surprised / kiss...)
===============================================================================

 Each expression is a set of FACE DIAL values:
   advanced-face mouth : C_mouth_CTRL.smile / pucker / lipRoll / zip
   standard brows      : L/R_brow{Inner,Mid,Outer}_CTRL.raise (+ .furrow inner)
   standard cheeks     : L/R_cheek_CTRL.puff / cheekRaise
   jaw                 : C_jaw_CTRL.rotateX (open) / jawSide / jawThrust
   eyes                : L/R_blink_CTRL.blink

 Applying an expression zeros every dial any preset uses first (so expressions
 don't accumulate), then sets that expression's dials. Missing controls / attrs
 are skipped, so a preset works whether the advanced face, the standard face,
 or both are in the scene.

   apply_expression("smile")            -> set the smile dials
   apply_expression("angry", reset_first=False)   -> layer, don't reset
   list_expressions()                   -> ordered names
   install_expression_poses()           -> write them into the pose library
===============================================================================
"""

import maya.cmds as cmds


# expression -> {"node.attr": value}. Order of names below is the UI order.
EXPRESSIONS = {
    "neutral": {},                       # special: just zeros all dials
    "smile": {
        "C_mouth_CTRL.smile": 1.0,
        "L_cheek_CTRL.cheekRaise": 0.6, "R_cheek_CTRL.cheekRaise": 0.6,
        "L_browOuter_CTRL.raise": 0.2, "R_browOuter_CTRL.raise": 0.2,
    },
    "sad": {
        "C_mouth_CTRL.smile": -0.6,
        "L_browInner_CTRL.raise": 0.7, "R_browInner_CTRL.raise": 0.7,
        "L_browOuter_CTRL.raise": -0.3, "R_browOuter_CTRL.raise": -0.3,
    },
    "angry": {
        "C_mouth_CTRL.smile": -0.5,
        "C_mouth_CTRL.lipRoll": -0.4,
        "L_browInner_CTRL.furrow": 1.0, "R_browInner_CTRL.furrow": 1.0,
        "L_browInner_CTRL.raise": -0.3, "R_browInner_CTRL.raise": -0.3,
        "L_browOuter_CTRL.raise": -0.2, "R_browOuter_CTRL.raise": -0.2,
        "C_jaw_CTRL.jawThrust": 0.3,
    },
    "surprised": {
        "L_browInner_CTRL.raise": 1.0, "R_browInner_CTRL.raise": 1.0,
        "L_browMid_CTRL.raise": 1.0, "R_browMid_CTRL.raise": 1.0,
        "L_browOuter_CTRL.raise": 0.8, "R_browOuter_CTRL.raise": 0.8,
        "C_jaw_CTRL.rotateX": 18.0,
        "C_mouth_CTRL.pucker": -0.2,
    },
    "kiss": {
        "C_mouth_CTRL.pucker": 1.0,
        "C_mouth_CTRL.lipRoll": 0.5,
        "L_browInner_CTRL.raise": 0.3, "R_browInner_CTRL.raise": 0.3,
    },
}

_ORDER = ("neutral", "smile", "sad", "angry", "surprised", "kiss")

# every dial any preset touches — zeroed on a reset / neutral.
_ALL_DIALS = sorted({k for d in EXPRESSIONS.values() for k in d})


def list_expressions():
    return [n for n in _ORDER if n in EXPRESSIONS]


def _set(path, v):
    node, attr = path.split(".", 1)
    if not (cmds.objExists(node)
            and cmds.attributeQuery(attr, node=node, exists=True)):
        return False
    try:
        if cmds.getAttr(f"{node}.{attr}", lock=True):
            return False
        cmds.setAttr(f"{node}.{attr}", v)
        return True
    except Exception:
        return False


def apply_expression(name, reset_first=True):
    """Set the named expression's face dials. reset_first zeros every dial any
    preset uses first (so expressions don't stack). Returns the # of dials set
    (skips controls / attrs that aren't in the scene)."""
    if name not in EXPRESSIONS:
        cmds.warning("[face] unknown expression '%s'. Have: %s"
                     % (name, ", ".join(list_expressions())))
        return 0
    if reset_first or name == "neutral":
        for path in _ALL_DIALS:
            _set(path, 0.0)
    n = sum(1 for path, v in EXPRESSIONS[name].items() if _set(path, v))
    print("[face] Applied expression '%s' (%d dials)." % (name, n))
    return n


def _pose_data(name):
    """{node: {attr: value}} for an expression — full zeros for neutral."""
    items = ({p: 0.0 for p in _ALL_DIALS} if name == "neutral"
             else EXPRESSIONS[name])
    data = {}
    for path, v in items.items():
        node, attr = path.split(".", 1)
        data.setdefault(node, {})[attr] = v
    return data


def install_expression_poses(folder=None):
    """Write each expression into the pose library as a 'face_<name>' pose so
    they show up next to saved poses (apply via rig_pose_lib.apply_pose_file).
    Returns the pose names written."""
    import json
    import os
    import rig_pose_lib as rpl
    folder = folder or rpl.POSE_DIR
    if not os.path.isdir(folder):
        os.makedirs(folder)
    written = []
    for name in list_expressions():
        pose = "face_" + name
        with open(rpl._path(pose, folder), "w") as f:
            json.dump(_pose_data(name), f, indent=1)
        written.append(pose)
    print("[face] Installed %d expression poses into %s"
          % (len(written), folder))
    return written
