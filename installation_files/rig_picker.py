"""
===============================================================================
 RIG PICKER — anatomical controller selector for Danyal's Rig Builder
===============================================================================

 A separate Qt window. Each button maps to one rig controller and selects
 it on click (Shift+click to add, Ctrl+click to toggle). Buttons are laid
 out anatomically so the picker reads like a body diagram.

 The picker auto-detects which rig type is in the scene (biped /
 quadruped / bird) and shows the matching layout. Two tabs:
   * Body — global, COG, spine, limbs, tail
   * Face — eyes, lids, brows, jaw, mouth, lips, etc.

 Launched from rig_ui.py via the "Open Picker" button, or directly:

     import rig_picker
     from importlib import reload; reload(rig_picker)
     rig_picker.show()

===============================================================================
"""

import contextlib
import maya.cmds as cmds
import maya.OpenMayaUI as omui
import forge_theme as ft
try:
    from PySide2 import QtCore, QtWidgets, QtGui
    from shiboken2 import wrapInstance
except ImportError:                                  # Maya 2025+ (Qt6)
    from PySide6 import QtCore, QtWidgets, QtGui
    from shiboken6 import wrapInstance

import rig_pose_tools
import rig_creature
from importlib import reload as _reload
_reload(rig_pose_tools)
_reload(rig_creature)


WINDOW_OBJECT_NAME = "DanyalRigPickerWindow"


@contextlib.contextmanager
def _undo_chunk():
    cmds.undoInfo(openChunk=True)
    try:
        yield
    finally:
        cmds.undoInfo(closeChunk=True)


# -----------------------------------------------------------------------------
# Colors — match the rig's own draw-override palette so the picker reads
# like the rig itself.
# -----------------------------------------------------------------------------

COLORS = {
    "L":  "#4090f0",   # blue  — left side
    "R":  "#f04040",   # red   — right side
    "C":  "#f0d040",   # yellow — center
    "IK": "#40d060",   # green  — IK ctrls
    "PV": "#e070e0",   # pink   — pole vectors
    "S":  "#a0a0a0",   # gray   — settings ctrls
    "B":  "#d0d090",   # pale yellow — bendy
}


def _maya_main_window():
    ptr = omui.MQtUtil.mainWindow()
    return wrapInstance(int(ptr), QtWidgets.QWidget)


# =============================================================================
# Picker button — one selectable ctrl
# =============================================================================

class PickerButton(QtWidgets.QPushButton):
    """A small button that selects its target ctrl when clicked.

    Modifier behaviour matches the Maya viewport:
      - Plain click      : replace selection with this ctrl
      - Shift + click    : add this ctrl to the selection
      - Ctrl  + click    : toggle this ctrl in/out of the selection
    """

    def __init__(self, ctrl_name, label, color_key, parent=None):
        super(PickerButton, self).__init__(label, parent)
        self.ctrl_name = ctrl_name
        self.color_key = color_key
        col = COLORS.get(color_key, "#888")
        self.setStyleSheet(
            f"QPushButton {{"
            f"  background: {col};"
            f"  border: 1px solid #222;"
            f"  border-radius: 3px;"
            f"  color: #111;"
            f"  font-size: 9px;"
            f"  font-weight: bold;"
            f"  padding: 1px 2px;"
            f"}}"
            f"QPushButton:hover {{ border: 2px solid white; }}"
            f"QPushButton:pressed {{ background: white; }}"
            f"QPushButton:disabled {{ background: #444; color: #777; }}"
        )

    def mousePressEvent(self, event):
        modifiers = event.modifiers()
        if not cmds.objExists(self.ctrl_name):
            cmds.warning(f"[Picker] {self.ctrl_name} not in scene.")
            return
        if modifiers & QtCore.Qt.ShiftModifier:
            cmds.select(self.ctrl_name, add=True)
        elif modifiers & QtCore.Qt.ControlModifier:
            cmds.select(self.ctrl_name, tgl=True)
        else:
            cmds.select(self.ctrl_name, r=True)
        # Don't propagate — we don't want the default button "click" feel
        # to fire and emit clicked() (which would compose oddly with
        # selection state).
        event.accept()


# =============================================================================
# Picker canvas — fixed-size frame holding absolute-positioned buttons
# =============================================================================

class PickerCanvas(QtWidgets.QFrame):
    """Holds the anatomical button layout for one tab (body or face).

    Use add() to drop a PickerButton at an absolute (x, y) position. The
    canvas is a fixed size so the layout coordinates stay stable.
    """

    def __init__(self, w=460, h=620, parent=None):
        super(PickerCanvas, self).__init__(parent)
        self.setFixedSize(w, h)
        self.setStyleSheet(
            "QFrame { background: #1d1d1d; "
            "border: 1px solid #333; border-radius: 4px; }"
        )
        self.buttons = []   # list of PickerButton instances

    def add(self, ctrl_name, label, x, y, w=56, h=18, color="C"):
        btn = PickerButton(ctrl_name, label, color, self)
        btn.setGeometry(x, y, w, h)
        # Disable + grey-out if the target ctrl doesn't exist (yet).
        if not cmds.objExists(ctrl_name):
            btn.setEnabled(False)
            btn.setToolTip(f"{ctrl_name} — not in scene")
        else:
            btn.setToolTip(ctrl_name)
        self.buttons.append(btn)
        return btn

    def clear(self):
        for b in self.buttons:
            b.setParent(None)
            b.deleteLater()
        self.buttons = []


