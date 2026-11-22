"""
f5_overlay.py - monster HP + size overlay for Monster Hunter Frontier Forward.5 (private server).

  double-click f5_overlay.py       (or: python f5_overlay.py)  Windows, Python 3, no extra packages
                                   A console window opens with the menu; Ctrl+C or closing it quits.
                                   Start the game in windowed / borderless mode; run as Administrator
                                   if the game does
  python f5_overlay.py --console   no overlay window, print what is read (--once / --count N: print and exit)
  python f5_overlay.py --demo      show fake monsters to tune the look without the game

Options are changed in the black console window: type a key and press Enter (paged menu, > and < change page, 0 lists everything),
or edit f5_overlay.ini next to this file (created with defaults on first run). See README.md.

Two looks, selected with `style =` in the ini (or 't' in the menu):
  lvdew      Lvdew's look: name [size], HP numbers and a small HP bar that turns yellow in the capture range  [default]
  alexander  stacked translucent text boxes (like MH-HP-Overlay-For-PSP-Emulator)

The monster table (names, large/small flag, capture range) comes from Lvdew's Config.txt if it sits next to this
file, otherwise from the built-in copy. Crown thresholds come from the MHF monster size table by SirFist.

What it reads (reverse engineered from the F5 mhfo.dll, verified in game):
  * monster array pointer   mhfo.dll + 0x51E3378 (fallback 0x5725978); stride 0xD80; [01 01] flags; +3 = id
  * HP   cur = ror16(word@+0x614, 5) ^ ~T ^ KEY ^ 0x6A60     max = ror16(word@+0x6AC, 3) ^ ~T ^ KEY ^ 0x6A60
         T = u16 at mhfo.dll + 0x57D9AEE - 2*slot,  KEY = u16 at mhfo.dll + 0xC6F714   (signed 16-bit)
  * size (%)  u16 at +0xC2A of each monster struct (100 = normal)
Status buildup, KO and the like are not decoded yet, so they are not shown.
A log file (off by default, [Debug] in the ini or 'i' in the menu) records what the program sees.
"""
import configparser
import ctypes
import ctypes.wintypes as wt
import logging
import logging.handlers
import math
import os
import re
import signal
import struct
import sys
import threading
import time

# ----------------------------------------------------------------------------------------------
# F5 constants (RVAs inside mhfo.dll)
# ----------------------------------------------------------------------------------------------
TARGET_PROC = "mhf.exe"
ARRAY_PTR_RVAS = (0x51E3378, 0x5725978)
STRIDE = 0xD80
MAX_SLOTS = 40
TABLE_RVA = 0x57D9AEE
KEY_RVA = 0xC6F714
CONST = 0x6A60
OFF_ID, OFF_CUR, OFF_MAX, OFF_SIZE = 0x03, 0x614, 0x6AC, 0xC2A

# ----------------------------------------------------------------------------------------------
# Monster table (Lvdew [MonsterData] format): number = name, capture_lo%, capture_hi%, large
# ----------------------------------------------------------------------------------------------
DEFAULT_MONSTERS = """
001=Rathian,0,20,1
002=Fatalis,0,0,1
003=Kelbi,0,0,0
004=Mosswine,0,0,0
005=Bullfango,0,0,0
006=Yian Kut-ku,0,30,1
007=Lao-Shan Lung,0,0,1
008=Cephadrome,0,20,1
009=Felyne,0,0,0
010=Veggie Elder,0,0,0
011=Rathalos,0,40,1
012=Aptonoth,0,0,0
013=Genprey,0,0,0
014=Diablos,0,40,1
015=Khezu,0,30,1
016=Velociprey,0,0,0
017=Gravios,0,40,1
018=Cart,0,0,0
019=Vespoid,0,0,0
020=Gypceros,0,30,1
021=Plesioth,0,30,1
022=Basarios,0,40,1
023=Melynx,0,0,0
024=Hornetaur,0,0,0
025=Apceros,0,0,0
026=Monoblos,0,40,1
027=Velocidrome,0,30,1
028=Gendrome,0,30,1
029=Boulder,0,0,0
030=Ioprey,0,0,0
031=Iodrome,0,30,1
032=Poogie,0,0,0
033=Kirin,0,0,1
034=Cephalos,0,0,0
035=Giaprey,0,0,0
036=Crimson Fatalis,20,50,1
037=Pink Rathian,0,20,1
038=Blue Yian Kut-ku,0,30,1
039=Purple Gypceros,0,30,1
040=Yian Garuga,0,40,1
041=Silver Rathalos,0,40,1
042=Gold Rathian,0,20,1
043=Black Diablos,0,40,1
044=White Monoblos,0,40,1
045=Red Khezu,0,30,1
046=Green Plesioth,0,30,1
047=Black Gravios,0,40,1
048=Daimyo Hermitaur,0,40,1
049=Azure Rathalos,0,40,1
050=Ashen Lao-Shan Lung,0,0,1
051=Blangonga,0,40,1
052=Congalala,0,30,1
053=Rajang,0,25,1
054=Kushala Daora,0,30,1
055=Shen Gaoren,0,0,1
056=Great Thunderbug,0,0,0
057=Shakalaka,0,0,0
058=Yama Tsukami,0,0,1
059=Chameleos,0,30,1
060=Rusted Kushala Daora,0,30,1
061=Blango,0,0,0
062=Conga,0,0,0
063=Remobra,0,0,0
064=Lunastra,0,40,1
065=Teostra,0,40,1
066=Hermitaur,0,0,0
067=Shogun Ceanataur,0,20,1
068=Bulldrome,0,40,1
069=Anteka,0,0,0
070=Popo,0,0,0
071=White Fatalis,20,50,1
072=Yama Tsukami,0,0,1
073=Ceanataur,0,0,0
074=Hypnoc,0,20,1
075=Lavasioth,0,30,1
076=Tigrex,0,22,1
077=Akantor,0,25,1
078=Bright Hypnoc,0,30,1
079=Red Lavasioth,0,30,1
080=Espinas,0,30,1
081=Flaming Espinas,0,30,1
082=Silver Hypnoc,0,30,1
083=Akura Vashimu,0,30,1
084=Akura Jebia,0,30,1
085=Berukyurosu,0,30,1
086=Cactus,0,0,0
087=Objects,0,0,0
088=Gorge Rocks,0,0,0
089=Pariapuria,0,30,1
090=White Espinas,0,30,1
091=Kamu Orugaron,0,25,1
092=Nono Orugaron,0,25,1
093=Raviente,0,0,1
094=Dyuragaua,0,22,1
095=Doragyurosu,0,30,1
096=Gurenzeburu,0,20,1
097=Burukku,0,0,0
098=Erupe,0,0,0
099=Rukodiora,0,20,1
100=Unknown,0,0,1
101=Gogomoa,0,30,1
102=Kokomoa,0,0,0
103=Taikun Zamuza,0,0,1
104=Abiorugu,0,30,1
105=Kuarusepusu,0,30,1
106=Odibatorasu,0,25,1
119=Goruganosu,0,30,1
120=Aruganosu,0,30,1
149=Berserk Raviente,0,20,1
"""


def parse_monsters(text):
    table = {}
    for line in text.splitlines():
        m = re.match(r"\s*(\d+)\s*=\s*([^,]+?)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)", line)
        if m:
            table[int(m.group(1))] = (m.group(2), int(m.group(3)), int(m.group(4)), int(m.group(5)))
    return table


def load_monsters(path):
    table = parse_monsters(DEFAULT_MONSTERS)
    if path and os.path.exists(path):
        table.update(parse_monsters(open(path, "rb").read().decode("utf-8", "replace")))
    return table


# ----------------------------------------------------------------------------------------------
# Crown thresholds - "MHF monster sizes" table by SirFist (gist f4f6640467d9166d3e4f132aea5d3cbc).
# id,small,silver,gold  as size multipliers x100 (quest size 100 = 1.00). Size <= small -> Small,
# >= silver -> Silver, >= gold -> Gold. Monsters with no crown (all 1.00 in the table) are left out.
# ----------------------------------------------------------------------------------------------
CROWN_DATA = """
1,93,118,129  6,90,114,122  8,93,118,122  11,90,114,127  14,97,123,139  15,93,118,135  17,97,123,135
20,93,118,125  21,97,123,134  22,93,118,129  26,94,116,127  27,90,114,123  28,90,113,123  31,90,113,136
33,97,130,177  37,93,118,129  38,90,114,122  39,93,118,125  40,91,115,120  41,90,114,127  42,93,118,129
43,97,123,139  44,94,116,127  45,93,118,135  46,97,123,134  47,97,123,135  48,88,110,123  49,90,114,127
51,99,125,138  52,97,115,125  53,105,125,140  54,91,110,120  59,96,120,141  60,91,110,120  64,91,115,121
65,88,110,125  67,94,114,120  68,98,113,130  74,91,115,121  75,97,123,134  76,90,119,123  78,91,115,121
79,97,123,134  80,90,114,127  81,90,114,127  82,91,115,121  83,90,115,124  84,90,115,124  85,90,115,124
89,90,115,124  90,90,114,127  91,91,110,120  92,91,110,120  94,91,115,124  95,90,115,124  96,91,115,121
99,91,110,120  101,91,110,120  103,91,110,120  104,91,115,121  105,91,115,121  107,91,115,121
108,91,115,121  109,91,115,121  110,91,115,121  111,91,115,121  112,91,115,121  114,91,115,121
115,91,115,121  116,91,115,121  121,85,100,115  122,91,115,121  123,91,115,121  125,90,115,124
126,91,115,121  127,90,119,123  128,91,110,120  129,96,110,120  139,97,119,123  140,91,110,120
141,91,115,121  142,91,115,121  146,91,110,120  147,91,115,121  148,91,115,121  150,88,110,125
151,91,110,115  152,91,115,121  153,91,110,120  154,91,115,121  158,97,108,112  159,91,110,115
161,97,107,109  162,91,110,120  164,91,110,120  165,91,110,120  169,91,110,120
"""
CROWNS = {}
for _row in CROWN_DATA.split():
    _i, _s, _si, _g = (int(v) for v in _row.split(","))
    CROWNS[_i] = (_s, _si, _g)

