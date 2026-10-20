"""Overnight share basket: sizing, buy gates, sell routing, fills -> holdings -> nightly P&L, and the options
trader treating basket shares/orders as expected rather than halting."""

from datetime import date, datetime, timezone

import pytest

from execution import overnight_basket as ob
from execution.broker_api import SimBehaviour, SimulatedBroker
from execution.reconciliation import reconcile

BUY_TIME = datetime(2026, 10, 21, 19, 40, tzinfo=timezone.utc)     # 15:40 ET
PRE_OPEN = datetime(2026, 10, 22, 13, 10, tzinfo=timezone.utc)     # 09:10 ET
AFTER_OPEN = datetime(2026, 10, 22, 13, 40, tzinfo=timezone.utc)   # 09:40 ET
PICKS = ["NVDA", "TSM", "AVGO", "BRK.A"]
PRICES = {"NVDA": 190.0, "TSM": 300.0, "AVGO": 350.0, "BRK.A": 700_000.0}


@pytest.fixture
def basket():
    return ob.OvernightBasket(ob.open_db(":memory:"))


@pytest.fixture
def broker():
    return SimulatedBroker(equity=100_000.0, leg_prices=dict(PRICES))


def buy(basket, broker, **kw):
    args = dict(prices=PRICES, picks=PICKS, full_day=True, kill_level=0, stop_file=False)
    args.update(kw)
    return basket.buy(broker, kw.pop("now", BUY_TIME), **{k: v for k, v in args.items() if k != "now"})


def test_size_equal_risk_pool():
    q = ob.size(100_000, {"A": 100.0, "B": 50.0})
    slot = 100_000 * 0.015 / 0.12 / 2                  # $6,250 each
    assert q == {"A": round(slot / 100), "B": round(slot / 50)}
    assert ob.size(100_000, {"X": 30_000.0}) == {}      # one share is more than twice the slot
    assert ob.size(100_000, {"Y": 9_000.0}) == {"Y": 1}


def test_buy_sizes_and_skips_unaffordable(basket, broker):
    res = buy(basket, broker)
    assert len(res.submitted) == 3 and any("BRK.A" in s for s in res.skipped)
    total = sum(q * PRICES[s] for s, q in {r["symbol"]: r["qty"] for r in basket.working()}.items())
    assert total == pytest.approx(100_000 * 0.015 / 0.12 * 3 / 4, rel=0.05)
    assert all(c.startswith("oqn-20261021-buy-") for c in broker.submitted)


@pytest.mark.parametrize("kw,why", [
    (dict(kill_level=1), "kill switch"), (dict(stop_file=True), "STOP"), (dict(full_day=False), "full trading day"),
    (dict(picks=[]), "no picks"), (dict(now=datetime(2026, 10, 21, 19, 55, tzinfo=timezone.utc)), "outside the buy window"),
])
def test_buy_gates(basket, broker, kw, why):
    res = buy(basket, broker, **kw)
    assert not res.submitted and why in res.summary() and not broker.submitted


def test_no_double_buy_and_not_while_holding(basket, broker):
    buy(basket, broker)
    assert "already bought today" in buy(basket, broker).summary() or "still holding" in buy(basket, broker).summary()
    basket.sync(broker)
    nxt = datetime(2026, 10, 22, 19, 40, tzinfo=timezone.utc)
    assert "still holding" in buy(basket, broker, now=nxt).summary()


def test_full_cycle_pnl(basket, broker):
    buy(basket, broker)
    basket.sync(broker)                                  # closing auction happened
    held = basket.holdings()
    assert set(held) == {"NVDA", "TSM", "AVGO"}
    for s, q in held.items():
        assert broker._positions[s] == q
    broker.leg_prices.update({"NVDA": 199.5, "TSM": 300.0, "AVGO": 343.0})
    res = basket.sell(broker, PRE_OPEN, market_open=False)
    assert all("opening auction" in s for s in res.submitted)
    assert basket.settle_night(date(2026, 10, 22)) is None          # sells not filled yet
    basket.sync(broker)
    night = basket.settle_night(date(2026, 10, 22))
    expect = held["NVDA"] * 9.5 + held["AVGO"] * -7.0
    assert night["pnl"] == pytest.approx(expect) and night["names"] == 3
    assert basket.holdings() == {} and not broker._positions
    assert basket.totals()["nights"] == 1


def test_late_sell_uses_market_order_and_retries_rejects(basket, broker):
    buy(basket, broker)
    basket.sync(broker)
    broker.queue(SimBehaviour(mode="reject"), SimBehaviour(mode="reject"), SimBehaviour(mode="reject"))
    first = basket.sell(broker, PRE_OPEN, market_open=False)
    basket.sync(broker)
    assert sum("rejected" in n for n in first.notes) == 3 and len(basket.holdings()) == 3          # all three rejected, still held
    res = basket.sell(broker, AFTER_OPEN, market_open=True)
    assert len(res.submitted) == 3 and all("market" in s for s in res.submitted)
    assert all(c.endswith("-2") for c in broker.submitted[-3:])
    basket.sync(broker)
    assert basket.holdings() == {} and basket.settle_night(date(2026, 10, 22)) is not None


def test_sell_waits_on_holiday_and_never_sells_more_than_broker_has(basket, broker):
    buy(basket, broker)
    basket.sync(broker)
    assert "market closed today" in basket.sell(broker, PRE_OPEN, False, trading_day=False).summary()
    broker.add_position("TSM", -broker._positions["TSM"])            # shares gone at the broker
    res = basket.sell(broker, PRE_OPEN, False)
    assert any("TSM" in s for s in res.skipped) and len(res.submitted) == 2


def test_reconciliation_expects_basket(basket, broker):
    buy(basket, broker)
    # while auction orders rest: they're ours, so not "unknown"
    pending = broker.open_orders()
    r = reconcile([], [], pending, expected_open_orders={r["client_id"] for r in basket.working()})
    assert r.ok
    held, orders = basket.expectations(broker)                        # fills applied
    r = reconcile([], broker.positions(), broker.open_orders(), hedge_shares=held, expected_open_orders=orders)
    assert r.ok and held
    r = reconcile([], broker.positions(), broker.open_orders())       # without the basket: the trader would halt
    assert not r.ok


def test_trader_session_uses_basket(basket, broker, monkeypatch):
    from core.config import load_settings
    from data.storage.db import MarketDataStore
    from execution.trader import TradingSession
    buy(basket, broker)
    basket.sync(broker)
    s = TradingSession(load_settings("development"), MarketDataStore(":memory:"), broker, basket=basket)
    hedge, orders = s._expected_extras(0)
    assert hedge == basket.holdings() and orders == set()


def test_picks_file_excludes_spy(tmp_path):
    p = tmp_path / "picks.json"
    ob.save_picks(2026, ["NVDA", "SPY", "TSM"], p)
    assert ob.load_picks(2026, p) == ["NVDA", "TSM"] and ob.load_picks(2027, p) == []


def test_full_trading_day():
    now = datetime(2026, 10, 21, 19, 40, tzinfo=timezone.utc)
    assert ob.full_trading_day("2026-10-21T16:00:00-04:00", now)
    assert not ob.full_trading_day("2026-11-27T13:00:00-05:00", datetime(2026, 11, 27, 17, 0, tzinfo=timezone.utc))
