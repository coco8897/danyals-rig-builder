"""
===============================================================================
 FACE CAPTURE - drive the face rig live from a webcam or a phone camera
===============================================================================

 The camera side is face_tracker.py (MediaPipe, runs in a normal Python
 outside Maya). It sends the 52 face shapes and the head rotation to this
 panel over your own computer (UDP 127.0.0.1). The panel turns them into the
 face rig's shape dials (face_shapes) and the neck / head controls, live, and
 records takes as keys.

     import face_capture
     face_capture.show()                      # the panel

 Or without the panel:
     face_capture.import_csv(r"D:/takes/take01.csv")   # Live Link Face (iPhone)
                                                       # or face_tracker CSV

 Pipeline for each frame:
   raw shape -> minus your calibrated neutral face -> mirror (optional)
   -> gain for its group -> clamp 0..1 -> smoothing (One Euro filter)
 Head: rotation relative to the calibrated neutral, split 35 % neck / 65 %
 head, added on top of the controls' pose when you connected.
===============================================================================
"""

import bisect
import csv
import glob
import json
import math
import os
import shutil
import socket
import subprocess
import sys
import time

import maya.cmds as cmds
import maya.api.OpenMaya as om2
import maya.api.OpenMayaAnim as oma2

import face_shapes
import face_tracker

SHAPES = face_shapes.SHAPES
PORT = face_tracker.DEFAULT_PORT
WINDOW_OBJECT_NAME = "DanyalFaceCaptureWindow"
PYTHON_OPTVAR = "DanyalFaceCapturePython"
HEAD_CTRLS = (("C_neck_CTRL", 0.35), ("C_head_CTRL", 0.65))

GROUPS = ("blink", "eyes", "brows", "mouth", "jaw", "cheeks", "tongue")
DEFAULT_GAINS = {"blink": 1.3, "eyes": 1.2, "brows": 1.3, "mouth": 1.2,
                 "jaw": 1.1, "cheeks": 1.0, "tongue": 1.0}

# The parts you can switch on and off in the panel -> the groups they cover.
REGIONS = (("Eyes", "eyes", ("blink", "eyes")),
           ("Brows", "brows", ("brows",)),
           ("Mouth", "mouth", ("mouth", "jaw", "tongue")),
           ("Cheeks", "cheeks", ("cheeks",)))


def region_shapes(groups):
    """The shape names covered by these groups."""
    return {s for s in SHAPES if group_of(s) in groups}


def group_of(shape):
    if shape.startswith("eyeBlink"):
        return "blink"
    for prefix, group in (("eye", "eyes"), ("brow", "brows"), ("jaw", "jaw"),
                          ("mouth", "mouth"), ("cheek", "cheeks"),
                          ("nose", "cheeks")):
        if shape.startswith(prefix):
            return group
    return "tongue"


def _clamp01(v):
    return 0.0 if v < 0.0 else 1.0 if v > 1.0 else v


def _smooth01(v):
    v = _clamp01(v)
    return v * v * (3.0 - 2.0 * v)


# =============================================================================
# Maths
# =============================================================================

def euler_matrix(e):
    """3x3 rotation (column vectors, R = Rz Ry Rx) from degrees x / y / z."""
    x, y, z = (math.radians(a) for a in e)
    cx, sx, cy, sy, cz, sz = (math.cos(x), math.sin(x), math.cos(y),
                              math.sin(y), math.cos(z), math.sin(z))
    return [[cz * cy, cz * sy * sx - sz * cx, cz * sy * cx + sz * sx],
            [sz * cy, sz * sy * sx + cz * cx, sz * sy * cx - cz * sx],
            [-sy, cy * sx, cy * cx]]


def _mul(a, b):
    return [[sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)]
            for i in range(3)]


def _transpose(a):
    return [[a[j][i] for j in range(3)] for i in range(3)]


class OneEuro(object):
    """One Euro filter: smooth when still, quick when moving."""

    def __init__(self, min_cutoff=1.0, beta=0.3, d_cutoff=1.0):
        self.min_cutoff, self.beta, self.d_cutoff = min_cutoff, beta, d_cutoff
        self.x = self.dx = self.t = None

    @staticmethod
    def _alpha(dt, cutoff):
        tau = 1.0 / (2.0 * math.pi * cutoff)
        return 1.0 / (1.0 + tau / dt)

    def __call__(self, x, t):
        if self.t is None:
            self.x, self.dx, self.t = x, 0.0, t
            return x
        dt = max(1e-4, t - self.t)
        self.t = t
        dx = (x - self.x) / dt
        a_d = self._alpha(dt, self.d_cutoff)
        self.dx = a_d * dx + (1.0 - a_d) * self.dx
        a = self._alpha(dt, self.min_cutoff + self.beta * abs(self.dx))
        self.x = a * x + (1.0 - a) * self.x
        return self.x


