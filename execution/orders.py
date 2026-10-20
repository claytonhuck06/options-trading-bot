"""Order management: turn an approved trade (or an exit) into broker orders safely.

  * Opening orders need a still-valid risk approval; the approval is checked
    again immediately before sending.
  * Prices: the first limit sits between the mid and the executable price; each
    cancel-and-resubmit step moves closer, never past the worst acceptable price.
    For an opening order that bound is the approved price plus a small allowance;
    if the market has already moved past it, the order is abandoned, not chased.
  * Idempotency: every order id is derived from its plan, and each row is stored
    before sending. If a send fails ambiguously, the broker is asked whether the
    order exists before anything else happens. Nothing is ever sent twice.
  * An order is only "done" once the broker reports a terminal status; a fill is
    only believed when the broker reports it.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Callable

from core.config import ExecutionSettings
from core.logging_setup import get_logger
from execution.broker_api import BrokerAPI, BrokerOrder, OrderLeg, OrderStatus, SubmitTimeout
from execution.order_store import OrderStore
from risk.positions import LegQuote

log = get_logger("orders")


@dataclass
class ExecutionResult:
    plan_key: str
    status: str                   # filled | partial | unfilled | aborted | failed
    filled_qty: int = 0
    avg_net_price: float | None = None           # signed: + debit paid, - credit received
    leg_prices: dict[str, float] = field(default_factory=dict)   # qty-weighted avg per leg
    reason: str | None = None
    client_order_ids: list[str] = field(default_factory=list)


def net_prices(legs: list[OrderLeg], quotes: dict[str, LegQuote]) -> tuple[float, float]:
    """(mid, executable) signed net price per unit. Executable = buy at ask, sell at bid."""
    mid = exe = 0.0
    for l in legs:
        q = quotes[l.symbol]
        sign = 1 if l.side == "buy" else -1
        mid += sign * l.ratio * q.mid
        exe += sign * l.ratio * (q.ask if l.side == "buy" else q.bid)
    return mid, exe


def _is_stock(legs: list[OrderLeg]) -> bool:
    return len(legs) == 1 and legs[0].intent == "stock"


def _round_toward_worse(price: float) -> float:
    """Round to a cent in the direction that is more likely to fill (we pay up / accept less)."""
    import math
    return math.ceil(price * 100 - 1e-9) / 100


class OrderManager:
    def __init__(self, broker: BrokerAPI, book: OrderStore, cfg: ExecutionSettings,
                 sleep: Callable[[float], None] = time.sleep, monotonic: Callable[[], float] = time.monotonic,
                 confirm: Callable[[str], bool] | None = None):
        self.broker, self.book, self.cfg = broker, book, cfg
        self.sleep, self.monotonic = sleep, monotonic
        self.confirm = confirm
        self.consecutive_failures = 0

    # --- public ------------------------------------------------------------------
    def execute(self, plan_key: str, position_id: str, intent: str, legs: list[OrderLeg], qty: int,
                get_quotes: Callable[[], dict[str, LegQuote]], worst_price: float | None,
                still_approved: Callable[[], bool] | None = None, description: str = "") -> ExecutionResult:
        """Work an order until filled, abandoned or out of attempts.

        worst_price: signed net price we must never be worse than (we require price <= worst_price).
        still_approved: re-checked right before every send of an opening order.
        """
        if not self.broker.is_paper:
            raise RuntimeError("refusing to trade a non-paper broker")
        previous = self.book.orders_for_plan(plan_key)
        if any(o["status"] in ("pending_submit", "working", "partial", "unknown", "filled") for o in previous):
            return ExecutionResult(plan_key, "aborted", reason="duplicate_plan")   # already handled: never send twice

        result = ExecutionResult(plan_key, "unfilled")
        remaining = qty
        cost_total = 0.0
        leg_totals: dict[str, float] = {}
        attempts = self.cfg.max_reprice_attempts
        for attempt in range(attempts):
            quotes = get_quotes()
            mid, exe = net_prices(legs, quotes)
            if worst_price is not None and exe > worst_price + 1e-9:
                result.reason = "price_moved_beyond_limit"
                if attempt == 0:
                    result.status = "aborted"
                break
            frac = self.cfg.entry_start_fraction + (1 - self.cfg.entry_start_fraction) * (attempt / max(attempts - 1, 1))
            limit = _round_toward_worse(mid + frac * (exe - mid))
            if worst_price is not None:
                limit = min(limit, worst_price)
            if intent == "open":
                if still_approved is not None and not still_approved():
                    result.reason = "approval_expired"
                    result.status = result.status if result.filled_qty else "aborted"
                    break
                if self.cfg.require_manual_confirmation and self.confirm is not None:
                    if not self.confirm(f"{description} x{remaining} @ {limit:+.2f} (+ debit / - credit)"):
                        result.reason = "declined_by_operator"
                        result.status = result.status if result.filled_qty else "aborted"
                        break

            coid = f"oq-{plan_key}-{len(previous) + attempt}"
            # Share orders are kept out of the option transaction-cost tally (which counts x100 per contract).
            self.book.create(coid, plan_key, position_id, intent, legs, remaining, limit, None if _is_stock(legs) else mid)
            result.client_order_ids.append(coid)
            order = self._send(coid, legs, remaining, limit)
            if order is None:
                result.status, result.reason = ("partial" if result.filled_qty else "failed"), "submit_failed"
                break
            order = self._work(coid, order)
            if order.status == OrderStatus.UNKNOWN:
                result.status, result.reason = ("partial" if result.filled_qty else "failed"), "order_state_unknown"
                break
            if order.filled_qty:
                net = order.net_fill_price(legs)
                self.book.record_fill(coid, order.filled_qty, net, order.leg_fill_prices)
                self.book.update(coid, order.status.value, order, net)
                cost_total += net * order.filled_qty
                for s, p in order.leg_fill_prices.items():
                    leg_totals[s] = leg_totals.get(s, 0.0) + p * order.filled_qty
                result.filled_qty += order.filled_qty
                remaining -= order.filled_qty
            if order.status == OrderStatus.REJECTED:
                result.reason = "rejected_by_broker"
                self.consecutive_failures += 1
                break
            if remaining <= 0:
                break

        if result.filled_qty:
            result.avg_net_price = cost_total / result.filled_qty
            result.leg_prices = {s: v / result.filled_qty for s, v in leg_totals.items()}
            result.status = "filled" if result.filled_qty >= qty else "partial"
            self.consecutive_failures = 0
        elif result.status == "failed":
            self.consecutive_failures += 1
        self.book.event("execution", {"plan": plan_key, "status": result.status, "filled": result.filled_qty,
                                      "reason": result.reason, "orders": result.client_order_ids})
        log.info("execution %s: %s, filled %d/%d%s", plan_key, result.status, result.filled_qty, qty,
                 f" ({result.reason})" if result.reason else "")
        return result

    # --- internals ---------------------------------------------------------------------
    def _send(self, coid: str, legs: list[OrderLeg], qty: int, limit: float) -> BrokerOrder | None:
        try:
            if _is_stock(legs):
                order = self.broker.submit_stock(coid, legs[0].symbol, legs[0].side, qty, abs(limit))
            else:
                order = self.broker.submit_multileg(coid, legs, qty, limit)
        except SubmitTimeout as exc:
            # Unknown whether it arrived: ask the broker, never resend blindly.
            self.book.event("submit_ambiguous", {"client_order_id": coid, "error": str(exc)})
            self.sleep(self.cfg.poll_interval_seconds)
            order = self.broker.find_order(coid)
            if order is None:
                self.book.update(coid, "submit_failed", note=str(exc))
                return None
        self.book.update(coid, order.status.value, order)
        return order

    def _work(self, coid: str, order: BrokerOrder) -> BrokerOrder:
        """Poll until terminal or timed out; on timeout cancel and confirm the final state."""
        start = self.monotonic()
        while not order.status.terminal and self.monotonic() - start < self.cfg.order_timeout_seconds:
            self.sleep(self.cfg.poll_interval_seconds)
            order = self._refresh(order)
            self.book.update(coid, order.status.value, order)
        if order.status.terminal:
            return order
        try:
            self.broker.cancel_order(order.broker_id)
        except Exception as exc:
            self.book.event("cancel_error", {"client_order_id": coid, "error": str(exc)})
        for _ in range(10):                                   # the cancel and a fill can race: wait for the truth
            self.sleep(self.cfg.poll_interval_seconds)
            order = self._refresh(order)
            if order.status.terminal:
                self.book.update(coid, order.status.value, order)
                return order
        self.book.update(coid, "unknown", order, note="cancel not confirmed")
        return BrokerOrder(order.broker_id, coid, OrderStatus.UNKNOWN, order.qty, order.filled_qty, order.limit_price,
                           order.leg_fill_prices)

    def _refresh(self, order: BrokerOrder) -> BrokerOrder:
        try:
            return self.broker.get_order(order.broker_id)
        except Exception as exc:
            self.book.event("status_error", {"broker_id": order.broker_id, "error": str(exc)})
            return order
