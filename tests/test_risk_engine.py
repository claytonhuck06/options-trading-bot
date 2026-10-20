"""Risk engine, kill switch, exits, hedging — including failure scenarios."""

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import pytest

from core.config import load_settings
from data.storage.db import MarketDataStore
from pricing.black_scholes import price
from risk.exits import evaluate_exit
from risk.hedging import choose_hedge
from risk.kill_switch import HealthInputs, evaluate
from risk.position_sizing import size_contracts
from risk.positions import LegQuote, OptionLeg, Portfolio, Position, StockHedge, exposure, max_defined_loss, unrealized_pnl
from risk.risk_engine import RiskContext, TradeRequest, approval_is_valid, approve
from risk.state import RESET_PHRASE, Level, RiskStateStore
from risk.stress import DEFAULT

S = load_settings()
NOW = datetime(2026, 10, 20, 15, 0, tzinfo=timezone.utc)          # 11:00 ET
TODAY = date(2026, 10, 20)
EXP = date(2026, 11, 6)
SPOT = 768.0


def q(K, kind="call", iv=0.14, half=0.02, age=2):
    from risk.positions import years_left
    p = price(SPOT, K, years_left(EXP, NOW), 0.04, 0.0, iv, kind)
    return LegQuote(round(p - half, 2), round(p + half, 2), iv, NOW - timedelta(seconds=age))


QUOTES = {f"C{k}": q(k) for k in range(740, 800)} | {f"P{k}": q(k, "put") for k in range(740, 800)}


def leg(K, n, kind="call"):
    sym = f"{kind[0].upper()}{K}"
    qt = QUOTES[sym]
    return OptionLeg(sym, "SPY", kind, float(K), EXP, n, qt.ask if n > 0 else qt.bid)


def request(rid="r1", k1=770, k2=771, n=10, kind="call"):
    return TradeRequest(rid, "SPY", (leg(k1, 1, kind), leg(k2, -1, kind)), 0.20, n, f"{kind[0].upper()}{k1}")


@pytest.fixture
def state():
    st = RiskStateStore(MarketDataStore(":memory:"))
    st.start_day(TODAY, 20_000)
    return st


def ctx(state, **kw):
    day = state.start_day(TODAY, 20_000)
    base = dict(now=NOW, settings=S, state=state, day=day, equity=20_000, level=Level.NORMAL, portfolio=Portfolio(),
                quotes=QUOTES, spots={"SPY": SPOT}, rate=0.04, stress=DEFAULT, data_age_seconds=2)
    base.update(kw)
    return RiskContext(**base)


def open_position(pid, k1, k2, n, kind="call"):
    p = Position(pid, "SPY", [leg(k1, n, kind), leg(k2, -n, kind)], NOW, 0, 0, 0, entry_fees=0.1 * n, exit_fees=0.1 * n)
    p.max_loss = max_defined_loss(p)
    p.profit_target, p.stop_loss = 0.2 * p.max_loss, 0.5 * p.max_loss
    return p


# --- sizing --------------------------------------------------------------------

def test_sizing_never_exceeds_budgets():
    assert size_contracts(30, 100, 500) == 3
    assert size_contracts(30, 100, 50) == 1
    assert size_contracts(120, 100, 500) == 0
    assert size_contracts(0, 100, 500) == 0


def test_max_defined_loss_vertical():
    p = open_position("x", 770, 775, 1)
    debit = (QUOTES["C770"].ask - QUOTES["C775"].bid) * 100
    assert p.max_loss == pytest.approx(debit + 0.2)
    credit = open_position("y", 775, 770, 1)               # short the lower strike: credit spread
    assert credit.max_loss == pytest.approx(500 - (QUOTES["C770"].bid - QUOTES["C775"].ask) * 100 + 0.2)


# --- approvals ------------------------------------------------------------------

