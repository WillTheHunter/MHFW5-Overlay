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
# Status build-up offsets inside a monster struct. Taken from Relial's mhfz-monster-viewer for the G1 client, whose
# monster struct (size 0xD80, HP at 0x614 / 0x6AC) matches F5. NOT yet confirmed on F5: check with the log.
STATUS_DEFS = [   # key, label, threshold offset, current offset, colour
    ("poison", "Poison", 0x888, 0x88A, "#c084fc"),
    ("para", "Para", 0x880, 0x886, "#ffb347"),
    ("sleep", "Sleep", 0x86A, 0x86C, "#6cb8ff"),
    ("ko", "KO", 0xA64, 0x872, "#ffe066"),
    ("tranq", "Tranq", 0x878, 0x87A, "#66e08a"),
]
OFF_POISON_DUR, OFF_ATK, OFF_DEF = 0x890, 0x898, 0x89C
# Quest timer (found with f5_timer_finder.py on F5): u32 frame counters, 30 per second, inside mhfo.dll
TIMER_REMAIN_RVA = 0x54ECA20       # frames left; the quest limit (u32, frames) follows right after it
TIMER_ELAPSED_RVA = 0x4B31A78      # frames since the quest started (the same value !timer prints as (1658f))
# Quest-end carving countdown (60 s or less): found as a value that mirrors the quest time during the quest and
# then jumps to 1800 frames and counts down. A heap object whose pointer sits at a fixed place in mhfo.dll.
HUD_PTR_RVA, HUD_TIMER_OFF = 0x546C58C, 0x3D8
CARVE_MAX_FRAMES = 5400            # 3 minutes: bigger values are not a carving countdown
HOLD_SECONDS = 3.0          # keep the last good monster list this long when a read comes back empty
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
    ("General", "show_small_monsters", "show_small_monsters", "false", "bool",
     "also list small monsters (Velociprey, Hermitaur ...) below the large ones"),
    ("General", "hide_dead_small_monsters", "hide_dead_small", "true", "bool", "hide small monsters at 0 HP"),
    ("General", "show_size", "show_size", "true", "bool", "show the quest size of large monsters"),
    ("General", "show_crown", "show_crown", "true", "bool",
     "write Small / Silver / Gold next to the name when the size is inside the crown range"),
    ("General", "show_hp_percentage", "show_hp_percentage", "true", "bool", "show HP as a percentage"),
    ("General", "show_initial_hp", "show_initial_hp", "true", "bool", "show the maximum HP next to the current HP"),
    ("General", "highlight_capturable", "highlight_capturable", "true", "bool",
     "yellow text / bar (lvdew) or capture colours (alexander) while HP is inside the capture range"),
    ("General", "show_icons", "show_icons", "true", "bool",
     "lvdew style: small / silver / gold crown icon at the end of the name line; capture icon and skull (bones) "
     "icon at the end of the HP line. Off = crown written as text, no capture / dead icons"),
    ("General", "show_timer", "show_timer", "true", "bool",
     "show the quest time above the monsters (read from the game, 30 frames = 1 second)"),
    ("General", "show_carve_timer", "show_carve_timer", "true", "bool",
     "after a quest: show the carving countdown (60 seconds, or shorter in some quests) instead of the quest time"),
    ("General", "stack_mode", "stack_mode", "game", "game|always",
     "game = the overlay sits just above the game window and goes behind other programs when you switch away; "
     "always = it stays on top of every window"),
    ("General", "timer_direction", "timer_direction", "up", "up|down",
     "up = time since the quest started, like !timer (0:55 / 50:00); down = time left (49:05 / 50:00)"),
    ("General", "low_hp_percent", "low_hp_percent", "0", "float:0:100",
     "also highlight large monsters that have no capture range once HP is below this percent (0 = never)"),

    ("General", "only_in_quest", "only_in_quest", "true", "bool",
     "hide the overlay outside quests (in town the game keeps odd entries). It needs at least one known large "
     "monster; unknown ids are ignored. Switch off if the overlay stays hidden in a real quest"),
    ("General", "only_when_focused", "only_when_focused", "false", "bool",
     "show the overlay only while the game window is the active window (it is always hidden while this console / terminal is active)"),
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
    ("Layout", "x", "x", "1", "float:0:100", None),
    ("Layout", "y", "y", "50", "float:0:100", None),
    ("Layout", "fix_x", "fix_x", "0", "int:-5000:5000", "extra pixels added to the position (fine tuning)"),
    ("Layout", "fix_y", "fix_y", "0", "int:-5000:5000", None),

    ("Lvdew", None, None, None, None, "Settings for style = lvdew"),
    ("Lvdew", "text_size", "lv_text_size", "9", "int:6:72", "text size in points"),
    ("Lvdew", "font_family", "lv_font_family", "Segoe UI", "str", "font name (must be installed)"),
    ("Lvdew", "font_weight", "lv_font_weight", "bold", "normal|bold", None),
    ("Lvdew", "numbers", "lv_numbers", "below", "below|inline|off",
     "HP numbers: below = own line under the name, inline = same line as the name, off = hidden"),
    ("Lvdew", "show_bar", "lv_bar", "true", "bool", "draw the HP bar"),
    ("Lvdew", "bar_mode", "lv_bar_mode", "simple", "simple|capture",
     "simple = whole bar turns yellow in the capture range; capture = bar stays green and the capture range "
     "part of it is painted yellow"),
    ("Lvdew", "icon_size", "lv_icon_size", "0", "int:0:64",
     "icon size in pixels. 0 = automatic (same as the text height). Built in sizes: 12 16 20 24 32 48, the nearest is used"),
    ("Lvdew", "bar_width", "lv_bar_width", "180", "int:20:1200", "bar length in pixels"),
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

    ("Status", None, None, None, None,
     "Status build-ups of large monsters, read from the game like Relial's monster viewer does (poison, paralysis, "
     "sleep, KO = stun, tranquilizer). The offsets come from the G1 client and still have to be confirmed on F5"),
    ("Status", "mode", "status_mode", "active", "off|active|all",
     "off = nothing, active = only statuses that have started to build up (value above 0), all = every status always"),
    ("Status", "always_show", "status_always", "false", "bool",
     "show every status all the time (a status the monster cannot get shows as '-'), not only once it is building up"),
    ("Status", "poison", "st_poison", "true", "bool", "show poison"),
    ("Status", "para", "st_para", "true", "bool", "show paralysis"),
    ("Status", "sleep", "st_sleep", "true", "bool", "show sleep"),
    ("Status", "ko", "st_ko", "true", "bool", "show KO (the game calls it stun)"),
    ("Status", "tranq", "st_tranq", "true", "bool", "show tranquilizer"),
    ("Status", "multipliers", "show_multipliers", "false", "bool",
     "show the monster's attack and defense multiplier (rage / enrage, hardened states)"),
    ("Status", "effective_hp", "show_effective_hp", "false", "bool",
     "show HP divided by the defense multiplier (effective HP) next to the HP numbers"),
    ("Status", "poison_color", "st_poison_color", "#c084fc", "str", "colours: names or #rrggbb"),
    ("Status", "para_color", "st_para_color", "#ffb347", "str", None),
    ("Status", "sleep_color", "st_sleep_color", "#6cb8ff", "str", None),
    ("Status", "ko_color", "st_ko_color", "#ffe066", "str", None),
    ("Status", "tranq_color", "st_tranq_color", "#66e08a", "str", None),

    ("Launch", None, None, None, None,
     "Start the game together with this overlay. Only happens when mhf.exe is not already running. "
     "NOTE: the password is stored here as plain text"),
    ("Launch", "autostart", "autostart", "false", "bool", "start the game when this program starts"),
    ("Launch", "game_exe", "game_exe", "", "str", "full path of the game, for example D:\\Extra\\f5\\mhf.exe"),
    ("Launch", "username", "username", "", "str", "login name, typed into the launcher field for you"),
    ("Launch", "password", "password", "", "str", "password, typed into the launcher field for you"),
    ("Launch", "user_field", "user_field", "0", "int:0:30",
     "which edit control of the launcher gets the login name. 0 = automatic (the field right above the password). "
     "The log lists the launcher's controls with numbers: put the right number here if the name lands in the wrong place"),
    ("Launch", "login_mode", "login_mode", "fill", "fill|autolc",
     "fill = like Lvdew: fill in the launcher's fields and you press Login yourself. "
     "autolc = start the game with /AUTOLC username password (some launchers then pick server 2 instead of 1)"),

    ("Debug", None, None, None, None,
     "A log file helps when something does not work. Off by default; also switchable with 'i' in the menu"),
    ("Debug", "log", "log", "false", "bool", "write the log file"),
    ("Debug", "log_file", "log_file", "f5_overlay.log", "str", "relative paths are next to f5_overlay.py"),
    ("Debug", "log_level", "log_level", "info", "info|debug", "debug also logs every change of the monster list"),
]
SECTION_ORDER = ["General", "Layout", "Lvdew", "Alexander", "Status", "Launch", "Debug"]


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
             "dead": bool(is_large and cur <= 0),
             "capture": bool(is_large and cur > 0 and settings.highlight_capturable and in_range),
             "hot": bool(is_large and settings.highlight_capturable and (in_range or low))}
        e["status"], e["mult"], e["eff"] = [], "", None
        if is_large:
            dfn, atk = m.get("dfn"), m.get("atk")
            if settings.status_mode != "off":
                show_all = settings.status_mode == "all" or settings.status_always
                for key, label, _t, _c, _col in STATUS_DEFS:
                    if not getattr(settings, "st_" + key):
                        continue
                    v = (m.get("st") or {}).get(key)
                    colr = getattr(settings, "st_%s_color" % key)
                    if v and (show_all or v[0] > 0):
                        extra = ""
                        if key == "poison" and m.get("pdur"):
                            extra = " %ds" % (m["pdur"] // 30)
                        e["status"].append((f"{label} {v[0]}/{v[1]}{extra}", colr))
                    elif not v and show_all:
                        e["status"].append((f"{label} -", colr))
            if settings.show_multipliers and atk is not None and dfn is not None:
                e["mult"] = f"ATK x{atk:.2f}  DEF x{dfn:.2f}"
            if settings.show_effective_hp and dfn:
                e["eff"] = (int(round(cur / dfn)), int(round(mx / dfn)))
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
    if e.get("eff"):
        t += f" | eff {e['eff'][0]}/{e['eff'][1]}"
    return t


def timer_text(timer, settings):
    """'12:05 / 50:00' from (limit_frames, remaining_frames), or '' when there is no timer / it is switched off."""
    if not timer or not settings.show_timer:
        return ""
    limit, remain = timer
    shown = limit - remain if settings.timer_direction == "up" else remain

    def fmt(frames):
        sec = max(0, frames) // 30
        h, m, s = sec // 3600, sec // 60 % 60, sec % 60
        return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"
    return f"{fmt(shown)} / {fmt(limit)}"


def extra_text(e):
    """Second line (status build-ups, multipliers) of an entry, plain text; '' when there is nothing."""
    parts = [txt for txt, _c in e.get("status", [])]
    if e.get("mult"):
        parts.append(e["mult"])
    return "  ".join(parts)


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
    u32.SetWindowPos.argtypes = [_P, _P, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, wt.UINT]
    if hasattr(u32, "SetWindowLongPtrW"):
        u32.SetWindowLongPtrW.argtypes = [_P, ctypes.c_int, _P]
        u32.SetWindowLongPtrW.restype = _P
    u32.GetWindowLongW.argtypes = [_P, ctypes.c_int]
    u32.GetWindowLongW.restype = ctypes.c_long
    u32.SetWindowLongW.argtypes = [_P, ctypes.c_int, ctypes.c_long]
    u32.SetWindowLongW.restype = ctypes.c_long
    u32.GetForegroundWindow.restype = _P
    u32.GetAsyncKeyState.argtypes = [ctypes.c_int]
    u32.GetAsyncKeyState.restype = ctypes.c_short
    k32.GetConsoleWindow.restype = _P
    u32.GetClassNameW.argtypes = [_P, _P, ctypes.c_int]
    u32.EnumChildWindows.argtypes = [_P, _P, _P]
    u32.SendMessageW.argtypes = [_P, wt.UINT, ctypes.c_size_t, _P]
    u32.SendMessageW.restype = ctypes.c_ssize_t
    u32.GetWindowRect.argtypes = [_P, _P]
    u32.IsWindowEnabled.argtypes = [_P]
    u32.GetDlgCtrlID.argtypes = [_P]
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

    def _terminal_pids(self):
        """Process ids of this program and of the programs that started it (cmd, PowerShell, Windows Terminal ...)."""
        if getattr(self, "_term_pids", None) is None:
            pids, parent = {os.getpid()}, {}
            try:
                snap = k32.CreateToolhelp32Snapshot(0x2, 0)
                if snap not in _BAD:
                    e = PROCESSENTRY32W()
                    e.dwSize = ctypes.sizeof(e)
                    ok = k32.Process32FirstW(snap, ctypes.byref(e))
                    while ok:
                        parent[e.th32ProcessID] = e.th32ParentProcessID
                        ok = k32.Process32NextW(snap, ctypes.byref(e))
                    k32.CloseHandle(snap)
                p = os.getpid()
                for _ in range(6):
                    p = parent.get(p)
                    if not p or p in pids:
                        break
                    pids.add(p)
            except Exception:
                pass
            self._term_pids = pids
        return self._term_pids

    def foreground_kind(self):
        """What has the focus: 'game', 'console' (this program's window / any terminal) or 'other'."""
        fg = u32.GetForegroundWindow()
        if not fg:
            return "other"
        pid = wt.DWORD()
        u32.GetWindowThreadProcessId(fg, ctypes.byref(pid))
        if pid.value == self.pid:
            return "game"
        if fg == k32.GetConsoleWindow() or pid.value in self._terminal_pids():
            return "console"
        buf = ctypes.create_unicode_buffer(128)
        u32.GetClassNameW(fg, buf, 128)
        if buf.value in ("ConsoleWindowClass", "CASCADIA_HOSTING_WINDOW_CLASS", "PseudoConsoleWindow"):
            return "console"
        return "other"

    def foreground_ok(self):
        """True when the game window is the active window."""
        return self.foreground_kind() == "game"

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
# Start the game and fill in the launcher (like Lvdew)
# ----------------------------------------------------------------------------------------------
def find_pids(exe_name):
    """Process ids of all running programs with this file name (empty list if none / not Windows)."""
    pids = []
    if os.name != "nt":
        return pids
    snap = k32.CreateToolhelp32Snapshot(0x2, 0)
    if snap in _BAD:
        return pids
    e = PROCESSENTRY32W()
    e.dwSize = ctypes.sizeof(e)
    ok = k32.Process32FirstW(snap, ctypes.byref(e))
    while ok:
        if e.szExeFile.lower() == exe_name.lower():
            pids.append(e.th32ProcessID)
        ok = k32.Process32NextW(snap, ctypes.byref(e))
    k32.CloseHandle(snap)
    return pids


def _windows_of(pids):
    found = []
    cb_type = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def cb(hwnd, _):
        pid = wt.DWORD()
        u32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids and u32.IsWindowVisible(hwnd):
            found.append(hwnd)
        return True

    u32.EnumWindows(cb_type(cb), 0)
    return found


def _class_of(h):
    buf = ctypes.create_unicode_buffer(128)
    u32.GetClassNameW(h, buf, 128)
    return buf.value


def _edit_controls(hwnd):
    """Edit-like child controls of a window, top to bottom: dicts with h (handle to write to), cls, password,
    top, left, id, text, and 'ok' (visible, enabled, not read-only)."""
    out = []
    cb_type = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def cb(h, _):
        cls = _class_of(h)
        low = cls.lower()
        if "edit" in low or "textbox" in low:
            style = u32.GetWindowLongW(h, -16) & 0xFFFFFFFF
            r = wt.RECT()
            u32.GetWindowRect(h, ctypes.byref(r))
            parent = u32.GetParent(h)
            target = parent if parent and _class_of(parent).lower() == "combobox" else h   # drop-down name list
            out.append({"h": target, "inner": h, "cls": cls + ("(in ComboBox)" if target != h else ""),
                        "password": bool(style & 0x20), "top": r.top, "left": r.left,
                        "w": r.right - r.left, "id": u32.GetDlgCtrlID(h), "text": _get_text(target),
                        "ok": bool(u32.IsWindowVisible(h)) and bool(u32.IsWindowEnabled(h))
                              and not (style & 0x800) and (r.right - r.left) > 20})
        return True

    cbf = cb_type(cb)
    u32.EnumChildWindows(hwnd, cbf, 0)
    out.sort(key=lambda d: (d["top"], d["left"]))
    return out


def pick_fields(edits, user_field=0):
    """-> (user_control, password_control) from the list of edit controls."""
    usable = [e for e in edits if e["ok"]]
    pass_ed = next((e for e in usable if e["password"]), None)
    if user_field:
        user_ed = edits[user_field - 1] if 0 < user_field <= len(edits) else None
        return user_ed, pass_ed
    cands = [e for e in usable if not e["password"]]
    if pass_ed is not None:
        above = [e for e in cands if e["top"] < pass_ed["top"]]
        user_ed = max(above, key=lambda e: e["top"]) if above else None      # the field right above the password
    else:
        user_ed = cands[0] if cands else None
    return user_ed, pass_ed


def _child_classes(hwnd):
    out = []
    cb_type = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

    def cb(h, _):
        buf = ctypes.create_unicode_buffer(128)
        u32.GetClassNameW(h, buf, 128)
        out.append(buf.value)
        return True

    cbf = cb_type(cb)
    u32.EnumChildWindows(hwnd, cbf, 0)
    return out


def _get_text(h):
    buf = ctypes.create_unicode_buffer(512)
    u32.SendMessageW(h, 0x000D, 512, buf)                  # WM_GETTEXT
    return buf.value


def _set_text(h, text):
    u32.SendMessageW(h, 0x000C, 0, ctypes.create_unicode_buffer(text))     # WM_SETTEXT


def fill_launcher(s, stop_after=90.0):
    """Wait for the launcher window of mhf.exe and type the user name / password into its fields."""
    t0, logged, logged_pick, done, listed = time.time(), False, False, 0, set()
    while time.time() - t0 < stop_after:
        time.sleep(0.25)
        pids = set(find_pids(TARGET_PROC))
        if not pids:
            continue
        for hwnd in _windows_of(pids):
            edits = _edit_controls(hwnd)
            if not edits:
                if time.time() - t0 > 8 and hwnd not in listed:
                    listed.add(hwnd)
                    buf = ctypes.create_unicode_buffer(128)
                    u32.GetClassNameW(hwnd, buf, 128)
                    log.info("Window 0x%X (%s) has no edit controls; children: %s", hwnd, buf.value,
                             ", ".join(sorted(set(_child_classes(hwnd)))) or "none")
                continue
            if not logged:
                logged = True
                log.info("Launcher window 0x%X (%s) edit controls, top to bottom:", hwnd, _class_of(hwnd))
                for i, e in enumerate(edits, 1):
                    log.info("  #%d class=%s id=%d x=%d y=%d width=%d password=%s usable=%s text=%r", i, e["cls"],
                             e["id"], e["left"], e["top"], e["w"], e["password"], e["ok"],
                             "***" if e["password"] and e["text"] else e["text"])
                print("[launcher found, filling in the login fields]")
            user_ed, pass_ed = pick_fields(edits, s.user_field)
            if not logged_pick:
                logged_pick = True
                log.info("Login name goes to #%s, password to #%s",
                         edits.index(user_ed) + 1 if user_ed in edits else None,
                         edits.index(pass_ed) + 1 if pass_ed in edits else None)
            ok = True
            if s.username and user_ed:
                if _get_text(user_ed["h"]) != s.username:
                    _set_text(user_ed["h"], s.username)
                    ok = False
            if s.password and pass_ed:
                if _get_text(pass_ed["h"]) != s.password:
                    _set_text(pass_ed["h"], s.password)
                    ok = False
            done = done + 1 if ok else 0
            if done >= 12:                                  # stable for about 3 seconds
                log.info("Launcher fields filled")
                return True
    log.info("Launcher fields: gave up (%s)", "window never found" if not logged else "not stable")
    return False


def start_game(s, force=False):
    """Start the game if mhf.exe is not running yet. Returns a message for the menu."""
    if os.name != "nt":
        return "starting the game only works on Windows"
    if not s.game_exe:
        return "set the game path first (key ge)"
    if not os.path.isfile(s.game_exe):
        return f"game file not found: {s.game_exe}"
    if find_pids(TARGET_PROC):
        return "mhf.exe is already running - not started again"
    import subprocess
    cmd = [s.game_exe]
    if s.login_mode == "autolc" and s.username:
        cmd += ["/AUTOLC", s.username, s.password]
    try:
        subprocess.Popen(cmd, cwd=os.path.dirname(os.path.abspath(s.game_exe)))
    except OSError as e:
        log.exception("Could not start the game")
        return f"could not start the game: {e}" + (
            " (needs Administrator: start this program as Administrator)" if getattr(e, "winerror", 0) == 740 else "")
    log.info("Game started: %s (mode %s)", s.game_exe, s.login_mode)
    if s.login_mode == "fill" and (s.username or s.password):
        threading.Thread(target=fill_launcher, args=(s,), daemon=True, name="launcher").start()
    return "game started"


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
        self._raw_logged = set()
        self.timer = None            # (limit_frames, remaining_frames) or None
        self.carve = None            # frames left of the carving countdown after a quest, or None
        self._tm_last = None         # state of the quest timer / carving detection
        self._tm_change = 0.0
        self._tm_running = False
        self._hud_prev = None
        self._hud_dec = 0
        self._seen = {}              # (slot, id) -> consecutive reads in which the entry looked valid
        self._good = []              # last list that was shown
        self._good_t = 0.0
        self._diag_t = 0.0
        self._arr = None             # last array address that held live monsters
        self.why = ""                # reason of the last empty read (goes into the log)

    def read_timer(self):
        g = self.g
        base = g.mhfo[0]
        remain = g.u32(base + TIMER_REMAIN_RVA)
        limit = g.u32(base + TIMER_REMAIN_RVA + 4)
        if remain is None or limit is None or not (0 < limit <= 30 * 3600 * 4) or remain > limit:
            return None
        return limit, remain

    def read_carve(self, timer):
        """Carving countdown. Active only after the quest timer has stopped, while the game's HUD value counts down."""
        g = self.g
        now = time.time()
        remain = timer[1] if timer else None
        if remain is not None:
            if remain != self._tm_last:
                if self._tm_last is not None and remain < self._tm_last:
                    self._tm_running = True
                self._tm_last, self._tm_change = remain, now
        frozen = self._tm_running and now - self._tm_change > 1.0
        hud = None
        p = g.u32(g.mhfo[0] + HUD_PTR_RVA)
        if p and 0x10000 <= p < 0x7FFF0000:
            hud = g.u32(p + HUD_TIMER_OFF)
        if hud is not None and self._hud_prev is not None and hud != self._hud_prev:
            self._hud_dec = self._hud_dec + 1 if hud < self._hud_prev else 0
        if hud != self._hud_prev:
            self._hud_prev = hud
        if not (frozen and hud is not None and 0 < hud <= CARVE_MAX_FRAMES and hud != remain):
            self._hud_dec = 0
            if self.carve is not None:
                log.info("Carving countdown ended / not active (hud=%s, quest timer %s)", hud, timer)
            return None
        if self._hud_dec >= 3:
            if self.carve is None:
                log.info("Carving countdown started: %d frames (%.1f s), quest timer %s", hud, hud / 30.0, timer)
            return hud
        return self.carve if self.carve is not None and self._hud_dec else None

    def _read_status(self, blk):
        """Status build-ups, attack / defence multipliers of one monster struct (plausibility checked)."""
        st = {}
        for key, _label, o_thr, o_cur, _col in STATUS_DEFS:
            thr = struct.unpack_from("<h", blk, o_thr)[0]
            cur = struct.unpack_from("<h", blk, o_cur)[0]
            if 0 < thr <= 30000 and 0 <= cur <= 32000:
                st[key] = (cur, thr)
        dur = struct.unpack_from("<H", blk, OFF_POISON_DUR)[0]
        atk, dfn = struct.unpack_from("<ff", blk, OFF_ATK)
        return {"st": st, "pdur": dur if 0 < dur < 20000 else 0,
                "atk": atk if 0.01 < atk < 100 else None, "dfn": dfn if 0.01 < dfn < 100 else None}

    def read_monsters(self):
        g = self.g
        base = g.mhfo[0]
        key = g.u16(base + KEY_RVA)
        arr = None
        ptrs = []
        for rva in ARRAY_PTR_RVAS:
            p = g.u32(base + rva)
            ptrs.append(p)
            if p:
                # the first pointer that is set decides; an unrelated old array is never used as a fallback
                if g.read(p, 2) == b"\x01\x01":
                    arr = p
                break
        if arr is None and self._arr and g.read(self._arr, 2) == b"\x01\x01":
            arr = self._arr                            # pointer glitch: keep using the array that worked
            self.why = "pointer %s unusable, using remembered array 0x%X" % (
                [hex(x) if x else x for x in ptrs], arr)
        if arr is None or key is None:
            self.why = "no array: pointers=%s flags=%s key=%s" % (
                [hex(x) if x else x for x in ptrs],
                (g.read(ptrs[0], 2) or b"").hex() if ptrs and ptrs[0] else "-",
                None if key is None else hex(key))
            return None
        self._arr = arr
        total = STRIDE * (MAX_SLOTS - 1) + OFF_SIZE + 2
        blob = g.read(arr, total)                      # one syscall for the whole array
        if blob is not None and len(blob) < total:
            blob = None
        tbl = g.read(base + TABLE_RVA - 2 * (MAX_SLOTS - 1), 2 * MAX_SLOTS)   # all T values at once
        if tbl is None or len(tbl) < 2 * MAX_SLOTS:
            self.why = "T table unreadable"
            return None
        out = []
        rej = []
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
                rej.append("slot %d id %d hp-decode %d/%d (key %X T %X)" % (slot, blk[OFF_ID], cur, mx, key, t))
                continue
            size = struct.unpack_from("<H", blk, OFF_SIZE)[0]
            size = size if 10 <= size <= 400 else None
            mid = blk[OFF_ID]
            info = self.monsters.get(mid)
            if mid == 0 or (info is not None and info[3] and size is None):
                rej.append("slot %d id %d no size (raw %d)" % (slot, mid, size or 0))
                continue                     # a large monster always has a size: this slot is stale / garbage
            if info is None and size is None:
                continue
            mon = {"slot": slot, "id": mid, "cur": max(cur, 0), "max": mx, "size": size}
            try:
                mon.update(self._read_status(blk))
            except struct.error:
                pass
            out.append(mon)
        # an entry is trusted after it looked valid in two reads in a row (filters one-off garbage)
        seen = {}
        stable = []
        for m in out:
            k = (m["slot"], m["id"])
            seen[k] = self._seen.get(k, 0) + 1
            if seen[k] >= 2:
                stable.append(m)
        self._seen = seen
        self.why = "array 0x%X read ok, %d valid, %d stable; rejected: %s" % (
            arr, len(out), len(stable), "; ".join(rej[:4]) or "none")
        return stable

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
            for m in monsters:
                k = (m["slot"], m["id"])
                if k not in self._raw_logged and m.get("st") is not None:
                    self._raw_logged.add(k)
                    log.info("Status raw slot %d id %d: %s atk=%s def=%s poison_dur=%s", m["slot"], m["id"],
                             " ".join("%s=%d/%d" % (kk, v[0], v[1]) for kk, v in m["st"].items()) or "(none plausible)",
                             m["atk"], m["dfn"], m["pdur"])
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
                self.timer = self.read_timer()
                self.carve = self.read_carve(self.timer)
                now = time.time()
                if mons:
                    self._good, self._good_t = mons, now
                    self._set("ok", mons)
                elif self._good and now - self._good_t < HOLD_SECONDS:
                    if now - self._diag_t > 0.5:
                        self._diag_t = now
                        log.info("Empty read, holding last list. Reason: %s", self.why)
                    self._set("ok", self._good)          # short glitch: keep showing the last good list
                else:
                    if self._good:
                        log.warning("Monster list lost after %.1fs hold. Reason: %s", HOLD_SECONDS, self.why)
                    self._good = []
                    if mons is None:
                        self._set("game running, no quest")
                    else:
                        self._set("ok", [])
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
DEMO_TIMER = (90000, 90000 - 1658)
DEMO_MONSTERS = [
    {"slot": 0, "id": 27, "cur": 1600, "max": 1600, "size": 115, "pdur": 240,
     "st": {"poison": (120, 300), "para": (0, 200), "ko": (70, 150), "sleep": (45, 220), "tranq": (0, 100)},
     "atk": 1.5, "dfn": 0.8},
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
        self._icons = {}
        self.timer_str = ""
        self._stack_key = None
        self._last_origin = "unset"
        self._last_status = None
        self._last_fg = None
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
        if e.get("eff"):
            t += f"  eff {e['eff'][0]}/{e['eff'][1]}"
        return t

    def icon(self, name, size):
        """PhotoImage of a built in icon in the nearest built in size (kept, or Tk drops the picture)."""
        sz = min(ICON_SIZES, key=lambda v: abs(v - size))
        key = (name, sz)
        if key not in self._icons:
            try:
                self._icons[key] = self.tk.PhotoImage(master=self.root, data="".join(ICON_DATA[name][sz]),
                                                      format="png")
            except Exception:
                log.exception("Could not load icon %s", name)
                self._icons[key] = None
        return self._icons[key], sz

    def _draw_lvdew(self, entries):
        s, cv = self.s, self.cv
        font = self.font(s.lv_font_family, s.lv_text_size, s.lv_font_weight, "pt")
        lh0 = font.metrics("linespace")
        isz = s.lv_icon_size or lh0
        isz = min(ICON_SIZES, key=lambda v: abs(v - isz)) if s.show_icons else 0
        lh = max(lh0, isz)                      # line height: icons may be a little taller than the text
        dy = (lh - lh0) // 2
        icon_gap = 3

        def seg_w(seg):
            return isz + icon_gap if seg[0].startswith("\0") else font.measure(seg[0])
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
            marks = []                                   # status icons: capture / dead
            if s.show_icons:
                if e["crown"]:
                    segs.append((" ", col))
                    segs.append(("\0" + e["crown"].lower(), None))
                if e["dead"]:
                    marks.append(("\0dead", None))
                elif e["capture"]:
                    marks.append(("\0capture", None))
            elif e["crown"]:
                segs.append((" " + e["crown"], CROWN_COLORS.get(e["crown"], "#ffffff")))
            lines = []
            if s.lv_numbers == "inline":
                segs.append(("   " + self._lvdew_numbers(e), col))
                segs += marks
            lines.append(segs)
            if s.lv_numbers == "below":
                lines.append([(self._lvdew_numbers(e), col)] + ([(" ", col)] + marks if marks else []))
            elif s.lv_numbers == "off" and marks:
                segs += [(" ", col)] + marks
            if e.get("status"):
                st_segs = []
                for txt, c in e["status"]:
                    st_segs += [(txt, self.color(c, "#ffffff")), ("  ", col)]
                lines.append(st_segs[:-1])
            if e.get("mult"):
                lines.append([(e["mult"], col)])
            widths = [sum(seg_w(sg) for sg in ln) for ln in lines]
            tw, th = max(widths), lh * len(lines)
            gap = 3
            if not bar_on:
                rw, rh = tw, th
            elif pos in ("below", "above"):
                rw, rh = max(tw, bw), th + gap + bh
            else:
                rw, rh = tw + 8 + bw, max(th, bh)
            rows.append((e, lines, tw, th, rw, rh, hot))

        timer_h = (lh + s.lv_row_gap) if self.timer_str else 0
        W = max([r[4] for r in rows] + [font.measure(self.timer_str) if self.timer_str else 0]) + 2 * pad
        H = sum(r[5] for r in rows) + s.lv_row_gap * max(0, len(rows) - 1) + 2 * pad + timer_h
        frame_kw = self.shape(self.color(s.lv_frame_color, "#000000"), s.lv_frame_opacity)
        if s.lv_frame and rows and frame_kw:
            cv.create_rectangle(0, 0, W, H, **frame_kw)
        box_kw = self.shape(self.color(s.lv_box_color, "#000000"), s.lv_box_opacity)
        shadow_col = self.color(s.lv_shadow_color, "#000000")
        y = pad
        if self.timer_str:
            tcol = self.color(s.lv_text_color, "#ffffff")
            if s.lv_shadow:
                cv.create_text(pad + 1, pad + dy + 1, text=self.timer_str, anchor="nw", fill=shadow_col, font=font)
            cv.create_text(pad, pad + dy, text=self.timer_str, anchor="nw", fill=tcol, font=font)
            y += timer_h
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
                    if text.startswith("\0"):
                        img, _sz = self.icon(text[1:], isz)
                        if img is not None:
                            cv.create_image(cx, ty + i * lh + (lh - isz) // 2, image=img, anchor="nw")
                        cx += isz + icon_gap
                        continue
                    if s.lv_shadow:
                        cv.create_text(cx + 1, ty + i * lh + dy + 1, text=text, anchor="nw", fill=shadow_col, font=font)
                    cv.create_text(cx, ty + i * lh + dy, text=text, anchor="nw", fill=col, font=font)
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
        boxes = []
        if self.timer_str:
            boxes.append(({"capture": False}, self.timer_str if self.timer_str.startswith("Carve") else "Time " + self.timer_str, True))
        for e in entries:
            boxes.append((e, entry_text(e, s), False))
            xt = extra_text(e)
            if xt:
                boxes.append((e, xt, True))
        entries = [b[0] for b in boxes]
        texts = [b[1] for b in boxes]
        extras = [b[2] for b in boxes]
        widths = [font.measure(t) + 2 * padx for t in texts]
        W = max(widths)
        h = lh + 2 * pady
        y = 0
        for e, t, w, extra in zip(entries, texts, widths, extras):
            if s.align:
                w = W
            x = {"left": 0, "right": W - w}.get(s.orientation, (W - w) // 2)
            cap = e["capture"] and not extra
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

    def _stack(self):
        """z-order: 'game' = owned by the game window (always just above it, behind other programs when the
        game is not in front); 'always' (and the preview) = topmost."""
        s = self.s
        game_hwnd = None
        if os.name == "nt" and s.stack_mode == "game" and not s.preview and self.reader is not None:
            game_hwnd = self.reader.g._hwnd
            if game_hwnd and not u32.IsWindow(game_hwnd):
                game_hwnd = None
        key = ("game", game_hwnd) if game_hwnd else ("top",)
        if key == self._stack_key:
            return
        self._stack_key = key
        try:
            if game_hwnd:
                top = u32.GetParent(self.root.winfo_id()) or self.root.winfo_id()
                self.root.attributes("-topmost", False)
                setptr = getattr(u32, "SetWindowLongPtrW", None) or u32.SetWindowLongW
                setptr(top, -8, game_hwnd)                       # GWLP_HWNDPARENT: owner = game window
                u32.SetWindowPos(top, 0, 0, 0, 0, 0, 0x1 | 0x2 | 0x10)   # HWND_TOP, no move / size / activate
                log.info("Overlay stacked above the game window 0x%X", game_hwnd)
            else:
                self.root.attributes("-topmost", True)
                log.info("Overlay is topmost (mode %s)", "preview" if s.preview else s.stack_mode)
        except Exception:
            log.exception("Could not change the overlay stacking")
            self.root.attributes("-topmost", True)

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
        if not s.preview and self.reader is not None and self.reader.g.h:
            kind = self.reader.g.foreground_kind()
            if kind != self._last_fg:
                log.info("Foreground window: %s", kind)
                self._last_fg = kind
            # never draw over the menu console / terminal; with only_when_focused also hide for every other app
            if kind == "console" or (s.only_when_focused and kind != "game"):
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
        carve = None if (s.preview or self.reader is None or not s.show_carve_timer) else self.reader.carve
        if carve is not None:
            sec = max(0, carve) // 30
            self.timer_str = f"Carve {sec // 60}:{sec % 60:02d}"
        else:
            self.timer_str = timer_text(DEMO_TIMER if (s.preview or self.reader is None) else self.reader.timer, s)
        show = bool(entries) or carve is not None
        origin = self._origin() if show else None
        if origin != self._last_origin:
            log.info("Game window origin: %r", origin)
            self._last_origin = origin
        if not show or origin is None:
            self.hide()
            return
        sig = (s.version, self.timer_str, tuple((e["name"], e["cur"], e["max"], e["size"], e["crown"], e["capture"], e["dead"], e["hot"],
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
            self.visible = True
            self._stack_key = None
            log.info("Overlay shown, geometry %s", geo)
        self._stack()

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
PAGES.append(("Icons and starting the game with the overlay", [
    _T("ic", "show_icons", "crown / capture / dead icons (lvdew style)"),
    _N("is", "lv_icon_size", "icon size in pixels (0 = automatic)", int, 0, 64),
    _T("gs", "autostart", "start the game with this program (if not running)"),
    {"key": "ge", "type": "text", "attr": "game_exe", "label": "game path (full path of mhf.exe)"},
    {"key": "gu", "type": "text", "attr": "username", "label": "login name"},
    _N("gf", "user_field", "launcher field number for the name (0 = automatic, see log)", int, 0, 30),
    {"key": "gp", "type": "text", "attr": "password", "label": "password (kept as plain text in the ini)",
     "secret": True},
    _C("gm", "login_mode", "login: fill = fill the fields, autolc = /AUTOLC", ["fill", "autolc"]),
    {"key": "go", "type": "action", "attr": "", "label": "start the game now (if it is not running)"},
]))
PAGES.append(("Status build-ups (poison, para, sleep, KO, tranq), multipliers and quest timer", [
    _C("st", "status_mode", "show status build-ups: off / active (only above 0) / all", ["off", "active", "all"]),
    _T("sp", "st_poison", "poison"),
    _T("sa", "st_para", "paralysis"),
    _T("ss", "st_sleep", "sleep"),
    _T("sk", "st_ko", "KO (stun)"),
    _T("sq", "st_tranq", "tranquilizer"),
    _T("sm", "show_multipliers", "attack / defense multiplier"),
    _T("se", "show_effective_hp", "effective HP (HP / defense multiplier)"),
    _T("sv", "status_always", "always show every status ('-' = the monster has none)"),
]))
PAGES.append(("Quest timers and window", [
    _T("tm", "show_timer", "quest timer"),
    _C("td", "timer_direction", "quest timer counts up (elapsed) / down (time left)", ["up", "down"]),
#    _T("cm", "show_carve_timer", "carving countdown after the quest"),
    _C("ot", "stack_mode", "overlay stacking: game = only above the game window / always = on top of all", ["game", "always"]),
]))
SPECS = {c["key"]: c for _title, cmds in PAGES for c in cmds}


def _value(s, c):
    t = c["type"]
    if t == "toggle":
        return "ON" if getattr(s, c["attr"]) else "off"
    if t == "action":
        return ""
    if t == "text":
        v = getattr(s, c["attr"])
        if c.get("secret"):
            return "*" * len(v) if v else "(empty)"
        return v or "(empty)"
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
    log.info("menu command: %r", parts[0] + " ***" if key == "gp" and len(parts) > 1 else line)
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
    if t == "action":
        return start_game(s)
    if t == "text":
        rest = line[len(parts[0]):].strip().strip('"').strip()
        if not rest:                                   # asked here, so nothing sits in the command history
            try:
                if c.get("secret"):
                    import getpass
                    rest = getpass.getpass(f"  {c['label'].split(' (')[0]} (typing is hidden): ")
                else:
                    rest = input(f"  {c['label'].split(',')[0]}: ").strip().strip('"').strip()
            except (EOFError, KeyboardInterrupt):
                return "cancelled"
        setattr(s, c["attr"], rest)
        s.version += 1
        return "saved to " + s.path if s.save() else "could not write the ini file"
    if t == "toggle":
        setattr(s, c["attr"], not getattr(s, c["attr"]))
        if c["attr"] == "log":
            _apply_log(s, base_dir)
        if c["attr"] == "autostart" and s.autostart:
            miss = [n for n, v in (("game path (ge)", s.game_exe), ("login name (gu)", s.username)) if not v]
            if miss:
                s.warn = "Autostart is on, still missing: " + ", ".join(miss)
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
            if a == "user_field":
                s.save()
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
        frozen = getattr(sys, "frozen", False)           # built with PyInstaller: relaunch the exe itself
        params = subprocess.list2cmdline(([] if frozen else [os.path.abspath(__file__)]) + args + ["--elevated"])
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
    # next to the exe when built with PyInstaller, else next to this script
    here = os.path.dirname(os.path.abspath(sys.executable if getattr(sys, "frozen", False) else __file__))
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
    if settings.autostart and "--no-launch" not in args:
        print("[autostart]", start_game(settings))
    print("[waiting for mhf.exe]")
    if any(a in args for a in ("--console", "--once", "--count")):
        count = int(args[args.index("--count") + 1]) if "--count" in args else None
        try:
            _console_loop(reader, table, count, "--once" in args)
        except KeyboardInterrupt:
            pass
    else:
        run_overlay(settings, table, reader, here)


# ----------------------------------------------------------------------------------------------
# Built in icons (PNG, base64). Sizes in pixels: ICON_SIZES. Keys: small, silver, gold, capture, dead
# ----------------------------------------------------------------------------------------------
ICON_SIZES = [12, 16, 20, 24, 32, 48]
ICON_DATA = {
    'small': {
        12: (
            "iVBORw0KGgoAAAANSUhEUgAAAAwAAAAMCAYAAABWdVznAAABmUlEQVR42m2RMWtTYRSGn/N9txFTC4UKtZqCUtpAlFboJAVBFFws"
            "uGkH8Ue4+RfUBmyWooPGpdjFLm6KoD9CrbFGwRq5otHcpDb3fq/DvYKDB17Ow8M50+uBCHCAAQK8GXIGAl84R84OAHPgcoyKPQlU"
            "/nUFA7AG3AMuRREAS5Pj1jo2YZ+B8yO5O1vcNJivml49NS2etARonJl3v768dvr2AS0t2hBYPV21+PkTp1NzJmoz1pXY77TJmmtO"
            "6pq2P/pse8eCBqaHd506700S+9UT1gdYf1BHEplE+njTwvJhtDzttbHpgkQqkTbrJuARwNRshd20i3ZjC5drJfVWrmpn4Ygu1rw6"
            "sQ/DLpqt2Fdg2ntHL+4SHR+PLsStqextZ+BG2gntn9/5NJcykR3NXjwbuOZWdsd7tpAw57hy89roUMmh372EtH6LtH7b0qRPqr3y"
            "3o2Vcuoc1yUs8h6FwNi7OIka66A+lA86QNxfNRjt+9YPIwTGvEdWdDEDnAOGReMGrgQhAwJQAl4Cb/4+4F3e+P8mhDzAgT/40rVQ"
            "D6dsKgAAAABJRU5ErkJggg=="
        ),
        16: (
            "iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAACZUlEQVR42oWTOWtVURSFv73PfQ7EARNFkMTGCQKKirMQG23sLII4"
            "dVFR0UYLY0ScELFwCIiBWDiRn2EERUVIoTYxikNinBDlGaPx3XvusniJebHQBQcO66y9WZuzlwFGGWIsDPDhe+QfsIrjlcXu4D5G"
            "w7DGKnRlUQh/GgaAUC7cCxwO/uctgbLWRzluA/eBeyGw3h22b6cKaFsw27V4vgm4taqeancoFFgyXPMQ6CRg6mg1XT5pAisCTcCd"
            "jWtdn3ssG/poQ5s2uIAuYDNY3+lDro5Wl2EiMRv40GOZRKntnGeLFrhOHAhS5plkkpBEdrbZtHCe68oZl0T29qllhg0CdF+7YHkU"
            "uUSub5ZJZO/7XIeaCzrSEtT/xiUR9dWiRB5FfvO85UAfwNa5tWjoIzFNUcxRX6+rYQXakaBtNYkaVrve9rryHJVSVPpEOr/OBOxC"
            "IgCdF4+UrUqo5Rg6UDtD2t+k4orp2lIddPREGBkntraYgHudnSS4QZLQMHOa/fz5gdj9YKrWLp+i1pXVynY2qbhopk4tca1bU6Oe"
            "BzUaeEecMcXSkR+zioXqun68aumv0qy8bvk7v3R1kGVPgQkFuuZkHNw3nv6HdfmP0nvfc+57N1A/ur3C3Hl0o2VyjIMTUon4+nmI"
            "l9qTeKHN48sejxIxDo5L25urohmPGxvLC5cAmKNcVlX07373jnyoCJMmR1bOMjDofyaedRnjq0v+JaRITKqvL7uvHKEDWAiUxmbC"
            "vBwmxWHLBeAF0AiosoFJWAjkI0SM2O7dTHz1ChsYIE+nY2kv9uTJaHqN/8P+SmAlz2+WaCqrNSC54wAAAABJRU5ErkJggg=="
        ),
        20: (
            "iVBORw0KGgoAAAANSUhEUgAAABQAAAAUCAYAAACNiR0NAAADgElEQVR42o2UX2jWZRTHP+d53tf2l9e5vW7odDpXa0MtIzKR2qVF"
            "EkRRCUXURVDQTRdhk/5cJKmIfxAtS1dEF1lEFxlFFwaSBEF2k5hrOEFJ59R3e+fmfu/7/J5vF++rLmGwA+fi4Zzz/Z7ncM4X5mZW"
            "9TmZq/psRf6O3NnIZiV1d4L19tJw7yKa50hAvrWVhR0dtHV30zijyAPk83QBJ4BTuRxrZgB5gL4+ajo6aGtrIA8sBDgLjACjwJna"
            "Wh4CMIOGWp4CLq5f7fT4ehMwVlPDS1b9WC5HJ/A7cAW4BJwDmHz1OafvvjQ9usYEjLS10AdsAYuvPOnT5F8Luk7yxosuBROwvbOd"
            "VcDp3uVeRwa8Xn/eCZgCKHz9sYsSceKCJQ/f7yJYqL/L6cA7PlXZgoQUkUQ6sMvFxjoT2NTaVV6XT7sgET/fbREoAFzb0W8xpEQF"
            "4vSIlb456OM/x30iWZCcxi46FS85SUhCf/9q8cgBrxsXLFVAIUXb3rIITAJ81bPcVL5MCCVuFkUJTY2bdm3NaEVvVitXe+3emtH4"
            "mLudE1C5hMIIYWWnCfiRRc10A9f2biZKxFKCSgmKqen9LRktrUM7W9AHnVktazG997ZXjKYkQeWkMob9/VYGirkcD+AcmLEtn0PF"
            "QZI0VLocOuPUtcTp6Lou6YWnVbxvgT66J6vlnV5nB00SSlM0MUi5tclkxj7nwMWI9Sxlz+g4ozt2WtZ5FErG4f0LmB8ia9u7KTz4"
            "GGOWYd28edQ58dmeFkLicI649UPLjBR0ZfFitsWIecBfLTJhjsmhc/bEa89aPDLQ7grnaii0jlMYHqLnxHFiOeXbxikm7o4sq1nI"
            "pcEmlrSNh5ffxE+X2FEs8j3gMwBpxMwYSqP44Ytmu3ytls17h/n5F8/2g5E/skUMMTwP+jd5Nmw4z6F3l3H0kyaLXCVG/pIws8ot"
            "++rmb8w3mI7ta0zLk9lbK3L6pNenh70OHvI6ddJLqswvmfT6aV99mF+PgE1VjExm5lFPB2Jr1/U4OizCjcq15utTnukzzCAEOP+n"
            "iBiZhpSmFTc0OW0RFG9iZACkigxNJHJrNuKsessCYgTdTDCrBBGSIZMrpwLIVlNuaZiANuARIFTf3Ip577NO2TQlxhjLQJxRmwV+"
            "A85X+7hDLeegyzHeFlIzBNRVgf+nsDabYM6Iz8pRdf4DvALIexFe+lQAAAAASUVORK5CYII="
        ),
        24: (
            "iVBORw0KGgoAAAANSUhEUgAAABgAAAAYCAYAAADgdz34AAAEs0lEQVR42rWWXYjexRXGf2fm/ci7ySbZbKxNSeIGk7CasLbGiEFK"
            "qbRg9aZsI3oh4rdI0aIXIm1pTKFQsO2FFFPa0FjiBxhQo2DrVZINmGwv0nz4FRfRmJA1u0k22c2+b/7/d2aeXvzffd2uu1QKPTCc"
            "YTjznDMznOcZ+D+btQaA/kvcdJ++boIS4KYlsJZPcyRJMxJplhg3A2tWc7PNaxX6q1UenCPO5jrBplIJ39lJUpNyFvmk0eBUa7MB"
            "8Q7wu0s81cjZCpTLnnVLI78Yhjrgp526a/EC+qxMc2ICFwIR4EIrILb8UBnWtauu8a1SiTfA1H+Lzx+5wzXA5Bx7OiusnVbslWYM"
            "FBg2hTUOcP6Ga51eeM6lZ5+0vOxMZnxyZRfru7rYBHwIpl8+5BsaddJlwu+eLpIApxcu5Ie9vXQ7x7vg9NjdPry4zWtDrxNwHuDc"
            "/f0+SUSJuGtbe/MXwPkl853eer6UKVjM66gxTpKI77zimsu6nYAx4DiY/vybUlA0SaS7bnMJOAdw7q7bnEK01JhAEvnr2139pm97"
            "3Xl7KX424Cclk+RUeKSEJPTZIUt33u60sc9r1zYflVDjIgoZ+vEPnICLAB+tXmGKI2QpR+FysVkTFnXJoiKSTGdOOO143uvFbWWd"
            "PeWKGCE1SBqzqIRCA8UM5SctrVlpCThNrcLjgP70jDUl1MxQaKIUinlKprdf8brx+pI6Fpa1aInT9zY67fpbWUlWxORF1SFDEmn7"
            "M8UjVyps4abl1Jxx4BuL0dgxYkwFeMhRSujAHq9rrvG6eQnafRV6ubeqm1eZ1q91OrjPK6UvC4oJXfrAGsu6LZlxbOVKugBYtICf"
            "AOnnD1iuQAwZiqG4gnvvK2tDt+nz7/aqfusmnevr1v7eqtYsc3rwXi8JxSZqNpEi+tXDlgPqmMc97W7csgXnHHs6a2h4kGZqgR8b"
            "mKdVfR369VIUnv2jRv7ymoauu0Kn+xbr4bVeV/dWdXTvfEkoRXRyP6GzZnKOgdWrqQLOA7ZvH2nRfD4dr9MfGq72o83io3/VbOfv"
            "VzBan2De0iYbjwxROXEIN3GBC5MZe3sygquQfbqKby5vcMXyPD31hPHuMYXOGj8dPsNxwLlWi9tEnQEzBv95RDZ2aF7a8Yce1n8n"
            "4/GHIkc7jd/Whjj68WGOX55g+8pLDCX42aMZ624c59XnVnFqoCMNfiBvxuGLk7wzRY6lqXsKETMjORN7d3fR05Oz+bFTVBckhs+W"
            "eXN/5HBHjiHyYPR/v8TmewLNxknGRuHg2wvNWR2JzPs2i7YJ0FzBi3+/ocd0eGdnyBq+aKi8aKyjB7xe3lHSS38t6chBL8mUYvFW"
            "9UnT4M6OsL7HBOx37qsMa75Y/MeGNT7oC8s0QtAwQWcIGiFokqCMoNyiJok6Q9AoIY4SNG5BIy67eoULwL7pCdpXFAspqbx3Ivq1"
            "G/EmMFdQogQpWXtuZvi2TIkEyPCfDyeABSn9px5Mt8EspzR0ksvTFMy1kcC30k7Re4uWNbVWBY5Mw9OsKuTcHDJnRnPw+vILb531"
            "PZwIo+u+lMT338e2bv2Kzs8uczNkcKauVoHKTKD/9acx1+/ia9u/AdctslQyH8vBAAAAAElFTkSuQmCC"
        ),
        32: (
            "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"
            "RK5CYII="
        ),
        48: (
            "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"
        ),
    },
    'silver': {
        12: (
            "iVBORw0KGgoAAAANSUhEUgAAAAwAAAAMCAYAAABWdVznAAAB1ElEQVR42l2Sz0vTcRzGX5/P5zvYKteXZrhpupFtMZAuSgcPEwut"
            "g3SpQ6fuXbxI9AeUVKQkWUZoHQoEqTwM9NAgKqFDRnQJFuRaO9Ram/RLi+37/bw7uKB6wQMPD8/teRTgsIUAPqCVUhZARMyfrKkt"
            "tNYopQEcrTVAG9DR9I5SmqYH4CowB4w4TgCgf0eLWwiH3Q/AIcdxADLALDBNZ1e3TF7PSmJvehO4ti/Z821xOS/ZXEGSqQN1YCqe"
            "SK1fnFyQrnhKVKw9/vXhSjFUKhUCueV7HD95mvpG2YoVFXL3qPvzMxwePkY8kWwcGehuANwcHbsghU/iFaviXb4yZ6O9R6Wjb0gm"
            "pm7ZUk28dxXxRsfGBbgLEI20Rj8+Xa3KymrJ9mYGZHBGJHzms6T7M/LsRck+fl6V1t2xCgQ7jTbmx+bGd7Mr4g65sZr/+mVer33Z"
            "SX29SDqQZ39P2M8t5fSjXHZcG1lyfM9Txpj35corr+/gWdt2fth/MH+DbaEgIydmibS7/uLCKbTWRd/zlGOMEWttS+FNzZk4d4l6"
            "4xfbQ0F+1oU7t6cJmIBZy5ex1oaNMaKaW3QDg0ADUPyLNN/wBHir/l5ba43811aAtRZrLQC/AbOqvRG/A+DXAAAAAElFTkSuQmCC"
        ),
        16: (
            "iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAACrUlEQVR42q2TbUjVZxjGf8/9/H07x5e0NCutbOxDEZ3SoqLWhhTr"
            "aww2S4XBYEXisj5sVAhzDGEwIioiqI1RK0aRthAyQU8zMaIy8WWm5tCOx8SiyCMdOR7/9z7ogUODPu2Cm/u6n+d+eL78Lni/7Hy9"
            "VyauS7w3RjBGYnNsT955AyKCWBv/KyICUAF8N+8BnLk7S9wZzUA70Gat3WmMYdeuMi9wLndJvi7LW6nAJZ/v4wXGCAkJCeuBFuAe"
            "4AfQqm9P6JdfH1NgEvgK8Ps2bNWbtweiTa3B6aJNOxR4CJQBwc9Lv9HKwz8poACh6w090ZHXOlNxpFazc5bqF6UH9UFvJNofVB0I"
            "qj7u1+je8ipdsnSFHjz0gw6NabS+oTsKZsoA/ZVHaj+sqDrK23CE0OQbNzsnm0DgmW2qO4nOwu7PDrG8YMVsMPCcrIWZ4vEmc+bn"
            "Hzl9snoUYF9mVo423Q3MdvSFtHMgog0tT7SwcI0mbyzT9M0lWli0Whv/eqrdQ1HtHAxrY+vwzKJFuQrsF1X94/WrCf/FX09ISkrq"
            "bIongca6s4QXb2N75SWcT39nMGkjt+pOkZhkcZxk9/Jvp5yXL8fb/X7/LyIiruM41TeuXZgOjoyZ5y+6GBxqxJtegEfAipCeUcDw"
            "cCtj412MjoxSf/X8tLX2eHFxcdQCqLqBSCSyJzffsywxddRd5ys3bfWnedQ7RspEOx9MNbPvQA1vQh3u/bt35E5zczfoUdV5MFxX"
            "jYi4kvjW3bLpe9cmWbIW5tHhv4E4lqJPzrFq9RrCUx+5fT01GGPUdV1jjNEYWaqq3olnIjfr/pTJ0CvS0jJIy/chBnp7url/r41U"
            "b7qM/xNGVT0iVuNzAHAFWAvMxPGu7+RG53H+Gyj5T6hU1cQ4j/m5PjfHiv9T/wIy9ymouuyFhAAAAABJRU5ErkJggg=="
        ),
        20: (
            "iVBORw0KGgoAAAANSUhEUgAAABQAAAAUCAYAAACNiR0NAAAD5ElEQVR42p2UbWjVdRTHP+f3v+zuv103trsn2XRbOa+tfGBgRi6c"
            "SZaZyggRYvhCIqgo0BJToTeVFUhayzC0AjXKB0iTQDFfpDlFYU58WD7NKem2Nrvb2u7d3f///51euNlavjC/cOCc8zt8z++8+cD9"
            "SYbigQZ1VG0Ae498pMddOfdYYkaYOoCtrKyMuK6b2dPTkxzq6T1m7yovs7CwoLS0sigajY0ZYeQAZGVlTQAagHMZGdlVo99LSp5w"
            "S0sriyKRonwgH+Aq0AF0AhdDrjsDQERwXbcWaJ84aYpWPlalQHd6evpSkTtXZmdnlwGngC6gHbgGkKiZU6vvrtui48smKvBHNFpY"
            "Y4xZDdiaOQuDQ0dv+UdP9aTmzlscAIrIx3l5Y6uA38YWl+nKNZu0Zk6tAgmA+NvvbLC/96jdd/iaN2HiZAV8Y4y+8traoKHpL7+x"
            "2dPG5kBPnfOCV998P0hLCyswUBGbot/tbfRb29WuWlNvge4QQFvbDeJdlvHjSpz6rT8FB/d/68QmTfWrZ88zCM7t23GwloKCqHl9"
            "+VqdVjUzdeHsifDc5xcHxeOKnf5EoG23WgBCAuzKLyhevGPPiaCgINexKriZrvo+0h2Ps3f3Zxw/tA0RYfqsl1hSt5zsnBxsgHp+"
            "UpyQ4UbLjeDlutlOR8fNA05eXt6Zrs6OOs/z3aefWUhqoE8SiaQYY9j86Ur27P6K9ooVxMdM4cLhDfT82cmTT80jkegVY8CYsP2y"
            "/j093vBzf0ZG9lJEBBFZ57qZuuvHJu/MpaSeuxrovoPNWvFwrs5YsV8nb1LNWpPS/LrtWhEr1B8OnNXzLVabLqf0+32NXiSSpSJS"
            "LyIYVZXy8vKNyWR/+/av1zuhULpiDL82fE7SRpjwyEyqo0o6/QS50wkkk5MnthBYDyeUZnd880mor6+3K6e4+CNVFQdwuru7+4wx"
            "ibabrS/Mn19nL17fZpJeJ/Fr/Zy/fpOm0ExsoORf+YISJ86jMx6iv68VO5Dvr/9wueN53qpkb+9hwAkBWGtFRK74gXDkZL0UFafx"
            "4qINhLzt7NzyAdreTDgjg9yB8yxYtprZzy2i4dhGWls2G88PsNZeVlUREUIAxhgFwkGQJJqTz6zqtxgMAhYsWUasciqXTv+CMQ6x"
            "qpWUx6YxODDI41VvcKxhK6oeQPqQxx3DYVk/sJoqs6cbG0mm+hERIpEIsenPIgiel6Lp5BGCwCccdgkSOTqYGrSAVf0vcRYgokP0"
            "UCNGh7b+Kxxj1BhREVT+6dcOew3zUIEioBrwRzFORiBqOJcRS0LAMaDtPiAs/xvco39ieDDZ4Sv+BnlWugNoznkYAAAAAElFTkSu"
            "QmCC"
        ),
        24: (
            "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"
            "AAAASUVORK5CYII="
        ),
        32: (
            "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"
            "TkSuQmCC"
        ),
        48: (
            "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"
        ),
    },
    'gold': {
        12: (
            "iVBORw0KGgoAAAANSUhEUgAAAAwAAAAMCAYAAABWdVznAAABnElEQVR42m2RP2tTYRSHn/O+t0pbQwsiUYyg1BqoIYgdSg0ooqO7"
            "OIgfwkX8Ai7aDImKuNg/g13ELm7qoF+ixAaqUkn/oIlN2pvc+/4cbgQHDxzOw49zlvN4IAIcYIAAb4acgcAPM0fGDgBz4DKMhjMP"
            "FP7NhgxADXgJ3IoiACr5Sds4fdy2gBsjWXZ1uFOnXDR9fmeavWhdoD5fdr+31iPtbHpVZm0ALFwq2u6Ht06lCyZmpqwt0W9tWrpY"
            "c1LH6XuT9FuDoEPTYs2p1TRJ9IvnrAfw4lUVSaSSJW9eE07OzKtQqmh1hSBZIpEsVU3AMsCp8wV+DDpot0WYuzKta0+liQcdleeK"
            "+rlN6HfQdMG2gTPeO/b32kRnJ0du7jTzaePrvmt0S/R/Nbk8tsqJeCL9+D52y2vpE+9ZQ8Kc4/bDu+MDHeTiXpvk8SOS2oIl/QMS"
            "xeOH9++MJc5xT8Ii71EI5L7s9aL6cxFi49ho9u9nVcNGu36jbYRAzntkQxdTwHVgkBl3BhyBkAIhYz4B638P8C4z/r8KIWvg6B8k"
            "m7UuYySKEwAAAABJRU5ErkJggg=="
        ),
        16: (
            "iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAACcklEQVR42oWT0UvWdxTGP+d8f7+a0xiG28VId5NGUpFFIXTVcje7"
            "2o0EQ+gm5tZWN3mRGZQWCkEUbq3YTW6F/8JuRnrhVlsUTNqwWmhqLGNEpenL+/6+v6eLV/J1F9sDX/hyzvMczoHnMcAoQ6yGAb78"
            "j/wHrOJ5pdgd3FdxWOZYBa9MCuHNwAAQysJDwLGKXgJlrvvKBteBX4CxEGhzh44OqoHLmxpc25tMwLXWVta7Q5rSsqy5CYwQMA0P"
            "mi72mcBeAAeB0Y/3uP55YNnSnBU++cgF3Ab2g82c6XIND7rARGI2P3ffMoni5bOebdvk6j0SpGiZlEgKksgGuk1bGl2X+l0S2exd"
            "ywx7BTAxdN7yKHKJXC8tk8gez7h6j6L+LjQ95ZKIem5RIo8iv3recmAG4NONG1BhjlgqoTxHk49ce3eb0s0HVLP9gPbsTDU1FZTn"
            "qFhExaeUmupNwGdIBGDkwnEkkUmuvm7U+OGXavteeq9vSVW7PtfACSSZJOJgjwkYGxkh8eDEJOHkwCUrFOawP27V8vOPgXfrU6oA"
            "y9fyQeMabv60holf61h4Aqe/JguBU/v2kVmFoW4P9VbvKBTfzxtaZvzctxl3/Ctq3k5p4QKHO43ZGw15ofi3f3H21QTQvOJeYe78"
            "9kPPuqjFqpJEnLxn8cogcegb4tRDixIxLqal77qroxm/t7eXDZcAmKNcVD8PCz46Ki+8gJp3oLE5AWD6XuTPW8ZbtSV/lkYkapqb"
            "yzauPGEY2AoUV2fCvBwmxeWVU+AvoB1Q5QCTsJCQo7I0ZlhnJ1WTk9j8PHmpDitNY+PjK+k1/h/2rwRW1nkNTi8k7UHpDiQAAAAA"
            "SUVORK5CYII="
        ),
        20: (
            "iVBORw0KGgoAAAANSUhEUgAAABQAAAAUCAYAAACNiR0NAAADgklEQVR42o2UX2iWdRTHP+f3vK/udZuva/83cTkWaysLjVowS4iC"
            "QCG6yUVI1EVQBEVQohl0MWnNakUr/0DSHyUToguJuukiSoK6kEpRNynLZb7b3Nq7Nt9n7/P8vl3s2VyDwX5wLn6cc77fczjnfGF5"
            "zxJb1nOJLZUULIpdimxJUrcYrL2dspsbqFwmAdW1tdQ0NVHX2kr5gqQAoLqaFuAkcCabZeMCoABgyxZKmpqoqyujGqgB+A3IASPA"
            "+UyGuwDMoCzDw8Dfnbc5PdhpAv4pKeFxSxrLZmkGfgRGgSvARYCpp7Y7fXHEdO9GE5Crq2IL8DKYf+KhIA4vW6R/CZ/d4WIwAa83"
            "r2UDcLZ9faDPDjs90+UETAOMHz/gvISfHLKw43bnwaLSlU77XwliRRZJKLH4cJ/z5atMYNMdGwINn3WRhP+wzzwwDjDWu9t8FOMV"
            "4Qs5mzl+MPCD3wWhRCQ5jVxJ6+pwWpJJQudOmj/2fqBrQxYrQlGMenaaB6YAjrWtNxWHiaKZ+Uq8hCYnnN7sNt3Rgjpb0RuvmsbH"
            "gusxESqGKMoR3dpsAr6ioZJWYOydXXgJPxOimRAVY6c9O53qK7Na80C3qrbuVU1NlV58LiUvp7AwGycR9++2IpDPZtmEc2BGT3UW"
            "5QcJ42i2yvPnnFqbnDqe/1y39EsVewqq2vGJbrpxhQYHnCQUx2hygGJthcmMd50D5z3Wto63RyYY6d1naRegaMY4+t5qQltBc/tm"
            "NleKlX4CV3MnqfJSjvStIQoDnMN3v2ap3LhGGxvp8R4LgOBqnklzTF24aFuf7jL/6Qdr3chQKenMKGcu5Tml+4hlrMvtpW3191Rl"
            "6skNZFlXl4+efIGgMENvPs8JIHAAsce850Is8eVHN9jIXxl2vTXEI48F1Awdourbu2n86X4qfj/A9q4UL+37g+HLcOJQhXkT3nPa"
            "++u3HCSbv626DH3TvzouTqfnJqlfTqX08X509CA6/WtqfnUK005f95dG2VIEPJpgpFILj7oQma9tmfTDF0V0DSwF9dmIhm0BGCiM"
            "+fNn8BjpUlHRUtB0wTzIz2GkAKRZGZoM5TZtxZmS8xd4D1KMzDDAOWYdMmTeFWMBpBOM+b4F1AH3AFHyZ94XBEHaKR3HeO99EfAL"
            "ctPAD8ClRFMWqeUydDkZwFy8gFUJ8P8U1pYSzAX+JTkS4z8h8MiQCyzQ1wAAAABJRU5ErkJggg=="
        ),
        24: (
            "iVBORw0KGgoAAAANSUhEUgAAABgAAAAYCAYAAADgdz34AAAEuUlEQVR42rWWb2zdVRnHP885v3vbu65Ax2Qy2tExShY35hhTISSG"
            "QCCBvSFTh8giKqgvyDRqNKBG2QteqPyLJnMhjIBbwGkEDDHKCx2lCd3A2GnZ3KIwoRvboF3bdb2393fPOV9f3Lv2UtpISHySJ+fk"
            "5DnP9zkn5/l+D/yfzRoOoP8R1zymDwqQAa4JwBpjmgckzQLSHDFuVq45zc01LxXZ2NLCXfPE2XwnuDrL8O3tJNUoVCOvVyocbWw2"
            "IH4O/O8zvlfJ2QoUCp5ViyM/OA5lwDeduuO8hayxArWJCVwIRICxRkBsjP8qwKrpqksszTKeA9PG63z+9U2uAibn2NNe5LKmYpeY"
            "8dKsXKcBTq3/mNMTP7f0s29bXnAmM15f0sHqjg6uBv4Jph9+1Vc07KQpwgP31kGAt89p44aVKznfOV4Gpy2bfdi13evKlU7AKYCR"
            "r2z0SbIoEX/zy+nNJ4BTi9qcnt+WVRUtViuocpokEV/4tat99HwnYBQ4DKZH78+Cokki3XqzS8AIwMjnNziFSKpMIIn8mcd8+VNr"
            "vW7dkMX/9PlJyTTjTPuRAZc2bXD6xOVev93uoxKqnEYhR7fc4ASMAxy6tMsU36GachSmGgnOWNQZi0pIMh17K9Oj2wp6YltRJ45m"
            "M0AVksYsSihUUKyifMhSzzJLwNuUinwD0Pb7rCahUEWhhlJAtSpSQr97qqBPX4GWtKJl7ejaNejJHa1KyVTLUcob++rFpcfuswio"
            "WOTHXNVJyRn9F5yHRgeJMdWThxylhP78l4LWXoYu7lmtxbc9rcVfeFrLr1inFRei3j2tSqkRW0MxojMHrXLhIktmDC5bRgcAixby"
            "GSB9/07LFYkhRzHUr+aOzU4rLrlIa+4fUudPpUX3jOvcb76mi3q69aXbvSRTqKFaDSmiH33NckALWvni2U50W77Ds87R+4unVDjx"
            "V5Jz4DwM9Lfx2t5E4dJruPnKTm5cXKaFMXxbF37FtRx4NTLQdw4+A+/g6MvEh3dScI6+pZ3spp4G6+0lndvGkdNlNoaKlW76LBwa"
            "KNmvHupibHIcVzrDGwvu4N3UznjVk8pjrKr9hFLtFBNvrGBp1yQf6ayl737L6B9UaC9x9/GTHAaca7S4TZR5yYx9r/wDG93fmnY8"
            "2M3H15bZfFfGwvIRJl+8hcODe9Dofi4fvpNs+CBfvttY/clxdj/SzVDfgvTKQbwZ+8cneeEsOWZn+zxEzIzkEC8+18Hy5TmbthzD"
            "FRPHjhfo7+vlgkPXgc9ocYHrbyxy2+018qkhHn+nk71/aDdvZSSq3k+z6DQBmqvz4h/Xd5sGdraHfMrX33msv/dX+wvatSPT7ied"
            "9v+t8J6mK5dN+3YtCKu7TUCfc+9nWPP1xT+t6/FBJ62qYYJOEjRM0ChBgSBZkCwqt6gRCxqxEEcsaMKChn31ki4XgN5mgOkrinUp"
            "KR54M/qe9XgTmNUpUUBKhjSjJN7NSIoAmfxbxxPAwpTeqwfNtq+ak/17iKkmBWvUI9W531xjHpto+exaC/D3pnyaU4Wcm0fmzNi7"
            "b11h8Plh382b4d1VM5J44AC2dev7dH5umZslg7N1tQUozk70YX8a8/0uPrD9F+q4mWdT531FAAAAAElFTkSuQmCC"
        ),
        32: (
            "iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAAGxklEQVR42u2XbYxeRRXHf2fuvc+z+7SFlq4tZaFtRCxtYwETgwTB"
            "8mJ401ajkcRoUkBINGpLkIoipImEEpqqgAGsSAworekH01YhIKXUioAaeS2FmtAFW1pbutvdffZ5uzPz98N9dtkubQExxg+e5ORO"
            "7sz8zzn/M5lzBv4vhSSAvcu1Brj22I0a/9fFjRnb+wE6lNoRGBieOxv4wmGcOhRrh9J3pPtwUV/mHHudo+kcNwDZqHQeCeNtkyuA"
            "8VlGNANEkntqErcDPW2jsQ0cxkNX3bghwjckABMoSYy1QSwds6dAhDKwNE2Z7hxNBSwPOKAGMNRedJA6YwtwXNvRtP09NXNsKda4"
            "cNO30sZ9K1wjNZcDShNeAOaPYasD+LnZ220AdczYCea/fInzNy12+fWLrDmp4pqAUscmYEYb7ktZSg+giZWkuvrWNGrApBZh8xrX"
            "7O5KmoCylAEHS4YdN+PewrhrXnpB6m++LvFfXZB6MG/GG5hjDzht3+gkEdXCP/ZL50upy51DpYwnyhm3pCk5oI/Pzup/fyRpKCf6"
            "IdTsJyrH73zGteZ/LMnBlCbEcsYtpZR7nEPg8l8sLwVVTRJ6dr0JTM6xGzPeAKenNyTKvanehyTCb36WVB0FE23K/RUL0kb9JScF"
            "1GqafB35BsprSDnye01LLk9icS6KfYaL969MpSaq96E8RxvXuLcccI5dYHpqnVMU8nXUqhIlmo+vTRqXnJP4i88ttdbdlQ1pv9XV"
            "MOVNJ8lJYkTzOop1pBZa+9Mknn9mEi44KwuPrs6kFsoHkR9CMaDH1owwsJdSyguAVn7PGhI+HypAQgMpItVMOuCihpCvIUWTZBro"
            "NT28PtXGDZlqAzbiiK8hBaR+K7SFwhBSE+XVAvtH33UeiGnKLtKU7wOaPtWate20QgPFFpJHvu1IqBYRSqg+4LTq9pI+dW5ZXRV0"
            "3Hi0cH5Zd67s0NBgwUreKIyGoSJFahWYoYaGtppmTnMRUJJwG8C0JOEvgJZdZUH1Ig3y7UhiQVuMqNpvWrq4pI+ebJo1Gc2eNVlz"
            "5nRp3gw0dya6/CsVVQecQij2KLRx8nYAg/jl33SttvEdwIkAZI5FzqFxHcTXN5liE4W8DRBQ8EX0d9xR0qnznOZ0J5p64Q/UcdV2"
            "dV71kiZ/5ladMrdTM7ozrbqzs0hF/pYDwRfnY88frHb0OGs6Q85x9UgVnDmRHUOec+otjh/ot3zhAhTBuRQkMAf9B4ybljt6dwSS"
            "+bfi519Hmowjs0B96pk03CQm7H6I1/ZkXPrFQFZqX7UG0YNromuvd/GJ51R2CS/GyDVA3QH2ah/9PnKzc+T3bRCvbDElBjFCiAXI"
            "k5tTXnspx6YcT/cnvs4FUyKzu1JK5RIT4n7q0z9Pcuwcel6us+nBzsJwgKgiym2PG/euU+YcXmIlsI8iNgRYCDzsHJvzQHbjj3HU"
            "ILYgzWDH1g7W/WoKnRWwUkIi48xumJxGonOUshQXHeWjOpk0ER5cM5Udz1RIsiJ6+tHSFRZbuZxzbI6RB4brRDLq3vYx8qoZF728"
            "wyZ89gzHtI/IeraVuf/2E6j1Jwz4QbqO6uP5wdn81U5hd38NgqcWxzPhwCZOS++ilRvHdnSx59Wj+cDUGl0neJ76nYXv/JDEOZoh"
            "8G1gW9vmiANqn4fXSimn+WDzTp9NnDUjc3f/ZDr9/yxx4cJeYjln375IZfBxXt45lVrHB2nkYtz+RzijuZhq7wBnnWWcNz+y7blj"
            "2Ll1HCceX9Mjm314aAtpmrA5Rpa17R1U5YadGKn3jSH404Yp7N9V4ezz9nHRov186JWEa69JOYZePtm6jL3bT8VKFbrtSapVMWFC"
            "xpVXimndvdQHxZ9/fxx/++0xDLy5y9omq4Af3Q+kY/qDGEUAUe1z7JTj/Avf5NNX7Ca24KRZnuU3J6y6K6OnR3TXnoUahLLj5JMz"
            "liwW07o9wRsLruyj0TJ6Xy+bwkhTkozpRZQerterH0g4fUEfJ11cJW8V/Pg6zJ4XWHGbePF5xz92limnMGNm4MNzc8xEKwdDxBw+"
            "97Vetq6v8NyvE4OARlF/2CazVOIBQPfckHmpuEYVOajwvLPaQd9lV2ceUJaxfmyr9jYGQqAFhLUbQ+zph1iDJMVkgJkAorAoI9ow"
            "khXeSyNcS0aQkYxHj22KsY0d302bvfpQLdp/SB89EgPD+fkjUEkSaknC8EUVxxyikBgms9Q5nLUPlPfKVazV6PdCCMQQqABPj7X3"
            "bz8mzMDMCOHGNnPLZO0UvSec9/h/rAjoBCqjHiz2XnDsfT7PbIzqXWCK/yX5F+uaE3kYcBXeAAAAAElFTkSuQmCC"
        ),
        48: (
            "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"
        ),
    },
    'capture': {
        12: (
            "iVBORw0KGgoAAAANSUhEUgAAAAwAAAAMCAYAAABWdVznAAACAUlEQVR42i2RT0jTcRxA3/f32/b7q1S66Vxm82c6PEWi2+gSFFYE"
            "5ZRFpUkklmC3OoReLEjCa1BJOamuWl2CIKjbhIL+XIKIHYwRIlIapPjb3KfDOrz74z09C/pXEODqcDye3yiXWfd9C2jb77oX+ppj"
            "bz7/Xl8BPmZBR0SUqWkX57oOlWRwUPY5zp1Wx31apWZaLg3LXLKrFNS0IRFRACQca/lcc6xyLBxeBm7nulMrj3qSP4Gpo5HIj/Mt"
            "zZVEjbsMoAGBaMh4eybcqOps24+YZvJEU7TheDQWjZhWd8R2/NPRvarJMN8BAeXo2qe/O5WRa63exL1UemDVL1EfDICmWPNLRMwQ"
            "4/n84v1CYdrS9ceBGwfaDm6LvLr77fvJTYU8TB3OaApQsMc0GFvKL8wWCtM3Pe+1rrSoppQAogAVQq/2AhBBlMLXNAEqSqFEBGXr"
            "2pfNncrlsbg38SCd7l/d3iZiGLID6lfJJ2yEGFlaWshVleYBAqfqd8+/THbLeJv3vsE2XxT7+qWYGZBG23x+PdH+YTHdI0fqduWA"
            "AAAxI1iMO9aGFdDngOHZnuRaLplaBQZNXc95jv0nagSL/1VFAUO3OjvKfn9GYpY101Fb+6zdrX3S4roz/tmMTHUmykB1XBb06j5G"
            "r8S9rbhtTwK9QK/nupNjXusWMKqALOj/AJIPsSzlURkUAAAAAElFTkSuQmCC"
        ),
        16: (
            "iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAADWklEQVR42lWTX0xbZRjGf993zulpgbb0z8qA6mgLOAlmi/JnMMZA"
            "SKcYLkycbEBgiybGqDfeeMFCYoYaE5d4Oy+8cjGbZlKNLpsaFhOnEGOiRo12EwZDxrYsMhil7Tnf58Wq0efyze95Lp43D9yTaQgB"
            "cKQ9EtlI+f13gZAl5ZAl5SGgMuWvyHXEouvAaIk1Sl5k6TA8UF2dLw6P6GeTyQIQlpCRMA1EnqtPFpwjo/qJeM0WMFTySCmFUK7W"
            "6d5Y9L0P9rRawnXYch3LhOajyVT3WDLZbUJTzsUSjuJMZ7unOxZ939X6gBRCARhBQ0ztDQXcSo9V2Gbb17bbvlPA8dn+tP66P62B"
            "ySrbPhWz7T9DHqvQFY24QcOYAgxpCuHaUvzyaDgi39n9MPHyMs/1fO5GYyAw8Eg0Skt0G4lAcGA1n79eW1FhnWzroD9WJU0pfzWF"
            "cAXwLvCK37JePNPaNvlYqp7M0iISwWBtHKTg4+VlhHIZrEtw7kqWg5cuvXbXdd8G3hQ7/eV6vej8tLyVbwOOfba/Z+Lxhp1FcjkT"
            "XHGvZlNj287nV36z0jMXp4Cpaq9v1m8au2R7ZWjji56uh+I+34/AifG52eMzi/OWEmgFKEAJrS8uLVhj33w7CZy43+f94cs97bta"
            "AsENKSQIrdAgAUwAJfiftEYYBo6UGtCqxCqtEQ/6y/S64/58LZdvNYSY+Kq3f6JzR6JAMW+hlUADUirKvM509nfPkzMzrwJv1Hh9"
            "35WbRrNxq1DcccdxxyzDePmjjo5jfffFycxnjcubm6IxUAlSkFlZEn/cvGEM1iXYHQj0nL66qNad4gu3C4UIphBELXn4raYG/Uln"
            "+1ZPLDoPvN4QDM4VD49oZ2RU1weDc8BUurpq4fy+zvzEAwkdteSQKQSmo7VRVDSdXFhU2c3sEnAW+D67tnZn9tbNVksILq+tnQau"
            "XlhZ9VxYWX2qodxbl1e62dH6Q0p1pSNejzq7t1Wf692ve7dXaRP2H03V//VMKnXbhH191VX6074ePd3VoSO2rYAD4r9jsqQcOhSv"
            "3dIjI+r5RDIPhG0pMrYUGSD8UmNDXo8Pq6F4Tc6S8t8x/fMos5Q21hIKbyTKyjeBkISnDTgIhJIVFZtt4dAGMF5iTYC/ASItNeBN"
            "XdgbAAAAAElFTkSuQmCC"
        ),
        20: (
            "iVBORw0KGgoAAAANSUhEUgAAABQAAAAUCAYAAACNiR0NAAAE0ElEQVR42l1VW0xUVxRdZ997587cOzOIM4qDVZBHQEQx1gjV6vAo"
            "iqiUokh91UeV2ia2Mf1oUtPSNOlXE5s0amLV1D60WmvS1JpUQPCRWPCRGjV92BZbaxAQ0Iwg3pl7z+7HDJCwPs7ZOSd7JTt77bWB"
            "UagkBACs8arqo6lerw3gAABBoO0EvAFAADiQ5jXtcZrWC2BlIkfFGFDioybodg/eXLbc/iEcZgCHlfj7eQAXEvHhH4sX8s2aKhn0"
            "eAYArEjk0vBBBEiVuSbL6z3eVlJs5E8I2h7pOAAGQj7f+GmmWZhuGHNDPt94AE8MoTj5gWC0raLMzPT7TqrE1SQgE1yQOpDqkPii"
            "IS9LFSzljfv39d8HBxUAdC8SqWrIn6XvmjnLcy8SqQSAXx8PKjfvdelK1Hbem56t2VIc0RmpACRqAQVATo7hivoVkgAYwB1ViFM6"
            "0fZkt7u1r7qGe16qYa+mndGJ6gniFIC7ADhZ02SO1xsFkFMLKHRSwDGAiEGKbC4qFB/k5clUjyfJZu62pMSyyZMXjjdNTDC8WDp5"
            "StiS0pbg7mcM099QMNtpLSkWBhF7gMcnhXAUBureDYVuHOt/eKszFqv9bO483pKRaUxLTp7zIGotf2d6nkgzvQKCEPB4lNuPIy++"
            "XzB7zt45c90VoUnY2tYmLvT1bdiVlvbLuUePVgoBMAONLqIlUSnXLE5JOXomXCqhuyQkE5gJ4LhgBAFCSAghYVu0pKWFGru617uI"
            "jkSlPA2gkmb6vJGd2emLo1I26wp909jdve6Vq+0EEopj24IBgAQgBJgZTiwmAFa3Xr5MjV3da/U4WeOOzKzKPJ8vQk8ltN2zC5wP"
            "8/PKoo68xPv3n/iqo6N255V2W9FdzOARsTKYhduFt69etg799Xc119d/F5Xy4ke5OeWf5uY4Tx1HI0EQgISQMq7La9cAQLAjIDmu"
            "2FFGFgQBUrU4fyjEEIhXwYBkhijweyOlKUHfJ3/+06orSqnlOOt25uZ+vfvZInZiMZAqhEjwMjOkI1kx3Kg734pvO+7U6YpywnKc"
            "5h0ZmWXNPd2R4aa06ERllpRrKkOpR0+XlDpgMAQIQtBI1SQAwRIsZIwdqmhtRUtn51pdoeOWI5sE8AIxUFcfCq2wpFxdkZJy9PTz"
            "C2T/YEQ5eOcPtbDpJ7rY2ytZEFhR0PSgixc2naEvO26rMWuIzpYWi8Jg8JjlyFWfh8MrGKgDCQEDCBUmmUO/lS7ghrxcmW54egDs"
            "A/Dq2oyMGK/fyLxhE6/PyowB2Axgb5bffPDxrHznSnERz/QbgwYQIgAjozfD1C1dwAFwHcCbAMon6nqGV9N+7qlZyQ9X1XKSy3Uh"
            "RdfTAZQDeAvALYOEk226reHREwDgAVKHgNvzgklmUSBgDdis/ftkiM51de9zmK8dLHzuEIGxpb1tgyLE/PCkia+nGYYzTtOcS729"
            "rvbe/icBILsP6MSw5XgUpXpGktf6b9li5s2brLPhRTaAPak+XyDH77dy/f6naUlJ4wDsuVheYvO2jdbd5RU83e+LehSlmhJtGzXY"
            "+F0V0F0D15csld8XzR9rsOdHDDa8iG8sLbcDumsAQNVYsmFoCedd7VW1/imGOboCiF4j0LbhFTDVNG2/qj4E8PLYFfA/CfvzJjIp"
            "op8AAAAASUVORK5CYII="
        ),
        24: (
            "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"
        ),
        32: (
            "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"
        ),
        48: (
            "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"
            "AAAASUVORK5CYII="
        ),
    },
    'dead': {
        12: (
            "iVBORw0KGgoAAAANSUhEUgAAAAwAAAAMCAYAAABWdVznAAACEUlEQVR42iXBP0sbYQDA4d/73qWXu5iaNNGixZAIoigqMRAXU+si"
            "aqFUCtbFIYvWT+DsJyjdbb+AYKFLKdehYKlm0Cgi2NZUIg7+QSU25shx916HPg/8N21ZlpvJZO4Nw3ASiYTT0dHhAAGQB3TgASBl"
            "EARC0zQxOTkZmnk+Y5qmGV5cXAz39vaGpZTfcrncTymlp+u6CyghhCAIgql4PP4Z8G9vb7XBwUF1cHAggbfALyAMuMAXAaT6+/s/"
            "LS0tDX3c2GBoeFjYts3IyAgnJyckk0mEFBTGCqyurv6RQHutVhs+PDwUioBSqURraysLCwtks1l/7vWcd1o99Y6PfzeVUg8l4MRi"
            "sWY+n2f5zTLX19ccHR3RbDZpNBra5cWlnk6n9afj45oVseoSeGIYht7V1RU4jiPu7u6wLAvf9wmHDZLJBJVKBdu2A6fhCKlp2ovZ"
            "2ZdaKpVSxWKR+fl56vU6nZ0dnJ2dsbn5nXQ6TWGsIEzT1LRQKPRqdHQ0q3wVmJYps9ks0WgLtv2V9fV1dnd3ATAMQ5XL5b+667pq"
            "enqK8/ML1t6v8bitnR9bW1SOK6ysrDAx8YxH8TjJZBul7W1fAB9yuVwxEomoaLRF6rpOubxHLBZjYGAApRRXV1fs7Oz4ruvWtJ6e"
            "nnfd3Rnz5ubGv79vuJZlep7neX19fd7+/p7r+74KhUJeNBoNqtUq/wBw+9NqNbUeZQAAAABJRU5ErkJggg=="
        ),
        16: (
            "iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAADUElEQVR42j2ST0ibdxzGP7/3ffNGE4I2osbY+qc6VIyeulz0sFqs"
            "LFmjwmahw5MI89YJBgTXXAbSXnZwPSjSeXbiLOsEVw/CGLIWXR2uYF2LmHRF5rTNH2Oi7/vdYaEPPDyX5/A8PA+Axv9YBHKmab4z"
            "DCMNpJVSaYfDkQSywFPALHhVQTEANE3Dtm0zHA6b1RerHX9sbakXL3YZHBwk8TrB8k/LZLPZckAvUAEC2BqAZVmapml2Q2MDpsO0"
            "nz3boqKigssNl61XL19JLpf71efzhTRNyyqlLMMwzjVNswBRgKaUskVk2ePxfJzP561cLqcD1NXVyd7engJ2gHmgFLALyV8DDxAR"
            "BXzd09OTGR0dtQKBVru7u1uGhoYEkOHhYfF4PNLf3y+BQEBaW1slGo1KZ2enAL9ohT6fBYNBVyQSkd7eXnV6muXatS68Xi8zMzOU"
            "l5dLLBY77+vrOy+vKD8PhUJnoXD4FGinsMJTv98vjY2NVnt7m1RXVwsg3jKvHB39K3V1dfL48c8yMTEhLpfLbvygUfx+/xHwUtN1"
            "3VZKMTk5yerqKg+XHnLv3l10XUdXOoZhoBSYpkkqleJ6z3U2NzaZm5szAN2oqqoqTiQS7qmpKeLxOKlUivn5eQAEeT97Pp+npKSE"
            "Rz8+or6+nuxpNg/Y6Lr+aU1NjX12lrelgJWVFQGktKREjo+Ppba2Vp48+U2i0aiEwyE7Ho/LD4uLSWBPAdGbNwfuVlb67ObmZm1k"
            "ZISTkxP6+/t5/vxP9vf3uXLlQ4qKijg4OCCZTEogEFDvksnk75sbxwZALpejo6ODlpYWEok4AEtLS8RiMW7d+pydnR0ymQwAly5d"
            "pKuri0QiwebGBgagnE4nAwMDjI2NcXh4iG3buN1u1tfX2d7eJhgM0tTURCQSoazMi99fjdfrVdPT05YB5HZ3/2JhYQER4erVj7As"
            "S87OztXa2hrBYJDx8XHRNU0VFRfz5u833PnqDqUXLpybpsMB8I3P5xNAysrKxOVyCSA+X6Uopd5/wu12CyCaponf7xfA0nU9A/DP"
            "3HcPMm1tgfTIyBeZ21/eTrW1t71dWPg+7XQ63y4vL6d8Pt/x/fvfJnt7I5kbNz5Jz87OpnVdP1FKHf4HFUJwrIGzT8EAAAAASUVO"
            "RK5CYII="
        ),
        20: (
            "iVBORw0KGgoAAAANSUhEUgAAABQAAAAUCAYAAACNiR0NAAAEmklEQVR42m2SS0yUZxSGn/PNDIWZSWeKCBW8cIkDVFHqJcEEBhNM"
            "rMWEBE11YVy4qiaUlRp158JVu+mipqb1Uje60kRjbXDRxGiCRrtQB5pAoQ4zIgzO/OMAA/P//+nCGdM2vsmb7+TLdy7fe14Awzts"
            "AtJAFsgA1gf4BlgAvi3mePgfvKXA7/fLwsLCxz09PWb16tXYts21a9dYu3YtHR0d2LbNw4cPNZFIiIhUqiofQmk6ysrKANzOrk5E"
            "RCcmJnBdl21bt9LU1KSZdJpcLucAf4nIn8VcKZ6mOK14/13Y4/GAKj6fj7GxMYwxKFDhryCdyWBZlgaDwb2Li4sjIgLgGmNQ1fcs"
            "dXCBNuCPYDDgyeXmtdidYDBAPr+EbdsU3/1e1Lmknxaluw+cL112+3y+77dt21azbl29yWazEgqF6OzsJJfLkU5n6O3tJZ/PSz6/"
            "2LB79xctqVSquaKiojkajTYHg8H1lmXtsm17DcYYgPORSERVdV5V9dmzZ9rU1KRzc3N64cKPKiKqqjowMKA1n9bYqmp3d3fbO3fu"
            "tFXVVtWlwcHBApDxuq4LMD8xMeGuWrWq3HVd/H4/k5OTRCIRysvL8fv9OI5DoVAAVY/jOAQCAYaGhqirq8V11bOwuDAD2F4RQVVN"
            "MBAw+/fvdwAcx6G8/CN++vkic3NzhEIhPB4P5t0i8Hg8OI5DZWUl/f37cByHp0+f5oeHhwVVFeC7jW0bdXl5yU6n36hlZdS2C9rW"
            "1qYiouFwWFVVjx07qjU1NaqqGo1GtbOrU1XVVVU9d+7cK+ClV0RURLzPnz1n+/btfL5lC/O5eR48eEAymQSgKEtp8biuSygU4tat"
            "W0jRP8Ai4PPW1dWtTiQSew8dOsTVq1ffG310dIQ9e75kamqqtLh3JUUwxrC8vExjYyOnT59W13Xlzp07vps3b6qZnp3eJCKNR49+"
            "TSwWk3T6DUtLeVpaWolGo9i2jes6uK6LiFAoFIjFYszMvGZl9Ur6+vro6+ujubnZV/pCf0VFhV6/fk0BvXHjhqqqOo6jT5480TNn"
            "zmhTU6Oqqp46dUpFRH1lPvV4PFo0dYnxIuk3xuijR8N6//59VVVNJKY09/atzsy8VlXVffv6ddeuHq2vr/9PkTVr1ujly5fd27dv"
            "6+HDh5PAS29J5NraWqanX3PkyBFGR0fxer0sLeWprq5mZGSU8fFxAKqrq4lEIhw48BWtLS0kkq/o7e1lZGQEwPECpiS667rU19dz"
            "8uQJ4vE4jQ0NDD96xPHjJ9ixo4MrV36hsvITVqyoAsC2CwwODpJOp0kmkwURMQLsBu5WVVXR0NCAiJDNWoRCYZ2bSxEKhWVsfEyt"
            "jCWtra0qIqRSKQkGAnh9PpaXl5ic/Nv1+XxpUDHAvg0bPqO9vZ3Hjx+Tz+fx+/0MDw/L2nXrJJvNYmUs6e/vJzU7K7FYTLq6ukgk"
            "k8TjcTZvbiccDptCobDCtp0yAGvwm4Hs3d9+tYwx1vnzP1iXLl3MGGNm7t0bSp89ezYjIrMvXrzIHDx4MBUOh2fi8bjV1rbRam9v"
            "t8bHx6zI+vUWkDXGvPoHltRc+kJjfUoAAAAASUVORK5CYII="
        ),
        24: (
            "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"
            "VBrI/Bcn6TC22zvXJgAAAABJRU5ErkJggg=="
        ),
        32: (
            "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"
            "sICr/wHZsRvC4lLKewAAAABJRU5ErkJggg=="
        ),
        48: (
            "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"
        ),
    },
}


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
