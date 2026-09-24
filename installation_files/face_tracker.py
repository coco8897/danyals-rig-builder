"""
===============================================================================
 FACE TRACKER - webcam / phone camera face tracking for Maya (MediaPipe)
===============================================================================

 A small app that runs OUTSIDE Maya, in a normal Python (3.9 to 3.13) with
 MediaPipe installed:

     python -m pip install mediapipe

 It watches a camera, finds your face and works out the 52 face shapes
 (eyeBlinkLeft, jawOpen, mouthSmileLeft ...) plus the head rotation, then
 sends them to Maya's Face Capture panel over your own computer (UDP on
 127.0.0.1, nothing leaves the machine). The Face Capture panel can start it
 for you; you can also run it by hand:

     python face_tracker.py                      # PC webcam 0
     python face_tracker.py --camera 1           # another camera (DroidCam,
                                                 #  Iriun: phone as a webcam)
     python face_tracker.py --url http://192.168.1.20:8080/video
                                                 # phone IP camera app stream
     python face_tracker.py --video take.mp4 --csv take.csv
                                                 # a video file -> a CSV the
                                                 #  panel imports (no Maya
                                                 #  needed while it runs)
     python face_tracker.py --list               # which cameras are there

 In the preview window: Q or Esc quits, M flips the preview (display only).

 The face model (face_landmarker.task, about 4 MB, Apache 2.0, by Google) is
 downloaded next to this file the first time, if it isn't there.

 Left / Right in what's sent are the PERFORMER's own left and right, the
 same as iPhone ARKit (checked: looking to your own left reads eyeLookOutLeft
 + eyeLookInRight). --swap flips them if a mirrored camera feed needs it.
===============================================================================
"""

import argparse
import csv
import json
import math
import os
import socket
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
MODEL_NAME = "face_landmarker.task"
MODEL_URL = ("https://storage.googleapis.com/mediapipe-models/face_landmarker/"
             "face_landmarker/float16/1/face_landmarker.task")
DEFAULT_PORT = 54321
PROTOCOL = 1

SHAPES = (
    "eyeBlinkLeft", "eyeLookDownLeft", "eyeLookInLeft", "eyeLookOutLeft",
    "eyeLookUpLeft", "eyeSquintLeft", "eyeWideLeft",
    "eyeBlinkRight", "eyeLookDownRight", "eyeLookInRight", "eyeLookOutRight",
    "eyeLookUpRight", "eyeSquintRight", "eyeWideRight",
    "jawForward", "jawLeft", "jawRight", "jawOpen",
    "mouthClose", "mouthFunnel", "mouthPucker", "mouthLeft", "mouthRight",
    "mouthSmileLeft", "mouthSmileRight", "mouthFrownLeft", "mouthFrownRight",
    "mouthDimpleLeft", "mouthDimpleRight", "mouthStretchLeft",
    "mouthStretchRight", "mouthRollLower", "mouthRollUpper",
    "mouthShrugLower", "mouthShrugUpper", "mouthPressLeft", "mouthPressRight",
    "mouthLowerDownLeft", "mouthLowerDownRight", "mouthUpperUpLeft",
    "mouthUpperUpRight",
    "browDownLeft", "browDownRight", "browInnerUp", "browOuterUpLeft",
    "browOuterUpRight",
    "cheekPuff", "cheekSquintLeft", "cheekSquintRight",
    "noseSneerLeft", "noseSneerRight",
    "tongueOut",
)


# =============================================================================
# Pure helpers (no MediaPipe / OpenCV needed: Maya's tests use these)
# =============================================================================

def swap_name(name):
    """eyeBlinkLeft <-> eyeBlinkRight, mouthLeft <-> mouthRight."""
    if name.endswith("Left"):
        return name[:-4] + "Right"
    if name.endswith("Right"):
        return name[:-5] + "Left"
    return name


def swap_sides(shapes):
    return {swap_name(k): v for k, v in shapes.items()}


