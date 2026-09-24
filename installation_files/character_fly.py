"""
===============================================================================
 CHARACTER FLY - fly a bird with WASD, like a game
===============================================================================

   W        fly faster (harder wingbeats)     S       slow down, flare
   A / D    turn (the bird banks into it)     Shift   dive, wings tucked
   Space    climb, or take off from the ground
   Esc      stop and key

 On the ground press Space (or W) to take off. To land, hold S near the
 ground: the legs reach forward, the wings and tail flare, the feet plant
 and the wings fold. Let go of everything and the bird cruises, gliding
 between bursts of wingbeats.

 Every frame is recorded while you fly and keyed when you stop:

   * the root (C_global_CTRL) carries the path, height and heading
   * the COG pitches with climbs and dives, banks into turns (the real
     coordinated-turn angle for the speed) and rises with each downstroke
   * the wings beat: the downstroke sweeps forward fully spread, the
     upstroke flexes the elbow and wrist and lets the feathers close; they
     glide between bursts, tuck back for a dive, flare to land and fold on
     the ground. IK or FK wings, feathers spread with the stroke
   * the legs tuck up with the talons closed in flight and reach forward
     with the talons open to land
   * the tail fans and lifts to brake and swings into turns
   * the head stays level while the body pitches and banks

 Speeds and the wingbeat scale with the wingspan, so a big eagle flaps
 slower than a small bird.

     import character_fly
     character_fly.show()          # the Fly Mode panel

 Scripting / tests use FlyPass directly (no window):

     f = character_fly.FlyPass()
     f._begin()
     for keys in ({"space"},) * 48:
         f.held = set(keys)
         f._tick_once()
     f._finish()
===============================================================================
"""

import math

import maya.cmds as cmds
import maya.api.OpenMaya as om2

import character_walk as cw
from character_walk import (ROOT, COG, _clamp, _smooth, _frac, _mm,
                            _root_matrix, _about, _settable, _qt)

HEAD = "C_head_CTRL"
CHEST = "C_spine_chest_CTRL"
TAIL_BASE = "C_tailBase_CTRL"
TAIL_SETTINGS = "C_tailFan_SETTINGS_CTRL"
WINDOW_OBJECT_NAME = "DanyalCharacterFlyWindow"

# Speeds are for a bird with a 2.5 m wingspan and scale with the span.
DEFAULT_PARAMS = {
    "cruise_speed": 450.0,     # cm / s
    "fast_speed": 800.0,       # W
    "slow_speed": 170.0,       # S
    "dive_speed": 1200.0,      # Shift
    "climb_rate": 260.0,       # cm / s with Space
    "turn_rate": 75.0,         # deg / s
    "bank": 1.0,               # how far it banks into turns
    "flap_rate": 2.6,          # wingbeats / s at cruise
    "flap_size": 1.0,          # wingbeat amplitude
    "glide": 1.0,              # 0 = flaps all the time, 1 = glides between
    "body_motion": 1.0,        # pitch, bob and flare
}
REFERENCE_SPAN = 250.0          # wingspan the speeds above are tuned for

UP = om2.MVector(0.0, 1.0, 0.0)


def _rot(axis, degrees):
    return om2.MQuaternion(math.radians(degrees), axis).asMatrix()


def _rot_x(d):
    return _rot(om2.MVector(1.0, 0.0, 0.0), d)


def _rot_y(d):
    return _rot(om2.MVector(0.0, 1.0, 0.0), d)


def _rot_z(d):
    return _rot(om2.MVector(0.0, 0.0, 1.0), d)


def _point(m):
    return om2.MVector(m[12], m[13], m[14])


def _moved(p, m):
    q = om2.MPoint(p) * m
    return om2.MVector(q.x, q.y, q.z)


def _ease(value, target, rate, dt):
    return value + (target - value) * min(1.0, rate * dt)


# =============================================================================
# Reading the rig
# =============================================================================

def find_wings():
    """Wings with a shoulder, elbow and wrist: each in IK mode (the hand
    control moves) or FK mode (the shoulder, elbow and wrist turn).

    Feathered wings (the bird: *_wing, feathers on wingSpread 0..1) and
    membrane wings (the Dragon preset: arms whose fingers are long spars,
    closed with spread -5, open as built at 0)."""
    wings = []
    for settings in cmds.ls("*_SETTINGS_CTRL", type="transform") or []:
        prefix = settings[:-len("_SETTINGS_CTRL")]
        if "wing" in prefix.lower():
            tip = "%s_wingTip_BIND_JNT" % prefix
            tip = tip if cmds.objExists(tip) else None
            spars = [tip] if tip else []
            spread = ("wingSpread", 0.0, 1.0)
        elif "arm" in prefix.lower() and cw.is_wing_arm(prefix):
            # The spars are often all one length: take the outermost for
            # the span (the same finger on both sides).
            spars = cw.wing_spars(prefix)
            tip = max(spars, key=lambda j: abs(cmds.xform(
                j, q=True, ws=True, t=True)[0]))
            spread = ("spread", -5.0, 0.0)
        else:
            continue
        joints = ["%s_%s_BIND_JNT" % (prefix, j)
                  for j in ("shoulder", "elbow", "wrist")]
        if not all(cmds.objExists(j) for j in joints):
            continue
        fk = (cmds.attributeQuery("ikFkSwitch", node=settings, exists=True)
              and cmds.getAttr(settings + ".ikFkSwitch") > 0.5)
        wing = {"prefix": prefix, "settings": settings, "joints": joints,
                "tip": tip, "spars": spars, "spread": spread,
                "mode": "fk" if fk else "ik",
                "ctrl": prefix + "_IK_CTRL", "pv": prefix + "_PV_CTRL",
                "fk": ["%s_%s_FK_CTRL" % (prefix, j)
                       for j in ("shoulder", "elbow", "wrist")]}
        needed = wing["fk"] if fk else [wing["ctrl"]]
        if all(cmds.objExists(n) for n in needed):
            wings.append(wing)
    return wings


