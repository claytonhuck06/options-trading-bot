"""Short-the-spike backtest: signal rules, SSR skips, stop and exit logic, sizing, costs and no look-ahead."""

import math

import numpy as np
import pandas as pd
import pytest

from research import spike_short as ss


def bars(symbol, closes, opens=None, highs=None, lows=None, volume=1e6, raw_factor=1.0, start="2020-01-01"):
    """One symbol's daily bars. raw_factor: raw price = adjusted × factor (e.g. after a later reverse split)."""
    n = len(closes)
    days = pd.bdate_range(start, periods=n)
    closes = np.asarray(closes, float)
    opens = np.asarray(opens if opens is not None else closes, float)
    highs = np.asarray(highs if highs is not None else np.maximum(opens, closes), float)
    lows = np.asarray(lows if lows is not None else np.minimum(opens, closes), float)
    return pd.DataFrame({"symbol": symbol, "day": days, "open": opens, "high": highs, "low": lows, "close": closes,
                         "volume": volume, "raw_close": closes * raw_factor})


def frame(*parts):
    df = pd.concat(parts).sort_values(["symbol", "day"], ignore_index=True)
    return ss.add_features(df)


BASE = [4.0] * 25     # 25 quiet days of history at $4 with 1M shares = $4M/day


def test_excluded_names_and_symbols():
    assert ss.excluded("ABCDW", "Foo Corp Warrants")
    assert ss.excluded("SPY", "SPDR S&P 500 ETF Trust")
    assert ss.excluded("0029900E0", "Old thing")
    assert ss.excluded("BRK.B", "Berkshire")
    assert not ss.excluded("MULN", "Mullen Automotive Inc.")


def test_cost_tiers():
    assert ss.cost_for(1.5, 10e6) == 0.02
    assert ss.cost_for(3.0, 10e6) == 0.01
    assert ss.cost_for(6.0, 10e6) == 0.006
    assert ss.cost_for(6.0, 3e6) == 0.012


def test_signal_needs_jump_price_volume_and_history():
    df = frame(bars("A", BASE + [5.3, 5.0]),                       # +32.5%: signals
               bars("B", BASE + [5.0, 5.0]),                       # +25%: no
               bars("C", [9.0] * 25 + [12.0, 12.0]),               # prev close $9: no
               bars("D", BASE + [5.3, 5.0], volume=1e5),           # $0.5M volume: no
               bars("E", [4.0] * 10 + [5.3, 5.0]))                 # 10 days history: no
    sig = ss.signals(df)
    assert list(sig["symbol"]) == ["A"]
    assert sig.iloc[0]["day"] == pd.bdate_range("2020-01-01", periods=27)[25]


def test_reverse_split_is_not_a_jump():
    """Raw price jumps 10x from a reverse split, but the split-adjusted series is flat: no signal."""
    adj = bars("R", BASE + [4.0, 4.0])
    adj.loc[25:, "raw_close"] = 40.0
    sig = ss.signals(frame(adj))
    assert sig.empty


def test_under_8_uses_raw_price():
    """Adjusted price is $4 but the stock actually traded at $40 then (later reverse split): not eligible."""
    sig = ss.signals(frame(bars("R", BASE + [5.3, 5.0], raw_factor=10.0)))
    assert sig.empty


def test_ssr_flag_on_spike_day():
    lows = BASE + [3.5, 5.0]            # fell below 90% of $4 intraday before closing up
    sig = ss.signals(frame(bars("A", BASE + [5.3, 5.0], lows=lows)))
    assert bool(sig.iloc[0]["ssr_today"])


def _sim(df, **kw):
    sig = ss.signals(df)
    cal = pd.DatetimeIndex(sorted(df["day"].unique()))
    return ss.simulate(df, sig, cal, **kw)


def test_day_trade_profit_and_costs():
    # spike to 5.3, next day opens 5.2, closes 4.68 (−10%), high stays below the stop
    df = frame(bars("A", BASE + [5.3, 4.68, 4.6], opens=BASE + [4.0, 5.2, 4.6], volume=2e6))
    res = _sim(df)
    assert len(res.trades) == 1
    t = res.trades[0]
    assert t.entry == pytest.approx(5.2) and t.exit == pytest.approx(4.68)
    assert t.entry_day > t.signal_day
    assert t.gross == pytest.approx(0.10)
    assert t.net == pytest.approx(0.10 - 0.006)
    assert not res.problems


