"""Webull-checklist short: dead-company screen, intraday peak entry, stop / stalled / close exits, SSR and sizing."""

import numpy as np
import pandas as pd
import pytest

from research import spike_short_v2 as v2


def minutes(path, start="09:30", vol=100_000):
    """path: list of (open, high, low, close) per minute starting at `start`."""
    h0, m0 = int(start[:2]), int(start[3:])
    rows = []
    for i, (o, h, l, c) in enumerate(path):
        t = h0 * 60 + m0 + i
        rows.append([f"{t // 60:02d}:{t % 60:02d}", o, h, l, c, vol])
    return v2.minutes_frame(rows)


def flat(px, n):
    return [(px, px, px, px)] * n


def spike_then(after, peak=10.0):
    """15 minutes ramping to `peak`, then a red 5-minute bar from peak to 8.8 (12% off the high), then `after`."""
    ramp = [(6 + i * 0.25, 6 + (i + 1) * 0.25, 6 + i * 0.25, 6 + (i + 1) * 0.25) for i in range(15)]  # 6 -> 9.75
    ramp[-1] = (9.5, peak, 9.5, peak)
    red = [(peak, peak, 9.6, 9.6), (9.6, 9.6, 9.3, 9.3), (9.3, 9.3, 9.1, 9.1), (9.1, 9.1, 8.9, 8.9), (8.9, 8.9, 8.8, 8.8)]
    return ramp + red + after


def test_entry_on_first_red_bar_10pct_off_high():
    m = minutes(spike_then(flat(8.8, 400)))
    t, why = v2.day_trade(m, prev_raw_close=5.0)
    assert why == "" and t.entry_time == "09:50" and t.entry == pytest.approx(8.8)
    assert t.how == "close" and t.exit == pytest.approx(8.8)
    assert t.exit_time == "15:59"


def test_no_entry_before_945():
    # crash 12% off the high inside the first 15 minutes, then flat: first eligible bar ends 9:45, still 12% off -> but
    # it must be red; a flat bar is not red, so no trade
    path = [(10, 10, 10, 10)] * 5 + [(10, 10, 8.8, 8.8)] * 5 + flat(8.8, 400)
    t, why = v2.day_trade(minutes(path), 5.0)
    assert t is None and why == "no rollover signal"


def test_needs_dollar_volume():
    t, why = v2.day_trade(minutes(spike_then(flat(8.8, 400)), vol=100), 5.0)
    assert t is None


def test_stop_10pct():
    after = flat(8.8, 3) + [(8.8, 9.8, 8.8, 9.7)] + flat(9.7, 300)
    t, _ = v2.day_trade(minutes(spike_then(after)), 5.0)
    assert t.how == "stop" and t.exit == pytest.approx(8.8 * 1.1)


def test_stalled_exit_after_fall_and_bounce():
    fall = [(8.8, 8.8, 7.0, 7.0)] * 5                         # down to 7.0 (-20%)
    bounce = [(7.0, 7.8, 7.0, 7.8)] * 5                       # 5-minute close 7.8 >= 7.0 × 1.1
    t, _ = v2.day_trade(minutes(spike_then(fall + bounce + flat(7.8, 300))), 5.0)
    assert t.how == "stalled" and t.exit == pytest.approx(7.8)


def test_ssr_skip():
    path = [(5, 5, 4.4, 5)] + spike_then(flat(8.8, 400))[1:]   # first minute dipped below 90% of the $5 prior close
    t, why = v2.day_trade(minutes(path), 5.0)
    assert t is None and why == "SSR in force"


def test_net_return_costs():
    t = v2.DayTrade("10:00", 4.0, "15:59", 3.0, "close", 10e6)
    gross, cost = v2.net_return(t)
    assert gross == pytest.approx(0.25) and cost == pytest.approx(0.01)
    t2 = v2.DayTrade("10:00", 4.0, "10:30", 4.4, "stop", 3e6)
    assert v2.net_return(t2)[1] == pytest.approx(0.02 + 0.01)


def test_simulate_three_per_day_by_entry_time():
    cal = pd.bdate_range("2020-01-06", periods=3)
    mk = lambda s, et: {"symbol": s, "day": cal[0], "gap": 1.0,
                        "trade": v2.DayTrade(et, 4.0, "15:59", 3.6, "close", 10e6)}
    eq, trades = v2.simulate([mk("A", "11:00"), mk("B", "09:50"), mk("C", "10:00"), mk("D", "10:30")], cal)
    assert [t.symbol for t in trades] == ["B", "C", "D"]
    assert trades[0].pnl == pytest.approx(v2.POSITION * v2.START_EQUITY * (0.10 - 0.01))
    assert eq.iloc[-1] == pytest.approx(v2.START_EQUITY + sum(t.pnl for t in trades))


def test_dead_company_screen():
    n = 300
    days = pd.bdate_range("2019-01-01", periods=n)
    close = np.r_[np.linspace(20, 3, n - 1), 3.0]              # long decline
    opens = close.copy()
    opens[-1] = 6.0                                             # +100% gap on the last day
    df = pd.DataFrame({"symbol": "Z", "day": days, "open": opens, "high": np.maximum(opens, close),
                       "low": np.minimum(opens, close), "close": close, "volume": 1e6, "raw_close": close})
    f = v2.add_features(df)
    s = v2.screen(f)
    assert list(s["day"]) == [days[-1]]
    up = df.assign(close=np.r_[np.linspace(3, 20, n - 1), 20.0])
    up.loc[n - 1, "open"] = 40.0
    up["raw_close"] = 4.0                                       # keep raw price under $8
    assert v2.screen(v2.add_features(up)).empty                 # not a dead company
