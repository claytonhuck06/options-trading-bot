"""Order management, reconciliation and recovery against the simulated broker."""

from datetime import date, datetime, timezone

import pytest

from core.config import load_settings
from data.storage.db import MarketDataStore
from execution.broker_api import BrokerPosition, OrderLeg, SimBehaviour, SimulatedBroker
from execution.order_store import OrderStore
from execution.orders import OrderManager, net_prices
from execution.reconciliation import reconcile, uncovered_short_symbols
from execution.recovery import recover
from risk.positions import LegQuote, OptionLeg, Position

CFG = load_settings().execution
NOW = datetime(2026, 10, 20, 15, 0, tzinfo=timezone.utc)
L, S = "SPY261106C00770000", "SPY261106C00771000"
LEGS = [OrderLeg(L, "buy", 1, "buy_to_open"), OrderLeg(S, "sell", 1, "sell_to_open")]
QUOTES = {L: LegQuote(5.00, 5.10, 0.14, NOW), S: LegQuote(4.40, 4.50, 0.14, NOW)}   # mid 0.60, executable 0.70


class Clock:
    def __init__(self):
        self.t = 0.0
        self.sleeps = []

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s

    def mono(self):
        return self.t


def setup(*behaviours, confirm=None, cfg=CFG):
    broker = SimulatedBroker(leg_prices={L: 5.05, S: 4.45})
    broker.queue(*behaviours)
    book = OrderStore(MarketDataStore(":memory:"))
    clock = Clock()
    mgr = OrderManager(broker, book, cfg, sleep=clock.sleep, monotonic=clock.mono, confirm=confirm)
    return broker, book, mgr


def run(mgr, key="req-1", qty=2, worst=0.75, approved=lambda: True, intent="open"):
    return mgr.execute(key, key, intent, LEGS, qty, lambda: QUOTES, worst, approved, "test spread")


def test_net_prices():
    mid, exe = net_prices(LEGS, QUOTES)
    assert mid == pytest.approx(0.60) and exe == pytest.approx(0.70)


def test_fill_first_try_at_halfway_price():
    broker, book, mgr = setup(SimBehaviour("fill"))
    r = run(mgr)
    assert r.status == "filled" and r.filled_qty == 2
    assert broker.submitted == ["oq-req-1-0"]
    assert broker.orders["sim-1"].limit_price == pytest.approx(0.65)        # halfway between 0.60 mid and 0.70
    assert r.avg_net_price == pytest.approx(0.65)
    row = book.orders_for_plan("req-1")[0]
    assert row["status"] == "filled" and row["filled_qty"] == 2


def test_never_sends_the_same_plan_twice():
    broker, _, mgr = setup(SimBehaviour("fill"))
    run(mgr)
    again = run(mgr)
    assert again.status == "aborted" and again.reason == "duplicate_plan" and len(broker.submitted) == 1


def test_price_moved_too_far_nothing_sent():
    broker, _, mgr = setup()
    r = run(mgr, worst=0.65)                                  # executable is 0.70 > 0.65
    assert r.status == "aborted" and r.reason == "price_moved_beyond_limit" and broker.submitted == []


def test_expired_approval_nothing_sent():
    broker, _, mgr = setup()
    r = run(mgr, approved=lambda: False)
    assert r.status == "aborted" and r.reason == "approval_expired" and broker.submitted == []


def test_unfilled_orders_are_cancelled_and_repriced_toward_executable():
    broker, book, mgr = setup(*[SimBehaviour("never")] * 3)
    r = run(mgr)
    assert r.status == "unfilled" and r.filled_qty == 0
    limits = [broker.orders[f"sim-{i}"].limit_price for i in (1, 2, 3)]
    assert limits == sorted(limits) and limits[-1] == pytest.approx(0.70) and max(limits) <= 0.75
    assert broker.cancel_calls == 3
    assert all(o.status.value == "canceled" for o in broker.orders.values())
    assert broker.open_orders() == []


def test_partial_fill_then_rest_filled():
    broker, _, mgr = setup(SimBehaviour("partial", fill_qty=1), SimBehaviour("fill"))
    r = run(mgr, qty=3)
    assert r.status == "filled" and r.filled_qty == 3
    assert broker.orders["sim-2"].qty == 2                  # second order only for what was left


def test_partial_fill_only():
    broker, _, mgr = setup(SimBehaviour("partial", fill_qty=1), SimBehaviour("never"), SimBehaviour("never"))
    r = run(mgr, qty=3)
    assert r.status == "partial" and r.filled_qty == 1


def test_rejection_counts_as_failure():
    broker, _, mgr = setup(SimBehaviour("reject"))
    r = run(mgr)
    assert r.status == "unfilled" and r.reason == "rejected_by_broker" and mgr.consecutive_failures == 1


def test_lost_response_is_looked_up_not_resent():
    broker, _, mgr = setup(SimBehaviour("lost_response"))
    r = run(mgr)
    assert r.status == "filled" and broker.submitted == ["oq-req-1-0"]     # sent exactly once


