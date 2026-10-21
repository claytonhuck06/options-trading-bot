"""Dual momentum paper trading: the live decision matches the backtest; deep in-the-money calls (contract choice,
spread gate, chasing, rolls) with shares for DBC and as a fallback; kill-switch gating; P&L kept out of the
account kill switch; the options trader expecting its shares, contracts and orders."""

import json
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from execution import dual_momentum_paper as dmp
from execution.broker_api import SimBehaviour, SimulatedBroker
from research.monthly_trend import Bar, MarketData

PRE_OPEN = datetime(2026, 11, 2, 14, 10, tzinfo=timezone.utc)      # 09:10 ET
LATE = datetime(2026, 11, 2, 14, 29, tzinfo=timezone.utc)          # 09:29 ET, auction cutoff passed, not open yet
AFTER_OPEN = datetime(2026, 11, 2, 15, 0, tzinfo=timezone.utc)     # 10:00 ET
TODAY = date(2026, 11, 2)
CALL = "SPY270115C00700000"                                         # 74 days out from TODAY
CALL2 = "SPY270219C00720000"
EFA_CALL = "EFA270115C00090000"
PRICES = {"SPY": 500.0, "EFA": 80.0, "IEF": 95.0, "VNQ": 90.0, "DBC": 22.0, CALL: 80.0, CALL2: 70.0, EFA_CALL: 14.0}
CACHE = Path(__file__).resolve().parents[1] / "data" / "storage" / "studies"


def decision(held, month_end=date(2026, 10, 30)):
    syms = ["SPY", "EFA", "IEF", "VNQ", "DBC"]
    return dmp.Decision(month_end, {s: 0.0 for s in syms}, {s: s in held for s in syms}, list(held),
                        {s: (0.5 if s in held else 0.0) for s in syms})


@pytest.fixture
def book():
    return dmp.DualMomentumBook(dmp.open_db(":memory:"), sleep=lambda s: None)


@pytest.fixture
def broker():
    return SimulatedBroker(equity=100_000.0, leg_prices=dict(PRICES))


def quote(sym):
    px = PRICES[sym]
    return (px - 0.2, px + 0.2)


def chooser(picks):
    """fund -> CallPick or reason; qty from the real sizing rule at ask x 100."""
    def choose(fund, budget):
        p = picks.get(fund, "no quote")
        if isinstance(p, str) and not dmp.is_option(p):
            return p
        ask = PRICES[p] + 0.2
        return dmp.CallPick(p, max(1, round(budget / (ask * 100))), PRICES[p] - 0.2, ask, 0.8)
    return choose


def run_month(book, broker, d, picks, now=AFTER_OPEN, market_open=True, kill=0, stop=False, resize=False):
    book.plan(broker, now.astimezone(dmp.ET).date(), d, PRICES, resize=resize)
    sh = book.trade_shares(broker, now, d, market_open, kill, stop)
    book.sync(broker)
    op = book.trade_options(broker, now, d, chooser(picks), quote, kill, stop, PRICES)
    book.sync(broker)
    return sh, op


# --- symbols, ticks, contract choice -------------------------------------------------------------

def test_option_symbol_parts():
    assert dmp.is_option(CALL) and not dmp.is_option("SPY") and not dmp.is_option("BRK.B")
    assert dmp.underlying_of(CALL) == "SPY" and dmp.underlying_of(EFA_CALL) == "EFA"
    assert dmp.expiry_of(CALL) == date(2027, 1, 15) and dmp.multiplier(CALL) == 100 and dmp.multiplier("DBC") == 1


def test_ticks():
    assert dmp.tick(80.123, CALL) == 80.12                            # SPY: pennies
    assert dmp.tick(14.13, EFA_CALL) == 14.10 and dmp.tick(14.13, EFA_CALL, up=True) == 14.20
    assert dmp.tick(2.42, EFA_CALL) == 2.40 and dmp.tick(2.42, EFA_CALL, up=False) == 2.40


def _q(sym, bid, ask, delta):
    return SimpleNamespace(symbol=sym, bid=bid, ask=ask, delta=delta)


def test_pick_call_closest_delta_and_size():
    exp = date(2027, 1, 15)
    quotes = [_q("SPY270115C00700000", 79.8, 80.2, 0.82), _q("SPY270115C00650000", 120, 121, 0.93),
              _q("SPY270115C00760000", 30, 30.4, 0.55), _q("SPY270115P00700000", 5, 5.1, -0.2)]
    p = dmp.pick_call(quotes, 770, 10_000, exp)
    assert p.symbol == "SPY270115C00700000" and p.qty == 1             # $8,020 a contract, 1.25 -> 1
    assert dmp.pick_call(quotes, 770, 20_000, exp).qty == 2


