"""Turn percentage risk limits into the day's dollar limits.

Daily limits use start-of-day equity. Drawdown limits use the equity
high-water mark. This module only does arithmetic; enforcement comes in
Phase 5.
"""

from __future__ import annotations

from dataclasses import dataclass

from core.config import RiskLimits


@dataclass(frozen=True)
class DailyRiskBudget:
    start_of_day_equity: float
    high_water_mark: float
    max_risk_per_trade: float
    max_aggregate_planned_risk: float
    daily_loss_trigger: float
    daily_profit_ceiling: float
    drawdown_warning_equity: float
    drawdown_shutdown_equity: float
    max_concurrent_positions: int


def _round(x: float) -> float:
    return round(x, 2)


def compute_daily_budget(limits: RiskLimits, start_of_day_equity: float, high_water_mark: float | None = None) -> DailyRiskBudget:
    if start_of_day_equity <= 0:
        raise ValueError("start_of_day_equity must be positive")
    hwm = max(high_water_mark or start_of_day_equity, start_of_day_equity)
    return DailyRiskBudget(
        start_of_day_equity=_round(start_of_day_equity),
        high_water_mark=_round(hwm),
        max_risk_per_trade=_round(start_of_day_equity * limits.per_trade_risk_pct),
        max_aggregate_planned_risk=_round(start_of_day_equity * limits.aggregate_planned_risk_pct),
        daily_loss_trigger=_round(start_of_day_equity * limits.daily_loss_limit_pct),
        daily_profit_ceiling=_round(start_of_day_equity * limits.daily_profit_stop_pct),
        drawdown_warning_equity=_round(hwm * (1 - limits.drawdown_warning_pct)),
        drawdown_shutdown_equity=_round(hwm * (1 - limits.drawdown_shutdown_pct)),
        max_concurrent_positions=limits.max_concurrent_positions,
    )


def take_profit_target(limits: RiskLimits, capital_at_risk: float) -> float:
    """Profit target in dollars, measured against the trade's capital at risk."""
    if capital_at_risk <= 0:
        raise ValueError("capital_at_risk must be positive")
    return _round(capital_at_risk * limits.take_profit_pct_of_risk)