class Processor(object):
    """Raw tracker values -> dial values (and head rotation)."""

    def __init__(self):
        self.neutral = {}
        self.head_neutral = None
        self.gains = dict(DEFAULT_GAINS)
        self.smoothing = 0.35
        self.mirror = False
        self.head_strength = 1.0
        self.maxima = {}                 # learned per-shape full values
        self.learning = False
        self.head_neutral_euler = None
        self._filters = {}

    def calibrate(self, raw_shapes, head=None):
        """This face (relaxed, looking at the camera) becomes zero."""
        self.neutral = {k: float(v) for k, v in (raw_shapes or {}).items()
                        if k in SHAPES}
        self.head_neutral = euler_matrix(head) if head else None
        self.head_neutral_euler = list(head) if head else None
        self._filters.clear()

    def learn(self, raw_shapes):
        """Remember the biggest value each shape reaches (Learn Range)."""
        for k, v in (raw_shapes or {}).items():
            if k in SHAPES and v > self.maxima.get(k, 0.0):
                self.maxima[k] = float(v)

    def reset_range(self):
        self.maxima = {}

    def reset_filters(self):
        self._filters.clear()

    def _filter(self, key, v, t, fast=False):
        if self.smoothing <= 1e-3:
            return v
        f = self._filters.get(key)
        s = min(1.0, self.smoothing)
        cutoff = 0.4 + 9.6 * (1.0 - s) ** 2        # Hz: 10 (light) .. 0.4
        beta = 0.6 * (1.0 - s) + 0.08
        if fast:                  # blinks last ~0.1 s: don't smooth them away
            cutoff, beta = cutoff * 4.0, beta * 3.0
        if f is None or abs(f.min_cutoff - cutoff) > 1e-6:
            f = self._filters[key] = OneEuro(cutoff, beta=beta)
        return f(v, t)

    def _normalised(self, raw, name):
        v = float(raw.get(name, 0.0))
        n = self.neutral.get(name, 0.0)
        top = self.maxima.get(name)
        if top is not None:
            return (v - n) / max(0.12, top - n)
        return (v - n) / max(0.05, 1.0 - n) if n > 0.0 else v

    def shapes(self, raw, t):
        if self.learning:
            self.learn(raw)
        out = {}
        for name in SHAPES:
            v = self._normalised(raw, name)
            key = face_tracker.swap_name(name) if self.mirror else name
            v = _clamp01(v * self.gains.get(group_of(key), 1.0))
            blink = key.startswith("eyeBlink")
            if blink:
                # a webcam rarely reads a full close: past most of the way,
                # it IS closed
                v = _smooth01((v - 0.08) / 0.72)
            if name == "mouthClose":
                # lips-together is relative to how open the jaw is (ARKit):
                # never seal a mouth that's just opening
                jaw = max(0.0, float(raw.get("jawOpen", 0.0))
                          - self.neutral.get("jawOpen", 0.0))
                close = max(0.0, float(raw.get("mouthClose", 0.0))
                            - self.neutral.get("mouthClose", 0.0))
                v = _clamp01(close / max(jaw, 0.05)) * _smooth01(
                    (jaw - 0.03) / 0.12)
            out[key] = _clamp01(self._filter(key, v, t, fast=blink))
        return out

    def head(self, head, t):
        """Degrees x / y / z to add to the character's head (world frame of
        a character facing +Z), or None."""
        if not head:
            return None
        r = euler_matrix(head)
        if self.head_neutral:
            r = _mul(r, _transpose(self.head_neutral))
        rx, ry, rz = face_tracker.matrix_to_euler(r)
        if self.mirror:
            ry, rz = -ry, -rz
        s = self.head_strength
        return [self._filter("head_" + a, v * s, t)
                for a, v in zip("xyz", (rx, ry, rz))]


def profile_path():
    return os.path.join(cmds.internalVar(userAppDir=True),
                        "DanyalsRigBuilder", "face_capture_profile.json")


def save_profile(proc, path=None):
    """Your neutral face, learned ranges and tuning, for next time."""
    path = path or profile_path()
    folder = os.path.dirname(path)
    if not os.path.isdir(folder):
        os.makedirs(folder)
    data = {"neutral": proc.neutral, "maxima": proc.maxima,
            "head_neutral": proc.head_neutral_euler, "gains": proc.gains,
            "smoothing": proc.smoothing, "mirror": proc.mirror,
            "head_strength": proc.head_strength}
    with open(path, "w") as fh:
        json.dump(data, fh, indent=1)
    return path


def load_profile(proc, path=None):
    path = path or profile_path()
    if not os.path.isfile(path):
        return False
    try:
        with open(path) as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return False
    proc.neutral = {k: float(v) for k, v in data.get("neutral", {}).items()
                    if k in SHAPES}
    proc.maxima = {k: float(v) for k, v in data.get("maxima", {}).items()
                   if k in SHAPES}
    head = data.get("head_neutral")
    proc.head_neutral_euler = head
    proc.head_neutral = euler_matrix(head) if head else None
    for k, v in data.get("gains", {}).items():
        if k in proc.gains:
            proc.gains[k] = float(v)
    proc.smoothing = float(data.get("smoothing", proc.smoothing))
    proc.mirror = bool(data.get("mirror", proc.mirror))
    proc.head_strength = float(data.get("head_strength", proc.head_strength))
    proc.reset_filters()
    return True


# =============================================================================
# The rig side
# =============================================================================

def _plug(node_attr):
    sel = om2.MSelectionList()
    sel.add(node_attr)
    return sel.getPlug(0)


class RigTarget(object):
    """Writes dial values and head rotation into the scene, fast (no undo
    queue, no auto keys)."""

    def __init__(self):
        self.refresh()

    def refresh(self):
        self.plugs = {}
        if face_shapes.exists():
            for s in SHAPES:
                plug = "%s.%s" % (face_shapes.CTRL, s)
                if cmds.getAttr(plug, k=True):
                    self.plugs[s] = (plug, _plug(plug))
        self.head = []
        for ctrl, share in HEAD_CTRLS:
            if not cmds.objExists(ctrl):
                continue
            chans = [ctrl + "." + a for a in ("rx", "ry", "rz")]
            if any(cmds.getAttr(c, lock=True) for c in chans):
                continue
            self.head.append({"ctrl": ctrl, "share": share})
        if len(self.head) == 1:
            self.head[0]["share"] = 1.0
        self.capture_head_rest()

    def capture_head_rest(self):
        for h in self.head:
            ctrl = h["ctrl"]
            h["rest"] = [cmds.getAttr(ctrl + "." + a) for a in ("rx", "ry",
                                                              "rz")]
            h["order"] = cmds.getAttr(ctrl + ".rotateOrder")
            pm = om2.MMatrix(cmds.getAttr(ctrl + ".parentMatrix[0]"))
            h["parent"] = om2.MTransformationMatrix(pm).asRotateMatrix()

    @property
    def ready(self):
        return bool(self.plugs)

    def head_rotations(self, delta):
        """{ctrl: [rx, ry, rz] degrees} for a world head rotation delta."""
        out = {}
        if not delta:
            return out
        for h in self.head:
            d = om2.MEulerRotation(*[math.radians(a * h["share"])
                                     for a in delta]).asMatrix()
            order = h["order"]
            rest = om2.MEulerRotation(*[math.radians(a) for a in h["rest"]],
                                      order=order).asMatrix()
            p = h["parent"]
            local = rest * p * d * p.inverse()
            e = om2.MTransformationMatrix(local).rotation()
            e = e.reorder(order)
            out[h["ctrl"]] = [math.degrees(e.x), math.degrees(e.y),
                              math.degrees(e.z)]
        return out

    def apply(self, values, head=None):
        auto = cmds.autoKeyframe(q=True, state=True)
        undo = cmds.undoInfo(q=True, state=True)
        try:
            if auto:
                cmds.autoKeyframe(state=False)
            if undo:
                cmds.undoInfo(stateWithoutFlush=False)
            for s, v in values.items():
                entry = self.plugs.get(s)
                if not entry:
                    continue
                try:
                    entry[1].setDouble(v)
                except RuntimeError:
                    cmds.setAttr(entry[0], v)
            for ctrl, rot in (head or {}).items():
                for a, v in zip(("rx", "ry", "rz"), rot):
                    cmds.setAttr("%s.%s" % (ctrl, a), v)
        finally:
            if undo:
                cmds.undoInfo(stateWithoutFlush=True)
            if auto:
                cmds.autoKeyframe(state=True)

    def restore_head(self):
        self.apply({}, {h["ctrl"]: h["rest"] for h in self.head})


