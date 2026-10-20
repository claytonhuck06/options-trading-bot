"""SPY monthly trend: signal arithmetic, next-open fills, dividends, cash interest, costs, log check, stats."""

from dataclasses import replace
from datetime import date, timedelta

import pytest

from research.monthly_trend import (
    Bar, MarketData, check_dividends, log_check, month_ends, round_trips, signals, simulate, stats, whipsaws,
)

END = date(1995, 7, 1)


def _days(start=date(1993, 1, 4), end=END):
    d, out = start, []
    while d < end:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _data(price_fn, divs=None, rate_pct=0.0, open_gap=0.0):
    """price_fn(day) -> close; open = previous close × (1 + open_gap)."""
    bars, prev = [], None
    for d in _days():
        c = price_fn(d)
        bars.append(Bar(d, (prev if prev is not None else c) * (1 + open_gap), c))
        prev = c
    return MarketData(bars, divs or {}, [(date(1990, 1, 1), rate_pct)])


def _month_index(d):
    return (d.year - 1993) * 12 + d.month - 1


# --- Signal --------------------------------------------------------------------------------------

def test_month_ends_skip_the_unfinished_month():
    days = _days(date(1993, 1, 4), date(1993, 3, 10))
    assert month_ends(days, date(1993, 3, 1)) == [date(1993, 1, 29), date(1993, 2, 26)]


def test_signal_average_includes_current_month_and_ties_go_to_cash():
    ends = [date(1993, m, 28) for m in range(1, 13)]
    close = {d: 100.0 for d in ends}
    close[ends[-1]] = 110.0
    sig = signals(ends, close, 10)
    assert ends[8] not in sig                                  # needs 10 closes
    assert sig[ends[9]] == (100.0, False)                      # equal to the average -> cash
    avg, own = sig[ends[11]]
    assert avg == pytest.approx(101.0) and own
    assert signals(ends, close, None)[ends[0]] == (None, True)


# --- Simulation ----------------------------------------------------------------------------------

def test_flat_market_buy_and_hold_one_trade_with_cost():
    data = _data(lambda d: 100.0)
    bh = simulate(data, None, 0.001, END)
    assert len(bh.trades) == 1 and bh.trades[0].day == date(1994, 1, 3) and bh.trades[0].side == "buy"
    assert bh.equity[max(bh.equity)] == pytest.approx(100_000 * 0.999)


def test_rule_trades_at_next_open_and_ignores_mid_month_crash():
    # Rises to end of Jun 1994, falls from July on. A one-day crash mid-May must not trigger a sale.
    def price(d):
        m = _month_index(d)
        if d == date(1994, 5, 16):
            return 50.0
        return 100 + m if m <= 17 else 117 - 5 * (m - 17)
    data = _data(price, open_gap=0.01)
    res = simulate(data, 10, 0.0, END)
    assert res.trades[0].side == "buy" and res.trades[0].day == date(1994, 1, 3)
    assert res.invested[date(1994, 5, 17)]                     # crash day ignored
    sell = res.trades[1]
    assert sell.side == "sell"
    signal_end = sell.signal_month_end
    days = sorted(res.equity)
    assert sell.day == days[days.index(signal_end) + 1]        # first trading day after the month-end
    assert sell.price == pytest.approx(price(signal_end) * 1.01)
    row = next(m for m in res.months if m.month_end == signal_end)
    assert row.intended == "sell" and row.fill_day == sell.day and "+1.00%" in row.note
    assert not log_check(res, days)


def test_dividend_reinvested_only_if_held_at_prior_close():
    ex = date(1994, 3, 18)
    data = _data(lambda d: 100.0, divs={ex: 1.0})
    bh = simulate(data, None, 0.0, END)
    assert bh.equity[ex] == pytest.approx(101_000)
    assert bh.equity[max(bh.equity)] == pytest.approx(101_000)


def test_cash_earns_the_tbill_rate():
    data = _data(lambda d: 200.0 - _month_index(d))            # always falling: the rule stays in cash
    res = simulate(data, 10, 0.0, END, start=date(1994, 1, 1))
    assert not res.trades
    first, last = min(res.equity), max(res.equity)
    assert res.equity[first] == pytest.approx(100_000)
    data5 = replace(data, rates=[(date(1990, 1, 1), 5.0)])
    res5 = simulate(data5, 10, 0.0, END)
    n = len(res5.equity) - 1
    assert res5.equity[last] == pytest.approx(100_000 * 1.05 ** (n / 252))


def test_log_check_catches_a_trade_without_a_signal():
    def price(d):
        m = _month_index(d)
        return 100 + m if m <= 17 else 117 - 5 * (m - 17)
    res = simulate(_data(price), 10, 0.0, END)
    days = sorted(res.equity)
    assert not log_check(res, days)
    bad = res.trades[1]
    res.trades[1] = replace(bad, day=bad.day + timedelta(days=3))
    assert log_check(res, days)


# --- Metrics -------------------------------------------------------------------------------------

def test_stats_drawdown_trades_and_whipsaw():
    def price(d):
        m = _month_index(d)
        return 100 + m if m <= 17 else 117 - 5 * (m - 17)
    res = simulate(_data(price), 10, 0.0, END)
    s = stats(res, date(1994, 1, 1), max(res.equity))
    peak = max(res.equity.values())
    trough_after = min(v for d, v in res.equity.items() if d > max(res.equity, key=res.equity.get))
    assert s.max_dd == pytest.approx(1 - trough_after / peak)
    assert s.trades == len(res.trades)
    trips = round_trips(res)
    assert trips[0][1] is not None and trips[0][2] == pytest.approx(res.trades[1].value / res.trades[0].value - 1)
    assert whipsaws(res) == []                                 # the one round trip lasted months


def test_dividend_schedule_check():
    divs = {date(1994, 3, 18): 0.3, date(1994, 6, 17): 0.3, date(1994, 9, 16): 0.3, date(1994, 12, 2): 0.3}
    out = check_dividends(MarketData([], divs, []), date(1994, 1, 1), date(1994, 12, 30))
    assert out["count"] == 4 and not out["wrong_years"] and out["off_schedule"] == [date(1994, 12, 2)]
