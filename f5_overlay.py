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
Status buildup, Stun and the like are not decoded yet, so they are not shown.
A log file (off by default, [Debug] in the ini or 'i' in the menu) records what the program sees.
"""
import array
import configparser
import ctypes
import json
import zlib
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
    ("stun", "Stun", 0xA64, 0x872, "#ffe066"),
    ("tranq", "Tranq", 0x878, 0x87A, "#66e08a"),
]
# Trip length per (monster id, animation state at +0x838): measured full-length trips (shorter ones were interrupted by hits).
# Pink Rathian (id 37): states 0x504 and 0x1004 both last 8.0 s. Unknown monsters show 'Trip +Ns' (counting up) and the log
# gets STATE838 lines so more rows can be added here.
TRIP_TAIL = 1.7        # seconds between the end of the +0x8 count-up and the end of the trip animation (measured: 1.6-2.0)
TRIP_STATES = {(37, 0x504): 8.0, (37, 0x1004): 8.0}
OFF_PART_HP, PART_SLOTS = 0x358, 24     # part health: i16 at +0x358 + 8 * part index (Relial monster viewer, G1)
OFF_POISON_DUR, OFF_ATK, OFF_DEF = 0x890, 0x898, 0x89C
# Found by reading mhfo.dll (sleep / para application code): byte at +0x893 holds the state flags of the monster:
# 0x01 tranquilized, 0x02 asleep, 0x08 paralysed (set when the build-up reaches its threshold, cleared when it ends).
OFF_FLAGS = 0x893
FLAG_SLEEP, FLAG_PARA = 0x02, 0x08
# Fields watched in the log (candidates for the real sleep / para / flash / trap countdowns; 0x8B8 = total, 0x8BA =
# remaining of the monster's action timer). Written to the log whenever one changes, for large monsters.
DBG_FIELDS = [("b", 0x828), ("b", 0x892), ("b", 0x893), ("b", 0xA9B), ("b", 0xA9D), ("b", 0xBCC), ("b", 0xC1D),
              ("h", 0x866), ("h", 0x868), ("h", 0x86E), ("h", 0x870), ("h", 0x872), ("h", 0x874), ("h", 0x876),
              ("h", 0x882), ("h", 0x884), ("h", 0x88C), ("h", 0x88E), ("h", 0x8B8), ("h", 0x8BA), ("h", 0x8BC),
              ("h", 0xA64), ("h", 0xA66), ("h", 0x434), ("h", 0x448), ("i", 0x610), ("b", 0x8B0), ("b", 0x8B4), ("b", 0x8B6)]
# Quest timer (found with f5_timer_finder.py on F5): u32 frame counters, 30 per second, inside mhfo.dll
TIMER_REMAIN_RVA = 0x54ECA20       # frames left; the quest limit (u32, frames) follows right after it
TIMER_ELAPSED_RVA = 0x4B31A78      # frames since the quest started (the same value !timer prints as (1658f))
# Quest-end return countdown (60 s or less): found as a value that mirrors the quest time during the quest and
# then jumps to 1800 frames and counts down. A heap object whose pointer sits at a fixed place in mhfo.dll.
VERSION = "v33"
HUD_PTR_RVA, HUD_TIMER_OFF = 0x546C58C, 0x538     # confirmed with f5_timer_finder.py --return: [mhfo+0x546C58C]+0x538 counts 1800 -> 0
RETURN_MAX_FRAMES = 5400            # 3 minutes: bigger values are not a return countdown
HOLD_SECONDS = 5.0          # keep the last good monster list this long when a read comes back empty
MAX_SLOTS = 40
TABLE_RVA = 0x57D9AEE
KEY_RVA = 0xC6F714
CONST = 0x6A60
OFF_ID, OFF_CUR, OFF_MAX, OFF_SIZE = 0x03, 0x614, 0x6AC, 0xC2A
READ_LEN = 0xC40      # bytes read per monster slot (size at +0xC2A, pitfall counter at +0xC32)

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


trace = logging.getLogger("f5trace")          # extra detail, only goes to the trace file
trace.addHandler(logging.NullHandler())
trace.propagate = False
trace.setLevel(logging.DEBUG)
_trace_handler = None


def tracing():
    return _trace_handler is not None


def enable_trace(path):
    """One file with everything: the normal log lines plus every change of every monster field, the status build-ups
    and hits, and the search for the return counter."""
    global _trace_handler
    disable_trace()
    try:
        h = logging.handlers.RotatingFileHandler(path, maxBytes=80 * 1024 * 1024, backupCount=3, encoding="utf-8")
    except OSError as e:
        print("could not open the trace file", path, "-", e)
        return False
    h.setLevel(logging.INFO)
    h.setFormatter(logging.Formatter("%(asctime)s.%(msecs)03d [%(threadName)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
    log.addHandler(h)
    trace.addHandler(h)
    _trace_handler = h
    log.info("Trace file on: %s (version %s)", path, VERSION)
    return True


def disable_trace():
    global _trace_handler
    if _trace_handler is not None:
        log.removeHandler(_trace_handler)
        trace.removeHandler(_trace_handler)
        _trace_handler.close()
        _trace_handler = None


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
    ("General", "show_return_timer", "show_return_timer", "true", "bool",
     "after a quest: show the return countdown (60 seconds, or shorter in some quests) instead of the quest time"),
    ("General", "timer_without_monsters", "timer_always", "false", "bool",
     "show the quest timer on its own, even when no (large) monster is present (only while a quest is running)"),
    ("General", "capture_skip_untrappable", "capture_skip_untrappable", "true", "bool",
     "never show the capture state for monsters that cannot be trapped / captured (elder dragons, list below)"),
    ("General", "untrappable_ids", "untrappable_ids", "2,7,50,55,58,72,93,149", "str",
     "monster numbers that cannot be trapped or captured (Fatalis, Lao-Shan Lung, Ashen Lao-Shan Lung, Shen Gaoren, "
     "Yama Tsukami, Raviente, Berserk Raviente); edit freely"),
    ("General", "diag_fields", "diag_fields", "true", "bool",
     "write the monster fields that change during a hunt into the log (needed to find the flash / trap / enrage "
     "fields; switch off when everything works, it makes the log bigger)"),
    ("General", "capture_needs_tranq", "capture_needs_tranq", "true", "bool",
     "show the capture state only for monsters that can be tranquilized (the game gives them a tranquilizer "
     "threshold above 0; monsters that cannot be captured have 0)"),
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
    ("General", "show_messages", "show_messages", "true", "bool",
     "show the game status messages in the console ([waiting for mhf.exe], [game running, no quest], [ok] and the status line of the menu)"),

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
     "sleep, stun, tranquilizer). The offsets come from the G1 client and still have to be confirmed on F5"),
    ("Status", "mode", "status_mode", "active", "off|active|all",
     "off = nothing, active = only statuses that have started to build up (value above 0), all = every status always"),
    ("Status", "show_invalid", "status_invalid", "false", "bool",
     "in mode 'all': also list the statuses the monster has no threshold for, as '-' (for example 'Para -')"),
    ("Status", "poison", "st_poison", "true", "bool", "show poison"),
    ("Status", "para", "st_para", "true", "bool", "show paralysis"),
    ("Status", "sleep", "st_sleep", "true", "bool", "show sleep"),
    ("Status", "stun", "st_stun", "true", "bool", "show Stun"),
    ("Status", "tranq", "st_tranq", "true", "bool", "show tranquilizer"),
    ("Status", "active_timers", "st_active_timers", "true", "bool",
     "show how long a monster has been asleep / paralysed (counted from the moment the game sets the state)"),
    ("Status", "sleep_timer", "st_sleep_timer", "true", "bool", "show the time since the monster fell asleep"),
    ("Status", "para_timer", "st_para_timer", "true", "bool", "show the time since the monster was paralysed"),
    ("Status", "enrage_timer", "st_enrage_timer", "true", "bool",
     "show how long a monster has been enraged (detected from its attack multiplier going up), as 'Enrage 12s'"),
    ("Status", "flash_timer", "st_flash_timer", "true", "bool",
     "show the flash (flash bomb) time left as 'Flash 9s' (experimental: found from one observation of the monster "
     "data, the counter at +0xB46 with flag 0x800 at +0xA6A)"),
    ("Status", "pitfall_timer", "st_pitfall_timer", "true", "bool",
     "show the pitfall hold time left as 'Pitfall 14s' (counter at +0xC32, its start value is the real hold length)"),
    ("Status", "shock_timer", "st_shock_timer", "true", "bool",
     "show the shock trap hold time left as 'Shock 6s' (counter at +0x8, 240 frames = 8 s on the Rathian)"),
    ("Status", "parts", "show_parts", "false", "bool",
     "EXPERIMENTAL: list the break-part health values stored in the monster (+0x358 + 8 per part, parts with a value above 0), "
     "as 'Parts 0:120 3:80'. Part names are not known, only the numbers"),
    ("Status", "names", "st_names", "true", "bool",
     "show the status names (Poison, Para, Enrage ...). Off = only the icon and the numbers; a name is only hidden on lines "
     "that have an icon, so with icons off the names stay"),
    ("Status", "icon_outline", "icon_outline", "true", "bool",
     "keep the black outline of the status icons. Off = the near-black pixels around the icons are made transparent"),
    ("Status", "layout", "status_layout", "poison,para,sleep,tranq|stun,enrage,trip,flash,shock,pitfall|parts", "str",
     "lvdew style: which statuses go on which line. Lines are separated by |, statuses by commas, in display order. Names: "
     "poison para sleep stun tranq enrage trip flash shock pitfall parts. A status that is not listed goes on the first line. "
     "Empty = everything on one line"),
    ("Status", "percent", "st_percent", "true", "bool",
     "show the build-up percentage of poison / para / sleep / Stun / tranq: 'Para 100/200 (50%) 8s' (never for enrage, "
     "traps, flash or trip)"),
    ("Status", "percent_hide", "st_percent_hide", "none", "none|zero|full|both",
     "hide the percentage when the build-up is at 0% (zero), at 100% (full), at both (both) or never (none)"),
    ("Status", "icons", "status_icons", "always", "off|always|active",
     "status icons (lvdew style): off = none, always = an icon in front of every status line, active = the icon only "
     "while that status / trap / enrage timer is running"),
    ("Status", "trip_timer", "st_trip_timer", "true", "bool",
     "show the 'Trip' timer: the monster stumbles / is held for about 16.5 s (the counter at +0x8 counts up 0..120 twice)"),
    ("Status", "stun_timer", "st_stun_timer", "true", "bool", "show the Stun time left as 'Stun 8/125 7s' (counter at +0x8, 300 frames = 10 s)"),
    ("Status", "trap_color", "st_trap_color", "#7fd4ff", "str", None),
    ("Status", "pitfall_color", "st_pitfall_color", "#70c888", "str", None),
    ("Status", "shock_color", "st_shock_color", "#b890c0", "str", None),
    ("Status", "sleep_seconds", "st_sleep_seconds", "0", "int:0:600",
     "length of a sleep in seconds used for the countdown (0 = measure it: the overlay learns it and counts up until then)"),
    ("Status", "para_seconds", "st_para_seconds", "0", "int:0:600",
     "length of a paralysis in seconds for the countdown (0 = measure and learn)"),
    ("Status", "enrage_seconds", "st_enrage_seconds", "0", "int:0:600",
     "length of an enrage in seconds for the countdown (0 = measure and learn)"),
    ("Status", "status_time_overrides", "status_time_overrides", "", "str",
     "per monster lengths, 'number:sleep:para' separated by commas, e.g. 27:40:25,11:30:30"),
    ("Status", "multipliers", "show_multipliers", "false", "bool",
     "show the monster's attack and defense multiplier (rage / enrage, hardened states)"),
    ("Status", "effective_hp", "show_effective_hp", "false", "bool",
     "show HP divided by the defense multiplier (effective HP) next to the HP numbers"),
    ("Status", "poison_color", "st_poison_color", "#c084fc", "str", "colours: names or #rrggbb"),
    ("Status", "para_color", "st_para_color", "#ffb347", "str", None),
    ("Status", "sleep_color", "st_sleep_color", "#6cb8ff", "str", None),
    ("Status", "stun_color", "st_stun_color", "#ffe066", "str", None),
    ("Status", "tranq_color", "st_tranq_color", "#66e08a", "str", None),
    ("Status", "enrage_color", "st_enrage_color", "#ff6a4d", "str", None),
    ("Status", "flash_color", "st_flash_color", "#fff3a0", "str", None),

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
    ("Debug", "trace_log", "trace_log", "false", "bool",
     "with the log on, also write the trace file (f5_overlay_trace.log): the normal log plus EVERY change of every "
     "monster field, status build-ups and hits, and the search for the return counter. Big, but it is what is needed "
     "to find new timers"),
    ("Debug", "hitzone_dump", "hz_dump", "false", "bool",
     "ONE-TIME research: when a quest starts, search the monsters' loaded data for the hit-zone tables (header with pointers at "
     "+0xD4..+0x11C, 0x28-byte entries ended by 0xFFFF, found by the relocation code of mhfo.dll) and write them to "
     "f5_hitzone_dump.json next to the ini (a few hundred KB). Send me that file to build the hit-zone display"),
    ("Debug", "trace_file", "trace_file", "f5_overlay_trace.log", "str", "name of the trace file"),
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
            old = {"stun": "ko", "stun_timer": "ko_timer", "stun_color": "ko_color"}.get(key)      # settings renamed KO -> Stun
            if old and cp.has_section(sec) and not cp.has_option(sec, key) and cp.has_option(sec, old):
                raw = cp.get(sec, old)
            if key == "layout" and isinstance(raw, str):
                raw = raw.replace("|ko,", "|stun,").replace(",ko,", ",stun,").replace(",ko|", ",stun|").replace("|ko|", "|stun|")
                raw = "stun" if raw.strip() == "ko" else raw
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


def act_text(a):
    """(elapsed, length or None) -> ' 18s' (time left) or ' +12s' (counting up while the length is unknown)."""
    el, exp = a
    if exp and exp > el:
        return " %ds" % math.ceil(exp - el)
    return " +%ds" % int(el)


def untrappable_set(settings):
    text = settings.untrappable_ids
    cache = getattr(settings, "_untrap_cache", None)
    if cache is None or cache[0] != text:
        ids = set()
        for part in str(text).replace(";", ",").split(","):
            part = part.strip()
            if part.isdigit():
                ids.add(int(part))
        cache = (text, ids)
        settings._untrap_cache = cache
    return cache[1]


def pct_text(v, settings):
    """' (50%)' for a build-up (cur, thr) per the percent settings; '' when switched off or hidden at 0% / 100%."""
    if not settings.st_percent or not v or v[1] <= 0:
        return ""
    pct = max(0, min(100, int(100 * v[0] / v[1])))
    hide = settings.st_percent_hide
    if (pct == 0 and hide in ("zero", "both")) or (v[0] >= v[1] and hide in ("full", "both")):
        return ""
    return " (%d%%)" % pct


def _nm(settings, label, icon, bare=False):
    """Status name in front of a value; '' when names are off and the line has an icon. bare = without trailing space."""
    if not (settings.st_names or icon is None):
        return ""
    return label if bare else label + " "


def layout_lines(e, settings):
    """Indices of e['status'] grouped into display lines by the status_layout setting."""
    keys = e.get("st_keys") or []
    n = len(e.get("status") or [])
    spec = (settings.status_layout or "").strip()
    if not spec:
        return [list(range(n))] if n else []
    lines, used = [], set()
    for ln in spec.split("|"):
        row = []
        for name in [x.strip().lower() for x in ln.split(",") if x.strip()]:
            for i, k in enumerate(keys[:n]):
                if k == name and i not in used:
                    row.append(i)
                    used.add(i)
        lines.append(row)
    rest = [i for i in range(n) if i not in used]
    if rest:
        if lines:
            lines[0] += rest
        else:
            lines = [rest]
    return [r for r in lines if r]


def _icon_for(settings, name, running):
    """Icon name for a status line, or None: status_icons off / always / active (only while running)."""
    mode = settings.status_icons
    if mode == "off" or (mode == "active" and not running):
        return None
    return name


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
        if in_range and settings.capture_skip_untrappable and m["id"] in untrappable_set(settings):
            in_range = False                    # elder dragons etc.: cannot be trapped, so no capture state
        if in_range and settings.capture_needs_tranq and m.get("st") is not None and "tranq" not in m["st"]:
            in_range = False                    # tranquilizer threshold is 0: this monster cannot be captured
        low = (hi == 0 and settings.low_hp_percent > 0 and bool(is_large)
               and pct_exact <= settings.low_hp_percent)
        e = {"id": m["id"], "name": name, "large": bool(is_large), "cur": cur, "max": mx,
             "pct": math.ceil(pct_exact), "frac": frac, "lo": lo, "hi": hi,
             "size": m.get("size") if is_large else None,
             "crown": get_crown(m.get("size"), m["id"], name, settings) if is_large else "",
             "dead": bool(is_large and cur <= 0),
             "capture": bool(is_large and cur > 0 and settings.highlight_capturable and in_range),
             "hot": bool(is_large and settings.highlight_capturable and (in_range or low))}
        e["status"], e["st_icons"], e["st_keys"], e["mult"], e["eff"] = [], [], [], "", None
        if is_large:
            dfn, atk = m.get("dfn"), m.get("atk")
            if settings.status_mode != "off":
                show_all = settings.status_mode == "all"
                for key, label, _t, _c, _col in STATUS_DEFS:
                    if not getattr(settings, "st_" + key):
                        continue
                    v = (m.get("st") or {}).get(key)
                    colr = getattr(settings, "st_%s_color" % key)
                    a_ = (m.get("act") or {}).get(key) if key in ("sleep", "para", "stun") else None
                    if a_ is not None and not (settings.st_active_timers and getattr(settings, "st_%s_timer" % key)):
                        a_ = None
                    if v and (show_all or v[0] > 0 or a_ is not None):
                        extra = ""
                        if key == "poison" and m.get("pdur"):
                            extra = " %ds" % (m["pdur"] // 30)
                        elif a_ is not None:
                            extra = act_text(a_)
                        running = (bool(m.get("pdur")) if key == "poison" else
                                   bool(m.get("f893", 0) & 0x01) if key == "tranq" else
                                   (m.get("act") or {}).get(key) is not None)
                        ic_ = _icon_for(settings, "capture" if key == "tranq" else key, running)
                        e["status"].append((f"{_nm(settings, label, ic_)}{v[0]}/{v[1]}{pct_text(v, settings)}{extra}", colr))
                        e["st_icons"].append(ic_)
                        e["st_keys"].append(key)
                    elif not v and show_all and settings.status_invalid:
                        e["status"].append((f"{label} -", colr))
            a_ = (m.get("act") or {}).get("enrage")
            if a_ is not None and settings.st_active_timers and settings.st_enrage_timer:
                ic_ = _icon_for(settings, "rage", True)
                e["status"].append(((_nm(settings, "Enrage", ic_, True) + act_text(a_)).lstrip(), settings.st_enrage_color))
                e["st_icons"].append(ic_)
                e["st_keys"].append("enrage")
            tr_ = m.get("trip")
            if tr_ is not None and settings.st_active_timers and settings.st_trip_timer:
                ic_ = _icon_for(settings, "trip", True)
                e["status"].append(("%s%ds" % (_nm(settings, "Trip", ic_), math.ceil(tr_)), settings.st_trap_color))
                e["st_icons"].append(ic_)
                e["st_keys"].append("trip")
            tu_ = m.get("trip_up")
            if tu_ is not None and tr_ is None and settings.st_active_timers and settings.st_trip_timer:
                ic_ = _icon_for(settings, "trip", True)
                e["status"].append(("%s+%ds" % (_nm(settings, "Trip", ic_), int(tu_)), settings.st_trap_color))
                e["st_icons"].append(ic_)
                e["st_keys"].append("trip")
            tp = m.get("trap")
            hd = m.get("hold")
            if tp is None and hd is not None and hd[0] == "Shock":
                tp = hd
            if tp is not None and settings.st_active_timers and (
                    settings.st_pitfall_timer if tp[0] == "Pitfall" else settings.st_shock_timer):
                pit_ = tp[0] == "Pitfall"
                ic_ = _icon_for(settings, "pitfall" if pit_ else "shock", True)
                e["status"].append(("%s%ds" % (_nm(settings, tp[0], ic_), math.ceil(tp[1])),
                                    settings.st_pitfall_color if pit_ else settings.st_shock_color))
                e["st_icons"].append(ic_)
                e["st_keys"].append("pitfall" if pit_ else "shock")
            fl = m.get("flash")
            if fl is not None and settings.st_active_timers and settings.st_flash_timer:
                ic_ = _icon_for(settings, "flash", True)
                e["status"].append(("%s%ds" % (_nm(settings, "Flash", ic_), math.ceil(fl)), settings.st_flash_color))
                e["st_icons"].append(ic_)
                e["st_keys"].append("flash")
            if settings.show_parts and m.get("parts"):
                pl = ["%d:%d" % (i, v) for i, v in m["parts"] if 0 < v < 30000]
                if pl:
                    e["status"].append(("Parts " + " ".join(pl), "#ffffff"))
                    e["st_icons"].append(None)
                    e["st_keys"].append("parts")
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


def strip_outline_png(b64, dark=70):
    """base64 PNG (RGBA, 8 bit, not interlaced) -> base64 PNG where the near-black pixels (the icon outline) are
    transparent. Falls back to the original picture when the format is not as expected."""
    import base64, zlib
    try:
        raw = base64.b64decode(b64)
        pos, idat, ihdr = 8, b"", None
        while pos < len(raw):
            ln, typ = struct.unpack(">I4s", raw[pos:pos + 8])
            body = raw[pos + 8:pos + 8 + ln]
            if typ == b"IHDR":
                ihdr = struct.unpack(">IIBBBBB", body)
            elif typ == b"IDAT":
                idat += body
            pos += 12 + ln
        w, h, depth, ctype, _c, _f, inter = ihdr
        if depth != 8 or ctype != 6 or inter != 0:
            return b64
        data = zlib.decompress(idat)
        bpp, stride = 4, w * 4
        rows, prev = [], bytearray(stride)
        p = 0
        for _y in range(h):
            ft = data[p]
            cur = bytearray(data[p + 1:p + 1 + stride])
            p += 1 + stride
            for i in range(stride):
                a = cur[i - bpp] if i >= bpp else 0
                b = prev[i]
                c = prev[i - bpp] if i >= bpp else 0
                if ft == 1:
                    cur[i] = (cur[i] + a) & 255
                elif ft == 2:
                    cur[i] = (cur[i] + b) & 255
                elif ft == 3:
                    cur[i] = (cur[i] + ((a + b) >> 1)) & 255
                elif ft == 4:
                    pa, pb, pc = abs(b - c), abs(a - c), abs(a + b - 2 * c)
                    pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                    cur[i] = (cur[i] + pr) & 255
            rows.append(cur)
            prev = cur
        out = bytearray()
        for cur in rows:
            out.append(0)
            for x in range(0, stride, 4):
                r, g, bl, al = cur[x:x + 4]
                if al and max(r, g, bl) <= dark:
                    cur[x:x + 4] = b"\0\0\0\0"
            out += cur

        def chunk(t, d):
            c = struct.pack(">I", len(d)) + t + d
            return c + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
        png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
               + chunk(b"IDAT", zlib.compress(bytes(out), 9)) + chunk(b"IEND", b""))
        return base64.b64encode(png).decode()
    except Exception:
        return b64


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

    def writable_regions(self, max_region=96 << 20, max_total=420 << 20):
        """Committed, writable memory regions (where a countdown can live), capped in size."""
        addr, mbi, total = 0, MBI(), 0
        while addr < 0x7FFFFFFF:
            if not k32.VirtualQueryEx(self.h, ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi)):
                break
            if (mbi.State == 0x1000 and (mbi.Protect & 0xCC) and not (mbi.Protect & 0x100)
                    and 0 < mbi.RegionSize <= max_region):
                total += mbi.RegionSize
                if total > max_total:
                    break
                yield mbi.BaseAddress, mbi.RegionSize
            addr = mbi.BaseAddress + mbi.RegionSize

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
        """Process ids of this program and of the terminal programs above it (cmd, PowerShell, Windows Terminal ...).
        Only programs that are really terminals count: explorer.exe (it started the script when you double-click it,
        and it is also the taskbar / desktop / Alt+Tab) must never be treated as the console."""
        if getattr(self, "_term_pids", None) is None:
            names = {"cmd.exe", "powershell.exe", "pwsh.exe", "windowsterminal.exe", "wt.exe", "conhost.exe",
                     "openconsole.exe", "python.exe", "pythonw.exe", "py.exe", "pyw.exe", "bash.exe", "wsl.exe",
                     "mintty.exe", "alacritty.exe", "f5overlay.exe"}
            pids, info = {os.getpid()}, {}
            try:
                snap = k32.CreateToolhelp32Snapshot(0x2, 0)
                if snap not in _BAD:
                    e = PROCESSENTRY32W()
                    e.dwSize = ctypes.sizeof(e)
                    ok = k32.Process32FirstW(snap, ctypes.byref(e))
                    while ok:
                        info[e.th32ProcessID] = (e.th32ParentProcessID, e.szExeFile.lower())
                        ok = k32.Process32NextW(snap, ctypes.byref(e))
                    k32.CloseHandle(snap)
                p = os.getpid()
                for _ in range(8):
                    par = info.get(p, (None, ""))[0]
                    if not par or par in pids or par not in info:
                        break
                    if info[par][1] not in names:
                        break                           # explorer.exe or anything else: stop here
                    pids.add(par)
                    p = par
                log.info("Terminal processes: %s", sorted((q, info.get(q, (0, "?"))[1]) for q in pids))
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


def _edit_controls(hwnd, with_text=False):
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
                        "w": r.right - r.left, "id": u32.GetDlgCtrlID(h),
                        "text": _get_text(target) if with_text else "",
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
    """Wait for the launcher window of mhf.exe and type the login name / password into its fields.
    Each field is written once, checked after a moment, and rewritten at most twice more: the launcher clicks every
    time a field is written, so it must not be written continuously."""
    t0, logged, listed = time.time(), False, set()
    want = {"user": s.username, "pass": s.password}
    done = {k: not v for k, v in want.items()}                 # nothing to write -> already done
    tries, last = {"user": 0, "pass": 0}, {"user": 0.0, "pass": 0.0}
    if all(done.values()):
        return True

    def same(a, b):
        return a.strip().lower() == b.strip().lower()

    while time.time() - t0 < stop_after:
        time.sleep(0.4)
        pids = set(find_pids(TARGET_PROC))
        if not pids:
            continue
        for hwnd in _windows_of(pids):
            edits = _edit_controls(hwnd, with_text=not logged)
            if not edits:
                if time.time() - t0 > 8 and hwnd not in listed:
                    listed.add(hwnd)
                    log.info("Window 0x%X (%s) has no edit controls; children: %s", hwnd, _class_of(hwnd),
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
            for key, ed in (("user", user_ed), ("pass", pass_ed)):
                if done[key] or ed is None or time.time() - last[key] < 1.2:
                    continue
                if key == "pass":
                    # Windows never hands the text of another program's password box back (it always reads 0
                    # characters), so the old "check and rewrite" loop typed the password three times. Write it once,
                    # and check only the length, which the password box does report.
                    _set_text(ed["h"], want[key])
                    tries[key] = 1
                    last[key] = time.time()
                    time.sleep(0.15)
                    n = u32.SendMessageW(ed["h"], 0x00C1, ctypes.c_size_t(-1).value, 0)      # EM_LINELENGTH
                    done[key] = True
                    log.info("Launcher pass field written once (box reports %d characters, expected %d)",
                             n, len(want[key]))
                    continue
                cur = _get_text(ed["h"])
                if tries[key] and same(cur, want[key]):
                    done[key] = True
                    log.info("Launcher %s field filled (attempt %d)", key, tries[key])
                elif tries[key] >= 3:
                    done[key] = True
                    log.info("Launcher %s field: gave up after 3 attempts (field reads back %d characters)",
                             key, len(cur))
                else:
                    _set_text(ed["h"], want[key])
                    tries[key] += 1
                    last[key] = time.time()
            if all(done.values()):
                log.info("Launcher fields done")
                return True
    log.info("Launcher fields: stopped (%s)", "window never found" if not logged else "timeout")
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

def status_overrides(settings):
    """'27:40:25, 11:30:30' -> {(27, 'sleep'): 40, (27, 'para'): 25}  (monster number : sleep s : paralysis s)."""
    text = settings.status_time_overrides
    cache = getattr(settings, "_ovr_cache", None)
    if cache is None or cache[0] != text:
        out = {}
        for part in str(text).split(","):
            bits = [b.strip() for b in part.split(":")]
            if len(bits) == 3 and all(b.isdigit() for b in bits):
                out[(int(bits[0]), "sleep")] = int(bits[1])
                out[(int(bits[0]), "para")] = int(bits[2])
        cache = (text, out)
        settings._ovr_cache = cache
    return cache[1]


class Reader(threading.Thread):
    def __init__(self, monsters, interval=0.03, debug=False):
        super().__init__(daemon=True)
        self.interval = interval
        self.g = Game()
        self.monsters = monsters
        self.debug = debug
        self.state = {"status": "waiting for mhf.exe", "monsters": []}
        self.settings = None         # set by main(); used for the show_messages switch
        self._last_sig = None
        self._lock = threading.Lock()
        self._raw_logged = set()
        self.timer = None            # (limit_frames, remaining_frames) or None
        self.retcd = None            # frames left of the return countdown after a quest, or None
        self._tm_last = None         # state of the quest timer / return detection
        self._tm_change = 0.0
        self._tm_running = False
        self._hud_prev = None
        self._cv, self._cv_lock, self._cv_last, self._cv_t, self._cv_diag = {}, None, 1 << 30, 0.0, 0.0
        self._diag_return = 0.0
        self._hud_dec = 0
        self._seen = {}              # (slot, id) -> consecutive reads in which the entry looked valid
        self._good = []              # last list that was shown
        self._good_t = 0.0
        self._diag_t = 0.0
        self._arr = None             # last array address that held live monsters
        self._hz_arr = None          # array for which the hit-zone dump was already made
        self._arr_t = 0.0
        self._alt, self._alt_t = None, 0.0
        self._alt_chk = 0.0
        self._low_chk = 0.0
        self._act = {}
        self._dbg = {}
        self._raw, self._volatile, self._diff_t = {}, {}, 0.0
        self._hpmax = {}
        self._tr_st, self._tr_f, self._tr_full, self._tr_snap_t, self._hits = {}, {}, {}, {}, {}
        self._ret_done = False
        self._tev = {}
        self._atk_base = {}
        self._hunt = None
        self._hunt_addrs = []
        self._fz_t = 0.0
        self._hunt_n = 0
        self._hunt_given = 0.0
        self._dbg_t = 0.0
        self.timer_live = False
        self.why = ""                # reason of the last empty read (goes into the log)

    def read_timer(self):
        g = self.g
        base = g.mhfo[0]
        remain = g.u32(base + TIMER_REMAIN_RVA)
        limit = g.u32(base + TIMER_REMAIN_RVA + 4)
        if remain is None or limit is None or not (0 < limit <= 30 * 3600 * 4) or remain > limit:
            return None
        return limit, remain

    def read_return(self, timer):
        """Return countdown. Active only after the quest timer has stopped, while the game's HUD value counts down."""
        g = self.g
        now = time.time()
        remain = timer[1] if timer else None
        if remain is not None:
            if remain != self._tm_last:
                if self._tm_last is not None and remain < self._tm_last:
                    self._tm_running = True
                self._tm_last, self._tm_change = remain, now
        self.timer_live = remain is not None and self._tm_change > 0 and now - self._tm_change < 2.0
        frozen = (self._tm_running and now - self._tm_change > 1.0 and remain is not None and timer[0]
                  and remain < timer[0] - 30)             # a clock that just sits at its start value is not stopped
        if frozen:
            if not self._fz_t:
                self._fz_t = now
                self._hunt, self._hunt_addrs, self._hunt_n = None, [], 0
                self._cv = {}
                self._ret_done = False
                trace.info("QUEST CLOCK STOPPED at %s: looking for the return countdown", timer)
        else:
            self._fz_t = 0.0
            self._ret_done = False
        hud = None
        p = g.u32(g.mhfo[0] + HUD_PTR_RVA)
        if p and 0x10000 <= p < 0x7FFF0000:
            hud = g.u32(p + HUD_TIMER_OFF)
        if hud is not None and self._hud_prev is not None and hud != self._hud_prev:
            self._hud_dec = self._hud_dec + 1 if hud < self._hud_prev else 0
        if hud != self._hud_prev:
            self._hud_prev = hud
        if frozen and now - self._diag_return > 5.0:
            self._diag_return = now
            log.info("Return watch: quest timer stopped at %s, hud pointer %s value %s (decreasing reads: %d)",
                     remain, hex(p) if p else p, hud, self._hud_dec)
        if not (frozen and hud is not None and 30 < hud <= RETURN_MAX_FRAMES):
            self._hud_dec = 0
            scanned = self.return_scan(frozen)
            if scanned is not None:
                if self.retcd is None:
                    log.info("Return countdown started (auto-detected): %d frames, quest timer %s", scanned, timer)
                return scanned
            if self.retcd is not None:
                log.info("Return countdown ended / not active (hud=%s, quest timer %s)", hud, timer)
            return None
        if self._hud_dec >= 3:
            if self.retcd is None:
                log.info("Return countdown started: %d frames (%.1f s), quest timer %s", hud, hud / 30.0, timer)
            return hud
        return self.retcd if self.retcd is not None and self._hud_dec else None

    # -- return countdown, auto detection ------------------------------------------------------------------
    def _hunt_addrs_live(self):
        """True while the addresses the hunt found are still being tracked (about 4 s before trying again)."""
        if not self._hunt_addrs:
            return False
        if time.time() - self._hunt_given > 4.0:
            self._hunt_addrs = []
            return False
        return True

    def _scan_blocks(self):
        g = self.g
        base = g.mhfo[0]
        blocks = []
        seen_p = []
        for rva in (HUD_PTR_RVA - 0x10, HUD_PTR_RVA - 8, HUD_PTR_RVA, 0xC1D160, 0x1E3721C, 0x546A858):
            p = g.u32(base + rva)
            if p and 0x10000 <= p < 0x7FFF0000 and not any(abs(p - q) < 0x1000 for q in seen_p):
                seen_p.append(p)
                blocks.append((p - 0x400, 0x2400))
        blocks.append((base + TIMER_REMAIN_RVA - 0x200, 0x400))
        for a in self._hunt_addrs:
            blocks.append((a, 4))
        return blocks

    def _hash_pages(self, want=None):
        """{page address: crc} of all writable memory (want = None), or {page: bytes} of the pages whose crc differs
        from the dict `want`. Pages are 4 KB; identical pages are skipped, so only what changed is examined."""
        g = self.g
        hashes, changed = {}, {}
        for base, size in g.writable_regions():
            for off in range(0, size, 1 << 20):
                n = min(1 << 20, size - off)
                d = g.read(base + off, n)
                if not d:
                    continue
                for i in range(0, len(d) - 4095, 4096):
                    page = d[i:i + 4096]
                    a = base + off + i
                    c = zlib.crc32(page)
                    hashes[a] = c
                    if want is not None and a in want and want[a] != c and len(changed) < 6000:
                        changed[a] = page
        return hashes, changed

    def hunt_step(self, now):
        """Memory-wide search for the return countdown: pages that change between snapshots, then words in them that
        go down by about 30 per second. Runs in three steps (hash everything, keep changed pages, compare again)."""
        h = self._hunt
        if h is None:
            if self._hunt_n >= 6:
                return
            t0 = time.time()
            hashes, _ = self._hash_pages()
            self._hunt = {"phase": 1, "t": time.time(), "hash": hashes}
            log.info("Return hunt %d: %d pages hashed in %.2f s", self._hunt_n + 1, len(hashes), time.time() - t0)
            return
        if now - h["t"] < 0.6:
            return
        if h["phase"] == 1:
            ts = time.time()
            hashes, changed = self._hash_pages(h["hash"])
            self._hunt = {"phase": 2, "t": (ts + time.time()) / 2, "pages": changed}     # mid-pass time of the copies
            log.info("Return hunt: %d pages changed", len(changed))
            return
        if h["phase"] == 2:
            ts = time.time()
            found = []
            pages = list(h["pages"].items())
            dt = max(0.3, (ts + (ts + 0.02 * len(pages) / 1000)) / 2 - h["t"])      # the re-reads are quick
            for a, old in pages:
                new = self.g.read(a, 4096)
                if not new or len(new) < 4096:
                    continue
                if new == old:
                    continue
                wo = struct.unpack("<1024I", old)
                wn = struct.unpack("<1024I", new)
                for i in range(1024):
                    vo, vn = wo[i], wn[i]
                    if vn < vo and 30 < vo <= RETURN_MAX_FRAMES:
                        rate = (vo - vn) / dt
                        if 12 <= rate <= 70:
                            found.append(a + 4 * i)
                            if len(found) >= 300:
                                break
                if len(found) >= 300:
                    break
            self._hunt_addrs = found
            self._hunt_given = time.time()
            self._hunt = None
            self._hunt_n += 1
            log.info("Return hunt: %d words falling at about 30/s: %s", len(found),
                     " ".join("%X" % x for x in found[:12]))

    def return_scan(self, quest_over):
        """Looks for a u32 that counts down at about 30 per second (a 30 fps countdown) in the objects around the quest
        HUD, because the counter does not always sit at the same offset. Returns the frames left or None."""
        g = self.g
        now = time.time()
        if self._cv_lock is not None:
            v = g.u32(self._cv_lock)
            if v is not None and 0 < v <= RETURN_MAX_FRAMES and v <= self._cv_last:
                self._cv_last = v
                return v
            if (v is not None and self._cv_last <= 150 and self._cv_last + 30 < v <= RETURN_MAX_FRAMES
                    and not getattr(self, "_cv_reloaded", False)):
                # the ~2.8 s lead-in reloads the same counter to the real 60 s / 20 s value: keep following it
                self._cv_reloaded = True
                self._cv_last = v
                log.info("Return counter 0x%X reloaded to %d (%.1f s)", self._cv_lock, v, v / 30.0)
                return v
            log.info("Return counter 0x%X ended (value %s)", self._cv_lock, v)
            if not v or v <= 30:
                # it counted down to the end: the later countdowns (reward screens, 120 s) are not this one
                self._ret_done = True
                log.info("Return countdown finished; later countdowns in this quest are ignored")
            self._cv_lock, self._cv, self._cv_last = None, {}, 1 << 30
            self._cv_reloaded = False
            return None
        if self._ret_done:
            return None
        if not quest_over:
            self._cv = {}
            return None
        if self._fz_t and now - self._fz_t < 150 and not self._hunt_addrs_live():
            try:
                self.hunt_step(now)
            except Exception:
                log.exception("Return hunt failed")
                self._hunt_n = 99
        if now - self._cv_t < 0.2:
            return None
        self._cv_t = now
        seen = 0
        for addr, size in self._scan_blocks():
            data = g.read(addr, size)
            if not data:
                continue
            n = len(data) // 4
            for i, v in enumerate(struct.unpack_from("<%dI" % n, data)):
                if not (30 < v <= RETURN_MAX_FRAMES):
                    continue
                a = addr + 4 * i
                seen += 1
                e = self._cv.get(a)
                if e is None:
                    self._cv[a] = [v, now, 0]
                    continue
                dt = now - e[1]
                if v < e[0]:
                    rate = (e[0] - v) / max(dt, 0.01)
                    e[2] = e[2] + 1 if 18 <= rate <= 45 else 0
                elif v > e[0]:
                    e[2] = 0
                e[0], e[1] = v, now
                if e[2] >= 4 and dt > 0.05:
                    self._cv_lock, self._cv_last = a, v
                    self._cv_reloaded = False
                    if tracing():
                        threading.Thread(target=self._pointer_hunt, args=(a,), daemon=True, name="pointers").start()
                    log.info("Return counter found at 0x%X (value %d = %.1f s), quest timer %s", a, v, v / 30.0,
                             self.timer)
                    return v
        if now - self._cv_diag > 5.0:
            self._cv_diag = now
            log.info("Return scan: %d candidate words in range, none counting down at 30/s yet", seen)
        return None

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

    def _slot_monster(self, blk, slot, tbl, key, rej):
        """One monster struct -> dict, or None when it is empty / garbage (reason appended to rej)."""
        need = READ_LEN
        if not blk or len(blk) < need or blk[0] != 1 or blk[1] != 1:
            return None
        t = struct.unpack_from("<H", tbl, 2 * (MAX_SLOTS - 1 - slot))[0]
        w1 = struct.unpack_from("<H", blk, OFF_CUR)[0]
        w2 = struct.unpack_from("<H", blk, OFF_MAX)[0]
        cur, mx = decode_hp(w1, w2, t, key)
        if mx > 0 and mx + 1 < cur <= 3 * mx:
            # the game can raise the HP above the stored maximum a few seconds into a quest (party-size multiplier):
            # accept it and use the highest value seen as the maximum
            mx = cur
        if mx > 0:
            hk = (slot, blk[OFF_ID])
            mx = max(mx, self._hpmax.get(hk, 0))
            self._hpmax[hk] = mx
        if mx <= 0 or cur < -1 or cur > mx + 1:
            rej.append("slot %d id %d hp-decode %d/%d (key %X T %X)" % (slot, blk[OFF_ID], cur, mx, key, t))
            return None
        size = struct.unpack_from("<H", blk, OFF_SIZE)[0]
        size = size if 10 <= size <= 400 else None
        mid = blk[OFF_ID]
        info = self.monsters.get(mid)
        if mid == 0 or (info is not None and info[3] and size is None):
            rej.append("slot %d id %d no size (raw %d)" % (slot, mid, size or 0))
            return None                      # a large monster always has a size: this slot is stale / garbage
        if info is None and size is None:
            return None
        mon = {"slot": slot, "id": mid, "cur": max(cur, 0), "max": mx, "size": size}
        try:
            mon.update(self._read_status(blk))
        except struct.error:
            pass
        if info is not None and info[3]:
            mon["f893"] = blk[OFF_FLAGS]
            a6a, b46 = struct.unpack_from("<H", blk, 0xA6A)[0], struct.unpack_from("<H", blk, 0xB46)[0]
            mon["h8"] = struct.unpack_from("<H", blk, 0x8)[0]
            mon["parts"] = [(i, struct.unpack_from("<h", blk, OFF_PART_HP + 8 * i)[0]) for i in range(PART_SLOTS)]
            mon["h838"] = struct.unpack_from("<H", blk, 0x838)[0]
            mon["c32"] = struct.unpack_from("<H", blk, 0xC32)[0]
            mon["a94"] = struct.unpack_from("<H", blk, 0xA94)[0]
            if (a6a & 0xA00) and 0 < b46 <= 0x800 and cur > 0:
                # flash: +0xA6A flag (0x200 on the Azure Rathalos; 0x800 seen earlier; Kamu's sleep also sets 0x200 but leaves
                # +0xB46 at 0) and +0xB46 = frames left (900 = 30 s measured), counting down 1 per frame
                mon["flash"] = b46 / 30.0
            if self.settings is None or self.settings.diag_fields:
                mon["raw"] = bytes(blk[0x820:need])
            vals = []
            for kind, off in DBG_FIELDS:
                vals.append(blk[off] if kind == "b" else struct.unpack_from("<H" if kind == "h" else "<I", blk, off)[0])
            mon["dbg"] = tuple(vals)
        return mon

    def _duration(self, mid, name):
        """Length in seconds of sleep / paralysis / enrage if known: ini value, per-monster override or measured."""
        s = self.settings
        if s is not None:
            ov = status_overrides(s).get((mid, name))
            if ov:
                return float(ov)
            v = getattr(s, "st_%s_seconds" % name, 0)
            if v:
                return float(v)
        return None

    def _track_trap(self, mons):
        """Hold timers read from the monster data (frames, 30 = 1 s). Sets on each monster dict:
        m["hold"] = (kind, seconds left) for kind Stun / Para / Shock (+0x8 counter jumps UP to 300 / 299 / 240 and counts
        down 1 per frame; Stun and Para are told apart by the para flag bit 0x08 at the jump), also Sleep (900);
        m["trap"] = ("Pitfall", left): +0xC32 jumps from 0 to the hold length (600 = 20 s, 360 = 12 s on an enraged
        monster) and counts down 1 per frame;
        m["trip"] = left for monsters/animation states listed in TRIP_STATES (measured), else m["trip_up"] = seconds since
        the +0x8 count-up (0..120) marked the trip.
        Every change of +0x838 is logged with the time spent in the previous state, to learn more states."""
        hold = self.__dict__.setdefault("_hold", {})
        pv = self.__dict__.setdefault("_h8_prev", {})
        cand = self.__dict__.setdefault("_h8_cand", {})
        trip = self.__dict__.setdefault("_trip", {})
        pit = self.__dict__.setdefault("_pit", {})
        jp = self.__dict__.setdefault("_h8_jump", {})
        s838 = self.__dict__.setdefault("_s838", {})
        now = time.monotonic()
        seen = set()
        for m in mons:
            k = (m["slot"], m["id"])
            v = m.get("h8")
            if v is None:
                continue
            seen.add(k)
            prev, pv[k] = pv.get(k, 0), v
            alive = m.get("cur", 1) > 0
            a838 = m.get("h838")
            # --- log every animation-state change with the duration of the previous one
            o = s838.get(k)
            if a838 is not None and (o is None or o[0] != a838):
                if o is not None:
                    log.info("STATE838 slot %d id %d: 0x%X -> 0x%X (0x%X lasted %.1f s) t=%s +8=%d flags=0x%02X", k[0], k[1],
                             o[0], a838, o[0], now - o[1], self._trace_time(), v, m.get("f893") or 0)
                s838[k] = (a838, now)
            fl_ = self.__dict__.setdefault("_flash_on", {})
            if m.get("flash") is not None and k not in fl_:
                fl_[k] = m["flash"]
                log.info("FLASH START slot %d id %d length %.1f s (counter %d) t=%s", k[0], k[1], m["flash"],
                         int(m["flash"] * 30), self._trace_time())
            elif m.get("flash") is None and k in fl_:
                log.info("FLASH END slot %d id %d (started at %.1f s) t=%s", k[0], k[1], fl_.pop(k), self._trace_time())
            if not alive:
                hold.pop(k, None); trip.pop(k, None); pit.pop(k, None); jp.pop(k, None)
                continue
            # --- hold timers from +0x8
            h = hold.get(k)
            if h is not None and (v == 120 or (v == 0 and h["kind"] == "Sleep")) and h.get("last", h["v0"]) > 170 and now - h["t0"] < 3.0:
                pv[k] = prev                         # the sleep start flickers the counter to 120 (trip count-up cap) or 0 for a frame (Shogun: 900 -> 0 -> 900)
                v = prev
            if h is not None:
                h["last"] = v
                idle = now - h["chg"]
                # the counter holds its start value for about 1-4 s (lead-in) before it starts to fall: only end when it is
                # idle for long at the start value, idle for 0.6 s once falling, or the animation state changed and it never moved
                stale = v == prev and (idle > 0.6 if v != h["v0"] else (idle > 5.0 or (idle > 0.5 and a838 != h["a838"] and h["kind"] != "Sleep")))
                if h["kind"] == "Sleep":
                    if m.get("f893") is not None and m["f893"] & FLAG_SLEEP:
                        h["flag_seen"] = True
                    elif h.get("flag_seen"):
                        h["woke"] = h.get("woke") or now
                        stale = stale or now - h["woke"] > 0.5      # woken up by a hit: the counter is no longer meaningful
                if v == 0 or v > prev or stale:
                    log.info("HOLD END %s slot %d id %d counter=%d (started at %d) t=%s", h["kind"], k[0], k[1], v, h["v0"],
                             self._trace_time())
                    hold.pop(k)
                    h = None
                else:
                    if v != prev:
                        h["chg"] = now
                    m["hold"] = (h["kind"], v / 30.0)
            # a hold starts when the counter jumps UP (from 0 / 65535 / a trip count-up) to the hold length in frames; the
            # length differs per monster and kind (stun 300, para 299 or 449, sleep 600 or 900, shock 240 or 299), so it is
            # read from the jump. The kind comes from the game's own flags (para 0x08, stun 0x10, asleep 0x02) or, for a
            # trap, from the animation state (+0x838 low byte 0x04). Moves that merely load the counter with 300 (Kamu
            # Orugaron state 0xF01) have none of these and are ignored.
            base = 0 if prev == 0xFFFF else prev
            if h is None and v > base + 50 and 60 <= v <= 1200 and v != 0xFFFF:
                jp[k] = {"v": v, "t": now, "a838": a838}
            jc = jp.get(k)
            if h is None and jc is not None:
                if now - jc["t"] > 1.2:
                    jp.pop(k)
                    log.info("COUNTER JUMP not a hold slot %d id %d: +0x8 -> %d (%.1f s) state 0x%X flags=0x%02X t=%s", k[0], k[1],
                             jc["v"], jc["v"] / 30.0, jc["a838"] or 0, m.get("f893") or 0, self._trace_time())
                else:
                    fl = m.get("f893") or 0
                    kind = None
                    if fl & FLAG_PARA and jc["v"] <= 700:
                        kind = "Para"
                    elif fl & 0x10 and jc["v"] <= 700:
                        kind = "Stun"
                    elif fl & FLAG_SLEEP:
                        kind = "Sleep"
                    elif ((jc["a838"] or 0) & 0xFF) == 0x04 and not (m.get("c32") or 0) > 0 and jc["v"] >= 200:
                        kind = "Shock"
                    if kind:
                        jp.pop(k)
                        v0 = jc["v"]
                        hold[k] = {"kind": kind, "chg": now, "t0": now, "v0": v0, "a838": a838}
                        m["hold"] = (kind, v / 30.0)
                        log.info("HOLD START %s slot %d id %d counter=%d (%.1f s, jumped to %d) state 0x%X flags=0x%02X t=%s",
                                 kind, k[0], k[1], v, v / 30.0, v0, a838 or 0, fl, self._trace_time())
            # --- trip: a known (monster, animation state) has a measured length; otherwise the +0x8 count-up (0..120, starts
            # about 2 s after the fall) marks a trip and the timer counts up until the animation state changes
            ent = s838.get(k)
            tl = TRIP_STATES.get((k[1], a838))
            if tl is not None and ent is not None:
                left = tl - (now - ent[1])
                if left > 0:
                    m["trip"] = left
                trip.pop(k, None)
            else:
                ramp = prev == 0 and 0 < v <= 60
                if ramp:
                    cand[k] = v
                elif k in cand and prev > 0 and v > prev and v <= 125:
                    cand.pop(k)
                    ent0 = s838.get(k)
                    trip[k] = (now - v / 30.0, a838, max(0.0, (now - v / 30.0) - ent0[1]) if ent0 and ent0[0] == a838 else 0.0)
                    log.info("TRIP START (unknown length) slot %d id %d state 0x%X counter=%d t=%s", k[0], k[1], a838 or 0, v,
                             self._trace_time())
                elif v == 0 or v < prev:
                    cand.pop(k, None)
                tp = trip.get(k)
                if tp is not None:
                    if a838 != tp[1] or now - tp[0] > 30.0:
                        log.info("TRIP END slot %d id %d after %.1f s t=%s", k[0], k[1], now - tp[0], self._trace_time())
                        trip.pop(k)
                    else:
                        # no counter holds the trip length, but the count-up (+0x8, 0 -> 120 = 4 s) always starts a fixed
                        # time after the fall and the trip ends ~1.7 s after it stops (Yian Garuga: starts at once, 5.6 s;
                        # Rathian: starts 2 s late, 8.0 s), so the end is estimated from when the count-up began
                        ent1 = s838.get(k)
                        total = tp[2] + 4.0 + TRIP_TAIL
                        if ent1 is not None and ent1[0] == tp[1]:
                            left = total - (now - ent1[1])
                            if left > 0:
                                m["trip"] = left
                            m["trip_est"] = True
                        else:
                            m["trip_up"] = now - tp[0]
            # --- pitfall: +0xC32 jumps from 0 to the hold length in frames (600 = 20 s normally, 360 = 12 s when the monster
            # was enraged) and counts down 1 per frame after a ~1 s lead-in; the length differs per situation, so read it
            c32 = m.get("c32")
            if c32 is not None:
                pt = pit.get(k)
                pprev = self._c32_prev.get(k, 0) if hasattr(self, "_c32_prev") else 0
                self.__dict__.setdefault("_c32_prev", {})[k] = c32
                if pt is not None:
                    idle = now - pt["chg"]
                    if c32 == 0 or c32 > pprev or (c32 == pprev and idle > (5.0 if c32 == pt["v0"] else 0.6)):
                        log.info("TRAP END Pitfall slot %d id %d counter=%d (started at %d = %.1f s) t=%s", k[0], k[1], c32,
                                 pt["v0"], pt["v0"] / 30.0, self._trace_time())
                        pit.pop(k)
                        pt = None
                    elif c32 != pprev:
                        pt["chg"] = now
                if pt is None and c32 > pprev + 50 and 60 <= c32 <= 3000 and (a838 or 0) & 0xFF == 0x04:
                    pt = pit[k] = {"v0": c32, "chg": now}
                    log.info("TRAP START Pitfall slot %d id %d counter=%d (%.1f s) state 0x%X t=%s", k[0], k[1], c32,
                             c32 / 30.0, a838 or 0, self._trace_time())
                if pt is not None:
                    m["trap"] = ("Pitfall", c32 / 30.0)
        for d in (hold, trip, pit, cand, jp):
            for k in list(d):
                if k not in seen:
                    d.pop(k, None)

    def _track_active(self, mons, now):
        """Sleep / paralysis / enrage: time since the state began (flag set by the game, build-up at its threshold, or
        attack multiplier raised). Counts down when the length is known (ini or measured earlier), up otherwise.
        Never runs on a dead monster. Also logs changing fields (finding new timers)."""
        for m in mons:
            f = m.get("f893")
            if f is None:
                continue
            sts = m.get("st") or {}
            alive = m["cur"] > 0
            states = {}
            for name, bit in (("sleep", FLAG_SLEEP), ("para", FLAG_PARA)):
                cur_thr = sts.get(name)
                states[name] = bool(f & bit) or bool(cur_thr and cur_thr[1] > 0 and cur_thr[0] >= cur_thr[1])
            hold = m.get("hold")
            if m.get("h8") is not None:        # exact hold countdown available: Stun / para last exactly as long as it
                states["para"] = bool(hold and hold[0] == "Para")
                states["stun"] = bool(hold and hold[0] == "Stun")
                if hold and hold[0] in ("Para", "Stun"):
                    states["sleep"] = False    # sleep flag already set but the sleep only begins when the stun ends
            else:
                states["stun"] = False
            atk = m.get("atk")
            if atk:
                bk = (m["slot"], m["id"])
                base = min(self._atk_base.get(bk, atk), atk)
                self._atk_base[bk] = base
                states["enrage"] = atk >= base * 1.15
            else:
                states["enrage"] = False
            act = {}
            for name, on in states.items():
                on = on and alive
                k = (m["slot"], m["id"], name)
                st = self._act.get(k)
                if on:
                    if st is None:
                        st = self._act[k] = [now, now]
                        log.info("State START t=%s slot %d id %d: %s (flags 0x%02X atk %s)", self._trace_time(), m["slot"], m["id"],
                                 name, f, atk)
                    st[1] = now
                elif st is not None and now - st[1] > 0.4:
                    dur = st[1] - st[0]
                    log.info("State END t=%s slot %d id %d: %s after %.1f s%s", self._trace_time(), m["slot"], m["id"], name, dur,
                             "" if alive else " (monster dead)")
                    del self._act[k]
                    st = None
                if st is not None and (on or name not in ("stun", "para")):
                    el = now - st[0]
                    exp = self._duration(m["id"], name)
                    if hold and hold[0].lower() == name:
                        exp = el + hold[1]            # real countdown in the monster data (+0x8)
                    elif name == "enrage" and m.get("a94") and 0 < m["a94"] <= 6000:
                        exp = el + m["a94"] / 30.0   # enrage countdown at +0xA94 (2400 frames = 80 s)
                    act[name] = (el, exp)
            m["act"] = act
            raw = m.get("raw")
            if raw is not None:
                old = self._raw.get(m["slot"])
                self._raw[m["slot"]] = raw
                if old is not None and old != raw and len(old) == len(raw) and now - self._diff_t >= 0.25:
                    ch = []
                    vol = self._volatile.setdefault(m["slot"], {})
                    for i in range(0, len(raw) - 1, 2):
                        if raw[i:i + 2] != old[i:i + 2]:
                            off = 0x820 + i
                            vol[off] = vol.get(off, 0) + 1
                            if vol[off] <= 120:
                                ch.append("%X:%X>%X" % (off, int.from_bytes(old[i:i + 2], "little"),
                                                       int.from_bytes(raw[i:i + 2], "little")))
                    if ch:
                        self._diff_t = now
                        log.info("Fields slot %d id %d: %s", m["slot"], m["id"], " ".join(ch[:24]))

    def _log_timer_events(self, mons, now):
        """Always logged (not only in the trace file): the moment a status build-up reaches its threshold, with the hunt
        clock, how far over the threshold it went (270/250) and when it falls back below it. A status whose build-up
        stays below the threshold (250/260) does not start its timer and is not logged."""
        el = self._trace_time()
        for m in mons:
            key = (m["slot"], m["id"])
            for name, (cur, thr) in (m.get("st") or {}).items():
                on = thr > 0 and cur >= thr
                k = (key, name)
                st = self._tev.get(k)
                if on and st is None:
                    self._tev[k] = (now, el)
                    log.info("TIMER START t=%s slot %d id %d %s: %d/%d (over by %d) hp=%d", el, m["slot"], m["id"], name,
                             cur, thr, cur - thr, m["cur"])
                elif st is not None and (not on or m["cur"] <= 0):
                    log.info("TIMER END t=%s (started t=%s) slot %d id %d %s after %.1f s, build-up now %d/%d%s", el, st[1],
                             m["slot"], m["id"], name, now - st[0], cur, thr, "" if m["cur"] > 0 else " (monster dead)")
                    del self._tev[k]

    def _trace_time(self):
        q = self.timer
        if q and q[0] and q[1] is not None:
            return "%.1f" % ((q[0] - q[1]) / 30.0)
        return "-"

    def _trace_snap(self, m, why):
        full = m.get("full")
        if not full:
            return
        words = struct.unpack("<%dH" % (STRIDE // 2), full)
        trace.info("SNAP %s t=%s slot %d id %d", why, self._trace_time(), m["slot"], m["id"])
        for off in range(0, STRIDE, 64):
            trace.info("S %03X %s", off, " ".join("%04X" % w for w in words[off // 2: off // 2 + 32]))

    def _trace_monsters(self, mons, now):
        """Everything that can help to find a timer: every changed 16-bit word of every monster struct (with the
        seconds since the quest started), status build-ups with their thresholds, the moment each threshold is hit
        and how many times, and a full dump now and then."""
        el = self._trace_time()
        for m in mons:
            key = (m["slot"], m["id"])
            sts = m.get("st") or {}
            f = m.get("f893")
            for name, (cur, thr) in sts.items():
                prev = self._tr_st.get((key, name))
                if prev != (cur, thr):
                    trace.info("BUILD t=%s slot %d id %d %s %d/%d (was %s) flags=%s hp=%d", el, m["slot"], m["id"], name,
                               cur, thr, "%d/%d" % prev if prev else "-", "%02X" % f if f is not None else "-", m["cur"])
                    was_hit = bool(prev and prev[1] > 0 and prev[0] >= prev[1])
                    if thr > 0 and cur >= thr and not was_hit:
                        n = self._hits.get((key, name), 0) + 1
                        self._hits[(key, name)] = n
                        trace.info("HIT t=%s slot %d id %d %s #%d (%d/%d)", el, m["slot"], m["id"], name, n, cur, thr)
                    self._tr_st[(key, name)] = (cur, thr)
            if f is not None and self._tr_f.get(key) != f:
                trace.info("FLAGS t=%s slot %d id %d f893 %s>%s", el, m["slot"], m["id"],
                           "%02X" % self._tr_f[key] if key in self._tr_f else "-", "%02X" % f)
                self._tr_f[key] = f
            full = m.get("full")
            if not full:
                continue
            old = self._tr_full.get(key)
            self._tr_full[key] = full
            if old is None:
                self._trace_snap(m, "first-seen")
                self._tr_snap_t[key] = now
                continue
            if old != full and len(old) == len(full):
                wo = struct.unpack("<%dH" % (STRIDE // 2), old)
                wn = struct.unpack("<%dH" % (STRIDE // 2), full)
                ch = ["%X:%X>%X" % (2 * i, a, b) for i, (a, b) in enumerate(zip(wo, wn)) if a != b]
                trace.info("D t=%s slot %d id %d n=%d %s", el, m["slot"], m["id"], len(ch), " ".join(ch[:800]))
            if now - self._tr_snap_t.get(key, now) > 30.0:
                self._tr_snap_t[key] = now
                self._trace_snap(m, "periodic")

    # ---- hit-zone research dump ----------------------------------------------------------------
    HZ_HDR_OFFS = (0xD4, 0xD8, 0xDC, 0xE0, 0xE4, 0xF0, 0xF4, 0xF8, 0xFC, 0x100, 0x104, 0x108, 0x10C, 0x110, 0x114, 0x118, 0x11C)

    def _hz_sig(self, h):
        """Is there a loaded monster data file at h? (header pointers at +0xD4..+0x11C all point forward inside the file, the
        way the relocation code of mhfo.dll leaves them) -> header bytes or None"""
        g = self.g
        blk = g.read(h, 0x300)
        if not blk or len(blk) < 0x300:
            return None
        good = 0
        for off in self.HZ_HDR_OFFS:
            p = struct.unpack_from("<I", blk, off)[0]
            if h + 0x100 <= p < h + 0x2000000 and p % 2 == 0:
                good += 1
        if good < 14:
            return None
        t = struct.unpack_from("<I", blk, 0xDC)[0]
        tab = g.read(t, 32)
        if not tab or len(tab) < 32:
            return None
        q = struct.unpack("<8I", tab)
        if not any(h <= x < h + 0x2000000 for x in q):
            return None
        return blk

    _HZ_RE = None

    @staticmethod
    def _hz_entry_ok(e):
        """One 0x28-byte HitzoneInfo (u16 id, u16 second_vector_indicator 0/1, u16 hzv_idx, u16 part_idx, 4 flag bytes,
        f32 scale, 2 x 3 f32 vectors; layout of Relial's viewer, confirmed on the Azure Rathalos dump)."""
        try:
            u0, sv, hz, pi = struct.unpack_from("<4H", e, 0)
            vals = struct.unpack_from("<7f", e, 12)
        except struct.error:
            return False
        for x in vals:
            if x != x or abs(x) > 1e5:
                return False
        return 0 < u0 < 256 and sv <= 1 and hz < 64 and pi < 64 and 0.0 <= vals[0] < 500.0

    def _hz_chains(self, arr, slots, run_starts, value_ats, ranges=()):
        """Pointer chains monster struct -> d1 -> d2 -> d3 that end at a hit-zone run start (exact) or within 0x400 bytes of a
        value table."""
        g = self.g
        chains = []

        def hit(v):
            if v in run_starts:
                return "run"
            for a in value_ats:
                if a - 0x400 <= v <= a + 0x400:
                    return "values"
            for lo, hi in ranges:
                if lo <= v <= hi:
                    return "struct"
            return None

        def ok(v):
            return 0x400000 <= v < 0x40000000 and v % 4 == 0

        for slot, mid in slots[:1]:
            mon = g.read(arr + slot * STRIDE, STRIDE)
            if not mon:
                continue
            seen = set()
            for i0 in range(0, STRIDE, 4):
                d1 = struct.unpack_from("<I", mon, i0)[0]
                if not ok(d1) or d1 in seen:
                    continue
                seen.add(d1)
                if hit(d1):
                    chains.append({"kind": hit(d1), "path": ["mon+0x%X" % i0], "to": d1})
                b1 = g.read(d1, 0x800)
                if not b1:
                    continue
                for i1 in range(0, len(b1) - 3, 4):
                    d2 = struct.unpack_from("<I", b1, i1)[0]
                    if not ok(d2):
                        continue
                    if hit(d2):
                        chains.append({"kind": hit(d2), "path": ["mon+0x%X" % i0, "0x%X" % d1, "+0x%X" % i1], "to": d2})
                    if len(chains) > 300:
                        return chains
                    time.sleep(0.0005)
                    b2 = g.read(d2, 0x200)
                    if not b2:
                        continue
                    for i2 in range(0, len(b2) - 3, 4):
                        d3 = struct.unpack_from("<I", b2, i2)[0]
                        if ok(d3) and hit(d3):
                            chains.append({"kind": hit(d3), "path": ["mon+0x%X" % i0, "0x%X" % d1, "+0x%X" % i1, "0x%X" % d2, "+0x%X" % i2],
                                           "to": d3})
        return chains

    def _hitzone_dump(self, arr, slots):
        """Research dump for the hit-zone tables (f5_hitzone_dump.json, small): scans all readable memory of the game for
        runs of 0x28-byte HitzoneInfo entries ended by an entry whose fields are 0xFFFF, writes them, the bytes around each
        run, every pointer to a run start (the header's table of 8 pointers) and the memory the nearby header pointers lead
        to (where the per-part values live)."""
        try:
            import re
            g = self.g
            t0 = time.time()
            if Reader._HZ_RE is None:
                one = b"[\\x01-\\xff]\\x00[\\x00\\x01]\\x00.\\x00.\\x00.{4}.{3}[\\x00\\x3c-\\x43].{24}"
                Reader._HZ_RE = re.compile(b"(?=(?:" + one + b"){4})", re.S)
            rx = Reader._HZ_RE
            # known values to look for (Shogun Ceanataur, MHFU table the user supplied; fire/water/ice/thunder/dragon bytes, in
            # both element orders): head, body, shell, legs elements
            sigs = [bytes(x) for x in ((10, 10, 20, 35, 0), (10, 10, 35, 20, 0), (5, 5, 10, 20, 0), (5, 5, 20, 10, 0),
                                       (5, 5, 10, 25, 0), (5, 5, 25, 10, 0), (5, 5, 10, 15, 0), (5, 5, 15, 10, 0))]
            head_re = re.compile(b"|".join(re.escape(x) for x in sigs[:2]), re.S)
            body_re = re.compile(b"|".join(re.escape(x) for x in sigs[2:]), re.S)
            out = {"version": VERSION, "array": arr, "monsters": [], "arrays": [], "values": [], "refs": [], "headers": [],
                   "scanned_mb": 0}
            for slot, mid in slots:
                mon = g.read(arr + slot * STRIDE, STRIDE)
                out["monsters"].append({"slot": slot, "id": mid, "struct": mon.hex() if mon else None})
            CH = 256 << 10        # small chunks + a pause: the regex holds the GIL, big chunks froze the overlay for 1-2 s
            total = 0
            for base, size in g.regions():
                if size > (256 << 20):
                    continue
                for off in range(0, size, CH):
                    n = min(CH + 0x800, size - off)
                    time.sleep(0.002)
                    buf = g.read(base + off, n)
                    if not buf:
                        continue
                    total += len(buf)
                    pos = 0
                    for m in head_re.finditer(buf):    # head element bytes, then other parts' elements within 0x100 bytes
                        i = m.start() - 4
                        if i < 0 or i >= CH and off + CH < size:
                            continue
                        win = buf[max(0, i - 0x100):i + 0x300]
                        if len(body_re.findall(win)) >= 3 and len(out["values"]) < 60:
                            lo = max(0, i - 0x200)
                            out["values"].append({"addr": base + off + lo, "at": base + off + i, "data": buf[lo:i + 0x300].hex()})
                    for m in rx.finditer(buf):
                        i = m.start()
                        if i < pos or (i >= CH and off + CH < size):
                            continue
                        cnt = 0
                        j = i
                        term = False
                        while j + 0x28 <= len(buf) and cnt < 400:
                            if buf[j:j + 2] == b"\xff\xff":
                                term = True
                                break
                            if not self._hz_entry_ok(buf[j:j + 0x28]):
                                break
                            cnt += 1
                            j += 0x28
                        pos = j + 1
                        if cnt >= 3 and term and len(out["arrays"]) < 1500:
                            out["arrays"].append({"addr": base + off + i, "count": cnt, "data": buf[i:j + 0x28].hex()})
            out["scanned_mb"] = total >> 20
            starts = {x["addr"] for x in out["arrays"]}
            if starts:
                highs = sorted({a >> 16 for a in starts})[:200]
                rp = re.compile(b".{2}(?:" + b"|".join(re.escape(struct.pack("<H", h)) for h in highs) + b")", re.S)
                for base, size in g.regions():
                    if size > (256 << 20):
                        continue
                    for off in range(0, size, CH):
                        n = min(CH + 8, size - off)
                        time.sleep(0.002)
                        buf = g.read(base + off, n)
                        if not buf:
                            continue
                        for m in rp.finditer(buf):
                            i = m.start()
                            if i % 4 == 0 and len(out["refs"]) < 3000:
                                v = struct.unpack_from("<I", buf, i)[0]
                                if v in starts:
                                    out["refs"].append({"at": base + off + i, "to": v})
            # references to the value tables (a pointer to one of its records, or to the start of its block)
            vt = [x["at"] for x in out["values"]]
            out["value_refs"] = []
            if vt:
                hv = sorted({(a - 0x400) >> 16 for a in vt} | {(a + 0x400) >> 16 for a in vt})
                rp2 = re.compile(b".{2}(?:" + b"|".join(re.escape(struct.pack("<H", h)) for h in hv) + b")", re.S)
                for base, size in g.regions():
                    if size > (256 << 20):
                        continue
                    for off in range(0, size, CH):
                        n = min(CH + 8, size - off)
                        time.sleep(0.002)
                        buf = g.read(base + off, n)
                        if not buf:
                            continue
                        for m in rp2.finditer(buf):
                            i = m.start()
                            if i % 4 or i >= CH and off + CH < size:
                                continue
                            v = struct.unpack_from("<I", buf, i)[0]
                            if any(a - 0x400 <= v <= a + 0x400 for a in vt) and len(out["value_refs"]) < 200:
                                out["value_refs"].append({"at": base + off + i, "to": v, "ctx": buf[max(0, i - 0x60):i + 0x64].hex(),
                                                          "ctx_base": base + off + max(0, i - 0x60)})
            # the per-hunt object that holds the 12-pointer groups: its address range is taken from the value references
            rng = []
            for r in sorted(out["value_refs"], key=lambda x: x["at"]):
                if rng and r["at"] - rng[-1][1] <= 0x200:
                    rng[-1][1] = r["at"]
                else:
                    rng.append([r["at"], r["at"]])
            rng = [(lo - 0x100, hi + 0x100) for lo, hi in rng if hi < 0x40000000 and hi - lo < 0x1000]
            out["struct_ranges"] = rng
            out["struct_refs"] = []
            if rng:
                hv2 = sorted({x >> 16 for lo, hi in rng for x in (lo, hi)})
                rp3 = re.compile(b".{2}(?:" + b"|".join(re.escape(struct.pack("<H", h)) for h in hv2) + b")", re.S)
                for base, size in g.regions():
                    if size > (256 << 20):
                        continue
                    for off in range(0, size, CH):
                        n = min(CH + 8, size - off)
                        time.sleep(0.002)
                        buf = g.read(base + off, n)
                        if not buf:
                            continue
                        for m in rp3.finditer(buf):
                            i = m.start()
                            if i % 4 or i >= CH and off + CH < size:
                                continue
                            v = struct.unpack_from("<I", buf, i)[0]
                            if any(lo <= v <= hi for lo, hi in rng) and len(out["struct_refs"]) < 200:
                                out["struct_refs"].append({"at": base + off + i, "to": v,
                                                           "ctx": buf[max(0, i - 0x40):i + 0x44].hex(), "ctx_base": base + off + max(0, i - 0x40)})
            try:
                out["chains"] = self._hz_chains(arr, slots, starts, vt, rng)
            except Exception:
                log.exception("Hit-zone pointer chains failed")
            # header neighbourhoods: the 8-pointer table sits at header +0xDC; dump around each cluster of references and
            # what the dwords around it point to
            refs = sorted(out["refs"], key=lambda r: r["at"])
            clusters = []
            for r in refs:
                if clusters and r["at"] - clusters[-1][-1]["at"] <= 8:
                    clusters[-1].append(r)
                else:
                    clusters.append([r])
            for cl in [c for c in clusters if len(c) >= 2][:12]:
                a0 = cl[0]["at"]
                ctx = g.read(a0 - 0x100, 0x300)
                if not ctx or len(ctx) < 0x300:
                    continue
                h = {"table_at": a0, "refs": [c["to"] for c in cl], "ctx_base": a0 - 0x100, "ctx": ctx.hex(), "blobs": {}}
                for k in range(0, 0x300, 4):
                    v = struct.unpack_from("<I", ctx, k)[0]
                    if 0x400000 <= v < 0x40000000 and v % 2 == 0 and len(h["blobs"]) < 120:
                        raw = g.read(v, 0x180)
                        if raw:
                            h["blobs"]["0x%X" % v] = raw.hex()
                out["headers"].append(h)
            path = os.path.join(os.path.dirname(self.settings.path) if self.settings else ".", "f5_hitzone_dump.json")
            with open(path, "w", encoding="utf-8") as f:
                json.dump(out, f)
            log.info("Hit-zone dump written to %s: %d run(s) found, %d value table(s), %d pointer(s) to them, %d header area(s), "
                     "%d MB scanned, %.1f s", path, len(out["arrays"]), len(out["values"]), len(out["refs"]), len(out["headers"]), out["scanned_mb"],
                     time.time() - t0)
        except Exception:
            log.exception("Hit-zone dump failed")

    def _pointer_hunt(self, addr):
        """After a counter is locked: which places in mhfo.dll's data point at (or just before) it? (the finder's
        POINTER search, done in the background so the overlay keeps reading)"""
        try:
            g = self.g
            base, size = g.mhfo
            t0 = time.time()
            lo, found = addr - 0x1000, 0
            ctx = g.read(addr - 0x20, 0x60)
            if ctx:
                trace.info("CONTEXT 0x%X: %s", addr - 0x20,
                           " ".join("%08X" % w for w in struct.unpack("<%dI" % (len(ctx) // 4), ctx[:len(ctx) // 4 * 4])))
            for off in range(0, size, 1 << 22):
                d = g.read(base + off, min(1 << 22, size - off))
                if not d:
                    continue
                words = memoryview(d[:len(d) // 4 * 4]).cast("I")
                for i, v in enumerate(words):
                    if lo <= v <= addr:
                        trace.info("POINTER mhfo+0x%X (value 0x%X) -> 0x%X (offset 0x%X)", off + 4 * i, v, addr, addr - v)
                        found += 1
                        if found >= 60:
                            break
                if found >= 60:
                    break
            trace.info("Pointer search for 0x%X done in %.1f s, %d pointers inside mhfo.dll", addr, time.time() - t0, found)
        except Exception:
            log.exception("Pointer search failed")

    def _quick_ok(self, arr, tbl, key):
        """Cheap check: does a valid monster sit in slot 0 of an array that starts at arr?"""
        g = self.g
        blk = g.read(arr, READ_LEN)
        return self._slot_monster(blk, 0, tbl, key, []) is not None

    def _parse(self, arr, tbl, key):
        """Read the whole array at arr. Returns (monsters, rejected-reasons)."""
        g = self.g
        total = STRIDE * (MAX_SLOTS - 1) + READ_LEN
        blob = g.read(arr, total)                      # one syscall for the whole array
        if blob is not None and len(blob) < total:
            blob = None
        out, rej = [], []
        need = READ_LEN
        for slot in range(MAX_SLOTS):
            if blob is not None:
                blk = blob[slot * STRIDE: slot * STRIDE + need]
            else:                                      # some page unreadable: fall back to per-slot reads
                blk = g.read(arr + slot * STRIDE, need)
            mon = self._slot_monster(blk, slot, tbl, key, rej)
            if mon is not None:
                if blob is not None and tracing():
                    full = blob[slot * STRIDE: slot * STRIDE + STRIDE]
                    if len(full) == STRIDE:
                        mon["full"] = bytes(full)
                out.append(mon)
        return out, rej

    def read_monsters(self):
        """Monster list. The game's pointers are not reliable (the primary one flips between the real array, a slot
        inside it, static buffers and 0), so the array is chosen by checking its CONTENT and then kept (sticky)."""
        g = self.g
        base = g.mhfo[0]
        key = g.u16(base + KEY_RVA)
        now = time.time()
        ptrs = [g.u32(base + rva) for rva in ARRAY_PTR_RVAS]
        tbl = g.read(base + TABLE_RVA - 2 * (MAX_SLOTS - 1), 2 * MAX_SLOTS)   # all T values at once
        if key is None or tbl is None or len(tbl) < 2 * MAX_SLOTS:
            self.why = "no key / T table (key=%s)" % (None if key is None else hex(key))
            return None
        # candidate bases: the pointers, and "pointer minus k slots" (the primary pointer often points INTO the array)
        cands = []
        mod_lo, mod_hi = base, base + g.mhfo[1]
        for p in reversed(ptrs):                       # the second pointer was the steadier one in the logs
            if p and p > 0x10000 and not (mod_lo <= p < mod_hi):     # quest arrays live on the heap, never in mhfo.dll
                cands.append(p)
        for p in list(cands):
            for k in range(1, 9):
                if p - k * STRIDE > 0x10000:
                    cands.append(p - k * STRIDE)
        seen_c = set()
        cands = [c for c in cands if not (c in seen_c or seen_c.add(c))]
        out, rej, arr, why = [], [], None, ""
        # 1. keep the array that worked, as long as it still holds valid monsters
        if self._arr and not (mod_lo <= self._arr < mod_hi):
            out, rej = self._parse(self._arr, tbl, key)
            if out:
                arr = self._arr
                # a NEW quest allocates a new array and the old one may linger: switch only when a pointer-derived
                # candidate has been valid and different for a whole second in a row
                alt = self._alt if now - self._alt_chk < 0.2 else False
                if alt is False:
                    self._alt_chk = now
                    alt = None
                    for c in cands:
                        d = c - self._arr
                        if d == 0 or (0 < d <= STRIDE * (MAX_SLOTS - 1) and d % STRIDE == 0):
                            continue                  # a slot INSIDE the current array, not another array
                        if self._quick_ok(c, tbl, key):
                            alt = c
                            break
                if alt is None:
                    self._alt, self._alt_t = None, 0.0
                elif alt != self._alt:
                    self._alt, self._alt_t = alt, now
                elif now - self._alt_t >= 1.0:
                    o2, r2 = self._parse(alt, tbl, key)
                    if o2:
                        log.info("Monster array switched 0x%X -> 0x%X (%d monster(s) there)", self._arr, alt, len(o2))
                        out, rej, arr = o2, r2, alt
                        self._hpmax = {}
                        self._alt, self._alt_t = None, 0.0
            # the pointer may have shown us slot k of the array while we were reading from it as slot 0: when a start
            # lower by whole slots holds the same monster at the matching slot, that is the real array
            if arr is not None and now - self._low_chk >= 0.2 and out:
                self._low_chk = now
                for k in range(1, 9):
                    b = arr - k * STRIDE
                    if b > 0x10000 and self._quick_ok(b, tbl, key):
                        o3, r3 = self._parse(b, tbl, key)
                        if any(x["slot"] == k and x["id"] == out[0]["id"] for x in o3):
                            log.info("Monster array start corrected 0x%X -> 0x%X (was reading slot %d as slot 0)", arr, b, k)
                            out, rej, arr = o3, r3, b
                            break
        # 2. otherwise take the first candidate that holds a valid monster
        if arr is None:
            for c in cands:
                if c == self._arr or not self._quick_ok(c, tbl, key):
                    continue
                o2, r2 = self._parse(c, tbl, key)
                if o2:
                    for k in range(1, 9):             # c may be slot k of the real array
                        b = c - k * STRIDE
                        if b > 0x10000 and self._quick_ok(b, tbl, key):
                            o3, r3 = self._parse(b, tbl, key)
                            if any(x["slot"] == k and x["id"] == o2[0]["id"] for x in o3):
                                o2, r2, c = o3, r3, b
                                break
                    out, rej, arr = o2, r2, c
                    self._hpmax = {}
                    self._tr_st, self._tr_f, self._tr_full, self._hits = {}, {}, {}, {}
                    self._tev = {}
                    trace.info("=== QUEST / ARRAY START 0x%X, %d monster(s), timer %s", c, len(o2), self.timer)
                    log.info("Monster array found at 0x%X (pointers %s), %d monster(s)", c,
                             [hex(x) if x else x for x in ptrs], len(o2))
                    break
        if arr is None:
            self.why = "no array holds a valid monster: pointers=%s remembered=%s rejected: %s" % (
                [hex(x) if x else x for x in ptrs], hex(self._arr) if self._arr else None, "; ".join(rej[:4]) or "none")
            return None
        self._arr = arr
        if self.settings is not None and getattr(self.settings, "hz_dump", False) and self._hz_arr != arr:
            self._hz_arr = arr
            lst = [(m["slot"], m["id"]) for m in out]
            threading.Thread(target=self._hitzone_dump, args=(arr, lst), daemon=True, name="hzdump").start()
        # an entry is trusted after it looked valid in two reads in a row (filters one-off garbage)
        seen = {}
        stable = []
        for m in out:
            k = (m["slot"], m["id"])
            seen[k] = self._seen.get(k, 0) + 1
            if seen[k] >= 2:
                stable.append(m)
        self._seen = seen
        self._track_trap(stable)
        self._track_active(stable, now)
        self._log_timer_events(stable, now)
        if tracing():
            self._trace_monsters(stable, now)
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
            if self.settings is None or self.settings.show_messages:
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
                self.retcd = self.read_return(self.timer)
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
     "st": {"poison": (120, 300), "para": (0, 200), "stun": (70, 150), "sleep": (45, 220), "tranq": (0, 100)},
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
        if pct == getattr(self, "_alpha_set", None):
            return                       # re-setting -alpha on a colour-keyed layered window can flash it black for a frame
        self._alpha_set = pct
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

    def icon(self, name, size, strip=False):
        """PhotoImage of a built in icon in the nearest built in size (kept, or Tk drops the picture). strip = remove the
        near-black outline pixels."""
        sz = min(ICON_SIZES, key=lambda v: abs(v - size))
        key = (name, sz, bool(strip))
        if key not in self._icons:
            try:
                data = "".join(ICON_DATA[name][sz])
                if strip:
                    data = strip_outline_png(data)
                self._icons[key] = self.tk.PhotoImage(master=self.root, data=data, format="png")
            except Exception:
                log.exception("Could not load icon %s", name)
                self._icons[key] = None
        return self._icons[key], sz

    def _draw_lvdew(self, entries):
        s, cv = self.s, self.cv
        font = self.font(s.lv_font_family, s.lv_text_size, s.lv_font_weight, "pt")
        lh0 = font.metrics("linespace")
        isz = s.lv_icon_size or lh0
        isz = min(ICON_SIZES, key=lambda v: abs(v - isz))       # also used by the status icons
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
                icons_ = e.get("st_icons") or []
                for grp in layout_lines(e, s):
                    st_segs = []
                    for i_ in grp:
                        txt, c = e["status"][i_]
                        ic_ = icons_[i_] if i_ < len(icons_) else None
                        if ic_:
                            st_segs.append(("\0!" + ic_, None))
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
                        strip = text.startswith("\0!") and not s.icon_outline
                        img, _sz = self.icon(text[2:] if text.startswith("\0!") else text[1:], isz, strip)
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
            boxes.append(({"capture": False}, self.timer_str if self.timer_str.startswith("Return") else "Time " + self.timer_str, True))
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

    def hide(self, why=""):
        if self.visible:
            self.root.withdraw()
            self.visible = False
            extra = ""
            if self.reader is not None:
                extra = " | reader: %s | %s" % (self.reader.snapshot()["status"], self.reader.why)
            log.info("Overlay hidden: %s%s", why or "?", extra)

    def _tick_body(self):
        s = self.s
        if not s.enabled or (s.hide_on_screenshot and time.time() < self.hide_until):
            self.hide("disabled / screenshot")
            return
        if not s.preview and self.reader is not None and self.reader.g.h:
            kind = self.reader.g.foreground_kind()
            if kind != self._last_fg:
                log.info("Foreground window: %s", kind)
                self._last_fg = kind
            # never draw over the menu console / terminal; with only_when_focused also hide for every other app
            if kind == "console" or (s.only_when_focused and kind != "game"):
                self.hide("foreground window is %s" % kind)
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
        retcd = None if (s.preview or self.reader is None or not s.show_return_timer) else self.reader.retcd
        if retcd is not None:
            sec = max(0, retcd) // 30
            tt = timer_text(DEMO_TIMER if (s.preview or self.reader is None) else self.reader.timer, s)
            self.timer_str = (f"{tt}   " if tt else "") + f"Return {sec // 60}:{sec % 60:02d}"
        else:
            self.timer_str = timer_text(DEMO_TIMER if (s.preview or self.reader is None) else self.reader.timer, s)
        in_quest = True if (s.preview or self.reader is None) else bool(snap["monsters"])
        timer_only = bool(s.timer_always and self.timer_str and not entries and in_quest)
        show = bool(entries) or retcd is not None or timer_only
        origin = self._origin() if show else None
        if origin != self._last_origin:
            log.info("Game window origin: %r", origin)
            self._last_origin = origin
        if not show or origin is None:
            if origin is None and show:
                self.hide("game window not found / minimised")
            else:
                why = "no monsters to show (reader status %s, %d raw, %d after quest filter)" % (
                    status, len(snap["monsters"]) if not (s.preview or self.reader is None) else 0, len(state))
                self.hide(why)
            return
        sig = (s.version, self.timer_str, tuple((e["name"], e["cur"], e["max"], e["size"], e["crown"], e["capture"], e["dead"], e["hot"],
                                 e["pct"], tuple(e["status"]), e["mult"], e["eff"]) for e in entries))
        if sig != self._sig:
            self._sig = sig
            self.apply_opacity()
            new_size = self.draw(entries)
            self._size = new_size
            self.cv.config(width=max(1, new_size[0]), height=max(1, new_size[1]))
            self._geo_dirty = True
        W, H = self._size
        ox, oy, gw, gh = origin
        fx, fy = effective_xy(s)
        px = ox + int((gw - W) * fx / 100) + s.fix_x
        py = oy + int((gh - H) * fy / 100) + s.fix_y
        geo = f"{max(1, W)}x{max(1, H)}+{px}+{py}"
        if geo != self._geo:
            self._geo = geo
            self.root.geometry(geo)
            self.root.update_idletasks()       # canvas size and window size change together, not on separate frames
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
        _T("ms", "show_messages", "status messages in the console (waiting for mhf.exe, game running, ok ...)"),
        _T("ad", "run_as_admin", "ask for Administrator when started (next start)"),
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
PAGES.append(("Status build-ups (poison, para, sleep, Stun, tranq), multipliers and quest timer", [
    _C("st", "status_mode", "show status build-ups: off / active (only above 0) / all", ["off", "active", "all"]),

    _T("sp", "st_poison", "poison"),
    _T("sa", "st_para", "paralysis"),
    _T("ss", "st_sleep", "sleep"),
    _T("sk", "st_stun", "Stun"),
    _T("sq", "st_tranq", "tranquilizer"),
    _T("sm", "show_multipliers", "attack / defense multiplier"),
    _T("se", "show_effective_hp", "effective HP (HP / defense multiplier)"),
    _T("sv", "status_invalid", "in mode all: also show statuses the monster does not have, as '-'"),
]))
PAGES.append(("Status display: build-up %, icons", [
    _T("sb", "show_parts", "break-part health numbers (experimental)"),
    _T("sl", "st_percent", "build-up % (Para 100/200 (50%))"),
    _T("sd", "st_names", "status names (off = icons and numbers only)"),
    _T("so", "icon_outline", "black outline of the status icons"),
    {"key": "ls", "type": "text", "attr": "status_layout", "label": "status lines, e.g. poison,para|stun,enrage,trip|parts"},
    _C("sj", "st_percent_hide", "hide the % at: none / zero (0%) / full (100%) / both", ["none", "zero", "full", "both"]),
    _C("si", "status_icons", "status icons: off / always / active (only while running)", ["off", "always", "active"]),
]))
PAGES.append(("Status timers: sleep, para, Stun, enrage", [
    _T("sz", "st_active_timers", "active state timers (sleep / para): master switch"),
    _T("sw", "st_sleep_timer", "sleep timer (time since it fell asleep)"),
    _T("sy", "st_para_timer", "paralysis timer (time since it was paralysed)"),
    _T("sn", "st_enrage_timer", "enrage timer"),
    _T("sg", "st_stun_timer", "Stun timer"),
]))
PAGES.append(("Flash, trap timers and capture state", [
    _T("sf", "st_flash_timer", "flash timer"),
    _T("sx", "st_pitfall_timer", "pitfall timer"),
    _T("su", "st_shock_timer", "shock trap timer"),
    _T("sr", "st_trip_timer", "trip timer (monster stumbles / is held about 16.5 s)"),
    _T("cq", "capture_needs_tranq", "capture state only for monsters that can be tranquilized"),
    _T("cu", "capture_skip_untrappable", "no capture state for monsters that cannot be trapped (elder dragons, list in ini)"),
]))
PAGES.append(("Logs (la = all logs on / off at once)", [
    {"key": "la", "type": "logall", "attr": "", "label": "ALL logs on / off at once (normal log + trace file + hit-zone research dump)"},
    _T("i", "log", "normal log file (f5_overlay.log): small, enough for most problems"),
    _T("tr", "trace_log", "trace file with EVERY monster field change (f5_overlay_trace.log, big: ~10 MB per hunt)"),
    _T("df", "diag_fields", "read the extra monster fields the trace file needs (only used while the trace is on)"),
    _C("lv", "log_level", "log detail: info / debug (debug also logs every change of the monster list)", ["info", "debug"]),
    _T("hz", "hz_dump", "hit-zone research dump at quest start (f5_hitzone_dump.json, one time, scans the game memory ~40 s)"),
]))
PAGES.append(("Quest timers and window", [
    _T("tm", "show_timer", "quest timer"),
    _C("td", "timer_direction", "quest timer counts up (elapsed) / down (time left)", ["up", "down"]),
    _T("cm", "show_return_timer", "return countdown after the quest"),
    _T("tw", "timer_always", "show the timer even when no monster is present (quests only)"),
    _C("ot", "stack_mode", "overlay stacking: game = only above the game window / always = on top of all", ["game", "always"]),
]))
SPECS = {c["key"]: c for _title, cmds in PAGES for c in cmds}


def _value(s, c):
    t = c["type"]
    if t == "toggle":
        return "ON" if getattr(s, c["attr"]) else "off"
    if t == "action":
        return ""
    if t == "logall":
        n = int(bool(s.log)) + int(bool(s.log and s.trace_log)) + int(bool(s.hz_dump))
        return "ON" if n == 3 else ("off" if n == 0 else "partly")
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
    lines += ["", f"  version {VERSION}", "  Type a key and press Enter. Numbers / colours: key + value, e.g.  f 12   tc gold   op 80",
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


def status_line(reader, s=None):
    if s is not None and not s.show_messages:
        return ""
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
    if s.log and s.trace_log:
        tpath = s.trace_file if os.path.isabs(s.trace_file) else os.path.join(base_dir, s.trace_file)
        if not enable_trace(tpath):
            s.trace_log = False
    else:
        disable_trace()


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
    if t == "logall":
        on = not (s.log or s.trace_log or s.hz_dump)    # anything on -> everything off, otherwise everything on
        s.log, s.trace_log, s.hz_dump = on, on, on
        _apply_log(s, base_dir)
        s.version += 1
        return "all logs " + ("ON" if on else "off")
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
        if c["attr"] in ("log", "trace_log"):
            _apply_log(s, base_dir)
        if c["attr"] == "autostart" and s.autostart:
            miss = [n for n, v in (("game path (ge)", s.game_exe), ("login name (gu)", s.username)) if not v]
            if miss:
                s.warn = "Autostart is on, still missing: " + ", ".join(miss)
    elif t == "cycle":
        cur, vals = getattr(s, c["attr"]), c["vals"]
        setattr(s, c["attr"], vals[(vals.index(cur) + 1) % len(vals)] if cur in vals else vals[0])
        if c["attr"] == "log_level":
            _apply_log(s, base_dir)
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
        print(menu_text(s, ui["page"], status_line(reader, s)))
    except UnicodeEncodeError:                         # console cannot show the block characters
        print(menu_text(s, ui["page"], status_line(reader, s)).replace(paint(BANNER, "cyan"), BANNER_PLAIN))


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
    log.info("Starting %s; config=%s monsters=%d style=%s", VERSION, settings.path, len(table), settings.style)
    if "--demo" in args:
        settings.preview = True
        settings.run_as_admin = False
        return run_overlay(settings, table, None, here)
    if os.name != "nt":
        print("This tool reads the game's memory through the Windows API, so it only runs on Windows.")
        return
    reader = Reader(table, settings.update_time, debug="--debug" in args)
    reader.settings = settings
    reader.start()
    if settings.autostart and "--no-launch" not in args:
        res = start_game(settings)
        if settings.show_messages:
            print("[autostart]", res)
    if settings.show_messages:
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
            "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"
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
    'poison': {
        12: (
            "iVBORw0KGgoAAAANSUhEUgAAAAwAAAAMCAYAAABWdVznAAABpklEQVR42l2Su2uTYRjFf8/7fpdoiNV6afFCRbRi0VS8UKyoCGIGQVxUiuBUXIrg5iIOTu0f"
            "4GhxMBVdRBEEIZoMrbRaJQgSxCG02oKLBRFz+d73cegnRM96zm8558CquoH7WVn7LSfZBaAIbEs9oVN5erJAaVQu6GumfJmiXjdX1IjMd9G1PgX+gUbOcFS/"
            "U26V7JS+DIq6LNPNy7agwJiiFgj+hg2w9SxD/hfOPNsyzZNNFZZ1xZ70R3xIuD8idIBPswRAoyZ1k9NcMrgygODIsUZmqJo27e3ACWAOaAJChkxflrg+yR39"
            "TMV95IVelILukB69ZM7psDmowFugXzoqOA68G+awy8s+N8BOP8dj/ULZL1BpjdsbasXMnqIvAyApFAGHgPlHTPiqPHfjG27rw/iu1uVVcszkPXA6AFTBCtIS"
            "5H2ETXrZyA/7m9q6RawLSZpOeukWYLNJ23KKBg5nGrQ/leSNHEh2JVe/ntfCzyG3JEsyo9VGRPShcw+jqMTEu2PCxZuM6lMm9R4TOih7Fbhl/hu98wZ7gAcW"
            "WwNmgWuyapk/1cWX48gTLLsAAAAASUVORK5CYII="
        ),
        16: (
            "iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAACl0lEQVR42nWTS0hUYRSAv/PfO3OtJm0aLbOmh45KEmFWkovcZougiIjotalFm3aRaS5EahUE"
            "QRBFRIsIpAyXUmqWYbqoRS+VtEZHoYwell7HmTunxYxQlmf9n3O+c87/ARiAPBbnA1eBt0AMeAKcLaPMTzqEhcLBiQCvIhLWOnNKL5sGPWUOao4EFGhZycol"
            "mUb/FqmhxgHatkmp9kurO0yXN0h7Kkp3st266a6yQirIBUnnWvPzrSjRTQJNN2ggSK7/vYnJkDMu8eSMWadhs8xy9GHqebiSyK0xvs7MpzAJEsEIBf4i1lq/"
            "iNMXGqA1v5v+QIzvTJkyr1R9YoX7eF92IE1g/1UAEJdZoyCCj+DsckKJZWQnAlgYZkkgatwCcr+2YDwgMbd4ACtAYNEE3w5VSLFvM5vEF3ckMrWGcDJEUlwu"
            "ct0MMSI/ma5QtApwBYbnRrGSJL6ABHvldXU5EQpZ7wU1R8dlxJzkPB8Y5YjstXaa7esC4t/6gbFjgE+gY45EQoSWArcEo9Xs0MPs0xUSSpUQ1h65o4N06CAd"
            "yRHpil+z670s41eDOSqZs4iLGxekVdHuKLHxV/TPTOEW3qbJy2e1GZVPfLK/GTflWlW6JTVlftCnb1YpnbcNoIAoKoK0+7DPOTh3yymSEtbrJNP05r3lQcFT"
            "olkTfNZJq1orDVC4iN2r57apgCrqr6PejhPPMyI4OCnBRtOgGWBFNEWmqbHnfSyvkUbPwup9zUeGZNiEdSNVE+WU26VkJ/xk43htPBMgVkvt2P/UkGaaLeBh"
            "hRTrS+65Qzz13vE4NcAj74qpjfvEVoM5vpBdJiPYBqAnj5AeY7+e5oTukmoF8YBLmp5pQUMFYHPawjNAJ/ACuA/s+fPNb5ZL91MyqAoTAAAAAElFTkSuQmCC"
        ),
        20: (
            "iVBORw0KGgoAAAANSUhEUgAAABQAAAAUCAYAAACNiR0NAAADuElEQVR42oWUS2yUVRTHf+feb+abmU6nLa/WSmmlLamKL7CBVi0oopgAGhNXRmNkxRITowuM"
            "IDHqwkjixoUadONjYWKCEINSGhRB+ggPQSiFFkG0hT5sO52Z7/vuccE0VkT4b2/u757zP/f84aoMQJJktSBvAQeAfqAT+ChFfC3/SLiJDICH1wqcQNDFplHX"
            "y6P6sGnWtCQUCCzydhFmbgqNE18EDFVIWj8123J90l44Q0d4Rtqjn82X+bVeWwioxb4xs4jrSRTEQ3YY7POfydZcsy7zf2NE4lhCHDHiVNqSwovmVftdcDif"
            "Jr10golfi1D3n3afpDUdoQ88QYu26XLvPIOiEnGsZIDh2Dh5cgxGk/GX9AX1xKayZNffyEtzhN4SIL2GZpkgMAl8DlacZN+cbr6ZfZAxL0eOArdEVbqASnW4"
            "FuV1A+h1gYoKYBIkUEQEj7xcfV5FCHGERCg4ixGgYNnmiu2aa/00sygPgcI+6XEl+OQIeXDkHu4bXcyq4aVUhCnKSHGOC/YiQ3h4QQXp5lrmVhWhbibUWMpG"
            "Ben6inY9J71hFeXgHMvHbqcyX04JcX6RE2ziHS+reULCZ6/w1w8DDB0GPvDx62ZCBSBFbEmW4NtFUjPnfd0c3kaDmSIUX0S+Zjev8B4pfJ6Tp2jQhZqTrPuJ"
            "TrNLfxSUMzFiTwcEx+CqJwZwBrPB4bZ4xOY/QitN1LtBGdTP2Wnr9VY+lK3U6EKyFNRiyEgi6rD7daN7Mzbppo6XknlojLExW5yWUbS7lPjOHEGsj4HSQ/RE"
            "xzmdShBnB1t0PrXyO8MoTvIUZIhRs8Tdaau9TLhLD1Q53FmH65o20wFmnEKvIBtXs3ppAwtWWcxAG/fKXTRFw0zgbET77CN0Zk7jYTjLH6yOVlBHlQYEjxnk"
            "XyN3xUq9PeyZHCUbRLjk/TRqgNoEPj1lfZwq66errJdzyctYDEY9mqROgOrv2etdu5PT30ASiALxCck5n5hThHn5ecQin4oww6yglJAIIWRcJxUorGSlu96S"
            "K6AbuKMf6N/NIWsIUYTGyRrWXVrJ40PLyIRJksTp57x06ykR5KRBnP2f0LAdDEQenrnM6Lq4aGEtKxglZ9JRAt9ZYlgy4hVelnfp04suQWJTSHjB3iDVxOGO"
            "G0zLfo4uusJIdDeNJClRRfRPuaSbZbvtcF2exW4PCD6+UazNTJO5gnwC5FOktIlGbaRePTwFxj3Ma4A/Hbw3i3OZTpUU/pos+WeA2uLZ0STJL6aYOjTzwt/6"
            "rYK9XghotAAAAABJRU5ErkJggg=="
        ),
        24: (
            "iVBORw0KGgoAAAANSUhEUgAAABgAAAAYCAYAAADgdz34AAAEwUlEQVR42o2WWWxVVRSGv7X3Ge7tpQMdpKUMMqg1EBQl0QZwHiImGKYYiUY0MRrnJxL1RRN8"
            "4NWYEB+MSTUm6oOR4BRRHIBCJFAGo6RWRTCWSltq29t7zzl7Lx960VqhspOTk5zsff611v//a23hn2UAD7RGBA+muHZF5wFGkK6Q8EhC8j5wfMLei16m8r5D"
            "kGOAIuh0qdV6qVUEBRT41cDDk85MuWRCNOsMpsPjq+4xNyWbZINv9a0iSHBaTvp35BP/pt8Ro6jBPOHx2yrn9f8AAGYbzF6Pn/W83F96Rh+J+knMKEUUiIlp"
            "kmm6Pfy4/FS2NXLelw3m9oxsz/+VywKE2OccftV6uan8sj4ddtNnioxJgMUijFKkjyFZ7peSj8i+dAdzgqDojsp/LpiFESDDLwqw+qxuTM8wZgEJsRwp/MyB"
            "aT8iIoQYftK+YH22KpxpGtThbi1QaKpEL1MAGBRtuJLZMofmeJgSVcQcrO3hq6bD7Knr4uuao1gCyqTEroqltGVAC2RLJpX6/BkAQSO1RETWIwgBvdEQkTfk"
            "NeaPaIREHKBkOKq1kAKRx9RVOAguBGJ0vHzpcTnFMGMaEeKAZUOLqcqqiXyO9qE2rAoOB6DdnIyAwWmEhyolSio8/Ee6dvyrWfunFBdeIwvcVSyyvzPMdFfN"
            "/JFZXDE6m8asmjJlZlLPZ+Yb87Z+ZABGKV8aYq+PCC7J8APAUCUT+ZeKAsQqrPpOekr3cmOuQDX9DBNgMAoZKdUS8bp5jy36GikZKhoiXOlF2zP8GmCNwaii"
            "+yfyYgHj0R8sMu8Mw9ftkSPazlU6lxYJCckTkxN4kVfo0O3EEvCQWcvj8gD3sTq5xSxLc2LT7/mlSdG7BCkAn58DmOjk+Qbp8Gh7TN7cyQ16NYtKOUL5wHwa"
            "7/ddMksuYRvPs1ivZoQSDq8RITUS+067j0d1iwy4P43BPOXxr04Q0d+WzwcET2RkjwELJpJVIK9v8ZIsYgmnGCQkIMSSkZHhWMhMOqNOvzF7QZz3PR5dDvTZ"
            "SW0j9fi9VVS9GxDsttidIYHx6OXr5JbiJtYEJxiQPBFlk9IbnaXgY2IsvQyyVNuk2/zkvvcnGnLkDmRk39nzNb+UdDgjO+5whxSWePyKzWx0TbSEKV7GbMLH"
            "zd9yuPZH/giGmDs2Y9w9GmLxyYe6OxS026NfTNatVvgwFQFEDrdQgDk0SoaXmJhTuUH64kHqXBW/5QY5HY0QEFAmoZkGz3j7qZmqp/tzBrLIsAo6IKNpjkgz"
            "HLNKzTSWGjgbjDFnbAYzkhoSMiKEM9ovgBrMKBWLTzWEnCU87DSR7fJ1eDMr6KVIjctz9+mVnI77aSnVEamhSJE8lvf5Mh5vluaYw005lXSclGSHIL3v8EV0"
            "WLrcpdT7EUpE3jB/rJlADUVKtFLvd5l96afsF4OcKpHu+tvJUwAYBwMBJk7IbtspB5LlLM4uZ244Pg8MEZYGChwyR5OndasvaRoKslnRXRN9MNXEEyAvyBuK"
            "bojIsZHVbiXX+ipq/VnO2t3Sad7lI5NqiiAdij4CpBd7ITgXRM5gNgM/V7Kb/Jw0mCcnCEemHBTnAVGAiOgyYJ3DtTlco0UGLGGXIB+UKfdM3v8XkCP9H/Ck"
            "U1MAAAAASUVORK5CYII="
        ),
        32: (
            "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"
        ),
        48: (
            "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"
        ),
    },
    'para': {
        12: (
            "iVBORw0KGgoAAAANSUhEUgAAAAwAAAAMCAYAAABWdVznAAABiElEQVR42lWST0hUcRDHPzPvp4t48FqCdxHWgyH+eUEGHQSvWdstiA5G0N1TsCIFVpfqHEGI"
            "F+nYQepsgbBZHqVLmS2xi7uY7r73vh3eW7O5zAwz3/nOdxgAzKjGs/Zn7oodX7xgzXiao6tz1hkasm/ApDsAAQB3cGfpVsUkke1+Nq09NnVOyXZ2TMPD9h24"
            "VIA8x+TJcuWmnSQJyaePZM/WTBJJrWYaGbF6Xx8TEgZgQFQqAbD14rlLItlYN73M49MP710h8LoYfMawcu+udSXS3ZrpySNX68iyXwdodtq6/REL7kAUAXD/"
            "wZJLIt3bQ6sPXe022cEP0vIoLSiac8m5pakA0hCgNCCSDmSCLDIBvzlnvZWqd24jiXR/H61WXd2OZYeHpvFxa4bAVE80gIfQA1kikXz9YmnlRi56e9sURfbq"
            "3z7FpQrQu7ebrsXrtIGf629cl2NrhMDMfwyWh2OTZWvEU9YEns5MWKM8ZnUgLupnHFHhFwf6aQHzg4NcKwXqQHz+Nf4CxmyjuK1B4dYAAAAASUVORK5CYII="
        ),
        16: (
            "iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAACV0lEQVR42m2STYjNYRTGf+f878dcuT5mpquxoExCJitZmrAwMZMoK4qFxoIFG6wUJR9FouY2"
            "E0OmDCmmFEtrNqKIsvGVGa4Z8yG5977/x+I/19wZTr1vvW89v85zzgNJtWSzvGhstF+5BvuZimwiFfG5UGBiXo4pN5sC+qIIAJs+SZlBJs+qBXnT0AOrDn8x"
            "PX9u2r3L9eihaWQYPXvmcVubCyimUv9CXMKAY0tb0Ns3VCTC8IjFBw+Y3r1FEnGpZJV160zA9elOqIeYOwAnmpssvHxhZQl9+oT27TW9foUkVCpZec0aF3C9"
            "u5s04NRVZrq9YqHgGh31qoRGx9C2ra6REZOEJiet0rrClE6z1n2G4GaUFbHF3bounCXk87EDDPQ7nZ3GwoUAhKH7pEolTebzjEszYqKIzWDjgzdcEkFC5067"
            "Lp11SUgi9BdNwBSw1RL3jhkUCizJZuz70KBLohoCOn/GVLyciEMg7rlqMfC9eRHtf8W1a3oLlCv6O5DFTRBC8nYHT+YTR1lKczdQZ4GJW9eIJaoS6utFZ07N"
            "WCj2uICPmQyra9pZkGw2gQz0m2qQnh7TlctRDVK9eNGUy/GupYXmWYkEUnWQsYGbFkJAEpX2jVY9fsxVDUiySmurK52mzWelIKm0O0QROzGLv33130ePmIDR"
            "VJryh/de6dxuAu52dJCdGySAKEq+Tu7f4+HQQRPw1J3Hh7s97OhyAfd6kxQy10KNtmx+zsY2bTABTxoaaG/M24/1a03A7d7e/4vrASvN+AY8Xb6cRUDBoATc"
            "kYjmTv8PVGAUt9Dl1roAAAAASUVORK5CYII="
        ),
        20: (
            "iVBORw0KGgoAAAANSUhEUgAAABQAAAAUCAYAAACNiR0NAAADRElEQVR42oWUS2icVRiGn+/8M9OJjS01tzYXEyU2FaQgGjKtgWCzaLRipLG6UjcVobgQFCuE"
            "2G7UXQrBSylVvFBEUQgiBkGR2nbVghulqWhbNEZppkmMlcz8l/O6+JM4mTb6wVmcA+fhfO/7fgfAkdbTwDSwULGmnONS1dnk+vXsWrqToaoMCJwj8Z6Jnnvd"
            "wMghRX/Ok81m4cSHRmkenjvomZs1zIyjx4yTJ5OZ+nqGikVOLUHjSmgAkMnwTC6Hxo5YSSKSkITfv9/5E++alvYKQ4sHBpyAubo6+isZleUAnOMgoMMvWyQR"
            "RREKI/ToXtPYaAqNohS6e3cK3bhxpX1XDQ3MIJ/nBUCvvuLKEj6OUeLR4/tMb72eQpMkhfb3BwLm163j/rWgWYANGxgGNDoaJN6jcjltd+gR05tvmLxPXxrH"
            "Fg4+7GTwiUtRgatqO+rspGFhgd72NqedO0SSQC4Hv08bcejo6HB4D+YAKfGxENeXAWzbRh1wqqPV6eIFt2yMpn5Bu+4L9M1XThLyQosLFvX1moBiPk9vZcsG"
            "WFMTjcCZW5udLl+wciWsryfQt1+nsMSj0jXivh0mYKalkUK1fhlnYMaR29ud/rjsyhKKQjT1q6mv4PTdWbcUGXS1iO+5xzzw222b6a6OjVvlMiKOlQDCIJuD"
            "pi1i7mqFSma4rAEkSY4ia+hnjY00Aac3N6Cfz1OS8N6jv66hwT2m8Y/dSmRKixYXCk7A911dNFf6sMqUzk4agNOtW9DFSUoSiuMU+tCDTp9+FGj5bHbW4u3b"
            "TcDZTZtoW37YddOydSv1wJm2ZvTTZGqO92h2Dj22z2nic7cyhleuWNjdbTLj7eUc3nAEV6At6PwPloQhkkg+eN/+rsmbvpxwPoqQZOGTTwQeGA+CGwNXoIUC"
            "twDn2lqdJAuPH7M4E+CBuZZmk+SSZw84AaVcjj1rjd6ynm4p5D8eHnH+vXesDCib4TNg5vhRF734vPPAYk0Ne/8Lls5jKu3wXXeYXht2JUA35TlkxtjOu00H"
            "nnIhUK6tZWitj3aV23V13AxcurPdlHUIeKmvnTxQ7GozAfO1NQz+H6wyS3ngC2AOGLF/f6JxYDoX8ICtAfsHJSWyBE7mdCcAAAAASUVORK5CYII="
        ),
        24: (
            "iVBORw0KGgoAAAANSUhEUgAAABgAAAAYCAYAAADgdz34AAAEEUlEQVR42o2Wa4hVVRTHf2vt433MOHObmdQImb4koY1ajlpSX9QQrVFBCSIIEmwkcGQS+pAQ"
            "QiVKWEI4hUT4IcKUdLSglz0ktIggCIICKx9NpPlIR+veex579eGc69zRO5ML9oezzj7/314v9gEQUmsH9gIXgL/r1l/O8SNwAriU+S4C53I5dgGF7HulgQng"
            "gCQIeCBJOLp6pVam3E6hWjFyOeHMWWNwENb3QuKMJAYV5aefhSNfeXI5e3tRJ099/AvVDOKvhziApiZmi+AXL5SKmYRm+GzZc5vUP7ZKvRk2sjRZu9bFgOXz"
            "7AGK/xeJBAEbAeuaIZXz56h6j1UqqWBfn9qShWJxhFWrWBSl/nXrNMwg+24mXRQKPAvY3G6JL16QilkqaIb194s9skTMDIvjGkR8b6+LMsheoKler1E9KBbp"
            "B6z7XgmvDEtSD3mmX2zpQ2Lm0+coTiFPrglCEHPuGkQaQaiD9AG+q0v92bNqSYKFYSq6+XmxZUvEhofF4jiNJvFiG/pcpCpWLDK/pqUNojAAEVoBm3yrEAQg"
            "AmbppjumwtCQI44FVUgSUDHfs8y8UzAjN1YNNDv9S4CtWu4ii0enaMfLavNmBzZ8Wa3ef3C/VJyKiXCqWGTq9cW+lq9ini2Are5xcVwR7/2IyCtbxe6b5ezq"
            "JakXTw7tkWqQip9samJOo05SQJzjBcBW9bjYyhJ7P9KO27eJzetydvVyKl7zD+6RyAnmlJNT2rh7LHE6OrgLCB9d4SILxScRFmUnf3W72ILZamE1Fa8V+913"
            "JFHBO8fxqW3MrG8SrieZ4QDFTBJvMYLV3k66TcgXjEvna5uzvKqgTgCisnKBcUwAraXo4cVSTapE9ak4MIjdM0Ns6KSOiuLAfo2y/j/aWaJtrEm+VuR8nm2A"
            "rVgq1aRC6Os65cBBbNZ0taFTKaRSTv27d2sM2ISAjzo6aBlvkmttugWwlcskjNNIfA2y7z2x6dMCO51FUs4gAwMSgZgqH7a30zrWJNdDtgK2fKmESZSmqwY5"
            "9IHY/G7nfz81ehYGBjQUESsUWNCo4GNCVvZIXP5npB5//iHDpVaJp92pduI39bV6vL7T3RRgFKS5mRcBv6LHeTOJvz4mYXubmCpXgcqihWpm4l/boWGWok/H"
            "S1Gji6gX8LveUP/9d1IuFsSAM/kc34D4Lz9X/+YuF4FYEPBZSwsdYxX5hruhs5M24PiD96sd+0TLE5sxYKjUwhrg9OOrNXxrpysDFgR8USpxy3gXzg2nD5QN"
            "zQWxzU/rvx0lDDhTKjFHYPvkNrGNT+iVTPxwayvtNyteb0ea8mLNeTHg12LA3Mz/bUuTWKBigfL+zHEGbCyrbdwEnAN+mDCB7rp367Pfm70zJjFxPPH/AOAq"
            "bRWlQ9IZAAAAAElFTkSuQmCC"
        ),
        32: (
            "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"
        ),
        48: (
            "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"
            "MGTQZwAAAABJRU5ErkJggg=="
        ),
    },
    'sleep': {
        12: (
            "iVBORw0KGgoAAAANSUhEUgAAAAwAAAAMCAYAAABWdVznAAAB2UlEQVR42m2QPWhTYRiFn/f77r2JvblpatQ0ahs11f4sKaVIpbS6qZWiCAUdBFFB6GRFCy66"
            "6CAuInTybxEKGQQHoQ6CYhF0KOhY4yKkKIGaQpu/m3s/h6ZOPXA4yznwcIRNPegatWfsuBjjW7qxZoKgIfr3t8oTYBbQQABgAYjI4c5BZ0dkn4TNsqOOXUsH"
            "xSVf5y8u95kQkRMIHxHAAAhwALgOFE/O9oTTH4ZMe5fzAzjCNlJTeTTwYuJutjmzeDSIus4K8HZnj/3eS+nFaEJ9UY66vUmjBIx5NHE/e2tkOlmfv7JslUvV"
            "mptwot2jEd054OLuauPVucKnSskfVyY0KC2Z9XKDdw9/RTr6bd0zGXejKa1/vm4GXX0p/+vc6nql5D8VJf+xssAl4Gor8/tzXninMFQfupDygRXtcIbWXQKs"
            "OjF1Kpm1J52Y6lW2DE88zrRXGjVdN+uq96zrFT83uv2qeWmJYIzBibSpG4OX3fSeAQ8vHWd+qlCp/vW/RzwVhH5o19aCN1s4WhQAzzPD8ebNpVyl/3QyBOa2"
            "vVSEwIxhebvtkfPPDumN6oaV6GtKtEONcRzLGIR7KEBtjQRQ0Zi1sDcXqx8c8/5kxttqliMLrZK0DMA/sB+YDIx9vtwAAAAASUVORK5CYII="
        ),
        16: (
            "iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAAC2UlEQVR42m2TTWhcVRiGn++cc+/cmYn5MU2mMSQNk0mTKq3YgD9g2vrTalQ0tBYrLqS4caVF"
            "cBkKxY3iQnFhdWO6EGsFEQVRESkqVAuttqLWVmZjKKZpO8l0kpnc3Hs+FzNqib7w7c75zst7nhea6kc4F2SlHuSkFmRlKcyZpSAyNReahgi/AgOts8J1cmJA"
            "PZu6i+HoQ2/nk3rqo8Dm8EsOJ5a2Qj49tq88VinXt4jhD/UYIGWNSi5jrnQOOe0ccY1cv9Xb9/fpdGU82fPORg2ydjls42YOYgAHmLVOAAaBe4FfiuPdemh2"
            "Itk3c4taZ67aDPcZJ4gBsYKsuSoA25/eEAEfj25bp69c3ZHu/+DWxGXsRRvKUeB94DPgU+Bz4FjrweaCvvG+HPDVyES3Tv9+x8rjr230gAJl4OJdz9+oU0cK"
            "ev9LvfrYWwM6dE+7ApNNN0ZQrxM9w7mvnzs1Xq8tV4OfP5ynHiek4p0NLAtnVpM0Vpl4YT3tg3l/5IHzwezJ6k6x8qVDFWBlddnz0YHz2ZVGQtQp4JRUPMag"
            "P84suocPFunu7fLvPfGbnT1ZPVrYUjgxd3bO/B2HBZ4BbgKWWknHwCPAjr2HR/zYrk5598ky5e+v1IFp4DBQt60FKsKpIGDBeyLgMlAFJne/WerZ+myHP/vJ"
            "HIuVqhZ3RSZeYHL5cnpcDOXmnwpelc0Smh82T2XxQLxokVCoVWM9/uol531M/515wjbs3Hcp88QB0rQq0oy8PdthGZvKNlJvg9L2daiJeH3TGRtfS04AlRZA"
            "BlgEftL0Xw6kVCIDfAvooy8PJy+euy0p3t2lwBc2kCZAFozjPyAZgKiLARea2admRtND17bqtgMF7Rqy6iK50ILGtsZcj7IBRAw0Kgx1rM/0tw8H/sI31dXB"
            "nUH84Bu9q2HeloANIv8UyLdGWVPPHoHTgtRyNwTzbR3Bn8aaKnAa6Pm/KgP8BTto/gtOnSI+AAAAAElFTkSuQmCC"
        ),
        20: (
            "iVBORw0KGgoAAAANSUhEUgAAABQAAAAUCAYAAACNiR0NAAAD1ElEQVR42oWUS2zUVRTGf+fe+f9npi0z0AdMgYJYJOkDUFPAmDSiATSKC8XEdMPDxKgsjBtl"
            "oWJiggsj8bExGuJC8RE1iiSCRAgBiQQl8ZEalDYliNLadoCWdl7/ufe4YAaLIJ7k5t7FPV++c77zHQDDpdgMZIHx65xs5R9T8q4IAUQMqp6jC1cnb1v6WBBN"
            "XvCBNQFW4ri8UB4XEg2h//mj8+bUgbHjYmWZOr0WHrEpb2sxFM5JkJ9QgqSgJU9jcy3Na+LMaE35vr2TAsQE0H8IXYVcpb4JOAOcBoYBDWOhX7+zU58dWlZe"
            "uWWeE0RNzDxRyQkqt/mv8mloYFpjW3gTwrH0rKR/ct8y93J+pVuztVWBkombx01MEAvGCsYKYq7u4WXqdZm6pomhiV2ppuTt69/p8PPvqjMHXzlb2vPCyaIN"
            "5UtX0lEgA5QreQpYYDfwPiBSoavJJHPyeT5PzUx0bXi3o5DpiieOvDqse7f1C/ANQHpO0N3cFaIONLIYK1wcjfjzu9wJoL0qiojgCwW2pWYlutbvbM/XLCzH"
            "j741yoHtZySRtiqWbueU9gfS+RXPpOzY6bKtTYekFyT9qUMlPlj7W0EMqL8E6MUKvqztKzZlormrau1g35jOvSfuNt6/gDKOQqlsauoDet/IJV+bN8DN6zLc"
            "sTVN7o+YHnnxTAyhVOlapWTBo+xrurFmTV1LgHOKDQFRMOBRwpoYZ49OMq8zzUNvt+Jixn+84aT2H8r2BTVBT5SLfgIkdrm1sGVkIDc4MkAIRBWhDFAAlgOL"
            "2+9u1HVvLpBy5Pn0kX7pP5xFRL6IctFQVSS5QnIBMUL3c4114YTR2Z0Jd2z35Iy+Pdn9basb29a+3qKTF3Pm0PMj9O7JEk9ZV86rjQr+CNBdVXnqcHcC7wEt"
            "U0YjAUzb+FWbzyxPcHE4p4WJPCbpCOvVD+5z8tnGv3rFcEtVFCpKO1UeTDUHS1a9NN2pqC1esFAOkTrR73cMS2F7WXzkUVG885gYtjiiAHHVq70MoMYIQHF8"
            "yCVvWJ6mflES9Um3f8uPseJY1Av8AISVzlcHe1dFB/m3nxdV/KytKxr8U8eX6tMnb/UL76z3QN/sjkSL2EvWu3zMtRcDAEFtEAKJJfc1+Z4P56N4PukZ0P6D"
            "5wTwZ38p1KpTq46YOow6jPorl4OtAougruQ3T5+buPfRrzuKBVMMxoZyxDMlWfzwtNLvhwszo7xmRThYAXBTytZr7EMB1FkRvt0xaFQ0FySVRDrhJ0dKVj1M"
            "Uf5/o0p5CXACuACMgIwCg8B54Fdg8fXWP8DfMGCZEq3snAoAAAAASUVORK5CYII="
        ),
        24: (
            "iVBORw0KGgoAAAANSUhEUgAAABgAAAAYCAYAAADgdz34AAAE9ElEQVR42oWVb4xU1RXAf+fe99786c7OApINf1aCIisLbNsQbYKVWE2FYEysaFBLSypR2zTt"
            "p0rUDySmVvSLH6zxT0L1izbGP0RJTFOjEUkqMWaTVhOkQoFdFQTZnRl2ZnbevHfv6Yd5g8Pq4knOezf33XvePff8zjkAQkfmAfuAaWAKqMyhU8A54M1sDz02"
            "viUCBECK5VYcr196TS4uX0bOJSBqEGxnmRrEW4KCoTKeuBMHahbDnXheBizgvusHQXdgDGXvcDfsGogv2ZDac1VvbJAzocljXYSrC+JF+5YU5Nh7reTEgZqY"
            "kAU+vrgHAaAAPuEUYF/fVumn4FFVTGBABJxh8yPLGFiNBKHVfz83ZQEjxk5kB9e5fmCzjwKMA3HcdIW45g+1z2kzrvrBuOJ04x8vl5U3lSnMK6TvPDThPn7l"
            "dCRWnvVt/3SPLdOjc4sIFIZYbELZD/gtu9e4P5/8qe768lp31falKaA2J0+IdNb26nddlMzyxkURK5OUvepZ/YuHR3TdjvmSqvDPXeMzB1+YKJhQ3vKJHgQu"
            "BXzPfs2ufAJ4Bpjstd8Z5FghwiFAt+wemXnk5DX68BfrdcO9yxygYdHssxF7jBUNC0ZzfUZzJauFcqCFcqC5PqsiosCOboyDzLjSxyXSZK8Ys2rL48ONkdv7"
            "oqlTdd7/yxnG9p4ygE+a/mqgXBoMWjv+NdRKkqTkWgZXt5jAYktB+6Wbj0e1461QDKjvuGSBNHJ2Q9u7tT9/YHl77a/nFc98WZXqZy3qrWlW39GP82oUHZRQ"
            "yUd5Pvl7M9+sJ5QX5ln+s4ioHOIaeduaSi0G2+XqfB60Y9cviBvePJA22omVMGT+Oth83RCppqTqaKdeor7AnH3f8eqtxzFYtj43TLEUQhK6vXcfieJais3Z"
            "oy7u4Cs9WbhJ4B/lRXmk8E1gVBUxgthOFE1oSKqOtKn8cs8Ii9fnmKkZfXn74XT8w0psArPTp/6ZDFcvPUEOgQeBTRkdMou2BrACWJYrBvqrF0dkyU9CknrA"
            "q/cc9UcOTJqoKC+2m/og8MVF80AMiJULdPFVpQUmkMOlhTl/71ujbuexUf3d/hEdWjOgGaIeaAFV4MYu+jIrEwEGgN8DQ0CazcfAGuD6W568PF1714CdPFmX"
            "6YkWJw7WiPrBiSfAtj/YPR01K+kfRHhKtYNpr3jgUeC+qGgQKyBgrCAiePVqStivP49pVXHB/IDhrQM4nyLWUZQwHftrwzQrpBn85ykSwGfs/mhwVc795t3B"
            "mWYjKaQzAel0hBCKmWfMvu2f69eHGgJY31Z82jHUoVKK2hnF6i/EVHue7bimduz5uq1XU1taUOSKjUWCYgC+mFSOx2G74U8Dr2UE9pQK7ZaKN7O5C3pENwZ3"
            "ZV3NC9bf8eyV+qfPRvX+w+v88vUDDqjZgr2NbnGbrXM1HL4pHv9zDldamGPbC8PMH7VMf6W88dujjI/VjAin3Yz7T3Z6maMX+O68mVUNcZ6dJjDl255c2Sqv"
            "MlI9O8PYnlNMVxqy6If5xARyBfBY5v5cqrOvRQAVK6hnydLRPn/l1n6TGE9QNvrj+3/A3R8tZdvbg1paHHpgkZjzh5KL9ZfeIAteFahMHmuZv2381EYlO53P"
            "G6fGmXbbJefOJH2N06kBahklcrF2OVu63mwC/guczXQy06+y95GeTDXfZ/T/5Tge2IJpZHkAAAAASUVORK5CYII="
        ),
        32: (
            "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"
        ),
        48: (
            "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"
        ),
    },
    'stun': {
        12: (
            "iVBORw0KGgoAAAANSUhEUgAAAAwAAAAMCAYAAABWdVznAAABfklEQVR42nWRO2hUURRF1753MqOOGLHwA0owCpqAiI2fiPgB8ymCoJBOECzSaCA4ghY6lZUE"
            "xNiZPmAZK7ET+9gk2KmFGFAw0ehE33t3W8wz6EB2edgHFmtDmRggRgCeAS9ibN82Sg3oBa6OjcjXL8vADeAAUC87Wm8HMTd4Wr+Hzwe/W5C/vCeNXAgePqU8"
            "Bl4BsXwQMYJg6u64bJO5wKnAtrKHjWCJGZsArAOGkv350ych2eQ26eWcErDc089utWEiQGgCs7PE+iZlA4dJK59g+TP6tkQaHVD3h0Vu2wAUf/kB9hzpkZvj"
            "8tGDKvp65XuT0asflY71q7Vjq97s2s7Uf5YqFe4Ajy8el5cWlFa/yk649V3529fBh/ZpXupwW60CMH3/luyC4tcaqfWDfHRIa8C10LFLrbRx7tJgKGxlNsk5"
            "+f69WqnDzhD+UQVkMZI2d9G4csah2XDl7EllM48Ux4bY9lNMdCIFgGoX07XAPHATOAE82FJjMbSX5w/pSoy67YFgXAAAAABJRU5ErkJggg=="
        ),
        16: (
            "iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAACWElEQVR42m2TXWjOYRjGf/fz/N/33cReH7EyOxgHPkMmhRHlYKKlzZt8bZGSKDkSJzt0KgeG"
            "yIl8RZKUfK22UYjkhEIk5Wttycz2/t//czl437Gx6+Q5ee7rvu/rum5PEQZ4IAUolWJ+CNwGqoB2IF36AyBGgxl4D6kIgDsbljtNqiCeMI5lUQTOjVqGL71p"
            "YKvE2hDYPG6MbXrSRdL92aL2R9SGQFZiJfAF6Bk2TbEzsG/KeNOunNPmdU6Xzvggoc9vTc1NTs2NTivmm4BnuRy+RGB/CDIZVo8tp7/jog1IDEooxEgBSSTq"
            "tV+rFpnMOClhwIiFfGmKHZm0xQ/uWVyIUTyI4jzq+0GhYY0FoKulhbIR3YfBDYm3vclLoqAESejVY0sMk8FToKJU7IZIPOAMgo+Yadjh622ks9Xy75+bja+E"
            "1y/Mqsosxpj28StJWZr7EtIwMyNf3GjPmgWmG8csWb/KApimV5mWLvDqfWfh7nnLg/WVcnEbWPavlQ0V5fQBb9KYTrdaOH7E1HnLS0LKo0tnnU4dddqX83KO"
            "3f9mw4AZdXVMTkW0799uRS2EQh4lMZII6reB2jlO3rPxv3ANSVuZpQZ4eWi/JUlMMvAL5fOot9uSxQudgKv19WRGdQOIouJSp+pXe0kWSyQS4fsnK0ysMKU9"
            "OVcsjf4jMCCbpaYsZT0fOlzy8xvxuROmmxdMEvm2VisAnZdbRxzY31CVmHfOrTYd3GaqmmwCXgDvl8wzHdjiVJ5BaZg92nENyTILeAhcAxoP5CivnsRUYC/Q"
            "ZXClFCp+A7dN/e1cLBAzAAAAAElFTkSuQmCC"
        ),
        20: (
            "iVBORw0KGgoAAAANSUhEUgAAABQAAAAUCAYAAACNiR0NAAADM0lEQVR42oWUbWiVZRjHf9d9n7PjtpPnRSaOSobJyTLFhaxaa9EaFGEvhgeiIAr8kqYQBIHQ"
            "GxII9qkgiKgP69OQPqTgl4Q0DTHnp5VBJRs1K5VN0u3snOft34c9q7M4bTfcPNxwP//rf93X77pg6bJ0u/T8DjDmPdX07JrurLjcf75Dzogqa03AtVyOnqag"
            "zfeWUXQg4UYO0+k9Z/vucbp82s125Eyr2vhMwo19TNZW8LeYwm7gW+A88AOYjo44SejAHpcAATAGXABOAoOtnBpAIUfPXw0mBnsd998H9ZtGz0bTawdiS5yo"
            "zRjvveuo3YCOrBg9LiauaLxapffIEZJUS/+IbihRMOP85o2mcMJqEpGEFCGFSAmSkEQ4/qVrFDtN3vOhtKSAS4tRKLABmNxSsWRu2hpBA8UhUoyiBopi9Ms5"
            "C4p5S4Bv7u4iv1zFfSo6BFzf9aSPFVoSBSgKUByj2RlLtm6yGFC+nV3N/7VCJQHctm1854xpi3BxjDzgs6AIspEsK1jTYarVebNcZnX6bq5V2j7FZj+Yfjvl"
            "6hKamzT9fNYkoas/mqrDXqc+dfOAblnF620ZyHpohZHzCzGOPzfgwqmvbO7t/Zas6zKB6cHtpq0Vp5EPvCQaOx62ALgOHE33IaBtiWAa5MTmW03tGQTogTst"
            "efpR02Cv096XMlJgikP067glTww5PbTda0e/17qSmwHKzU4X32AY+BzH7kyGizsHnXSNSHUk2b8YCUkWS9a4eMwHpbz9uX49pZYd5GyBg84cjwDBy8+aFBIH"
            "NZQ0YSShqZ8s7F7rBJypVmlrhZBLC5QFyLezB5g/+IYLJZIoWGAzjtH0FUu23OUSYHJNnk0rDQwHuMf7WG3G1DOPZZIwdElUX+iaKEJXL1lUzJu855M01WWn"
            "zyJGr4Dp0mnXkIg1a8HNy1ZXSCiRvPWqRcD07V3csZxDA9i3jxxw7sVhF89/b8FHBy3p7zV1lUwvPGU6+YXpj6/djWLeZMah1KX/X8FCgRLwe3/F1F00AbPA"
            "MeB9YBxM91acussmYHQ5weapvBc4Axy+rUxfJu2K5wcotbexExgFTgADiyn/DYLLepcaoqF2AAAAAElFTkSuQmCC"
        ),
        24: (
            "iVBORw0KGgoAAAANSUhEUgAAABgAAAAYCAYAAADgdz34AAAEE0lEQVR42p2VW4hWZRSGn/V9+z86B8camrnIEaWQCFKsBMnswkSwyAwRGsO0RFJvxBsx0pui"
            "JKyBohK7EKMu8kIoQ8Gb7DCVpJIYZCZaqZMols7hP+y9v7eL/x/9c2Z0aMHig/3t/b7r9K4NY5urn9OAvcBnwKz6M+N/mtWBPRABzmCPB2VAZhy9A5ob7n1D"
            "ILe1ES8WCiwGtHKRr7622gYAFbNsHiOwMSNutM6mJqZGEd4L319iO8FmnjxkafukYJMfMrtWVt/EZlan8E+1Sm5wkHPAqfFEvgA4DaSAhn3dikhKkQLq2eoE"
            "NnwX6uclYOlYVRiueTDjoDebv2G5DbR1WJPFkC2aVr0UrLUjgCAeNN7rcSpdrWVevqJ028fylUQ/AQ8CSR1TjSQeIBuxFdALz1hJVWINEaRa5IrrniIJqUJQ"
            "lXTLahcbKOPpaQh21D4YEHnPbkDrl9ugUqqVQRSqdeAUhQRVSkgJac8mVwaTcxwGmsbb7Fwmw/eA3t3m4hBQUrlBkFZrGRz41BLvLESeP7JZpo9V/5vNAxSL"
            "zHCOK8Wc15XffVBaI0mrKMQolE0PP+ACEJxxCZjX+P1tZx+4KwQKzz1lam4LVAfAZ8BFUC2BZcXcmdisKRZ3tNmdZrwFTKg31jX4yDLNg8iMA+B05qAbVIzU"
            "j073mk71Wq3BA+j+qU4/73Pphm4bBNRWYI134K0m/TEz6WxjMnDt+fmuqhM2tG+nhacfN2Uc8oaeXWxasgAtWRBJAZ390voLWUu840Ih4pPI8xHwAfDozRVy"
            "ANks93hHumy2xffdfUNQzXnUlDOBaVJTpPO/eCW1aQrbX3EhY6aMc/I4tRZNZuyo40Y3T1EL0FtX6Xfec3F6pwsn9ljoO2zq+9Gp/7yXhEK5NraqojPHXXKy"
            "11cuHfGljUujqsHb3v2XoJGkPZ/nkXld5HMZXgT0xGMW6zJBQ0hJgzaSugtJhCNfuFKxYPKe981GEoxq2YidgNYss1Qpab00I7Tx6zGXTGpxAq7mo5E9GG0/"
            "OcDaoDWXYR+gN152sQJK6+ILMQop6vvTaVqXS4FSFLG2EXw0huFNGQD/N1yNsuw2g2PHvROOJIEkhiSpfzGg8NdFOWecbW9nVx1X4/rDLV2Kd8b+jHf67WtX"
            "Umy1BSiCRFAFqYI2rnIJkE7I030rZY/QxsQm5gKV7oVeii3VOYu/2Wul9Std5fVNbujCUauoTLj8g/VH3mTGt52dFBuW6K13kxlvFjIWDrzqyu9stjDj3uv6"
            "EKDWoql7kdNXu1zlyTmuDKTNBWaPZwEOp7glF6HW/HXQs1HEhy0tLJxQYB1wCOgHU1uTCagAM8dDMJxeB7AD+LyQY+2UDroa982KLvLtzcypL739wLpG4H8B"
            "hF4OcxWgYaIAAAAASUVORK5CYII="
        ),
        32: (
            "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"
        ),
        48: (
            "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"
            "83/abaGXIfifxgAAAABJRU5ErkJggg=="
        ),
    },
    'rage': {
        12: (
            "iVBORw0KGgoAAAANSUhEUgAAAAwAAAAMCAYAAABWdVznAAABJklEQVR42o2RzUoCURiGn/MzthBHZZhoE0ExV1AEIrRJvADvoGuoy/AiugG3gsu2LVy6K5AQ"
            "pVXQQkTPeduMOrXqwAsvZ/HxPN8HgC2z76ZM9W/fDdAAPP97O5PA6hJ3skGsEBdYPogAnGOZEznDcILhnbChwCmQaUpTHbwCufok6pMokKuD15SmApkKnDwQ"
            "LRhbYf1Gv9grDvI6OhJK0HuSA3SoCAigjlkuaIc1WShwGtGQyCVyjWiowGlNFha0Qx2zBHgaUJPIdzNa2xt86JHEHkm8xocZra3IdwNqAh73bMMuXmNSzWlr"
            "QqpJ2cek6uIFDA83sqAId8BDC3N7hTsFeCN8fqFX4NnCSyxdAawF3NEvLYM7bsj+vaIDvKmssRznq7N+ABzWZqHydsOuAAAAAElFTkSuQmCC"
        ),
        16: (
            "iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAABqUlEQVR42p3TsU5VQRAG4G/PuYAQrtfL4VYYCBRaUPEKVPba0mh8Elp4BTpqnkETGujQRjTY"
            "2BBDDIZouNlzxoIDHAkUuslmd+b/Z3d2dv6EEsn/jUhQdDxNx27atbgHu7YTXuAx4h8yueb+TIgVpRnJb+Gr2jOlHj6pwXOljGO1ZaVpyS/hpMXrA4McRvnI"
            "IE9L+cwwhyovKPKCIocqnxnmaSkftdwDg4y6hyK3ed1dy07OD3F69z0s2vlD3Pi7WHf0UDeijLbCdXtzI3nrUVvtpOxggfrqqLrA6WdNk9piTmLHpVKybda2"
            "WaVkx6VJLCsl4oumwSlsrelFqMZhFLv6gdgwFXv6sacfG6YCsasfYT5CNV7TC2xZ4gk+vDQZ5+bGYZQPDZp1EzEvxbwU6ybi0KAJo3xubvzKZOBjG8uQRewv"
            "KmLTTBwbRqjqrMpZlUNVHxvGpplYUgT225jbTo2r+rzGO1yMFLGqjFVljK6CLvAeb+L2h4vUOaQp2k3mKVYwavHvOOnxrbnVSNGRC+033ygztYzib4Gku+p9"
            "SDzXcanTY3H3RvgDBsyuRvXnkmsAAAAASUVORK5CYII="
        ),
        20: (
            "iVBORw0KGgoAAAANSUhEUgAAABQAAAAUCAYAAACNiR0NAAACOUlEQVR42rWUP08UURTFf2/+ZNhZNuzuzJoIhgSVRhMKY2OlBSZ0NsZKPwBfwYLQGLSjpOUL"
            "UFPY2xg7l0bJNiw0mEg06/Jm5ljsm8mwAU0M3mTy5tx55+Te3DMXrjkMEAC6Lj1TqnouI6Bw2NQwl+RKjMNyeBuYr+F/iZI7NIDm8bjpahwh+uTcxWcOwy/E"
            "Z3IA7uMzg+E74gs59/BpuBqOKRi6XrItYitSK1J7wJwF7D4tK3p2QNsasAbsgLYVPbtPywL2gLmKt0VsgSwA/PPaVEbuHLvcqNbXyOXGUxjg3J1BORRTG3vd"
            "AqUNpu9dxQuu8lKdaP/yvR4BkFkUTMYkfExliwKxgMdrGgAs4FGgyiK+M5Ew2EnzNgCO++S3zETELOHRwLDLmGdEzOLxhuaFKnYZE2NYwiv9KOeEYwzstDAa"
            "0slEqoJU2zQFaJ0ZHdGVSCQSHdHVOjMCtE1TBalEqiGdrIWRgR1uwG3gbJWwyEgykSon0QaxXGdaxtcyfoU3iFWQSKTKSLKnhAVw5rQghBeAHhOqT9uKtBA9"
            "HdLRJrHWCLVGqE1iHdKW6EmkRZ+2fUIoQE4DAB+gAc+Bkwj0kkh7tPJTus64Pfek9pSu3aOVvyJSNKn4xHErreolmfzTb4EBoAijRTyt4GsFX4t4ijBl6wPg"
            "neNUGnU7+UBugFVofoCHP+ABcAfoujvfgK+z8OkRfHwPP1Xjcok/y02WX2VgXVyefm1zwR8Mb6bWnabua1rov8Vvg+/2cLkbGUcAAAAASUVORK5CYII="
        ),
        24: (
            "iVBORw0KGgoAAAANSUhEUgAAABgAAAAYCAYAAADgdz34AAAC10lEQVR42tXVz2tcVRQH8M99741mEiZpZiZuhFpE2tpFQaGWSpetq/4H/g+FrrpXXIjQXf+F"
            "7ixddCMVdCUIxUI3Ki6aFtzYSGhjTKYzc4+L3DdOBzUVFPTAhfO+957vueeeH49/WVJZ/19JqNdYPUbkEs0TPMIajpaDLaZga0V/VPbmsYrYJD3hacI3OIb4"
            "B5+r5dpMiJ7ktEYISbIju2/qVZVjarAl+94UnFAbqsCmqR9lp9V6qhnHfRM7AkzPa3LYyGGYw0a+50hGvqpb8I18Sy8jI9/Sm+FXdTPyPUee4zivyZg2qA4s"
            "w1joYL94HhccRnPxj+bwccH2xXMcueS48XtiZivNVUA1p/sLPC1wtNL8naw1c/qLSnNYB7bl0GCyYBQv0KkNRs94mZiF1hTjX4WEiXBBx0eWwQUdE6GR7Irn"
            "oquK62clXQkPhtJrD6xbkVLCtnDUtoHkW+u6kixU5a6tvie8advPwiPr1iWBX0S8btuW2KwavtgS6TPjnCT7wrrKZUseyt6347E8Iz+4ZfK47D2UXbZkXWW/"
            "9MAd47wlUsOXlnk7MTmunjzVz2EYI4PYM4hLOoEYSnFFN27oxQ29uKIbQykQl3RizyBGBhGG8VQ/H1dPEpNl3gI1HyMu6oy2i5NsEPsG8aHl6Bey+dWX4gPL"
            "sWcQuZBv6+f3dEaIwtnyqxtuIk6op7f1JmGQw0aEjdjRjztW47qVuG4l7liNHf042B9GGOTbepOT6imi4dOS93q+IusOn5QmjHc0cc3K+K618a7+NAyn7XgI"
            "w+mu/vSutfE1y+OzmjayXDiqxQqefSxxrjq4wW77HBtSnFLHGU2c0cQp9SwHbUVX3Fzi3CLfYo/UHIzMVd4Yc3GPd3ESr2ClnNvFT/iuy1cNn+/wwyKHP2nC"
            "tt/y/KGzdMe8BB2efc1eHGLnkC5v51a0efmDadK+dV4kfhEHh52N/8RP/zdKnk1AipHQPQAAAABJRU5ErkJggg=="
        ),
        32: (
            "iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAADuElEQVR42u2Wz2tcVRTHP/fH5EeTmUxnkkoKChbSVtwI/lZMsYsIQXTjrrsI4qpZiP4VWbno"
            "woUbXRUquKirMlpDkYpIF/UnFkGxtqQ2sbZNeO/de7p4Z6Z3XlLSpItx0QOX9949997vOd9z7jkPHsqAxQBOn4MQ+V8w8C5w1IGYChMCBH33lY0hMb9LYyqF"
            "PreiV0BCOd0xg6bBGxCB8BSeR7F9Xl0l8i0FDniVGqMYRHXnyLmuq1sYXqbW060jfElOAJ7D80jl3D+JXKDAgMOXDMhJ6iJMiTCpY0o6NASQPRhZpZXop2QW"
            "L8qezOL7dKu0ZA9GAOnQ2HTuSeoCiC9HKbcQAkKB9GJ6U+0WYA2hrnqfxLgb75Do1pCexzf13KBzXrGo5pZVUEmSyiYgLtFXE8tUdGlC2uRbtjjXDvoa2ge5"
            "v1u97/gW7HZjllCbPSAD8X5qYtXLA1iCFqQDOyQywYplUkL9X0SqOKP6mSHcQIi9BcJHjPO6+v4GQ6C3JwI3EDKFGd0iQAnWLQtcBvipV3TvghzG0cAQgVNk"
            "WL1yAtQxHGOEY4xQ1wJVKKWfkRGBBoYn9H6kZvx4F+uyFTgPyBnymCNYNSAA+3HMa4VbYp1lMoaxGAXbILJBpNA9w1iWyVhiHQHmqTGNI6jeAjnCGfJu2M8D"
            "vOa073zKuAiTktGWQFsibbnEXmlqVWti5ARjcpt2UtnKcZu2nGCsb+0l9krUszLd8wnjAgTFnDOAs/C1wEvT2PAdE24aR6FsWAwdMt7iP1Y1rodwvEKNg5p8"
            "vxJZJucXpbaJ4RR1jjJE1NzxGP4m8jRr4QrRGTgX4Ug3Fs/bMn+KZ/DhCi0RJiVPLL9IU+ao9er/vcYcNblIs8dkrvuv0pJn8QEoFOuFtMoCHFdaioO48BUT"
            "PXrLQ8rRoSGLjMgsXmZwMoOTWbwsMqKNp1yXJ2E6y4QcwgWgUIzFCnbv5QNdIBbyBYbj9+rN5tGWDVqyQUtBN6+5QFMWGI4GckD07PdTTFPpNwF408KHER7T"
            "UhmOUGOeIfsi3sxgmcBSq9zvHGGNyG9EvqGQL8jiWXIKBbLwR4TjwOcJ1qYq0VVMAu85WAiwLy1ibUzYj2USy6hOrgPXiPxF5DrS1ywdrAT4GFgCrqXg95K0"
            "m+6z8LaH0x5WzDYJSPlfKR5WPJy28A79DrjtSnw6byuWtoEnLRw28LiBKWCs+z8jsCLwe4SfgR+AfyrAcTf/n0Y7pt1lo/PbdWuzQ2PsfeyR3Xr7UAYidwA+"
            "rN+TtJs/cwAAAABJRU5ErkJggg=="
        ),
        48: (
            "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"
        ),
    },
    'trip': {
        12: (
            "iVBORw0KGgoAAAANSUhEUgAAAAwAAAAMCAYAAABWdVznAAABnElEQVR42l2SMWtUURCFv5l33yMuKGKIaRSRgMIiIiIEUbBU0SZFKrsgGrARLBQEH0IsFwur"
            "+A8khaCFJoXBZRtNsFDcImowUXRlDeIuuDHv3jsWZnU3p5riG85w5sCmbJzEchxAuHWw4m+WLwBYXs4sR7uc6w4yQ/gLoPHEyiG8Ns1QkfoGPXJmiAhmU1wv"
            "As8kZ8GWfweimAjRpnQidKTt7oQZGydR7uPMcOygnZxktvNmaITU2mRxzerJWDwSK0kWPtsiKWWk141iKTtvX6h6P1kLG6fn7Bvz7cb+4V5Gvk/rxOCpOMpA"
            "EimFn37X1bMuqRyG98Ti8mNdff4utrSkzaCt11rTxGHY5noUxJeAX93sIFM0hagEE/oCwD5m58JXqqG4UgvrR+dshflGY/dwPzRNaoaze0xajbXO0tCIfeKB"
            "X+Vi8SIZC4/4YXc5boukluOUS3gRPC12FlXObDvQ/EBHtifrOpiOhof6lmuhxR45RkH93/E9jjlqTwZm/dPSDbP/H+5Kt1ZDbhPjwt5XvNy3LEK0nL5q/AFU"
            "b7+Ccw0sOAAAAABJRU5ErkJggg=="
        ),
        16: (
            "iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAACJklEQVR42oWTTUhUcRTFf/f/3jh+gEUlGFEErSoLWtSiNpZba2fbqKBVm3YVyKiQLVIGclWL"
            "Vi1C27VskUThRgIzMRTKz2EsRczPmXn/d1q8kUwHPKt7L9zDPefeC7sgMAAJ06Ozr/T4/B0AZTKOCnB7mtuSWkcHxqmF1vjE4gUAmjudlJDvRLgzMRADeAkH"
            "iInFdefZSgjADO2ZYJtVz6hTF+9LXbSaEY8NkIqxIA4slHBmRL7L+vTQniaSkkkdliRja5SoZzK8xLvSKM1NN63oahW4WsVmxPpC1jXpfhRq6D8pEibhymPj"
            "x8hqzkl5Wvxq/bhfP9mtfNiuBdPmCC0A+kCoDG7b8F0wlCPjC7Zc1I8lr5c5FcgXc4cvVtqCzXS728evxrc4RoHIBdSI0qFzBapeX0txrhrW8PHAz2D17td4"
            "gQNuTREF4nia6vnvqRfOpwIQIv6niagR54uAB6rBFSEAl0akSeKAjTCwrYoSohwZbbEc+bkl+Sc5/SG/PlNfUUJiYj+BRADgJ8kqbyr9Trdo5eC4VuiOpqra"
            "NYtKYztMVPkEBKYMTvdIqZfn+og2x2kG0BzzmqUXQJ/J6i0q9XBjuye5A4F1Ev86SpoNzkSfuF5zmsFv/VTFq+bjDXMSzq7wgAn6wnUuG3svcrcsl8ng/AhT"
            "fpSeci3c95kA1EZgluzEDR+pY7gxDcBg4te+BAwkzR0diOmGN26qYahMEFd6pr/KdRw3CmBuxAAAAABJRU5ErkJggg=="
        ),
        20: (
            "iVBORw0KGgoAAAANSUhEUgAAABQAAAAUCAYAAACNiR0NAAADKElEQVR42o2VT2hcVRTGf/e+eW+mYWZaa2xxZdVBULBIF6Igoq5ciVAXQheizaL4B9xI3b1k"
            "UUX8gzsRSl2IIE1dBKQkoRgrtR0xLZaMVsZKIKEVTZtMps28mem793MxL2lSJ61nc3jn3fPde75z7ncNm5iEMQYp3r0Dl75HEFTNyMzh1fhmebYfEADDPd8p"
            "Xd7G1vZ+n2s/vz4uMCJbeyeTMFJvs+QUD7ozxrmT0ZfZP7u26f864SF2ZiX1krZh7F2y9p5ub+33WGOQXqOkg2xdPe0GQMVr/nFELf2AA8bgJGyhDARAmPH6"
            "LOnCO3eX2GW+xdjRtdwM1Ga8SMIQMu+L1IJH+azzE0PG4LFY8gEUgsAYpCrl7S8sHuMRPe0iP8YIytBuNkrc5GzhAiX3C5P6G3Xq7NPvxUEtIV3liOqVvJvj"
            "hC6h5Dv2r+f0tg2qTVHUPBNaCnSjw0in+24rTd8fU8KoXKBO3b66nqoNFoOtv0W+dpRIIpydpSCRu1KtlF2bcellOaeuc1Xv/YCUMASGudNsqdWINE2ozwlr"
            "MVEcY83Zg9Hex57sfmh308FhCRGRtb6oblr6YiDnX9llbNsYcjh/NTHdvRft8o8FGlaseOgCTeTnTHT+t/BNy0AAFo/oTZcM4MGH2PRhYBFJWavbKGcgNPJ5"
            "IN/rPgEpluV8IewQx9haTDQ9TSgRzk5lJR+vlHWNcekpOanr3BkvFyptbiy5dpRoKiZ3x6a4y2ZCTeSSnSM32sMt131uTA1G1QqU1Nm8KbeOjU5RcheYVAO1"
            "6nZf8xKDaiAtcETHK3n3Byf0J0pO32ZsMkCjmB36mEn9gJJzDAHoHx7SfCDNB18DXKlS1jgTOobSj3hjNXfjXY4zSbLcT5c97mcObNnDYQnLMp6Wg5ZzEmbw"
            "CZqc5yUucjJY4UViTHZN+nN4/W3uzUoJAJKzVDSD9CtfAWiKHIBep7jYRxxytwIWP+WvrAQBFBqI0HofGA8OnsFnInt9NWf9PbabKTXDve/OzKDl3H3WVh8o"
            "ZEKCMT0x6aeL/wFck/fhns+n25doRp+wEn2zPm4M6vcU/AvFScB2NJbFngAAAABJRU5ErkJggg=="
        ),
        24: (
            "iVBORw0KGgoAAAANSUhEUgAAABgAAAAYCAYAAADgdz34AAAEJklEQVR42pWVS2xVVRSGv73POffRlvIIHSgxMdEgVMELTXCAAxM1wQGIURoZGB8DR5qIIUSt"
            "entNAGOMzhwZwInR4iM8YiKEgANCQhotaVNKUgoBqV6BCvT2ce49Z/8OzukDWmjdyc7ZWWev9a+19r/WMsyxVMSaEi78ZOWjvuMN63k95qOevcxz2Tlv9GIA"
            "PNWabc68S+heLxYTPZH8+18AAqMidlJ5SypffaHGqsvOrSyPUyrOiFLFeTg7a4o68ADUx2ZdQurm6AT4hDPzimBSaRt5lXi2800CAJrSSEz69THtxfTckbyP"
            "tgcFbQ8Ks6VtCn1CaSFvkePImhWUAHgKAZBJtz8VmWklrnwZFFxjdBg/Pqi3aTIgaQpkCqA9NZTjpMtywT7I+7Xf2GkMsYSJDJbAQtaycWNiPDwdrMk31360"
            "D2uZy7vv8KlIk7HOku8UOTxMIT7FeV1EtXN8CqC/M5s1hvQPxwHCcxTUz3n1o9ov7Lwz1bOyQEWsOpPcV3pZqwEGNIqiK/4OVdggLVNUaTmg4cWrdM0MqILi"
            "s+xCGAlPHXjTbc3JoFs9NMdl/6wquHGtOxnHg6rFXf2K6JNQddAW57Jh9r5K7uX13MdqYBGQA7KAxcvV11UiRltc/oF9GfvtUtyTDlu28Aq4o3tGxura6q/H"
            "deM3QxhFhMAN4CaMl7OcOhYO+ovrG9aZYORgtlExARCl0BkL1VHrFjwzbvRzAzQ42dCKJuGOSPpmU7177Xl8Y3MBIkOS/SWICN9mq7WRpvxzlryP8XBEyE08"
            "kAAZcGDc41gaEmR54IQVCIFvwEdkgXxKYwtYxoxvhnJ12Zq/sOHGaRewdnwEyGDIpRHEsZeLqZihPS0s2b8P752l0jZn7bCFTcaPThwccXVt9RqtG/cAQ8LB"
            "KnKOyFVdbVA3hu79yL/THF+zZzWMq0VbTzp3XVH8db/G6FPoqfonH8+v30yn6fGkVsPeYK0uMaAqiv7yd9QqTRuk+xXf4oCGFq+Kr5gBDaFaX0rTjjtoqtnq"
            "IRVqLwV10a8yGpsotH/ZrBCpbI4DVHooqJvz6ka1Y3cvNDvduDFIu2lhiO+5ykPVi+zKP8J7Eiaq4FExECru7CRoeIyu6i1eop8Bv8wH8Rd8pm3k0e0g03tR"
            "IqzyNLDcdbM7+4Rpk/CMQX6IIxSEcOgQsTrwsuv5gyu8yCUu22G2VnI0GpMQbGL5k6dSKnZ8FV3njB9wDAQnJoETd8K0QlsTENNKV/ghGzMeZsFuyiLJxAwA"
            "k7AfU6IC/DoJfDWRR6PgB4Cb5t8WXDqzz9xp5+6MAqMteJMDKJ1otY7MCzrU6PTDoiNI5rYhdY/m5s9oTiD2E8+4eHGZxeaMC01QbG83pWmemhLurs1uzhqZ"
            "YNfnK5Yzplas6TVtfT/Nd6b/B+8vJfpil7YNAAAAAElFTkSuQmCC"
        ),
        32: (
            "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"
        ),
        48: (
            "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"
        ),
    },
    'shock': {
        12: (
            "iVBORw0KGgoAAAANSUhEUgAAAAwAAAAMCAYAAABWdVznAAACLElEQVR42iXSy0sUcQAH8O/v95udmd2Z3Z1d95XLiq27dTA00dKeRksQIiEUQQcPlQTVbQk6"
            "FKmQByGKyFMeQioPHUKMSiESde1xcNXNkx7qEIWRuNvMPmb28evg52/4YHBwkALwEkYeReobZoK+0KzP63tBKQW1oT8UCo0AOIhdVABA9wTCE9FwrPfGpSTq"
            "AxHcf3rnx+fV+YGe433j0UgcqfTclU8rC93t7de+0+Hh4crhliPK3nCMT314ZXpc3prbqf0ljLj93iD/mlkqnDt1IQRgXzo9XmaiKD7nNd555miP02FXmF12"
            "0JnFN85fWz/Z1vbvqGiTxMZwE1LpubCqqgnWcaDrtVvxqE0NcVw9f5N821hFanlOmhydjhfNAl3fXCOt+9sIpaxRUz0tgmpXK3ZJYXpeJzkjB78ngOZYCy9Z"
            "pdr65hq7PTCEolmAQ1aqnHNOug8luNPuQg0co8knMAo6zLKJklmES3Eh5A/j7uMkcnoWRkkH5eCQJBnvFqbwcGIEgiBAssnwe4KglGHs5QNMvn0GSZTAOQcL"
            "1AWHEl09yGyksbQyj6y+A1mU8SWTwj8ji3tjt1Cn+XDxbD8yG8sgna3HqifaTqNoFvA+NY1qtQoOju2dP2iOtSJfNHCyIwHFrmIx/RGCZZmUMQGay4vLfdeR"
            "M7IghMDGBORLedS5fbDKJsqVCizLBNE0bVYWFfBaFZxzCIINAFDjHIwylMvm7gkmoGTl8R/nGdJgVXjHsAAAAABJRU5ErkJggg=="
        ),
        16: (
            "iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAADaElEQVR42p3L709VZQDA8e/zPOfccy54lcslroIiWOocc4StTWjqWs1IaukKZrSp2Ror56jM"
            "VkyiDdG5NWvFK2Wrd7lW1mZOQBLMHIG11UIojRRFhGsX5XK5P885Ty9c/0Cf9x8AASCEwLbtj4FO4BjQmZ+ff1hKSU1NjYXiBaC+oKBgCfcJ/lNaWroR6Coq"
            "XKoPNh3VBxrb9f7d7+uixcXTDQ21QdM0T9du2qobanfpQG5gJG9x3nKttQCkam1tNbq6up7c/vSON5eFl2cqVq/jxdpdbklRGWcufHu178LFmbrNL73z8OpH"
            "nayTdZ97vC7c3f/dSvd798vz4+cN2dbW5jiOjiwvXuHOzEbl4eOt6o9rl9WV66PKUKYsLCj8K+tmU+O3/5ZfnP5clj+01gvkLFzQdqHNEUKkjWAw2B6NRtfO"
            "J+LqkfL1XnBRPmM3/hQgiMVjZZFIZNvJnhMzq0rXFD1YssqNJ+Iy62bLPM87FAgEMLRL8xPrazg32EPHgc+kkorgwnxxsvcEsbnZRW/tbN4fynuA/ktn+em3"
            "H5WSCr/PX1JVveG9SyMDSMtnOVs2bHVvTd9k7MYVlDJwXZeei6eprtyomxvbndLiFfqXy0M8VrkJy7KxfLbeXP2MY/tsR2qtjfh8TJmGSa4/F7Qmnozzxo53"
            "uRebET/83Gv0D50Va1aUc7DpKAIBCDE3P2torQ1DozEMk2QqwZXro5SvrCDyzxRLwyUc2ddBKp3k1bq9uK6Lz7S4FZkgmZrHMEwAJBq01kilaD/WQt9gDwXB"
            "QtKZNJ7nYVt+EqkEtuVndGyYlk/3kXEyCCHuP41GSImSilQ6yf4P93BuqBvHzSKlRCmFkopLwwPsPbSbOzPT+EwfQkg0Gul5Hjm2n8b6JiyfRSqdpPmjJnoH"
            "zjB89VdaPnmbRGqeb3pPEIlOYflsGuubyLH9eJ6HYRomk5Fb3Lkb4ZXn93D8qw4SqQSn+r9mZOx3XNflxu1rWKaF3/Lz8rbXmIxMkMlkMA0Tw9OeWxxexmRk"
            "gsp1m/jg9SPcnBonmUxQVbER0zCZT8TxPI/tW3YSDi2hb7CH4vAyPO0h8hYF9VNVz5K3MEg8MYflszGViZASrT20BiklAJlsmnQmxYKcAPdid+keOIUIhUKd"
            "0WiU/yMUCvEvZ357hfb+iKgAAAAASUVORK5CYII="
        ),
        20: (
            "iVBORw0KGgoAAAANSUhEUgAAABQAAAAUCAYAAACNiR0NAAAE2ElEQVR42qWUaWxUVRiGn3Pume3OpdN26nQRWjEhotGwiI1StP0hbhDFBWKMinEZcSGuMS4J"
            "ahRBEYzYoCjGEEKMEtEYISQmLtiiqEmh2li0AQttoZSuM3Pn3pl77/HHUNTEfz6/Tk5O3vPly/u+ghIC0IsW3VbR3r575djYmJ68QyKm1U4bPNp39D0hRLDi"
            "9hWpjj87aoQUesvDWw5duOzCwpm3k6TT6VA8Hl8GbCizErr5kiv1grktuqVxoZ4182KdSCQGw+EwVVVVc4UhemtStYVkRVUR+Hrp0qWJfwxVYt26dXHLspzZ"
            "M+fp+XNair99cdzr+OSI9/vuk4Xtr37umabZ1draWqOUGnrg1sf0xxt264/W7wquWXC9Vkp+1dy81ALkpKhKp9MJ0zT7d23a6829oDG45arb9E8f/6EPftrr"
            "b35+u7Ys6+Csi2c1NtRN17ve3htEwhE9+/x5ev+H3V597XStlFrQ3NysgIgEvK1bt44LIUTGnjBy+Sx72r5gLDNC95EuEAJAHPj5wKGMPXGq+0iXaGlcqPtP"
            "HMPzi7KyPBl4njfS9l2bB7gylUqlpdSPOI4Tt8wpzKg/j7NTU7HzWXbv/UzEojGKxWJdKGrcOjI2/OPTbzxC3rH1FKuMQGsC35fAci31imQytUJls9nNCbOK"
            "nMzS3vEtqx5cKwZO9pMsP4un73lRbPmkFdd1k3POn/eO1hANR/n1j4NSSgNlGMLzPSrKKp+KRU1Gs8NIx817992y0rv28ht4d8dbBDoglawGoO/kMd7a/jrL"
            "rrldb1v7mbdt7U798qMbKC+rxPd9QiqM7eRYeNl1/r03P+y5ruMpIYTyAw8hBL7vUSgUEEIghCBkhLBMC8ssE4D6tecgD710FyPjw7z2RCuhUJggCAi0Nny/"
            "pCEnraO1xpAGIRVCo/EDHzMWZ+Oz77Ov4xt+6GxDGYq61FTWPr6Rq5oW47h5lKHQ+m9PqzMHQ2G7NmOZUarKz8J2cjhunhkNM9m6ZicZO8MUcwofrN6B7/tk"
            "chOEQ2Fy+SzKMM6IysnpIqEIecfmyXUPMJYdJRqJEQQ+juvgFlyioShuwSVnZykUC0TCUZ5vfYrB4ROYsfi/BQUCt+gihKCrp5OVq+9mIjdOwionEo6i0RiG"
            "gRCCeMzCjMV57s3H2NP2OUII3IKLOJ08KRB4QZHqZA1aa0IqRPfhLlauvpvOng56jh4iGo5yamwIz/M43NfDo2vu5ct9u0r71prqZC1e4CEQSCEltpNn/pxm"
            "lt+QpugVUYai+3AXr2xeRcYeZ39nO3c+cxO2k+NIfw+jEyMoQ1H0iixfcj/z51yB7dgIKTEMw3jh0oua6B04THWylnPqzqWj+2cAKsoqGRjqZ9OH63HcPPsO"
            "fEsmlyHv5hkY6mP5kjT1NdP5vfc3DGnwU9f3GFKKF5pmtzA0epKv9u/hrhvvJxKJgi5ZZ/DUcaZVN1BTVUsQ+AwOH6foFbjuiiVc3bSYzTs2UlFWScIq5/vO"
            "vQgVUt7jdzxL40VNHBvsxYzGMaPx0+aWSCHwg6DUtUIihCA4/VneyWE7OaZVN/DjL+1s2PYKImbGdG3lVMqsUk8GQYDWwT/q978RlNIkZSkbE9lxjo/0Ierr"
            "61cNnOjD8wL+D0pJ6mqm8hfcCDbUNtMW6AAAAABJRU5ErkJggg=="
        ),
        24: (
            "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"
        ),
        32: (
            "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"
        ),
        48: (
            "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"
        ),
    },
    'pitfall': {
        12: (
            "iVBORw0KGgoAAAANSUhEUgAAAAwAAAAMCAYAAABWdVznAAACGElEQVR42iXBTUgUUQAH8P97bz5258OddtVdk3TzWyR3EVIvoqTVLQRPHYwOfdy7BBWYhy5B"
            "1L1DBw2igwTtQp5CCypIkTYVhWot2CWUcG13dnzzZl6Hfj9gDhRAXCXsceOp5BsnlViON8YXKaUwaGQ2lUo9AJDFf5TNTcyxwu7XF8ls25WpezNdw9cnO/d3"
            "Sg1u+bDWMz30LH2uf4xITNd+Hb3O3shW6Pz8vGgf6TPjp5OysPTpOHrCCqOOeaASNWY1x2Tx4647MD2c4uA960/XfaZr2kKAcKT3YsbWDJ1phka38mv2n9IB"
            "O/p92ME0RUt0JPFjdbvVsKxJ1n62e0l3olZTZwtGb54n5S8/8W11U599eavbdzktF/ZIayZNwGg66piDimbpghka8/7WiVdxYTY1oOVMmxSeH5YLe2zqzgz8"
            "OodmaAGVkKR3YlAqtgZI4NKjqziuehDHPoTHodtRNJyMI3d7AV6lBlHloBISSkTFZu4z3j58BapQKLoKsykGwijePclhbXEFTFcgJcDsZud+z4UMShtFfH+/"
            "De+wBiWiYu/DDryKi/zd5zATNoYuj6G0UQTpGO0L0uP9EHWOrfwawiAEpER1/wgtg+3gVQ9d4wNQrQiKK9tQBBeUMoqoY2Lk2iS8iguAgKkMvObBTNgQXCDw"
            "AwguQGJObJkaCmQQAhJgKgMAhBKglCLgPkAAwihCV+Af2VTQVQi0pUkAAAAASUVORK5CYII="
        ),
        16: (
            "iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAADZElEQVR42nWTXUzVZQCHn/fjz58jHDiHA4IKAs2PcGZm2lIjaibRRllt5WKxddFqdOeabniR"
            "m/Z11bp1a2trdpNc1BrJTHHQBermHDBZcKQi5OsAhwMcD+f8P963G73sd/08N8/2E4AArBCCiBv5NpfPlQJGgIxXVCxmMpnu1tZWd6hv4PWAQEYqy/9YWlqa"
            "e+wBUNtQ+yLQF9uWsO1fd9rWcyftse63bWJr1UJXR1fcddzeve2H7KH3X7KR6KaxmlhNPRYBSNVyrkVP9k28sr+j+VSsLuFt29/Is50tYby+ivHeu8mb/f3p"
            "PScPn9l2oCEI/SDc++Zz1cNXbu1sDpt/mhqY0nLwwkAQ2CAVb9gcZtPr8vfPe9TC2LRKjc8qqZSs2lx53wYmn/5nUd65NChr9tYZNxopHbwwGCAo6Fg8/sXK"
            "8spTXjav6g/uNKXxKMvJOWEFbKw/bFxMpd6aupxKb969dWvlji2hly1IE4SN1tgvo9EoOrTm7K7jTzNxfYR3Ln4shZJsipeKkZ4hcmvZ8pdPnzhdkijjfv8o"
            "f98cV1JJVLGz/ckjz3TP3voLqVwn2NN+MMw8WGYpOY/UChMa/uy7S+PRJtv62btBorHaTt+Z5IkXmtDFDtp17O62/YEqdgKJtbqwnlPKURSVuGDBy+Zp+fQN"
            "citZMXnjnk72j4rqplrav+pECIEQiMLqhsZarbEgtcLf8EiNz7Bl33bW5leJ1VVy4psP8Dd8jnS9iglClKvJPFjGy3moIgWAtADWIpTg6vnLJK+NUlpVRpD3"
            "McaiIw5eroCOFLEw9oDesz8Sej4IgbUgsRYhJUop/LzHz6e+J3ltBBOECCmQSiGlZPp2kp6ui2RTq+giByEEWIu0xuBEijjS1YZ2Hfy8x69nfmDi6jBzw1P8"
            "1n0JP1dguGeI9YUM2nU4+kkbTqQIawxaOZq12TTZxTUOf3ScoYtX8XIFRn+5zfy9fzGhIT21iHYdnIjL8x8eY3VmmcDzUY5GW2PDstoEqzNp6pr3UH7+PVam"
            "U/g5j4aju1COppDNY43hQEczZTUxJq6PUFabwBqLiMbK7I7X9lESL6WwvoEudpBaIaQEa7GPGgGEnk+Q93GjER6uZLl/ZQSRSCS+yyynMVgkAvPoof+3x4xE"
            "EEtU8B9fKYnAgxGcrQAAAABJRU5ErkJggg=="
        ),
        20: (
            "iVBORw0KGgoAAAANSUhEUgAAABQAAAAUCAYAAACNiR0NAAAEv0lEQVR42o2TaYjVVRiHn3P+y91muTNzZ2HMyRHHkdKWgVDKmtJCy7RINC0hM5iSigqiBaGi"
            "BYLCgiIzK9A0IrMEdUjbcLkpQhZGXkdLm3GccUbneme59/7Xc/pwkyboQw+cD+fD7+U973leQQkB6NkLFlQdS6cfH8nlNEgBSpvYomZi7UBvT+8GIYRa8ciK"
            "ur6f/2yQQuqODx/rWjp9qXcpzyU6OjqsaCKxFFgbq0joKbfM0JNvvFK3zLlKT7i2WScrKwds2yaVSrWZwuyubkh5lTVJXyJ+WLJkSeW4pjABWlpabEOITRPa"
            "miOReDS498PVwnc87ERM9Rw+IbetfP/Cq2vXNqx58qndsx69LdUyZwZaa334o+9v3r5t+1ft7e2L9u7dWwCQgHny5EkTpYbufH1F6Due0fn8FkMHyvCLnuGO"
            "OoaUMtixYXNT5WXVqemLrtObl7/Ft69tE7e/ujwsm5i8JZ1OX9Pe3i4BWwLBxo0bh4WQwhkpGl7eIdN5hGIuz2DmzN8PQez75WCXN+JcGMj0iilzZujcmSFU"
            "EMpEdbkKgiC778C+AHDNurq6jmIuH3McJxEpi1Hb0gha4I45HNv5k5h621X4vt9YbsWXuYF7eMfTm+6YdH2rjlTEhFYarZQUiAdsbZwuq6vEHBsbW29WR5AF"
            "xZ8HMsx/eZkYPpslUVvJrWsWi4Pr9+C4Ts1lbZPfR4MZten7tVsKQyJNKcIgJFaVeMaKRyhmi5h+0QtufHYh5zN9ZnrdHmbcM4uy+iQoTa53iP1v76Jt2Ww9"
            "7+XlIVobo+dy4rNV71K4OIZhmXgFl9Z5V4f1V0zU3724DRMhTBUoEKDDkNAN0AKElBiWiV0WJVIeE6DN/qPdfP7wOgrZMe5euxLDNtGhRmsMFYQgSr9cQmuE"
            "IZGWUbqGCjseYfG6Dk7tz9D9YxfSNEhOqOGuNx6gdX4bvuMjTTle6ZKHANIy8AsexVyeRG0FXt7Fdzxqpzay4tMncUcdIhUx7t/8BCpUOCNFzIiJl3eRxj9F"
            "5aXuDNvEK7psf+JjnIt5rJiNDjW+4xF4AWbUInB93DGHwAuwohada7YwOpDDittorccVFBC6AQjo/62HravX4w4XiFbGsCIWaJCGRAB2WRQ7HmHnc5+Q6Tzy"
            "ryyAFAJUoCivT4IGwzIYyPTyxeoP6D/azfmT/ZgRi/yFUcJQMfT7AF8+toHjX/+MYRmgobwhiQoUQoAUUuIXXJpnT2Pmg3MJ/RBpSs5lzrDnla24I0W6D3Wx"
            "+b638PMu2dPnKGTHkKZB6IfMXDWX5hum4RfckhmmYb7UNKuFbPcg5fVJqifV03vkFADxqgTDZy+y/51d+EWP0weO44wW8Ysew31ZZq6aS9XlKQa7ziKlQe/h"
            "PzAMYbx0+U2t5AdHOPHNUWY+NBcrWhqyCjWjAxdJTkxR0ZBEKcVof47QD5i+8Dqmzb+W9Hu7iVeVEU3G6UmfQERMO7jh6QU0z5pKtmeIaCKCFbdBCIQsndLO"
            "ljwVArQGHYT4BQ8n71LdVMPpQydIv7kLEY/FdbyxgmhFHAEopUDr8a7+JwJACKSUaMAZKVDoG0E0NTW9cKFvEBUoFAo5bnn+D5cy0pSkGuv4C2a6MhN5DcKG"
            "AAAAAElFTkSuQmCC"
        ),
        24: (
            "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"
        ),
        32: (
            "iVBORw0KGgoAAAANSUhEUgAAACAAAAAgCAYAAABzenr0AAAIl0lEQVR42r2Xe3BcVR3HP+ex9+5md/PsJmkhLdOmqZS2aAtuC30AgVIo1IIWHQvO8BikKg8B"
            "QRlQlBHGGRks44wWRxG0yqOotIC2IBpAyoTXYGlLQ+lYEtLHJs1js8977zn+sZs0SQOKw/ibOXNmzj33/L7n/H7n+/0dwVhTgGhublZTp06dkfV9FYs6jDdP"
            "CmvTBZHP5/va29u7AFFuRggxZq61FkAChglMTDgoBOFw+EDgBQnPL9rx8wTChBxHNk5u3N7d3b3E87wQ4M08aeaJrgxfq7QM2cBapZVMHU79o6ur68Hy5oLx"
            "vvRRn8JOmjRpRSqVmuFG3Vg+W6iJVFeo4+c0YwIzBhwWub99jwiCoFIphed5XjKZnLdjx47nstlsYpyPKxsaGhpTqdQ9xhgN+BMBkFLKwHXdO4DT8kN5AGYs"
            "mc1F91/JUG8aFdKAHQZgN5x3l/CLvjXWsGDBgrm733nn2Ww2m1j8tRXF6UtnS+MHFDMFnv3hE7ans+fu5lnNuY53On4y/iTkmJ0ZMyBDyl/1o8sKyStaObi7"
            "i6FD/Vg/oDCYpZjOU0jnKAzlS7E1VkqpSPWkbhocGKhfeu35xdZvX+wERV8PfHBEN50yQ6/ZcI2uSMT9vR1772047riVZefqGAAAxqBUSOlPnT9ft7TOI9XR"
            "zZZvPYzxDVIrrAWEQEg5KiRgAmOFEHbuRQvljifb2fiV+9ly62/YtO4BEi2Txcp71loLcqC3t3V87snhDPY8rzRoITeQIZfOIYTg3bad7Nn2Fm48ghN10W6I"
            "EpKSZTNZiSVircVayPWmR74NHuon15+hckqNUCFpPc/LjfIpASFLLvG11hZRuipSHd2h1Arj+QhgX9tOjuw7hAwprLUIIazjuqYmUbM9FAqJbT94VMxedQpn"
            "3Hgh9S3HobRCKkng+WBL17TcvHJvdTKZbBgaGnLmJucGL/y5LYIQWGNxow7WWqwflHpr+cP1v2TB2mWcc8caMBZrjLr6O9dP8QcHNwV+sHBX284vbVr3CzN7"
            "5QKpwyGyR/KlkJW5QSlVlUwmpwwUB5QTOIHjOIFIJBKD/f39Ggu+7zmhaFhd8cdbqJpcy8sPPMu/XtnD8tvXUN1UR2pPN+GaKLFEFb9adQ+DB/qMsKIQ2MAK"
            "yFuoHZ1TdTMauPyJW+jbn+KhNT/G+MaXCA9AKEl1dbWvi8Vi3PM8KqpjOCpMpjfNrqde57RrlnPq5WeSvLIVExj8os+klimE42HefrKd9KF+jDFy0ozGiHJC"
            "WGsrhAXlaoSA7h37EaP4y1qwWO3WxbSwMNQzSLFYRNvAWDceZvl31yAdJbbe+Rgvb9hGy/KTiTdUU8x7pSMUYIKAvvd7+fu9Wwj8gJaz5rF6/RXWL3hYC1IK"
            "oV3NM7f9ju5/7scYgzUl7gg8n1h9FSvu/KI1nuHp2zdiA4O0IBBCaEcLHdJoV+MXPAa7jqDDIYQQCCnAQiji0rvvIIMH+6hsrOXMW1fjFz3hF30BVggp2HzT"
            "Q+zY3I61lmhtHO3qYT0oraWlEEoIBMKC0KNFY7ghQGh5jEaYICBSHQXAibo4sTBCSoQQGC9g6/ceZffWNwGY1DyZlT9ci/FNKRRClC6ctWOusZxIjLDlNlqA"
            "pMDPedTPmsLy29fQ894BNn75Pvq7eohUR9n7/NvseuZ1ABIzp3DJz64m1lCF7wUfInljtWBiHMYeA6KYLTJ/7RKUo3h786vs2vwai766nJZzT6a/q5fDHR/Q"
            "eutqookq8pkcTiQ8EoL/EkAZrrFoR1NI51FSjQGR688w53OfZe7FC/HzHoHnI5Ti9K+fCwj8gkcxVxih7FDY+fgn0Lb+KeqaG6mojVHMFcewo5CSYraAHU4s"
            "KcEY8un8CEhEKcuUq2n/9d/AWoRSx4T22BwoH9fBXZ1sWreB3JEhnIgzpiYYBiHLCTisSlLJMUDDlRGeu+tx3vj9i2PW/mgA5QWlVhzu6OaxazaQPTKEEwlh"
            "goD/ZMPxDscjbLvrcd545CWkVh9af8nxmzd+gBCiRBJKknq3u3wSGZwKl8ALsMaMODOBGUnW4XE3VnL+5iMvIZXEBqa0pm8+PATWgnYUsdp4SemkwJRBHO7o"
            "5vF1G8j2pIknKnEqwhhjQJR2Goo4WGPQYYdwZQV/vXvTiHMTGIQUJWKqiyMdNSYSeizRGJJXnc0L65+mrzOFkMMLlEBsvHQ9VcfXEYq4nH3bRcQn17Dl5oep"
            "nlrHGTev4rWH2njtt230vZ8qkVP5XxMYaqbWk7zqbOy4XJKjQ28Di1tVwbIbL6CmKYE1BqHkSD/Um6bxpCYWf2MF1VMTbPv+Y+ze+gb7t3cQch2mLzmRpTes"
            "5JTLzkBIgdSlf2umJVh24wWEKyuwgWV05a5cx70zEIaW1nmYwGKtwa0Ic/yC6Rza1UmuP1NKImupPaGeqckWBg/0sf3n29i99U2Uo8n1Zeh97yCZ3jRe3kMp"
            "Rc/eQxSG8tSdUM+yb16IGwvjF70SNQvoeH4HykpEZSxu88Jj9b2X0/nqu7z1xCvUNCVYct155AdytN33FH2dKf4Xqz2hgaU3rCQcc3nxp3+h//0U8z6/kKZT"
            "Z/Knmx4kbEPoYTLxCx7TFs0itecAna+/h5f3cCsjLLnufN7atJ1iJlcSHimO6rs5Gk8pZbm6FFhjcCrCnPyF03CjLn7Bp2fvAZrmz2DaolkUM4XSrbCjk9BY"
            "/LzH/EuXUtfciCkG+BacaJhFV5/zkXw+8esK/IKPX/AxXsCnLzmdaQtb8PMejNIYPTw7FHHKpbdl5llzCAo+iKMyLT7m8VsL2tXDDxlmnjUHv+CjtMLoYITw"
            "RDwet+lMmsYTm8qFhx2uePkkbWTNctF7cHcn8WgcHYvFgkKhQM/OA9iJH7CfuAkkjuMQi8XQixcv/sy+zn1EY1H+n5YZyjC9aTr/Bgz1DIytm085AAAAAElF"
            "TkSuQmCC"
        ),
        48: (
            "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"
        ),
    },
    'flash': {
        12: (
            "iVBORw0KGgoAAAANSUhEUgAAAAwAAAAMCAYAAABWdVznAAABqklEQVR42pWSzUtUYRyFn/e+971zxxxzcMykBjNpqAGDrIgoKCKHRCuaaSGTEERQYov+AlvW"
            "XzBB4SJoEyFBi6D20cqwqEn6gKKFSDofdxzm4965vzbuooVnfR448BwArRQAVy6f21tLDcV+as241lw4NNKzeunsngowsdXRiIgCsvmpIb9azEsuk1wDXgCL"
            "16aGy5XPecllkg1gUkS2OHi/O+HuanV40276HE71TvsdCb788J45rm07lkysbbS+A6ctgC5jblZq/s7hQbdVb3Q+3p5ORW9kR2L1RrC8fzAalj2/a4cxtwAU"
            "sAxc7e+LjM/PjhaiUZvzJwZo+yFvP6xTKrV48Kh4/U+l+Q54Ti6TlDv5VBU4lohHnn57fTGQlZlAvs4En15OBj0x+zEwNpc/4OUySbG0trBtZQAHEP6NAMZo"
            "y2htoZRiRYRsf1/kzL3Z0YL7/0lLSrEIoI0xR9yIro2lex8Cd5/cPymF+eMCzB1NxxccY1W7HScNaA1IGIavEvGIve4Fv7SSgd+r9YNLxVKzWvM3vGaou11r"
            "X3mzfQpY2LY4tnuNv8zgwjJjVVAIAAAAAElFTkSuQmCC"
        ),
        16: (
            "iVBORw0KGgoAAAANSUhEUgAAABAAAAAQCAYAAAAf8/9hAAAC40lEQVR42qWTTWxUZRSGn/Pd7/Z2bm+ntHZsa7HtUENrqlUHSaUQQSS1USqKSIkRDAtXYtwZ"
            "dWFipDtW+JcYSY2mYgySSmhCqsEFUxJJpib+BNFItP60CGlxLFNm7s9x0W6NJj7r87455+R9YRkrAsB9db6dv7nZV2DUdel1oQc41tbiq+85l4C+lVlnRYsx"
            "Ijiw9Z47G+evfrWncnZsIAReE5G3EA4Bo1+PPxjOffF4mLu1fs5aNphlE+OowsGDbPI8e+qZJ9bWFsuxczo/a/LTl0tVrukToTNJ8JsbU+2xY0xj4AZnCld2"
            "i9HJJGFWAAvsFmFMlc+Ac4CKcPuTQ9mh0vVIj3/6y3FVLqysvUFgs8IQcMoIRCLygSoT6cC9O1XtZID3Vflz/85O2ftw1qjyB3A0qLHNgW9zCh+KyEmBCGAE"
            "6Fi3pr4uU+8tvPNqn+4abNPVTb4uFob18tRjmmnwdO+OrL758nqtT7u/P7W5vRroAEakrtbVKEp+vrYU9wPZjblMPv/RYPzbr4tO4yoPVZj/q8JNTX6ce2TC"
            "fHl+IQdcqUk5Z6w1HXRn0+WpsQHNtgazTU1eFnhp/6NrVH/aF14rDGtpelj1x33RroE2BQ5kMl5ne6s/NzU2oN3ZdNkA4qccjCEVl9QCyj+jyXW1VqTaTzkA"
            "Ig11VZqozl0thv1Ay8ZcZupfTrgLWFyVdvNGpBngMNDV33VD7X994rODt3hAN3AYETDL2TyZDtyFVLXzNtAFvHf63W06/vq9CrwB9AQ1djTwbRE4akQQWQ6G"
            "VdgjwgvlSpKPIv0B6BWhLUl0beHbef3uYnEGaKmESakSJlZgu8I0cBFVxFo2+Sm7dOj5nI4f2aovPt2jwOdVrpmxVr4Hzr5yoFc/OXK/jjx3h3pVTtF1WQeI"
            "EUGSWPLlpeihY5MzC1vW3xgObWmNgG/CSCeiWE8AF3ZuWx313dYQfTw5cylO4gfiiAIg/N86/w0EdEKMO/AOQgAAAABJRU5ErkJggg=="
        ),
        20: (
            "iVBORw0KGgoAAAANSUhEUgAAABQAAAAUCAYAAACNiR0NAAADU0lEQVR42rWVS2xUdRTGf//7nzszdx5tnaltOp1pg2JTaTqDTIlKKVaSEl7FxChUqiwMDivT"
            "SGIwJqxscNOgLjDE4CNBQlwoIYEGMTEaiQukFMXSlgCSNFJbUvrAtjNz597jwmsUN5pYv+SsTs6X8/wO/AmfUgA8AowbhioBAmwF0sBDwC5A1O++m34/DV6M"
            "j7/B8BxZ4NY7r2dl6PNtxcZlZQKsN7U6bJqqF3i6pSkmw18+VejtyQhwA2hUHsc9ZMGgrzVe4Z8+dbhd5O6egkztLjyWqbS9rMZQ/ADs2bS2xpa7ubzM5QrH"
            "+1qlLGKOh0LmShEUYChAxeNEpqb4qT4Rjrc/Ws3MbBGtFV+dn+DObPFkzf3WNseV4uRU/mxVPNjZlq2iVHKpKPdz9tw447cXb2azNAwMUFKABgLAEFAFfAKc"
            "B3wGmC7kensyjTNzRfo+HB4EPgYKXn/XAM8CI1677sGTSuGGg/oWcAhYJhdypmGo6eFTW+XbYxsE+NlvGgArgCNWUE8qRQFo+StRczpdHfam8sYDqYj09mRk"
            "eX1UgOvphgpxrnTLwqUuqU+EBbi2Ynm59PakpbY6JIbBXqUgna4OA80amJiYmG+PRDnplNTpO7PFjmxTLHXiyHoJaSO2obWGTFMM06cI+Aw61tTEjr7d5hz9"
            "7Lrx9XeT/YZSL4fCVI6NzfcDB7AC2tm8LiHAQDRKPJWyEsCvx/taXZl+qVS6/JzkB7skP9gl7o87RWZz9rv7Vwtwu7m5/D6gEriweV1CrIB2UAoZ7e+0D+5b"
            "JYZSV1MpK+H36y2RkM8ZPLHJKQ7tlIXBHbJwcYfYV7rlm2MdTsBv2JGIr6221koqpa4e3LdKRvs7baUQQwS0RkXDJigijiPam+A/QUol16cgEg2baI0SAayA"
            "trdvrBNgxILaurpQzb8t+YlMeQWQAoa3b6wTK6BtvGy+tyyS2lAA51598WHXvvaC8+YrK+WjA4+LM/q8uCPdcmj/annrtazIjV2l3c88KMBpbSgsiyRw6Y/K"
            "1iaTZbH/tjaKZLIsBrQu+WL7PAsAH4iwOJ93zgCXgS265T3ThV8+/WKsYmauCDBRtN293uldXMw7Qe/03vdIZcnFYcnl638R2CV9Ab8BpVbR6rWgbmQAAAAA"
            "SUVORK5CYII="
        ),
        24: (
            "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"
        ),
        32: (
            "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"
        ),
        48: (
            "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"
            "HLc8hU4AAAAASUVORK5CYII="
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
