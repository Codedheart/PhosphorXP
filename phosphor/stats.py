"""EXP and meso bookkeeping.

The HUD shows EXP *within the current level* plus a percentage. From each
reading we can bound the EXP the level requires:

    R in [exp / (pct + 0.01)%, exp / (pct - 0.005)%]

(covers both rounding and truncation of the displayed percentage).

Readings from the same level must agree on R, which gives a free misread
filter and lets us handle level-ups without an EXP table: the EXP gained over
a level-up is (R - last_exp) + new_exp.

Times passed in are *active* seconds (a clock that stops while paused), so
rates never include paused time.
"""
from __future__ import annotations

import bisect
import math
from dataclasses import dataclass, field

INF = float("inf")

# Dying costs at most ~10% of a level, so a bigger drop in the displayed
# percentage can only be a level-up. (Right after a level-up the percentage is
# tiny, which makes the required-EXP range too wide to tell levels apart.)
LEVEL_DROP_PCT = 15.0


def req_interval(exp: int, pct: float) -> tuple[float, float]:
    """Bounds on the EXP required for this level, given a displayed reading."""
    if exp <= 0:
        return 0.0, INF
    lo = exp / ((pct + 0.01) / 100.0)
    hi = exp / ((pct - 0.005) / 100.0) if pct > 0.005 else INF
    return lo, hi


def _overlap(a: tuple[float, float], b: tuple[float, float], slack: float = 0.002) -> bool:
    return a[0] <= b[1] * (1 + slack) and b[0] <= a[1] * (1 + slack)


def _meet(a: tuple[float, float], b: tuple[float, float]) -> tuple[float, float]:
    lo, hi = max(a[0], b[0]), min(a[1], b[1])
    return (lo, hi) if lo <= hi else (min(lo, hi), max(lo, hi))


@dataclass
class _Pending:
    kind: str                       # "level" | "loss" | "jump" | "rebase"
    exp: int
    pct: float
    interval: tuple[float, float]
    seen: int = 1


@dataclass
class ExpTracker:
    confirm: int = 2                # readings needed to accept anything unusual
    big_jump: float = 0.04          # a single gain above this fraction of R needs confirming

    started: bool = False
    req: tuple[float, float] = (0.0, INF)
    exp: int = 0
    pct: float = 0.0
    gained: float = 0.0
    levels: int = 0
    lost: float = 0.0
    times: list[float] = field(default_factory=list)
    totals: list[float] = field(default_factory=list)
    pending: _Pending | None = None
    rejected: int = 0
    _rebase_next: bool = False

    # ------------------------------------------------------------- input
    def reset(self) -> None:
        self.__init__(confirm=self.confirm, big_jump=self.big_jump)  # type: ignore[misc]

    def rebase(self) -> None:
        """Take the next reading as the new baseline without counting the change (after a pause)."""
        self._rebase_next = True
        self.pending = None

    @property
    def req_mid(self) -> float | None:
        lo, hi = self.req
        if hi == INF:
            return None
        return (lo + hi) / 2.0

    def feed(self, t: float, exp: int, pct: float) -> str:
        iv = req_interval(exp, pct)
        if not self.started or self._rebase_next:
            same_level = self.started and _overlap(iv, self.req)
            self.req = _meet(self.req, iv) if same_level else iv
            self.exp, self.pct = exp, pct
            if not self.started:
                self.started = True
                self._record(t)
            self._rebase_next = False
            return "baseline"

        # "0 [0.00%]" says nothing about R: wait for an informative reading to
        # tell a level-up apart from dying at low EXP.
        if iv[1] == INF and exp < self.exp:
            self.pending = _Pending("zero", exp, pct, iv)
            return "pending"
        if self.pending and self.pending.kind == "zero" and iv[1] != INF:
            self.pending = None
            if self.pct < LEVEL_DROP_PCT and _overlap(iv, self.req):   # died near 0
                self.lost += self.exp
                self.gained -= self.exp
                self._set(0, 0.0, self.req)
            else:                               # levelled up through 0
                r = self.req_mid if self.req_mid is not None else self.req[0]
                self.gained += max(0.0, r - self.exp)
                self.levels += 1
                self._set(0, 0.0, iv)
            self._record(t)

        if exp < self.exp and self.pct - pct >= LEVEL_DROP_PCT:
            return self._pend(t, "level", exp, pct, iv)

        same = _overlap(iv, self.req)
        if same and exp == self.exp:
            self.pending = None
            self.req = _meet(self.req, iv)
            return "same"

        if same and exp > self.exp:
            mid = self.req_mid
            if mid and (exp - self.exp) > self.big_jump * mid:
                return self._pend(t, "jump", exp, pct, iv)
            self.pending = None
            self.gained += exp - self.exp
            self._set(exp, pct, _meet(self.req, iv))
            self._record(t)
            return "gain"

        if same and exp < self.exp:
            return self._pend(t, "loss", exp, pct, iv)

        # The reading belongs to a different level.
        if pct < self.pct and exp < self.exp and iv[0] >= self.req[0] * 0.98:
            return self._pend(t, "level", exp, pct, iv)
        return self._pend(t, "rebase", exp, pct, iv)

    # ----------------------------------------------------------- helpers
    def _set(self, exp: int, pct: float, req: tuple[float, float]) -> None:
        self.exp, self.pct, self.req = exp, pct, req

    def _record(self, t: float) -> None:
        if self.times and t < self.times[-1]:
            t = self.times[-1]
        self.times.append(t)
        self.totals.append(self.gained)

    def _pend(self, t: float, kind: str, exp: int, pct: float, iv: tuple[float, float]) -> str:
        p = self.pending
        if p and p.kind == kind and _overlap(iv, p.interval) and exp >= p.exp:
            p.seen += 1
            p.exp, p.pct, p.interval = exp, pct, _meet(p.interval, iv)
        else:
            self.pending = p = _Pending(kind, exp, pct, iv)
        if p.seen < self.confirm:
            return "pending"
        self.pending = None
        if kind == "jump":
            self.gained += exp - self.exp
            self._set(exp, pct, _meet(self.req, iv))
        elif kind == "loss":
            self.lost += self.exp - exp
            self.gained -= self.exp - exp
            self._set(exp, pct, _meet(self.req, iv))
        elif kind == "level":
            r = self.req_mid if self.req_mid is not None else self.req[0]
            self.gained += max(0.0, r - self.exp) + exp
            self.levels += 1
            self._set(exp, pct, p.interval)
        else:  # rebase: different character / unexplained jump; don't count it
            self._set(exp, pct, p.interval)
            self.rejected += 1
            return "rebased"
        self._record(t)
        return kind

    # ------------------------------------------------------------ output
    def gained_at(self, t: float) -> float:
        i = bisect.bisect_right(self.times, t) - 1
        return self.totals[i] if i >= 0 else 0.0

    def rate(self, now: float, window: float | None = None) -> float | None:
        """EXP per hour over the trailing ``window`` seconds (whole session if None)."""
        if not self.started or now <= 0:
            return None
        start = self.times[0] if self.times else 0.0
        span = now - start
        if window is not None and span > window:
            g = self.gained - self.gained_at(now - window)
            span = window
        else:
            g = self.gained
        if span < 10:
            return None
        return g / span * 3600.0

    def eta_seconds(self, rate_per_hour: float | None) -> float | None:
        mid = self.req_mid
        if mid is None or not rate_per_hour or rate_per_hour <= 0:
            return None
        return max(0.0, mid - self.exp) / rate_per_hour * 3600.0

    def pct_of_level(self, amount: float | None) -> float | None:
        mid = self.req_mid
        if amount is None or not mid:
            return None
        return amount / mid * 100.0


