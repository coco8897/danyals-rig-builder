# Creatures: combining limbs

Four arms, wings on a person, a centaur, a dragon, an insect with six legs,
a cape, tentacles, antennae. The Rig Builder builds these on top of the
normal biped: every extra limb is a full copy of an arm, leg or tail rig
(or your own joint chain) with its own names, so the base character keeps
working exactly as before.

This guide covers the **Creature Limbs + Custom Chains** panel, the presets,
attaching limbs anywhere, custom FK / IK chains, the EZ and Free guide modes,
and what you get after Build Rig.

---

## The 60-second version

1. **Create Guides** and fit the normal biped guides to your model, as usual.
2. Open **Build Rig > Creature Limbs + Custom Chains** (the folded section
   above the Build button).
3. Pick a **Preset** and click **Add Preset**, or add limbs one at a time with
   **Add Limb Guides**.
4. New guide locators appear under `CREATURE_GUIDES_GRP`. Move them onto your
   model.
5. **Build Rig**.

That's it. Open the **Picker** and there's a new **Creature** tab with
buttons for every extra limb and chain.

---

## Presets

| Preset | Adds | Notes |
|---|---|---|
| **Four-Armed** | `lowerArm`: a second arm pair under the first, with clavicles and fingers | Move the guides to the lower ribcage |
| **Winged** | `wing`: a 3-joint wing pair (shoulder, elbow, wrist) on the upper back, with shrug clavicles | Bat / demon / angel wings on a standing character. For feathered birds, use the Bird rig type |
| **Centaur** | `hindLeg`: a hind leg pair 60 units back, `horseTail`: a tail at the rear | Turns off the normal Tail module for you, since the tail belongs on the horse body |
| **Six-Legged** | `midLeg` and `hindLeg`: two extra leg pairs behind the first | Insects, spiders (add a third pair for eight legs), crabs |
| **Dragon** | Lays the body guides out on all fours, turns the biped arms into wings with long finger spars, and adds a `frontLeg` pair on the chest | See *Dragons* below |

Presets are all or nothing: if any limb in a preset would clash with one you
already added, nothing is created and the panel tells you which name clashed.

---

## Adding a limb by hand

