"""The broker interface the execution engine talks to, plus a simulated broker
for tests. The real implementation is execution/alpaca_paper.py, the ONLY
module in the project allowed to send, cancel or close orders.

Price convention (Alpaca multi-leg orders): a positive net limit price is a
debit paid, a negative one is a credit received.
"""

from __future__ import annotations

import itertools
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Protocol


class OrderStatus(str, Enum):
    WORKING = "working"          # accepted, resting, not yet filled
    PARTIAL = "partial"
    FILLED = "filled"
    CANCELED = "canceled"        # includes expired / done for day
    REJECTED = "rejected"
    UNKNOWN = "unknown"

    @property
    def terminal(self) -> bool:
        return self in (OrderStatus.FILLED, OrderStatus.CANCELED, OrderStatus.REJECTED)


@dataclass(frozen=True)
class OrderLeg:
    symbol: str
    side: str                    # buy | sell
    ratio: int                   # per unit of the strategy
    intent: str                  # buy_to_open | sell_to_open | buy_to_close | sell_to_close | stock (shares)


@dataclass(frozen=True)
class BrokerOrder:
    broker_id: str
    client_order_id: str
    status: OrderStatus
    qty: int
    filled_qty: int
    limit_price: float
    leg_fill_prices: dict[str, float] = field(default_factory=dict)   # symbol -> avg fill price per share

    def net_fill_price(self, legs: list[OrderLeg]) -> float | None:
        if not self.filled_qty or not self.leg_fill_prices:
            return None
        return sum((1 if l.side == "buy" else -1) * l.ratio * self.leg_fill_prices[l.symbol] for l in legs)


@dataclass(frozen=True)
class BrokerPosition:
    symbol: str
    qty: int                     # signed: + long, - short
    avg_entry_price: float
    asset_class: str             # us_option | us_equity


class BrokerAPI(Protocol):
    is_paper: bool

    def submit_multileg(self, client_order_id: str, legs: list[OrderLeg], qty: int, limit_price: float) -> BrokerOrder: ...
    def submit_stock(self, client_order_id: str, symbol: str, side: str, qty: int, limit_price: float) -> BrokerOrder: ...
    def get_order(self, broker_id: str) -> BrokerOrder: ...
    def find_order(self, client_order_id: str) -> BrokerOrder | None: ...
    def cancel_order(self, broker_id: str) -> None: ...
    def open_orders(self) -> list[BrokerOrder]: ...
    def positions(self) -> list[BrokerPosition]: ...
    def equity(self) -> float: ...
    def last_equity(self) -> float: ...          # equity at the previous close (start of today's P&L)


class SubmitTimeout(Exception):
    """The submit call failed in a way that leaves it unknown whether the broker received the order."""


# --- simulated broker -------------------------------------------------------

@dataclass
class SimBehaviour:
    """What the simulated broker does with the next submitted order."""
    mode: str = "fill"           # fill | partial | never | reject | lost_response | legged
    fill_qty: int | None = None  # for partial
    fill_after_polls: int = 0    # how many status checks before the fill appears
    fill_on_cancel: bool = False  # the fill races the cancel request


