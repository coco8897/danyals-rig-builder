# Pipeline & Integration Guide

**Who this is for:** animators, technical animators, and anyone folding these
rigs into an existing production pipeline — not just the person who pressed
"Build Rig".

`QUICKSTART.md` covers *how to build a rig*. This covers *how to work with one
on a team*: what the rig guarantees, what you can rely on, what will break it,
and where it hands off.

---

## 1. Where this sits in a production

```
   MODEL  →  RIG  →  ANIMATE  →  EXPORT / RENDER
             ▲         ▲            ▲
             │         │            │
      Rig Builder  animator     Export Rig +
      (this tool)  works only   Export Animation
                   on _CTRLs
```

The tool is a **rig-construction** tool. It is deliberately *not* in the
animator's way afterwards: once a rig is built, everything an animator touches
is a normal Maya control curve with normal keyable channels. There is no custom
node type, no plugin dependency, and no runtime requirement — an animator with
a plain Maya install can open, animate, and render a scene containing one of
these rigs **without installing anything.**

> **Practical consequence:** only the *rigger* needs the tool installed.
> Animators receive a normal Maya file.

---

## 2. The scene contract

Every biped rig builds this structure. Tools, referencing, and export all rely
on it — treat it as the interface.

```
CHARACTER_RIG_GRP           top node — reference / namespace this
└── C_global_CTRL           whole-character transform + globalScale
    ├── controls_GRP        every animator control
    ├── joints_GRP          the deforming skeleton
    └── misc_GRP            hidden helpers (ribbons, follicles, IK guts)
ADV_FACE_GRP                advanced-face system (if built)
geo / Geo / GEO             your model
```

Other modes build the same shape under `QUADRUPED_RIG_GRP`,
`BIRD_RIG_GRP`, `VEHICLE_RIG_GRP`.

### Naming conventions

| Pattern | Meaning | Animator touches it? |
|---|---|---|
| `L_` `R_` `C_` prefix | left / right / centre | — |
| `*_CTRL` | **animation control** | ✅ yes — this is your surface |
| `*_BIND_JNT` | deforming joint (skin target) | ❌ never key these |
| `*_OFFSET` / `*_AUTO` | rest-pose and automation groups above a control | ❌ no |
| `*_drv_*` / `*_IK_*` / `*_FK_*` | internal driver chains | ❌ no |

Two rules cover 95% of pipeline problems:

1. **Animate `_CTRL` nodes only.**
2. **Skin to `_BIND_JNT` joints only.** Driver chains are hidden for a reason.

### Rest pose

Every control is **zeroed at rest** — `translate/rotate = 0`, `scale = 1`.
Positional offset is carried by the `_OFFSET` group above it, not the control.

That means an animator can select every control, hit **Reset Transformations**,
and land exactly on the bind pose. Use that as your "back to neutral". It also
means a control sitting at non-zero values in a delivered file is a *dirty
control* — see §7.

---

## 3. Handoff: rigger → animator

**What the rigger delivers:**

- A Maya scene containing `CHARACTER_RIG_GRP` + the bound `geo` group.
- Validator run clean (§7) — **0 errors**.
- All controls zeroed, no leftover test keys, timeline at start frame.

**How the animator consumes it:** reference it, don't import it.

```
File → Create Reference…  →  character.ma      namespace: e.g. "hero"
```

Referencing means rig fixes propagate to shots already in progress. Because the
rig is name-driven, keep the namespace consistent across shots
(`hero:C_global_CTRL`, etc.).

**Scaling the character:** use `C_global_CTRL.globalScale` — never scale the
group above it. Stretchy IK, ribbons and bendy joints are all wired to respect
`globalScale`; a raw group scale will double-transform.

---

## 4. What the animator gets

| System | Where | Notes |
|---|---|---|
| IK / FK switching | each limb's settings control | blend attribute, 0 = FK, 1 = IK |
| IK/FK match | Animation Tools → Match IK/FK | snaps one to the other without a pop |
| Space switching | Animation Tools → Add Space Switches | IK hands/feet/head/poles → World / COG / parent |
| Pose library | Animation Tools → Pose Library | save / apply / **mirror L↔R** poses and clips |
| Control picker | Open Picker | anatomical selector — no hunting in the viewport |
| Face | Advanced Face controls + expression presets | see §5 |
| Secondary motion | `*_dynamics_CTRL` | jiggle / follow-through; **bake before export** |

**Auto behaviours** (fist curl, smile cascade, blink, jaw follow) are written to
`_AUTO` groups *above* the controls, so an animator's manual offset always
composes on top rather than fighting it. Dial the attribute *and* hand-pose the
control — both apply.

---

## 5. The face

The advanced face conforms to your model's actual edge loops, so it is fitted
per-character rather than assumed.

**Rigger's face workflow:** Advanced Face window → fit the eye and lip loops →
place the eyeball guide sphere → **Build Advanced Face** → bind → optionally
**Add Per-Joint Tweak Controls** for fine cleanup.

**Animator's face surface:**