# =============================================================================
# Layout data — per rig type, per tab
#
# Each entry: (ctrl_name, label, x, y, width, height, color_key)
#
# Coordinates assume a 460x620 canvas. The canvas centre is at x=230.
# =============================================================================

# -----------------------------------------------------------------------------
# Biped — body
# -----------------------------------------------------------------------------

BIPED_BODY = [
    # ---- Head + neck ----
    ("C_head_CTRL",      "HEAD",      195,  20, 70, 28, "C"),
    ("C_neck_CTRL",      "NECK",      200,  55, 60, 18, "C"),

    # ---- Spine ----
    ("C_spine_chest_CTRL", "CHEST",   200,  90, 60, 20, "C"),
    ("C_spine_FK_03_CTRL", "SP 3",    205, 125, 50, 16, "C"),
    ("C_spine_FK_02_CTRL", "SP 2",    205, 150, 50, 16, "C"),
    ("C_spine_FK_01_CTRL", "SP 1",    205, 175, 50, 16, "C"),
    ("C_spine_hip_CTRL", "HIP",       200, 205, 60, 20, "C"),
    ("C_spine_mid_CTRL", "SPINE MID", 200, 142, 60, 16, "S"),
    ("C_cog_CTRL",       "COG",       200, 240, 60, 22, "C"),

    # ---- Left clavicle + arm (left side of canvas) ----
    ("L_clavicle_CTRL",   "L CLV",    130, 100, 60, 20, "L"),
    ("L_arm_shoulder_FK_CTRL", "L SH",  72, 135, 56, 18, "L"),
    ("L_arm_elbow_FK_CTRL",    "L EL",  36, 180, 56, 18, "L"),
    ("L_arm_wrist_FK_CTRL",    "L WR",  10, 230, 56, 18, "L"),
    ("L_arm_IK_CTRL",          "L HAND IK", 10, 255, 70, 22, "IK"),
    ("L_arm_PV_CTRL",          "PV",   36, 205, 30, 18, "PV"),
    ("L_arm_SETTINGS_CTRL",    "L SET", 80, 165, 40, 16, "S"),
    ("L_arm_weapon_CTRL",      "L WPN", 30, 285, 56, 16, "B"),

    # ---- Left fingers (compact column at hand) ----
    ("L_thumbProx_FK_CTRL",  "L thumb",  86, 250, 50, 14, "L"),
    ("L_indexProx_FK_CTRL",  "L idx",    86, 268, 50, 14, "L"),
    ("L_middleProx_FK_CTRL", "L mid",    86, 286, 50, 14, "L"),
    ("L_ringProx_FK_CTRL",   "L ring",   86, 304, 50, 14, "L"),
    ("L_pinkyProx_FK_CTRL",  "L pinky",  86, 322, 50, 14, "L"),

    # ---- Right clavicle + arm (mirror — right side of canvas) ----
    ("R_clavicle_CTRL",   "R CLV",    270, 100, 60, 20, "R"),
    ("R_arm_shoulder_FK_CTRL", "R SH", 332, 135, 56, 18, "R"),
    ("R_arm_elbow_FK_CTRL",    "R EL", 368, 180, 56, 18, "R"),
    ("R_arm_wrist_FK_CTRL",    "R WR", 394, 230, 56, 18, "R"),
    ("R_arm_IK_CTRL",          "R HAND IK", 380, 255, 70, 22, "IK"),
    ("R_arm_PV_CTRL",          "PV",  394, 205, 30, 18, "PV"),
    ("R_arm_SETTINGS_CTRL",    "R SET", 340, 165, 40, 16, "S"),
    ("R_arm_weapon_CTRL",      "R WPN", 374, 285, 56, 16, "B"),

    ("R_thumbProx_FK_CTRL",  "R thumb",  324, 250, 50, 14, "R"),
    ("R_indexProx_FK_CTRL",  "R idx",    324, 268, 50, 14, "R"),
    ("R_middleProx_FK_CTRL", "R mid",    324, 286, 50, 14, "R"),
    ("R_ringProx_FK_CTRL",   "R ring",   324, 304, 50, 14, "R"),
    ("R_pinkyProx_FK_CTRL",  "R pinky",  324, 322, 50, 14, "R"),

    # ---- Left leg (down from hip, left side) ----
    ("L_leg_hip_FK_CTRL",   "L HIP FK",  155, 285, 60, 18, "L"),
    ("L_leg_knee_FK_CTRL",  "L KNEE FK", 155, 360, 60, 18, "L"),
    ("L_leg_ankle_FK_CTRL", "L ANK FK",  155, 425, 60, 18, "L"),
    ("L_leg_ball_FK_CTRL",  "L BALL",    155, 455, 60, 16, "L"),
    ("L_leg_IK_CTRL",       "L FOOT IK", 145, 485, 80, 24, "IK"),
    ("L_leg_PV_CTRL",       "L KN PV",   100, 360, 50, 18, "PV"),
    ("L_leg_SETTINGS_CTRL", "L SET",     100, 290, 40, 16, "S"),
    ("L_leg_toeTip_CTRL",   "L toes",    155, 515, 80, 16, "L"),

    # ---- Right leg (mirror) ----
    ("R_leg_hip_FK_CTRL",   "R HIP FK",  245, 285, 60, 18, "R"),
    ("R_leg_knee_FK_CTRL",  "R KNEE FK", 245, 360, 60, 18, "R"),
    ("R_leg_ankle_FK_CTRL", "R ANK FK",  245, 425, 60, 18, "R"),
    ("R_leg_ball_FK_CTRL",  "R BALL",    245, 455, 60, 16, "R"),
    ("R_leg_IK_CTRL",       "R FOOT IK", 235, 485, 80, 24, "IK"),
    ("R_leg_PV_CTRL",       "R KN PV",   310, 360, 50, 18, "PV"),
    ("R_leg_SETTINGS_CTRL", "R SET",     320, 290, 40, 16, "S"),
    ("R_leg_toeTip_CTRL",   "R toes",    225, 515, 80, 16, "R"),

    # ---- Tail (side column on the right edge — labelled 1 to 7) ----
    ("C_tail_SETTINGS_CTRL",  "TAIL S",   400,  90, 50, 16, "S"),
    ("C_tail_01_FK_CTRL",     "TAIL 1",   400, 115, 50, 16, "C"),
    ("C_tail_02_FK_CTRL",     "TAIL 2",   400, 135, 50, 16, "C"),
    ("C_tail_03_FK_CTRL",     "TAIL 3",   400, 155, 50, 16, "C"),
    ("C_tail_04_FK_CTRL",     "TAIL 4",   400, 175, 50, 16, "C"),
    ("C_tail_05_FK_CTRL",     "TAIL 5",   400, 195, 50, 16, "C"),
    ("C_tail_06_FK_CTRL",     "TAIL 6",   400, 215, 50, 16, "C"),
    ("C_tail_07_FK_CTRL",     "TAIL 7",   400, 235, 50, 16, "C"),

    # ---- Global root (bottom) ----
    ("C_global_CTRL",    "GLOBAL", 155, 580, 150, 28, "C"),
]

