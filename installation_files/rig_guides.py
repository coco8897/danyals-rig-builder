"""
===============================================================================
 RIG GUIDES - Locator-based placement system for character_rig_builder
===============================================================================

 Workflow:
   1. GuideSystem().build()  -> spawns colored locators at default proportions
   2. User drags locators to fit their character mesh
   3. GuideSystem().mirror_left_to_right()  -> copies L positions to R (mirrored)
   4. positions = GuideSystem().read_positions()
   5. CharacterRig(positions=positions).build()

 Each guide is a locator under GUIDES_GRP. Guides remember their canonical
 default positions on the prefs node so they can be reset.

 Save / load to JSON:
   GuideSystem().save_to_json("C:/path/to/myCharacter.json")
   GuideSystem().load_from_json("C:/path/to/myCharacter.json")
===============================================================================
"""

import json
import math
import maya.cmds as cmds


# =============================================================================
# CONSTANTS
# =============================================================================

GUIDES_GRP_NAME = "RIG_GUIDES_GRP"
GUIDE_SUFFIX = "_GUIDE"
GUIDE_SCALE = 1.0      # local scale X/Y/Z applied to each locator shape
LABEL_SCALE = 8.0      # text annotation size

# Color indices
COLOR_CENTER = 17  # yellow
COLOR_LEFT   = 18  # cyan
COLOR_RIGHT  = 20  # pink
COLOR_FACE   = 14  # green