# =============================================================================
# Receiving
# =============================================================================

class Receiver(object):
    """Non-blocking UDP listener on 127.0.0.1 (polled from a Qt timer)."""

    def __init__(self, port=PORT, host="127.0.0.1"):
        self.port, self.host = port, host
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setblocking(False)
        self.sock.bind((host, port))
        self.last_time = None
        self.count = 0

    def poll(self):
        """The newest packet waiting (older ones are dropped), or None."""
        latest = None
        while True:
            try:
                data = self.sock.recv(65536)
            except (BlockingIOError, socket.timeout):
                break
            except OSError:
                break          # e.g. Windows ICMP resets on a closed port
            try:
                pkt = json.loads(data.decode("utf-8"))
            except ValueError:
                continue
            if isinstance(pkt, dict) and isinstance(pkt.get("shapes"), dict):
                latest = pkt
        if latest is not None:
            self.last_time = time.perf_counter()
            self.count += 1
        return latest

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


# =============================================================================
# Recording to keys
# =============================================================================

def scene_fps():
    return om2.MTime(1.0, om2.MTime.kSeconds).asUnits(om2.MTime.uiUnit())


def _sample_at(times, values, t):
    i = bisect.bisect_left(times, t)
    if i <= 0:
        return values[0]
    if i >= len(times):
        return values[-1]
    t0, t1 = times[i - 1], times[i]
    w = (t - t0) / (t1 - t0) if t1 > t0 else 0.0
    return values[i - 1] + (values[i] - values[i - 1]) * w


def _write_curve(plug_name, frames, values, angular=False):
    """Replace the keys on plug_name between the first and last frame."""
    if cmds.getAttr(plug_name, lock=True):
        return 0
    lo, hi = frames[0], frames[-1]
    if cmds.keyframe(plug_name, q=True, keyframeCount=True):
        cmds.cutKey(plug_name, time=(lo, hi), clear=True)
    else:
        cmds.setKeyframe(plug_name, t=lo, v=cmds.getAttr(plug_name))
        cmds.cutKey(plug_name, time=(lo, lo), clear=True)
    curves = cmds.listConnections(plug_name, s=True, d=False,
                                  type="animCurve") or []
    if curves:
        sel = om2.MSelectionList()
        sel.add(curves[0])
        fn = oma2.MFnAnimCurve(sel.getDependNode(0))
    else:
        fn = oma2.MFnAnimCurve()
        fn.create(_plug(plug_name))
    unit = om2.MTime.uiUnit()
    times = om2.MTimeArray([om2.MTime(float(f), unit) for f in frames])
    vals = om2.MDoubleArray([math.radians(v) if angular else v
                             for v in values])
    fn.addKeys(times, vals, oma2.MFnAnimCurve.kTangentAuto,
               oma2.MFnAnimCurve.kTangentAuto, True)
    return len(frames)


def write_keys(samples, start_frame, fps=None, channels=None):
    """samples: [(seconds, {shape: value}, {ctrl: [rx, ry, rz]})]. Keys every
    whole frame from start_frame (resampled). Returns the frame range."""
    if len(samples) < 2:
        return None
    fps = fps or scene_fps()
    t0 = samples[0][0]
    times = [s[0] - t0 for s in samples]
    last = start_frame + times[-1] * fps
    frames = list(range(int(math.ceil(start_frame - 1e-6)),
                        int(math.floor(last + 1e-6)) + 1))
    if len(frames) < 2:
        return None
    ftimes = [(f - start_frame) / fps for f in frames]
    if face_shapes.exists():
        for s in SHAPES:
            plug = "%s.%s" % (face_shapes.CTRL, s)
            if channels is not None and s not in channels:
                continue
            if not cmds.getAttr(plug, k=True):
                continue
            vals = [smp[1].get(s, 0.0) for smp in samples]
            _write_curve(plug, frames, [_sample_at(times, vals, t)
                                        for t in ftimes])
    ctrls = sorted({c for smp in samples for c in (smp[2] or {})})
    for ctrl in ctrls:
        have = [smp for smp in samples if ctrl in (smp[2] or {})]
        if len(have) < 2:
            continue
        ht = [smp[0] - t0 for smp in have]
        for i, a in enumerate(("rx", "ry", "rz")):
            vals = [smp[2][ctrl][i] for smp in have]
            _write_curve("%s.%s" % (ctrl, a), frames,
                         [_sample_at(ht, vals, t) for t in ftimes],
                         angular=True)
    return frames[0], frames[-1]


