"""Phase 3 backtest engine: P&L arithmetic, exit order, data-gap handling, weekly ladder, setting choice."""

from datetime import date, timedelta

import pytest

from research.condor_backtest import (
    FEE, ExitParams, LegQuote, RunResult, Setting, Trade, choose, max_drawdown, neighbours, simulate,
)
from strategies.iron_condor import Condor, CondorParams, Decision, Leg

START = date(2019, 1, 7)                                     # a Monday
DAYS = [START + timedelta(days=i) for i in range(120) if (START + timedelta(days=i)).weekday() < 5]
PARAMS = CondorParams(45, 0.20, 5.0)


def _condor(day: date, credit_legs=(1.0, 0.2, 1.0, 0.2), contracts=2) -> Condor:
    sp_bid, lp_ask, sc_bid, lc_ask = credit_legs
    exp = day + timedelta(days=45)
    return Condor(day, exp, 45,
                  Leg("P", 250, -1, sp_bid, sp_bid + 0.02, -0.2, "own"), Leg("P", 245, 1, lp_ask - 0.02, lp_ask, -0.1, "own"),
                  Leg("C", 300, -1, sc_bid, sc_bid + 0.02, 0.2, "own"), Leg("C", 305, 1, lc_ask - 0.02, lc_ask, 0.1, "own"),
                  5.0, round(sp_bid + sc_bid - lp_ask - lc_ask, 4), contracts)


class FakeMarket:
    """cost path: day -> cost to close (spread 0.02 per leg), or None for a bad-data day."""

    def __init__(self, costs: dict[date, float | None], spot: float = 275.0):
        self.days = DAYS
        self.spot = {d: spot for d in DAYS}
        self.costs = costs

    def path(self, c: Condor):
        out = {}
        for d, cost in self.costs.items():
            if cost is None:
                out[d] = (LegQuote(0.5, 0.5),) * 4                            # locked quotes: fails data check
            else:
                # shorts' asks sum to cost + longs' bids; longs bid 0.01 each
                out[d] = (LegQuote(cost / 2 + 0.01 - 0.02, cost / 2 + 0.01), LegQuote(0.01, 0.03),
                          LegQuote(cost / 2 + 0.01 - 0.02, cost / 2 + 0.01), LegQuote(0.01, 0.03))
        return out


def _decisions(days, **kw):
    return {d: Decision(d, PARAMS, _condor(d, **kw), "built", "built") for d in days}


def _run(costs, exit_params, entry_days, entry_to=None, **kw):
    m = FakeMarket(costs)
    s = Setting(PARAMS, exit_params)
    return simulate(s, _decisions(entry_days, **kw), m, DAYS[0], entry_to or entry_days[-1], set())


def test_profit_target_pnl_includes_fees_both_sides():
    c = _condor(START)
    assert c.credit == pytest.approx(1.6)
    costs = {DAYS[0]: 1.6, DAYS[1]: 1.2, DAYS[2]: 0.8}
    r = _run(costs, ExitParams(0.50, 2.0), [DAYS[0]])
    t = r.trades[0]
    assert t.exit_reason == "profit_target" and t.exit_day == DAYS[2]
    assert t.pnl == pytest.approx((1.6 - 0.8) * 100 * 2 - 2 * FEE * 4 * 2)
    assert t.pnl_stress < t.pnl < t.pnl_optimistic


def test_stop_checked_before_target_and_no_exit_on_entry_day():
    costs = {DAYS[0]: 10.0, DAYS[1]: 4.8}                        # entry-day mark ignored for exits
    t = _run(costs, ExitParams(0.50, 2.0), [DAYS[0]]).trades[0]
    assert t.exit_day == DAYS[1] and t.exit_reason == "stop"     # 4.8 >= 3 × 1.6


def test_bad_data_day_carries_mark_and_skips_exit():
    costs = {DAYS[0]: 1.6, DAYS[1]: None, DAYS[2]: 0.7}
    r = _run(costs, ExitParams(0.50, None), [DAYS[0]])
    t = r.trades[0]
    assert t.exit_day == DAYS[2]
    assert r.equity[DAYS[1]] == pytest.approx(r.equity[DAYS[0]])  # mark carried forward


def test_time_exit_at_21_days():
    c = _condor(DAYS[0])
    costs = {d: 1.5 for d in DAYS if d <= c.expiry}
    t = _run(costs, ExitParams(None, None), [DAYS[0]]).trades[0]
    assert t.exit_reason == "time_exit" and (c.expiry - t.exit_day).days <= 21
    assert (c.expiry - DAYS[DAYS.index(t.exit_day) - 1]).days > 21


def test_weekly_ladder_max_one_per_week_and_three_open():
    costs = {d: 1.5 for d in DAYS}
    r = _run(costs, ExitParams(None, None), DAYS[:40], entry_to=DAYS[39])
    entries = sorted(t.condor.day for t in r.trades)
    weeks = [d.isocalendar()[:2] for d in entries]
    assert len(weeks) == len(set(weeks))
    for d in DAYS:
        open_on_d = [t for t in r.trades if t.condor.day <= d and (t.exit_day is None or t.exit_day > d)]
        assert len(open_on_d) <= 3
    assert entries[0] == DAYS[0] and entries[1] == DAYS[5] and entries[2] == DAYS[10]


def test_max_drawdown():
    eq = {DAYS[0]: 100, DAYS[1]: 110, DAYS[2]: 95, DAYS[3]: 120, DAYS[4]: 112}
    assert max_drawdown(eq) == (15, DAYS[2])


def _fake_result(s, n, pnl):
    c = _condor(START)
    trades = []
    for _ in range(n):
        t = Trade(s, c)
        t.pnl = pnl
        trades.append(t)
    return RunResult(s, trades)


def test_choose_needs_60_trades_and_smooths_over_scored_neighbours_only():
    a = Setting(CondorParams(45, 0.20, 5.0), ExitParams(0.5, 2.0))
    b = Setting(CondorParams(30, 0.20, 5.0), ExitParams(0.5, 2.0))          # neighbour of a
    thin = Setting(CondorParams(45, 0.16, 5.0), ExitParams(0.5, 2.0))       # neighbour of a, too few trades
    lone = Setting(CondorParams(60, 0.10, 10.0), ExitParams(None, None))    # great score but few trades
    res = {a: _fake_result(a, 80, 50.0), b: _fake_result(b, 80, -10.0), thin: _fake_result(thin, 10, -500.0),
           lone: _fake_result(lone, 59, 300.0)}
    best, raw, smooth = choose(res)
    assert raw[lone] is None and raw[thin] is None
    assert best == a
    assert smooth[a] == pytest.approx((raw[a] + raw[b]) / 2)                 # thin neighbour left out
    assert b in neighbours(a, ("dte",)) and thin in neighbours(a, ("delta",))
