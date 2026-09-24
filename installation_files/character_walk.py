"""
===============================================================================
 CHARACTER WALK - drive a character around with WASD, like a game
===============================================================================

   W / S    walk forward / back
   A / D    turn left / right (turns on the spot when standing)
   Shift    run
   Space    jump
   Esc      stop

 Every frame is recorded while you walk and keyed when you stop:

   * the root (C_global_CTRL) carries the path: translateX/Y/Z + rotateY
   * each IK foot steps and PLANTS in world space, so it stays put while
     the body passes over it (and while turning), with heel strike and
     toe-off on the foot's roll attribute
   * the COG bobs, sways over the standing foot, leans into speed and banks
     into turns; the hips and chest twist with the stride
   * the arms hang relaxed (easing down from a T / A pose) and swing from
     the shoulder opposite the legs, IK or FK; elbows bend more running
   * Space jumps with a crouch, a push off the toes, tucked legs and arms
     thrown up in the air, feet touching down first and the knees absorbing
     the landing
   * on a ground mesh the feet land on the surface and the hips follow
     (slopes, steps), and a leg never over-stretches

 Stride and speed scale with the character's leg length, so a giant takes
 big slow steps and a small creature quick little ones. Extra legs
 (creature limbs) and quadruped legs step in a trot: pairs alternate front
 to back.

     import character_walk
     character_walk.show()          # the Walk Mode panel

 Scripting / tests use WalkPass directly (no window):

     w = character_walk.WalkPass()
     w._begin()
     for keys in ({"w"},) * 48:
         w.held = set(keys)
         w._tick_once()
     w._finish()
===============================================================================
"""

import math

import maya.cmds as cmds
import maya.api.OpenMaya as om2

ROOT = "C_global_CTRL"
COG = "C_cog_CTRL"
HIP = "C_spine_hip_CTRL"
CHEST = "C_spine_chest_CTRL"
GROUND_ATTR = "walkGround"
WINDOW_OBJECT_NAME = "DanyalCharacterWalkWindow"

# Speeds are for a character with ~1 m legs and scale with leg length.
DEFAULT_PARAMS = {
    "walk_speed": 140.0,       # cm / s
    "run_speed": 430.0,
    "back_speed": 80.0,
    "accel": 380.0,            # cm / s^2
    "decel": 520.0,
    "turn_rate": 160.0,        # deg / s
    "run_turn_rate": 110.0,
    "stride": 1.0,             # stride length multiplier
    "step_height": 1.0,
    "body_bob": 1.0,
    "lean": 1.0,
    "arm_swing": 1.0,
    "jump": 1.0,
    "arms_down": 1.0,          # 1 = arms hang relaxed, 0 = keep the rig pose
}
REFERENCE_LEG = 90.0            # leg length the speeds above are tuned for


def _clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