# Default guide positions for a 170cm-tall human in Maya cm units.
# (Mirrors the defaults baked into character_rig_builder.py at SCALE=10.)
DEFAULT_GUIDES = {
    # ---- Root / COG (drives C_cog_CTRL placement) ----
    "C_root":          {"pos": (   0, 100,   0), "color": COLOR_CENTER},

    # ---- Center spine + neck/head ----
    # The three C_spine_NN guides between pelvis and chest are OPTIONAL
    # per-FK-ctrl placement. By default they interpolate linearly between
    # pelvis (y=100) and chest (y=140). Move them to author a curved
    # spine (e.g. forward hunch, S-curve) — SpineRig reads these positions
    # if present, otherwise it falls back to the linear interpolation.
    "C_pelvis":        {"pos": (   0, 100,   0), "color": COLOR_CENTER},
    "C_spine_01":      {"pos": (   0, 110,   0), "color": COLOR_CENTER},
    "C_spine_02":      {"pos": (   0, 120,   0), "color": COLOR_CENTER},
    "C_spine_03":      {"pos": (   0, 130,   0), "color": COLOR_CENTER},
    "C_chest":         {"pos": (   0, 140,   0), "color": COLOR_CENTER},
    "C_neck":          {"pos": (   0, 148,   0), "color": COLOR_CENTER},
    "C_head":          {"pos": (   0, 158,   0), "color": COLOR_CENTER},
    "C_headTip":       {"pos": (   0, 170,   0), "color": COLOR_CENTER},
    "C_face":          {"pos": (   0, 158,  10), "color": COLOR_FACE},

    # ---- Left clavicle + arm ----
    # NOTE: there's no L_clavicleTip / R_clavicleTip guide — the clavicle's
    # tip joint sits at the same world position as the shoulder, so
    # read_positions() derives clavicle_tip from L_shoulder automatically.
    # One less locator for the rigger to place.
    "L_clavicle":      {"pos": (   5, 140,   2), "color": COLOR_LEFT},
    "L_shoulder":      {"pos": (  22, 138,   0), "color": COLOR_LEFT},
    "L_elbow":         {"pos": (  48, 138,  -4), "color": COLOR_LEFT},
    "L_wrist":         {"pos": (  72, 138,   0), "color": COLOR_LEFT},

    # ---- Right clavicle + arm ----
    "R_clavicle":      {"pos": (  -5, 140,   2), "color": COLOR_RIGHT},
    "R_shoulder":      {"pos": ( -22, 138,   0), "color": COLOR_RIGHT},
    "R_elbow":         {"pos": ( -48, 138,  -4), "color": COLOR_RIGHT},
    "R_wrist":         {"pos": ( -72, 138,   0), "color": COLOR_RIGHT},

    # ---- Left leg ----
    "L_hip":           {"pos": (  12,  97,   0), "color": COLOR_LEFT},
    "L_knee":          {"pos": (  12,  52,   3), "color": COLOR_LEFT},
    "L_ankle":         {"pos": (  12,  10,   0), "color": COLOR_LEFT},
    "L_ball":          {"pos": (  12,   3,  10), "color": COLOR_LEFT},
    "L_toe":           {"pos": (  12,   3,  20), "color": COLOR_LEFT},
    # toeTip = very front of the foot for toe curl / standing-on-toes
    # deformation. New BIND joint, child of toe.
    "L_toeTip":        {"pos": (  12,   3,  25), "color": COLOR_LEFT},

    # ---- Right leg ----
    "R_hip":           {"pos": ( -12,  97,   0), "color": COLOR_RIGHT},
    "R_knee":          {"pos": ( -12,  52,   3), "color": COLOR_RIGHT},
    "R_ankle":         {"pos": ( -12,  10,   0), "color": COLOR_RIGHT},
    "R_ball":          {"pos": ( -12,   3,  10), "color": COLOR_RIGHT},
    "R_toe":           {"pos": ( -12,   3,  20), "color": COLOR_RIGHT},
    "R_toeTip":        {"pos": ( -12,   3,  25), "color": COLOR_RIGHT},

    # ---- Face: jaw ----
    "C_jaw":           {"pos": (   0, 156,   7), "color": COLOR_FACE},
    "C_jawTip":        {"pos": (   0, 152,  11), "color": COLOR_FACE},

    # ---- Face: eyes (look-at target far in front of head) ----
    "L_eye":           {"pos": (   3, 162,   9), "color": COLOR_FACE},
    "R_eye":           {"pos": (  -3, 162,   9), "color": COLOR_FACE},
    "C_eyesLookAt":    {"pos": (   0, 162,  50), "color": COLOR_FACE},

    # ---- Face: detailed eyelids (3 per upper, 3 per lower, both eyes) ----
    "L_eyelidUpperInner": {"pos": ( 1.5, 163,  9.5), "color": COLOR_FACE},
    "L_eyelidUpperMid":   {"pos": ( 3.0, 163.5, 9.5), "color": COLOR_FACE},
    "L_eyelidUpperOuter": {"pos": ( 4.5, 163,  9.5), "color": COLOR_FACE},
    "L_eyelidLowerInner": {"pos": ( 1.5, 161,  9.5), "color": COLOR_FACE},
    "L_eyelidLowerMid":   {"pos": ( 3.0, 160.5, 9.5), "color": COLOR_FACE},
    "L_eyelidLowerOuter": {"pos": ( 4.5, 161,  9.5), "color": COLOR_FACE},
    "R_eyelidUpperInner": {"pos": (-1.5, 163,  9.5), "color": COLOR_FACE},
    "R_eyelidUpperMid":   {"pos": (-3.0, 163.5, 9.5), "color": COLOR_FACE},
    "R_eyelidUpperOuter": {"pos": (-4.5, 163,  9.5), "color": COLOR_FACE},
    "R_eyelidLowerInner": {"pos": (-1.5, 161,  9.5), "color": COLOR_FACE},
    "R_eyelidLowerMid":   {"pos": (-3.0, 160.5, 9.5), "color": COLOR_FACE},
    "R_eyelidLowerOuter": {"pos": (-4.5, 161,  9.5), "color": COLOR_FACE},

    # ---- Face: brow (3 per side) ----
    "L_browInner":     {"pos": (   1, 165,  10), "color": COLOR_FACE},
    "L_browMid":       {"pos": (   3, 165.5, 10), "color": COLOR_FACE},
    "L_browOuter":     {"pos": (   5, 164, 9.5), "color": COLOR_FACE},
    "R_browInner":     {"pos": (  -1, 165,  10), "color": COLOR_FACE},
    "R_browMid":       {"pos": (  -3, 165.5, 10), "color": COLOR_FACE},
    "R_browOuter":     {"pos": (  -5, 164, 9.5), "color": COLOR_FACE},

    # ---- Face: mouth corners ----
    "L_mouthCorner":   {"pos": (   3, 152,  10), "color": COLOR_FACE},
    "R_mouthCorner":   {"pos": (  -3, 152,  10), "color": COLOR_FACE},

    # ---- Face: lips (3 upper + 3 lower) for zippy / mouth open ----
    "C_upperLip":      {"pos": (   0, 152.5, 11), "color": COLOR_FACE},
    "L_upperLipMid":   {"pos": ( 1.5, 152.3, 10.8), "color": COLOR_FACE},
    "R_upperLipMid":   {"pos": (-1.5, 152.3, 10.8), "color": COLOR_FACE},
    "C_lowerLip":      {"pos": (   0, 151.5, 11), "color": COLOR_FACE},
    "L_lowerLipMid":   {"pos": ( 1.5, 151.7, 10.8), "color": COLOR_FACE},
    "R_lowerLipMid":   {"pos": (-1.5, 151.7, 10.8), "color": COLOR_FACE},

    # ---- Face: cheeks ----
    "L_cheek":         {"pos": (   5, 158,  10), "color": COLOR_FACE},
    "R_cheek":         {"pos": (  -5, 158,  10), "color": COLOR_FACE},

    # ---- Face: nose ----
    "C_noseTip":       {"pos": (   0, 159,  12), "color": COLOR_FACE},
    "L_nostril":       {"pos": (   1, 158, 11.5), "color": COLOR_FACE},
    "R_nostril":       {"pos": (  -1, 158, 11.5), "color": COLOR_FACE},

    # ---- Face: tongue (3-joint FK chain) ----
    "C_tongue01":      {"pos": (   0, 154,   7), "color": COLOR_FACE},
    "C_tongue02":      {"pos": (   0, 154,   9), "color": COLOR_FACE},
    "C_tongue03":      {"pos": (   0, 154,  11), "color": COLOR_FACE},

    # ---- Face: teeth (upper follows head, lower follows jaw) ----
    "C_upperTeeth":    {"pos": (   0, 154,  10), "color": COLOR_FACE},
    "C_lowerTeeth":    {"pos": (   0, 152,  10), "color": COLOR_FACE},

    # ---- Ears (single FK joint per side, parented to head) ----
    "L_ear":           {"pos": (   8, 160,   0), "color": COLOR_LEFT},
    "R_ear":           {"pos": (  -8, 160,   0), "color": COLOR_RIGHT},

    # ---- Fingers (5 per hand, parented under wrist) ----
    # Conventions (default T-pose: arm along +X, palm faces -Y):
    #   Thumb has 3 BIND joints + tip:  meta, prox, dist, tip
    #   Index / middle / ring / pinky:  meta, prox, mid, dist, tip
    # 'meta' sits at the metacarpal base (in the palm), 'prox' is the
    # knuckle, 'mid' is the PIP joint (non-thumb), 'dist' is the DIP
    # joint, 'tip' is at the fingertip for orient only (not skinned).

    # -- Left thumb (sticks forward + slightly up from palm) --
    # Each finger guide carries a "parent" key naming the locator it
    # should re-parent under at build() time. Meta joints sit under the
    # wrist guide; subsequent segments chain off the previous one. This
    # means moving the wrist guide drags the whole hand along, and the
    # outliner shows the actual anatomical chain instead of 50 flat
    # siblings.
    "L_thumbMeta":     {"pos": (73.0, 138.0,  2.0), "color": COLOR_LEFT,  "parent": "L_wrist"},
    "L_thumbProx":     {"pos": (74.5, 138.0,  3.5), "color": COLOR_LEFT,  "parent": "L_thumbMeta"},
    "L_thumbDist":     {"pos": (75.5, 138.0,  4.5), "color": COLOR_LEFT,  "parent": "L_thumbProx"},
    "L_thumbTip":      {"pos": (76.0, 138.0,  5.0), "color": COLOR_LEFT,  "parent": "L_thumbDist"},

    # -- Left index --
    "L_indexMeta":     {"pos": (73.0, 138.0,  1.0), "color": COLOR_LEFT,  "parent": "L_wrist"},
    "L_indexProx":     {"pos": (75.0, 138.0,  1.0), "color": COLOR_LEFT,  "parent": "L_indexMeta"},
    "L_indexMid":      {"pos": (76.5, 138.0,  1.0), "color": COLOR_LEFT,  "parent": "L_indexProx"},
    "L_indexDist":     {"pos": (77.5, 138.0,  1.0), "color": COLOR_LEFT,  "parent": "L_indexMid"},
    "L_indexTip":      {"pos": (78.0, 138.0,  1.0), "color": COLOR_LEFT,  "parent": "L_indexDist"},

    # -- Left middle --
    "L_middleMeta":    {"pos": (73.0, 138.0,  0.0), "color": COLOR_LEFT,  "parent": "L_wrist"},
    "L_middleProx":    {"pos": (75.0, 138.0,  0.0), "color": COLOR_LEFT,  "parent": "L_middleMeta"},
    "L_middleMid":     {"pos": (76.7, 138.0,  0.0), "color": COLOR_LEFT,  "parent": "L_middleProx"},
    "L_middleDist":    {"pos": (77.7, 138.0,  0.0), "color": COLOR_LEFT,  "parent": "L_middleMid"},
    "L_middleTip":     {"pos": (78.2, 138.0,  0.0), "color": COLOR_LEFT,  "parent": "L_middleDist"},

    # -- Left ring --
    "L_ringMeta":      {"pos": (73.0, 138.0, -1.0), "color": COLOR_LEFT,  "parent": "L_wrist"},
    "L_ringProx":      {"pos": (75.0, 138.0, -1.0), "color": COLOR_LEFT,  "parent": "L_ringMeta"},
    "L_ringMid":       {"pos": (76.5, 138.0, -1.0), "color": COLOR_LEFT,  "parent": "L_ringProx"},
    "L_ringDist":      {"pos": (77.4, 138.0, -1.0), "color": COLOR_LEFT,  "parent": "L_ringMid"},
    "L_ringTip":       {"pos": (77.9, 138.0, -1.0), "color": COLOR_LEFT,  "parent": "L_ringDist"},

    # -- Left pinky --
    "L_pinkyMeta":     {"pos": (73.0, 138.0, -2.0), "color": COLOR_LEFT,  "parent": "L_wrist"},
    "L_pinkyProx":     {"pos": (74.5, 138.0, -2.0), "color": COLOR_LEFT,  "parent": "L_pinkyMeta"},
    "L_pinkyMid":      {"pos": (75.7, 138.0, -2.0), "color": COLOR_LEFT,  "parent": "L_pinkyProx"},
    "L_pinkyDist":     {"pos": (76.4, 138.0, -2.0), "color": COLOR_LEFT,  "parent": "L_pinkyMid"},
    "L_pinkyTip":      {"pos": (76.8, 138.0, -2.0), "color": COLOR_LEFT,  "parent": "L_pinkyDist"},

    # -- Right side (X mirrored) --
    "R_thumbMeta":     {"pos": (-73.0, 138.0,  2.0), "color": COLOR_RIGHT, "parent": "R_wrist"},
    "R_thumbProx":     {"pos": (-74.5, 138.0,  3.5), "color": COLOR_RIGHT, "parent": "R_thumbMeta"},
    "R_thumbDist":     {"pos": (-75.5, 138.0,  4.5), "color": COLOR_RIGHT, "parent": "R_thumbProx"},
    "R_thumbTip":      {"pos": (-76.0, 138.0,  5.0), "color": COLOR_RIGHT, "parent": "R_thumbDist"},

    "R_indexMeta":     {"pos": (-73.0, 138.0,  1.0), "color": COLOR_RIGHT, "parent": "R_wrist"},
    "R_indexProx":     {"pos": (-75.0, 138.0,  1.0), "color": COLOR_RIGHT, "parent": "R_indexMeta"},
    "R_indexMid":      {"pos": (-76.5, 138.0,  1.0), "color": COLOR_RIGHT, "parent": "R_indexProx"},
    "R_indexDist":     {"pos": (-77.5, 138.0,  1.0), "color": COLOR_RIGHT, "parent": "R_indexMid"},
    "R_indexTip":      {"pos": (-78.0, 138.0,  1.0), "color": COLOR_RIGHT, "parent": "R_indexDist"},

    "R_middleMeta":    {"pos": (-73.0, 138.0,  0.0), "color": COLOR_RIGHT, "parent": "R_wrist"},
    "R_middleProx":    {"pos": (-75.0, 138.0,  0.0), "color": COLOR_RIGHT, "parent": "R_middleMeta"},
    "R_middleMid":     {"pos": (-76.7, 138.0,  0.0), "color": COLOR_RIGHT, "parent": "R_middleProx"},
    "R_middleDist":    {"pos": (-77.7, 138.0,  0.0), "color": COLOR_RIGHT, "parent": "R_middleMid"},
    "R_middleTip":     {"pos": (-78.2, 138.0,  0.0), "color": COLOR_RIGHT, "parent": "R_middleDist"},

    "R_ringMeta":      {"pos": (-73.0, 138.0, -1.0), "color": COLOR_RIGHT, "parent": "R_wrist"},
    "R_ringProx":      {"pos": (-75.0, 138.0, -1.0), "color": COLOR_RIGHT, "parent": "R_ringMeta"},
    "R_ringMid":       {"pos": (-76.5, 138.0, -1.0), "color": COLOR_RIGHT, "parent": "R_ringProx"},
    "R_ringDist":      {"pos": (-77.4, 138.0, -1.0), "color": COLOR_RIGHT, "parent": "R_ringMid"},
    "R_ringTip":       {"pos": (-77.9, 138.0, -1.0), "color": COLOR_RIGHT, "parent": "R_ringDist"},

    "R_pinkyMeta":     {"pos": (-73.0, 138.0, -2.0), "color": COLOR_RIGHT, "parent": "R_wrist"},
    "R_pinkyProx":     {"pos": (-74.5, 138.0, -2.0), "color": COLOR_RIGHT, "parent": "R_pinkyMeta"},
    "R_pinkyMid":      {"pos": (-75.7, 138.0, -2.0), "color": COLOR_RIGHT, "parent": "R_pinkyProx"},
    "R_pinkyDist":     {"pos": (-76.4, 138.0, -2.0), "color": COLOR_RIGHT, "parent": "R_pinkyMid"},
    "R_pinkyTip":      {"pos": (-76.8, 138.0, -2.0), "color": COLOR_RIGHT, "parent": "R_pinkyDist"},

    # ---- Tail (7 BIND joints + tip, arcs back + down from pelvis) ----
    "C_tail_01":       {"pos": (0.0, 98.0,  -5.0), "color": COLOR_CENTER},
    "C_tail_02":       {"pos": (0.0, 96.0, -10.0), "color": COLOR_CENTER},
    "C_tail_03":       {"pos": (0.0, 94.0, -15.0), "color": COLOR_CENTER},
    "C_tail_04":       {"pos": (0.0, 92.0, -20.0), "color": COLOR_CENTER},
    "C_tail_05":       {"pos": (0.0, 89.0, -25.0), "color": COLOR_CENTER},
    "C_tail_06":       {"pos": (0.0, 85.0, -30.0), "color": COLOR_CENTER},
    "C_tail_07":       {"pos": (0.0, 80.0, -33.0), "color": COLOR_CENTER},
    "C_tailTip":       {"pos": (0.0, 75.0, -35.0), "color": COLOR_CENTER},
}


