"""Relative momentum: hold the top 2 of 5 ETFs by 12-month total return, exactly as pre-registered.

Rules: docs/research/relative_momentum_preregistration.md.
  * At each month-end, score = total-return index now / 12 month-ends ago - 1; rank (ties to the first-listed
    asset), hold the top two at 50% each from the next open. Rebalanced every month, costs on every trade.
  * Benchmarks: the same five equal-weighted (rebalanced monthly) and SPY buy-and-hold.
  * Simulator, data and statistics come from research/multi_asset_trend.py and research/monthly_trend.py.
"""

from __future__ import annotations

import csv
import sqlite3
from datetime import date
from pathlib import Path

from research import monthly_trend as mt
from research import multi_asset_trend as ma
from research.monthly_trend import MarketData, Result, first_of_month, month_ends, stats

ASSETS = ma.ASSETS                          # SPY, EFA, IEF, VNQ, DBC: the trend test's basket, unchanged
NAMES = ma.NAMES
LOOKBACK = 12
TOP = 2
NEIGHBOURS = (11, 13)
INFO_LOOKBACKS = (3, 6, 9)
INFO_TOPS = (1, 3)
WARMUP_MONTHS = 14                          # 14 month-end closes so a 13-month lookback starts with the rest
COST = ma.COST
COST_LEVELS = ma.COST_LEVELS
STRESS_COST = ma.STRESS_COST
CONFIRMATION_START = ma.CONFIRMATION_START
DISCOVERY_END = ma.DISCOVERY_END
SPY_CONF_SHARPE_RATIO = 0.9
STOCKS = frozenset({"SPY", "EFA"})


def tr_index(data: MarketData) -> dict[date, float]:
    """Total-return index: 1 on the first day, then x (close + dividend that day) / previous close."""
    out, idx, prev = {}, 1.0, None
    for b in data.bars:
        if prev is not None:
            idx *= (b.close + data.dividends.get(b.day, 0.0)) / prev
        out[b.day] = idx
        prev = b.close
    return out


def rank(scores: dict[str, float], order: list[str]) -> list[str]:
    """Highest score first; ties go to the asset listed first."""
    return sorted(order, key=lambda s: (-scores[s], order.index(s)))


def momentum_rule(datas: dict[str, MarketData], lookback: int, top: int, end_before: date) -> ma.Rule:
    syms = list(datas)
    ends = month_ends(ma.common_days(datas, end_before), end_before)
    pos = {e: i for i, e in enumerate(ends)}
    tr = {s: tr_index(datas[s]) for s in syms}

    def rule(me: date):
        i = pos.get(me)
        if i is None or i < lookback:
            return None
        then = ends[i - lookback]
        scores = {s: tr[s][me] / tr[s][then] - 1 for s in syms}
        picked = set(rank(scores, syms)[:top])
        return scores, {s: s in picked for s in syms}, {s: (1 / top if s in picked else 0.0) for s in syms}
    return rule


def simulate(datas: dict[str, MarketData], lookback: int | None, cost: float, end_before: date, start: date,
             top: int = TOP) -> Result:
    """lookback=None is the equal-weight hold of the same assets."""
    if lookback is None:
        return ma.simulate(datas, None, cost, end_before, start)
    return ma.simulate(datas, lookback, cost, end_before, start, rule=momentum_rule(datas, lookback, top, end_before))


def log_check(res: Result, days: list[date], top: int = TOP) -> list[str]:
    """Bar 1: trade timing and 'held = chosen' (from the trend test), plus held = top `top` re-ranked from the log."""
    problems = ma.log_check(res, days)
    for m in res.months:
        if m.held_after is None:
            continue
        order = list(m.close)
        expect = frozenset(rank(m.average, order)[:top])
        if m.held_after != expect:
            problems.append(f"{m.month_end}: held {sorted(m.held_after)}, top {top} by score {sorted(expect)}")
    return problems


