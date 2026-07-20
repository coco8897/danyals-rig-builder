"""
===============================================================================
 Danyal's Rig Builder — Drag-and-Drop Installer
===============================================================================

 HOW TO INSTALL
 --------------
 Drag this file (install.py) onto an open Maya viewport. That's it.
 The installer will:
   1. Copy the tool's Python modules into your user Maya scripts folder
   2. Add a "Danyal's Rig" button to your current shelf
   3. Save the shelf so the button persists across Maya sessions
   4. Show a confirmation dialog

 PRIVACY
 -------
 On install, ONE anonymous ping (random ID + tool/Maya version + OS — no
 personal data) may be sent so the author can count installs. Full
 disclosure + opt-out instructions are at the top of rig_telemetry.py.

 HOW TO USE
 ----------
 Click the new "Danyal's Rig" shelf button.

 HOW TO UNINSTALL
 ----------------
 Run this in Maya's Python script editor:

     import install
     install.uninstall()

 Removes the source files and the shelf button.

 REQUIREMENTS
 ------------
   * Maya 2022 or newer (Python 3)
   * Optional: NG Skin Tools 2 for the auto-skin + paint workflow.
     The UI degrades gracefully if NG isn't installed.

===============================================================================
"""

import os
import shutil
import maya.cmds as cmds
import maya.mel as mel


# All tool modules live in this subfolder beside install.py — users only
# ever touch install.py at the root.
PAYLOAD_DIR = "installation_files"

# Every module the tool needs (rig_ui's full transitive import set — an
# incomplete list here is exactly how "works on my machine" bugs ship).
SOURCE_FILES = (
    "advanced_face.py",
    "advanced_face_ui.py",
    "bird_guides.py",
    "bird_rig_builder.py",
    "character_rig_builder.py",
    "quadruped_guides.py",
    "quadruped_rig_builder.py",
    "raycast_ground.py",
    "rig_correctives.py",
    "rig_dynamics.py",
    "rig_export.py",
    "rig_face_presets.py",
    "rig_guides.py",
    "rig_info.py",
    "rig_picker.py",
    "rig_pose_lib.py",
    "rig_pose_tools.py",
    "rig_skin.py",
    "rig_space_switch.py",
    "rig_telemetry.py",
    "rig_ui.py",
    "rig_validate.py",
    "vehicle_bind.py",
    "vehicle_drive.py",
    "vehicle_guides.py",
    "vehicle_rig_builder.py",
)

SHELF_BUTTON_LABEL = "Danyal's Rig"
SHELF_BUTTON_ANNOTATION = "Launch Danyal's Rig Builder"

# The shelf button executes this Python on click. reload() lets the user
# pick up edits to rig_ui without restarting Maya.
SHELF_BUTTON_CMD = (
    "import rig_ui\n"
    "from importlib import reload\n"
    "reload(rig_ui)\n"
    "rig_ui.show()"
)


# =============================================================================
# Maya drag-drop entry point
# =============================================================================

def onMayaDroppedPythonFile(*args):
    """Maya calls this automatically when the file is dragged into the
    viewport. The *args signature varies by Maya version (some pass
    the dropped file path, some pass nothing), so we just accept any
    positional args."""
    install()


# =============================================================================
# Install
# =============================================================================

