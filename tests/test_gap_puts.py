"""Gap puts: news classifier, gap screen, contract choice, exit pricing, sizing and no look-ahead."""

from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest

from research import gap_puts as gp


def art(headline, symbols=("ABC",), summary=""):
    return {"headline": headline, "summary": summary, "symbols": list(symbols), "created_at": "2025-01-02T12:00:00Z"}


@pytest.mark.parametrize("headline,summary,syms,expected", [
    ("ABC Announces FDA Approval For Lead Drug", "", ("ABC",), True),
    ("ABC Shares Are Trading Higher", "", ("ABC",), False),
    ("Why ABC Shares Are Trading Higher", "The company announced a $50M contract.", ("ABC",), True),
    ("12 Health Care Stocks Moving In Thursday's Pre-Market Session", "", ("ABC", "D", "E", "F", "G"), False),
    ("ABC Reports Q3 Results", "", ("ABC",), True),
    ("ABC Shares Halted On Circuit Breaker", "", ("ABC",), False),
    ("Something About XYZ", "", ("XYZ",), False),          # not tagged with this symbol
])
def test_is_catalyst(headline, summary, syms, expected):
    assert gp.is_catalyst(art(headline, syms, summary), "ABC") is expected


def test_in_window_uses_eastern_times():
    d, p = date(2025, 1, 3), date(2025, 1, 2)
    inside = {"created_at": "2025-01-03T14:40:00Z"}       # 9:40 ET
    after = {"created_at": "2025-01-03T14:50:00Z"}        # 9:50 ET, after the 9:45 cut-off
    before = {"created_at": "2025-01-02T14:00:00Z"}       # 9:00 ET on t-1, before 9:30
    assert gp.in_window(inside, d, p)
    assert not gp.in_window(after, d, p)
    assert not gp.in_window(before, d, p)


def bars(symbol, closes, opens, volume=1e6, start="2024-03-01"):
    n = len(closes)
    days = pd.bdate_range(start, periods=n)
    closes, opens = np.asarray(closes, float), np.asarray(opens, float)
    return pd.DataFrame({"symbol": symbol, "day": days, "open": opens, "high": np.maximum(opens, closes),
                         "low": np.minimum(opens, closes), "close": closes, "volume": volume, "raw_close": closes})


def test_gap_screen():
    base = [4.0] * 25
    df = gp.add_features(pd.concat([
        bars("A", base + [5.0, 5.0], base + [5.3, 5.0]),          # open +32.5%: gap
        bars("B", base + [5.3, 5.0], base + [4.1, 5.0]),          # closed up but opened flat: no
        bars("C", [0.5] * 25 + [0.7, 0.7], [0.5] * 25 + [0.7, 0.7], volume=1e7),   # open under $1: no
        bars("D", base + [5.0, 5.0], base + [5.3, 5.0], volume=1e5),               # $0.4M/day: no
    ]).sort_values(["symbol", "day"], ignore_index=True))
    g = gp.gaps(df)
    assert list(g["symbol"]) == ["A"]
    assert g.iloc[0]["gap"] == pytest.approx(0.325)


def test_gap_before_option_data_start_is_ignored():
    base = [4.0] * 25
    df = gp.add_features(bars("A", base + [5.0, 5.0], base + [5.3, 5.0], start="2023-12-01"))
    assert gp.gaps(df).empty


def test_choose_contract_nearest_expiry_and_strike():
    cs = [{"symbol": "X1", "expiry": "2025-02-21", "strike": 5.0},
          {"symbol": "X2", "expiry": "2025-02-14", "strike": 5.0},
          {"symbol": "X3", "expiry": "2025-02-14", "strike": 4.5},
          {"symbol": "X4", "expiry": "2025-02-14", "strike": 5.5}]
    assert gp.choose_contract(cs, 5.2)["symbol"] == "X2"
    assert gp.choose_contract(cs, 5.25)["symbol"] == "X2"     # tie 5.0 vs 5.5 -> lower strike
    assert gp.choose_contract([], 5.0) is None


def test_vwap_and_opt_day():
    assert gp.vwap([{"vw": 1.0, "v": 1}, {"vw": 2.0, "v": 3}]) == pytest.approx(1.75)
    assert gp.vwap([]) is None
    assert gp.opt_day("ABC250214P00005000") == date(2025, 2, 14)


def cand(sym, day, gap=0.4, entry=1.0, exit_px=2.0, charge=True, explained=False, hold=gp.HOLD, exit_days=7):
    d = pd.Timestamp(day)
    c = gp.Candidate(sym, d, gap, 5.0, 5.0, explained, [], 0, {5: 0.1, 1: 0.0, 10: 0.0},
                     contract=f"{sym}250314P00005000", strike_raw=5.0, entry=entry)
    c.exits[hold] = (d + pd.Timedelta(days=exit_days), exit_px, charge)
    return c


def test_trade_value_spreads():
    c = cand("A", "2025-01-06", entry=1.0, exit_px=2.0)
    buy, sell, _ = gp.trade_value(c, gp.HOLD, 0.05)
    assert buy == pytest.approx(1.05) and sell == pytest.approx(1.90)
    c2 = cand("B", "2025-01-06", entry=1.0, exit_px=2.0, charge=False)
    assert gp.trade_value(c2, gp.HOLD, 0.05)[1] == pytest.approx(2.0)


def test_simulate_sizing_pnl_and_slots():
    cal = pd.bdate_range("2025-01-06", periods=20)
    cs = [cand(s, "2025-01-06", gap=g) for s, g in (("A", 0.4), ("B", 0.9), ("C", 0.5), ("D", 0.6))]
    eq, trades, problems = gp.simulate(cs, cal)
    assert not problems
    assert sorted(t.symbol for t in trades) == ["B", "C", "D"]               # 3 slots, biggest gaps first
    t = trades[0]
    per = 1.05 * 100 + gp.FEE
    assert t.contracts == int(gp.RISK * gp.START_EQUITY // per)
    assert t.paid == pytest.approx(t.contracts * per)
    assert t.received == pytest.approx(t.contracts * (1.90 * 100 - gp.FEE))
    assert eq.iloc[-1] - gp.START_EQUITY == pytest.approx(sum(x.pnl for x in trades))


def test_simulate_filters_by_news_and_flags_bad_exit():
    cal = pd.bdate_range("2025-01-06", periods=20)
    cs = [cand("A", "2025-01-06", explained=True), cand("B", "2025-01-06", exit_days=0)]
    _, trades, problems = gp.simulate(cs, cal)
    assert trades == [] and problems                       # A has news; B "exits" on entry day
    _, trades, _ = gp.simulate(cs[:1], cal, explained=True)
    assert [t.symbol for t in trades] == ["A"]


def test_worthless_put_loses_whole_premium():
    cal = pd.bdate_range("2025-01-06", periods=20)
    _, trades, _ = gp.simulate([cand("A", "2025-01-06", exit_px=0.0, charge=False)], cal)
    assert trades[0].ret == pytest.approx(-1.0)