# -----------------------------------------------------------------------------
# Quadruped (horse) — body
# Front legs near top, back legs near bottom, spine horizontal
# -----------------------------------------------------------------------------

QUAD_BODY = [
    # ---- Core ----
    ("C_global_CTRL",   "GLOBAL", 155, 580, 150, 28, "C"),
    ("C_cog_CTRL",      "COG",    200, 290, 60, 22, "C"),

    # ---- Head + neck ----
    ("C_head_CTRL",      "HEAD",  40, 100, 70, 28, "C"),
    ("C_neck_CTRL",      "NECK",  90, 145, 60, 20, "C"),

    # ---- Horizontal spine (chest on the left, hip on the right) ----
    ("C_spine_chest_CTRL", "CHEST", 130, 195, 60, 20, "C"),
    ("C_spine_FK_03_CTRL", "SP 3", 175, 220, 50, 16, "C"),
    ("C_spine_FK_02_CTRL", "SP 2", 220, 220, 50, 16, "C"),
    ("C_spine_FK_01_CTRL", "SP 1", 265, 220, 50, 16, "C"),
    ("C_spine_hip_CTRL", "HIP",    315, 195, 60, 20, "C"),

    # ---- Face (jaw, eyes) ----
    ("C_jaw_CTRL",       "JAW",   25,  80, 50, 16, "C"),
    ("L_eye_aim_CTRL",   "L EYE", 25,  60, 30, 14, "L"),
    ("R_eye_aim_CTRL",   "R EYE", 60,  60, 30, 14, "R"),
    ("C_eyes_lookAt_CTRL", "LOOK",-10, 30, 50, 16, "C"),

    # ---- Left front leg ----
    ("L_frontLeg_scapula_FK_CTRL", "L SCAP",  90, 250, 60, 16, "L"),
    ("L_frontLeg_shoulder_FK_CTRL", "L FR SH",105, 285, 60, 16, "L"),
    ("L_frontLeg_elbow_FK_CTRL",   "L FR EL", 90, 325, 60, 16, "L"),
    ("L_frontLeg_knee_FK_CTRL",    "L FR KN", 75, 370, 60, 16, "L"),
    ("L_frontLeg_IK_CTRL",         "L FR IK",  90, 420, 60, 22, "IK"),
    ("L_frontLeg_PV_CTRL",         "PV",       50, 325, 30, 16, "PV"),
    ("L_frontLeg_SETTINGS_CTRL",   "L SET",    40, 285, 40, 16, "S"),

    # ---- Right front leg ----
    ("R_frontLeg_scapula_FK_CTRL", "R SCAP", 165, 250, 60, 16, "R"),
    ("R_frontLeg_shoulder_FK_CTRL", "R FR SH",165, 285, 60, 16, "R"),
    ("R_frontLeg_elbow_FK_CTRL",   "R FR EL",165, 325, 60, 16, "R"),
    ("R_frontLeg_knee_FK_CTRL",    "R FR KN",165, 370, 60, 16, "R"),
    ("R_frontLeg_IK_CTRL",         "R FR IK",165, 420, 60, 22, "IK"),
    ("R_frontLeg_PV_CTRL",         "PV",     225, 325, 30, 16, "PV"),
    ("R_frontLeg_SETTINGS_CTRL",   "R SET",  225, 285, 40, 16, "S"),

    # ---- Left back leg ----
    ("L_backLeg_hip_FK_CTRL",     "L BK HIP", 270, 250, 60, 16, "L"),
    ("L_backLeg_stifle_FK_CTRL",  "L STIFLE", 270, 285, 60, 16, "L"),
    ("L_backLeg_hock_FK_CTRL",    "L HOCK",   270, 325, 60, 16, "L"),
    ("L_backLeg_IK_CTRL",         "L BK IK",  270, 420, 60, 22, "IK"),
    ("L_backLeg_PV_CTRL",         "PV",       230, 370, 30, 16, "PV"),
    ("L_backLeg_SETTINGS_CTRL",   "L SET",    230, 285, 40, 16, "S"),

    # ---- Right back leg ----
    ("R_backLeg_hip_FK_CTRL",     "R BK HIP", 345, 250, 60, 16, "R"),
    ("R_backLeg_stifle_FK_CTRL",  "R STIFLE", 345, 285, 60, 16, "R"),
    ("R_backLeg_hock_FK_CTRL",    "R HOCK",   345, 325, 60, 16, "R"),
    ("R_backLeg_IK_CTRL",         "R BK IK",  345, 420, 60, 22, "IK"),
    ("R_backLeg_PV_CTRL",         "PV",       405, 370, 30, 16, "PV"),
    ("R_backLeg_SETTINGS_CTRL",   "R SET",    405, 285, 40, 16, "S"),

    # ---- Tail (hanging off the right of the body) ----
    ("C_tail_01_FK_CTRL",   "TAIL 1", 400, 195, 50, 16, "C"),
    ("C_tail_03_FK_CTRL",   "TAIL 3", 400, 215, 50, 16, "C"),
    ("C_tail_05_FK_CTRL",   "TAIL 5", 400, 235, 50, 16, "C"),
    ("C_tail_07_FK_CTRL",   "TAIL 7", 400, 255, 50, 16, "C"),
    ("C_tail_SETTINGS_CTRL", "TAIL S", 400, 170, 50, 16, "S"),
]

