"""
===============================================================================
 RIG TELEMETRY — anonymous install ID + usage ping (transparent, opt-out)
===============================================================================

 WHAT THIS DOES (full disclosure — also stated in the README):
   * On install, a RANDOM ID (a UUID) is generated and stored locally in
     danyals_rig_builder_id.json in your Maya scripts folder.
   * On install and on the FIRST launch of each version, ONE small ping is
     sent so the author can count installs and see roughly which
     regions / Maya versions the tool is used in.

 WHAT IS SENT — and nothing else:
   * the random install ID (no link to you or your machine name)
   * event name ("install" / "launch")
   * tool version, Maya version, OS name (e.g. "windows")
   * the OS time-zone name (e.g. "Pakistan Standard Time" — a rough,
     anonymous region hint)
 NO user name, NO email, NO scene contents, NO file paths.

 OPT OUT (any one of these):
   * set the environment variable  DRB_NO_TELEMETRY=1
   * or edit danyals_rig_builder_id.json ->  "optOut": true
   * or set TELEMETRY_ENABLED = False below
 The tool works EXACTLY the same with telemetry off.
===============================================================================
"""

import json
import os
import platform
import threading
import uuid

TOOL_VERSION = "1.0.1"
TELEMETRY_ENABLED = True

# The collection endpoint (a URL the author controls — a Google Apps Script
# web app that appends rows to a private sheet). EMPTY = telemetry fully
# disabled, nothing is ever sent.
TELEMETRY_URL = ("https://script.google.com/macros/s/"
                 "AKfycbx24XZMIuh4bnoFKclJ-3W4QKwyqt457yh59K3_jz8H_hzmr"
                 "__QrDtsH-i1bf6oeMb_/exec")

_ID_FILE = "danyals_rig_builder_id.json"


def _id_path():
    """The id file lives in the user Maya scripts dir (beside the tool);
    falls back to the home dir outside Maya."""
    try:
        import maya.cmds as cmds
        return os.path.join(cmds.internalVar(userScriptDir=True), _ID_FILE)
    except Exception:
        return os.path.join(os.path.expanduser("~"), _ID_FILE)


def _load():
    try:
        with open(_id_path()) as f:
            return json.load(f)
    except Exception:
        return {}


def _save(data):
    try:
        with open(_id_path(), "w") as f:
            json.dump(data, f, indent=1)
    except Exception:
        pass


def ensure_install_id():
    """Create (or return) the random install ID."""
    data = _load()
    if not data.get("id"):
        data["id"] = uuid.uuid4().hex
        data.setdefault("optOut", False)
        data.setdefault("pinged", [])
        _save(data)
    return data["id"]


def _enabled():
    if not TELEMETRY_ENABLED or not TELEMETRY_URL:
        return False
    if os.environ.get("DRB_NO_TELEMETRY"):
        return False
    if _load().get("optOut"):
        return False
    return True


def _maya_version():
    try:
        import maya.cmds as cmds
        return cmds.about(version=True)
    except Exception:
        return "unknown"


def _send(payload):
    """Fire-and-forget POST; short timeout, never raises, never blocks Maya."""
    try:
        from urllib import request, parse
        req = request.Request(TELEMETRY_URL,
                              data=parse.urlencode(payload).encode("utf-8"))
        request.urlopen(req, timeout=4).read()
    except Exception:
        pass


def ping(event, once_per_version=False):
    """Send one anonymous event ping (in a background thread). With
    once_per_version=True the event is only ever sent once per tool version
    per machine (used for "launch" so day-to-day work sends nothing)."""
    try:
        install_id = ensure_install_id()
        if not _enabled():
            return False
        data = _load()
        key = "%s@%s" % (event, TOOL_VERSION)
        if once_per_version and key in data.get("pinged", []):
            return False
        import time
        payload = {
            "id": install_id,
            "event": event,
            "version": TOOL_VERSION,
            "maya": _maya_version(),
            "os": platform.system().lower() or "unknown",
            # rough anonymous region hint (no IP lookup needed server-side)
            "tz": (time.tzname[0] if time.tzname else "unknown"),
        }
        threading.Thread(target=_send, args=(payload,), daemon=True).start()
        data.setdefault("pinged", []).append(key)
        _save(data)
        return True
    except Exception:
        return False


def opt_out():
    """Permanently disable telemetry on this machine."""
    data = _load()
    data["optOut"] = True
    _save(data)
    print("[Danyal's Rig Builder] Telemetry disabled on this machine.")
