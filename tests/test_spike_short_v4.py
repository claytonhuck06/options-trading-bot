"""v4: 50% stop, risk-pool sizing, locate + borrow costs, IBKR list parsing, account-hit accounting."""

import pandas as pd
import pytest

from research import spike_short_v4 as v4


def ev(closes, opens=None, highs=None, sym="A", gap=1.0, dv20=10e6, day="2020-01-06"):
    days = pd.bdate_range(day, periods=len(closes))
    opens = opens or [4.0] + list(closes[:-1])
    highs = highs or [max(o, c) for o, c in zip(opens, closes)]
    return {"symbol": sym, "day": days[0], "gap": gap, "path": list(zip(days, opens, highs, closes)),
            "raw_factor": 1.0, "dv20": dv20}


def test_position_sizes_to_risk_pool():
    assert v4.POSITION == pytest.approx(0.015 / 0.55)
    assert v4.position_for(None) == v4.NO_STOP_POSITION


def test_same_day_green_pays_locate_no_borrow():
    marks, stopped, cost = v4.plan(ev([3.0]))
    assert marks[-1][1] == 3.0 and not stopped
    assert cost == pytest.approx(0.01 + v4.LOCATE)


def test_stop_intraday_on_entry_day():
    marks, stopped, cost = v4.plan(ev([5.0], highs=[6.5]))          # level 6.0
    assert stopped and marks[-1][1] == pytest.approx(6.0)
    assert cost == pytest.approx(0.01 + v4.STOP_SLIP + v4.LOCATE)


def test_gap_through_stop_exits_at_open():
    e = ev([4.5, 9.0], opens=[4.0, 8.0], highs=[4.6, 9.5])
    marks, stopped, cost = v4.plan(e)
    assert stopped and marks[-1][1] == 8.0
    assert cost == pytest.approx(0.01 + v4.STOP_SLIP + v4.LOCATE + v4.BORROW_APR / 365)


def test_borrow_fee_per_night():
    _, _, cost = v4.plan(ev([4.5, 4.2, 3.8]))
    assert cost == pytest.approx(0.01 + v4.LOCATE + v4.BORROW_APR * 2 / 365)


def test_account_hit_is_capped_near_risk_on_stop():
    cal = pd.bdate_range("2020-01-06", periods=3)
    _, trades = v4.simulate([ev([5.0], highs=[6.5])], cal)
    t = trades[0]
    assert t.account_hit == pytest.approx(-v4.POSITION * (0.5 + t.cost))
    assert t.account_hit > -0.016


def test_parse_ibkr():
    text = ("#BOF|2026.10.21|14:55:14\n#SYM|CUR|NAME|CON|ISIN|REBATERATE|FEERATE|AVAILABLE|FIGI|\n"
            "ABC|USD|ABC INC|1|X|-50.1|55.2|>10000000|B|\nXYZ|USD|XYZ|2|X|-1|120.5|3500|B|\nBAD|USD|B|3|X|||NA|B|\n")
    t = v4.parse_ibkr(text)
    assert t["ABC"] == (10_000_000, 55.2) and t["XYZ"] == (3500, 120.5) and "BAD" not in t