def test_cost_tier_uses_raw_entry_price():
    df = frame(bars("A", BASE + [5.3, 4.68, 4.6], opens=BASE + [4.0, 5.2, 4.6], volume=2e6))
    # dollar volume on t = 5.3 × 2M = $10.6M (not thin); raw entry $5.20 -> 0.6%
    assert _sim(df).trades[0].cost == pytest.approx(0.006)


def test_stop_hit_exits_at_stop_plus_slippage():
    df = frame(bars("A", BASE + [5.3, 6.0, 6.0], opens=BASE + [4.0, 5.0, 6.0], highs=BASE + [5.3, 6.5, 6.0], volume=2e6))
    t = _sim(df).trades[0]
    assert t.stopped and t.exit == pytest.approx(6.0)               # 5.0 × 1.2
    assert t.cost == pytest.approx(0.006 + ss.STOP_SLIPPAGE)
    assert t.net == pytest.approx(-0.2 - 0.006 - ss.STOP_SLIPPAGE)


def test_ssr_at_open_skips_trade():
    df = frame(bars("A", BASE + [5.3, 4.5, 4.5], opens=BASE + [4.0, 4.7, 4.5], volume=2e6))   # 4.7 < 0.9 × 5.3
    res = _sim(df)
    assert res.trades == [] and res.skipped_ssr == 1


def test_position_size_and_equity():
    df = frame(bars("A", BASE + [5.3, 4.68, 4.6], opens=BASE + [4.0, 5.2, 4.6], volume=2e6))
    res = _sim(df)
    t = res.trades[0]
    assert t.notional == pytest.approx(ss.POSITION * ss.START_EQUITY)
    assert res.equity.iloc[-1] - ss.START_EQUITY == pytest.approx(t.notional * t.net)


def test_max_three_open_picks_highest_dollar_volume():
    parts = [bars(s, BASE + [5.3, 5.0, 5.0], opens=BASE + [4.0, 5.2, 5.0], volume=v)
             for s, v in (("A", 1e6), ("B", 2e6), ("C", 3e6), ("D", 4e6))]
    res = _sim(frame(*parts))
    assert sorted(t.symbol for t in res.trades) == ["B", "C", "D"]
    assert res.skipped_slots == 1


def test_multi_day_hold_gap_above_stop_exits_at_open():
    closes = BASE + [5.3, 5.0, 5.5, 5.0, 5.0]
    opens = BASE + [4.0, 5.0, 5.0, 7.0, 5.0]
    df = frame(bars("A", closes, opens=opens, volume=2e6))
    t = _sim(df, hold=3).trades[0]
    assert t.stopped and t.exit == pytest.approx(7.0)


def test_filter_is_applied():
    df = frame(bars("A", BASE + [5.3, 4.68, 4.6], opens=BASE + [4.0, 5.2, 4.6], volume=2e6))
    assert _sim(df, eligible=lambda r: False).trades == []


def test_dv20_excludes_spike_day():
    df = frame(bars("A", BASE + [5.3, 5.0], volume=1e6))
    sig = ss.signals(df)
    assert sig.iloc[0]["dv20_before"] == pytest.approx(4e6)


def test_in_grid():
    idx = pd.MultiIndex.from_product([pd.IntervalIndex.from_breaks(ss.PRICE_BANDS, closed="left"),
                                      pd.IntervalIndex.from_breaks(ss.DV_BANDS, closed="left")])
    grid = pd.DataFrame({"stocks": 30, "etb_share": 0.0, "shortable": False}, index=idx)
    grid.loc[(idx[0][0].__class__(5.0, 8.0, closed="left"), idx[0][1].__class__(5e6, 20e6, closed="left")), "shortable"] = True
    assert ss.in_grid(grid, 6.0, 10e6)
    assert not ss.in_grid(grid, 6.0, 2e6)
    assert not ss.in_grid(grid, 0.3, 10e6)
    assert not ss.in_grid(grid, float("nan"), 10e6)