| Control | Key attributes |
|---|---|
| `L/R_blink_CTRL` | `blink`, `blinkHeight`, `lidOverlap` (how far the lids close past each other, set by Smart Bind Face), `lidFollow` (with the shape dials) |
| `C_mouth_CTRL` | `smile` (lifts the cheeks too), `pucker`, `lipRoll`, `zip`, `jawFollow` |
| `L/R_mouthCorner_CTRL` | translate to pose both lips' corner |
| `C_jaw_CTRL` | rotate to open, plus `jawSide`, `jawThrust` |
| `L/R_brow*_CTRL` | `raise`, `furrow` (inner brows) |
| `L/R_cheek_CTRL` | `puff`, `cheekRaise` |
| Expression presets | one click: Neutral / Smile / Sad / Angry / Surprised / Kiss |

Presets set the dials and can be installed into the pose library, so they behave
like any other saved pose (blend, key, mirror).

**Face shape dials:** in the Advanced Face window, **Add / Rebuild Shape Dials**
puts `C_faceShapes_CTRL` on the face with 52 dials (0 to 1) named like iPhone
ARKit and MediaPipe face tracking: `eyeBlinkLeft`, `eyeLookUpLeft`, `jawOpen`,
`mouthSmileLeft`, `mouthFunnel`, `browInnerUp`, `cheekPuff`, `tongueOut` and the
rest. Each one drives the rig you already have and adds on top of the other
dials and hand posing, so they're good for keyframing and they're what face
capture data plugs into. Left and Right are the character's own sides. Parts a
face doesn't have are hidden. The visemes row (AI, E, O, U, MBP, FV, L, etc)
sets lip sync mouth shapes from the dials; Shift+click keys them. The dials
also add `lidFollow` to the blink controls: the lids ride up and down with the
eyes. Rebuilding the Advanced Face keeps the dials and their values; Remove
takes them off cleanly. Exporting zeroes them for the bind pose, like every
other control.

In Python: `import face_shapes; face_shapes.build()`, `face_shapes.set_values({"jawOpen": 0.5})`,
`face_shapes.apply_viseme("O", key=True)`, `face_shapes.remove()`.

**Face capture:** `face_tracker.py` runs outside Maya in a Python with
MediaPipe (`python -m pip install mediapipe`) and streams the 52 shapes plus
head rotation as small JSON packets over UDP to 127.0.0.1 (port 54321 by
default). The **Face Capture** panel (`face_capture.show()`) listens, removes
the calibrated neutral, applies per-group gains, mirror and One Euro
smoothing, and drives `C_faceShapes_CTRL` and `C_neck_CTRL` / `C_head_CTRL`
(35 / 65 split) without touching undo or auto key. Record resamples the take
onto whole frames and writes keys from the current frame, replacing keys in
that range. `face_capture.import_csv(path)` keys a Live Link Face CSV (frame
rate read from its timecodes) or a tracker CSV. Left and Right are the
performer's own sides, like ARKit. The **Drive** switches limit capture to
whole regions (eyes, brows, mouth, cheeks, head); an unticked region is
neither driven nor keyed. **Stop and Reset**, closing the panel and Esc all
stop the tracker (it listens for a quit on the port above the data port),
zero the dials and put the neck and head back.

`showTweaks` on a blink or mouth control reveals the per-joint tweak controls
for surgical fixes; leave it off for day-to-day animation.

---

## 6. Skinning notes for tech animators

Skin targets are `_BIND_JNT` only. Beyond that:

**Smart Bind Face** (Advanced Face window) is the face bind for every case.
Select the character's meshes, or nothing to use everything in the `geo`
group, and click it. Each mesh is recognised and bound the way that part
moves:

| Mesh | Gets |
|---|---|
| One head + body mesh, or a head mesh | keeps its body skin (makes one if it has none, or only has face joints from an older bind), then a face layer only around the face: the jaw takes the lower face with soft edges into the cheeks and neck; lips and lids are split by the mesh's own mouth / eye openings, following the surface, so the mouth opens and the lids close; brows, cheeks, nose and ears get soft falloffs. Nothing below the neck is touched. |
| Eyeballs | 100 % on the eye joint |
| Eyelashes | the lash row of their lid |
| Separate brows | a copy of the skin under them |
| Upper / lower teeth, tongue | the teeth joints, the tongue chain |
| Inner mouth | head above the lip line, jaw below |
| Hair / hat | 100 % head |

Run it again any time (after moving a face joint, say): the face layer is
redone from scratch. It measures with every control at rest, so a posed
face (a blink left on, a capture take) doesn't throw it off. The rim of each
eye opening goes fully on the lash joints, and it sets each blink control's
`lidOverlap` so the mesh's eye opening actually shuts on a full blink. In
Python: `import face_bind; face_bind.smart_bind()`.

**Lid seal:** the blink closes each lid along a curve through its master
controls; between masters the upper and lower curves bend differently, so
before the seal a lash joint here and there stayed a little open. Now, over
the last 30 % of a blink, every lash joint meets the point straight across
on the other lid (where depends on `blinkHeight`), never going inside the
eyeball. Faces built before this: **Repair Face** in the Advanced Face
window adds it (plus the mouth corner and smile fixes) without a rebuild.

