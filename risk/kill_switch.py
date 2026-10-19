"""Kill switch: independent of any language model, evaluated every cycle.

Triggers and what they do:
  daily loss >= limit (incl. unrealised)        -> FLATTEN_TODAY
  daily profit >= ceiling                       -> FLATTEN_TODAY
  drawdown from high-water mark >= warning      -> NO_NEW_ENTRIES until a person acknowledges it for the
                                                   day (needed again each day while below the line)
  drawdown >= hard limit                        -> HALTED
  market data stale while positions are open    -> HALTED
  broker positions don't reconcile              -> HALTED
  repeated execution failures                   -> HALTED
  unexpected / uncovered position               -> HALTED
  portfolio risk cannot be calculated           -> HALTED
  manual stop (STOP file or command)            -> HALTED
Levels only escalate during a day; HALTED needs a manual, authorized reset.
A shutdown cannot guarantee instant liquidation in a disrupted market.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from core.config import RiskLimits
from risk.state import DaySnapshot, Level, RiskStateStore


@dataclass
class HealthInputs:
    equity: float                          # current account equity incl. unrealised P&L
    open_positions: int
    data_age_seconds: float | None         # age of the freshest market data; None = unknown
    reconciled: bool = True
    consecutive_execution_failures: int = 0
    unexpected_positions: list[str] = field(default_factory=list)
    uncovered_shorts: list[str] = field(default_factory=list)
    risk_calc_ok: bool = True
    manual_stop: bool = False


@dataclass(frozen=True)
class KillSwitchResult:
    level: Level
    triggers: list[str]


def evaluate(inputs: HealthInputs, day: DaySnapshot, limits: RiskLimits, state: RiskStateStore, today: date) -> KillSwitchResult:
    triggers: list[tuple[Level, str]] = []
    pnl = inputs.equity - day.start_equity
    hwm = max(state.update_high_water_mark(inputs.equity), day.high_water_mark)
    drawdown = 1 - inputs.equity / hwm if hwm > 0 else 0.0

    if pnl <= -limits.daily_loss_limit_pct * day.start_equity:
        triggers.append((Level.FLATTEN_TODAY, "daily_loss_limit"))
    if pnl >= limits.daily_profit_stop_pct * day.start_equity:
        triggers.append((Level.FLATTEN_TODAY, "daily_profit_ceiling"))
    if drawdown >= limits.drawdown_shutdown_pct:
        triggers.append((Level.HALTED, "drawdown_shutdown"))
    elif drawdown >= limits.drawdown_warning_pct:
        if state.warning_acknowledged(today):
            triggers.append((Level.NORMAL, "drawdown_warning_acknowledged"))
        else:
            triggers.append((Level.NO_NEW_ENTRIES, "drawdown_warning"))
    if inputs.open_positions and (inputs.data_age_seconds is None or inputs.data_age_seconds > limits.max_quote_age_with_positions_seconds):
        triggers.append((Level.HALTED, "market_data_unreliable_with_positions"))
    if not inputs.reconciled:
        triggers.append((Level.HALTED, "broker_reconciliation_failed"))
    if inputs.consecutive_execution_failures >= limits.max_consecutive_execution_failures:
        triggers.append((Level.HALTED, "repeated_execution_failures"))
    if inputs.unexpected_positions:
        triggers.append((Level.HALTED, "unexpected_position"))
    if inputs.uncovered_shorts:
        triggers.append((Level.HALTED, "uncovered_short_option"))
    if not inputs.risk_calc_ok:
        triggers.append((Level.HALTED, "risk_calculation_failed"))
    if inputs.manual_stop:
        triggers.append((Level.HALTED, "manual_stop"))

    if not any(t == "drawdown_warning" for _, t in triggers):
        state.relax_warning(today)
    level = state.level(today)
    for lvl, name in triggers:
        level = state.escalate(lvl, name, today)
    return KillSwitchResult(level, [name for _, name in triggers])
