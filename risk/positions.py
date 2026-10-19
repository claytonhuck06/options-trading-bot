"""Positions, live valuation and Greeks for the risk engine.

Values are always taken at EXECUTABLE prices where it matters: a long leg is
worth its bid (what selling it would fetch) and a short leg costs its ask
(what buying it back would cost). That makes unrealised P&L conservative.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime

from pricing.black_scholes import greeks as bs_greeks
from pricing.black_scholes import price as bs_price

MULTIPLIER = 100
SECONDS_PER_YEAR = 365.0 * 24 * 3600


@dataclass(frozen=True)
class OptionLeg:
    symbol: str
    underlying: str
    option_type: str
    strike: float
    expiration: date
    quantity: int                  # signed contracts: +long, -short
    entry_price: float             # per share, the price actually paid / received


@dataclass(frozen=True)
class LegQuote:
    bid: float
    ask: float
    iv: float                      # implied vol to reprice the leg under stress
    quote_ts: datetime

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2


@dataclass
class Position:
    position_id: str
    underlying: str
    legs: list[OptionLeg]
    opened_at: datetime
    max_loss: float                # $ for the whole position, fees included
    profit_target: float           # $ for the whole position
    stop_loss: float               # $ loss at which the position is closed (positive number)
    anchor_symbol: str | None = None
    entry_fees: float = 0.0        # $ already paid
    exit_fees: float = 0.0         # $ expected to close

    @property
    def expiration(self) -> date:
        return min(l.expiration for l in self.legs)

    def uncovered_shorts(self) -> list[OptionLeg]:
        """Short legs not matched by an equal long leg of the same type and expiry. Must always be empty."""
        problems = []
        for kind in ("call", "put"):
            for exp in {l.expiration for l in self.legs}:
                longs = sum(l.quantity for l in self.legs if l.option_type == kind and l.expiration == exp and l.quantity > 0)
                shorts = -sum(l.quantity for l in self.legs if l.option_type == kind and l.expiration == exp and l.quantity < 0)
                if shorts > longs:
                    problems += [l for l in self.legs if l.option_type == kind and l.expiration == exp and l.quantity < 0]
        return problems


@dataclass(frozen=True)
class StockHedge:
    symbol: str
    shares: int                    # signed
    entry_price: float


@dataclass
class Portfolio:
    positions: list[Position] = field(default_factory=list)
    hedges: list[StockHedge] = field(default_factory=list)


def years_left(expiration: date, now: datetime) -> float:
    from zoneinfo import ZoneInfo
    close = datetime(expiration.year, expiration.month, expiration.day, 16, 0, tzinfo=ZoneInfo("America/New_York"))
    return max((close - now).total_seconds(), 0.0) / SECONDS_PER_YEAR


def liquidation_value(pos: Position, quotes: dict[str, LegQuote]) -> float:
    """$ received (positive) or paid (negative) to close every leg now at executable prices."""
    total = 0.0
    for l in pos.legs:
        q = quotes[l.symbol]
        total += l.quantity * (q.bid if l.quantity > 0 else q.ask) * MULTIPLIER
    return total


def entry_cost(pos: Position) -> float:
    """$ paid (positive) or received (negative) to open."""
    return sum(l.quantity * l.entry_price * MULTIPLIER for l in pos.legs)


def unrealized_pnl(pos: Position, quotes: dict[str, LegQuote]) -> float:
    return liquidation_value(pos, quotes) - entry_cost(pos) - pos.entry_fees - pos.exit_fees


def model_value(pos: Position, quotes: dict[str, LegQuote], spot: float, now: datetime, rate: float,
                spot_shift: float = 0.0, vol_shift: float = 0.0) -> float:
    """$ value of the position at model prices, optionally under a shocked spot (fraction) and vol (absolute)."""
    s = spot * (1 + spot_shift)
    total = 0.0
    for l in pos.legs:
        T = years_left(l.expiration, now)
        iv = max(quotes[l.symbol].iv + vol_shift, 0.01)
        total += l.quantity * bs_price(s, l.strike, T, rate, 0.0, iv, l.option_type) * MULTIPLIER
    return total


@dataclass(frozen=True)
class Exposure:
    delta_shares: float            # SPY-equivalent shares (beta-weighted)
    delta_dollars: float
    gamma: float
    vega: float                    # $ per 1 vol point
    theta: float                   # $ per calendar day


def exposure(pos: Position, quotes: dict[str, LegQuote], spot: float, now: datetime, rate: float, beta: float,
             spy_price: float) -> Exposure:
    d = g = v = th = 0.0
    for l in pos.legs:
        T = years_left(l.expiration, now)
        if T <= 0:
            continue
        gr = bs_greeks(spot, l.strike, T, rate, 0.0, quotes[l.symbol].iv, l.option_type)
        q = l.quantity * MULTIPLIER
        d += q * gr.delta
        g += q * gr.gamma
        v += q * gr.vega / 100
        th += q * gr.theta / 365
    spy_shares = d * beta * spot / spy_price
    return Exposure(spy_shares, spy_shares * spy_price, g, v, th)


def max_defined_loss(pos: Position) -> float:
    """$ worst case at expiry for a set of same-expiry legs, from the payoff at every strike (and beyond)."""
    strikes = sorted({l.strike for l in pos.legs})
    points = [0.0] + strikes + [strikes[-1] * 10]
    worst = math.inf
    for s in points:
        payoff = sum(l.quantity * MULTIPLIER * (max(s - l.strike, 0) if l.option_type == "call" else max(l.strike - s, 0))
                     for l in pos.legs)
        worst = min(worst, payoff)
    return entry_cost(pos) - worst + pos.entry_fees + pos.exit_fees
