"""Standard backtest report numbers and walk-forward testing."""

import math
from dataclasses import dataclass, field
from datetime import date, timedelta

import pytest

from backtesting.metrics import compute, table
from backtesting.walk_forward import report_lines, walk_forward


def test_standard_measures():
    daily = [(date(2026, 1, 1), 100_000, 101_000), (date(2026, 1, 2), 101_000, 99_000), (date(2026, 1, 3), 99_000, 102_000)]
    m = compute([1_000, -2_000, 3_000], daily, 100_000, ["profit_target", "stop_loss", "profit_target"])
    assert m.trades == 3 and m.win_rate == pytest.approx(2 / 3)
    assert m.profit_factor == pytest.approx(4_000 / 2_000)
    assert m.expectancy == pytest.approx(2_000 / 3)
    assert m.total_pnl == 2_000 and m.return_pct == pytest.approx(0.02)
    assert m.max_drawdown == 2_000 and m.max_drawdown_pct == pytest.approx(2_000 / 101_000)
    assert m.longest_drawdown_days == 1
    assert m.sharpe is not None and m.sortino is not None and m.sortino > m.sharpe
    assert m.exit_reasons == {"profit_target": 2, "stop_loss": 1}
    assert any("Profit factor | 2.00" in row for row in table(m))


def test_no_losses_and_no_trades():
    assert math.isinf(compute([5, 10], [], 1_000).profit_factor)
    empty = compute([], [], 1_000)
    assert empty.win_rate is None and empty.sharpe is None and empty.max_drawdown == 0


# --- walk-forward with a fake strategy -----------------------------------------------------

@dataclass
class T:
    pnl: float
    reason: str = "x"


@dataclass
class R:
    trades: list = field(default_factory=list)
    daily: list = field(default_factory=list)


DAYS = [date(2026, 1, 1) + timedelta(days=i) for i in range(40)]


def fake_run(params, days):
    """'good' earns $100/day on days 0-29 and loses after; 'bad' always loses $50/day."""
    res, eq = R(), 100_000.0
    for d in days:
        pnl = (100 if DAYS.index(d) < 30 else -100) if params["k"] == "good" else -50
        res.trades.append(T(pnl))
        res.daily.append((d, eq, eq + pnl))
        eq += pnl
    return res


def test_walk_forward_picks_on_training_and_judges_only_on_test():
    seen = []

    def run(params, days):
        seen.append((params["k"], days[0], days[-1]))
        return fake_run(params, days)

    wf = walk_forward(DAYS, [{"k": "bad"}, {"k": "good"}], run, train_days=10, test_days=10, score="total_pnl", min_trades=1)
    assert [f.test for f in wf.folds] == [(DAYS[10], DAYS[19]), (DAYS[20], DAYS[29]), (DAYS[30], DAYS[39])]
    assert all(f.chosen == {"k": "good"} for f in wf.folds)
    # The last test window is where 'good' stops working: out-of-sample results show it.
    assert [f.out_of_sample.total_pnl for f in wf.folds] == [1_000, 1_000, -1_000]
    assert wf.positive_folds == 2 and wf.combined.total_pnl == 1_000 and wf.combined.trades == 30
    # Per fold: every candidate runs on the training window, then ONLY the chosen one runs on the test window.
    assert len(seen) == 3 * 3
    for i, f in enumerate(wf.folds):
        train_calls, test_call = seen[3 * i:3 * i + 2], seen[3 * i + 2]
        assert {c[1:] for c in train_calls} == {f.train} and test_call == ("good",) + f.test
    assert any("2 of 3 test windows made money" in line for line in report_lines(wf))


def test_walk_forward_rejects_impossible_windows():
    with pytest.raises(ValueError):
        walk_forward(DAYS[:5], [{}], fake_run, 10, 10)
