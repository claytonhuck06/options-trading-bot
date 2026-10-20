"""Dual momentum paper trading: the live decision matches the backtest, sizing, monthly rebalance routing,
kill-switch gating, retries, and the options trader expecting its shares (SPY included)."""

from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from execution import dual_momentum_paper as dmp
from execution.broker_api import SimBehaviour, SimulatedBroker
from research.monthly_trend import Bar, MarketData

PRE_OPEN = datetime(2026, 11, 2, 14, 10, tzinfo=timezone.utc)      # 09:10 ET
LATE = datetime(2026, 11, 2, 14, 29, tzinfo=timezone.utc)          # 09:29 ET, auction cutoff passed, not open yet
AFTER_OPEN = datetime(2026, 11, 2, 15, 0, tzinfo=timezone.utc)     # 10:00 ET
PRICES = {"SPY": 500.0, "EFA": 80.0, "IEF": 95.0, "VNQ": 90.0, "DBC": 22.0}
CACHE = Path(__file__).resolve().parents[1] / "data" / "storage" / "studies"


def decision(held, month_end=date(2026, 10, 30)):
    syms = list(PRICES)
    return dmp.Decision(month_end, {s: 0.0 for s in syms}, {s: s in held for s in syms}, list(held),
                        {s: (0.5 if s in held else 0.0) for s in syms})


@pytest.fixture
def book():
    return dmp.DualMomentumBook(dmp.open_db(":memory:"))


@pytest.fixture
def broker():
    return SimulatedBroker(equity=100_000.0, leg_prices=dict(PRICES))


def rebalance(book, broker, d, now=PRE_OPEN, market_open=False, kill=0, stop=False):
    return book.rebalance(broker, now, d, PRICES, market_open, kill, stop)


def test_size_is_one_equal_risk_trade():
    d = decision(["SPY", "IEF"])
    assert dmp.SLEEVE == pytest.approx(0.06)                          # 1.5% risk / 25% stress drop
    assert dmp.target_shares(d, 100_000, PRICES) == {"SPY": 6, "IEF": 32}   # $3,000 each -> 6 x $500, 31.6 x $95
    assert dmp.target_shares(decision([]), 100_000, PRICES) == {}


def test_first_month_buys_in_opening_auction_then_finishes(book, broker):
    d = decision(["SPY", "IEF"])
    res = rebalance(book, broker, d)
    assert sorted(res.submitted) == ["buy 32 IEF (opening auction)", "buy 6 SPY (opening auction)"]
    assert all(c.startswith("oqm-20261102-buy-") for c in broker.submitted)
    assert not book.finish(d.month_end, date(2026, 11, 2))           # still working
    book.sync(broker)
    assert book.holdings() == {"SPY": 6, "IEF": 32}
    assert book.finish(d.month_end, date(2026, 11, 2))
    assert "already done" in rebalance(book, broker, d).summary()


def test_next_month_rotates_sells_first(book, broker):
    rebalance(book, broker, decision(["SPY", "IEF"]))
    book.sync(broker)
    book.finish(date(2026, 10, 30), date(2026, 11, 2))
    broker.submitted.clear()
    res = rebalance(book, broker, decision(["SPY", "DBC"], date(2026, 11, 30)), now=AFTER_OPEN, market_open=True)
    assert broker.submitted[0].startswith("oqm-20261102-sell-IEF")
    assert "sell 32 IEF (market)" in res.submitted and "buy 136 DBC (market)" in res.submitted
    assert not any("SPY" in s for s in res.submitted)                # unchanged: no trade


def test_all_cash_sells_everything(book, broker):
    rebalance(book, broker, decision(["SPY", "IEF"]))
    book.sync(broker)
    book.finish(date(2026, 10, 30), date(2026, 11, 2))
    d = decision([], date(2026, 11, 30))
    res = rebalance(book, broker, d)
    assert sorted(res.submitted) == ["sell 32 IEF (opening auction)", "sell 6 SPY (opening auction)"]
    book.sync(broker)
    assert book.holdings() == {} and book.finish(d.month_end, date(2026, 12, 1))