def _smooth(t):
    t = _clamp(t, 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def _frac(v):
    return v - math.floor(v)


def _wrap(a):
    return (a + math.pi) % (2.0 * math.pi) - math.pi


def _mm(node, attr="worldMatrix"):
    return om2.MMatrix(cmds.getAttr("%s.%s" % (node, attr)))


def _yaw_matrix(yaw, pos=(0.0, 0.0, 0.0)):
    """World matrix (Maya row-vector convention) of a rotation about Y."""
    c, s = math.cos(yaw), math.sin(yaw)
    return om2.MMatrix((c, 0.0, -s, 0.0,
                        0.0, 1.0, 0.0, 0.0,
                        s, 0.0, c, 0.0,
                        pos[0], pos[1], pos[2], 1.0))


def _root_matrix(x, y, z, yaw):
    return _yaw_matrix(yaw, (x, y, z))


def _translate(x, y, z):
    m = om2.MMatrix()
    m[12], m[13], m[14] = x, y, z
    return m


def _about(pivot, rot):
    """Rotate about a pivot point (row-vector matrices)."""
    return _translate(-pivot.x, -pivot.y, -pivot.z) * rot \
        * _translate(pivot.x, pivot.y, pivot.z)


def _rotation_only(m):
    r = om2.MMatrix(m)
    r[12] = r[13] = r[14] = 0.0
    return r


def _with_position(m, pos):
    r = om2.MMatrix(m)
    r[12], r[13], r[14] = pos.x, pos.y, pos.z
    return r


def _to_root(point, root_x, root_z, yaw):
    """World XZ -> root-space XZ (inverse of _from_root)."""
    dx, dz = point[0] - root_x, point[1] - root_z
    c, s = math.cos(yaw), math.sin(yaw)
    return dx * c - dz * s, dx * s + dz * c


def _from_root(local_x, local_z, root_x, root_z, yaw):
    c, s = math.cos(yaw), math.sin(yaw)
    return (root_x + local_x * c + local_z * s,
            root_z - local_x * s + local_z * c)


# =============================================================================
# Reading the rig
# =============================================================================

def can_walk():
    return cmds.objExists(ROOT) and bool(find_legs())


def _is_leg(ctrl):
    """A biped / creature foot (has the smart foot roll), or any IK leg
    control (quadruped front / back legs, bird legs)."""
    if cmds.attributeQuery("roll", node=ctrl, exists=True):
        return True
    return "leg" in ctrl.lower()


def find_legs():
    """IK feet on the character."""
    legs = []
    for ctrl in cmds.ls("*_IK_CTRL", type="transform") or []:
        if not _is_leg(ctrl):
            continue
        prefix = ctrl[:-len("_IK_CTRL")]
        side = prefix[0] if prefix[:2] in ("L_", "R_") else "C"
        # A bird's foot rolls on footRoll (heel, ball, tip like `roll`).
        roll = next((a for a in ("roll", "footRoll")
                     if cmds.attributeQuery(a, node=ctrl, exists=True)), None)
        legs.append({"ctrl": ctrl, "prefix": prefix, "side": side,
                     "label": prefix[2:] if side != "C" else prefix,
                     "roll_attr": roll})
    return legs


def wing_spars(prefix):
    """Finger tips under an arm's wrist, longest first."""
    wrist = prefix + "_wrist_BIND_JNT"
    if not cmds.objExists(wrist):
        return []
    w = cmds.xform(wrist, q=True, ws=True, t=True)
    tips = [j for j in cmds.listRelatives(wrist, ad=True, type="joint") or []
            if j.endswith("Tip_BIND_JNT")]
    return sorted(tips, key=lambda j: -math.dist(
        w, cmds.xform(j, q=True, ws=True, t=True)))


def is_wing_arm(prefix):
    """An arm used as a wing (the Dragon preset's): its fingers are long
    membrane spars, much longer than the forearm."""
    elbow, wrist = prefix + "_elbow_BIND_JNT", prefix + "_wrist_BIND_JNT"
    tips = wing_spars(prefix)
    if not (tips and cmds.objExists(elbow)):
        return False
    w = cmds.xform(wrist, q=True, ws=True, t=True)
    fore = math.dist(cmds.xform(elbow, q=True, ws=True, t=True), w)
    return math.dist(w, cmds.xform(tips[0], q=True, ws=True, t=True)) \
        > 1.6 * fore


def find_arms():
    """Arms with a shoulder, elbow and wrist: each in IK mode (the hand
    control moves) or FK mode (the shoulder and elbow controls turn). Arms
    that are wings (a dragon's) are left alone: they don't swing."""
    arms = []
    for settings in cmds.ls("*_SETTINGS_CTRL", type="transform") or []:
        prefix = settings[:-len("_SETTINGS_CTRL")]
        if "arm" not in prefix.lower() or "leg" in prefix.lower():
            continue
        if is_wing_arm(prefix):
            continue
        joints = [prefix + "_%s_BIND_JNT" % j
                  for j in ("shoulder", "elbow", "wrist")]
        if not all(cmds.objExists(j) for j in joints):
            continue
        fk = cmds.attributeQuery("ikFkSwitch", node=settings, exists=True) \
            and cmds.getAttr(settings + ".ikFkSwitch") > 0.5
        arm = {"prefix": prefix, "joints": joints, "mode": "fk" if fk else "ik",
               "side": prefix[0] if prefix[:2] in ("L_", "R_") else "C",
               "ctrl": prefix + "_IK_CTRL", "pv": prefix + "_PV_CTRL",
               "fk_shoulder": prefix + "_shoulder_FK_CTRL",
               "fk_elbow": prefix + "_elbow_FK_CTRL"}
        needed = ([arm["ctrl"]] if not fk
                  else [arm["fk_shoulder"], arm["fk_elbow"]])
        if all(cmds.objExists(n) for n in needed):
            arms.append(arm)
    return arms


def find_ik_arms():
    """The arms that are in IK mode."""
    return [a for a in find_arms() if a["mode"] == "ik"]


def _settable(plug):
    node = plug.split(".", 1)[0]
    return (cmds.objExists(node) and not cmds.getAttr(plug, lock=True)
            and cmds.getAttr(plug, se=True))


def walk_channels(legs=None, arms=None):
    """Every channel a walk pass keys."""
    legs = find_legs() if legs is None else legs
    arms = find_arms() if arms is None else arms
    plugs = ["%s.%s" % (ROOT, c) for c in ("translateX", "translateY",
                                           "translateZ", "rotateY")]
    trs = ("translateX", "translateY", "translateZ",
           "rotateX", "rotateY", "rotateZ")
    if cmds.objExists(COG):
        plugs += ["%s.%s" % (COG, c) for c in trs]
    for ctrl, chans in ((HIP, ("rotateX", "rotateY", "rotateZ")),
                        (CHEST, ("rotateY",))):
        if cmds.objExists(ctrl):
            plugs += ["%s.%s" % (ctrl, c) for c in chans]
    for leg in legs:
        plugs += ["%s.%s" % (leg["ctrl"], c) for c in trs]
        if leg.get("roll_attr"):
            plugs.append("%s.%s" % (leg["ctrl"], leg["roll_attr"]))
    for arm in arms:
        if arm["mode"] == "ik":
            plugs += ["%s.%s" % (arm["ctrl"], c) for c in trs]
            if cmds.objExists(arm["pv"]):
                plugs += ["%s.%s" % (arm["pv"], c) for c in trs[:3]]
        else:
            for ctrl in (arm["fk_shoulder"], arm["fk_elbow"]):
                plugs += ["%s.%s" % (ctrl, c) for c in trs[3:]]
    return [p for p in plugs if _settable(p)]


def assigned_ground():
    if (cmds.objExists(ROOT) and cmds.attributeQuery(
            GROUND_ATTR, node=ROOT, exists=True)):
        mesh = cmds.getAttr("%s.%s" % (ROOT, GROUND_ATTR))
        if mesh and cmds.objExists(mesh):
            return mesh
    return None


def assign_ground(mesh):
    """Walk on this mesh (None = the flat ground the character stands on)."""
    if not cmds.objExists(ROOT):
        raise RuntimeError("Build a character rig first.")
    if not cmds.attributeQuery(GROUND_ATTR, node=ROOT, exists=True):
        cmds.addAttr(ROOT, ln=GROUND_ATTR, dt="string")
    cmds.setAttr("%s.%s" % (ROOT, GROUND_ATTR), mesh or "", type="string")


def clear_walk_animation(reset_pose=True):
    """Remove every key a walk pass lays down (and zero those channels)."""
    n = 0
    for plug in walk_channels():
        if cmds.keyframe(plug, q=True, keyframeCount=True):
            cmds.cutKey(plug, clear=True)
            n += 1
        if reset_pose:
            cmds.setAttr(plug, 0.0)
    return n


# =============================================================================
# The walk pass
# =============================================================================

class WalkPass(object):
    """A walk pass without the window: _begin(), then _tick_once() per frame
    with `held` set to the keys down, then _finish() to key it."""

    def __init__(self, fps=None, params=None):
        from vehicle_drive import scene_fps
        self.fps = fps or scene_fps()
        self.params = dict(DEFAULT_PARAMS)
        self.params.update(params or {})
        self.held = set()
        self.recorder = None

    # ---- set up ----------------------------------------------------------------

    def _begin(self):
        from vehicle_drive import DriveRecorder, pause_cached_playback
        import vehicle_sim
        if not can_walk():
            raise RuntimeError("No character with IK legs in the scene.")
        self.cache_was_on = pause_cached_playback()
        if cmds.attributeQuery("autoWalk", node=ROOT, exists=True):
            cmds.setAttr(ROOT + ".autoWalk", 0)      # we do the walking now
        self.gravity = vehicle_sim._gravity()
        self.legs = find_legs()
        self.arms = find_arms()

        # Where the character is now (a pass continues from here).
        self.x = cmds.getAttr(ROOT + ".translateX")
        self.z = cmds.getAttr(ROOT + ".translateZ")
        self.root_y = cmds.getAttr(ROOT + ".translateY")
        self.heading = math.radians(cmds.getAttr(ROOT + ".rotateY"))
        for leg in self.legs:
            m = _mm(leg["ctrl"])
            leg["pos"] = [m[12], m[13], m[14]]
            leg["yaw"] = math.atan2(m[8], m[10])
            leg["swinging"] = False
        self._read_rest()

        # Ground: a mesh, or the level the character stands on now.
        import raycast_ground
        mesh = assigned_ground()
        self.ground_fn = raycast_ground._mesh_fn(mesh) if mesh else None
        self.ground_accel = (self.ground_fn.autoUniformGridParams()
                             if self.ground_fn else None)
        self.flat_y = self.root_y

        size = self.leg_length / REFERENCE_LEG
        self.size = size
        self.speed = 0.0
        self.yaw_rate = 0.0
        self.accel_now = 0.0
        self.phase = 0.0
        # Jump: None -> "crouch" (gather) -> "air" (push off, fly) -> "land"
        # (absorb) -> None. jump_y is the body's height over standing.
        self.jump = None
        self.jump_t = 0.0
        self.jump_y = 0.0
        self.jump_vy = 0.0
        self.jump_lean = 0.0
        self.jump_arm = 0.0
        self.jump_arm_w = 0.0
        self.space_down = False
        self.cog_drop = 0.0
        self.arm_blend = 1.0 if all(a["lowered"] for a in self.arms) else 0.0
        self.root_y_s = self.root_y
        self.prev_euler = {}
        self._start_frame = int(round(cmds.currentTime(q=True)))
        self.recorder = DriveRecorder(walk_channels(self.legs, self.arms),
                                      self._start_frame)

    def _read_rest(self):
        """Rest pose of the feet, COG, hips and hands in the root's space,
        measured with the character's controls zeroed."""
        ctrls = [COG, HIP, CHEST] + [l["ctrl"] for l in self.legs]
        for arm in self.arms:
            ctrls += [arm["ctrl"], arm["pv"], arm["fk_shoulder"],
                      arm["fk_elbow"]]
        # Are the arms already lowered (a pass continuing from a walk)?
        for arm in self.arms:
            node = arm["ctrl"] if arm["mode"] == "ik" else arm["fk_shoulder"]
            moved = 0.0
            for ch in (("tx", "ty", "tz") if arm["mode"] == "ik"
                       else ("rx", "ry", "rz")):
                if cmds.attributeQuery(ch, node=node, exists=True):
                    moved = max(moved, abs(cmds.getAttr(node + "." + ch)))
            arm["lowered"] = moved > (10.0 if arm["mode"] == "ik" else 25.0)
        held = []
        for ctrl in ctrls:
            if not cmds.objExists(ctrl):
                continue
            for ch in ("tx", "ty", "tz", "rx", "ry", "rz", "roll", "footRoll"):
                if not cmds.attributeQuery(ch, node=ctrl, exists=True):
                    continue
                plug = "%s.%s" % (ctrl, ch)
                if not _settable(plug):
                    continue
                held.append((plug, cmds.getAttr(plug)))
                cmds.setAttr(plug, 0.0)
        try:
            root_inv = _mm(ROOT, "worldInverseMatrix")
            self.cog_rest = None
            if cmds.objExists(COG):
                m = _mm(COG) * root_inv
                self.cog_rest = (m[12], m[13], m[14])
            lengths = []
            for leg in self.legs:
                m = _mm(leg["ctrl"]) * root_inv
                leg["rest"] = (m[12], m[13], m[14])
                leg["rest_yaw"] = math.atan2(m[8], m[10])
                hip = leg["prefix"] + "_hip_BIND_JNT"
                knee = leg["prefix"] + "_knee_BIND_JNT"
                ankle = leg["prefix"] + "_ankle_BIND_JNT"
                chain = [j for j in cmds.ls(leg["prefix"] + "_*_BIND_JNT",
                                            type="joint") or []]
                if all(cmds.objExists(j) for j in (hip, knee, ankle)):
                    hp = _mm(hip) * root_inv
                    kn = _mm(knee) * root_inv
                    an = _mm(ankle) * root_inv
                    leg["hip_rest"] = (hp[12], hp[13], hp[14])
                    seg = (math.dist((hp[12], hp[13], hp[14]),
                                     (kn[12], kn[13], kn[14]))
                           + math.dist((kn[12], kn[13], kn[14]),
                                       (an[12], an[13], an[14])))
                    leg["length"] = seg
                    leg["ankle_up"] = an[13] - m[13]
                elif chain:
                    # Any other leg: from its highest joint straight down.
                    tops = [_mm(j) * root_inv for j in chain]
                    tp = max(tops, key=lambda t: t[13])
                    leg["hip_rest"] = (tp[12], tp[13], tp[14])
                    leg["length"] = math.dist((tp[12], tp[13], tp[14]),
                                              (m[12], m[13], m[14]))
                    leg["ankle_up"] = 0.0
                else:
                    top = self.cog_rest[1] if self.cog_rest else 100.0
                    leg["hip_rest"] = (m[12], top, m[14])
                    leg["length"] = top - m[13]
                    leg["ankle_up"] = 0.0
                lengths.append(leg["length"])
            self.leg_length = (sum(lengths) / len(lengths)) if lengths \
                else REFERENCE_LEG
            for arm in self.arms:
                pts = []
                for j in arm["joints"]:
                    m = _mm(j) * root_inv
                    pts.append(om2.MVector(m[12], m[13], m[14]))
                s0, e0, w0 = pts
                arm["shoulder"], arm["elbow"], arm["wrist"] = s0, e0, w0
                arm["upper"] = (e0 - s0).length()
                arm["fore"] = (w0 - e0).length()
                arm["rest_dir"] = (w0 - s0).normal()
                arm["rest_upper_dir"] = (e0 - s0).normal()
                arm["rest_bend"] = (e0 - s0).angle(w0 - e0)
                for key, node in (("rest", arm["ctrl"]), ("rest_pv", arm["pv"]),
                                  ("rest_fs", arm["fk_shoulder"]),
                                  ("rest_fe", arm["fk_elbow"])):
                    arm[key] = (_mm(node) * root_inv
                                if cmds.objExists(node) else None)
        finally:
            for plug, value in held:
                if not cmds.keyframe(plug, q=True, keyframeCount=True):
                    cmds.setAttr(plug, value)
                else:
                    cmds.setAttr(plug, value)
        # Pair legs front to back and alternate pairs (a trot), so one side
        # never lifts all its feet at once.
        labels = sorted({l["label"] for l in self.legs},
                        key=lambda lb: -sum(l["rest"][2] for l in self.legs
                                            if l["label"] == lb))
        for leg in self.legs:
            i = labels.index(leg["label"])
            left = leg["side"] != "R"
            leg["offset"] = 0.0 if left != (i % 2 == 1) else 0.5

    # ---- per frame -------------------------------------------------------------

    def ground(self, x, z):
        if self.ground_fn is None:
            return self.flat_y
        import raycast_ground
        return raycast_ground.raycast_down(self.ground_fn, x, z,
                                           default=self.flat_y,
                                           accel=self.ground_accel)

    def _tick_once(self):
        dt = 1.0 / self.fps
        p = self.params
        keys = self.held
        size = self.size
        walk_v = p["walk_speed"] * size
        run_v = p["run_speed"] * size
        run = "shift" in keys

        # ---- where to go ----
        forward = ("w" in keys) - ("s" in keys)
        turn = ("a" in keys) - ("d" in keys)
        if self.jump == "air":
            target = self.speed               # no steering in mid-air
            turn = 0
        elif forward > 0:
            target = run_v if run else walk_v
        elif forward < 0:
            target = -p["back_speed"] * size
        else:
            target = 0.0
        rate = p["accel"] * size if abs(target) > abs(self.speed) \
            else p["decel"] * size
        before = self.speed
        self.speed += _clamp(target - self.speed, -rate * dt, rate * dt)
        self.accel_now = (self.speed - before) / dt
        run_blend = _smooth((abs(self.speed) - 1.3 * walk_v)
                            / max(1e-6, 0.9 * walk_v))
        turn_max = math.radians(p["turn_rate"] + (p["run_turn_rate"]
                                                  - p["turn_rate"]) * run_blend)
        self.yaw_rate += (turn * turn_max - self.yaw_rate) * min(1.0, 10.0 * dt)
        self.heading += self.yaw_rate * dt
        vx = self.speed * math.sin(self.heading)
        vz = self.speed * math.cos(self.heading)
        self.x += vx * dt
        self.z += vz * dt

        # ---- jump ----
        space = "space" in keys
        if space and not self.space_down and self.jump is None:
            self._start_jump()
        self.space_down = space
        if self.jump is not None:
            self._update_jump(dt)
        self.airborne = self.jump is not None

        # ---- gait ----
        stride = (self.leg_length * (0.7 + 0.5 * abs(self.speed) / walk_v)
                  * p["stride"])
        duty = 0.62 + (0.38 - 0.62) * run_blend
        freq = abs(self.speed) / max(stride, 1e-6)
        if forward:
            freq = max(freq, 0.9)          # step off promptly from standing
        if abs(self.speed) < 0.3 * walk_v and abs(self.yaw_rate) > 0.15:
            freq = max(freq, 1.1 * min(1.0, abs(self.yaw_rate) / turn_max))
        if not self.airborne and freq < 0.6:
            settle = any(l["swinging"] for l in self.legs) or any(
                math.dist(l["pos"][0::2], self._rest_spot(
                    l, self.x, self.z, self.heading)[0::2])
                > 0.12 * self.leg_length for l in self.legs)
            if settle:
                freq = max(freq, 1.2)
        if not self.airborne:
            # A foot left too far behind the hip quickens the steps, so the
            # stride adapts instead of the leg over-stretching.
            limit = self.leg_length * (0.45 + 0.2 * run_blend)
            worst = 0.0
            for leg in self.legs:
                if leg["swinging"]:
                    continue
                spot = self._rest_spot(leg, self.x, self.z, self.heading)
                worst = max(worst, math.hypot(leg["pos"][0] - spot[0],
                                              leg["pos"][2] - spot[2]))
            if worst > limit and freq > 0.0:
                freq *= min(3.0, 1.0 + 4.0 * (worst - limit) / limit)
            self.phase += freq * dt
        stance_time = duty / max(freq, 0.8)

        # ---- feet ----
        ground_ys = []
        for leg in self.legs:
            self._step_leg(leg, freq, duty, stance_time, run_blend,
                           (vx, vz), dt)
            ground_ys.append(leg["ground"])

        # ---- root height follows the ground under the feet ----
        target_y = sum(ground_ys) / len(ground_ys)
        self.root_y_s += (target_y - self.root_y_s) * min(1.0, 12.0 * dt)
        cmds.setAttr(ROOT + ".translateX", self.x)
        cmds.setAttr(ROOT + ".translateY", self.root_y_s)
        cmds.setAttr(ROOT + ".translateZ", self.z)
        cmds.setAttr(ROOT + ".rotateY", math.degrees(self.heading))

        self._pose_body(run_blend, walk_v, dt)
        root_m = _root_matrix(self.x, self.root_y_s, self.z, self.heading)
        for leg in self.legs:
            self._pose_foot(leg)
        self._pose_arms(run_blend, walk_v, root_m, dt)
        self.recorder.capture()

    # ---- jumping -------------------------------------------------------------

    CROUCH_TIME = 0.13

    def _start_jump(self):
        """Gather: bend down and bring the feet under the body."""
        L = self.leg_length
        self.jump = "crouch"
        self.jump_t = 0.0
        self.jump_depth = 0.13 * L * math.sqrt(max(0.2, self.params["jump"]))
        for leg in self.legs:
            leg["jump_from"] = list(leg["pos"])
            leg["swinging"] = False

    def _update_jump(self, dt):
        L = self.leg_length
        g = self.gravity
        self.jump_t += dt
        t = self.jump_t
        if self.jump == "crouch":
            s = _smooth(t / self.CROUCH_TIME)
            self.jump_y = -self.jump_depth * s
            self.jump_lean = math.radians(16.0) * s
            self.jump_arm = math.radians(-45.0) * s
            self.jump_arm_w = min(1.0, self.jump_arm_w + dt / 0.08)
            if t >= self.CROUCH_TIME:
                # Push off: fast enough to rise from the crouch to the apex.
                apex = 0.45 * L * self.params["jump"]
                self.jump_vy = math.sqrt(2.0 * g * (apex + self.jump_depth))
                self.jump_flight = (self.jump_vy + math.sqrt(max(
                    0.0, self.jump_vy ** 2 - 2.0 * g * self.jump_depth))) / g
                self.jump = "air"
                self.jump_t = 0.0
                for leg in self.legs:
                    leg["jump_from"] = list(leg["pos"])
        elif self.jump == "air":
            self.jump_vy -= g * dt
            self.jump_y += self.jump_vy * dt
            f = _clamp(t / max(self.jump_flight, 1e-3), 0.0, 1.0)
            self.jump_lean = (math.radians(16.0) * (1.0 - _smooth(f / 0.3))
                              + math.radians(7.0) * _smooth((f - 0.55) / 0.45))
            # arms throw up on the push, then settle forward for the landing
            up = math.radians(-45.0) + math.radians(120.0) * _smooth(t / 0.14)
            self.jump_arm = up + (math.radians(30.0) - up) * _smooth(
                (f - 0.35) / 0.65)
            self.jump_arm_w = 1.0
            if self.jump_y <= 0.0 and self.jump_vy < 0.0 and t > 0.05:
                self.jump = "land"
                self.jump_t = 0.0
                for leg in self.legs:
                    spot = self._rest_spot(leg, self.x, self.z, self.heading)
                    leg["pos"] = [leg["pos"][0],
                                  self.ground(leg["pos"][0], leg["pos"][2])
                                  + leg["rest"][1], leg["pos"][2]]
                    leg["yaw"] = self.heading + leg["rest_yaw"]
                    leg["swinging"] = False
        elif self.jump == "land":
            # The knees soak up the landing: a damped spring back to standing.
            k = 95.0
            c = 2.0 * 0.75 * math.sqrt(k)
            self.jump_vy += (-k * self.jump_y - c * self.jump_vy) * dt
            self.jump_y = max(-0.3 * L, self.jump_y + self.jump_vy * dt)
            squash = max(0.0, -self.jump_y) / (0.3 * L)
            self.jump_lean = math.radians(18.0) * squash
            self.jump_arm = math.radians(30.0) * (1.0 - _smooth(t / 0.35))
            self.jump_arm_w = 1.0 - _smooth(t / 0.45)
            if t > 0.45 and abs(self.jump_y) < 0.3 and abs(self.jump_vy) < 10.0:
                self.jump = None
                self.jump_y = self.jump_vy = self.jump_lean = 0.0
                self.jump_arm_w = 0.0

    def _jump_leg(self, leg, vel):
        spot = self._rest_spot(leg, self.x, self.z, self.heading)
        L = self.leg_length
        leg["yaw"] = self.heading + leg["rest_yaw"]
        if self.jump == "crouch":
            s = _smooth(self.jump_t / self.CROUCH_TIME)
            src = leg["jump_from"]
            moved = math.hypot(spot[0] - src[0], spot[2] - src[2])
            hop = 0.06 * L * math.sin(math.pi * s) if moved > 0.05 * L else 0.0
            leg["pos"] = [src[0] + (spot[0] - src[0]) * s,
                          src[1] + (spot[1] - src[1]) * s + hop,
                          src[2] + (spot[2] - src[2]) * s]
        elif self.jump == "air":
            if self.jump_y < 0.0:                       # pushing off the toes
                leg["pos"] = list(leg["jump_from"])
                leg["roll"] = 45.0 * _smooth(1.0 + self.jump_y
                                             / max(self.jump_depth, 1e-3))
            else:
                f = _clamp(self.jump_t / max(self.jump_flight, 1e-3), 0.0, 1.0)
                tuck = (0.3 * L * math.sqrt(self.params["jump"])
                        * math.sin(math.pi * f))
                lead = 0.12 * f
                x = spot[0] + vel[0] * lead
                z = spot[2] + vel[1] * lead
                y = max(spot[1] + self.jump_y + tuck,
                        self.ground(x, z) + leg["rest"][1])
                leg["pos"] = [x, y, z]
                leg["roll"] = 25.0 * (1.0 - f)
        # "land": feet stay planted where they touched down
        leg["ground"] = spot[1] - leg["rest"][1]

    def _rest_spot(self, leg, x, z, yaw):
        rx, ry, rz = leg["rest"]
        wx, wz = _from_root(rx, rz, x, z, yaw)
        return [wx, self.ground(wx, wz) + ry, wz]

    def _step_leg(self, leg, freq, duty, stance_time, run_blend, vel, dt):
        phi = _frac(self.phase + leg["offset"])
        moving = min(1.0, abs(self.speed) / (self.params["walk_speed"]
                                             * self.size) + abs(self.yaw_rate))
        leg["roll"] = 0.0
        leg["pitch"] = 0.0
        if self.jump is not None:
            self._jump_leg(leg, vel)
            return
        if phi < duty:
            if leg["swinging"]:                        # touch down
                leg["pos"] = list(leg["target"])
                leg["yaw"] = leg["target_yaw"]
                leg["swinging"] = False
            # heel strike (toes up, easing flat), then toe-off late in the
            # stance. (Heel strike tips the foot on its control rather than
            # a negative roll, which rocks the whole foot down on this rig.)
            leg["pitch"] = 0.0
            if phi < 0.12:
                leg["pitch"] = -14.0 * (1.0 - phi / 0.12) * moving
            elif phi > duty - 0.22:
                leg["roll"] = 38.0 * (phi - (duty - 0.22)) / 0.22 * moving
            leg["ground"] = leg["pos"][1] - leg["rest"][1]
            return

        t = (phi - duty) / (1.0 - duty)
        if not leg["swinging"]:
            leg["swinging"] = True
            leg["start"] = list(leg["pos"])
            leg["start_yaw"] = leg["yaw"]
        # Predict where the body will be when this foot lands (capped, so a
        # slow first step from standing doesn't reach for the horizon) and
        # land half a stance ahead of the hip, within a comfortable reach.
        land_in = min((1.0 - phi) / max(freq, 1e-6), (1.0 - duty) / 0.9)
        ahead = self.yaw_rate * (land_in + 0.5 * stance_time)
        yaw = self.heading + ahead
        px = self.x + vel[0] * land_in
        pz = self.z + vel[1] * land_in
        rx, ry, rz = leg["rest"]
        lead = _to_root((vel[0] * 0.5 * stance_time, vel[1] * 0.5 * stance_time),
                        0.0, 0.0, yaw)
        limit = self.leg_length * (0.42 + 0.25 * run_blend)
        wx, wz = _from_root(rx + _clamp(lead[0], -limit, limit),
                            rz + _clamp(lead[1], -limit, limit), px, pz, yaw)
        ground = self.ground(wx, wz)
        leg["target"] = [wx, ground + ry, wz]
        leg["target_yaw"] = yaw + leg["rest_yaw"]
        s = _smooth(t)
        start = leg["start"]
        dist = math.hypot(wx - start[0], wz - start[2])
        lift = (self.leg_length * (0.10 + 0.06 * run_blend)
                * self.params["step_height"]
                * _clamp(dist / (0.35 * self.leg_length) + 0.25, 0.25, 1.0))
        leg["pos"] = [start[0] + (wx - start[0]) * s,
                      start[1] + (leg["target"][1] - start[1]) * s
                      + lift * math.sin(math.pi * t),
                      start[2] + (wz - start[2]) * s]
        leg["yaw"] = leg["start_yaw"] + _wrap(leg["target_yaw"]
                                              - leg["start_yaw"]) * s
        if t < 0.35:
            leg["roll"] = 38.0 * (1.0 - t / 0.35) * moving
        elif t > 0.7:
            leg["pitch"] = -14.0 * (t - 0.7) / 0.3 * moving
        leg["ground"] = min(start[1], leg["target"][1]) - leg["rest"][1]

    def _pose_body(self, run_blend, walk_v, dt):
        p = self.params
        L = self.leg_length
        speed_f = min(1.0, abs(self.speed) / walk_v)
        gait = 0.0 if self.jump is not None else 1.0    # no walk bob mid-jump
        bob_walk = -0.022 * L * (0.5 + 0.5 * math.cos(4.0 * math.pi
                                                      * self.phase)) * speed_f
        bob_run = 0.03 * L * math.cos(4.0 * math.pi * self.phase) * run_blend
        ty = ((bob_walk * (1.0 - run_blend) + bob_run) * p["body_bob"] * gait
              - 0.05 * L * run_blend + self.jump_y)
        tx = (-0.018 * L * math.sin(2.0 * math.pi * self.phase) * speed_f
              * (1.0 - run_blend) * p["body_bob"] * gait)
        # a crouch pushes the hips back as the chest leans forward
        tz = (0.04 * L * run_blend * p["lean"]
              - 0.35 * max(0.0, -self.jump_y))
        # Keep every leg within reach: drop the hips if a foot is too far.
        drop = 0.0
        if self.cog_rest:
            for leg in self.legs:
                hx, hy, hz = leg["hip_rest"]
                fx, fy, fz = leg["pos"]
                lx, lz = _to_root((fx, fz), self.x, self.z, self.heading)
                hip_y = self.root_y_s + hy + ty
                ankle_y = fy + leg["ankle_up"]
                horiz = math.hypot(lx - (hx + tx), lz - (hz + tz))
                reach = 0.985 * leg["length"]
                if horiz < reach:
                    need = (hip_y - ankle_y) - math.sqrt(reach * reach
                                                         - horiz * horiz)
                    drop = max(drop, need)
        # drop at once when a leg needs it, rise back smoothly
        self.cog_drop = drop if drop > self.cog_drop else (
            self.cog_drop + (drop - self.cog_drop) * min(1.0, 10.0 * dt))
        ty -= max(0.0, self.cog_drop)
        lean_x = ((math.radians(6.0) * min(1.5, self.speed / walk_v) * 0.5
                   + 0.00012 * self.accel_now) * gait + self.jump_lean) \
            * p["lean"]
        bank_z = _clamp(-self.yaw_rate * self.speed / walk_v * 0.08,
                        -0.3, 0.3) * p["lean"]
        self._set(COG, "translateX", tx)
        self._set(COG, "translateY", ty)
        self._set(COG, "translateZ", tz)
        self._set(COG, "rotateX", math.degrees(_clamp(lean_x, -0.4, 0.5)))
        self._set(COG, "rotateY", 0.0)
        self._set(COG, "rotateZ", math.degrees(bank_z))
        twist = math.radians(7.0) * math.sin(2.0 * math.pi * self.phase) \
            * speed_f * p["body_bob"]
        self._set(HIP, "rotateY", math.degrees(twist))
        self._set(HIP, "rotateZ", 3.0 * math.cos(2.0 * math.pi * self.phase)
                  * speed_f * (1.0 - run_blend) * p["body_bob"])
        self._set(HIP, "rotateX", 0.0)
        self._set(CHEST, "rotateY", -0.8 * math.degrees(twist))
        self.cog_motion = (tx, ty, tz, lean_x, bank_z)

    def _pose_foot(self, leg):
        x, y, z = leg["pos"]
        world = (om2.MEulerRotation(math.radians(leg.get("pitch", 0.0)), 0.0,
                                    0.0).asMatrix()
                 * _yaw_matrix(leg["yaw"], (x, y, z)))
        self._set_world(leg["ctrl"], world)
        if leg.get("roll_attr"):
            self._set(leg["ctrl"], leg["roll_attr"], leg["roll"])

    def _body_matrix(self):
        """How the COG has moved this frame, as a root-space matrix about
        its rest position (the arms hang off it)."""
        tx, ty, tz, lean_x, bank_z = self.cog_motion
        c0 = om2.MVector(*self.cog_rest)
        rot = (om2.MEulerRotation(lean_x, 0.0, bank_z,
                                  om2.MEulerRotation.kZXY).asMatrix())
        return _about(om2.MVector(0.0, 0.0, 0.0) + c0, rot) \
            * _translate(tx, ty, tz)

    def _pose_arms(self, run_blend, walk_v, root_m, dt):
        """Arms hang relaxed at the sides (easing down from the rig's T or A
        pose) and swing from the shoulder opposite the legs, elbows bending
        more when running. A jump throws them back, then up."""
        if not self.arms or not self.cog_rest:
            return
        p = self.params
        self.arm_blend = min(1.0, self.arm_blend + dt / 0.35)
        down = self.arm_blend * p["arms_down"]
        body = self._body_matrix()
        amp = (math.radians(18.0 + 24.0 * run_blend) * p["arm_swing"]
               * min(1.2, abs(self.speed) / walk_v))
        by_side = {l["side"]: l for l in self.legs if l["label"] == "leg"} \
            or {l["side"]: l for l in self.legs}
        forward = om2.MVector(0.0, 0.0, 1.0)
        for arm in self.arms:
            leg = by_side.get(arm["side"])
            phi = _frac(self.phase + (leg["offset"] if leg else 0.0))
            fwd = -amp * math.cos(2.0 * math.pi * phi)
            fwd += (self.jump_arm - fwd) * self.jump_arm_w
            bend = (math.radians(12.0 + 70.0 * run_blend) + 0.5 * max(0.0, fwd)
                    + math.radians(30.0) * self.jump_arm_w)
            hang = down
            if arm["rest_bend"] > math.radians(45.0):
                # Arms the rig carries folded (a raptor's): keep the fold
                # and just rock them a little.
                hang = 0.0
                fwd *= 0.35
                bend = arm["rest_bend"] + 0.3 * (bend - math.radians(12.0))
            s0 = arm["shoulder"]
            side = 1.0 if s0.x >= 0.0 else -1.0
            relaxed = om2.MVector(0.2 * side, -1.0, 0.1).normal()
            d = (arm["rest_dir"] * (1.0 - hang) + relaxed * hang).normal()
            d = d.rotateBy(om2.MQuaternion(-fwd, om2.MVector(1.0, 0.0, 0.0)))
            axis = d ^ forward
            axis = (axis.normal() if axis.length() > 1e-6
                    else om2.MVector(-1.0, 0.0, 0.0))
            u, f = arm["upper"], arm["fore"]
            alpha = math.atan2(f * math.sin(bend), u + f * math.cos(bend))
            upper = d.rotateBy(om2.MQuaternion(-alpha, axis))
            fore = upper.rotateBy(om2.MQuaternion(bend, axis))
            elbow = s0 + upper * u
            hand = elbow + fore * f
            rest_fore = (arm["wrist"] - arm["elbow"]).normal()
            if arm["mode"] == "ik":
                rot = _rotation_only(arm["rest"]) \
                    * om2.MQuaternion(rest_fore, fore).asMatrix()
                self._set_world(arm["ctrl"],
                                _with_position(rot, hand) * body * root_m)
                if arm["rest_pv"] is not None:
                    mid = (s0 + hand) * 0.5
                    out = elbow - mid
                    out = (out.normal() if out.length() > 1e-3 else
                           om2.MVector(0.0, 0.0, -1.0))
                    pv = elbow + out * (u + f)
                    self._set_world(arm["pv"],
                                    _translate(pv.x, pv.y, pv.z) * body
                                    * root_m)
            else:
                q_up = om2.MQuaternion(arm["rest_upper_dir"], upper)
                shoulder = arm["rest_fs"] * _about(s0, q_up.asMatrix())
                self._set_world(arm["fk_shoulder"], shoulder * body * root_m)
                turned = rest_fore.rotateBy(q_up)
                q_el = om2.MQuaternion(turned, fore)
                elbow_m = (arm["rest_fe"] * _about(s0, q_up.asMatrix())
                           * _about(elbow, q_el.asMatrix()))
                self._set_world(arm["fk_elbow"], elbow_m * body * root_m)

    # ---- writing values ----------------------------------------------------------

    def _set(self, node, attr, value):
        plug = "%s.%s" % (node, attr)
        if cmds.objExists(node) and cmds.attributeQuery(
                attr, node=node, exists=True) and _settable(plug):
            cmds.setAttr(plug, value)

    def _set_world(self, ctrl, world):
        local = world * _mm(ctrl, "parentInverseMatrix")
        tm = om2.MTransformationMatrix(local)
        t = tm.translation(om2.MSpace.kTransform)
        order = cmds.getAttr(ctrl + ".rotateOrder")
        e = tm.rotation().reorder(order)
        prev = self.prev_euler.get(ctrl)
        if prev is not None:
            e = e.closestSolution(prev)
        self.prev_euler[ctrl] = e
        for attr, value in (("translateX", t.x), ("translateY", t.y),
                            ("translateZ", t.z),
                            ("rotateX", math.degrees(e.x)),
                            ("rotateY", math.degrees(e.y)),
                            ("rotateZ", math.degrees(e.z))):
            self._set(ctrl, attr, value)

    def _finish(self):
        from vehicle_drive import resume_cached_playback
        rec = self.recorder
        n = rec.bake() if rec else 0
        resume_cached_playback(getattr(self, "cache_was_on", False))
        if n:
            cmds.currentTime(rec.end, edit=True)
        return (rec.start if rec else 0, rec.end if rec else 0, n)


# =============================================================================
# The WASD panel
# =============================================================================

def _qt():
    try:
        from PySide2 import QtCore, QtWidgets
        from shiboken2 import wrapInstance
    except ImportError:
        from PySide6 import QtCore, QtWidgets
        from shiboken6 import wrapInstance
    return QtCore, QtWidgets, wrapInstance


def _make_session_class():
    QtCore, QtWidgets, wrapInstance = _qt()
    import maya.OpenMayaUI as omui

    key_map = {
        QtCore.Qt.Key_W: "w", QtCore.Qt.Key_A: "a",
        QtCore.Qt.Key_S: "s", QtCore.Qt.Key_D: "d",
        QtCore.Qt.Key_Up: "w", QtCore.Qt.Key_Left: "a",
        QtCore.Qt.Key_Down: "s", QtCore.Qt.Key_Right: "d",
        QtCore.Qt.Key_Shift: "shift", QtCore.Qt.Key_Space: "space",
    }

    class WalkSession(QtWidgets.QDialog, WalkPass):
        """Small focused panel that captures WASD and walks the character."""

        def __init__(self, fps=None, parent=None):
            ptr = omui.MQtUtil.mainWindow()
            main = wrapInstance(int(ptr), QtWidgets.QWidget)
            QtWidgets.QDialog.__init__(self, parent or main)
            from vehicle_drive import scene_fps
            self.fps = fps or scene_fps()
            self.params = dict(DEFAULT_PARAMS)
            self.held = set()
            self.recorder = None
            self.walking = False
            self.setObjectName(WINDOW_OBJECT_NAME)
            self.setWindowTitle("Walk Mode")
            self.setWindowFlags(self.windowFlags() | QtCore.Qt.Window
                                | QtCore.Qt.WindowStaysOnTopHint)
            self.setFocusPolicy(QtCore.Qt.StrongFocus)
            self.setMinimumWidth(270)
            self.timer = QtCore.QTimer(self)
            self.timer.setInterval(int(1000.0 / self.fps))
            self.timer.timeout.connect(self._tick)
            self._build_ui(QtCore, QtWidgets)
            self.key_map = key_map

        def _build_ui(self, QtCore, QtWidgets):
            lay = QtWidgets.QVBoxLayout(self)
            lay.setContentsMargins(12, 12, 12, 12)
            lay.setSpacing(8)
            title = QtWidgets.QLabel("WALK MODE")
            title.setStyleSheet("QLabel { font-size: 14pt; font-weight: bold;"
                                " color: #ffd9a0; }")
            lay.addWidget(title)
            help_lbl = QtWidgets.QLabel(
                "W / S     walk forward / back\n"
                "A / D     turn (on the spot when standing)\n"
                "Shift     run\n"
                "Space    jump\n"
                "Esc        stop\n\n"
                "Keep THIS panel focused (click it if keys stop responding).")
            help_lbl.setStyleSheet("QLabel { color: #ccc; }")
            lay.addWidget(help_lbl)

            tune = QtWidgets.QGroupBox("Movement")
            grid = QtWidgets.QGridLayout(tune)
            grid.setSpacing(4)
            rows = (("Walk speed", "walk_speed", 20.0, 600.0, 10.0,
                     "cm per second for a 1 m leg (scales with the character)."),
                    ("Run speed", "run_speed", 100.0, 1500.0, 20.0,
                     "Shift speed."),
                    ("Turn rate", "turn_rate", 30.0, 540.0, 10.0,
                     "Degrees per second."),
                    ("Stride", "stride", 0.4, 2.5, 0.05,
                     "Longer or shorter steps."),
                    ("Step height", "step_height", 0.0, 3.0, 0.1,
                     "How high the feet lift."),
                    ("Body bob", "body_bob", 0.0, 3.0, 0.1,
                     "Bounce, sway and hip twist."),
                    ("Lean", "lean", 0.0, 3.0, 0.1,
                     "Leaning into speed and turns."),
                    ("Arms down", "arms_down", 0.0, 1.0, 0.05,
                     "1 = arms hang relaxed at the sides, 0 = keep the "
                     "rig's T / A pose."),
                    ("Arm swing", "arm_swing", 0.0, 3.0, 0.1,
                     "Arms swing from the shoulder, opposite the legs."),
                    ("Jump", "jump", 0.2, 3.0, 0.1, "Jump strength."))
            for r, (label, key, lo, hi, step, tip) in enumerate(rows):
                grid.addWidget(QtWidgets.QLabel(label), r, 0)
                sp = QtWidgets.QDoubleSpinBox()
                sp.setRange(lo, hi)
                sp.setSingleStep(step)
                sp.setValue(self.params[key])
                sp.setToolTip(tip)
                sp.setFocusPolicy(QtCore.Qt.ClickFocus)
                sp.valueChanged.connect(
                    lambda v, k=key: self.params.__setitem__(k, v))
                grid.addWidget(sp, r, 1)
            lay.addWidget(tune)

            ground_row = QtWidgets.QHBoxLayout()
            self.ground_lbl = QtWidgets.QLabel()
            self._refresh_ground()
            btn_ground = QtWidgets.QPushButton("Use Selected as Ground")
            btn_ground.setToolTip("Select a terrain mesh and click: feet land "
                                  "on it.\nClick with nothing selected to "
                                  "walk on flat ground.")
            btn_ground.setFocusPolicy(QtCore.Qt.NoFocus)
            btn_ground.clicked.connect(self._on_ground)
            ground_row.addWidget(self.ground_lbl, 1)
            ground_row.addWidget(btn_ground)
            lay.addLayout(ground_row)

            self.status = QtWidgets.QLabel("Ready: press Start, then WASD.")
            self.status.setStyleSheet("QLabel { color: #8f8; border-top: 1px "
                                      "solid #444; padding-top: 6px; }")
            lay.addWidget(self.status)
            row = QtWidgets.QHBoxLayout()
            self.btn_start = QtWidgets.QPushButton("Start Walking")
            self.btn_start.clicked.connect(self.start_walking)
            self.btn_stop = QtWidgets.QPushButton("Stop (Esc)")
            self.btn_stop.clicked.connect(self.stop_walking)
            self.btn_stop.setEnabled(False)
            for b in (self.btn_start, self.btn_stop):
                b.setFocusPolicy(QtCore.Qt.NoFocus)
                row.addWidget(b)
            lay.addLayout(row)
            self.btn_clear = QtWidgets.QPushButton("Clear Walk Animation")
            self.btn_clear.setFocusPolicy(QtCore.Qt.NoFocus)
            self.btn_clear.clicked.connect(self._on_clear)
            lay.addWidget(self.btn_clear)

        def _refresh_ground(self):
            mesh = assigned_ground()
            self.ground_lbl.setText("Ground: %s" % (mesh or "flat"))

        def _on_ground(self):
            sel = [s for s in (cmds.ls(sl=True, type="transform") or [])
                   if cmds.listRelatives(s, s=True, type="mesh", ni=True)]
            assign_ground(sel[0] if sel else None)
            self._refresh_ground()

        def start_walking(self):
            if not can_walk():
                cmds.warning("No character with IK legs found. Build a "
                             "character rig first (legs in IK).")
                return
            cmds.undoInfo(openChunk=True)
            try:
                self._begin()
            except Exception:
                cmds.undoInfo(closeChunk=True)
                raise
            self.held.clear()
            self.walking = True
            self.btn_start.setEnabled(False)
            self.btn_stop.setEnabled(True)
            self.status.setText("WALKING (recording): WASD, Shift, Space. "
                                "Esc to stop.")
            self.timer.start()
            self.setFocus()
            self.activateWindow()
            self.raise_()

        def stop_walking(self):
            if not self.walking:
                return
            self.walking = False
            self.timer.stop()
            self.held.clear()
            self.status.setText("Writing keys...")
            QtWidgets.QApplication.processEvents()
            try:
                start, end, n = self._finish()
            finally:
                try:
                    cmds.undoInfo(closeChunk=True)
                except Exception:
                    pass
            self.btn_start.setEnabled(True)
            self.btn_stop.setEnabled(False)
            self.status.setText("Stopped. Keyed frames %d to %d. Press play."
                                % (start, end) if n else "Stopped.")

        def _on_clear(self):
            if self.walking:
                self.stop_walking()
            cmds.undoInfo(openChunk=True)
            try:
                n = clear_walk_animation()
            finally:
                cmds.undoInfo(closeChunk=True)
            self.status.setText("Cleared %d walk channels." % n)

        def _tick(self):
            if not self.walking:
                return
            self._tick_once()
            self.status.setText("REC frame %d   speed %.0f"
                                % (self.recorder.end, self.speed))

        def keyPressEvent(self, event):
            if event.isAutoRepeat():
                return
            if event.key() == QtCore.Qt.Key_Escape:
                self.stop_walking()
                return
            k = self.key_map.get(event.key())
            if k:
                self.held.add(k)
                event.accept()
                return
            QtWidgets.QDialog.keyPressEvent(self, event)

        def keyReleaseEvent(self, event):
            if event.isAutoRepeat():
                return
            k = self.key_map.get(event.key())
            if k:
                self.held.discard(k)
                event.accept()
                return
            QtWidgets.QDialog.keyReleaseEvent(self, event)

        def focusOutEvent(self, event):
            self.held.clear()          # never keep walking on a lost key-up
            QtWidgets.QDialog.focusOutEvent(self, event)

        def closeEvent(self, event):
            self.stop_walking()
            QtWidgets.QDialog.closeEvent(self, event)

    return WalkSession


_walk_window = None


def show():
    """Open the Walk Mode panel."""
    global _walk_window
    QtCore, QtWidgets, _ = _qt()
    for w in QtWidgets.QApplication.topLevelWidgets():
        if w.objectName() == WINDOW_OBJECT_NAME:
            try:
                w.close()
                w.deleteLater()
            except Exception:
                pass
    if not can_walk():
        cmds.warning("No character with IK legs in the scene. Build a "
                     "character rig first.")
    _walk_window = _make_session_class()()
    _walk_window.show()
    _walk_window.raise_()
    _walk_window.setFocus()
    return _walk_window
