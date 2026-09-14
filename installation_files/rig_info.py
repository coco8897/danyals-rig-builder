"""
===============================================================================
 RIG INFO — click-through help popups with optional images
===============================================================================

 A small "i" button next to a main UI button. Clicking the "i" opens a
 popup that shows:
   * The action's title
   * A 1-2 paragraph description (from the INFO_REGISTRY below)
   * An image loaded from images/{button_id}.png if that file exists,
     otherwise a placeholder rectangle saying "image coming soon"

 Drop new PNGs into  C:/.../scripts/images/  and the popups will pick
 them up automatically the next time they open.

 Wiring it up in rig_ui.py:

     from rig_info import make_info_button
     ...
     row.addWidget(btn_create_guides)
     row.addWidget(make_info_button("create_guides"))

===============================================================================
"""

import os
import maya.cmds as cmds
try:
    from PySide2 import QtCore, QtWidgets, QtGui
except ImportError:                                  # Maya 2025+ (Qt6)
    from PySide6 import QtCore, QtWidgets, QtGui


# -----------------------------------------------------------------------------
# Image lookup
# -----------------------------------------------------------------------------

IMAGES_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "images",
)


def _image_path(button_id):
    """Return the absolute path to images/{button_id}.png, or None if
    the file doesn't exist."""
    path = os.path.join(IMAGES_DIR, f"{button_id}.png")
    return path if os.path.isfile(path) else None


# -----------------------------------------------------------------------------
# Registry: button_id -> (title, description)
#
# To add entries: pick a unique button_id, give it a clear title, and
# write a short description. Drop an image at images/{button_id}.png.
# -----------------------------------------------------------------------------