def test_pick_call_gates():
    exp = date(2027, 1, 15)
    wide = [_q("VNQ270115C00080000", 10.0, 11.0, 0.8)]
    assert "too wide" in dmp.pick_call(wide, 90, 10_000, exp)
    pricey = [_q("SPY270115C00700000", 199.8, 200.2, 0.8)]
    assert "over 1.5x" in dmp.pick_call(pricey, 900, 10_000, exp)     # one contract $20k vs $10k
    assert "no two-sided" in dmp.pick_call([_q("SPY270115C00700000", 0, 80, 0.8)], 770, 10_000, exp)


def test_choose_call_uses_first_third_friday_75_days_out():
    seen = {}
    contracts = [SimpleNamespace(expiration=e, option_type="call", tradable=True)
                 for e in (date(2027, 1, 22), date(2027, 1, 15), date(2027, 2, 19))]

    class Src:
        def fetch_contracts(self, u, gte, lte):
            seen["window"] = (gte, lte)
            return contracts

        def fetch_underlying(self, u):
            return SimpleNamespace(last_price=770.0, bid=None, ask=None)

        def fetch_chain(self, u, g, l, strike_gte=None, strike_lte=None):
            seen["expiry"] = g
            return [_q("SPY270115C00700000", 79.8, 80.2, 0.8)]
    p = dmp.choose_call(Src(), "SPY", date(2026, 11, 1), 10_000)
    assert seen["window"][0] == date(2027, 1, 15) and seen["expiry"] == date(2027, 1, 15) and p.qty == 1


# --- the month -----------------------------------------------------------------------------------

def test_plan_dbc_shares_option_funds_pending(book, broker):
    row = book.plan(broker, TODAY, decision(["SPY", "DBC"]), PRICES)
    assert json.loads(row["targets"]) == {"DBC": 455} and json.loads(row["pending"]) == ["SPY"]   # $10k / $22


def test_first_month_dbc_in_auction_spy_call_after_open(book, broker):
    d = decision(["SPY", "DBC"])
    book.plan(broker, TODAY, d, PRICES)
    sh = book.trade_shares(broker, PRE_OPEN, d, False, 0, False)
    assert sh.submitted == ["buy 455 DBC (opening auction)"]
    book.sync(broker)
    op = book.trade_options(broker, AFTER_OPEN, d, chooser({"SPY": CALL}), quote, 0, False, PRICES)
    assert op.submitted == [f"buy 1 {CALL}"]
    order = next(o for o in broker.orders.values() if o.client_order_id.startswith(f"oqm-20261102-buy-{CALL}"))
    assert order.limit_price == pytest.approx(80.0)                   # first step: the mid
    book.sync(broker)
    assert book.holdings() == {"DBC": 455, CALL: 1}
    assert book.finish(d.month_end, TODAY, 1.0)


def test_chase_steps_to_the_ask_then_gives_up(book, broker):
    broker.queue(*[SimBehaviour(mode="never")] * 6)
    d = decision(["SPY"])
    _, op = run_month(book, broker, d, {"SPY": CALL})
    prices = [o.limit_price for o in broker.orders.values()]
    assert prices == pytest.approx([80.0, 80.04, 80.08, 80.12, 80.16, 80.2])
    assert all(o.status.terminal for o in broker.orders.values())    # each step cancelled before the next
    assert "0 of 1 filled; will retry" in op.summary()
    assert not book.finish(d.month_end, TODAY)


def test_chase_fills_on_a_later_step(book, broker):
    broker.queue(SimBehaviour(mode="never"), SimBehaviour(mode="never"))
    run_month(book, broker, decision(["SPY"]), {"SPY": CALL})
    assert book.holdings() == {CALL: 1} and len(broker.orders) == 3


def test_wide_quote_falls_back_to_shares(book, broker):
    d = decision(["VNQ", "SPY"])
    _, op = run_month(book, broker, d, {"SPY": CALL, "VNQ": "quote too wide (10.00 x 11.00)"})
    assert "VNQ: shares instead of a call" in op.summary()
    assert json.loads(book.month(d.month_end)["targets"])["VNQ"] == 111   # $10k / $90
    sh = book.trade_shares(broker, AFTER_OPEN, d, True, 0, False)    # the next pass buys the shares
    assert sh.submitted == ["buy 111 VNQ (market)"]
    book.sync(broker)
    assert book.finish(d.month_end, TODAY)


