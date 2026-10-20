"""SPY trend call spreads: signal (no look-ahead), spread builder, P&L arithmetic, exit order, bar-8 mode."""

from datetime import date, timedelta

import pytest

from research.condor_backtest import FEE, LegQuote
from research.trend_backtest import (
    ExitParams, Setting, Trade, choose, neighbours, simulate, value_to_close,
)
from strategies.iron_condor import ChainRow, Leg
from strategies.spy_trend import (
    SIGNAL_LIGHTS, SIGNAL_MOMENTUM, SIGNAL_NONE, CallSpread, SpreadDecision, TrendParams, TrendSignal,
    build_call_spread,
)

START = date(2019, 1, 7)                                                   # a Monday
DAYS = [START + timedelta(days=i) for i in range(120) if (START + timedelta(days=i)).weekday() < 5]
PARAMS = TrendParams(45, 0.50, 0.30)


# --- Signal --------------------------------------------------------------------------------------

def _closes(values, start=date(2010, 1, 4)):
    days, d = [], start
    while len(days) < len(values):
        if d.weekday() < 5:
            days.append(d)
        d += timedelta(days=1)
    return dict(zip(days, values))


def test_signal_needs_history_and_counts_missing_as_risk_off():
    sig = TrendSignal(_closes([100.0] * 150))
    day = max(sig.days) + timedelta(days=1)
    assert sig.state(day).below_trend is None
    assert not sig.risk_on(day, SIGNAL_LIGHTS)
    assert sig.risk_on(day, SIGNAL_NONE)


def test_steady_uptrend_is_risk_on_and_drop_below_average_turns_it_off():
    vals = [100 * 1.001 ** i for i in range(300)]
    closes = _closes(vals)
    sig = TrendSignal(closes)
    last = max(closes)
    nxt = last + timedelta(days=3)
    assert sig.risk_on(nxt, SIGNAL_LIGHTS) and sig.risk_on(nxt, SIGNAL_MOMENTUM)
    closes2 = dict(closes)
    closes2[nxt] = vals[-1] * 0.85                                     # big drop on day `nxt`
    sig2 = TrendSignal(closes2)
    assert sig2.risk_on(nxt, SIGNAL_LIGHTS), "the drop on day t must not be visible on day t (no look-ahead)"
    after = nxt + timedelta(days=1)
    assert sig2.state(after).below_trend is True
    assert not sig2.risk_on(after, SIGNAL_LIGHTS)


def test_wild_market_turns_on_volatility_light():
    vals = [100 * 1.001 ** i for i in range(280)] + [130 if i % 2 else 120 for i in range(25)]
    sig = TrendSignal(_closes(vals))
    st = sig.state(max(sig.days) + timedelta(days=1))
    assert st.too_wild is True


def test_falling_market_fails_momentum_variant_only():
    vals = [200 * 0.999 ** i for i in range(260)] + [200 * 0.999 ** 260 * 1.004 ** i for i in range(40)]
    sig = TrendSignal(_closes(vals))
    st = sig.state(max(sig.days) + timedelta(days=1))
    assert st.momentum is not None and st.momentum < 0


# --- Builder -------------------------------------------------------------------------------------

def _chain(day: date):
    """Calls on 280..320 with vendor deltas; puts present for the forward fit."""
    rows = []
    for k in range(280, 321, 5):
        d = max(0.02, min(0.98, 0.5 - (k - 300) * 0.02))
        c_mid = max(0.2, 300 - k + 6 * d)
        p_mid = max(0.2, k - 300 + 6 * (1 - d))
        rows.append(ChainRow(float(k), round(c_mid - 0.05, 2), round(c_mid + 0.05, 2), None, d,
                             round(p_mid - 0.05, 2), round(p_mid + 0.05, 2), None, d - 1))
    return {day + timedelta(days=45): rows}


