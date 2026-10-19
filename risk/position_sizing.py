"""Percentage-based sizing: how many spreads fit both the per-trade and the
remaining aggregate risk budget. Never rounds up."""

from __future__ import annotations

import math


def size_contracts(max_loss_per_spread: float, per_trade_budget: float, remaining_aggregate: float) -> int:
    if max_loss_per_spread <= 0 or not math.isfinite(max_loss_per_spread):
        return 0
    allowance = min(per_trade_budget, remaining_aggregate)
    return max(int(allowance // max_loss_per_spread), 0)