def can_fly():
    return cmds.objExists(ROOT) and bool(find_wings())


def fly_channels(wings=None, legs=None):
    """Every channel a flight keys."""
    wings = find_wings() if wings is None else wings
    legs = cw.find_legs() if legs is None else legs
    tr = ("translateX", "translateY", "translateZ")
    ro = ("rotateX", "rotateY", "rotateZ")
    plugs = ["%s.%s" % (ROOT, c) for c in tr + ("rotateY",)]
    for node, chans in ((COG, tr + ro), (HEAD, ro), (TAIL_BASE, ro),
                        (TAIL_SETTINGS, ("tailSpread", "tailLift"))):
        if cmds.objExists(node):
            plugs += ["%s.%s" % (node, c) for c in chans
                      if cmds.attributeQuery(c, node=node, exists=True)]
    for wing in wings:
        if wing["mode"] == "ik":
            plugs += ["%s.%s" % (wing["ctrl"], c) for c in tr + ro]
            if cmds.objExists(wing["pv"]):
                plugs += ["%s.%s" % (wing["pv"], c) for c in tr]
        else:
            for ctrl in wing["fk"]:
                plugs += ["%s.%s" % (ctrl, c) for c in ro]
        attr = wing["spread"][0]
        if cmds.attributeQuery(attr, node=wing["settings"], exists=True):
            plugs.append("%s.%s" % (wing["settings"], attr))
    for leg in legs:
        plugs += ["%s.%s" % (leg["ctrl"], c) for c in tr + ro]
        settings = leg["prefix"] + "_SETTINGS_CTRL"
        if cmds.objExists(settings) and cmds.attributeQuery(
                "talonCurl", node=settings, exists=True):
            plugs.append(settings + ".talonCurl")
    return [p for p in dict.fromkeys(plugs) if _settable(p)]


def clear_fly_animation(reset_pose=True):
    """Remove every key a flight lays down (and put those channels back to
    their defaults)."""
    n = 0
    for plug in fly_channels():
        if cmds.keyframe(plug, q=True, keyframeCount=True):
            cmds.cutKey(plug, clear=True)
            n += 1
        if reset_pose:
            node, attr = plug.split(".", 1)
            default = cmds.attributeQuery(attr, node=node, listDefault=True)
            cmds.setAttr(plug, default[0] if default else 0.0)
    return n


# =============================================================================
# The flight
# =============================================================================