CROWN_COLORS = {"Gold": "#ffd34d", "Silver": "#dfe3ea", "Small": "#8fd3ff"}


# ----------------------------------------------------------------------------------------------
# Logging (off by default; switch on in the ini [Debug] section, with --log, or with 'i' in the menu)
# ----------------------------------------------------------------------------------------------
log = logging.getLogger("f5overlay")
log.addHandler(logging.NullHandler())
log.propagate = False
log.setLevel(logging.DEBUG)
_log_handler = None


def enable_logging(path, level="info"):
    global _log_handler
    disable_logging()
    try:
        h = logging.handlers.RotatingFileHandler(path, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8")
    except OSError as e:
        print("could not open the log file", path, "-", e)
        return False
    h.setLevel(logging.DEBUG if str(level).lower() == "debug" else logging.INFO)
    h.setFormatter(logging.Formatter("%(asctime)s.%(msecs)03d [%(levelname)s] [%(threadName)s] %(message)s",
                                     datefmt="%Y-%m-%d %H:%M:%S"))
    log.addHandler(h)
    _log_handler = h
    log.info("=" * 72)
    log.info("Logging on (level=%s). Python %s, %s", level, sys.version.split()[0], sys.executable)
    return True


def disable_logging():
    global _log_handler
    if _log_handler is not None:
        log.info("Logging off")
        log.removeHandler(_log_handler)
        _log_handler.close()
        _log_handler = None


# ----------------------------------------------------------------------------------------------
# Settings (f5_overlay.ini). One table drives reading, defaults and the commented file that is written.
# (section, key, attribute, default, kind, comment)   kind: str | bool | int:lo:hi | float:lo:hi | a|b|c
# ----------------------------------------------------------------------------------------------
PRESETS = {"left-center": (0, 50), "top-left": (0, 0), "top-right": (100, 0), "bottom-left": (0, 100),
           "bottom-right": (100, 100), "right-center": (100, 50)}
PRESETS_LIST = ["custom"] + list(PRESETS)
PRESETS_LIST_STR = PRESETS_LIST


def effective_xy(s):
    """Position in percent of the game window: the preset if one is set, else x and y."""
    return PRESETS.get(s.preset, (s.x, s.y))


SCHEMA = [
    ("General", None, None, None, None,
     "style: lvdew = Lvdew's look (name, numbers, small HP bar)  |  alexander = stacked translucent boxes"),
    ("General", "style", "style", "lvdew", "lvdew|alexander", None),
    ("General", "update_time", "update_time", "0.03", "float:0.01:5",
     "seconds between memory reads and redraws. 0.03 = about 30 times a second. Lower = faster, 0.01 is the limit"),
    ("General", "show_small_monsters", "show_small_monsters", "true", "bool",
     "also list small monsters (Velociprey, Hermitaur ...) below the large ones"),
    ("General", "hide_dead_small_monsters", "hide_dead_small", "true", "bool", "hide small monsters at 0 HP"),
    ("General", "show_size", "show_size", "true", "bool", "show the quest size of large monsters"),
    ("General", "show_crown", "show_crown", "true", "bool",
     "write Small / Silver / Gold next to the name when the size is inside the crown range"),
    ("General", "show_hp_percentage", "show_hp_percentage", "true", "bool", "show HP as a percentage"),
    ("General", "show_initial_hp", "show_initial_hp", "true", "bool", "show the maximum HP next to the current HP"),
    ("General", "highlight_capturable", "highlight_capturable", "true", "bool",
     "yellow text / bar (lvdew) or capture colours (alexander) while HP is inside the capture range"),
    ("General", "low_hp_percent", "low_hp_percent", "0", "float:0:100",
     "also highlight large monsters that have no capture range once HP is below this percent (0 = never)"),

    ("General", "only_in_quest", "only_in_quest", "true", "bool",
     "hide the overlay outside quests (in town the game keeps odd entries). It needs at least one known large "
     "monster; unknown ids are ignored. Switch off if the overlay stays hidden in a real quest"),
    ("General", "only_when_focused", "only_when_focused", "false", "bool",
     "show the overlay only while the game window (or this console) is the active window"),
    ("General", "hide_on_screenshot", "hide_on_screenshot", "false", "bool",
     "hide the overlay for a moment when a screenshot key is pressed (off by default)"),
    ("General", "screenshot_hide_seconds", "screenshot_hide_seconds", "2", "float:0.2:30",
     "how long it stays hidden after a screenshot key"),
    ("General", "screenshot_keys", "screenshot_keys", "PrintScreen, Alt+PrintScreen, Win+Shift+S", "str",
     "screenshot keys, comma separated. Modifiers: Ctrl, Alt, Shift, Win. Keys: PrintScreen, F1-F24, A-Z, 0-9 ..."),
    ("General", "run_as_admin", "run_as_admin", "true", "bool",
     "when started without Administrator rights, ask Windows for them (one UAC prompt) and restart. "
     "Needed when the game runs as Administrator"),
    ("General", "clear_screen", "clear_screen", "true", "bool",
     "clear this console window after every menu command so you never have to scroll"),
    ("General", "menu_mode", "menu_mode", "pages", "pages|all",
     "console menu: pages (10 commands per page) or all (every option in one list; key 0 switches)"),
    ("General", "banner", "banner", "true", "bool", "text art title above the menu"),

    ("Layout", None, None, None, None,
     "x and y: position as percent of the game window. x = 100, y = 0 is the top-right corner."),
    ("Layout", "preset", "preset", "custom", "|".join(PRESETS_LIST_STR),
     "custom = use x and y below. left-center = Lvdew's original place (left side, centered on height). "
     "Others: top-left, top-right, bottom-left, bottom-right, right-center"),
    ("Layout", "x", "x", "100", "float:0:100", None),
    ("Layout", "y", "y", "0", "float:0:100", None),
    ("Layout", "fix_x", "fix_x", "0", "int:-5000:5000", "extra pixels added to the position (fine tuning)"),
    ("Layout", "fix_y", "fix_y", "0", "int:-5000:5000", None),

    ("Lvdew", None, None, None, None, "Settings for style = lvdew"),
    ("Lvdew", "text_size", "lv_text_size", "10", "int:6:72", "text size in points"),
    ("Lvdew", "font_family", "lv_font_family", "Segoe UI", "str", "font name (must be installed)"),
    ("Lvdew", "font_weight", "lv_font_weight", "bold", "normal|bold", None),
    ("Lvdew", "numbers", "lv_numbers", "below", "below|inline|off",
     "HP numbers: below = own line under the name, inline = same line as the name, off = hidden"),
    ("Lvdew", "show_bar", "lv_bar", "true", "bool", "draw the HP bar"),
    ("Lvdew", "bar_mode", "lv_bar_mode", "simple", "simple|capture",
     "simple = whole bar turns yellow in the capture range; capture = bar stays green and the capture range "
     "part of it is painted yellow"),
    ("Lvdew", "bar_width", "lv_bar_width", "240", "int:20:1200", "bar length in pixels"),
    ("Lvdew", "bar_height", "lv_bar_height", "8", "int:2:60", "bar thickness in pixels"),
    ("Lvdew", "bar_position", "lv_bar_position", "below", "below|above|left|right",
     "where the bar sits relative to the text of each monster"),
    ("Lvdew", "max_rows", "lv_max_rows", "5", "int:1:40", "show at most this many monsters"),
    ("Lvdew", "row_gap", "lv_row_gap", "8", "int:0:80", "pixels between two monsters"),
    ("Lvdew", "shadow", "lv_shadow", "true", "bool", "black shadow behind the text (easier to read)"),
    ("Lvdew", "frame", "lv_frame", "false", "bool", "dark rectangle behind everything"),
    ("Lvdew", "frame_color", "lv_frame_color", "#000000", "str", None),
    ("Lvdew", "frame_opacity", "lv_frame_opacity", "100", "int:0:100",
     "opacity settings (0-100): 0 = not drawn, 100 = solid. Values in between are drawn as a fine dither "
     "(Windows cannot blend a single item), so they can look grainy; text itself cannot be made see-through "
     "separately, use the overall opacity for that"),
    ("Lvdew", "box_color", "lv_box_color", "#000000", "str", "optional box behind each monster (off by default)"),
    ("Lvdew", "box_opacity", "lv_box_opacity", "0", "int:0:100", None),
    ("Lvdew", "shadow_color", "lv_shadow_color", "#000000", "str", "colour of the text shadow"),
    ("Lvdew", "bar_opacity", "lv_bar_opacity", "100", "int:0:100", "opacity of the filled part of the bar"),
    ("Lvdew", "bar_back_opacity", "lv_bar_back_opacity", "100", "int:0:100", "opacity of the empty part of the bar"),
    ("Lvdew", "text_color", "lv_text_color", "#FFFFFF", "str", "colours: names (white, yellow ...) or #rrggbb"),
    ("Lvdew", "text_capture_color", "lv_text_capture_color", "#F0F000", "str", "text colour in the capture range"),
    ("Lvdew", "bar_color", "lv_bar_color", "#10D010", "str", None),
    ("Lvdew", "bar_capture_color", "lv_bar_capture_color", "#F0F000", "str", None),
    ("Lvdew", "bar_back_color", "lv_bar_back_color", "#303030", "str", "empty part of the bar"),
    ("Lvdew", "opacity", "lv_opacity", "100", "int:20:100", "opacity of the whole overlay, 0-100"),

    ("Alexander", None, None, None, None, "Settings for style = alexander"),
    ("Alexander", "font_size", "font_size", "18", "int:6:96", "text size in pixels"),
    ("Alexander", "font_family", "font_family", "Consolas, monaco, monospace", "str", None),
    ("Alexander", "font_weight", "font_weight", "bold", "normal|bold", None),
    ("Alexander", "align", "align", "true", "bool", "give every box the same width"),
    ("Alexander", "orientation", "orientation", "center", "left|center|right", "text alignment"),
    ("Alexander", "text_color", "colors.text_color", "aquamarine", "str", "colours: names or #rrggbb"),
    ("Alexander", "background_color", "colors.background_color", "#2f4f4f", "str", None),
    ("Alexander", "capture_text_color", "colors.capture_text_color", "yellow", "str", None),
    ("Alexander", "capture_background_color", "colors.capture_background_color", "#1e6b1e", "str", None),
    ("Alexander", "background_opacity", "background_opacity", "100", "int:0:100",
     "opacity of the boxes: 0 = no boxes, 100 = solid, in between = dither (see Lvdew section)"),
    ("Alexander", "capture_background_opacity", "capture_background_opacity", "100", "int:0:100", None),
    ("Alexander", "window_opacity", "window_opacity", "85", "int:20:100",
     "opacity of the whole overlay, 0-100 (Windows cannot fade only the box backgrounds)"),

    ("Debug", None, None, None, None,
     "A log file helps when something does not work. Off by default; also switchable with 'i' in the menu"),
    ("Debug", "log", "log", "false", "bool", "write the log file"),
    ("Debug", "log_file", "log_file", "f5_overlay.log", "str", "relative paths are next to f5_overlay.py"),
    ("Debug", "log_level", "log_level", "info", "info|debug", "debug also logs every change of the monster list"),
]
SECTION_ORDER = ["General", "Layout", "Lvdew", "Alexander", "Debug"]


def _tobool(v):
    return str(v).strip().lower() in ("1", "true", "yes", "on")


def _convert(raw, kind, default):
    """String from the ini -> typed value; falls back to the default when invalid."""
    def conv(r):
        if kind == "bool":
            return _tobool(r)
        if kind == "str":
            return str(r).strip()
        if "|" in kind:
            r = str(r).strip().lower()
            if r not in kind.split("|"):
                raise ValueError(r)
            return r
        t, lo, hi = kind.split(":")
        n = float(r)
        n = max(float(lo), min(float(hi), n))
        return int(round(n)) if t == "int" else n
    try:
        return conv(raw)
    except (ValueError, TypeError):
        return conv(default)


def _setattr_path(obj, path, value):
    if "." in path:
        d, k = path.split(".")
        getattr(obj, d)[k] = value
    else:
        setattr(obj, path, value)


def _getattr_path(obj, path):
    if "." in path:
        d, k = path.split(".")
        return getattr(obj, d)[k]
    return getattr(obj, path)


class Settings:
    def __init__(self, path=None):
        self.path = path
        self.colors = {}
        self.crowns = {}
        self.enabled, self.preview, self.version = True, False, 0     # runtime only
        self.warn = ""
        cp = configparser.ConfigParser(interpolation=None, inline_comment_prefixes=(";",))
        cp.optionxform = str
        if path and os.path.exists(path):
            try:
                cp.read(path, encoding="utf-8")
                if not cp.has_section("Lvdew"):         # ini of an older version: keep it, start a new one
                    try:
                        os.replace(path, path + ".old")
                        print(f"{os.path.basename(path)} was from an older version, saved as {os.path.basename(path)}.old"
                              " and a new one was created.")
                    except OSError:
                        pass
                    cp = configparser.ConfigParser(interpolation=None)
                    cp.optionxform = str
            except configparser.Error as e:
                print("could not parse", path, "-", e, "- using defaults")
                cp = configparser.ConfigParser(interpolation=None)
                cp.optionxform = str
        for sec, key, attr, default, kind, _c in SCHEMA:
            if key is None:
                continue
            raw = cp.get(sec, key, fallback=default) if cp.has_section(sec) else default
            _setattr_path(self, attr, _convert(raw, kind, default))
        if cp.has_section("Crowns"):
            for name, val in cp.items("Crowns"):
                try:
                    m, s, g = (int(x) for x in val.split(","))
                    self.crowns[name.lower()] = (m, s, g)
                except ValueError:
                    pass
        if path and not os.path.exists(path):
            self.save()

    def to_ini(self):
        out = []
        for sec in SECTION_ORDER:
            out.append(f"[{sec}]")
            for s, key, attr, _d, _k, comment in SCHEMA:
                if s != sec:
                    continue
                if key is None:
                    out.append(f"; {comment}")
                    continue
                if comment:
                    out.append(f"; {comment}")
                val = _getattr_path(self, attr)
                if isinstance(val, bool):
                    val = "true" if val else "false"
                elif isinstance(val, float):
                    val = f"{val:g}"
                out.append(f"{key} = {val}")
            out.append("")
        out.append("[Crowns]")
        out.append("; Optional overrides in percent of normal size:  Monster name = small_max, silver_min, gold_min")
        out.append("; Velocidrome = 90, 114, 123")
        for n, (m, s, g) in self.crowns.items():
            out.append(f"{n} = {m}, {s}, {g}")
        return "\n".join(out) + "\n"

    def save(self, path=None):
        path = path or self.path
        if not path:
            return False
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.to_ini())
            return True
        except OSError as e:
            print("could not write", path, "-", e)
            return False