INFO_REGISTRY = {
    # --- Guides (biped) ---
    "create_guides": (
        "Create Guides",
        "Spawns a set of colour-coded locators at the default biped "
        "proportions. Drag each locator to fit your character mesh, then "
        "click Mirror L → R to copy left-side placements to the right.\n\n"
        "Blue = left side, red = right, yellow = center."
    ),
    "mirror_guides": (
        "Mirror Guides L → R",
        "Copies the world position of every L_* guide locator to its "
        "R_* twin with the X coordinate negated. Use this after placing "
        "the left side so you don't have to manually mirror.\n\n"
        "Ctrl+Z fully reverts the mirror as a single action."
    ),
    "reset_guides": (
        "Reset Guides to Defaults",
        "Snaps every guide locator back to the script's hardcoded T-pose "
        "default. Useful if you've moved things around and want to start "
        "over without deleting and recreating."
    ),
    "save_guides": (
        "Save Guides…",
        "Writes the current world positions of every guide locator to a "
        "JSON file you can re-load later or share with a teammate. "
        "Includes face / fingers / tail guides if they exist."
    ),
    "load_guides": (
        "Load Guides…",
        "Reads a previously saved JSON file and snaps the guides to "
        "those positions. Creates the guides first if they don't exist."
    ),
    "delete_guides": (
        "Delete Guides",
        "Removes the entire guides group from the scene. Safe to run "
        "after you've built the rig — the rig itself doesn't depend on "
        "the guides being present."
    ),

    # --- Build (biped) ---
    "build_rig": (
        "Build Rig",
        "Reads the current guide locator positions, runs the full "
        "CharacterRig pipeline, and parents everything under "
        "CHARACTER_RIG_GRP.\n\n"
        "Order: core → spine → neck → clavicles → arms (+fingers) → "
        "legs → tail → face. Auto-walk is wired onto the global ctrl."
    ),
    "creature_limbs": (
        "Creature Limbs",
        "Adds extra arms, legs, tails or your own custom chains on top of "
        "the biped: four arms, a dragon, a centaur, insect legs, capes, "
        "tentacles, antennae.\n\n"
        "1. Pick a preset and click Add Preset, or set Type / Name / Attach "
        "/ Side and click Add Limb Guides.\n"
        "   Attach > Custom: select ANY guide or joint (spine 2, tail 3, "
        "another limb's knee) and click Pick Selected.\n"
        "   Type > Chain: pick the joint count and FK, IK or FK + IK "
        "controls. Drew your own joints? Select the root and click From "
        "Selected Joints.\n"
        "2. Move the new guides (under CREATURE_GUIDES_GRP) to fit your "
        "model. With 'Both (mirrored)' you only place the left side. Guide "
        "mode EZ links them so parents carry their children.\n"
        "3. Build Rig.\n\n"
        "Every extra limb gets its own names (L_lowerArm_IK_CTRL ...), "
        "controls, auto-walk, space switching, pose mirroring and a Creature "
        "tab in the picker. Remove Limb deletes its guides; rebuild to drop "
        "it from the rig. Full guide: CREATURES.md."
    ),
    "delete_rig": (
        "Delete Rig",
        "Removes CHARACTER_RIG_GRP and every node beneath it. The "
        "guides stay in the scene so you can tweak and rebuild without "
        "starting from scratch."
    ),
    "verify": (
        "Verify Build",
        "Prints a diagnostic comparing each BIND joint's world position "
        "against the corresponding guide locator. Use this if something "
        "looks visually off after build — it'll tell you which joint "
        "deviated and by how much."
    ),

    # --- Skinning ---
    "select_skin_jnts": (
        "Select Skinning Joints",
        "Selects every joint in the rig whose name ends with _BIND_JNT. "
        "These are the only joints you should skin your mesh to — FK / "
        "IK / DRV chains are hidden helpers that drive the BINDs."
    ),
    "smooth_bind": (
        "Auto-Skin (Geodesic Voxel)",
        "Smooth-binds the selected mesh to every BIND joint using "
        "Maya's Geodesic Voxel binder (bindMethod=3). Topology-aware: "
        "respects mesh boundaries, handles overlapping geometry far "
        "better than closest-distance binding. Use as the initial pass, "
        "then refine with NG Skin Tools."
    ),
    "ng_init": (
        "Auto-Skin + NG Skin Tools",
        "Three actions in one click: auto-skin the selected mesh, "
        "initialize NG Skin Tools layers on the resulting skinCluster, "
        "and open the NG panel ready for painting."
    ),
    "delta_mush": (
        "Apply Delta Mush",
        "Adds a deltaMush deformer on top of the existing skinCluster. "
        "Smooths skinning artifacts (elbow / knee / armpit zigzags) "
        "non-destructively without manual weight paint touch-ups.\n\n"
        "Tweak iterations on the deltaMush node if the result is too soft."
    ),

    # --- Quadruped ---
    "quad_create_guides": (
        "Create Horse Guides",
        "Spawns the quadruped guide locators at default horse "
        "proportions: croup, withers, neck arc, four legs, fanned tail, "
        "and face."
    ),
    "quad_mirror": (
        "Mirror Horse Guides L → R",
        "Copies left-side leg positions to the right with X negated. "
        "Mirrors front + back legs in one pass."
    ),
    "quad_build": (
        "Build Horse Rig",
        "Reuses the CoreRig / SpineRig / NeckHeadRig / TailRig / FaceRig "
        "from the biped builder and adds a dedicated 4-joint QuadLegRig "
        "per leg (with anatomically distinct front vs back chains). "
        "Top group: QUADRUPED_RIG_GRP."
    ),

    # --- Bird ---
    "bird_create_guides": (
        "Create Raptor Guides",
        "Spawns the bird guide locators at default eagle / hawk "
        "proportions. Includes per-feather guide spots for the wing "
        "primaries + secondaries and a fanned tail."
    ),
    "bird_mirror": (
        "Mirror Raptor Guides L → R",
        "Copies left-side wing + leg positions to the right with X "
        "negated. The elbow's clear gull-wing bend offset is preserved "
        "(otherwise the IK chain would be collinear and the elbow could "
        "not bend)."
    ),
    "bird_build": (
        "Build Raptor Rig",
        "Builds the bird with IK/FK wings (humerus / radius / manus + "
        "primary + secondary feather controllers), avian legs (femur / "
        "tibiotarsus / tarsometatarsus + 4 toes with talons), and a "
        "fanned tail. Top group: BIRD_RIG_GRP."
    ),

    # --- Game export ---
    "game_skeleton": (
        "Make Game Skeleton",
        "Reparents every loose BIND chain (C_pelvis_BIND_JNT, "
        "C_chest_BIND_JNT, etc.) under C_root_BIND_JNT so the "
        "skeleton has a single root for FBX export to game engines.\n\n"
        "Safe to run anytime after Build — does not break the visual "
        "rig because BIND joints are driven by constraints, not parent "
        "inheritance. Ribbon-bendy joints are left in place; they need "
        "their follicle parent."
    ),
    "export_rig": (
        "Export Rig (.fbx)",
        "Writes an FBX file containing the BIND skeleton (rooted at "
        "C_root_BIND_JNT) plus the skinned mesh, ready to drop into "
        "Unreal or Unity. Runs the Game Skeleton step first to make "
        "sure the hierarchy is clean.\n\n"
        "The Include Mesh / Include Bendy checkboxes above control the "
        "scope of the export."
    ),
    "export_anim": (
        "Export Animation (.fbx)",
        "Bakes every BIND joint's animation across the current playback "
        "range, then writes an FBX with animation only (no mesh). Uses "
        "minimizeRotation + disableImplicitControl so the baked curves "
        "are clean of constraint feedback."
    ),

    # --- Picker ---
    "open_picker": (
        "Open Picker",
        "Opens a separate window with the rig's controllers laid out "
        "anatomically. Click a button to select that ctrl in the "
        "viewport.\n\nShift + click = add to selection. "
        "Ctrl + click = toggle the ctrl in or out of the selection."
    ),
}


