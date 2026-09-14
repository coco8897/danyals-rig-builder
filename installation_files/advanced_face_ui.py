"""
===============================================================================
 ADVANCED FACE — separate window (keeps the main UI uncluttered)
===============================================================================

 Launch:
     import advanced_face_ui
     from importlib import reload; reload(advanced_face_ui)
     advanced_face_ui.show()

 Or from the main panel's "Advanced Face…" button.

 Workflow:
   1. Select the WHOLE eye (or mouth) edge loop on your mesh (right-click →
      Edge → double-click a loop edge to grab the whole closed loop).
   2. Click Fit Eye L / Fit Eye R / Fit Mouth — the loop is auto-split into
      its upper + lower halves and captured (button turns green).
   3. Build Advanced Face.
===============================================================================
"""

import maya.cmds as cmds
import maya.OpenMayaUI as omui
try:
    from PySide2 import QtCore, QtWidgets
    from shiboken2 import wrapInstance
except ImportError:                                  # Maya 2025+ (Qt6)
    from PySide6 import QtCore, QtWidgets
    from shiboken6 import wrapInstance

import advanced_face
import forge_theme as ft
import rig_face_presets
from importlib import reload as _reload
_reload(advanced_face)
_reload(rig_face_presets)


WINDOW_OBJECT_NAME = "DanyalAdvancedFaceWindow"


def _maya_main_window():
    ptr = omui.MQtUtil.mainWindow()
    return wrapInstance(int(ptr), QtWidgets.QWidget)


# Whole-loop features: select the ENTIRE eye / mouth loop and the tool
# auto-splits it into the upper + lower halves.
#   (feature_id, button label, [regions it fills], is_outer_crease)
_EYE_FEATURES = [
    ("eyeL", "Eye  L   (lash line, whole loop)",
     ["L_lidUpper", "L_lidLower"], False),
    ("eyeLOuter", "Eye  L   crease (outer loop, optional)",
     ["L_lidUpperOuter", "L_lidLowerOuter"], True),
    ("eyeR", "Eye  R   (lash line, whole loop)",
     ["R_lidUpper", "R_lidLower"], False),
    ("eyeROuter", "Eye  R   crease (outer loop, optional)",
     ["R_lidUpperOuter", "R_lidLowerOuter"], True),
]
_MOUTH_FEATURE = ("mouth", "Mouth   (whole loop)", ["C_lipUpper", "C_lipLower"])


