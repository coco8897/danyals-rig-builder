"""
===============================================================================
 RIG UI - PySide2/6 panel for the modular character rig builder
===============================================================================

 Launch from Maya Script Editor (Python tab):

    import rig_ui
    from importlib import reload; reload(rig_ui)
    rig_ui.show()

 Provides:
   * Guides:   Create / Mirror L->R / Reset / Save / Load / Delete
   * Build:    module checkboxes + bendy count + Build / Delete rig
   * Status bar at the bottom
===============================================================================
"""

import contextlib
import os
from importlib import reload

import maya.cmds as cmds
import maya.OpenMayaUI as omui

try:
    from PySide2 import QtCore, QtWidgets
    from shiboken2 import wrapInstance
except ImportError:                                  # Maya 2025+ (Qt6)
    from PySide6 import QtCore, QtWidgets
    from shiboken6 import wrapInstance

import rig_guides
import character_rig_builder
import quadruped_guides
import quadruped_rig_builder
import bird_guides
import bird_rig_builder
import vehicle_guides
import vehicle_rig_builder
import motorcycle_guides
import motorcycle_rig_builder
import vehicle_drive
import vehicle_sim
import vehicle_parts
import vehicle_trailers
import vehicle_crash
import vehicle_cargo
import vehicle_tracks
import prop_rig
import character_walk
import character_fly
import chain_sim
import face_tracker
import face_shapes
import face_capture
import vehicle_bind
import raycast_ground
import rig_space_switch
import rig_pose_lib
import forge_theme as ft
import rig_skin
import advanced_face
import advanced_face_ui
import rig_export
import rig_creature

# Forge-suite accent for this tool (amber). The rest of the palette
# lives in forge_theme so every Forge tool stays visually in sync.
ACCENT = ft.ACCENT_RIG
import rig_picker
import rig_info
import rig_correctives
import rig_dynamics
import rig_validate

# Allow the user to reload submodules between UI launches without restarting.
reload(rig_guides)
reload(character_rig_builder)
reload(quadruped_guides)
reload(quadruped_rig_builder)
reload(bird_guides)
reload(bird_rig_builder)
reload(vehicle_guides)
reload(vehicle_rig_builder)
reload(vehicle_drive)
reload(vehicle_sim)
reload(vehicle_parts)
reload(vehicle_trailers)
reload(vehicle_crash)
reload(vehicle_cargo)
reload(prop_rig)
reload(character_walk)
reload(character_fly)
reload(chain_sim)
reload(face_tracker)
reload(face_shapes)
reload(face_capture)
reload(vehicle_bind)
reload(raycast_ground)
reload(rig_space_switch)
reload(rig_pose_lib)
reload(rig_skin)
reload(advanced_face)
reload(advanced_face_ui)
reload(rig_export)
reload(rig_creature)
reload(rig_picker)
reload(rig_info)
reload(rig_correctives)
reload(rig_dynamics)
reload(rig_validate)


WINDOW_OBJECT_NAME = "ModularRigBuilderWindow"


@contextlib.contextmanager
def undo_chunk():
    """Group everything done inside the `with` block into ONE undoable
    action.

    Maya does not journal a multi-command operation run from a Qt
    button callback (Create Guides, Mirror L→R, Build Rig, …) as a
    single revertible step unless it is bracketed by an explicit
    openChunk / closeChunk pair. Without this, Ctrl+Z after a Mirror
    either does nothing or unwinds one locator at a time. Wrapping the
    operation here makes a single Ctrl+Z revert the whole thing.
    """
    cmds.undoInfo(openChunk=True)
    try:
        yield
    finally:
        cmds.undoInfo(closeChunk=True)


class CollapsibleBox(QtWidgets.QWidget):
    """A titled section that folds away its contents. The header is a clickable
    bar (▸ collapsed / ▾ expanded); clicking it shows/hides the body. Built so
    the panel can ship with the advanced sections COLLAPSED by default — no
    clutter — and the user opens only what they need.

        box = CollapsibleBox("Skinning")
        lay = QtWidgets.QGridLayout()
        ...                                   # add widgets to `lay`
        box.setContentLayout(lay)
        root.addWidget(box)
    """

    def __init__(self, title="", expanded=False, parent=None):
        super(CollapsibleBox, self).__init__(parent)
        self.toggle = QtWidgets.QToolButton()
        self.toggle.setText(title)
        self.toggle.setCheckable(True)
        self.toggle.setChecked(expanded)
        self.toggle.setToolButtonStyle(QtCore.Qt.ToolButtonTextBesideIcon)
        self.toggle.setArrowType(QtCore.Qt.DownArrow if expanded
                                 else QtCore.Qt.RightArrow)
        self.toggle.setStyleSheet(ft.section_header_css(ACCENT))
        self.toggle.setSizePolicy(QtWidgets.QSizePolicy.Expanding,
                                  QtWidgets.QSizePolicy.Fixed)
        self.toggle.toggled.connect(self._on_toggled)

        self.content = QtWidgets.QWidget()
        self.content.setVisible(expanded)
        # Scope the rail to THIS widget by object name. A bare "QWidget {}"
        # rule cascades to every label/combo inside and draws a bar on each.
        self.content.setObjectName("collapsibleContent")
        self.content.setAttribute(QtCore.Qt.WA_StyledBackground, True)
        self.content.setStyleSheet(
            "QWidget#collapsibleContent { border-left: 2px solid %s; }"
            % ft.BORDER)

        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(0)
        lay.addWidget(self.toggle)
        lay.addWidget(self.content)

    def _on_toggled(self, checked):
        self.toggle.setArrowType(QtCore.Qt.DownArrow if checked
                                 else QtCore.Qt.RightArrow)
        self.content.setVisible(checked)

    def setContentLayout(self, layout):
        layout.setContentsMargins(8, 4, 4, 6)
        self.content.setLayout(layout)


# =============================================================================
# Helpers
# =============================================================================

def _maya_main_window():
    ptr = omui.MQtUtil.mainWindow()
    return wrapInstance(int(ptr), QtWidgets.QWidget)


def _close_existing(name):
    """Kill any prior instance of the window before opening a new one."""
    for w in QtWidgets.QApplication.topLevelWidgets():
        if w.objectName() == name:
            w.close()
            w.deleteLater()


# =============================================================================
# UI
# =============================================================================