def clear_capture_keys(start=None, end=None):
    """Delete the keys on the shape dials and head controls (in a range, or
    all of them)."""
    plugs = []
    if face_shapes.exists():
        plugs += ["%s.%s" % (face_shapes.CTRL, s) for s in SHAPES]
    for ctrl, _ in HEAD_CTRLS:
        if cmds.objExists(ctrl):
            plugs += ["%s.%s" % (ctrl, a) for a in ("rx", "ry", "rz")]
    n = 0
    for p in plugs:
        if not cmds.keyframe(p, q=True, keyframeCount=True):
            continue
        if start is None:
            cmds.cutKey(p, clear=True)
        else:
            cmds.cutKey(p, time=(start, end), clear=True)
        n += 1
    return n


# =============================================================================
# CSV (Live Link Face on iPhone, or face_tracker --csv)
# =============================================================================

def _parse_timecode(tc):
    """'HH:MM:SS:FF.mmm' -> (seconds, frame number, sub-frame)."""
    parts = tc.strip().split(":")
    if len(parts) < 4:
        return None
    h, m, s = int(parts[0]), int(parts[1]), int(parts[2])
    ff = float(parts[3])
    return h * 3600 + m * 60 + s, ff


def read_csv(path):
    """[(seconds, {shape: value}, head degrees [x, y, z] or None)]."""
    with open(path, newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.reader(fh))
    if len(rows) < 2:
        return []
    header = [h.strip() for h in rows[0]]
    col = {}
    for i, name in enumerate(header):
        key = name[:1].lower() + name[1:]
        if key in SHAPES:
            col[key] = i
    if not col:
        raise ValueError("%s has no face shape columns (like EyeBlinkLeft)."
                         % os.path.basename(path))
    hi = {k: header.index(k) for k in ("HeadYaw", "HeadPitch", "HeadRoll")
          if k in header}
    tci = header.index("Timecode") if "Timecode" in header else None
    parsed = []
    max_ff = 0.0
    for r in rows[1:]:
        if not r or len(r) < len(header):
            continue
        stamp = _parse_timecode(r[tci]) if tci is not None else None
        try:
            shapes = {k: float(r[i]) for k, i in col.items()}
        except ValueError:
            continue
        head = None
        if len(hi) == 3:
            try:
                head = [math.degrees(float(r[hi["HeadPitch"]])),
                        math.degrees(float(r[hi["HeadYaw"]])),
                        math.degrees(float(r[hi["HeadRoll"]]))]
            except ValueError:
                head = None
        if stamp:
            max_ff = max(max_ff, stamp[1])
        parsed.append((stamp, shapes, head))
    # the source frame rate, from the highest frame number in the timecodes
    src_fps = 60.0
    for rate in (24.0, 25.0, 30.0, 48.0, 50.0, 60.0, 120.0):
        if max_ff < rate:
            src_fps = rate
            break
    out = []
    for i, (stamp, shapes, head) in enumerate(parsed):
        if stamp:
            t = stamp[0] + stamp[1] / src_fps
        else:
            t = i / src_fps
        if out and t <= out[-1][0]:
            t = out[-1][0] + 1e-4
        out.append((t, shapes, head))
    return out


def import_csv(path, start_frame=None, processor=None, head=True):
    """Key a CSV take onto the shape dials (and neck / head) from start_frame
    (default: the current frame). Gains / mirror / smoothing come from
    processor (defaults if None; no neutral). Returns (first, last) frame."""
    rows = read_csv(path)
    if len(rows) < 2:
        raise ValueError("No frames in %s." % os.path.basename(path))
    if not face_shapes.exists():
        face_shapes.build()
    proc = processor or Processor()
    target = RigTarget()
    samples = []
    for t, shapes, h in rows:
        vals = proc.shapes(shapes, t)
        rots = target.head_rotations(proc.head(h, t)) if head else {}
        samples.append((t, vals, rots))
    start = cmds.currentTime(q=True) if start_frame is None else start_frame
    rng = write_keys(samples, start)
    if rng:
        print("[faceCapture] Imported %s: frames %d to %d."
              % (os.path.basename(path), rng[0], rng[1]))
    return rng


# =============================================================================
# Starting the tracker
# =============================================================================

def _clean_env():
    env = dict(os.environ)
    for k in list(env):
        if k.upper() in ("PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP",
                         "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH",
                         "QT_QPA_FONTDIR"):
            env.pop(k)
    return env


def _candidates():
    seen, out = set(), []
    saved = cmds.optionVar(q=PYTHON_OPTVAR) if cmds.optionVar(
        exists=PYTHON_OPTVAR) else ""
    names = [saved] if saved else []
    for exe in ("python", "python3", "py"):
        w = shutil.which(exe)
        if w:
            names.append(w)
    local = os.environ.get("LOCALAPPDATA", "")
    if local:
        names += sorted(glob.glob(os.path.join(
            local, "Programs", "Python", "Python3*", "python.exe")),
            reverse=True)
    maya_dir = os.path.dirname(sys.executable).lower()
    for n in names:
        key = os.path.normcase(os.path.abspath(n))
        if key in seen or not os.path.isfile(n):
            continue
        if os.path.dirname(key).lower() == maya_dir:
            continue                      # Maya's own Python can't run it
        seen.add(key)
        out.append(n)
    return out


def _python_cmd(exe):
    return [exe, "-3"] if os.path.basename(exe).lower() in ("py", "py.exe") \
        else [exe]


def python_has_mediapipe(exe, timeout=90):
    try:
        r = subprocess.run(_python_cmd(exe) + [
            "-c", "import mediapipe, cv2; print('ok')"],
            capture_output=True, text=True, timeout=timeout, env=_clean_env(),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return r.returncode == 0 and "ok" in r.stdout
    except (OSError, subprocess.SubprocessError):
        return False


def find_python():
    """A Python that has MediaPipe + OpenCV (remembered once found)."""
    for exe in _candidates():
        if python_has_mediapipe(exe):
            cmds.optionVar(sv=(PYTHON_OPTVAR, exe))
            return exe
    return None


def tracker_script():
    return os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "face_tracker.py")