def test_approves_and_resizes_to_per_trade_limit(state):
    d = approve(request(n=50), ctx(state))
    assert d.approved and 1 <= d.contracts < 50 and "resized" in d.reasons
    assert d.position.max_loss <= 0.015 * 20_000 + 1e-9
    assert d.position.profit_target == pytest.approx(0.2 * d.position.max_loss)
    assert approval_is_valid(d, NOW) and not approval_is_valid(d, NOW + timedelta(seconds=S.risk.approval_valid_seconds + 1))


def test_duplicate_request_refused(state):
    assert approve(request("same"), ctx(state)).approved
    d = approve(request("same"), ctx(state))
    assert not d.approved and d.reasons == ["duplicate_request"]


def test_uncovered_short_never_approved(state):
    naked = TradeRequest("naked", "SPY", (leg(770, -1),), 0.1, 1)
    d = approve(naked, ctx(state))
    assert not d.approved and "uncovered_short_option" in d.reasons


@pytest.mark.parametrize("change, reason", [
    (dict(level=Level.NO_NEW_ENTRIES), "kill_switch_no_new_entries"),
    (dict(level=Level.HALTED), "kill_switch_halted"),
    (dict(data_age_seconds=120), "market_data_stale"),
    (dict(data_age_seconds=None), "market_data_stale"),
    (dict(now=datetime(2026, 10, 20, 19, 10, tzinfo=timezone.utc)), "outside_entry_window"),
    (dict(equity=20_000 - 300), "daily_loss_limit_reached"),
    (dict(equity=21_000), "daily_profit_ceiling_reached"),
])
def test_refusals(state, change, reason):
    d = approve(request(), ctx(state, **change))
    assert not d.approved and reason in d.reasons


def test_max_concurrent_positions(state):
    pf = Portfolio([open_position(f"p{i}", 770 + i, 771 + i, 1) for i in range(3)])
    assert "max_concurrent_positions" in approve(request(), ctx(state, portfolio=pf)).reasons


def test_aggregate_risk_budget(state):
    big = open_position("big", 780, 781, 1)                  # small exposure; only its budget use matters here
    big.max_loss = 880                                       # $880 of the $900 aggregate budget already used
    one_spread = request().legs
    per_spread = (one_spread[0].entry_price - one_spread[1].entry_price) * 100 + 0.20
    assert per_spread > 20                                   # so not even one more spread fits
    d = approve(request(n=10), ctx(state, portfolio=Portfolio([big])))
    assert not d.approved and d.reasons == ["risk_budget_exhausted"]
    big.max_loss = 800                                       # $100 left: limited by per-trade AND aggregate
    huge = request("r3", 760, 780)                           # a $20-wide spread can never fit the $300 per-trade limit
    assert approve(huge, ctx(state, portfolio=Portfolio([big]))).reasons == ["trade_larger_than_per_trade_limit"]
    d = approve(request("r2", n=10), ctx(state, portfolio=Portfolio([big])))
    assert d.approved and d.position.max_loss <= 100 + 1e-9


def test_correlated_exposure_counts_against_delta_limit(state):
    # Long SPY shares already near the delta limit: a bullish spread must be cut down or refused.
    hedges = [StockHedge("SPY", 19, SPOT)]                   # ~$14,600 of SPY delta vs a $15,000 limit
    d = approve(request(n=10), ctx(state, portfolio=Portfolio(hedges=hedges)))
    assert not d.approved and "portfolio_delta_limit" in d.reasons


def test_stress_check_uses_remaining_daily_allowance(state):
    # Already down $290 of a $300 daily limit: almost no stress allowance left.
    d = approve(request(n=3), ctx(state, equity=19_710))
    assert not d.approved and "stress_loss_exceeds_daily_allowance" in d.reasons


# --- kill switch ----------------------------------------------------------------

def health(**kw):
    base = dict(equity=20_000, open_positions=1, data_age_seconds=2)
    base.update(kw)
    return HealthInputs(**base)


