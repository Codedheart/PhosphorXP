import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_settings_survive_restart():
    os.environ["PHOSPHOR_DATA"] = tempfile.mkdtemp()
    from phosphor.config import Config
    c = Config()
    c["theme"] = "cotton"
    c["opacity"] = 0.65
    c["idle_pause_sec"] = 10
    c["compact"] = True
    c["hide_from_capture"] = False
    c["exp_region"] = {"anchor": "client", "x": 1, "y": 2, "w": 3, "h": 4}
    c["hotkeys"]["pause"] = "ctrl+shift+F1"
    c.save()
    d = Config()
    for k in ("theme", "opacity", "idle_pause_sec", "compact", "hide_from_capture", "exp_region"):
        assert d[k] == c[k], k
    assert d["hotkeys"]["pause"] == "ctrl+shift+F1"
    assert d["hotkeys"]["reset"]          # defaults still filled in


def test_all_palettes_are_dark():
    from phosphor.themes import PALETTES

    def lum(h):
        r, g, b = (int(h[i:i + 2], 16) / 255 for i in (1, 3, 5))
        return 0.2126 * r + 0.7152 * g + 0.0722 * b

    for key, _name, _grp, col in PALETTES:
        assert lum(col["bg"]) < 0.12, key
        assert lum(col["fg"]) - lum(col["bg"]) > 0.35, key     # readable text
