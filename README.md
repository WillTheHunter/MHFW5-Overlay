# F5 HP / Size overlay

A lightweight HP and size overlay for **Monster Hunter Frontier Forward.5 (F5)**, written in Python with no third-party packages, and buildable into a single `.exe`.

For every monster it shows:

- Current HP, maximum HP and HP percentage
- Quest size percentage
- Small / Silver / Gold crown (as an icon or as text)
- Capture-range highlight and a capture icon
- A skull (bones) icon when the monster is dead
- Optionally small monsters too

It has two visual styles, a console menu with six pages, an optional log, and can start the game for you and fill in the login fields.

## Contents

1. [Requirements](#requirements)
2. [Start](#start)
3. [Building a Windows executable](#building-a-windows-executable-exe)
4. [The console menu](#the-console-menu)
5. [Styles and icons](#styles-and-icons)
6. [Starting the game with the overlay](#starting-the-game-with-the-overlay)
7. [f5_overlay.ini](#f5_overlayini)
8. [Crowns](#crowns)
9. [Colours and transparency](#colours-and-transparency)
10. [Behaviour notes](#behaviour-notes)
11. [Contributors](#contributors)
12. [Credits and acknowledgements](#credits--acknowledgements)

## Requirements

- Windows 10 or Windows 11
- Python 3.8 or newer when running from source (not needed for the `.exe`)
- Tkinter, included in the standard Windows Python installer (keep "tcl/tk and IDLE" ticked)
- The game in **windowed or borderless** mode. The overlay is a normal Windows window and cannot draw over exclusive fullscreen.

The overlay reads the game's memory, so it needs the same privilege level as the game. It asks for Administrator rights by itself (see `run_as_admin`).

## Start

1. Make sure `f5_overlay.py` is in a folder you can write to. `f5_overlay.ini` is created next to it on the first run.
2. Double-click `f5_overlay.py`, or run `python f5_overlay.py`.
3. A black console window opens with the menu. The overlay appears when you are in a quest.
4. To quit press **Ctrl+C**, type `q` and press Enter, or close the console window.

If the program was not started as Administrator it asks Windows for it (one UAC prompt) and restarts itself. Turn this off with `run_as_admin = false`.

If an older, incompatible `f5_overlay.ini` is found it is renamed to `f5_overlay.ini.old` and a new one is created.

### Command-line options

| Option | Description |
|---|---|
| `--console` | Print the game data in the console, without the overlay |
| `--once` | Read once, print the result and exit |
| `--count N` | Read and print N times, then exit |
| `--demo` | Show fake monsters so you can adjust the overlay without the game |
| `--style lvdew` / `--style alexander` | Start with that style |
| `--config path\Config.txt` | Load another monster table |
| `--log` / `--log path\file.log` | Enable logging (optionally to another file) |
| `--debug` | Debug logging and extra console output |
| `--no-launch` | Do not auto-start the game this time, even if `autostart = true` |
| `--no-admin` | Do not ask for Administrator rights |

## Building a Windows executable (.exe)

The finished `.exe` is a single file and does not need Python on the target PC. Build it on Windows.

The script already works as an `.exe`: the ini, log and optional `Config.txt` live **next to the exe**, the icons are built in, and the Administrator request is built into the exe.

**Quick way**

1. Install Python from <https://www.python.org/downloads/windows/> and tick **Add Python to PATH**.
2. Put `f5_overlay.py` and `build_exe.bat` in one folder.
3. Double-click `build_exe.bat`.
4. Your exe is `dist\F5Overlay.exe`. Copy it wherever you like.

**Manual way** (what the batch file does)

```bat
python -m pip install --upgrade pip pyinstaller
python -m PyInstaller --noconfirm --clean --onefile --console --uac-admin --name F5Overlay f5_overlay.py
```

- `--console` is needed: the menu lives in the console window.
- `--uac-admin` makes Windows ask for Administrator every time the exe is started, so the game can be read even when it runs elevated.
- `--onedir` instead of `--onefile` builds a folder (starts faster; distribute the whole folder).

**Troubleshooting the build**

- "Module not found": build with the same Python where PyInstaller was installed (`python -m PyInstaller`).
- The exe cannot find its settings: `f5_overlay.ini` is created next to the exe, not in the build folder. Check that the folder is writable (do not keep it in `Program Files`).
- Antivirus flags the exe: this is common for PyInstaller programs that read another process's memory. Build it yourself from the source and allow it.
- The log: set `log = true` (menu key `i`) and read `f5_overlay.log` next to the exe.

## The console menu

A text-art title is shown at the top (`bn` turns it off). Type a key and press Enter. Numbers and colours are a key followed by a value:

```text
f 12
x 80
tc gold
bc #00ff00
op 80
```

There are two views:

- **Pages** (default): six pages of at most ten commands. `>` next page, `<` previous page, `1`-`6` jump to a page.
- **All options**: `0` (or `all`) lists every option in one list. `0`, `pages`, `>`, `<` or a page number goes back. The choice is saved as `menu_mode`.

The console is cleared after every command (`cl` turns this off). `s` saves to the ini, `q` quits, Ctrl+C or closing the window also quits. A `-` means the option does not exist in the style that is selected.

### Page 1 - Display

| Key | Description |
|---|---|
| `o` | Overlay on / off |
| `m` | Small monsters |
| `z` | Monster size percentage |
| `c` | Crown (Small / Silver / Gold) |
| `p` | HP percentage |
| `h` | Maximum HP |
| `k` | Capture-range highlight |
| `d` | Hide dead small monsters |
| `t` | Switch style (lvdew / alexander) |
| `v` | Preview with fake monsters (to place the overlay) |

### Page 2 - Look and position

| Key | Description |
|---|---|
| `f N` | Text size of the current style (lvdew points / alexander pixels) |
| `w` | Position preset: custom, left-center, top-left, top-right, bottom-left, bottom-right, right-center |
| `x N`, `y N` | Custom position in % of the game window (`x 100` = right edge, `y 0` = top) |
| `r` | Lvdew: HP bar on / off |
| `l` | Lvdew: bar position (below / above / left / right) |
| `n` | Lvdew: HP numbers (below / inline / off) |
| `e` | Lvdew: bar mode (simple / capture) |
| `b N`, `g N` | Lvdew: bar width and height in pixels |

### Page 3 - Colours

A colour name (`red`, `gold`, `darkslategray`) or `#rrggbb`.

| Key | Description |
|---|---|
| `tc` | Text colour |
| `tcc` | Text colour in the capture range |
| `bg` | Box colour (alexander: boxes; lvdew: optional box) |
| `bgc` | Alexander: box colour in the capture range |
| `bc` | Lvdew: HP bar colour |
| `bcc` | Lvdew: HP bar colour in the capture range |
| `bb` | Lvdew: empty part of the HP bar |
| `sc` | Lvdew: text shadow colour |
| `fc` | Lvdew: frame colour |

An unknown colour name keeps the old colour and shows a warning.

### Page 4 - Opacity (0-100)

| Key | Description |
|---|---|
| `op` | Whole overlay (the only way to fade the text) |
| `bgo` | Boxes (lvdew: optional box, 0 = none) |
| `bgco` | Alexander: boxes in the capture range |
| `bo` | Lvdew: filled part of the HP bar |
| `bbo` | Lvdew: empty part of the HP bar |
| `fo` | Lvdew: frame |

### Page 5 - When to show / system

| Key | Description |
|---|---|
| `j` | Hide the overlay outside quests (town) |
| `a` | Only while the game window is focused |
| `sh` | Hide while taking screenshots |
| `cl` | Clear the console after every command |
| `bn` | Text-art title |
| `ad` | Ask for Administrator when started (next start) |
| `i` | Write a log file |
| `u N` | Read / redraw interval in milliseconds (lower = faster) |

### Page 6 - Icons and starting the game

| Key | Description |
|---|---|
| `ic` | Crown / capture / dead icons (lvdew style) |
| `is N` | Icon size in pixels (0 = automatic) |
| `gs` | Start the game with this program (only if `mhf.exe` is not running) |
| `ge` | Path of the game (`mhf.exe`) |
| `gu` | Login name |
| `gp` | Password |
| `gf N` | Launcher field number that gets the login name (0 = automatic) |
| `gm` | Login mode: `fill` or `autolc` |
| `go` | Start the game now (if it is not running) |

## Styles and icons

### Lvdew (default)

The look of the original Lvdew tool: name, quest size, HP numbers and a small bar that changes colour in the capture range.

```text
Velocidrome  [115%] [gold crown]
1600/1600  (100%)  [capture icon]
[==============================]
```

With `show_icons = true`:

- **First line, at the end:** the crown icon, one of *small crown*, *silver crown* or *gold crown*, when the size is inside the crown range.
- **Second line, at the end:** the **capture icon** while the monster is inside its capture range, or the **bones (skull) icon** when it is dead (the skull replaces the capture icon).
- If `numbers` is `inline` or `off`, there is no second line, so those icons follow the first line.
- With `show_icons = false` the crown is written as a coloured word (Small / Silver / Gold) and there are no capture or dead icons.
- `icon_size = 0` matches the text height; otherwise the nearest built-in size (12, 16, 20, 24, 32, 48 pixels) is used.

The text and bar turn yellow in the capture range (ranges come from Lvdew's monster table). The text has a black shadow for readability.

### Alexander

Stacked translucent boxes, inspired by `MH-HP-Overlay-For-PSP-Emulator`:

```text
(115%) Velocidrome Silver: 100% | 1600 | 1600
```

The box uses the capture colours in the capture range. This style writes the crown as text; icons are for the lvdew style.

Switch with `t` or `style` in the ini.

### Icons

The five icons are built into the script (base64 PNG, six sizes each), so nothing has to be copied next to the script or exe. They were supplied by William and upscaled with Upscayl; the artwork belongs to its respective owners.

## Starting the game with the overlay

The overlay can start the game for you and fill in the launcher's fields, like Lvdew did.

1. Open page 6 (`6`).
2. `ge` - type the full path of `mhf.exe` (for example `D:\Extra\f5\mhf.exe`).
3. `gu` - login name. `gp` - password. If you type just `gu` or `gp` without a value you are asked for it (the password is hidden while typing).
4. `gs` - switch the autostart on.

Each of these is saved in `f5_overlay.ini` as soon as you enter it.

On the next start the overlay starts the game **only if `mhf.exe` is not already running**. `go` starts it right away from the menu, and `--no-launch` skips it for one start.

**Login modes (`gm`)**

- `fill` (default, same idea as Lvdew): the game is started normally, then the overlay waits for the launcher window and types the login name and password into its two fields. You press Login yourself. It does not use any command-line parameter, so the server list stays as the launcher normally shows it.
- `autolc`: the game is started with `/AUTOLC username password`. Some launchers then select server 2 instead of server 1; if you see that, use `fill`.

**Notes**

- The password is stored as **plain text** in `f5_overlay.ini` (it is hidden on screen and masked in the log). Do not share the ini.
- `fill` looks for edit-type controls in the launcher. The one with the password style gets the password; the login name goes to the visible field right above it (a drop-down name list is written as a whole). Hidden, disabled and read-only fields are skipped.
- If the name lands in the wrong field, turn the log on (`i`), start the game again and open `f5_overlay.log`. It lists the launcher's fields numbered from top to bottom (class, position, current text). Type that number with `gf` (for example `gf 2`) and it is saved.
- If nothing is filled at all, look for the log line `has no edit controls; children: ...`; it lists the control types the launcher uses.
- Starting the game needs the same privilege as the overlay (Administrator, if the game requires it).

## f5_overlay.ini

Created on the first run. Change it with the menu or by editing the file. Invalid values fall back to the default.

### [General]

| Key | Default | Description |
|---|---|---|
| `style` | `lvdew` | `lvdew` or `alexander` |
| `update_time` | `0.03` | Seconds between memory reads and redraws (about 30 per second); minimum 0.01 |
| `show_small_monsters` | `false` | Also list small monsters |
| `hide_dead_small_monsters` | `true` | Hide small monsters at 0 HP |
| `show_size` | `true` | Show the quest size of large monsters |
| `show_crown` | `true` | Show the crown |
| `show_icons` | `true` | Lvdew style: crown, capture and dead icons (off = crown as text) |
| `show_hp_percentage` | `true` | Show the HP percentage |
| `show_initial_hp` | `true` | Show the maximum HP next to the current HP |
| `highlight_capturable` | `true` | Highlight monsters in the capture range |
| `low_hp_percent` | `0` | Also highlight large monsters without a capture range below this percent (0 = never) |
| `only_in_quest` | `true` | Hide the overlay outside quests |
| `only_when_focused` | `false` | Show only while the game window is the active window. Independent of this, the overlay is always hidden while the console / terminal is active |
| `hide_on_screenshot` | `false` | Hide the overlay for a moment when a screenshot key is pressed |
| `screenshot_hide_seconds` | `2` | How long it stays hidden |
| `screenshot_keys` | `PrintScreen, Alt+PrintScreen, Win+Shift+S` | Comma-separated combinations (Ctrl, Alt, Shift, Win + key) |
| `run_as_admin` | `true` | Ask Windows for Administrator when started without it |
| `clear_screen` | `true` | Clear the console after every menu command |
| `menu_mode` | `pages` | `pages` or `all` |
| `banner` | `true` | Text-art title above the menu |

### [Layout]

| Key | Default | Description |
|---|---|---|
| `preset` | `custom` | `custom`, `left-center` (Lvdew's original place), `top-left`, `top-right`, `bottom-left`, `bottom-right`, `right-center` |
| `x`, `y` | `1`, `50` | With `custom`: position in % of the game window (`100, 0` = top-right corner). The default is the left side, centered on the height |
| `fix_x`, `fix_y` | `0` | Extra pixels added to any position |

### [Lvdew]

| Key | Default | Description |
|---|---|---|
| `text_size` | `9` | Text size in points |
| `font_family` | `Segoe UI` | Font name (must be installed) |
| `font_weight` | `bold` | `normal` or `bold` |
| `numbers` | `below` | HP numbers: `below`, `inline` or `off` |
| `show_bar` | `true` | Draw the HP bar |
| `bar_mode` | `simple` | `simple` = whole bar turns yellow in the capture range; `capture` = bar stays green and the capture range part is yellow |
| `icon_size` | `0` | Icon size in pixels; 0 = same as the text height |
| `bar_width` | `180` | Bar length in pixels |
| `bar_height` | `8` | Bar thickness in pixels |
| `bar_position` | `below` | `below`, `above`, `left`, `right` |
| `max_rows` | `5` | Show at most this many monsters |
| `row_gap` | `8` | Pixels between monsters |
| `shadow` | `true` | Black shadow behind the text |
| `frame` | `false` | Dark rectangle behind everything |
| `frame_color`, `frame_opacity` | `#000000`, `100` | Frame colour / opacity |
| `box_color`, `box_opacity` | `#000000`, `0` | Optional box behind each monster (0 = none) |
| `shadow_color` | `#000000` | Shadow colour |
| `bar_opacity`, `bar_back_opacity` | `100`, `100` | Opacity of the filled / empty part of the bar |
| `text_color` | `#FFFFFF` | Text colour |
| `text_capture_color` | `#F0F000` | Text colour in the capture range |
| `bar_color` | `#10D010` | Bar colour |
| `bar_capture_color` | `#F0F000` | Bar colour in the capture range |
| `bar_back_color` | `#303030` | Empty part of the bar |
| `opacity` | `100` | Opacity of the whole overlay |

### [Alexander]

| Key | Default | Description |
|---|---|---|
| `font_size` | `18` | Text size in pixels |
| `font_family`, `font_weight` | `Consolas, monaco, monospace`, `bold` | Font |
| `align` | `true` | Same width for every box |
| `orientation` | `center` | `left`, `center`, `right` |
| `text_color`, `background_color` | `aquamarine`, `#2f4f4f` | Normal colours |
| `capture_text_color`, `capture_background_color` | `yellow`, `#1e6b1e` | Colours in the capture range |
| `background_opacity`, `capture_background_opacity` | `100`, `100` | Box opacity (0 = no boxes) |
| `window_opacity` | `85` | Opacity of the whole overlay |

### [Launch]

| Key | Default | Description |
|---|---|---|
| `autostart` | `false` | Start the game when this program starts (only if `mhf.exe` is not running) |
| `game_exe` | empty | Full path of `mhf.exe` |
| `username` | empty | Login name |
| `password` | empty | Password, **plain text** |
| `user_field` | `0` | Number of the launcher field that gets the login name. 0 = automatic: the visible field right above the password field |
| `login_mode` | `fill` | `fill` or `autolc` (see above) |

### [Debug]

| Key | Default | Description |
|---|---|---|
| `log` | `false` | Write a log file (also menu key `i`, or `--log`) |
| `log_file` | `f5_overlay.log` | Relative paths are next to the script / exe |
| `log_level` | `info` | `info` or `debug` |

The log records startup, attaching to the game, status changes, monster-list changes, the window position, hide / show events, why the monster list was empty when it vanished, and errors with tracebacks. Send `f5_overlay.log` if something does not work.

### [Crowns]

Optional overrides by monster name:

```ini
[Crowns]
Velocidrome = 90, 114, 123
```

The three values are, in percent of normal size: the largest Small size, the smallest Silver size and the smallest Gold size.

## Crowns

Thresholds come from SirFist's MHF monster size table. The size read from the game is compared with them:

- **Small**: size at or below the Small value
- **Silver**: size at or above the Silver value
- **Gold**: size at or above the Gold value

100 is normal size. Monsters without thresholds in the table get no crown; some F5 monsters may differ from the MHF table, so use `[Crowns]` to correct them. Baruragaru's Silver value is listed as 1.00 in the source and is ignored, since it would trigger at normal size.

## Colours and transparency

Both styles accept colours for text, capture text, boxes, bars, shadow and frame.

Windows and Tkinter allow only some transparency:

- The **whole overlay** can be faded evenly, text included (`op`).
- Boxes, bars and frames cannot be blended smoothly with the game. Their opacity is drawn as a fine dither between 0 and 100, which can look grainy; 100 is solid and 0 is not drawn.
- Text cannot be made see-through on its own.
- Icons keep their own soft edges and follow the whole-overlay opacity.

## Behaviour notes

**Town.** In town the game keeps entries in the monster list that are not quest monsters. With `only_in_quest = true` the overlay stays hidden unless at least one known large monster is present, and ignores unknown ids. If it stays hidden in a real quest, set `only_in_quest = false` (menu key `j`).

**Console and focus.** The overlay is never drawn over the program's own console, cmd, PowerShell or Windows Terminal window, so it cannot cover the menu. The preview (`v`) is the exception, so you can place it. With `only_when_focused = true` it also hides while any other program is active.

**Monster list dropouts.** The overlay polls the game's memory. Polling can catch the game while it rebuilds the monster array, which gave empty or garbage reads. The reader therefore: uses only the main monster array pointer (and the address that last worked if the pointer glitches), ignores implausible slots (a large monster without a size, id 0, HP out of range), trusts a slot only after two reads in a row, and keeps the last good list for 5 seconds (`HOLD_SECONDS` in the script) when a read comes back empty. After a quest ends the overlay therefore stays for about 5 seconds before it disappears.

**Screenshots.** The screenshot keys are checked every 8 ms and the overlay is hidden at once for `screenshot_hide_seconds`. Add keys such as `F12` to `screenshot_keys` for Steam or other tools. Screenshots started by something other than a key press cannot be detected.

**Limits.**

- Status buildup, KO and similar values are not decoded for F5, so they are not shown.
- Capture ranges and the large / small flag come from Lvdew's monster table, built into the program. A `Config.txt` next to the script or exe (or `--config`) overrides it.
- Windowed or borderless only, not exclusive fullscreen.
- Built for the F5 client and its memory layout; other versions use different addresses.

## Contributors

- **Will** - project owner. Set the goals and requirements, supplied the F5 client and server setup, the icons, the reference tools and the tuned default settings, and tested every version on the real game (reports and logs that drove most fixes).
- **Labin222** - tested the overlay, gave suggestions, helped with the tranq bomb icon.
- **Claude (Anthropic)** - wrote the code and documentation together with Will: the memory reader, both overlay styles, the console menu, icons, auto-start and login, and the exe build.


## Credits & Acknowledgements

This project was made possible in part by the work, research and ideas of other Monster Hunter community developers. Special thanks to:

- **[Relial - MHFZ Monster Viewer](https://github.com/Relial/mhfz-monster-viewer)**
  Reference and inspiration for reading live monster information from Monster Hunter Frontier, especially hitzone values (HZVs) and monster identification during hunts.

- **[Alexander Lancellott - GitHub](https://github.com/Alexander-Lancellott)**
  Inspiration for the overlay's visual presentation, especially the stacked-box style of the Alexander layout. His Monster Hunter HP overlays were a useful reference.

- **Lvdew - Lvdew's MHF HP Display (Lvdew.exe, Revive2)**
  Reference for the original Monster Hunter Frontier HP overlay: its HP display, monster table, capture-range highlighting, launcher login filling and `Config.txt` format. Its `ReadMe!!.txt` documents the Revive2 version for the Season 8.0 client.

- **[SirFist - MHF Monster Sizes](https://gist.github.com/SirFist/f4f6640467d9166d3e4f132aea5d3cbc)**
  Source of the monster size data: base sizes and the Small, Silver and Gold crown thresholds.

### A note on attribution

These projects are credited for their contributions, references and inspiration. This overlay was adapted and developed for Monster Hunter Frontier Forward.5, with its own memory-reading implementation, size detection, crown display, configuration system and other features. All credit for the original work belongs to its respective authors. The icon artwork belongs to its respective owners.