# -----------------------------------------------------------------------------
# Bird (raptor) — body
# Wings extend wide, legs short, tail fan at the back
# -----------------------------------------------------------------------------

BIRD_BODY = [
    # ---- Core ----
    ("C_global_CTRL",   "GLOBAL", 155, 580, 150, 28, "C"),
    ("C_cog_CTRL",      "COG",    200, 310, 60, 22, "C"),

    # ---- Head + neck (top, beak forward) ----
    ("C_head_CTRL",      "HEAD",  200,  30, 60, 28, "C"),
    ("C_neck_CTRL",      "NECK",  200,  65, 60, 18, "C"),
    ("C_jaw_CTRL",       "BEAK",  200,   8, 60, 16, "C"),
    ("L_eye_aim_CTRL",   "L EYE", 130,  40, 40, 14, "L"),
    ("R_eye_aim_CTRL",   "R EYE", 290,  40, 40, 14, "R"),

    # ---- Spine (chest above, hip below) ----
    ("C_spine_chest_CTRL", "CHEST", 200, 100, 60, 20, "C"),
    ("C_spine_FK_02_CTRL", "SP 2",205, 130, 50, 16, "C"),
    ("C_spine_FK_01_CTRL", "SP 1",205, 155, 50, 16, "C"),
    ("C_spine_hip_CTRL", "HIP",   200, 185, 60, 20, "C"),

    # ---- Left wing (extends left, far) ----
    ("L_wing_shoulder_FK_CTRL", "L W SH",  150, 105, 50, 16, "L"),
    ("L_wing_elbow_FK_CTRL",    "L W EL",   85, 115, 50, 16, "L"),
    ("L_wing_wrist_FK_CTRL",    "L W WR",   20, 130, 50, 16, "L"),
    ("L_wing_IK_CTRL",          "L WING IK",10, 155, 80, 24, "IK"),
    ("L_wing_PV_CTRL",          "L PV",     50,  90, 35, 14, "PV"),
    ("L_wing_SETTINGS_CTRL",    "L W SET",  90, 145, 50, 16, "S"),

    # ---- Right wing (mirror) ----
    ("R_wing_shoulder_FK_CTRL", "R W SH",  260, 105, 50, 16, "R"),
    ("R_wing_elbow_FK_CTRL",    "R W EL",  325, 115, 50, 16, "R"),
    ("R_wing_wrist_FK_CTRL",    "R W WR",  390, 130, 50, 16, "R"),
    ("R_wing_IK_CTRL",          "R WING IK",370, 155, 80, 24, "IK"),
    ("R_wing_PV_CTRL",          "R PV",     375,  90, 35, 14, "PV"),
    ("R_wing_SETTINGS_CTRL",    "R W SET", 320, 145, 50, 16, "S"),

    # ---- Left leg (down from hip) ----
    ("L_birdLeg_femur_FK_CTRL",         "L FEM",   155, 245, 60, 16, "L"),
    ("L_birdLeg_tibiotarsus_FK_CTRL",   "L TIB",   155, 285, 60, 16, "L"),
    ("L_birdLeg_tarsometatarsus_FK_CTRL","L TARS", 155, 340, 60, 16, "L"),
    ("L_birdLeg_IK_CTRL",                "L FT IK",155, 400, 60, 24, "IK"),
    ("L_birdLeg_PV_CTRL",                "L PV",   100, 285, 40, 16, "PV"),
    ("L_birdLeg_SETTINGS_CTRL",          "L L SET",100, 245, 50, 16, "S"),
    ("L_birdLeg_digit2Prox_FK_CTRL",     "L D2",   140, 430, 30, 14, "L"),
    ("L_birdLeg_digit3Prox_FK_CTRL",     "L D3",   172, 430, 30, 14, "L"),
    ("L_birdLeg_digit4Prox_FK_CTRL",     "L D4",   205, 430, 30, 14, "L"),
    ("L_birdLeg_halluxProx_FK_CTRL",     "L Hx",   140, 450, 30, 14, "L"),

    # ---- Right leg (mirror) ----
    ("R_birdLeg_femur_FK_CTRL",         "R FEM",   245, 245, 60, 16, "R"),
    ("R_birdLeg_tibiotarsus_FK_CTRL",   "R TIB",   245, 285, 60, 16, "R"),
    ("R_birdLeg_tarsometatarsus_FK_CTRL","R TARS", 245, 340, 60, 16, "R"),
    ("R_birdLeg_IK_CTRL",                "R FT IK",245, 400, 60, 24, "IK"),
    ("R_birdLeg_PV_CTRL",                "R PV",   320, 285, 40, 16, "PV"),
    ("R_birdLeg_SETTINGS_CTRL",          "R L SET",310, 245, 50, 16, "S"),
    ("R_birdLeg_digit2Prox_FK_CTRL",     "R D2",   245, 430, 30, 14, "R"),
    ("R_birdLeg_digit3Prox_FK_CTRL",     "R D3",   277, 430, 30, 14, "R"),
    ("R_birdLeg_digit4Prox_FK_CTRL",     "R D4",   245, 450, 30, 14, "R"),
    ("R_birdLeg_halluxProx_FK_CTRL",     "R Hx",   277, 450, 30, 14, "R"),

    # ---- Tail fan (below hip) ----
    ("C_tailBase_CTRL",          "TAIL BASE",  175, 220, 110, 18, "C"),
    ("C_tailfeather_01_CTRL",    "T1",  60, 480, 30, 14, "B"),
    ("C_tailfeather_02_CTRL",    "T2", 100, 490, 30, 14, "B"),
    ("C_tailfeather_03_CTRL",    "T3", 140, 500, 30, 14, "B"),
    ("C_tailfeather_04_CTRL",    "T4", 215, 510, 30, 14, "B"),
    ("C_tailfeather_05_CTRL",    "T5", 290, 500, 30, 14, "B"),
    ("C_tailfeather_06_CTRL",    "T6", 330, 490, 30, 14, "B"),
    ("C_tailfeather_07_CTRL",    "T7", 370, 480, 30, 14, "B"),
    ("C_tailFan_SETTINGS_CTRL",  "T S", 215, 480, 30, 14, "S"),
]


