"""
===============================================================================
 RIG UI - PySide2 panel for the modular character rig builder
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

from PySide2 import QtCore, QtWidgets
from shiboken2 import wrapInstance

import rig_guides
import character_rig_builder
import quadruped_guides
import quadruped_rig_builder
import bird_guides
import bird_rig_builder
import vehicle_guides
import vehicle_rig_builder
import vehicle_drive
import vehicle_bind
import raycast_ground
import rig_space_switch
import rig_pose_lib
import rig_skin
import advanced_face
import advanced_face_ui
import rig_export
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
reload(vehicle_bind)
reload(raycast_ground)
reload(rig_space_switch)
reload(rig_pose_lib)
reload(rig_skin)
reload(advanced_face)
reload(advanced_face_ui)
reload(rig_export)
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
        self.toggle.setStyleSheet(
            "QToolButton { border: none; font-weight: bold; padding: 5px 2px; "
            "color: #cfd3d8; text-align: left; }"
            "QToolButton:hover { color: #ffffff; }")
        self.toggle.setSizePolicy(QtWidgets.QSizePolicy.Expanding,
                                  QtWidgets.QSizePolicy.Fixed)
        self.toggle.toggled.connect(self._on_toggled)

        self.content = QtWidgets.QWidget()
        self.content.setVisible(expanded)
        self.content.setStyleSheet(
            "QWidget { border-left: 2px solid #3a3f44; }")

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
        self.setMinimumWidth(340)
        self.setWindowFlags(self.windowFlags() | QtCore.Qt.Window)

        self.guide_system = rig_guides.GuideSystem()
        self.rig = None
        # Quadruped (horse) workflow
        self.quad_guide_system = quadruped_guides.QuadGuideSystem()
        self.quad_rig = None
        # Bird (raptor) workflow
        self.bird_guide_system = bird_guides.BirdGuideSystem()
        self.bird_rig = None
        # Vehicle workflow
        self.vehicle_guide_system = vehicle_guides.VehicleGuideSystem()
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

        # ---------- RIG TYPE MODE SELECTOR ----------
        # Switching the mode shows that template's sections and hides the
        # other's, so the panel stays compact instead of stacking every
        # biped + quadruped option at once.
        mode_row = QtWidgets.QHBoxLayout()
        mode_lbl = QtWidgets.QLabel("Rig Type:")
        mode_lbl.setStyleSheet("QLabel { font-weight: bold; }")
        mode_row.addWidget(mode_lbl)
        self.mode_combo = QtWidgets.QComboBox()
        self.mode_combo.addItems(["Biped", "Quadruped", "Bird", "Vehicle"])
        mode_row.addWidget(self.mode_combo, 1)

        # Quick-launch the picker — always visible (independent of rig type).
        self.btn_open_picker = QtWidgets.QPushButton("Open Picker")
        self.btn_open_picker.setStyleSheet(
            "QPushButton { background: #3a4f6a; color: #cfe0ff; "
            "padding: 4px 10px; font-weight: bold; "
            "border-radius: 3px; }"
            "QPushButton:hover { background: #5c80b0; color: white; }"
        )
        self.btn_open_picker.setToolTip(
            "Open the anatomical ctrl picker window.")
        mode_row.addWidget(self.btn_open_picker)
        mode_row.addWidget(rig_info.make_info_button("open_picker"))
        root.addLayout(mode_row)

        # ---------- GUIDES SECTION (biped) ----------
        self.guides_box = QtWidgets.QGroupBox("Guides")
        gl = QtWidgets.QGridLayout(self.guides_box)
        gl.setSpacing(6)

        self.btn_create_guides = QtWidgets.QPushButton("Create Guides")
        self.btn_mirror = QtWidgets.QPushButton("Mirror L → R")
        self.btn_reset = QtWidgets.QPushButton("Reset to Defaults")
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
        gl.addWidget(self._with_info(self.btn_delete_guides, "delete_guides"),
                     3, 0, 1, 2)

        # Toggle the floating viewport name labels on every guide.
        # Drives the showLabels bool attr on RIG_GUIDES_GRP.
        self.btn_toggle_labels = QtWidgets.QPushButton(
            "Toggle Guide Labels")
        self.btn_toggle_labels.setToolTip(
            "Show / hide the floating text labels above each guide "
            "(like Advanced Skeleton). Toggles\n"
            "RIG_GUIDES_GRP.showLabels.")
        gl.addWidget(self.btn_toggle_labels, 4, 0, 1, 2)

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
        gl.addWidget(self.cb_symmetric, 5, 0, 1, 2)

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
            "QPushButton { color: #80c0ff; font-weight: bold; padding: 5px; }")
        self.btn_advanced_face.setToolTip(
            "Open the Advanced Face window — fit eyelid + lip joints to your\n"
            "actual mesh edge loops (AdvancedSkeleton-style), with as many\n"
            "joints as the spinners below. On Build Advanced Face the\n"
            "standard eyelid/lip joints are deleted so they don't collide.\n"
            "The simple locator face is unchanged.")
        face_vlayout.addWidget(self.btn_advanced_face)

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
        self.btn_build.setStyleSheet(
            "QPushButton { font-weight: bold; padding: 6px; }"
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
            "QPushButton { padding: 7px; font-weight: bold; color: #8fe28f; }")
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
        self.btn_ng_init.setStyleSheet("QPushButton { color: #80c0ff; }")
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

        # ---------- QUADRUPED (HORSE) SECTION ----------
        self.quad_box = QtWidgets.QGroupBox("Quadruped (Horse)")
        ql = QtWidgets.QGridLayout(self.quad_box)
        ql.setSpacing(6)

        self.btn_quad_create_guides = QtWidgets.QPushButton(
            "Create Horse Guides")
        self.btn_quad_mirror = QtWidgets.QPushButton("Mirror L → R")
        self.btn_quad_reset = QtWidgets.QPushButton("Reset to Defaults")
        self.btn_quad_delete_guides = QtWidgets.QPushButton("Delete Guides")
        self.btn_quad_build = QtWidgets.QPushButton("Build Horse Rig")
        self.btn_quad_build.setStyleSheet(
            "QPushButton { font-weight: bold; padding: 6px; }")
        self.btn_quad_delete_rig = QtWidgets.QPushButton("Delete Horse Rig")

        # Source toggle — build from guides or from baked-in defaults.
        self.rb_quad_from_guides = QtWidgets.QRadioButton("From guides")
        self.rb_quad_from_defaults = QtWidgets.QRadioButton("From defaults")
        self.rb_quad_from_guides.setChecked(True)

        ql.addWidget(self._with_info(self.btn_quad_create_guides,
                                       "quad_create_guides"),
                     0, 0, 1, 2)
        ql.addWidget(self._with_info(self.btn_quad_mirror, "quad_mirror"),
                     1, 0)
        ql.addWidget(self.btn_quad_reset,         1, 1)
        ql.addWidget(self.btn_quad_delete_guides, 2, 0, 1, 2)
        ql.addWidget(self.rb_quad_from_guides,    3, 0)
        ql.addWidget(self.rb_quad_from_defaults,  3, 1)
        ql.addWidget(self._with_info(self.btn_quad_build, "quad_build"),
                     4, 0)
        ql.addWidget(self.btn_quad_delete_rig,    4, 1)

        root.addWidget(self.quad_box)

        # ---------- BIRD (raptor) SECTION ----------
        self.bird_box = QtWidgets.QGroupBox("Bird (Raptor)")
        bdl = QtWidgets.QGridLayout(self.bird_box)
        bdl.setSpacing(6)

        self.btn_bird_create_guides = QtWidgets.QPushButton(
            "Create Raptor Guides")
        self.btn_bird_mirror = QtWidgets.QPushButton("Mirror L → R")
        self.btn_bird_reset = QtWidgets.QPushButton("Reset to Defaults")
        self.btn_bird_delete_guides = QtWidgets.QPushButton("Delete Guides")
        self.btn_bird_build = QtWidgets.QPushButton("Build Raptor Rig")
        self.btn_bird_build.setStyleSheet(
            "QPushButton { font-weight: bold; padding: 6px; }")
        self.btn_bird_delete_rig = QtWidgets.QPushButton("Delete Raptor Rig")

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
        bdl.addWidget(self.btn_bird_delete_guides, 2, 0, 1, 2)
        bdl.addWidget(self.rb_bird_from_guides,    3, 0)
        bdl.addWidget(self.rb_bird_from_defaults,  3, 1)
        bdl.addWidget(self._with_info(self.btn_bird_build, "bird_build"),
                      4, 0)
        bdl.addWidget(self.btn_bird_delete_rig,    4, 1)

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
        self.btn_vehicle_reset_guides = QtWidgets.QPushButton(
            "Reset Guides")
        self.btn_vehicle_delete_guides = QtWidgets.QPushButton(
            "Delete Guides")

        self.btn_vehicle_build = QtWidgets.QPushButton("Build Vehicle Rig")
        self.btn_vehicle_build.setStyleSheet(
            "QPushButton { font-weight: bold; padding: 6px; }")
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
        self.cb_vehicle_spring = QtWidgets.QCheckBox("Coil-over springs")
        self.cb_vehicle_spring.setChecked(True)
        self.cb_vehicle_ctrls = QtWidgets.QCheckBox("Per-spoke ctrls")
        self.cb_vehicle_ctrls.setChecked(True)

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
        self.btn_vehicle_drive.setStyleSheet(
            "QPushButton { background: #3a4f6a; color: #cfe0ff; "
            "padding: 6px; font-weight: bold; border-radius: 3px; }"
            "QPushButton:hover { background: #5c80b0; color: white; }")
        self.btn_vehicle_drive.setToolTip(
            "Open the live drive panel. W/S accelerate+brake, A/D steer,\n"
            "Esc stops. Every frame is keyframed — drive around, stop,\n"
            "and you have the animation with wheels spinning, steering,\n"
            "and tires deforming. Assign a ground mesh first for terrain.")

        spoke_row = QtWidgets.QHBoxLayout()
        spoke_row.addWidget(QtWidgets.QLabel("Spokes:"))
        spoke_row.addWidget(self.vehicle_spoke_spin)
        spoke_row.addWidget(self.cb_vehicle_spring)
        spoke_row.addWidget(self.cb_vehicle_ctrls)
        spoke_row.addStretch(1)

        src_row = QtWidgets.QHBoxLayout()
        src_row.addWidget(self.rb_vehicle_from_guides)
        src_row.addWidget(self.rb_vehicle_from_defaults)
        src_row.addStretch(1)

        vl.addWidget(self.btn_vehicle_create_guides,  0, 0, 1, 2)
        vl.addWidget(self.btn_vehicle_reset_guides,   1, 0)
        vl.addWidget(self.btn_vehicle_delete_guides,  1, 1)
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
            "Body / Door / Hood / Trunk / Rim / Spring = single rigid bone.")
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

        root.addWidget(self.vehicle_box)

        # ---------- GAME EXPORT SECTION (always visible) ----------
        # FBX export for Unreal / Unity. Works across all rig types
        # because they all share C_root_BIND_JNT as the top of the
        # BIND skeleton. Stays visible regardless of which Rig Type
        # the dropdown is on — animators bake + export from any mode.
        self.export_box = CollapsibleBox("Game Export (Unreal / Unity)")
        el = QtWidgets.QGridLayout()
        el.setSpacing(6)

        self.btn_game_skeleton = QtWidgets.QPushButton(
            "Make Game Skeleton (root + clean hierarchy)")
        self.btn_game_skeleton.setStyleSheet(
            "QPushButton { padding: 4px; }")
        self.btn_game_skeleton.setToolTip(
            "Reparent every loose BIND chain (pelvis, chest, etc.) under\n"
            "C_root_BIND_JNT so the skeleton has a single root for FBX\n"
            "export. Safe to run anytime after Build — does not break\n"
            "the visual rig.")

        self.btn_export_rig = QtWidgets.QPushButton(
            "Export Rig (.fbx)…")
        self.btn_export_rig.setStyleSheet(
            "QPushButton { padding: 6px; font-weight: bold; }")
        self.btn_export_rig.setToolTip(
            "Export the BIND skeleton + skinned mesh as an FBX file\n"
            "ready for Unreal / Unity. Picks a save path; runs the\n"
            "Game Skeleton step first so the hierarchy is clean.")

        self.btn_export_anim = QtWidgets.QPushButton(
            "Export Animation (.fbx)…")
        self.btn_export_anim.setStyleSheet(
            "QPushButton { padding: 6px; font-weight: bold; }")
        self.btn_export_anim.setToolTip(
            "Bake every BIND joint's animation over the current\n"
            "playback range, then export as FBX (animation-only).\n"
            "Use this for game takes / cycles.")

        # Include-bendy toggle — most engines don't want the ribbon
        # bendy joints, but offer it for high-fidelity exports.
        self.cb_export_bendy = QtWidgets.QCheckBox(
            "Include ribbon bendy joints")
        self.cb_export_bendy.setChecked(False)
        self.cb_export_bendy.setToolTip(
            "If checked, ribbon-driven bendy joints (spine_NN, arm/leg\n"
            "upper_bendy_NN, etc.) are included in the export. Off by\n"
            "default — bendy joints are driven by follicles and most\n"
            "real-time engines prefer the cleaner anchor skeleton.")

        # Include-mesh toggle for the rig export.
        self.cb_export_mesh = QtWidgets.QCheckBox("Include mesh (rig only)")
        self.cb_export_mesh.setChecked(True)
        self.cb_export_mesh.setToolTip(
            "Include the geo group in the rig export so the engine gets\n"
            "the skinned mesh + skeleton in one file. Animation export\n"
            "always excludes the mesh.")

        el.addWidget(self._with_info(self.btn_game_skeleton,
                                       "game_skeleton"),
                     0, 0, 1, 2)
        el.addWidget(self.cb_export_bendy,   1, 0)
        el.addWidget(self.cb_export_mesh,    1, 1)
        el.addWidget(self._with_info(self.btn_export_rig, "export_rig"),
                     2, 0)
        el.addWidget(self._with_info(self.btn_export_anim, "export_anim"),
                     2, 1)

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

        self.btn_skin_grad_auto = QtWidgets.QPushButton(
            "Gradient Skin: All Bones  (one-click falloff)")
        self.btn_skin_grad_auto.setStyleSheet(
            "QPushButton { color: #9fe09f; }")
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
        exp_lbl = QtWidgets.QLabel("⚠  Experimental  —  gradient bone-falloff "
                                   "(smooth it after)")
        exp_lbl.setStyleSheet(
            "QLabel { color: #e0a040; font-weight: bold; padding-top: 6px; "
            "border-top: 1px solid #555; }")

        sl.addWidget(self.btn_skin_bind,       0, 0, 1, 2)
        sl.addWidget(self.btn_skin_mirror_lr,  1, 0)
        sl.addWidget(self.btn_skin_mirror_rl,  1, 1)
        sl.addWidget(self.btn_skin_copy,       2, 0, 1, 2)
        sl.addWidget(exp_lbl,                  3, 0, 1, 2)
        sl.addWidget(self.btn_skin_grad_auto,  4, 0, 1, 2)
        sl.addWidget(self.btn_skin_grad_chain, 5, 0, 1, 2)
        sl.addWidget(self.btn_skin_save,       6, 0)
        sl.addWidget(self.btn_skin_load,       6, 1)
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
        pose_hdr.setStyleSheet("QLabel { color: #8ab; font-weight: bold; "
                               "padding-top: 4px; }")
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

        # ---------- STATUS ----------
        self.status = QtWidgets.QLabel("")
        self.status.setStyleSheet(
            "QLabel { color: #aaa; padding: 4px; "
            "border-top: 1px solid #444; }"
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
        Quadruped → Quadruped (Horse) box visible.
        Bird      → Bird (Raptor) box visible.
        Vehicle   → Vehicle box visible.
        """
        mode = self.mode_combo.currentText()
        is_biped   = (mode == "Biped")
        is_quad    = (mode == "Quadruped")
        is_bird    = (mode == "Bird")
        is_vehicle = (mode == "Vehicle")
        self.guides_box.setVisible(is_biped)
        self.build_box.setVisible(is_biped)
        self.quad_box.setVisible(is_quad)
        self.bird_box.setVisible(is_bird)
        self.vehicle_box.setVisible(is_vehicle)
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
        self.btn_open_picker.clicked.connect(self._on_open_picker)
        self.btn_create_guides.clicked.connect(self._on_create_guides)
        self.btn_mirror.clicked.connect(self._on_mirror)
        self.btn_reset.clicked.connect(self._on_reset)
        self.btn_save.clicked.connect(self._on_save)
        self.btn_load.clicked.connect(self._on_load)
        self.btn_delete_guides.clicked.connect(self._on_delete_guides)
        self.btn_toggle_labels.clicked.connect(self._on_toggle_labels)
        self.cb_symmetric.toggled.connect(self._on_symmetric_toggled)
        self.btn_build.clicked.connect(self._on_build)
        self.btn_delete_rig.clicked.connect(self._on_delete_rig)
        self.btn_verify.clicked.connect(self._on_verify)
        self.btn_select_skin_jnts.clicked.connect(self._on_select_skin_joints)
        self.btn_smooth_bind.clicked.connect(self._on_smooth_bind)
        self.btn_select_geo.clicked.connect(self._on_select_geo)
        self.btn_wire_mesh_vis.clicked.connect(self._on_wire_mesh_vis)
        self.btn_ng_init.clicked.connect(self._on_ng_init)
        self.btn_delta_mush.clicked.connect(self._on_delta_mush)
        self.btn_auto_skin_all.clicked.connect(self._on_auto_skin_all)
        self.btn_advanced_face.clicked.connect(self._on_advanced_face)
        # Skinning
        self.btn_skin_bind.clicked.connect(self._on_skin_bind)
        self.btn_skin_mirror_lr.clicked.connect(
            lambda: self._on_skin_mirror(True))
        self.btn_skin_mirror_rl.clicked.connect(
            lambda: self._on_skin_mirror(False))
        self.btn_skin_copy.clicked.connect(self._on_skin_copy)
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
        # Quadruped (horse)
        self.btn_quad_create_guides.clicked.connect(
            self._on_quad_create_guides)
        self.btn_quad_mirror.clicked.connect(self._on_quad_mirror)
        self.btn_quad_reset.clicked.connect(self._on_quad_reset)
        self.btn_quad_delete_guides.clicked.connect(
            self._on_quad_delete_guides)
        self.btn_quad_build.clicked.connect(self._on_quad_build)
        self.btn_quad_delete_rig.clicked.connect(self._on_quad_delete_rig)
        # Bird (raptor)
        self.btn_bird_create_guides.clicked.connect(
            self._on_bird_create_guides)
        self.btn_bird_mirror.clicked.connect(self._on_bird_mirror)
        self.btn_bird_reset.clicked.connect(self._on_bird_reset)
        self.btn_bird_delete_guides.clicked.connect(
            self._on_bird_delete_guides)
        self.btn_bird_build.clicked.connect(self._on_bird_build)
        self.btn_bird_delete_rig.clicked.connect(self._on_bird_delete_rig)
        # Vehicle
        self.btn_vehicle_create_guides.clicked.connect(
            self._on_vehicle_create_guides)
        self.btn_vehicle_reset_guides.clicked.connect(
            self._on_vehicle_reset_guides)
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
        self.btn_vehicle_bind.clicked.connect(self._on_vehicle_bind)
        # Game export
        self.btn_game_skeleton.clicked.connect(self._on_game_skeleton)
        self.btn_export_rig.clicked.connect(self._on_export_rig)
        self.btn_export_anim.clicked.connect(self._on_export_animation)

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
            msgs.append("Horse: " + "/".join(quad))
        self.status.setText("   |   ".join(msgs))

    def _info(self, text):
        print(f"[RigBuilderUI] {text}")
        self._refresh_status()

    # -----------------------------------------------------------------------
    # Slots — Guides
    # -----------------------------------------------------------------------

    def _on_create_guides(self):
        try:
            with undo_chunk():
                self.guide_system.build()
            self._info("Guides created.")
        except Exception as e:
            cmds.warning(f"Guide creation failed: {e}")

    def _on_mirror(self):
        with undo_chunk():
            self.guide_system.mirror_left_to_right()
        self._info("Mirrored L → R.")

    def _on_reset(self):
        with undo_chunk():
            self.guide_system.reset_to_defaults()
        self._info("Guides reset.")

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

    def _on_delete_rig(self):
        if not cmds.objExists("CHARACTER_RIG_GRP"):
            cmds.warning("No rig to delete.")
            return
        cmds.delete("CHARACTER_RIG_GRP")
        self.rig = None
        self._info("Rig deleted.")

    # -----------------------------------------------------------------------
    # Slots — Quadruped (horse)
    # -----------------------------------------------------------------------

    def _on_quad_create_guides(self):
        try:
            with undo_chunk():
                self.quad_guide_system.build()
            self._info("Horse guides created.")
        except Exception as e:
            cmds.warning(f"Horse guide creation failed: {e}")

    def _on_quad_mirror(self):
        with undo_chunk():
            self.quad_guide_system.mirror_left_to_right()
        self._info("Horse guides mirrored L → R.")

    def _on_quad_reset(self):
        with undo_chunk():
            self.quad_guide_system.reset_to_defaults()
        self._info("Horse guides reset to defaults.")

    def _on_quad_delete_guides(self):
        with undo_chunk():
            self.quad_guide_system.delete()
        self._info("Horse guides deleted.")

    def _on_quad_build(self):
        if cmds.objExists("QUADRUPED_RIG_GRP"):
            cmds.warning("A horse rig already exists. "
                         "Delete it before rebuilding.")
            return
        positions = None
        if self.rb_quad_from_guides.isChecked():
            if not self.quad_guide_system.exists():
                cmds.warning("No horse guides found. Create guides first "
                             "or pick 'From defaults'.")
                return
            positions = self.quad_guide_system.read_positions()
        # One undo chunk for the whole horse build.
        cmds.undoInfo(openChunk=True)
        try:
            self.quad_rig = quadruped_rig_builder.QuadrupedRig(
                positions=positions,
            )
            self.quad_rig.build()
            self._info("Horse rig built.")
        except Exception as e:
            cmds.warning(f"Horse rig build failed: {e}")
            raise
        finally:
            cmds.undoInfo(closeChunk=True)

    def _on_quad_delete_rig(self):
        if not cmds.objExists("QUADRUPED_RIG_GRP"):
            cmds.warning("No horse rig to delete.")
            return
        cmds.delete("QUADRUPED_RIG_GRP")
        self.quad_rig = None
        self._info("Horse rig deleted.")

    # -----------------------------------------------------------------------
    # Slots — Bird (raptor)
    # -----------------------------------------------------------------------

    def _on_bird_create_guides(self):
        try:
            with undo_chunk():
                self.bird_guide_system.build()
            self._info("Raptor guides created.")
        except Exception as e:
            cmds.warning(f"Raptor guide creation failed: {e}")

    def _on_bird_mirror(self):
        with undo_chunk():
            self.bird_guide_system.mirror_left_to_right()
        self._info("Raptor guides mirrored L → R.")

    def _on_bird_reset(self):
        with undo_chunk():
            self.bird_guide_system.reset_to_defaults()
        self._info("Raptor guides reset to defaults.")

    def _on_bird_delete_guides(self):
        with undo_chunk():
            self.bird_guide_system.delete()
        self._info("Raptor guides deleted.")

    def _on_bird_build(self):
        if cmds.objExists("BIRD_RIG_GRP"):
            cmds.warning("A raptor rig already exists. "
                         "Delete it before rebuilding.")
            return
        positions = None
        if self.rb_bird_from_guides.isChecked():
            if not self.bird_guide_system.exists():
                cmds.warning("No raptor guides found. Create guides first "
                             "or pick 'From defaults'.")
                return
            positions = self.bird_guide_system.read_positions()
        # One undo chunk for the whole raptor build.
        cmds.undoInfo(openChunk=True)
        try:
            self.bird_rig = bird_rig_builder.BirdRig(
                positions=positions,
            )
            self.bird_rig.build()
            self._info("Raptor rig built.")
        except Exception as e:
            cmds.warning(f"Raptor rig build failed: {e}")
            raise
        finally:
            cmds.undoInfo(closeChunk=True)

    def _on_bird_delete_rig(self):
        if not cmds.objExists("BIRD_RIG_GRP"):
            cmds.warning("No raptor rig to delete.")
            return
        cmds.delete("BIRD_RIG_GRP")
        self.bird_rig = None
        self._info("Raptor rig deleted.")

    # -----------------------------------------------------------------------
    # Slots — Vehicle
    # -----------------------------------------------------------------------

    def _on_vehicle_create_guides(self):
        try:
            with undo_chunk():
                self.vehicle_guide_system.build()
            self._info("Car guides created (left + center). Place them, "
                       "then Build From Guides — right side mirrors.")
        except Exception as e:
            cmds.warning(f"Car guide creation failed: {e}")

    def _on_vehicle_reset_guides(self):
        with undo_chunk():
            self.vehicle_guide_system.reset_to_defaults()
        self._info("Car guides reset to defaults.")

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
        cmds.undoInfo(openChunk=True)
        try:
            self.vehicle_rig = vehicle_rig_builder.VehicleRig(
                positions=positions,
                spoke_count=self.vehicle_spoke_spin.value(),
                build_spoke_ctrls=self.cb_vehicle_ctrls.isChecked(),
            )
            # Coil-over springs: gate by the checkbox (toggle the
            # 'suspension' module which drives the spring build).
            if not self.cb_vehicle_spring.isChecked():
                self.vehicle_rig.modules.discard("suspension")
            self.vehicle_rig.build()
            self._info("Vehicle rig built.")
        except Exception as e:
            cmds.warning(f"Vehicle rig build failed: {e}")
            raise
        finally:
            cmds.undoInfo(closeChunk=True)

    def _on_vehicle_delete_rig(self):
        if not cmds.objExists("VEHICLE_RIG_GRP"):
            cmds.warning("No vehicle rig to delete.")
            return
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

    def _on_export_rig(self):
        """Pick a save path and export the rig (skeleton + skinning)."""
        if not self._rig_root():
            cmds.warning("No rig in scene. Build one first.")
            return
        filepath, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Export Rig as FBX", "",
            "FBX Files (*.fbx);;All Files (*)",
        )
        if not filepath:
            return   # user cancelled
        if not filepath.lower().endswith(".fbx"):
            filepath += ".fbx"
        include_mesh  = self.cb_export_mesh.isChecked()
        include_bendy = self.cb_export_bendy.isChecked()
        try:
            result = rig_export.export_rig_fbx(
                filepath, include_mesh=include_mesh,
                include_bendy=include_bendy,
            )
        except Exception as e:
            cmds.warning(f"Export failed: {e}")
            return
        if result:
            self._info(f"Rig exported → {result}")

    def _on_export_animation(self):
        """Pick a save path, bake the playback range, export FBX (anim only)."""
        if not self._rig_root():
            cmds.warning("No rig in scene. Build one first.")
            return
        filepath, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Export Animation as FBX", "",
            "FBX Files (*.fbx);;All Files (*)",
        )
        if not filepath:
            return
        if not filepath.lower().endswith(".fbx"):
            filepath += ".fbx"
        include_bendy = self.cb_export_bendy.isChecked()
        try:
            result = rig_export.export_animation_fbx(
                filepath, include_bendy=include_bendy,
            )
        except Exception as e:
            cmds.warning(f"Export failed: {e}")
            return
        if result:
            self._info(f"Animation exported → {result}")

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
    for mod in (rig_guides, character_rig_builder,
                quadruped_guides, quadruped_rig_builder,
                bird_guides, bird_rig_builder,
                vehicle_guides, vehicle_rig_builder, vehicle_drive,
                advanced_face_ui,
                rig_export, rig_picker, rig_info):
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
