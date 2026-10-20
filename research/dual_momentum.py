"""Dual momentum: top 2 of 5 by 12-month total return, each held only if above its 10-month average, as pre-registered.

Rules: docs/research/dual_momentum_preregistration.md.
  * Rank exactly as research/relative_momentum.py; trend test exactly as research/multi_asset_trend.py.
  * A top-two fund that fails the trend test leaves its 50% in T-bills (it is not replaced by the third fund).
  * Main comparison: relative momentum without the cash test. Also SPY buy-and-hold and the equal-weight basket.
"""

from __future__ import annotations

import csv
import sqlite3
from datetime import date
from pathlib import Path
from statistics import mean

from research import monthly_trend as mt
from research import multi_asset_trend as ma
from research import relative_momentum as rm
from research.monthly_trend import MarketData, Result, first_of_month, month_ends, rate_lookup, signals, stats

ASSETS = rm.ASSETS
NAMES = rm.NAMES
RANK_LOOKBACK = 12
TREND = 10
NEIGHBOURS = (9, 11)
INFO_TRENDS = (8, 12)
TOP = 2
COST = rm.COST
COST_LEVELS = rm.COST_LEVELS
STRESS_COST = rm.STRESS_COST
CONFIRMATION_START = rm.CONFIRMATION_START
DISCOVERY_END = rm.DISCOVERY_END
DD_RATIO_BAR = 0.8
CAGR_GAP_BAR = 0.015
SPY_CONF_SHARPE_RATIO = 0.9


def cash_growth(datas: dict[str, MarketData], end_before: date) -> dict[date, float]:
    """T-bill index on the common trading days, compounded daily as in the simulator."""
    days = ma.common_days(datas, end_before)
    rate = rate_lookup(next(iter(datas.values())).rates)
    out, idx = {}, 1.0
    for i, d in enumerate(days):
        if i:
            idx *= (1 + rate(d)) ** (1 / 252)
        out[d] = idx
    return out


def dual_rule(datas: dict[str, MarketData], trend: int | None, end_before: date, tbill: bool = False) -> ma.Rule:
    """trend = months for the 10-month-average test; tbill=True uses Antonacci's test instead (info only)."""
    syms = list(datas)
    mom = rm.momentum_rule(datas, RANK_LOOKBACK, TOP, end_before)
    ends = month_ends(ma.common_days(datas, end_before), end_before)
    pos = {e: i for i, e in enumerate(ends)}
    cls = {s: {b.day: b.close for b in datas[s].bars} for s in syms}
    sig = {s: signals(ends, cls[s], trend) for s in syms} if not tbill else None
    cash = cash_growth(datas, end_before) if tbill else None

    def passes(s: str, me: date, score: float) -> bool | None:
        if tbill:
            then = ends[pos[me] - RANK_LOOKBACK]
            return score > cash[me] / cash[then] - 1
        return sig[s][me][1] if me in sig[s] else None

    def rule(me: date):
        r = mom(me)
        if r is None:
            return None
        scores, picked, _ = r
        ok = {s: passes(s, me, scores[s]) for s in syms}
        if any(v is None for v in ok.values()):
            return None
        held = {s: picked[s] and ok[s] for s in syms}
        return scores, held, {s: (1 / TOP if held[s] else 0.0) for s in syms}
    return rule


def simulate(datas: dict[str, MarketData], trend: int | None, cost: float, end_before: date, start: date,
             tbill: bool = False) -> Result:
    return ma.simulate(datas, trend, cost, end_before, start, rule=dual_rule(datas, trend, end_before, tbill))


