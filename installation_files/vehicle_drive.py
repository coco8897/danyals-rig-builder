"""
===============================================================================
 VEHICLE DRIVE — live WASD driving for the vehicle rig
===============================================================================

 Drive the car around the viewport with the keyboard, like a game. Every
 frame is keyframed, so when you stop you have the animation — wheels
 spinning, front wheels steering, and (if a ground mesh is assigned)
 tires deforming against the surface, all baked in.

   W / S   — accelerate / brake + reverse
   A / D   — steer left / right
   Esc     — stop driving

 Usage:
     import vehicle_drive
     from importlib import reload; reload(vehicle_drive)
     vehicle_drive.show()          # opens the Drive panel; click it, then WASD

 How it works:
   * C_global_CTRL carries the car's WORLD position (translateX/Z) + heading
     (rotateY).
   * C_chassis_CTRL.odometer accumulates distance driven → drives wheel spin
     (so wheels roll by how far you've travelled, even around curves).
   * C_steering_CTRL.rotateZ is set from the steer angle → front wheels turn.
   * Each tick sets a keyframe on those channels and advances the timeline.

 NOTE: Maya only sends keys to the focused window, so the little Drive panel
 must stay focused while you drive (click it once if WASD stops responding).
===============================================================================
"""

import contextlib
import math
import maya.cmds as cmds
import maya.OpenMayaUI as omui
from PySide2 import QtCore, QtWidgets
from shiboken2 import wrapInstance

import vehicle_rig_builder
import raycast_ground
from importlib import reload as _reload
_reload(raycast_ground)


@contextlib.contextmanager
def _undo_chunk():
    cmds.undoInfo(openChunk=True)
    try:
        yield
    finally:
        cmds.undoInfo(closeChunk=True)


WINDOW_OBJECT_NAME = "DanyalVehicleDriveWindow"

DRIVE_ROOT   = "C_global_CTRL"
ODO_ATTR     = "C_chassis_CTRL.odometer"
STEER_CTRL   = "C_steering_CTRL"
BODY_AUTO    = "C_body_AUTO"   # instant terrain tilt (node-driven)
BODY_OSC     = "C_body_OSC"    # drive-loop spring-damper LAG offset
# The three body channels we oscillate: (auto attr, osc attr).
_BODY_CHANS = ("rotateX", "rotateZ", "translateY")


def has_body_osc():
    return cmds.objExists(BODY_OSC) and cmds.objExists(BODY_AUTO)


# Default handling parameters (Maya units, SCALE=10 → 1 unit ≈ 1 cm).
DEFAULT_PARAMS = {
    "accel":       260.0,   # units/sec^2 throttle
    "brake":       420.0,   # units/sec^2 brake / reverse push
    "friction":    160.0,   # units/sec^2 coast-down decay
    "max_speed":   900.0,   # units/sec forward cap
    "max_reverse": 320.0,   # units/sec reverse cap
    "max_steer":    35.0,   # degrees of steering lock
    "steer_speed":  90.0,   # degrees/sec the steer angle eases toward target
    "wheelbase":   260.0,   # front-to-back axle distance (bicycle model)
    "handbrake_decel": 750.0,  # units/sec^2 — strong slowdown on handbrake
    "drift_mult":   2.4,    # handbrake turn multiplier (rear breaks loose)
    # Body oscillation (spring-damper on the body shell). The body lags
    # its instant terrain tilt and overshoots/settles like real mass.
    "body_stiffness": 55.0,  # spring constant — higher = snappier, less lag
    "body_damping":   7.0,   # damping — lower = more bouncy overshoot
    "body_osc":       1.0,   # master 0..1 amount of the oscillation
}


def step_oscillator(osc, target, dt, stiffness, damping):
    """Advance one critically-tunable spring-damper toward `target`.

    osc = dict(value, vel). Returns the new osc dict.
        accel = stiffness*(target - value) - damping*vel
    Sub-steps internally so it stays stable at low fps / high stiffness.
    """
    value = osc["value"]
    vel = osc["vel"]
    # Sub-step for stability (semi-implicit Euler).
    steps = max(1, int(dt * 120) + 1)
    h = dt / steps
    for _ in range(steps):
        accel = stiffness * (target - value) - damping * vel
        vel += accel * h
        value += vel * h
    return {"value": value, "vel": vel}


