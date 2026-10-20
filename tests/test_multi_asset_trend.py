"""Multi-asset monthly trend: common start, equal weights, cash for 'off' assets, monthly rebalance, log check."""

from dataclasses import replace
from datetime import date, timedelta

import pytest

from research.monthly_trend import Bar, MarketData, stats
from research.multi_asset_trend import common_days, common_start, log_check, simulate

END = date(1996, 1, 1)


def _days(start, end=END):
    d, out = start, []
    while d < end:
        if d.weekday() < 5:
            out.append(d)
        d += timedelta(days=1)
    return out


def _m(d):
    return (d.year - 1993) * 12 + d.month - 1


def _asset(price_fn, start=date(1993, 1, 4), divs=None):
    bars, prev = [], None
    for d in _days(start):
        c = price_fn(d)
        bars.append(Bar(d, prev if prev is not None else c, c))
        prev = c
    return MarketData(bars, divs or {}, [(date(1990, 1, 1), 0.0)])


UP = lambda d: 100.0 + _m(d)                                       # noqa: E731
DOWN = lambda d: 300.0 - _m(d)                                     # noqa: E731
FLAT = lambda d: 50.0                                              # noqa: E731


def test_start_waits_for_twelve_month_ends_of_the_youngest_asset():
    datas = {"A": _asset(UP), "B": _asset(UP, start=date(1993, 6, 1))}
    days = common_days(datas, END)
    assert days[0] == date(1993, 6, 1)
    assert common_start(days, END) == date(1994, 6, 1)             # 12th month-end is 1994-05-31


def test_off_assets_sit_in_cash_and_on_assets_get_equal_shares():
    datas = {"A": _asset(UP), "B": _asset(DOWN), "C": _asset(FLAT), "D": _asset(UP)}
    res = simulate(datas, 10, 0.0, END)
    start = min(res.equity)
    first = [t for t in res.trades if t.day == start]
    assert {t.symbol for t in first} == {"A", "D"}                  # B falling, C tied with its average -> off
    assert all(t.value == pytest.approx(25_000) for t in first)     # 1/4 each at the open; the rest stays cash
    assert not log_check(res, common_days(datas, END))


def test_equal_weight_hold_rebalances_monthly_with_costs():
    datas = {"A": _asset(UP), "B": _asset(FLAT)}
    ew = simulate(datas, None, 0.001, END)
    start = min(ew.equity)
    first = [t for t in ew.trades if t.day == start]
    assert sum(t.value for t in first) == pytest.approx(100_000 * (1 - 0.001), rel=1e-6)
    later = [t for t in ew.trades if t.day > start]
    assert later and all(t.signal_month_end < t.day for t in later)
    sells = [t for t in later if t.symbol == "A"]
    assert sells and all(t.side == "sell" for t in sells)          # A keeps rising, so it's trimmed back to 50%
    assert ew.invested[start] == pytest.approx(1.0)


def test_dividend_paid_to_holder_and_reinvested():
    ex = date(1995, 3, 15)
    datas = {"A": _asset(FLAT, divs={ex: 1.0}), "B": _asset(FLAT)}
    ew = simulate(datas, None, 0.0, END)
    before = max(d for d in ew.equity if d < ex)
    assert ew.equity[ex] - ew.equity[before] == pytest.approx(50_000 / 50.0 * 1.0)


def test_log_check_catches_wrong_holdings():
    datas = {"A": _asset(UP), "B": _asset(DOWN)}
    res = simulate(datas, 10, 0.0, END)
    days = common_days(datas, END)
    assert not log_check(res, days)
    m = res.months[3]
    res.months[3] = replace(m, held_after=frozenset({"A", "B"}))
    assert log_check(res, days)


def test_rule_drawdown_is_zero_when_falling_asset_is_avoided():
    datas = {"A": _asset(UP), "B": _asset(DOWN)}
    res = simulate(datas, 10, 0.0, END)
    s = stats(res, min(res.equity), max(res.equity))
    assert s.max_dd == pytest.approx(0.0, abs=1e-9)
    assert s.invested == pytest.approx(0.5, abs=0.01)