def matrix_to_euler(m):
    """Head rotation (degrees, x / y / z) from MediaPipe's 4x4 face transform
    (camera space: x right in the picture, y up, z toward the camera). The
    angles are in the same frame as a Maya character facing +Z at the camera:
    +y turns the head to the performer's left, -x tilts it up, -z leans it to
    the performer's left shoulder. Rotate order xyz."""
    r = [[float(m[i][j]) for j in range(3)] for i in range(3)]
    # normalise the columns (the transform carries scale)
    for j in range(3):
        n = math.sqrt(sum(r[i][j] ** 2 for i in range(3))) or 1.0
        for i in range(3):
            r[i][j] /= n
    ry = math.asin(max(-1.0, min(1.0, -r[2][0])))
    rx = math.atan2(r[2][1], r[2][2])
    rz = math.atan2(r[1][0], r[0][0])
    return [math.degrees(rx), math.degrees(ry), math.degrees(rz)]


def make_packet(frame, t, shapes, head=None, found=True, fps=0.0):
    """One UDP datagram (JSON, well under 2 KB)."""
    return json.dumps({
        "v": PROTOCOL, "frame": int(frame), "t": round(float(t), 4),
        "found": bool(found), "fps": round(float(fps), 1),
        "shapes": {k: round(float(v), 4) for k, v in shapes.items()},
        "head": [round(float(a), 3) for a in head] if head else None,
    }, separators=(",", ":")).encode("utf-8")


def csv_header():
    """Live Link Face style columns (so both import the same way)."""
    return (["Timecode", "BlendShapeCount"]
            + [s[0].upper() + s[1:] for s in SHAPES]
            + ["HeadYaw", "HeadPitch", "HeadRoll"])


