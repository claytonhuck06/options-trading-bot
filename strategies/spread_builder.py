"""Defined-risk vertical spreads built around apparently mispriced contracts.

A vertical spread buys one option and sells another of the same type and
expiry, in equal quantity. Its payoff is bounded on both sides, so the
maximum loss is known before entry; no leg is ever an uncovered short.

Anchors are contracts the pricing engine says look cheap (buy them) or rich
(sell them). Each anchor is paired with a clean neighbouring strike on the
opposite side of the trade, in both directions, for every configured width.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date

from core.config import StrategySettings
from pricing.engine import ContractResult, PricingResult

MULTIPLIER = 100


@dataclass(frozen=True)
class Leg:
    contract: ContractResult
    side: int                       # +1 buy, -1 sell

    @property
    def strike(self) -> float:
        return self.contract.option.strike

    @property
    def price(self) -> float:
        """Executable price: pay the ask to buy, receive the bid to sell."""
        o = self.contract.option
        return o.ask if self.side > 0 else o.bid

    @property
    def symbol(self) -> str:
        return self.contract.option.symbol


@dataclass(frozen=True)
class VerticalSpread:
    long: Leg
    short: Leg
    anchor: str                     # symbol of the mispriced leg the trade is built around

    def __post_init__(self):
        a, b = self.long.contract.option, self.short.contract.option
        if self.long.side != 1 or self.short.side != -1:
            raise ValueError("long leg must be bought and short leg sold")
        if a.option_type != b.option_type or a.expiration != b.expiration or a.strike == b.strike:
            raise ValueError("a vertical needs the same type and expiry and different strikes")

    @property
    def legs(self) -> tuple[Leg, Leg]:
        return self.long, self.short

    @property
    def option_type(self) -> str:
        return self.long.contract.option.option_type

    @property
    def expiration(self) -> date:
        return self.long.contract.option.expiration

    @property
    def width(self) -> float:
        return abs(self.long.strike - self.short.strike)

    @property
    def net_premium(self) -> float:
        """Per share. Positive = debit paid, negative = credit received."""
        return self.long.price - self.short.price

    @property
    def structure(self) -> str:
        """Debit when the long leg is the more valuable one by construction:
        a lower-strike call or a higher-strike put."""
        lower_long = self.long.strike < self.short.strike
        return "debit" if lower_long == (self.option_type == "call") else "credit"

    @property
    def direction(self) -> str:
        # For both calls and puts, owning the lower strike and selling the higher one gains as SPY rises.
        return "bullish" if self.long.strike < self.short.strike else "bearish"

    def _payoff_range(self) -> tuple[float, float]:
        kl, ks = self.long.strike, self.short.strike
        diff = (ks - kl) if self.option_type == "call" else (kl - ks)
        return min(0.0, diff), max(0.0, diff)

    @property
    def max_loss(self) -> float:
        """Per share, before fees: what we pay minus the worst payoff at expiry."""
        return self.net_premium - self._payoff_range()[0]

    @property
    def max_profit(self) -> float:
        return self._payoff_range()[1] - self.net_premium

    @property
    def fair_value(self) -> float:
        return self.long.contract.fair_price - self.short.contract.fair_price

    @property
    def entry_edge(self) -> float:
        """Per share: model value of the position minus what it costs at executable prices."""
        return self.fair_value - self.net_premium

    @property
    def uncertainty(self) -> float:
        return math.hypot(self.long.contract.price_uncertainty, self.short.contract.price_uncertainty)

    @property
    def exit_half_spreads(self) -> float:
        """Per share: what closing both legs costs relative to mid."""
        return sum((l.contract.option.ask - l.contract.option.bid) / 2 for l in self.legs)

    def net_greek(self, name: str) -> float:
        return sum(l.side * getattr(l.contract, name) for l in self.legs)

    def describe(self) -> str:
        o = self.long.contract.option
        exp = o.expiration.strftime("%b %d")
        kind = "C" if self.option_type == "call" else "P"
        return (f"{self.direction} {self.option_type} {self.structure} spread {exp}: "
                f"buy {self.long.strike:g}{kind} @ {self.long.price:.2f} / sell {self.short.strike:g}{kind} @ {self.short.price:.2f}")


def box_gap(spread: VerticalSpread, result: PricingResult) -> float | None:
    """Per share: how far the quotes put a riskless box in our favour, or None if it cannot be checked.

    Our vertical plus the opposite-type vertical at the same two strikes (buy the
    complement of the short leg, sell the complement of the long leg) is a box,
    worth the discounted strike width whatever SPY does. Buying a box (a debit
    vertical) for less than that, or selling one (a credit vertical) for more,
    at executable prices is free money that real quotes do not offer: one of
    the four quotes is wrong. The engine checks call/put parity only near the
    money, so this is the parity check for in-the-money legs. Positive = impossible.
    """
    other = "put" if spread.option_type == "call" else "call"
    by_key = {(c.option.expiration, c.option.option_type, c.option.strike): c for c in result.contracts}
    comp_long = by_key.get((spread.expiration, other, spread.long.strike))
    comp_short = by_key.get((spread.expiration, other, spread.short.strike))
    rate = next((e.rate for e in result.expiries if e.expiration == spread.expiration), None)
    if comp_long is None or comp_short is None or rate is None:
        return None
    pv_width = spread.width * math.exp(-rate * spread.long.contract.years)
    box_cost = spread.net_premium + comp_short.option.ask - comp_long.option.bid
    return pv_width - box_cost if spread.structure == "debit" else -box_cost - pv_width


def early_exercise_allowance(spread: VerticalSpread, result: PricingResult) -> float:
    """Per share: American early exercise lets a box drift from the European value by up to this much."""
    strikes = {spread.long.strike, spread.short.strike}
    return sum(c.early_exercise_premium for c in result.contracts
               if c.option.expiration == spread.expiration and c.option.strike in strikes)


def build_candidates(result: PricingResult, cfg: StrategySettings) -> list[VerticalSpread]:
    by_key = {(c.option.expiration, c.option.option_type, c.option.strike): c for c in result.contracts if c.reliable}
    seen, out = set(), []
    for anchor in by_key.values():
        sides = []
        if anchor.buy_z >= cfg.min_signal_z:
            sides.append(1)
        if anchor.sell_z >= cfg.min_signal_z:
            sides.append(-1)
        for side in sides:
            for width in cfg.spread_widths:
                for direction in (-1, 1):
                    k = anchor.option.strike + direction * width
                    partner = by_key.get((anchor.option.expiration, anchor.option.option_type, k))
                    if partner is None:
                        continue
                    long_c, short_c = (anchor, partner) if side > 0 else (partner, anchor)
                    key = (long_c.option.symbol, short_c.option.symbol)
                    if key in seen:
                        continue
                    seen.add(key)
                    out.append(VerticalSpread(Leg(long_c, 1), Leg(short_c, -1), anchor.option.symbol))
    return out
