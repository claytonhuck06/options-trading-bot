"""Compare what we think we hold with what the broker says we hold.

The broker's confirmed positions and orders are the source of truth. Any
difference (a missing leg, an extra position, a quantity mismatch, an open
order we did not send) means the system's picture of its own risk is wrong,
so it must stop trading until a person resolves it. Broker positions are also
checked directly for short options without matching protection.
"""

from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass, field

from execution.broker_api import BrokerOrder, BrokerPosition
from risk.positions import Position

OCC = re.compile(r"^(?P<root>[A-Z.]{1,6})(?P<exp>\d{6})(?P<type>[CP])(?P<strike>\d{8})$")


@dataclass
class ReconResult:
    missing_at_broker: dict[str, int] = field(default_factory=dict)      # symbol -> qty we expected
    unexpected_at_broker: dict[str, int] = field(default_factory=dict)   # symbol -> qty the broker shows
    qty_mismatch: dict[str, tuple[int, int]] = field(default_factory=dict)   # symbol -> (expected, actual)
    unknown_open_orders: list[str] = field(default_factory=list)
    uncovered_shorts: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not (self.missing_at_broker or self.unexpected_at_broker or self.qty_mismatch
                    or self.unknown_open_orders or self.uncovered_shorts)

    def summary(self) -> str:
        if self.ok:
            return "reconciled"
        parts = []
        for name in ("missing_at_broker", "unexpected_at_broker", "qty_mismatch", "unknown_open_orders", "uncovered_shorts"):
            v = getattr(self, name)
            if v:
                parts.append(f"{name}={v}")
        return "; ".join(parts)


def uncovered_short_symbols(positions: list[BrokerPosition]) -> list[str]:
    groups: dict[tuple, list[tuple[str, int]]] = defaultdict(list)
    for p in positions:
        m = OCC.match(p.symbol)
        if p.asset_class != "us_option" or not m:
            continue
        groups[(m["root"], m["exp"], m["type"])].append((p.symbol, p.qty))
    bad = []
    for legs in groups.values():
        if -sum(q for _, q in legs if q < 0) > sum(q for _, q in legs if q > 0):
            bad += [s for s, q in legs if q < 0]
    return sorted(bad)


def reconcile(book: list[Position], broker_positions: list[BrokerPosition], broker_open_orders: list[BrokerOrder],
              hedge_shares: dict[str, int] | None = None, our_order_prefix: str = "oq-",
              expected_open_orders: set[str] | None = None) -> ReconResult:
    expected: dict[str, int] = defaultdict(int)
    for pos in book:
        for leg in pos.legs:
            expected[leg.symbol] += leg.quantity
    for sym, sh in (hedge_shares or {}).items():
        expected[sym] += sh
    actual = {p.symbol: p.qty for p in broker_positions}

    r = ReconResult()
    for sym in set(expected) | set(actual):
        e, a = expected.get(sym, 0), actual.get(sym, 0)
        if e == a:
            continue
        if a == 0:
            r.missing_at_broker[sym] = e
        elif e == 0:
            r.unexpected_at_broker[sym] = a
        else:
            r.qty_mismatch[sym] = (e, a)
    for o in broker_open_orders:
        mine = o.client_order_id.startswith(our_order_prefix)
        if not mine or (expected_open_orders is not None and o.client_order_id not in expected_open_orders):
            r.unknown_open_orders.append(o.client_order_id or o.broker_id)
    r.uncovered_shorts = uncovered_short_symbols(broker_positions)
    return r