**One combined head+body mesh, other routes:**

| Route | When | How |
|---|---|---|
| **A — export-first** | heading for a game engine | Build rig → Build face → **Make Game Skeleton** → **Auto-Skin Everything**. Folding the face joints under the head puts them in the single hierarchy, so auto-skin includes them. |
| **B — non-destructive** | keeping the full control rig | **Auto-Skin Everything** first → build face → **Smart Bind Face**. The body's weights are kept and only the face is layered on top. |

Do not run route B's face bind on a mesh you intend to re-auto-skin afterwards;
auto-skin skips already-bound meshes.

**Other passes:** Delta Mush for non-destructive smoothing · volume correctives
for elbow/knee collapse · gradient bone-falloff skinning (marked experimental)
for a clean tubular starting point on a limb chain · mirror / copy / save / load
weights (weights survive a rig rebuild via save → rebuild → bind → load).

---

## 7. Validate before every handoff

**Rig Finishing → Validate Rig** is the pre-flight check. It reports
ERROR / WARN / INFO for:

- controls left off their rest pose (**the most common broken delivery**)
- meshes unbound, or bound by more than one skinCluster
- leftover construction history
- duplicate node names — these break every name-driven tool, including this one
- scaled joints, junk/unknown nodes, dependency cycles

**Ship at 0 errors.** Warnings are judgement calls: an unbound prop mesh is
fine, a dirty control usually is not. **Validate + Auto-Clean** removes only
provably safe junk and never touches geometry or weights.

---

## 8. Export to Unreal / Unity

Exports never change the working rig. Each one bakes a temporary clean copy
of the skeleton (same bone names), writes it and deletes it, so the FBX holds
real bones only and the rig file stays safe to keep animating in.

1. **Export Rig (.fbx)**: the skeleton in its rest pose plus every skinned
   mesh with its weights. Import it into the engine once as the skeletal
   mesh.
2. **Export Animation (.fbx)** for each move, or save **Clips** (name +
   frames) and **Export All Clips to Folder**. One move per file; the engine
   names the animation after the file.
3. Keep **Root bone at the ground** the same for the rig and every
   animation. On: a `root` bone at the ground carries the travel (root
   motion). **In place** keeps the character at the origin for cycles the
   engine moves.

The skeleton in the file is: `root` (optional), `C_root_BIND_JNT`, then every
deform joint under its nearest deform parent (loose chains under
`C_root_BIND_JNT`, face lid / lip joints under the head). Dynamic chains,
vehicle wheels, treads and trailers are baked like everything else. Crash
dents (vehicle blendShapes) stay in Maya: the exported car is undamaged.
**Make Game Skeleton** is still there for the export-first *skinning* path
below, but exporting doesn't need it.

The tag `builtWith` on `C_global_CTRL` records the tool version a rig was built
with. Handy when a file resurfaces a year later.

---

## 9. Team adoption checklist

- [ ] One rigger installs the tool; animators need nothing.
- [ ] Agree a **namespace convention** for referenced characters.
- [ ] Agree where **guide JSON** files live — they are the rig's source of
      truth and let anyone rebuild a character from scratch.
- [ ] Save **skin weights** to disk (`Save Weights…`) and commit them alongside
      the guides; that pair makes a rig fully reproducible.
- [ ] Validator clean before any handoff.
- [ ] Decide route A or B (§6) for your mesh topology *before* skinning starts.
- [ ] If your studio blocks outbound traffic, switch the anonymous install
      counter off (`DRB_NO_TELEMETRY=1`) — see README.

---

## 10. Troubleshooting

| Symptom | Cause / fix |
|---|---|
| Shelf button missing after install | No active shelf at install time. Run `import rig_ui; rig_ui.show()` from the Script Editor. |
| Rig double-transforms when scaled | A group above `C_global_CTRL` was scaled. Use `globalScale` instead. |
| Face doesn't deform after skinning | Mesh was bound before the face was built, or bound to body joints only. See §6. |
| Blink deforms nothing | The face mesh isn't bound to the lid joints — run **Smart Bind Face**. |
| Limb pops on IK/FK switch | Use **Match IK/FK** rather than switching the blend attribute raw. |
| A tool can't find a node | Duplicate names in the scene. Run the validator. |
| Rebuild fails: "rig already exists" | Delete `CHARACTER_RIG_GRP` first (or use Delete Rig). |
| `No module named 'PySide2'` on Maya 2025/2026 | Fixed in v1.0.1 — update to the latest release. |
| Model is in an A-pose, not a T-pose | Just place the guides on the A-pose. Only guide *positions* are read; joints orient down the chain automatically. |

---

## 11. Feedback

This is a public test release and the roadmap is driven by what people actually
hit. Issues, requests, and pipeline war stories all welcome:

**https://github.com/coco8897/danyals-rig-builder/issues**

*Danyal's Rig Builder © 2026 Danyal Tareen*
