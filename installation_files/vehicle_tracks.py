"""
===============================================================================
 TYRE TRACKS AND BURNOUTS
===============================================================================

 Two things a driven vehicle should leave behind.

 TRACKS: bake the marks the tyres leave on the ground. A tyre that is
 simply rolling leaves nothing on tarmac, so by default only the DRIVEN
 wheels mark, and only where they are actually SLIDING:

   * sideways, when the back steps out in a drift, a handbrake turn or a
     spin, measured as the angle between where the wheel is pointing and
     where it is really going;
   * or lengthways, when the tyre is turning faster or slower than the
     ground is going by: wheelspin off the line, a burnout, or a locked
     wheel under braking.

 Turn that off (only_slip=False) and every wheel marks everywhere it
 touches, which is what you want on sand, mud or snow.

 Each mark is a flat ribbon of polygons under the contact patch that
 follows wherever the wheel actually went: a drive, a physics sim or
 hand-keyed animation, flat ground or terrain. It stops where the tyre
 leaves the ground and narrows when a bike leans. Ordinary geometry with
 UVs running along its length, so you can shade it however you like.

 BURNOUT: hold it on the brakes and light up the driven wheels. The
 wheels spin far faster than the vehicle moves, the vehicle creeps
 forward, the body squats and lifts its nose, and the marks left behind
 are the wide dark ones a burnout makes.

 Usage in Maya (Python):

     import vehicle_tracks
     from importlib import reload; reload(vehicle_tracks)
     vehicle_tracks.bake_tracks()              # the slides, rear wheels
     vehicle_tracks.bake_tracks(wheels="all", only_slip=False)   # sand
     vehicle_tracks.burnout(frames=45)         # then look at the marks
     vehicle_tracks.clear_tracks()

===============================================================================
"""

import math
import maya.cmds as cmds
import maya.api.OpenMaya as om

import vehicle_rig_builder as vrb


TRACKS_GRP = "TYRE_TRACKS_GRP"
TRACK_SUFFIX = "_tyreTrack_GEO"
MATERIAL = "tyreTrack_MAT"

# How wide a mark is, as a fraction of the tyre's radius. A road tyre is
# about this square-ish; the width can be passed in for anything else.
WIDTH_FRACTION = 0.45
# How far above the ground the ribbon sits, as a fraction of the radius,
# so it doesn't fight the terrain surface in the viewport.
LIFT_FRACTION = 0.01
# A tyre counts as touching when its bottom is within this much of the
# ground (as a fraction of the radius): tyres squash, terrain samples
# wobble, and a mark that flickers off is worse than one that lingers.
CONTACT_TOLERANCE = 0.08
# Rows closer together than this (fraction of the width) are skipped, so
# a parked wheel doesn't stack thousands of degenerate faces.
MIN_STEP_FRACTION = 0.05
# Sliding. SLIP_ANGLE: how far the wheel has to be pointing away from
# where it is really going (degrees) before it counts as sideways. A
# tyre gives up somewhere around 8 to 12 degrees of slip angle, so a
# clean corner stays quiet and a drift lights up.
SLIP_ANGLE = 12.0
# SLIP_RATIO: how far the distance the tyre ROLLED can differ from the
# distance it actually TRAVELLED before it counts as spinning or locked.
# 0.25 = a quarter out.
SLIP_RATIO = 0.25
# Drive Mode records how much the tyres were sliding on
# C_chassis_CTRL.tyreSlip (0 = gripping, 1 = let go). Anything past this
# marks, which is what catches a handbrake turn: the back locks up and
# slides, even while it is still pointing where it is going.
SLIP_ATTR = "C_chassis_CTRL.tyreSlip"
SLIP_RECORDED = 0.3


# =============================================================================
# Reading the rig
# =============================================================================

def track_prefixes(prefixes=None, wheels="driven"):
    """Which wheels lay marks.

    "driven" (the default): the wheels with the power, since those are the
    ones that light up. "all": every wheel on the ground, for sand or mud
    where even a rolling tyre leaves a rut. On a tracked vehicle just the
    front and back road wheel of each side, since the rest of the side
    runs in the same line.
    """
    if prefixes:
        return list(prefixes)
    if wheels == "driven" and not vrb.is_tracked():
        return driven_prefixes("rear")
    wheels = vrb.all_wheel_prefixes()
    if vrb.is_tracked():
        sides = {}
        for p in wheels:
            sides.setdefault(p[0], []).append(p)
        wheels = []
        for side in sorted(sides):
            row = sides[side]
            wheels += [row[0], row[-1]] if len(row) > 1 else row
    return wheels


