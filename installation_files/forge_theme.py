"""
===============================================================================
 FORGE THEME - shared visual language for the Forge tool suite
===============================================================================

 One palette, one stylesheet, one accent per tool. Import this instead of
 hand-writing colours so every tool looks like part of the same product:

     import forge_theme as ft

     self.setStyleSheet(ft.style(ft.ACCENT_RIG))
     header.setStyleSheet(ft.title_css(ft.ACCENT_RIG))
     note.setStyleSheet(ft.MUTED_CSS)

 The palette below is the one already used by DamageForge / ForgeUV /
 ShapeForge. Only ACCENT changes per tool, so the family reads as a set while
 each tool stays identifiable at a glance.

 ACCENTS
   DamageForge  #d9a441  amber
   ShapeForge   #e0a24f  orange
   ForgeUV      #4fb8c6  cyan   (the odd one out, kept for continuity)
   Rig Builder  #d9a441  amber

 The suite reads as ONE warm amber brand. Tools are told apart by their
 title and content, not by hue, so new tools should default to the amber
 rather than inventing a colour.
===============================================================================
"""

# --- surfaces ---------------------------------------------------------------
BG = "#1d1f24"      # window background
SURFACE = "#24262d"      # raised: buttons, headers
SUNKEN = "#16181c"      # inset: line edits, combos, spin boxes
BORDER = "#32353e"      # dividers, outlines
BORDER_DIM = "#2a2c33"      # disabled outlines
OFF = "#6a6c74"      # outline of an unticked checkbox / radio button

# --- text -------------------------------------------------------------------
TEXT = "#e8e6e1"      # primary
MUTED = "#9a9890"      # secondary / group titles / hints
DISABLED = "#5c5a54"      # disabled + fine print
ON_ACCENT = "#151310"      # text on top of an accent fill

# --- semantic ---------------------------------------------------------------
OK = "#7fbf8e"      # success / confirmation
WARN = "#d9a441"      # caution / experimental
ERROR = "#d98080"      # failure

# --- per-tool accents -------------------------------------------------------
ACCENT_DAMAGE = "#d9a441"
ACCENT_SHAPE = "#e0a24f"
ACCENT_UV = "#4fb8c6"
ACCENT_RIG = "#d9a441"   # brand amber, matches the rest of the suite

_TEMPLATE = """
QWidget { background:%(bg)s; color:%(text)s;
          font-family:'Segoe UI',sans-serif; font-size:12px; }
QLabel#title { font-weight:700; font-size:15px; color:%(a)s; }
QLabel#subtitle { color:%(muted)s; font-size:11px; }
QLabel#credit { color:%(disabled)s; font-size:10px; }

QGroupBox { border:1px solid %(border)s; border-radius:6px;
            margin-top:12px; padding-top:6px; }
QGroupBox::title { subcontrol-origin:margin; left:8px; padding:0 4px;
                   color:%(muted)s; font-size:10px; letter-spacing:1px; }

QPushButton { background:%(surface)s; border:1px solid %(border)s;
              border-radius:5px; padding:6px 12px; }
QPushButton:hover { border-color:%(a)s; color:%(a)s; }
QPushButton:disabled { color:%(disabled)s; border-color:%(border_dim)s; }
QPushButton#primary { background:%(a)s; color:%(on_accent)s;
                      font-weight:600; border:none; padding:7px 12px; }
QPushButton#primary:hover { color:%(on_accent)s; }
QPushButton#primary:disabled { background:%(border)s; color:%(disabled)s; }
QPushButton#ghost { background:transparent; border:1px solid %(border)s;
                    color:%(muted)s; }
QPushButton#ghost:hover { border-color:%(a)s; color:%(a)s; }

QToolButton { background:transparent; border:none; color:%(text)s;
              text-align:left; padding:4px 2px; }
QToolButton:hover { color:%(a)s; }

QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {
    background:%(sunken)s; border:1px solid %(border)s;
    border-radius:4px; padding:3px 6px; }
QLineEdit:focus, QComboBox:focus,
QSpinBox:focus, QDoubleSpinBox:focus { border-color:%(a)s; }
QSpinBox, QDoubleSpinBox { min-width:44px; padding-right:16px; }

QCheckBox, QRadioButton { spacing:6px; }
QCheckBox::indicator { width:12px; height:12px; border-radius:3px; }
QCheckBox::indicator:unchecked {
    background:%(sunken)s; border:1px solid %(off)s; }
QCheckBox::indicator:checked {
    background:%(a)s; border:1px solid %(a)s; image:url("%(check)s"); }
QCheckBox::indicator:unchecked:hover { border-color:%(a)s; }
QCheckBox::indicator:disabled { background:%(sunken)s;
    border:1px solid %(border_dim)s; }

/* Radio buttons are round with a dot, so they never read as checkboxes,
   and the option that is off has dimmer text. */
QRadioButton::indicator { width:10px; height:10px; border-radius:7px; }
QRadioButton::indicator:unchecked {
    background:%(sunken)s; border:2px solid %(off)s; }
QRadioButton::indicator:checked { border:2px solid %(a)s;
    background:qradialgradient(cx:0.5, cy:0.5, radius:0.5, fx:0.5, fy:0.5,
        stop:0 %(a)s, stop:0.55 %(a)s, stop:0.7 %(sunken)s,
        stop:1 %(sunken)s); }
QRadioButton::indicator:unchecked:hover { border-color:%(a)s; }
QRadioButton:checked { color:%(text)s; }
QRadioButton:unchecked { color:%(muted)s; }
QRadioButton:unchecked:hover { color:%(text)s; }

QSlider::groove:horizontal { height:4px; background:%(border)s;
                             border-radius:2px; }
QSlider::handle:horizontal { background:%(a)s; width:12px; margin:-5px 0;
                             border-radius:6px; }
QProgressBar { background:%(border)s; border:none; border-radius:2px;
               text-align:center; }
QProgressBar::chunk { background:%(a)s; border-radius:2px; }

QScrollArea { border:none; }
QScrollBar:vertical { background:%(bg)s; width:10px; margin:0; }
QScrollBar::handle:vertical { background:%(border)s; border-radius:5px;
                              min-height:24px; }
QScrollBar::handle:vertical:hover { background:%(a)s; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; }
QToolTip { background:%(sunken)s; color:%(text)s;
           border:1px solid %(border)s; padding:4px; }
"""


