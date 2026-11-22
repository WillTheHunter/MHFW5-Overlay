# F5 HP / Size overlay

Shows the HP (current, max, %) and the size (%) of the monsters in your Monster Hunter Frontier Forward.5 quest, with
Small / Silver / Gold next to the name when the size is inside the crown range. Small monsters are shown too.
No extra packages: only Python 3 for Windows (tkinter comes with it).

## Start
1. Start the game in **windowed or borderless** mode (the overlay is a normal window on top of the game, it cannot
   draw over exclusive fullscreen).
2. Double-click `f5_overlay.py` (or run `python f5_overlay.py`). If it is not running as Administrator it asks Windows for
   it (one UAC prompt) and restarts itself in a new window, because the game may run as Administrator and Windows then
   refuses the memory access otherwise. If you press No it continues without. Turn this off with `run_as_admin = false`.
3. A black console window opens with the menu and stays open while it runs. The overlay appears when you are in a quest.
4. To quit: press **Ctrl+C** in that window (it closes cleanly), type `q` + Enter, or just close the window.

On the first run `f5_overlay.ini` is created next to the script with all defaults. If you have an ini from an older
version it is renamed to `f5_overlay.ini.old` and a new one is created.

Command line: `--console` (print what is read, no overlay), `--once` / `--count N` (print and exit), `--demo` (fake
monsters, no game needed), `--style lvdew|alexander`, `--config path\Config.txt`, `--log [file]`, `--debug`.

## The console menu
A text-art title is shown at the top (`bn` turns it off). Type a key and press Enter. Two views:
* **Pages** (default): 5 pages of at most 10 commands. `>` and `<` change page, `1`-`5` jump to a page.
* **All options** in one list: type `0` (or `all`). Type `0` again (or `pages`, `>`, `<`, a page number) to go back to pages.
  The view is saved as `menu_mode` in the ini.

The console is cleared after every command (`cl` turns that off), so there is never anything to scroll. Numbers and
colours are typed as key + value: `f 12`, `x 80`, `tc gold`, `bc #00ff00`, `op 80`. `s` writes everything to the ini,
`q` quits. Ctrl+C or closing the window also quits. A `-` in the list means the option does not exist in the style in use
(for example the bar options while the style is alexander).

**Page 1 - Display**
| key | what |
|---|---|
| o | overlay on / off |
| m | small monsters |
| z | monster size % |
| c | crown (Small / Silver / Gold) |
| p | HP percentage |
| h | max HP |
| k | capture range highlight |
| d | hide dead small monsters |
| t | switch style (lvdew / alexander) |
| v | preview with fake monsters, handy to place the overlay without being in a quest |