| Setting | What it does |
|---|---|
| **Type** | Arm, Leg, Tail, or **Chain (custom controls)**. Arm / Leg / Tail build the same rig as the biped's own. Chain is your own joint chain, see below |
| **Name** | Becomes part of every node name, e.g. `lowerArm` gives `L_lowerArm_IK_CTRL`. Letters and digits, starting with a letter |
| **Attach** | What the limb hangs off: Chest, Pelvis, COG, Head, or **Custom (pick)** for any guide or joint. *Default* is chest for arms and chains, pelvis for legs and tails |
| **Side** | *Both (mirrored)*: you place the left guides, the right side is mirrored on build. *Left only* / *Right only*: a single limb (a crab's one big claw). Chains can also be *Centre* (one chain down the middle, like a cape) |
| **Clavicle** | Arms only. Gives the extra arm its own shrug control, like the biped's |
| **Fingers** | Arms only. Five fingers, placed relative to the extra arm's wrist |

Click **Add Limb Guides**. Arm, leg and tail guides are copied from the
biped's own guides and nudged aside so you can see them.

The list under the buttons shows every extra limb in the scene. Pick one and
use **Select Guides** to grab its locators, or **Remove Limb** to delete them.
Removing guides doesn't touch a rig you already built: rebuild to drop the
limb from the rig too.

### Attach anywhere (Custom)

Set **Attach** to **Custom (pick)**, select ONE guide or joint in the
viewport, and click **Pick Selected**. The new limb hangs off the joint that
guide builds:

* `C_spine_02_GUIDE`: the nearest spine joint (a second pair of wings
  mid-back)
* `C_tail_03_GUIDE`: the third tail joint (spikes or fins along a tail)
* `C_jaw_GUIDE`, `C_head_GUIDE`: mandibles, horns, antennae
* `L_hindLeg_knee_GUIDE`: another creature limb's knee (a spur)
* a joint in a rig you already built

On a mirrored limb the right side automatically attaches to the right-hand
twin of what you picked (pick `L_hindLeg_knee`, the right spur goes on
`R_hindLeg_knee`). A limb attached to another creature limb is always built
after it.

### Names you can't use

Names the biped or the face already use are blocked, in any capitalisation:
`arm`, `leg`, `tail`, `neck`, `head`, `chest`, `jaw`, `eye`, `brow`, finger
names and so on. So are names starting with `lid` or `lip`. Pick something
descriptive instead: `lowerArm`, `wing`, `hindLeg`, `tail2`, `tentacle`.

---

## Custom chains (your own controls)

Like Advanced Skeleton's custom joints: add a chain of any length and choose
the controls it gets. Set **Type** to **Chain (custom controls)**:

| Setting | What it does |
|---|---|
| **Joints** | How many joints (each gets an FK control). A tip joint is added at the end |
| **Controls** | **FK + IK (switch)**: both, with an `ikFkSwitch` on the chain's settings control. **FK only**: one rotate control per joint (capes, hair, fins). **IK only**: root / mid / tip spline controls you drag around (tentacles, antennae). IK needs 2 joints or more |

Two ways to place it:

* **Add Chain Guides**: a row of guides starting at what it attaches to. Move
  them into shape.
* **From Selected Joints**: draw joints in Maya with the Joint Tool, select
  the ROOT joint and click. The chain guides land exactly on your joints and
  the joint count follows your drawing. If you drew the root under a rig
  joint and Attach is *Default*, the chain attaches to that joint. Delete
  your drawn joints afterwards, the guides are what Build Rig uses.

Chains follow whatever they attach to, FK and IK controls alike, so an IK
antenna turns with the head. FK chains also get `curl` and `wag` on their
settings control for quick overlapping motion.

---

## Guide modes: EZ and Free

The **Guide mode** menu in the Guides section changes how the guides move
while you place them:

* **Free** (default): every guide moves on its own. Full control, exactly
  like before.
* **EZ (linked)**: the guides are linked like the skeleton. Move the root and
  the whole body follows, move a shoulder and the elbow, wrist and fingers
  come along, move the head and the face comes with it. Creature limbs link
  to what they attach to.

Switch any time. Nothing moves when you switch, only the linking changes, and
Build Rig reads the same positions either way. Mirror, Reset, Save / Load
Guides and Create Guides all work in both modes.

A good habit: block out the proportions in **EZ**, then switch to **Free** to
fine-tune single guides.

---

## Placing guides for common creatures

**Four arms.** Keep the upper pair on the normal guides. Drag the
`L_lowerArm_*` guides down to the lower ribcage. The clavicle guide sits near
the spine, the shoulder guide where the arm meets the torso.

**Wings on a standing character.** Put `L_wing_clavicle` on the upper back
near the spine, then the shoulder, elbow and wrist along the wing's leading
edge.

**Dragons.** Use the **Dragon** preset. It re-poses the biped guides:

* the body lies flat facing +Z, legs become the hind legs, the tail runs out
  the back
* the biped **arms become the wings**, and the finger guides fan out into
  long **wing spars** for the membrane
* a `frontLeg` pair is added under the chest (legs, so they get foot roll and
  walk)

Then move the guides onto your model. On the built rig, open and fold the
wings with the wing FK controls, fan the membrane with **spread** on
`L_arm_SETTINGS_CTRL`, and set **walkArmSwing** to 0 on the global control
before using auto-walk so the wings don't flap. For a **wyvern** (wings for
arms, two legs), apply the Dragon preset and remove the `frontLeg` limb.