def _clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


def step_drive(state, keys, dt, params=None):
    """Pure driving integrator — no Maya calls, fully testable.

    state: dict(speed, heading_rad, x, z, odometer, steer_deg)
        x, z are the REAR-AXLE position (the car pivots about the rear
        axle, like a real car — the rear wheels roll forward and trace
        the path, the front wheels steer, so the body rotates around the
        rear and the back never slides sideways).
    keys:  set of held keys among {'w','a','s','d'}
    dt:    seconds since last tick
    Returns the new state dict.
    """
    p = params or DEFAULT_PARAMS
    speed = state["speed"]
    heading = state["heading_rad"]
    x, z = state["x"], state["z"]
    odo = state["odometer"]
    steer = state["steer_deg"]

    # ---- throttle / brake / handbrake ----
    handbrake = "space" in keys
    if handbrake:
        # Handbrake overrides the throttle — strong decel toward 0.
        d = p["handbrake_decel"] * dt
        if speed > 0:
            speed = max(0.0, speed - d)
        elif speed < 0:
            speed = min(0.0, speed + d)
    elif "w" in keys:
        speed += p["accel"] * dt
    elif "s" in keys:
        speed -= p["brake"] * dt
    else:
        decay = p["friction"] * dt
        if speed > 0:
            speed = max(0.0, speed - decay)
        elif speed < 0:
            speed = min(0.0, speed + decay)
    speed = _clamp(speed, -p["max_reverse"], p["max_speed"])

    # ---- steering (eases toward the held direction, self-centers) ----
    # Sign convention: D = steer RIGHT = positive steer; A = LEFT = negative.
    # Positive steer feeds C_steering_CTRL.rotateZ, which (through the
    # rig's STEERING_RATIO = -1) points the front wheels to the RIGHT —
    # matching the direction the car curves below. So the wheels visibly
    # point INTO the turn.
    step = p["steer_speed"] * dt
    if "d" in keys:
        steer = min(p["max_steer"], steer + step)
    elif "a" in keys:
        steer = max(-p["max_steer"], steer - step)
    else:
        if steer > 0:
            steer = max(0.0, steer - step)
        elif steer < 0:
            steer = min(0.0, steer + step)

    # ---- bicycle model, pivoting about the REAR AXLE ----
    # The rear axle rolls forward along the heading (it never slides
    # sideways), then the heading turns by speed*tan(steer)/wheelbase.
    # heading rate is NEGATIVE for positive steer so D (right) curves the
    # car toward -X (right, driver facing +Z) — matching the wheels.
    dist = speed * dt
    x += math.sin(heading) * dist
    z += math.cos(heading) * dist
    odo += dist
    if abs(speed) > 1e-4:
        rate = speed * math.tan(math.radians(steer)) / p["wheelbase"]
        # Handbrake breaks the rear loose → whips the car around faster
        # (a handbrake / drift turn) instead of the clean rear-axle arc.
        if handbrake:
            rate *= p["drift_mult"]
        heading -= rate * dt

    return {"speed": speed, "heading_rad": heading, "x": x, "z": z,
            "odometer": odo, "steer_deg": steer}


def measure_geometry():
    """Read the car's wheelbase + rear-axle offset from the actual rig
    hubs, in the car's own forward direction (works at any pose).

    Returns (wheelbase, rear_to_center) where rear_to_center is the
    forward distance from the rear axle to C_global_CTRL's pivot (the
    point the manipulator + body sit at). Falls back to the defaults if
    the hubs aren't found.
    """
    wb_default = DEFAULT_PARAMS["wheelbase"]
    rtc_default = wb_default / 2.0
    hubs = ("LF_hub_BIND_JNT", "RF_hub_BIND_JNT",
            "LB_hub_BIND_JNT", "RB_hub_BIND_JNT")
    if not all(cmds.objExists(h) for h in hubs):
        return wb_default, rtc_default
    h = math.radians(cmds.getAttr(f"{DRIVE_ROOT}.rotateY"))
    fwd = (math.sin(h), math.cos(h))
    cx, _, cz = cmds.xform(DRIVE_ROOT, q=True, ws=True, t=True)

    def fwd_dist(node):
        x, _, z = cmds.xform(node, q=True, ws=True, t=True)
        return (x - cx) * fwd[0] + (z - cz) * fwd[1]

    front = 0.5 * (fwd_dist("LF_hub_BIND_JNT") + fwd_dist("RF_hub_BIND_JNT"))
    back  = 0.5 * (fwd_dist("LB_hub_BIND_JNT") + fwd_dist("RB_hub_BIND_JNT"))
    wheelbase = abs(front - back)
    if wheelbase < 1.0:
        return wb_default, rtc_default
    rear_to_center = -back   # forward distance from rear axle to centre
    return wheelbase, rear_to_center


