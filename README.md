

# F5 HP / Size overlay

A lightweight HP and size overlay for **Monster Hunter Frontier Forward.5 (F5)**.

Displays each monster's:

- Current HP and maximum HP
- HP percentage
- Size percentage
- Small / Silver / Gold crown indicator when the monster is within a crown threshold
- Capture-range highlighting

Small monsters can also be displayed. The overlay has two visual styles and a configurable console menu.

## Requirements

- Windows 10 or Windows 11
- Python 3.8 or newer (when running from source)
- Tkinter, included with standard Windows Python installations
- No third-party Python packages are required

The overlay reads the game's process memory. Run it with the same privilege level as the game. If the game is running as Administrator, the overlay may also need Administrator permissions.

## Start

### Running from Python

1. Start the game in **windowed or borderless** mode. The overlay is a normal Windows window and cannot draw over exclusive fullscreen.
2. Make sure `f5_overlay.py` and `f5_overlay.ini` are in the same folder.
3. Double-click `f5_overlay.py`, or run:

   ```bat
   python f5_overlay.py
   ```

4. A black console window opens with the menu. The overlay appears when you are in a quest.
5. To quit, press **Ctrl+C**, type `q` and press Enter, or close the console window.

If the overlay is not running as Administrator, it can request elevation through Windows UAC and restart itself. This is needed when the game runs as Administrator. You can disable this behaviour with `run_as_admin = false` in the INI file.

On the first run, `f5_overlay.ini` is created next to the script with all default settings. If an older incompatible INI is detected, it is renamed to `f5_overlay.ini.old` and a new one is created.

### Command-line options

| Option | Description |
|---|---|
| `--console` | Read the game data and print it in the console without showing the overlay |
| `--once` | Read once, print the result and exit |
| `--count N` | Read and print N times, then exit |
| `--demo` | Show fake monsters so you can adjust the overlay without starting the game |
| `--style lvdew` | Start with the Lvdew style |
| `--style alexander` | Start with the Alexander style |
| `--config path\Config.txt` | Load a custom monster table |
| `--log` | Enable logging |
| `--log path\file.log` | Enable logging and use the specified log file |
| `--debug` | Enable debug logging and additional console output |

Examples:

```bat
python f5_overlay.py --demo
python f5_overlay.py --console
python f5_overlay.py --once
python f5_overlay.py --count 10
python f5_overlay.py --style alexander
python f5_overlay.py --log
python f5_overlay.py --debug
```

## Building a Windows executable (.exe)

You can package the overlay into a standalone Windows executable using **PyInstaller**. The target computer does not need Python installed to run the packaged executable.

Build the executable on Windows.

### 1. Install Python

Install Python 3.8 or newer from:

https://www.python.org/downloads/windows/

During installation, enable **Add Python to PATH**.

### 2. Prepare the project folder

Put these files together in one folder:

```text
F5Overlay/
├── f5_overlay.py
└── f5_overlay.ini
```

The INI file is optional at build time because the program can generate one with default settings on its first run.

### 3. Install PyInstaller

Open Command Prompt in the project folder and run:

```bat
python -m pip install --upgrade pip
python -m pip install pyinstaller
```

### 4. Make the executable use its own folder for configuration

Before building, update the `here` assignment inside `main()`.

Find:

```python
here = os.path.dirname(os.path.abspath(__file__))
```

Replace it with:

```python
here = os.path.dirname(os.path.abspath(
    sys.executable if getattr(sys, "frozen", False) else __file__
))
```

This makes the program use the script's folder when running from Python, and the executable's folder when running as a packaged EXE. This is important because a PyInstaller one-file executable extracts its bundled files to a temporary location.

### 5. Build the executable

Run:

```bat
python -m PyInstaller --onefile --console --name F5Overlay f5_overlay.py
```

When the build finishes, the executable will be here:

```text
dist/F5Overlay.exe
```

The `--console` option is intentional: the overlay uses the console window for its menu and status messages.

### 6. Run the packaged version

Copy `F5Overlay.exe` to the folder where you want to keep the overlay.

Your final folder can look like this:

```text
F5Overlay/
├── F5Overlay.exe
└── f5_overlay.ini
```

If you do not copy an INI file, the program creates one with default settings on its first run.

Keep the INI beside the EXE. This lets you edit settings without rebuilding the executable.

### Optional: build a folder-based version

Instead of a single EXE, you can build a folder-based distribution:

```bat
python -m PyInstaller --onedir --console --name F5Overlay f5_overlay.py
```

The output will be in:

```text
dist/F5Overlay/
```

Distribute the entire folder, not just the EXE.

### Troubleshooting the build