# ----------------------------------------------------------------------------------------------
# Pure helpers (no Windows needed - unit-testable)
# ----------------------------------------------------------------------------------------------
def ror16(v, n):
    return ((v >> n) | (v << (16 - n))) & 0xFFFF


def s16(v):
    return v - 0x10000 if v & 0x8000 else v


def decode_hp(w614, w6ac, table, key):
    mask = (~table & 0xFFFF) ^ key ^ CONST
    return s16(ror16(w614, 5) ^ mask), s16(ror16(w6ac, 3) ^ mask)


def get_crown(size, mid, name, settings):
    """'Small' / 'Silver' / 'Gold' / '' from the monster id (or name override) and size in percent."""
    if not settings.show_crown or not size:
        return ""
    t = settings.crowns.get(name.lower()) or CROWNS.get(mid)
    if not t:
        return ""
    small, silver, gold = t
    if size >= gold:
        return "Gold"
    if silver > 100 and size >= silver:
        return "Silver"
    if size <= small:
        return "Small"
    return ""


_VK_NAMED = {"enter": 0x0D, "return": 0x0D, "tab": 0x09, "space": 0x20, "esc": 0x1B, "escape": 0x1B,
             "backspace": 0x08, "ins": 0x2D, "insert": 0x2D, "del": 0x2E, "delete": 0x2E, "home": 0x24,
             "end": 0x23, "pgup": 0x21, "pageup": 0x21, "pgdown": 0x22, "pagedown": 0x22, "left": 0x25,
             "up": 0x26, "right": 0x27, "down": 0x28, "pause": 0x13, "printscreen": 0x2C, "prtsc": 0x2C,
             "prtscn": 0x2C, "print": 0x2C, "snapshot": 0x2C, "scrolllock": 0x91}
_MODS = {"ctrl": (0x11,), "control": (0x11,), "alt": (0x12,), "shift": (0x10,), "win": (0x5B, 0x5C),
         "meta": (0x5B, 0x5C), "super": (0x5B, 0x5C)}


def parse_combo(text):
    """'Alt+PrintScreen' -> (list of modifier vk tuples, key vk) or None."""
    parts = [p.strip().lower() for p in text.split("+") if p.strip()]
    if not parts:
        return None
    key, mods = parts[-1], []
    for p in parts[:-1]:
        if p not in _MODS:
            return None
        mods.append(_MODS[p])
    if key in _VK_NAMED:
        vk = _VK_NAMED[key]
    elif re.fullmatch(r"f([1-9]|1\d|2[0-4])", key):
        vk = 0x6F + int(key[1:])
    elif len(key) == 1 and key.isascii() and key.isalnum():
        vk = ord(key.upper())
    else:
        return None
    return mods, vk


class KeyWatcher:
    """True from poll() while one of the comma separated combos is held down."""

    def __init__(self, key_down=None):
        self.key_down = key_down or (lambda vk: bool(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000))
        self._cache = {}

    def poll(self, text):
        combos = self._cache.get(text)
        if combos is None:
            combos = [c for c in (parse_combo(p) for p in text.split(",")) if c]
            self._cache[text] = combos
        for mods, vk in combos:
            if self.key_down(vk) and all(any(self.key_down(v) for v in group) for group in mods):
                return True
        return False


