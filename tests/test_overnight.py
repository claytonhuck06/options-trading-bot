"""Holding positions overnight: multi-day risk pool, overnight exit rules, early assignment,
gap-aware daily loss, and the quiet period right after the open."""

from datetime import date, datetime, timedelta, timezone

import pytest

import execution.trader as trader_mod
from core.config import load_settings
from data.models import OptionContract, OptionQuote, QualityResult, UnderlyingQuote
from data.storage.db import MarketDataStore
from execution.assignment import detect_assignments
from execution.broker_api import BrokerPosition, SimBehaviour, SimulatedBroker
from execution.trader import TradingSession
from risk.exits import evaluate_exit
from risk.overnight import assignment_risk, next_ex_dividend, third_friday
from risk.positions import LegQuote, OptionLeg, Position, Portfolio
from risk.risk_engine import approve
from risk.state import Level, RiskStateStore
from tests.test_risk_engine import NOW, QUOTES, S, ctx, leg

EXP = date(2026, 11, 6)


def iron_fly(k=768, wing=5, n=1, pid="fly", opened=NOW, overnight=True):
    legs = [leg(k - wing, n, "put"), leg(k, -n, "put"), leg(k, -n, "call"), leg(k + wing, n, "call")]
    p = Position(pid, "SPY", legs, opened, 0, 0, 0, entry_fees=0.2 * n, exit_fees=0.2 * n, hold_overnight=overnight)
    from risk.positions import max_defined_loss
    p.max_loss = max_defined_loss(p)
    p.profit_target, p.stop_loss = 0.2 * p.max_loss, 0.5 * p.max_loss
    return p


def fly_request(rid, multi_day):
    from risk.risk_engine import TradeRequest
    return TradeRequest(rid, "SPY", (leg(763, 1, "put"), leg(768, -1, "put"), leg(768, -1, "call"), leg(773, 1, "call")),
                        0.40, 10, None, multi_day=multi_day)


# --- risk pool -------------------------------------------------------------------

def test_multi_day_trade_uses_its_own_3pct_budget():
    from risk.state import RiskStateStore
    from data.storage.db import MarketDataStore
    st = RiskStateStore(MarketDataStore(":memory:"))
    same_day = approve(fly_request("a", False), ctx(st))
    multi = approve(fly_request("b", True), ctx(st))
    assert same_day.position.max_loss <= 0.005 * 20_000                                        # 0.5% of $20k = $100
    assert multi.approved and multi.contracts > same_day.contracts
    assert 0.005 * 20_000 < multi.position.max_loss <= 0.03 * 20_000                            # 3% of $20k = $600
    assert multi.position.hold_overnight and not same_day.position.hold_overnight


def test_multi_day_limited_to_one_open_position():
    from risk.state import RiskStateStore
    from data.storage.db import MarketDataStore
    st = RiskStateStore(MarketDataStore(":memory:"))
    d = approve(fly_request("c", True), ctx(st, portfolio=Portfolio([iron_fly()])))
    assert not d.approved and "max_concurrent_positions" in d.reasons


def test_multi_day_refused_when_overnight_not_allowed(config_dir, edit_yaml):
    from risk.state import RiskStateStore
    from data.storage.db import MarketDataStore
    edit_yaml(config_dir / "risk_limits.yaml", allow_overnight_positions=False)
    s = load_settings("development", config_dir=config_dir)
    d = approve(fly_request("d", True), ctx(RiskStateStore(MarketDataStore(":memory:")), settings=s))
    assert not d.approved and "overnight_positions_not_allowed" in d.reasons


def test_overnight_not_allowed_in_live(config_dir):
    from core.config import ConfigError
    with pytest.raises(ConfigError):
        load_settings("live", config_dir=config_dir)


# --- exit rules ----------------------------------------------------------------------

AFTER_CLOSE_RULE = datetime(2026, 10, 20, 19, 35, tzinfo=timezone.utc)      # 15:35 ET