def read_state_from_scene(rear_to_center=None):
    """Seed the drive state from the car's current pose. The tracked
    x,z is the REAR AXLE, derived from C_global's centre."""
    if rear_to_center is None:
        rear_to_center = DEFAULT_PARAMS["wheelbase"] / 2.0
    cx = cmds.getAttr(f"{DRIVE_ROOT}.translateX")
    cz = cmds.getAttr(f"{DRIVE_ROOT}.translateZ")
    heading = math.radians(cmds.getAttr(f"{DRIVE_ROOT}.rotateY"))
    # rear axle = centre - forward * rear_to_center
    rx = cx - math.sin(heading) * rear_to_center
    rz = cz - math.cos(heading) * rear_to_center
    odo = cmds.getAttr(ODO_ATTR)
    steer = (cmds.getAttr(f"{STEER_CTRL}.rotateZ")
             if cmds.objExists(STEER_CTRL) else 0.0)
    return {"speed": 0.0, "heading_rad": heading, "x": rx, "z": rz,
            "odometer": odo, "steer_deg": steer}


def apply_state(state, key_frame=True, rear_to_center=None):
    """Push the drive state onto the rig and (optionally) keyframe it.
    The state's x,z is the rear axle; C_global's centre is computed as
    rear_axle + forward * rear_to_center so the body sits correctly while
    the motion pivots about the rear axle (no slide)."""
    if rear_to_center is None:
        rear_to_center = DEFAULT_PARAMS["wheelbase"] / 2.0
    heading = state["heading_rad"]
    cx = state["x"] + math.sin(heading) * rear_to_center
    cz = state["z"] + math.cos(heading) * rear_to_center
    cmds.setAttr(f"{DRIVE_ROOT}.translateX", cx)
    cmds.setAttr(f"{DRIVE_ROOT}.translateZ", cz)
    cmds.setAttr(f"{DRIVE_ROOT}.rotateY", math.degrees(heading))
    cmds.setAttr(ODO_ATTR, state["odometer"])
    if cmds.objExists(STEER_CTRL):
        cmds.setAttr(f"{STEER_CTRL}.rotateZ", state["steer_deg"])
    if key_frame:
        cmds.setKeyframe([f"{DRIVE_ROOT}.translateX",
                          f"{DRIVE_ROOT}.translateZ",
                          f"{DRIVE_ROOT}.rotateY",
                          ODO_ATTR])
        if cmds.objExists(STEER_CTRL):
            cmds.setKeyframe(f"{STEER_CTRL}.rotateZ")


def can_drive():
    """True if a drivable vehicle rig is in the scene."""
    return (cmds.objExists(DRIVE_ROOT)
            and cmds.attributeQuery("odometer",
                                     node="C_chassis_CTRL", exists=True))


# The exact channels a drive pass keyframes. clear_drive_animation()
# wipes keys on these (and only these) so you can redo the drive cleanly.
def _drive_channels():
    chans = [f"{DRIVE_ROOT}.translateX",
             f"{DRIVE_ROOT}.translateZ",
             f"{DRIVE_ROOT}.rotateY",
             ODO_ATTR]
    if cmds.objExists(STEER_CTRL):
        chans.append(f"{STEER_CTRL}.rotateZ")
    # Body oscillation offset channels (keyed by the drive loop).
    if cmds.objExists(BODY_OSC):
        for c in _BODY_CHANS:
            chans.append(f"{BODY_OSC}.{c}")
    # Ground-raycast footprint sources (keyed by the drive loop when a
    # terrain mesh is assigned).
    for p in raycast_ground.WHEEL_PREFIXES:
        for t in raycast_ground.FOOT_TAGS:
            src = f"{p}_foot{t}Src_ADL"
            if cmds.objExists(src):
                chans.append(f"{src}.input1")
    return chans