def test_kill_switch_blocks_buys_but_not_sells(book, broker):
    rebalance(book, broker, decision(["SPY", "IEF"]))
    book.sync(broker)
    book.finish(date(2026, 10, 30), date(2026, 11, 2))
    d = decision(["SPY", "DBC"], date(2026, 11, 30))
    res = rebalance(book, broker, d, kill=2)
    assert res.submitted == ["sell 32 IEF (opening auction)"] and "kill switch level 2" in res.summary()
    book.sync(broker)
    assert not book.finish(d.month_end, date(2026, 12, 1))           # not done: DBC still to buy
    res = rebalance(book, broker, d)                                  # next weekday, kill switch clear
    assert res.submitted == ["buy 136 DBC (opening auction)"]


def test_stop_file_blocks_buys(book, broker):
    res = rebalance(book, broker, decision(["SPY", "IEF"]), stop=True)
    assert not res.submitted and "STOP file" in res.summary()


def test_no_orders_between_auction_cutoff_and_open(book, broker):
    res = rebalance(book, broker, decision(["SPY"]), now=LATE)
    assert not res.submitted and "will retry" in res.summary()


def test_rejected_auction_order_is_retried_with_the_months_targets(book, broker):
    broker.queue(SimBehaviour(mode="reject"))
    d = decision(["SPY", "IEF"])
    res = rebalance(book, broker, d)
    assert any("rejected" in s for s in res.skipped)
    book.sync(broker)
    broker.leg_prices["SPY"] = 400.0                                  # price moved: targets stay the month's own
    res = rebalance(book, broker, d, now=AFTER_OPEN, market_open=True)
    assert res.submitted == ["buy 6 SPY (market)"]
    book.sync(broker)
    assert book.finish(d.month_end, date(2026, 11, 2))


def test_decide_refuses_stale_data():
    bars = [Bar(date(2026, 10, 29), 1.0, 1.0)]
    datas = {"SPY": MarketData(bars, {}, [])}
    with pytest.raises(ValueError, match="no 2026-10-30 close"):
        dmp.decide(datas, date(2026, 10, 30))


@pytest.mark.skipif(not (CACHE / "dbc_daily_yahoo.json").exists(), reason="backtest price cache not present")
def test_live_decision_matches_backtest_every_month():
    from research import dual_momentum as dm
    from research import monthly_trend as mt
    from research import multi_asset_trend as ma
    five = {s: mt.load_symbol(CACHE, s, allow_splits=True) for s in dm.ASSETS}
    eb = date(2026, 10, 1)
    start = ma.common_start(ma.common_days(five, eb), eb, 14)
    res = dm.simulate(five, dm.TREND, dm.COST, eb, start)
    checked = 0
    for m in res.months:
        if m.held_after is None:
            continue
        got = dmp.decide(five, m.month_end)
        assert frozenset(got.held) == m.held_after, m.month_end
        checked += 1
    assert checked > 200


def test_trader_expects_momentum_and_basket_shares(book, broker):
    from core.config import load_settings
    from data.storage.db import MarketDataStore
    from execution import overnight_basket as ob
    from execution.trader import TradingSession
    rebalance(book, broker, decision(["SPY", "IEF"]))
    basket = ob.OvernightBasket(ob.open_db(":memory:"))
    s = TradingSession(load_settings("development"), MarketDataStore(":memory:"), broker, basket=basket, momentum=book)
    hedge, orders = s._expected_extras(0)
    assert hedge == {"SPY": 6, "IEF": 32} and orders == set()        # fills applied by the sync inside
    hedge, _ = s._expected_extras(100)                                # assignment shares add to momentum's SPY
    assert hedge["SPY"] == 106


def test_assignment_check_counts_momentum_spy_as_known(book, broker, monkeypatch):
    from core.config import load_settings
    from data.storage.db import MarketDataStore
    from execution import trader
    seen = {}
    monkeypatch.setattr(trader, "detect_assignments",
                        lambda book_, actual, underlying, known: seen.setdefault("known", known) and [])
    rebalance(book, broker, decision(["SPY"]))
    book.sync(broker)
    s = trader.TradingSession(load_settings("development"), MarketDataStore(":memory:"), broker, momentum=book)
    s._handle_assignments([], broker.positions(), [])
    assert seen["known"] == 6               # so a real assignment on top of these shares still adds up
