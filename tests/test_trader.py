"""Trading-session cycles end to end with the simulated broker and a synthetic market database."""

from datetime import date, datetime, timedelta, timezone

import pytest

import execution.trader as trader_mod
from core.config import load_settings
from data.models import OptionContract, OptionQuote, QualityResult, UnderlyingQuote
from data.storage.db import MarketDataStore
from execution.broker_api import SimBehaviour, SimulatedBroker
from execution.trader import TradingSession
from risk.positions import OptionLeg, Position
from risk.state import Level

NOW = datetime(2026, 10, 20, 15, 0, tzinfo=timezone.utc)      # 11:00 ET
EXP = date(2026, 11, 6)
L, S = "SPY261106C00770000", "SPY261106C00771000"


@pytest.fixture
def settings(config_dir, edit_yaml):
    edit_yaml(config_dir / "environments" / "paper.yaml", trading__enabled=True, trading__allow_order_submission=True)
    return load_settings("paper", config_dir=config_dir)


def market(store, at=NOW):
    store.upsert_contracts([OptionContract(s, "SPY", "SPY", "call", "american", k, EXP, 100, "active", True) for s, k in ((L, 770.0), (S, 771.0))])
    cid = store.start_cycle("SPY", "indicative")
    store.insert_underlying_quote(cid, UnderlyingQuote("SPY", "iex", 767.99, 768.01, 1, 1, at, 768.0, at, at), QualityResult())
    store.insert_option_quotes(cid, [
        (OptionQuote(L, "indicative", 5.00, 5.10, 50, 50, "Q", "Q", at, at, implied_volatility=0.14), QualityResult()),
        (OptionQuote(S, "indicative", 4.40, 4.50, 50, 50, "Q", "Q", at, at, implied_volatility=0.14), QualityResult()),
    ])


def session(settings, broker, now=NOW, tmp_path=None, monkeypatch=None):
    store = MarketDataStore(":memory:")
    market(store, now)
    s = TradingSession(settings, store, broker, now=lambda: now, sleep=lambda _: None)
    return s, store


def held_position(s, broker, n=1):
    pos = Position("p1", "SPY", [OptionLeg(L, "SPY", "call", 770.0, EXP, n, 5.10), OptionLeg(S, "SPY", "call", 771.0, EXP, -n, 4.40)],
                   NOW - timedelta(minutes=5), 70.2 * n, 14 * n, 35 * n, entry_fees=0.1, exit_fees=0.1)
    s.book.open_position(pos)
    broker.add_position(L, n)
    broker.add_position(S, -n)
    return pos


def test_normal_cycle_reconciles_and_holds(settings, monkeypatch, tmp_path):
    monkeypatch.setattr(trader_mod, "STOP_FILE", tmp_path / "STOP")
    broker = SimulatedBroker()
    s, _ = session(settings, broker)
    held_position(s, broker)
    rep = s.cycle()
    assert rep.reconciled and rep.level == "NORMAL" and rep.exits == []


def test_manual_stop_file_halts_and_flattens(settings, monkeypatch, tmp_path):
    stop = tmp_path / "STOP"
    stop.write_text("stop")
    monkeypatch.setattr(trader_mod, "STOP_FILE", stop)
    broker = SimulatedBroker(leg_prices={L: 5.0, S: 4.5})
    broker.queue(SimBehaviour("fill"))
    s, _ = session(settings, broker)
    held_position(s, broker)
    rep = s.cycle()
    assert rep.level == "HALTED" and "manual_stop" in rep.triggers
    assert rep.exits and "emergency" in rep.exits[0]
    assert broker.positions() == []                          # flattened at the broker
    assert s.book.open_positions() == []


def test_unexpected_broker_position_halts(settings, monkeypatch, tmp_path):
    monkeypatch.setattr(trader_mod, "STOP_FILE", tmp_path / "STOP")
    broker = SimulatedBroker()
    broker.add_position("SPY261106P00700000", -1)            # a naked short we did not open
    s, _ = session(settings, broker)
    rep = s.cycle()
    assert not rep.reconciled and rep.level == "HALTED"
    assert "uncovered_short_option" in rep.triggers and "broker_reconciliation_failed" in rep.triggers
    assert rep.entry is None


def test_stale_data_with_positions_halts(settings, monkeypatch, tmp_path):
    monkeypatch.setattr(trader_mod, "STOP_FILE", tmp_path / "STOP")
    broker = SimulatedBroker()
    store = MarketDataStore(":memory:")
    market(store, NOW - timedelta(minutes=10))               # last data is 10 minutes old
    clock = [NOW]
    s = TradingSession(settings, store, broker, now=lambda: clock[0], sleep=lambda _: None)
    held_position(s, broker)
    first = s.cycle()                                         # just started: the collector gets a few minutes
    assert first.level == "NORMAL" and "waiting for fresh market data after a pause" in first.notes
    levels = {}
    for minutes in range(1, 7):                               # still nothing fresh after the 5-minute grace: halt
        clock[0] = NOW + timedelta(minutes=minutes)
        rep = s.cycle()
        levels[minutes] = rep.level
        if rep.level == "HALTED":
            break
    assert levels[4] == "NORMAL" and minutes == 5
    assert "market_data_unreliable_with_positions" in rep.triggers


def test_market_close_exit(settings, monkeypatch, tmp_path):
    monkeypatch.setattr(trader_mod, "STOP_FILE", tmp_path / "STOP")
    late = datetime(2026, 10, 20, 19, 35, tzinfo=timezone.utc)   # 15:35 ET, after begin_closing
    broker = SimulatedBroker(leg_prices={L: 5.0, S: 4.5})
    broker.queue(SimBehaviour("fill"))
    s, _ = session(settings, broker, now=late)
    held_position(s, broker)
    rep = s.cycle()
    assert rep.exits and "market_close" in rep.exits[0] and broker.positions() == []


def test_no_entries_when_order_submission_disabled(monkeypatch, tmp_path, config_dir, edit_yaml):
    monkeypatch.setattr(trader_mod, "STOP_FILE", tmp_path / "STOP")
    edit_yaml(config_dir / "environments" / "paper.yaml", trading__enabled=False, trading__allow_order_submission=False)
    settings = load_settings("paper", config_dir=config_dir)
    assert not settings.can_submit_orders
    s, _ = session(settings, SimulatedBroker())
    assert s.cycle().entry is None


def test_startup_halts_when_book_and_broker_disagree(settings, monkeypatch, tmp_path):
    monkeypatch.setattr(trader_mod, "STOP_FILE", tmp_path / "STOP")
    broker = SimulatedBroker()
    broker.add_position(L, 1)                                # something at the broker we have no record of
    s, _ = session(settings, broker)
    rep = s.startup()
    assert not rep.safe_to_resume
    assert s.state.level(NOW.date()) == Level.HALTED
