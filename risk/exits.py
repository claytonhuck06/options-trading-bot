"""Exit rules, checked for every open position every cycle (first match wins).

  emergency     kill switch at FLATTEN_TODAY or HALTED
  market_close  at or after the configured begin-closing time (no overnight risk)
  stop_loss     unrealised loss >= the position's stop (50% of max loss by default)
  profit_target unrealised profit >= the position's target (20% of max loss by default)
  mispricing    the anchor's mispricing has mostly closed (|z| below the exit level)
  time          held longer than the maximum holding period

P&L is measured at executable prices including the fees to close, so a
"profit" exit is profit you could actually take. A position only counts as
closed once the broker confirms the fills (Phase 6).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from zoneinfo import ZoneInfo

from core.config import Settings
from risk.positions import LegQuote, Position, unrealized_pnl
from risk.state import Level

EASTERN = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class ExitDecision:
    close: bool
    reason: str | None
    pnl: float


def evaluate_exit(pos: Position, quotes: dict[str, LegQuote], now: datetime, settings: Settings,
                  level: Level = Level.NORMAL, anchor_z: float | None = None) -> ExitDecision:
    pnl = unrealized_pnl(pos, quotes)
    lim = settings.risk
    if level >= Level.FLATTEN_TODAY:
        return ExitDecision(True, "emergency", pnl)
    if now.astimezone(EASTERN).time() >= time.fromisoformat(settings.schedule.begin_closing):
        return ExitDecision(True, "market_close", pnl)
    if pnl <= -pos.stop_loss:
        return ExitDecision(True, "stop_loss", pnl)
    if pnl >= pos.profit_target:
        return ExitDecision(True, "profit_target", pnl)
    if anchor_z is not None and abs(anchor_z) < lim.mispricing_exit_z:
        return ExitDecision(True, "mispricing_gone", pnl)
    if (now - pos.opened_at).total_seconds() >= lim.max_holding_minutes * 60:
        return ExitDecision(True, "time", pnl)
    return ExitDecision(False, None, pnl)
