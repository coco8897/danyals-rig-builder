"""
===============================================================================
 RIG VALIDATE — ship-readiness health check
===============================================================================

 A pre-export pass over the scene that flags the things that bite you AFTER
 you hand a rig off: controls left off their rest pose, unbound or
 double-bound meshes, leftover construction history, duplicate names, junk
 nodes that bloat the file, scaled joints, and dependency cycles.

     import rig_validate as rv
     rv.validate()              # print a scannable report, return the issues
     rv.validate(fix=True)      # same, then auto-clean the safe stuff

 Each issue has a level (ERROR / WARN / INFO), a one-line title, the offending
 nodes, and whether an auto-fix exists. `fix=True` only ever touches the safe
 ones (junk nodes, unused nodes, empty layers) — never your geometry or
 weights.
===============================================================================
"""
from __future__ import print_function

import maya.cmds as cmds
import maya.mel as mel

ERROR, WARN, INFO = "ERROR", "WARN", "INFO"
_ORDER = {ERROR: 0, WARN: 1, INFO: 2}


class Issue(object):
    def __init__(self, level, title, nodes=None, hint="", fixable=False):
        self.level = level
        self.title = title
        self.nodes = nodes or []
        self.hint = hint
        self.fixable = fixable

    def __repr__(self):
        return "<%s %s (%d)>" % (self.level, self.title, len(self.nodes))


def _close(vals, target, tol=1e-3):
    return all(abs(v - target) <= tol for v in vals)


# -- individual checks --------------------------------------------------------
def check_dirty_controls():
    """Controls (*_CTRL) should sit at their rest pose: zero translate/rotate,
    unit scale, custom keyable attrs at default. A dirty rest pose is the #1
    thing that ships broken."""
    dirty = []
    for ctrl in cmds.ls("*_CTRL", type="transform"):
        bad = False
        for at, dv in (("translate", 0.0), ("rotate", 0.0), ("scale", 1.0)):
            try:
                if not _close(cmds.getAttr("%s.%s" % (ctrl, at))[0], dv):
                    bad = True
            except Exception:
                pass
        for at in cmds.listAttr(ctrl, ud=True, k=True) or []:
            plug = "%s.%s" % (ctrl, at)
            try:
                if cmds.getAttr(plug, type=True) not in ("double", "float",
                                                         "doubleLinear",
                                                         "doubleAngle", "bool"):
                    continue
                dft = (cmds.attributeQuery(at, node=ctrl, ld=True) or [0])[0]
                if abs(cmds.getAttr(plug) - dft) > 1e-3:
                    bad = True
            except Exception:
                pass
        if bad:
            dirty.append(ctrl)
    if dirty:
        return [Issue(WARN, "%d control(s) not at rest pose" % len(dirty),
                      dirty, "select them and zero the channels (or "
                      "rig_pose tools) before export")]
    return []


def check_skinnable_geo():
    """Every render mesh should be bound exactly once. Flag unbound meshes and
    meshes carrying more than one skinCluster."""
    out, unbound, multi = [], [], []
    for shp in cmds.ls(type="mesh", ni=True) or []:
        tr = (cmds.listRelatives(shp, p=True, f=True) or [None])[0]
        # skip control / guide / curve display meshes
        if tr and any(t in tr for t in ("_CTRL", "_GUIDE", "Output")):
            continue
        hist = cmds.listHistory(shp, pdo=True) or []
        scs = [h for h in hist if cmds.nodeType(h) == "skinCluster"]
        if not scs:
            unbound.append(shp)
        elif len(scs) > 1:
            multi.append(shp)
    if unbound:
        out.append(Issue(WARN, "%d mesh(es) not bound to the skeleton"
                         % len(unbound), unbound,
                         "bind them, or ignore if they're props"))
    if multi:
        out.append(Issue(ERROR, "%d mesh(es) have MULTIPLE skinClusters"
                         % len(multi), multi,
                         "delete the extra skinCluster — only one is allowed"))
    return out


def check_history():
    """Construction history (poly* / tweak) left upstream of a bind bloats the
    file and can fight the deformation."""
    dirty = []
    for shp in cmds.ls(type="mesh", ni=True) or []:
        tr = (cmds.listRelatives(shp, p=True) or [None])[0]
        if tr and any(t in tr for t in ("_CTRL", "_GUIDE")):
            continue
        hist = cmds.listHistory(shp, pdo=True) or []
        junk = [h for h in hist if cmds.nodeType(h) in
                ("polyTweak", "tweak", "polySoftEdge", "polyNormalPerVertex")]
        if junk:
            dirty.append(shp)
    if dirty:
        return [Issue(WARN, "%d mesh(es) carry leftover history" % len(dirty),
                      dirty, "Edit > Delete by Type > Non-Deformer History")]
    return []


def check_duplicate_names():
    """Non-unique short names (Maya shows them with a | path) break every tool
    that looks a node up by name — including this rig's own scripts."""
    seen, dupes = {}, set()
    for n in cmds.ls(dag=True, long=True) or []:
        short = n.split("|")[-1]
        if short in seen:
            dupes.add(short)
        seen[short] = True
    if dupes:
        return [Issue(ERROR, "%d duplicated node name(s)" % len(dupes),
                      sorted(dupes), "rename so every node is unique")]
    return []