# =============================================================================
# Outliner organisation — anatomical group containers
# =============================================================================
#
# Each guide listed here gets parented under the named group transform
# inside RIG_GUIDES_GRP at build() time. Result: instead of 128 sibling
# locators, the outliner reads like a body diagram (SPINE_GUIDES,
# HEAD_GUIDES, L_ARM_GUIDES, …) that a new rigger can collapse and
# navigate at a glance.
#
# Guides NOT listed here stay at the top of RIG_GUIDES_GRP (the
# `parent`-keyed entries — finger chains — are intentionally omitted
# because they sit under their parent locator, which is itself in
# L_ARM_GUIDES / R_ARM_GUIDES).
# =============================================================================

GUIDE_GROUPS = {
    "SPINE_GUIDES":   ["C_root", "C_pelvis",
                       "C_spine_01", "C_spine_02", "C_spine_03",
                       "C_chest"],
    "HEAD_GUIDES":    ["C_neck", "C_head", "C_headTip", "C_face"],
    "FACE_GUIDES":    [
        "C_jaw", "C_jawTip",
        "L_eye", "R_eye", "C_eyesLookAt",
        "L_eyelidUpperInner", "L_eyelidUpperMid", "L_eyelidUpperOuter",
        "L_eyelidLowerInner", "L_eyelidLowerMid", "L_eyelidLowerOuter",
        "R_eyelidUpperInner", "R_eyelidUpperMid", "R_eyelidUpperOuter",
        "R_eyelidLowerInner", "R_eyelidLowerMid", "R_eyelidLowerOuter",
        "L_browInner", "L_browMid", "L_browOuter",
        "R_browInner", "R_browMid", "R_browOuter",
        "L_mouthCorner", "R_mouthCorner",
        "C_upperLip", "L_upperLipMid", "R_upperLipMid",
        "C_lowerLip", "L_lowerLipMid", "R_lowerLipMid",
        "L_cheek", "R_cheek",
        "C_noseTip", "L_nostril", "R_nostril",
        "C_tongue01", "C_tongue02", "C_tongue03",
        "C_upperTeeth", "C_lowerTeeth",
        "L_ear", "R_ear",
    ],
    "L_ARM_GUIDES":   ["L_clavicle",
                       "L_shoulder", "L_elbow", "L_wrist"],
    "R_ARM_GUIDES":   ["R_clavicle",
                       "R_shoulder", "R_elbow", "R_wrist"],
    "L_LEG_GUIDES":   ["L_hip", "L_knee", "L_ankle",
                       "L_ball", "L_toe", "L_toeTip"],
    "R_LEG_GUIDES":   ["R_hip", "R_knee", "R_ankle",
                       "R_ball", "R_toe", "R_toeTip"],
    "TAIL_GUIDES":    ["C_tail_01", "C_tail_02", "C_tail_03",
                       "C_tail_04", "C_tail_05", "C_tail_06",
                       "C_tail_07", "C_tailTip"],
}

# Reverse-lookup so _apply_grouping() can find a guide's group in O(1).
GUIDE_GROUP_OF = {g: grp
                   for grp, members in GUIDE_GROUPS.items()
                   for g in members}


# =============================================================================
# Plain-English notes — shown in the Attribute Editor when a rigger
# selects a locator. The point is to make the rig self-documenting so a
# newcomer doesn't need to memorise terms like "metacarpal" or "ball" to
# know where each guide goes.
# =============================================================================

