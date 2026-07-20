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
**Biped · Quadruped · Bird · Vehicle**.

---

## 1. Guides — rough in the proportions

1. **Create Guides** — colored locators spawn at default proportions.
2. Drag them to match your mesh. Work the **left** side only.
3. **Mirror L → R** to copy the left onto the right.
4. **Save Guides…** to keep the layout as JSON (survives a rebuild).

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
   weights, 5 influences.
3. **Apply Delta Mush** to smooth artifacts non-destructively.
4. Paint to taste. **Mirror Wts L → R** to keep both sides symmetric.

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

* **Make Game Skeleton** strips the rig down to the `_BIND_JNT` hierarchy.
* **Export FBX** writes the skinned mesh + skeleton (+ baked animation) for
  Unreal / Unity.

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

Source is one file per module and heavily commented — open the module whose
name matches the feature (`rig_correctives.py`, `rig_dynamics.py`,
`rig_validate.py`, …) if you want to see how it works or tune a default.
