"""Pixel-font templates for the MapleStory Worlds (Classic) HUD.

The game draws its EXP readout and inventory meso count with tiny bitmap
fonts. At UI scale 1.0 every glyph is a crisp pattern of whole pixels, so we
can read them by template matching instead of a general OCR engine.

Glyph shapes were transcribed from the MIT-licensed "Maple Classic Exp
Overlay" project (github.com/beanguri/Maple-Classic-Exp-Overlay); see
THIRD_PARTY.md.

Each font is laid out on a fixed grid of ``rows`` native pixel rows. A glyph's
template is ``rows`` tall and as wide as its ink extent (including any leading
blank columns, which are part of the glyph's spacing). Blank columns between
glyphs are matched separately by the reader.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np


@dataclass
class Font:
    name: str
    rows: int            # template grid height (native pixels)
    digit_top: int       # grid row where the top of a digit sits
    digit_height: int    # height of a digit in native pixels
    glyphs: dict[str, np.ndarray] = field(default_factory=dict)


def _grid(rows: int, top: int, pattern: list[str], lead: int = 0) -> np.ndarray:
    """Place a '#'/'X' pattern into a rows-tall float template."""
    w = lead + max(len(r) for r in pattern)
    t = np.zeros((rows, w), dtype=np.float32)
    for i, line in enumerate(pattern):
        for j, ch in enumerate(line):
            if ch in "#X":
                t[top + i, lead + j] = 1.0
    return t


# ---------------------------------------------------------------- EXP readout
# Digits are 7 px tall. Grid: 1 margin row, 7 digit rows, 2 rows below.
_EXP_ROWS, _EXP_TOP = 10, 1
_EXP_DIGITS = {
    "0": [".###.", "#...#", "#...#", "#...#", "#...#", "#...#", ".###."],
    "1": [".#", "##", ".#", ".#", ".#", ".#", ".#"],
    "2": [".###.", "#...#", "....#", "...#.", "..#..", ".#...", "#####"],
    "3": [".###.", "#...#", "....#", "..##.", "....#", "#...#", ".###."],
    "4": ["...#.", "..##.", ".#.#.", "#..#.", "#####", "...#.", "...#."],
    "5": ["#####", "#....", "#....", ".###.", "....#", "#...#", ".###."],
    "6": [".###.", "#...#", "#....", "####.", "#...#", "#...#", ".###."],
    "7": ["#####", "....#", "....#", "....#", "...#.", "..#..", "..#.."],
    "8": [".###.", "#...#", "#...#", ".###.", "#...#", "#...#", ".###."],
    "9": [".###.", "#...#", "#...#", ".####", "....#", "#...#", ".###."],
    "%": [".#....#", "#.#..#.", ".#..#..", "...#...", "..#..#.", ".#..#.#", "#....#."],
    "[": ["##", "#.", "#.", "#.", "#.", "#.", "##"],
    "]": ["##", ".#", ".#", ".#", ".#", ".#", "##"],
}


def _exp_font() -> Font:
    f = Font("exp", _EXP_ROWS, _EXP_TOP, 7)
    for ch, pat in _EXP_DIGITS.items():
        lead = 1 if ch == "1" else 0
        f.glyphs[ch] = _grid(_EXP_ROWS, _EXP_TOP, pat, lead)
    # punctuation sits on the baseline (grid row 7)
    f.glyphs[","] = _grid(_EXP_ROWS, _EXP_TOP + 5, [".#", "#."], lead=1)
    f.glyphs["."] = _grid(_EXP_ROWS, _EXP_TOP + 6, ["#"], lead=1)
    return f


# ---------------------------------------------------------- inventory mesos
# Digits are 8 px tall; the comma hangs 2 px below the baseline.
_MESO_ROWS, _MESO_TOP = 11, 1
_MESO_DIGITS = {
    "0": [".XXX.", "X...X", "X...X", "X...X", "X...X", "X...X", "X...X", ".XXX."],
    "1": [".X", "XX", ".X", ".X", ".X", ".X", ".X", "XXX"],
    "2": [".XXX.", "X...X", "....X", "...X.", "..X..", ".X...", "X....", "XXXXX"],
    "3": [".XXX.", "X...X", "....X", "..XX.", "....X", "....X", "X...X", ".XXX."],
    "4": ["...X.", "..XX.", ".X.X.", "X..X.", "XXXXX", "...X.", "...X.", "...X."],
    "5": ["XXXXX", "X....", "X....", "XXXX.", "....X", "....X", "X...X", ".XXX."],
    "6": ["..XX.", ".X...", "X....", "XXXX.", "X...X", "X...X", "X...X", ".XXX."],
    "7": ["XXXXX", "....X", "...X.", "...X.", "..X..", "..X..", ".X...", ".X..."],
    "8": [".XXX.", "X...X", "X...X", ".XXX.", "X...X", "X...X", "X...X", ".XXX."],
    "9": [".XXX.", "X...X", "X...X", "X...X", ".XXXX", "....X", "...X.", ".XX.."],
}


def _meso_font() -> Font:
    f = Font("meso", _MESO_ROWS, _MESO_TOP, 8)
    for ch, pat in _MESO_DIGITS.items():
        lead = 1 if ch == "1" else 0
        f.glyphs[ch] = _grid(_MESO_ROWS, _MESO_TOP, pat, lead)
    f.glyphs[","] = _grid(_MESO_ROWS, _MESO_TOP + 6, [".X", ".X", ".X", "X."], lead=1)
    return f


EXP_FONT = _exp_font()
MESO_FONT = _meso_font()