def quest_filter(state, table, settings):
    """Town / loading screens: keep only known monster ids and require a large monster to be present."""
    if not settings.only_in_quest:
        return state
    known = [m for m in state if m["id"] in table and m["max"] >= 100]
    if not any(table[m["id"]][3] for m in known):
        return []
    return [m for m in state if m["id"] in table]


def build_entries(monsters_state, table, settings):
    """Raw monster reads -> display entries (dicts), large monsters first, then small ones."""
    large, small = [], []
    for m in monsters_state:
        name, lo, hi, is_large = table.get(m["id"], (f"#{m['id']}", 0, 0, 1))
        cur, mx = m["cur"], m["max"]
        if mx <= 5:
            continue
        if not is_large and (not settings.show_small_monsters or (settings.hide_dead_small and cur <= 0)):
            continue
        frac = max(0.0, min(1.0, cur / mx))
        pct_exact = 100.0 * cur / mx
        in_range = hi > 0 and lo <= pct_exact <= hi
        low = (hi == 0 and settings.low_hp_percent > 0 and bool(is_large)
               and pct_exact <= settings.low_hp_percent)
        e = {"id": m["id"], "name": name, "large": bool(is_large), "cur": cur, "max": mx,
             "pct": math.ceil(pct_exact), "frac": frac, "lo": lo, "hi": hi,
             "size": m.get("size") if is_large else None,
             "crown": get_crown(m.get("size"), m["id"], name, settings) if is_large else "",
             "capture": bool(is_large and settings.highlight_capturable and in_range),
             "hot": bool(is_large and settings.highlight_capturable and (in_range or low))}
        (large if is_large else small).append(e)
    return large + small


def entry_text(e, settings):
    """Alexander-style one-line text:  (115%) Velocidrome Gold: 100% | 1600 | 1600"""
    t = ""
    if e["large"] and settings.show_size and e["size"]:
        t += f"({e['size']}%) "
    t += e["name"]
    if e["crown"]:
        t += f" {e['crown']}"
    t += ":"
    if settings.show_hp_percentage:
        t += f" {e['pct']}% |"
    t += f" {e['cur']}"
    if settings.show_initial_hp:
        t += f" | {e['max']}"
    return t


