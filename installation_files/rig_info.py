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
        "Create Guides",
        "Spawns the quadruped guide locators for the Animal picked above: "
        "Horse, Cat / Dog or Raptor, with the Front and Back feet chosen. "
        "Spine, neck arc, four limbs, tail and face.\n\n"
        "Moving a paw, ball or wrist guide brings its toes or fingers "
        "along.\n\n"
        "Already placed your guides? Change Front or Back and click this "
        "again: only the legs below the shoulders / hips are swapped, the "
        "rest stays where you put it. To switch to a different animal, "
        "use Reset to Defaults."
    ),
    "quad_feet": (
        "Foot types",
        "Each leg pair gets its own foot, and they mix freely (a griffin: "
        "Claw front, Paw back).\n\n"
        "Hoof (horse, deer, cow): stands on the hoof tip, the fetlock "
        "raised. Heel / hoof / fetlock roll.\n\n"
        "Paw (cat, dog, wolf): stands on the toe pads with the wrist or "
        "ankle raised. Four toes.\n\n"
        "Claw (raptor): a long raised ankle bone, three toes forward, a "
        "sickle claw held up and a small dewclaw.\n\n"
        "Arm (front only, raptor arms): held off the ground, an IK hand "
        "that rides the chest, three clawed fingers.\n\n"
        "Paw and Claw feet: roll (heel, then ball, then toe tips like the "
        "biped foot), heelRoll, ballRoll (lifts the wrist / ankle, toes "
        "stay planted), toeRoll (up on the claws), toeBend, footBank. "
        "The SETTINGS control has toeCurl, toeSpread and sickleClaw "
        "(fingerCurl / fingerSpread on arms)."
    ),
    "quad_mirror": (
        "Mirror Quadruped Guides L → R",
        "Copies left-side leg positions to the right with X negated. "
        "Mirrors front + back legs, toes and fingers in one pass."
    ),
    "quad_build": (
        "Build Quadruped Rig",
        "Reuses the CoreRig / SpineRig / NeckHeadRig / TailRig / FaceRig "
        "from the biped builder and adds a QuadLegRig per limb for its "
        "foot type: hoof reverse foot, paw / claw reverse foot standing "
        "on the toes, or an IK arm. Toes and fingers get FK controls "
        "(untick Toes and fingers for one piece per foot). A raptor's arms "
        "are named L_frontArm / R_frontArm so Walk Mode swings them. "
        "Top group: QUADRUPED_RIG_GRP."
    ),

    # --- Bird ---
    "bird_create_guides": (
        "Create Bird Guides",
        "Spawns the bird guide locators at default eagle / hawk "
        "proportions. Includes per-feather guide spots for the wing "
        "primaries + secondaries and a fanned tail."
    ),
    "bird_mirror": (
        "Mirror Bird Guides L → R",
        "Copies left-side wing + leg positions to the right with X "
        "negated. The elbow's clear gull-wing bend offset is preserved "
        "(otherwise the IK chain would be collinear and the elbow could "
        "not bend)."
    ),
    "bird_build": (
        "Build Bird Rig",
        "Builds the bird with IK/FK wings (humerus / radius / manus + "
        "primary + secondary feather controllers), avian legs (femur / "
        "tibiotarsus / tarsometatarsus + 4 toes with talons), and a "
        "fanned tail. Top group: BIRD_RIG_GRP.\n\n"
        "The IK wings ride the chest and stretch from the shoulder; the "
        "feathers follow the wing in IK and FK. The legs stretch so the feet "
        "stay planted, and footRoll rocks back on the heel, lifts the ankle "
        "over planted toes, then rolls up onto the toe tips (plus heelRoll, "
        "ballRoll, toeRoll, toeBend, footBank)."
    ),

    # --- Game export ---
    "game_skeleton": (
        "Make Game Skeleton",
        "Re-parents the rig's loose BIND chains (C_pelvis_BIND_JNT, "
        "C_chest_BIND_JNT...) under C_root_BIND_JNT and folds the face "
        "joints under the head, so Auto-Skin Everything binds one clean "
        "joint hierarchy.\n\n"
        "Not needed for exporting: Export Rig and Export Animation build "
        "their own clean skeleton either way."
    ),
    "export_rig": (
        "Export Rig (.fbx)",
        "Writes the character for Unreal / Unity: the skeleton in its rest "
        "pose plus every mesh skinned to it, with the same weights.\n\n"
        "Your rig is not changed. The file holds real bones only (no "
        "controls or groups): an optional 'root' bone at the ground, then "
        "C_root_BIND_JNT and the rest of the skeleton.\n\n"
        "Import this first, then import animations onto its skeleton. Keep "
        "the 'Root bone at the ground' setting the same for all of them."
    ),
    "export_anim": (
        "Export Animation (.fbx)",
        "Bakes the frames onto a clean copy of the skeleton and saves an "
        "FBX animation, one move per file (the engine names the animation "
        "after the file, so the clip name makes a good file name). Your rig "
        "is "
        "not changed: no keys are added to it and nothing is re-parented.\n\n"
        "Root bone at the ground: the 'root' bone carries the character's "
        "travel (root motion). In place: the character stays at the "
        "origin, for cycles the engine moves."
    ),
    "export_clips": (
        "Clips",
        "Name a move and its frames (walk_fwd 1 to 32, run 40 to 64...), "
        "Save as Clip, repeat, then Export All Clips to write one FBX per "
        "clip. Clips are saved with the scene; click one to load its "
        "frames."
    ),

    # --- Picker ---
    "open_picker": (
        "Open Picker",
        "Opens a separate window with the rig's controllers laid out "
        "anatomically. Click a button to select that ctrl in the "
        "viewport.\n\nShift + click = add to selection. "
        "Ctrl + click = toggle the ctrl in or out of the selection."
    ),
    "walk_mode": (
        "Walk Mode (WASD)",
        "Walk your character around like a game character and keep the "
        "animation.\n\nW / S walk, A / D turn (on the spot when standing), "
        "Shift runs, Space jumps, Esc stops. Click the Walk Mode panel "
        "first so it gets the keys.\n\nFeet step and stay planted, the "
        "body bobs and leans, the arms hang down and swing, jumps crouch "
        "and land softly, and with a ground mesh the "
        "feet land on it. Everything is keyed when you stop, starting at "
        "the current frame. Works on bipeds, creatures with extra legs, "
        "quadrupeds and birds (legs in IK)."
    ),
    "tail_physics": (
        "Simulate & Bake Tail",
        "Follow-through for tails, like the vehicle's Simulate & Bake.\n\n"
        "Animate the character first (hand keys, Walk Mode or Fly Mode), "
        "pick the tail and press Simulate & Bake. The tail swings when the "
        "body speeds up, slows down or turns, then settles. The result is "
        "keyed, so it scrubs, renders and exports exactly. Change the "
        "animation or the dials and bake again.\n\n"
        "Stiffness: how fast it springs back (wobbles per second). Damping: "
        "how quickly the wobble dies. Swing: how far it swings. Whip: how "
        "much looser the tip is.\n\n"
        "Your animation is never changed: the swing lives on a physics "
        "layer. The 'physics' dial on the tail's SETTINGS control blends "
        "it (keyable, 0 = your animation only). Clear deletes the swing, "
        "Remove Layer takes the layer off the rig. Works on biped, "
        "quadruped and raptor tails, extra tails and custom chains, in IK "
        "or FK."
    ),
    "face_capture": (
        "Face Capture (webcam / phone)",
        "Animate the face with your own face, live from a camera, and keep "
        "it as keys.\n\n"
        "One-time setup: install Python 3.9 to 3.13 from python.org and run "
        "  python -m pip install mediapipe  . The first Start downloads "
        "Google's face model (about 4 MB).\n\n"
        "1. Build the face and the Advanced Face, then Add Shape Dials (52 "
        "dials named like iPhone ARKit).\n"
        "2. Start Tracker: a window shows your camera with the face points. "
        "A phone works as a webcam with DroidCam or Iriun (pick its camera "
        "number), or as an IP camera stream URL on the same Wi-Fi.\n"
        "3. Relax your face, look at the camera, Calibrate Neutral. Then "
        "Learn Range and make your biggest faces for 10 seconds (open wide, "
        "blink hard, smile, brows up): each dial learns your full value.\n"
        "4. Tune the gains (blink, brows, mouth ...) and smoothing while you "
        "watch the character. Wink your left eye: if the wrong eye closes, "
        "tick Mirror.\n"
        "5. Drive: tick only the parts your face should move (Eyes, Brows, "
        "Mouth, Cheeks, Head). Untick Head to animate the face without "
        "moving the neck and head; untick Brows and Cheeks for eyes and "
        "mouth only. Unticked parts are left alone, keys included.\n"
        "6. Record from the current frame; click again to stop and key it.\n"
        "7. Stop and Reset closes the camera and puts the face back to "
        "neutral (recorded keys stay). Closing the panel or pressing Esc "
        "does the same.\n\n"
        "Import CSV keys an iPhone Live Link Face recording, or a video file "
        "you tracked. Everything stays on your computer."
    ),
    "fly_mode": (
        "Fly Mode (WASD)",
        "Fly a bird or a dragon (the Dragon creature preset) like a game "
        "and keep the animation.\n\n"
        "On the ground press Space (or W) to take off. In the air: W flies "
        "faster, S slows down, A / D bank and turn, Space climbs, Shift "
        "dives with the wings tucked. To land, hold S close to the ground. "
        "Esc stops. Click the Fly Mode panel first so it gets the keys.\n\n"
        "The wings beat for real (spread downstroke, flexed upstroke), "
        "glide between bursts, flare to land and fold on the ground; the "
        "body banks into turns and pitches with climbs and dives, the legs "
        "tuck with the talons closed, the tail fans to brake and the head "
        "stays level. IK or FK wings. Everything is keyed when you stop, "
        "starting at the current frame, so you can fly again from the last "
        "frame or walk off after landing."
    ),
    "prop_rig": (
        "Prop Rig",
        "Rig a single prop (a sword, a gun, a chair, a crate) with three "
        "joints, the way game engines like them:\n"
        "  root: where the prop sits (placement)\n"
        "  move: the prop itself; the mesh is bound here\n"
        "  attach: the grip or socket that goes in a hand\n\n"
        "1. Type a name, select the prop's mesh and Create Prop Guides.\n"
        "2. Put root at the base, move at the pivot you want to animate "
        "from, attach at the grip. Rotate the attach guide to aim the "
        "socket.\n"
        "3. Build / Rebuild Prop. The mesh is bound for you. Animate "
        "<name>_move_CTRL; <name>_root_CTRL places it (propScale scales "
        "it).\n\n"
        "To hold it: select a hand joint or control (a referenced "
        "character's works too) and Attach to Selected: the grip snaps into "
        "the hand and follows. Pick Up / Put Down switch between the hand "
        "and the root on the current frame without a jump, and key it.\n\n"
        "Every node starts with the prop's name, so any number of props "
        "live in one scene. Export Prop FBX writes the 3 joints and the "
        "mesh (or the animation); a character's export leaves props out."
    ),
    "neck_ik": (
        "Neck joints and the IK head",
        "Give your character the neck it needs, and an IK head control to "
        "animate it with.\n\n"
        "Neck joints: 1 is a person (the default, nothing changes). A "
        "dragon, horse, swan or giraffe wants 4 to 6. The extra guides "
        "appear between the neck and head guides: drag them along the "
        "neck. The Dragon preset sets this up for you (5 joints, IK on).\n\n"
        "IK head: the neck runs on a spline with three controls. Drag "
        "C_headIK_CTRL and the whole neck arcs to follow it, so you can "
        "plant the head on a target and animate the body underneath. "
        "C_neck_root_IK_CTRL bends the base and C_neck_mid_IK_CTRL shapes "
        "the middle (it already follows the head half way).\n\n"
        "On C_neck_SETTINGS_CTRL:\n"
        "   ikFkSwitch: 0 = the IK spline, 1 = the FK chain (one control "
        "per neck joint), the same as the arms and legs. The controls of "
        "the mode you're not in hide themselves.\n"
        "   stretch: 0 = a fixed-length neck, 1 = it stretches to follow a "
        "head control pulled past its reach.\n\n"
        "C_head_CTRL still rotates the head in both modes and rides the "
        "neck's tip, so the face, picker, pose library and face capture "
        "work exactly as before."
    ),
    "fit_guides": (
        "Fit Guides to Selected Mesh",
        "Make the guides the size of your model, so you don't have to scale "
        "them by hand.\n\n"
        "Select your character's mesh (several is fine: body, hair, clothes) "
        "and click. The guides scale to the model's height, stand on its "
        "lowest point and centre on it. Then drag each guide onto the model "
        "as usual and build: the rig comes out at that size, controls and "
        "joints included.\n\n"
        "It also happens by itself: create the guides with the model "
        "selected and they arrive already fitted.\n\n"
        "Why you need it: the guides are built for a 170 cm human in Maya "
        "centimetres. A model exported at game scale (1 unit = 1 metre) is "
        "1.7 units tall, so the guides come out 100 times too big. Fit "
        "sorts that out in one click.\n\n"
        "The same button is in the Quadruped and Bird sections. Vehicles "
        "have Fit Guides to Model (the whole set to the model's length) and "
        "Fit Wheels to Selected Tyres (each wheel's place and size)."
    ),
    "vehicle_track_wheels": (
        "Tank Wheels",
        "Rig a tank with as many wheels as the model has, each its own size "
        "(a cartoon tank with big and small wheels, rollers on top, gears "
        "inside).\n\n"
        "1. Tick Tracked (tank treads), open Tank Wheels, select the tank's "
        "wheel meshes and its tread, and click Fit Wheels from Selected "
        "Meshes. You get one wheel guide per wheel, on its centre and sized "
        "to it, and the rest of the guides are scaled to the tank.\n"
        "2. Check each wheel's job (its circle's colour) and fix any with "
        "Set on Selected:\n"
        "   Road wheel (orange): on the ground on its own suspension; the "
        "tread runs under it.\n"
        "   Roller (cyan): on the hull; the tread wraps over it.\n"
        "   Gear (grey): inside the loop; it only spins (Reverse spin turns "
        "it the other way, like a gear meshed with the wheel next to it).\n"
        "3. Tread thickness: how thick the tread is under the road wheels "
        "(Fit measures it). Then Build Vehicle Rig.\n\n"
        "What you get: the tread wraps round the road wheels and rollers "
        "like a rubber band and follows the ground; every wheel spins at "
        "the tread's speed for its own size, so small wheels spin faster. "
        "Controls are drawn at the tank's size.\n\n"
        "Bind: the rims as Rim (a mesh holding several wheels, even both "
        "sides, binds each piece to its own wheel) and a one-piece modelled "
        "tread as Tread: it flows round the wheels. Or use Instance Link "
        "Mesh for a tread made of links."
    ),
    "motorcycle": (
        "Motorcycle",
        "A bike is a vehicle with two wheels on one line, so it is built "
        "with the same controls as the car rig and everything that works "
        "on a car works here: WASD Drive Mode, terrain, export, bind.\n\n"
        "1. Create Bike Guides, then Fit Guides to Selected Model (or drag "
        "the seven locators onto your bike). Fit Wheels to Selected Tyres "
        "snaps the two wheels and their sizes exactly.\n"
        "2. The line from the steering head down to the front axle is the "
        "steering axis, so where you put those two sets the rake: lean the "
        "head back for a chopper, stand it up for a sportbike.\n"
        "3. Build Motorcycle Rig.\n\n"
        "What you get: the fork steers and its suspension slides along the "
        "fork tubes, the swingarm swings and aims at the rear axle, the "
        "shock compresses, both tyres ride the ground on their own, and the "
        "whole bike leans on C_chassis_CTRL.lean with its pivot on the "
        "ground, so the tyres stay planted instead of sliding out.\n\n"
        "Drive Mode (Animate tab) drives it with WASD and leans it into "
        "every corner by how fast and how tight it is going, which you can "
        "scale or take over with lean / leanAmount.\n\n"
        "Bind: Frame, Fork (upper and lower tubes), Handlebar, Swingarm, "
        "Rim and Tire, the same way as a car."
    ),
    "vehicle_tracks": (
        "Tyre tracks and burnouts",
        "Bake Tyre Tracks lays the marks the wheels left over the timeline. "
        "A tyre that is simply rolling leaves nothing on tarmac, so by "
        "default only the DRIVEN wheels mark, and only where they were "
        "really SLIDING: sideways in a drift, a spin or a handbrake turn, "
        "or lengthways when the tyre is turning faster or slower than the "
        "road is going by (wheelspin off the line, a burnout, a locked "
        "wheel under braking). Untick Only when sliding and every wheel "
        "marks everywhere it touches, for sand, mud or snow.\n\n"
        "Each mark is a flat ribbon under the contact patch that follows "
        "wherever the wheel really went: a drive, a physics bake or your "
        "own keys, flat ground or terrain. Where the tyre was off the "
        "ground there is a gap, and a leaning bike leaves a narrower "
        "mark.\n\n"
        "They are ordinary meshes under TYRE_TRACKS_GRP with UVs running "
        "along their length, so you can shade them, blur them or delete "
        "the ones you don't want. Bake again after changing the animation, "
        "or Clear Tracks first.\n\n"
        "Burnout spins the driven wheels from the current frame: they turn "
        "far faster than the vehicle moves, it creeps forward, the body "
        "squats onto the back wheels and the wide dark marks are baked for "
        "you. Pick rear, front or all wheels, and how long it lasts. Clear "
        "Burnout takes the keys back off."
    ),
    "vehicle_springs": (
        "Springs",
        "Pick the suspension each axle has, so trucks and 4x4s get the parts "
        "they really have.\n\n"
        "Coil-over: a coil spring + damper from the spring guide down to the "
        "wheel (most cars). Bind the coil as Spring.\n"
        "Leaf spring + shock: a stack of leaves running fore and aft beside "
        "the wheel, bolted to the frame at the front, hung from a swinging "
        "shackle at the back, with the axle clamped to its middle; plus a "
        "shock absorber (trucks, pickups, jeeps, old cars). Picking it adds "
        "6 orange guides for that axle: put them on the leaf's front eye, "
        "its middle (where it sits on the axle), its rear eye, the shackle's "
        "pin on the frame and the shock's top and bottom mounts.\n"
        "None: the wheel still travels, with no spring parts.\n\n"
        "Solid axle: one beam ties the two wheels, so when one wheel rides "
        "up a bump the axle tilts and both wheels lean with it. Off = each "
        "wheel moves on its own.\n\n"
        "After building, bind the meshes with Bind Selected Mesh to Part: "
        "Leaf Spring (it bends as the axle moves: the leaf flattens under "
        "load and arches as the wheel drops, while the shackle swings to "
        "keep it the same length), Shackle, Shock Body and Shock Rod (they "
        "telescope) and Axle (the axle tube). Each finds the nearest wheel "
        "for you."
    ),
    "vehicle_cargo": (
        "Cargo",
        "Make roof racks, spare wheels, jerry cans and lights move with the "
        "ride.\n\n"
        "1. Each item must be its own mesh (separate it from the body if it "
        "was modelled as one piece). Bind the body to the vehicle first.\n"
        "2. Select the items and click Add Selected as Cargo. Each gets a "
        "joint and a control pivoting at its base, riding the body.\n"
        "3. Drive, Simulate Physics, or hand-key the vehicle, then Bake "
        "Cargo (Drive Mode and Simulate Physics bake it for you).\n\n"
        "What you get: items lean back when you accelerate, forward when you "
        "brake and out in turns, hop over bumps and landings, and rattle, "
        "more the faster you go. Tall, narrow items tip more than low, wide "
        "ones.\n\n"
        "Tune each item on its <item>_cargo_CTRL: stiffness (how tightly it's "
        "strapped), damping (how long it wobbles), maxLean, rattle, bounce, "
        "then bake again. physics (keyable) fades the bake in and out, and "
        "the control animates on top of it."
    ),
    "vehicle_crash": (
        "Crash Damage",
        "Make walls, poles, barriers or parked cars solid and dent the car "
        "where it hits them.\n\n"
        "1. Select the obstacle meshes and click Add Selected as Obstacles "
        "(not the ground mesh: the wheels already drive on that). Tick They "
        "dent too first for another car or anything that should crumple: "
        "both get dented and meet in the middle.\n"
        "2. Drive into them in Drive Mode, or Simulate Physics on a path "
        "that goes into them. The car stops instead of passing through: the "
        "faster the hit, the deeper the front crushes. A glancing hit knocks "
        "the car sideways. When the pass ends, the dents are baked for you.\n"
        "3. Hand-keyed a crash instead? Click Bake Crash Damage.\n\n"
        "Moving obstacles: key a wrecking ball, a ram or another car and "
        "Simulate Physics. It shoves and dents the vehicle when it hits, "
        "even a parked one.\n\n"
        "The ground counts too (crashGround, on by default): a rollover "
        "crushes the roof, a hard landing that bottoms out dents the "
        "underside. Simulate Physics lands the body on the ground instead of "
        "letting it sink through; metal that touches the ground at rest is "
        "left alone.\n\n"
        "What dents: every mesh bound to the vehicle except the wheels "
        "(body, hood, doors, trunk). Bind them first with Bind Selected Mesh "
        "to Part. The metal that touched is pushed in, the metal around it "
        "is dragged along and buckles.\n\n"
        "Tune on C_chassis_CTRL > CRASH: crashDamage (0 = no dents, keyable), "
        "crashStrength (lower = softer car, deeper crush), crashMaxCrush, "
        "crashSpread, crashCrumple, crashBounce, crashStopsPath, "
        "crashGround. Change a "
        "setting, then bake or simulate again.\n\n"
        "The dents are standard blendShapes in front of the skin, keyed to "
        "the moment of impact, so the file needs no plugin. Game export "
        "ships the undamaged car."
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
