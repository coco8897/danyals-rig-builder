# Danyal's Rig Builder

**A free, modular auto-rigging toolset for Autodesk Maya.** Build a fully-controlled,
production-style rig from guides in minutes: biped, quadruped, bird, vehicle,
motorcycle or prop, plus fantasy creatures (extra arms, legs, wings and tails),
with an Advanced-Skeleton-inspired facial system, skinning tools, pose library,
expression presets, and game-engine export.

Vehicles drive with WASD, simulate real physics on any terrain, tow trailers,
dent when they crash and leave tyre marks where they slide.

Built by **Danyal Tareen** · free to use for personal & commercial work ·
[License](LICENSE.txt). Using it commercially? Just tell me: one email to
dannytareen21@gmail.com or an issue here, no fee and nothing to sign, I only
want to know where it is being used. Please don't resell or redistribute
modified copies, share the official link instead.

![Downloads](https://img.shields.io/github/downloads/coco8897/danyals-rig-builder/total?color=brightgreen)

### 📖 Documentation

| Doc | For |
|---|---|
| **[QUICKSTART.md](QUICKSTART.md)** | **Start here:** zero to a finished, export-ready rig |
| **[CREATURES.md](CREATURES.md)** | Combining limbs for fantasy creatures: four arms, wings, centaurs, insects, extra tails |
| **[VEHICLES.md](VEHICLES.md)** | Cars, 6 / 8-wheel trucks, tanks and motorcycles: driving, physics simulation, turrets, excavator arms, trailers and crash damage |
| **[PIPELINE.md](PIPELINE.md)** | Animators & tech animators: naming, hierarchy, handoff, referencing, troubleshooting |
| **READ ME FIRST.txt** | Included in the download: install steps in plain text |

---

## Install (30 seconds, drag & drop)

1. **Download** the latest `DanyalsRigBuilder.zip` from the Releases page.
2. **Extract** the zip anywhere (don't skip this, Maya can't run it from inside the zip).
3. **Drag `install.py` onto an open Maya viewport.**
   (Leave the `installation_files` folder exactly where it is, the installer
   reads from it.)

That's it. A **"Danyal's Rig" (DR)** button appears on your current shelf. Click it
to open the Rig Builder. Requires **Maya 2022 or newer**, including **Maya 2025 / 2026** (PySide6/Qt6 is handled automatically).

To uninstall: `import install; install.uninstall()` in the Script Editor.

## Quick start

1. **Create Guides** → drag the guide locators onto your model (symmetric mode
   auto-mirrors L→R).
2. **Build Rig** → full body rig: IK/FK arms + legs with stretchy IK and bendy
   ribbons, reverse foot with smart roll, ribbon spine, fingers with fist/spread,
   space switching, pose mirroring. Want a creature? Open **Creature Limbs**,
   add a preset or your own extra limbs, place their guides, then build.
3. **Face** → open the **Advanced Face** window, select your eye + lip edge loops,
   click Fit, drop the eyeball guide in the socket, **Build Advanced Face**:
   mesh-conforming lids/lips, spherical-arc blink, sticky lips, smile/frown,
   pucker, lip roll, jaw slide + thrust, brows, cheeks, one-click expressions.
   Then **Add Shape Dials** for the 52 face shapes (ARKit names) and visemes.
4. **Skin** → **Auto-Skin Everything** (Geodesic Voxel) or the gradient
   bone-falloff tool, **Smooth Weights** and **Tidy Weights**,
   mirror/copy/save/load weights, Delta Mush, optional NG Skin Tools
   bridge.
5. **Ship** → rig health-check validator, then **Export Rig** and **Export
   Animation** / **Clips** to FBX for Unreal / Unity: a clean skeleton with
   optional root motion, and your rig is never changed by exporting.

The included **QUICKSTART.md** walks the full workflow.

## Feature highlights

* **6 rig types**: biped, quadruped (horse, cat / dog or raptor), bird
  (wings + feathers), vehicle (4 / 6 / 8 wheels or tank treads, drivable
  WASD mode with a real handbrake drift, and a physics pass that bakes
  suspension, body roll, jumps and landings onto any terrain, with a
  keyable manual override), motorcycle, and props.
* **Motorcycles**: a fork that steers about the rake you gave it and
  telescopes, a swingarm that swings and aims at the rear axle, and a bike
  that leans about its tyres. WASD drives it and leans it into every
  corner by itself.
* **Quadruped feet**: hooves, paws that stand on their toe pads, raptor
  claws with a sickle claw and dewclaw, or clawed arms off the ground.
  Pick them per leg pair and mix them (a griffin: claws front, paws back).
  Toes curl and spread, and the foot rolls from heel to ball to toe tips.
* **Props**: a sword, a gun or a chair in one click: root, move and attach
  joints, a grip that snaps into a character's hand and picks up / puts
  down without a jump, and its own FBX export. Any number per scene.
* **Vehicle parts**: turrets that aim at a target, excavator arms with
  hydraulic rams, tipper beds and your own hinges, all riding the physics.
* **Tyre tracks and burnouts**: bake the marks the wheels leave, only
  where the tyres were actually sliding (a drift, a spin, wheelspin, a
  locked wheel) so a clean lap stays clean, or everywhere they touch for
  sand and mud. Burnout spins the driven wheels while the vehicle creeps
  and squats, and lays the wide dark marks itself.
* **Trailers**: tow up to 3 in a chain. They swing behind the hitch, cut
  corners, jackknife in reverse and ride the terrain on their own wheels.
* **Tank wheels, any layout**: as many road wheels, rollers and gears as
  the model has, each its own size, fitted from the meshes in one click.
  The tread wraps them like a rubber band and every wheel spins for its
  size.
* **Springs**: pick each axle's suspension: coil-overs, or leaf springs
  that flatten and arch on a swinging shackle with telescoping shocks, and
  solid axles that tilt both wheels over a bump.
* **Cargo**: spare wheels, jerry cans and roof-rack gear lean when you
  accelerate or brake, sway in turns, hop over bumps and rattle with speed.
* **Crash damage**: tag walls, poles or parked cars as obstacles. Drive Mode
  and the physics pass stop at them, and the body dents where it hit
  (pushed in, dragged along, buckled), keyed to the moment of impact. The
  ground counts too: rollovers crush the roof, hard landings dent the
  underside. Crash into another car and both crumple; key a wrecking ball
  into a parked car and the physics pass shoves and dents it.
* **Creature limbs + custom chains**: add extra arms, legs, tails or your own
  FK / IK / FK+IK joint chains to the biped, attached to any joint (presets
  for Dragon, Four-Armed, Winged, Centaur and Six-Legged). Each one is a full
  rig with its own picker tab, auto-walk, space switching and IK/FK match.
* **EZ or Free guides**: place guides one by one, or link them like a
  skeleton so moving a shoulder brings the whole arm.
* **Necks with an IK head**: give a neck as many joints as it needs and an
  IK head control the whole neck arcs to follow, switchable to an FK chain.
  The Dragon preset ships with it.
* **Fit Guides to Selected Mesh**: select your model and the guides take its
  size, wherever it stands and whatever units it was modelled in. The rig
  builds at that size too, controls and joints included. Biped, quadruped,
  bird and vehicle.
* **Advanced Face**: fits your actual edge loops; eyelids sweep over an eyeball
  guide sphere; per-joint tweak controls; expression presets saved to the pose
  library; picker face board; mouth corner controls; one smile that lifts the
  cheeks too. **Smart Bind Face** skins it right even on one head + body mesh:
  the body keeps its skin, the lips split at the mouth opening so it opens,
  eyeballs, teeth, lashes and brows each bound the way they move.
* **Face Shapes**: one control with the 52 standard face tracking dials
  (eyeBlinkLeft, jawOpen, mouthSmileLeft, browInnerUp ... the names iPhone
  ARKit and MediaPipe use), each driving your face rig on top of hand posing.
  Lip sync visemes built from them, and lids that follow the eyes.
* **Face Capture**: animate the face with your own face from a PC webcam or a
  phone camera (MediaPipe tracking, runs on your computer), calibrate, tune
  and record straight to keys, head turns included. Imports iPhone Live Link
  Face CSVs too.
* **Walk Mode (WASD)**: walk, run, turn and jump a character with the
  keyboard and keep the animation: planted feet, body bob and lean, arm
  swing, terrain. Bipeds, creatures, quadrupeds and birds.
* **Fly Mode (WASD)**: fly a bird or a dragon like a game and keep the
  animation: take off, real wingbeats with glides, banked turns, dives, and
  landings where the legs reach forward and the wings fold.
* **Tail physics**: simulate and bake follow-through on any tail. It swings
  when the body speeds up, slows or turns and settles when it stops, keyed
  on a layer you can dial from 0 to 1.
* **Animator tools**: anatomical control picker, pose & clip library with
  L↔R flip, IK/FK matching, space switching, auto-walk, secondary-motion
  jiggle chains, corrective pose-readers.
* **Ship-readiness**: validator that catches dirty controls, broken skins,
  duplicate names and cycles before you export.

## Feedback, please!

This is a **free public test release**. If something breaks or you wish it worked
differently, **open a GitHub Issue** (screenshots + Maya version help a lot).
That's the whole point of releasing it. 🙏

## Privacy note (telemetry)

On install and first launch the tool sends **one anonymous ping** (a random ID,
tool version, Maya version, and OS) so I can count installs and see roughly
where it's used. **No personal data, no scene data, ever.** Opt out any time:
set the environment variable `DRB_NO_TELEMETRY=1`, or run
`import rig_telemetry; rig_telemetry.opt_out()`. Full details in
[installation_files/rig_telemetry.py](installation_files/rig_telemetry.py).

## License, in one breath

Free to use (including commercially) · everything you make with it is yours ·
the tool itself stays mine: no reselling, no re-hosting, no distributing
modified versions. Full text: [LICENSE.txt](LICENSE.txt).

**Credit is never required, but if this tool helped ship something, a
"Rigged with Danyal's Rig Builder" in your credits or post would mean a lot.** 💛

---

*Danyal's Rig Builder © 2026 Danyal Tareen, dannytareen21@gmail.com*