# -----------------------------------------------------------------------------
# Face panel — same buttons across all rig types since they reuse FaceRig
# -----------------------------------------------------------------------------

FACE_PANEL = [
    # ---- Center skull outline (visual reference) ----
    # Top centre = forehead area

    # ---- Brows ----
    ("L_browOuter_CTRL",  "L brow out", 60,  90, 70, 16, "L"),
    ("L_browMid_CTRL",    "L brow mid",140,  80, 70, 16, "L"),
    ("L_browInner_CTRL",  "L brow in", 220,  85, 70, 16, "L"),
    ("R_browInner_CTRL",  "R brow in", 240,  85, 70, 16, "R"),
    ("R_browMid_CTRL",    "R brow mid",250,  80, 70, 16, "R"),
    ("R_browOuter_CTRL",  "R brow out",330,  90, 70, 16, "R"),

    # ---- Eye look-at master + per-eye ----
    ("C_eyes_lookAt_CTRL", "EYES LOOK", 175, 50, 110, 22, "IK"),
    ("L_eye_aim_CTRL",     "L EYE", 100, 130, 70, 16, "L"),
    ("R_eye_aim_CTRL",     "R EYE", 290, 130, 70, 16, "R"),

    # ---- Advanced (mesh-conforming) face: blink + mouth ----
    ("L_blink_CTRL", "L BLINK", 105, 112, 70, 14, "L"),
    ("R_blink_CTRL", "R BLINK", 295, 112, 70, 14, "R"),
    ("C_mouth_CTRL", "MOUTH (smile/zip)", 150, 356, 160, 16, "C"),

    # ---- Eyelids (3 per arc × 4 arcs = 12 total) ----
    ("L_eyelidUpperOuter_CTRL", "L up out",  60,  155, 70, 14, "L"),
    ("L_eyelidUpperMid_CTRL",   "L up mid", 130,  155, 70, 14, "L"),
    ("L_eyelidUpperInner_CTRL", "L up in",  200,  155, 70, 14, "L"),
    ("L_eyelidLowerOuter_CTRL", "L lo out",  60,  175, 70, 14, "L"),
    ("L_eyelidLowerMid_CTRL",   "L lo mid", 130,  175, 70, 14, "L"),
    ("L_eyelidLowerInner_CTRL", "L lo in",  200,  175, 70, 14, "L"),

    ("R_eyelidUpperInner_CTRL", "R up in",  230,  155, 70, 14, "R"),
    ("R_eyelidUpperMid_CTRL",   "R up mid", 300,  155, 70, 14, "R"),
    ("R_eyelidUpperOuter_CTRL", "R up out", 370,  155, 70, 14, "R"),
    ("R_eyelidLowerInner_CTRL", "R lo in",  230,  175, 70, 14, "R"),
    ("R_eyelidLowerMid_CTRL",   "R lo mid", 300,  175, 70, 14, "R"),
    ("R_eyelidLowerOuter_CTRL", "R lo out", 370,  175, 70, 14, "R"),

    # ---- Cheeks ----
    ("L_cheek_CTRL", "L cheek", 80, 250, 70, 16, "L"),
    ("R_cheek_CTRL", "R cheek", 330, 250, 70, 16, "R"),

    # ---- Nose ----
    ("C_nose_CTRL", "NOSE", 200, 220, 60, 16, "C"),

    # ---- Mouth corners + lips (center column) ----
    ("L_mouthCorner_CTRL", "L mouth", 100, 320, 70, 16, "L"),
    ("R_mouthCorner_CTRL", "R mouth", 290, 320, 70, 16, "R"),

    # Lips — standard mode uses Center / LMid / RMid corners per row.
    ("C_upperLip_LMid_CTRL",   "up L mid", 100, 295, 70, 14, "L"),
    ("C_upperLip_Center_CTRL", "up center", 175, 295, 110, 14, "C"),
    ("C_upperLip_RMid_CTRL",   "up R mid", 290, 295, 70, 14, "R"),
    ("C_lowerLip_LMid_CTRL",   "lo L mid", 100, 335, 70, 14, "L"),
    ("C_lowerLip_Center_CTRL", "lo center", 175, 335, 110, 14, "C"),
    ("C_lowerLip_RMid_CTRL",   "lo R mid", 290, 335, 70, 14, "R"),

    # ---- Jaw + teeth + tongue ----
    ("C_jaw_CTRL",          "JAW",      180, 380, 100, 22, "C"),
    ("C_upperTeeth_CTRL",   "up teeth", 175, 410, 110, 14, "B"),
    ("C_lowerTeeth_CTRL",   "lo teeth", 175, 430, 110, 14, "B"),
    ("C_tongue_01_CTRL", "tongue 1", 200, 460, 60, 14, "C"),
    ("C_tongue_02_CTRL", "tongue 2", 200, 480, 60, 14, "C"),
    ("C_tongue_03_CTRL", "tongue 3", 200, 500, 60, 14, "C"),

    # ---- Ears (horse/bird) ----
    ("L_ear_CTRL", "L ear", 40, 130, 50, 16, "L"),
    ("R_ear_CTRL", "R ear", 370, 130, 50, 16, "R"),
]