def ground_under(prefix):
    """The ground height under this wheel right now, in world units: the
    terrain sample the rig's own suspension is using, so tracks land on
    the same surface the tyre is riding."""
    src = "%s_footCenterSrc_ADL.input1" % prefix
    if cmds.objExists(src):
        return cmds.getAttr(src)
    if cmds.objExists(vrb.GROUND_LOC_NAME):
        return cmds.xform(vrb.GROUND_LOC_NAME, q=True, ws=True, t=True)[1]
    return 0.0


def contact_drop(prefix):
    """How far below the hub this wheel meets the ground, in WORLD units."""
    return vrb.contact_radius(prefix) * vrb.rig_scale()


def track_width(prefix, width=None):
    if width:
        return float(width)
    return WIDTH_FRACTION * contact_drop(prefix) * 2.0


def _hub(prefix):
    for name in ("%s_hub_BIND_JNT" % prefix, "%s_wheel_CTRL" % prefix):
        if cmds.objExists(name):
            return name
    return None


def _frame_range(start, end):
    if start is None:
        start = cmds.playbackOptions(q=True, min=True)
    if end is None:
        end = cmds.playbackOptions(q=True, max=True)
    return int(round(start)), int(round(end))


# =============================================================================
# Baking the marks
# =============================================================================

def recorded_slip():
    """What the drive recorded for this frame, or None if the rig has no
    record (hand-keyed animation, an older rig, a physics bake)."""
    if not cmds.objExists(SLIP_ATTR):
        return None
    return cmds.getAttr(SLIP_ATTR)


def wheel_spin(prefix):
    """How far this wheel has turned, in degrees: the odometer's auto-spin
    plus any spin keyed on the control (a burnout)."""
    total = 0.0
    for node in ("%s_spinAuto" % prefix, "%s_wheel_CTRL" % prefix):
        if cmds.objExists(node):
            total += cmds.getAttr(node + ".rotateX")
    return total


def _sample(prefix, drop, tol):
    """This frame's contact patch for one wheel, or None if it is off the
    ground: (centre on the ground, the horizontal axle direction, how far
    the wheel has spun)."""
    hub = _hub(prefix)
    if not hub:
        return None
    m = cmds.xform(hub, q=True, ws=True, m=True)
    pos = m[12:15]
    ground = ground_under(prefix)
    if pos[1] - drop > ground + tol:
        return None                      # airborne
    axis = m[0:3]                        # the axle: across the tyre
    length = math.sqrt(sum(c * c for c in axis))
    if length < 1e-9:
        return None
    axis = [c / length for c in axis]    # unit, whatever the rig scale is
    # Flattened onto the ground WITHOUT re-normalising: a leaned wheel is
    # riding its edge, so its axle only reaches cos(lean) across, and the
    # mark it leaves is that much narrower.
    flat = (axis[0], 0.0, axis[2])
    if math.hypot(flat[0], flat[2]) < 1e-3:
        return None                      # axle pointing straight up
    return (pos[0], ground, pos[2]), flat, wheel_spin(prefix)


def slipping(prev, now, radius, slip_angle=SLIP_ANGLE,
             slip_ratio=SLIP_RATIO):
    """Is this wheel sliding between these two samples, and how much?

    Two ways a tyre marks the road:
      sideways  the angle between the way the wheel points and the way it
                is really going (a drift, a spin, a handbrake turn);
      lengthways  the distance it ROLLED against the distance it actually
                travelled (wheelspin, a burnout, a locked wheel).

    Returns 0.0 when it is just rolling, or how far past the threshold it
    is (1.0 = at the threshold, higher = more violent).
    """
    (p_centre, _p_axis, p_spin) = prev
    (centre, axis, spin) = now
    travelled = _dist(p_centre, centre)
    rolled = abs(math.radians(spin - p_spin)) * radius
    worst = 0.0
    # Lengthways: rolling and going are meant to match.
    biggest = max(travelled, rolled)
    if biggest > 1e-4:
        ratio = abs(rolled - travelled) / biggest
        worst = max(worst, ratio / max(slip_ratio, 1e-6))
    # Sideways: only meaningful once it has actually moved somewhere.
    if travelled > 1e-3:
        vdir = [(c - p) / travelled for c, p in zip(centre, p_centre)]
        fwd = (axis[2], 0.0, -axis[0])   # across the axle = rolling line
        n = math.hypot(fwd[0], fwd[2])
        if n > 1e-6:
            fwd = (fwd[0] / n, 0.0, fwd[2] / n)
            dot = abs(vdir[0] * fwd[0] + vdir[2] * fwd[2])
            angle = math.degrees(math.acos(max(0.0, min(1.0, dot))))
            worst = max(worst, angle / max(slip_angle, 1e-6))
    return worst if worst >= 1.0 else 0.0


