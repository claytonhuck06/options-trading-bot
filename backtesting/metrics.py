"""The standard backtest report numbers, computed the same way for every strategy.

  win rate          share of trades that made money after all costs
  profit factor     money made on winners / money lost on losers (above 1 = profitable overall)
  expectancy        average $ per trade, with its t-statistic (above ~2 = unlikely to be luck)
  worst drawdown    largest fall from a previous equity peak, in $ and %, and how long it lasted
  Sharpe ratio      average daily return / its volatility, annualised (risk-adjusted return; cash earns 0)
  Sortino ratio     the same, counting only downside volatility
Every figure uses P&L after bid/ask costs and fees as the backtest recorded it.
"""

from __future__ import annotations

import math
import statistics
from collections import Counter
from dataclasses import dataclass, field
from datetime import date

TRADING_DAYS_PER_YEAR = 252


@dataclass
class Metrics:
    trades: int = 0
    win_rate: float | None = None
    avg_win: float | None = None
    avg_loss: float | None = None
    profit_factor: float | None = None
    expectancy: float | None = None
    expectancy_t: float | None = None
    total_pnl: float = 0.0
    return_pct: float = 0.0
    max_drawdown: float = 0.0          # $ (positive number)
    max_drawdown_pct: float = 0.0      # fraction of the peak
    longest_drawdown_days: int = 0
    sharpe: float | None = None
    sortino: float | None = None
    days: int = 0
    exit_reasons: dict[str, int] = field(default_factory=dict)


def _ratio(mean: float, spread: float) -> float | None:
    return mean / spread * math.sqrt(TRADING_DAYS_PER_YEAR) if spread > 0 else None


def compute(trade_pnls: list[float], daily: list[tuple[date, float, float]], equity0: float,
            exit_reasons: list[str] | None = None) -> Metrics:
    """trade_pnls: $ per closed trade. daily: (day, start equity, end equity), in date order."""
    m = Metrics(trades=len(trade_pnls), days=len(daily), exit_reasons=dict(Counter(exit_reasons or [])))
    if trade_pnls:
        wins = [p for p in trade_pnls if p > 0]
        losses = [p for p in trade_pnls if p <= 0]
        m.win_rate = len(wins) / len(trade_pnls)
        m.avg_win = statistics.fmean(wins) if wins else None
        m.avg_loss = statistics.fmean(losses) if losses else None
        lost = -sum(losses)
        m.profit_factor = sum(wins) / lost if lost > 0 else (math.inf if wins else None)
        m.expectancy = statistics.fmean(trade_pnls)
        if len(trade_pnls) > 1 and statistics.stdev(trade_pnls) > 0:
            m.expectancy_t = m.expectancy / (statistics.stdev(trade_pnls) / math.sqrt(len(trade_pnls)))
    m.total_pnl = sum(trade_pnls)
    m.return_pct = m.total_pnl / equity0 if equity0 else 0.0

    if daily:
        peak, peak_day_idx, longest = equity0, -1, 0
        for i, (_, _, end) in enumerate(daily):
            if end >= peak:
                peak, peak_day_idx = end, i
            else:
                dd = peak - end
                if dd > m.max_drawdown:
                    m.max_drawdown, m.max_drawdown_pct = dd, dd / peak
                longest = max(longest, i - peak_day_idx)
        m.longest_drawdown_days = longest
        rets = [(end - start) / start for _, start, end in daily if start > 0]
        if len(rets) > 1:
            mean = statistics.fmean(rets)
            m.sharpe = _ratio(mean, statistics.stdev(rets))
            downside = math.sqrt(sum(min(r, 0.0) ** 2 for r in rets) / len(rets))
            m.sortino = _ratio(mean, downside)
    return m


def from_result(result, equity0: float) -> Metrics:
    """Metrics for a backtesting.engine.BacktestResult (or anything with .trades and .daily)."""
    closed = [t for t in result.trades if t.pnl is not None]
    return compute([t.pnl for t in closed], result.daily, equity0, [t.reason or "unknown" for t in closed])


def fmt(value: float | None, kind: str = "num") -> str:
    if value is None:
        return "–"
    if isinstance(value, float) and math.isinf(value):
        return "∞"
    return {"usd": f"${value:,.0f}", "pct": f"{value * 100:.1f}%", "ratio": f"{value:.2f}"}.get(kind, f"{value:,.2f}")


def table(m: Metrics) -> list[str]:
    """Markdown rows for the standard report."""
    return [
        "| Measure | Value |", "|---|---:|",
        f"| Trades | {m.trades} |",
        f"| Win rate | {fmt(m.win_rate, 'pct')} |",
        f"| Average win / loss | {fmt(m.avg_win, 'usd')} / {fmt(m.avg_loss, 'usd')} |",
        f"| Profit factor | {fmt(m.profit_factor, 'ratio')} |",
        f"| Average per trade (t-stat) | {fmt(m.expectancy, 'usd')} ({fmt(m.expectancy_t, 'ratio')}) |",
        f"| Total P&L (return) | {fmt(m.total_pnl, 'usd')} ({fmt(m.return_pct, 'pct')}) |",
        f"| Worst drawdown | {fmt(m.max_drawdown, 'usd')} ({fmt(m.max_drawdown_pct, 'pct')}), {m.longest_drawdown_days} days under water |",
        f"| Sharpe ratio (annualised) | {fmt(m.sharpe, 'ratio')} |",
        f"| Sortino ratio (annualised) | {fmt(m.sortino, 'ratio')} |",
        f"| Trading days | {m.days} |",
    ]