def expected_holdings(datas: dict[str, MarketData], end_before: date, trend: int = TREND) -> dict[date, frozenset]:
    """Bar 1's independent recomputation, straight from the bars: top two by 12-month total return, then each one
    kept only if its close is above the mean of its last `trend` month-end closes."""
    syms = list(datas)
    days = sorted(set.intersection(*[{b.day for b in datas[s].bars if b.day < end_before} for s in syms]))
    last_in_month: dict[tuple[int, int], date] = {}
    for d in days:
        last_in_month[(d.year, d.month)] = d
    ends = sorted(last_in_month.values())
    tr, close = {}, {s: {b.day: b.close for b in datas[s].bars} for s in syms}
    for s in syms:
        idx, prev, tr[s] = 1.0, None, {}
        for b in datas[s].bars:
            if prev is not None:
                idx *= (b.close + datas[s].dividends.get(b.day, 0.0)) / prev
            tr[s][b.day], prev = idx, b.close
    out = {}
    for i in range(max(RANK_LOOKBACK, trend - 1), len(ends)):
        me, then = ends[i], ends[i - RANK_LOOKBACK]
        score = {s: tr[s][me] / tr[s][then] - 1 for s in syms}
        top = sorted(syms, key=lambda s: (-score[s], syms.index(s)))[:TOP]
        out[me] = frozenset(s for s in top if close[s][me] > mean(close[s][e] for e in ends[i + 1 - trend:i + 1]))
    return out


def log_check(res: Result, days: list[date], expected: dict[date, frozenset]) -> list[str]:
    problems = ma.log_check(res, days)
    for m in res.months:
        if m.held_after is not None and m.held_after != expected.get(m.month_end):
            problems.append(f"{m.month_end}: held {sorted(m.held_after)}, expected {sorted(expected.get(m.month_end, []))}")
    return problems


def blocked(res: Result, datas_order: list[str]) -> dict[str, int]:
    """Months each fund was in the top two but held out by the trend test."""
    out = {s: 0 for s in datas_order}
    for m in res.months:
        if m.held_after is None:
            continue
        for s in rm.rank(m.average, datas_order)[:TOP]:
            if s not in m.held_after:
                out[s] += 1
    return out


# --- Bars ----------------------------------------------------------------------------------------

def evaluate(main: Result, rel: Result, spy: Result, neighbours: dict[int, Result], stress: tuple[Result, Result],
             days: list[date], expected: dict[date, frozenset], full: tuple[date, date],
             conf: tuple[date, date]) -> mt.Verdict:
    v = mt.Verdict()
    problems = log_check(main, days, expected)
    v.bars.append(("1 · Log check", not problems, f"{len(main.trades)} trades over {len(main.months)} month-ends, "
                   f"{len(problems)} problems" + (f": {problems[:3]}" if problems else "")))
    s, r = stats(main, *full), stats(rel, *full)
    v.bars.append(("2 · Sharpe ≥ relative momentum's (full)", s.sharpe >= r.sharpe, f"{s.sharpe:.2f} vs {r.sharpe:.2f}"))
    v.bars.append(("3 · Max drawdown ≤ 0.8 × relative momentum's (full)", s.max_dd <= DD_RATIO_BAR * r.max_dd,
                   f"{s.max_dd:.1%} vs limit {DD_RATIO_BAR * r.max_dd:.1%} (relative momentum {r.max_dd:.1%})"))
    v.bars.append(("4 · CAGR ≥ relative momentum's − 1.5 pp (full)", s.cagr >= r.cagr - CAGR_GAP_BAR,
                   f"{s.cagr:.2%} vs {r.cagr:.2%}"))
    sc, rc = stats(main, *conf), stats(rel, *conf)
    v.bars.append(("5 · Confirmation: Sharpe ≥ relative momentum's", sc.sharpe >= rc.sharpe,
                   f"{sc.sharpe:.2f} vs {rc.sharpe:.2f}"))
    parts, ok6 = [], True
    for n, res in neighbours.items():
        a = stats(res, *full)
        ok = a.sharpe >= r.sharpe and a.max_dd <= DD_RATIO_BAR * r.max_dd
        ok6 &= ok
        parts.append(f"{n} mo: Sharpe {a.sharpe:.2f}, drawdown {a.max_dd:.1%} ({'pass' if ok else 'fail'})")
    v.bars.append(("6 · Trend lookbacks 9 and 11 pass bars 2 and 3", ok6, "; ".join(parts)))
    a, c = stats(stress[0], *full), stats(stress[1], *full)
    v.bars.append(("7 · At 0.25% per side, Sharpe ≥ relative momentum's", a.sharpe >= c.sharpe,
                   f"{a.sharpe:.2f} vs {c.sharpe:.2f}"))
    p = stats(spy, *full)
    v.bars.append(("8 · vs SPY (full): Sharpe ≥ and drawdown ≤ SPY's", s.sharpe >= p.sharpe and s.max_dd <= p.max_dd,
                   f"Sharpe {s.sharpe:.2f} vs {p.sharpe:.2f}; drawdown {s.max_dd:.1%} vs {p.max_dd:.1%}"))
    pc = stats(spy, *conf)
    v.bars.append(("9 · vs SPY (confirmation): Sharpe ≥ 0.9 × SPY's", sc.sharpe >= SPY_CONF_SHARPE_RATIO * pc.sharpe,
                   f"{sc.sharpe:.2f} vs limit {SPY_CONF_SHARPE_RATIO * pc.sharpe:.2f} (SPY {pc.sharpe:.2f})"))
    return v