class SimulatedBroker:
    is_paper = True

    def __init__(self, equity: float = 20_000.0, leg_prices: dict[str, float] | None = None):
        self._equity = equity
        self._last_equity: float | None = None
        self.leg_prices = leg_prices or {}
        self.behaviours: list[SimBehaviour] = []
        self.orders: dict[str, BrokerOrder] = {}
        self._meta: dict[str, tuple[list[OrderLeg], SimBehaviour, list[int]]] = {}
        self._positions: dict[str, int] = {}
        self._ids = itertools.count(1)
        self.submitted: list[str] = []
        self.cancel_calls = 0

    def queue(self, *behaviours: SimBehaviour) -> None:
        self.behaviours.extend(behaviours)

    def _apply_fill(self, oid: str, qty: int) -> None:
        o = self.orders[oid]
        legs, beh, _ = self._meta[oid]
        legs_to_fill = [l for l in legs if l.side == "sell"] if beh.mode == "legged" else legs
        for l in legs_to_fill:
            sign = 1 if l.side == "buy" else -1
            self._positions[l.symbol] = self._positions.get(l.symbol, 0) + sign * l.ratio * qty
            if self._positions[l.symbol] == 0:
                del self._positions[l.symbol]
        prices = {l.symbol: self.leg_prices.get(l.symbol, 1.0) for l in legs}
        # Make the legs' net match the limit, like a real fill at the limit.
        if legs:
            first = legs[0]
            others = sum((1 if l.side == "buy" else -1) * l.ratio * prices[l.symbol] for l in legs[1:])
            sign = 1 if first.side == "buy" else -1
            prices[first.symbol] = round((o.limit_price - others) / (sign * first.ratio), 4)
        status = OrderStatus.FILLED if o.filled_qty + qty >= o.qty else OrderStatus.PARTIAL
        self.orders[oid] = replace(o, filled_qty=o.filled_qty + qty, status=status, leg_fill_prices=prices)

    def submit_multileg(self, client_order_id, legs, qty, limit_price):
        if any(o.client_order_id == client_order_id for o in self.orders.values()):
            raise ValueError("client_order_id must be unique")      # mirrors the real broker
        beh = self.behaviours.pop(0) if self.behaviours else SimBehaviour()
        oid = f"sim-{next(self._ids)}"
        status = OrderStatus.REJECTED if beh.mode == "reject" else OrderStatus.WORKING
        self.orders[oid] = BrokerOrder(oid, client_order_id, status, qty, 0, limit_price)
        self._meta[oid] = (legs, beh, [0])
        self.submitted.append(client_order_id)
        if beh.mode == "partial":
            self._apply_fill(oid, beh.fill_qty or max(qty // 2, 1))
        if beh.mode == "lost_response":
            raise SubmitTimeout("connection reset after send")
        return self.orders[oid]

    def submit_stock(self, client_order_id, symbol, side, qty, limit_price):
        sign = 1 if side == "buy" else -1
        return self.submit_multileg(client_order_id, [OrderLeg(symbol, side, 1, "stock")], qty, sign * limit_price)

    def get_order(self, broker_id):
        o = self.orders[broker_id]
        legs, beh, polls = self._meta[broker_id]
        polls[0] += 1
        if o.status == OrderStatus.WORKING and beh.mode in ("fill", "lost_response", "legged") and polls[0] > beh.fill_after_polls:
            self._apply_fill(broker_id, o.qty - o.filled_qty)
        return self.orders[broker_id]

    def find_order(self, client_order_id):
        return next((o for o in self.orders.values() if o.client_order_id == client_order_id), None)

    def cancel_order(self, broker_id):
        self.cancel_calls += 1
        o = self.orders[broker_id]
        _, beh, _ = self._meta[broker_id]
        if o.status.terminal:
            return
        if beh.fill_on_cancel:
            self._apply_fill(broker_id, o.qty - o.filled_qty)
            return
        self.orders[broker_id] = replace(self.orders[broker_id], status=OrderStatus.CANCELED)

    def open_orders(self):
        return [o for o in self.orders.values() if not o.status.terminal]

    def positions(self):
        return [BrokerPosition(s, q, self.leg_prices.get(s, 1.0), "us_option" if len(s) > 6 else "us_equity")
                for s, q in self._positions.items()]

    def equity(self):
        return self._equity

    def last_equity(self):
        return self._equity if self._last_equity is None else self._last_equity

    # test helpers
    def add_position(self, symbol: str, qty: int) -> None:
        self._positions[symbol] = self._positions.get(symbol, 0) + qty
        if self._positions[symbol] == 0:
            del self._positions[symbol]