def _footprint_source_channels():
    """The ground-sampling input1 channels the drive loop keyframes when a
    terrain mesh is assigned. These are INFRASTRUCTURE, not car motion."""
    out = set()
    for p in raycast_ground.WHEEL_PREFIXES:
        for t in raycast_ground.FOOT_TAGS:
            src = f"{p}_foot{t}Src_ADL"
            if cmds.objExists(src):
                out.add(f"{src}.input1")
    return out


def _restore_ground_sources():
    """Re-establish the live ground hookup on the footprint sources after a
    drive clear. The drive loop disconnects the closestPointOnMesh feeds at
    start and keyframes input1 directly; if we just cut those keys the
    sources go dead and terrain stops being detected until a scene reload.

    If a terrain mesh is still assigned, re-wire the per-wheel sampling;
    otherwise reconnect the flat C_ground_LOC so the suspension still reads
    a real ground value (Y=0) instead of a frozen number.
    """
    mesh = vehicle_rig_builder.assigned_ground_mesh()
    if mesh and cmds.objExists(mesh):
        vehicle_rig_builder.assign_ground_mesh(mesh)
    else:
        # No terrain assigned — make the footprint sources read the flat
        # ground locator again (clear_bake reconnects it).
        raycast_ground.clear_bake(reconnect_flat=True)


def clear_drive_animation(reset_pose=True):
    """Delete every keyframe a drive pass laid down, so you can re-drive
    from scratch. Optionally zero the MOTION channels back to the start
    pose.

    Only touches the drive channels (global translateX/Z + rotateY,
    odometer, steering rotateZ, body-OSC offsets, ground footprint
    samples) — your manual posing on other ctrls is left alone.

    Importantly, the ground footprint-sample channels are NOT zeroed: their
    keys are cut and then their live terrain hookup is restored, so the
    ground stays detectable after a clear (no scene reload needed).

    Returns the number of channels cleared.
    """
    if not can_drive():
        cmds.warning("[drive] No drivable vehicle rig in scene.")
        return 0
    ground_chans = _footprint_source_channels()
    n = 0
    for ch in _drive_channels():
        node, attr = ch.split(".", 1)
        if not cmds.objExists(node):
            continue
        is_ground = ch in ground_chans
        # Remove all animation on this channel.
        if cmds.keyframe(ch, q=True, keyframeCount=True) > 0:
            if is_ground:
                # cutKey(clear=True) would DELETE the footprint
                # addDoubleLinear node and kill terrain following; delete
                # the animCurve directly so the node survives.
                raycast_ground.clear_channel_keys(ch)
            else:
                cmds.cutKey(ch, clear=True)
            n += 1
        # Reset MOTION channels to the start pose — but never the ground
        # sources (those get their live connection restored below, and a
        # static 0 would silently disable terrain following).
        if reset_pose and not is_ground:
            try:
                cmds.setAttr(ch, 0)
            except Exception:
                pass
    # Reconnect terrain sampling so clearing a drive never loses the ground.
    if ground_chans:
        _restore_ground_sources()
    print(f"[drive] Cleared drive animation on {n} channel(s)"
          + (" and reset the car to start pose." if reset_pose else "."))
    return n


# =============================================================================
# Interactive Qt drive session
# =============================================================================

def _maya_main_window():
    ptr = omui.MQtUtil.mainWindow()
    return wrapInstance(int(ptr), QtWidgets.QWidget)


_KEY_MAP = {
    QtCore.Qt.Key_W: "w", QtCore.Qt.Key_A: "a",
    QtCore.Qt.Key_S: "s", QtCore.Qt.Key_D: "d",
    QtCore.Qt.Key_Up: "w", QtCore.Qt.Key_Left: "a",
    QtCore.Qt.Key_Down: "s", QtCore.Qt.Key_Right: "d",
    QtCore.Qt.Key_Space: "space",   # handbrake
}