- If Windows reports a missing Python module, make sure you are building with the same Python installation where PyInstaller was installed.
- If the game does not attach, check that the overlay and game are running with compatible Administrator permissions.
- If the overlay cannot find its settings, check that `f5_overlay.ini` is beside the EXE and that you applied the path adjustment above.
- If Windows Defender or another security tool flags the executable, check the build and source yourself before allowing it.

## The console menu

A text-art title is shown at the top (`bn` turns it off). Type a key and press Enter.

There are two menu views:

- **Pages** (default): five pages of up to ten commands. Use `>` and `<` to change pages, or `1`–`5` to jump to a page.
- **All options**: type `0` or `all` to show every option in one list. Type `0` again, `pages`, `>`, `<`, or a page number to return to the pages view.

The selected view is saved as `menu_mode` in the INI.

The console is cleared after every command (`cl` turns this off). Numbers and colours are entered as a key followed by a value:

```text
f 12
x 80
tc gold
bc #00ff00
op 80
```

- `s` saves all settings to the INI.
- `q` quits.
- Ctrl+C or closing the window also quits.
- A `-` in the menu means the option is not available for the currently selected style.

### Page 1 - Display

| Key | Description |
|---|---|
| `o` | Toggle the overlay |
| `m` | Show or hide small monsters |
| `z` | Show or hide monster size percentage |
| `c` | Show or hide crown labels |
| `p` | Show or hide HP percentage |
| `h` | Show or hide maximum HP |
| `k` | Toggle capture-range highlighting |
| `d` | Hide small monsters at 0 HP |
| `t` | Switch style (Lvdew / Alexander) |
| `v` | Preview with fake monsters |

### Page 2 - Look and position

| Key | Description |
|---|---|
| `f N` | Text size of the current style (Lvdew points, Alexander pixels) |
| `w` | Position preset: custom, left-center, top-left, top-right, bottom-left, bottom-right, right-center |
| `x N`, `y N` | Custom position as a percentage of the game window |
| `r` | Lvdew: toggle HP bar |
| `l` | Lvdew: bar position (below / above / left / right) |
| `n` | Lvdew: HP numbers (below / inline / off) |
| `e` | Lvdew: bar mode (simple / capture) |
| `b N`, `g N` | Lvdew: bar width and height in pixels |

For custom positioning, `x 100` places the overlay at the right edge and `y 0` places it at the top.

### Page 3 - Colours

Enter a colour name such as `red`, `gold`, `darkslategray`, or a hex value such as `#rrggbb`.

| Key | Description |
|---|---|
| `tc` | Text colour |
| `tcc` | Text colour in the capture range |
| `bg` | Box colour |
| `bgc` | Alexander: box colour in the capture range |
| `bc` | Lvdew: HP bar colour |
| `bcc` | Lvdew: HP bar colour in the capture range |
| `bb` | Lvdew: empty HP bar colour |
| `sc` | Lvdew: text shadow colour |
| `fc` | Lvdew: frame colour |

An unknown colour name keeps the previous colour and displays a warning.

### Page 4 - Opacity

Values are from 0 to 100.

| Key | Description |
|---|---|
| `op` | Whole overlay opacity |
| `bgo` | Box opacity |
| `bgco` | Alexander: boxes in the capture range |
| `bo` | Lvdew: filled HP bar opacity |
| `bbo` | Lvdew: empty HP bar opacity |
| `fo` | Lvdew: frame opacity |

### Page 5 - When to show / system

| Key | Description |
|---|---|
| `j` | Hide the overlay outside quests (town) |
| `a` | Only show while the game window is active |
| `sh` | Hide while taking screenshots |
| `cl` | Clear the console after every command |
| `bn` | Show or hide the text-art title |
| `ad` | Ask for Administrator rights when started |
| `i` | Toggle logging |
| `u N` | Read/redraw interval in milliseconds |

## Styles

### Lvdew (default)

The original Lvdew-inspired layout.

Each monster is displayed with its name, size percentage and crown label, followed by HP numbers and a small HP bar.

Example:

```text
Velocidrome [115%] Silver
1600/1600 (100%)
[====================]
```

The text and bar turn yellow while the monster is inside its capture range. Capture ranges come from Lvdew's monster table. The text has a black shadow to help it remain readable.

### Alexander

A stacked translucent-box layout inspired by `MH-HP-Overlay-For-PSP-Emulator`.

Example:

```text
(115%) Velocidrome Silver: 100% | 1600 | 1600
```

The box uses the configured capture colours while the monster is inside its capture range.

Switch styles with `t` in the menu or set `style` in the INI.

## f5_overlay.ini

The INI file is created automatically on first run. Settings can be changed through the console menu or by editing the file directly.

Invalid values are ignored and the default is used.

### [General]