def _rows(prefix, start, end, step, drop, tol, min_step, radius,
          only_slip=True, slip_angle=SLIP_ANGLE, slip_ratio=SLIP_RATIO):
    """Walk the frame range and collect the rows of each mark: one list
    per unbroken stretch where this wheel was down AND (if only_slip)
    sliding. Lifting off the ground or gripping again ends a mark."""
    segments, current, last, prev = [], [], None, None
    f = start
    while f <= end + 1e-6:
        cmds.currentTime(f, edit=True)
        hit = _sample(prefix, drop, tol)
        if hit is None:                              # airborne
            if len(current) > 1:
                segments.append(current)
            current, last, prev = [], None, None
            f += step
            continue
        centre, axis, _spin = hit
        mark = True
        if only_slip:
            told = recorded_slip()
            mark = bool(told is not None and told >= SLIP_RECORDED)
            if not mark:
                mark = bool(prev and slipping(prev, hit, radius, slip_angle,
                                              slip_ratio))
        prev = hit
        if not mark:                                 # gripping: no mark
            if len(current) > 1:
                segments.append(current)
            current, last = [], None
            f += step
            continue
        # A slide is short, and a wheel spinning on the spot barely moves
        # at all, so rows may sit much closer together than on a long
        # rolling bake. The floor only exists to stop zero-area faces.
        gap = min_step if not only_slip else max(1e-4, min_step * 0.2)
        if last is None or _dist(centre, last) >= gap:
            current.append((centre, axis))
            last = centre
        f += step
    if len(current) > 1:
        segments.append(current)
    return segments


def _dist(a, b):
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def _build_mesh(name, segments, half, lift):
    """One mesh from the collected rows: a ribbon per segment, UVs running
    along its length."""
    pts, counts, connects, us, vs = [], [], [], [], []
    for seg in segments:
        base = len(pts)
        run = 0.0
        prev = None
        for centre, axis in seg:
            left = om.MPoint(centre[0] + axis[0] * half, centre[1] + lift,
                             centre[2] + axis[2] * half)
            right = om.MPoint(centre[0] - axis[0] * half, centre[1] + lift,
                              centre[2] - axis[2] * half)
            if prev is not None:
                run += _dist(centre, prev)
            prev = centre
            pts += [left, right]
            u = run / max(2.0 * half, 1e-6)
            us += [u, u]
            vs += [0.0, 1.0]
        for i in range(len(seg) - 1):
            a = base + 2 * i
            counts.append(4)
            connects += [a, a + 1, a + 3, a + 2]
    if not counts:
        return None
    mfn = om.MFnMesh()
    obj = mfn.create(pts, counts, connects)
    mfn.setUVs(us, vs)
    mfn.assignUVs(counts, connects)
    node = cmds.rename(om.MFnDagNode(obj).fullPathName(), name)
    cmds.sets(node, e=True, fe=_material())
    return node


def _material():
    """A plain dark shader for the marks, made once."""
    sg = MATERIAL + "SG"
    if not cmds.objExists(sg):
        shader = cmds.shadingNode("lambert", asShader=True, n=MATERIAL)
        cmds.setAttr(shader + ".color", 0.05, 0.045, 0.04, type="double3")
        cmds.setAttr(shader + ".transparency", 0.35, 0.35, 0.35,
                     type="double3")
        sg = cmds.sets(renderable=True, noSurfaceShader=True, empty=True,
                       n=sg)
        cmds.connectAttr(shader + ".outColor", sg + ".surfaceShader",
                         f=True)
    return sg