@pytest.mark.parametrize("inputs, level, trigger", [
    (health(equity=19_700), Level.FLATTEN_TODAY, "daily_loss_limit"),
    (health(equity=21_000), Level.FLATTEN_TODAY, "daily_profit_ceiling"),
    (health(data_age_seconds=300), Level.HALTED, "market_data_unreliable_with_positions"),
    (health(data_age_seconds=None), Level.HALTED, "market_data_unreliable_with_positions"),
    (health(reconciled=False), Level.HALTED, "broker_reconciliation_failed"),
    (health(consecutive_execution_failures=3), Level.HALTED, "repeated_execution_failures"),
    (health(unexpected_positions=["AAPL"]), Level.HALTED, "unexpected_position"),
    (health(uncovered_shorts=["SPY261106C00771000"]), Level.HALTED, "uncovered_short_option"),
    (health(risk_calc_ok=False), Level.HALTED, "risk_calculation_failed"),
    (health(manual_stop=True), Level.HALTED, "manual_stop"),
])
def test_kill_switch_triggers(state, inputs, level, trigger):
    r = evaluate(inputs, state.start_day(TODAY, 20_000), S.risk, state, TODAY)
    assert r.level == level and trigger in r.triggers


def test_stale_data_without_positions_is_not_an_emergency(state):
    r = evaluate(health(open_positions=0, data_age_seconds=None), state.start_day(TODAY, 20_000), S.risk, state, TODAY)
    assert r.level == Level.NORMAL


def test_drawdown_levels_from_high_water_mark():
    st = RiskStateStore(MarketDataStore(":memory:"))
    st.start_day(date(2026, 10, 19), 25_000)                 # high-water mark 25k
    day = st.start_day(TODAY, 23_700)
    r = evaluate(health(equity=23_700), day, S.risk, st, TODAY)       # 5.2% below 25k
    assert r.level == Level.NO_NEW_ENTRIES
    st.update_high_water_mark(25_000)
    r = evaluate(health(equity=22_400), day, S.risk, st, TODAY)       # 10.4% below 25k
    assert r.level == Level.HALTED


def test_warning_clears_but_halt_needs_manual_reset(state):
    state.escalate(Level.NO_NEW_ENTRIES, "drawdown_warning", TODAY)
    assert evaluate(health(), state.start_day(TODAY, 20_000), S.risk, state, TODAY).level == Level.NORMAL
    state.escalate(Level.HALTED, "manual_stop", TODAY)
    assert evaluate(health(), state.start_day(TODAY, 20_000), S.risk, state, TODAY).level == Level.HALTED
    assert state.level(TODAY + timedelta(days=1)) == Level.HALTED      # survives into the next day
    with pytest.raises(PermissionError):
        state.manual_reset("please", TODAY)
    state.manual_reset(RESET_PHRASE, TODAY)
    assert state.level(TODAY) == Level.NORMAL


def test_daily_stop_expires_next_day(state):
    state.escalate(Level.FLATTEN_TODAY, "daily_loss_limit", TODAY)
    assert state.level(TODAY) == Level.FLATTEN_TODAY
    assert state.level(TODAY + timedelta(days=1)) == Level.NORMAL


def test_levels_never_step_down_by_escalate(state):
    state.escalate(Level.HALTED, "manual_stop", TODAY)
    assert state.escalate(Level.NO_NEW_ENTRIES, "drawdown_warning", TODAY) == Level.HALTED


def test_high_water_mark_adjusts_for_cash_flows(state):
    assert state.high_water_mark() == 20_000
    assert state.record_cash_flow(-5_000) == 15_000           # a withdrawal is not a drawdown


# --- partial fills ------------------------------------------------------------

def test_partial_fill_leaving_a_naked_short_is_detected():
    p = Position("legged", "SPY", [leg(771, -1)], NOW, 0, 0, 0)        # only the short leg filled
    assert p.uncovered_shorts()
    filled = Position("ok", "SPY", [leg(770, 1), leg(771, -1)], NOW, 0, 0, 0)
    assert not filled.uncovered_shorts()


# --- exits ------------------------------------------------------------------

