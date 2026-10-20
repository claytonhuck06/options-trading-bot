"""Relative momentum: total-return scores, ranking with ties, top two at 50% each, log check, turnover."""

from dataclasses import replace
from datetime import date, timedelta

import pytest

from research.monthly_trend import Bar, MarketData
from research.multi_asset_trend import common_days, common_start
from research.relative_momentum import log_check, rank, simulate, stock_share, tr_index, turnover

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


def _start(datas):
    return common_start(common_days(datas, END), END, 14)


def test_total_return_index_adds_dividends_back():
    a = _asset(lambda d: 100.0, divs={date(1993, 1, 6): 2.0})
    tr = tr_index(a)
    assert tr[date(1993, 1, 5)] == pytest.approx(1.0)
    assert tr[date(1993, 1, 6)] == pytest.approx(1.02)             # flat price, 2% dividend -> +2%


def test_dividend_payer_outranks_same_price_non_payer():
    divs = {d: 0.5 for d in _days(date(1993, 1, 4)) if d.day == 15}
    datas = {"A": _asset(lambda d: 100.0), "B": _asset(lambda d: 100.0, divs=divs), "C": _asset(lambda d: 90.0)}
    res = simulate(datas, 12, 0.0, END, _start(datas), top=1)
    assert all(m.held_after == frozenset({"B"}) for m in res.months if m.held_after is not None)


def test_ties_go_to_first_listed():
    assert rank({"A": 0.1, "B": 0.1, "C": 0.2}, ["A", "B", "C"]) == ["C", "A", "B"]


def test_holds_top_two_at_half_each_fully_invested():
    datas = {"A": _asset(lambda d: 100.0 + _m(d)), "B": _asset(lambda d: 300.0 - _m(d)),
             "C": _asset(lambda d: 100.0 + 3 * _m(d)), "D": _asset(lambda d: 50.0)}
    start = _start(datas)
    res = simulate(datas, 12, 0.0, END, start)
    first = [t for t in res.trades if t.day == start]
    assert {t.symbol for t in first} == {"A", "C"}                  # best two 12-month returns
    assert all(t.value == pytest.approx(50_000) for t in first)
    assert min(res.invested.values()) > 0.99                        # never in cash
    assert not log_check(res, common_days(datas, END))
    assert turnover(res)[0] == 0


def test_rotates_when_leadership_changes():
    flip = date(1995, 1, 1)
    datas = {"A": _asset(lambda d: 100.0 + (3 * _m(d) if d < flip else 72 - 2 * (_m(d) - 24))),
             "B": _asset(lambda d: 100.0 + _m(d)), "C": _asset(lambda d: 100.0 + 0.5 * _m(d))}
    res = simulate(datas, 12, 0.0, END, _start(datas))
    held = [m.held_after for m in res.months if m.held_after is not None]
    assert held[0] == frozenset({"A", "B"}) and held[-1] == frozenset({"B", "C"})
    assert turnover(res)[0] >= 1
    assert not log_check(res, common_days(datas, END))


def test_log_check_catches_wrong_holdings():
    datas = {"A": _asset(lambda d: 100.0 + _m(d)), "B": _asset(lambda d: 50.0), "C": _asset(lambda d: 40.0)}
    res = simulate(datas, 12, 0.0, END, _start(datas))
    i = next(i for i, m in enumerate(res.months) if m.held_after is not None)
    res.months[i] = replace(res.months[i], held_after=frozenset({"A", "C"}))
    assert any("top 2 by score" in p for p in log_check(res, common_days(datas, END)))


def test_stock_share_counts_spy_plus_efa_months():
    datas = {"SPY": _asset(lambda d: 100.0 + 2 * _m(d)), "EFA": _asset(lambda d: 100.0 + _m(d)),
             "IEF": _asset(lambda d: 100.0)}
    res = simulate(datas, 12, 0.0, END, _start(datas))
    assert stock_share(res) == pytest.approx(1.0)


def test_equal_weight_benchmark_holds_everything():
    datas = {"A": _asset(lambda d: 100.0 + _m(d)), "B": _asset(lambda d: 50.0)}
    ew = simulate(datas, None, 0.0, END, _start(datas))
    first = [t for t in ew.trades if t.day == min(ew.equity)]
    assert {t.symbol for t in first} == {"A", "B"}