def test_lost_order_never_resent():
    broker, book, mgr = setup()

    def boom(*a, **k):
        from execution.broker_api import SubmitTimeout
        raise SubmitTimeout("no response")
    broker.submit_multileg = boom
    r = run(mgr)
    assert r.status == "failed" and r.reason == "submit_failed" and mgr.consecutive_failures == 1
    assert book.orders_for_plan("req-1")[0]["status"] == "submit_failed"


def test_fill_racing_the_cancel_is_recorded():
    broker, _, mgr = setup(SimBehaviour("never", fill_on_cancel=True))
    r = run(mgr)
    assert r.status == "filled" and r.filled_qty == 2


def test_manual_confirmation_when_enabled():
    cfg = CFG.model_copy(update={"require_manual_confirmation": True})
    broker, _, mgr = setup(confirm=lambda text: False, cfg=cfg)
    r = run(mgr)
    assert r.status == "aborted" and r.reason == "declined_by_operator" and broker.submitted == []


def test_exits_are_not_held_up_by_confirmation():
    cfg = CFG.model_copy(update={"require_manual_confirmation": True})
    broker, _, mgr = setup(SimBehaviour("fill"), confirm=lambda text: False, cfg=cfg)
    r = run(mgr, intent="close", worst=None)
    assert r.status == "filled"


def test_default_is_automatic():
    assert CFG.require_manual_confirmation is False


def test_refuses_non_paper_broker():
    broker, _, mgr = setup()
    broker.is_paper = False
    with pytest.raises(RuntimeError):
        run(mgr)


# --- reconciliation -----------------------------------------------------------

def book_position(n=2):
    return Position("p1", "SPY", [OptionLeg(L, "SPY", "call", 770, date(2026, 11, 6), n, 5.1),
                                  OptionLeg(S, "SPY", "call", 771, date(2026, 11, 6), -n, 4.4)], NOW, 100, 20, 50)


def test_reconciled_when_matching():
    r = reconcile([book_position()], [BrokerPosition(L, 2, 5.1, "us_option"), BrokerPosition(S, -2, 4.4, "us_option")], [])
    assert r.ok


@pytest.mark.parametrize("broker_positions, field", [
    ([BrokerPosition(L, 2, 5.1, "us_option")], "missing_at_broker"),
    ([BrokerPosition(L, 2, 5.1, "us_option"), BrokerPosition(S, -2, 4.4, "us_option"),
      BrokerPosition("AAPL", 10, 200, "us_equity")], "unexpected_at_broker"),
    ([BrokerPosition(L, 3, 5.1, "us_option"), BrokerPosition(S, -2, 4.4, "us_option")], "qty_mismatch"),
])
def test_reconciliation_differences(broker_positions, field):
    r = reconcile([book_position()], broker_positions, [])
    assert not r.ok and getattr(r, field)


def test_unknown_open_order_flagged():
    from execution.broker_api import BrokerOrder, OrderStatus
    stray = BrokerOrder("x", "manual-123", OrderStatus.WORKING, 1, 0, 1.0)
    assert not reconcile([], [], [stray]).ok


def test_uncovered_short_detected_from_broker_positions():
    assert uncovered_short_symbols([BrokerPosition(S, -1, 4.4, "us_option")]) == [S]
    assert uncovered_short_symbols([BrokerPosition(L, 1, 5.1, "us_option"), BrokerPosition(S, -1, 4.4, "us_option")]) == []


def test_legged_fill_caught_by_reconciliation():
    broker, book, mgr = setup(SimBehaviour("legged"))        # broker fills only the short leg (atomicity broken)
    run(mgr, qty=1)
    r = reconcile([book_position(1)], broker.positions(), broker.open_orders())
    assert not r.ok and r.uncovered_shorts == [S]


# --- crash recovery -------------------------------------------------------------

def test_recovery_checks_unresolved_orders_and_never_resends():
    broker = SimulatedBroker(leg_prices={L: 5.05, S: 4.45})
    book = OrderStore(MarketDataStore(":memory:"))
    # An order we wrote down but crashed before sending:
    book.create("oq-crash-0", "crash", "crash", "open", LEGS, 1, 0.65, 0.60)
    # An order that reached the broker and filled while we were down:
    book.create("oq-filled-0", "filled", "filled", "open", LEGS, 1, 0.65, 0.60)
    o = broker.submit_multileg("oq-filled-0", LEGS, 1, 0.65)
    broker.get_order(o.broker_id)
    sent_before = list(broker.submitted)
    rep = recover(broker, book)
    assert rep.orders_never_sent == ["oq-crash-0"] and rep.orders_found_filled == ["oq-filled-0"]
    assert broker.submitted == sent_before                    # nothing resent
    assert not rep.safe_to_resume                             # the fill is not in our book: a person must look
