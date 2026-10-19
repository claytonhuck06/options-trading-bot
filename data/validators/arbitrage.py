"""Cross-quote consistency checks within one expiry.

Real, executable quotes cannot offer a riskless profit. When a set of quotes
does, at least one of them is wrong, so every quote involved is flagged and
kept out of the volatility fit. (We cannot tell which one is wrong.)

Checks, using executable prices (buy at ask, sell at bid):
  * monotonic   — a higher-strike call (lower-strike put) cannot be bid above
                  the ask of its neighbour.
  * vertical    — a call/put vertical spread cannot be sold for more than the
                  discounted strike difference.
  * butterfly   — buying K1 and K3 and selling 2x K2 (equal spacing) cannot
                  produce a credit.
  * parity      — call minus put must be consistent with the forward price.
"""

from __future__ import annotations

import math
from collections import defaultdict

from pricing.snapshot import SnapOption


def find_violations(options: list[SnapOption], years: float, rate: float, tolerance: float = 0.0) -> dict[str, set[str]]:
    """Return {symbol: {check names it failed}} for one expiry."""
    flags: dict[str, set[str]] = defaultdict(set)
    disc = math.exp(-rate * years)

    def flag(name: str, *opts: SnapOption) -> None:
        for o in opts:
            flags[o.symbol].add(name)

    for kind in ("call", "put"):
        legs = sorted((o for o in options if o.option_type == kind), key=lambda o: o.strike)
        for lo, hi in zip(legs, legs[1:]):
            width = (hi.strike - lo.strike) * disc
            if kind == "call":
                if hi.bid > lo.ask + tolerance:
                    flag("monotonic", lo, hi)
                if lo.bid - hi.ask > width + tolerance:
                    flag("vertical", lo, hi)
            else:
                if lo.bid > hi.ask + tolerance:
                    flag("monotonic", lo, hi)
                if hi.bid - lo.ask > width + tolerance:
                    flag("vertical", lo, hi)
        for a, b, c in zip(legs, legs[1:], legs[2:]):
            if abs((b.strike - a.strike) - (c.strike - b.strike)) < 1e-9:
                if a.ask + c.ask - 2 * b.bid < -tolerance:
                    flag("butterfly", a, b, c)
    return dict(flags)


def parity_violations(options: list[SnapOption], forward: float, years: float, rate: float, tolerance: float = 0.0,
                      max_moneyness: float | None = None) -> dict[str, set[str]]:
    """Flag call/put pairs whose executable prices are inconsistent with the forward.
    C - P = (F - K) * disc must fall inside [C_bid - P_ask, C_ask - P_bid].
    American early exercise breaks this equality for in-the-money options, so
    pairs further than max_moneyness (in log terms) from the forward are skipped."""
    disc = math.exp(-rate * years)
    calls = {o.strike: o for o in options if o.option_type == "call"}
    puts = {o.strike: o for o in options if o.option_type == "put"}
    flags: dict[str, set[str]] = {}
    for k in calls.keys() & puts.keys():
        if max_moneyness is not None and abs(math.log(k / forward)) > max_moneyness:
            continue
        c, p = calls[k], puts[k]
        target = (forward - k) * disc
        if not (c.bid - p.ask - tolerance <= target <= c.ask - p.bid + tolerance):
            flags[c.symbol] = {"parity"}
            flags[p.symbol] = {"parity"}
    return flags