def stop_tracker(port=PORT, host="127.0.0.1"):
    """Ask a running tracker to quit (it listens on port + 1), whoever
    started it. Returns True if the message went out."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.sendto(b"quit", (host, port + 1))
        s.close()
        return True
    except OSError:
        return False


def start_tracker(python, camera=0, url="", video="", port=PORT,
                  extra=()):
    """Launch face_tracker.py in its own console window. Returns the
    process."""
    args = _python_cmd(python) + [tracker_script(), "--port", str(port)]
    if video:
        args += ["--video", video]
    elif url:
        args += ["--url", url]
    else:
        args += ["--camera", str(int(camera))]
    args += list(extra)
    flags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
    return subprocess.Popen(args, env=_clean_env(), creationflags=flags,
                            cwd=os.path.dirname(tracker_script()))


# =============================================================================
# Panel
# =============================================================================

def _qt():
    try:
        from PySide2 import QtCore, QtWidgets
        from shiboken2 import wrapInstance
    except ImportError:
        from PySide6 import QtCore, QtWidgets
        from shiboken6 import wrapInstance
    return QtCore, QtWidgets, wrapInstance


def _make_panel_class():
    QtCore, QtWidgets, wrapInstance = _qt()
    import maya.OpenMayaUI as omui
    try:
        import forge_theme as ft
    except ImportError:
        ft = None

    class FaceCapturePanel(QtWidgets.QDialog):

        def __init__(self, parent=None, profile=True):
            ptr = omui.MQtUtil.mainWindow()
            main = wrapInstance(int(ptr), QtWidgets.QWidget) if ptr else None
            QtWidgets.QDialog.__init__(self, parent or main)
            self.setObjectName(WINDOW_OBJECT_NAME)
            self.setWindowTitle("Face Capture")
            self.setWindowFlags(self.windowFlags() | QtCore.Qt.Window)
            if ft:
                self.setStyleSheet(ft.style(ft.ACCENT_RIG))
            self.setMinimumWidth(360)
            self.proc = Processor()
            self.use_profile = profile
            self.profile_loaded = load_profile(self.proc) if profile else False
            self.learn_until = None
            self.target = RigTarget()
            self.receiver = None
            self.tracker = None
            self.last_pkt = None
            self.take = None
            self.take_start = None
            self.t_start = time.perf_counter()
            self._build_ui()
            self._listen()
            self.timer = QtCore.QTimer(self)
            self.timer.setInterval(15)
            self.timer.timeout.connect(self._tick)
            self.timer.start()
            self._refresh_rig()
            if self.profile_loaded:
                self._say("Loaded your saved calibration%s." % (
                    " and ranges" if self.proc.maxima else ""))

        # ---- UI ----------------------------------------------------------------
        def _build_ui(self):
            lay = QtWidgets.QVBoxLayout(self)
            lay.setContentsMargins(12, 12, 12, 12)
            lay.setSpacing(8)
            title = QtWidgets.QLabel("FACE CAPTURE")
            title.setStyleSheet("QLabel { font-size: 14pt; font-weight: bold;"
                                " color: #ffd9a0; }")
            lay.addWidget(title)
            self.status = QtWidgets.QLabel("")
            self.status.setWordWrap(True)
            lay.addWidget(self.status)

            cam = QtWidgets.QGroupBox("1. Camera")
            g = QtWidgets.QGridLayout(cam)
            g.addWidget(QtWidgets.QLabel("Source"), 0, 0)
            self.source = QtWidgets.QComboBox()
            self.source.addItems(["PC webcam / phone as webcam",
                                  "Phone or IP camera stream (URL)",
                                  "Video file"])
            self.source.currentIndexChanged.connect(self._on_source)
            g.addWidget(self.source, 0, 1, 1, 2)
            self.cam_label = QtWidgets.QLabel("Camera #")
            g.addWidget(self.cam_label, 1, 0)
            self.camera = QtWidgets.QSpinBox()
            self.camera.setRange(0, 9)
            self.camera.setToolTip(
                "0 is usually the built-in / first webcam. A phone running\n"
                "DroidCam or Iriun shows up as another camera number.")
            g.addWidget(self.camera, 1, 1)
            self.url = QtWidgets.QLineEdit()
            self.url.setPlaceholderText("http://192.168.1.20:8080/video")
            self.url.setToolTip(
                "The phone's video stream address from an IP camera app\n"
                "(same Wi-Fi as this PC), or a video file path.")
            g.addWidget(self.url, 1, 1, 1, 2)
            self.browse = QtWidgets.QPushButton("...")
            self.browse.clicked.connect(self._on_browse_video)
            g.addWidget(self.browse, 1, 3)
            row = QtWidgets.QHBoxLayout()
            self.btn_start = QtWidgets.QPushButton("Start Tracker")
            self.btn_start.setToolTip(
                "Opens the tracker window (your camera with the face points).\n"
                "Needs a Python with MediaPipe: python -m pip install "
                "mediapipe")
            self.btn_start.clicked.connect(self._on_start)
            self.btn_stop = QtWidgets.QPushButton("Stop Tracker")
            self.btn_stop.clicked.connect(self._on_stop)
            row.addWidget(self.btn_start, 2)
            row.addWidget(self.btn_stop, 1)
            g.addLayout(row, 2, 0, 1, 4)
            g.addWidget(QtWidgets.QLabel("Port"), 3, 0)
            self.port = QtWidgets.QSpinBox()
            self.port.setRange(1024, 65535)
            self.port.setValue(PORT)
            self.port.editingFinished.connect(self._listen)
            g.addWidget(self.port, 3, 1)
            lay.addWidget(cam)
            self._on_source(0)

            rig = QtWidgets.QGroupBox("2. Face")
            v = QtWidgets.QVBoxLayout(rig)
            r1 = QtWidgets.QHBoxLayout()
            self.btn_dials = QtWidgets.QPushButton("Add Shape Dials")
            self.btn_dials.clicked.connect(self._on_add_dials)
            r1.addWidget(self.btn_dials)
            self.btn_cal = QtWidgets.QPushButton("Calibrate Neutral")
            self.btn_cal.setToolTip(
                "Relax your face and look straight at the camera, then\n"
                "click: that face becomes zero on every dial.")
            self.btn_cal.clicked.connect(self._on_calibrate)
            r1.addWidget(self.btn_cal)
            v.addLayout(r1)
            r1b = QtWidgets.QHBoxLayout()
            self.btn_learn = QtWidgets.QPushButton("Learn Range (10 s)")
            self.btn_learn.setToolTip(
                "Click, then make BIG faces for 10 seconds: open wide, blink\n"
                "hard, smile, frown, brows up and down, puff, pucker, look\n"
                "around. Each dial learns YOUR full value, so a wide-open\n"
                "mouth reads as fully open and a blink fully closes.")
            self.btn_learn.clicked.connect(self._on_learn)
            r1b.addWidget(self.btn_learn, 2)
            b_reset = QtWidgets.QPushButton("Reset Range")
            b_reset.clicked.connect(self._on_reset_range)
            r1b.addWidget(b_reset, 1)
            v.addLayout(r1b)
            r2 = QtWidgets.QHBoxLayout()
            self.chk_live = QtWidgets.QCheckBox("Live")
            self.chk_live.setChecked(True)
            self.chk_live.setToolTip("Drive the rig while the tracker runs.")
            self.chk_live.toggled.connect(self._on_live)
            self.chk_mirror = QtWidgets.QCheckBox("Mirror L/R")
            self.chk_mirror.setToolTip(
                "Wink your LEFT eye: the character's left eye should close.\n"
                "If the other one does, tick this.")
            self.chk_mirror.toggled.connect(self._on_mirror)
            for w in (self.chk_live, self.chk_mirror):
                r2.addWidget(w)
            r2.addStretch(1)
            v.addLayout(r2)
            r3 = QtWidgets.QHBoxLayout()
            drive = QtWidgets.QLabel("Drive:")
            drive.setToolTip(
                "Which parts your face drives. Untick Head (and Brows and\n"
                "Cheeks) to animate just the eyes and mouth: everything\n"
                "unticked is left exactly as it is, and recording doesn't\n"
                "touch its keys.")
            r3.addWidget(drive)
            self.regions = {}
            for label, key, groups in REGIONS:
                cb = QtWidgets.QCheckBox(label)
                cb.setChecked(True)
                cb.setToolTip("Let your %s drive the rig." % label.lower())
                cb.toggled.connect(
                    lambda on, g=groups: self._on_region(g, on))
                self.regions[key] = (cb, groups)
                r3.addWidget(cb)
            self.chk_head = QtWidgets.QCheckBox("Head")
            self.chk_head.setChecked(True)
            self.chk_head.setToolTip(
                "Turn the neck and head with yours. Untick it to keep the "
                "head\nstill and animate only the face.")
            self.chk_head.toggled.connect(self._on_head_toggled)
            r3.addWidget(self.chk_head)
            r3.addStretch(1)
            v.addLayout(r3)
            lay.addWidget(rig)

            tune = QtWidgets.QGroupBox("3. Tuning")
            tg = QtWidgets.QGridLayout(tune)
            self.sliders = {}
            rows = [("Smoothing", "smoothing", 0.0, 1.0, self.proc.smoothing,
                     "Steadier (more) or snappier (less).")]
            rows += [(k.title(), k, 0.0, 3.0, self.proc.gains[k],
                      "How strongly your %s reach the character." % k)
                     for k in GROUPS]
            rows.append(("Head", "head", 0.0, 2.0, self.proc.head_strength,
                         "How far the head turns with yours."))
            for i, (label, key, lo, hi, dv, tip) in enumerate(rows):
                lbl = QtWidgets.QLabel(label)
                sl = QtWidgets.QSlider(QtCore.Qt.Horizontal)
                sl.setRange(int(lo * 100), int(hi * 100))
                sl.setValue(int(round(dv * 100)))
                sl.setToolTip(tip)
                val = QtWidgets.QLabel("%.2f" % dv)
                val.setFixedWidth(34)
                sl.valueChanged.connect(
                    lambda x, k=key, vl=val: self._on_slider(k, x, vl))
                tg.addWidget(lbl, i, 0)
                tg.addWidget(sl, i, 1)
                tg.addWidget(val, i, 2)
                self.sliders[key] = sl
            lay.addWidget(tune)
            self.chk_mirror.setChecked(self.proc.mirror)

            meters = QtWidgets.QGroupBox("Live meters (strongest dials)")
            mg = QtWidgets.QGridLayout(meters)
            mg.setVerticalSpacing(2)
            self.meters = []
            for i in range(8):
                name = QtWidgets.QLabel("")
                name.setFixedWidth(140)
                bar = QtWidgets.QProgressBar()
                bar.setRange(0, 100)
                bar.setTextVisible(False)
                bar.setFixedHeight(9)
                mg.addWidget(name, i, 0)
                mg.addWidget(bar, i, 1)
                self.meters.append((name, bar))
            lay.addWidget(meters)

            rec = QtWidgets.QGroupBox("4. Record")
            rg = QtWidgets.QGridLayout(rec)
            self.btn_rec = QtWidgets.QPushButton("Record")
            self.btn_rec.setCheckable(True)
            self.btn_rec.setToolTip(
                "Records from the current frame, in real time, onto the\n"
                "shape dials and the neck / head. Click again to stop and\n"
                "write the keys (the old keys in that range are replaced).")
            self.btn_rec.toggled.connect(self._on_record)
            rg.addWidget(self.btn_rec, 0, 0, 1, 2)
            b_clear = QtWidgets.QPushButton("Clear Capture Keys")
            b_clear.clicked.connect(self._on_clear_keys)
            rg.addWidget(b_clear, 0, 2)
            self.btn_reset = QtWidgets.QPushButton(
                "Stop and Reset  (camera off, face back to neutral)")
            self.btn_reset.setToolTip(
                "Stops the tracker (closes the camera window), puts every\n"
                "shape dial back to 0 and the neck and head back to the pose\n"
                "they were in. Your recorded keys are kept: use Clear\n"
                "Capture Keys for those. Closing this panel or pressing Esc\n"
                "does the same.")
            self.btn_reset.clicked.connect(self._on_reset_all)
            rg.addWidget(self.btn_reset, 2, 0, 1, 3)
            b_csv = QtWidgets.QPushButton(
                "Import CSV (iPhone Live Link Face or tracker)...")
            b_csv.setToolTip(
                "Keys a recorded CSV from the current frame, with the gains,\n"
                "mirror and smoothing above.")
            b_csv.clicked.connect(self._on_import_csv)
            rg.addWidget(b_csv, 1, 0, 1, 3)
            lay.addWidget(rec)

        # ---- helpers ----------------------------------------------------------------
        def _say(self, text):
            self.status.setText(text)

        def _listen(self):
            port = self.port.value()
            if self.receiver and self.receiver.port == port:
                return
            if self.receiver:
                self.receiver.close()
                self.receiver = None
            try:
                self.receiver = Receiver(port)
                self._say("Listening on port %d. Start the tracker." % port)
            except OSError as e:
                self._say("Port %d is busy (%s). Pick another port for both "
                          "the panel and the tracker." % (port, e))

        def _refresh_rig(self):
            self.target.refresh()
            has = face_shapes.exists()
            self.btn_dials.setText("Rebuild Shape Dials" if has
                                   else "Add Shape Dials")
            if not has and not face_shapes.has_face():
                self._say("No face rig in the scene: build a character with "
                          "a face (and the Advanced Face) first.")

        # ---- slots ------------------------------------------------------------------
        def _on_source(self, idx):
            webcam = idx == 0
            self.camera.setVisible(webcam)
            self.cam_label.setText("Camera #" if webcam else
                                   ("URL" if idx == 1 else "File"))
            self.url.setVisible(not webcam)
            self.browse.setVisible(idx == 2)
            self.url.setPlaceholderText(
                "http://192.168.1.20:8080/video" if idx == 1
                else "D:/takes/me_talking.mp4")

        def _on_browse_video(self):
            path, _ = QtWidgets.QFileDialog.getOpenFileName(
                self, "Video file", "", "Video (*.mp4 *.mov *.avi *.mkv)")
            if path:
                self.url.setText(path)

        def _on_start(self):
            self._on_stop()
            self._say("Looking for a Python with MediaPipe...")
            QtWidgets.QApplication.processEvents()
            py = find_python()
            if not py:
                self._say(
                    "No Python with MediaPipe found. Install Python 3.9 to "
                    "3.13 from python.org, then run:  python -m pip install "
                    "mediapipe   and click Start again.")
                return
            idx = self.source.currentIndex()
            text = self.url.text().strip()
            if idx and not text:
                self._say("Enter the stream URL or video file first.")
                return
            try:
                self.tracker = start_tracker(
                    py, camera=self.camera.value(),
                    url=text if idx == 1 else "",
                    video=text if idx == 2 else "",
                    port=self.port.value())
            except OSError as e:
                self._say("Could not start the tracker: %s" % e)
                return
            self._say("Tracker starting (the first run downloads the face "
                      "model, about 4 MB)...")

        def _on_stop(self):
            # tell it to quit (works even if something else started it),
            # then make sure ours is gone
            stop_tracker(self.port.value())
            if self.tracker and self.tracker.poll() is None:
                try:
                    self.tracker.wait(timeout=1.5)
                except Exception:
                    try:
                        self.tracker.terminate()
                    except Exception:
                        pass
            self.tracker = None

        def _on_add_dials(self):
            try:
                res = face_shapes.build()
            except RuntimeError as e:
                self._say(str(e))
                return
            self._refresh_rig()
            self._say("%d shape dials ready." % len(res["wired"]))

        def _on_calibrate(self):
            if not self.last_pkt or not self.last_pkt.get("found", True):
                self._say("No face coming in yet: start the tracker and look "
                          "at the camera.")
                return
            self.proc.calibrate(self.last_pkt["shapes"],
                                self.last_pkt.get("head"))
            self.target.capture_head_rest()
            self._save()
            self._say("Calibrated: this face is neutral now. Next: Learn "
                      "Range.")

        def _save(self):
            if self.use_profile:
                try:
                    save_profile(self.proc)
                except OSError:
                    pass

        def _on_learn(self):
            if not self.last_pkt or not self.last_pkt.get("found", True):
                self._say("No face coming in yet: start the tracker first.")
                return
            if not self.proc.neutral:
                self.proc.calibrate(self.last_pkt["shapes"],
                                    self.last_pkt.get("head"))
                self.target.capture_head_rest()
            self.proc.reset_range()
            self.proc.learning = True
            self.learn_until = time.perf_counter() + 10.0
            self._say("Make BIG faces now: open wide, blink hard, smile, "
                      "frown, brows up and down, puff, pucker, look around!")

        def _finish_learning(self):
            self.proc.learning = False
            self.learn_until = None
            for k in GROUPS:                    # ranges do the work now
                self.sliders[k].setValue(100)
            self._save()
            weak = [k for k in ("jawOpen", "eyeBlinkLeft", "eyeBlinkRight",
                                "mouthSmileLeft", "browInnerUp")
                    if self.proc.maxima.get(k, 0.0)
                    - self.proc.neutral.get(k, 0.0) < 0.12]
            self._say("Range learned%s. Gains reset to 1.00." % (
                "" if not weak else " (barely moved: %s, try again bigger)"
                % ", ".join(weak)))

        def _on_reset_range(self):
            self.proc.reset_range()
            self.proc.learning = False
            self.learn_until = None
            self._save()
            self._say("Range cleared.")

        def _on_live(self, on):
            if not on:
                self.target.restore_head()

        def enabled_groups(self):
            groups = set()
            for cb, gs in self.regions.values():
                if cb.isChecked():
                    groups.update(gs)
            return groups

        def _on_region(self, groups, on):
            """Switching a part off leaves it exactly as it is: its dials go
            back to 0 once, then capture never touches them again."""
            if not on and self.target.ready:
                self.target.apply({s: 0.0 for s in region_shapes(groups)})

        def _on_reset_all(self):
            """One button: camera off, face neutral, head back."""
            self._on_stop()
            if self.take is not None:
                self.btn_rec.setChecked(False)
            if face_shapes.exists():
                face_shapes.reset()
            self.target.restore_head()
            self.proc.reset_filters()
            self._say("Stopped. Camera closed, face back to neutral (your "
                      "keys are still there).")

        def _on_mirror(self, on):
            self.proc.mirror = on
            self.proc.reset_filters()

        def _on_head_toggled(self, on):
            if not on:
                self.target.restore_head()

        def _on_slider(self, key, x, label):
            v = x / 100.0
            label.setText("%.2f" % v)
            if key == "smoothing":
                self.proc.smoothing = v
            elif key == "head":
                self.proc.head_strength = v
            else:
                self.proc.gains[key] = v

        def _on_record(self, on):
            if on:
                if not self.target.ready:
                    self._on_add_dials()
                self.take = []
                self.take_start = cmds.currentTime(q=True)
                self.btn_rec.setText("Stop  (recording)")
                self._say("Recording from frame %d..." % self.take_start)
                return
            self.btn_rec.setText("Record")
            take, self.take = self.take or [], None
            if len(take) < 2:
                self._say("Nothing recorded (no face data came in).")
                return
            channels = {k for smp in take for k in smp[1]}
            rng = write_keys(take, self.take_start, channels=channels)
            if rng:
                cmds.playbackOptions(minTime=min(rng[0], cmds.playbackOptions(
                    q=True, minTime=True)), maxTime=max(
                    rng[1], cmds.playbackOptions(q=True, maxTime=True)))
                cmds.currentTime(rng[0])
                self._say("Keyed frames %d to %d (%.1f s)." % (
                    rng[0], rng[1], take[-1][0] - take[0][0]))

        def _on_clear_keys(self):
            n = clear_capture_keys()
            self._say("Cleared keys on %d channels." % n)

        def _on_import_csv(self):
            path, _ = QtWidgets.QFileDialog.getOpenFileName(
                self, "Face capture CSV", "", "CSV (*.csv)")
            if not path:
                return
            try:
                rng = import_csv(path, processor=self.proc,
                                 head=self.chk_head.isChecked())
            except (ValueError, OSError) as e:
                self._say(str(e))
                return
            self._refresh_rig()
            if rng:
                self._say("Imported frames %d to %d." % rng)

        # ---- the loop --------------------------------------------------------------
        def _tick(self):
            if not self.receiver:
                return
            pkt = self.receiver.poll()
            now = time.perf_counter()
            if pkt is None:
                if self.receiver.last_time and now - self.receiver.last_time \
                        > 1.5 and not self.status.text().startswith("Waiting"):
                    self._say("Waiting for the tracker...")
                return
            self.last_pkt = pkt
            found = pkt.get("found", True) and pkt["shapes"]
            if not found:
                self._say("Tracker running, but no face in view.")
                return
            if self.proc.learning:
                self.proc.learn(pkt["shapes"])
                left = (self.learn_until or now) - now
                if left <= 0.0:
                    self._finish_learning()
                elif self.receiver.count % 10 == 0:
                    self._say("Learning: BIG faces!  %.0f s" % left)
            if not self.target.ready and face_shapes.exists():
                self.target.refresh()
            t = now - self.t_start
            live = self.chk_live.isChecked()
            if not (live or self.take is not None):
                return
            vals = self.proc.shapes(pkt["shapes"], t)
            groups = self.enabled_groups()
            if len(groups) < len(GROUPS):
                vals = {k: x for k, x in vals.items()
                        if group_of(k) in groups}
            rots = {}
            if self.chk_head.isChecked():
                rots = self.target.head_rotations(
                    self.proc.head(pkt.get("head"), t))
            if live:
                self.target.apply(vals, rots)
            if self.receiver.count % 4 == 0:
                top = sorted(vals.items(), key=lambda kv: -kv[1])[:8]
                for (name, bar), (k, x) in zip(self.meters, top):
                    name.setText(k)
                    bar.setValue(int(round(x * 100)))
            if self.take is not None:
                self.take.append((t, vals, rots))
                if len(self.take) % 15 == 0:
                    self._say("Recording  %.1f s" % (self.take[-1][0]
                                                    - self.take[0][0]))
            elif self.receiver.count % 30 == 0 and not self.proc.learning:
                self._say("Live: %.0f fps from the tracker%s." % (
                    pkt.get("fps", 0.0),
                    "" if self.proc.neutral else
                    "  (click Calibrate Neutral with a relaxed face)"))

        def _shutdown(self):
            """Everything off: timer, tracker (its camera window too), the
            port, and the face back to neutral. Safe to call twice."""
            self.timer.stop()
            self._save()
            if self.take is not None:
                self.btn_rec.setChecked(False)
            self._on_stop()
            if face_shapes.exists():
                face_shapes.reset()
            try:
                self.target.restore_head()
            except Exception:
                pass
            if self.receiver:
                self.receiver.close()
                self.receiver = None

        def reject(self):
            # Esc: on its own Qt just hides the dialog (no closeEvent), which
            # left the camera running and the face posed.
            self._shutdown()
            QtWidgets.QDialog.reject(self)

        def closeEvent(self, event):
            self._shutdown()
            QtWidgets.QDialog.closeEvent(self, event)

    return FaceCapturePanel


_window = None


def show():
    """Open the Face Capture panel."""
    global _window
    QtCore, QtWidgets, _ = _qt()
    for w in QtWidgets.QApplication.topLevelWidgets():
        if w.objectName() == WINDOW_OBJECT_NAME:
            try:
                w.close()
                w.deleteLater()
            except Exception:
                pass
    _window = _make_panel_class()()
    _window.show()
    _window.raise_()
    return _window