def test_builder_picks_strikes_by_delta_and_sizes_to_one_percent():
    day = date(2019, 3, 4)
    dec = build_call_spread(day, PARAMS, 300.0, _chain(day), 0.02)
    assert dec.spread is not None, dec.reason
    s = dec.spread
    assert s.long_call.strike < s.short_call.strike
    assert s.debit == pytest.approx(s.long_call.ask - s.short_call.bid)
    assert s.contracts == int(1000 // (s.debit * 100))
    assert s.planned_max_loss <= 1000


def test_builder_rejects_when_no_expiry_near_target():
    day = date(2019, 3, 4)
    dec = build_call_spread(day, TrendParams(30, 0.5, 0.3), 300.0, _chain(day), 0.02)
    assert dec.spread is None and dec.code == "no_expiry"


# --- Backtest ------------------------------------------------------------------------------------

def _spread(day: date, debit=2.0, contracts=5) -> CallSpread:
    exp = day + timedelta(days=45)
    return CallSpread(day, exp, 45, Leg("C", 300, 1, debit + 0.4, debit + 0.5, 0.5, "own"),
                      Leg("C", 305, -1, 0.5, 0.6, 0.3, "own"), debit, contracts)


class FakeMarket:
    """value path: day -> spread value (each leg 0.10 wide), or None for a bad-data day."""

    def __init__(self, values, spot=300.0):
        self.days = DAYS
        self.spot = {d: spot for d in DAYS}
        self.values = values

    def path(self, s):
        out = {}
        for d, v in self.values.items():
            out[d] = (LegQuote(0.5, 0.5),) * 2 if v is None else (LegQuote(v + 1.0, v + 1.1), LegQuote(0.9, 1.0))
        return out


class FakeSignal:
    def __init__(self, off_from=None):
        self.off_from = off_from

    def risk_on(self, day, variant):
        return variant == SIGNAL_NONE or self.off_from is None or day < self.off_from


def _setting(pt=0.5, stop=0.5, signal=SIGNAL_LIGHTS):
    return Setting(signal, PARAMS, ExitParams(pt, stop))


def _run(values, setting, signal=None, entry_to=None):
    s = _spread(DAYS[0])
    decisions = {DAYS[0]: SpreadDecision(DAYS[0], PARAMS, s, "built", "built")}
    return simulate(setting, decisions, FakeMarket(values), signal or FakeSignal(), DAYS[0], entry_to or DAYS[0], set())


def test_value_to_close_uses_bid_of_long_and_ask_of_short():
    assert value_to_close((LegQuote(3.0, 3.1), LegQuote(1.0, 1.2))) == pytest.approx((1.8, 0.3))
    assert value_to_close((LegQuote(0.5, 0.5), LegQuote(1.0, 1.2))) is None


def test_trade_pnl_arithmetic_and_costs():
    t = Trade(_setting(), _spread(DAYS[0], debit=2.0, contracts=5))
    t.close(DAYS[5], "profit_target", 3.0, 0.2)
    fees = FEE * 2 * 5 * 2
    assert t.pnl == pytest.approx((3.0 - 2.0) * 100 * 5 - fees)
    assert t.pnl_stress == pytest.approx(((3.0 - 0.05) - (2.0 + 0.25 * 0.2)) * 100 * 5 - fees)
    assert t.pnl < t.pnl_optimistic


def test_profit_target_then_stop():
    r = _run({DAYS[1]: 2.1, DAYS[2]: 3.0}, _setting(pt=0.5))
    assert r.trades[0].exit_reason == "profit_target" and r.trades[0].exit_day == DAYS[2]
    r = _run({DAYS[1]: 1.0}, _setting(stop=0.5))
    assert r.trades[0].exit_reason == "stop"


def test_trend_exit_and_no_signal_mode():
    vals = {d: 2.05 for d in DAYS[1:30]}
    r = _run(vals, _setting(pt=None, stop=None), FakeSignal(off_from=DAYS[3]))
    assert r.trades[0].exit_reason == "trend_exit" and r.trades[0].exit_day == DAYS[3]
    r = _run(vals, _setting(pt=None, stop=None, signal=SIGNAL_NONE), FakeSignal(off_from=DAYS[3]))
    assert r.trades[0].exit_reason == "time_exit"


def test_bad_data_day_carries_forward():
    r = _run({DAYS[1]: None, DAYS[2]: 3.5}, _setting(pt=0.5))
    t = r.trades[0]
    assert t.exit_day == DAYS[2] and t.gap_days == 1


def test_no_entry_when_risk_off():
    r = _run({DAYS[1]: 3.0}, _setting(), FakeSignal(off_from=DAYS[0]))
    assert r.trades == [] and r.unfinished == 0


def test_neighbours_and_choose_skip_thin_settings():
    s = _setting()
    nb = neighbours(s, ("dte", "long", "short"))
    assert {(n.entry.target_dte, n.entry.long_delta, n.entry.short_delta) for n in nb} == \
        {(30, 0.5, 0.3), (60, 0.5, 0.3), (45, 0.7, 0.3), (45, 0.5, 0.2)}
    from research.trend_backtest import RunResult
    best, raw, _ = choose({s: RunResult(s, [])})
    assert best is None and raw[s] is None