# ----------------------------------------------------------------------------------------------
# Windows process access
# ----------------------------------------------------------------------------------------------
if os.name == "nt":
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    u32 = ctypes.WinDLL("user32", use_last_error=True)
    # explicit prototypes: without them 64-bit Python overflows on handles / INVALID_HANDLE_VALUE
    _P = ctypes.c_void_p
    k32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
    k32.OpenProcess.restype = _P
    k32.CreateToolhelp32Snapshot.argtypes = [wt.DWORD, wt.DWORD]
    k32.CreateToolhelp32Snapshot.restype = _P
    for _f in (k32.Process32FirstW, k32.Process32NextW, k32.Module32FirstW, k32.Module32NextW):
        _f.argtypes = [_P, _P]
        _f.restype = wt.BOOL
    k32.CloseHandle.argtypes = [_P]
    k32.GetExitCodeProcess.argtypes = [_P, ctypes.POINTER(wt.DWORD)]
    k32.ReadProcessMemory.argtypes = [_P, _P, _P, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
    k32.ReadProcessMemory.restype = wt.BOOL
    u32.GetWindowThreadProcessId.argtypes = [_P, ctypes.POINTER(wt.DWORD)]
    u32.IsWindowVisible.argtypes = [_P]
    u32.IsWindow.argtypes = [_P]
    u32.IsIconic.argtypes = [_P]
    u32.GetClientRect.argtypes = [_P, _P]
    u32.ClientToScreen.argtypes = [_P, _P]
    u32.EnumWindows.argtypes = [_P, _P]
    u32.GetParent.argtypes = [_P]
    u32.GetParent.restype = _P
    u32.GetWindowLongW.argtypes = [_P, ctypes.c_int]
    u32.GetWindowLongW.restype = ctypes.c_long
    u32.SetWindowLongW.argtypes = [_P, ctypes.c_int, ctypes.c_long]
    u32.SetWindowLongW.restype = ctypes.c_long
    u32.GetForegroundWindow.restype = _P
    u32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    u32.GetAsyncKeyState.restype = ctypes.c_short
    k32.GetConsoleWindow.restype = _P
    _BAD = (None, 0, 0xFFFFFFFF, 0xFFFFFFFFFFFFFFFF)

    class PROCESSENTRY32W(ctypes.Structure):
        _fields_ = [("dwSize", wt.DWORD), ("cntUsage", wt.DWORD), ("th32ProcessID", wt.DWORD),
                    ("th32DefaultHeapID", ctypes.c_size_t), ("th32ModuleID", wt.DWORD),
                    ("cntThreads", wt.DWORD), ("th32ParentProcessID", wt.DWORD),
                    ("pcPriClassBase", wt.LONG), ("dwFlags", wt.DWORD), ("szExeFile", wt.WCHAR * 260)]

    class MODULEENTRY32W(ctypes.Structure):
        _fields_ = [("dwSize", wt.DWORD), ("th32ModuleID", wt.DWORD), ("th32ProcessID", wt.DWORD),
                    ("GlblcntUsage", wt.DWORD), ("ProccntUsage", wt.DWORD),
                    ("modBaseAddr", ctypes.c_size_t), ("modBaseSize", wt.DWORD),
                    ("hModule", ctypes.c_size_t), ("szModule", wt.WCHAR * 256),
                    ("szExePath", wt.WCHAR * 260)]

    class MBI(ctypes.Structure):
        _fields_ = ([("BaseAddress", ctypes.c_size_t), ("AllocationBase", ctypes.c_size_t),
                     ("AllocationProtect", wt.DWORD)]
                    + ([("PartitionId", wt.WORD)] if ctypes.sizeof(ctypes.c_void_p) == 8 else [])
                    + [("RegionSize", ctypes.c_size_t), ("State", wt.DWORD),
                       ("Protect", wt.DWORD), ("Type", wt.DWORD)])


class Game:
    def __init__(self):
        self.h = None
        self.pid = None
        self.mhfo = None   # (base, size)
        self._hwnd = None
        self._hwnd_t = 0.0

    def attach(self):
        snap = k32.CreateToolhelp32Snapshot(0x2, 0)
        if snap in _BAD:
            raise PermissionError("cannot list processes (error %d) - run this as Administrator"
                                  % ctypes.get_last_error())
        e = PROCESSENTRY32W()
        e.dwSize = ctypes.sizeof(e)
        pid = None
        ok = k32.Process32FirstW(snap, ctypes.byref(e))
        while ok:
            if e.szExeFile.lower() == TARGET_PROC:
                pid = e.th32ProcessID
                break
            ok = k32.Process32NextW(snap, ctypes.byref(e))
        k32.CloseHandle(snap)
        if not pid:
            return False
        snap = k32.CreateToolhelp32Snapshot(0x8 | 0x10, pid)
        if snap in _BAD:
            err = ctypes.get_last_error()
            if err == 299:        # ERROR_PARTIAL_COPY: the game is still starting up
                return False
            raise PermissionError("cannot read mhf.exe's modules (error %d) - run this as Administrator, "
                                  "the same way you run the game" % err)
        m = MODULEENTRY32W()
        m.dwSize = ctypes.sizeof(m)
        mhfo = None
        ok = k32.Module32FirstW(snap, ctypes.byref(m))
        while ok:
            if m.szModule.lower() in ("mhfo.dll", "mhfo-hd.dll"):
                mhfo = (m.modBaseAddr, m.modBaseSize)
                break
            ok = k32.Module32NextW(snap, ctypes.byref(m))
        k32.CloseHandle(snap)
        if not mhfo:
            return False
        h = k32.OpenProcess(0x0010 | 0x0400, False, pid)
        if not h:
            raise PermissionError("OpenProcess failed (error %d) - run this as Administrator"
                                  % ctypes.get_last_error())
        self.h, self.pid, self.mhfo = h, pid, mhfo
        return True

    def alive(self):
        if not self.h:
            return False
        code = wt.DWORD()
        k32.GetExitCodeProcess(self.h, ctypes.byref(code))
        return code.value == 259  # STILL_ACTIVE

    def detach(self):
        if self.h:
            k32.CloseHandle(self.h)
        self.h = self.pid = self.mhfo = self._hwnd = None

    def read(self, addr, size):
        buf = ctypes.create_string_buffer(size)
        n = ctypes.c_size_t()
        if not k32.ReadProcessMemory(self.h, ctypes.c_void_p(addr), buf, size, ctypes.byref(n)):
            return None
        return buf.raw[: n.value]

    def u16(self, addr):
        d = self.read(addr, 2)
        return struct.unpack("<H", d)[0] if d and len(d) == 2 else None

    def u32(self, addr):
        d = self.read(addr, 4)
        return struct.unpack("<I", d)[0] if d and len(d) == 4 else None

    def regions(self):
        addr, mbi = 0, MBI()
        while addr < 0x7FFFFFFF:
            if not k32.VirtualQueryEx(self.h, ctypes.c_void_p(addr), ctypes.byref(mbi),
                                      ctypes.sizeof(mbi)):
                break
            if mbi.State == 0x1000 and not (mbi.Protect & 0x101) and mbi.RegionSize:
                yield mbi.BaseAddress, mbi.RegionSize
            addr = mbi.BaseAddress + mbi.RegionSize

    def read_big(self, base, size):
        d = self.read(base, size)
        if d is not None:
            return d
        out = bytearray()
        for off in range(0, size, 1 << 20):
            n = min(1 << 20, size - off)
            part = self.read(base + off, n)
            out += part if part else bytes(n)
        return bytes(out)

    def _find_window(self):
        found = []
        cb_type = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

        def cb(hwnd, _):
            pid = wt.DWORD()
            u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value == self.pid and u32.IsWindowVisible(hwnd):
                r = wt.RECT()
                u32.GetClientRect(hwnd, ctypes.byref(r))
                if r.right > 200 and r.bottom > 150:
                    found.append((r.right * r.bottom, hwnd))
            return True

        u32.EnumWindows(cb_type(cb), 0)
        return max(found)[1] if found else None

    def foreground_ok(self):
        """True when the game window (or this program's console) is the active window."""
        fg = u32.GetForegroundWindow()
        if not fg:
            return False
        pid = wt.DWORD()
        u32.GetWindowThreadProcessId(fg, ctypes.byref(pid))
        if pid.value in (self.pid, os.getpid()):
            return True
        return fg == k32.GetConsoleWindow()

    def game_window_origin(self):
        """Screen x, y, width, height of the game's client area, or None (cheap: hwnd is cached)."""
        if not self.pid:
            return None
        now = time.time()
        if not self._hwnd or not u32.IsWindow(self._hwnd):
            if now - self._hwnd_t < 1.0:
                return None
            self._hwnd, self._hwnd_t = self._find_window(), now
            if not self._hwnd:
                return None
        if u32.IsIconic(self._hwnd):
            return None
        r = wt.RECT()
        u32.GetClientRect(self._hwnd, ctypes.byref(r))
        pt = wt.POINT(0, 0)
        u32.ClientToScreen(self._hwnd, ctypes.byref(pt))
        return pt.x, pt.y, r.right, r.bottom


# ----------------------------------------------------------------------------------------------
# Reader (background thread)
# ----------------------------------------------------------------------------------------------
class Reader(threading.Thread):
    def __init__(self, monsters, interval=0.03, debug=False):
        super().__init__(daemon=True)
        self.interval = interval
        self.g = Game()
        self.monsters = monsters
        self.debug = debug
        self.state = {"status": "waiting for mhf.exe", "monsters": []}
        self._last_sig = None
        self._lock = threading.Lock()

    def read_monsters(self):
        g = self.g
        base = g.mhfo[0]
        key = g.u16(base + KEY_RVA)
        arr = None
        for rva in ARRAY_PTR_RVAS:
            p = g.u32(base + rva)
            if p and g.read(p, 2) == b"\x01\x01":
                arr = p
                break
        if arr is None or key is None:
            return None
        total = STRIDE * (MAX_SLOTS - 1) + OFF_SIZE + 2
        blob = g.read(arr, total)                      # one syscall for the whole array
        if blob is not None and len(blob) < total:
            blob = None
        tbl = g.read(base + TABLE_RVA - 2 * (MAX_SLOTS - 1), 2 * MAX_SLOTS)   # all T values at once
        if tbl is None or len(tbl) < 2 * MAX_SLOTS:
            return None
        out = []
        need = OFF_SIZE + 2
        for slot in range(MAX_SLOTS):
            if blob is not None:
                blk = blob[slot * STRIDE: slot * STRIDE + need]
            else:                                      # some page unreadable: fall back to per-slot reads
                blk = g.read(arr + slot * STRIDE, need)
            if not blk or len(blk) < need or blk[0] != 1 or blk[1] != 1:
                continue
            t = struct.unpack_from("<H", tbl, 2 * (MAX_SLOTS - 1 - slot))[0]
            w1 = struct.unpack_from("<H", blk, OFF_CUR)[0]
            w2 = struct.unpack_from("<H", blk, OFF_MAX)[0]
            cur, mx = decode_hp(w1, w2, t, key)
            if mx <= 0 or cur < -1 or cur > mx + 1:
                continue
            size = struct.unpack_from("<H", blk, OFF_SIZE)[0]
            out.append({"slot": slot, "id": blk[OFF_ID], "cur": max(cur, 0), "max": mx,
                        "size": size if 10 <= size <= 400 else None})
        return out

    def _set(self, status, monsters=()):
        monsters = list(monsters)
        sig = tuple((m["slot"], m["id"], m.get("size")) for m in monsters)
        with self._lock:
            changed = self.state["status"] != status
            self.state.update(status=status, monsters=monsters)
        if changed:
            print(f"[{status}]")
            log.info("Reader status: %s", status)
        if sig != self._last_sig:
            self._last_sig = sig
            log.info("Monsters: %s", ", ".join("slot %d id %d size %s%% hp %d/%d" % (
                m["slot"], m["id"], m.get("size"), m["cur"], m["max"]) for m in monsters) or "(none)")

    def run(self):
        log.info("Reader thread started; waiting for %s", TARGET_PROC)
        while True:
            try:
                if not self.g.alive():
                    if self.g.h:
                        log.warning("Game process is gone; detached")
                    self.g.detach()
                    if not self.g.attach():
                        self._set("waiting for mhf.exe")
                        time.sleep(1.0)
                        continue
                    log.info("Attached to %s pid=%s mhfo base=0x%X size=0x%X", TARGET_PROC, self.g.pid,
                             self.g.mhfo[0], self.g.mhfo[1])
                mons = self.read_monsters()
                if mons is None:
                    self._set("game running, no quest")
                else:
                    self._set("ok", mons)
                if self.debug:
                    print(self.state)
            except Exception as e:
                log.exception("Reader error")
                self.g.detach()
                self._set(f"error: {e}")
                time.sleep(2.0)
            time.sleep(self.interval)

    def snapshot(self):
        with self._lock:
            return dict(self.state)


# ----------------------------------------------------------------------------------------------
# Overlay window (tkinter, standard library) - two styles drawn on one canvas
# ----------------------------------------------------------------------------------------------
DEMO_MONSTERS = [
    {"slot": 0, "id": 27, "cur": 1600, "max": 1600, "size": 115},
    {"slot": 1, "id": 27, "cur": 215, "max": 1400, "size": 88},
    {"slot": 2, "id": 11, "cur": 9800, "max": 21000, "size": 128},
    {"slot": 3, "id": 1, "cur": 2100, "max": 15000, "size": 100},
    {"slot": 4, "id": 66, "cur": 80, "max": 80, "size": 100},
    {"slot": 5, "id": 4, "cur": 41, "max": 60, "size": 100},
    {"slot": 6, "id": 4, "cur": 0, "max": 54, "size": 100},
]
TRANS = "#010101"      # colour Windows treats as see-through


class OverlayWindow:
    def __init__(self, settings, table, reader=None):
        import tkinter as tk
        import tkinter.font as tkfont
        self.tk, self.tkfont = tk, tkfont
        self.s, self.table, self.reader = settings, table, reader
        self.stop = False
        self.visible = False
        self._sig = None
        self._geo = None
        self._size = (0, 0)
        self._fonts = {}
        self._last_origin = "unset"
        self._last_status = None
        self.hide_until = 0.0
        self.shot = KeyWatcher() if os.name == "nt" else None
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)
        self.root.configure(bg=TRANS)
        try:
            self.root.attributes("-transparentcolor", TRANS)
        except tk.TclError:
            pass
        self.cv = tk.Canvas(self.root, width=10, height=10, bg=TRANS, highlightthickness=0)
        self.cv.pack()
        self.root.update_idletasks()
        hwnd = 0
        if os.name == "nt":                  # click-through, no focus, hidden from the taskbar
            hwnd = u32.GetParent(self.root.winfo_id()) or self.root.winfo_id()
            ex = u32.GetWindowLongW(hwnd, -20)
            u32.SetWindowLongW(hwnd, -20, ex | 0x80000 | 0x20 | 0x80 | 0x08000000)
        log.info("Overlay window created: hwnd=0x%X style=%s", hwnd or 0, settings.style)
        if self.shot:
            self.root.after(8, self._fast_poll)

    # ---- helpers -----------------------------------------------------------------------------
    def apply_opacity(self):
        pct = self.s.lv_opacity if self.s.style == "lvdew" else self.s.window_opacity
        try:
            self.root.attributes("-alpha", pct / 100.0)
        except self.tk.TclError:
            pass

    def color(self, name, default="#ffffff"):
        try:
            self.root.winfo_rgb(name)
            return name
        except self.tk.TclError:
            self.s.warn = f"unknown colour '{name}' - using {default}"
            return default

    @staticmethod
    def shape(color, opacity, outline=""):
        """Canvas options for a filled shape at the given opacity, or None when it should not be drawn."""
        if opacity <= 0:
            return None
        kw = {"fill": color, "outline": outline}
        if opacity < 88:       # dither: the skipped pixels show the see-through key colour
            kw["stipple"] = ("gray75" if opacity >= 63 else "gray50" if opacity >= 38
                             else "gray25" if opacity >= 13 else "gray12")
            kw["outline"] = ""
        return kw

    def font(self, family, size, weight, unit="px"):
        key = (family, size, weight, unit)
        if key not in self._fonts:
            have = {f.lower(): f for f in self.tkfont.families(self.root)}
            fam = next((have[x.strip().strip("'\"").lower()] for x in family.split(",")
                        if x.strip().strip("'\"").lower() in have), "Segoe UI")
            self._fonts[key] = self.tkfont.Font(root=self.root, family=fam,
                                                size=-size if unit == "px" else size,
                                                weight="bold" if weight == "bold" else "normal")
        return self._fonts[key]

    def rrect(self, x1, y1, x2, y2, r, **kw):
        r = min(r, (x2 - x1) // 2, (y2 - y1) // 2)
        pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2, x2 - r, y2,
               x1 + r, y2, x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
        return self.cv.create_polygon(pts, smooth=True, **kw)

    # ---- style: lvdew --------------------------------------------------------------------------
    def _lvdew_numbers(self, e):
        s = self.s
        t = f"{e['cur']}"
        if s.show_initial_hp:
            t += f"/{e['max']}"
        if s.show_hp_percentage:
            t += f"  ({e['pct']}%)"
        return t

    def _draw_lvdew(self, entries):
        s, cv = self.s, self.cv
        font = self.font(s.lv_font_family, s.lv_text_size, s.lv_font_weight, "pt")
        lh = font.metrics("linespace")
        pos = s.lv_bar_position
        bw, bh = s.lv_bar_width, s.lv_bar_height
        bar_on = s.lv_bar
        pad = 4 if (s.lv_frame or s.lv_box_opacity > 0) else 0
        text_col = self.color(s.lv_text_color, "#ffffff")
        text_hot = self.color(s.lv_text_capture_color, "#f0f000")
        bar_col = self.color(s.lv_bar_color, "#10d010")
        bar_hot = self.color(s.lv_bar_capture_color, "#f0f000")
        bar_back = self.color(s.lv_bar_back_color, "#303030")

        rows = []
        for e in entries[: s.lv_max_rows]:
            hot = e["hot"]
            col = text_hot if hot else text_col
            name = e["name"] + (f"  [{e['size']}%]" if (e["large"] and s.show_size and e["size"]) else "")
            segs = [(name, col)]
            if e["crown"]:
                segs.append((" " + e["crown"], CROWN_COLORS.get(e["crown"], "#ffffff")))
            lines = []
            if s.lv_numbers == "inline":
                segs.append(("   " + self._lvdew_numbers(e), col))
            lines.append(segs)
            if s.lv_numbers == "below":
                lines.append([(self._lvdew_numbers(e), col)])
            widths = [sum(font.measure(t) for t, _c in ln) for ln in lines]
            tw, th = max(widths), lh * len(lines)
            gap = 3
            if not bar_on:
                rw, rh = tw, th
            elif pos in ("below", "above"):
                rw, rh = max(tw, bw), th + gap + bh
            else:
                rw, rh = tw + 8 + bw, max(th, bh)
            rows.append((e, lines, tw, th, rw, rh, hot))

        W = max((r[4] for r in rows), default=0) + 2 * pad
        H = sum(r[5] for r in rows) + s.lv_row_gap * max(0, len(rows) - 1) + 2 * pad
        frame_kw = self.shape(self.color(s.lv_frame_color, "#000000"), s.lv_frame_opacity)
        if s.lv_frame and rows and frame_kw:
            cv.create_rectangle(0, 0, W, H, **frame_kw)
        box_kw = self.shape(self.color(s.lv_box_color, "#000000"), s.lv_box_opacity)
        shadow_col = self.color(s.lv_shadow_color, "#000000")
        y = pad
        for e, lines, tw, th, rw, rh, hot in rows:
            x = pad
            if not bar_on:
                tx, ty, bx, by = x, y, 0, 0
            elif pos == "below":
                tx, ty, bx, by = x, y, x, y + th + 3
            elif pos == "above":
                tx, ty, bx, by = x, y + bh + 3, x, y
            elif pos == "left":
                tx, ty, bx, by = x + bw + 8, y + (rh - th) // 2, x, y + (rh - bh) // 2
            else:  # right
                tx, ty, bx, by = x, y + (rh - th) // 2, x + tw + 8, y + (rh - bh) // 2
            if box_kw:
                self.rrect(x - 3, y - 2, x + rw + 3, y + rh + 2, 5, **box_kw)
            for i, segs in enumerate(lines):
                cx = tx
                for text, col in segs:
                    if s.lv_shadow:
                        cv.create_text(cx + 1, ty + i * lh + 1, text=text, anchor="nw", fill=shadow_col, font=font)
                    cv.create_text(cx, ty + i * lh, text=text, anchor="nw", fill=col, font=font)
                    cx += font.measure(text)
            if bar_on:
                back_kw = self.shape(bar_back, s.lv_bar_back_opacity, "#000000")
                if back_kw:
                    cv.create_rectangle(bx, by, bx + bw, by + bh, **back_kw)
                fill = int(bw * e["frac"])
                if fill > 0:
                    if s.lv_bar_mode == "capture" and e["hi"] > 0 and e["large"]:
                        kw = self.shape(bar_col, s.lv_bar_opacity)
                        if kw:
                            cv.create_rectangle(bx, by, bx + fill, by + bh, **kw)
                        a, b = int(bw * e["lo"] / 100), min(int(bw * e["hi"] / 100), fill)
                        kw = self.shape(bar_hot, s.lv_bar_opacity)
                        if b > a and kw:
                            cv.create_rectangle(bx + a, by, bx + b, by + bh, **kw)
                    else:
                        kw = self.shape(bar_hot if hot else bar_col, s.lv_bar_opacity)
                        if kw:
                            cv.create_rectangle(bx, by, bx + fill, by + bh, **kw)
            y += rh + s.lv_row_gap
        return W, H

    # ---- style: alexander ----------------------------------------------------------------------
    def _draw_alexander(self, entries):
        s, cv = self.s, self.cv
        font = self.font(s.font_family, s.font_size, s.font_weight, "px")
        lh = font.metrics("linespace")
        padx = 15 if s.orientation == "center" else 10
        pady, gap = 5, 5
        texts = [entry_text(e, s) for e in entries]
        widths = [font.measure(t) + 2 * padx for t in texts]
        W = max(widths)
        h = lh + 2 * pady
        y = 0
        for e, t, w in zip(entries, texts, widths):
            if s.align:
                w = W
            x = {"left": 0, "right": W - w}.get(s.orientation, (W - w) // 2)
            cap = e["capture"]
            bg = self.color(s.colors["capture_background_color" if cap else "background_color"], "#2f4f4f")
            fg = self.color(s.colors["capture_text_color" if cap else "text_color"], "#7fffd4")
            kw = self.shape(bg, s.capture_background_opacity if cap else s.background_opacity)
            if kw:
                self.rrect(x, y, x + w, y + h, 6, **kw)
            if s.align and s.orientation == "left":
                cv.create_text(x + padx, y + h // 2, text=t, anchor="w", fill=fg, font=font)
            elif s.align and s.orientation == "right":
                cv.create_text(x + w - padx, y + h // 2, text=t, anchor="e", fill=fg, font=font)
            else:
                cv.create_text(x + w // 2, y + h // 2, text=t, anchor="center", fill=fg, font=font)
            y += h + gap
        return W, y - gap

    def draw(self, entries):
        self.cv.delete("all")
        return self._draw_lvdew(entries) if self.s.style == "lvdew" else self._draw_alexander(entries)

    # ---- main loop ---------------------------------------------------------------------------
    def _origin(self):
        if self.reader is not None and self.reader.g.h:
            o = self.reader.g.game_window_origin()
            if o:
                return o
        if self.s.preview:
            return 0, 0, self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        return None

    def _fast_poll(self):
        """Screenshot keys are checked every 8 ms so the overlay is gone before the capture happens."""
        if self.stop:
            return
        try:
            if self.s.hide_on_screenshot and self.shot.poll(self.s.screenshot_keys):
                if self.hide_until <= time.time():
                    log.info("Screenshot key pressed: hiding for %.1f s", self.s.screenshot_hide_seconds)
                self.hide_until = time.time() + self.s.screenshot_hide_seconds
                if self.visible:
                    self.root.withdraw()
                    self.visible = False
            self.root.after(8, self._fast_poll)
        except self.tk.TclError:
            pass

    def hide(self):
        if self.visible:
            self.root.withdraw()
            self.visible = False
            log.info("Overlay hidden")

    def _tick_body(self):
        s = self.s
        if not s.enabled or (s.hide_on_screenshot and time.time() < self.hide_until):
            self.hide()
            return
        if (s.only_when_focused and not s.preview and self.reader is not None and self.reader.g.h
                and not self.reader.g.foreground_ok()):
            self.hide()
            return
        if s.preview or self.reader is None:
            state, status = DEMO_MONSTERS, "preview"
        else:
            snap = self.reader.snapshot()
            status = snap["status"]
            state = quest_filter(snap["monsters"], self.table, s) if status == "ok" else []
        if status != self._last_status:
            log.info("Overlay sees reader status: %s", status)
            self._last_status = status
        entries = build_entries(state, self.table, s) if state else []
        origin = self._origin() if entries else None
        if origin != self._last_origin:
            log.info("Game window origin: %r", origin)
            self._last_origin = origin
        if not entries or origin is None:
            self.hide()
            return
        sig = (s.version, tuple((e["name"], e["cur"], e["max"], e["size"], e["crown"], e["capture"], e["hot"],
                                 e["pct"]) for e in entries))
        if sig != self._sig:
            self._sig = sig
            self.apply_opacity()
            self._size = self.draw(entries)
            self.cv.config(width=max(1, self._size[0]), height=max(1, self._size[1]))
        W, H = self._size
        ox, oy, gw, gh = origin
        fx, fy = effective_xy(s)
        px = ox + int((gw - W) * fx / 100) + s.fix_x
        py = oy + int((gh - H) * fy / 100) + s.fix_y
        geo = f"{max(1, W)}x{max(1, H)}+{px}+{py}"
        if geo != self._geo:
            self._geo = geo
            self.root.geometry(geo)
        if not self.visible:
            self.root.deiconify()
            self.root.attributes("-topmost", True)
            self.visible = True
            log.info("Overlay shown, geometry %s", geo)

    def tick(self):
        if self.stop:
            self.root.destroy()
            return
        try:
            self._tick_body()
        except self.tk.TclError:
            return                      # window was closed
        except Exception:
            log.exception("Unhandled exception while updating / drawing the overlay")
        try:
            self.root.after(max(10, int(self.s.update_time * 1000)), self.tick)
        except self.tk.TclError:
            pass

    def run(self):
        self.tick()
        self.root.mainloop()


# ----------------------------------------------------------------------------------------------
# Console menu: "type a key, press Enter". Paged (10 commands per page) or all options in one list.
# ----------------------------------------------------------------------------------------------
BANNER = """\
███████╗███████╗ ░█████╗░██╗░░░██╗███████╗██████╗░██╗░░░░░░█████╗░██╗░░░██╗
██╔════╝██╔════╝ ██╔══██╗██║░░░██║██╔════╝██╔══██╗██║░░░░░██╔══██╗╚██╗░██╔╝
█████╗░░██████╗░ ██║░░██║╚██╗░██╔╝█████╗░░██████╔╝██║░░░░░███████║░╚████╔╝░
██╔══╝░░╚════██╗ ██║░░██║░╚████╔╝░██╔══╝░░██╔══██╗██║░░░░░██╔══██║░░╚██╔╝░░
██║░░░░░██████╔╝ ╚█████╔╝░░╚██╔╝░░███████╗██║░░██║███████╗██║░░██║░░░██║░░░
╚═╝░░░░░╚═════╝░ ░╚════╝░░░░╚═╝░░░╚══════╝╚═╝░░╚═╝╚══════╝╚═╝░░╚═╝░░░╚═╝░░░"""
BANNER_PLAIN = "=== F5  HP / SIZE  OVERLAY ==="
ANSI = False
_CODES = {"cyan": "36", "green": "32", "yellow": "33", "red": "31", "dim": "90"}


def enable_ansi():
    """Colours in the console (Windows 10+ needs the virtual terminal flag)."""
    global ANSI
    try:
        if os.name == "nt":
            k = ctypes.windll.kernel32
            h = k.GetStdHandle(-11)
            mode = wt.DWORD()
            if k.GetConsoleMode(h, ctypes.byref(mode)) and k.SetConsoleMode(h, mode.value | 0x4):
                ANSI = True
        else:
            ANSI = sys.stdout.isatty()
    except Exception:
        ANSI = False


def paint(text, color):
    return f"\033[{_CODES[color]}m{text}\033[0m" if ANSI else text


def _T(key, attr, label):
    return {"key": key, "type": "toggle", "attr": attr, "label": label}


def _C(key, attr, label, vals):
    return {"key": key, "type": "cycle", "attr": attr, "label": label, "vals": vals}


def _N(key, attr, label, kind, lo, hi):
    return {"key": key, "type": "number", "attr": attr, "label": label, "kind": kind, "lo": lo, "hi": hi}


def _S(key, kind, label, mapping, lo=0, hi=100):
    """Style-aware option: mapping = {style: attribute}; kind 'color' or 'opacity'."""
    return {"key": key, "type": kind, "label": label, "map": mapping, "lo": lo, "hi": hi}


PAGES = [
    ("Display", [
        _T("o", "enabled", "overlay on / off"),
        _T("m", "show_small_monsters", "small monsters"),
        _T("z", "show_size", "monster size %"),
        _T("c", "show_crown", "crown (Small / Silver / Gold)"),
        _T("p", "show_hp_percentage", "HP percentage"),
        _T("h", "show_initial_hp", "max HP"),
        _T("k", "highlight_capturable", "capture range highlight"),
        _T("d", "hide_dead_small", "hide dead small monsters"),
        _C("t", "style", "style", ["lvdew", "alexander"]),
        _T("v", "preview", "preview with fake monsters (to place the overlay)"),
    ]),
    ("Look and position", [
        _N("f", "@fontsize", "text size (lvdew points / alexander pixels)", int, 6, 96),
        _C("w", "preset", "position preset (custom = use x and y)", PRESETS_LIST),
        _N("x", "x", "custom position X in % of the game window (100 = right)", float, 0, 100),
        _N("y", "y", "custom position Y in % (0 = top)", float, 0, 100),
        _T("r", "lv_bar", "lvdew: HP bar"),
        _C("l", "lv_bar_position", "lvdew: bar position", ["below", "above", "left", "right"]),
        _C("n", "lv_numbers", "lvdew: HP numbers", ["below", "inline", "off"]),
        _C("e", "lv_bar_mode", "lvdew: bar mode (simple / capture)", ["simple", "capture"]),
        _N("b", "lv_bar_width", "lvdew: bar width in pixels", int, 20, 1200),
        _N("g", "lv_bar_height", "lvdew: bar height in pixels", int, 2, 60),
    ]),
    ("Colours (name like red / gold, or #rrggbb)", [
        _S("tc", "color", "text colour", {"lvdew": "lv_text_color", "alexander": "colors.text_color"}),
        _S("tcc", "color", "text colour in the capture range",
           {"lvdew": "lv_text_capture_color", "alexander": "colors.capture_text_color"}),
        _S("bg", "color", "box colour (alexander: boxes, lvdew: optional box)",
           {"lvdew": "lv_box_color", "alexander": "colors.background_color"}),
        _S("bgc", "color", "alexander: box colour in the capture range",
           {"alexander": "colors.capture_background_color"}),
        _S("bc", "color", "lvdew: HP bar colour", {"lvdew": "lv_bar_color"}),
        _S("bcc", "color", "lvdew: HP bar colour in the capture range", {"lvdew": "lv_bar_capture_color"}),
        _S("bb", "color", "lvdew: empty part of the HP bar", {"lvdew": "lv_bar_back_color"}),
        _S("sc", "color", "lvdew: text shadow colour", {"lvdew": "lv_shadow_color"}),
        _S("fc", "color", "lvdew: frame colour", {"lvdew": "lv_frame_color"}),
    ]),
    ("Opacity 0-100 (in between = dither)", [
        _S("op", "opacity", "whole overlay (the only way to fade the text)",
           {"lvdew": "lv_opacity", "alexander": "window_opacity"}, 20, 100),
        _S("bgo", "opacity", "boxes (lvdew: optional box, 0 = none)",
           {"lvdew": "lv_box_opacity", "alexander": "background_opacity"}),
        _S("bgco", "opacity", "alexander: boxes in the capture range", {"alexander": "capture_background_opacity"}),
        _S("bo", "opacity", "lvdew: filled part of the HP bar", {"lvdew": "lv_bar_opacity"}),
        _S("bbo", "opacity", "lvdew: empty part of the HP bar", {"lvdew": "lv_bar_back_opacity"}),
        _S("fo", "opacity", "lvdew: frame", {"lvdew": "lv_frame_opacity"}),
    ]),
    ("When to show / system", [
        _T("j", "only_in_quest", "hide the overlay outside quests (town)"),
        _T("a", "only_when_focused", "only while the game window is focused"),
        _T("sh", "hide_on_screenshot", "hide while taking screenshots"),
        _T("cl", "clear_screen", "clear this console after every command"),
        _T("bn", "banner", "text art title above the menu"),
        _T("ad", "run_as_admin", "ask for Administrator when started (next start)"),
        _T("i", "log", "write a log file (f5_overlay.log)"),
        _N("u", "@interval", "read / redraw interval in ms (lower = faster)", int, 10, 2000),
    ]),
]
SPECS = {c["key"]: c for _title, cmds in PAGES for c in cmds}


def _value(s, c):
    t = c["type"]
    if t == "toggle":
        return "ON" if getattr(s, c["attr"]) else "off"
    if t == "cycle":
        return str(getattr(s, c["attr"]))
    if t in ("color", "opacity"):
        attr = c["map"].get(s.style)
        return "-" if attr is None else str(_getattr_path(s, attr))
    a = c["attr"]
    if a == "@fontsize":
        v = s.lv_text_size if s.style == "lvdew" else s.font_size
    elif a == "@interval":
        v = int(round(s.update_time * 1000))
    else:
        v = getattr(s, a)
    return f"{v:g}"


def _rows(s, cmds):
    return [f"  {c['key']:<6}{c['label']:<58} {_value(s, c)}" for c in cmds]


def menu_text(s, page=0, status=""):
    allmode = s.menu_mode == "all"
    page %= len(PAGES)
    lines = [""]
    if s.banner:
        lines += [paint(BANNER, "cyan")]
    lines += ["", "  Type a key and press Enter. Numbers / colours: key + value, e.g.  f 12   tc gold   op 80",
              f"  Style in use: {s.style}  ('-' = the option does not exist in this style)", ""]
    if allmode:
        for i, (title, cmds) in enumerate(PAGES):
            lines.append(paint(f" {title}", "yellow"))
            lines += _rows(s, cmds)
            lines.append("")
        lines += ["  0  back to pages     s  save settings to f5_overlay.ini     q  quit     Enter  refresh"]
    else:
        title, cmds = PAGES[page]
        lines.append(paint(f" Page {page + 1}/{len(PAGES)}: {title}", "yellow"))
        lines += _rows(s, cmds)
        lines += ["", f"  >  next page    <  previous page    1-{len(PAGES)}  jump to a page    0  list ALL options",
                  "  s  save settings to f5_overlay.ini     q  quit     Enter  refresh"]
    lines.append("")
    if status:
        lines.append("  " + status)
    if s.warn:
        lines.append("  " + paint(s.warn, "red"))
    return "\n".join(lines)


def status_line(reader):
    if reader is None:
        return "Preview mode (no game)"
    snap = reader.snapshot()
    n = len(snap["monsters"])
    ok = snap["status"] == "ok"
    return paint(f"Game: {snap['status']}" + (f" - {n} monster(s) read" if ok else ""),
                 "green" if ok else "yellow")


def _apply_log(s, base_dir):
    if s.log:
        path = s.log_file if os.path.isabs(s.log_file) else os.path.join(base_dir, s.log_file)
        if not enable_logging(path, s.log_level):
            s.log = False
    else:
        disable_logging()


_COLOR_RE = re.compile(r"#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})|[A-Za-z][A-Za-z0-9]*")


def handle_command(line, s, reader=None, base_dir=".", ui=None):
    """Apply one typed command. Returns 'quit', or a message to print (may be empty).
    ui = {"page": n} is the menu page, changed by > < and the digits."""
    ui = ui if ui is not None else {"page": 0}
    s.warn = ""
    line = line.strip()
    if not line:
        return ""
    parts = line.split()
    key = parts[0].lower()
    m = re.fullmatch(r"([a-z]+)(-?\d+(?:\.\d+)?)", key)
    if m and m.group(1) in SPECS:                      # "f22" works as well as "f 22"
        parts, key = [m.group(1), m.group(2)], m.group(1)
    log.info("menu command: %r", line)
    if key == "q":
        return "quit"
    if key == "s":
        return f"saved to {s.path}" if s.save() else "could not write the ini file"
    if key in ("0", "all", "pages"):
        s.menu_mode = "all" if (key == "all" or (key == "0" and s.menu_mode == "pages")) else "pages"
        return ""
    if key in (">", "+", "next"):
        s.menu_mode, ui["page"] = "pages", (ui["page"] + 1) % len(PAGES)
        return ""
    if key in ("<", "-", "prev", "previous"):
        s.menu_mode, ui["page"] = "pages", (ui["page"] - 1) % len(PAGES)
        return ""
    if key.isdigit() and 1 <= int(key) <= len(PAGES) and len(parts) == 1:
        s.menu_mode, ui["page"] = "pages", int(key) - 1
        return ""
    c = SPECS.get(key)
    if c is None:
        return f"unknown command '{line}'"
    t = c["type"]
    if t == "toggle":
        setattr(s, c["attr"], not getattr(s, c["attr"]))
        if c["attr"] == "log":
            _apply_log(s, base_dir)
    elif t == "cycle":
        cur, vals = getattr(s, c["attr"]), c["vals"]
        setattr(s, c["attr"], vals[(vals.index(cur) + 1) % len(vals)] if cur in vals else vals[0])
    elif t in ("color", "opacity"):
        attr = c["map"].get(s.style)
        if attr is None:
            return f"'{key}' does not exist in the {s.style} style (switch style with t)"
        if len(parts) < 2:
            return f"give a value, for example:  {key} " + ("gold" if t == "color" else "80")
        if t == "color":
            val = parts[1] if _COLOR_RE.fullmatch(parts[1]) else None
            if val is None:
                return "colour must be a name (red, gold, darkslategray ...) or #rrggbb"
            _setattr_path(s, attr, val)
        else:
            try:
                _setattr_path(s, attr, int(max(c["lo"], min(c["hi"], float(parts[1])))))
            except ValueError:
                return "that is not a number"
    else:
        if len(parts) < 2:
            return f"give a value, for example:  {key} 20"
        try:
            v = max(c["lo"], min(c["hi"], c["kind"](float(parts[1]))))
        except ValueError:
            return "that is not a number"
        a = c["attr"]
        if a == "@fontsize":
            setattr(s, "lv_text_size" if s.style == "lvdew" else "font_size", v)
        elif a == "@interval":
            s.update_time = v / 1000.0
            if reader:
                reader.interval = s.update_time
        else:
            if a in ("x", "y") and s.preset != "custom":     # start from where the preset put it
                s.x, s.y = effective_xy(s)
                s.preset = "custom"
            setattr(s, a, v)
    s.version += 1
    return ""


def clear_console(s):
    if s.clear_screen:
        os.system("cls" if os.name == "nt" else "clear")


def _show_menu(s, ui, reader):
    clear_console(s)
    try:
        print(menu_text(s, ui["page"], status_line(reader)))
    except UnicodeEncodeError:                         # console cannot show the block characters
        print(menu_text(s, ui["page"], status_line(reader)).replace(paint(BANNER, "cyan"), BANNER_PLAIN))


def console_thread(s, reader, win, base_dir):
    ui = {"page": 0}
    enable_ansi()
    _show_menu(s, ui, reader)
    while True:
        try:
            line = input("> ")
        except (EOFError, KeyboardInterrupt):
            win.stop = True
            return
        res = handle_command(line, s, reader, base_dir, ui)
        if res == "quit":
            win.stop = True
            return
        _show_menu(s, ui, reader)
        if res:
            print("  ->", res)


def run_overlay(settings, table, reader, base_dir="."):
    try:
        win = OverlayWindow(settings, table, reader)
    except ImportError:
        print("tkinter is missing. Reinstall Python from python.org and keep 'tcl/tk and IDLE' ticked.")
        raise

    def on_ctrl_c(*_):
        if not win.stop:
            print("\nClosing...")
        win.stop = True
    signal.signal(signal.SIGINT, on_ctrl_c)          # Ctrl+C closes cleanly, no traceback
    if hasattr(signal, "SIGBREAK"):
        signal.signal(signal.SIGBREAK, on_ctrl_c)
    threading.Thread(target=console_thread, args=(settings, reader, win, base_dir), daemon=True).start()
    try:
        win.run()
    except KeyboardInterrupt:
        pass
    log.info("Closed")
    print("Closed.")


def _console_loop(reader, monsters, count=None, once=False):
    n, t0 = 0, time.time()
    while True:
        s = reader.snapshot()
        line = [f"[{s['status']}]"]
        for m in s["monsters"]:
            name = monsters.get(m["id"], (f"#{m['id']}",))[0]
            line.append(f"{name} {m['cur']}/{m['max']} size={m['size']}%")
        print("  ".join(line))
        n += 1
        if count and n >= count:
            return
        if once and ((s["status"] == "ok" and time.time() - t0 > 1) or time.time() - t0 > 30):
            return
        time.sleep(1)


def ensure_admin(args, settings):
    """Relaunch this script elevated (one UAC prompt). Returns True when the elevated copy was started."""
    if os.name != "nt" or not settings.run_as_admin or any(a in args for a in ("--no-admin", "--elevated", "--demo")):
        return False
    try:
        if ctypes.windll.shell32.IsUserAnAdmin():
            return False
        import subprocess
        params = subprocess.list2cmdline([os.path.abspath(__file__)] + args + ["--elevated"])
        shell = ctypes.windll.shell32
        shell.ShellExecuteW.restype = ctypes.c_void_p
        rc = shell.ShellExecuteW(None, "runas", sys.executable, params, None, 1)
    except Exception as e:
        print("Could not ask for Administrator rights:", e)
        return False
    if (rc or 0) > 32:
        print("Restarting as Administrator in a new window...")
        return True
    print("Administrator permission was not given - continuing without it "
          "(this only matters if the game runs as Administrator).")
    return False


def main():
    args = sys.argv[1:]
    here = os.path.dirname(os.path.abspath(__file__))
    if os.name == "nt":
        try:
            ctypes.windll.kernel32.SetConsoleTitleW("F5 HP / Size overlay  (Ctrl+C or close this window to quit)")
        except Exception:
            pass
    settings = Settings(os.path.join(here, "f5_overlay.ini"))
    if "--style" in args:
        settings.style = args[args.index("--style") + 1].lower()
    if "--log" in args:
        settings.log = True
        i = args.index("--log")
        if i + 1 < len(args) and not args[i + 1].startswith("--"):
            settings.log_file = args[i + 1]
    if "--debug" in args:
        settings.log, settings.log_level = True, "debug"
    if ensure_admin(args, settings):
        return
    _apply_log(settings, here)
    cfg_path = args[args.index("--config") + 1] if "--config" in args else os.path.join(here, "Config.txt")
    table = load_monsters(cfg_path)
    log.info("Starting; config=%s monsters=%d style=%s", settings.path, len(table), settings.style)
    if "--demo" in args:
        settings.preview = True
        settings.run_as_admin = False
        return run_overlay(settings, table, None, here)
    if os.name != "nt":
        print("This tool reads the game's memory through the Windows API, so it only runs on Windows.")
        return
    reader = Reader(table, settings.update_time, debug="--debug" in args)
    reader.start()
    print("[waiting for mhf.exe]")
    if any(a in args for a in ("--console", "--once", "--count")):
        count = int(args[args.index("--count") + 1]) if "--count" in args else None
        try:
            _console_loop(reader, table, count, "--once" in args)
        except KeyboardInterrupt:
            pass
    else:
        run_overlay(settings, table, reader, here)


def _pause():
    """Keep the console open when the script was started by double click."""
    try:
        input("\nPress Enter to close...")
    except (EOFError, KeyboardInterrupt):
        pass


if __name__ == "__main__":
    try:
        main()
    except Exception:
        log.exception("Fatal error")
        import traceback
        traceback.print_exc()
        _pause()