class FlyPass(object):
    """A flight without the window: _begin(), then _tick_once() per frame
    with `held` set to the keys down, then _finish() to key it."""

    CROUCH_TIME = 0.14
    LAUNCH_TIME = 0.55

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
        import raycast_ground
        if not can_fly():
            raise RuntimeError("No bird with wings in the scene.")
        self.cache_was_on = pause_cached_playback()
        if cmds.attributeQuery("autoWalk", node=ROOT, exists=True):
            cmds.setAttr(ROOT + ".autoWalk", 0)
        self.gravity = vehicle_sim._gravity()
        self.wings = find_wings()
        self.legs = cw.find_legs()

        self.x = cmds.getAttr(ROOT + ".translateX")
        self.y = cmds.getAttr(ROOT + ".translateY")
        self.z = cmds.getAttr(ROOT + ".translateZ")
        self.heading = math.radians(cmds.getAttr(ROOT + ".rotateY"))
        self._read_rest()

        # Flat ground is the world floor (or lower, if the bird stands
        # below it). A pass can start in mid-air, so it can't be read from
        # where the bird is now; for terrain use Use Selected as Ground.
        self.flat_y = min(0.0, self.y)
        mesh = cw.assigned_ground()
        self.ground_fn = raycast_ground._mesh_fn(mesh) if mesh else None
        self.ground_accel = (self.ground_fn.autoUniformGridParams()
                             if self.ground_fn else None)

        p = self.params
        size = self.size
        on_ground = self.y <= self.ground(self.x, self.z) + 0.05 * self.leg_length
        self.state = "ground" if on_ground else "air"
        self.state_t = 1.0
        self.speed = 0.0 if on_ground else p["cruise_speed"] * size
        self.vy = 0.0
        self.yaw_rate = 0.0
        self.pitch = self.bank = 0.0
        self.phase = 0.0
        self.intensity = 0.0
        self.cruise_clock = 0.0
        # The wings start in the rig's pose and settle into the flight pose.
        self.fold = 0.0
        self.arm_fold = 0.0
        self.sweep = self.twist = 0.0
        self.tuck = 0.0 if on_ground else 1.0
        self.reach = 0.0
        self.crouch = 0.0
        self.squash = self.squash_v = 0.0
        self.talon = 0.0
        self.tail_spread = self.tail_lift = 0.0
        self.launched = False
        self.space_down = False
        self.prev_euler = {}
        self.feet_world = {}
        if on_ground:
            self._plant_feet()
        self._start_frame = int(round(cmds.currentTime(q=True)))
        self.recorder = DriveRecorder(fly_channels(self.wings, self.legs),
                                      self._start_frame)

    def _read_rest(self):
        """Rest pose of the body, wings, legs and head in the root's space,
        measured with the character's controls zeroed."""
        ctrls = [COG, CHEST, HEAD, TAIL_BASE]
        for wing in self.wings:
            ctrls += [wing["ctrl"], wing["pv"]] + wing["fk"]
        ctrls += [l["ctrl"] for l in self.legs]
        held = []
        for ctrl in ctrls:
            if not cmds.objExists(ctrl):
                continue
            for ch in ("tx", "ty", "tz", "rx", "ry", "rz"):
                plug = "%s.%s" % (ctrl, ch)
                if cmds.attributeQuery(ch, node=ctrl, exists=True) \
                        and _settable(plug):
                    held.append((plug, cmds.getAttr(plug)))
                    cmds.setAttr(plug, 0.0)
        try:
            root_inv = _mm(ROOT, "worldInverseMatrix")

            def rest(node):
                return _mm(node) * root_inv if cmds.objExists(node) else None

            self.cog_rest = rest(COG)
            self.chest_rest = rest(CHEST) or self.cog_rest
            self.head_rest = rest(HEAD)
            span = 0.0
            for wing in self.wings:
                pts = [_point(rest(j)) for j in wing["joints"]]
                tip = (_point(rest(wing["tip"])) if wing["tip"]
                       else pts[2] + (pts[2] - pts[1]))
                wing["S"], wing["E"], wing["W"] = pts
                wing["T"] = tip
                wing["out"] = 1.0 if tip.x >= pts[0].x else -1.0
                # Folding swings the hand round until it points straight
                # back: the shoulder (-78) and elbow (-150) folds turn it
                # too, so the wrist fold makes up the rest.
                # (a membrane wing folds its spars' average direction)
                spar_pts = [_point(rest(j)) for j in wing["spars"]] or [tip]
                hand = om2.MVector()
                for sp in spar_pts:
                    hand += (sp - pts[2]).normal()
                ahead = math.degrees(math.atan2(-hand.z, hand.x
                                                * wing["out"]))
                # A membrane's spars fan inward as they close, so it stops a
                # little short of straight back to keep them off the back.
                short = 12.0 if len(wing["spars"]) > 1 else 0.0
                wing["hand_fold"] = _clamp(90.0 - ahead + 72.0 - short,
                                           90.0, 175.0)
                wing["rest_ik"] = rest(wing["ctrl"])
                wing["rest_pv"] = rest(wing["pv"])
                wing["rest_fk"] = [rest(c) for c in wing["fk"]]
                span = max(span, abs(tip.x) * 2.0)
            self.span = span or REFERENCE_SPAN
            self.size = self.span / REFERENCE_SPAN
            lengths = []
            for leg in self.legs:
                leg["rest_m"] = rest(leg["ctrl"])
                named = ["%s_%s_BIND_JNT" % (leg["prefix"], j)
                         for j in ("hip", "knee", "ankle")]
                if all(cmds.objExists(j) for j in named):
                    chain = named          # a biped / creature leg
                else:
                    chain = [j for j in cmds.ls(
                        leg["prefix"] + "_*_BIND_JNT", type="joint") or []
                        if "Prox" not in j and "Dist" not in j
                        and "Tip" not in j and "bendy" not in j]
                pts = sorted((_point(rest(j)) for j in chain),
                             key=lambda v: -v.y)
                foot = _point(leg["rest_m"])
                if pts:
                    hip = pts[0]
                    length = sum((a - b).length()
                                 for a, b in zip(pts, pts[1:]))
                else:
                    hip = foot + UP * 50.0
                    length = 50.0
                leg["hip"] = hip
                leg["length"] = max(length, (hip - foot).length())
                lengths.append(leg["length"])
            self.leg_length = (sum(lengths) / len(lengths)) if lengths \
                else 0.2 * self.span
        finally:
            for plug, value in held:
                cmds.setAttr(plug, value)

    def ground(self, x, z):
        if self.ground_fn is None:
            return self.flat_y
        import raycast_ground
        return raycast_ground.raycast_down(self.ground_fn, x, z,
                                           default=self.flat_y,
                                           accel=self.ground_accel)

    # ---- per frame -------------------------------------------------------------

    def _set_state(self, state):
        self.state = state
        self.state_t = 0.0
        if state == "takeoff":
            self.launched = False

    def _tick_once(self):
        dt = 1.0 / self.fps
        p = self.params
        keys = self.held
        size = self.size
        L = self.leg_length
        cruise = p["cruise_speed"] * size
        fast = p["fast_speed"] * size
        slow = p["slow_speed"] * size
        dive_v = p["dive_speed"] * size
        climb = p["climb_rate"] * size
        w, s = "w" in keys, "s" in keys
        dive, space = "shift" in keys, "space" in keys
        turn = ("a" in keys) - ("d" in keys)
        self.state_t += dt
        g = self.ground(self.x, self.z)

        # ---- speed, height and state ----
        if self.state == "ground":
            self.speed = 0.0
            self.vy = 0.0
            self.y = g
            if (space and not self.space_down) or w:
                self._set_state("takeoff")
        elif self.state == "takeoff":
            t = self.state_t
            if t < self.CROUCH_TIME:
                self.crouch = _smooth(t / self.CROUCH_TIME)
            else:
                if not self.launched:
                    self.launched = True
                    self.vy = math.sqrt(2.0 * self.gravity * 0.45 * L) + climb
                self.crouch = max(0.0, 1.0 - (t - self.CROUCH_TIME) / 0.1)
                self.vy = _ease(self.vy, climb * 1.2, 2.0, dt)
                self.speed = _ease(self.speed, cruise if w else slow * 1.4,
                                   2.5, dt)
                self.y += self.vy * dt
                if t > self.CROUCH_TIME + self.LAUNCH_TIME:
                    self._set_state("air")
        elif self.state == "air":
            target = fast if w else slow if s else dive_v if dive else cruise
            rate = (p["cruise_speed"] * 0.9 if target > self.speed
                    else p["cruise_speed"] * 1.1) * size
            if dive:
                rate += 0.6 * self.gravity
            self.speed += _clamp(target - self.speed, -rate * dt, rate * dt)
            if space:
                vy_target = climb
            elif dive:
                vy_target = -0.55 * self.speed
            elif s:
                vy_target = -0.45 * climb
            else:
                vy_target = 0.0
            if self.speed < 0.8 * slow and not space:
                vy_target = min(vy_target, -0.5 * climb)
            self.vy = _ease(self.vy, vy_target, 2.5 if not dive else 4.0, dt)
            self.y += self.vy * dt
            height = self.y - g
            if (s and not space and self.vy <= 0.0
                    and height < 1.3 * L and self.speed < 1.6 * slow):
                self._set_state("landing")
            elif height < 0.6 * L:
                # Too fast to land: skim over the ground instead.
                self.y = g + 0.6 * L
                self.vy = max(self.vy, 0.0)
        elif self.state == "landing":
            if space:
                self._set_state("air")
            else:
                self.speed = _ease(self.speed, 0.0, 2.2, dt)
                self.vy = _ease(self.vy, -0.5 * climb, 3.0, dt)
                self.y += self.vy * dt
                if self.y <= g:
                    self.y = g
                    self.squash_v = self.vy * 0.4
                    self._set_state("ground")
                    self._plant_feet()
        self.space_down = space
        airborne = self.state in ("air", "landing") or (
            self.state == "takeoff" and self.launched)

        # ---- heading ----
        max_turn = math.radians(p["turn_rate"])
        self.yaw_rate = _ease(self.yaw_rate, turn * max_turn,
                              3.0 if airborne else 6.0, dt)
        self.heading += self.yaw_rate * dt
        self.x += self.speed * math.sin(self.heading) * dt
        self.z += self.speed * math.cos(self.heading) * dt
        if self.state == "ground" and abs(self.yaw_rate) > 1e-3:
            self._hop_feet()

        # ---- attitude: bank into turns, pitch with climbs and flares ----
        motion = p["body_motion"]
        if airborne:
            bank = math.atan(self.speed * self.yaw_rate / self.gravity)
            bank = _clamp(bank * p["bank"], -math.radians(60.0),
                          math.radians(60.0))
            pitch = 0.75 * math.atan2(self.vy, max(self.speed, 0.5 * slow))
            if self.state == "landing":
                # Flare: body up, wings and tail catching the air.
                pitch = math.radians(30.0) * motion
            elif self.state == "takeoff":
                pitch = min(pitch, math.radians(30.0))
            elif s and self.speed < 1.5 * slow:
                pitch = max(pitch, 0.0) + math.radians(22.0) * motion
        else:
            bank = 0.0
            pitch = (math.radians(22.0) * self.crouch * motion
                     if self.state == "takeoff" else 0.0)
        pitch = _clamp(pitch, -math.radians(55.0), math.radians(50.0))
        self.bank = _ease(self.bank, bank, 4.0, dt)
        self.pitch = _ease(self.pitch, pitch, 3.5, dt)

        # ---- wingbeats ----
        if self.state == "ground":
            beat = 0.0
        elif self.state == "takeoff":
            beat = 1.0 if self.launched else 0.3
        elif self.state == "landing":
            beat = 0.55
        elif dive:
            beat = 0.0
        elif w or space:
            beat = 1.0
        elif s:
            beat = 0.8
        else:
            beat = 0.6
            freq_now = p["flap_rate"] / math.sqrt(max(size, 0.05))
            flap_time = 3.0 / freq_now
            glide_time = 1.6 * p["glide"]
            self.cruise_clock += dt
            if glide_time > 0.05 and self.vy <= 0.5 * climb:
                cycle = flap_time + glide_time
                if _frac(self.cruise_clock / cycle) * cycle > flap_time:
                    beat = 0.0
        self.intensity = _ease(self.intensity, beat,
                               6.0 if beat > self.intensity else 2.5, dt)
        freq = (p["flap_rate"] / math.sqrt(max(size, 0.05))
                * (0.85 + 0.35 * self.intensity))
        if self.state in ("takeoff", "landing"):
            freq *= 1.2
        if airborne or self.state == "takeoff":
            self.phase += freq * dt

        fold = 1.0 if self.state == "ground" else (0.7 if dive and
                                                   self.state == "air"
                                                   else 0.0)
        self.fold = _ease(self.fold, fold,
                          2.5 if fold > self.fold else 6.0, dt)
        flare = self.state == "landing" or (s and self.speed < 1.5 * slow
                                             and airborne)
        self.sweep = _ease(self.sweep, (12.0 if flare else
                                        -18.0 if dive else 0.0), 4.0, dt)
        self.twist = _ease(self.twist, 22.0 if flare else 0.0, 4.0, dt)

        # ---- legs and talons ----
        tuck = 1.0 if (self.state == "air"
                       or (self.state == "takeoff"
                           and self.state_t > self.CROUCH_TIME + 0.2)) else 0.0
        reach = 1.0 if self.state == "landing" else 0.0
        self.tuck = _ease(self.tuck, tuck, 4.0, dt)
        self.reach = _ease(self.reach, reach, 5.0, dt)
        talon = 7.0 * self.tuck - 2.0 * self.reach
        self.talon = _ease(self.talon, talon, 6.0, dt)

        # Landing squash: a damped spring back to standing.
        k = 90.0
        self.squash_v += (-k * self.squash - 2.0 * 0.7 * math.sqrt(k)
                          * self.squash_v) * dt
        self.squash = _clamp(self.squash + self.squash_v * dt, -0.25 * L,
                             0.1 * L)

        # ---- tail ----
        spread = 1.0 if flare else 0.1 if dive else 0.45
        spread = min(1.0, spread + 0.6 * abs(self.bank))
        self.tail_spread = _ease(self.tail_spread, spread, 4.0, dt)
        lift = 25.0 if flare else -8.0 if dive else -math.degrees(self.pitch) * 0.3
        self.tail_lift = _ease(self.tail_lift, lift, 4.0, dt)

        # ---- write the pose ----
        cmds.setAttr(ROOT + ".translateX", self.x)
        cmds.setAttr(ROOT + ".translateY", self.y)
        cmds.setAttr(ROOT + ".translateZ", self.z)
        cmds.setAttr(ROOT + ".rotateY", math.degrees(self.heading))
        self._pose_body(dt)
        root_m = _root_matrix(self.x, self.y, self.z, self.heading)
        self._pose_wings()
        self._pose_legs(root_m)
        self._pose_tail()
        self._pose_head(root_m)
        self.recorder.capture()

    # ---- body ----------------------------------------------------------------

    def _pose_body(self, dt):
        motion = self.params["body_motion"]
        L = self.leg_length
        stroke = math.sin(2.0 * math.pi * (self.phase - 0.1))
        bob = 0.012 * self.span * self.intensity * stroke * motion
        drop = -0.18 * L * self.crouch + self.squash
        self._set(COG, "translateY", bob + drop)
        self._set(COG, "rotateX", -math.degrees(self.pitch))
        self._set(COG, "rotateZ", -math.degrees(self.bank))

    # ---- wings ---------------------------------------------------------------

    def _stroke(self):
        """Flap angle, forward sweep, twist and elbow / wrist flex for the
        current point of the wingbeat, scaled by how hard it's flapping."""
        p = self.params
        amp = self.intensity * p["flap_size"]
        down = 0.55
        ph = _frac(self.phase)
        if ph < down:
            u = ph / down
            wave = math.cos(math.pi * u)              # +1 top .. -1 bottom
            bulge = math.sin(math.pi * u)
            sweep = 10.0 * bulge * amp
            twist = -8.0 * bulge * amp
            flex = 0.0
            spread = 1.0
        else:
            u = (ph - down) / (1.0 - down)
            wave = -math.cos(math.pi * u)             # -1 bottom .. +1 top
            bulge = math.sin(math.pi * u)
            sweep = -14.0 * bulge * amp
            twist = 14.0 * bulge * amp
            flex = 0.4 * bulge * amp
            spread = 1.0 - 0.6 * bulge * amp
        theta = 6.0 + (48.0 if wave > 0.0 else 42.0) * wave * amp
        return theta, sweep, twist, flex, spread

    def _wing_pose(self, wing, theta, sweep, twist, flex):
        """Pivot matrices (root space, rest frame) for the shoulder, elbow and
        wrist: the shoulder flaps (about the body's long axis), sweeps and
        twists; folding swings the upper wing back along the body, the
        forearm forward and the hand back, like a real bird's wing."""
        out = wing["out"]
        fold = self.fold
        bend = max(flex, fold)
        sweep = sweep + self.sweep - 78.0 * fold
        theta = theta - 20.0 * fold
        twist = twist + self.twist
        shoulder = _rot_x(-twist) * _rot_y(-out * sweep) * _rot_z(out * theta)
        m_s = _about(wing["S"], shoulder)
        m_e = _about(wing["E"], _rot_y(-out * 150.0 * bend)) * m_s
        m_w = _about(wing["W"], _rot_y(out * wing["hand_fold"] * bend)) \
            * m_e
        return m_s, m_e, m_w

    def _pose_wings(self):
        theta, sweep, twist, flex, spread = self._stroke()
        if self.state == "ground":
            spread = 0.0
        spread *= 1.0 - self.fold
        if self.state == "air" and "shift" in self.held:
            spread = min(spread, 0.3)
        chest_now = _mm(CHEST if cmds.objExists(CHEST) else COG)
        carry = self.chest_rest.inverse() * chest_now
        for wing in self.wings:
            m_s, m_e, m_w = self._wing_pose(wing, theta, sweep, twist, flex)
            if wing["mode"] == "ik":
                self._set_world(wing["ctrl"], wing["rest_ik"] * m_w * carry)
                if wing["rest_pv"] is not None:
                    self._place_pole(wing, m_s, m_e, carry)
            else:
                for ctrl, rest, m in zip(wing["fk"], wing["rest_fk"],
                                         (m_s, m_e, m_w)):
                    self._set_world(ctrl, rest * m * carry)
            attr, closed, opened = wing["spread"]
            self._set(wing["settings"], attr,
                      closed + (opened - closed) * _clamp(spread, 0.0, 1.0))

    def _place_pole(self, wing, m_s, m_e, carry):
        s = _moved(wing["S"], m_s * carry)
        e = _moved(wing["E"], m_s * carry)
        w = _moved(wing["W"], m_e * carry)
        reach = (_point(wing["rest_pv"]) - wing["E"]).length()
        line = w - s
        if line.length() > 1e-6:
            mid = s + line.normal() * ((e - s) * line.normal())
            out = e - mid
        else:
            out = om2.MVector()
        if out.length() > 0.03 * (e - s).length():
            pos = e + out.normal() * reach
        else:
            pos = _point(wing["rest_pv"] * m_s * carry)
        m = om2.MMatrix()
        m[12], m[13], m[14] = pos.x, pos.y, pos.z
        self._set_world(wing["pv"], m)

    # ---- legs ----------------------------------------------------------------

    def _plant_feet(self):
        """Feet on the ground under the body, where they stand at rest."""
        root_m = _root_matrix(self.x, self.y, self.z, self.heading)
        self.plant_heading = self.heading
        for leg in self.legs:
            m = leg["rest_m"] * root_m
            pos = _point(m)
            pos.y += self.ground(pos.x, pos.z) - self.y
            m[13] = pos.y
            self.feet_world[leg["ctrl"]] = m

    def _hop_feet(self):
        """Turning on the ground: step the feet round in little hops."""
        if abs(self.heading - self.plant_heading) > math.radians(20.0):
            self.squash_v += 0.8 * self.leg_length
            self._plant_feet()

    def _pose_legs(self, root_m):
        L = self.leg_length
        cog_now = _mm(COG)
        carry = self.cog_rest.inverse() * cog_now
        for leg in self.legs:
            rest = leg["rest_m"]
            foot = _point(rest)
            hip = leg["hip"]
            tucked = om2.MVector(foot.x, hip.y - 0.5 * L, hip.z - 0.45 * L)
            reached = om2.MVector(foot.x, hip.y - 0.84 * L, hip.z + 0.3 * L)
            air_pos = foot + (tucked - foot) * self.tuck
            air_pos = air_pos + (reached - air_pos) * self.reach
            angle = 105.0 * self.tuck - 20.0 * self.reach
            air = _rotation(rest) * _rot_x(angle)
            air[12], air[13], air[14] = air_pos.x, air_pos.y, air_pos.z
            air = air * carry
            planted = self.feet_world.get(leg["ctrl"])
            if self.state == "ground" and planted is not None:
                target = planted
            elif self.state == "takeoff" and planted is not None:
                # Push off the ground, then let the legs trail and tuck.
                w = _smooth((self.state_t - self.CROUCH_TIME) / 0.25)
                target = _blend(planted, air, w)
            else:
                target = air
            self._set_world(leg["ctrl"], target)
            settings = leg["prefix"] + "_SETTINGS_CTRL"
            self._set(settings, "talonCurl", self.talon)

    # ---- tail and head --------------------------------------------------------

    def _pose_tail(self):
        self._set(TAIL_SETTINGS, "tailSpread", _clamp(self.tail_spread, 0, 1))
        self._set(TAIL_SETTINGS, "tailLift", _clamp(self.tail_lift, -45, 45))
        self._set(TAIL_BASE, "rotateY", -math.degrees(self.yaw_rate) * 0.25)
        self._set(TAIL_BASE, "rotateZ", -math.degrees(self.bank) * 0.3)

    def _pose_head(self, root_m):
        """Birds keep their heads level: undo most of the body's pitch and
        bank on the head control."""
        if self.head_rest is None or not cmds.objExists(HEAD):
            return
        now = _mm(HEAD)
        level = self.head_rest * root_m
        q_now = om2.MTransformationMatrix(now).rotation(asQuaternion=True)
        q_level = om2.MTransformationMatrix(level).rotation(asQuaternion=True)
        q = om2.MQuaternion.slerp(q_now, q_level, 0.75)
        target = q.asMatrix()
        target[12], target[13], target[14] = now[12], now[13], now[14]
        self._set_world(HEAD, target)

    # ---- writing values --------------------------------------------------------

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


