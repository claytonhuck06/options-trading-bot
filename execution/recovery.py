"""Start-up recovery after a restart or crash.

  1. For every order whose outcome we did not record, ask the broker by its client
     order id and record what actually happened. Orders are NEVER resent here.
  2. Reconcile our position book against the broker.
  3. Report whether it is safe to resume. If not, the kill switch halts trading
     until a person resolves the difference.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from execution.broker_api import BrokerAPI
from execution.order_store import OrderStore
from execution.reconciliation import ReconResult, reconcile


@dataclass
class RecoveryReport:
    orders_checked: int = 0
    orders_found_filled: list[str] = field(default_factory=list)
    orders_never_sent: list[str] = field(default_factory=list)
    still_open_at_broker: list[str] = field(default_factory=list)
    recon: ReconResult | None = None

    @property
    def safe_to_resume(self) -> bool:
        # A fill we did not apply to the book shows up as a reconciliation difference.
        return self.recon is not None and self.recon.ok and not self.still_open_at_broker


def recover(broker: BrokerAPI, book: OrderStore) -> RecoveryReport:
    rep = RecoveryReport()
    for row in book.unresolved():
        rep.orders_checked += 1
        coid = row["client_order_id"]
        order = broker.find_order(coid)
        if order is None:
            book.update(coid, "submit_failed", note="not found at broker during recovery")
            rep.orders_never_sent.append(coid)
            continue
        book.update(coid, order.status.value, order)
        if order.filled_qty:
            rep.orders_found_filled.append(coid)
        if not order.status.terminal:
            rep.still_open_at_broker.append(coid)
    rep.recon = reconcile(book.open_positions(), broker.positions(), broker.open_orders(),
                          expected_open_orders=set())
    book.event("recovery", {"checked": rep.orders_checked, "filled": rep.orders_found_filled,
                            "never_sent": rep.orders_never_sent, "still_open": rep.still_open_at_broker,
                            "recon": rep.recon.summary()})
    return rep