def test_next_month_keeps_a_call_with_time_left_and_sells_dropped(book, broker):
    run_month(book, broker, decision(["SPY", "EFA"]), {"SPY": CALL2, "EFA": EFA_CALL})
    book.finish(date(2026, 10, 30), TODAY)
    held = book.holdings()
    d = decision(["SPY", "DBC"], date(2026, 11, 30))
    nxt = datetime(2026, 12, 1, 15, 0, tzinfo=timezone.utc)
    row = book.plan(broker, date(2026, 12, 1), d, PRICES)
    assert json.loads(row["targets"]) == {"DBC": 455, CALL2: held[CALL2]}   # Feb call, 80 days left: kept
    assert json.loads(row["pending"]) == []
    book.trade_shares(broker, nxt, d, True, 0, False)
    op = book.trade_options(broker, nxt, d, chooser({}), quote, 0, False, PRICES)
    assert op.submitted == [f"sell {held[EFA_CALL]} {EFA_CALL}"]
    book.sync(broker)
    assert book.finish(d.month_end, date(2026, 12, 1))


def test_call_under_40_days_is_rolled_at_the_rebalance(book, broker):
    run_month(book, broker, decision(["SPY"]), {"SPY": CALL})
    book.finish(date(2026, 10, 30), TODAY)
    jan = datetime(2027, 1, 4, 15, 0, tzinfo=timezone.utc)            # CALL expires in 11 days
    _, op = run_month(book, broker, decision(["SPY"], date(2026, 12, 31)), {"SPY": CALL2}, now=jan)
    assert op.submitted == [f"sell 1 {CALL}", f"buy 1 {CALL2}"]
    assert book.holdings() == {CALL2: 1}


def test_roll_due_mid_month(book, broker):
    d = decision(["SPY"])
    run_month(book, broker, d, {"SPY": CALL})
    book.finish(d.month_end, TODAY)
    assert book.roll_due(date(2027, 1, 1), d) == [CALL]               # 14 days left
    row = book.month(d.month_end)
    assert row["done"] is None and json.loads(row["pending"]) == ["SPY"] and CALL not in json.loads(row["targets"])
    assert book.roll_due(date(2026, 12, 1), d) == []


def test_kill_switch_blocks_buys_but_not_sells(book, broker):
    run_month(book, broker, decision(["SPY", "EFA"]), {"SPY": CALL, "EFA": EFA_CALL})
    book.finish(date(2026, 10, 30), TODAY)
    d = decision(["DBC", "EFA"], date(2026, 11, 30))
    nxt = datetime(2026, 12, 1, 15, 0, tzinfo=timezone.utc)
    book.plan(broker, date(2026, 12, 1), d, PRICES)
    sh = book.trade_shares(broker, nxt, d, True, 2, False)
    op = book.trade_options(broker, nxt, d, chooser({}), quote, 2, False, PRICES)
    assert not sh.submitted and "kill switch level 2" in sh.summary()
    assert op.submitted == [f"sell 1 {CALL}"]
    book.sync(broker)
    assert not book.finish(d.month_end, date(2026, 12, 1))


def test_stop_file_blocks_option_buys(book, broker):
    d = decision(["SPY"])
    _, op = run_month(book, broker, d, {"SPY": CALL}, stop=True)
    assert not op.submitted and "STOP file" in op.summary()
    assert json.loads(book.month(d.month_end)["pending"]) == ["SPY"]  # still to choose once allowed


def test_no_share_orders_between_auction_cutoff_and_open(book, broker):
    d = decision(["DBC"])
    book.plan(broker, TODAY, d, PRICES)
    res = book.trade_shares(broker, LATE, d, False, 0, False)
    assert not res.submitted and "will retry" in res.summary()


def test_resize_replans_and_swaps_shares_for_a_call(book, broker):
    d = decision(["SPY", "DBC"])
    book.conn.execute("INSERT INTO holdings VALUES ('SPY', 13, 772.0, '2026-10-21')")
    broker.add_position("SPY", 13)
    run_month(book, broker, d, {"SPY": CALL}, resize=True)
    assert not book.trade_shares(broker, AFTER_OPEN, d, True, 0, False).submitted
    assert book.holdings() == {"DBC": 455, CALL: 1}


# --- P&L, alerts, kill-switch exclusion ------------------------------------------------------------