def bake_tracks(prefixes=None, start=None, end=None, step=1.0, width=None,
                lift=None, tag=None, keep=True, wheels="driven",
                only_slip=True, slip_angle=SLIP_ANGLE,
                slip_ratio=SLIP_RATIO):
    """Lay the marks the wheels leave over a frame range.

    wheels:    "driven" (the default: the wheels with the power, since
               those are the ones that light up) or "all".
    only_slip: mark only where the tyre is actually SLIDING, sideways or
               lengthways (the default). False marks everywhere it
               touches, which is what sand, mud and snow want.
    prefixes:  name the wheels yourself, overriding `wheels`.
    step:      frames between samples. 1 = every frame; 2 is usually
               plenty and half the polygons.
    width:     mark width in world units (default: from the tyre).
    tag:       suffix for the mesh names, so a second bake (a burnout,
               say) sits beside the first instead of replacing it.
    keep:      leave marks from earlier bakes in place.
    slip_angle / slip_ratio: how far sideways, and how far out between
               rolling and going, counts as sliding.

    Returns the meshes it made.
    """
    if not cmds.objExists("C_chassis_CTRL"):
        cmds.warning("[tracks] No vehicle rig in the scene.")
        return []
    prefixes = track_prefixes(prefixes, wheels)
    if not prefixes:
        cmds.warning("[tracks] This rig has no wheels to lay marks with.")
        return []
    start, end = _frame_range(start, end)
    if end <= start:
        cmds.warning("[tracks] Nothing to bake: the range is empty.")
        return []
    if not keep:
        clear_tracks()
    now = cmds.currentTime(q=True)
    made = []
    try:
        for prefix in prefixes:
            drop = contact_drop(prefix)
            half = 0.5 * track_width(prefix, width)
            tol = CONTACT_TOLERANCE * drop
            rise = LIFT_FRACTION * drop if lift is None else float(lift)
            segments = _rows(prefix, start, end, step, drop, tol,
                             MIN_STEP_FRACTION * 2.0 * half, drop,
                             only_slip, slip_angle, slip_ratio)
            if not segments:
                continue
            name = prefix + TRACK_SUFFIX + (tag or "")
            name = _unique(name)
            mesh = _build_mesh(name, segments, half, rise)
            if mesh:
                made.append(mesh)
    finally:
        cmds.currentTime(now, edit=True)
    if not made:
        cmds.warning(
            "[tracks] Nothing to lay over frames %d to %d: %s"
            % (start, end,
               "no wheel slid, they gripped the whole way. Drift, pull the "
               "handbrake, burn out or spin them up, or untick Only when "
               "sliding to mark everywhere they touch."
               if only_slip else "no wheel touched the ground."))
        return []
    if not cmds.objExists(TRACKS_GRP):
        cmds.group(em=True, n=TRACKS_GRP)
    cmds.parent(made, TRACKS_GRP)
    cmds.select(cl=True)
    print("[tracks] Laid %d mark(s) over frames %d to %d."
          % (len(made), start, end))
    return made


def _unique(name):
    if not cmds.objExists(name):
        return name
    i = 1
    while cmds.objExists("%s%d" % (name, i)):
        i += 1
    return "%s%d" % (name, i)


def has_tracks():
    return cmds.objExists(TRACKS_GRP)


def clear_tracks():
    """Remove every baked mark."""
    if cmds.objExists(TRACKS_GRP):
        cmds.delete(TRACKS_GRP)
        print("[tracks] Cleared.")
        return True
    return False


# =============================================================================
# Burnout
# =============================================================================

# Which wheels get the power.
DRIVEN = ("rear", "front", "all")


def driven_prefixes(which="rear"):
    """The wheels a burnout spins. On a bike that is the back wheel; on a
    car the back axle; 'all' is four-wheel drive."""
    wheels = vrb.scene_wheel_prefixes()
    if not wheels:
        return []
    if which == "all" or vrb.is_tracked():
        return wheels
    axle = "B" if which == "rear" else "F"
    picked = [p for p in wheels if p[1:] == axle]
    return picked or wheels


