"""Short at the open, cover when green (else hold up to 3 more days): exits, stop, costs, borrow fee, slots."""

import pandas as pd
import pytest

from research import spike_short_v3 as v3


def ev(closes, opens=None, highs=None, sym="A", gap=1.0, dv20=10e6, day="2020-01-06"):
    """closes for t..t+3 (business days); open of t is 4.0 unless given."""
    days = pd.bdate_range(day, periods=len(closes))
    opens = opens or [4.0] + list(closes[:-1])
    highs = highs or [max(o, c) for o, c in zip(opens, closes)]
    return {"symbol": sym, "day": days[0], "gap": gap, "path": list(zip(days, opens, highs, closes)),
            "raw_factor": 1.0, "dv20": dv20, "f1": True, "f2": True}


def test_green_at_close_covers_same_day():
    marks, stopped, cost = v3.plan(ev([3.0, 5, 5, 5]), None, 3)
    assert marks == [(pd.Timestamp("2020-01-06"), 3.0)] and not stopped
    assert cost == pytest.approx(0.01)                       # $4 entry tier, no nights


def test_red_holds_until_first_green_close():
    marks, _, cost = v3.plan(ev([4.5, 4.2, 3.8, 5.0]), None, 3)
    assert [m for _, m in marks] == [4.5, 4.2, 3.8]
    assert cost == pytest.approx(0.01 + v3.BORROW_APR * 2 / 365)


def test_still_red_exits_at_t_plus_3():
    marks, _, _ = v3.plan(ev([4.5, 4.6, 4.7, 4.8]), None, 3)
    assert len(marks) == 4 and marks[-1][1] == 4.8


def test_hold_two_variant_and_same_day_variant():
    assert len(v3.plan(ev([4.5, 4.6, 4.7, 4.8]), None, 2)[0]) == 3
    assert len(v3.plan(ev([4.5, 4.6, 4.7, 4.8]), None, 0)[0]) == 1


def test_stop_on_later_day_gap_exits_at_open():
    e = ev([4.5, 6.0, 4, 4], opens=[4.0, 5.5, 6, 4])         # day 2 opens 5.5 >= 4.8 stop
    marks, stopped, cost = v3.plan(e, 0.20, 3)
    assert stopped and marks[-1][1] == 5.5 and len(marks) == 2
    assert cost == pytest.approx(0.01 + 0.01 + v3.BORROW_APR * 1 / 365)


def test_weekend_counts_as_borrow_nights():
    e = ev([4.5, 3.9], day="2020-01-10")                      # Friday -> Monday
    _, _, cost = v3.plan(e, None, 3)
    assert cost == pytest.approx(0.01 + v3.BORROW_APR * 3 / 365)


def test_simulate_slots_marks_and_pnl():
    cal = pd.bdate_range("2020-01-06", periods=8)
    a = ev([4.5, 4.2, 3.8, 5.0], sym="A", gap=3.0)             # 3 days
    b = ev([3.0], sym="B", gap=2.0)                            # same day
    c = ev([3.0], sym="C", gap=1.5)
    d = ev([3.0], sym="D", gap=1.0)                            # 4th on day 1: no slot
    e2 = ev([3.0], sym="E", day="2020-01-07")                  # day 2: B, C closed, slot free
    eq, trades = v3.simulate([a, b, c, d, e2], cal)
    assert sorted(t.symbol for t in trades) == ["A", "B", "C", "E"]
    ta = next(t for t in trades if t.symbol == "A")
    assert ta.exit_day == pd.Timestamp("2020-01-08") and ta.exit == 3.8
    assert ta.pnl == pytest.approx(v3.POSITION * v3.START_EQUITY * (0.05 - ta.cost))
    assert eq.iloc[-1] == pytest.approx(v3.START_EQUITY + sum(t.pnl for t in trades))