| Key | Default | Description |
|---|---|---|
| `style` | `lvdew` | `lvdew` or `alexander` |
| `update_time` | `0.03` | Seconds between memory reads and redraws. 0.03 is about 30 times per second; minimum 0.01 |
| `show_small_monsters` | `true` | Also list small monsters |
| `hide_dead_small_monsters` | `true` | Hide small monsters at 0 HP |
| `show_size` | `true` | Show the quest size of large monsters |
| `show_crown` | `true` | Show Small / Silver / Gold labels |
| `show_hp_percentage` | `true` | Show HP percentage |
| `show_initial_hp` | `true` | Show maximum HP next to current HP |
| `highlight_capturable` | `true` | Highlight monsters inside their capture range |
| `low_hp_percent` | `0` | Also highlight large monsters without a capture range below this percentage; 0 disables it |
| `only_in_quest` | `true` | Hide the overlay outside quests |
| `only_when_focused` | `false` | Show only while the game window or console is active |
| `hide_on_screenshot` | `false` | Hide the overlay briefly when a screenshot key is pressed |
| `screenshot_hide_seconds` | `2` | How long the overlay stays hidden |
| `screenshot_keys` | `PrintScreen, Alt+PrintScreen, Win+Shift+S` | Comma-separated screenshot key combinations |
| `run_as_admin` | `true` | Request Administrator permissions when needed |
| `clear_screen` | `true` | Clear the console after each menu command |
| `menu_mode` | `pages` | `pages` or `all` |
| `banner` | `true` | Show the text-art title |

### [Layout]

`preset` can be set to:

- `custom`
- `left-center` — Lvdew's original position, on the left side and vertically centered
- `top-left`
- `top-right`
- `bottom-left`
- `bottom-right`
- `right-center`

With `custom`, the overlay uses `x` and `y` as percentages of the game window. `x = 100`, `y = 0` is the top-right corner.

`fix_x` and `fix_y` add extra pixel offsets for fine positioning.

### [Lvdew]

| Key | Default | Description |
|---|---|---|
| `text_size` | `10` | Text size in points |
| `font_family` | `Segoe UI` | Font name; must be installed |
| `font_weight` | `bold` | `normal` or `bold` |
| `numbers` | `below` | HP numbers: `below`, `inline`, or `off` |
| `show_bar` | `true` | Draw the HP bar |
| `bar_mode` | `simple` | `simple` turns the whole bar yellow in capture range; `capture` paints only the capture-range part yellow |
| `bar_width` | `240` | Bar width in pixels |
| `bar_height` | `8` | Bar height in pixels |
| `bar_position` | `below` | `below`, `above`, `left`, or `right` |
| `max_rows` | `5` | Maximum number of monsters shown |
| `row_gap` | `8` | Pixels between monster rows |
| `shadow` | `true` | Black text shadow |
| `frame` | `false` | Dark rectangle behind everything |
| `frame_color` | `#000000` | Frame colour |
| `frame_opacity` | `100` | Frame opacity |
| `box_color` | `#000000` | Optional box behind each monster |
| `box_opacity` | `0` | Box opacity; 0 disables the box |
| `shadow_color` | `#000000` | Text shadow colour |
| `bar_opacity` | `100` | Filled bar opacity |
| `bar_back_opacity` | `100` | Empty bar opacity |
| `text_color` | `#FFFFFF` | Normal text colour |
| `text_capture_color` | `#F0F000` | Text colour in capture range |
| `bar_color` | `#10D010` | Normal HP bar colour |
| `bar_capture_color` | `#F0F000` | HP bar colour in capture range |
| `bar_back_color` | `#303030` | Empty HP bar colour |
| `opacity` | `100` | Whole overlay opacity |

### [Alexander]

The Alexander style supports:

- `font_size` — text size in pixels
- `font_family` — font name
- `font_weight` — `normal` or `bold`
- `align` — use the same width for every box
- `orientation` — `left`, `center`, or `right`
- `text_color` — normal text colour
- `background_color` — normal box colour
- `capture_text_color` — text colour in capture range
- `capture_background_color` — box colour in capture range
- `background_opacity` — normal box opacity, 0–100
- `capture_background_opacity` — capture-range box opacity, 0–100
- `window_opacity` — opacity of the whole overlay

Colours can be names such as `aquamarine` or `gold`, or hex values such as `#rrggbb`.

### [Debug]

| Key | Default | Description |
|---|---|---|
| `log` | `false` | Enable logging |
| `log_file` | `f5_overlay.log` | Log file path; relative paths are next to the script or executable |
| `log_level` | `info` | `info` or `debug` |

Logging is disabled by default. When enabled, it records startup, attaching to the game, status changes, monster-list changes, window position, hide/show events and errors with tracebacks.

