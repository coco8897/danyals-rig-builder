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
      Rig Builder  animator     Make Game Skeleton
      (this tool)  works only   + Export FBX
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
| `L/R_blink_CTRL` | `blink`, `blinkHeight`, `lidFollow` |
| `C_mouth_CTRL` | `smile`, `pucker`, `lipRoll`, `zip`, `jawFollow` |
| `C_jaw_CTRL` | rotate to open, plus `jawSide`, `jawThrust` |
| `L/R_brow*_CTRL` | `raise`, `furrow` (inner brows) |
| `L/R_cheek_CTRL` | `puff`, `cheekRaise` |
| Expression presets | one click: Neutral / Smile / Sad / Angry / Surprised / Kiss |

Presets set the dials and can be installed into the pose library, so they behave
like any other saved pose (blend, key, mirror).

`showTweaks` on a blink or mouth control reveals the per-joint tweak controls
for surgical fixes; leave it off for day-to-day animation.

---

## 6. Skinning notes for tech animators

Skin targets are `_BIND_JNT` only. Beyond that:

**Separate head/body meshes** — bind the body with **Auto-Skin Everything**,
bind the head with **Bind Selected Face Mesh → Face Joints** (face joints only,
so no spine influence bleeds into a cheek).

**One combined head+body mesh** — two supported routes:

| Route | When | How |
|---|---|---|
| **A — export-first** | heading for a game engine | Build rig → Build face → **Make Game Skeleton** → **Auto-Skin Everything**. Folding the face joints under the head puts them in the single hierarchy, so auto-skin includes them. |
| **B — non-destructive** | keeping the full control rig | **Auto-Skin Everything** first → build face → **Bind Selected Face Mesh → Face Joints**. This *adds* the face joints to the existing skinCluster and re-weights only the face region — the body's weights are preserved. |

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

1. **Make Game Skeleton** — reduces to a single clean `_BIND_JNT` hierarchy
   under one root, and folds the advanced-face joints under the head so they
   travel with the character.
2. **Bake** any dynamic-chain motion — the sim nodes should not ship, the
   resulting keys should.
3. **Export FBX** — skinned mesh + skeleton (+ baked animation).

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
| Blink deforms nothing | The face mesh isn't bound to the lid joints — run **Bind Selected Face Mesh → Face Joints**. |
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