def _rotation(m):
    r = om2.MMatrix(m)
    r[12] = r[13] = r[14] = 0.0
    return r


def _blend(a, b, w):
    """Blend two world matrices (position lerp, rotation slerp)."""
    ta = om2.MTransformationMatrix(a)
    tb = om2.MTransformationMatrix(b)
    q = om2.MQuaternion.slerp(ta.rotation(asQuaternion=True),
                              tb.rotation(asQuaternion=True), w)
    pa, pb = _point(a), _point(b)
    pos = pa + (pb - pa) * w
    m = q.asMatrix()
    m[12], m[13], m[14] = pos.x, pos.y, pos.z
    return m


# =============================================================================
# The panel
# =============================================================================

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

    class FlySession(QtWidgets.QDialog, FlyPass):
        """Small focused panel that captures WASD and flies the bird."""

        def __init__(self, fps=None, parent=None):
            ptr = omui.MQtUtil.mainWindow()
            main = wrapInstance(int(ptr), QtWidgets.QWidget) if ptr else None
            QtWidgets.QDialog.__init__(self, parent or main)
            from vehicle_drive import scene_fps
            self.fps = fps or scene_fps()
            self.params = dict(DEFAULT_PARAMS)
            self.held = set()
            self.recorder = None
            self.flying = False
            self.setObjectName(WINDOW_OBJECT_NAME)
            self.setWindowTitle("Fly Mode")
            self.setWindowFlags(self.windowFlags() | QtCore.Qt.Window
                                | QtCore.Qt.WindowStaysOnTopHint)
            self.setFocusPolicy(QtCore.Qt.StrongFocus)
            self.setMinimumWidth(280)
            self.timer = QtCore.QTimer(self)
            self.timer.setInterval(int(1000.0 / self.fps))
            self.timer.timeout.connect(self._tick)
            self._build_ui(QtCore, QtWidgets)
            self.key_map = key_map

        def _build_ui(self, QtCore, QtWidgets):
            lay = QtWidgets.QVBoxLayout(self)
            lay.setContentsMargins(12, 12, 12, 12)
            lay.setSpacing(8)
            title = QtWidgets.QLabel("FLY MODE")
            title.setStyleSheet("QLabel { font-size: 14pt; font-weight: bold;"
                                " color: #ffd9a0; }")
            lay.addWidget(title)
            help_lbl = QtWidgets.QLabel(
                "W          fly faster\n"
                "S           slow down (hold low down to land)\n"
                "A / D     bank and turn\n"
                "Space    climb, or take off\n"
                "Shift     dive\n"
                "Esc        stop\n\n"
                "Keep THIS panel focused (click it if keys stop responding).")
            help_lbl.setStyleSheet("QLabel { color: #ccc; }")
            lay.addWidget(help_lbl)

            tune = QtWidgets.QGroupBox("Flight")
            grid = QtWidgets.QGridLayout(tune)
            grid.setSpacing(4)
            rows = (("Cruise speed", "cruise_speed", 50.0, 3000.0, 25.0,
                     "cm per second for a 2.5 m wingspan (scales with the "
                     "bird)."),
                    ("Fast speed", "fast_speed", 100.0, 5000.0, 25.0,
                     "Speed while holding W."),
                    ("Slow speed", "slow_speed", 20.0, 1000.0, 10.0,
                     "Speed while holding S."),
                    ("Dive speed", "dive_speed", 100.0, 6000.0, 50.0,
                     "Speed while holding Shift."),
                    ("Climb rate", "climb_rate", 20.0, 2000.0, 10.0,
                     "How fast Space climbs (cm per second)."),
                    ("Turn rate", "turn_rate", 10.0, 360.0, 5.0,
                     "Degrees per second."),
                    ("Bank", "bank", 0.0, 2.0, 0.1,
                     "How far the bird leans into turns."),
                    ("Wingbeats / s", "flap_rate", 0.3, 20.0, 0.1,
                     "Wingbeats per second for a 2.5 m wingspan."),
                    ("Wingbeat size", "flap_size", 0.0, 2.0, 0.05,
                     "How far the wings sweep up and down."),
                    ("Glide", "glide", 0.0, 3.0, 0.1,
                     "Gliding between bursts of wingbeats while cruising "
                     "(0 = flap all the time)."),
                    ("Body motion", "body_motion", 0.0, 2.0, 0.1,
                     "Pitch, bob and the landing flare."))
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
            btn_ground.setToolTip("Select a terrain mesh and click: the bird "
                                  "takes off from and lands on it.\nClick "
                                  "with nothing selected for flat ground.")
            btn_ground.setFocusPolicy(QtCore.Qt.NoFocus)
            btn_ground.clicked.connect(self._on_ground)
            ground_row.addWidget(self.ground_lbl, 1)
            ground_row.addWidget(btn_ground)
            lay.addLayout(ground_row)

            self.status = QtWidgets.QLabel("Ready: press Start, then Space "
                                           "to take off.")
            self.status.setStyleSheet("QLabel { color: #8f8; border-top: 1px "
                                      "solid #444; padding-top: 6px; }")
            lay.addWidget(self.status)
            row = QtWidgets.QHBoxLayout()
            self.btn_start = QtWidgets.QPushButton("Start Flying")
            self.btn_start.clicked.connect(self.start_flying)
            self.btn_stop = QtWidgets.QPushButton("Stop (Esc)")
            self.btn_stop.clicked.connect(self.stop_flying)
            self.btn_stop.setEnabled(False)
            for b in (self.btn_start, self.btn_stop):
                b.setFocusPolicy(QtCore.Qt.NoFocus)
                row.addWidget(b)
            lay.addLayout(row)
            self.btn_clear = QtWidgets.QPushButton("Clear Fly Animation")
            self.btn_clear.setFocusPolicy(QtCore.Qt.NoFocus)
            self.btn_clear.clicked.connect(self._on_clear)
            lay.addWidget(self.btn_clear)

        def _refresh_ground(self):
            mesh = cw.assigned_ground()
            self.ground_lbl.setText("Ground: %s" % (mesh or "flat"))

        def _on_ground(self):
            sel = [s for s in (cmds.ls(sl=True, type="transform") or [])
                   if cmds.listRelatives(s, s=True, type="mesh", ni=True)]
            cw.assign_ground(sel[0] if sel else None)
            self._refresh_ground()

        def start_flying(self):
            if not can_fly():
                cmds.warning("No bird with wings found. Build a bird rig "
                             "first.")
                return
            cmds.undoInfo(openChunk=True)
            try:
                self._begin()
            except Exception:
                cmds.undoInfo(closeChunk=True)
                raise
            self.held.clear()
            self.flying = True
            self.btn_start.setEnabled(False)
            self.btn_stop.setEnabled(True)
            self.status.setText("FLYING (recording): WASD, Space, Shift. "
                                "Esc to stop.")
            self.timer.start()
            self.setFocus()
            self.activateWindow()
            self.raise_()

        def stop_flying(self):
            if not self.flying:
                return
            self.flying = False
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
            if self.flying:
                self.stop_flying()
            cmds.undoInfo(openChunk=True)
            try:
                n = clear_fly_animation()
            finally:
                cmds.undoInfo(closeChunk=True)
            self.status.setText("Cleared %d fly channels." % n)

        def _tick(self):
            if not self.flying:
                return
            self._tick_once()
            height = self.y - self.ground(self.x, self.z)
            self.status.setText("REC frame %d   %s   speed %.0f   height %.0f"
                                % (self.recorder.end, self.state,
                                   self.speed, height))

        def keyPressEvent(self, event):
            if event.isAutoRepeat():
                return
            if event.key() == QtCore.Qt.Key_Escape:
                self.stop_flying()
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
            self.held.clear()          # never keep flying on a lost key-up
            QtWidgets.QDialog.focusOutEvent(self, event)

        def closeEvent(self, event):
            self.stop_flying()
            QtWidgets.QDialog.closeEvent(self, event)

    return FlySession


_fly_window = None


def show():
    """Open the Fly Mode panel."""
    global _fly_window
    QtCore, QtWidgets, _ = _qt()
    for w in QtWidgets.QApplication.topLevelWidgets():
        if w.objectName() == WINDOW_OBJECT_NAME:
            try:
                w.close()
                w.deleteLater()
            except Exception:
                pass
    if not can_fly():
        cmds.warning("No wings in the scene. Build a bird, or a biped with "
                     "the Dragon creature preset, first.")
    _fly_window = _make_session_class()()
    _fly_window.show()
    _fly_window.raise_()
    _fly_window.setFocus()
    return _fly_window