**Page 2 - Look and position**
| key | what |
|---|---|
| f N | text size of the current style (lvdew points, alexander pixels) |
| w | position preset: custom, left-center (Lvdew's original place), top-left, top-right, bottom-left, bottom-right, right-center |
| x N, y N | custom position in % of the game window (x 100 = right edge, y 0 = top); typing one switches the preset to custom |
| r | lvdew: HP bar on / off |
| l | lvdew: bar position (below / above / left / right) |
| n | lvdew: HP numbers (below / inline / off) |
| e | lvdew: bar mode (simple / capture) |
| b N, g N | lvdew: bar width, bar height (pixels) |

**Page 3 - Colours** (a name such as `red`, `gold`, `darkslategray`, or `#rrggbb`; an unknown name keeps the old look and shows a warning)
| key | what |
|---|---|
| tc | text colour |
| tcc | text colour in the capture range |
| bg | box colour (alexander: the boxes; lvdew: the optional box behind each monster) |
| bgc | alexander: box colour in the capture range |
| bc, bcc, bb | lvdew: HP bar colour, bar colour in the capture range, empty part of the bar |
| sc, fc | lvdew: text shadow colour, frame colour |

**Page 4 - Opacity** (0-100)
| key | what |
|---|---|
| op | whole overlay (also the only way to make the text itself see-through) |
| bgo | boxes (lvdew: the optional box, 0 = none) |
| bgco | alexander: boxes in the capture range |
| bo, bbo | lvdew: filled part / empty part of the HP bar |
| fo | lvdew: frame |

**Page 5 - When to show / system**
| key | what |
|---|---|
| j | hide the overlay outside quests (town) |
| a | only show while the game window is the active window |
| sh | hide while taking screenshots (off by default) |
| cl | clear the console after every command |
| bn | text art title |
| ad | ask for Administrator rights when started (takes effect at the next start) |
| i | write a log file |
| u N | read/redraw interval in milliseconds |

## Styles
* **lvdew** (default): Lvdew's look. Per monster: `Name  [size%] Crown`, the HP numbers `1600/1600  (100%)` and a small
  bar. The text and bar turn yellow while the monster is inside its capture range (the range table from Lvdew's
  Config.txt). Text has a black shadow so it stays readable.
* **alexander**: stacked translucent boxes like MH-HP-Overlay-For-PSP-Emulator:
  `(115%) Velocidrome Silver: 100% | 1600 | 1600`. The box uses the capture colours in the capture range.

## f5_overlay.ini
Everything in the file is commented. Wrong values are ignored and the default is used.

**[General]**
| key | default | meaning |
|---|---|---|
| style | lvdew | `lvdew` or `alexander` |
| update_time | 0.03 | seconds between memory reads and redraws (0.03 = about 30 per second, minimum 0.01) |
| show_small_monsters | true | also list small monsters, after the large ones |
| hide_dead_small_monsters | true | hide small monsters at 0 HP |
| show_size | true | show the quest size of large monsters |
| show_crown | true | Small / Silver / Gold next to the name |
| show_hp_percentage | true | HP percentage |
| show_initial_hp | true | maximum HP next to the current HP |
| highlight_capturable | true | yellow (lvdew) / capture colours (alexander) inside the capture range |
| low_hp_percent | 0 | also highlight large monsters **without** a capture range below this percent (0 = never) |
| only_in_quest | true | hide the overlay outside quests. See "Town" below |
| only_when_focused | false | show the overlay only while the game window (or this console) is the active window |
| hide_on_screenshot | false | hide the overlay when a screenshot key is pressed |
| screenshot_hide_seconds | 2 | how long it stays hidden |
| screenshot_keys | PrintScreen, Alt+PrintScreen, Win+Shift+S | comma separated key combinations (Ctrl, Alt, Shift, Win + PrintScreen, F1-F24, A-Z, 0-9 ...) |
| run_as_admin | true | restart elevated (one UAC prompt) when not started as Administrator |
| clear_screen | true | clear the console after every menu command |
| menu_mode | pages | `pages` or `all` (every option in one list) |
| banner | true | text art title above the menu |

**[Layout]** `preset` (`custom`, `left-center` = Lvdew's original place on the left side centered on the height, `top-left`, `top-right`, `bottom-left`, `bottom-right`, `right-center`); with `custom` the overlay uses `x`, `y` in percent of the game window (100, 0 = top-right corner); `fix_x`, `fix_y` extra pixels added to any position.

**[Lvdew]**
| key | default | meaning |
|---|---|---|
| text_size | 10 | text size in points |
| font_family, font_weight | Segoe UI, bold | font (must be installed), `normal` or `bold` |
| numbers | below | HP numbers: `below` the name, `inline` on the name line, or `off` |
| show_bar | true | draw the HP bar |
| bar_mode | simple | `simple`: whole bar turns yellow in the capture range. `capture`: bar stays green, the capture range part is painted yellow |
| bar_width, bar_height | 240, 8 | bar size in pixels |
| bar_position | below | `below`, `above`, `left` or `right` of the monster's text |
| max_rows | 5 | most monsters shown at once |
| row_gap | 8 | pixels between two monsters |
| shadow | true | black text shadow |
| frame, frame_color | false, #000000 | dark rectangle behind everything |
| text_color, text_capture_color | #FFFFFF, #F0F000 | text colours (normal, in capture range) |
| bar_color, bar_capture_color, bar_back_color | #10D010, #F0F000, #303030 | bar colours |
| opacity | 100 | opacity of the whole overlay, 20-100 |
| shadow_color | #000000 | text shadow colour |
| box_color, box_opacity | #000000, 0 | optional box behind each monster (opacity 0 = none) |
| bar_opacity, bar_back_opacity | 100, 100 | opacity of the filled / empty part of the bar |
| frame_opacity | 100 | opacity of the frame |

**[Alexander]** `font_size` (pixels), `font_family`, `font_weight`, `align` (same width for every box), `orientation`
(left / center / right), `text_color`, `background_color`, `capture_text_color`, `capture_background_color`,
`background_opacity`, `capture_background_opacity` (the boxes, 0-100), `window_opacity` (whole overlay). Colours are names (aquamarine, gold ...) or `#rrggbb`.

**[Debug]** `log` (false), `log_file` (f5_overlay.log, next to the script), `log_level` (`info` or `debug`).
The log is off by default. When on, it records start-up, attaching to the game, status changes, every change of the
monster list (id, size, HP), the window position, hide/show and any error with its traceback. Send it when
something does not work. Switch it on from the menu with `i` or start with `--log`.

**[Crowns]** optional per-monster overrides in percent of normal size: `Velocidrome = 90, 114, 123`
(Small at or below the first, Silver at or above the second, Gold at or above the third).

## Crowns
Thresholds come from SirFist's MHF monster size table. The size read from the game (100 = normal, 115 = 115%) is compared
with them: Gold if size >= gold, Silver if size >= silver, Small if size <= small. Monsters with no crown in that table
(Shen Gaoren, Raviente ...) never get one. The table is for the MHF series, so a few F5 monsters may differ; fix them in
`[Crowns]`. Baruragaru lists silver as 1.00 there, so its silver is ignored (it would trigger at normal size).

## Colours and transparency: what is possible
Every colour can be set (text, capture text, boxes, bar parts, shadow, frame) in both styles. Transparency has a limit
that comes from Windows and tkinter: the **whole overlay** can be faded evenly (`opacity` / `window_opacity`, text
included), but a single item cannot be blended with the game. For boxes, bars and the frame the opacity options therefore do
this: `100` solid, `0` not drawn, and values in between are drawn as a fine **dither** (every other pixel left out, so you see
the game through the gaps). It works but can look grainy; whether it looks good depends on your screen scaling, so try a value
with `bgo` / `bo` and go back to 100 if you do not like it. Text cannot be dithered, so text transparency is only the whole
overlay `op`.

## Town (`only_in_quest`)
In town the game keeps entries in its monster list that are not real quest monsters. I cannot see what exactly is in
there from here, so this option is a guess: the overlay is hidden unless at least one *known large monster* (one from
the monster table) is present, and entries with unknown ids are ignored. If it ever hides the overlay in a real quest, set
`only_in_quest = false` (key `j`). If something odd still shows in town, switch the log on with level `debug`, walk around
town for a few seconds and send me `f5_overlay.log`: it lists the entries the game holds (id, size, HP), so the rule can be made exact.

## Screenshots
The screenshot keys are checked every 8 ms and the overlay is hidden at once for `screenshot_hide_seconds`. It covers
PrintScreen, Alt+PrintScreen and the Windows snipping tool (Win+Shift+S) by default. Add other keys (for example `F12`
for Steam or a capture program) to `screenshot_keys`. A capture that is triggered by something else than a key press
cannot be detected.

## Notes
* Status buildup and KO are not decoded for F5, so they are not shown.
* Capture ranges and the large/small flag come from Lvdew's table (built in; a `Config.txt` next to the script overrides it).
* If it says `error: ...` or does not attach, switch the log on and send me `f5_overlay.log`.
* The overlay position follows the game window. It only works while the game is windowed or borderless.

## Credits & Acknowledgements

This project was made possible in part by the work, research, and ideas of other Monster Hunter community developers. Special thanks to:

- **[Relial - MHFZ Monster Viewer](https://github.com/Relial/mhfz-monster-viewer)**  
  Used as a reference and inspiration for displaying live monster information from Monster Hunter Frontier. The project focuses primarily on hitzone values (HZVs) and identifying monster information during hunts.

- **[Alexander Lancellott - GitHub](https://github.com/Alexander-Lancellott)**  
  Inspiration for the overlay's visual presentation and HP display, particularly the stacked-box style used in the Alexander layout. His work on Monster Hunter HP overlays provided a useful reference for presenting monster information clearly.

- **Lvdew - Lvdew's MHF HP Display (Lvdew.exe, Revive2)**  
  Reference for the original Monster Hunter Frontier HP overlay functionality. This project draws inspiration from Lvdew's HP display, monster data tables, capture-range highlighting, and `Config.txt` configuration format. The accompanying `ReadMe!!.txt` documents the Revive2 version, which supports the Season 8.0 client.

### A note on attribution

These projects are credited for their contributions, references, and inspiration. This overlay has been adapted and developed specifically for Monster Hunter Frontier Forward.5, with its own memory-reading implementation, size detection, crown display, configuration system, and other features.

All credit for the original work belongs to its respective authors.