GUIDE_NOTES = {
    # ---- Core ----
    "C_root":      "Skeleton root. Leave at world origin (0, 0, 0).",
    "C_pelvis":    "Pelvis / hip — sits at the navel height, body center.",
    "C_spine_01":  "Lower spine FK ctrl. Move to curve the spine forward / back.",
    "C_spine_02":  "Mid spine FK ctrl. Pose this for hunch / S-curve / arch shapes.",
    "C_spine_03":  "Upper spine FK ctrl, just below the chest.",
    "C_chest":     "Chest / upper spine end — at the sternum.",
    "C_neck":      "Base of the neck where it meets the chest.",
    "C_head":      "Center of the head — usually inside the skull, at the jaw hinge level.",
    "C_headTip":   "Top of the head / hair line. Orient-only — not skinned.",
    "C_face":      "Front of the face, roughly between the eyes. Anchor for face ctrls.",

    # ---- Arms (Left) ----
    "L_clavicle":      "Inner end of the left collarbone, beside the sternum.",
    "L_shoulder":      "Left shoulder — where the upper arm meets the body. (Doubles as the outer end of the collarbone.)",
    "L_elbow":         "Left elbow — slightly behind the line between shoulder and wrist (gives IK a bend direction).",
    "L_wrist":         "Left wrist — where the forearm meets the hand. Moving this drags every finger along.",
    "R_clavicle":      "Inner end of the right collarbone.",
    "R_shoulder":      "Right shoulder. (Doubles as the outer end of the collarbone.)",
    "R_elbow":         "Right elbow — slightly behind the shoulder-wrist line.",
    "R_wrist":         "Right wrist. Moving this drags every finger along.",

    # ---- Legs ----
    "L_hip":     "Left hip socket — top of the thigh.",
    "L_knee":    "Left knee — slightly forward of the hip-ankle line (gives IK a bend direction).",
    "L_ankle":   "Left ankle — top of the foot.",
    "L_ball":    "Ball of the left foot — where the toes flex against the ground (where the foot rolls during a step).",
    "L_toe":     "Joint at the base of the left toes — front-of-foot pivot.",
    "L_toeTip":  "Tip of the left toes. Orient-only — not skinned.",
    "R_hip":     "Right hip socket.",
    "R_knee":    "Right knee — slightly forward of the hip-ankle line.",
    "R_ankle":   "Right ankle.",
    "R_ball":    "Ball of the right foot — the foot-roll pivot.",
    "R_toe":     "Base of the right toes.",
    "R_toeTip":  "Tip of the right toes. Orient-only.",

    # ---- Fingers (Left — Right mirrors via Mirror L→R) ----
    "L_thumbMeta":  "Base of the thumb, inside the palm. (Metacarpal joint.)",
    "L_thumbProx":  "Lower thumb knuckle — closest to the palm.",
    "L_thumbDist":  "Upper thumb knuckle — closest to the nail.",
    "L_thumbTip":   "Tip of the thumb. Orient-only — not skinned.",
    "L_indexMeta":  "Base of the index finger, inside the palm.",
    "L_indexProx":  "Index finger — knuckle nearest the palm.",
    "L_indexMid":   "Index finger — middle knuckle (PIP joint).",
    "L_indexDist":  "Index finger — knuckle nearest the nail (DIP joint).",
    "L_indexTip":   "Tip of the index finger. Orient-only.",
    "L_middleMeta": "Base of the middle finger, inside the palm.",
    "L_middleProx": "Middle finger — knuckle nearest the palm.",
    "L_middleMid":  "Middle finger — middle knuckle.",
    "L_middleDist": "Middle finger — knuckle nearest the nail.",
    "L_middleTip":  "Tip of the middle finger. Orient-only.",
    "L_ringMeta":   "Base of the ring finger, inside the palm.",
    "L_ringProx":   "Ring finger — knuckle nearest the palm.",
    "L_ringMid":    "Ring finger — middle knuckle.",
    "L_ringDist":   "Ring finger — knuckle nearest the nail.",
    "L_ringTip":    "Tip of the ring finger. Orient-only.",
    "L_pinkyMeta":  "Base of the pinky finger, inside the palm.",
    "L_pinkyProx":  "Pinky — knuckle nearest the palm.",
    "L_pinkyMid":   "Pinky — middle knuckle.",
    "L_pinkyDist":  "Pinky — knuckle nearest the nail.",
    "L_pinkyTip":   "Tip of the pinky. Orient-only.",

    # ---- Face: jaw, eyes ----
    "C_jaw":     "Jaw hinge — at the back of the lower jaw, in front of the ear.",
    "C_jawTip":  "Front of the lower jaw / chin. Orient-only — not skinned.",
    "L_eye":     "Centre of the left eyeball.",
    "R_eye":     "Centre of the right eyeball.",
    "C_eyesLookAt": "World-space look-at target for both eyes. Move this to point the eyes.",

    # ---- Face: eyelids ----
    "L_eyelidUpperInner": "Left upper eyelid — inner corner (near nose).",
    "L_eyelidUpperMid":   "Left upper eyelid — middle / peak.",
    "L_eyelidUpperOuter": "Left upper eyelid — outer corner (near temple).",
    "L_eyelidLowerInner": "Left lower eyelid — inner corner.",
    "L_eyelidLowerMid":   "Left lower eyelid — middle.",
    "L_eyelidLowerOuter": "Left lower eyelid — outer corner.",
    "R_eyelidUpperInner": "Right upper eyelid — inner corner.",
    "R_eyelidUpperMid":   "Right upper eyelid — middle.",
    "R_eyelidUpperOuter": "Right upper eyelid — outer corner.",
    "R_eyelidLowerInner": "Right lower eyelid — inner corner.",
    "R_eyelidLowerMid":   "Right lower eyelid — middle.",
    "R_eyelidLowerOuter": "Right lower eyelid — outer corner.",

    # ---- Face: brows ----
    "L_browInner": "Inner end of the left brow, near the nose bridge.",
    "L_browMid":   "Middle of the left brow.",
    "L_browOuter": "Outer end of the left brow, above the temple.",
    "R_browInner": "Inner end of the right brow.",
    "R_browMid":   "Middle of the right brow.",
    "R_browOuter": "Outer end of the right brow.",

    # ---- Face: mouth ----
    "L_mouthCorner": "Left corner of the mouth.",
    "R_mouthCorner": "Right corner of the mouth.",
    "C_upperLip":    "Centre of the upper lip — the cupid's bow.",
    "L_upperLipMid": "Midway between the left mouth corner and the upper lip centre.",
    "R_upperLipMid": "Midway between the right mouth corner and the upper lip centre.",
    "C_lowerLip":    "Centre of the lower lip.",
    "L_lowerLipMid": "Midway between the left mouth corner and the lower lip centre.",
    "R_lowerLipMid": "Midway between the right mouth corner and the lower lip centre.",

    # ---- Face: cheeks / nose / tongue / teeth / ears ----
    "L_cheek":     "Left cheekbone — under the eye, above the mouth.",
    "R_cheek":     "Right cheekbone.",
    "C_noseTip":   "Tip of the nose.",
    "L_nostril":   "Outside of the left nostril (for nose-flare).",
    "R_nostril":   "Outside of the right nostril.",
    "C_tongue01":  "Tongue — back, near the throat.",
    "C_tongue02":  "Tongue — middle.",
    "C_tongue03":  "Tongue — tip.",
    "C_upperTeeth": "Upper teeth anchor — follows the head.",
    "C_lowerTeeth": "Lower teeth anchor — follows the jaw.",
    "L_ear":       "Centre of the left ear.",
    "R_ear":       "Centre of the right ear.",

    # ---- Tail ----
    "C_tail_01": "Tail base — closest to the pelvis.",
    "C_tail_02": "Tail segment 2.",
    "C_tail_03": "Tail segment 3.",
    "C_tail_04": "Tail segment 4 — middle of the tail.",
    "C_tail_05": "Tail segment 5.",
    "C_tail_06": "Tail segment 6.",
    "C_tail_07": "Tail segment 7 — near the tail tip.",
    "C_tailTip": "Tip of the tail. Orient-only — not skinned.",
}


# =============================================================================
# GUIDE SYSTEM
# =============================================================================