class AdvancedFaceUI(QtWidgets.QDialog):

    def __init__(self, parent=None, lid_joints=None, lip_joints=None):
        super(AdvancedFaceUI, self).__init__(parent or _maya_main_window())
        self.setObjectName(WINDOW_OBJECT_NAME)
        self.setWindowTitle("Advanced Face (mesh-conforming)")
        self.setStyleSheet(ft.style(ft.ACCENT_RIG))
        self.setWindowFlags(self.windowFlags() | QtCore.Qt.Window)
        self.setMinimumWidth(320)

        # One AdvancedFace model, loading any saved fits from the scene.
        # lid/lip joint counts come from the main panel's spinners so the
        # animator can crank up the detail.
        self.face = advanced_face.AdvancedFace(
            lid_joints=lid_joints, lip_joints=lip_joints)
        self.face._load_fits()

        self._fit_buttons = {}      # feature_id -> button
        self._fit_rows = {}         # feature_id -> row widget (hide on sym)
        self._feature_regions = {}  # feature_id -> [regions]
        self._build_ui()
        self._refresh_fit_states()

    # -----------------------------------------------------------------------

    def _build_ui(self):
        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(10, 10, 10, 10)
        root.setSpacing(8)

        _title = QtWidgets.QLabel("ADVANCED FACE")
        _title.setObjectName("title")
        root.addWidget(_title)
        _sub = QtWidgets.QLabel("mesh-conforming eyes + mouth")
        _sub.setObjectName("subtitle")
        root.addWidget(_sub)

        intro = QtWidgets.QLabel(
            "Mesh-conforming heavy face. Select the WHOLE eye (or mouth) "
            "edge loop on your mesh, then click Fit — it auto-splits the "
            "loop into the upper + lower halves. Fitted features turn green. "
            "Build when ready.")
        intro.setWordWrap(True)
        intro.setStyleSheet("QLabel { color: %s; }" % ft.MUTED)
        root.addWidget(intro)

        # Head / jaw joint fields (the detail joints constrain to these).
        jrow = QtWidgets.QGridLayout()
        jrow.addWidget(QtWidgets.QLabel("Head joint:"), 0, 0)
        self.head_field = QtWidgets.QLineEdit("C_head_BIND_JNT")
        jrow.addWidget(self.head_field, 0, 1)
        jrow.addWidget(QtWidgets.QLabel("Jaw joint:"), 1, 0)
        self.jaw_field = QtWidgets.QLineEdit("C_jaw_BIND_JNT")
        jrow.addWidget(self.jaw_field, 1, 1)
        jrow.addWidget(QtWidgets.QLabel("Blink height:"), 2, 0)
        self.blink_height_spin = QtWidgets.QDoubleSpinBox()
        self.blink_height_spin.setRange(0.0, 1.0)
        self.blink_height_spin.setSingleStep(0.05)
        self.blink_height_spin.setValue(0.1)
        self.blink_height_spin.setToolTip(
            "Default where the lids MEET: 0 = at the lower lash (upper does\n"
            "all the work, real-life ~0.1), 1 = at the upper lid. This is\n"
            "the STARTING value — it's also a LIVE 'blinkHeight' attr on\n"
            "each blink ctrl, so you can dial it per shot without rebuilding.\n"
            "The blink ORBITS the eyeball (L/R_eyeball_LOC = centre/pivot;\n"
            "L/R_upperLid_LOC = how far the UPPER lid closes).")
        jrow.addWidget(self.blink_height_spin, 2, 1)
        self.flip_curve_chk = QtWidgets.QCheckBox(
            "Flip eye curvature (if the lids bulge INWARD)")
        self.flip_curve_chk.setToolTip(
            "The blink should sweep the lids OUT over the eyeball. The\n"
            "direction is auto-detected from the eyeball locator, but if a\n"
            "particular mesh still arcs the lids INTO the head, tick this and\n"
            "rebuild to flip the sweep the other way.")
        jrow.addWidget(self.flip_curve_chk, 3, 0, 1, 2)
        self.eye_sym_chk = QtWidgets.QCheckBox(
            "Symmetric eyes (one ball drives both)")
        self.eye_sym_chk.setChecked(True)
        self.eye_sym_chk.setToolTip(
            "ON: one eyeball ball (L_eyeball_LOC) drives BOTH eyes — the right\n"
            "mirrors it across the centre line. Best for a symmetric face.\n"
            "OFF: an asymmetric face — each eye gets its OWN ball "
            "(R_eyeball_LOC,\nseeded at the mirror of the left) to place "
            "independently.")
        jrow.addWidget(self.eye_sym_chk, 4, 0, 1, 2)
        root.addLayout(jrow)

        # Eyes box — lash-line button per eye (required) + crease button
        # (optional second loop -> two joint rows per lid for skinning).
        lids = QtWidgets.QGroupBox("Eyes — select the whole eye loop")
        lg = QtWidgets.QVBoxLayout(lids)
        for fid, label, regions, outer in _EYE_FEATURES:
            side = "L" if fid.startswith("eyeL") else "R"
            fn = (lambda s=side: self._on_fit_eye_outer(s)) if outer \
                else (lambda s=side: self._on_fit_eye(s))
            lg.addWidget(self._make_feature_button(fid, label, regions, fn))
        # Snap the eyeball ball onto a selected eyeball MESH (easier than
        # dragging it into the socket by hand).
        self.btn_snap_eye = QtWidgets.QPushButton(
            "Snap Eyeball Ball  →  Selected Eye Mesh")
        self.btn_snap_eye.setToolTip(
            "Hard to drag the eyeball ball into the socket? Select your actual\n"
            "EYEBALL MESH and click — the ball jumps to its centre and scales\n"
            "to it. Nudge by hand afterwards if it's slightly off, then Build.\n"
            "(In symmetric mode it always sets the LEFT ball.)")
        self.btn_snap_eye.clicked.connect(self._on_snap_eyeball)
        lg.addWidget(self.btn_snap_eye)
        root.addWidget(lids)

        # Symmetric eyes: the RIGHT-eye fit buttons are redundant (the right
        # eye mirrors the left), so hide them while symmetry is on; show them
        # when it's off so each eye can be fitted independently.
        self.eye_sym_chk.toggled.connect(self._on_eye_sym_toggled)
        self._on_eye_sym_toggled(self.eye_sym_chk.isChecked())

        # Mouth box — one button; the whole loop auto-splits into upper +
        # lower lip.
        lips = QtWidgets.QGroupBox("Mouth — select the whole lip loop")
        pg = QtWidgets.QVBoxLayout(lips)
        fid, label, regions = _MOUTH_FEATURE
        pg.addWidget(self._make_feature_button(
            fid, label, regions, self._on_fit_mouth))
        root.addWidget(lips)

        # Build / clear
        brow = QtWidgets.QHBoxLayout()
        self.btn_build = QtWidgets.QPushButton("Build Advanced Face")
        self.btn_build.setObjectName("primary")
        self.btn_build.setStyleSheet("QPushButton { padding: 7px; }")
        self.btn_build.clicked.connect(self._on_build)
        self.btn_clear = QtWidgets.QPushButton("Delete / Reset")
        self.btn_clear.clicked.connect(self._on_clear)
        brow.addWidget(self.btn_build, 2)
        brow.addWidget(self.btn_clear, 1)
        root.addLayout(brow)

        # Re-attach an already-built face to the head (repair, no rebuild).
        self.btn_attach = QtWidgets.QPushButton("Re-attach to Head (fix follow)")
        self.btn_attach.setToolTip(
            "If the face doesn't follow the head, click this. The detail\n"
            "joints are curve-driven, so they can't be moved by parenting —\n"
            "this constrains the face CONTROLS to the Head joint above, which\n"
            "makes the whole face travel + turn with the head. No rebuild.")
        self.btn_attach.clicked.connect(self._on_attach_head)
        root.addWidget(self.btn_attach)

        # One-click skin: select the mesh -> bind to the face joints.
        self.btn_bind = QtWidgets.QPushButton(
            "Bind Selected Face Mesh  →  Face Joints")
        self.btn_bind.setObjectName("primary")
        self.btn_bind.setStyleSheet(
            "QPushButton { padding: 7px; }")
        self.btn_bind.setToolTip(
            "Select your face geo in the viewport, then click this. It\n"
            "smooth-binds the mesh to every face joint (lids, lips, head,\n"
            "jaw, eyes...) with closest-point weights — so the blink and\n"
            "mouth drive it right away. Replaces any existing skin; refine\n"
            "afterwards with weight paint. (Select several meshes to bind\n"
            "them all.)")
        self.btn_bind.clicked.connect(self._on_bind_face)
        root.addWidget(self.btn_bind)

        self.btn_bind_lids = QtWidgets.QPushButton(
            "Bind Eyelids Only  →  Lid Joints (clean blink test)")
        self.btn_bind_lids.setToolTip(
            "Select just the EYELID mesh, then click this. It binds only to\n"
            "the eyelid joints (+ eye + head) — no lip/jaw influence to\n"
            "fight — so you can preview a clean blink in isolation before\n"
            "committing to the whole face.")
        self.btn_bind_lids.clicked.connect(self._on_bind_eyelids)
        root.addWidget(self.btn_bind_lids)

        # Per-joint tweak controls (fine eye-close adjustment).
        self.btn_tweaks = QtWidgets.QPushButton(
            "Add Per-Joint Tweak Controls  (fine eye-close)")
        self.btn_tweaks.setToolTip(
            "Adds a small control on EVERY lid/lip detail joint so you can\n"
            "nudge any single joint by hand to perfect the close, on top of\n"
            "the blink. Hidden by default — turn on 'showTweaks' on a blink /\n"
            "mouth ctrl to reveal them. Run after Build (+ bind).")
        self.btn_tweaks.clicked.connect(self._on_add_tweaks)
        root.addWidget(self.btn_tweaks)

        # One-click EXPRESSION presets — set the whole face's dials at once
        # (smile / pucker / brows / cheeks / jaw). Neutral resets.
        exp_box = QtWidgets.QGroupBox("Expressions (one click)")
        eg = QtWidgets.QHBoxLayout(exp_box)
        for nm in rig_face_presets.list_expressions():
            b = QtWidgets.QPushButton(nm.capitalize())
            b.setToolTip("Set the whole face to '%s' (smile/pucker/lipRoll +\n"
                         "brow raise/furrow + cheek + jaw dials). 'Neutral'\n"
                         "resets. Works on whatever face dials are built." % nm)
            b.clicked.connect(lambda _=False, n=nm: self._on_expression(n))
            eg.addWidget(b)
        root.addWidget(exp_box)

        self.status = QtWidgets.QLabel("")
        self.status.setStyleSheet(
            "QLabel { color: %s; padding-top: 4px; "
            "border-top: 1px solid %s; }" % (ft.OK, ft.BORDER))
        root.addWidget(self.status)

        self.adjustSize()

    def _make_feature_button(self, fid, label, regions, on_click):
        row = QtWidgets.QWidget()
        h = QtWidgets.QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        btn = QtWidgets.QPushButton(f"Fit  {label}")
        btn.clicked.connect(lambda: on_click())
        self._fit_buttons[fid] = btn
        self._fit_rows[fid] = row
        self._feature_regions[fid] = regions
        h.addWidget(btn, 1)
        info = QtWidgets.QLabel("[ ]")
        info.setObjectName(f"state_{fid}")
        info.setFixedWidth(18)
        h.addWidget(info)
        return row

    # -----------------------------------------------------------------------

    def _on_eye_sym_toggled(self, symmetric):
        """Hide the RIGHT-eye fit buttons while symmetry is on (the right eye
        mirrors the left); show them when it's off."""
        for fid in ("eyeR", "eyeROuter"):
            row = self._fit_rows.get(fid)
            if row is not None:
                row.setVisible(not symmetric)

    def _on_snap_eyeball(self):
        _reload(advanced_face)
        sym = self.eye_sym_chk.isChecked()
        cmds.undoInfo(openChunk=True)
        try:
            loc = advanced_face.snap_eyeball_to_mesh(
                side="L" if sym else None, mirror=sym)
        finally:
            cmds.undoInfo(closeChunk=True)
        if loc:
            self.status.setText(
                f"Snapped {loc} to the selected eye mesh — nudge if needed, "
                f"then Build.")
        else:
            self.status.setText("Select your eyeball MESH first, then snap.")

    def _on_fit_eye(self, side):
        n = self.face.fit_eye(side)
        if n:
            self.status.setText(
                f"Fitted {side} eye: {n} joints, auto-split upper + lower.")
            self._refresh_fit_states()
        else:
            self.status.setText(
                "Fit failed — select the WHOLE eye loop first.")

    def _on_fit_eye_outer(self, side):
        n = self.face.fit_eye_outer(side)
        if n:
            self.status.setText(
                f"Fitted {side} eye crease: {n} joints — two-row lid (lash "
                f"line + crease) for cleaner skinning.")
            self._refresh_fit_states()
        else:
            self.status.setText(
                "Fit failed — select the WHOLE outer (crease) loop first.")

    def _on_fit_mouth(self):
        n = self.face.fit_mouth()
        if n:
            self.status.setText(
                f"Fitted mouth: {n} joints, auto-split upper + lower lip.")
            self._refresh_fit_states()
        else:
            self.status.setText(
                "Fit failed — select the WHOLE mouth loop first.")

    def _on_build(self):
        # HOT-RELOAD on every Build: an open window otherwise keeps
        # building with whatever code was loaded when it opened — fixes
        # landed on disk between clicks silently don't apply (the
        # stale-module trap). Fits live on the scene's network node, so a
        # fresh model loses nothing.
        _reload(advanced_face)
        fresh = advanced_face.AdvancedFace(
            lid_joints=self.face.lid_joints,
            lip_joints=self.face.lip_joints)
        fresh._load_fits()
        if not fresh.fits and self.face.fits:
            fresh.fits = dict(self.face.fits)
        self.face = fresh

        self.face.head_joint = self.head_field.text().strip()
        self.face.jaw_joint = self.jaw_field.text().strip()
        self.face.blink_height = self.blink_height_spin.value()
        self.face.flip_curvature = self.flip_curve_chk.isChecked()
        self.face.eye_symmetry = self.eye_sym_chk.isChecked()
        if not self.face.fits:
            cmds.warning("No regions fitted yet.")
            return
        cmds.undoInfo(openChunk=True)
        try:
            self.face.build()
            self.status.setText(
                f"Built: {', '.join(self.face.fitted_regions())}.")
        except Exception as e:
            cmds.warning(f"Advanced face build failed: {e}")
            raise
        finally:
            cmds.undoInfo(closeChunk=True)

    def _on_attach_head(self):
        head = self.head_field.text().strip()
        if not cmds.objExists("ADV_FACE_controls_GRP"):
            cmds.warning("No advanced face in the scene — build it first.")
            return
        cmds.undoInfo(openChunk=True)
        try:
            ok = advanced_face.attach_to_head(head)
        finally:
            cmds.undoInfo(closeChunk=True)
        if ok:
            self.status.setText(
                f"Re-attached the face to '{head}' — move the head ctrl to "
                f"check it follows now.")

    def _on_bind_face(self):
        _reload(advanced_face)
        sel = [s for s in (cmds.ls(sl=True, transforms=True) or [])
               if cmds.listRelatives(s, type="mesh", ni=True)]
        if not sel:
            self.status.setText(
                "Select your face mesh in the viewport first, then Bind.")
            return
        cmds.undoInfo(openChunk=True)
        try:
            made = advanced_face.bind_face_mesh()
        finally:
            cmds.undoInfo(closeChunk=True)
        if made:
            self.status.setText(
                f"Bound {len(made)} mesh(es) to the face joints — set "
                f"blink/zip to test, then paint weights.")
        else:
            self.status.setText(
                "Bind failed — build the face first, then select the mesh.")

    def _on_bind_eyelids(self):
        _reload(advanced_face)
        sel = [s for s in (cmds.ls(sl=True, transforms=True) or [])
               if cmds.listRelatives(s, type="mesh", ni=True)]
        if not sel:
            self.status.setText(
                "Select your eyelid mesh in the viewport first, then Bind.")
            return
        cmds.undoInfo(openChunk=True)
        try:
            made = advanced_face.bind_eyelids()
        finally:
            cmds.undoInfo(closeChunk=True)
        if made:
            self.status.setText(
                f"Bound {len(made)} eyelid mesh(es) to the lid joints — set "
                f"L/R_blink_CTRL.blink to 1 to test.")
        else:
            self.status.setText(
                "Bind failed — build the face first, then select the mesh.")

    def _on_add_tweaks(self):
        _reload(advanced_face)
        head = (self.head_field.text().strip() or "C_head_BIND_JNT")
        cmds.undoInfo(openChunk=True)
        try:
            made = advanced_face.add_tertiary_controls(head_joint=head)
        finally:
            cmds.undoInfo(closeChunk=True)
        if made:
            self.status.setText(
                f"Added {len(made)} per-joint tweak controls (hidden). Turn "
                f"on 'showTweaks' on a blink/mouth ctrl to reveal + use them.")
        else:
            self.status.setText(
                "No tweak controls added — build the advanced face first.")

    def _on_expression(self, name):
        _reload(rig_face_presets)
        cmds.undoInfo(openChunk=True)
        try:
            n = rig_face_presets.apply_expression(name)
        finally:
            cmds.undoInfo(closeChunk=True)
        self.status.setText(f"Expression '{name}' applied ({n} dials). "
                            f"'Neutral' resets.")

    def _on_clear(self):
        self.face.delete()
        self.status.setText("Advanced face + fits deleted.")
        self._refresh_fit_states()

    def _refresh_fit_states(self):
        fitted = set(self.face.fitted_regions())
        for fid, btn in self._fit_buttons.items():
            regions = self._feature_regions.get(fid, [])
            done = bool(regions) and all(r in fitted for r in regions)
            lbl = self.findChild(QtWidgets.QLabel, f"state_{fid}")
            if done:
                btn.setStyleSheet(
                    "QPushButton { border-color: %s; color: %s; }"
                    % (ft.OK, ft.OK))
                if lbl:
                    lbl.setText("[x]")
                    lbl.setStyleSheet("QLabel { color: %s; }" % ft.OK)
            else:
                btn.setStyleSheet("")
                if lbl:
                    lbl.setText("[ ]")
                    lbl.setStyleSheet("QLabel { color: %s; }" % ft.DISABLED)


_win = None


def show(lid_joints=None, lip_joints=None):
    global _win
    # Reload the model module every time the window opens, so code fixes
    # (e.g. the head-follow) always take effect on reopen — no Maya restart.
    _reload(advanced_face)
    for w in QtWidgets.QApplication.topLevelWidgets():
        if w.objectName() == WINDOW_OBJECT_NAME:
            try:
                w.close()
                w.deleteLater()
            except Exception:
                pass
    _win = AdvancedFaceUI(lid_joints=lid_joints, lip_joints=lip_joints)
    _win.show()
    _win.raise_()
    return _win
