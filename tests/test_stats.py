import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from phosphor.stats import ActiveClock, ExpTracker, MesoTracker, req_interval  # noqa: E402

R1 = 95_706          # EXP needed for "level A" (68,601 is 71.68%)
R2 = 100_951         # next level


def reading(exp, req, trunc=False):
    pct = exp / req * 100
    return exp, (int(pct * 100) / 100 if trunc else round(pct, 2))


def test_interval_contains_truth():
    for exp in (1, 500, 68_601, 95_000):
        for trunc in (False, True):
            e, p = reading(exp, R1, trunc)
            lo, hi = req_interval(e, p)
            assert lo <= R1 <= hi or p == 0


def test_gain_and_rate():
    t = ExpTracker()
    for i, exp in enumerate(range(10_000, 40_001, 1_000)):
        t.feed(i * 10.0, *reading(exp, R1))
    assert t.gained == 30_000
    assert abs(t.rate(300.0) - 30_000 / 300 * 3600) < 1
    # 5-minute window equals session here; a 60 s window sees the last 6 gains
    assert abs(t.rate(300.0, 60) - 6_000 * 60) < 1
    eta = t.eta_seconds(t.rate(300.0))
    assert abs(eta - (R1 - 40_000) / (30_000 / 300)) < 30


def test_misread_rejected():
    t = ExpTracker()
    t.feed(0, *reading(60_000, R1))
    t.feed(10, *reading(61_000, R1))
    # digit misread: 81,000 with a percentage that belongs to 61,500
    assert t.feed(20, 81_000, reading(61_500, R1)[1]) == "pending"
    t.feed(30, *reading(62_000, R1))
    assert t.gained == 2_000
    assert t.rejected == 0


def test_level_up_needs_confirmation():
    t = ExpTracker()
    t.feed(0, *reading(90_000, R1))
    t.feed(10, *reading(95_000, R1))
    assert t.feed(20, *reading(2_000, R2)) == "pending"
    assert t.feed(30, *reading(3_000, R2)) == "level"
    # (R1 - 95,000) + 3,000 on top of the 5,000 before
    assert abs(t.gained - (5_000 + (R1 - 95_000) + 3_000)) < 10
    assert t.levels == 1
    assert t.feed(40, *reading(4_000, R2)) == "gain"


def test_level_up_through_zero():
    t = ExpTracker()
    t.feed(0, *reading(94_000, R1))
    t.feed(10, *reading(95_500, R1))
    assert t.feed(20, 0, 0.0) == "pending"
    t.feed(30, *reading(1_500, R2))
    assert t.levels == 1
    assert abs(t.gained - (1_500 + (R1 - 95_500) + 1_500)) < 10


def test_death_loss():
    t = ExpTracker()
    t.feed(0, *reading(50_000, R1))
    t.feed(10, *reading(45_000, R1))
    t.feed(20, *reading(45_000, R1))
    assert t.lost == 5_000 and t.gained == -5_000


def test_character_switch_rebases():
    t = ExpTracker()
    t.feed(0, *reading(50_000, R1))
    t.feed(10, *reading(51_000, R1))
    t.feed(20, *reading(400_000, 1_000_000))
    assert t.feed(30, *reading(400_500, 1_000_000)) == "rebased"
    assert t.gained == 1_000
    assert t.feed(40, *reading(401_500, 1_000_000)) == "gain"
    assert t.gained == 2_000


def test_rebase_after_pause():
    t = ExpTracker()
    t.feed(0, *reading(50_000, R1))
    t.feed(10, *reading(51_000, R1))
    t.rebase()
    t.feed(20, *reading(70_000, R1))
    t.feed(30, *reading(71_000, R1))
    assert t.gained == 2_000


def test_meso_needs_two_reads():
    m = MesoTracker()
    assert m.feed(0, 1_000) == "wait"
    assert m.feed(1, 1_000) == "baseline"
    assert m.feed(2, 9_999) == "wait"          # one-off misread ignored
    m.feed(3, 1_500)
    m.feed(4, 1_500)
    m.feed(5, 1_200)
    m.feed(6, 1_200)
    assert m.net == 200 and m.earned == 500 and m.spent == 300


def test_clock():
    c = ActiveClock(100.0)
    c.pause(160.0)
    assert c.elapsed(500.0) == 60.0
    c.start(600.0)
    assert c.elapsed(630.0) == 90.0


def test_level_up_to_tiny_percentage():
    """Just after levelling the % is tiny and the R-range overlaps the old level's."""
    t = ExpTracker()
    t.feed(0, *reading(93_000, R1))
    t.feed(10, *reading(95_600, R1))
    assert t.feed(20, *reading(50, R2)) == "pending"
    assert t.feed(30, *reading(120, R2)) == "level"
    assert t.levels == 1 and t.lost == 0
    assert abs(t.gained - (2_600 + (R1 - 95_600) + 120)) < 15
    assert t.feed(40, *reading(400, R2)) == "gain"
    assert t.rate(40) > 0


def test_death_near_zero_is_a_loss():
    t = ExpTracker()
    t.feed(0, *reading(5_000, R1))
    t.feed(10, *reading(6_000, R1))
    t.feed(20, 0, 0.0)
    t.feed(30, *reading(300, R1))
    assert t.levels == 0 and t.lost == 6_000


def test_level_up_through_zero_low_pct():
    t = ExpTracker()
    t.feed(0, *reading(95_000, R1))
    t.feed(10, 0, 0.0)
    t.feed(20, *reading(30, R2))     # 0.03%: range overlaps the old level
    assert t.levels == 1 and t.lost == 0