@dataclass
class MesoTracker:
    """Inventory meso balance. A value must be seen twice in a row before it counts."""
    confirm: int = 2
    start: int | None = None
    balance: int | None = None
    earned: int = 0
    spent: int = 0
    t0: float | None = None
    _cand: int | None = None
    _seen: int = 0

    _rebase: bool = False

    def reset(self) -> None:
        self.__init__(confirm=self.confirm)  # type: ignore[misc]

    def rebase(self) -> None:
        """Next stable value replaces the balance without counting (after a pause)."""
        self._rebase = True

    def feed(self, t: float, value: int) -> str:
        if value == self._cand:
            self._seen += 1
        else:
            self._cand, self._seen = value, 1
        if self._seen < self.confirm:
            return "wait"
        if self._rebase and self.balance is not None:
            if value != self.balance:
                self.start = (self.start or 0) + (value - self.balance)
                self.balance = value
            self._rebase = False
            return "rebased"
        if value == self.balance:
            return "same"
        if self.balance is None:
            self.start = self.balance = value
            self.t0 = t
            return "baseline"
        d = value - self.balance
        if d > 0:
            self.earned += d
        else:
            self.spent += -d
        self.balance = value
        return "change"

    @property
    def net(self) -> int:
        return 0 if self.balance is None or self.start is None else self.balance - self.start

    def rates(self, now: float) -> tuple[float, float] | None:
        """(net/hr, earned/hr) since the baseline."""
        if self.t0 is None or now - self.t0 < 30:
            return None
        h = (now - self.t0) / 3600.0
        return self.net / h, self.earned / h


class ActiveClock:
    """Seconds of tracked (unpaused) time."""

    def __init__(self, now: float) -> None:
        self._acc = 0.0
        self._since: float | None = None
        self.start(now)

    def start(self, now: float) -> None:
        if self._since is None:
            self._since = now

    def pause(self, now: float) -> None:
        if self._since is not None:
            self._acc += now - self._since
            self._since = None

    def rewind(self, seconds: float) -> None:
        self._acc -= seconds

    @property
    def running(self) -> bool:
        return self._since is not None

    def elapsed(self, now: float) -> float:
        return self._acc + ((now - self._since) if self._since is not None else 0.0)

    def reset(self, now: float, running: bool = True) -> None:
        self._acc, self._since = 0.0, (now if running else None)


def fmt_int(v: float | None) -> str:
    if v is None or math.isnan(v):
        return "--"
    return f"{int(round(v)):,}"


def fmt_short(v: float | None) -> str:
    if v is None:
        return "--"
    a = abs(v)
    sign = "-" if v < 0 else ""
    for div, suf in ((1e9, "B"), (1e6, "M"), (1e3, "K")):
        if a >= div:
            return f"{sign}{a / div:.2f}{suf}" if a / div < 100 else f"{sign}{a / div:.1f}{suf}"
    return f"{sign}{a:.0f}"


def fmt_dur(sec: float | None) -> str:
    if sec is None or sec == INF:
        return "--:--:--"
    sec = int(sec)
    h, rem = divmod(sec, 3600)
    m, s = divmod(rem, 60)
    if h >= 100:
        return f"{h}h"
    return f"{h:02d}:{m:02d}:{s:02d}"
