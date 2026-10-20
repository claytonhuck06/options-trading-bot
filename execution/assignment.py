"""Recognise an early assignment instead of halting on it.

When one of our short options is exercised early, the broker removes that short leg and puts
SPY shares in the account: 100 short shares per assigned call, 100 long shares per assigned put.
Reconciliation alone would see "an option missing and shares we never bought" and halt.

This module only accepts the exact pattern an assignment produces: every missing contract is
one of OUR short legs, and the new shares equal exactly what those contracts deliver. Anything
else (a missing long leg, a share count that does not match) is not explained by assignment
and still halts the system through normal reconciliation.

The trader then records the assignment, removes the assigned leg from the book, sells or buys
back the shares, and closes what is left of the position.
"""

from __future__ import annotations

from dataclasses import dataclass

from execution.broker_api import BrokerPosition
from risk.positions import MULTIPLIER, OptionLeg, Position


@dataclass(frozen=True)
class Assignment:
    position_id: str
    symbol: str                # the assigned short option
    option_type: str
    strike: float
    contracts: int             # how many of our short contracts were assigned
    shares: int                # signed shares this delivered: -100/call, +100/put


def detect_assignments(book: list[Position], broker_positions: list[BrokerPosition], underlying: str,
                       known_shares: int = 0) -> list[Assignment]:
    """Assignments that fully explain the difference between our book and the broker, or [] if none do."""
    actual = {p.symbol: p.qty for p in broker_positions}
    new_shares = actual.get(underlying, 0) - known_shares
    if new_shares == 0:
        return []

    expected: dict[str, int] = {}
    for pos in book:
        for leg in pos.legs:
            expected[leg.symbol] = expected.get(leg.symbol, 0) + leg.quantity

    found: list[Assignment] = []
    for sym, exp_qty in expected.items():
        held = actual.get(sym, 0)
        if held == exp_qty:
            continue
        if exp_qty >= 0 or not (exp_qty < held <= 0):
            return []                          # a long leg changed, or more shorts appeared: not an assignment
        missing = held - exp_qty               # short contracts no longer at the broker
        for pos in book:                       # attribute the missing contracts to positions in book order
            for leg in pos.legs:
                if leg.symbol != sym or leg.quantity >= 0 or missing == 0:
                    continue
                n = min(-leg.quantity, missing)
                found.append(Assignment(pos.position_id, sym, leg.option_type, leg.strike, n, _delivered(leg, n)))
                missing -= n
    if not found or sum(a.shares for a in found) != new_shares:
        return []
    return found


def _delivered(leg: OptionLeg, contracts: int) -> int:
    return (-1 if leg.option_type == "call" else 1) * contracts * MULTIPLIER


def apply_to_position(pos: Position, assignments: list[Assignment]) -> Position:
    """The position with its assigned short contracts removed."""
    legs = []
    for leg in pos.legs:
        gone = sum(a.contracts for a in assignments if a.position_id == pos.position_id and a.symbol == leg.symbol)
        qty = leg.quantity + gone              # short quantity is negative; assigned contracts bring it toward zero
        if qty != 0:
            legs.append(OptionLeg(leg.symbol, leg.underlying, leg.option_type, leg.strike, leg.expiration, qty, leg.entry_price))
    pos.legs = legs
    return pos
