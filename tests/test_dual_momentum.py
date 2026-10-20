"""Dual momentum: top two by 12-month return, each kept only if above its 10-month average, else cash."""

from dataclasses import replace
from datetime import date, timedelta

import pytest

from research.monthly_trend import Bar, MarketData
from research.multi_asset_trend import common_days, common_start
from research.dual_momentum import blocked, expected_holdings, log_check, simulate

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


def _asset(price_fn, rate=0.0):
    bars, prev = [], None
    for d in _days(date(1993, 1, 4)):
        c = price_fn(d)
        bars.append(Bar(d, prev if prev is not None else c, c))
        prev = c
    return MarketData(bars, {}, [(date(1990, 1, 1), rate)])


def _start(datas):
    return common_start(common_days(datas, END), END, 14)


def _check(datas, res):
    return log_check(res, common_days(datas, END), expected_holdings(datas, END))


def test_both_leaders_trending_up_fully_invested():
    datas = {"A": _asset(lambda d: 100.0 + 2 * _m(d)), "B": _asset(lambda d: 100.0 + _m(d)),
             "C": _asset(lambda d: 300.0 - _m(d))}
    res = simulate(datas, 10, 0.0, END, _start(datas))
    first = [t for t in res.trades if t.day == min(res.equity)]
    assert {t.symbol for t in first} == {"A", "B"}
    assert all(t.value == pytest.approx(50_000) for t in first)
    assert not _check(datas, res)


def test_everything_falling_goes_to_cash_not_to_the_least_bad():
    datas = {"A": _asset(lambda d: 300.0 - _m(d)), "B": _asset(lambda d: 300.0 - 2 * _m(d)),
             "C": _asset(lambda d: 300.0 - 3 * _m(d))}
    res = simulate(datas, 10, 0.0, END, _start(datas))
    assert not res.trades
    assert max(res.invested.values()) == 0.0
    reviews = sum(m.held_after is not None for m in res.months)
    assert blocked(res, list(datas)) == {"A": reviews, "B": reviews, "C": 0}
    assert not _check(datas, res)


def test_leader_below_its_average_leaves_half_in_cash_not_the_third_fund():
    # A: big rise then a slide, so its 12-month return still leads while it sits below its 10-month average.
    def a(d):
        m = _m(d)
        return 100.0 + 10 * m if m < 18 else 280.0 - 3 * (m - 18)
    datas = {"A": _asset(a), "B": _asset(lambda d: 100.0 + _m(d)), "C": _asset(lambda d: 100.0 + 0.5 * _m(d))}
    res = simulate(datas, 10, 0.0, END, _start(datas))
    half = [m for m in res.months if m.held_after == frozenset({"B"})]
    assert half, "expected months with A ranked top but blocked"
    d = half[0].rebalance_day
    assert res.invested[d] == pytest.approx(0.5, abs=0.01)
    assert not _check(datas, res)


def test_cash_earns_t_bills():
    datas = {"A": _asset(lambda d: 300.0 - _m(d), rate=5.0), "B": _asset(lambda d: 300.0 - 2 * _m(d), rate=5.0)}
    res = simulate(datas, 10, 0.0, END, _start(datas))
    days = sorted(res.equity)
    years = (days[-1] - days[0]).days / 365.25
    assert res.equity[days[-1]] / 100_000 - 1 == pytest.approx(0.05 * years, rel=0.1)


def test_log_check_catches_holding_a_blocked_fund():
    datas = {"A": _asset(lambda d: 100.0 + 2 * _m(d)), "B": _asset(lambda d: 300.0 - _m(d)),
             "C": _asset(lambda d: 300.0 - 2 * _m(d))}
    res = simulate(datas, 10, 0.0, END, _start(datas))
    i = next(i for i, m in enumerate(res.months) if m.held_after is not None)
    res.months[i] = replace(res.months[i], held_after=frozenset({"A", "B"}))
    assert any("expected" in p for p in _check(datas, res))


def test_t_bill_version_blocks_funds_that_lag_cash():
    datas = {"A": _asset(lambda d: 100.0 + 0.01 * _m(d), rate=5.0), "B": _asset(lambda d: 100.0, rate=5.0)}
    res = simulate(datas, None, 0.0, END, _start(datas), tbill=True)
    assert max(res.invested.values()) == 0.0                        # A's ~0.1% a year loses to 5% T-bills