Enable it from the menu with `i`, or start the program with `--log`.

The log can be useful when troubleshooting. Send `f5_overlay.log` if something does not work.

### [Crowns]

Optional per-monster crown threshold overrides can be added by monster name.

Format:

```ini
[Crowns]
Velocidrome = 90, 114, 123
```

The three values are:

1. Maximum size for a Small crown
2. Minimum size for a Silver crown
3. Minimum size for a Gold crown

All values are percentages of normal size.

## Crowns

Crown thresholds come from SirFist's MHF monster size table.

The size read from the game is compared with the thresholds:

- **Small:** size is at or below the Small threshold
- **Silver:** size is at or above the Silver threshold
- **Gold:** size is at or above the Gold threshold

A size of 100 represents normal size; 115 represents 115%.

Monsters without crown thresholds in the source table do not receive a crown label. The source table covers the MHF series, so some F5 monsters may differ. You can correct these with the `[Crowns]` section in the INI.

Baruragaru's Silver threshold is listed as 1.00 in the source data and is ignored, since it would otherwise trigger at normal size.

## Colours and transparency

Both styles support custom colours for text, capture text, boxes, bars, shadows and frames.

Windows and Tkinter limit how individual overlay elements can be made transparent:

- The **whole overlay** can be faded evenly, including text.
- Individual boxes, bars and frames cannot be smoothly blended with the game behind them.
- Their opacity settings therefore use a fine dithering effect for values between 0 and 100.
- `100` is solid and `0` means the element is not drawn.
- Dithering can look grainy depending on display scaling.

Text cannot be dithered separately. Use the whole-overlay opacity setting (`op`) if you want the text itself to become transparent.

## Town (`only_in_quest`)

In town, the game can keep entries in its monster list that are not real quest monsters.

The overlay uses a filter that hides itself unless at least one known large monster is present. Unknown IDs are ignored.

If the overlay stays hidden during a real quest, set:

```ini
only_in_quest = false
```

You can also toggle this setting with `j` in the menu.

If something unexpected still appears in town, enable debug logging, walk around town for a few seconds and inspect `f5_overlay.log`. It records the monster entries the game holds, including ID, size and HP.

## Screenshots

Screenshot keys are checked every 8 ms. When one is detected, the overlay is hidden immediately for the configured `screenshot_hide_seconds`.

The default keys are:

- PrintScreen
- Alt+PrintScreen
- Win+Shift+S

Add other keys to `screenshot_keys`, such as `F12` for Steam or another capture program.

Screenshots triggered by something other than a key press cannot be detected.

## Notes and limitations

- Status buildup, KO and similar values are not decoded for F5, so they are not displayed.
- Capture ranges and the large/small monster flag come from Lvdew's monster table, which is built into the program.
- A `Config.txt` placed next to the script or executable overrides the built-in monster table. You can also specify a custom file with `--config`.
- If the program reports an error or cannot attach to the game, enable logging and check `f5_overlay.log`.
- The overlay follows the game window.
- The overlay only works in windowed or borderless mode, not exclusive fullscreen.
- The overlay is designed for the F5 client and its memory layout. Other game versions may use different addresses or structures.

## Credits & Acknowledgements

This project was made possible in part by the work, research and ideas of other Monster Hunter community developers. Special thanks to:

- **[Relial - MHFZ Monster Viewer](https://github.com/Relial/mhfz-monster-viewer)**  
  Used as a reference and inspiration for displaying live monster information from Monster Hunter Frontier. The project focuses primarily on hitzone values (HZVs) and identifying monster information during hunts.

- **[Alexander Lancellott - GitHub](https://github.com/Alexander-Lancellott)**  
  Inspiration for the overlay's visual presentation and HP display, particularly the stacked-box style used in the Alexander layout. His work on Monster Hunter HP overlays provided a useful reference for presenting monster information clearly.

- **Lvdew - Lvdew's MHF HP Display (Lvdew.exe, Revive2)**  
  Reference for the original Monster Hunter Frontier HP overlay functionality. This project draws inspiration from Lvdew's HP display, monster data tables, capture-range highlighting and `Config.txt` configuration format. The accompanying `ReadMe!!.txt` documents the Revive2 version, which supports the Season 8.0 client.

- **[SirFist - MHF Monster Sizes](https://gist.github.com/SirFist/f4f6640467d9166d3e4f132aea5d3cbc)**  
  Source for monster size data, including base sizes and Small, Silver and Gold crown multipliers and thresholds used by the overlay.

### A note on attribution

These projects are credited for their contributions, references and inspiration. This overlay has been adapted and developed specifically for Monster Hunter Frontier Forward.5, with its own memory-reading implementation, size detection, crown display, configuration system and other features.

All credit for the original work belongs to its respective authors.