def install():
    """Copy source files into the user scripts folder, add a shelf
    button, and save the shelf state."""
    # The modules live in the installation_files/ subfolder beside this file.
    source_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                              PAYLOAD_DIR)
    target_dir = cmds.internalVar(userScriptDir=True)

    # Validate sources are present (catches the "dragged from inside the
    # zip without extracting" mistake, or a moved/renamed payload folder).
    missing = [f for f in SOURCE_FILES
               if not os.path.isfile(os.path.join(source_dir, f))]
    if missing:
        cmds.confirmDialog(
            title="Danyal's Rig Builder — install failed",
            message=(
                "These required files are missing from the '" + PAYLOAD_DIR +
                "' folder:\n\n  " + "\n  ".join(missing[:8]) +
                ("\n  ..." if len(missing) > 8 else "") +
                "\n\nMake sure you extracted the FULL DanyalsRigBuilder.zip "
                "(install.py AND the '" + PAYLOAD_DIR + "' folder together), "
                "then drag install.py from the extracted folder."
            ),
            button="OK", icon="critical",
        )
        return

    # Copy each source file. shutil.copy2 preserves timestamps.
    copied = []
    for fname in SOURCE_FILES:
        src = os.path.join(source_dir, fname)
        dst = os.path.join(target_dir, fname)
        shutil.copy2(src, dst)
        copied.append(fname)

    # Anonymous install ping (random ID, no personal data — see the
    # disclosure at the top of rig_telemetry.py; opt-out supported).
    try:
        import sys
        if target_dir not in sys.path:
            sys.path.append(target_dir)
        import rig_telemetry
        rig_telemetry.ping("install")
    except Exception:
        pass

    # Add (or refresh) the shelf button on the currently-active shelf.
    # In headless / batch Maya there's no shelf — skip gracefully and
    # tell the user to launch via the Python command.
    shelf_msg = ""
    shelf_added = False
    try:
        g_shelf_top_level = mel.eval('$tmp = $gShelfTopLevel')
        current_shelf = cmds.tabLayout(g_shelf_top_level, q=True,
                                        selectTab=True)
        # Remove existing buttons with our label so re-install doesn't dup.
        _remove_buttons_on_shelf(current_shelf)
        cmds.shelfButton(
            parent=current_shelf,
            label=SHELF_BUTTON_LABEL,
            annotation=SHELF_BUTTON_ANNOTATION,
            image="out_joint.png",          # Maya's built-in joint icon
            imageOverlayLabel="DR",         # "Danyal's Rig" overlay
            overlayLabelColor=(1.0, 0.8, 0.2),
            overlayLabelBackColor=(0.0, 0.0, 0.0, 0.4),
            command=SHELF_BUTTON_CMD,
            sourceType="python",
        )
        # Persist the shelf so the button survives a Maya restart.
        mel.eval('saveAllShelves $gShelfTopLevel;')
        shelf_added = True
        shelf_msg = ("Shelf button \"" + SHELF_BUTTON_LABEL
                      + "\" added to shelf '" + current_shelf + "'.")
    except RuntimeError:
        # No shelf available (batch / headless / unusual Maya state).
        shelf_msg = ("No active shelf detected — the button wasn't "
                      "added. Launch the UI manually with:\n"
                      "    import rig_ui; rig_ui.show()")

    cmds.confirmDialog(
        title="Danyal's Rig Builder installed",
        message=(
            "Danyal's Rig Builder is installed.\n\n"
            "Source files copied to:\n"
            "  " + target_dir + "\n\n"
            + shelf_msg +
            ("\n\nClick the button to launch the UI." if shelf_added else "")
        ),
        button="OK", icon="information",
    )


# =============================================================================
# Uninstall
# =============================================================================

def uninstall():
    """Remove the source files from the user scripts folder and delete
    every "Danyal's Rig" shelf button on every shelf."""
    target_dir = cmds.internalVar(userScriptDir=True)

    removed_files = []
    for fname in SOURCE_FILES:
        path = os.path.join(target_dir, fname)
        if os.path.isfile(path):
            try:
                os.remove(path)
                removed_files.append(fname)
            except OSError as e:
                cmds.warning("Could not delete " + path + ": " + str(e))

    # Scan every shelf for our button. Wrapped in try/except for
    # headless / batch mode where there's no shelf UI.
    removed_buttons = 0
    try:
        g_shelf_top_level = mel.eval('$tmp = $gShelfTopLevel')
        for shelf in (cmds.tabLayout(g_shelf_top_level, q=True,
                                      childArray=True) or []):
            removed_buttons += _remove_buttons_on_shelf(shelf)
        mel.eval('saveAllShelves $gShelfTopLevel;')
    except RuntimeError:
        pass

    cmds.confirmDialog(
        title="Danyal's Rig Builder uninstalled",
        message=(
            "Removed " + str(len(removed_files)) + " source file(s) and "
            + str(removed_buttons) + " shelf button(s)."
        ),
        button="OK",
    )


# =============================================================================
# Internals
# =============================================================================

def _remove_buttons_on_shelf(shelf_name):
    """Delete every shelfButton on `shelf_name` whose label matches the
    Danyal's Rig button label. Returns the count removed."""
    children = cmds.shelfLayout(shelf_name, q=True, childArray=True) or []
    removed = 0
    for child in children:
        # Only shelfButton items have a 'label' attribute; skip others.
        if cmds.objectTypeUI(child) != "shelfButton":
            continue
        if cmds.shelfButton(child, q=True, label=True) == SHELF_BUTTON_LABEL:
            cmds.deleteUI(child)
            removed += 1
    return removed