def test_exit_rules_in_priority_order():
    p = open_position("e", 770, 775, 1)
    flat = evaluate_exit(p, QUOTES, NOW + timedelta(minutes=5), S)
    assert not flat.close and flat.pnl < 0                    # just the spread paid to enter

    def moved(dS):
        from risk.positions import years_left
        T = years_left(EXP, NOW)
        return {k: LegQuote(price(SPOT + dS, float(k[1:]), T, 0.04, 0, 0.14, "call" if k[0] == "C" else "put") - 0.02,
                            price(SPOT + dS, float(k[1:]), T, 0.04, 0, 0.14, "call" if k[0] == "C" else "put") + 0.02,
                            0.14, NOW) for k in QUOTES}

    assert evaluate_exit(p, moved(+15), NOW, S).reason == "profit_target"
    assert evaluate_exit(p, moved(-15), NOW, S).reason == "stop_loss"
    assert evaluate_exit(p, QUOTES, NOW, S, anchor_z=0.1).reason == "mispricing_gone"
    assert evaluate_exit(p, QUOTES, NOW + timedelta(minutes=121), S).reason == "time"
    assert evaluate_exit(p, QUOTES, datetime(2026, 10, 20, 19, 31, tzinfo=timezone.utc), S).reason == "market_close"
    assert evaluate_exit(p, moved(+15), NOW, S, level=Level.HALTED).reason == "emergency"


def test_unrealized_pnl_uses_executable_prices():
    p = open_position("e", 770, 775, 1)
    expected = (QUOTES["C770"].bid - QUOTES["C770"].ask - QUOTES["C775"].ask + QUOTES["C775"].bid) * 100 - 0.2
    assert unrealized_pnl(p, QUOTES) == pytest.approx(expected)


# --- hedging ------------------------------------------------------------------

def _exp(p):
    return exposure(p, QUOTES, SPOT, NOW, 0.04, 1.0, SPOT)


def test_no_action_inside_limits():
    p = open_position("a", 770, 775, 1)
    assert choose_hedge([p], {"a": _exp(p)}, 0.0, QUOTES, SPOT, 20_000, S.risk, 0.05).kind == "none"


def test_cheapest_fix_chosen_when_delta_too_large():
    p = open_position("a", 740, 790, 10)                      # deep bullish spread, big delta
    e = _exp(p)
    assert abs(e.delta_dollars) > 10_000
    action = choose_hedge([p], {"a": e}, 0.0, QUOTES, SPOT, 20_000, S.risk, 0.05)
    assert action.kind in ("hedge", "reduce", "close")
    assert abs(action.delta_after) <= 10_000
    if action.kind == "hedge":
        assert action.quantity < 0 and abs(action.quantity) <= S.risk.max_hedge_shares


def test_hedge_not_offered_beyond_share_cap():
    p = open_position("a", 740, 790, 10)
    lim = S.risk.model_copy(update={"max_hedge_shares": 1})
    action = choose_hedge([p], {"a": _exp(p)}, 0.0, QUOTES, SPOT, 20_000, lim, 0.05)
    assert action.kind in ("reduce", "close")


def test_drawdown_warning_needs_daily_acknowledgement():
    st = RiskStateStore(MarketDataStore(":memory:"))
    st.start_day(date(2026, 10, 19), 25_000)
    day = st.start_day(TODAY, 23_700)                        # 5.2% below the high-water mark
    assert evaluate(health(equity=23_700), day, S.risk, st, TODAY).level == Level.NO_NEW_ENTRIES
    st.acknowledge_warning(TODAY)
    r = evaluate(health(equity=23_700), day, S.risk, st, TODAY)
    assert r.level == Level.NORMAL and "drawdown_warning_acknowledged" in r.triggers
    tomorrow = TODAY + timedelta(days=1)
    day2 = st.start_day(tomorrow, 23_700)
    assert evaluate(health(equity=23_700), day2, S.risk, st, tomorrow).level == Level.NO_NEW_ENTRIES
