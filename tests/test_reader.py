"""Reader regression tests on real HUD crops (fixtures: MIT, see THIRD_PARTY.md)."""
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from phosphor.reader import parse_exp, read_exp, read_meso  # noqa: E402

FX = Path(__file__).parent / "fixtures"


def load(name):
    return np.asarray(Image.open(FX / name).convert("RGB"))


EXP_CASES = {
    "exp_win-1024x768.png": (68_601, 71.68),
    "exp_win-1280x720.png": (68_601, 71.68),
    "exp_win-1920x1080.png": (68_601, 71.68),
    "exp_win-1920x1200.png": (68_601, 71.68),
    "exp_full-1280x720.png": (68_601, 71.68),
    "exp_full-1920x1080.png": (68_601, 71.68),
    "filtered_exp-1920x1080.png": (14_503, 6.03),
}


def test_exp_fixtures():
    for name, want in EXP_CASES.items():
        r, _ = read_exp(load(name))
        assert r is not None, name
        assert (r.exp, r.pct) == want, (name, r)


def test_exp_hint_is_fast_and_stable():
    img = load("exp_win-1920x1080.png")
    r, hint = read_exp(img)
    t = time.perf_counter()
    for _ in range(20):
        r2, hint2 = read_exp(img, hint)
    per = (time.perf_counter() - t) / 20
    assert (r2.exp, r2.pct) == (r.exp, r.pct)
    assert hint2 == hint
    assert per < 0.05, per


def test_parse_exp_variants():
    assert parse_exp("68,601 [71.68%]") == (68_601, 71.68)
    assert parse_exp("1 4,503 [6.03%]") == (14_503, 6.03)
    assert parse_exp("221 ,609 75.32%]") == (221_609, 75.32)     # faint '['
    assert parse_exp("68.601 [71 .68%]") == (68_601, 71.68)      # comma read as period
    assert parse_exp("68,60 [71.68%]") is None                    # bad grouping
    assert parse_exp("[71.68%]") is None


def test_meso_fixtures():
    for name in ("inventory_gh7.png", "inventory_gh11.png", "inventory_gh28.png"):
        img = load(name)
        assert read_meso(img)[0] == 18_047, name                      # whole inventory
        assert read_meso(img[int(img.shape[0] * 0.8):])[0] == 18_047, name  # loose box


def test_blank_regions_read_nothing():
    assert read_exp(np.zeros((30, 120, 3), np.uint8))[0] is None
    assert read_meso(np.full((20, 120, 3), 255, np.uint8))[0] is None
    noise = np.random.default_rng(1).integers(0, 255, (30, 150, 3), dtype=np.uint8)
    assert read_exp(noise)[0] is None