class DriveSession(QtWidgets.QDialog):
    """Small focused panel that captures WASD and drives the car."""

    def __init__(self, fps=30, params=None, parent=None):
        super(DriveSession, self).__init__(parent or _maya_main_window())
        self.setObjectName(WINDOW_OBJECT_NAME)
        self.setWindowTitle("Drive Mode")
        self.setWindowFlags(self.windowFlags() | QtCore.Qt.Window
                            | QtCore.Qt.WindowStaysOnTopHint)
        self.setFocusPolicy(QtCore.Qt.StrongFocus)
        self.setMinimumWidth(260)

        self.fps = fps
        self.params = dict(params or DEFAULT_PARAMS)
        self.rear_to_center = self.params["wheelbase"] / 2.0
        self.held = set()
        self.state = None
        self.driving = False

        self.timer = QtCore.QTimer(self)
        self.timer.setInterval(int(1000.0 / fps))
        self.timer.timeout.connect(self._tick)

        self._build_ui()

    # -----------------------------------------------------------------------

    def _build_ui(self):
        lay = QtWidgets.QVBoxLayout(self)
        lay.setContentsMargins(12, 12, 12, 12)
        lay.setSpacing(8)

        title = QtWidgets.QLabel("🚗  DRIVE MODE")
        title.setStyleSheet("QLabel { font-size: 14pt; font-weight: bold; "
                            "color: #cfe0ff; }")
        lay.addWidget(title)

        help_lbl = QtWidgets.QLabel(
            "W / S   — accelerate / brake + reverse\n"
            "A / D   — steer left / right\n"
            "Space — handbrake (drift / quick stop)\n"
            "Esc     — stop driving\n\n"
            "Keep THIS panel focused (click it if keys stop responding).")
        help_lbl.setStyleSheet("QLabel { color: #ccc; }")
        lay.addWidget(help_lbl)

        # ---- Tuning (adjust the feel live; this is where 'drift' lives) ----
        tune = QtWidgets.QGroupBox("Handling")
        grid = QtWidgets.QGridLayout(tune)
        grid.setSpacing(4)
        self._tune_spins = {}

        def add_spin(row, label, key, lo, hi, step, tip):
            grid.addWidget(QtWidgets.QLabel(label), row, 0)
            sp = QtWidgets.QDoubleSpinBox()
            sp.setRange(lo, hi)
            sp.setSingleStep(step)
            sp.setValue(self.params[key])
            sp.setToolTip(tip)
            sp.setFocusPolicy(QtCore.Qt.ClickFocus)
            sp.valueChanged.connect(
                lambda v, k=key: self.params.__setitem__(k, v))
            grid.addWidget(sp, row, 1)
            self._tune_spins[key] = sp

        add_spin(0, "Drift", "drift_mult", 1.0, 6.0, 0.1,
                 "Handbrake turn multiplier — higher = wilder drifts.")
        add_spin(1, "Top speed", "max_speed", 100.0, 4000.0, 50.0,
                 "Maximum forward speed (units/sec).")
        add_spin(2, "Accel", "accel", 50.0, 1500.0, 20.0,
                 "How hard the throttle pulls (units/sec^2).")
        add_spin(3, "Steer lock", "max_steer", 10.0, 60.0, 1.0,
                 "Maximum steering angle in degrees.")
        add_spin(4, "Body bounce", "body_osc", 0.0, 1.0, 0.05,
                 "How much the body lags + overshoots the terrain tilt. "
                 "0 = instant (no bounce), 1 = full spring-damper.")
        add_spin(5, "Body settle", "body_damping", 1.0, 20.0, 0.5,
                 "Damping. LOWER = bouncier / more overshoot, "
                 "HIGHER = settles faster with less wobble.")
        add_spin(6, "Body stiffness", "body_stiffness", 10.0, 150.0, 5.0,
                 "Spring stiffness. Higher = snappier, follows the "
                 "terrain tilt more tightly with less lag.")
        lay.addWidget(tune)

        self.status = QtWidgets.QLabel("Ready — press Start, then WASD.")
        self.status.setStyleSheet("QLabel { color: #8f8; "
                                  "border-top: 1px solid #444; padding-top: 6px; }")
        lay.addWidget(self.status)

        row = QtWidgets.QHBoxLayout()
        self.btn_start = QtWidgets.QPushButton("Start Driving")
        self.btn_start.setStyleSheet("QPushButton { font-weight: bold; "
                                     "padding: 6px; }")
        self.btn_start.clicked.connect(self.start_driving)
        self.btn_stop = QtWidgets.QPushButton("Stop (Esc)")
        self.btn_stop.clicked.connect(self.stop_driving)
        self.btn_stop.setEnabled(False)
        # No keyboard focus on the buttons — so Space (handbrake) goes to
        # the dialog's key handler, not a button "click".
        self.btn_start.setFocusPolicy(QtCore.Qt.NoFocus)
        self.btn_stop.setFocusPolicy(QtCore.Qt.NoFocus)
        row.addWidget(self.btn_start)
        row.addWidget(self.btn_stop)
        lay.addLayout(row)

        # Clear the baked drive animation so you can take another pass.
        self.btn_clear = QtWidgets.QPushButton("Clear Drive Animation")
        self.btn_clear.setToolTip(
            "Delete the keyframes from the last drive pass (car position,\n"
            "heading, odometer, steering) and reset the car to its start\n"
            "pose, so you can re-drive from scratch. Other manual posing\n"
            "is left untouched.")
        self.btn_clear.setFocusPolicy(QtCore.Qt.NoFocus)
        self.btn_clear.clicked.connect(self._on_clear)
        lay.addWidget(self.btn_clear)

    # -----------------------------------------------------------------------

    def start_driving(self):
        if not can_drive():
            cmds.warning("No drivable vehicle rig found. Build a vehicle "
                         "rig first.")
            return
        # Measure this car's actual wheelbase + rear-axle offset so the
        # turn radius and the no-slide pivot are correct for any model.
        wb, rtc = measure_geometry()
        self.params["wheelbase"] = wb
        self.rear_to_center = rtc
        self.state = read_state_from_scene(self.rear_to_center)
        # Ground raycast — if a terrain mesh is assigned, sample it with a
        # true DOWNWARD ray each frame (avoids the closestPointOnMesh
        # "snap onto the obstacle flank" premature-lift bug). Grab the
        # MFnMesh once at the start of the pass.
        self.ground_fn = None
        self.ground_src_plugs = []
        gmesh = vehicle_rig_builder.assigned_ground_mesh()
        if gmesh and raycast_ground.has_footprints():
            self.ground_fn = raycast_ground._mesh_fn(gmesh)
            # Disconnect the CPOM nodes so the raycast can set the values.
            raycast_ground.prepare_for_raycast()
            for p in raycast_ground.WHEEL_PREFIXES:
                for t in raycast_ground.FOOT_TAGS:
                    src = f"{p}_foot{t}Src_ADL"
                    if cmds.objExists(src):
                        self.ground_src_plugs.append(f"{src}.input1")
        # Body oscillators — one spring-damper per body channel, seeded
        # at the current instant target so they don't jump on the first
        # frame.
        self.body_osc = {}
        if has_body_osc():
            for chan in _BODY_CHANS:
                t = cmds.getAttr(f"{BODY_AUTO}.{chan}")
                self.body_osc[chan] = {"value": t, "vel": 0.0}
        self.held.clear()
        self.driving = True
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self.status.setText("DRIVING — WASD. Esc to stop.")
        # Open an undo chunk so the whole drive is one undo step.
        cmds.undoInfo(openChunk=True)
        self._start_frame = int(cmds.currentTime(q=True))
        self.timer.start()
        self.setFocus()
        self.activateWindow()
        self.raise_()

    def stop_driving(self):
        if not self.driving:
            return
        self.driving = False
        self.timer.stop()
        self.held.clear()
        try:
            cmds.undoInfo(closeChunk=True)
        except Exception:
            pass
        end = int(cmds.currentTime(q=True))
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self.status.setText(f"Stopped. Keyed frames "
                            f"{self._start_frame}–{end}. Press play.")

    def _on_clear(self):
        if self.driving:
            self.stop_driving()
        with _undo_chunk():
            n = clear_drive_animation(reset_pose=True)
        # Rewind to the start so the next pass begins clean.
        try:
            cmds.currentTime(cmds.playbackOptions(q=True, min=True),
                             edit=True)
        except Exception:
            pass
        self.status.setText(f"Cleared drive animation ({n} channels). "
                            f"Ready for another pass.")

    # -----------------------------------------------------------------------

    def _tick(self):
        if not self.driving or self.state is None:
            return
        dt = 1.0 / self.fps
        self.state = step_drive(self.state, self.held, dt, self.params)
        apply_state(self.state, key_frame=True,
                    rear_to_center=self.rear_to_center)
        # Ground raycast — sample the terrain straight DOWN under each
        # footprint (now that the car has moved this frame) and key the
        # suspension source. True downward ray, so a wheel only lifts
        # once it's actually over an obstacle — no premature snap.
        self._tick_ground_raycast()
        # Body oscillation — must run AFTER the suspension updated, so the
        # node network has C_body_AUTO at the new instant tilt to lag.
        self._tick_body_osc(dt)
        # Advance the timeline so each tick lays down the next frame.
        cmds.currentTime(cmds.currentTime(q=True) + 1, edit=True)
        spd = self.state["speed"]
        self.status.setText(f"DRIVING — speed {spd:7.1f}   "
                            f"steer {self.state['steer_deg']:5.1f}°")

    def _tick_ground_raycast(self):
        """Raycast each footprint straight down onto the assigned terrain
        and keyframe the suspension ground source. No-op if no mesh."""
        if not self.ground_fn:
            return
        raycast_ground.sample_footprints(self.ground_fn)
        if self.ground_src_plugs:
            cmds.setKeyframe(self.ground_src_plugs)

    def _tick_body_osc(self, dt):
        """Advance the body spring-dampers toward the live instant tilt,
        write the LAG offset onto C_body_OSC, and keyframe it."""
        if not self.body_osc or not has_body_osc():
            return
        amount = self.params.get("body_osc", 1.0)
        stiff = self.params.get("body_stiffness", 55.0)
        damp = self.params.get("body_damping", 7.0)
        keyed = []
        for chan in _BODY_CHANS:
            target = cmds.getAttr(f"{BODY_AUTO}.{chan}")
            osc = step_oscillator(self.body_osc[chan], target, dt,
                                  stiff, damp)
            self.body_osc[chan] = osc
            # OSC offset = (damped - target) * amount, so the final shown
            # tilt = target + offset = lerp(target, damped, amount).
            offset = (osc["value"] - target) * amount
            plug = f"{BODY_OSC}.{chan}"
            cmds.setAttr(plug, offset)
            keyed.append(plug)
        cmds.setKeyframe(keyed)

    # ---- key capture ----

    def keyPressEvent(self, event):
        if event.isAutoRepeat():
            return
        if event.key() == QtCore.Qt.Key_Escape:
            self.stop_driving()
            return
        k = _KEY_MAP.get(event.key())
        if k:
            self.held.add(k)
            event.accept()
            return
        super(DriveSession, self).keyPressEvent(event)

    def keyReleaseEvent(self, event):
        if event.isAutoRepeat():
            return
        k = _KEY_MAP.get(event.key())
        if k:
            self.held.discard(k)
            event.accept()
            return
        super(DriveSession, self).keyReleaseEvent(event)

    def closeEvent(self, event):
        self.stop_driving()
        super(DriveSession, self).closeEvent(event)


_drive_window = None


def show():
    """Open the Drive Mode panel."""
    global _drive_window
    for w in QtWidgets.QApplication.topLevelWidgets():
        if w.objectName() == WINDOW_OBJECT_NAME:
            try:
                w.close()
                w.deleteLater()
            except Exception:
                pass
    if not can_drive():
        cmds.warning("No drivable vehicle rig in scene. Build a vehicle "
                     "rig first (it needs C_global_CTRL + odometer).")
    _drive_window = DriveSession()
    _drive_window.show()
    _drive_window.raise_()
    _drive_window.setFocus()
    return _drive_window