# --- Report --------------------------------------------------------------------------------------

def _write_log(res: Result, datas: dict[str, MarketData], end_before: date, path: Path) -> None:
    syms = list(datas)
    ends = month_ends(ma.common_days(datas, end_before), end_before)
    cls = {s: {b.day: b.close for b in datas[s].bars} for s in syms}
    sig = {s: signals(ends, cls[s], TREND) for s in syms}
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["month_end", *[f"{s}_12m_return" for s in syms], *[f"{s}_rank" for s in syms],
                    *[f"{s}_trend" for s in syms], "held_next", "cash_share", "rebalance_day"])
        for m in res.months:
            order = rm.rank(m.average, syms)
            held = [s for s in syms if m.on[s]]
            w.writerow([m.month_end, *[f"{m.average[s]:+.4f}" for s in syms], *[order.index(s) + 1 for s in syms],
                        *["on" if sig[s][m.month_end][1] else "off" for s in syms],
                        " + ".join(held) or "cash", f"{1 - len(held) / TOP:.0%}", m.rebalance_day or ""])


def run(history: sqlite3.Connection | None, out_dir: Path, today: date, cache_dir: Path) -> tuple[Path, bool]:
    end_before = first_of_month(today)
    allp = {s: mt.load_symbol(cache_dir, s, allow_splits=True) for s in (*ASSETS, ma.GOLD)}
    five = {s: allp[s] for s in ASSETS}
    gold_swap = {s: allp[s] for s in ("SPY", "EFA", "IEF", "VNQ", ma.GOLD)}
    days = ma.common_days(five, end_before)
    start = ma.common_start(days, end_before, rm.WARMUP_MONTHS)        # same start as relative momentum
    last = [d for d in days if d >= start][-1]
    full, disc, conf = (start, last), (start, DISCOVERY_END), (CONFIRMATION_START, last)

    main = simulate(five, TREND, COST, end_before, start)
    rel = rm.simulate(five, RANK_LOOKBACK, COST, end_before, start)
    ew = rm.simulate(five, None, COST, end_before, start)
    spy = mt.simulate(allp["SPY"], None, COST, end_before, start)
    neighbours = {n: simulate(five, n, COST, end_before, start) for n in NEIGHBOURS}
    info = {n: simulate(five, n, COST, end_before, start) for n in INFO_TRENDS}
    antonacci = simulate(five, None, COST, end_before, start, tbill=True)
    by_cost = {c: (simulate(five, TREND, c, end_before, start), rm.simulate(five, RANK_LOOKBACK, c, end_before, start))
               for c in COST_LEVELS}
    expected = expected_holdings(five, end_before)
    verdict = evaluate(main, rel, spy, neighbours, by_cost[STRESS_COST], days, expected, full, conf)
    gold = (simulate(gold_swap, TREND, COST, end_before, start),
            rm.simulate(gold_swap, RANK_LOOKBACK, COST, end_before, start))

    img = out_dir / "img" / "dual_momentum_equity.png"
    rm._chart([(spy, "SPY buy-and-hold", "#b0b0b0"), (rel, "Relative momentum (top 2)", "#d08a2c"),
               (main, "Dual momentum (top 2 + trend-to-cash)", "#1f6fb2")], start, img,
              title=f"Dual vs relative momentum vs SPY, $100k from {start:%b %Y} (0.05% per side)")
    log_csv = out_dir / "dual_momentum_log.csv"
    _write_log(main, five, end_before, log_csv)
    changed, rebalances = rm.turnover(main)
    held = [m.held_after for m in main.months if m.held_after is not None]

    L = [f"# Dual Momentum (Top 2 of 5 + 10-Month Trend-to-Cash) Backtest — {today.isoformat()}", "",
         "*Paper research only; not investment advice. Rules: "
         "[dual_momentum_preregistration.md](dual_momentum_preregistration.md), committed before this ran.*", "",
         f"## Verdict: **{'GO' if verdict.passed else 'NO-GO'}** "
         f"({sum(ok for _, ok, _ in verdict.bars)} of {len(verdict.bars)} bars passed)", "",
         "| Bar | Result | Detail |", "|---|---|---|"]
    L += [f"| {name} | {'PASS' if ok else '**FAIL**'} | {detail} |" for name, ok, detail in verdict.bars]
    L += ["", "## Results at 0.05% per side", ""]
    for label, (a, b) in (("Full period", full), ("Discovery", disc), ("Confirmation", conf)):
        L += [f"**{label}: {a.isoformat()} to {b.isoformat()}**", "", mt.HEADER,
              mt._row("Dual momentum", stats(main, a, b)), mt._row("Relative momentum (top 2)", stats(rel, a, b)),
              mt._row("5 assets, equal weight", stats(ew, a, b)), mt._row("SPY buy-and-hold", stats(spy, a, b)), ""]
    L += ["*Time invested is the average share of the account in ETFs; the rest is in T-bills.*", "",
          f"![Account value and drawdown](img/{img.name})", "", "## How often the cash rule acted", "",
          f"- Fully invested {sum(len(h) == 2 for h in held) / len(held):.0%} of months, half in cash "
          f"{sum(len(h) == 1 for h in held) / len(held):.0%}, all cash {sum(len(h) == 0 for h in held) / len(held):.0%}.",
          f"- Holdings changed at {changed} of {rebalances} monthly reviews ({changed / rebalances:.0%}).",
          "- Months a top-two fund was held out by the trend test: " + ", ".join(
              f"{NAMES[s]} ({s}) {n}" for s, n in blocked(main, list(five)).items()) + ".",
          "", "## Trend lookbacks (full period)", "", mt.HEADER]
    for n in sorted([TREND, *NEIGHBOURS, *INFO_TRENDS]):
        r = main if n == TREND else (neighbours[n] if n in neighbours else info[n])
        tag = " (main)" if n == TREND else (" (bar 6)" if n in NEIGHBOURS else " (info only)")
        L.append(mt._row(f"{n} months{tag}", stats(r, *full)))
    L += [mt._row("Relative momentum (no cash rule)", stats(rel, *full)), "",
          "## Antonacci's absolute test instead (full period, information only)", "",
          "A top-two fund is held only if its 12-month total return beats T-bills over the same 12 months.", "",
          mt.HEADER, mt._row("Dual momentum, T-bill test", stats(antonacci, *full)),
          mt._row("Dual momentum, 10-month average (main)", stats(main, *full)), "",
          "## Trading costs (full period)", "", "| Cost per side | Dual CAGR | Dual Sharpe | Relative CAGR | "
          "Relative Sharpe | Total cost paid by dual |", "|---|---|---|---|---|---|"]
    for c, (r, rb) in by_cost.items():
        a, b = stats(r, *full), stats(rb, *full)
        L.append(f"| {c:.2%} | {a.cagr:.2%} | {a.sharpe:.2f} | {b.cagr:.2%} | {b.sharpe:.2f} | "
                 f"${sum(t.cost for t in r.trades):,.0f} |")
    L += ["", "## Gold instead of commodities (full period, information only)", "", mt.HEADER,
          mt._row("GLD basket: dual momentum", stats(gold[0], *full)),
          mt._row("GLD basket: relative momentum", stats(gold[1], *full)), "", "## Data notes", "",
          f"- Same data and start as the relative momentum test: {start} to {last}. "
          f"Last complete month used: {last:%B %Y}.",
          f"- Monthly log (each fund's score, rank and trend on/off, what was held, cash share): "
          f"[{log_csv.name}]({log_csv.name}).",
          "- Cash earns FRED DTB3 (3-month T-bill), using the rate published before each day.",
          "- Taxes are ignored. Monthly rotation and selling would make them worse for the rule than for SPY held."]
    path = out_dir / f"dual_momentum_backtest_{today.isoformat()}.md"
    path.write_text("\n".join(L) + "\n")
    return path, verdict.passed
