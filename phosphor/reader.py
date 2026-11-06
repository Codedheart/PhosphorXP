"""Read the EXP readout and inventory mesos from screen pixels.

Pipeline (pure numpy, no OCR engine):
 1. Turn the crop into ink maps: a main channel (white EXP digits / dark meso
    digits) and an accent channel (the green EXP brackets).
 2. Find the text band and estimate the game's UI scale from its height.
 3. Resample the crop back onto the font's native pixel grid (area average).
 4. Decode the line with dynamic programming: every native column is either
    part of a glyph template or a blank column; the cheapest explanation wins.
 5. Parse the string and sanity-check it.

The best (scale, phase) found for a region is cached in a ``Hint`` so a normal
frame costs a single decode (a few ms).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np

from .fonts import EXP_FONT, MESO_FONT, Font

ACCENT_GLYPHS = {"[", "]"}


# ---------------------------------------------------------------- resampling
def area_matrix(src_len: int, start: float, step: float, n: int) -> np.ndarray:
    """(n, src_len) matrix averaging source pixels over [start+i*step, start+(i+1)*step)."""
    edges = start + step * np.arange(n + 1)
    lo = np.clip(edges[:-1, None], 0, src_len)
    hi = np.clip(edges[1:, None], 0, src_len)
    j = np.arange(src_len)[None, :]
    overlap = np.clip(np.minimum(hi, j + 1) - np.maximum(lo, j), 0, None)
    return (overlap / step).astype(np.float32)


def resample(ink: np.ndarray, x0: float, y0: float, s: float, rows: int) -> np.ndarray:
    """Resample (C, H, W) ink onto a native grid of ``rows`` rows starting at (x0, y0)."""
    _, h, w = ink.shape
    cols = int((w - x0) // s)
    if cols < 4:
        return np.zeros((ink.shape[0], rows, 0), np.float32)
    ay = area_matrix(h, y0, s, rows)
    ax = area_matrix(w, x0, s, cols)
    return np.stack([ay @ c @ ax.T for c in ink])


# ------------------------------------------------------------------ decoding
# After a bilinear UI upscale and our area downsample, a 1 px stroke spreads to
# roughly this profile on the native grid (measured for scales 1.36-2.8).
_KERNELS = {0: None, 1: np.array([0.14, 0.72, 0.14], np.float32)}
_tmpl_cache: dict[tuple[str, int], dict[str, np.ndarray]] = {}


def _templates(font: Font, blur: int) -> dict[str, np.ndarray]:
    key = (font.name, blur)
    if key not in _tmpl_cache:
        k = _KERNELS[blur]
        out = {}
        for ch, t in font.glyphs.items():
            if k is not None:
                p = np.pad(t, 1)
                p = np.apply_along_axis(lambda v: np.convolve(v, k, mode="same"), 0, p)
                p = np.apply_along_axis(lambda v: np.convolve(v, k, mode="same"), 1, p)
                t = p[1:-1, 1:-1].astype(np.float32)
            out[ch] = t
        _tmpl_cache[key] = out
    return _tmpl_cache[key]


def decode(native: np.ndarray, font: Font, label_before: int | None = None,
           blur: int = 0) -> tuple[str, float]:
    """Best glyph/blank segmentation of a (C, rows, W) native line. Returns (text, cost)."""
    _, rows, W = native.shape
    if W == 0:
        return "", float("inf")
    n = np.clip(native, 0.0, 1.0)
    main = n[0]
    acc = n[1] if n.shape[0] > 1 else np.zeros_like(main)

    # Anything taller than the digits (the bold "EXP." label) sits left of the
    # number: blank it and everything before it.
    if label_before is not None and font.digit_top > 0:
        tall = np.where(main[:font.digit_top].max(axis=0) > 0.6)[0]
        tall = tall[tall < label_before]
        if len(tall):
            main = main.copy()
            main[:, :tall[-1] + 1] = 0

    m2, a2 = main ** 2, acc ** 2
    blank = m2.sum(axis=0) + a2.sum(axis=0)

    glyphs = _templates(font, blur)
    gcost: dict[str, np.ndarray] = {}
    for ch, t in glyphs.items():
        wg = t.shape[1]
        if wg > W:
            continue
        if ch in ACCENT_GLYPHS:
            on, off = acc, m2
        else:
            on, off = main, a2
        win = np.lib.stride_tricks.sliding_window_view(on, (rows, wg))[0]
        offw = np.lib.stride_tricks.sliding_window_view(off, (rows, wg))[0]
        gcost[ch] = ((win - t) ** 2).sum(axis=(1, 2)) + offw.sum(axis=(1, 2))

    best = np.full(W + 1, np.inf)
    best[0] = 0.0
    back: list[tuple[int, str | None]] = [(0, None)] * (W + 1)
    widths = {ch: glyphs[ch].shape[1] for ch in gcost}
    for x in range(1, W + 1):
        b = best[x - 1] + blank[x - 1]
        bp: tuple[int, str | None] = (x - 1, None)
        for ch, c in gcost.items():
            wg = widths[ch]
            if wg <= x:
                v = best[x - wg] + c[x - wg]
                if v < b:
                    b, bp = v, (x - wg, ch)
        best[x], back[x] = b, bp

    out: list[str] = []
    x, gap = W, 0
    while x > 0:
        px, ch = back[x]
        if ch is None:
            gap += 1
        else:
            if gap >= 3 and out:
                out.append(" ")
            gap = 0
            out.append(ch)
        x = px
    text = "".join(reversed(out)).strip()
    ink_px = max(1.0, float(((main > 0.3) | (acc > 0.3)).sum()))
    return text, float(best[W]) / ink_px


@dataclass
class Hint:
    """Cached geometry for a region: native grid origin, scale and blur model."""
    x0: float
    y0: float
    s: float
    blur: int = 0


def _band(profile: np.ndarray, frac: float = 0.15) -> tuple[int, int] | None:
    """Rows of the text line: the run of inked rows carrying the most ink."""
    if profile.size == 0 or profile.max() <= 0:
        return None
    on = np.where(profile >= frac * profile.max())[0]
    runs = np.split(on, np.where(np.diff(on) > 1)[0] + 1)
    run = max(runs, key=lambda r: float(profile[r].sum()))
    return int(run[0]), int(run[-1])


def read_line(ink: np.ndarray, font: Font, band_profile: np.ndarray,
              hint: Hint | None = None, accept=None,
              label_before_px: float | None = None) -> tuple[str, float, Hint | None]:
    """Decode a line of text. ``accept(text)`` says whether a result parses.

    Tries the cached hint first, then searches scale, sub-pixel phase and blur.
    """
    def lb(h: Hint) -> int | None:
        return None if label_before_px is None else int((label_before_px - h.x0) / h.s)

    if hint is not None:
        t, c = decode(resample(ink, hint.x0, hint.y0, hint.s, font.rows), font, lb(hint), hint.blur)
        if accept is None or accept(t):
            return t, c, hint

    b = _band(band_profile)
    if b is None:
        return "", float("inf"), None
    top, bot = b
    bh = bot - top + 1
    centre = (top + bot + 1) / 2.0

    best: tuple[float, float, str, Hint] | None = None
    for dh in np.arange(-1.5, 1.6, 0.25):
        s = (bh + dh) / font.digit_height
        if s < 0.9:
            continue
        blurs = (0,) if abs(s - 1.0) < 0.03 else (0, 1)
        for yo in (-0.25, 0.0, 0.25):
            y0 = centre - font.digit_height * s / 2 + yo * s - font.digit_top * s
            for k in range(3):
                nat = resample(ink, k * s / 3, y0, s, font.rows)
                for blur in blurs:
                    h = Hint(x0=k * s / 3, y0=y0, s=float(s), blur=blur)
                    t, c = decode(nat, font, lb(h), blur)
                    ok = accept is None or accept(t)
                    score = c + (0 if ok else 10.0)
                    if best is None or score < best[0]:
                        best = (score, c, t, h)
    if best is None:
        return "", float("inf"), None
    _, c, t, h = best
    return t, c, h


# ------------------------------------------------------------------ EXP bar
_STRICT_RE = re.compile(r"(\d[\d,.]*)\[(\d{1,3})\.(\d{2})%\]?")
_LOOSE_RE = re.compile(r"(\d[\d,. ]*?)\s+(\d{1,3})\.(\d{2})%\]")


@dataclass
class ExpReading:
    exp: int             # EXP into the current level, as displayed
    pct: float           # 0..100, as displayed (2 decimals)
    text: str
    cost: float


def _parse_int(s: str) -> int | None:
    s = s.replace(".", ",").strip(",")
    if not s:
        return None
    if "," in s and not re.fullmatch(r"\d{1,3}(,\d{3})+", s):
        return None
    return int(s.replace(",", ""))


def parse_exp(text: str) -> tuple[int, float] | None:
    """'68,601 [71.68%]' -> (68601, 71.68). Tolerates split digits and a faint '['."""
    t = re.sub(r"\s*([,.%])\s*", r"\1", text)
    ms = list(_STRICT_RE.finditer(t.replace(" ", ""))) or list(_LOOSE_RE.finditer(t))
    if not ms:
        return None
    m = ms[-1]
    tok = m.group(1).replace(" ", "")
    val = None
    lead = re.match(r"\d*", tok).end()  # label debris can only precede the first group
    for i in range(lead):               # longest valid tail
        val = _parse_int(tok[i:])
        if val is not None:
            break
    if val is None:
        return None
    pct = int(m.group(2)) + int(m.group(3)) / 100.0
    if pct > 100.0:
        return None
    return val, pct


def exp_ink(rgb: np.ndarray) -> np.ndarray:
    """(2, H, W) ink: [white text, green brackets], each 0..1."""
    f = rgb.astype(np.float32)
    r, g, b = f[..., 0], f[..., 1], f[..., 2]
    green = np.clip((g - b - 25.0) / 60.0, 0, 1) * ((g - r) > 12) * (g > 110)
    # the EXP gauge fill under the text is green too: drop rows that are mostly green
    green[(green > 0.5).mean(axis=1) > 0.2] = 0
    mn = f.min(axis=2)
    lo = float(np.median(mn))
    hi = max(lo + 40.0, float(np.percentile(mn, 99.5)))
    white = np.clip((mn - lo) / (hi - lo), 0, 1) * (1.0 - green)
    # window bevels/borders are long bright horizontal lines; text never is
    if white.shape[1] > 30:
        white[(white > 0.25).mean(axis=1) > 0.5] = 0
    return np.stack([white, green]).astype(np.float32)


def read_exp(rgb: np.ndarray, hint: Hint | None = None) -> tuple[ExpReading | None, Hint | None]:
    ink = exp_ink(rgb)
    white, green = ink
    cols = np.where(green.sum(axis=0) >= 1.5)[0]
    label_before = None
    clusters = np.split(cols, np.where(np.diff(cols) > 2)[0] + 1) if len(cols) else []
    if len(clusters) >= 2 and clusters[-1][0] - clusters[-2][-1] > 6:
        # last two green clusters are "[" and "]" around the percentage; they
        # span exactly the digit rows, and the label lies left of "["
        lo, hi = int(clusters[-2][0]), int(clusters[-1][-1])
        g = np.zeros_like(green)
        g[:, lo:hi + 1] = green[:, lo:hi + 1]
        band = g.sum(axis=1)
        label_before = float(lo)
    else:
        band = white.sum(axis=1)
    text, cost, h = read_line(ink, EXP_FONT, band, hint=hint,
                              accept=lambda t: parse_exp(t) is not None,
                              label_before_px=label_before)
    p = parse_exp(text)
    if p is None or cost > 0.6:
        return None, hint
    return ExpReading(p[0], p[1], text, cost), h


# ------------------------------------------------------------------- mesos
def _inner_box(rgb: np.ndarray) -> tuple[slice, slice]:
    """Rows/cols of the white meso text box inside a loose selection."""
    bright = rgb.min(axis=2) > 200
    rows = np.where(bright.mean(axis=1) > 0.5)[0]
    if len(rows) == 0:
        return slice(0, 0), slice(0, 0)
    runs = np.split(rows, np.where(np.diff(rows) > 1)[0] + 1)
    tall = [r for r in runs if len(r) >= 6]
    # the meso box sits at the very bottom of the inventory window
    run = tall[-1] if tall else max(runs, key=len)
    r0, r1 = int(run[0]), int(run[-1])
    cols = np.where(bright[r0:r1 + 1].mean(axis=0) > 0.3)[0]
    if len(cols) == 0:
        return slice(r0, r1 + 1), slice(None)
    cruns = np.split(cols, np.where(np.diff(cols) > 1)[0] + 1)
    c = max(cruns, key=len)                  # widest bright span = the text box
    return slice(r0, r1 + 1), slice(int(c[0]), int(c[-1]) + 1)


_MESO_RE = re.compile(r"\d{1,3}(,\d{3})*")


def read_meso(rgb: np.ndarray, hint: Hint | None = None) -> tuple[int | None, Hint | None]:
    rs, cs = _inner_box(rgb)
    box = rgb[rs, cs].astype(np.float32)
    if box.size == 0 or box.shape[0] < 6:
        return None, hint
    mx = box.max(axis=2)
    hi = float(np.median(mx))
    lo = float(mx.min())
    if hi - lo < 60:
        return None, hint
    ink = np.clip((hi - mx) / (hi - lo), 0, 1)
    # the box's own border/shadow lines are not text
    ink[(ink > 0.4).mean(axis=1) > 0.5] = 0
    ink[:, (ink > 0.4).mean(axis=0) > 0.8] = 0
    ink = ink[None]

    def ok(t: str) -> bool:
        return bool(_MESO_RE.fullmatch(t.replace(" ", "")))

    text, cost, h = read_line(ink, MESO_FONT, ink[0].sum(axis=1), hint=hint, accept=ok)
    t = text.replace(" ", "")
    if not ok(t) or cost > 0.6:
        return None, hint
    return int(t.replace(",", "")), h