def turnover(res: Result) -> tuple[int, int]:
    """(month-ends where at least one holding changed, month-ends with a rebalance)."""
    held = [m.held_after for m in res.months if m.held_after is not None]
    return sum(a != b for a, b in zip(held, held[1:])), len(held)


def stock_share(res: Result) -> float:
    held = [m.held_after for m in res.months if m.held_after is not None]
    return sum(h == STOCKS for h in held) / len(held)


# --- Bars ----------------------------------------------------------------------------------------

def evaluate(main: Result, ew: Result, spy: Result, neighbours: dict[int, Result], stress: tuple[Result, Result],
             days: list[date], full: tuple[date, date], conf: tuple[date, date]) -> mt.Verdict:
    v = mt.Verdict()
    problems = log_check(main, days)
    v.bars.append(("1 · Log check", not problems, f"{len(main.trades)} trades over {len(main.months)} month-ends, "
                   f"{len(problems)} problems" + (f": {problems[:3]}" if problems else "")))
    s, b = stats(main, *full), stats(ew, *full)
    v.bars.append(("2 · Sharpe ≥ equal-weight hold (full)", s.sharpe >= b.sharpe, f"{s.sharpe:.2f} vs {b.sharpe:.2f}"))
    v.bars.append(("3 · CAGR ≥ equal-weight hold (full)", s.cagr >= b.cagr, f"{s.cagr:.2%} vs {b.cagr:.2%}"))
    v.bars.append(("4 · Max drawdown ≤ equal-weight hold's (full)", s.max_dd <= b.max_dd,
                   f"{s.max_dd:.1%} vs {b.max_dd:.1%}"))
    sc, bc = stats(main, *conf), stats(ew, *conf)
    v.bars.append(("5 · Confirmation: Sharpe ≥ and CAGR ≥ equal-weight hold",
                   sc.sharpe >= bc.sharpe and sc.cagr >= bc.cagr,
                   f"Sharpe {sc.sharpe:.2f} vs {bc.sharpe:.2f}; CAGR {sc.cagr:.2%} vs {bc.cagr:.2%}"))
    parts, ok6 = [], True
    for n, r in neighbours.items():
        a = stats(r, *full)
        ok = a.sharpe >= b.sharpe and a.cagr >= b.cagr
        ok6 &= ok
        parts.append(f"{n} mo: Sharpe {a.sharpe:.2f}, CAGR {a.cagr:.2%} ({'pass' if ok else 'fail'})")
    v.bars.append(("6 · 11 and 13 months pass bars 2 and 3", ok6, "; ".join(parts)))
    a, c = stats(stress[0], *full), stats(stress[1], *full)
    v.bars.append(("7 · At 0.25% per side, Sharpe ≥ equal-weight hold", a.sharpe >= c.sharpe,
                   f"{a.sharpe:.2f} vs {c.sharpe:.2f}"))
    p = stats(spy, *full)
    v.bars.append(("8 · vs SPY (full): Sharpe ≥ and drawdown ≤ SPY's",
                   s.sharpe >= p.sharpe and s.max_dd <= p.max_dd,
                   f"Sharpe {s.sharpe:.2f} vs {p.sharpe:.2f}; drawdown {s.max_dd:.1%} vs {p.max_dd:.1%}"))
    pc = stats(spy, *conf)
    v.bars.append(("9 · vs SPY (confirmation): Sharpe ≥ 0.9 × SPY's", sc.sharpe >= SPY_CONF_SHARPE_RATIO * pc.sharpe,
                   f"{sc.sharpe:.2f} vs limit {SPY_CONF_SHARPE_RATIO * pc.sharpe:.2f} (SPY {pc.sharpe:.2f})"))
    return v


# --- Report --------------------------------------------------------------------------------------