class GuideSystem(object):

    def __init__(self):
        self.guides_grp = None
        self.guides = {}     # name -> locator transform

    # -----------------------------------------------------------------------
    # Build / delete
    # -----------------------------------------------------------------------

    def exists(self):
        return cmds.objExists(GUIDES_GRP_NAME)

    def build(self):
        if self.exists():
            self._refresh_handles()
            # Delete any locators that USED to be in DEFAULT_GUIDES but
            # were removed in a later version (e.g. L_clavicleTip /
            # R_clavicleTip, which are now derived from the shoulder).
            # Walk every transform under the guides group whose name ends
            # in GUIDE_SUFFIX but isn't in DEFAULT_GUIDES.
            stale = self._stale_guide_locators()
            if stale:
                cmds.delete(stale)
                print(f"[GuideSystem] Removed {len(stale)} stale guide(s) "
                       f"that are no longer in DEFAULT_GUIDES: "
                       f"{', '.join(stale)}")
                self._refresh_handles()
            # Then fill in any guide locators that DEFAULT_GUIDES has
            # grown since this scene was first built (e.g. the new
            # intermediate spine guides). Without this read_positions()
            # would crash on the missing names.
            n_created = 0
            for name, info in DEFAULT_GUIDES.items():
                if name in self.guides:
                    continue
                self._create_guide(name, info["pos"], info["color"])
                n_created += 1
            if n_created:
                print(f"[GuideSystem] Added {n_created} new guide(s) "
                       f"that were missing from this scene.")
            # Resize every existing locator's shape to the current
            # GUIDE_SCALE so a constant change propagates retroactively
            # without forcing the user to delete + recreate.
            self._refresh_locator_sizes()
            # Apply any organisation rules that may have been added since
            # this scene was built (group containers, parent-child chains,
            # plain-English notes, viewport labels). Every pass is
            # idempotent — silently no-ops on guides already in the
            # right state. This lets users "click Create Guides" on an
            # existing scene to pick up new organisation without having
            # to delete + recreate.
            grouped    = self._apply_grouping()
            reparented = self._apply_parenting()
            noted      = self._apply_notes()
            labelled   = self._apply_labels()
            # Re-assert the symmetricMode visibility — the user may have
            # toggled some R locators on or off manually between sessions.
            self._ensure_symmetric_attr()
            self._apply_symmetric_visibility()
            changed = grouped + reparented + noted + labelled
            if changed:
                print(f"[GuideSystem] {GUIDES_GRP_NAME} already exists; "
                       f"refreshed organisation "
                       f"({grouped} grouped, {reparented} parented, "
                       f"{noted} notes, {labelled} labels).")
            else:
                cmds.warning(f"{GUIDES_GRP_NAME} already exists and "
                             f"organisation is already up to date. "
                             f"Delete first to rebuild from defaults.")
            return

        self.guides_grp = cmds.group(em=True, n=GUIDES_GRP_NAME)
        cmds.setAttr(f"{self.guides_grp}.useOutlinerColor", 1)
        cmds.setAttr(f"{self.guides_grp}.outlinerColor", 0.4, 0.9, 1.0)

        for name, info in DEFAULT_GUIDES.items():
            self._create_guide(name, info["pos"], info["color"])

        # ---- Three organisation passes (order matters) ----
        # 1. Group containers FIRST — drops each top-level locator into
        #    its anatomical group (L_ARM_GUIDES, FACE_GUIDES, …).
        # 2. Parent-child chains — fingers parent under their wrist
        #    (which already lives inside L_ARM_GUIDES, so the chain
        #    transitively ends up grouped).
        # 3. Notes — adds a plain-English description attr to each
        #    locator so new riggers can read what a guide is for in
        #    the Attribute Editor.
        # Default cmds.parent() preserves world position on each move,
        # so positions stay exactly where they were created.
        self._apply_grouping()
        self._apply_parenting()
        self._apply_notes()
        self._apply_labels()
        # Symmetric mode: default ON. Hides R_ locators so the rigger
        # only sees the L (and C) side; build()-time mirror copies L→R.
        self._ensure_symmetric_attr()
        self._apply_symmetric_visibility()

        cmds.select(cl=True)
        print(f"[GuideSystem] Created {len(DEFAULT_GUIDES)} guides under "
              f"{GUIDES_GRP_NAME}.")

    # -----------------------------------------------------------------------
    # Viewport labels (annotation children that show each guide's name)
    # -----------------------------------------------------------------------

    LABEL_SUFFIX = "_label"

    # Side prefix → pretty suffix shown in parentheses on labels.
    # ("C_" labels get no suffix — they're center, no ambiguity).
    _SIDE_SUFFIX = {"L_": " (L)", "R_": " (R)"}

    # Friendly labels override the auto-generated text for guides whose
    # internal name needs an English translation. Anything not listed
    # here falls through to a generic prefix-strip + camelCase split.
    _LABEL_OVERRIDES = {
        "C_pelvis":     "Pelvis",
        "C_chest":      "Chest",
        "C_neck":       "Neck",
        "C_head":       "Head",
        "C_headTip":    "Head Tip",
        "C_face":       "Face",
        "C_root":       "Root",
        "C_jaw":        "Jaw",
        "C_jawTip":     "Chin",
        "C_eyesLookAt": "Eyes Look-At",
        "C_noseTip":    "Nose",
        "C_upperLip":   "Upper Lip",
        "C_lowerLip":   "Lower Lip",
        "C_upperTeeth": "Upper Teeth",
        "C_lowerTeeth": "Lower Teeth",
        "C_tongue01":   "Tongue 1",
        "C_tongue02":   "Tongue 2",
        "C_tongue03":   "Tongue 3",
    }

    @classmethod
    def _friendly_label(cls, guide_name):
        """Convert an internal guide name into a short label for the
        viewport. 'L_thumbMeta' → 'Thumb Meta (L)', 'C_tail_03' → 'Tail 3'.
        """
        if guide_name in cls._LABEL_OVERRIDES:
            return cls._LABEL_OVERRIDES[guide_name]
        # Strip side prefix and remember what side it was on.
        side_suffix = ""
        for prefix, suffix in cls._SIDE_SUFFIX.items():
            if guide_name.startswith(prefix):
                guide_name = guide_name[len(prefix):]
                side_suffix = suffix
                break
        else:
            if guide_name.startswith("C_"):
                guide_name = guide_name[2:]
        # camelCase → "camel Case", underscores → spaces, then Title Case.
        out = []
        for i, ch in enumerate(guide_name):
            if i > 0 and ch.isupper() and guide_name[i - 1].islower():
                out.append(" ")
            out.append(ch)
        text = "".join(out).replace("_", " ").strip()
        # Title-case each word; collapse leading zeros on numeric tokens
        # so "01" reads as "1" (friendlier than zero-padded indices).
        text = " ".join(str(int(w)) if w.isdigit() else w.capitalize()
                         for w in text.split())
        return text + side_suffix

    def _ensure_label_master_attr(self):
        """Add a `showLabels` bool attr on the guides group that drives
        every label's visibility. Returns the full attr name (for
        connectAttr) or None if the group doesn't exist yet."""
        if not self.guides_grp or not cmds.objExists(self.guides_grp):
            return None
        attr = f"{self.guides_grp}.showLabels"
        if not cmds.attributeQuery("showLabels",
                                    node=self.guides_grp, exists=True):
            cmds.addAttr(self.guides_grp, ln="showLabels", at="bool",
                          dv=True, k=True)
        return attr

    def _apply_labels(self):
        """Add a small annotation child to each guide showing a friendly
        label in the viewport (the way Advanced Skeleton displays joint
        names). Visibility is driven from `RIG_GUIDES_GRP.showLabels`
        so the user can toggle them with one keyframe.

        Idempotent — skips guides that already have a label child.

        Returns the number of labels created this call.
        """
        master = self._ensure_label_master_attr()
        if not master:
            return 0
        n_added = 0
        for guide_name in DEFAULT_GUIDES:
            loc = self.guides.get(guide_name)
            if not loc or not cmds.objExists(loc):
                continue
            label_xform_name = loc + self.LABEL_SUFFIX
            if cmds.objExists(label_xform_name):
                continue
            text = self._friendly_label(guide_name)
            pos = cmds.xform(loc, q=True, ws=True, t=True)
            # Annotation is created at a world position; the line from
            # text → target is invisible when text == target.
            cmds.select(loc, r=True)
            ann_shape = cmds.annotate(loc, tx=text, p=pos)
            # cmds.annotate returns the SHAPE node; rename its transform
            # so it sits next to the locator in the outliner.
            ann_xform = cmds.listRelatives(ann_shape, p=True)[0]
            ann_xform = cmds.rename(ann_xform, label_xform_name)
            # Reparent under the locator so it follows when the user
            # drags the guide around (preserves world position).
            cmds.parent(ann_xform, loc)
            # Tag the SHAPE node (Maya renames it on parent so look it up).
            ann_shapes = cmds.listRelatives(ann_xform, s=True) or []
            for s in ann_shapes:
                # Drive visibility from the master toggle and disable
                # selection so it doesn't grab clicks intended for the
                # locator.
                cmds.connectAttr(master, f"{s}.visibility", f=True)
                try:
                    cmds.setAttr(f"{s}.overrideEnabled", 1)
                    cmds.setAttr(f"{s}.overrideDisplayType", 2)  # reference
                except Exception:
                    pass
            # Lock the label transform so users don't accidentally drag
            # the text around independent of the locator.
            for a in ("tx", "ty", "tz", "rx", "ry", "rz",
                       "sx", "sy", "sz"):
                try:
                    cmds.setAttr(f"{ann_xform}.{a}",
                                  l=True, k=False, cb=False)
                except Exception:
                    pass
            n_added += 1
        cmds.select(cl=True)
        if n_added:
            print(f"[GuideSystem] Added {n_added} viewport labels "
                   f"(toggle via {GUIDES_GRP_NAME}.showLabels).")
        return n_added

    def set_labels_visible(self, visible):
        """Toggle all viewport guide labels on/off."""
        if not cmds.objExists(GUIDES_GRP_NAME):
            cmds.warning("No guides in scene.")
            return
        if not cmds.attributeQuery("showLabels",
                                    node=GUIDES_GRP_NAME, exists=True):
            cmds.warning("No showLabels attr — rebuild guides "
                          "to add labels.")
            return
        cmds.setAttr(f"{GUIDES_GRP_NAME}.showLabels", bool(visible))

    # -----------------------------------------------------------------------
    # Symmetric mode — hides every R_ locator and lets _on_build auto-mirror
    # L → R right before extracting positions.
    # -----------------------------------------------------------------------

    SYMMETRIC_ATTR = "symmetricMode"

    def _ensure_symmetric_attr(self):
        if not self.guides_grp or not cmds.objExists(self.guides_grp):
            return None
        if not cmds.attributeQuery(self.SYMMETRIC_ATTR,
                                    node=self.guides_grp, exists=True):
            cmds.addAttr(self.guides_grp, ln=self.SYMMETRIC_ATTR,
                          at="bool", dv=True, k=True)
        return f"{self.guides_grp}.{self.SYMMETRIC_ATTR}"

    def is_symmetric_mode(self):
        if not cmds.objExists(GUIDES_GRP_NAME):
            return True   # default
        if not cmds.attributeQuery(self.SYMMETRIC_ATTR,
                                    node=GUIDES_GRP_NAME, exists=True):
            return True
        return bool(cmds.getAttr(f"{GUIDES_GRP_NAME}.{self.SYMMETRIC_ATTR}"))

    def set_symmetric_mode(self, on):
        """Flip symmetric mode + push the resulting visibility onto every
        R_-prefixed locator. R groups stay collapsed in the outliner; only
        L and C guides are visible / animatable in symmetric mode."""
        attr = self._ensure_symmetric_attr()
        if not attr:
            cmds.warning("No guides in scene.")
            return
        cmds.setAttr(attr, bool(on))
        self._apply_symmetric_visibility()

    def _apply_symmetric_visibility(self):
        """Push the current symmetricMode state onto every R_-prefixed
        guide locator's visibility. Idempotent. Returns the count of
        locators whose visibility was set."""
        if not self.guides:
            self._refresh_handles()
        show = not self.is_symmetric_mode()
        n = 0
        for guide_name, loc in self.guides.items():
            if not guide_name.startswith("R_"):
                continue
            if not cmds.objExists(loc):
                continue
            try:
                cmds.setAttr(f"{loc}.visibility", show)
                n += 1
            except Exception:
                pass
        return n

    # -----------------------------------------------------------------------

    def _apply_grouping(self):
        """Create anatomical group containers (SPINE_GUIDES, L_ARM_GUIDES,
        …) under RIG_GUIDES_GRP and reparent each named locator into its
        group. Idempotent — skips locators already in the right group.

        Returns the number of locators that were reparented this call.
        """
        moved = 0
        for guide_name, group_name in GUIDE_GROUP_OF.items():
            loc = self.guides.get(guide_name)
            if not loc or not cmds.objExists(loc):
                continue
            # Make sure the group container exists.
            if not cmds.objExists(group_name):
                cmds.group(em=True, n=group_name, p=self.guides_grp)
            current_parent = (cmds.listRelatives(loc, p=True)
                               or [None])[0]
            if current_parent == group_name:
                continue
            # Absolute parent preserves world position.
            cmds.parent(loc, group_name)
            moved += 1
        if moved:
            print(f"[GuideSystem] Moved {moved} guide(s) into anatomical "
                   f"group containers.")
        return moved

    def _apply_notes(self):
        """Add a plain-English 'notes' string attr to every locator that
        has an entry in GUIDE_NOTES. Idempotent — adds the attr if it
        doesn't already exist, and only overwrites the value when the
        text actually differs (so re-running build() doesn't mark every
        locator dirty in the undo queue).

        Returns the number of locators that had their notes set/updated.
        """
        updated = 0
        for guide_name, text in GUIDE_NOTES.items():
            loc = self.guides.get(guide_name)
            if not loc or not cmds.objExists(loc):
                continue
            if not cmds.attributeQuery("notes", node=loc, exists=True):
                cmds.addAttr(loc, ln="notes", dt="string")
            current = cmds.getAttr(f"{loc}.notes") or ""
            if current == text:
                continue
            cmds.setAttr(f"{loc}.notes", text, type="string")
            updated += 1
        if updated:
            print(f"[GuideSystem] Wrote notes onto {updated} locator(s).")
        return updated

    def _apply_parenting(self):
        """Re-parent each guide whose DEFAULT_GUIDES entry has a 'parent'
        key. Idempotent — skips guides that are already correctly parented.

        Returns the number of guides that were reparented this call (0 if
        the hierarchy was already up to date).
        """
        reparented = 0
        for name, info in DEFAULT_GUIDES.items():
            parent_name = info.get("parent")
            if not parent_name:
                continue
            child_loc  = self.guides.get(name)
            parent_loc = self.guides.get(parent_name)
            if not child_loc or not parent_loc:
                cmds.warning(f"[GuideSystem] Missing parent {parent_name!r} "
                             f"for {name!r} — leaving under top group.")
                continue
            current_parent = (cmds.listRelatives(child_loc, p=True)
                               or [None])[0]
            if current_parent == parent_loc:
                continue
            # Default cmds.parent() preserves world position via
            # absolute parenting — what we want here.
            cmds.parent(child_loc, parent_loc)
            reparented += 1
        if reparented:
            print(f"[GuideSystem] Built parent-child chains for "
                   f"{reparented} guide(s).")
        return reparented

    def delete(self):
        if self.exists():
            cmds.delete(GUIDES_GRP_NAME)
            self.guides = {}
            print(f"[GuideSystem] Deleted {GUIDES_GRP_NAME}.")

    def reset_to_defaults(self):
        """Snap every existing guide back to its DEFAULT_GUIDES position."""
        if not self.exists():
            cmds.warning("No guides to reset.")
            return
        self._refresh_handles()
        for name, info in DEFAULT_GUIDES.items():
            loc = self.guides.get(name)
            if loc and cmds.objExists(loc):
                cmds.xform(loc, ws=True, t=info["pos"])
        print("[GuideSystem] Reset all guides to defaults.")

    # -----------------------------------------------------------------------
    # Mirror
    # -----------------------------------------------------------------------

    def mirror_left_to_right(self):
        """Copy every L_* guide's world position to R_* with X negated."""
        if not self.exists():
            cmds.warning("No guides to mirror.")
            return
        self._refresh_handles()
        mirrored = 0
        for name, loc in self.guides.items():
            if not name.startswith("L_"):
                continue
            r_name = "R_" + name[2:]
            r_loc = self.guides.get(r_name)
            if not r_loc or not cmds.objExists(r_loc):
                continue
            x, y, z = cmds.xform(loc, q=True, ws=True, t=True)
            cmds.xform(r_loc, ws=True, t=(-x, y, z))
            mirrored += 1
        print(f"[GuideSystem] Mirrored {mirrored} L -> R guides.")

    # -----------------------------------------------------------------------
    # Read positions for CharacterRig
    # -----------------------------------------------------------------------

    def read_positions(self):
        """Return a positions dict matching CharacterRig's expected schema."""
        if not self.exists():
            cmds.error(f"{GUIDES_GRP_NAME} does not exist. "
                       f"Run GuideSystem().build() first.")
            return {}
        self._refresh_handles()
        p = self._pos  # shorthand

        return {
            "core": {
                "cog": p("C_root"),
            },
            "spine": {
                "hip":   p("C_pelvis"),
                "chest": p("C_chest"),
                # Optional per-FK-ctrl positions. SpineRig uses these
                # if present, otherwise it interpolates linearly between
                # hip and chest. Moving these guides authors a curved
                # spine (hunch, S-curve, arch, etc.).
                "fk_01": p("C_spine_01"),
                "fk_02": p("C_spine_02"),
                "fk_03": p("C_spine_03"),
            },
            "neck": {
                "neck":     p("C_neck"),
                "head":     p("C_head"),
                "head_tip": p("C_headTip"),
            },
            # clavicle_tip is derived from the shoulder position — both
            # sit at the same world point on a real anatomical clavicle,
            # so we drop the separate locator and let read_positions
            # synthesize the tip from the shoulder. ClavicleRig still
            # gets the dict it expects.
            "L_clavicle": {
                "clavicle":     p("L_clavicle"),
                "clavicle_tip": p("L_shoulder"),
            },
            "R_clavicle": {
                "clavicle":     p("R_clavicle"),
                "clavicle_tip": p("R_shoulder"),
            },
            "L_arm": {
                "shoulder": p("L_shoulder"),
                "elbow":    p("L_elbow"),
                "wrist":    p("L_wrist"),
            },
            "R_arm": {
                "shoulder": p("R_shoulder"),
                "elbow":    p("R_elbow"),
                "wrist":    p("R_wrist"),
            },
            "L_leg": {
                "hip":    p("L_hip"),
                "knee":   p("L_knee"),
                "ankle":  p("L_ankle"),
                "ball":   p("L_ball"),
                "toe":    p("L_toe"),
                "toeTip": p("L_toeTip"),
            },
            "R_leg": {
                "hip":    p("R_hip"),
                "knee":   p("R_knee"),
                "ankle":  p("R_ankle"),
                "ball":   p("R_ball"),
                "toe":    p("R_toe"),
                "toeTip": p("R_toeTip"),
            },
            "face": {
                "jaw":            p("C_jaw"),
                "jawTip":         p("C_jawTip"),
                "L_eye":          p("L_eye"),
                "R_eye":          p("R_eye"),
                "eyesLookAt":     p("C_eyesLookAt"),
                # Detailed eyelids (12 joints)
                "L_eyelidUpperInner": p("L_eyelidUpperInner"),
                "L_eyelidUpperMid":   p("L_eyelidUpperMid"),
                "L_eyelidUpperOuter": p("L_eyelidUpperOuter"),
                "L_eyelidLowerInner": p("L_eyelidLowerInner"),
                "L_eyelidLowerMid":   p("L_eyelidLowerMid"),
                "L_eyelidLowerOuter": p("L_eyelidLowerOuter"),
                "R_eyelidUpperInner": p("R_eyelidUpperInner"),
                "R_eyelidUpperMid":   p("R_eyelidUpperMid"),
                "R_eyelidUpperOuter": p("R_eyelidUpperOuter"),
                "R_eyelidLowerInner": p("R_eyelidLowerInner"),
                "R_eyelidLowerMid":   p("R_eyelidLowerMid"),
                "R_eyelidLowerOuter": p("R_eyelidLowerOuter"),
                # Brow
                "L_browInner":    p("L_browInner"),
                "L_browMid":      p("L_browMid"),
                "L_browOuter":    p("L_browOuter"),
                "R_browInner":    p("R_browInner"),
                "R_browMid":      p("R_browMid"),
                "R_browOuter":    p("R_browOuter"),
                # Mouth corners
                "L_mouthCorner":  p("L_mouthCorner"),
                "R_mouthCorner":  p("R_mouthCorner"),
                # Lips
                "C_upperLip":     p("C_upperLip"),
                "L_upperLipMid":  p("L_upperLipMid"),
                "R_upperLipMid":  p("R_upperLipMid"),
                "C_lowerLip":     p("C_lowerLip"),
                "L_lowerLipMid":  p("L_lowerLipMid"),
                "R_lowerLipMid":  p("R_lowerLipMid"),
                # Cheeks
                "L_cheek":        p("L_cheek"),
                "R_cheek":        p("R_cheek"),
                # Nose
                "C_noseTip":      p("C_noseTip"),
                "L_nostril":      p("L_nostril"),
                "R_nostril":      p("R_nostril"),
                # Tongue
                "tongue01":       p("C_tongue01"),
                "tongue02":       p("C_tongue02"),
                "tongue03":       p("C_tongue03"),
                # Teeth (upper attaches to head, lower attaches to jaw)
                "upperTeeth":     p("C_upperTeeth"),
                "lowerTeeth":     p("C_lowerTeeth"),
                # Ears (one FK joint per side, parented to head)
                "L_ear":          p("L_ear"),
                "R_ear":          p("R_ear"),
            },
            # Fingers — keyed by side, then finger name, then joint slot.
            # Thumb has 3 BIND joints; others have 4. 'tip' is for orient
            # only and is not a BIND joint.
            "L_fingers": {
                "thumb":  {"meta": p("L_thumbMeta"),
                            "prox": p("L_thumbProx"),
                            "dist": p("L_thumbDist"),
                            "tip":  p("L_thumbTip")},
                "index":  {"meta": p("L_indexMeta"),
                            "prox": p("L_indexProx"),
                            "mid":  p("L_indexMid"),
                            "dist": p("L_indexDist"),
                            "tip":  p("L_indexTip")},
                "middle": {"meta": p("L_middleMeta"),
                            "prox": p("L_middleProx"),
                            "mid":  p("L_middleMid"),
                            "dist": p("L_middleDist"),
                            "tip":  p("L_middleTip")},
                "ring":   {"meta": p("L_ringMeta"),
                            "prox": p("L_ringProx"),
                            "mid":  p("L_ringMid"),
                            "dist": p("L_ringDist"),
                            "tip":  p("L_ringTip")},
                "pinky":  {"meta": p("L_pinkyMeta"),
                            "prox": p("L_pinkyProx"),
                            "mid":  p("L_pinkyMid"),
                            "dist": p("L_pinkyDist"),
                            "tip":  p("L_pinkyTip")},
            },
            "R_fingers": {
                "thumb":  {"meta": p("R_thumbMeta"),
                            "prox": p("R_thumbProx"),
                            "dist": p("R_thumbDist"),
                            "tip":  p("R_thumbTip")},
                "index":  {"meta": p("R_indexMeta"),
                            "prox": p("R_indexProx"),
                            "mid":  p("R_indexMid"),
                            "dist": p("R_indexDist"),
                            "tip":  p("R_indexTip")},
                "middle": {"meta": p("R_middleMeta"),
                            "prox": p("R_middleProx"),
                            "mid":  p("R_middleMid"),
                            "dist": p("R_middleDist"),
                            "tip":  p("R_middleTip")},
                "ring":   {"meta": p("R_ringMeta"),
                            "prox": p("R_ringProx"),
                            "mid":  p("R_ringMid"),
                            "dist": p("R_ringDist"),
                            "tip":  p("R_ringTip")},
                "pinky":  {"meta": p("R_pinkyMeta"),
                            "prox": p("R_pinkyProx"),
                            "mid":  p("R_pinkyMid"),
                            "dist": p("R_pinkyDist"),
                            "tip":  p("R_pinkyTip")},
            },
            # Tail — 7 BIND segments + tip (orient only).
            "tail": {
                "tail_01": p("C_tail_01"),
                "tail_02": p("C_tail_02"),
                "tail_03": p("C_tail_03"),
                "tail_04": p("C_tail_04"),
                "tail_05": p("C_tail_05"),
                "tail_06": p("C_tail_06"),
                "tail_07": p("C_tail_07"),
                "tip":     p("C_tailTip"),
            },
        }

    # -----------------------------------------------------------------------
    # Snap eyelid guides to a selected mesh edge loop
    # -----------------------------------------------------------------------

    def snap_eyelid_guides_from_loop(self, side):
        """Snap the 6 eyelid guides (UpperInner/Mid/Outer + LowerInner/Mid/Outer)
        for one side to vertices on the currently-selected mesh edge loop.

        Workflow:
          1. Position {side}_eye_GUIDE at the eyeball center
          2. Right-click the mesh → Edge → double-click any edge in the eyelid
             loop to select the whole loop
          3. Call this method (or click the UI button)

        The 6 guides are placed at 6 evenly-spaced angular positions around
        the eye center in the XY plane (mesh assumed to face +Z).
        """
        if side not in ("L", "R"):
            cmds.warning(f"side must be 'L' or 'R', got {side!r}")
            return

        eye_guide = f"{side}_eye{GUIDE_SUFFIX}"
        if not cmds.objExists(eye_guide):
            cmds.warning(f"{eye_guide} doesn't exist. Run Create Guides first.")
            return
        eye_pos = cmds.xform(eye_guide, q=True, ws=True, t=True)

        sel = cmds.ls(sl=True, flatten=True) or []
        if not sel:
            cmds.warning("Select the eyelid edge loop on your mesh first "
                         "(right-click mesh → Edge → double-click a loop edge).")
            return

        # Convert any selection (edges / verts / faces) to a flat vertex list
        verts = cmds.polyListComponentConversion(sel, tv=True)
        verts = cmds.filterExpand(verts, sm=31) or []   # 31 = polygon vertex
        if len(verts) < 6:
            cmds.warning(f"Need at least 6 vertices in the loop; got "
                         f"{len(verts)}. Make sure you selected the FULL loop.")
            return

        # For each vert, compute angle around eye center in the XY plane
        # (character faces +Z, so the eyelid loop is roughly in XY).
        vert_data = []
        for v in verts:
            pos = cmds.xform(v, q=True, ws=True, t=True)
            rel_x = pos[0] - eye_pos[0]
            rel_y = pos[1] - eye_pos[1]
            angle = math.degrees(math.atan2(rel_y, rel_x))
            if angle < 0:
                angle += 360.0
            vert_data.append({"angle": angle, "pos": pos, "vert": v})

        # Target angles per guide.
        # Angle convention (looking down -Z at the front of the face):
        #   0°  = +X (screen right)
        #   90° = +Y (up)
        #   180° = -X (screen left)
        #   270° = -Y (down)
        #
        # Inner and Outer guides land AT THE EYE CORNERS so the lid curves
        # actually start/end at the corner geometry. Otherwise the corners
        # were "barren" — no detail joints there, animator couldn't shape
        # those regions of the lid. Upper Inner + Lower Inner snap to the
        # SAME corner vertex (and same for outer); the upper and lower lid
        # curves both anchor at the corners, which is anatomically correct
        # — the lid loop closes at those two points.
        #
        # For L eye (at +X), Inner = toward face center (-X) = 180°
        #                    Outer = away from center (+X)   = 0°
        # For R eye (at -X), Inner = toward face center (+X) = 0°
        #                    Outer = away (-X)                = 180°
        if side == "L":
            targets = {
                "UpperInner": 180,   # inner corner (shared with LowerInner)
                "UpperMid":   90,    # top of eye
                "UpperOuter": 0,     # outer corner (shared with LowerOuter)
                "LowerInner": 180,
                "LowerMid":   270,   # bottom of eye
                "LowerOuter": 0,
            }
        else:  # R
            targets = {
                "UpperInner": 0,
                "UpperMid":   90,
                "UpperOuter": 180,
                "LowerInner": 0,
                "LowerMid":   270,
                "LowerOuter": 180,
            }

        # For each target angle, find the closest vert by angular distance
        def angular_distance(a, b):
            d = abs(a - b)
            return min(d, 360 - d)

        snapped = 0
        for label, target_angle in targets.items():
            best = min(vert_data,
                       key=lambda d: angular_distance(d["angle"], target_angle))
            guide_name = f"{side}_eyelid{label}{GUIDE_SUFFIX}"
            if cmds.objExists(guide_name):
                cmds.xform(guide_name, ws=True, t=best["pos"])
                snapped += 1
            else:
                cmds.warning(f"Guide doesn't exist: {guide_name}")

        cmds.select(cl=True)
        loop_vert_count = len(verts)
        print(f"[GuideSystem] Snapped {snapped} {side} eyelid guides to "
              f"the selected loop ({loop_vert_count} verts). "
              f"Rebuild rig to apply.")
        return loop_vert_count

    # -----------------------------------------------------------------------
    # Save / load JSON
    # -----------------------------------------------------------------------

    def save_to_json(self, filepath):
        if not self.exists():
            cmds.error("No guides to save.")
            return
        self._refresh_handles()
        data = {name: list(self._pos(name)) for name in self.guides
                if cmds.objExists(self.guides[name])}
        with open(filepath, "w") as f:
            json.dump(data, f, indent=2)
        print(f"[GuideSystem] Saved {len(data)} guide positions to {filepath}")

    def load_from_json(self, filepath):
        with open(filepath, "r") as f:
            data = json.load(f)
        if not self.exists():
            self.build()
        else:
            self._refresh_handles()
        for name, pos in data.items():
            loc = self.guides.get(name)
            if loc and cmds.objExists(loc):
                cmds.xform(loc, ws=True, t=tuple(pos))
        print(f"[GuideSystem] Loaded {len(data)} guide positions from "
              f"{filepath}")

    # -----------------------------------------------------------------------
    # Internals
    # -----------------------------------------------------------------------

    def _create_guide(self, name, pos, color):
        loc_name = name + GUIDE_SUFFIX
        loc = cmds.spaceLocator(n=loc_name)[0]
        for axis in ("X", "Y", "Z"):
            cmds.setAttr(f"{loc}Shape.localScale{axis}", GUIDE_SCALE)
        cmds.xform(loc, ws=True, t=pos)
        cmds.setAttr(f"{loc}Shape.overrideEnabled", 1)
        cmds.setAttr(f"{loc}Shape.overrideColor", color)
        # Hide rotate / scale — guides are positions only
        for a in ("rx", "ry", "rz", "sx", "sy", "sz"):
            cmds.setAttr(f"{loc}.{a}", l=True, k=False, cb=False)
        cmds.parent(loc, self.guides_grp)
        self.guides[name] = loc

    def _stale_guide_locators(self):
        """Return the names of any *_GUIDE locator that's a child of the
        guides group but isn't in DEFAULT_GUIDES anymore — e.g.
        L_clavicleTip_GUIDE / R_clavicleTip_GUIDE after they were
        removed from the schema."""
        if not cmds.objExists(GUIDES_GRP_NAME):
            return []
        valid = {name + GUIDE_SUFFIX for name in DEFAULT_GUIDES}
        # Walk descendants of the guides group.
        descendants = cmds.listRelatives(GUIDES_GRP_NAME, ad=True,
                                          type="transform") or []
        out = []
        for node in descendants:
            short = node.split("|")[-1]
            if short.endswith(GUIDE_SUFFIX) and short not in valid:
                # Skip non-locator label children: *_GUIDE_label.
                if short.endswith(self.LABEL_SUFFIX):
                    continue
                # Confirm it's actually a locator (has a locator shape).
                shapes = cmds.listRelatives(node, s=True,
                                              type="locator") or []
                if shapes:
                    out.append(short)
        return out

    def _refresh_locator_sizes(self):
        """Push the current GUIDE_SCALE onto every existing locator shape
        so changes to the constant propagate to scenes built earlier."""
        for loc in self.guides.values():
            shapes = cmds.listRelatives(loc, s=True,
                                          type="locator") or []
            for sh in shapes:
                for ax in ("X", "Y", "Z"):
                    try:
                        cmds.setAttr(f"{sh}.localScale{ax}", GUIDE_SCALE)
                    except Exception:
                        pass

    def _refresh_handles(self):
        """Re-discover guide locators + the top group by name (in case the
        GuideSystem instance was created fresh against a scene that
        already has guides — e.g. a saved Maya file)."""
        if cmds.objExists(GUIDES_GRP_NAME):
            self.guides_grp = GUIDES_GRP_NAME
        self.guides = {}
        for name in DEFAULT_GUIDES:
            loc = name + GUIDE_SUFFIX
            if cmds.objExists(loc):
                self.guides[name] = loc

    def _pos(self, name):
        loc = self.guides.get(name)
        if not loc or not cmds.objExists(loc):
            cmds.error(f"Missing guide locator: {name}{GUIDE_SUFFIX}")
        return tuple(cmds.xform(loc, q=True, ws=True, t=True))