class RigBuilderUI(QtWidgets.QDialog):

    def __init__(self, parent=None):
        super(RigBuilderUI, self).__init__(parent or _maya_main_window())
        self.setObjectName(WINDOW_OBJECT_NAME)
        self.setWindowTitle("Danyal's Rig Builder")
        self.setMinimumWidth(360)
        self.setWindowFlags(self.windowFlags() | QtCore.Qt.Window)
        self.setStyleSheet(ft.style(ACCENT))

        self.guide_system = rig_guides.GuideSystem()
        self.rig = None
        # Quadruped workflow (horse, cat / dog, raptor)
        self.quad_guide_system = quadruped_guides.QuadGuideSystem()
        self.quad_rig = None
        # Bird (eagle / hawk) workflow
        self.bird_guide_system = bird_guides.BirdGuideSystem()
        self.bird_rig = None
        # Vehicle workflow
        self.vehicle_guide_system = vehicle_guides.VehicleGuideSystem()
        self.moto_guide_system = motorcycle_guides.MotorcycleGuideSystem()
        self.vehicle_rig = None

        self._build_ui()
        self._connect()
        self._refresh_status()
        # Pick up the symmetric-mode state from the scene (if guides
        # already exist) so the checkbox reflects reality on launch.
        if (cmds.objExists("RIG_GUIDES_GRP")
                and cmds.attributeQuery("symmetricMode",
                                          node="RIG_GUIDES_GRP",
                                          exists=True)):
            self.cb_symmetric.blockSignals(True)
            self.cb_symmetric.setChecked(
                self.guide_system.is_symmetric_mode())
            self.cb_symmetric.blockSignals(False)

    # -----------------------------------------------------------------------

    def _build_ui(self):
        # The panel has grown a lot (4 rig types + skinning + export +
        # vehicle). Put all the content inside a SCROLL AREA so it
        # scrolls vertically instead of overflowing the screen. The dialog
        # itself holds only the scroll area; `root` is the inner layout
        # everything is added to (unchanged below).
        self._content = QtWidgets.QWidget()
        root = QtWidgets.QVBoxLayout(self._content)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        # ---------- BRANDED HEADER (matches the Forge tool suite) ----------
        hdr = QtWidgets.QVBoxLayout()
        hdr.setSpacing(1)
        _title = QtWidgets.QLabel("RIG BUILDER")
        _title.setObjectName("title")
        hdr.addWidget(_title)
        _sub = QtWidgets.QLabel(
            "Biped / Quadruped / Bird / Vehicle  ·  guides to game-ready")
        _sub.setObjectName("subtitle")
        hdr.addWidget(_sub)
        root.addLayout(hdr)

        _rule = QtWidgets.QFrame()
        _rule.setFrameShape(QtWidgets.QFrame.HLine)
        _rule.setStyleSheet("color: %s;" % ft.BORDER)
        root.addWidget(_rule)

        # ---------- RIG TYPE MODE SELECTOR ----------
        # Switching the mode shows that template's sections and hides the
        # other's, so the panel stays compact instead of stacking every
        # biped + quadruped option at once.
        mode_row = QtWidgets.QHBoxLayout()
        mode_lbl = QtWidgets.QLabel("Rig Type:")
        mode_lbl.setStyleSheet("QLabel { color: %s; font-weight: 600; }"
                               % ft.MUTED)
        mode_row.addWidget(mode_lbl)
        self.mode_combo = QtWidgets.QComboBox()
        self.mode_combo.addItems(["Biped", "Quadruped", "Bird", "Vehicle",
                                  "Motorcycle",
                                  "Prop"])
        mode_row.addWidget(self.mode_combo, 1)

        # Quick-launch the picker — always visible (independent of rig type).
        self.btn_open_picker = QtWidgets.QPushButton("Open Picker")
        self.btn_open_picker.setObjectName("primary")
        self.btn_open_picker.setStyleSheet(
            "QPushButton { padding: 4px 10px; }")
        self.btn_open_picker.setToolTip(
            "Open the anatomical ctrl picker window.")
        mode_row.addWidget(self.btn_open_picker)
        mode_row.addWidget(rig_info.make_info_button("open_picker"))
        # WASD for characters (bipeds, creatures, quadrupeds, birds).
        self.btn_walk_mode = QtWidgets.QPushButton("Walk Mode (WASD)")
        self.btn_walk_mode.setStyleSheet("QPushButton { padding: 4px 10px; }")
        self.btn_walk_mode.setToolTip(
            "Walk the character with W A S D, Shift to run, Space to jump.\n"
            "Feet plant, the body bobs, arms swing; keyed when you stop.")
        mode_row.addWidget(self.btn_walk_mode)
        mode_row.addWidget(rig_info.make_info_button("walk_mode"))
        # Fly anything with wings (birds, the Dragon creature preset).
        self.btn_fly_mode = QtWidgets.QPushButton("Fly Mode (WASD)")
        self.btn_fly_mode.setStyleSheet("QPushButton { padding: 4px 10px; }")
        self.btn_fly_mode.setToolTip(
            "Fly a bird or a dragon with W A S D: Space climbs or takes off,\n"
            "Shift dives, hold S low down to land. Keyed when you stop.")
        mode_row.addWidget(self.btn_fly_mode)
        mode_row.addWidget(rig_info.make_info_button("fly_mode"))
        root.addLayout(mode_row)

        # ---------- GUIDES SECTION (biped) ----------
        self.guides_box = QtWidgets.QGroupBox("Guides")
        gl = QtWidgets.QGridLayout(self.guides_box)
        gl.setSpacing(6)

        self.btn_create_guides = QtWidgets.QPushButton("Create Guides")
        self.btn_mirror = QtWidgets.QPushButton("Mirror L → R")
        self.btn_reset = QtWidgets.QPushButton("Reset to Defaults")
        # Neck: how many joints, and whether it gets an IK head control.
        self.neck_segments_spin = QtWidgets.QSpinBox()
        self.neck_segments_spin.setRange(1, rig_guides.GuideSystem
                                         .NECK_SEGMENT_MAX)
        self.neck_segments_spin.setValue(1)
        self.neck_segments_spin.setToolTip(
            "How many joints the neck has. 1 is a person; a dragon, horse "
            "or\nswan wants 4 to 6. The extra guides appear between the "
            "neck and\nhead guides: drag them along the neck.")
        self.cb_neck_ik = QtWidgets.QCheckBox("IK head")
        self.cb_neck_ik.setToolTip(
            "Runs an IK spline through the neck and adds C_headIK_CTRL:\n"
            "drag it and the whole neck arcs to follow, so you can plant a\n"
            "dragon's head and animate the body under it. Switch back to "
            "the\nFK chain any time on C_neck_SETTINGS_CTRL.ikFkSwitch "
            "(with\nstretch there too). Needs 2 or more neck joints.")
        neck_row = QtWidgets.QHBoxLayout()
        neck_row.addWidget(QtWidgets.QLabel("Neck joints:"))
        neck_row.addWidget(self.neck_segments_spin)
        neck_row.addWidget(self.cb_neck_ik)
        neck_row.addWidget(rig_info.make_info_button("neck_ik"))
        neck_row.addStretch(1)

        self.btn_fit_guides = QtWidgets.QPushButton(
            "Fit Guides to Selected Mesh")
        self.btn_fit_guides.setToolTip("Select your character's mesh (or meshes) and click: the guides scale to its\nheight, stand on its lowest point and centre on it, so you only have to\nplace them. The rig builds at that size, controls and all.")
        self.btn_save = QtWidgets.QPushButton("Save Guides...")
        self.btn_load = QtWidgets.QPushButton("Load Guides...")
        self.btn_delete_guides = QtWidgets.QPushButton("Delete Guides")

        gl.addWidget(self._with_info(self.btn_create_guides, "create_guides"),
                     0, 0, 1, 2)
        gl.addWidget(self._with_info(self.btn_mirror,        "mirror_guides"),
                     1, 0)
        gl.addWidget(self._with_info(self.btn_reset,         "reset_guides"),
                     1, 1)
        gl.addWidget(self._with_info(self.btn_save,          "save_guides"),
                     2, 0)
        gl.addWidget(self._with_info(self.btn_load,          "load_guides"),
                     2, 1)
        gl.addLayout(neck_row,                               3, 0, 1, 2)
        gl.addWidget(self._with_info(self.btn_fit_guides, "fit_guides"),
                     4, 0, 1, 2)
        gl.addWidget(self._with_info(self.btn_delete_guides, "delete_guides"),
                     5, 0, 1, 2)

        # Toggle the floating viewport name labels on every guide.
        # Drives the showLabels bool attr on RIG_GUIDES_GRP.
        self.btn_toggle_labels = QtWidgets.QPushButton(
            "Toggle Guide Labels")
        self.btn_toggle_labels.setToolTip(
            "Show / hide the floating text labels above each guide "
            "(like Advanced Skeleton). Toggles\n"
            "RIG_GUIDES_GRP.showLabels.")
        gl.addWidget(self.btn_toggle_labels, 6, 0, 1, 2)

        # Symmetric mode — hides R_ locators in the viewport and tells
        # Build Rig to auto-mirror L → R right before reading positions.
        self.cb_symmetric = QtWidgets.QCheckBox(
            "Symmetric mode (hide R guides, auto-mirror on build)")
        self.cb_symmetric.setChecked(True)
        self.cb_symmetric.setToolTip(
            "Hides every R_ guide locator so the rigger only sees the L\n"
            "(and C) side. When Build Rig runs, it mirrors L → R first\n"
            "so the rig is symmetric out of the box.\n"
            "Uncheck to author an asymmetric character — R guides show,\n"
            "Build Rig stops auto-mirroring.")
        gl.addWidget(self.cb_symmetric, 7, 0, 1, 2)

        # Guide mode: Free (every guide on its own) or EZ (linked like a
        # skeleton, so moving a parent guide carries its children).
        mode_row = QtWidgets.QHBoxLayout()
        mode_row.addWidget(QtWidgets.QLabel("Guide mode:"))
        self.guide_mode_combo = QtWidgets.QComboBox()
        self.guide_mode_combo.addItems(
            ["Free  (move each guide on its own)",
             "EZ  (linked: move a parent, children follow)"])
        self.guide_mode_combo.setToolTip(
            "Free: every guide moves on its own. Full control, like before.\n"
            "EZ: guides are linked in a hierarchy like the skeleton. Move the\n"
            "root and the whole body follows, move a shoulder and the elbow,\n"
            "wrist and fingers come along. Creature limbs link to what they\n"
            "attach to.\n"
            "Switch any time: nothing moves, only the linking changes.")
        mode_row.addWidget(self.guide_mode_combo, 1)
        gl.addLayout(mode_row, 8, 0, 1, 2)

        root.addWidget(self.guides_box)

        # ---------- BUILD SECTION (biped) ----------
        self.build_box = QtWidgets.QGroupBox("Build Rig")
        bl = QtWidgets.QVBoxLayout(self.build_box)
        bl.setSpacing(6)

        # Module checkboxes — ALL are now optional
        modules_row = QtWidgets.QGridLayout()
        self.cb_spine     = QtWidgets.QCheckBox("Spine")
        self.cb_neck      = QtWidgets.QCheckBox("Neck + Head")
        self.cb_clavicles = QtWidgets.QCheckBox("Clavicles")
        self.cb_arms      = QtWidgets.QCheckBox("Arms (IK/FK)")
        self.cb_legs      = QtWidgets.QCheckBox("Legs (IK/FK + foot roll)")
        self.cb_face      = QtWidgets.QCheckBox("Face")
        self.cb_fingers   = QtWidgets.QCheckBox("Fingers (5/hand)")
        self.cb_tail      = QtWidgets.QCheckBox("Tail (IK spline + FK)")

        for cb in (self.cb_spine, self.cb_neck, self.cb_clavicles,
                   self.cb_arms, self.cb_legs, self.cb_face,
                   self.cb_fingers, self.cb_tail):
            cb.setChecked(True)

        modules_row.addWidget(self.cb_spine,     0, 0)
        modules_row.addWidget(self.cb_neck,      0, 1)
        modules_row.addWidget(self.cb_clavicles, 1, 0)
        modules_row.addWidget(self.cb_arms,      1, 1)
        modules_row.addWidget(self.cb_legs,      2, 0)
        modules_row.addWidget(self.cb_face,      2, 1)
        modules_row.addWidget(self.cb_fingers,   3, 0)
        modules_row.addWidget(self.cb_tail,      3, 1)
        bl.addLayout(modules_row)

        # Face sub-module checkboxes (3x3 grid)
        face_box = QtWidgets.QGroupBox("Face submodules")
        face_box.setCheckable(False)
        face_vlayout = QtWidgets.QVBoxLayout(face_box)
        face_vlayout.setSpacing(4)

        # The heavy face IS the mesh-conforming ADVANCED FACE now (its own
        # window) — there's no separate curve-driven 'heavy' checkbox. The
        # Lid/Lip joint-count spinners below feed the advanced face, and on
        # Build Advanced Face the standard eyelid/lip joints are removed so
        # the two don't collide.
        self.btn_advanced_face = QtWidgets.QPushButton(
            "Advanced Face (mesh-conforming)…")
        self.btn_advanced_face.setStyleSheet(
            "QPushButton { color: %s; font-weight: 600; padding: 5px; }"
            % ACCENT)
        self.btn_advanced_face.setToolTip(
            "Open the Advanced Face window — fit eyelid + lip joints to your\n"
            "actual mesh edge loops (AdvancedSkeleton-style), with as many\n"
            "joints as the spinners below. On Build Advanced Face the\n"
            "standard eyelid/lip joints are deleted so they don't collide.\n"
            "The simple locator face is unchanged.")
        face_vlayout.addWidget(self.btn_advanced_face)
        cap_row = QtWidgets.QHBoxLayout()
        self.btn_face_capture = QtWidgets.QPushButton(
            "Face Capture (webcam / phone)")
        self.btn_face_capture.setToolTip(
            "Drive the face live from your webcam or phone camera and record\n"
            "it as keys (MediaPipe tracking), or import an iPhone Live Link\n"
            "Face CSV. Uses the 52 face shape dials.")
        cap_row.addWidget(self.btn_face_capture, 1)
        cap_row.addWidget(rig_info.make_info_button("face_capture"))
        face_vlayout.addLayout(cap_row)

        # Detail joint count per lid arc / lip curve — feeds the ADVANCED
        # FACE. Crank these up for denser lids/lips.
        heavy_row = QtWidgets.QHBoxLayout()
        heavy_row.addWidget(QtWidgets.QLabel("Lid jnts/arc:"))
        self.lid_jnts_spin = QtWidgets.QSpinBox()
        self.lid_jnts_spin.setRange(4, 64)
        self.lid_jnts_spin.setValue(16)
        self.lid_jnts_spin.setToolTip(
            "NOTE: the Advanced Face now places ONE joint per fitted loop\n"
            "VERTEX (1:1 with your edge loop, for clean weight painting) —\n"
            "this spinner no longer affects it. Want more joints? Select a\n"
            "denser loop. (Only the legacy heavy mode reads this value.)")
        heavy_row.addWidget(self.lid_jnts_spin)
        heavy_row.addSpacing(12)
        heavy_row.addWidget(QtWidgets.QLabel("Lip jnts/curve:"))
        self.lip_jnts_spin = QtWidgets.QSpinBox()
        self.lip_jnts_spin.setRange(4, 64)
        self.lip_jnts_spin.setValue(16)
        self.lip_jnts_spin.setToolTip(
            "NOTE: the Advanced Face now places ONE joint per fitted loop\n"
            "VERTEX — this spinner no longer affects it. Select a denser\n"
            "loop for more joints. (Only the legacy heavy mode reads this.)")
        heavy_row.addWidget(self.lip_jnts_spin)
        heavy_row.addStretch(1)
        face_vlayout.addLayout(heavy_row)

        face_grid = QtWidgets.QGridLayout()
        face_grid.setSpacing(4)
        face_vlayout.addLayout(face_grid)
        self.cb_face_jaw     = QtWidgets.QCheckBox("Jaw")
        self.cb_face_eyes    = QtWidgets.QCheckBox("Eyes (look-at)")
        self.cb_face_eyelids = QtWidgets.QCheckBox("Eyelids (12 jnts)")
        self.cb_face_brow    = QtWidgets.QCheckBox("Brow (3/side)")
        self.cb_face_mouth   = QtWidgets.QCheckBox("Mouth corners")
        self.cb_face_lips    = QtWidgets.QCheckBox("Lips (zippy)")
        self.cb_face_cheeks  = QtWidgets.QCheckBox("Cheeks")
        self.cb_face_nose    = QtWidgets.QCheckBox("Nose + nostrils")
        self.cb_face_tongue  = QtWidgets.QCheckBox("Tongue (3-jnt)")
        self.cb_face_teeth   = QtWidgets.QCheckBox("Teeth (upper+lower)")
        self.cb_face_ears    = QtWidgets.QCheckBox("Ears (L/R FK)")
        face_subs = (
            self.cb_face_jaw, self.cb_face_eyes, self.cb_face_eyelids,
            self.cb_face_brow, self.cb_face_mouth, self.cb_face_lips,
            self.cb_face_cheeks, self.cb_face_nose, self.cb_face_tongue,
            self.cb_face_teeth, self.cb_face_ears,
        )
        for cb in face_subs:
            cb.setChecked(True)
        face_grid.addWidget(self.cb_face_jaw,     0, 0)
        face_grid.addWidget(self.cb_face_eyes,    0, 1)
        face_grid.addWidget(self.cb_face_eyelids, 0, 2)
        face_grid.addWidget(self.cb_face_brow,    1, 0)
        face_grid.addWidget(self.cb_face_mouth,   1, 1)
        face_grid.addWidget(self.cb_face_lips,    1, 2)
        face_grid.addWidget(self.cb_face_cheeks,  2, 0)
        face_grid.addWidget(self.cb_face_nose,    2, 1)
        face_grid.addWidget(self.cb_face_tongue,  2, 2)
        face_grid.addWidget(self.cb_face_teeth,   3, 0)
        face_grid.addWidget(self.cb_face_ears,    3, 1)
        bl.addWidget(face_box)

        # Disable face sub-checkboxes when Face is unchecked
        def _toggle_face_subs():
            on = self.cb_face.isChecked()
            for cb in face_subs:
                cb.setEnabled(on)
        self.cb_face.toggled.connect(_toggle_face_subs)

        # Bendy count + FK chain count
        bendy_row = QtWidgets.QHBoxLayout()
        bendy_row.addWidget(QtWidgets.QLabel("Spine bendy:"))
        self.bendy_spin = QtWidgets.QSpinBox()
        self.bendy_spin.setRange(3, 15)
        self.bendy_spin.setValue(7)
        bendy_row.addWidget(self.bendy_spin)
        bendy_row.addSpacing(12)
        bendy_row.addWidget(QtWidgets.QLabel("Spine FK ctrls:"))
        self.spine_fk_spin = QtWidgets.QSpinBox()
        self.spine_fk_spin.setRange(0, 8)
        self.spine_fk_spin.setValue(3)
        self.spine_fk_spin.setToolTip(
            "FK ctrls inserted between hip and chest.\n"
            "Rotations chain up so rotating hip rotates the whole spine.\n"
            "Set to 0 to disable (hip+chest become independent siblings).")
        bendy_row.addWidget(self.spine_fk_spin)
        bendy_row.addStretch(1)
        bl.addLayout(bendy_row)

        # ---- Creature limbs + custom chains (collapsed) ----
        self.creature_box = CollapsibleBox(
            "Creature Limbs + Custom Chains")
        cr = QtWidgets.QVBoxLayout()
        cr.setSpacing(5)

        preset_row = QtWidgets.QHBoxLayout()
        preset_row.addWidget(QtWidgets.QLabel("Preset:"))
        self.creature_preset_combo = QtWidgets.QComboBox()
        for name, preset in rig_creature.PRESETS.items():
            self.creature_preset_combo.addItem(name)
            self.creature_preset_combo.setItemData(
                self.creature_preset_combo.count() - 1, preset["about"],
                QtCore.Qt.ToolTipRole)
        preset_row.addWidget(self.creature_preset_combo, 1)
        self.btn_creature_preset = QtWidgets.QPushButton("Add Preset")
        self.btn_creature_preset.setToolTip(
            "Drop guides for every limb in the preset. Move them to fit your\n"
            "model, then Build Rig.")
        preset_row.addWidget(self.btn_creature_preset)
        preset_row.addWidget(rig_info.make_info_button("creature_limbs"))
        cr.addLayout(preset_row)

        limb_grid = QtWidgets.QGridLayout()
        limb_grid.setSpacing(4)
        self.creature_type_combo = QtWidgets.QComboBox()
        self.creature_type_combo.addItems(
            ["Arm", "Leg", "Tail", "Chain (custom controls)"])
        self.creature_type_combo.setToolTip(
            "Arm / Leg / Tail: a full copy of the biped's rig for that limb.\n"
            "Chain: your own joint chain (cape, antenna, tentacle, horn,\n"
            "extra spine...) with FK, IK or switchable FK + IK controls.")
        self.creature_label_edit = QtWidgets.QLineEdit()
        self.creature_label_edit.setPlaceholderText("name, e.g. lowerArm")
        self.creature_label_edit.setToolTip(
            "Becomes part of every node name: L_lowerArm_IK_CTRL ...\n"
            "Letters and digits, starting with a letter. Names the biped or\n"
            "face already use (arm, neck, jaw ...) are not allowed.")
        self.creature_parent_combo = QtWidgets.QComboBox()
        self.creature_parent_combo.addItems(
            ["Default", "Chest", "Pelvis", "COG", "Head", "Custom (pick)"])
        self.creature_parent_combo.setToolTip(
            "What the limb hangs off. Default: chest for arms and chains,\n"
            "pelvis for legs and tails.\n"
            "Custom: select ANY guide or joint (spine 2, tail 3, another\n"
            "limb's knee...) and click Pick Selected.")
        self.creature_side_combo = QtWidgets.QComboBox()
        self.cb_creature_clavicle = QtWidgets.QCheckBox("Clavicle")
        self.cb_creature_clavicle.setChecked(True)
        self.cb_creature_fingers = QtWidgets.QCheckBox("Fingers")
        limb_grid.addWidget(QtWidgets.QLabel("Type:"), 0, 0)
        limb_grid.addWidget(self.creature_type_combo, 0, 1)
        limb_grid.addWidget(QtWidgets.QLabel("Name:"), 0, 2)
        limb_grid.addWidget(self.creature_label_edit, 0, 3)
        limb_grid.addWidget(QtWidgets.QLabel("Attach:"), 1, 0)
        limb_grid.addWidget(self.creature_parent_combo, 1, 1)
        limb_grid.addWidget(QtWidgets.QLabel("Side:"), 1, 2)
        limb_grid.addWidget(self.creature_side_combo, 1, 3)
        cr.addLayout(limb_grid)

        # Custom attach: pick the selected guide / joint.
        self.creature_attach_row = QtWidgets.QWidget()
        ar = QtWidgets.QHBoxLayout(self.creature_attach_row)
        ar.setContentsMargins(0, 0, 0, 0)
        self.btn_creature_pick = QtWidgets.QPushButton("Pick Selected")
        self.btn_creature_pick.setToolTip(
            "Select one guide locator (e.g. C_spine_02_GUIDE, C_tail_03_GUIDE,\n"
            "L_hindLeg_knee_GUIDE) or one joint, then click. The new limb\n"
            "hangs off the joint that guide builds.")
        self.creature_attach_edit = QtWidgets.QLineEdit()
        self.creature_attach_edit.setReadOnly(True)
        self.creature_attach_edit.setPlaceholderText(
            "select a guide or joint, then Pick Selected")
        ar.addWidget(self.btn_creature_pick)
        ar.addWidget(self.creature_attach_edit, 1)
        cr.addWidget(self.creature_attach_row)

        # Arm-only options.
        self.creature_arm_row = QtWidgets.QWidget()
        arm_l = QtWidgets.QHBoxLayout(self.creature_arm_row)
        arm_l.setContentsMargins(0, 0, 0, 0)
        arm_l.addWidget(self.cb_creature_clavicle)
        arm_l.addWidget(self.cb_creature_fingers)
        arm_l.addStretch(1)
        cr.addWidget(self.creature_arm_row)

        # Chain-only options.
        self.creature_chain_row = QtWidgets.QWidget()
        ch = QtWidgets.QHBoxLayout(self.creature_chain_row)
        ch.setContentsMargins(0, 0, 0, 0)
        ch.addWidget(QtWidgets.QLabel("Joints:"))
        self.creature_joints_spin = QtWidgets.QSpinBox()
        self.creature_joints_spin.setRange(1, 40)
        self.creature_joints_spin.setValue(5)
        self.creature_joints_spin.setToolTip(
            "Joints in the chain, each with its own FK control. A tip joint\n"
            "is added at the end.")
        ch.addWidget(self.creature_joints_spin)
        ch.addSpacing(10)
        ch.addWidget(QtWidgets.QLabel("Controls:"))
        self.creature_controls_combo = QtWidgets.QComboBox()
        self.creature_controls_combo.addItems(
            ["FK + IK (switch)", "FK only", "IK only"])
        self.creature_controls_combo.setToolTip(
            "FK: one rotate control per joint (capes, tails, fingers).\n"
            "IK: root / mid / tip spline controls you move around\n"
            "(tentacles, antennae). Needs 2+ joints.\n"
            "FK + IK: both, with an ikFkSwitch on the chain's settings.")
        ch.addWidget(self.creature_controls_combo, 1)
        cr.addWidget(self.creature_chain_row)

        add_row = QtWidgets.QHBoxLayout()
        self.btn_creature_add = QtWidgets.QPushButton("Add Limb Guides")
        self.btn_creature_add.setToolTip(
            "Arm / Leg / Tail: copies the biped's own guides into a new limb\n"
            "under CREATURE_GUIDES_GRP, nudged aside so you can see it.\n"
            "Chain: a row of guides starting at what it attaches to.\n"
            "With 'Both (mirrored)' only the left side gets guides; the right\n"
            "side is mirrored on Build Rig.")
        self.btn_creature_from_joints = QtWidgets.QPushButton(
            "From Selected Joints")
        self.btn_creature_from_joints.setToolTip(
            "Drew your own joints in Maya? Select the chain's ROOT joint and\n"
            "click: chain guides land exactly on your joints (their count\n"
            "sets the joint count). If your root sits under a rig joint and\n"
            "Attach is Default, the chain attaches to that joint.")
        add_row.addWidget(self.btn_creature_add, 1)
        add_row.addWidget(self.btn_creature_from_joints, 1)
        cr.addLayout(add_row)

        self.creature_list = QtWidgets.QListWidget()
        self.creature_list.setFixedHeight(84)
        cr.addWidget(self.creature_list)
        list_row = QtWidgets.QHBoxLayout()
        self.btn_creature_select = QtWidgets.QPushButton("Select Guides")
        self.btn_creature_remove = QtWidgets.QPushButton("Remove Limb")
        list_row.addWidget(self.btn_creature_select)
        list_row.addWidget(self.btn_creature_remove)
        cr.addLayout(list_row)
        creature_note = QtWidgets.QLabel(
            "Move the new guides, then Build Rig. Extra limbs get their own "
            "controls, auto-walk, space switching and a Creature tab in the "
            "picker.")
        creature_note.setWordWrap(True)
        creature_note.setStyleSheet(ft.subtitle_css())
        cr.addWidget(creature_note)
        self.creature_box.setContentLayout(cr)
        bl.addWidget(self.creature_box)

        # Source toggle
        source_row = QtWidgets.QHBoxLayout()
        self.rb_from_guides = QtWidgets.QRadioButton("Build from guides")
        self.rb_from_defaults = QtWidgets.QRadioButton("Build from defaults")
        self.rb_from_guides.setChecked(True)
        source_row.addWidget(self.rb_from_guides)
        source_row.addWidget(self.rb_from_defaults)
        source_row.addStretch(1)
        bl.addLayout(source_row)

        # Build / Delete buttons
        action_row = QtWidgets.QHBoxLayout()
        self.btn_build = QtWidgets.QPushButton("Build Rig")
        self.btn_build.setObjectName("primary")
        self.btn_build.setStyleSheet(
            "QPushButton { padding: 7px; }"
        )
        self.btn_delete_rig = QtWidgets.QPushButton("Delete Rig")
        action_row.addWidget(self.btn_build, 2)
        action_row.addWidget(rig_info.make_info_button("build_rig"))
        action_row.addWidget(self.btn_delete_rig, 1)
        action_row.addWidget(rig_info.make_info_button("delete_rig"))
        bl.addLayout(action_row)

        # ---- ONE-CLICK AUTO-SKIN (the only skin button most users need) ----
        # Grabs every mesh in the geo group AND every BIND joint (body +
        # face), then Geodesic-Voxel binds them in a single shot. No manual
        # selection required.
        self.btn_auto_skin_all = QtWidgets.QPushButton(
            "Auto-Skin Everything  (meshes → all joints)")
        self.btn_auto_skin_all.setStyleSheet(
            "QPushButton { padding: 7px; font-weight: 600; color: %s; }"
            % ft.OK)
        self.btn_auto_skin_all.setToolTip(
            "One click — no selection needed. Finds every mesh in the geo\n"
            "group, gathers every BIND joint (body + face), and Geodesic-\n"
            "Voxel binds them all. Refine later in NG Skin Tools / Delta Mush.")
        bl.addWidget(self.btn_auto_skin_all)

        # ---- Debug / manual tools (collapsed by default — no clutter) ----
        # Everything that used to sit out in the open: the granular skin
        # steps, the selection helpers and the build diagnostic.
        self.debug_box = CollapsibleBox("Debug / manual skin + diagnostics")
        dbg = QtWidgets.QVBoxLayout()
        dbg.setSpacing(5)

        self.btn_verify = QtWidgets.QPushButton(
            "Verify Build (print joint vs guide positions)")
        dbg.addWidget(self._with_info(self.btn_verify, "verify"))

        self.btn_select_skin_jnts = QtWidgets.QPushButton(
            "Select All Skinning Joints (BIND_JNT)")
        self.btn_select_skin_jnts.setToolTip(
            "Select every BIND joint (body + face). Hold SHIFT to ADD them\n"
            "to the current selection — e.g. Select Meshes, then SHIFT-click\n"
            "this, and you have meshes + joints ready to hand-bind.")
        dbg.addWidget(self._with_info(self.btn_select_skin_jnts,
                                      "select_skin_jnts"))

        self.btn_select_geo = QtWidgets.QPushButton(
            "Select All Meshes in Geo Group")
        self.btn_select_geo.setToolTip(
            "Select every mesh under the 'geo' group. Hold SHIFT to ADD to\n"
            "the current selection instead of replacing it.")
        dbg.addWidget(self.btn_select_geo)

        self.btn_wire_mesh_vis = QtWidgets.QPushButton(
            "Wire Root meshVis → Geo Group")
        self.btn_wire_mesh_vis.setToolTip(
            "Connect C_global_CTRL.meshVis to the geo group's .v. Use after\n"
            "importing the mesh if you built the rig first.")
        dbg.addWidget(self.btn_wire_mesh_vis)

        self.btn_smooth_bind = QtWidgets.QPushButton(
            "Auto-Skin (Geodesic Voxel) → BIND joints")
        self.btn_smooth_bind.setToolTip(
            "Geodesic-Voxel bind the SELECTED mesh(es) to every BIND joint.\n"
            "(Auto-Skin Everything above does the whole geo group at once.)")
        dbg.addWidget(self._with_info(self.btn_smooth_bind, "smooth_bind"))

        self.btn_ng_init = QtWidgets.QPushButton(
            "Auto-Skin Mesh + Open NG Skin Tools")
        self.btn_ng_init.setStyleSheet(
            "QPushButton { color: %s; }" % ACCENT)
        self.btn_ng_init.setToolTip(
            "Geodesic-Voxel bind the selected mesh, init NG Skin Tools\n"
            "layers, and open the NG panel. Requires NG Skin Tools 2.")
        dbg.addWidget(self._with_info(self.btn_ng_init, "ng_init"))

        self.btn_delta_mush = QtWidgets.QPushButton(
            "Apply Delta Mush to Selected Mesh")
        self.btn_delta_mush.setToolTip(
            "Add a deltaMush deformer on top of the skinCluster to smooth\n"
            "skinning artifacts non-destructively.")
        dbg.addWidget(self._with_info(self.btn_delta_mush, "delta_mush"))

        self.debug_box.setContentLayout(dbg)
        bl.addWidget(self.debug_box)

        root.addWidget(self.build_box)

        # ---------- QUADRUPED SECTION ----------
        self.quad_box = QtWidgets.QGroupBox("Quadruped")
        ql = QtWidgets.QGridLayout(self.quad_box)
        ql.setSpacing(6)

        # Starting animal + a foot type per leg pair (they mix freely).
        self.quad_animal_combo = QtWidgets.QComboBox()
        for key in quadruped_guides.ANIMALS:
            self.quad_animal_combo.addItem(
                quadruped_guides.ANIMAL_LABELS[key], key)
        self.quad_animal_combo.setToolTip(
            "The animal the guides start from. It also picks the feet:\n"
            "Horse = hooves, Cat / Dog = paws, Raptor = arms + claws.")
        self.quad_front_combo = QtWidgets.QComboBox()
        for key in quadruped_guides.FRONT_FEET:
            self.quad_front_combo.addItem(
                quadruped_guides.FOOT_LABELS[key], key)
        self.quad_back_combo = QtWidgets.QComboBox()
        for key in quadruped_guides.BACK_FEET:
            self.quad_back_combo.addItem(
                quadruped_guides.FOOT_LABELS[key], key)
        for combo, which in ((self.quad_front_combo, "front"),
                             (self.quad_back_combo, "back")):
            combo.setToolTip(
                f"Foot type of the {which} legs.\n"
                "Hoof: stands on the hoof tip (horse, deer, cow).\n"
                "Paw: stands on the toe pads, four toes (cat, dog).\n"
                "Claw: long raised ankle, three toes, sickle claw and\n"
                "dewclaw (raptor)."
                + ("\nArm: held off the ground with clawed fingers\n"
                   "(raptor arms)." if which == "front" else ""))
        self.chk_quad_toes = QtWidgets.QCheckBox("Toes and fingers")
        self.chk_quad_toes.setChecked(True)
        self.chk_quad_toes.setToolTip(
            "Build a chain for each toe / finger on paws, claws and arms,\n"
            "with curl and spread dials. Off: one piece per foot.")

        animal_row = QtWidgets.QHBoxLayout()
        animal_row.addWidget(QtWidgets.QLabel("Animal"))
        animal_row.addWidget(self.quad_animal_combo, 1)
        animal_row.addWidget(self.chk_quad_toes)
        feet_row = QtWidgets.QHBoxLayout()
        feet_row.addWidget(QtWidgets.QLabel("Front"))
        feet_row.addWidget(self.quad_front_combo, 1)
        feet_row.addWidget(QtWidgets.QLabel("Back"))
        feet_row.addWidget(self.quad_back_combo, 1)
        feet_row.addWidget(rig_info.make_info_button("quad_feet"))

        self.btn_quad_create_guides = QtWidgets.QPushButton("Create Guides")
        self.btn_quad_mirror = QtWidgets.QPushButton("Mirror L → R")
        self.btn_quad_reset = QtWidgets.QPushButton("Reset to Defaults")
        self.btn_quad_fit = QtWidgets.QPushButton(
            "Fit Guides to Selected Mesh")
        self.btn_quad_fit.setToolTip("Select your character's mesh (or meshes) and click: the guides scale to its\nheight, stand on its lowest point and centre on it, so you only have to\nplace them. The rig builds at that size, controls and all.")
        self.btn_quad_delete_guides = QtWidgets.QPushButton("Delete Guides")
        self.btn_quad_build = QtWidgets.QPushButton("Build Quadruped Rig")
        self.btn_quad_build.setObjectName("primary")
        self.btn_quad_build.setStyleSheet(
            "QPushButton { padding: 7px; }")
        self.btn_quad_delete_rig = QtWidgets.QPushButton(
            "Delete Quadruped Rig")

        # Source toggle — build from guides or from baked-in defaults.
        self.rb_quad_from_guides = QtWidgets.QRadioButton("From guides")
        self.rb_quad_from_defaults = QtWidgets.QRadioButton("From defaults")
        self.rb_quad_from_guides.setChecked(True)

        ql.addLayout(animal_row, 0, 0, 1, 2)
        ql.addLayout(feet_row, 1, 0, 1, 2)
        ql.addWidget(self._with_info(self.btn_quad_create_guides,
                                       "quad_create_guides"),
                     2, 0, 1, 2)
        ql.addWidget(self._with_info(self.btn_quad_mirror, "quad_mirror"),
                     3, 0)
        ql.addWidget(self.btn_quad_reset,         3, 1)
        ql.addWidget(self._with_info(self.btn_quad_fit, "fit_guides"),
                     4, 0, 1, 2)
        ql.addWidget(self.btn_quad_delete_guides, 5, 0, 1, 2)
        ql.addWidget(self.rb_quad_from_guides,    6, 0)
        ql.addWidget(self.rb_quad_from_defaults,  6, 1)
        ql.addWidget(self._with_info(self.btn_quad_build, "quad_build"),
                     7, 0)
        ql.addWidget(self.btn_quad_delete_rig,    7, 1)

        root.addWidget(self.quad_box)

        # ---------- BIRD (eagle / hawk) SECTION ----------
        self.bird_box = QtWidgets.QGroupBox("Bird (Eagle / Hawk)")
        bdl = QtWidgets.QGridLayout(self.bird_box)
        bdl.setSpacing(6)

        self.btn_bird_create_guides = QtWidgets.QPushButton(
            "Create Bird Guides")
        self.btn_bird_mirror = QtWidgets.QPushButton("Mirror L → R")
        self.btn_bird_reset = QtWidgets.QPushButton("Reset to Defaults")
        self.btn_bird_fit = QtWidgets.QPushButton(
            "Fit Guides to Selected Mesh")
        self.btn_bird_fit.setToolTip("Select your character's mesh (or meshes) and click: the guides scale to its\nheight, stand on its lowest point and centre on it, so you only have to\nplace them. The rig builds at that size, controls and all.")
        self.btn_bird_delete_guides = QtWidgets.QPushButton("Delete Guides")
        self.btn_bird_build = QtWidgets.QPushButton("Build Bird Rig")
        self.btn_bird_build.setObjectName("primary")
        self.btn_bird_build.setStyleSheet(
            "QPushButton { padding: 7px; }")
        self.btn_bird_delete_rig = QtWidgets.QPushButton("Delete Bird Rig")

        # Source toggle — build from guides or from baked-in defaults.
        self.rb_bird_from_guides = QtWidgets.QRadioButton("From guides")
        self.rb_bird_from_defaults = QtWidgets.QRadioButton("From defaults")
        self.rb_bird_from_guides.setChecked(True)

        bdl.addWidget(self._with_info(self.btn_bird_create_guides,
                                        "bird_create_guides"),
                      0, 0, 1, 2)
        bdl.addWidget(self._with_info(self.btn_bird_mirror, "bird_mirror"),
                      1, 0)
        bdl.addWidget(self.btn_bird_reset,         1, 1)
        bdl.addWidget(self._with_info(self.btn_bird_fit, "fit_guides"),
                      2, 0, 1, 2)
        bdl.addWidget(self.btn_bird_delete_guides, 3, 0, 1, 2)
        bdl.addWidget(self.rb_bird_from_guides,    4, 0)
        bdl.addWidget(self.rb_bird_from_defaults,  4, 1)
        bdl.addWidget(self._with_info(self.btn_bird_build, "bird_build"),
                      5, 0)
        bdl.addWidget(self.btn_bird_delete_rig,    5, 1)

        root.addWidget(self.bird_box)

        # ---------- VEHICLE SECTION ----------
        self.vehicle_box = QtWidgets.QGroupBox("Vehicle (4-Wheeled)")
        vl = QtWidgets.QGridLayout(self.vehicle_box)
        vl.setSpacing(6)

        # --- Guides (left + center; right is mirrored on build) ---
        self.btn_vehicle_create_guides = QtWidgets.QPushButton(
            "Create Car Guides (L + C)")
        self.btn_vehicle_create_guides.setToolTip(
            "Spawn the per-car locators: rim centres, spring tops,\n"
            "door hinges, steering, hood, trunk. You place only the\n"
            "LEFT side + CENTER — the right side mirrors on build.")
        self.btn_vehicle_fit_wheels = QtWidgets.QPushButton(
            "Fit Wheels to Selected Tyres")
        self.btn_vehicle_fit_wheels.setToolTip(
            "Select tyre meshes (one per axle is enough, either side, or\n"
            "both tyres of an axle in one mesh) and click: the nearest wheel\n"
            "guide, the vehicle's or a trailer's axle guide, jumps to each\n"
            "tyre's centre and its circle takes the tyre's size. Or place a\n"
            "wheel guide by hand and set its Radius in the channel box:\n"
            "every axle has its own, and moving a guide never changes its\n"
            "size.")
        self.btn_vehicle_reset_guides = QtWidgets.QPushButton(
            "Reset Guides")
        self.btn_vehicle_delete_guides = QtWidgets.QPushButton(
            "Delete Guides")

        self.btn_vehicle_build = QtWidgets.QPushButton("Build Vehicle Rig")
        self.btn_vehicle_build.setObjectName("primary")
        self.btn_vehicle_build.setStyleSheet(
            "QPushButton { padding: 7px; }")
        self.btn_vehicle_delete_rig = QtWidgets.QPushButton(
            "Delete Vehicle Rig")

        # Build source: from the guides you placed, or baked-in defaults.
        self.rb_vehicle_from_guides = QtWidgets.QRadioButton("From guides")
        self.rb_vehicle_from_defaults = QtWidgets.QRadioButton(
            "From defaults")
        self.rb_vehicle_from_guides.setChecked(True)

        # Spoke count + feature toggles.
        self.vehicle_spoke_spin = QtWidgets.QSpinBox()
        self.vehicle_spoke_spin.setRange(6, 32)
        self.vehicle_spoke_spin.setValue(16)
        self.vehicle_spoke_spin.setToolTip(
            "Number of tire spokes per wheel. More = smoother tread "
            "deformation, heavier scene.")
        self.cb_vehicle_spring = QtWidgets.QCheckBox("Suspension")
        self.cb_vehicle_spring.setChecked(True)
        self.cb_vehicle_spring.setToolTip(
            "Springs + suspension travel (each wheel rides the ground, the\n"
            "body rolls and pitches). Pick each axle's spring type in the\n"
            "Springs section. Off = wheels fixed to the body.")

        # Springs, per axle: type + solid axle.
        self.springs_box = CollapsibleBox(
            "Springs  (coil-over, leaf spring, solid axle)")
        sg = QtWidgets.QGridLayout()
        sg.setSpacing(5)
        self.spring_rows = {}
        for r, (key, label) in enumerate((
                ("front", "Front axle"), ("mid1", "Middle axle 1"),
                ("mid2", "Middle axle 2"), ("back", "Back axle"))):
            lab = QtWidgets.QLabel(label)
            combo = QtWidgets.QComboBox()
            combo.addItems(["Coil-over", "Leaf spring + shock", "None"])
            combo.setToolTip(
                "Coil-over: a coil + damper from the spring guide down to\n"
                "the wheel (cars).\n"
                "Leaf spring + shock: a bending leaf (7 joints) on a\n"
                "swinging shackle, plus a telescoping shock (trucks, 4x4s,\n"
                "old cars). Adds 6 orange guides per axle: place them on\n"
                "the leaf's eyes, its middle, the shackle pin and the shock\n"
                "mounts.\n"
                "None: the wheel still travels, with no spring parts.")
            solid = QtWidgets.QCheckBox("Solid axle")
            solid.setToolTip(
                "One beam ties the two wheels: when one rides over a bump\n"
                "the axle tilts and both wheels lean with it. Builds\n"
                "<axle>_axle_BIND_JNT for the axle tube (bind it as Axle).\n"
                "Off = independent wheels.")
            sg.addWidget(lab, r, 0)
            sg.addWidget(combo, r, 1)
            sg.addWidget(solid, r, 2)
            self.spring_rows[key] = (lab, combo, solid)
        self.lbl_springs_hint = QtWidgets.QLabel(
            "Tracked vehicles use coil-overs on independent road wheels.")
        self.lbl_springs_hint.setWordWrap(True)
        sg.addWidget(self.lbl_springs_hint, 4, 0, 1, 2)
        sg.addWidget(rig_info.make_info_button("vehicle_springs"), 4, 2,
                     QtCore.Qt.AlignRight)
        sg.setColumnStretch(1, 1)
        self.springs_box.setContentLayout(sg)

        # Tank wheels: a free layout for tracked vehicles.
        self.track_box = CollapsibleBox(
            "Tank Wheels  (any layout: road wheels, rollers, gears)")
        tg = QtWidgets.QGridLayout()
        tg.setSpacing(5)
        hint = QtWidgets.QLabel(
            "Select the tank's wheel meshes and its tread, then Fit: one "
            "guide per wheel, sized to it, with its job guessed. Orange = "
            "road wheel (on the ground), cyan = roller (the tread wraps "
            "over it), grey = gear (just spins).")
        hint.setWordWrap(True)
        tg.addWidget(hint, 0, 0, 1, 3)
        self.btn_track_fit = QtWidgets.QPushButton(
            "Fit Wheels from Selected Meshes")
        self.btn_track_fit.setToolTip(
            "Select the tank's wheel meshes (one side, both sides, or a\n"
            "mesh holding both wheels of a pair) and its tread. Makes one\n"
            "L_trackWheel guide per wheel, on its centre and sized to it,\n"
            "guesses road wheel / roller / gear, measures the tread's\n"
            "thickness, and scales the other guides to the tank.")
        tg.addWidget(self.btn_track_fit, 1, 0, 1, 3)
        self.btn_track_add = QtWidgets.QPushButton("Add Wheel Guide")
        self.btn_track_add.setToolTip(
            "Adds a wheel guide: on each selected wheel mesh, else next to\n"
            "the chassis guide. It gets the role picked below.")
        self.btn_track_remove = QtWidgets.QPushButton("Remove Selected")
        tg.addWidget(self.btn_track_add, 2, 0, 1, 2)
        tg.addWidget(self.btn_track_remove, 2, 2)
        self.track_role_combo = QtWidgets.QComboBox()
        self.track_role_combo.addItems(list(vehicle_guides.TRACK_ROLE_LABELS))
        self.cb_track_reverse = QtWidgets.QCheckBox("Reverse spin")
        self.cb_track_reverse.setToolTip(
            "Turn the other way (a gear meshed with the wheel next to it).")
        self.btn_track_role = QtWidgets.QPushButton("Set on Selected")
        self.btn_track_role.setToolTip(
            "Select wheel guides, pick a role (and spin direction), click.")
        tg.addWidget(self.track_role_combo, 3, 0)
        tg.addWidget(self.cb_track_reverse, 3, 1)
        tg.addWidget(self.btn_track_role, 3, 2)
        tg.addWidget(QtWidgets.QLabel("Tread thickness:"), 4, 0)
        self.track_thickness_spin = QtWidgets.QDoubleSpinBox()
        self.track_thickness_spin.setRange(0.0, 100000.0)
        self.track_thickness_spin.setDecimals(3)
        self.track_thickness_spin.setToolTip(
            "How thick the tread is under the road wheels: they ride this\n"
            "far above the ground, and the tread loop runs round them at\n"
            "half of it. Fit measures it from the tread mesh.")
        tg.addWidget(self.track_thickness_spin, 4, 1)
        tg.addWidget(rig_info.make_info_button("vehicle_track_wheels"), 4, 2,
                     QtCore.Qt.AlignRight)
        self.track_label = QtWidgets.QLabel("")
        self.track_label.setWordWrap(True)
        tg.addWidget(self.track_label, 5, 0, 1, 3)
        tg.setColumnStretch(1, 1)
        self.track_box.setContentLayout(tg)
        self.cb_vehicle_ctrls = QtWidgets.QCheckBox("Per-spoke ctrls")
        self.cb_vehicle_ctrls.setChecked(True)

        # Layout: axles (4 / 6 / 8 wheels), second steering axle, tank treads.
        self.vehicle_axle_combo = QtWidgets.QComboBox()
        self.vehicle_axle_combo.addItems(
            ["4 wheels (2 axles)", "6 wheels (3 axles)", "8 wheels (4 axles)"])
        self.vehicle_axle_combo.setToolTip(
            "Trucks, APCs and trailers. Changing it adds or removes the\n"
            "middle-axle guides (wheel + spring) right away, so every axle\n"
            "can be placed and sized on its own.")
        self.cb_vehicle_steer2 = QtWidgets.QCheckBox("Steer 2nd axle")
        self.cb_vehicle_steer2.setToolTip(
            "Big trucks steer the second axle too, at about half the angle.")
        self.cb_vehicle_tracked = QtWidgets.QCheckBox("Tracked (tank treads)")
        self.cb_vehicle_tracked.setToolTip(
            "A tread loop around each side's road wheels, with link joints\n"
            "that run with the distance travelled. The tank skid-steers: the\n"
            "outer tread runs faster in turns and A / D turn it on the spot\n"
            "in Drive Mode. Instance a link mesh with 'Instance Link Mesh'.")
        self.btn_vehicle_links = QtWidgets.QPushButton("Instance Link Mesh")
        self.btn_vehicle_links.setToolTip(
            "Select one tread link mesh (modelled at the origin, running\n"
            "along +Z, axle along X) and click: it's instanced onto every\n"
            "tread link joint of a tracked vehicle. Click with nothing\n"
            "selected to see how long to model the link.")

        # Ground mesh: assign the SELECTED mesh for per-wheel terrain.
        self.btn_vehicle_assign_ground = QtWidgets.QPushButton(
            "Assign Selected Mesh as Ground")
        self.btn_vehicle_assign_ground.setToolTip(
            "Select a polygon ground/terrain mesh, then click this.\n"
            "Each tire samples the surface beneath it (closestPointOnMesh)\n"
            "so they flatten against slopes / hills independently.\n"
            "For sharp obstacles, also 'Bake Ground Raycast' or use Drive\n"
            "Mode — both sample straight DOWN so wheels don't lift early.")
        self.btn_vehicle_clear_ground = QtWidgets.QPushButton(
            "Clear Ground (flat)")
        self.btn_vehicle_clear_ground.setToolTip(
            "Revert all tires to the flat C_ground_LOC.")

        # Bake a TRUE straight-down ground sample across the timeline.
        # The live closestPointOnMesh hookup lifts the wheel early when an
        # obstacle's flank is the nearest point (the choppy frame-25->28
        # lift). Baking a downward raycast per frame keys the real ground
        # directly under each wheel, so it stays planted until it's over
        # the bump. Use this for hand-keyed animation (drive mode already
        # raycasts live).
        self.btn_vehicle_bake_ground = QtWidgets.QPushButton(
            "Bake Ground Raycast (timeline)")
        self.btn_vehicle_bake_ground.setToolTip(
            "For hand-keyed car animation over terrain. Bakes a straight-\n"
            "DOWN ground sample under each wheel across the playback range,\n"
            "fixing the early/choppy lift you get when closestPointOnMesh\n"
            "snaps onto an obstacle's flank before the wheel is over it.\n"
            "Assign a ground mesh first. Re-bake after moving the car or\n"
            "terrain. 'Clear Ground' removes the bake.")

        # One-shot in-place upgrade for rigs built before the latest fixes:
        # body-motion (negative gains + nose-up uphill) AND tyre deflation
        # squat (flat tyres sag on flat ground / while driving).
        self.btn_vehicle_fix_body = QtWidgets.QPushButton(
            "Upgrade Existing Rig (body + tyres)")
        self.btn_vehicle_fix_body.setToolTip(
            "For cars built earlier — upgrades them in place, no rebuild:\n"
            " • Body motion: unclamps bodyRoll / bodyPitch / bodyBob so you\n"
            "   can dial them NEGATIVE, and flips pitch so driving UPHILL\n"
            "   lifts the nose instead of diving forward.\n"
            " • Tyre deflation: adds the squat so a flat tyre (pressure 0)\n"
            "   visibly sags + flattens on flat ground / while driving.\n"
            "Newly built rigs already have all this — safe to run any time\n"
            "(idempotent; it only adds what's missing).")

        # Live WASD driving — keyframes the car as you drive it.
        self.btn_vehicle_drive = QtWidgets.QPushButton(
            "🚗  Drive Mode (WASD)")
        self.btn_vehicle_drive.setObjectName("primary")
        self.btn_vehicle_drive.setStyleSheet(
            "QPushButton { padding: 6px; }")
        self.btn_vehicle_drive.setToolTip(
            "Open the live drive panel. W/S accelerate+brake, A/D steer,\n"
            "Esc stops. Every frame is keyframed — drive around, stop,\n"
            "and you have the animation with wheels spinning, steering,\n"
            "and tires deforming. Assign a ground mesh first for terrain.")

        # ---- Tracks the tyres leave, and burnouts ----
        self.btn_vehicle_tracks = QtWidgets.QPushButton("Bake Tyre Tracks")
        self.btn_vehicle_tracks.setToolTip(
            "Lay the marks the tyres left over the timeline: a flat ribbon\n"
            "under the contact patch, following wherever the wheel really\n"
            "went (a drive, a physics bake or your own keys), on flat ground\n"
            "or terrain. A jump leaves a gap, because the tyre was in the\n"
            "air. Ordinary geometry with UVs along its length: shade it\n"
            "however you like. Bake again for a second pass.")
        self.track_wheels_combo = QtWidgets.QComboBox()
        self.track_wheels_combo.addItems(["Driven wheels", "All wheels"])
        self.track_wheels_combo.setToolTip(
            "Which wheels mark. The driven ones are the pair that lights\n"
            "up, so those are the default.")
        self.cb_track_slip = QtWidgets.QCheckBox("Only when sliding")
        self.cb_track_slip.setChecked(True)
        self.cb_track_slip.setToolTip(
            "On (the default): mark only where the tyre was actually\n"
            "sliding, sideways in a drift, spin or handbrake turn, or\n"
            "lengthways when it is turning faster or slower than the road\n"
            "is going by (wheelspin, a burnout, a locked wheel). A tyre\n"
            "that is simply rolling leaves nothing on tarmac.\n"
            "Off: mark everywhere the tyres touch, which is what sand,\n"
            "mud and snow want.")
        self.btn_vehicle_burnout = QtWidgets.QPushButton("Burnout")
        self.btn_vehicle_burnout.setToolTip(
            "Light up the driven wheels from the current frame: they spin\n"
            "far faster than the vehicle moves, it creeps forward, the body\n"
            "squats, and it lays the wide dark marks a burnout leaves.\n"
            "Pick which wheels drive it on the right.")
        self.burnout_wheels_combo = QtWidgets.QComboBox()
        self.burnout_wheels_combo.addItems(["Rear wheels", "Front wheels",
                                            "All wheels"])
        self.burnout_wheels_combo.setToolTip(
            "Which wheels get the power. A bike's back wheel counts as rear.")
        self.burnout_frames_spin = QtWidgets.QSpinBox()
        self.burnout_frames_spin.setRange(5, 400)
        self.burnout_frames_spin.setValue(45)
        self.burnout_frames_spin.setPrefix("frames ")
        self.burnout_frames_spin.setToolTip("How long the burnout lasts.")
        self.btn_vehicle_clear_tracks = QtWidgets.QPushButton("Clear Tracks")
        self.btn_vehicle_clear_tracks.setToolTip(
            "Delete every baked mark (the burnout keys stay).")
        self.btn_vehicle_clear_burnout = QtWidgets.QPushButton(
            "Clear Burnout")
        self.btn_vehicle_clear_burnout.setToolTip(
            "Take the burnout keys off the wheels and the body again.")

        # Physics: simulate the car along its path and bake the result.
        self.btn_vehicle_simulate = QtWidgets.QPushButton(
            "Simulate Physics (bake timeline)")
        self.btn_vehicle_simulate.setObjectName("primary")
        self.btn_vehicle_simulate.setStyleSheet(
            "QPushButton { padding: 6px; }")
        self.btn_vehicle_simulate.setToolTip(
            "tyFlow-style ground reaction. The car follows the path you\n"
            "drove or keyed (C_global_CTRL move + turn), and physics does the\n"
            "rest: suspension springs, body roll in turns, nose dive when\n"
            "braking, jumps off ramps, landings and rebounds, on the assigned\n"
            "ground mesh (or flat ground).\n"
            "Bakes keys over the playback range. Run it again after changing\n"
            "the path or settings; it always starts from your original path.\n"
            "Tune it on C_chassis_CTRL > PHYSICS (stiffness, damping, grip,\n"
            "centre of mass, path follow, start drop).")
        self.btn_vehicle_clear_sim = QtWidgets.QPushButton(
            "Clear Simulation")
        self.btn_vehicle_clear_sim.setToolTip(
            "Remove the baked physics and put your original path back.")

        spoke_row = QtWidgets.QVBoxLayout()
        spokes = QtWidgets.QHBoxLayout()
        spokes.addWidget(QtWidgets.QLabel("Spokes:"))
        spokes.addWidget(self.vehicle_spoke_spin)
        spokes.addWidget(self.cb_vehicle_spring)
        spokes.addWidget(self.cb_vehicle_ctrls)
        spokes.addStretch(1)
        layout_row = QtWidgets.QHBoxLayout()
        layout_row.addWidget(self.vehicle_axle_combo)
        layout_row.addWidget(self.cb_vehicle_steer2)
        layout_row.addWidget(self.cb_vehicle_tracked)
        layout_row.addStretch(1)
        spoke_row.addLayout(spokes)
        spoke_row.addLayout(layout_row)
        spoke_row.addWidget(self.springs_box)
        spoke_row.addWidget(self.track_box)

        src_row = QtWidgets.QHBoxLayout()
        src_row.addWidget(self.rb_vehicle_from_guides)
        src_row.addWidget(self.rb_vehicle_from_defaults)
        src_row.addStretch(1)

        self.btn_vehicle_fit = QtWidgets.QPushButton("Fit Guides to Model")
        self.btn_vehicle_fit.setToolTip(
            "Select the whole vehicle (its meshes) and click: every guide\n"
            "scales to the model's length, sits on its lowest point and\n"
            "centres on it. Then place them, and use Fit Wheels to Selected\n"
            "Tyres for the wheel sizes.")
        guide_row = QtWidgets.QHBoxLayout()
        guide_row.addWidget(self.btn_vehicle_create_guides, 1)
        guide_row.addWidget(self.btn_vehicle_fit, 1)
        guide_row.addWidget(self.btn_vehicle_fit_wheels, 1)
        vl.addLayout(guide_row,                       0, 0, 1, 2)
        self.btn_vehicle_save_guides = QtWidgets.QPushButton(
            "Save Guides...")
        self.btn_vehicle_save_guides.setToolTip(
            "Save every vehicle guide to a .json file: positions, wheel\n"
            "sizes, springs, tank wheels and tread thickness.")
        self.btn_vehicle_load_guides = QtWidgets.QPushButton(
            "Load Guides...")
        self.btn_vehicle_load_guides.setToolTip(
            "Load vehicle guides saved with Save Guides (creates them if\n"
            "the scene has none, with as many axles as the file).")
        guide_io = QtWidgets.QHBoxLayout()
        guide_io.addWidget(self.btn_vehicle_reset_guides, 1)
        guide_io.addWidget(self.btn_vehicle_save_guides, 1)
        guide_io.addWidget(self.btn_vehicle_load_guides, 1)
        guide_io.addWidget(self.btn_vehicle_delete_guides, 1)
        vl.addLayout(guide_io,                        1, 0, 1, 2)
        # --- Bind a selected mesh to its part bone(s) ---
        # Select the car part mesh, pick the type, hit Bind — the tool
        # auto-detects which corner (LF/RF/LB/RB) and skins it.
        self.vehicle_part_combo = QtWidgets.QComboBox()
        self.vehicle_part_combo.addItems(list(vehicle_bind.PART_TYPES))
        self.btn_vehicle_bind = QtWidgets.QPushButton(
            "Bind Selected Mesh to Part")
        self.btn_vehicle_bind.setToolTip(
            "Select the car part mesh, choose its type on the left, then\n"
            "click. The tool finds the nearest matching bone (auto-detects\n"
            "LF/RF/LB/RB for wheels & doors) and skins the mesh to it.\n"
            "Tire = hub + all spokes (so air pressure + bumps deform it);\n"
            "Leaf Spring = its 7 leaf joints (it bends with the axle);\n"
            "Body / Door / Hood / Trunk / Rim / Spring / Shackle / Shock /\n"
            "Axle = single rigid bone.")
        bind_row = QtWidgets.QHBoxLayout()
        bind_row.addWidget(self.vehicle_part_combo)
        bind_row.addWidget(self.btn_vehicle_bind, 1)

        vl.addLayout(spoke_row,                       2, 0, 1, 2)
        vl.addLayout(src_row,                         3, 0, 1, 2)
        vl.addWidget(self.btn_vehicle_build,          4, 0)
        vl.addWidget(self.btn_vehicle_delete_rig,     4, 1)
        vl.addWidget(self.btn_vehicle_assign_ground,  5, 0)
        vl.addWidget(self.btn_vehicle_clear_ground,   5, 1)
        vl.addWidget(self.btn_vehicle_bake_ground,    6, 0)
        vl.addWidget(self.btn_vehicle_fix_body,       6, 1)
        vl.addLayout(bind_row,                        7, 0, 1, 2)
        vl.addWidget(self.btn_vehicle_drive,          8, 0, 1, 2)
        vl.addWidget(self.btn_vehicle_simulate,       9, 0)
        vl.addWidget(self.btn_vehicle_clear_sim,      9, 1)
        vl.addWidget(self.btn_vehicle_links,          10, 0, 1, 2)
        track_row = QtWidgets.QHBoxLayout()
        track_row.addWidget(self.btn_vehicle_burnout, 1)
        track_row.addWidget(self.burnout_wheels_combo)
        track_row.addWidget(self.burnout_frames_spin)
        clear_row = QtWidgets.QHBoxLayout()
        clear_row.addWidget(self.btn_vehicle_clear_tracks)
        clear_row.addWidget(self.btn_vehicle_clear_burnout)
        bake_row = QtWidgets.QHBoxLayout()
        bake_row.addWidget(self.btn_vehicle_tracks, 1)
        bake_row.addWidget(self.track_wheels_combo)
        bake_row.addWidget(self.cb_track_slip)
        vl.addLayout(bake_row,                        11, 0, 1, 2)
        vl.addLayout(track_row,                       12, 0, 1, 2)
        vl.addLayout(clear_row,                       13, 0, 1, 2)

        # ---- Vehicle parts: turrets, excavator arms, custom hinges ----
        self.parts_box = CollapsibleBox("Vehicle Parts  (turrets, arms, hinges)")
        pl = QtWidgets.QVBoxLayout()
        pl.setSpacing(5)
        prow = QtWidgets.QHBoxLayout()
        prow.addWidget(QtWidgets.QLabel("Preset:"))
        self.parts_preset_combo = QtWidgets.QComboBox()
        for name, preset in vehicle_parts.PRESETS.items():
            self.parts_preset_combo.addItem(name)
            self.parts_preset_combo.setItemData(
                self.parts_preset_combo.count() - 1, preset["about"],
                QtCore.Qt.ToolTipRole)
        prow.addWidget(self.parts_preset_combo, 1)
        self.btn_parts_preset = QtWidgets.QPushButton("Add Preset Guides")
        prow.addWidget(self.btn_parts_preset)
        pl.addLayout(prow)

        hgrid = QtWidgets.QGridLayout()
        hgrid.setSpacing(4)
        self.part_name_edit = QtWidgets.QLineEdit()
        self.part_name_edit.setPlaceholderText("name, e.g. crane")
        self.part_axis_combo = QtWidgets.QComboBox()
        self.part_axis_combo.addItems(["Y (turn)", "X (tilt up/down)",
                                       "Z (roll)"])
        self.part_parent_combo = QtWidgets.QComboBox()
        self.cb_part_limits = QtWidgets.QCheckBox("Limits")
        self.part_min_spin = QtWidgets.QDoubleSpinBox()
        self.part_max_spin = QtWidgets.QDoubleSpinBox()
        for sp, val in ((self.part_min_spin, -45.0),
                        (self.part_max_spin, 45.0)):
            sp.setRange(-360.0, 360.0)
            sp.setValue(val)
            sp.setSuffix(" deg")
        self.cb_part_aim = QtWidgets.QCheckBox("Can aim at target")
        hgrid.addWidget(QtWidgets.QLabel("Name:"), 0, 0)
        hgrid.addWidget(self.part_name_edit, 0, 1)
        hgrid.addWidget(QtWidgets.QLabel("Axis:"), 0, 2)
        hgrid.addWidget(self.part_axis_combo, 0, 3)
        hgrid.addWidget(QtWidgets.QLabel("On:"), 1, 0)
        hgrid.addWidget(self.part_parent_combo, 1, 1)
        hgrid.addWidget(self.cb_part_aim, 1, 2, 1, 2)
        hgrid.addWidget(self.cb_part_limits, 2, 0)
        hgrid.addWidget(self.part_min_spin, 2, 1)
        hgrid.addWidget(self.part_max_spin, 2, 2, 1, 2)
        pl.addLayout(hgrid)
        self.btn_part_add = QtWidgets.QPushButton("Add Hinge at Selection")
        self.btn_part_add.setToolTip(
            "Adds a hinge guide at the selected locator / joint / vertex (or\n"
            "above the chassis if nothing is selected). Move the guide onto\n"
            "the pivot, then Build Parts.")
        pl.addWidget(self.btn_part_add)
        self.parts_list = QtWidgets.QListWidget()
        self.parts_list.setFixedHeight(84)
        pl.addWidget(self.parts_list)
        brow = QtWidgets.QHBoxLayout()
        self.btn_parts_build = QtWidgets.QPushButton("Build Parts")
        self.btn_parts_build.setObjectName("primary")
        self.btn_parts_build.setToolTip(
            "Build every part from its guides onto the vehicle rig (replaces\n"
            "parts built before). Parts ride the body, so they follow the\n"
            "suspension and the physics simulation.")
        self.btn_part_remove = QtWidgets.QPushButton("Remove Part")
        self.btn_part_remove.setToolTip(
            "Delete the selected part's guides, and any part attached to it.\n"
            "Build Parts again to update the rig.")
        brow.addWidget(self.btn_parts_build, 1)
        brow.addWidget(self.btn_part_remove)
        pl.addLayout(brow)
        self.parts_box.setContentLayout(pl)
        vl.addWidget(self.parts_box,                  14, 0, 1, 2)

        # ---- Trailers: towed behind the vehicle, up to 3 in a chain ----
        self.trailers_box = CollapsibleBox("Trailers  (towed, up to 3)")
        tl = QtWidgets.QVBoxLayout()
        tl.setSpacing(5)
        trow = QtWidgets.QHBoxLayout()
        trow.addWidget(QtWidgets.QLabel("Axles:"))
        self.trailer_axles_spin = QtWidgets.QSpinBox()
        self.trailer_axles_spin.setRange(1, vehicle_trailers.MAX_AXLES)
        self.trailer_axles_spin.setValue(2)
        trow.addWidget(self.trailer_axles_spin)
        self.btn_trailer_add = QtWidgets.QPushButton("Add Trailer Guides")
        self.btn_trailer_add.setToolTip(
            "Adds guides for the next trailer, behind the vehicle (or the\n"
            "last trailer). Put the HITCH guide on the tow point and each\n"
            "AXLE guide on that axle's LEFT wheel centre, and set its\n"
            "Radius until the circle matches the tyre (or select the tyres\n"
            "and click Fit Wheels to Selected Tyres), then Build Trailers.")
        trow.addWidget(self.btn_trailer_add, 1)
        tl.addLayout(trow)
        self.trailers_list = QtWidgets.QListWidget()
        self.trailers_list.setFixedHeight(60)
        tl.addWidget(self.trailers_list)
        trow2 = QtWidgets.QHBoxLayout()
        self.btn_trailers_build = QtWidgets.QPushButton("Build Trailers")
        self.btn_trailers_build.setObjectName("primary")
        self.btn_trailers_build.setToolTip(
            "Build every trailer from its guides (replaces trailers built\n"
            "before). Trailer wheels spin, have suspension and follow the\n"
            "ground mesh. Build Vehicle Rig rebuilds them too.")
        self.btn_trailer_remove = QtWidgets.QPushButton("Remove Last")
        self.btn_trailer_remove.setToolTip(
            "Delete the last trailer's guides. Build Trailers to update.")
        trow2.addWidget(self.btn_trailers_build, 1)
        trow2.addWidget(self.btn_trailer_remove)
        tl.addLayout(trow2)
        self.btn_trailers_bake = QtWidgets.QPushButton(
            "Bake Trailers (timeline)")
        self.btn_trailers_bake.setToolTip(
            "Make the trailers follow the vehicle over the playback range.\n"
            "Use it after hand-keying the vehicle. Drive Mode and Simulate\n"
            "Physics do this for you.")
        tl.addWidget(self.btn_trailers_bake)
        self.trailers_box.setContentLayout(tl)
        vl.addWidget(self.trailers_box,               15, 0, 1, 2)

        # ---- Crash damage: solid obstacles + dents where the car hits ----
        self.crash_box = CollapsibleBox("Crash Damage  (walls, poles, dents)")
        cl = QtWidgets.QVBoxLayout()
        cl.setSpacing(5)
        crow = QtWidgets.QHBoxLayout()
        self.btn_crash_add = QtWidgets.QPushButton("Add Selected as Obstacles")
        self.btn_crash_add.setToolTip(
            "Walls, poles, barriers, parked cars: select their meshes and\n"
            "click. Drive Mode and Simulate Physics stop the car at them\n"
            "instead of driving through, and dent the car where it hits.")
        self.btn_crash_remove = QtWidgets.QPushButton("Remove Selected")
        self.btn_crash_remove.setToolTip(
            "Selected meshes stop being obstacles.")
        crow.addWidget(self.btn_crash_add, 1)
        crow.addWidget(self.btn_crash_remove)
        crow.addWidget(rig_info.make_info_button("vehicle_crash"))
        cl.addLayout(crow)
        self.cb_crash_soft = QtWidgets.QCheckBox(
            "They dent too (another car, a fence)")
        self.cb_crash_soft.setToolTip(
            "Tick before adding another car (or anything that should crumple)\n"
            "as an obstacle: it gets dented where it's hit, and the two\n"
            "crumple zones share the crush. Leave it off for walls and\n"
            "poles. Add the same mesh again to change it.")
        cl.addWidget(self.cb_crash_soft)
        self.crash_label = QtWidgets.QLabel()
        self.crash_label.setWordWrap(True)
        self.crash_label.setStyleSheet(ft.subtitle_css())
        cl.addWidget(self.crash_label)
        crow2 = QtWidgets.QHBoxLayout()
        self.btn_crash_bake = QtWidgets.QPushButton("Bake Crash Damage")
        self.btn_crash_bake.setObjectName("primary")
        self.btn_crash_bake.setToolTip(
            "Walk the animation (playback range plus every keyed frame of\n"
            "C_global_CTRL) and dent the body, hood, doors and trunk where\n"
            "they went into an obstacle. Drive Mode and Simulate Physics do\n"
            "this for you; use it after hand-keying a crash or changing the\n"
            "CRASH settings on C_chassis_CTRL.")
        self.btn_crash_clear = QtWidgets.QPushButton("Clear Damage")
        self.btn_crash_clear.setToolTip(
            "Delete every baked dent (the car is as modelled again).")
        crow2.addWidget(self.btn_crash_bake, 1)
        crow2.addWidget(self.btn_crash_clear)
        cl.addLayout(crow2)
        self.crash_box.setContentLayout(cl)
        vl.addWidget(self.crash_box,                  16, 0, 1, 2)

        # ---- Cargo: roof racks and loose parts that move with the ride ----
        self.cargo_box = CollapsibleBox("Cargo  (roof racks, loose parts)")
        gl = QtWidgets.QVBoxLayout()
        gl.setSpacing(5)
        grow = QtWidgets.QHBoxLayout()
        self.btn_cargo_add = QtWidgets.QPushButton("Add Selected as Cargo")
        self.btn_cargo_add.setToolTip(
            "Select the loose parts (spare wheel, jerry cans, lights on the\n"
            "rack), each its own mesh, and click. Each gets a joint and a\n"
            "control pivoting at its base, and its mesh is bound to it.\n"
            "Bind the vehicle body first (it rides whatever it was bound to).")
        self.btn_cargo_remove = QtWidgets.QPushButton("Remove Selected")
        self.btn_cargo_remove.setToolTip(
            "Selected cargo meshes (or their controls) go back to riding the\n"
            "body rigidly.")
        grow.addWidget(self.btn_cargo_add, 1)
        grow.addWidget(self.btn_cargo_remove)
        grow.addWidget(rig_info.make_info_button("vehicle_cargo"))
        gl.addLayout(grow)
        self.cargo_label = QtWidgets.QLabel()
        self.cargo_label.setWordWrap(True)
        self.cargo_label.setStyleSheet(ft.subtitle_css())
        gl.addWidget(self.cargo_label)
        grow2 = QtWidgets.QHBoxLayout()
        self.btn_cargo_bake = QtWidgets.QPushButton("Bake Cargo")
        self.btn_cargo_bake.setObjectName("primary")
        self.btn_cargo_bake.setToolTip(
            "Play the vehicle's motion (playback range plus every keyed frame)\n"
            "through each item: it leans back when you accelerate, forward\n"
            "when you brake, out in turns, hops over bumps and rattles with\n"
            "speed. Drive Mode and Simulate Physics do this for you; bake\n"
            "again after hand-keying or changing an item's settings.")
        self.btn_cargo_clear = QtWidgets.QPushButton("Clear Bake")
        self.btn_cargo_clear.setToolTip("Remove the baked cargo motion.")
        self.btn_cargo_select = QtWidgets.QPushButton("Select Controls")
        self.btn_cargo_select.setToolTip(
            "Select every cargo control, to tune stiffness, damping, maxLean,\n"
            "rattle and bounce in the channel box.")
        grow2.addWidget(self.btn_cargo_bake, 1)
        grow2.addWidget(self.btn_cargo_clear)
        grow2.addWidget(self.btn_cargo_select)
        gl.addLayout(grow2)
        self.cargo_box.setContentLayout(gl)
        vl.addWidget(self.cargo_box,                  17, 0, 1, 2)

        root.addWidget(self.vehicle_box)

        # ---------- MOTORCYCLE SECTION ----------
        # A bike is a vehicle with its wheels in a line: same chassis, same
        # odometer, same Drive Mode, plus a fork, a swingarm and a lean.
        self.moto_box = QtWidgets.QGroupBox("Motorcycle  (fork, swingarm, "
                                            "lean)")
        ml = QtWidgets.QGridLayout(self.moto_box)
        ml.setSpacing(6)

        self.btn_moto_create_guides = QtWidgets.QPushButton(
            "Create Bike Guides")
        self.btn_moto_create_guides.setToolTip(
            "Seven locators on the centre line: frame, front and back\n"
            "wheel (each with its own Radius), steering head, handlebar,\n"
            "swingarm pivot and shock top. The line from the steering head\n"
            "to the front axle is the steering axis, so where you put them\n"
            "sets the rake.")
        self.btn_moto_fit_guides = QtWidgets.QPushButton(
            "Fit Guides to Selected Model")
        self.btn_moto_fit_guides.setToolTip(
            "Select your bike's meshes and click: the guides size\n"
            "themselves to the model, then snap to its wheels if they can\n"
            "be found. Place the rest by hand and you are ready to build.")
        self.btn_moto_fit_wheels = QtWidgets.QPushButton(
            "Fit Wheels to Selected Tyres")
        self.btn_moto_fit_wheels.setToolTip(
            "Select the two tyre meshes: each wheel guide jumps to its\n"
            "centre and takes its size, and the rest of the guides move\n"
            "with them, keeping their place on the bike.")
        self.btn_moto_reset_guides = QtWidgets.QPushButton("Reset Guides")
        self.btn_moto_delete_guides = QtWidgets.QPushButton("Delete Guides")
        self.btn_moto_save_guides = QtWidgets.QPushButton("Save Guides")
        self.btn_moto_load_guides = QtWidgets.QPushButton("Load Guides")

        self.rb_moto_from_guides = QtWidgets.QRadioButton("From guides")
        self.rb_moto_from_defaults = QtWidgets.QRadioButton("From defaults")
        self.rb_moto_from_guides.setChecked(True)
        moto_src = QtWidgets.QButtonGroup(self)
        moto_src.addButton(self.rb_moto_from_guides)
        moto_src.addButton(self.rb_moto_from_defaults)
        self._moto_src_group = moto_src

        self.moto_spoke_spin = QtWidgets.QSpinBox()
        self.moto_spoke_spin.setRange(6, 32)
        self.moto_spoke_spin.setValue(16)
        self.moto_spoke_spin.setPrefix("spokes ")
        self.moto_spoke_spin.setToolTip(
            "Tyre spokes per wheel: more = smoother tread deformation, "
            "heavier scene.")
        self.cb_moto_ctrls = QtWidgets.QCheckBox("Spoke ctrls")
        self.cb_moto_ctrls.setChecked(True)
        self.cb_moto_ctrls.setToolTip(
            "A squash / stretch control per spoke, for hand-shaping the "
            "tyre.")

        self.btn_moto_build = QtWidgets.QPushButton("Build Motorcycle Rig")
        self.btn_moto_build.setObjectName("primary")
        self.btn_moto_build.setStyleSheet("QPushButton { padding: 7px; }")
        self.btn_moto_delete_rig = QtWidgets.QPushButton(
            "Delete Motorcycle Rig")

        moto_note = QtWidgets.QLabel(
            "Drive it with WASD in the Animate tab: it leans into every "
            "corner by itself.")
        moto_note.setWordWrap(True)
        moto_note.setStyleSheet("color: #9aa0a6;")

        ml.addWidget(self._with_info(self.btn_moto_create_guides,
                                     "motorcycle"), 0, 0, 1, 2)
        ml.addWidget(self.btn_moto_fit_guides,   1, 0, 1, 2)
        ml.addWidget(self.btn_moto_fit_wheels,   2, 0, 1, 2)
        ml.addWidget(self.btn_moto_reset_guides, 3, 0)
        ml.addWidget(self.btn_moto_delete_guides, 3, 1)
        ml.addWidget(self.btn_moto_save_guides,  4, 0)
        ml.addWidget(self.btn_moto_load_guides,  4, 1)
        srow = QtWidgets.QHBoxLayout()
        srow.addWidget(self.rb_moto_from_guides)
        srow.addWidget(self.rb_moto_from_defaults)
        srow.addStretch(1)
        ml.addLayout(srow,                       5, 0, 1, 2)
        orow = QtWidgets.QHBoxLayout()
        orow.addWidget(self.moto_spoke_spin)
        orow.addWidget(self.cb_moto_ctrls)
        orow.addStretch(1)
        ml.addLayout(orow,                       6, 0, 1, 2)
        ml.addWidget(self.btn_moto_build,        7, 0, 1, 2)
        ml.addWidget(self.btn_moto_delete_rig,   8, 0, 1, 2)
        self.btn_moto_tracks = QtWidgets.QPushButton("Bake Tyre Tracks")
        self.btn_moto_tracks.setToolTip(
            "Lay the marks the two tyres left over the timeline. They\n"
            "narrow as the bike leans, and stop where it left the ground.")
        self.btn_moto_burnout = QtWidgets.QPushButton("Burnout")
        self.btn_moto_burnout.setToolTip(
            "Spin the back wheel up from the current frame while the bike\n"
            "creeps forward, and lay the mark it leaves.")
        moto_track_row = QtWidgets.QHBoxLayout()
        moto_track_row.addWidget(self.btn_moto_tracks, 1)
        moto_track_row.addWidget(self.btn_moto_burnout, 1)
        ml.addLayout(moto_track_row,             9, 0, 1, 2)
        ml.addWidget(moto_note,                  10, 0, 1, 2)

        root.addWidget(self.moto_box)

        # ---------- PROP SECTION ----------
        # One prop at a time (a sword, a gun, a chair): 3 joints (root,
        # move, attach), 2 controls, everything named after the prop so any
        # number of props share a scene with a character or a vehicle.
        self.prop_box = QtWidgets.QGroupBox("Prop  (root, move, attach)")
        pl = QtWidgets.QVBoxLayout(self.prop_box)
        pl.setSpacing(6)
        nrow = QtWidgets.QHBoxLayout()
        nrow.addWidget(QtWidgets.QLabel("New prop name:"))
        self.prop_name_edit = QtWidgets.QLineEdit("prop")
        self.prop_name_edit.setToolTip(
            "Every node of this prop starts with its name (sword_root_CTRL,\n"
            "sword_move_BIND_JNT ...), so props never clash.")
        nrow.addWidget(self.prop_name_edit, 1)
        nrow.addWidget(rig_info.make_info_button("prop_rig"))
        pl.addLayout(nrow)
        self.btn_prop_guides = QtWidgets.QPushButton(
            "Create Prop Guides (around selected mesh)")
        self.btn_prop_guides.setToolTip(
            "Select the prop's mesh(es) first. Three guides appear:\n"
            "  root (yellow, at the base): where the prop sits\n"
            "  move (blue, in the middle): its pivot for animation\n"
            "  attach (green): the grip / socket; rotate it to aim it\n"
            "Moving the root guide brings the other two.")
        pl.addWidget(self.btn_prop_guides)
        prow = QtWidgets.QHBoxLayout()
        prow.addWidget(QtWidgets.QLabel("Props:"))
        self.prop_combo = QtWidgets.QComboBox()
        self.prop_combo.setToolTip("The props in this scene. The buttons "
                                   "below work on this one.")
        prow.addWidget(self.prop_combo, 1)
        pl.addLayout(prow)
        brow = QtWidgets.QHBoxLayout()
        self.btn_prop_build = QtWidgets.QPushButton("Build / Rebuild Prop")
        self.btn_prop_build.setObjectName("primary")
        self.btn_prop_build.setToolTip(
            "Build the prop from its guides and bind its mesh to the move\n"
            "joint. Rebuilding keeps the binding and the attachment.")
        self.btn_prop_bind = QtWidgets.QPushButton("Bind Selected Mesh")
        self.btn_prop_bind.setToolTip(
            "Skin the selected mesh(es) to this prop's move joint.")
        brow.addWidget(self.btn_prop_build, 1)
        brow.addWidget(self.btn_prop_bind)
        pl.addLayout(brow)
        arow = QtWidgets.QHBoxLayout()
        self.btn_prop_attach = QtWidgets.QPushButton("Attach to Selected")
        self.btn_prop_attach.setToolTip(
            "Select a hand joint or control (a character's, even a\n"
            "referenced one) and click: the prop's attach point snaps into\n"
            "it and follows. move_CTRL.space picks Root or Attached.")
        self.btn_prop_detach = QtWidgets.QPushButton("Detach")
        self.btn_prop_detach.setToolTip("Stop following: the prop goes back "
                                        "on its root.")
        arow.addWidget(self.btn_prop_attach, 1)
        arow.addWidget(self.btn_prop_detach)
        pl.addLayout(arow)
        srow = QtWidgets.QHBoxLayout()
        self.btn_prop_pickup = QtWidgets.QPushButton("Pick Up (key)")
        self.btn_prop_pickup.setToolTip(
            "Switch to Attached on this frame without the prop jumping,\n"
            "and key it. Use it where the hand grabs the prop.")
        self.btn_prop_putdown = QtWidgets.QPushButton("Put Down (key)")
        self.btn_prop_putdown.setToolTip(
            "Switch to Root on this frame without a jump, and key it.")
        srow.addWidget(self.btn_prop_pickup)
        srow.addWidget(self.btn_prop_putdown)
        pl.addLayout(srow)
        drow = QtWidgets.QHBoxLayout()
        self.btn_prop_export = QtWidgets.QPushButton("Export Prop FBX...")
        self.btn_prop_export.setToolTip(
            "The prop's 3 joints + its mesh for Unreal / Unity (a\n"
            "character's export leaves props out). Tick Animation to bake\n"
            "the playback range instead.")
        self.cb_prop_anim = QtWidgets.QCheckBox("Animation")
        self.btn_prop_delete = QtWidgets.QPushButton("Delete Rig")
        self.btn_prop_delete.setToolTip(
            "Delete this prop's rig (the mesh is unbound, the guides stay).")
        self.btn_prop_delete_guides = QtWidgets.QPushButton("Delete Guides")
        drow.addWidget(self.btn_prop_export, 1)
        drow.addWidget(self.cb_prop_anim)
        pl.addLayout(drow)
        xrow = QtWidgets.QHBoxLayout()
        xrow.addWidget(self.btn_prop_delete)
        xrow.addWidget(self.btn_prop_delete_guides)
        pl.addLayout(xrow)
        root.addWidget(self.prop_box)

        # ---------- GAME EXPORT SECTION (always visible) ----------
        # FBX export for Unreal / Unity. Works across all rig types
        # because they all share C_root_BIND_JNT as the top of the
        # BIND skeleton. Stays visible regardless of which Rig Type
        # the dropdown is on — animators bake + export from any mode.
        self.export_box = CollapsibleBox("Game Export (Unreal / Unity)")
        el = QtWidgets.QGridLayout()
        el.setSpacing(6)

        # Exports read the rig and never change it: a temporary clean
        # skeleton (same bone names) is baked, written and deleted.
        self.cb_export_bendy = QtWidgets.QCheckBox(
            "Include ribbon bendy joints")
        self.cb_export_bendy.setChecked(False)
        self.cb_export_bendy.setToolTip(
            "Add the ribbon bendy joints (spine_NN, upper_bendy_NN...).\n"
            "Off by default: most real-time rigs want the cleaner skeleton.\n"
            "Joints your skin actually uses are always exported.")
        self.cb_export_mesh = QtWidgets.QCheckBox("Include skinned mesh")
        self.cb_export_mesh.setChecked(True)
        self.cb_export_mesh.setToolTip(
            "Export Rig: include every mesh skinned to the skeleton\n"
            "(exported as a copy with the same weights).")
        self.cb_export_root = QtWidgets.QCheckBox("Root bone at the ground")
        self.cb_export_root.setChecked(True)
        self.cb_export_root.setToolTip(
            "Adds a 'root' bone at the ground above the skeleton that\n"
            "carries the character's travel (root motion in Unreal / Unity).\n"
            "Use the SAME setting for the rig and all its animations.")
        self.cb_export_inplace = QtWidgets.QCheckBox("In place (no travel)")
        self.cb_export_inplace.setChecked(False)
        self.cb_export_inplace.setToolTip(
            "Animations stay at the origin: the walk or run cycles on the\n"
            "spot and the engine moves the character.")

        self.btn_export_rig = QtWidgets.QPushButton("Export Rig (.fbx)…")
        self.btn_export_rig.setStyleSheet(
            "QPushButton { padding: 6px; font-weight: 600; }")
        self.btn_export_rig.setToolTip(
            "The skeleton in its rest pose + the skinned mesh, for importing\n"
            "the character into Unreal / Unity. Your rig is not changed.")

        range_row = QtWidgets.QHBoxLayout()
        range_row.addWidget(QtWidgets.QLabel("Frames:"))
        self.export_start_spin = QtWidgets.QSpinBox()
        self.export_end_spin = QtWidgets.QSpinBox()
        for sp in (self.export_start_spin, self.export_end_spin):
            sp.setRange(-100000, 100000)
        range_row.addWidget(self.export_start_spin)
        range_row.addWidget(QtWidgets.QLabel("to"))
        range_row.addWidget(self.export_end_spin)
        self.btn_export_timeline = QtWidgets.QPushButton("Use Timeline")
        self.btn_export_timeline.setToolTip(
            "Fill the frames from the timeline's playback range.")
        range_row.addWidget(self.btn_export_timeline)
        clip_row = QtWidgets.QHBoxLayout()
        clip_row.addWidget(QtWidgets.QLabel("Clip:"))
        self.export_clip_edit = QtWidgets.QLineEdit()
        self.export_clip_edit.setPlaceholderText("name, e.g. walk_fwd")
        clip_row.addWidget(self.export_clip_edit, 1)

        self.btn_export_anim = QtWidgets.QPushButton(
            "Export Animation (.fbx)…")
        self.btn_export_anim.setStyleSheet(
            "QPushButton { padding: 6px; font-weight: 600; }")
        self.btn_export_anim.setToolTip(
            "Bake these frames onto a clean skeleton and save them as an\n"
            "FBX animation, one move per file (name it after the move).\n"
            "Your rig is not changed.")
        self.btn_clip_add = QtWidgets.QPushButton("Save as Clip")
        self.btn_clip_add.setToolTip(
            "Remember this name + frame range with the scene, so a whole\n"
            "set of moves (walk, run, jump...) exports in one click.")
        self.btn_clip_remove = QtWidgets.QPushButton("Remove Clip")
        self.clips_list = QtWidgets.QListWidget()
        self.clips_list.setFixedHeight(70)
        self.btn_export_clips = QtWidgets.QPushButton(
            "Export All Clips to Folder…")
        self.btn_export_clips.setToolTip(
            "One FBX per clip, named after the clip.")

        self.btn_game_skeleton = QtWidgets.QPushButton(
            "Make Game Skeleton (skinning workflow)")
        self.btn_game_skeleton.setToolTip(
            "Re-parents the rig's loose BIND chains under C_root_BIND_JNT\n"
            "and folds the face joints under the head, so Auto-Skin binds\n"
            "one clean hierarchy. Not needed for exporting.")

        el.addWidget(self.cb_export_bendy,   0, 0)
        el.addWidget(self.cb_export_mesh,    0, 1)
        el.addWidget(self.cb_export_root,    1, 0)
        el.addWidget(self.cb_export_inplace, 1, 1)
        el.addWidget(self._with_info(self.btn_export_rig, "export_rig"),
                     2, 0, 1, 2)
        el.addLayout(range_row,              3, 0, 1, 2)
        el.addLayout(clip_row,               4, 0, 1, 2)
        el.addWidget(self._with_info(self.btn_export_anim, "export_anim"),
                     5, 0, 1, 2)
        el.addWidget(self.btn_clip_add,      6, 0)
        el.addWidget(self.btn_clip_remove,   6, 1)
        el.addWidget(self.clips_list,        7, 0, 1, 2)
        el.addWidget(self._with_info(self.btn_export_clips, "export_clips"),
                     8, 0, 1, 2)
        el.addWidget(self._with_info(self.btn_game_skeleton,
                                     "game_skeleton"), 9, 0, 1, 2)

        self.export_box.setContentLayout(el)
        root.addWidget(self.export_box)

        # ---------- SKINNING (always visible) ----------
        # Bridge from skeleton to skinned character: bind, mirror, copy,
        # and save/load weights so a rebuild doesn't mean re-painting.
        self.skin_box = CollapsibleBox("Skinning")
        sl = QtWidgets.QGridLayout()
        sl.setSpacing(6)

        self.btn_skin_bind = QtWidgets.QPushButton(
            "Bind Selected Mesh to Skeleton")
        self.btn_skin_bind.setToolTip(
            "Smooth-bind the selected mesh to every _BIND_JNT joint\n"
            "(replacing any existing skin). 4 influences, joints labelled\n"
            "L/R so mirroring works.")
        self.btn_skin_mirror_lr = QtWidgets.QPushButton("Mirror Wts  L → R")
        self.btn_skin_mirror_lr.setToolTip(
            "Mirror the selected mesh's skin weights from the +X (left) to\n"
            "the -X (right) side.")
        self.btn_skin_mirror_rl = QtWidgets.QPushButton("Mirror Wts  R → L")
        self.btn_skin_mirror_rl.setToolTip(
            "Mirror weights from the right side onto the left.")
        self.btn_skin_copy = QtWidgets.QPushButton(
            "Copy Weights (1st → 2nd selected)")
        self.btn_skin_copy.setToolTip(
            "Select the SOURCE skinned mesh first, then the TARGET mesh,\n"
            "then click — copies weights by closest point (binds the target\n"
            "to the same joints if needed).")
        self.btn_skin_save = QtWidgets.QPushButton("Save Weights…")
        self.btn_skin_save.setToolTip(
            "Export the selected mesh's skin weights to disk. Pair with\n"
            "Load to survive a rig rebuild without re-painting.")
        self.btn_skin_load = QtWidgets.QPushButton("Load Weights…")
        self.btn_skin_load.setToolTip(
            "Import skin weights onto the selected (re-bound) mesh.")

        self.btn_skin_smooth = QtWidgets.QPushButton("Smooth Weights")
        self.btn_skin_smooth.setToolTip(
            "Relax the selected mesh's weights over its own surface: each\n"
            "vertex blends toward its neighbours, which softens the hard\n"
            "lines where one joint's area meets the next. Select some\n"
            "VERTICES first to smooth only them (a shoulder, an armpit).\n"
            "Run it two or three times, or raise the passes.")
        self.skin_smooth_spin = QtWidgets.QSpinBox()
        self.skin_smooth_spin.setRange(1, 20)
        self.skin_smooth_spin.setValue(3)
        self.skin_smooth_spin.setPrefix("passes ")
        self.skin_smooth_spin.setToolTip(
            "How many smoothing passes each click runs.")
        self.btn_skin_tidy = QtWidgets.QPushButton("Tidy Weights (game-ready)")
        self.btn_skin_tidy.setToolTip(
            "Drop weight specks, keep the 4 strongest joints per vertex and\n"
            "normalise: what a game engine expects, and what stops stray\n"
            "joints dragging a vertex a long way.")
        smooth_row = QtWidgets.QHBoxLayout()
        smooth_row.addWidget(self.btn_skin_smooth, 1)
        smooth_row.addWidget(self.skin_smooth_spin)

        self.btn_skin_grad_auto = QtWidgets.QPushButton(
            "Gradient Skin: All Bones  (one-click falloff)")
        self.btn_skin_grad_auto.setStyleSheet(
            "QPushButton { color: %s; }" % ft.OK)
        self.btn_skin_grad_auto.setToolTip(
            "One click: skin the selected mesh to the WHOLE skeleton with a\n"
            "smooth BONE FALLOFF — each bone's MIDDLE is full weight to its\n"
            "joint, ramping to a 50/50 blend at the joints (0,25,50,100,50,\n"
            "25,0 along the bone). A clean tubular base to smooth on top of.\n"
            "Select just some VERTICES first to limit it to them.")
        self.btn_skin_grad_chain = QtWidgets.QPushButton(
            "Gradient Skin: Selected Chain")
        self.btn_skin_grad_chain.setToolTip(
            "Select the MESH (or its verts) + a CHAIN of joints (2+, any\n"
            "pick order), then click — lays the same bone-falloff along just\n"
            "that chain (e.g. shoulder → elbow → wrist).")
        exp_lbl = QtWidgets.QLabel(
            "Automatic weights  —  bone falloff, kept to each part's own "
            "surface (smooth them after)")
        exp_lbl.setStyleSheet(
            "QLabel { color: %s; font-weight: 600; padding-top: 6px; "
            "border-top: 1px solid %s; }" % (ft.WARN, ft.BORDER))

        sl.addWidget(self.btn_skin_bind,       0, 0, 1, 2)
        sl.addWidget(self.btn_skin_mirror_lr,  1, 0)
        sl.addWidget(self.btn_skin_mirror_rl,  1, 1)
        sl.addWidget(self.btn_skin_copy,       2, 0, 1, 2)
        sl.addLayout(smooth_row,               3, 0, 1, 2)
        sl.addWidget(self.btn_skin_tidy,       4, 0, 1, 2)
        sl.addWidget(exp_lbl,                  5, 0, 1, 2)
        sl.addWidget(self.btn_skin_grad_auto,  6, 0, 1, 2)
        sl.addWidget(self.btn_skin_grad_chain, 7, 0, 1, 2)
        sl.addWidget(self.btn_skin_save,       8, 0)
        sl.addWidget(self.btn_skin_load,       8, 1)
        self.skin_box.setContentLayout(sl)
        root.addWidget(self.skin_box)

        # ---------- ANIMATION TOOLS (always visible) ----------
        # Production rig features that layer onto a built biped: parent
        # space switching for the IK / pole / head controls, etc.
        self.anim_box = CollapsibleBox("Animation Tools (biped)")
        al = QtWidgets.QGridLayout()
        al.setSpacing(6)

        self.btn_add_spaces = QtWidgets.QPushButton(
            "Add Space Switches")
        self.btn_add_spaces.setToolTip(
            "Add parent-space switching to a built biped's IK hands / feet,\n"
            "pole vectors and head. Each gets a 'space' enum in the channel\n"
            "box:\n"
            "  hands  World / COG / Chest      feet  World / COG / Hips\n"
            "  poles  World / Hand|Foot / COG  head  Neck / Chest / COG / World\n"
            "Run once after Build. Idempotent.")

        # Seamless (no-jump) switch for the SELECTED control(s).
        self.space_combo = QtWidgets.QComboBox()
        self.space_combo.addItems(
            ["World", "COG", "Chest", "Hips", "Hand", "Foot", "Neck"])
        self.btn_switch_space = QtWidgets.QPushButton(
            "Switch Selected (no jump)")
        self.btn_switch_space.setToolTip(
            "Seamlessly switch the SELECTED control(s) to the chosen space\n"
            "on the current frame — the control keeps its world pose (no\n"
            "pop) and is keyed. Controls that don't have that space are\n"
            "skipped, so you can select hands + feet and send all to World.")
        space_row = QtWidgets.QHBoxLayout()
        space_row.addWidget(self.space_combo)
        space_row.addWidget(self.btn_switch_space, 1)

        al.addWidget(self.btn_add_spaces, 0, 0, 1, 2)
        al.addLayout(space_row,           1, 0, 1, 2)

        # ---- Pose library ----
        pose_hdr = QtWidgets.QLabel("Pose Library")
        pose_hdr.setStyleSheet("QLabel { color: %s; font-weight: 600; "
                               "padding-top: 4px; }" % ft.MUTED)
        self.pose_name = QtWidgets.QLineEdit()
        self.pose_name.setPlaceholderText("pose name…")
        self.btn_pose_save = QtWidgets.QPushButton("Save Pose")
        self.btn_pose_save.setToolTip(
            "Save the current pose of every rig control to disk\n"
            "(~/danyal_rig_poses). Survives scene save + rebuild.")
        save_row = QtWidgets.QHBoxLayout()
        save_row.addWidget(self.pose_name, 1)
        save_row.addWidget(self.btn_pose_save)

        self.pose_combo = QtWidgets.QComboBox()
        self.pose_combo.setToolTip("Saved poses. Pick one, then Apply.")
        self.btn_pose_refresh = QtWidgets.QPushButton("⟳")
        self.btn_pose_refresh.setFixedWidth(28)
        self.btn_pose_refresh.setToolTip("Refresh the saved-pose list.")
        combo_row = QtWidgets.QHBoxLayout()
        combo_row.addWidget(self.pose_combo, 1)
        combo_row.addWidget(self.btn_pose_refresh)

        self.btn_pose_apply = QtWidgets.QPushButton("Apply")
        self.btn_pose_apply.setToolTip("Apply the selected pose to the rig.")
        self.btn_pose_apply_mirror = QtWidgets.QPushButton("Apply Mirrored")
        self.btn_pose_apply_mirror.setToolTip(
            "Apply the selected pose flipped left<->right.")
        self.btn_pose_apply_sel = QtWidgets.QPushButton("Apply to Selected")
        self.btn_pose_apply_sel.setToolTip(
            "Apply the pose to only the currently SELECTED controls.")
        self.btn_pose_delete = QtWidgets.QPushButton("Delete")
        self.btn_pose_delete.setToolTip("Delete the selected saved pose.")
        self.btn_pose_flip = QtWidgets.QPushButton("Flip Current Pose L↔R")
        self.btn_pose_flip.setToolTip(
            "Swap the LEFT and RIGHT halves of the rig's CURRENT pose\n"
            "(a true world mirror). Great for symmetrising or flipping a\n"
            "hand-made pose.")

        al.addWidget(pose_hdr,                  2, 0, 1, 2)
        al.addLayout(save_row,                  3, 0, 1, 2)
        al.addLayout(combo_row,                 4, 0, 1, 2)
        al.addWidget(self.btn_pose_apply,       5, 0)
        al.addWidget(self.btn_pose_apply_mirror, 5, 1)
        al.addWidget(self.btn_pose_apply_sel,   6, 0)
        al.addWidget(self.btn_pose_delete,      6, 1)
        al.addWidget(self.btn_pose_flip,        7, 0, 1, 2)
        self.anim_box.setContentLayout(al)
        root.addWidget(self.anim_box)
        self._refresh_poses()

        # ---------- RIG FINISHING (pre-export, always visible) ----------
        # The last-mile passes you run once the rig deforms: volume
        # correctives, secondary-motion dynamics, and a ship-readiness
        # health check before you hand it off.
        self.finish_box = CollapsibleBox("Rig Finishing (pre-export)")
        fl = QtWidgets.QGridLayout()
        fl.setSpacing(6)

        self.btn_add_correctives = QtWidgets.QPushButton(
            "Add Volume Correctives")
        self.btn_add_correctives.setToolTip(
            "Add pose-reader-driven corrective joints to a built biped's\n"
            "shoulders, elbows, hips and knees. A reader fires each one as\n"
            "the limb flexes, so you can paint volume back into the crease.\n"
            "They're named *_corrective_BIND_JNT — bind them like any joint.")

        self.btn_make_dynamic = QtWidgets.QPushButton(
            "Make Selected Chain Dynamic")
        self.btn_make_dynamic.setToolTip(
            "Select a joint chain (its root, or all its joints) — tail,\n"
            "ponytail, ear, rope, antenna — and drape an nHair dynamic over\n"
            "it for jiggle / follow-through. One *_dynamics_CTRL holds the\n"
            "follow / stiffness / damping / gravity dials. Play to settle,\n"
            "then bake the joints before export.")

        self.btn_validate = QtWidgets.QPushButton("Validate Rig")
        self.btn_validate.setToolTip(
            "Ship-readiness health check: controls off their rest pose,\n"
            "unbound or double-bound meshes, leftover history, duplicate\n"
            "names, scaled joints, junk nodes, dependency cycles. Prints a\n"
            "scannable report to the Script Editor.")
        self.btn_validate_fix = QtWidgets.QPushButton("Validate + Auto-Clean")
        self.btn_validate_fix.setToolTip(
            "Run the health check, then auto-delete the SAFE junk (unknown\n"
            "nodes, unused utility nodes, empty display layers). Never\n"
            "touches your geometry or weights.")

        fl.addWidget(self.btn_add_correctives, 0, 0, 1, 2)
        fl.addWidget(self.btn_make_dynamic,    1, 0, 1, 2)
        fl.addWidget(self.btn_validate,        2, 0)
        fl.addWidget(self.btn_validate_fix,    2, 1)
        self.finish_box.setContentLayout(fl)
        root.addWidget(self.finish_box)

        # ---------- TAIL PHYSICS (simulate & bake) ----------
        # Follow-through on tails, simulated over the animation and keyed
        # on a physics layer (the animation itself is never changed).
        self.tail_box = CollapsibleBox("Tail Physics  (simulate & bake)")
        tl = QtWidgets.QGridLayout()
        tl.setSpacing(6)
        tail_row = QtWidgets.QHBoxLayout()
        tail_row.addWidget(QtWidgets.QLabel("Tail"))
        self.tail_combo = QtWidgets.QComboBox()
        self.tail_combo.setToolTip(
            "The tails in the scene (biped, quadruped, raptor, extra tails\n"
            "and custom chains).")
        tail_row.addWidget(self.tail_combo, 1)
        self.btn_tail_refresh = QtWidgets.QPushButton("Refresh")
        tail_row.addWidget(self.btn_tail_refresh)

        self.tail_spins = {}
        dials = (("Stiffness", "physStiffness", 0.2, 20.0, 0.1,
                  "How fast the tail springs back (wobbles per second).\n"
                  "Higher = stiffer, like a raptor. Lower = floppy."),
                 ("Damping", "physDamping", 0.0, 1.0, 0.05,
                  "How quickly the wobble dies away."),
                 ("Swing", "physSwing", 0.0, 5.0, 0.05,
                  "How far the tail swings when the body speeds up,\n"
                  "slows down or turns."),
                 ("Whip", "physWhip", 0.0, 1.0, 0.05,
                  "How much looser the tip is than the base."))
        dial_grid = QtWidgets.QGridLayout()
        dial_grid.setSpacing(4)
        for i, (label, attr, lo, hi, step, tip) in enumerate(dials):
            sp = QtWidgets.QDoubleSpinBox()
            sp.setRange(lo, hi)
            sp.setSingleStep(step)
            sp.setDecimals(2)
            sp.setValue(chain_sim.DEFAULTS[attr])
            sp.setToolTip(tip)
            lbl = QtWidgets.QLabel(label)
            lbl.setToolTip(tip)
            dial_grid.addWidget(lbl, i // 2, (i % 2) * 2)
            dial_grid.addWidget(sp, i // 2, (i % 2) * 2 + 1)
            self.tail_spins[attr] = sp

        tail_range = QtWidgets.QHBoxLayout()
        tail_range.addWidget(QtWidgets.QLabel("Frames:"))
        self.tail_start_spin = QtWidgets.QSpinBox()
        self.tail_end_spin = QtWidgets.QSpinBox()
        for sp in (self.tail_start_spin, self.tail_end_spin):
            sp.setRange(-100000, 100000)
        tail_range.addWidget(self.tail_start_spin)
        tail_range.addWidget(QtWidgets.QLabel("to"))
        tail_range.addWidget(self.tail_end_spin)
        self.btn_tail_timeline = QtWidgets.QPushButton("Use Timeline")
        tail_range.addWidget(self.btn_tail_timeline)

        self.btn_tail_bake = QtWidgets.QPushButton("Simulate && Bake Tail")
        self.btn_tail_bake.setObjectName("primary")
        self.btn_tail_bake.setToolTip(
            "Simulate the tail over these frames and key the swing on its\n"
            "physics layer. Run it again after changing the animation or\n"
            "the dials. Blend it with 'physics' on the tail's SETTINGS\n"
            "control (0 = your animation only).")
        self.btn_tail_clear = QtWidgets.QPushButton("Clear Tail Physics")
        self.btn_tail_clear.setToolTip(
            "Delete the baked swing. The tail plays your animation again.")
        self.btn_tail_remove = QtWidgets.QPushButton("Remove Layer")
        self.btn_tail_remove.setToolTip(
            "Take the physics layer off the rig completely.")

        tl.addLayout(tail_row, 0, 0, 1, 2)
        tl.addLayout(dial_grid, 1, 0, 1, 2)
        tl.addLayout(tail_range, 2, 0, 1, 2)
        tl.addWidget(self._with_info(self.btn_tail_bake, "tail_physics"),
                     3, 0, 1, 2)
        tl.addWidget(self.btn_tail_clear, 4, 0)
        tl.addWidget(self.btn_tail_remove, 4, 1)
        self.tail_box.setContentLayout(tl)
        root.addWidget(self.tail_box)

        # ---------- STATUS ----------
        self.status = QtWidgets.QLabel("")
        self.status.setStyleSheet(
            "QLabel { color: %s; padding: 4px; "
            "border-top: 1px solid %s; }" % (ft.MUTED, ft.BORDER)
        )
        root.addWidget(self.status)
        root.addStretch(1)

        # ---------- Wrap everything in a scroll area ----------
        scroll = QtWidgets.QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self._content)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)

        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(scroll)

        # Sensible default window size; the scroll area handles overflow
        # so the panel never grows past the screen.
        self.resize(380, 720)
        # Cap the height to the available screen so the window can't grow
        # off-screen. Guarded — screen queries can be flaky in some
        # environments, and the scroll area already handles overflow.
        try:
            screen = QtWidgets.QApplication.primaryScreen()
            if screen is not None:
                avail_h = screen.availableGeometry().height()
                self.setMaximumHeight(max(400, avail_h - 60))
        except Exception:
            pass

        # Apply the initial section visibility.
        self._apply_mode()

    # -----------------------------------------------------------------------

    # -----------------------------------------------------------------------
    # UI helpers
    # -----------------------------------------------------------------------

    @staticmethod
    def _with_info(main_btn, info_id):
        """Wrap a main button + a small "ⓘ" info popup button into one
        compact row widget that can be dropped into a grid cell.

        The main button stretches; the info button stays its natural
        18px width on the right.
        """
        holder = QtWidgets.QWidget()
        hb = QtWidgets.QHBoxLayout(holder)
        hb.setContentsMargins(0, 0, 0, 0)
        hb.setSpacing(3)
        hb.addWidget(main_btn, 1)
        hb.addWidget(rig_info.make_info_button(info_id))
        return holder

    def _apply_mode(self):
        """Show the sections for the selected rig type, hide the others'.

        Biped     → Guides + Build Rig boxes visible.
        Quadruped → Quadruped box visible.
        Bird      → Bird box visible.
        Vehicle   → Vehicle box visible.
        Motorcycle → Motorcycle box visible.
        Prop      → Prop box visible.
        """
        mode = self.mode_combo.currentText()
        is_biped   = (mode == "Biped")
        is_quad    = (mode == "Quadruped")
        is_bird    = (mode == "Bird")
        is_vehicle = (mode == "Vehicle")
        self.guides_box.setVisible(is_biped)
        self.build_box.setVisible(is_biped)
        self.quad_box.setVisible(is_quad)
        if is_quad:
            self._sync_quad_choice()
        self.bird_box.setVisible(is_bird)
        self.vehicle_box.setVisible(is_vehicle)
        self.moto_box.setVisible(mode == "Motorcycle")
        self.prop_box.setVisible(mode == "Prop")
        if mode == "Prop":
            self._refresh_props()
        # NOTE: no adjustSize() here — the scroll area handles overflow,
        # so the window keeps its size and just scrolls. Calling
        # adjustSize would fight the scroll area and re-grow the panel.

    def _on_mode_changed(self, *args):
        self._apply_mode()

    def keyPressEvent(self, event):
        """Forward Ctrl+Z / Ctrl+Shift+Z to Maya's undo / redo.

        A Qt tool window swallows these shortcuts while it has focus, so
        without this an animator who nudges a guide and presses Ctrl+Z
        (with the panel focused) would see nothing happen. Routing the
        keys straight to cmds.undo()/redo() makes undo behave normally
        regardless of which window has focus.
        """
        if (event.key() == QtCore.Qt.Key_Z
                and (event.modifiers() & QtCore.Qt.ControlModifier)):
            try:
                if event.modifiers() & QtCore.Qt.ShiftModifier:
                    cmds.redo()
                else:
                    cmds.undo()
            except Exception:
                pass
            event.accept()
            return
        super(RigBuilderUI, self).keyPressEvent(event)

    # -----------------------------------------------------------------------

    def _connect(self):
        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        self.btn_moto_create_guides.clicked.connect(
            self._on_moto_create_guides)
        self.btn_moto_fit_guides.clicked.connect(self._on_moto_fit_guides)
        self.btn_moto_fit_wheels.clicked.connect(self._on_moto_fit_wheels)
        self.btn_moto_reset_guides.clicked.connect(
            self._on_moto_reset_guides)
        self.btn_moto_delete_guides.clicked.connect(
            self._on_moto_delete_guides)
        self.btn_moto_save_guides.clicked.connect(self._on_moto_save_guides)
        self.btn_moto_load_guides.clicked.connect(self._on_moto_load_guides)
        self.btn_moto_build.clicked.connect(self._on_moto_build)
        self.btn_moto_delete_rig.clicked.connect(self._on_moto_delete_rig)
        self.btn_open_picker.clicked.connect(self._on_open_picker)
        self.btn_walk_mode.clicked.connect(self._on_walk_mode)
        self.btn_fly_mode.clicked.connect(self._on_fly_mode)
        self.btn_tail_refresh.clicked.connect(self._refresh_tails)
        self.tail_combo.currentIndexChanged.connect(self._on_tail_picked)
        self.btn_tail_timeline.clicked.connect(self._on_tail_timeline)
        self.btn_tail_bake.clicked.connect(self._on_tail_bake)
        self.btn_tail_clear.clicked.connect(self._on_tail_clear)
        self.btn_tail_remove.clicked.connect(self._on_tail_remove)
        self._refresh_tails()
        self._on_tail_timeline()
        self.btn_create_guides.clicked.connect(self._on_create_guides)
        self.btn_mirror.clicked.connect(self._on_mirror)
        self.btn_reset.clicked.connect(self._on_reset)
        self.neck_segments_spin.valueChanged.connect(self._on_neck_segments)
        self.cb_neck_ik.toggled.connect(self._on_neck_ik)
        self._sync_neck_row()
        self.btn_fit_guides.clicked.connect(self._on_fit_guides)
        self.btn_quad_fit.clicked.connect(self._on_quad_fit_guides)
        self.btn_bird_fit.clicked.connect(self._on_bird_fit_guides)
        self.btn_vehicle_fit.clicked.connect(self._on_vehicle_fit_guides)
        self.btn_save.clicked.connect(self._on_save)
        self.btn_load.clicked.connect(self._on_load)
        self.btn_delete_guides.clicked.connect(self._on_delete_guides)
        self.btn_toggle_labels.clicked.connect(self._on_toggle_labels)
        self.cb_symmetric.toggled.connect(self._on_symmetric_toggled)
        self.guide_mode_combo.blockSignals(True)
        self.guide_mode_combo.setCurrentIndex(
            1 if self.guide_system.is_ez_mode() else 0)
        self.guide_mode_combo.blockSignals(False)
        self.guide_mode_combo.currentIndexChanged.connect(
            self._on_guide_mode_changed)
        self.btn_build.clicked.connect(self._on_build)
        self.btn_delete_rig.clicked.connect(self._on_delete_rig)
        self.btn_creature_preset.clicked.connect(self._on_creature_preset)
        self.btn_creature_add.clicked.connect(self._on_creature_add)
        self.btn_creature_select.clicked.connect(self._on_creature_select)
        self.btn_creature_remove.clicked.connect(self._on_creature_remove)
        self.creature_type_combo.currentIndexChanged.connect(
            self._on_creature_type_changed)
        self.creature_parent_combo.currentIndexChanged.connect(
            self._on_creature_parent_changed)
        self.btn_creature_pick.clicked.connect(self._on_creature_pick)
        self.btn_creature_from_joints.clicked.connect(
            self._on_creature_from_joints)
        self._on_creature_type_changed()
        self._refresh_creature_list()
        self.btn_verify.clicked.connect(self._on_verify)
        self.btn_select_skin_jnts.clicked.connect(self._on_select_skin_joints)
        self.btn_smooth_bind.clicked.connect(self._on_smooth_bind)
        self.btn_select_geo.clicked.connect(self._on_select_geo)
        self.btn_wire_mesh_vis.clicked.connect(self._on_wire_mesh_vis)
        self.btn_ng_init.clicked.connect(self._on_ng_init)
        self.btn_delta_mush.clicked.connect(self._on_delta_mush)
        self.btn_auto_skin_all.clicked.connect(self._on_auto_skin_all)
        self.btn_advanced_face.clicked.connect(self._on_advanced_face)
        self.btn_face_capture.clicked.connect(self._on_face_capture)
        # Skinning
        self.btn_skin_bind.clicked.connect(self._on_skin_bind)
        self.btn_skin_mirror_lr.clicked.connect(
            lambda: self._on_skin_mirror(True))
        self.btn_skin_mirror_rl.clicked.connect(
            lambda: self._on_skin_mirror(False))
        self.btn_skin_copy.clicked.connect(self._on_skin_copy)
        self.btn_skin_smooth.clicked.connect(self._on_skin_smooth)
        self.btn_skin_tidy.clicked.connect(self._on_skin_tidy)
        self.btn_skin_grad_auto.clicked.connect(self._on_skin_gradient_auto)
        self.btn_skin_grad_chain.clicked.connect(self._on_skin_gradient_chain)
        self.btn_skin_save.clicked.connect(self._on_skin_save)
        self.btn_skin_load.clicked.connect(self._on_skin_load)
        # Animation tools
        self.btn_add_spaces.clicked.connect(self._on_add_spaces)
        self.btn_switch_space.clicked.connect(self._on_switch_space)
        self.btn_pose_save.clicked.connect(self._on_pose_save)
        self.btn_pose_refresh.clicked.connect(self._refresh_poses)
        self.btn_pose_apply.clicked.connect(
            lambda: self._on_pose_apply(mirror=False, selected=False))
        self.btn_pose_apply_mirror.clicked.connect(
            lambda: self._on_pose_apply(mirror=True, selected=False))
        self.btn_pose_apply_sel.clicked.connect(
            lambda: self._on_pose_apply(mirror=False, selected=True))
        self.btn_pose_delete.clicked.connect(self._on_pose_delete)
        self.btn_pose_flip.clicked.connect(self._on_pose_flip)
        # Rig finishing (pre-export)
        self.btn_add_correctives.clicked.connect(self._on_add_correctives)
        self.btn_make_dynamic.clicked.connect(self._on_make_dynamic)
        self.btn_validate.clicked.connect(
            lambda: self._on_validate(fix=False))
        self.btn_validate_fix.clicked.connect(
            lambda: self._on_validate(fix=True))
        # Quadruped
        self.quad_animal_combo.currentIndexChanged.connect(
            self._on_quad_animal_changed)
        self.btn_quad_create_guides.clicked.connect(
            self._on_quad_create_guides)
        self.btn_quad_mirror.clicked.connect(self._on_quad_mirror)
        self.btn_quad_reset.clicked.connect(self._on_quad_reset)
        self.btn_quad_delete_guides.clicked.connect(
            self._on_quad_delete_guides)
        self.btn_quad_build.clicked.connect(self._on_quad_build)
        self.btn_quad_delete_rig.clicked.connect(self._on_quad_delete_rig)
        # Bird (eagle / hawk)
        self.btn_bird_create_guides.clicked.connect(
            self._on_bird_create_guides)
        self.btn_bird_mirror.clicked.connect(self._on_bird_mirror)
        self.btn_bird_reset.clicked.connect(self._on_bird_reset)
        self.btn_bird_delete_guides.clicked.connect(
            self._on_bird_delete_guides)
        self.btn_bird_build.clicked.connect(self._on_bird_build)
        self.btn_bird_delete_rig.clicked.connect(self._on_bird_delete_rig)
        # Vehicle
        self.btn_vehicle_fit_wheels.clicked.connect(
            self._on_vehicle_fit_wheels)
        self.vehicle_axle_combo.currentIndexChanged.connect(
            self._on_vehicle_axles_changed)
        self.btn_vehicle_create_guides.clicked.connect(
            self._on_vehicle_create_guides)
        self.btn_vehicle_reset_guides.clicked.connect(
            self._on_vehicle_reset_guides)
        self.btn_vehicle_save_guides.clicked.connect(
            self._on_vehicle_save_guides)
        self.btn_vehicle_load_guides.clicked.connect(
            self._on_vehicle_load_guides)
        self.btn_vehicle_delete_guides.clicked.connect(
            self._on_vehicle_delete_guides)
        self.btn_vehicle_build.clicked.connect(self._on_vehicle_build)
        self.btn_vehicle_delete_rig.clicked.connect(
            self._on_vehicle_delete_rig)
        self.btn_vehicle_assign_ground.clicked.connect(
            self._on_vehicle_assign_ground)
        self.btn_vehicle_clear_ground.clicked.connect(
            self._on_vehicle_clear_ground)
        self.btn_vehicle_bake_ground.clicked.connect(
            self._on_vehicle_bake_ground)
        self.btn_vehicle_fix_body.clicked.connect(
            self._on_vehicle_fix_body)
        self.btn_vehicle_drive.clicked.connect(self._on_vehicle_drive)
        self.btn_vehicle_tracks.clicked.connect(self._on_vehicle_tracks)
        self.btn_vehicle_burnout.clicked.connect(self._on_vehicle_burnout)
        self.btn_vehicle_clear_tracks.clicked.connect(
            self._on_vehicle_clear_tracks)
        self.btn_vehicle_clear_burnout.clicked.connect(
            self._on_vehicle_clear_burnout)
        self.btn_moto_tracks.clicked.connect(self._on_vehicle_tracks)
        self.btn_moto_burnout.clicked.connect(self._on_vehicle_burnout)
        self.btn_vehicle_simulate.clicked.connect(self._on_vehicle_simulate)
        self.btn_vehicle_links.clicked.connect(self._on_vehicle_links)
        self.btn_parts_preset.clicked.connect(self._on_parts_preset)
        self.btn_part_add.clicked.connect(self._on_part_add)
        self.btn_parts_build.clicked.connect(self._on_parts_build)
        self.btn_part_remove.clicked.connect(self._on_part_remove)
        self._refresh_parts_list()
        self.btn_trailer_add.clicked.connect(self._on_trailer_add)
        self.btn_trailer_remove.clicked.connect(self._on_trailer_remove)
        self.btn_trailers_build.clicked.connect(self._on_trailers_build)
        self.btn_trailers_bake.clicked.connect(self._on_trailers_bake)
        self._refresh_trailers_list()
        self.btn_prop_guides.clicked.connect(self._on_prop_guides)
        self.btn_prop_build.clicked.connect(self._on_prop_build)
        self.btn_prop_bind.clicked.connect(self._on_prop_bind)
        self.btn_prop_attach.clicked.connect(self._on_prop_attach)
        self.btn_prop_detach.clicked.connect(self._on_prop_detach)
        self.btn_prop_pickup.clicked.connect(
            lambda: self._on_prop_switch(True))
        self.btn_prop_putdown.clicked.connect(
            lambda: self._on_prop_switch(False))
        self.btn_prop_export.clicked.connect(self._on_prop_export)
        self.btn_prop_delete.clicked.connect(self._on_prop_delete)
        self.btn_prop_delete_guides.clicked.connect(
            self._on_prop_delete_guides)
        self.btn_cargo_add.clicked.connect(self._on_cargo_add)
        self.btn_cargo_remove.clicked.connect(self._on_cargo_remove)
        self.btn_cargo_bake.clicked.connect(self._on_cargo_bake)
        self.btn_cargo_clear.clicked.connect(self._on_cargo_clear)
        self.btn_cargo_select.clicked.connect(self._on_cargo_select)
        self._refresh_cargo_label()
        self.btn_crash_add.clicked.connect(self._on_crash_add)
        self.btn_crash_remove.clicked.connect(self._on_crash_remove)
        self.btn_crash_bake.clicked.connect(self._on_crash_bake)
        self.btn_crash_clear.clicked.connect(self._on_crash_clear)
        self._refresh_crash_label()
        self.cb_vehicle_tracked.toggled.connect(
            lambda on: self.cb_vehicle_steer2.setEnabled(not on))
        self.cb_vehicle_tracked.toggled.connect(
            lambda on: self._refresh_spring_rows())
        self.vehicle_axle_combo.currentIndexChanged.connect(
            lambda i: self._refresh_spring_rows())
        for key, (_, combo, solid) in self.spring_rows.items():
            combo.currentIndexChanged.connect(
                lambda i, k=key: self._on_spring_changed(k))
            solid.toggled.connect(
                lambda on, k=key: self._on_spring_changed(k))
        self._sync_springs_from_guides()
        self._refresh_spring_rows()
        self.btn_track_fit.clicked.connect(self._on_track_fit)
        self.btn_track_add.clicked.connect(self._on_track_add)
        self.btn_track_remove.clicked.connect(self._on_track_remove)
        self.btn_track_role.clicked.connect(self._on_track_set_role)
        self.track_thickness_spin.valueChanged.connect(
            self._on_track_thickness)
        self.cb_vehicle_tracked.toggled.connect(
            lambda on: self._refresh_track_box())
        self._refresh_track_box()
        self.btn_vehicle_clear_sim.clicked.connect(self._on_vehicle_clear_sim)
        self.btn_vehicle_bind.clicked.connect(self._on_vehicle_bind)
        # Game export
        self.btn_game_skeleton.clicked.connect(self._on_game_skeleton)
        self.btn_export_rig.clicked.connect(self._on_export_rig)
        self.btn_export_anim.clicked.connect(self._on_export_animation)
        self.btn_export_timeline.clicked.connect(self._on_export_use_timeline)
        self.btn_clip_add.clicked.connect(self._on_clip_add)
        self.btn_clip_remove.clicked.connect(self._on_clip_remove)
        self.btn_export_clips.clicked.connect(self._on_export_clips)
        self.clips_list.currentItemChanged.connect(self._on_clip_picked)
        self._on_export_use_timeline()
        self._refresh_clips()

    # -----------------------------------------------------------------------
    # Status helpers
    # -----------------------------------------------------------------------

    def _refresh_status(self):
        msgs = []
        msgs.append("Guides: present" if self.guide_system.exists()
                    else "Guides: —")
        msgs.append("Rig: present" if cmds.objExists("CHARACTER_RIG_GRP")
                    else "Rig: —")
        if (self.quad_guide_system.exists()
                or cmds.objExists("QUADRUPED_RIG_GRP")):
            quad = []
            quad.append("guides" if self.quad_guide_system.exists() else "—")
            quad.append("rig" if cmds.objExists("QUADRUPED_RIG_GRP")
                        else "—")
            msgs.append("Quadruped: " + "/".join(quad))
        self.status.setText("   |   ".join(msgs))

    def _info(self, text):
        print(f"[RigBuilderUI] {text}")
        self._refresh_status()

    # -----------------------------------------------------------------------
    # Slots — Guides
    # -----------------------------------------------------------------------

    def _on_create_guides(self):
        try:
            fresh = not self.guide_system.exists()
            picked = self._selected_meshes() if fresh else []
            with undo_chunk():
                self.guide_system.build()
                if self.guide_mode_combo.currentIndex() == 1:
                    rig_creature.set_guide_mode(True)
                s = self.guide_system.fit_to_meshes(picked) if picked else None
            self._sync_neck_row()
            self._info("Guides created." if not s else
                       "Guides created and fitted to the selected model "
                       "(%.4gx the default size)." % s)
        except Exception as e:
            cmds.warning(f"Guide creation failed: {e}")

    def _on_guide_mode_changed(self, index):
        ez = index == 1
        if not (cmds.objExists("RIG_GUIDES_GRP")
                or rig_creature.list_limbs()):
            self._info(f"{'EZ' if ez else 'Free'} guide mode will apply when "
                       f"you create guides.")
            return
        with undo_chunk():
            rig_creature.set_guide_mode(ez)
        self._info("EZ guide mode: guides linked, move a parent and its "
                   "children follow." if ez else
                   "Free guide mode: every guide moves on its own.")

    def _on_mirror(self):
        with undo_chunk():
            self.guide_system.mirror_left_to_right()
        self._info("Mirrored L → R.")

    def _on_reset(self):
        with undo_chunk():
            self.guide_system.reset_to_defaults()
        self._info("Guides reset.")

    def _sync_neck_row(self):
        """Show what the guides in the scene say about the neck."""
        gs = self.guide_system
        if not gs.exists():
            return
        for w, v in ((self.neck_segments_spin, gs.neck_segments()),
                     (self.cb_neck_ik, gs.neck_ik())):
            w.blockSignals(True)
            (w.setValue if hasattr(w, "setValue") else w.setChecked)(v)
            w.blockSignals(False)

    def _on_neck_segments(self, value):
        gs = self.guide_system
        if not gs.exists():
            return
        with undo_chunk():
            gs.set_neck_segments(value)
        if value < 2 and self.cb_neck_ik.isChecked():
            self.cb_neck_ik.setChecked(False)
        self._info("Neck is %d joint%s: drag the new guides along the neck."
                   % (value, "" if value == 1 else "s"))

    def _on_neck_ik(self, on):
        gs = self.guide_system
        if not gs.exists():
            return
        if on and self.neck_segments_spin.value() < 2:
            cmds.warning("An IK neck needs 2 or more neck joints.")
            self.cb_neck_ik.setChecked(False)
            return
        with undo_chunk():
            gs.set_neck_ik(on)
        self._info("IK head on: build the rig and drag C_headIK_CTRL." if on
                   else "IK head off: the neck builds as an FK chain.")

    def _fit_meshes(self):
        """The selected meshes, or a warning and []."""
        meshes = [m for m in cmds.ls(sl=True, type="transform") or []
                  if cmds.listRelatives(m, s=True, type="mesh", ni=True)]
        meshes += [m for m in cmds.ls(sl=True, type="mesh") or []]
        if not meshes:
            cmds.warning("Select your model's mesh (or meshes) first.")
        return meshes

    def _fit_guides(self, system, what, exists=True):
        """Scale a guide set to the selected model. Returns the scale."""
        meshes = self._fit_meshes()
        if not meshes:
            return None
        if exists and not system.exists():
            cmds.warning("Create the guides first.")
            return None
        with undo_chunk():
            s = system.fit_to_meshes(meshes)
        if s is None:
            cmds.warning("Couldn't measure that selection: pick the "
                         "model's meshes.")
            return None
        self._info("%s fitted to the model: %.4gx the default size. Place "
                   "them on it and build." % (what, s))
        return s

    def _on_fit_guides(self):
        self._fit_guides(self.guide_system, "Guides")

    def _on_quad_fit_guides(self):
        self._fit_guides(self.quad_guide_system, "Quadruped guides")

    def _on_bird_fit_guides(self):
        self._fit_guides(self.bird_guide_system, "Bird guides")

    def _on_vehicle_fit_guides(self):
        meshes = self._fit_meshes()
        if not meshes:
            return
        gs = self.vehicle_guide_system
        if not gs.exists():
            cmds.warning("Create Car Guides first.")
            return
        box = rig_guides.mesh_box(meshes)
        if not box:
            cmds.warning("Couldn't measure that selection: pick the "
                         "vehicle's meshes.")
            return
        with undo_chunk():
            s = gs.fit_to_model(box[0] + box[1])
        self._info("Vehicle guides fitted to the model: %.4gx. Now select "
                   "the tyres and click Fit Wheels to Selected Tyres." % s)

    def _on_save(self):
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save Guide Positions", "", "JSON (*.json)")
        if not path:
            return
        if not path.lower().endswith(".json"):
            path += ".json"
        self.guide_system.save_to_json(path)
        self._info(f"Saved to {path}")

    def _on_load(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Load Guide Positions", "", "JSON (*.json)")
        if not path:
            return
        with undo_chunk():
            self.guide_system.load_from_json(path)
        self._info(f"Loaded {path}")

    def _on_delete_guides(self):
        with undo_chunk():
            self.guide_system.delete()
        self._info("Guides deleted.")

    def _on_toggle_labels(self):
        """Flip RIG_GUIDES_GRP.showLabels."""
        if not cmds.objExists("RIG_GUIDES_GRP"):
            cmds.warning("No guides in scene. Create guides first.")
            return
        if not cmds.attributeQuery("showLabels",
                                    node="RIG_GUIDES_GRP", exists=True):
            cmds.warning("Labels attr not present — rebuild guides to "
                         "add the label system.")
            return
        cur = cmds.getAttr("RIG_GUIDES_GRP.showLabels")
        cmds.setAttr("RIG_GUIDES_GRP.showLabels", not cur)
        self._info(f"Guide labels {'shown' if not cur else 'hidden'}.")

    def _on_symmetric_toggled(self, checked):
        """Show / hide every R_ locator + flip the symmetricMode attr.
        Tolerates being called before guides exist (we'll just remember
        the checkbox state and apply on the next build)."""
        if cmds.objExists("RIG_GUIDES_GRP"):
            with undo_chunk():
                self.guide_system.set_symmetric_mode(checked)
            self._info(
                f"Symmetric mode {'ON' if checked else 'OFF'} — "
                f"R guides {'hidden' if checked else 'shown'}.")

    # -----------------------------------------------------------------------
    # Slots — Rig build / delete
    # -----------------------------------------------------------------------

    def _selected_modules(self):
        mods = set()
        if self.cb_spine.isChecked():     mods.add("spine")
        if self.cb_neck.isChecked():      mods.add("neck")
        if self.cb_clavicles.isChecked(): mods.add("clavicles")
        if self.cb_arms.isChecked():      mods.add("arms")
        if self.cb_legs.isChecked():      mods.add("legs")
        if self.cb_face.isChecked():      mods.add("face")
        if self.cb_fingers.isChecked():   mods.add("fingers")
        if self.cb_tail.isChecked():      mods.add("tail")
        return mods

    def _selected_face_submodules(self):
        subs = set()
        if self.cb_face_jaw.isChecked():     subs.add("jaw")
        if self.cb_face_eyes.isChecked():    subs.add("eyes")
        if self.cb_face_eyelids.isChecked(): subs.add("eyelids")
        if self.cb_face_brow.isChecked():    subs.add("brow")
        if self.cb_face_mouth.isChecked():   subs.add("mouth")
        if self.cb_face_lips.isChecked():    subs.add("lips")
        if self.cb_face_cheeks.isChecked():  subs.add("cheeks")
        if self.cb_face_nose.isChecked():    subs.add("nose")
        if self.cb_face_tongue.isChecked():  subs.add("tongue")
        if self.cb_face_teeth.isChecked():   subs.add("teeth")
        if self.cb_face_ears.isChecked():    subs.add("ears")
        return subs

    def _on_build(self):
        if cmds.objExists("CHARACTER_RIG_GRP"):
            cmds.warning("A rig already exists. Delete it before rebuilding.")
            return

        positions = None
        if self.rb_from_guides.isChecked():
            if not self.guide_system.exists():
                cmds.warning("No guides found. Create guides first or pick "
                             "'Build from defaults'.")
                return
            # Symmetric mode: copy L → R locator positions before reading,
            # so the rigger only had to place the left side.
            if self.guide_system.is_symmetric_mode():
                self.guide_system.mirror_left_to_right()
            positions = self.guide_system.read_positions()

        # Wrap the whole build in one undo chunk so a single Ctrl+Z
        # removes the entire rig instead of unwinding it node by node.
        cmds.undoInfo(openChunk=True)
        try:
            self.rig = character_rig_builder.CharacterRig(
                positions=positions,
                modules=self._selected_modules(),
                bendy_count=self.bendy_spin.value(),
                spine_fk_count=self.spine_fk_spin.value(),
                face_submodules=self._selected_face_submodules(),
                face_heavy=False,
                face_lid_joints_per_arc=self.lid_jnts_spin.value(),
                face_lip_joints_per_curve=self.lip_jnts_spin.value(),
                # Creature guides, if any (their positions are used as-is,
                # whichever build source is picked above).
                extra_limbs=rig_creature.read_extra_limbs(),
            )
            self.rig.build()
            # An advanced face that existed BEFORE this build is now wired
            # to DELETED head/jaw joints (Delete Rig + Build Rig recreates
            # them) — its lips/blink silently break. Rebuild it from the
            # fits saved in the scene so it re-wires to the fresh joints.
            rebuilt_face = False
            if cmds.objExists(advanced_face.TOP_GROUP):
                reload(advanced_face)   # never rebuild with stale code
                adv = advanced_face.AdvancedFace(
                    lid_joints=self.lid_jnts_spin.value(),
                    lip_joints=self.lip_jnts_spin.value())
                adv._load_fits()
                if adv.fits:
                    adv.build()
                    rebuilt_face = True
            self._info(
                "Rig built"
                + (" — advanced face re-built onto the new head/jaw "
                   "joints." if rebuilt_face
                   else ". For high-detail lids/lips, use the Advanced "
                        "Face (mesh-conforming) window."))
        except Exception as e:
            cmds.warning(f"Rig build failed: {e}")
            raise
        finally:
            cmds.undoInfo(closeChunk=True)

    # -----------------------------------------------------------------------
    # Slots: Creature limbs
    # -----------------------------------------------------------------------

    _CREATURE_PARENTS = (None, "chest", "pelvis", "cog", "head", "custom")
    _CREATURE_KINDS = ("arm", "leg", "tail", "chain")
    _CREATURE_CONTROLS = ("fkik", "fk", "ik")
    # (combo text, side code) per limb type
    _SIDE_ITEMS = [("Both (mirrored)", "LR"), ("Left only", "L"),
                   ("Right only", "R")]
    _CHAIN_SIDE_ITEMS = _SIDE_ITEMS + [("Centre (one chain)", "C")]

    def _creature_kind(self):
        return self._CREATURE_KINDS[self.creature_type_combo.currentIndex()]

    def _on_creature_type_changed(self, *_):
        kind = self._creature_kind()
        self.creature_arm_row.setVisible(kind == "arm")
        self.creature_chain_row.setVisible(kind == "chain")
        self.btn_creature_from_joints.setVisible(kind == "chain")
        self.btn_creature_add.setText("Add Chain Guides" if kind == "chain"
                                      else "Add Limb Guides")
        prev = self.creature_side_combo.currentData()
        self.creature_side_combo.blockSignals(True)
        self.creature_side_combo.clear()
        items = self._CHAIN_SIDE_ITEMS if kind == "chain" else self._SIDE_ITEMS
        for text, code in items:
            self.creature_side_combo.addItem(text, code)
        idx = self.creature_side_combo.findData(prev)
        self.creature_side_combo.setCurrentIndex(max(idx, 0))
        self.creature_side_combo.blockSignals(False)
        self.creature_side_combo.setEnabled(kind != "tail")
        self._on_creature_parent_changed()

    def _on_creature_parent_changed(self, *_):
        custom = (self._CREATURE_PARENTS[
            self.creature_parent_combo.currentIndex()] == "custom")
        self.creature_attach_row.setVisible(custom)

    def _on_creature_pick(self):
        try:
            target = rig_creature.selected_attach()
        except ValueError as e:
            cmds.warning(f"Can't attach: {e}")
            return
        self.creature_attach_edit.setText(target)
        self._info(f"New limbs will attach to "
                   f"{rig_creature.attach_label(target)}.")

    def _creature_attach_args(self):
        """(parent, attach) from the Attach combo; raises ValueError if
        Custom is chosen with nothing picked."""
        parent = self._CREATURE_PARENTS[
            self.creature_parent_combo.currentIndex()]
        if parent != "custom":
            return parent, None
        target = self.creature_attach_edit.text().strip()
        if not target:
            raise ValueError("Attach is Custom: select a guide or joint and "
                             "click Pick Selected first")
        return None, target

    def _refresh_creature_list(self):
        self.creature_list.clear()
        for limb in rig_creature.list_limbs():
            bits = [limb["type"]]
            if limb["type"] == "chain":
                bits.append("%d joints, %s" % (
                    limb.get("joints", 1),
                    {"fkik": "FK + IK", "fk": "FK", "ik": "IK"}.get(
                        limb.get("controls"), "FK + IK")))
            bits.append({"LR": "both sides", "L": "left", "R": "right"}.get(
                limb.get("side"), "centre"))
            on = (rig_creature.attach_label(limb.get("attach"))
                  if limb["parent"] == rig_creature.CUSTOM else limb["parent"])
            bits.append("on " + on)
            bits += [k for k in ("clavicle", "fingers") if limb.get(k)]
            item = QtWidgets.QListWidgetItem(
                "%s  (%s)" % (limb["label"], ", ".join(bits)))
            item.setData(QtCore.Qt.UserRole, limb["label"])
            self.creature_list.addItem(item)

    def _selected_creature_label(self):
        item = self.creature_list.currentItem()
        return item.data(QtCore.Qt.UserRole) if item else None

    def _on_creature_add(self):
        label = self.creature_label_edit.text().strip()
        kind = self._creature_kind()
        # Nudge each new limb aside so it doesn't sit on top of the one it
        # was copied from. (Chains start at what they attach to.)
        offset = {"arm": (0, -20, 0), "leg": (0, 0, -25),
                  "tail": (8, 0, 0), "chain": (0, 0, 0)}[kind]
        try:
            parent, attach = self._creature_attach_args()
            with undo_chunk():
                rig_creature.add_limb(
                    kind, label,
                    side=self.creature_side_combo.currentData() or "LR",
                    parent=parent, attach=attach, offset=offset,
                    clavicle=self.cb_creature_clavicle.isChecked(),
                    fingers=self.cb_creature_fingers.isChecked(),
                    joints=self.creature_joints_spin.value(),
                    controls=self._CREATURE_CONTROLS[
                        self.creature_controls_combo.currentIndex()])
        except ValueError as e:
            cmds.warning(f"Can't add {kind}: {e}")
            return
        self.creature_label_edit.clear()
        self._refresh_creature_list()
        self._info(f"Added {kind} '{label}' guides. Move them, then Build Rig.")

    def _on_creature_from_joints(self):
        label = self.creature_label_edit.text().strip()
        joints = cmds.ls(sl=True, type="joint") or []
        if len(joints) != 1:
            cmds.warning("Select the ROOT joint of the chain you drew "
                         "(exactly one joint).")
            return
        try:
            parent, attach = self._creature_attach_args()
            with undo_chunk():
                rig_creature.chain_from_joints(
                    joints[0], label,
                    side=self.creature_side_combo.currentData() or "C",
                    controls=self._CREATURE_CONTROLS[
                        self.creature_controls_combo.currentIndex()],
                    parent=parent, attach=attach)
        except ValueError as e:
            cmds.warning(f"Can't make chain: {e}")
            return
        self.creature_label_edit.clear()
        self._refresh_creature_list()
        self._info(f"Chain '{label}' guides placed on your joints. You can "
                   f"delete the joints you drew, then Build Rig.")

    def _on_creature_preset(self):
        name = self.creature_preset_combo.currentText()
        try:
            with undo_chunk():
                labels = rig_creature.add_preset(name)
        except ValueError as e:
            cmds.warning(f"Can't add preset {name}: {e}")
            return
        off = rig_creature.PRESETS[name].get("modules_off", [])
        module_boxes = {"tail": self.cb_tail, "legs": self.cb_legs,
                        "arms": self.cb_arms, "fingers": self.cb_fingers}
        for mod in off:
            if mod in module_boxes:
                module_boxes[mod].setChecked(False)
        self._refresh_creature_list()
        self._info(f"{name}: added {', '.join(labels)}"
                   + (f" (turned off the {', '.join(off)} module)" if off
                      else "") + ". Move the guides, then Build Rig.")

    def _on_creature_select(self):
        label = self._selected_creature_label()
        if not label:
            cmds.warning("Pick a limb in the list first.")
            return
        rig_creature.select_limb(label)

    def _on_creature_remove(self):
        label = self._selected_creature_label()
        if not label:
            cmds.warning("Pick a limb in the list first.")
            return
        deps = rig_creature.dependents(label)
        with undo_chunk():
            rig_creature.remove_limb(label)
        self._refresh_creature_list()
        if deps:
            cmds.warning(f"{', '.join(deps)} attached to '{label}'. Pick a "
                         f"new attach for them (remove and re-add) or Build "
                         f"Rig will stop with a missing-joint message.")
        self._info(f"Removed the '{label}' limb guides. Rebuild the rig to "
                   f"drop it from the rig too.")

    def _on_delete_rig(self):
        if not cmds.objExists("CHARACTER_RIG_GRP"):
            cmds.warning("No rig to delete.")
            return
        cmds.delete("CHARACTER_RIG_GRP")
        self.rig = None
        self._info("Rig deleted.")

    # -----------------------------------------------------------------------
    # Slots — Quadruped
    # -----------------------------------------------------------------------

    def _quad_choice(self):
        """(animal, front feet, back feet) picked in the panel."""
        return (self.quad_animal_combo.currentData(),
                self.quad_front_combo.currentData(),
                self.quad_back_combo.currentData())

    def _set_quad_feet(self, front, back):
        for combo, key in ((self.quad_front_combo, front),
                           (self.quad_back_combo, back)):
            i = combo.findData(key)
            if i >= 0:
                combo.setCurrentIndex(i)

    def _on_quad_animal_changed(self, *args):
        feet = quadruped_guides.template_feet(
            self.quad_animal_combo.currentData())
        self._set_quad_feet(feet["front"], feet["back"])

    def _sync_quad_choice(self):
        """Show the animal and feet of the guides already in the scene."""
        gs = self.quad_guide_system
        if not gs.exists():
            return
        i = self.quad_animal_combo.findData(gs.animal())
        if i >= 0:
            self.quad_animal_combo.blockSignals(True)
            self.quad_animal_combo.setCurrentIndex(i)
            self.quad_animal_combo.blockSignals(False)
        feet = gs.feet()
        self._set_quad_feet(feet["front"], feet["back"])

    def _on_quad_create_guides(self):
        animal, front, back = self._quad_choice()
        label = quadruped_guides.ANIMAL_LABELS[animal]
        gs = self.quad_guide_system
        try:
            if gs.exists():
                if gs.animal() != animal:
                    cmds.warning(
                        "These guides are a %s. Use Reset to Defaults to "
                        "turn them into a %s." % (
                            quadruped_guides.ANIMAL_LABELS[gs.animal()],
                            label))
                    return
                if gs.feet() == {"front": front, "back": back}:
                    cmds.warning("Quadruped guides already exist.")
                    return
                with undo_chunk():
                    gs.set_feet(front, back)
                self._info("Guides updated: %s front, %s back (body, "
                           "shoulders and hips kept)." % (front, back))
                return
            picked = self._selected_meshes()
            with undo_chunk():
                gs.build(animal, front, back)
                s = gs.fit_to_meshes(picked) if picked else None
            self._info(f"{label} guides created." if not s else
                       f"{label} guides created and fitted to the selected "
                       f"model ({s:.4g}x the default size).")
        except Exception as e:
            cmds.warning(f"Quadruped guide creation failed: {e}")

    def _on_quad_mirror(self):
        with undo_chunk():
            self.quad_guide_system.mirror_left_to_right()
        self._info("Quadruped guides mirrored L → R.")

    def _on_quad_reset(self):
        animal, front, back = self._quad_choice()
        with undo_chunk():
            self.quad_guide_system.reset_to_defaults(animal, front, back)
        self._info("Quadruped guides reset to %s defaults."
                   % quadruped_guides.ANIMAL_LABELS[animal])

    def _on_quad_delete_guides(self):
        with undo_chunk():
            self.quad_guide_system.delete()
        self._info("Quadruped guides deleted.")

    def _on_quad_build(self):
        if cmds.objExists("QUADRUPED_RIG_GRP"):
            cmds.warning("A quadruped rig already exists. "
                         "Delete it before rebuilding.")
            return
        animal, front, back = self._quad_choice()
        kwargs = {"toes": self.chk_quad_toes.isChecked()}
        if self.rb_quad_from_guides.isChecked():
            if not self.quad_guide_system.exists():
                cmds.warning("No quadruped guides found. Create guides "
                             "first or pick 'From defaults'.")
                return
            kwargs["positions"] = self.quad_guide_system.read_positions()
            feet = kwargs["positions"]["feet"]
            if feet != {"front": front, "back": back}:
                print("[RigBuilderUI] Building the guides' feet (%s front, "
                      "%s back). Click Create Guides to switch them to the "
                      "panel's." % (feet["front"], feet["back"]))
        else:
            kwargs.update(animal=animal, feet={"front": front, "back": back})
        # One undo chunk for the whole quadruped build.
        cmds.undoInfo(openChunk=True)
        try:
            self.quad_rig = quadruped_rig_builder.QuadrupedRig(**kwargs)
            self.quad_rig.build()
            self._info("Quadruped rig built.")
        except Exception as e:
            cmds.warning(f"Quadruped rig build failed: {e}")
            raise
        finally:
            cmds.undoInfo(closeChunk=True)

    def _on_quad_delete_rig(self):
        if not cmds.objExists("QUADRUPED_RIG_GRP"):
            cmds.warning("No quadruped rig to delete.")
            return
        cmds.delete("QUADRUPED_RIG_GRP")
        self.quad_rig = None
        self._info("Quadruped rig deleted.")

    # -----------------------------------------------------------------------
    # Slots — Bird (eagle / hawk)
    # -----------------------------------------------------------------------

    def _on_bird_create_guides(self):
        try:
            fresh = not self.bird_guide_system.exists()
            picked = self._selected_meshes() if fresh else []
            with undo_chunk():
                self.bird_guide_system.build()
                s = (self.bird_guide_system.fit_to_meshes(picked)
                     if picked else None)
            self._info("Bird guides created." if not s else
                       "Bird guides created and fitted to the selected "
                       "model (%.4gx the default size)." % s)
        except Exception as e:
            cmds.warning(f"Bird guide creation failed: {e}")

    def _on_bird_mirror(self):
        with undo_chunk():
            self.bird_guide_system.mirror_left_to_right()
        self._info("Bird guides mirrored L → R.")

    def _on_bird_reset(self):
        with undo_chunk():
            self.bird_guide_system.reset_to_defaults()
        self._info("Bird guides reset to defaults.")

    def _on_bird_delete_guides(self):
        with undo_chunk():
            self.bird_guide_system.delete()
        self._info("Bird guides deleted.")

    def _on_bird_build(self):
        if cmds.objExists("BIRD_RIG_GRP"):
            cmds.warning("A bird rig already exists. "
                         "Delete it before rebuilding.")
            return
        positions = None
        if self.rb_bird_from_guides.isChecked():
            if not self.bird_guide_system.exists():
                cmds.warning("No bird guides found. Create guides first "
                             "or pick 'From defaults'.")
                return
            positions = self.bird_guide_system.read_positions()
        # One undo chunk for the whole bird build.
        cmds.undoInfo(openChunk=True)
        try:
            self.bird_rig = bird_rig_builder.BirdRig(
                positions=positions,
            )
            self.bird_rig.build()
            self._info("Bird rig built.")
        except Exception as e:
            cmds.warning(f"Bird rig build failed: {e}")
            raise
        finally:
            cmds.undoInfo(closeChunk=True)

    def _on_bird_delete_rig(self):
        if not cmds.objExists("BIRD_RIG_GRP"):
            cmds.warning("No bird rig to delete.")
            return
        cmds.delete("BIRD_RIG_GRP")
        self.bird_rig = None
        self._info("Bird rig deleted.")

    # -----------------------------------------------------------------------
    # Slots — Vehicle
    # -----------------------------------------------------------------------

    def _on_vehicle_create_guides(self):
        try:
            existed = self.vehicle_guide_system.exists()
            with undo_chunk():
                self.vehicle_guide_system.build(
                    axles=self.vehicle_axle_combo.currentIndex() + 2)
                if existed:
                    self._sync_springs_from_guides()
                else:
                    for key in self.spring_rows:
                        self._on_spring_changed(key, quiet=True)
            self._info("Car guides created (left + center). Place them, "
                       "then Build From Guides — right side mirrors.")
        except Exception as e:
            cmds.warning(f"Car guide creation failed: {e}")

    def _on_vehicle_fit_wheels(self):
        if not self.vehicle_guide_system.exists():
            cmds.warning("Create Car Guides first.")
            return
        meshes = [m for m in cmds.ls(sl=True, type="transform") or []
                  if cmds.listRelatives(m, s=True, type="mesh", ni=True)]
        if not meshes:
            cmds.warning("Select the tyre meshes first.")
            return
        with undo_chunk():
            done = self.vehicle_guide_system.fit_wheels(meshes)
        self._info("Fitted " + ", ".join(
            f"{n} (radius {self.vehicle_guide_system.any_radius(n):.1f})"
            for n in done) + ".")

    # ---- Tank wheels ----

    def _selected_meshes(self):
        return [m for m in cmds.ls(sl=True, type="transform") or []
                if cmds.listRelatives(m, s=True, type="mesh", ni=True)]

    def _selected_track_guides(self):
        names = []
        for n in cmds.ls(sl=True, type="transform") or []:
            n = n.split("|")[-1]
            if n.endswith("_tyre"):
                n = n[:-len("_tyre")]
            if n.startswith(vehicle_guides.TRACK_WHEEL) and n.endswith(
                    vehicle_guides.GUIDE_SUFFIX):
                names.append(n[:-len(vehicle_guides.GUIDE_SUFFIX)])
        return names

    def _refresh_track_box(self):
        """Enable the section on a tracked vehicle and show what's there."""
        tracked = self.cb_vehicle_tracked.isChecked()
        self.track_box.content.setEnabled(tracked)
        gs = self.vehicle_guide_system
        names = gs.track_wheels() if gs.exists() else []
        self.track_thickness_spin.blockSignals(True)
        self.track_thickness_spin.setValue(gs.tread_thickness()
                                           if gs.exists() else 0.0)
        self.track_thickness_spin.blockSignals(False)
        if not tracked:
            self.track_label.setText("Tick Tracked (tank treads) to use it.")
        elif not names:
            self.track_label.setText(
                "No tank wheels yet: the tank uses the Wheels count above.")
        else:
            roles = [gs.track_role(n) for n in names]
            self.track_label.setText(
                "%d wheels a side: %d road, %d roller(s), %d gear(s). Build "
                "Vehicle Rig uses these (the Wheels count is ignored)."
                % (len(names), roles.count("road"), roles.count("roller"),
                   roles.count("gear")))

    def _on_track_fit(self):
        meshes = self._selected_meshes()
        if not meshes:
            cmds.warning("Select the tank's wheel meshes (and its tread).")
            return
        with undo_chunk():
            got = self.vehicle_guide_system.fit_track_wheels(meshes)
        self._refresh_track_box()
        if got["wheels"]:
            roles = [r for _n, r, _rad in got["wheels"]]
            self._info("Fitted %d wheels: %d road, %d roller(s), %d gear(s); "
                       "tread %.3f thick. Check the roles (colours) and "
                       "fix any with Set on Selected."
                       % (len(roles), roles.count("road"),
                          roles.count("roller"), roles.count("gear"),
                          got["thickness"]))

    def _on_track_add(self):
        gs = self.vehicle_guide_system
        role = vehicle_guides.TRACK_ROLES[self.track_role_combo.currentIndex()]
        reverse = self.cb_track_reverse.isChecked()
        added = []
        with undo_chunk():
            meshes = self._selected_meshes()
            if meshes:
                for w in vehicle_guides.measure_track(meshes)["wheels"]:
                    added.append(gs.add_track_wheel(w["pos"], w["radius"],
                                                    role, reverse))
            else:
                if not gs.exists():
                    gs.build()
                gs._refresh_handles()
                c = cmds.xform(gs.guides["C_chassis"], q=True, ws=True,
                               t=True)
                names = gs.track_wheels()
                r = (sum(gs.radius(n) for n in names) / len(names)
                     if names else 10.0)
                added.append(gs.add_track_wheel((abs(c[0]) + 2 * r, c[1],
                                                 c[2]), r, role, reverse))
        self._refresh_track_box()
        if added:
            cmds.select(gs.guides[added[-1]])
            self._info("Added %s: move it onto the wheel and set its Radius."
                       % ", ".join(added))

    def _on_track_remove(self):
        names = self._selected_track_guides()
        if not names:
            cmds.warning("Select the tank wheel guides to remove.")
            return
        with undo_chunk():
            for n in names:
                self.vehicle_guide_system.remove_track_wheel(n)
        self._refresh_track_box()

    def _on_track_set_role(self):
        names = self._selected_track_guides()
        if not names:
            cmds.warning("Select tank wheel guides first.")
            return
        role = vehicle_guides.TRACK_ROLES[self.track_role_combo.currentIndex()]
        with undo_chunk():
            for n in names:
                self.vehicle_guide_system.set_track_role(
                    n, role, self.cb_track_reverse.isChecked())
        self._refresh_track_box()

    def _on_track_thickness(self, value):
        if self.vehicle_guide_system.exists():
            self.vehicle_guide_system.set_tread_thickness(value)

    # ---- Springs ----

    def _spring_setting(self, key):
        _, combo, solid = self.spring_rows[key]
        return (vehicle_guides.SUSPENSION_TYPES[combo.currentIndex()],
                solid.isChecked())

    def _refresh_spring_rows(self):
        """Show a row per axle of the current layout; a tank's road wheels
        are always independent coil-overs."""
        axles = self.vehicle_axle_combo.currentIndex() + 2
        shown = {"front": True, "mid1": axles >= 3, "mid2": axles >= 4,
                 "back": True}
        tracked = self.cb_vehicle_tracked.isChecked()
        for key, widgets in self.spring_rows.items():
            for w in widgets:
                w.setVisible(shown[key])
                w.setEnabled(not tracked)
        self.lbl_springs_hint.setVisible(tracked)

    def _sync_springs_from_guides(self):
        """Show the spring settings stored on the guides in the scene."""
        gs = self.vehicle_guide_system
        if not gs.exists():
            return
        for key, (_, combo, solid) in self.spring_rows.items():
            for w in (combo, solid):
                w.blockSignals(True)
            combo.setCurrentIndex(
                vehicle_guides.SUSPENSION_TYPES.index(gs.suspension(key)))
            solid.setChecked(gs.solid_axle(key))
            for w in (combo, solid):
                w.blockSignals(False)

    def _on_spring_changed(self, key, quiet=False):
        kind, solid = self._spring_setting(key)
        gs = self.vehicle_guide_system
        if not gs.exists():
            return
        before = gs.spring_guides(key)
        with undo_chunk():
            gs.set_suspension(key, kind, solid)
        added = [n for n in gs.spring_guides(key) if n not in before]
        if added and not quiet:
            self._info(f"{len(added)} orange spring guides added for the "
                       f"{key} axle: place them on the leaf's eyes and "
                       f"middle, the shackle pin and the shock mounts.")

    def _spring_positions(self, positions):
        """The Springs settings into a VehicleRig positions dict (a build
        from defaults; guides carry their own)."""
        import copy
        out = copy.deepcopy(positions or
                            vehicle_rig_builder.VehicleRig.DEFAULT_POSITIONS)
        for key, axle, _, _ in vehicle_guides.AXLES:
            kind, solid = self._spring_setting(key)
            out[f"{axle}_suspension"] = kind
            out[f"{axle}_solidAxle"] = solid
        return out

    def _on_vehicle_axles_changed(self, index):
        if not self.vehicle_guide_system.exists():
            return
        with undo_chunk():
            changed = self.vehicle_guide_system.set_axles(index + 2)
        if changed:
            self._info(f"Guides now for {2 * (index + 2)} wheels: place "
                       f"and size each middle axle's wheel and spring.")

    def _on_vehicle_reset_guides(self):
        with undo_chunk():
            self.vehicle_guide_system.reset_to_defaults()
        self._info("Car guides reset to defaults.")

    def _on_vehicle_save_guides(self):
        if not self.vehicle_guide_system.exists():
            cmds.warning("No car guides to save: Create Car Guides first.")
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save Vehicle Guides", "", "JSON (*.json)")
        if not path:
            return
        if not path.lower().endswith(".json"):
            path += ".json"
        self.vehicle_guide_system.save_to_json(path)
        self._info(f"Vehicle guides saved to {path}")

    def _on_vehicle_load_guides(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Load Vehicle Guides", "", "JSON (*.json)")
        if not path:
            return
        gs = self.vehicle_guide_system
        with undo_chunk():
            gs.load_from_json(path)
        # Show what came in: the axle count, springs, tank wheels.
        self.vehicle_axle_combo.setCurrentIndex(gs.axle_count() - 2)
        self._sync_springs_from_guides()
        if gs.track_wheels():
            self.cb_vehicle_tracked.setChecked(True)
        self._refresh_track_box()
        self._info(f"Vehicle guides loaded from {path}")

    # ---- Motorcycle ----

    def _on_moto_create_guides(self):
        try:
            with undo_chunk():
                self.moto_guide_system.build()
            self._info("Bike guides created. Place them on your model (or "
                       "Fit Guides to Selected Model), then build.")
        except Exception as e:
            cmds.warning(f"Bike guide creation failed: {e}")

    def _on_moto_fit_guides(self):
        meshes = self._fit_meshes()
        if not meshes:
            return
        with undo_chunk():
            ok = self.moto_guide_system.fit_to_meshes(meshes)
        if not ok:
            cmds.warning("Couldn't measure that selection: pick the bike's "
                         "meshes.")
            return
        self._info("Bike guides fitted to the model. Check the steering "
                   "head, swingarm pivot and handlebar, then build.")

    def _on_moto_fit_wheels(self):
        meshes = self._fit_meshes()
        if not meshes:
            return
        with undo_chunk():
            n = self.moto_guide_system.fit_wheels(meshes)
        if not n:
            cmds.warning("Couldn't find two round wheels in that selection: "
                         "select the tyre meshes.")
            return
        gs = self.moto_guide_system
        self._info("Wheels fitted: front radius %.1f, back radius %.1f."
                   % (gs.radius("C_frontWheel"), gs.radius("C_backWheel")))

    def _on_moto_reset_guides(self):
        with undo_chunk():
            self.moto_guide_system.reset_to_defaults()
        self._info("Bike guides reset to defaults.")

    def _on_moto_delete_guides(self):
        with undo_chunk():
            self.moto_guide_system.delete()
        self._info("Bike guides deleted.")

    def _on_moto_save_guides(self):
        if not self.moto_guide_system.exists():
            cmds.warning("No bike guides to save.")
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save Bike Guides", "", "JSON (*.json)")
        if not path:
            return
        self.moto_guide_system.save_to_json(path)
        self._info("Bike guides saved to %s." % os.path.basename(path))

    def _on_moto_load_guides(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Load Bike Guides", "", "JSON (*.json)")
        if not path:
            return
        with undo_chunk():
            self.moto_guide_system.load_from_json(path)
        self._info("Bike guides loaded from %s." % os.path.basename(path))

    def _on_moto_build(self):
        if cmds.objExists("VEHICLE_RIG_GRP"):
            cmds.warning("A vehicle rig already exists. Delete it before "
                         "building a motorcycle.")
            return
        positions = None
        if self.rb_moto_from_guides.isChecked():
            if not self.moto_guide_system.exists():
                cmds.warning("No bike guides found. Create guides first or "
                             "pick 'From defaults'.")
                return
            positions = self.moto_guide_system.read_positions()
        cmds.undoInfo(openChunk=True)
        try:
            self.vehicle_rig = motorcycle_rig_builder.MotorcycleRig(
                positions=positions,
                spoke_count=self.moto_spoke_spin.value(),
                build_spoke_ctrls=self.cb_moto_ctrls.isChecked())
            self.vehicle_rig.build()
            gaps = []
            for prefix, key in (("CF", "frontWheel"), ("CB", "backWheel")):
                pos = self.vehicle_rig.positions[key]
                radius = self.vehicle_rig.positions[
                    "frontRadius" if prefix == "CF" else "backRadius"]
                if abs(pos[1] - radius) > 0.1:
                    gaps.append((prefix, pos[1] - radius))
            if gaps:
                cmds.warning(
                    "Tyres not on the ground (y 0) at rest: "
                    + ", ".join(f"{p} {g:+.1f}" for p, g in gaps)
                    + ". Move the wheel guide or change its radius.")
            self._info("Motorcycle rig built: rake %.0f deg, wheelbase %.0f."
                       % (self.vehicle_rig.rake(),
                          self.vehicle_rig.wheelbase())
                       + (" Some tyres aren't on the ground: see the Script "
                          "Editor." if gaps else "")
                       + " Drive it with WASD in the Animate tab.")
        except Exception as e:
            cmds.warning(f"Motorcycle rig build failed: {e}")
            raise
        finally:
            cmds.undoInfo(closeChunk=True)

    def _on_moto_delete_rig(self):
        if not cmds.objExists("VEHICLE_RIG_GRP"):
            cmds.warning("No motorcycle rig to delete.")
            return
        cmds.delete("VEHICLE_RIG_GRP")
        self.vehicle_rig = None
        self._info("Motorcycle rig deleted.")

    # ---- Tyre tracks and burnouts ----

    def _burnout_which(self):
        return ("rear", "front", "all")[
            self.burnout_wheels_combo.currentIndex()]

    def _on_vehicle_tracks(self):
        if not cmds.objExists("C_chassis_CTRL"):
            cmds.warning("Build a vehicle first.")
            return
        wheels = ("driven", "all")[self.track_wheels_combo.currentIndex()]
        only_slip = self.cb_track_slip.isChecked()
        with undo_chunk():
            made = vehicle_tracks.bake_tracks(wheels=wheels,
                                              only_slip=only_slip)
        if made:
            self._info("Laid %d tyre track(s) over the timeline%s. They are "
                       "meshes under %s: shade them, or Clear Tracks and "
                       "bake again."
                       % (len(made),
                          " where the tyres slid" if only_slip else "",
                          vehicle_tracks.TRACKS_GRP))

    def _on_vehicle_burnout(self):
        if not cmds.objExists("C_chassis_CTRL"):
            cmds.warning("Build a vehicle first.")
            return
        which = self._burnout_which()
        with undo_chunk():
            span = vehicle_tracks.burnout(
                frames=self.burnout_frames_spin.value(), which=which)
        if span:
            self._info("Burnout on the %s wheels, frames %d to %d, with the "
                       "marks baked. Clear Burnout takes the keys off again."
                       % (which, span[0], span[1]))

    def _on_vehicle_clear_tracks(self):
        with undo_chunk():
            gone = vehicle_tracks.clear_tracks()
        self._info("Tyre tracks cleared." if gone else "No tracks to clear.")

    def _on_vehicle_clear_burnout(self):
        with undo_chunk():
            n = vehicle_tracks.clear_burnout(self._burnout_which())
        self._info("Burnout keys removed from %d channel(s)." % n)

    def _on_vehicle_delete_guides(self):
        with undo_chunk():
            self.vehicle_guide_system.delete()
        self._info("Car guides deleted.")

    def _on_vehicle_build(self):
        if cmds.objExists("VEHICLE_RIG_GRP"):
            cmds.warning("A vehicle rig already exists. "
                         "Delete it before rebuilding.")
            return
        positions = None
        if self.rb_vehicle_from_guides.isChecked():
            if not self.vehicle_guide_system.exists():
                cmds.warning("No car guides found. Create guides first "
                             "or pick 'From defaults'.")
                return
            positions = self.vehicle_guide_system.read_positions()
            if not self.cb_vehicle_tracked.isChecked():
                positions.pop("trackWheels", None)
                positions.pop("treadThickness", None)
        elif any(self._spring_setting(k) != ("coil", False)
                 for k in self.spring_rows):
            positions = self._spring_positions(None)
        axles = self.vehicle_axle_combo.currentIndex() + 2
        if positions is not None and self.rb_vehicle_from_guides.isChecked():
            axles = max(axles, self.vehicle_guide_system.axle_count())
        cmds.undoInfo(openChunk=True)
        try:
            self.vehicle_rig = vehicle_rig_builder.VehicleRig(
                positions=positions,
                spoke_count=self.vehicle_spoke_spin.value(),
                build_spoke_ctrls=self.cb_vehicle_ctrls.isChecked(),
                axles=axles,
                tracked=self.cb_vehicle_tracked.isChecked(),
                steer_axles=2 if self.cb_vehicle_steer2.isChecked() else 1,
            )
            # Coil-over springs: gate by the checkbox (toggle the
            # 'suspension' module which drives the spring build).
            if not self.cb_vehicle_spring.isChecked():
                self.vehicle_rig.modules.discard("suspension")
            self.vehicle_rig.build()
            vehicle_sim.ensure_settings()     # PHYSICS attrs on the chassis
            vehicle_crash.ensure_settings()   # CRASH attrs on the chassis
            parts = (vehicle_parts.build_parts()
                     if vehicle_parts.list_parts() else [])
            trailers = (vehicle_trailers.build_trailers()
                        if vehicle_trailers.list_trailers() else [])
            cargo = vehicle_cargo.rebuild_cargo()
            self._refresh_cargo_label()
            gaps = (vehicle_guides.ground_gaps(positions)
                    if positions and self.rb_vehicle_from_guides.isChecked()
                    else [])
            gaps += vehicle_trailers.ground_gaps() if trailers else []
            if gaps:
                cmds.warning(
                    "Tyres not on the ground (y 0) at rest: "
                    + ", ".join(f"{p} {g:+.1f}" for p, g in gaps)
                    + ". Below it they squash, above it they float: move "
                      "the car (or its wheel guides) or change the radius.")
            self._info("Vehicle rig built."
                       + (" Some tyres aren't on the ground: see the "
                          "Script Editor." if gaps else "")
                       + (f" Parts rebuilt: {', '.join(parts)}." if parts
                          else "")
                       + (f" {len(trailers)} trailer(s) rebuilt."
                          if trailers else "")
                       + (f" {len(cargo)} cargo item(s) rebuilt."
                          if cargo else ""))
        except Exception as e:
            cmds.warning(f"Vehicle rig build failed: {e}")
            raise
        finally:
            cmds.undoInfo(closeChunk=True)

    def _on_vehicle_delete_rig(self):
        if not cmds.objExists("VEHICLE_RIG_GRP"):
            cmds.warning("No vehicle rig to delete.")
            return
        vehicle_trailers.delete_trailers()
        cmds.delete("VEHICLE_RIG_GRP")
        # The ground locator + plane connections live outside the group;
        # the C_ground_LOC is parented inside, so it goes with the delete.
        self.vehicle_rig = None
        self._info("Vehicle rig deleted.")

    def _on_vehicle_assign_ground(self):
        if not cmds.objExists("VEHICLE_RIG_GRP"):
            cmds.warning("Build a vehicle rig first.")
            return
        sel = cmds.ls(sl=True, type="transform") or []
        # Filter to transforms that actually have a mesh shape.
        meshes = [s for s in sel
                  if cmds.listRelatives(s, s=True, type="mesh", ni=True)]
        if not meshes:
            cmds.warning("Select a polygon ground/terrain mesh first.")
            return
        mesh = meshes[0]
        with undo_chunk():
            n = vehicle_rig_builder.assign_ground_mesh(mesh)
        self._info(f"Ground mesh '{mesh}' assigned — {n} spokes now "
                   f"sample terrain per-wheel.")

    def _on_vehicle_clear_ground(self):
        if not cmds.objExists("VEHICLE_RIG_GRP"):
            cmds.warning("No vehicle rig in scene.")
            return
        with undo_chunk():
            n = vehicle_rig_builder.clear_ground_mesh()
        self._info(f"Reverted {n} spokes to flat ground (C_ground_LOC).")

    def _on_vehicle_bake_ground(self):
        if not cmds.objExists("VEHICLE_RIG_GRP"):
            cmds.warning("Build a vehicle rig first.")
            return
        if not raycast_ground.has_footprints():
            cmds.warning("This rig has no footprint samplers — rebuild the "
                         "vehicle rig, then assign a ground mesh.")
            return
        mesh = vehicle_rig_builder.assigned_ground_mesh()
        if not mesh or not cmds.objExists(mesh):
            cmds.warning("Assign a ground mesh first "
                         "(Assign Selected Mesh as Ground).")
            return
        start = int(cmds.playbackOptions(q=True, min=True))
        end = int(cmds.playbackOptions(q=True, max=True))
        with undo_chunk():
            frames = raycast_ground.bake_over_range(mesh, start, end)
        self._info(
            f"Baked straight-down ground raycast on '{mesh}' over frames "
            f"{start}-{end} ({frames} frames). Wheels now stay planted "
            f"until they're over a bump. 'Clear Ground' removes the bake.")

    def _on_vehicle_fix_body(self):
        if not cmds.objExists("C_chassis_CTRL"):
            cmds.warning("Build a vehicle rig first.")
            return
        with undo_chunk():
            rep = vehicle_rig_builder.repair_body_motion()
            wheels = vehicle_rig_builder.repair_tire_sag()
        pitch = ("flipped to nose-up uphill" if rep.get("pitch_flipped")
                 else "already correct")
        self._info(
            f"Rig upgraded in place. Body: {rep.get('clamps_freed', 0)} "
            f"gain(s) unclamped (can go negative), pitch {pitch}. "
            f"Tyres: deflation squat added to {wheels} wheel(s) — set "
            f"tirePressure to 0 and a flat tyre now sags + flattens, even "
            f"on flat ground / while driving (tune per wheel with tireSag).")

    def _on_vehicle_simulate(self):
        if not vehicle_sim.has_vehicle():
            cmds.warning("Build a vehicle rig first.")
            return
        try:
            with undo_chunk():
                s = vehicle_sim.simulate()
        except (RuntimeError, ValueError) as e:
            cmds.warning(f"Simulation failed: {e}")
            return
        air = (f", {s['airborne_frames']} frames in the air"
               if s["airborne_frames"] else "")
        dents = (f", {s['dents']} crash dent(s)" if s.get("dents") else "")
        self._refresh_crash_label()
        self._info(f"Physics baked over {s['frames']} frames on "
                   f"{s['ground']} ground{air}{dents}. Press play. Tune on "
                   f"C_chassis_CTRL > PHYSICS and simulate again.")

    # -----------------------------------------------------------------------
    # Slots: Vehicle parts
    # -----------------------------------------------------------------------

    def _refresh_parts_list(self):
        self.parts_list.clear()
        names = []
        for p in vehicle_parts.list_parts():
            if p["kind"] == "hinge":
                lim = ("" if p.get("min") is None and p.get("max") is None
                       else ", %s to %s deg" % (p.get("min"), p.get("max")))
                text = "%s  (hinge %s on %s%s%s)" % (
                    p["name"], p["axis"].upper(), p["parent"], lim,
                    ", aims" if p.get("aim") else "")
                names.append(p["name"])
            else:
                text = "%s  (ram %s to %s)" % (p["name"], p["parent"],
                                               p["end_parent"])
            item = QtWidgets.QListWidgetItem(text)
            item.setData(QtCore.Qt.UserRole, p["name"])
            self.parts_list.addItem(item)
        current = self.part_parent_combo.currentText()
        self.part_parent_combo.clear()
        self.part_parent_combo.addItems([vehicle_parts.BODY] + names)
        idx = self.part_parent_combo.findText(current)
        self.part_parent_combo.setCurrentIndex(max(idx, 0))

    def _on_parts_preset(self):
        name = self.parts_preset_combo.currentText()
        try:
            with undo_chunk():
                added = vehicle_parts.add_preset(name)
        except ValueError as e:
            cmds.warning(f"Can't add {name}: {e}")
            return
        self._refresh_parts_list()
        self._info(f"{name}: guides for {', '.join(added)}. Move them onto "
                   f"your model's pivots, then Build Parts.")

    def _on_part_add(self):
        name = self.part_name_edit.text().strip()
        sel = cmds.ls(sl=True, fl=True) or []
        if sel:
            bb = cmds.exactWorldBoundingBox(sel)
            pos = ((bb[0] + bb[3]) / 2, (bb[1] + bb[4]) / 2,
                   (bb[2] + bb[5]) / 2)
        elif cmds.objExists("C_chassis_CTRL"):
            x, y, z = cmds.xform("C_chassis_CTRL", q=True, ws=True, t=True)
            pos = (x, y + 60.0, z)
        else:
            pos = (0.0, 140.0, 0.0)
        limits = self.cb_part_limits.isChecked()
        try:
            with undo_chunk():
                vehicle_parts.add_hinge(
                    name, pos, axis=("y", "x", "z")[
                        self.part_axis_combo.currentIndex()],
                    parent=self.part_parent_combo.currentText()
                    or vehicle_parts.BODY,
                    min_angle=self.part_min_spin.value() if limits else None,
                    max_angle=self.part_max_spin.value() if limits else None,
                    aim=self.cb_part_aim.isChecked())
        except ValueError as e:
            cmds.warning(f"Can't add hinge: {e}")
            return
        self.part_name_edit.clear()
        self._refresh_parts_list()
        self._info(f"Hinge '{name}' guide added. Move it onto the pivot, then "
                   f"Build Parts.")

    def _on_parts_build(self):
        try:
            with undo_chunk():
                names = vehicle_parts.build_parts()
        except (RuntimeError, ValueError) as e:
            cmds.warning(f"Build Parts failed: {e}")
            return
        self._info(f"Built {len(names)} part(s). Rotate the C_*_part_CTRL "
                   f"controls; turrets aim with aimAtTarget + C_partAim_LOC.")

    def _on_part_remove(self):
        item = self.parts_list.currentItem()
        if not item:
            cmds.warning("Pick a part in the list first.")
            return
        with undo_chunk():
            removed = vehicle_parts.remove_part(item.data(QtCore.Qt.UserRole))
        self._refresh_parts_list()
        self._info(f"Removed guides for {', '.join(removed)}. Build Parts to "
                   f"update the rig.")

    # -----------------------------------------------------------------------
    # Slots: Trailers
    # -----------------------------------------------------------------------

    def _refresh_trailers_list(self):
        self.trailers_list.clear()
        built = vehicle_trailers.trailer_count()
        for t in vehicle_trailers.list_trailers():
            n = len(t["axles"])
            self.trailers_list.addItem(
                "Trailer %d  (%d axle%s%s)" % (
                    t["index"], n, "" if n == 1 else "s",
                    ", built" if t["index"] <= built else ""))

    def _on_trailer_add(self):
        try:
            with undo_chunk():
                i = vehicle_trailers.add_trailer(
                    self.trailer_axles_spin.value())
        except ValueError as e:
            cmds.warning(f"Can't add a trailer: {e}")
            return
        self._refresh_trailers_list()
        self._info(f"Trailer {i} guides added. Put T{i}_hitch_GUIDE on the "
                   f"tow point and the axle guides on the LEFT wheel "
                   f"centres, then Build Trailers.")

    def _on_trailer_remove(self):
        with undo_chunk():
            i = vehicle_trailers.remove_last_trailer()
        self._refresh_trailers_list()
        self._info(f"Removed trailer {i} guides. Build Trailers to update."
                   if i else "No trailer guides to remove.")

    def _on_trailers_build(self):
        try:
            with undo_chunk():
                built = vehicle_trailers.build_trailers()
        except (RuntimeError, ValueError) as e:
            cmds.warning(f"Build Trailers failed: {e}")
            return
        self._refresh_trailers_list()
        gaps = vehicle_trailers.ground_gaps() if built else []
        if gaps:
            cmds.warning(
                "Trailer tyres not on the ground (y 0) at rest: "
                + ", ".join(f"{g} {d:+.1f}" for g, d in gaps)
                + ". Move the axle guide or change its Radius.")
        self._info(f"Built {len(built)} trailer(s). Drive, simulate or Bake "
                   f"Trailers to make them follow." if built else
                   "No trailer guides: Add Trailer Guides first.")

    def _on_trailers_bake(self):
        if not vehicle_trailers.has_trailers():
            cmds.warning("Build Trailers first.")
            return
        with undo_chunk():
            n = vehicle_trailers.bake_trailers()
        self._info(f"Trailers baked over {n} frames.")

    def _on_vehicle_links(self):
        sel = [s for s in (cmds.ls(sl=True, type="transform") or [])
               if cmds.listRelatives(s, s=True, type="mesh", ni=True)]
        if len(sel) != 1:
            spacing = vehicle_rig_builder.tread_link_spacing()
            cmds.warning("Select ONE tread link mesh first: model a single "
                         "link at the origin, running along +Z with its axle "
                         "along X"
                         + (f", about {spacing:.1f} units long." if spacing
                            else "."))
            return
        with undo_chunk():
            made = vehicle_rig_builder.instance_tread_links(sel[0])
        if made:
            self._info(f"Instanced '{sel[0]}' onto {len(made)} tread links "
                       f"(links are "
                       f"{vehicle_rig_builder.tread_link_spacing():.1f} "
                       f"units apart).")

    def _on_vehicle_clear_sim(self):
        with undo_chunk():
            done = vehicle_sim.clear_simulation()
            # The dents came from the simulated motion: bake them again for
            # the original path.
            dents = (vehicle_crash.bake_damage()["dents"]
                     if done and vehicle_crash.active() else 0)
            if done and vehicle_cargo.list_cargo():
                vehicle_cargo.bake_cargo()
        self._info(("Simulation cleared, original path restored."
                    + (f" {dents} crash dent(s) re-baked." if dents else ""))
                   if done else "No simulation to clear.")

    # -----------------------------------------------------------------------
    # Slots: Props
    # -----------------------------------------------------------------------

    def _refresh_props(self, select=None):
        current = select or self.prop_combo.currentText()
        self.prop_combo.blockSignals(True)
        self.prop_combo.clear()
        self.prop_combo.addItems(prop_rig.list_props())
        i = self.prop_combo.findText(current)
        if i >= 0:
            self.prop_combo.setCurrentIndex(i)
        self.prop_combo.blockSignals(False)

    def _prop(self):
        name = self.prop_combo.currentText()
        if not name:
            cmds.warning("No prop yet: select its mesh and Create Prop "
                         "Guides.")
        return name

    def _on_prop_guides(self):
        with undo_chunk():
            name = prop_rig.create_guides(self.prop_name_edit.text())
        self._refresh_props(name)
        self._info(f"Guides for '{name}': put root at the base, move at the "
                   f"pivot, attach at the grip, then Build.")

    def _on_prop_build(self):
        name = self._prop()
        if not name:
            return
        try:
            with undo_chunk():
                prop_rig.build(name)
        except RuntimeError as e:
            cmds.warning(str(e))
            return
        self._refresh_props(name)
        self._info(f"Prop '{name}' built. Animate {name}_move_CTRL.")

    def _on_prop_bind(self):
        name = self._prop()
        if not name:
            return
        try:
            with undo_chunk():
                done = prop_rig.bind(name)
        except RuntimeError as e:
            cmds.warning(str(e))
            return
        self._info(f"Bound {len(done)} mesh(es) to '{name}'." if done
                   else "Select the prop's mesh(es) first.")

    def _on_prop_attach(self):
        name = self._prop()
        if not name:
            return
        sel = [s for s in cmds.ls(sl=True, type="transform") or []
               if not s.startswith(name + "_")]
        if not sel:
            cmds.warning("Select the hand joint or control to attach "
                         f"'{name}' to.")
            return
        try:
            with undo_chunk():
                prop_rig.attach(name, sel[0])
        except RuntimeError as e:
            cmds.warning(str(e))
            return
        self._info(f"'{name}' is in {sel[0]}. Pick Up / Put Down switch it "
                   f"without a jump.")

    def _on_prop_detach(self):
        name = self._prop()
        if name:
            with undo_chunk():
                done = prop_rig.detach(name)
            self._info(f"'{name}' detached." if done
                       else f"'{name}' wasn't attached.")

    def _on_prop_switch(self, attached):
        name = self._prop()
        if name:
            with undo_chunk():
                ok = prop_rig.switch(name, attached, key=True)
            if ok:
                self._info(f"'{name}' {'picked up' if attached else 'put down'}"
                           f" and keyed on this frame.")

    def _on_prop_export(self):
        name = self._prop()
        if not name:
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Export prop", f"{name}.fbx", "FBX (*.fbx)")
        if not path:
            return
        if prop_rig.export_fbx(name, path,
                               animation=self.cb_prop_anim.isChecked()):
            self._info(f"'{name}' exported to {path}.")

    def _on_prop_delete(self):
        name = self._prop()
        if name:
            with undo_chunk():
                prop_rig.delete_rig(name)
            self._refresh_props(name)
            self._info(f"'{name}' rig deleted (guides kept).")

    def _on_prop_delete_guides(self):
        name = self._prop()
        if name:
            with undo_chunk():
                prop_rig.delete_guides(name)
            self._refresh_props()
            self._info(f"'{name}' guides deleted.")

    # -----------------------------------------------------------------------
    # Slots: Cargo
    # -----------------------------------------------------------------------

    def _refresh_cargo_label(self):
        items = sorted(vehicle_cargo.list_cargo())
        baked = [i for i in items if vehicle_cargo.is_built(i)
                 and cmds.keyframe(vehicle_cargo.nodes(i)[0] + ".bakeRx",
                                   q=True, keyframeCount=True)]
        self.cargo_label.setText(
            ("Cargo: " + ", ".join(items[:8])
             + (f" and {len(items) - 8} more" if len(items) > 8 else "")
             + (f"  |  {len(baked)} baked." if baked else "  |  not baked."))
            if items else "No cargo yet.")

    def _cargo_selected_items(self):
        items = vehicle_cargo.list_cargo()
        by_mesh = {cmds.ls(m, long=True)[0]: i for i, m in items.items()}
        out = []
        for n in cmds.ls(sl=True, long=True, type="transform") or []:
            if n in by_mesh:
                out.append(by_mesh[n])
            short = n.split("|")[-1]
            if short.endswith("_cargo_CTRL"):
                out.append(short[:-len("_cargo_CTRL")])
        return sorted(set(out))

    def _on_cargo_add(self):
        if not cmds.objExists("VEHICLE_RIG_GRP"):
            cmds.warning("Build a vehicle rig first.")
            return
        try:
            with undo_chunk():
                added = vehicle_cargo.add_cargo()
        except RuntimeError as e:
            cmds.warning(str(e))
            return
        self._refresh_cargo_label()
        if added:
            self._info(f"Cargo: {', '.join(added)}. Drive or simulate (or "
                       f"Bake Cargo) to see them move.")
        else:
            cmds.warning("Select the cargo meshes first (each item its own "
                         "mesh).")

    def _on_cargo_remove(self):
        items = self._cargo_selected_items()
        if not items:
            cmds.warning("Select cargo meshes or their controls.")
            return
        with undo_chunk():
            for i in items:
                vehicle_cargo.remove_cargo(i)
        self._refresh_cargo_label()
        self._info(f"No longer cargo: {', '.join(items)}.")

    def _on_cargo_bake(self):
        if not vehicle_cargo.list_cargo():
            cmds.warning("Add some cargo first.")
            return
        with undo_chunk():
            done = vehicle_cargo.bake_cargo()
        self._refresh_cargo_label()
        self._info(f"Cargo baked: {', '.join(done)}. Press play." if done
                   else "Nothing to bake: the range is too short.")

    def _on_cargo_clear(self):
        with undo_chunk():
            n = vehicle_cargo.clear_bake()
        self._refresh_cargo_label()
        self._info("Cargo bake cleared." if n else "No cargo bake.")

    def _on_cargo_select(self):
        ctrls = [vehicle_cargo.nodes(i)[0] for i in vehicle_cargo.list_cargo()
                 if vehicle_cargo.is_built(i)]
        if ctrls:
            cmds.select(ctrls)
        else:
            cmds.warning("No cargo controls.")

    # -----------------------------------------------------------------------
    # Slots: Crash damage
    # -----------------------------------------------------------------------

    def _refresh_crash_label(self):
        soft = set(vehicle_crash.soft_obstacles())
        names = [n.split("|")[-1] + (" (dents)" if n in soft else "")
                 for n in vehicle_crash.obstacles()]
        dents = sum(len(cmds.getAttr(bs + ".weight", mi=True) or [])
                    for bs in vehicle_crash.damage_nodes())
        text = ("Obstacles: " + ", ".join(names[:8])
                + (f" and {len(names) - 8} more" if len(names) > 8 else "")
                if names else "No obstacles yet.")
        if dents:
            text += f"  |  {dents} dent(s) baked."
        self.crash_label.setText(text)

    def _on_crash_add(self):
        soft = self.cb_crash_soft.isChecked()
        with undo_chunk():
            added = vehicle_crash.add_obstacles(soft=soft)
        self._refresh_crash_label()
        if added:
            self._info(f"Obstacles: {', '.join(added)}"
                       + (" (they dent too)" if soft else "")
                       + ". Drive or simulate into them; the car stops and "
                         "dents.")
        else:
            cmds.warning("Select the wall / pole / barrier meshes first.")

    def _on_crash_remove(self):
        with undo_chunk():
            gone = vehicle_crash.remove_obstacles()
        self._refresh_crash_label()
        self._info(f"No longer obstacles: {', '.join(gone)}." if gone
                   else "None of the selected meshes were obstacles.")

    def _on_crash_bake(self):
        if not cmds.objExists("VEHICLE_RIG_GRP"):
            cmds.warning("Build a vehicle rig first.")
            return
        if not vehicle_crash.active():
            cmds.warning("Nothing to crash into: add obstacles (select "
                         "them, then Add Selected as Obstacles) or turn on "
                         "crashGround, and bind the car's meshes.")
            return

        def progress(frac):
            self._info(f"Baking crash damage... {int(frac * 100)}%")
            QtWidgets.QApplication.processEvents()

        with undo_chunk():
            s = vehicle_crash.bake_damage(progress=progress)
        self._refresh_crash_label()
        if s["dents"]:
            self._info(f"{s['dents']} dent(s) baked on "
                       f"{', '.join(s['panels'])}, first hit at frame "
                       f"{s['first_hit']}. Dial C_chassis_CTRL.crashDamage.")
        else:
            self._info("No part of the car went into an obstacle or the "
                       "ground, so nothing dented.")

    def _on_crash_clear(self):
        with undo_chunk():
            n = vehicle_crash.clear_damage()
        self._refresh_crash_label()
        self._info("Crash damage cleared." if n else "No crash damage.")

    def _on_vehicle_drive(self):
        if not vehicle_drive.can_drive():
            cmds.warning("No drivable vehicle rig. Build a vehicle rig "
                         "first.")
            return
        vehicle_drive.show()
        self._info("Drive panel open — click it, then WASD. Esc stops.")

    def _on_vehicle_bind(self):
        if not cmds.objExists("VEHICLE_RIG_GRP"):
            cmds.warning("Build a vehicle rig first.")
            return
        part = self.vehicle_part_combo.currentText()
        with undo_chunk():
            sc = vehicle_bind.bind_part(part)
        if sc:
            self._info(f"Bound selected mesh to {part}.")

    # -----------------------------------------------------------------------
    # Slot — Open Picker
    # -----------------------------------------------------------------------

    def _on_walk_mode(self):
        if not character_walk.can_walk():
            cmds.warning("No character with IK legs in the scene. Build a "
                         "biped, quadruped or bird rig first.")
            return
        character_walk.show()
        self._info("Walk panel open: click it, then WASD (Shift runs, Space "
                   "jumps). Esc stops and keys the walk.")

    def _on_fly_mode(self):
        if not character_fly.can_fly():
            cmds.warning("No wings in the scene. Build a bird, or a biped "
                         "with the Dragon creature preset, first.")
            return
        character_fly.show()
        self._info("Fly panel open: click it, then Space to take off. WASD "
                   "flies, Shift dives, hold S low down to land. Esc stops "
                   "and keys the flight.")

    # -----------------------------------------------------------------------
    # Slots: tail physics
    # -----------------------------------------------------------------------

    def _refresh_tails(self):
        current = self.tail_combo.currentText()
        self.tail_combo.blockSignals(True)
        self.tail_combo.clear()
        for prefix in chain_sim.find_chains():
            self.tail_combo.addItem(prefix)
        i = self.tail_combo.findText(current)
        self.tail_combo.setCurrentIndex(max(0, i))
        self.tail_combo.blockSignals(False)
        self._on_tail_picked()

    def _on_tail_picked(self, *args):
        """Show the picked tail's own dials (if it was baked before)."""
        prefix = self.tail_combo.currentText()
        ctrl = chain_sim.settings_ctrl(prefix) if prefix else None
        for attr, sp in self.tail_spins.items():
            if ctrl and cmds.objExists(ctrl) and cmds.attributeQuery(
                    attr, node=ctrl, exists=True):
                sp.setValue(cmds.getAttr("%s.%s" % (ctrl, attr)))
            else:
                sp.setValue(chain_sim.DEFAULTS[attr])

    def _on_tail_timeline(self):
        self.tail_start_spin.setValue(
            int(cmds.playbackOptions(q=True, min=True)))
        self.tail_end_spin.setValue(
            int(cmds.playbackOptions(q=True, max=True)))

    def _on_tail_bake(self):
        prefix = self.tail_combo.currentText()
        if not prefix:
            self._refresh_tails()
            prefix = self.tail_combo.currentText()
        if not prefix:
            cmds.warning("No tail in the scene. Build a rig with a tail "
                         "first.")
            return
        settings = {a: sp.value() for a, sp in self.tail_spins.items()}
        try:
            with undo_chunk():
                res = chain_sim.bake(prefix, self.tail_start_spin.value(),
                                     self.tail_end_spin.value(),
                                     settings=settings)
        except Exception as e:
            cmds.warning(f"Tail bake failed: {e}")
            return
        self._info("Tail baked: %s, %d frames (tip swings up to %.0f). "
                   "Dial it with %s.physics." % (
                       prefix, res["frames"], res["max_tip_swing"],
                       chain_sim.settings_ctrl(prefix)))

    def _on_tail_clear(self):
        prefix = self.tail_combo.currentText()
        if not prefix:
            return
        with undo_chunk():
            chain_sim.clear(prefix)
        self._info("Tail physics cleared: %s plays your animation." % prefix)

    def _on_tail_remove(self):
        prefix = self.tail_combo.currentText()
        if not prefix:
            return
        with undo_chunk():
            n = chain_sim.remove(prefix)
        self._info("Tail physics layer removed from %s (%d joints)."
                   % (prefix, n))

    def _on_open_picker(self):
        try:
            rig_picker.show()
            self._info("Picker opened.")
        except Exception as e:
            cmds.warning(f"Could not open picker: {e}")

    # -----------------------------------------------------------------------
    # Slots — Game export (Unreal / Unity)
    # -----------------------------------------------------------------------

    def _on_game_skeleton(self):
        """Reparent loose BIND chains under C_root_BIND_JNT."""
        if not self._rig_root():
            cmds.warning("No rig in scene. Build one first.")
            return
        with undo_chunk():
            n = rig_export.organize_for_game_export()
        self._info(f"Game skeleton ready — reparented {n} joint(s) "
                   f"under C_root_BIND_JNT.")

    def _export_options(self):
        return {"include_bendy": self.cb_export_bendy.isChecked(),
                "ground_root": self.cb_export_root.isChecked()}

    def _pick_fbx(self, title, default_name=""):
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, title, default_name, "FBX Files (*.fbx);;All Files (*)")
        if path and not path.lower().endswith(".fbx"):
            path += ".fbx"
        return path

    def _on_export_rig(self):
        """Pick a save path and export the skeleton + skinned meshes."""
        if not self._rig_root():
            cmds.warning("No rig in scene. Build one first.")
            return
        path = self._pick_fbx("Export Rig as FBX")
        if not path:
            return
        result = rig_export.export_rig_fbx(
            path, include_mesh=self.cb_export_mesh.isChecked(),
            **self._export_options())
        if result:
            self._info(f"Rig exported → {result}")

    def _on_export_use_timeline(self):
        self.export_start_spin.setValue(
            int(cmds.playbackOptions(q=True, min=True)))
        self.export_end_spin.setValue(
            int(cmds.playbackOptions(q=True, max=True)))

    def _export_range(self):
        start = self.export_start_spin.value()
        end = self.export_end_spin.value()
        if end <= start:
            cmds.warning("Set the end frame after the start frame.")
            return None
        return start, end

    def _on_export_animation(self):
        """Bake the frame range onto a clean skeleton and save it as FBX."""
        if not self._rig_root():
            cmds.warning("No rig in scene. Build one first.")
            return
        frames = self._export_range()
        if not frames:
            return
        clip = self.export_clip_edit.text().strip()
        path = self._pick_fbx("Export Animation as FBX",
                              (clip + ".fbx") if clip else "")
        if not path:
            return
        result = rig_export.export_animation_fbx(
            path, frames[0], frames[1],
            in_place=self.cb_export_inplace.isChecked(),
            take=clip or None, **self._export_options())
        if result:
            self._info(f"Animation exported → {result}")

    def _refresh_clips(self):
        self.clips_list.clear()
        for c in rig_export.list_clips():
            item = QtWidgets.QListWidgetItem(
                "%s   (%d to %d)" % (c["name"], c["start"], c["end"]))
            item.setData(QtCore.Qt.UserRole, c)
            self.clips_list.addItem(item)

    def _on_clip_picked(self, item, _previous=None):
        if not item:
            return
        c = item.data(QtCore.Qt.UserRole)
        self.export_clip_edit.setText(c["name"])
        self.export_start_spin.setValue(int(c["start"]))
        self.export_end_spin.setValue(int(c["end"]))

    def _on_clip_add(self):
        frames = self._export_range()
        if not frames:
            return
        try:
            with undo_chunk():
                rig_export.add_clip(self.export_clip_edit.text(), *frames)
        except ValueError as e:
            cmds.warning(f"Can't save the clip: {e}")
            return
        self._refresh_clips()
        self._info(f"Clip '{self.export_clip_edit.text().strip()}' saved "
                   f"({frames[0]} to {frames[1]}).")

    def _on_clip_remove(self):
        item = self.clips_list.currentItem()
        if not item:
            cmds.warning("Pick a clip in the list first.")
            return
        with undo_chunk():
            rig_export.remove_clip(item.data(QtCore.Qt.UserRole)["name"])
        self._refresh_clips()

    def _on_export_clips(self):
        if not self._rig_root():
            cmds.warning("No rig in scene. Build one first.")
            return
        if not rig_export.list_clips():
            cmds.warning("No clips yet: set a name and frames, then Save as "
                         "Clip.")
            return
        folder = QtWidgets.QFileDialog.getExistingDirectory(
            self, "Export All Clips to Folder")
        if not folder:
            return
        written = rig_export.export_clips(
            folder, in_place=self.cb_export_inplace.isChecked(),
            **self._export_options())
        self._info(f"Exported {len(written)} clip(s) to {folder}.")

    def _on_verify(self):
        if not cmds.objExists("CHARACTER_RIG_GRP"):
            cmds.warning("No rig to verify. Build one first.")
            return
        # If we don't have a CharacterRig instance handy (e.g. UI was relaunched
        # after a build), spin up a transient one just to use its diagnose().
        rig = self.rig or character_rig_builder.CharacterRig()
        rig.diagnose()
        self._info("Diagnostic printed to Script Editor — check joint deltas.")

    def _on_select_skin_joints(self):
        """Select every BIND_JNT in the rig — ready for NG Skin Tools or
        Maya's native bindSkin. Excludes FK/IK/DRV driver chains and any
        non-skinned helpers."""
        if not self._rig_root():
            cmds.warning("No rig in scene. Build one first.")
            return
        bind_joints = self._collect_bind_joints()
        if not bind_joints:
            cmds.warning("No BIND joints found under CHARACTER_RIG_GRP.")
            return
        add = self._shift_held()
        cmds.select(bind_joints, add=add) if add \
            else cmds.select(bind_joints, r=True)
        self._info(f"{'Added' if add else 'Selected'} {len(bind_joints)} "
                   f"BIND joints {'to the selection' if add else ''} "
                   f"— ready for skinning.")

    @staticmethod
    def _shift_held():
        """True if Shift is down — lets the select buttons ADD to the current
        selection (select meshes, then SHIFT-click joints = both selected)."""
        return bool(QtWidgets.QApplication.keyboardModifiers()
                    & QtCore.Qt.ShiftModifier)

    def _on_wire_mesh_vis(self):
        """Connect C_global_CTRL.meshVis to the scene's geo group .v.

        Re-runs the same lookup the CoreRig uses at build time. Use this
        when the mesh was imported AFTER the rig was built (so the
        connection couldn't happen during build)."""
        if not cmds.objExists("C_global_CTRL"):
            cmds.warning("No C_global_CTRL in scene. Build the rig first.")
            return
        candidates = ("geo", "Geo", "GEO", "geometry",
                      "Geometry", "GEOMETRY", "meshes", "MESHES")
        grp = None
        for c in candidates:
            if cmds.objExists(c):
                grp = c
                break
        if not grp:
            for top in (cmds.ls(assemblies=True) or []):
                if "geo" in top.lower():
                    grp = top
                    break
        if not grp:
            cmds.warning("No geo group found in the scene.")
            return

        src = "C_global_CTRL.meshVis"
        dst = f"{grp}.v"
        existing = cmds.listConnections(dst, s=True, d=False,
                                          plugs=True) or []
        if src in existing:
            self._info(f"meshVis already wired to {grp}.")
            return
        try:
            cmds.connectAttr(src, dst, f=True)
            self._info(f"meshVis wired to '{grp}'.")
        except Exception as e:
            cmds.warning(f"Wire failed: {e}")

    def _geo_meshes(self):
        """Every mesh transform under the character's geo group, or [].

        Lookup: exact common names (geo / Geo / GEO / geometry / meshes …)
        first, then any top-level transform with 'geo' in its name. Meshes
        are gathered recursively at whatever depth they're nested."""
        candidates = ("geo", "Geo", "GEO", "geometry",
                      "Geometry", "GEOMETRY", "meshes", "MESHES")
        grp = None
        for c in candidates:
            if cmds.objExists(c):
                grp = c
                break
        if not grp:
            for t in (cmds.ls(assemblies=True) or []):
                if "geo" in t.lower():
                    grp = t
                    break
        if not grp:
            return None, []
        descendants = cmds.listRelatives(grp, ad=True, type="transform",
                                          fullPath=False) or []
        meshes = [n for n in descendants
                  if cmds.listRelatives(n, s=True, type="mesh")]
        if cmds.listRelatives(grp, s=True, type="mesh"):
            meshes.append(grp)
        return grp, meshes

    def _on_select_geo(self):
        """Select every mesh under the geo group. Hold SHIFT to ADD them to
        the current selection instead of replacing it."""
        grp, meshes = self._geo_meshes()
        if not grp:
            cmds.warning("No 'geo' group found. Looked for exact names "
                         "(geo, Geo, GEO, geometry, meshes) and any top-"
                         "level transform with 'geo' in its name.")
            return
        if not meshes:
            cmds.warning(f"No mesh transforms found under '{grp}'.")
            return
        add = self._shift_held()
        cmds.select(meshes, add=add) if add else cmds.select(meshes, r=True)
        self._info(f"{'Added' if add else 'Selected'} {len(meshes)} mesh "
                   f"transforms from '{grp}'.")

    def _on_auto_skin_all(self):
        """One click: gather every geo mesh + every BIND joint (body + face)
        and Geodesic-Voxel bind them all. No manual selection needed."""
        if not self._rig_root():
            cmds.warning("No rig in scene. Build one first.")
            return
        grp, meshes = self._geo_meshes()
        if not meshes:
            cmds.warning("No meshes found in a 'geo' group. Import your mesh "
                         "into a group named 'geo' first.")
            return
        joints = self._collect_bind_joints()
        if not joints:
            cmds.warning("No BIND joints found under the rig.")
            return
        with undo_chunk():
            cmds.select(meshes, r=True)
            bound = self._bind_selected_to_joints(meshes, joints)
        if bound:
            self._info(f"Auto-skinned {bound}/{len(meshes)} geo mesh(es) to "
                       f"{len(joints)} BIND joints (body + face). Refine in "
                       f"NG Skin Tools or add Delta Mush.")

    def _on_smooth_bind(self):
        """Auto-skin selected meshes to all BIND joints via Geodesic Voxel.

        Geodesic Voxel (bindMethod=3) is Maya's topology-aware binder
        introduced in 2015 — it voxelizes the mesh, walks geodesic
        distances across the surface, and produces weights that respect
        mesh boundaries. This is the closest in-the-box equivalent to
        third-party auto-skin tools like GoSkinning / Brave Rabbit.

        Two-step bind: skinCluster creates the cluster + initial weights,
        then geomBind recomputes with explicit falloff and voxel
        resolution. (falloff is NOT a skinCluster flag — it lives on
        geomBind. The Maya 2023 help text confirms this.)
        """
        if not self._rig_root():
            cmds.warning("No rig in scene. Build one first.")
            return
        meshes = [n for n in (cmds.ls(sl=True, type="transform") or [])
                  if cmds.listRelatives(n, s=True, type="mesh")]
        if not meshes:
            cmds.warning("Select one or more mesh transforms first.")
            return
        bind_joints = self._collect_bind_joints()
        if not bind_joints:
            cmds.warning("No BIND joints found under CHARACTER_RIG_GRP.")
            return
        bound = self._bind_selected_to_joints(meshes, bind_joints)
        if bound:
            self._info(f"Geodesic Voxel auto-skinned {bound} mesh(es) to "
                       f"{len(bind_joints)} BIND joints. Refine in NG Skin "
                       f"Tools or add Delta Mush.")

    def _bind_selected_to_joints(self, meshes, bind_joints):
        """Geodesic-Voxel bind each mesh to all bind joints. Skips already-
        bound meshes; refines with geomBind (falloff 0.2, 256 voxels) when
        available. Returns how many were newly bound. Shared by the one-click
        Auto-Skin and the manual Auto-Skin (Geodesic Voxel) buttons."""
        bound = 0
        for mesh in meshes:
            existing = cmds.ls(cmds.listHistory(mesh) or [],
                               type="skinCluster")
            if existing:
                cmds.warning(f"{mesh} already has skinCluster ({existing[0]}) "
                             f"— skipping. Detach skin first to rebind.")
                continue
            try:
                sc = cmds.skinCluster(
                    bind_joints, mesh, toSelectedBones=True,
                    bindMethod=3, skinMethod=0, maximumInfluences=5,
                    obeyMaxInfluences=True, normalizeWeights=1,
                    weightDistribution=1, name=f"{mesh}_skinCluster")[0]
                bound += 1
            except Exception as e:
                cmds.warning(f"Failed to bind {mesh}: {e}")
                continue
            try:
                cmds.geomBind(sc, bindMethod=3, falloff=0.2, maxInfluences=5,
                              geodesicVoxelParams=(256, True))
            except Exception as e:
                cmds.warning(f"{mesh}: bound, but geomBind refine failed "
                             f"({e}). Initial voxel weights still applied.")
        return bound

    def _rig_root(self):
        """Return whichever rig top-group is in the scene — the biped
        CHARACTER_RIG_GRP, the QUADRUPED_RIG_GRP, or the BIRD_RIG_GRP —
        or None if none exists. Lets the skinning buttons work for any
        rig type."""
        for grp in ("CHARACTER_RIG_GRP", "QUADRUPED_RIG_GRP",
                     "BIRD_RIG_GRP", "VEHICLE_RIG_GRP"):
            if cmds.objExists(grp):
                return grp
        return None

    def _collect_bind_joints(self):
        """Return every BIND_JNT under whichever rig top-group exists —
        shared by Select Skinning Joints + Auto-Skin buttons. Works for
        both the biped and the quadruped rig."""
        root = self._rig_root()
        if not root:
            return []
        all_joints = cmds.listRelatives(root, ad=True, type="joint") or []
        return [j for j in all_joints if j.endswith("_BIND_JNT")]

    # ------------------------------------------------------------------------
    # NG Skin Tools 2 integration
    # ------------------------------------------------------------------------

    def _on_ng_init(self):
        """One-click auto-skin + NG Skin Tools setup. For each selected
        mesh: smooth-bind to all BIND joints (if not already bound),
        initialize NG Skin Tools layers on the skinCluster, then open the
        NG panel ready to paint. No separate Smooth Bind step needed."""
        # ---- Validate selection + rig ----
        meshes = []
        for node in cmds.ls(sl=True, type="transform") or []:
            if cmds.listRelatives(node, s=True, type="mesh"):
                meshes.append(node)
        if not meshes:
            cmds.warning("Select one or more mesh transforms first.")
            return
        if not self._rig_root():
            cmds.warning("No rig in scene. Build one first.")
            return
        bind_joints = self._collect_bind_joints()
        if not bind_joints:
            cmds.warning("No BIND joints found under CHARACTER_RIG_GRP.")
            return

        # ---- Check NG Skin Tools availability up front ----
        try:
            import ngSkinTools2.api as ng_api
        except ImportError:
            cmds.warning("NG Skin Tools 2 not found. Install it from "
                         "https://www.ngskintools.com/ and restart Maya.")
            return

        bound = 0   # newly smooth-bound
        ng_ok = 0   # NG layers initialized

        for mesh in meshes:
            # Step 1: ensure mesh has a skinCluster (smooth bind if missing)
            history = cmds.listHistory(mesh) or []
            skin_clusters = cmds.ls(history, type="skinCluster")
            if not skin_clusters:
                try:
                    sc = cmds.skinCluster(
                        bind_joints, mesh,
                        toSelectedBones=True,
                        bindMethod=3,            # 3 = Geodesic Voxel
                        skinMethod=0,            # 0 = classic linear
                        maximumInfluences=5,
                        obeyMaxInfluences=True,
                        normalizeWeights=1,
                        weightDistribution=1,    # 1 = neighbors
                        name=f"{mesh}_skinCluster",
                    )[0]
                    skin_clusters = [sc]
                    bound += 1
                except Exception as e:
                    cmds.warning(f"Geodesic Voxel bind failed on {mesh}: {e}")
                    continue
                # Tune falloff + voxel resolution (separate geomBind call —
                # falloff is NOT a skinCluster flag in Maya 2023).
                try:
                    cmds.geomBind(
                        sc, bindMethod=3, falloff=0.2, maxInfluences=5,
                        geodesicVoxelParams=(256, True),
                    )
                except Exception as e:
                    cmds.warning(f"{mesh}: geomBind refinement skipped ({e})")

            # Step 2: initialize NG layers on the skinCluster
            try:
                ng_api.init_layers(skin_clusters[0])
                ng_ok += 1
            except Exception as e:
                cmds.warning(f"NG init failed on {mesh}: {e}")

        if ng_ok == 0:
            return

        # Step 3: open the NG Skin Tools panel
        try:
            from ngSkinTools2 import ui as ng_ui
            ng_ui.open_ui()
        except Exception:
            try:
                import ngSkinTools2
                ngSkinTools2.open_ui()
            except Exception as e:
                cmds.warning(f"NG initialized but failed to open UI: {e}")

        msg = (f"Auto-skinned {bound} new mesh(es) + NG initialized on "
               f"{ng_ok} skinCluster(s). Paint away.")
        if bound == 0:
            msg = (f"NG initialized on {ng_ok} existing skinCluster(s). "
                   f"Paint away.")
        self._info(msg)

    # ------------------------------------------------------------------------
    # Delta Mush — non-destructive smoothing
    # ------------------------------------------------------------------------

    def _on_delta_mush(self):
        """Add a deltaMush deformer to every selected mesh transform.

        Delta Mush captures the rest-pose surface and constrains deformed
        positions to stay near that surface, smoothing out zigzag weight
        artifacts without flattening intended shapes. It's non-destructive
        — toggle envelope to 0 to bypass, delete the deformer to remove.

        Defaults (production-friendly):
          smoothingIterations = 10  more iterations = smoother
          smoothingStep       = 0.5 per-iteration weight (0..1)
          pinBorderVertices   = on  keeps mesh boundary stable
        """
        meshes = []
        for node in cmds.ls(sl=True, type="transform") or []:
            if cmds.listRelatives(node, s=True, type="mesh"):
                meshes.append(node)
        if not meshes:
            cmds.warning("Select one or more mesh transforms first.")
            return

        added = 0
        for mesh in meshes:
            # Don't stack multiple deltaMush deformers on one mesh
            existing = [n for n in (cmds.listHistory(mesh) or [])
                        if cmds.nodeType(n) == "deltaMush"]
            if existing:
                cmds.warning(f"{mesh} already has deltaMush "
                             f"({existing[0]}) — skipping.")
                continue
            try:
                cmds.deltaMush(
                    mesh,
                    smoothingIterations=10,
                    smoothingStep=0.5,
                    envelope=1.0,
                    pinBorderVertices=True,
                    inwardConstraint=0.0,
                    outwardConstraint=0.0,
                )
                added += 1
            except Exception as e:
                cmds.warning(f"Delta Mush failed on {mesh}: {e}")

        if added:
            self._info(f"Delta Mush applied to {added} mesh(es). "
                       f"Adjust iterations/step on the deltaMush node if "
                       f"the result is too soft.")

    # ------------------------------------------------------------------------
    # Rig finishing (pre-export): correctives, dynamics, validate
    # ------------------------------------------------------------------------

    def _on_add_correctives(self):
        if not cmds.ls("*_BIND_JNT", type="joint"):
            cmds.warning("Build a biped first — no _BIND_JNT joints found.")
            return
        try:
            with undo_chunk():
                made = rig_correctives.build_biped_correctives()
        except Exception as e:
            cmds.warning(f"Add correctives failed: {e}")
            return
        if made:
            self._info(f"Added {len(made)} volume correctives "
                       f"(shoulders/elbows/hips/knees). Bind the "
                       f"*_corrective_BIND_JNT joints and paint the crease.")
        else:
            cmds.warning("No correctives added — is this a built biped?")

    def _on_make_dynamic(self):
        sel = cmds.ls(sl=True, type="joint") or [
            s for s in (cmds.ls(sl=True) or [])
            if cmds.nodeType(s) == "joint"]
        if not sel:
            cmds.warning("Select a joint chain first (its root, or all its "
                         "joints) — tail, ponytail, ear, rope…")
            return
        try:
            with undo_chunk():
                res = rig_dynamics.make_dynamic_chain(
                    sel if len(sel) > 1 else sel[0])
        except Exception as e:
            cmds.warning(f"Make dynamic failed: {e}")
            return
        if res:
            self._info(f"Chain is dynamic — dials on {res['control']}. "
                       f"Play to let it settle, then bake the joints "
                       f"before export.")

    def _on_validate(self, fix=False):
        if fix:
            with undo_chunk():
                issues = rig_validate.validate(fix=True, verbose=True)
        else:
            issues = rig_validate.validate(fix=False, verbose=True)
        errs = sum(1 for i in issues if i.level == rig_validate.ERROR)
        warns = sum(1 for i in issues if i.level == rig_validate.WARN)
        if not issues:
            self._info("Rig health check: all clear — ready to ship. "
                       "(Full report in the Script Editor.)")
        elif errs:
            self._info(f"Rig health check: {errs} error(s), {warns} "
                       f"warning(s). Fix the errors before export — see "
                       f"the Script Editor for the report.")
        else:
            self._info(f"Rig health check: 0 errors, {warns} warning(s) "
                       f"(judgement calls). Good to ship — report in the "
                       f"Script Editor.")

    def _on_advanced_face(self):
        """Open the separate Advanced Face (mesh-conforming) window, passing
        the lid/lip joint counts from the spinners."""
        try:
            # Always open with the current code on disk — a long-lived
            # main window would otherwise hand out a stale window module.
            reload(advanced_face_ui)
            advanced_face_ui.show(
                lid_joints=self.lid_jnts_spin.value(),
                lip_joints=self.lip_jnts_spin.value())
            self._info(
                f"Advanced Face window opened — fit the whole eye / mouth "
                f"loops ({self.lid_jnts_spin.value()} lid, "
                f"{self.lip_jnts_spin.value()} lip joints). Build deletes the "
                f"standard eyelid/lip joints so they don't collide.")
        except Exception as e:
            cmds.warning(f"Could not open Advanced Face: {e}")

    def _on_face_capture(self):
        reload(face_shapes)
        reload(face_capture)
        face_capture.show()
        if face_shapes.has_face():
            self._info("Face Capture open: Start Tracker, relax your face and "
                       "Calibrate Neutral, then Record.")
        else:
            self._info("Face Capture open. Build a character with a face (and "
                       "the Advanced Face) to drive.")

    # ---- Skinning ----
    def _selected_mesh(self):
        sel = cmds.ls(sl=True, type="transform") or []
        meshes = [s for s in sel
                  if cmds.listRelatives(s, s=True, type="mesh", ni=True)]
        return meshes

    def _on_skin_bind(self):
        meshes = self._selected_mesh()
        if not meshes:
            cmds.warning("Select a polygon mesh to bind.")
            return
        if not rig_skin.bind_joints():
            cmds.warning("Build a rig first — no BIND joints to bind to.")
            return
        with undo_chunk():
            sc = rig_skin.bind_mesh(meshes[0])
        if sc:
            self._info(f"Bound '{meshes[0]}' to the skeleton.")

    def _on_skin_mirror(self, left_to_right):
        meshes = self._selected_mesh()
        if not meshes:
            cmds.warning("Select the skinned mesh to mirror.")
            return
        with undo_chunk():
            rig_skin.mirror_weights(meshes[0], left_to_right=left_to_right)
        self._info(f"Mirrored weights {'L→R' if left_to_right else 'R→L'} "
                   f"on '{meshes[0]}'.")

    def _on_skin_copy(self):
        meshes = self._selected_mesh()
        if len(meshes) < 2:
            cmds.warning("Select the SOURCE mesh then the TARGET mesh.")
            return
        with undo_chunk():
            rig_skin.copy_weights(meshes[0], meshes[1])
        self._info(f"Copied weights '{meshes[0]}' → '{meshes[1]}'.")

    def _skin_target(self):
        """(mesh, vert_indices_or_None) from the selection — a vertex
        selection wins (limits the op to it), else the selected mesh."""
        verts = cmds.filterExpand(cmds.ls(sl=True, fl=True) or [],
                                  sm=31) or []
        if verts:
            mesh = verts[0].split(".")[0]
            idx = [int(v.split("[")[1].rstrip("]")) for v in verts]
            return mesh, idx
        meshes = self._selected_mesh()
        return (meshes[0] if meshes else None), None

    def _on_skin_smooth(self):
        mesh, verts = self._skin_target()
        if not mesh:
            cmds.warning("Select a skinned mesh (or some of its vertices).")
            return
        with undo_chunk():
            n = rig_skin.smooth_weights(
                mesh, iterations=self.skin_smooth_spin.value(), verts=verts)
        if n:
            self._info("Smoothed %d vertices of %s (%d pass%s). Run it again "
                       "if a seam still shows." % (
                           n, mesh, self.skin_smooth_spin.value(),
                           "" if self.skin_smooth_spin.value() == 1 else "es"))

    def _on_skin_tidy(self):
        mesh, verts = self._skin_target()
        if not mesh:
            cmds.warning("Select a skinned mesh (or some of its vertices).")
            return
        with undo_chunk():
            n = rig_skin.tidy_weights(mesh, verts=verts)
        if n:
            self._info("Tidied %d vertices of %s: 4 joints each, normalised."
                       % (n, mesh))

    def _on_skin_gradient_auto(self):
        mesh, verts = self._skin_target()
        if not mesh:
            cmds.warning("Select a polygon mesh (or some of its vertices) "
                         "to gradient-skin.")
            return
        if not rig_skin.bind_joints():
            cmds.warning("Build a rig first — no BIND joints to skin to.")
            return
        with undo_chunk():
            rig_skin.gradient_skin_auto(mesh, verts=verts)
        self._info(f"Gradient-skinned '{mesh}' to the whole skeleton "
                   f"(bone falloff)" + (" — selected verts" if verts else "")
                   + ". Smooth it next if you like.")

    def _on_skin_gradient_chain(self):
        joints = cmds.ls(sl=True, type="joint") or []
        mesh, verts = self._skin_target()
        if len(joints) < 2 or not mesh:
            cmds.warning("Select the MESH (or its verts) + a CHAIN of 2+ "
                         "joints (root..tip), then click.")
            return
        with undo_chunk():
            rig_skin.gradient_skin_chain(mesh, joints, verts=verts)
        self._info(f"Gradient-skinned '{mesh}' along {len(joints)} joints "
                   f"(bone falloff).")

    def _on_skin_save(self):
        meshes = self._selected_mesh()
        if not meshes:
            cmds.warning("Select the skinned mesh to save weights for.")
            return
        path, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save Skin Weights", f"{meshes[0]}.xml",
            "Weight files (*.xml)")
        if not path:
            return
        folder, fname = os.path.split(path)
        name = fname[:-4] if fname.lower().endswith(".xml") else fname
        with undo_chunk():
            rig_skin.save_weights(meshes[0], folder, name=name)
        self._info(f"Saved weights for '{meshes[0]}'.")

    def _on_skin_load(self):
        meshes = self._selected_mesh()
        if not meshes:
            cmds.warning("Select the (re-bound) mesh to load weights onto.")
            return
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Load Skin Weights", "", "Weight files (*.xml)")
        if not path:
            return
        folder, fname = os.path.split(path)
        name = fname[:-4] if fname.lower().endswith(".xml") else fname
        with undo_chunk():
            rig_skin.load_weights(meshes[0], folder, name=name)
        self._info(f"Loaded weights onto '{meshes[0]}'.")

    # ---- Animation tools: space switching ----
    def _on_add_spaces(self):
        if not cmds.objExists("L_arm_IK_CTRL"):
            cmds.warning("Build a biped rig first (needs the IK controls).")
            return
        with undo_chunk():
            n = rig_space_switch.build_biped_spaces()
        self._info(
            f"Added space switches to {n} control(s). Select a hand/foot/"
            f"pole/head and set its 'space' in the channel box — or use "
            f"'Switch Selected (no jump)' below for a seamless change.")

    def _on_switch_space(self):
        space = self.space_combo.currentText()
        sel = cmds.ls(sl=True) or []
        if not sel:
            cmds.warning("Select the control(s) to switch first.")
            return
        with undo_chunk():
            n = rig_space_switch.switch_selected_to(space, key=True)
        if n:
            self._info(f"Switched {n} control(s) to {space} space "
                       f"(seamless + keyed).")

    # ---- Animation tools: pose library ----
    def _refresh_poses(self):
        cur = self.pose_combo.currentText()
        self.pose_combo.clear()
        poses = rig_pose_lib.list_poses()
        self.pose_combo.addItems(poses)
        if cur in poses:
            self.pose_combo.setCurrentText(cur)

    def _on_pose_save(self):
        name = (self.pose_name.text() or "").strip()
        if not name:
            cmds.warning("Type a pose name first.")
            return
        if not rig_pose_lib.all_controls():
            cmds.warning("Build a rig first — no controls to save.")
            return
        path = rig_pose_lib.save_pose(name)
        self._refresh_poses()
        self.pose_combo.setCurrentText(name)
        self._info(f"Saved pose '{name}'.")

    def _on_pose_apply(self, mirror=False, selected=False):
        name = self.pose_combo.currentText()
        if not name:
            cmds.warning("No saved pose selected.")
            return
        with undo_chunk():
            n = rig_pose_lib.apply_pose_file(
                name, selected_only=selected, mirror=mirror)
        how = ("to selection" if selected
               else ("mirrored" if mirror else "to whole rig"))
        self._info(f"Applied pose '{name}' {how} ({n} attrs).")

    def _on_pose_delete(self):
        name = self.pose_combo.currentText()
        if not name:
            return
        rig_pose_lib.delete_pose(name)
        self._refresh_poses()
        self._info(f"Deleted pose '{name}'.")

    def _on_pose_flip(self):
        if not rig_pose_lib.all_controls():
            cmds.warning("Build a rig first.")
            return
        with undo_chunk():
            n = rig_pose_lib.flip_pose_lr()
        self._info(f"Flipped the current pose L↔R ({n} controls).")


# =============================================================================
# Entry point
# =============================================================================

def _reload_submodules():
    """Re-read every rig submodule from disk.

    Maya keeps Python modules cached in sys.modules across a session.
    `import rig_guides` after the first time is a no-op, so any edits
    you make to `rig_guides.py` (or the other rig submodules) DON'T
    show up until somebody calls reload() on them.

    The module-level `reload(rig_guides)` etc. at the top of this file
    only fires when `rig_ui` itself reloads. Calling this from `show()`
    instead means EVERY time the user opens the picker UI, the latest
    code from disk gets pulled in — no more "I edited the file but
    the rig still uses the old behavior" gotchas.

    Does NOT reload `rig_ui` itself — Python can't reload the module
    that's currently executing. If you edit `rig_ui.py` you still need:
        from importlib import reload
        import rig_ui; reload(rig_ui); rig_ui.show()
    """
    for mod in (ft, rig_guides, character_rig_builder,
                quadruped_guides, quadruped_rig_builder,
                bird_guides, bird_rig_builder,
                vehicle_guides, vehicle_rig_builder, vehicle_drive, vehicle_sim, vehicle_parts,
                character_walk, character_fly, chain_sim,
                face_tracker, face_shapes, face_capture,
                advanced_face, advanced_face_ui,
                rig_export, rig_creature, rig_picker, rig_info):
        try:
            reload(mod)
        except Exception as e:
            cmds.warning(f"Could not reload {mod.__name__}: {e}")


def show():
    # Pull in the latest version of every submodule before building the
    # window so any .py edits the user made between launches show up.
    _reload_submodules()
    _close_existing(WINDOW_OBJECT_NAME)
    # Anonymous first-launch ping (once per version, opt-out, fully disclosed
    # in rig_telemetry.py + the README). Guarded — can never break the UI.
    try:
        import rig_telemetry
        rig_telemetry.ping("launch", once_per_version=True)
    except Exception:
        pass
    win = RigBuilderUI()
    win.show()
    return win