def _chart(series: list[tuple[Result, str, str]], start: date, path: Path) -> None:
    import math

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 7), sharex=True, gridspec_kw={"height_ratios": [2, 1]})
    for res, label, color in series:
        ds = sorted(res.equity)
        vals = [res.equity[d] for d in ds]
        ax1.plot(ds, vals, label=label, color=color, lw=1.4)
        peak, dds = -math.inf, []
        for x in vals:
            peak = max(peak, x)
            dds.append(-(1 - x / peak) * 100)
        ax2.plot(ds, dds, color=color, lw=1.0)
    ax1.set_yscale("log")
    ax1.set_ylabel("Account value ($, log scale)")
    ax1.legend(loc="upper left")
    ax1.set_title(f"Top 2 of 5 by 12-month return vs holding, $100k from {start:%b %Y} (0.05% per side)")
    ax2.set_ylabel("Drawdown (%)")
    for ax in (ax1, ax2):
        ax.axvline(CONFIRMATION_START, color="#bbbbbb", ls="--", lw=0.8)
        ax.grid(alpha=0.25)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=110)
    plt.close(fig)


def _write_log(res: Result, path: Path) -> None:
    syms = list(res.months[0].close)
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["month_end", *[f"{s}_12m_return" for s in syms], *[f"{s}_rank" for s in syms], "held_next",
                    "rebalance_day"])
        for m in res.months:
            order = rank(m.average, syms)
            w.writerow([m.month_end, *[f"{m.average[s]:+.4f}" for s in syms],
                        *[order.index(s) + 1 for s in syms],
                        " + ".join(s for s in syms if m.on[s]), m.rebalance_day or ""])


def _holding_counts(res: Result) -> dict[str, int]:
    held = [m.held_after for m in res.months if m.held_after is not None]
    return {s: sum(s in h for h in held) for s in res.months[0].close}