def burnout(frames=45, which="rear", spin=2400.0, creep=45.0, squat=2.5,
            start=None, marks=True, step=1.0):
    """Light up the driven wheels.

    The wheels spin far faster than the vehicle moves (that IS a burnout:
    the tyre is turning, the ground is not going by), the vehicle creeps
    forward, the body squats onto its back wheels and the marks left
    behind are wide and dark.

    frames: how long it lasts.
    which:  'rear' (default), 'front' or 'all'.
    spin:   degrees a second the driven wheels spin up to.
    creep:  units a second the vehicle crawls forward.
    squat:  degrees the nose lifts.
    marks:  bake the skid marks when it is done.

    Returns (start frame, end frame).
    """
    if not cmds.objExists("C_chassis_CTRL"):
        cmds.warning("[burnout] No vehicle rig in the scene.")
        return None
    wheels = driven_prefixes(which)
    ctrls = [p + "_wheel_CTRL" for p in wheels
             if cmds.objExists(p + "_wheel_CTRL")]
    if not ctrls:
        cmds.warning("[burnout] No driven wheels found.")
        return None
    fps = _fps()
    start = int(round(cmds.currentTime(q=True) if start is None else start))
    end = start + int(max(2, frames))
    root = "C_global_CTRL"
    odo = "C_chassis_CTRL.odometer"
    body = "C_body_OSC" if cmds.objExists("C_body_OSC") else None
    scale = vrb.rig_scale()

    heading = math.radians(cmds.getAttr(root + ".rotateY"))
    fwd = (math.sin(heading), math.cos(heading))
    x0 = cmds.getAttr(root + ".translateX")
    z0 = cmds.getAttr(root + ".translateZ")
    odo0 = cmds.getAttr(odo)
    spin0 = {c: cmds.getAttr(c + ".rotateX") for c in ctrls}
    body0 = cmds.getAttr(body + ".rotateX") if body else 0.0

    angle = 0.0
    travelled = 0.0
    plugs = [root + ".translateX", root + ".translateZ", odo]
    plugs += [c + ".rotateX" for c in ctrls]
    if body:
        plugs.append(body + ".rotateX")
    for f in range(start, end + 1):
        t = (f - start) / float(max(1, end - start))       # 0 to 1
        dt = 1.0 / fps
        # The wheels light up fast and stay lit; the vehicle only starts
        # to go anywhere as they finally hook up near the end.
        spin_now = spin * min(1.0, t * 4.0)
        grip = t * t                                      # hooking up
        angle += spin_now * dt
        travelled += creep * (0.3 + 1.7 * grip) * dt
        cmds.setAttr(root + ".translateX", x0 + fwd[0] * travelled)
        cmds.setAttr(root + ".translateZ", z0 + fwd[1] * travelled)
        # The odometer rolls the wheels that AREN'T spinning by how far
        # the vehicle actually went.
        cmds.setAttr(odo, odo0 + travelled / max(scale, 1e-6))
        for c in ctrls:
            cmds.setAttr(c + ".rotateX", spin0[c] + angle)
        if body:
            # Squat onto the back wheels, then settle as it hooks up.
            cmds.setAttr(body + ".rotateX",
                         body0 - squat * math.sin(math.pi * min(1.0, t * 1.2)))
        cmds.setKeyframe(plugs, t=f)
    cmds.currentTime(end, edit=True)
    made = []
    if marks:
        made = bake_tracks(prefixes=wheels, start=start, end=end, step=step,
                           width=1.35 * track_width(wheels[0]),
                           tag="_burnout", only_slip=False)
    print("[burnout] Frames %d to %d on %s, %d mark(s)."
          % (start, end, ", ".join(wheels), len(made)))
    return start, end


def burnout_channels(which="rear"):
    """The channels a burnout keys, so they can be cleared again."""
    chans = ["C_global_CTRL.translateX", "C_global_CTRL.translateZ",
             "C_chassis_CTRL.odometer"]
    chans += [p + "_wheel_CTRL.rotateX" for p in driven_prefixes(which)
              if cmds.objExists(p + "_wheel_CTRL")]
    if cmds.objExists("C_body_OSC"):
        chans.append("C_body_OSC.rotateX")
    return [c for c in chans if cmds.objExists(c.split(".")[0])]


def clear_burnout(which="rear"):
    """Take the burnout keys off again (the marks stay until cleared)."""
    n = 0
    for chan in burnout_channels(which):
        if cmds.keyframe(chan, q=True, keyframeCount=True):
            cmds.cutKey(chan, cl=True)
            n += 1
    for p in driven_prefixes(which):
        ctrl = p + "_wheel_CTRL"
        if cmds.objExists(ctrl):
            cmds.setAttr(ctrl + ".rotateX", 0)
    print("[burnout] Cleared %d channel(s)." % n)
    return n


def _fps():
    unit = cmds.currentUnit(q=True, time=True)
    table = {"game": 15.0, "film": 24.0, "pal": 25.0, "ntsc": 30.0,
             "show": 48.0, "palf": 50.0, "ntscf": 60.0}
    if unit in table:
        return table[unit]
    if unit.endswith("fps"):
        try:
            return float(unit[:-3])
        except ValueError:
            pass
    return 24.0