def timecode(t, fps):
    frames = t * fps
    whole = int(frames)
    s_total = int(whole // fps)
    h, rem = divmod(s_total, 3600)
    mnt, sec = divmod(rem, 60)
    fr = whole - int(s_total * fps)
    return "%02d:%02d:%02d:%02d.%03d" % (h, mnt, sec, fr,
                                         int((frames - whole) * 1000))


def csv_row(t, fps, shapes, head):
    """HeadYaw / Pitch / Roll in radians (yaw = +y, pitch = +x, roll = +z of
    matrix_to_euler)."""
    head = head or (0.0, 0.0, 0.0)
    return ([timecode(t, fps), len(SHAPES)]
            + ["%.4f" % shapes.get(s, 0.0) for s in SHAPES]
            + ["%.5f" % math.radians(head[1]), "%.5f" % math.radians(head[0]),
               "%.5f" % math.radians(head[2])])


def model_path(path=None):
    """The face model, downloaded next to this file on first use."""
    path = path or os.path.join(HERE, MODEL_NAME)
    if os.path.isfile(path) and os.path.getsize(path) > 100000:
        return path
    print("Downloading the MediaPipe face model (about 4 MB) from\n  %s"
          % MODEL_URL)
    import urllib.request
    tmp = path + ".part"
    urllib.request.urlretrieve(MODEL_URL, tmp)
    os.replace(tmp, path)
    print("Saved %s" % path)
    return path


# =============================================================================
# Tracking
# =============================================================================

def open_source(args):
    import cv2
    if args.video:
        cap = cv2.VideoCapture(args.video)
    elif args.url:
        cap = cv2.VideoCapture(args.url)
    else:
        backend = cv2.CAP_DSHOW if sys.platform == "win32" else cv2.CAP_ANY
        cap = cv2.VideoCapture(args.camera, backend)
        if not cap.isOpened():
            cap = cv2.VideoCapture(args.camera)
        if args.width:
            cap.set(cv2.CAP_PROP_FRAME_WIDTH, args.width)
            cap.set(cv2.CAP_PROP_FRAME_HEIGHT, args.height)
        cap.set(cv2.CAP_PROP_FPS, 30)
    if not cap.isOpened():
        what = args.video or args.url or "camera %d" % args.camera
        raise SystemExit("Could not open %s." % what)
    return cap


def list_cameras(n=6):
    import cv2
    found = []
    for i in range(n):
        backend = cv2.CAP_DSHOW if sys.platform == "win32" else cv2.CAP_ANY
        cap = cv2.VideoCapture(i, backend)
        if cap.isOpened():
            ok, frame = cap.read()
            if ok:
                found.append((i, frame.shape[1], frame.shape[0]))
        cap.release()
    for i, w, h in found:
        print("camera %d: %dx%d" % (i, w, h))
    if not found:
        print("No cameras found.")
    return found


def create_landmarker(path):
    from mediapipe.tasks.python import vision
    from mediapipe.tasks.python.core.base_options import BaseOptions
    opts = vision.FaceLandmarkerOptions(
        base_options=BaseOptions(model_asset_path=path),
        running_mode=vision.RunningMode.VIDEO,
        num_faces=1,
        min_face_detection_confidence=0.5,
        min_face_presence_confidence=0.5,
        min_tracking_confidence=0.5,
        output_face_blendshapes=True,
        output_facial_transformation_matrixes=True)
    return vision.FaceLandmarker.create_from_options(opts)


def read_result(result, swap=False):
    """(shapes dict, head euler or None, landmarks or None) from a
    FaceLandmarkerResult."""
    if not result.face_blendshapes:
        return {}, None, None
    shapes = {c.category_name: float(c.score)
              for c in result.face_blendshapes[0]
              if c.category_name in SHAPES}
    if swap:
        shapes = swap_sides(shapes)
    head = None
    if result.facial_transformation_matrixes:
        head = matrix_to_euler(result.facial_transformation_matrixes[0])
    marks = result.face_landmarks[0] if result.face_landmarks else None
    return shapes, head, marks


_BARS = ("jawOpen", "eyeBlinkLeft", "eyeBlinkRight", "mouthSmileLeft",
         "mouthSmileRight", "browInnerUp", "mouthPucker", "mouthFunnel")


def draw_preview(frame, marks, shapes, fps, flip, sending):
    import cv2
    h, w = frame.shape[:2]
    if marks:
        for i, p in enumerate(marks):
            if i % 2 == 0:
                cv2.circle(frame, (int(p.x * w), int(p.y * h)), 1,
                           (120, 230, 255), -1)
    if flip:
        frame = cv2.flip(frame, 1)
    cv2.rectangle(frame, (0, 0), (w, 26), (24, 24, 28), -1)
    status = ("face found" if marks else "no face") + \
        ("  |  sending to Maya" if sending else "")
    cv2.putText(frame, "%4.1f fps  %s   (Q quits, M flips)" % (fps, status),
                (8, 18), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (230, 230, 230), 1,
                cv2.LINE_AA)
    for i, name in enumerate(_BARS):
        v = max(0.0, min(1.0, shapes.get(name, 0.0)))
        y = 40 + i * 18
        cv2.putText(frame, name, (8, y + 10), cv2.FONT_HERSHEY_SIMPLEX, 0.4,
                    (230, 230, 230), 1, cv2.LINE_AA)
        cv2.rectangle(frame, (130, y), (230, y + 10), (60, 60, 66), -1)
        cv2.rectangle(frame, (130, y), (130 + int(100 * v), y + 10),
                      (0, 200, 255), -1)
    return frame


def run(args):
    import cv2
    import mediapipe as mp
    if args.list:
        list_cameras()
        return 0
    landmarker = create_landmarker(model_path(args.model))
    cap = open_source(args)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    addr = (args.host, args.port)
    # Maya's Stop (or closing the panel) says "quit" here, so the camera
    # never keeps running after the panel is gone.
    ctrl = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    ctrl.setblocking(False)
    try:
        ctrl.bind((args.host, args.port + 1))
    except OSError:
        ctrl.close()
        ctrl = None
    writer, csv_file = None, None
    if args.csv:
        csv_file = open(args.csv, "w", newline="", encoding="utf-8")
        writer = csv.writer(csv_file)
        writer.writerow(csv_header())
    src_fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
    if not (1.0 < src_fps < 241.0):
        src_fps = 30.0
    offline = bool(args.video)
    flip = not offline and not args.url
    title = "Face Tracker (Danyal's Rig Builder)"
    t0 = time.perf_counter()
    last, fps, frame_no, last_ms = t0, 0.0, 0, -1
    print("Tracking %s -> Maya on %s:%d. Q quits."
          % (args.video or args.url or "camera %d" % args.camera,
             args.host, args.port))
    try:
        while True:
            if ctrl is not None:
                try:
                    if ctrl.recv(64).strip().lower().startswith(b"quit"):
                        print("Maya asked the tracker to stop.")
                        break
                except (BlockingIOError, OSError):
                    pass
            ok, frame = cap.read()
            if not ok:
                if offline:
                    break
                time.sleep(0.01)
                continue
            if offline:
                t = frame_no / src_fps
            else:
                t = time.perf_counter() - t0
            ms = int(t * 1000)
            if ms <= last_ms:
                ms = last_ms + 1              # timestamps must increase
            last_ms = ms
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            image = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
            result = landmarker.detect_for_video(image, ms)
            shapes, head, marks = read_result(result, swap=args.swap)
            now = time.perf_counter()
            fps = 0.9 * fps + 0.1 * (1.0 / max(1e-4, now - last))
            last = now
            if not args.no_send:
                try:
                    sock.sendto(make_packet(frame_no, t, shapes, head,
                                            found=bool(marks), fps=fps), addr)
                except OSError:
                    pass
            if writer is not None and shapes:
                writer.writerow(csv_row(t, src_fps, shapes, head))
            frame_no += 1
            if not args.no_preview:
                view = draw_preview(frame, marks, shapes, fps, flip,
                                    not args.no_send)
                cv2.imshow(title, view)
                key = cv2.waitKey(1) & 0xFF
                if key in (ord("q"), 27):
                    break
                if key == ord("m"):
                    flip = not flip
                try:
                    if cv2.getWindowProperty(title,
                                             cv2.WND_PROP_VISIBLE) < 1:
                        break
                except cv2.error:
                    break
            elif offline and frame_no % 100 == 0:
                print("  %d frames" % frame_no)
    except KeyboardInterrupt:
        pass
    finally:
        cap.release()
        landmarker.close()
        if ctrl is not None:
            ctrl.close()
        if csv_file:
            csv_file.close()
            print("Wrote %s (%d frames)" % (args.csv, frame_no))
        try:
            cv2.destroyAllWindows()
        except Exception:
            pass
    return 0


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="Webcam / phone face tracking for Maya's Face Capture "
                    "panel (MediaPipe).")
    p.add_argument("--camera", type=int, default=0,
                   help="camera number (0 = the first webcam)")
    p.add_argument("--url", default="",
                   help="a phone / IP camera stream URL instead")
    p.add_argument("--video", default="",
                   help="track a video file instead (as fast as it can)")
    p.add_argument("--csv", default="",
                   help="also write the shapes to this CSV file")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=DEFAULT_PORT)
    p.add_argument("--width", type=int, default=1280)
    p.add_argument("--height", type=int, default=720)
    p.add_argument("--model", default="", help="face_landmarker.task path")
    p.add_argument("--swap", action="store_true",
                   help="swap Left / Right (for a mirrored camera feed)")
    p.add_argument("--no-preview", action="store_true")
    p.add_argument("--no-send", action="store_true",
                   help="don't send to Maya (use with --csv)")
    p.add_argument("--list", action="store_true", help="list cameras")
    return p.parse_args(argv)


if __name__ == "__main__":
    sys.exit(run(parse_args()))