def run(history: sqlite3.Connection | None, out_dir: Path, today: date, cache_dir: Path) -> tuple[Path, bool]:
    end_before = first_of_month(today)
    allp = {s: mt.load_symbol(cache_dir, s, allow_splits=True) for s in (*ASSETS, ma.GOLD)}
    five = {s: allp[s] for s in ASSETS}
    gold_swap = {s: allp[s] for s in ("SPY", "EFA", "IEF", "VNQ", ma.GOLD)}
    six = dict(allp)
    days = ma.common_days(five, end_before)
    start = ma.common_start(days, end_before, WARMUP_MONTHS)
    last = [d for d in days if d >= start][-1]
    full, disc, conf = (start, last), (start, DISCOVERY_END), (CONFIRMATION_START, last)

    main = simulate(five, LOOKBACK, COST, end_before, start)
    ew = simulate(five, None, COST, end_before, start)
    spy = mt.simulate(allp["SPY"], None, COST, end_before, start)
    neighbours = {n: simulate(five, n, COST, end_before, start) for n in NEIGHBOURS}
    info = {n: simulate(five, n, COST, end_before, start) for n in INFO_LOOKBACKS}
    tops = {k: simulate(five, LOOKBACK, COST, end_before, start, top=k) for k in INFO_TOPS}
    by_cost = {c: (simulate(five, LOOKBACK, c, end_before, start), simulate(five, None, c, end_before, start))
               for c in COST_LEVELS}
    verdict = evaluate(main, ew, spy, neighbours, by_cost[STRESS_COST], days, full, conf)
    gold = {name: (simulate(d, LOOKBACK, COST, end_before, start), simulate(d, None, COST, end_before, start))
            for name, d in (("GLD instead of DBC", gold_swap), ("All six (with GLD)", six))}

    img = out_dir / "img" / "relative_momentum_equity.png"
    _chart([(spy, "SPY buy-and-hold", "#b0b0b0"), (ew, "5 assets, equal weight", "#d08a2c"),
            (main, "Top 2 of 5, 12-month return", "#1f6fb2")], start, img)
    log_csv = out_dir / "relative_momentum_log.csv"
    _write_log(main, log_csv)
    changed, rebalances = turnover(main)

    L = [f"# Relative Momentum (Top 2 of 5 ETFs, 12-Month Return) Backtest — {today.isoformat()}", "",
         "*Paper research only; not investment advice. Rules: "
         "[relative_momentum_preregistration.md](relative_momentum_preregistration.md), committed before this ran.*", "",
         f"## Verdict: **{'GO' if verdict.passed else 'NO-GO'}** "
         f"({sum(ok for _, ok, _ in verdict.bars)} of {len(verdict.bars)} bars passed)", "",
         "| Bar | Result | Detail |", "|---|---|---|"]
    L += [f"| {name} | {'PASS' if ok else '**FAIL**'} | {detail} |" for name, ok, detail in verdict.bars]
    L += ["", "## Results at 0.05% per side", ""]
    for label, (a, b) in (("Full period", full), ("Discovery", disc), ("Confirmation", conf)):
        L += [f"**{label}: {a.isoformat()} to {b.isoformat()}**", "", mt.HEADER,
              mt._row("Top 2 of 5, 12-month return", stats(main, a, b)),
              mt._row("5 assets, equal weight", stats(ew, a, b)),
              mt._row("SPY buy-and-hold", stats(spy, a, b)), ""]
    L += ["*Trades counts every buy or sell of one ETF, including small monthly rebalancing trades.*", "",
          f"![Account value and drawdown](img/{img.name})", "", "## Turnover and concentration", "",
          f"- Holdings changed at {changed} of {rebalances} monthly reviews ({changed / rebalances:.0%}).",
          f"- Months holding both stock funds (SPY + EFA) together: {stock_share(main):.0%}.",
          "- Months each fund was held: " + ", ".join(f"{NAMES[s]} ({s}) {n}" for s, n in
                                                      _holding_counts(main).items()) + ".",
          "", "## Lookbacks (full period)", "", mt.HEADER]
    for n in sorted([LOOKBACK, *NEIGHBOURS, *INFO_LOOKBACKS]):
        r = main if n == LOOKBACK else (neighbours[n] if n in neighbours else info[n])
        tag = " (main)" if n == LOOKBACK else (" (bar 6)" if n in NEIGHBOURS else " (info only)")
        L.append(mt._row(f"{n} months{tag}", stats(r, *full)))
    L.append(mt._row("5 assets, equal weight", stats(ew, *full)))
    L += ["", "## Number of funds held (12-month lookback, full period, information only)", "", mt.HEADER]
    for k in sorted([TOP, *INFO_TOPS]):
        r = main if k == TOP else tops[k]
        L.append(mt._row(f"Top {k}{' (main)' if k == TOP else ''}", stats(r, *full)))
    L += ["", "## Trading costs (full period)", "", "| Cost per side | Rule CAGR | Rule Sharpe | Hold CAGR | "
          "Hold Sharpe | Total cost paid by rule |", "|---|---|---|---|---|---|"]
    for c, (r, rb) in by_cost.items():
        a, b = stats(r, *full), stats(rb, *full)
        L.append(f"| {c:.2%} | {a.cagr:.2%} | {a.sharpe:.2f} | {b.cagr:.2%} | {b.sharpe:.2f} | "
                 f"${sum(t.cost for t in r.trades):,.0f} |")
    L += ["", "## Gold versions (full period, information only)", "", mt.HEADER]
    for name, (r, rb) in gold.items():
        L += [mt._row(f"{name}: top 2", stats(r, *full)), mt._row(f"{name}: equal weight", stats(rb, *full))]
    L += ["", "## Data notes", "",
          f"- Common trading days: {len(days):,} ({days[0]} to {days[-1]}); the test runs {start} to {last}. "
          f"Last complete month used: {last:%B %Y}.",
          f"- Monthly log (each fund's 12-month total return and rank, the two held, rebalance day): "
          f"[{log_csv.name}]({log_csv.name}).",
          "- Scores use a total-return index (dividends added back on their ex-dates).",
          "- Same cached Yahoo prices and FRED DTB3 cash rates as the trend tests; data checks are in "
          "[the trend report](multi_asset_trend_backtest_2026-10-20.md).",
          "- Taxes are ignored. Monthly rotation would make them worse for the rule than for SPY held."]
    path = out_dir / f"relative_momentum_backtest_{today.isoformat()}.md"
    path.write_text("\n".join(L) + "\n")
    return path, verdict.passed
