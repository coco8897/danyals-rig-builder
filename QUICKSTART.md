# Danyal's Rig Builder — Quickstart

Zero to a skinned, validated, export-ready rig. Five stages, top to bottom of
the panel. If you only read one doc, read this one.

---

## 0. Launch

Drag `install.py` onto Maya's viewport once — a **Danyal's Rig** shelf button
appears. Or from the Script Editor (Python):

```python
import rig_ui
from importlib import reload; reload(rig_ui)
rig_ui.show()
```

Pick your character type from the **mode** dropdown at the top:
**Biped · Quadruped · Bird · Vehicle · Motorcycle · Prop**.

---

## 1. Guides — rough in the proportions

1. **Create Guides** — colored locators spawn at default proportions.
2. **Fit Guides to Selected Mesh** — select your model and click: the guides
   scale to its height, stand on its lowest point and centre on it. (Create
   the guides with the model selected and this happens by itself.)
3. Drag them to match your mesh. Work the **left** side only.
4. **Mirror L → R** to copy the left onto the right.
5. **Save Guides…** to keep the layout as JSON (survives a rebuild).

> **Guides 100x too big?** Your model is probably at game scale (1 unit =
> 1 metre) while the guides are a 170 cm human in Maya centimetres. **Fit
> Guides to Selected Mesh** sorts it in one click, and the rig then builds
> at that size, controls and joints included. Quadruped, Bird, Vehicle and
> Motorcycle have the same button (the vehicle's is **Fit Guides to
> Model**, the bike's is **Fit Guides to Selected Model**).

> **A-pose, T-pose, or anything else?** All fine — just drag the guides onto
> the pose your model is actually in. Only the guide **positions** are read
> (never their rotation), and each joint is oriented by aiming it down the
> chain — so an A-pose arm gets correctly-oriented joints automatically.
> Tip: to move a whole limb at once, select several guides together, or move
> the `RIG_GUIDES_GRP` group.

> Have a mesh already? Import it first into a group named `geo` / `Geo` / `GEO`
> so visibility wiring hooks up automatically at build time.

---

## 2. Build — make the rig

1. Tick the modules you want (Spine / Arms / Legs / Face / Fingers / Tail) and
   the Face sub-modules.
2. **Build Rig** — the full rig appears under `CHARACTER_RIG_GRP`.
3. **Verify Build** (optional) — prints a guide-vs-joint position diff.

Everything you skin to is suffixed **`_BIND_JNT`**. FK/IK driver chains are
hidden helpers — never skin to those.

---

## 3. Skin — bind the mesh

1. **Select All Meshes in Geo Group**.
2. **Auto-Skin (Geodesic Voxel) → BIND joints** — topology-aware starting
   weights, 5 influences. Or **Gradient Skin: All Bones** for weights that
   follow the bones and stay on each part's own surface, so a hand resting
   by a hip doesn't pick up hip weights.
3. **Smooth Weights** (passes 3) softens the hard lines where one joint's
   area meets the next. Select just an armpit or a shoulder's vertices to
   smooth only those.
4. **Tidy Weights** keeps the 4 strongest joints per vertex and normalises:
   what a game engine expects.
5. **Apply Delta Mush** to smooth artifacts non-destructively.
6. Paint to taste. **Mirror Wts L → R** to keep both sides symmetric.

---

## 4. Finishing — the last-mile passes  *(new)*

The **Rig Finishing (pre-export)** section, run once the rig deforms:

* **Add Volume Correctives** — pose-reader-driven joints on the
  shoulders / elbows / hips / knees. Each fires as the limb flexes so you can
  paint volume back into the crease (the candy-wrapper / pinched-elbow fix).
  They're named `*_corrective_BIND_JNT`, so the skin tools pick them up — just
  bind and paint the small amount of fill you want at full bend.

* **Make Selected Chain Dynamic** — select a joint chain (tail, ponytail, ear,
  rope, antenna) and drape an nHair dynamic over it for jiggle and
  follow-through. One `*_dynamics_CTRL` holds the dials:

  | dial | does |
  |---|---|
  | `follow` | how hard it snaps back to the rig pose (1 = rigid) |
  | `stiffness` | resistance to bending |
  | `damping` | how fast the wobble dies down |
  | `drag` | air resistance |
  | `gravity` | world pull |
  | `startFrame` | frame the sim starts settling from |

  Play the timeline to watch it settle, tune the dials, then **bake the joints**
  before export (the dynamic nodes don't need to ship — just the motion).

---

## Quadrupeds: horse, cat / dog, raptor

Pick **Quadruped** in the mode dropdown, then choose an **Animal**. It sets
the feet for you, and you can change **Front** and **Back** on their own:

| Foot | For | Stands on |
|---|---|---|
| **Hoof** | horse, deer, cow | the tip of the hoof, fetlock raised |
| **Paw** | cat, dog, wolf | the toe pads, wrist / ankle raised, four toes |
| **Claw** | raptor | three toes on a long raised ankle bone, sickle claw held up, small dewclaw |
| **Arm** (front only) | raptor arms | nothing: held off the ground, three clawed fingers |

1. **Create Guides** and drag them onto your model. Moving a paw, ball or
   wrist guide brings its toes or fingers along.
2. Changed your mind about the feet after placing guides? Pick new
   **Front** / **Back** feet and click **Create Guides** again. Only the legs
   below the shoulders and hips are swapped. **Reset to Defaults** switches
   to a different animal.
3. **Mirror L → R**, then **Build Quadruped Rig**. Untick **Toes and
   fingers** for one piece per foot.

On a paw or claw foot control: `roll` (heel, then ball, then toe tips, like
the biped foot), `heelRoll`, `ballRoll` (the wrist or ankle swings up while
the toes stay planted), `toeRoll` (up on the claws), `toeBend`, `footBank`.
The leg's SETTINGS control has `toeCurl`, `toeSpread` and, on claws,
`sickleClaw` (+10 raises it, -10 strikes). Raptor arms have an IK hand that
rides the chest, plus `fingerCurl` and `fingerSpread`.

---

## Walk Mode: WASD for characters

Click **Walk Mode (WASD)** (next to Open Picker), click the little panel,
and walk your character around like a game.

| Key | Does |
|---|---|
| **W / S** | Walk forward / back |
| **A / D** | Turn (steps round on the spot when standing) |
| **Shift** | Run |
| **Space** | Jump |
| **Esc** | Stop and key it |

* Feet step and **stay planted** while the body passes over them, with heel
  strike and toe-off. The COG bobs, sways and leans into speed and turns,
  and the hips and chest twist.
* The **arms come down** from the T or A pose to hang relaxed, and swing from
  the shoulder opposite the legs, elbows bending more when you run (IK or
  FK arms). Set **Arms down** to 0 to keep the rig's own arm pose.
* **Space** jumps properly: a quick crouch with the arms swinging back, a
  push off the toes, legs tucked and arms thrown up in the air, then the
  feet land first and the knees soak it up.
* Stride and speed scale with the leg length, so giants take big slow steps.
* **Use Selected as Ground** in the panel: feet land on a terrain mesh
  (slopes, steps) and the hips follow. Legs never over-stretch.
* Works on bipeds, creatures with extra legs, quadrupeds (a trot, paws roll
  off their toes), raptors (two legs, arms kept folded) and birds. Legs must
  be in IK.
* It records from the current frame and keys when you stop, so walk again
  from the last frame to keep going. **Clear Walk Animation** starts over.
* Tune Walk / Run speed, Turn rate, Stride, Step height, Body bob, Lean, Arms
  down, Arm swing and Jump in the panel.

---

## Fly Mode: WASD for birds

Build a bird (or a biped with the **Dragon** creature preset), then click
**Fly Mode (WASD)** at the top of the panel, next to Walk Mode. Click the
little panel and **Start Flying**.

| Key | Does |
|---|---|
| **Space** | Take off from the ground, or climb |
| **W** | Fly faster, harder wingbeats |
| **S** | Slow down and flare. Hold it near the ground to land |
| **A / D** | Bank and turn |
| **Shift** | Dive with the wings tucked back |
| **Esc** | Stop and key it |

* Take off: a crouch, a jump and big wingbeats, then the legs tuck up with
  the talons closed.
* Let go of the keys and the bird cruises level, gliding between bursts of
  wingbeats. The downstroke sweeps the wings forward fully spread, the
  upstroke flexes the elbow and wrist and closes the feathers.
* Turns bank the body by the real angle for the speed, the tail swings into
  the turn and the head stays level.
* Land by holding **S** close to the ground: the body flares up, the legs
  reach forward with the talons open, the feet plant and the wings fold.
  Diving at the ground without **S** skims over it instead.
* **Use Selected as Ground** takes off from and lands on a terrain mesh.
* Works with IK or FK wings. It records from the current frame and keys when
  you stop, so fly again from the last frame, or walk off with Walk Mode
  after landing. **Clear Fly Animation** starts over.
* Tune the speeds, climb, turn rate, bank, wingbeats per second, wingbeat
  size, glide and body motion in the panel. Speeds and wingbeats scale with
  the wingspan.

---

## Tail physics: simulate and bake

Follow-through for tails, like the vehicle's Simulate & Bake. Animate the
character first (hand keys, Walk Mode or Fly Mode), then open **Tail Physics
(simulate & bake)**:

1. Pick the **Tail** (click **Refresh** after building a rig).
2. Set the dials:
   * **Stiffness**: how fast it springs back. Higher for a stiff raptor
     tail, lower for a floppy cat tail.
   * **Damping**: how quickly the wobble dies away.
   * **Swing**: how far it swings.
   * **Whip**: how much looser the tip is than the base.
3. **Use Timeline** (or type the frames) and **Simulate & Bake Tail**.

The tail swings when the body speeds up, slows down or turns, and settles
when it stops. It's written as keys, so it scrubs, renders and exports
exactly. Change the animation or the dials and bake again.

Your animation is never changed: the swing sits on a physics layer, and the
**physics** dial on the tail's SETTINGS control blends it (0 = your
animation only, keyable). **Clear Tail Physics** deletes the swing, **Remove
Layer** takes the layer off the rig. Works on biped, quadruped and raptor
tails, extra tails and custom chains, in IK or FK.

---

## Face Capture: animate the face with your webcam or phone

**One-time setup:** install Python (3.9 to 3.13, from python.org) and run
`python -m pip install mediapipe` in a command prompt. The first time the
tracker starts it downloads Google's face model (about 4 MB). Tracking runs
on your computer; nothing is sent anywhere.

1. Build the character with a face and the **Advanced Face**. In the Advanced
   Face window click **Add / Rebuild Shape Dials**: `C_faceShapes_CTRL` gets
   52 dials named like iPhone ARKit (`eyeBlinkLeft`, `jawOpen`,
   `mouthSmileLeft` ...). They work for hand keying too, and the **Visemes**
   row sets lip sync mouth shapes (Shift+click keys them).
2. Click **Face Capture (webcam / phone)** under Face submodules and **Start
   Tracker**. A window opens with your camera and the face points.
   * **PC webcam:** camera 0 (or 1, 2 ... if you have more).
   * **Phone as a webcam:** DroidCam or Iriun make the phone a camera number.
   * **Phone stream:** an IP camera app on the same Wi-Fi, pick *Phone or IP
     camera stream* and paste its video address.
   * **Video file:** track a clip you already filmed.
3. Relax your face, look straight at the camera, **Calibrate Neutral**. Then
   **Learn Range (10 s)** and pull your biggest faces: open wide, blink hard,
   smile, frown, brows up and down, puff, pucker, look around. Every dial
   learns your real full value, so a wide-open mouth opens the jaw all the
   way and a blink closes the eyes. Your calibration is saved for next time.
4. Pull faces and tune: **Blink / Eyes / Brows / Mouth / Jaw / Cheeks /
   Tongue** gains, **Smoothing** (steadier or snappier) and **Head** (how far
   the neck and head turn with yours). Wink your left eye: if the character's
   other eye closes, tick **Mirror L/R**.
5. **Drive:** tick only the parts you want your face to move: **Eyes, Brows,
   Mouth, Cheeks, Head**. Untick **Head** to keep the neck and head still and
   animate only the face; untick **Brows** and **Cheeks** for eyes and mouth
   only. Anything unticked is left exactly as it is, and recording doesn't
   touch its keys.
6. **Record** from the current frame, perform, click again to stop. Every
   frame is keyed on the dials and the neck / head; recording again over the
   same frames replaces them. **Clear Capture Keys** removes them all.
7. **Stop and Reset** closes the camera window and puts the face back to
   neutral (your keys stay). Closing the panel or pressing **Esc** does the
   same, so the camera never keeps running behind you.

**Import CSV** keys an iPhone **Live Link Face** recording, or a CSV from the
tracker (`python face_tracker.py --video take.mp4 --csv take.csv`), from the
current frame with the same gains and mirror.

---

## 5. Validate — before you export

* **Validate Rig** — a ship-readiness health check. It flags:
  * controls left **off their rest pose** (the #1 thing that ships broken),
  * meshes **not bound** or bound by **more than one** skinCluster,
  * leftover **construction history**,
  * **duplicate node names** (they break every name-based tool),
  * **scaled joints**, **junk / unknown nodes**, and **dependency cycles**.

  The report prints to the Script Editor, grouped **ERROR → WARN → INFO**.

* **Validate + Auto-Clean** — same check, then deletes the *safe* junk
  (unknown nodes, unused utility nodes, empty display layers). It never
  touches your geometry or weights.

**Rule of thumb:** ship when there are **0 errors**. Warnings are judgement
calls (an unbound prop mesh is fine; a dirty control usually isn't).

---

## 6. Export

Open **Game Export (Unreal / Unity)**. Exporting never changes your rig: a
clean copy of the skeleton is baked, written to the FBX and deleted, so the
file holds real bones only (no controls or groups) and your rig keeps working.

* **Export Rig (.fbx)**: the skeleton in its rest pose plus every mesh skinned
  to it, with the same weights. Import this into the engine first.
* **Export Animation (.fbx)**: set the **Frames** (or **Use Timeline**) and
  export. Name the file after the move (walk_fwd.fbx): the engine names the
  animation after the file.
* **Root bone at the ground** (on by default): a `root` bone carries the
  character's travel, for root motion. Keep it the same for the rig and all
  its animations. **In place** keeps the character at the origin instead.
* **Clips**: type a name, set the frames, **Save as Clip**, repeat for each
  move, then **Export All Clips to Folder** for one FBX per clip. Clips are
  saved with the scene.
* **Include ribbon bendy joints** adds the ribbon joints (joints your skin
  uses are always exported).

**Make Game Skeleton** is only for skinning (it gives Auto-Skin one clean
hierarchy); exporting doesn't need it.

---

## Cheat sheet

| Want to… | Do this |
|---|---|
| Re-pose to a saved pose | Animation Tools → Pose Library |
| Switch an IK hand to World | Animation Tools → Add Space Switches |
| Fix a collapsing elbow | Finishing → Add Volume Correctives |
| Make a tail jiggle | select it → Finishing → Make Selected Chain Dynamic |
| Check it's clean | Finishing → Validate Rig |
| Edit the face in detail | top bar → Advanced Face |
| Face tracking style dials (52 shapes) + visemes | Advanced Face → Add / Rebuild Shape Dials |
| A closed eye still shows a sliver | Advanced Face → Repair Face, then Smart Bind Face (or raise `lidOverlap` on the blink control) |
| Animate the face from a webcam or phone | Face submodules → Face Capture |
| Rig a motorcycle | mode **Motorcycle** → Create Bike Guides → Fit Guides to Selected Model → Build (see [VEHICLES.md](VEHICLES.md)) |
| Ride it | Animate → Drive Mode (WASD): it leans into the corners itself |

Source is one file per module and heavily commented — open the module whose
name matches the feature (`rig_correctives.py`, `rig_dynamics.py`,
`rig_validate.py`, …) if you want to see how it works or tune a default.
