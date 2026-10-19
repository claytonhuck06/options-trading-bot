"""When portfolio delta drifts outside its limit, choose the cheapest fix.

Options compared:
  A. hedge     buy/sell SPY shares to bring delta back inside the limit
  B. reduce    close part of the position contributing the most delta
  C. close     close that position entirely
Cost = the bid-ask paid plus fees. The cheapest option that restores the
limit wins. Limits are never raised to keep a losing position alive, and a
hedge that would exceed the share cap is not offered.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from core.config import RiskLimits
from risk.positions import MULTIPLIER, Exposure, LegQuote, Position

STOCK_HALF_SPREAD = 0.01          # $ per share, SPY is penny-wide
STOCK_FEE_PER_SHARE = 0.0


@dataclass(frozen=True)
class HedgeAction:
    kind: str                     # none | hedge | reduce | close
    position_id: str | None
    quantity: int                 # shares for a hedge (signed), spreads for reduce/close
    cost: float                   # $ estimated
    delta_after: float            # $ SPY-equivalent


def _close_cost(pos: Position, quotes: dict[str, LegQuote], fraction: float, fee_per_contract: float) -> float:
    half = sum(abs(l.quantity) * fraction * (quotes[l.symbol].ask - quotes[l.symbol].bid) / 2 * MULTIPLIER for l in pos.legs)
    fees = sum(abs(l.quantity) * fraction for l in pos.legs) * fee_per_contract
    return half + fees


def choose_hedge(positions: list[Position], exposures: dict[str, Exposure], hedge_delta_dollars: float,
                 quotes: dict[str, LegQuote], spy_price: float, equity: float, limits: RiskLimits,
                 fee_per_contract: float) -> HedgeAction:
    total = sum(e.delta_dollars for e in exposures.values()) + hedge_delta_dollars
    cap = limits.max_net_delta_pct * equity
    if abs(total) <= cap:
        return HedgeAction("none", None, 0, 0.0, total)

    options: list[HedgeAction] = []
    excess = total - math.copysign(cap, total)
    if limits.allow_stock_hedge:
        shares = -math.ceil(abs(excess) / spy_price) * int(math.copysign(1, excess))
        if abs(shares) <= limits.max_hedge_shares:
            options.append(HedgeAction("hedge", None, shares, abs(shares) * (STOCK_HALF_SPREAD + STOCK_FEE_PER_SHARE),
                                       total + shares * spy_price))

    worst = max(positions, key=lambda p: exposures[p.position_id].delta_dollars * math.copysign(1, total))
    e = exposures[worst.position_id]
    spreads = max(abs(l.quantity) for l in worst.legs)
    if e.delta_dollars * total > 0 and spreads > 1:
        per_spread = e.delta_dollars / spreads
        k = min(math.ceil(abs(excess / per_spread)), spreads)
        if k < spreads:
            options.append(HedgeAction("reduce", worst.position_id, k, _close_cost(worst, quotes, k / spreads, fee_per_contract),
                                       total - per_spread * k))
    options.append(HedgeAction("close", worst.position_id, spreads, _close_cost(worst, quotes, 1.0, fee_per_contract),
                               total - e.delta_dollars))

    valid = [o for o in options if abs(o.delta_after) <= cap]
    pool = valid or [o for o in options if o.kind == "close"]
    return min(pool, key=lambda o: o.cost)
