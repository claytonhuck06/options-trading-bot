"""Walk-forward testing: choose settings on past data, then judge them only on the data that follows.

The history is cut into consecutive folds. In each fold the candidate settings are all run on the
training window and the best one (by the chosen measure) is picked; that one setting is then run,
untouched, on the test window right after it. Only test windows count toward the verdict, and
stitched together they form one out-of-sample record that was never used to pick anything.
With a single candidate setting nothing is picked, and the folds simply show whether results hold
up period after period.

`run` is any function (settings, days) -> BacktestResult, so every strategy can be tested this way.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable

from backtesting.metrics import Metrics, compute, fmt, from_result, table

SCORES = ("sharpe", "total_pnl", "expectancy", "profit_factor")


@dataclass
class Fold:
    train: tuple[date, date]
    test: tuple[date, date]
    chosen: dict[str, Any]
    in_sample: Metrics
    out_of_sample: Metrics
    result: Any = None


@dataclass
class WalkForwardResult:
    folds: list[Fold] = field(default_factory=list)
    combined: Metrics = field(default_factory=Metrics)
    score: str = "sharpe"

    @property
    def positive_folds(self) -> int:
        return sum(1 for f in self.folds if f.out_of_sample.total_pnl > 0)


def _score(m: Metrics, name: str) -> float:
    v = getattr(m, name)
    if v is None or (isinstance(v, float) and math.isnan(v)):
        return -math.inf
    return v


def walk_forward(days: list[date], candidates: list[dict[str, Any]], run: Callable[[dict[str, Any], list[date]], Any],
                 train_days: int, test_days: int, step_days: int | None = None, equity0: float = 100_000.0,
                 score: str = "sharpe", min_trades: int = 5) -> WalkForwardResult:
    if score not in SCORES:
        raise ValueError(f"score must be one of {SCORES}")
    if not candidates:
        raise ValueError("at least one candidate setting is needed")
    if train_days < 1 or test_days < 1 or train_days + test_days > len(days):
        raise ValueError(f"need train ({train_days}) + test ({test_days}) days within the {len(days)} available")
    step = step_days or test_days
    out = WalkForwardResult(score=score)
    pnls: list[float] = []
    reasons: list[str] = []
    stitched: list[tuple[date, float, float]] = []
    equity = equity0

    start = 0
    while start + train_days + test_days <= len(days):
        train = days[start:start + train_days]
        test = days[start + train_days:start + train_days + test_days]
        best, best_metrics, best_score = candidates[0], None, -math.inf
        for params in candidates:
            m = from_result(run(params, train), equity0)
            # A setting that barely trades in training cannot be judged; it only wins if nothing else qualifies.
            s = _score(m, score) if m.trades >= min_trades else -math.inf
            if best_metrics is None or s > best_score:
                best, best_metrics, best_score = params, m, s
        res = run(best, test)
        oos = from_result(res, equity0)
        out.folds.append(Fold((train[0], train[-1]), (test[0], test[-1]), best, best_metrics, oos, res))

        closed = [t for t in res.trades if t.pnl is not None]
        pnls += [t.pnl for t in closed]
        reasons += [t.reason or "unknown" for t in closed]
        for d, s0, s1 in res.daily:                       # chain each fold's daily P&L onto one equity curve
            stitched.append((d, equity, equity + (s1 - s0)))
            equity += s1 - s0
        start += step

    out.combined = compute(pnls, stitched, equity0, reasons)
    return out


def report_lines(wf: WalkForwardResult) -> list[str]:
    L = ["## Walk-forward (out-of-sample only)", "",
         f"Settings chosen on each training window by **{wf.score.replace('_', ' ')}**, then run unchanged on the "
         f"following test window. {wf.positive_folds} of {len(wf.folds)} test windows made money.", "",
         "| Training window | Test window | Chosen setting | Training P&L | Test trades | Test P&L | Test win rate |",
         "|---|---|---|---:|---:|---:|---:|"]
    for f in wf.folds:
        chosen = ", ".join(f"{k}={v}" for k, v in f.chosen.items()) or "default"
        L.append(f"| {f.train[0]} → {f.train[1]} | {f.test[0]} → {f.test[1]} | {chosen} | {fmt(f.in_sample.total_pnl, 'usd')} | "
                 f"{f.out_of_sample.trades} | {fmt(f.out_of_sample.total_pnl, 'usd')} | {fmt(f.out_of_sample.win_rate, 'pct')} |")
    L += ["", "### All test windows combined", ""] + table(wf.combined)
    return L