**Centaur.** Fit the normal biped guides to the human half (its legs become
the horse's front legs). Drag the `L_hindLeg_*` guides to the back legs and
the `C_horseTail_*` guides along the tail. Want the horse body to bend? Add a
**centre FK chain** named `horseSpine` attached to the pelvis, running back
along the body, then attach the hind legs and tail to its last guide.

**Insects and spiders.** Add a leg pair per extra pair of legs (the
Six-Legged preset gives you two). Spread the hip guides along the body and
splay the knees outward.

**Capes, hair, fins, spikes.** Centre or mirrored **FK chains**, attached to
the chest, head or a tail joint.

**Tentacles and antennae.** **IK** or **FK + IK** chains attached to the
head, jaw or wherever they grow from.

**Naga (snake body).** No creature limb needed: untick **Legs** in the
module list and use the **Tail** module as the snake body.

**Two tails.** Add a Tail limb named `tail2` and move its guides beside the
first tail.

---

## What you get after Build Rig

Every extra limb is a full rig, not a simplified one:

* **Controls**: arms and legs get FK and IK with pole vector, IK/FK switch,
  stretch, bendy ribbons, reverse foot on legs, fingers with curl and spread.
  Tails and chains get the controls you picked.
* **Its own names**: `L_lowerArm_*`, `R_hindLeg_*`, `C_cape_*`. The biped's
  own controls keep their exact names, so existing poses, animation and
  scripts still work.
* **Picker**: a **Creature** tab with a button block per limb and chain
  (FK-only and IK-only chains show just their controls), plus IK/FK match
  buttons for each extra arm and leg. Click **Refresh** if the picker was
  open before you built.
* **Auto-walk**: extra leg pairs step in alternation with the biped's legs
  (the first extra pair steps opposite, like a trot), and extra arms swing
  with the arm on their side.
* **Space switching**: extra hands and feet get World / COG / Chest or Hips
  spaces, pole vectors get World / Hand or Foot / COG.
* **Pose tools**: Copy Pose L to R, pose library flip and IK/FK match all
  include the extra limbs.
* **Export**: Make Game Skeleton folds the extra joints into the single
  skeleton, and Auto-Skin Everything picks them up.

---

## Scripting

The panel is a front end for one option on `CharacterRig`:

```python
import rig_guides, character_rig_builder as crb

gs = rig_guides.GuideSystem(); gs.build()
rig = crb.CharacterRig(positions=gs.read_positions(), extra_limbs=[
    {"type": "arm", "label": "lowerArm", "offset": (0, -22, 0),
     "clavicle": True, "fingers": True},
    {"type": "leg", "label": "hindLeg", "offset": (0, 0, -60),
     "parent": "cog"},
    {"type": "chain", "label": "cape", "side": "C", "joints": 6,
     "controls": "fk", "parent": "C_chest_BIND_JNT"},
    {"type": "chain", "label": "spur", "joints": 2, "controls": "fk",
     "parent": "L_hindLeg_knee_BIND_JNT"},
])
rig.build()
```

Each limb spec takes `type` (`"arm"`, `"leg"`, `"tail"`, `"chain"`), `label`,
and optionally `side` (`"LR"`, `"L"`, `"R"`, and `"C"` for chains), `parent`
(`"chest"`, `"pelvis"`, `"cog"`, `"head"`, any joint name, or a
`(control, joint)` pair), `positions`, `offset`, `clavicle`, `fingers`, and
for chains `joints` and `controls` (`"fkik"`, `"fk"`, `"ik"`).

The creature guides are handled by `rig_creature`: `add_limb()`,
`chain_from_joints()`, `add_preset()`, `set_guide_mode()` and
`read_extra_limbs()`.

---

## Good to know

* **Extra arms and legs are copies of the biped's rigs.** A wing is an arm
  chain, not a feathered wing. For real birds use the **Bird** rig type.
* **The neck is one joint plus the head**, even on the Dragon preset, so very
  long S-curved necks aren't possible yet.
* **Tail limbs from guides have 7 segments**, same as the biped's tail. Use a
  chain when you need a different count.
* **Renaming a limb** means Remove Limb, then add it again with the new name.
* **Removing a limb that others attach to** warns you: re-add those limbs with
  a new attach, or Build Rig stops with a missing-joint message.
* **Creature guides live in `CREATURE_GUIDES_GRP`**, separate from
  `RIG_GUIDES_GRP`, and are saved with your scene. Save Guides / Load Guides
  cover the biped guides only.

### Troubleshooting

| Message | Fix |
|---|---|
| *label ... clashes with a name the base rig or face already uses* | Pick another name, e.g. `extraArm` instead of `arm` |
| *label ... is used twice* | Every extra limb needs its own name |
| *attach joint ... isn't in the rig* | The joint you attached to comes from a module that's turned off (tail, face, fingers), or from a limb you removed. Turn the module on or pick a new attach |
| *select exactly ONE guide locator or joint* | Pick Selected needs a single guide or joint selected |
| *IK needs at least 2 joints* | Raise **Joints**, or use **FK only** for a single joint |
| *creature limb ... is missing its guide ...* | A guide locator was deleted. Remove the limb and add it again |
| No Creature tab in the picker | Click **Refresh** in the picker. The tab only shows when the rig has extra limbs |

Found a creature this can't do? Open a GitHub Issue with a sketch or
screenshot. That's how this feature started.
