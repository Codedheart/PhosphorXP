# Phosphor XP

A small always-on-top EXP/hour overlay for **MapleStory Worlds (Artale / Classic)**, with a
CRT-terminal look and 18 color palettes. It reads the EXP numbers and the inventory meso count off
your screen, the same way a screen recorder would.

```
PHOSPHOR-XP █              ● 00:20:04
EXP / HOUR                  9.30% LV/H
1,200,300                     5M 1.26M
                             10M 1.22M
GAIN 67,003 (70.0%)             LV +1
NEXT LV 00:06:09              @71.68%
MESO/H +45.2K                NET 15.1K
> EXP 68,601 [71.68%]
```

## Download (no Python needed)

**[⬇ Download the latest version](../../releases/latest)**, then grab **`PhosphorXP-windows.zip`**
under *Assets*.

1. Right-click the zip, choose **Extract All…**, then **Extract**. Don't run it from inside the zip,
   and keep the folder together: `PhosphorXP.exe` needs the `_internal` folder next to it.
2. Double-click **`PhosphorXP.exe`**. If Windows says "Windows protected your PC", click
   **More info**, then **Run anyway**. Windows shows this for small apps that aren't code-signed.
3. Run the game in **windowed or borderless** mode. Exclusive fullscreen hides overlays and can
   make screen grabs come back black.
4. Right-click the overlay and choose **Select EXP region…**. You'll see a frozen screenshot of the
   game: drag a box around the numbers after `EXP.` (for example `68,601 [71.68%]`). The status
   line confirms what it read.
5. (Optional) Open your inventory, then choose **Select meso region…** and box the white meso field.
   Mesos only update while the inventory is open, so open it now and then.

Regions are saved relative to the game window, so you can move the game without redoing them.
If you change the game's resolution or UI size, select them again. All settings are saved in
`%APPDATA%\PhosphorXP` and carry over to new versions.

## Controls

| | |
|---|---|
| Drag | Move the overlay |
| Double-click | Switch between full and compact view |
| Right-click | Menu: pause, reset, regions, **What it sees…**, **Test level-up animation**, opacity, auto-pause, **Color palette** (18 themes, each shown in its own colors) |
| `Ctrl+Shift+F6` | Pause / resume (EXP gained while paused isn't counted) |
| `Ctrl+Shift+F7` | Reset the session (the old one is saved to `history.csv`) |
| `Ctrl+Shift+F8` | Click-through on/off. While it's on, mouse clicks pass through to the game, so use this key to turn it off again |
| `Ctrl+Shift+F9` | Compact view |

Settings, `history.csv` and `error.log` are kept in `%APPDATA%\PhosphorXP\`. You can change the
hotkeys in `config.json`.

## What the numbers mean

- **EXP / HOUR**: net EXP gained since the session started, divided by active (unpaused) time.
  Deaths count as losses.
- **5M / 10M** (right of the big number): EXP/hour over just the last 5 or 10 minutes.
- **% LV/H**: the hourly rate as a percentage of your current level.
- **NEXT LV**: time to level, based on your session EXP/HOUR.
- **MESO/H**: net change in your meso balance per hour, so shop spending counts against it.
- **Over 1,000,000 EXP:** once you have a million EXP in a level, the HUD shows only the percentage.
  Phosphor XP keeps tracking by converting that percentage to EXP with the level's EXP table. It
  learns your level the first time it sees the EXP number, follows your level-ups and remembers the
  level between sessions. If the first time you use it you're already past 1M, right-click and
  choose **Set level…** once. The overlay shows the level it's using next to your percentage
  (e.g. `LV62 @71.68%`).
- **Auto-pause when no EXP** (off by default): stops the clock after N seconds without EXP and removes the idle time past that point,
  so town trips don't drag your rate down.

### How it avoids misreads

The HUD shows EXP *within the level* plus a percentage. Each reading gives a narrow range for the
EXP your level needs (`exp ÷ pct`), and every reading on the same level has to agree with that
range. This means:

- Garbled reads are thrown out.
- Level-ups are detected without an EXP table: the gain is `(level total − old EXP) + new EXP`.
  A drop of 15+ percentage points (e.g. 98% → 0.4%) is always treated as a level-up, since dying
  costs far less than that. Each level-up plays a short CRT celebration on the overlay.
- Anything unusual (a level-up, an EXP drop, a big jump, a character switch) has to show up in two
  readings in a row before it counts.
- Mesos also have to be read the same twice in a row before they count.

The reader doesn't use an OCR engine. It matches the game's own pixel font after scaling the crop
back to its native size, and it handles crisp 1:1 and smoothly scaled UI (tested at 1.0×–3.9×).

## Anti-cheat notes

Phosphor XP only does what screen recorders, OBS and Discord overlays do:

- **Screen grab** of two small rectangles once or twice a second (GDI BitBlt via `mss`).
- **Finding the game window** by its title, to know where those rectangles are. It uses the same
  window-list calls Alt-Tab uses.
- **Its own window**: always-on-top, no taskbar button, doesn't take focus, optional click-through,
  optionally hidden from screen capture (`SetWindowDisplayAffinity`). Hiding it from capture is
  what lets it sit on top of the HUD without blocking its own reads. It also keeps it out of
  OBS/screenshots, so turn that off in the menu if you want it on stream.
- **Global hotkeys** through `RegisterHotKey`, the same mechanism media keys and Discord push-to-talk
  use.

It never opens the game process, reads or writes game memory or files, injects DLLs, hooks
DirectX or input, or sends keystrokes or clicks. It doesn't need admin rights, so don't run it as
admin. It makes no network connections.

There's no guarantee about how Nexon treats any third-party program. Read-only screen trackers
like this are widely used, but using one is still your call under the game's terms of service.

## Development

**Run from source** (needs Python 3.10+ from python.org): double-click **`run.bat`**. The first run
sets up a `.venv`, installs the dependencies and fetches the VT323 font.

**Tests:**

```
py -3 -m pip install -r requirements.txt pytest
py -3 -m pytest tests
```

**Making a release:** the download is built on GitHub's servers by
`.github/workflows/release.yml`. Push a version tag and it builds the `.exe`, runs the tests and
publishes a Release with `PhosphorXP-windows.zip` attached:

```
git tag v1.0
git push origin v1.0
```

In GitHub Desktop: **History**, right-click the commit, **Create Tag…** (e.g. `v1.0`), then
**Push origin**. To test a build without publishing, go to **Actions**, open **Build Windows
release** and click **Run workflow**. The zip appears on the run's page.

To build the same zip locally instead, double-click **`build.bat`**.

- `phosphor/reader.py`: pixel-font reader (numpy only).
- `phosphor/stats.py`: EXP and meso bookkeeping.
- `phosphor/engine.py`: capture thread.
- `phosphor/winapi.py`: user32 helpers.
- `phosphor/ui.py`: the Tk overlay.

The reader tests use real HUD crops (see THIRD_PARTY.md).