def check_scaled_joints():
    """Joints with non-unit scale skew skin weights and FBX export."""
    bad = [j for j in cmds.ls(type="joint")
           if not _close(cmds.getAttr("%s.scale" % j)[0], 1.0, 1e-3)]
    if bad:
        return [Issue(WARN, "%d joint(s) have non-unit scale" % len(bad), bad,
                      "freeze/reset joint scale to 1,1,1")]
    return []


def check_junk_nodes():
    """Unknown nodes (dead plug-in data) and Turtle leftovers bloat the file
    and throw load warnings on the next machine. These are safe to delete."""
    junk = list(cmds.ls(type="unknown") or [])
    for t in ("TurtleDefaultBakeLayer", "TurtleBakeLayerManager",
              "TurtleRenderOptions", "TurtleUIOptions"):
        junk += list(cmds.ls(type=t) or [])
    if junk:
        return [Issue(WARN, "%d junk / unknown node(s)" % len(junk), junk,
                      "safe to delete (run with fix=True)", fixable=True)]
    return []


def check_cycles():
    """Evaluation cycles make the rig jitter or evaluate differently per
    machine."""
    try:
        nodes = cmds.cycleCheck(all=True, list=True) or []
    except Exception:
        nodes = []
    nodes = sorted({n.split(".")[0] for n in nodes})
    if nodes:
        return [Issue(ERROR, "dependency cycle through %d node(s)"
                      % len(nodes), nodes, "break the loop (often a "
                      "double constraint or aim-at-self)")]
    return []


def check_rig_present():
    """Sanity: is there actually a rig here?"""
    if not cmds.ls("*_BIND_JNT", type="joint"):
        return [Issue(INFO, "no *_BIND_JNT joints found — nothing to validate",
                      [], "build a rig first")]
    return []


_CHECKS = [check_rig_present, check_duplicate_names, check_cycles,
           check_skinnable_geo, check_dirty_controls, check_history,
           check_scaled_joints, check_junk_nodes]


# -- safe auto-fix ------------------------------------------------------------
def _fix(issues):
    cleaned = 0
    for iss in issues:
        if not iss.fixable:
            continue
        for n in iss.nodes:
            if cmds.objExists(n):
                try:
                    cmds.lockNode(n, lock=False)
                    cmds.delete(n)
                    cleaned += 1
                except Exception:
                    pass
    try:
        mel.eval("MLdeleteUnused")              # unused shading/utility nodes
    except Exception:
        pass
    for lyr in cmds.ls(type="displayLayer") or []:
        if lyr != "defaultLayer" and not (cmds.editDisplayLayerMembers(
                lyr, q=True, fn=True) or []):
            try:
                cmds.delete(lyr)
                cleaned += 1
            except Exception:
                pass
    return cleaned


# -- entry point --------------------------------------------------------------
def validate(fix=False, verbose=True):
    """Run every check. Returns the list of Issue objects. With fix=True,
    auto-clean the safe ones afterwards and re-scan junk."""
    issues = []
    for chk in _CHECKS:
        try:
            issues += chk()
        except Exception as e:
            issues.append(Issue(WARN, "check %s failed: %s"
                                % (chk.__name__, str(e)[:60])))
    issues.sort(key=lambda i: _ORDER.get(i.level, 9))

    if fix:
        n = _fix(issues)
        if verbose:
            print("[validate] auto-cleaned %d junk node(s)." % n)
        issues = [i for i in issues if not i.fixable]
        issues += check_junk_nodes()       # rescan to confirm clear

    if verbose:
        _report(issues)
    return issues


def _report(issues):
    counts = {ERROR: 0, WARN: 0, INFO: 0}
    for i in issues:
        counts[i.level] = counts.get(i.level, 0) + 1
    bar = "=" * 64
    print("\n" + bar)
    print(" RIG HEALTH CHECK")
    print(bar)
    if not issues:
        print("  All clear -- nothing flagged. Ship it.")
        print(bar + "\n")
        return
    for i in issues:
        tag = {ERROR: "[X]", WARN: "[!]", INFO: "[i]"}[i.level]
        print("  %s %-5s %s" % (tag, i.level, i.title))
        if i.hint:
            print("            -> %s" % i.hint)
        if i.nodes:
            shown = ", ".join(n.split("|")[-1] for n in i.nodes[:6])
            more = "" if len(i.nodes) <= 6 else "  (+%d more)" % (
                len(i.nodes) - 6)
            print("            %s%s" % (shown, more))
    print(bar)
    print("  %d error(s), %d warning(s), %d info" % (counts[ERROR],
          counts[WARN], counts[INFO]))
    if counts[ERROR] == 0:
        print("  No blockers — warnings are judgement calls. Good to ship.")
    else:
        print("  Fix the errors before exporting.")
    print(bar + "\n")