# -----------------------------------------------------------------------------
# Info popup dialog
# -----------------------------------------------------------------------------

class InfoDialog(QtWidgets.QDialog):
    """One-shot popup with a title, description text and image."""

    def __init__(self, button_id, parent=None):
        super(InfoDialog, self).__init__(parent)
        info = INFO_REGISTRY.get(
            button_id,
            (button_id, "(No description registered for this button yet.)"),
        )
        title, description = info
        self.setWindowTitle(f"Help — {title}")
        self.setMinimumWidth(420)
        self.setMaximumWidth(560)

        root = QtWidgets.QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(8)

        # ---- Title ----
        h = QtWidgets.QLabel(title)
        h.setStyleSheet(
            "QLabel { font-size: 14pt; font-weight: bold; color: #fff; }")
        root.addWidget(h)

        # ---- Image (or placeholder) ----
        img_path = _image_path(button_id)
        img_lbl = QtWidgets.QLabel()
        img_lbl.setAlignment(QtCore.Qt.AlignCenter)
        img_lbl.setStyleSheet(
            "QLabel { background: #222; border: 1px dashed #555; }")
        img_lbl.setMinimumHeight(180)
        if img_path:
            pixmap = QtGui.QPixmap(img_path)
            scaled = pixmap.scaled(
                400, 240,
                QtCore.Qt.KeepAspectRatio,
                QtCore.Qt.SmoothTransformation,
            )
            img_lbl.setPixmap(scaled)
            img_lbl.setStyleSheet("QLabel { background: #1a1a1a; }")
        else:
            img_lbl.setText(
                f"(image coming soon)\n\n"
                f"Drop a PNG at:\n"
                f"images/{button_id}.png")
            img_lbl.setStyleSheet(
                "QLabel { background: #222; border: 1px dashed #555; "
                "color: #888; font-size: 9pt; padding: 30px; }")
        root.addWidget(img_lbl)

        # ---- Description ----
        d = QtWidgets.QLabel(description)
        d.setWordWrap(True)
        d.setStyleSheet("QLabel { color: #ccc; font-size: 10pt; }")
        root.addWidget(d)

        # ---- Close button ----
        btn_row = QtWidgets.QHBoxLayout()
        btn_row.addStretch(1)
        close = QtWidgets.QPushButton("Close")
        close.clicked.connect(self.accept)
        btn_row.addWidget(close)
        root.addLayout(btn_row)


# -----------------------------------------------------------------------------
# Info button — the small "i" you put next to a main button
# -----------------------------------------------------------------------------

class InfoButton(QtWidgets.QToolButton):
    """Small circular "i" that opens the info popup on click."""

    def __init__(self, button_id, parent=None):
        super(InfoButton, self).__init__(parent)
        self.button_id = button_id
        # Use a unicode info char so we don't need an icon asset.
        self.setText("ⓘ")   # circled latin small letter i
        self.setFixedSize(18, 18)
        self.setStyleSheet(
            "QToolButton {"
            "  background: #3a4f6a;"
            "  border: 1px solid #1a2535;"
            "  border-radius: 9px;"
            "  color: #cfe0ff;"
            "  font-size: 12px;"
            "  font-weight: bold;"
            "  padding: 0px;"
            "}"
            "QToolButton:hover { background: #5c80b0; color: white; }"
            "QToolButton:pressed { background: #2a3f5a; }"
        )
        # Tooltip preview of the registered title.
        info = INFO_REGISTRY.get(button_id)
        if info:
            self.setToolTip(f"What is this? — {info[0]}")
        else:
            self.setToolTip("(no help registered)")
        self.clicked.connect(self._open_popup)

    def _open_popup(self):
        dlg = InfoDialog(self.button_id, parent=self.window())
        # PySide2 has exec_() only; PySide6 uses exec().
        if hasattr(dlg, "exec"):
            dlg.exec()
        else:
            dlg.exec_()


# -----------------------------------------------------------------------------
# Convenience helper
# -----------------------------------------------------------------------------

def make_info_button(button_id, parent=None):
    """Return a small "i" button wired to the info popup for this ID.

    Use it like:
        row.addWidget(self.btn_create_guides)
        row.addWidget(make_info_button("create_guides"))
    """
    return InfoButton(button_id, parent=parent)