# Map of "rig type detected from top group" -> body layout.
BODY_LAYOUTS = {
    "CHARACTER_RIG_GRP": ("Biped",     BIPED_BODY),
    "QUADRUPED_RIG_GRP": ("Quadruped", QUAD_BODY),
    "BIRD_RIG_GRP":      ("Bird",      BIRD_BODY),
}


# =============================================================================
# Picker dialog
# =============================================================================

def detect_rig():
    """Return the top-group name of the first rig found in the scene,
    or None if there isn't one yet."""
    for grp in ("CHARACTER_RIG_GRP", "QUADRUPED_RIG_GRP", "BIRD_RIG_GRP"):
        if cmds.objExists(grp):
            return grp
    return None


class RigPickerUI(QtWidgets.QDialog):

    def __init__(self, parent=None):
        super(RigPickerUI, self).__init__(parent or _maya_main_window())
        self.setObjectName(WINDOW_OBJECT_NAME)
        self.setWindowTitle("Danyal's Rig Picker")
        self.setWindowFlags(self.windowFlags() | QtCore.Qt.Window)

        self.body_canvas = None
        self.face_canvas = None
        self.tabs = None
        self.status = None

        self._build_ui()
        self.refresh()

    # -----------------------------------------------------------------------

    def _build_ui(self):
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(4)

        # Top row: rig label + refresh + clear-selection
        top_row = QtWidgets.QHBoxLayout()
        self.rig_label = QtWidgets.QLabel("(no rig)")
        self.rig_label.setStyleSheet(
            "QLabel { font-weight: bold; color: #ccc; }")
        top_row.addWidget(self.rig_label, 1)

        self.btn_clear_sel = QtWidgets.QPushButton("Clear Sel")
        self.btn_clear_sel.setToolTip("Same as pressing Esc in the viewport — "
                                      "clears the current selection.")
        self.btn_clear_sel.clicked.connect(lambda: cmds.select(cl=True))
        top_row.addWidget(self.btn_clear_sel)

        self.btn_refresh = QtWidgets.QPushButton("Refresh")
        self.btn_refresh.setToolTip(
            "Re-detect which rig is in the scene and rebuild the buttons.")
        self.btn_refresh.clicked.connect(self.refresh)
        top_row.addWidget(self.btn_refresh)

        root.addLayout(top_row)

        # Tabs: Body / Face
        self.tabs = QtWidgets.QTabWidget()
        root.addWidget(self.tabs)

        self.body_canvas = PickerCanvas(460, 620)
        body_holder = QtWidgets.QWidget()
        bh_layout = QtWidgets.QVBoxLayout(body_holder)
        bh_layout.setContentsMargins(0, 0, 0, 0)
        bh_layout.addWidget(self.body_canvas, alignment=QtCore.Qt.AlignCenter)

        # --- Pose tools (biped only) ---
        self.pose_tools_box = QtWidgets.QGroupBox("Pose Tools (biped)")
        pt_lay = QtWidgets.QGridLayout(self.pose_tools_box)
        pt_lay.setSpacing(4)

        self.btn_copy_l_to_r = QtWidgets.QPushButton("Copy Pose  L → R")
        self.btn_copy_l_to_r.setToolTip(
            "Mirror the whole left-side pose onto the right side "
            "(arms, legs, fingers, clavicles).")
        self.btn_copy_r_to_l = QtWidgets.QPushButton("Copy Pose  R → L")
        self.btn_copy_r_to_l.setToolTip("Mirror the right pose onto the left.")
        self.btn_copy_l_to_r.clicked.connect(
            lambda: self._do_pose(rig_pose_tools.copy_pose_left_to_right,
                                  "Mirrored pose L → R"))
        self.btn_copy_r_to_l.clicked.connect(
            lambda: self._do_pose(rig_pose_tools.copy_pose_right_to_left,
                                  "Mirrored pose R → L"))

        # IK/FK match per limb — switches mode keeping the same pose.
        self.btn_ikfk = {}
        ikfk_specs = [("L Arm", "L_arm"), ("R Arm", "R_arm"),
                      ("L Leg", "L_leg"), ("R Leg", "R_leg")]

        pt_lay.addWidget(self.btn_copy_l_to_r, 0, 0)
        pt_lay.addWidget(self.btn_copy_r_to_l, 0, 1)
        ikfk_lbl = QtWidgets.QLabel("IK/FK match (keep pose):")
        ikfk_lbl.setStyleSheet("QLabel { color: #aaa; }")
        pt_lay.addWidget(ikfk_lbl, 1, 0, 1, 2)
        for i, (label, prefix) in enumerate(ikfk_specs):
            b = QtWidgets.QPushButton(label)
            b.setToolTip(f"Switch {prefix} between IK and FK while keeping "
                         f"the current pose (no pop).")
            b.clicked.connect(
                lambda _=False, p=prefix: self._do_ikfk(p))
            self.btn_ikfk[prefix] = b
            pt_lay.addWidget(b, 2 + i // 2, i % 2)

        bh_layout.addWidget(self.pose_tools_box)
        self.tabs.addTab(body_holder, "Body")

        self.face_canvas = PickerCanvas(460, 540)
        face_holder = QtWidgets.QWidget()
        fh_layout = QtWidgets.QVBoxLayout(face_holder)
        fh_layout.setContentsMargins(0, 0, 0, 0)
        fh_layout.addWidget(self.face_canvas, alignment=QtCore.Qt.AlignCenter)

        # --- Advanced-face live sliders (a simplified face board) ---
        self.face_sliders_box = QtWidgets.QGroupBox(
            "Advanced Face — live sliders")
        fs = QtWidgets.QGridLayout(self.face_sliders_box)
        fs.setSpacing(4)
        # (label, ctrl, attr, lo, hi)
        slider_specs = [
            ("Blink L", "L_blink_CTRL", "blink", 0.0, 1.0),
            ("Blink R", "R_blink_CTRL", "blink", 0.0, 1.0),
            ("Smile",   "C_mouth_CTRL", "smile", -1.0, 1.0),
            ("Zip",     "C_mouth_CTRL", "zip",   0.0, 1.0),
        ]
        self.face_sliders = {}     # (ctrl, attr) -> (slider, lo, hi)
        for row, (label, ctrl, attr, lo, hi) in enumerate(slider_specs):
            lbl = QtWidgets.QLabel(label)
            lbl.setFixedWidth(54)
            sld = QtWidgets.QSlider(QtCore.Qt.Horizontal)
            sld.setRange(0, 100)
            sld.valueChanged.connect(
                lambda v, c=ctrl, a=attr, lo_=lo, hi_=hi:
                self._drive_face_attr(c, a, lo_, hi_, v))
            fs.addWidget(lbl, row, 0)
            fs.addWidget(sld, row, 1)
            self.face_sliders[(ctrl, attr)] = (sld, lo, hi)
        fh_layout.addWidget(self.face_sliders_box)

        self.tabs.addTab(face_holder, "Face")

        # --- Creature tab: extra arms / legs / tails (auto-laid-out) ---
        self.creature_canvas = PickerCanvas(460, 120)
        self.creature_holder = QtWidgets.QWidget()
        ch_layout = QtWidgets.QVBoxLayout(self.creature_holder)
        ch_layout.setContentsMargins(0, 0, 0, 0)
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(False)
        scroll.setAlignment(QtCore.Qt.AlignHCenter)
        scroll.setWidget(self.creature_canvas)
        scroll.setMinimumHeight(300)
        ch_layout.addWidget(scroll, 1)
        self.creature_ikfk_box = QtWidgets.QGroupBox(
            "IK/FK match (keep pose), extra limbs")
        self.creature_ikfk_lay = QtWidgets.QGridLayout(self.creature_ikfk_box)
        self.creature_ikfk_lay.setSpacing(4)
        ch_layout.addWidget(self.creature_ikfk_box)
        self.creature_tab_index = self.tabs.addTab(self.creature_holder,
                                                   "Creature")

        # Status row at the bottom
        self.status = QtWidgets.QLabel("Click a button to select. "
                                       "Shift = add, Ctrl = toggle.")
        self.status.setStyleSheet(
            "QLabel { color: #888; padding: 4px; "
            "border-top: 1px solid #444; }")
        root.addWidget(self.status)

        self.adjustSize()

    # -----------------------------------------------------------------------

    def refresh(self):
        """Detect the active rig type, clear, and rebuild the picker buttons."""
        self.body_canvas.clear()
        self.face_canvas.clear()
        self._refresh_face_sliders()

        rig_top = detect_rig()
        self._refresh_creature(rig_top == "CHARACTER_RIG_GRP")
        if not rig_top:
            self.rig_label.setText("(no rig in scene — build one first)")
            self.status.setText(
                "No CHARACTER_RIG_GRP / QUADRUPED_RIG_GRP / BIRD_RIG_GRP "
                "in scene.")
            self.pose_tools_box.setVisible(False)
            return

        label, body_layout = BODY_LAYOUTS[rig_top]
        self.rig_label.setText(f"{label}   (top: {rig_top})")
        # Pose tools are biped-only (they assume the arm/leg IK/FK rig).
        self.pose_tools_box.setVisible(rig_top == "CHARACTER_RIG_GRP")

        # Build body buttons
        for entry in body_layout:
            ctrl, lbl, x, y, w, h, color = entry
            self.body_canvas.add(ctrl, lbl, x, y, w, h, color)

        # Build face buttons (same layout for all rig types — those that
        # don't exist on the current rig just appear disabled).
        for entry in FACE_PANEL:
            ctrl, lbl, x, y, w, h, color = entry
            self.face_canvas.add(ctrl, lbl, x, y, w, h, color)

        # Count enabled vs disabled
        n_body = len([b for b in self.body_canvas.buttons if b.isEnabled()])
        n_face = len([b for b in self.face_canvas.buttons if b.isEnabled()])
        total_b = len(self.body_canvas.buttons)
        total_f = len(self.face_canvas.buttons)
        self.status.setText(
            f"{label}: {n_body}/{total_b} body ctrls, "
            f"{n_face}/{total_f} face ctrls available.")

    # -----------------------------------------------------------------------
    # Pose tool handlers
    # -----------------------------------------------------------------------

    def _do_pose(self, fn, msg):
        try:
            with _undo_chunk():
                n = fn()
            self.status.setText(f"{msg} ({n} ctrls).")
        except Exception as e:
            cmds.warning(f"[picker] pose mirror failed: {e}")

    def _refresh_creature(self, is_biped):
        """Rebuild the Creature tab from the extra limbs on the built rig.
        The tab is hidden when the rig has none."""
        self.creature_canvas.clear()
        while self.creature_ikfk_lay.count():
            w = self.creature_ikfk_lay.takeAt(0).widget()
            if w:
                w.setParent(None)
                w.deleteLater()
        limbs = rig_creature.scene_extra_limbs() if is_biped else []
        entries, height, prefixes = rig_creature.picker_layout(limbs)
        self.creature_canvas.setFixedSize(460, height)
        for ctrl, lbl, x, y, w, h, color in entries:
            self.creature_canvas.add(ctrl, lbl, x, y, w, h, color)
        for i, prefix in enumerate(prefixes):
            b = QtWidgets.QPushButton(prefix.replace("_", " ", 1))
            b.setToolTip(f"Switch {prefix} between IK and FK while keeping "
                         f"the current pose (no pop).")
            b.clicked.connect(lambda _=False, p=prefix: self._do_ikfk(p))
            self.creature_ikfk_lay.addWidget(b, i // 2, i % 2)
        self.creature_ikfk_box.setVisible(bool(prefixes))
        show = bool(limbs)
        if hasattr(self.tabs, "setTabVisible"):
            self.tabs.setTabVisible(self.creature_tab_index, show)
        else:
            self.tabs.setTabEnabled(self.creature_tab_index, show)
        if limbs:
            self.tabs.setTabText(self.creature_tab_index,
                                 "Creature (%d)" % len(limbs))

    def _do_ikfk(self, prefix):
        try:
            with _undo_chunk():
                mode = rig_pose_tools.ikfk_match(prefix)
            if mode:
                self.status.setText(f"{prefix} matched to {mode} "
                                    f"(pose kept).")
        except Exception as e:
            cmds.warning(f"[picker] IK/FK match failed: {e}")

    # -----------------------------------------------------------------------
    # Advanced-face live sliders
    # -----------------------------------------------------------------------

    def _drive_face_attr(self, ctrl, attr, lo, hi, slider_val):
        """Drive an advanced-face attr live from a 0..100 slider."""
        if not (cmds.objExists(ctrl)
                and cmds.attributeQuery(attr, node=ctrl, exists=True)):
            return
        val = lo + (hi - lo) * (slider_val / 100.0)
        try:
            cmds.setAttr(f"{ctrl}.{attr}", val)
        except Exception:
            pass

    def _refresh_face_sliders(self):
        """Show the slider board only when an advanced face exists; enable
        each slider whose control is present and sync it to the live value."""
        adv = cmds.objExists("ADV_FACE_GRP")
        self.face_sliders_box.setVisible(adv)
        for (ctrl, attr), (sld, lo, hi) in self.face_sliders.items():
            ok = (cmds.objExists(ctrl)
                  and cmds.attributeQuery(attr, node=ctrl, exists=True))
            sld.setEnabled(ok)
            if ok:
                try:
                    val = cmds.getAttr(f"{ctrl}.{attr}")
                    sld.blockSignals(True)
                    sld.setValue(int(round((val - lo) / (hi - lo) * 100.0)))
                    sld.blockSignals(False)
                except Exception:
                    pass


# =============================================================================
# Entry point
# =============================================================================

_picker_window = None


def show():
    """Open (or re-open) the picker window. Closes any prior instance
    so reloads of the module always show the latest layout."""
    global _picker_window
    # Kill any prior instance hanging around (after a reload).
    for w in QtWidgets.QApplication.topLevelWidgets():
        if w.objectName() == WINDOW_OBJECT_NAME:
            try:
                w.close()
                w.deleteLater()
            except Exception:
                pass

    _picker_window = RigPickerUI()
    _picker_window.show()
    _picker_window.raise_()
    return _picker_window