def test_pnl_now_and_at_previous_close(book, broker):
    run_month(book, broker, decision(["SPY", "DBC"]), {"SPY": CALL})   # today: DBC 455 @ 22, 1 call @ 80
    broker.leg_prices[CALL] = 85.0
    broker.leg_prices["DBC"] = 23.0
    pos = broker.positions()
    assert book.pnl_now(pos) == pytest.approx(455 * 1 + 500)
    assert book.pnl_at_prev_close(TODAY, pos) == pytest.approx(0.0)   # nothing held at yesterday's close
    broker.lastday_prices = {CALL: 85.0, "DBC": 23.0}
    assert book.pnl_at_prev_close(date(2026, 11, 3), broker.positions()) == pytest.approx(955)
    assert book.pnl_at_prev_close(TODAY, pos) == pytest.approx(0.0)   # kept once computed


def test_loss_alert_once_per_month(book, broker):
    d = decision(["SPY"])
    run_month(book, broker, d, {"SPY": CALL})
    book.finish(d.month_end, TODAY, 20_000.0)
    assert book.loss_alert(d.month_end, 16_000.0) is None
    assert "down $5,100" in book.loss_alert(d.month_end, 14_900.0)
    assert book.loss_alert(d.month_end, 10_000.0) is None


def test_trader_takes_momentum_out_of_the_kill_switch(book, broker):
    from core.config import load_settings
    from data.storage.db import MarketDataStore
    from execution.trader import TradingSession
    run_month(book, broker, decision(["SPY"]), {"SPY": CALL})
    broker.leg_prices[CALL] = 60.0                                    # -$2,000 on the call
    s = TradingSession(load_settings("development"), MarketDataStore(":memory:"), broker, momentum=book)
    now, prev = s._momentum_pnl(TODAY, broker.positions())
    assert now == pytest.approx(-2_000) and prev == pytest.approx(0.0)
    plain = TradingSession(load_settings("development"), MarketDataStore(":memory:"), broker)
    assert plain._momentum_pnl(TODAY, broker.positions()) == (0.0, 0.0)


def test_trader_expects_momentum_contracts_and_shares(book, broker):
    from core.config import load_settings
    from data.storage.db import MarketDataStore
    from execution import overnight_basket as ob
    from execution.trader import TradingSession
    run_month(book, broker, decision(["SPY", "DBC"]), {"SPY": CALL})
    basket = ob.OvernightBasket(ob.open_db(":memory:"))
    s = TradingSession(load_settings("development"), MarketDataStore(":memory:"), broker, basket=basket, momentum=book)
    hedge, orders = s._expected_extras(100)
    assert hedge == {"SPY": 100, "DBC": 455, CALL: 1} and orders == set()


def test_assignment_check_counts_momentum_spy_shares_as_known(book, broker, monkeypatch):
    from core.config import load_settings
    from data.storage.db import MarketDataStore
    from execution import trader
    seen = {}
    monkeypatch.setattr(trader, "detect_assignments",
                        lambda book_, actual, underlying, known: seen.setdefault("known", known) and [])
    d = decision(["SPY"])
    run_month(book, broker, d, {"SPY": "quote too wide"})            # fallback: SPY shares
    book.trade_shares(broker, AFTER_OPEN, d, True, 0, False)
    book.sync(broker)
    s = trader.TradingSession(load_settings("development"), MarketDataStore(":memory:"), broker, momentum=book)
    s._handle_assignments([], broker.positions(), [])
    assert seen["known"] == 20


# --- signal and storage --------------------------------------------------------------------------

def test_decide_refuses_stale_data():
    bars = [Bar(date(2026, 10, 29), 1.0, 1.0)]
    with pytest.raises(ValueError, match="no 2026-10-30 close"):
        dmp.decide({"SPY": MarketData(bars, {}, [])}, date(2026, 10, 30))


def test_old_database_gets_new_columns(tmp_path):
    import sqlite3
    path = tmp_path / "dm.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE months (signal_month_end TEXT PRIMARY KEY, decided TEXT, held TEXT, scores TEXT, "
                 "trend TEXT, targets TEXT, equity REAL, done TEXT, note TEXT)")
    conn.commit()
    conn.close()
    cols = {r[1] for r in dmp.open_db(path).execute("PRAGMA table_info(months)")}
    assert {"pending", "start_value", "alerted"} <= cols


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
        assert frozenset(dmp.decide(five, m.month_end).held) == m.held_after, m.month_end
        checked += 1
    assert checked > 200