_CHECK_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="12" height="12" '
    'viewBox="0 0 12 12"><path d="M2.4 6.3 L4.9 8.8 L9.6 3.4" fill="none" '
    'stroke="%s" stroke-width="2" stroke-linecap="round" '
    'stroke-linejoin="round"/></svg>')


def _check_image():
    """Path of the tick drawn on a checked checkbox (written once to the
    temp folder, since a stylesheet can only load an image from a file).
    Empty if it can't be written: the box is then just filled."""
    import os
    import tempfile
    try:
        folder = os.path.join(tempfile.gettempdir(), "forge_theme")
        path = os.path.join(folder, "check_%s.svg" % ON_ACCENT.lstrip("#"))
        svg = _CHECK_SVG % ON_ACCENT
        if not os.path.isfile(path):
            if not os.path.isdir(folder):
                os.makedirs(folder)
            with open(path, "w") as f:
                f.write(svg)
        return path.replace("\\", "/")
    except Exception:
        return ""


def style(accent=ACCENT_RIG):
    """The full stylesheet for a tool window, tinted by `accent`."""
    return _TEMPLATE % {
        "bg": BG, "surface": SURFACE, "sunken": SUNKEN,
        "border": BORDER, "border_dim": BORDER_DIM, "off": OFF,
        "text": TEXT, "muted": MUTED, "disabled": DISABLED,
        "on_accent": ON_ACCENT, "a": accent, "check": _check_image(),
    }


# --- small helpers so tools don't hand-write colours ------------------------

def title_css(accent=ACCENT_RIG):
    return "color:%s; font-weight:700; font-size:15px;" % accent


def subtitle_css():
    return "color:%s; font-size:11px;" % MUTED


def credit_css():
    return "color:%s; font-size:10px;" % DISABLED


def status_css(kind="ok"):
    """kind: 'ok' | 'warn' | 'error' | 'muted'."""
    return "color:%s; font-size:11px;" % {
        "ok": OK, "warn": WARN, "error": ERROR, "muted": MUTED,
    }.get(kind, MUTED)


def accent_css(accent=ACCENT_RIG, bold=False):
    return "color:%s;%s" % (accent, " font-weight:600;" if bold else "")


def section_header_css(accent=ACCENT_RIG):
    """For the collapsible section headers in the rig panel."""
    return ("QToolButton { border:none; background:transparent; "
            "font-weight:600; color:%s; text-align:left; padding:4px 2px; }"
            "QToolButton:hover { color:%s; }" % (MUTED, accent))
