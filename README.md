# Danyal's Rig Builder

**A free, modular auto-rigging toolset for Autodesk Maya** build a fully-controlled,
production-style rig from guides in minutes: biped, quadruped, bird, or vehicle,
with an Advanced-Skeleton-inspired facial system, skinning tools, pose library,
expression presets, and game-engine export.

Built by **Danyal Tareen** · free to use for personal & commercial work ·
[License](LICENSE.txt) (please don't resell or redistribute modified copies
share the official link instead).

![Downloads](https://img.shields.io/github/downloads/coco8897/danyals-rig-builder/total)

---

## Install (30 seconds, drag & drop)

1. **Download** the latest `DanyalsRigBuilder.zip` from the Releases page.
2. **Extract** the zip anywhere (don't skip this Maya can't run it from inside the zip).
3. **Drag `install.py` onto an open Maya viewport.**
   (Leave the `installation_files` folder exactly where it is the installer
   reads from it.)

That's it. A **"Danyal's Rig" (DR)** button appears on your current shelf click it
to open the Rig Builder. Requires **Maya 2022+** (tested on 2023).

To uninstall: `import install; install.uninstall()` in the Script Editor.

## Quick start

1. **Create Guides** → drag the guide locators onto your model (symmetric mode
   auto-mirrors L→R).
2. **Build Rig** → full body rig: IK/FK arms + legs with stretchy IK and bendy
   ribbons, reverse foot with smart roll, ribbon spine, fingers with fist/spread,
   space switching, pose mirroring.
3. **Face** → open the **Advanced Face** window, select your eye + lip edge loops,
   click Fit, drop the eyeball guide in the socket, **Build Advanced Face**
   mesh-conforming lids/lips, spherical-arc blink, sticky lips, smile/frown,
   pucker, lip roll, jaw slide + thrust, brows, cheeks, one-click expressions.
4. **Skin** → **Auto-Skin Everything** (Geodesic Voxel), the gradient bone-falloff
   tool (experimental), mirror/copy/save/load weights, Delta Mush,
   optional NG Skin Tools bridge.
5. **Ship** → **Make Game Skeleton** (single joint hierarchy, face joints folded
   under the head), rig health-check validator, FBX export for Unreal/Unity.

The included **QUICKSTART.md** walks the full workflow.

## Feature highlights

* **4 rig types** biped, quadruped (horse), bird (wings + feathers), vehicle
  (steering, suspension, drivable WASD mode).
* **Advanced Face** fits your actual edge loops; eyelids sweep over an eyeball
  guide sphere; per-joint tweak controls; expression presets saved to the pose
  library; picker face board.
* **Animator tools** anatomical control picker, pose & clip library with
  L↔R flip, IK/FK matching, space switching, auto-walk, secondary-motion
  jiggle chains, corrective pose-readers.
* **Ship-readiness** validator that catches dirty controls, broken skins,
  duplicate names and cycles before you export.

## Feedback please!

This is a **free public test release**. If something breaks or you wish it worked
differently, **open a GitHub Issue** (screenshots + Maya version help a lot) —
that's the whole point of releasing it. 🙏

## Privacy note (telemetry)

On install and first launch the tool sends **one anonymous ping** a random ID,
tool version, Maya version, and OS so I can count installs and see roughly
where it's used. **No personal data, no scene data, ever.** Opt out any time:
set the environment variable `DRB_NO_TELEMETRY=1`, or run
`import rig_telemetry; rig_telemetry.opt_out()`. Full details in
[installation_files/rig_telemetry.py](installation_files/rig_telemetry.py).

## License, in one breath

Free to use (including commercially) · everything you make with it is yours ·
the tool itself stays mine no reselling, no re-hosting, no distributing
modified versions. Full text: [LICENSE.txt](LICENSE.txt).

**Credit is never required but if this tool helped ship something, a
"Rigged with Danyal's Rig Builder" in your credits or post would mean a lot.** 💛

---

*Danyal's Rig Builder © 2026 Danyal Tareen dannytareen21@gmail.com*
