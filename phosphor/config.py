"""Settings persisted as JSON in %APPDATA%\\PhosphorXP (or ~/.phosphorxp)."""
from __future__ import annotations

import json
import os
from pathlib import Path

APP_NAME = "Phosphor XP"


def data_dir() -> Path:
    base = os.environ.get("PHOSPHOR_DATA")
    if base:
        p = Path(base)
    elif os.name == "nt":
        p = Path(os.environ.get("APPDATA", Path.home())) / "PhosphorXP"
    else:
        p = Path.home() / ".phosphorxp"
    p.mkdir(parents=True, exist_ok=True)
    return p


DEFAULTS: dict = {
    # Regions: {"anchor": "client"|"screen", "x":, "y":, "w":, "h":}
    # "client" coordinates are relative to the game window's client area, so
    # moving the game window doesn't break them.
    "exp_region": None,
    "meso_region": None,
    "window_titles": ["MapleStory Worlds", "MapleStory"],
    "x": 80,
    "y": 80,
    "opacity": 0.9,
    "compact": False,
    "click_through": False,
    "hide_from_capture": True,
    "idle_pause_sec": 0,          # 0 = off; otherwise auto-pause after this long with no EXP
    "exp_interval": 1.0,          # seconds between EXP reads
    "meso_interval": 2.0,
    "level": None,                # learned from the HUD; needed when it shows % only
    "theme": "green",             # see phosphor/themes.py
    "hotkeys": {
        "pause": "ctrl+shift+F6",
        "reset": "ctrl+shift+F7",
        "clickthrough": "ctrl+shift+F8",
        "compact": "ctrl+shift+F9",
    },
}


class Config(dict):
    def __init__(self) -> None:
        super().__init__(json.loads(json.dumps(DEFAULTS)))
        self.path = data_dir() / "config.json"
        try:
            saved = json.loads(self.path.read_text(encoding="utf-8"))
            for k, v in saved.items():
                if k == "hotkeys" and isinstance(v, dict):
                    self["hotkeys"].update(v)
                elif k in DEFAULTS:
                    self[k] = v
        except (OSError, ValueError):
            pass

    def save(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self, indent=2), encoding="utf-8")
        tmp.replace(self.path)


def append_history(row: dict) -> None:
    """One line per finished session in history.csv."""
    path = data_dir() / "history.csv"
    cols = ["ended", "duration_min", "exp_gained", "exp_per_hr", "levels", "meso_net", "meso_per_hr"]
    new = not path.exists()
    with path.open("a", encoding="utf-8") as f:
        if new:
            f.write(",".join(cols) + "\n")
        f.write(",".join(str(row.get(c, "")) for c in cols) + "\n")
