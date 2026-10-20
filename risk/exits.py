"""Exit rules, checked for every open position every cycle (first match wins).

  emergency     kill switch at FLATTEN_TODAY or HALTED
  market_close  at or after the configured begin-closing time (no overnight risk), except positions
                opened by a multi-day strategy while overnight holding is allowed
  near_expiry   multi-day position within its final week before expiry
  dividend_assignment_risk / early_assignment_risk
                multi-day position with a short leg likely to be exercised early (risk/overnight.py)
  gap_stop      multi-day position already past its stop in the first half hour (the market gapped
                overnight); closed like a stop but allowed a wider price to get out
  stop_loss     unrealised loss >= the position's stop (50% of max loss by default)
  profit_target unrealised profit >= the position's target (20% of max loss by default)
  mispricing    the anchor's mispricing has mostly closed (|z| below the exit level)
  time          held longer than the maximum holding period (same-day positions only)

Weekly call spreads (strategy "weekly_spread") have their own short list: emergency, near_expiry,
early-assignment risk, and a time exit at the daily check once within time_exit_dte days of expiry.

P&L is measured at executable prices including the fees to close, so a
"profit" exit is profit you could actually take. A position only counts as
closed once the broker confirms the fills (Phase 6).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from zoneinfo import ZoneInfo

from core.config import Settings
from risk.overnight import assignment_risk, near_expiry
from risk.positions import LegQuote, Position, unrealized_pnl
from risk.state import Level

EASTERN = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class ExitDecision:
    close: bool
    reason: str | None
    pnl: float


def evaluate_exit(pos: Position, quotes: dict[str, LegQuote], now: datetime, settings: Settings,
                  level: Level = Level.NORMAL, anchor_z: float | None = None, spot: float | None = None) -> ExitDecision:
    pnl = unrealized_pnl(pos, quotes)
    lim = settings.risk
    local = now.astimezone(EASTERN)
    overnight = pos.hold_overnight and lim.allow_overnight_positions
    if level >= Level.FLATTEN_TODAY:
        return ExitDecision(True, "emergency", pnl)
    if pos.strategy == "weekly_spread":
        return _weekly_exit(pos, quotes, local, settings, pnl, spot)
    if not overnight and local.time() >= time.fromisoformat(settings.schedule.begin_closing):
        return ExitDecision(True, "market_close", pnl)
    if overnight:
        if near_expiry(pos, local.date(), lim.multiday):
            return ExitDecision(True, "near_expiry", pnl)
        if spot is not None:
            why = assignment_risk(pos, quotes, spot, local.date(), lim.multiday)
            if why:
                return ExitDecision(True, why, pnl)
        if pnl <= -pos.stop_loss and local.time() < time(10, 0) and pos.opened_at.astimezone(EASTERN).date() < local.date():
            return ExitDecision(True, "gap_stop", pnl)
    if pnl <= -pos.stop_loss:
        return ExitDecision(True, "stop_loss", pnl)
    if pnl >= pos.profit_target:
        return ExitDecision(True, "profit_target", pnl)
    if anchor_z is not None and abs(anchor_z) < lim.mispricing_exit_z:
        return ExitDecision(True, "mispricing_gone", pnl)
    if not overnight and (now - pos.opened_at).total_seconds() >= lim.max_holding_minutes * 60:
        return ExitDecision(True, "time", pnl)
    return ExitDecision(False, None, pnl)


def _weekly_exit(pos: Position, quotes: dict[str, LegQuote], local: datetime, settings: Settings, pnl: float,
                 spot: float | None) -> ExitDecision:
    """Weekly call spread: no stop and no profit target (as tested). Closed at the daily check time once it is
    within time_exit_dte days of expiry; closed at any time if a short leg risks early assignment, or if it is
    somehow inside the multi-day final-week guard."""
    ws, lim = settings.weekly_spread, settings.risk
    if near_expiry(pos, local.date(), lim.multiday):
        return ExitDecision(True, "near_expiry", pnl)
    if spot is not None:
        why = assignment_risk(pos, quotes, spot, local.date(), lim.multiday)
        if why:
            return ExitDecision(True, why, pnl)
    if (pos.expiration - local.date()).days <= ws.time_exit_dte and local.time() >= time.fromisoformat(ws.run_at):
        return ExitDecision(True, "time_exit", pnl)
    return ExitDecision(False, None, pnl)