def test_overnight_position_survives_the_close_rule_but_same_day_does_not():
    assert evaluate_exit(iron_fly(), QUOTES, AFTER_CLOSE_RULE, S, spot=768.0).close is False
    assert evaluate_exit(iron_fly(overnight=False), QUOTES, AFTER_CLOSE_RULE, S, spot=768.0).reason == "market_close"


def test_overnight_position_ignores_same_day_time_limit():
    late = NOW + timedelta(hours=3)
    assert evaluate_exit(iron_fly(), QUOTES, late, S, spot=768.0).close is False


def test_closed_before_final_week():
    near = datetime(2026, 10, 30, 15, 0, tzinfo=timezone.utc)                # 7 days before the Nov 6 expiry
    assert evaluate_exit(iron_fly(), QUOTES, near, S, spot=768.0).reason == "near_expiry"


def test_emergency_still_closes_overnight_positions():
    assert evaluate_exit(iron_fly(), QUOTES, NOW, S, Level.HALTED, spot=768.0).reason == "emergency"


def test_third_friday_and_ex_dividend():
    assert third_friday(2026, 12) == date(2026, 12, 18)
    assert next_ex_dividend(date(2026, 10, 20), S.risk.multiday) == date(2026, 12, 18)


def test_dividend_assignment_risk_day_before_ex_dividend():
    day_before = date(2026, 12, 17)
    deep_itm_call = {**QUOTES, "C768": LegQuote(12.40, 12.60, 0.14, NOW)}   # spot 780: $12 intrinsic, $0.50 time value
    assert assignment_risk(iron_fly(), deep_itm_call, 780.0, day_before, S.risk.multiday) == "dividend_assignment_risk"
    assert assignment_risk(iron_fly(), deep_itm_call, 780.0, date(2026, 12, 10), S.risk.multiday) is None


def test_early_assignment_risk_when_time_value_gone():
    no_time_value = {**QUOTES, "P768": LegQuote(17.98, 18.04, 0.14, NOW)}   # spot 750: $18 intrinsic, ~$0.01 time value
    assert assignment_risk(iron_fly(), no_time_value, 750.0, date(2026, 10, 21), S.risk.multiday) == "early_assignment_risk"


def test_gap_stop_after_overnight_gap():
    opened_yesterday = iron_fly(opened=NOW - timedelta(days=1))
    opening = datetime(2026, 10, 20, 13, 40, tzinfo=timezone.utc)            # 9:40 ET
    blown = {**QUOTES, "C768": LegQuote(9.0, 9.2, 0.14, opening), "C773": LegQuote(5.0, 5.2, 0.14, opening)}
    assert evaluate_exit(opened_yesterday, blown, opening, S, spot=776.0).reason == "gap_stop"
    assert evaluate_exit(opened_yesterday, blown, NOW, S, spot=776.0).reason == "stop_loss"


# --- assignment detection --------------------------------------------------------------

def _broker(**qty):
    return [BrokerPosition(s, q, 1.0, "us_equity" if s == "SPY" else "us_option") for s, q in qty.items()]


def test_detects_short_call_assignment():
    fly = iron_fly()
    got = detect_assignments([fly], _broker(P763=1, P768=-1, C773=1, SPY=-100), "SPY")
    assert [(a.symbol, a.contracts, a.shares) for a in got] == [("C768", 1, -100)]


def test_detects_short_put_assignment():
    got = detect_assignments([iron_fly(n=2)], _broker(P763=2, C768=-2, C773=2, SPY=200), "SPY")
    assert [(a.symbol, a.contracts, a.shares) for a in got] == [("P768", 2, 200)]


@pytest.mark.parametrize("broker", [
    dict(P763=1, P768=-1, C773=1, SPY=-50),            # share count does not match
    dict(P768=-1, C768=-1, C773=1, SPY=100),           # a LONG leg disappeared
    dict(P763=1, P768=-1, C768=-1, C773=1, SPY=100),   # shares with nothing missing
])
def test_unexplained_differences_are_not_assignments(broker):
    assert detect_assignments([iron_fly()], _broker(**broker), "SPY") == []


# --- trading session ---------------------------------------------------------------------

SYMS = {"P763": ("put", 763.0), "P768": ("put", 768.0), "C768": ("call", 768.0), "C773": ("call", 773.0)}


@pytest.fixture
def paper(config_dir, edit_yaml):
    edit_yaml(config_dir / "environments" / "paper.yaml", trading__enabled=True, trading__allow_order_submission=True)
    return load_settings("paper", config_dir=config_dir)


def market(store, at, spot=768.0):
    store.upsert_contracts([OptionContract(s, "SPY", "SPY", k, "american", K, EXP, 100, "active", True) for s, (k, K) in SYMS.items()])
    cid = store.start_cycle("SPY", "indicative")
    store.insert_underlying_quote(cid, UnderlyingQuote("SPY", "iex", spot - 0.01, spot + 0.01, 1, 1, at, spot, at, at), QualityResult())
    store.insert_option_quotes(cid, [(OptionQuote(s, "indicative", QUOTES[s].bid, QUOTES[s].ask, 50, 50, "Q", "Q", at, at,
                                                  implied_volatility=0.14), QualityResult()) for s in SYMS])


def make_session(settings, broker, now, data_at=None, monkeypatch=None, tmp_path=None):
    store = MarketDataStore(":memory:")
    market(store, data_at or now)
    return TradingSession(settings, store, broker, now=lambda: now, sleep=lambda _: None)


def hold(session, broker, pos):
    session.book.open_position(pos)
    for l in pos.legs:
        broker.add_position(l.symbol, l.quantity)


def test_early_assignment_is_unwound_instead_of_halting(paper, monkeypatch, tmp_path, alerts_sent):
    monkeypatch.setattr(trader_mod, "STOP_FILE", tmp_path / "STOP")
    broker = SimulatedBroker(equity=100_000, leg_prices={s: QUOTES[s].mid for s in SYMS} | {"SPY": 768.0})
    s = make_session(paper, broker, NOW)
    hold(s, broker, iron_fly(opened=NOW - timedelta(days=1)))
    broker.add_position("C768", 1)            # the short call was assigned overnight...
    broker.add_position("SPY", -100)          # ...and 100 short shares appeared
    broker.queue(SimBehaviour("fill"), SimBehaviour("fill"))
    rep = s.cycle()
    assert rep.level == "NORMAL" and rep.reconciled
    assert any("early assignment" in n for n in rep.notes)
    assert any("assignment shares buy 100: filled" in e for e in rep.exits)
    assert any("early_assignment: filled" in e for e in rep.exits)
    assert broker.positions() == [] and s.book.open_positions() == []
    assert any(a["title"] == "Early assignment" and a["priority"] == "5" for a in alerts_sent)
    row = s.store.conn.execute("SELECT exit_reason, realized_pnl FROM positions_book").fetchone()
    assert row[0] == "early_assignment" and row[1] is not None


def test_overnight_gap_counts_against_todays_loss_limit(paper, monkeypatch, tmp_path, alerts_sent):
    monkeypatch.setattr(trader_mod, "STOP_FILE", tmp_path / "STOP")
    broker = SimulatedBroker(equity=98_000)
    broker._last_equity = 100_000             # yesterday's close: a $2,000 (2%) overnight drop
    s = make_session(paper, broker, NOW)
    rep = s.cycle()
    assert "daily_loss_limit" in rep.triggers and rep.level == "FLATTEN_TODAY"
    assert any("Closing everything" in a["title"] for a in alerts_sent)


def test_stale_data_right_after_the_open_waits_instead_of_halting(paper, monkeypatch, tmp_path):
    monkeypatch.setattr(trader_mod, "STOP_FILE", tmp_path / "STOP")
    opening = datetime(2026, 10, 20, 13, 31, tzinfo=timezone.utc)            # 9:31 ET
    broker = SimulatedBroker(equity=100_000)
    s = make_session(paper, broker, opening, data_at=opening - timedelta(hours=17))   # yesterday's quotes
    hold(s, broker, iron_fly(opened=NOW - timedelta(days=1)))
    rep = s.cycle()
    assert rep.level == "NORMAL" and "waiting for fresh market data after the open" in rep.notes
    assert rep.exits == []


def test_overnight_position_not_touched_until_open_settles(paper, monkeypatch, tmp_path):
    monkeypatch.setattr(trader_mod, "STOP_FILE", tmp_path / "STOP")
    opening = datetime(2026, 10, 20, 13, 32, tzinfo=timezone.utc)            # 9:32 ET, fresh data
    broker = SimulatedBroker(equity=100_000)
    s = make_session(paper, broker, opening)
    hold(s, broker, iron_fly(opened=NOW - timedelta(days=1)))
    rep = s.cycle()
    assert any("waiting for the open to settle" in n for n in rep.notes) and rep.exits == []


def test_kill_switch_escalation_pushes_one_alert(paper, monkeypatch, tmp_path, alerts_sent):
    stop = tmp_path / "STOP"
    stop.write_text("x")
    monkeypatch.setattr(trader_mod, "STOP_FILE", stop)
    s = make_session(paper, SimulatedBroker(equity=100_000), NOW)
    s.cycle()
    s.cycle()
    halted = [a for a in alerts_sent if "HALTED" in a["title"]]
    assert len(halted) == 1 and halted[0]["priority"] == "5"


def test_startup_recognises_overnight_assignment(paper, monkeypatch, tmp_path, alerts_sent):
    monkeypatch.setattr(trader_mod, "STOP_FILE", tmp_path / "STOP")
    broker = SimulatedBroker(equity=100_000)
    s = make_session(paper, broker, NOW)
    hold(s, broker, iron_fly(opened=NOW - timedelta(days=1)))
    broker.add_position("P768", 1)
    broker.add_position("SPY", 100)
    rep = s.startup()
    assert rep.safe_to_resume
    assert s.state.level(NOW.date()) == Level.NORMAL
    assert s._assignment_shares() == 100


def test_multi_day_position_gets_room_in_the_daily_loss_limit(paper, monkeypatch, tmp_path):
    """A $2,000 drop with a multi-day position open does not force everything closed: that position's loss is
    already capped by its defined risk and handled by its own stop. (Without one, the same drop does: see
    test_overnight_gap_counts_against_todays_loss_limit.)"""
    monkeypatch.setattr(trader_mod, "STOP_FILE", tmp_path / "STOP")
    broker = SimulatedBroker(equity=98_000)
    broker._last_equity = 100_000
    s = make_session(paper, broker, NOW)
    fly = iron_fly(n=20, opened=NOW - timedelta(days=1))
    assert fly.max_loss > 500                       # 1.5% of $100k ($1,500) + this position's max loss > $2,000
    hold(s, broker, fly)
    rep = s.cycle()
    assert "daily_loss_limit" not in rep.triggers and rep.level == "NORMAL"


def test_daily_loss_beyond_the_multi_day_allowance_still_flattens(paper, monkeypatch, tmp_path):
    monkeypatch.setattr(trader_mod, "STOP_FILE", tmp_path / "STOP")
    fly = iron_fly(n=1, opened=NOW - timedelta(days=1))
    broker = SimulatedBroker(equity=100_000 - 1_500 - fly.max_loss - 1)
    broker._last_equity = 100_000
    s = make_session(paper, broker, NOW)
    hold(s, broker, fly)
    rep = s.cycle()
    assert "daily_loss_limit" in rep.triggers and rep.level == "FLATTEN_TODAY"
