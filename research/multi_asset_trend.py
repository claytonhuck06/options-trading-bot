"""Multi-asset monthly trend (Faber's 5 asset classes): backtest, exactly as pre-registered.

Rules: docs/research/multi_asset_trend_preregistration.md.
  * Each asset is "on" when its month-end close is above its 10-month average; on assets get 1/5 each, the rest is
    cash at T-bill rates. Rebalanced every month at the next open, costs on every trade.
  * Benchmarks: the same five always on (equal-weight hold, rebalanced monthly) and SPY buy-and-hold.
  * Data, signal and statistics come from research/monthly_trend.py (the SPY test).
"""

from __future__ import annotations

import csv
import math
import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Callable

from research import monthly_trend as mt
from research.monthly_trend import MarketData, Result, Stats, first_of_month, month_ends, rate_lookup, signals, stats

ASSETS = ("SPY", "EFA", "IEF", "VNQ", "DBC")
GOLD = "GLD"
NAMES = {"SPY": "US stocks", "EFA": "Foreign stocks", "IEF": "Treasury bonds", "VNQ": "Real estate",
         "DBC": "Commodities", "GLD": "Gold"}
LOOKBACK = 10
NEIGHBOURS = (9, 11)
INFO_LOOKBACKS = (8, 12)
WARMUP_MONTHS = 12
COST = 0.0005
COST_LEVELS = (0.0, 0.0005, 0.0010, 0.0025)
STRESS_COST = 0.0025
CONFIRMATION_START = date(2013, 1, 1)
DISCOVERY_END = date(2012, 12, 31)
DD_RATIO_BAR = 0.6
CAGR_GAP_BAR = 0.02
BIG_MOVE = 0.25


@dataclass(frozen=True)
class PTrade:
    day: date
    symbol: str
    side: str
    price: float
    value: float                  # dollars traded, before cost
    cost: float
    signal_month_end: date


@dataclass(frozen=True)
class PMonth:
    month_end: date
    close: dict[str, float]
    average: dict[str, float | None]
    on: dict[str, bool]
    rebalance_day: date | None
    held_after: frozenset[str] | None


def common_days(datas: dict[str, MarketData], end_before: date) -> list[date]:
    sets = [{b.day for b in d.bars if b.day < end_before} for d in datas.values()]
    return sorted(set.intersection(*sets))


def common_start(days: list[date], end_before: date, warmup: int = WARMUP_MONTHS) -> date:
    """First trading day after the month-end on which every asset has `warmup` month-end closes."""
    ends = month_ends(days, end_before)
    return days[days.index(ends[warmup - 1]) + 1]


# A rule maps a month-end to (score per asset, chosen per asset, target weight per asset), or None if it
# can't be computed yet. The default is the 10-month trend rule; research/relative_momentum.py supplies its own.
Rule = Callable[[date], tuple[dict[str, float | None], dict[str, bool], dict[str, float]] | None]


def simulate(datas: dict[str, MarketData], n: int | None, cost: float, end_before: date,
             start: date | None = None, rule: Rule | None = None) -> Result:
    syms = list(datas)
    days = common_days(datas, end_before)
    start = start or common_start(days, end_before)
    opn = {s: {b.day: b.open for b in datas[s].bars} for s in syms}
    cls = {s: {b.day: b.close for b in datas[s].bars} for s in syms}
    ends = month_ends(days, end_before)
    sig = {s: signals(ends, cls[s], n) for s in syms}
    rate = rate_lookup(next(iter(datas.values())).rates)
    end_set = set(ends)

    def trend_rule(me: date):
        if any(me not in sig[s] for s in syms):
            return None
        return ({s: sig[s][me][0] for s in syms}, {s: sig[s][me][1] for s in syms},
                {s: (1 / len(syms) if sig[s][me][1] else 0.0) for s in syms})

    rule = rule or trend_rule
    prior = [e for e in ends if e < start]
    if not prior or rule(prior[-1]) is None:
        raise ValueError(f"not enough history before {start} for a {n}-month signal")
    pending, pending_from = rule(prior[-1])[2], prior[-1]
    shares = {s: 0.0 for s in syms}
    cash, cash_idx, first = mt.EQUITY, 1.0, True
    res = Result({}, {}, [], [], {}, n, cost)
    month_row: PMonth | None = None

    for d in days:
        if d < start:
            continue
        if not first:
            growth = (1 + rate(d)) ** (1 / 252)
            cash *= growth
            cash_idx *= growth
        first = False
        div_cash = {s: shares[s] * datas[s].dividends.get(d, 0.0) for s in syms}
        if pending is not None:
            cash += sum(div_cash.values())
            div_cash = {s: 0.0 for s in syms}
            eq_open = cash + sum(shares[s] * opn[s][d] for s in syms)
            est = 0.0
            for _ in range(2):                                   # costs come out before sizing the targets
                tv = {s: pending[s] * (eq_open - est) for s in syms}
                est = sum(abs(tv[s] - shares[s] * opn[s][d]) * cost for s in syms)
            for s in syms:
                delta = tv[s] - shares[s] * opn[s][d]
                if abs(delta) > 1e-6 * eq_open:
                    res.trades.append(PTrade(d, s, "buy" if delta > 0 else "sell", opn[s][d], abs(delta),
                                             abs(delta) * cost, pending_from))
                shares[s] = tv[s] / opn[s][d] if tv[s] > 0 else 0.0
            cash = eq_open - sum(tv.values()) - est
            held = frozenset(s for s in syms if shares[s] > 0)
            if month_row is not None:
                res.months.append(PMonth(**{**month_row.__dict__, "rebalance_day": d, "held_after": held}))
                month_row = None
            elif not res.months:
                score, on, _ = rule(pending_from)
                res.months.append(PMonth(pending_from, {s: cls[s][pending_from] for s in syms}, score, on, d, held))
            pending = None
        for s in syms:
            if div_cash[s]:
                shares[s] += div_cash[s] / cls[s][d]
        invested = sum(shares[s] * cls[s][d] for s in syms)
        res.equity[d] = invested + cash
        res.invested[d] = invested / res.equity[d]
        res.cash_index[d] = cash_idx

        if d in end_set:
            score, on, weights = rule(d)
            month_row = PMonth(d, {s: cls[s][d] for s in syms}, score, on, None, None)
            pending, pending_from = weights, d
    if month_row is not None:
        res.months.append(month_row)
    return res


def log_check(res: Result, days: list[date]) -> list[str]:
    """Bar 1: trades only on the day after a month-end; holdings after each rebalance = the assets that were on."""
    problems = []
    nxt = {d: days[i + 1] for i, d in enumerate(days[:-1])}
    for t in res.trades:
        if nxt.get(t.signal_month_end) != t.day:
            problems.append(f"{t.day} {t.symbol}: not the first trading day after {t.signal_month_end}")
    for m in res.months:
        if m.rebalance_day is None:
            if m.month_end in nxt and nxt[m.month_end] <= max(res.equity):
                problems.append(f"{m.month_end}: no rebalance")
            continue
        if m.month_end >= min(res.equity) and m.rebalance_day != nxt.get(m.month_end):
            problems.append(f"{m.month_end}: rebalanced on {m.rebalance_day}")
        on = frozenset(s for s, v in m.on.items() if v)
        if m.held_after != on:
            problems.append(f"{m.month_end}: held {sorted(m.held_after)} but on {sorted(on)}")
    return problems


# --- Data checks ---------------------------------------------------------------------------------

def data_checks(datas: dict[str, MarketData], days: list[date], end_before: date) -> dict[str, dict]:
    out = {}
    first = days[0]
    common = set(days)
    for s, d in datas.items():
        bars = [b for b in d.bars if first <= b.day < end_before]
        moves = [(b.day, b.close / a.close - 1) for a, b in zip(bars, bars[1:])]
        big = max(moves, key=lambda t: abs(t[1]))
        out[s] = {"first": d.bars[0].day, "dropped": sum(b.day not in common for b in bars), "biggest": big,
                  "divs": sum(1 for x in d.dividends if first <= x < end_before)}
    return out


# --- Bars ----------------------------------------------------------------------------------------

def evaluate(main: Result, ew: Result, spy: Result, neighbours: dict[int, tuple[Result, Result]],
             stress: tuple[Result, Result], days: list[date], full: tuple[date, date],
             conf: tuple[date, date]) -> mt.Verdict:
    v = mt.Verdict()
    problems = log_check(main, days)
    v.bars.append(("1 · Log check", not problems, f"{len(main.trades)} trades over {len(main.months)} month-ends, "
                   f"{len(problems)} problems" + (f": {problems[:3]}" if problems else "")))
    s, b = stats(main, *full), stats(ew, *full)
    v.bars.append(("2 · Sharpe ≥ equal-weight hold (full)", s.sharpe >= b.sharpe, f"{s.sharpe:.2f} vs {b.sharpe:.2f}"))
    v.bars.append(("3 · Max drawdown ≤ 0.6 × equal-weight hold (full)", s.max_dd <= DD_RATIO_BAR * b.max_dd,
                   f"{s.max_dd:.1%} vs limit {DD_RATIO_BAR * b.max_dd:.1%} (hold {b.max_dd:.1%})"))
    v.bars.append(("4 · CAGR ≥ equal-weight hold − 2 pp (full)", s.cagr >= b.cagr - CAGR_GAP_BAR,
                   f"{s.cagr:.2%} vs {b.cagr:.2%}"))
    sc, bc = stats(main, *conf), stats(ew, *conf)
    v.bars.append(("5 · Confirmation: Sharpe ≥ and drawdown < equal-weight hold",
                   sc.sharpe >= bc.sharpe and sc.max_dd < bc.max_dd,
                   f"Sharpe {sc.sharpe:.2f} vs {bc.sharpe:.2f}; drawdown {sc.max_dd:.1%} vs {bc.max_dd:.1%}"))
    parts, ok6 = [], True
    for n, (r, rb) in neighbours.items():
        a, c = stats(r, *full), stats(rb, *full)
        ok = a.sharpe >= c.sharpe and a.max_dd <= DD_RATIO_BAR * c.max_dd
        ok6 &= ok
        parts.append(f"{n} mo: Sharpe {a.sharpe:.2f}, drawdown {a.max_dd:.1%} ({'pass' if ok else 'fail'})")
    v.bars.append(("6 · 9 and 11 months pass bars 2 and 3", ok6, "; ".join(parts)))
    a, c = stats(stress[0], *full), stats(stress[1], *full)
    v.bars.append(("7 · At 0.25% per side, Sharpe ≥ equal-weight hold", a.sharpe >= c.sharpe,
                   f"{a.sharpe:.2f} vs {c.sharpe:.2f}"))
    p = stats(spy, *full)
    v.bars.append(("8 · vs SPY: Sharpe ≥ and drawdown ≤ 0.6 × SPY's (full)",
                   s.sharpe >= p.sharpe and s.max_dd <= DD_RATIO_BAR * p.max_dd,
                   f"Sharpe {s.sharpe:.2f} vs {p.sharpe:.2f}; drawdown {s.max_dd:.1%} vs limit "
                   f"{DD_RATIO_BAR * p.max_dd:.1%} (SPY {p.max_dd:.1%})"))
    return v


# --- Report --------------------------------------------------------------------------------------

def _chart(series: list[tuple[Result, str, str]], start: date, path: Path) -> None:
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
    ax1.set_title(f"5-asset 10-month trend rule vs holding, $100k from {start:%b %Y} (0.05% per side)")
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
        w.writerow(["month_end", *[f"{s}_{k}" for s in syms for k in ("close", "average_10m", "on")],
                    "assets_on", "rebalance_day"])
        for m in res.months:
            row = [m.month_end]
            for s in syms:
                row += [f"{m.close[s]:.2f}", f"{m.average[s]:.2f}" if m.average[s] is not None else "",
                        "on" if m.on[s] else "off"]
            w.writerow(row + [sum(m.on.values()), m.rebalance_day or ""])


def _row(name: str, s: Stats) -> str:
    return mt._row(name, s)


def run(history: sqlite3.Connection | None, out_dir: Path, today: date, cache_dir: Path) -> tuple[Path, bool]:
    end_before = first_of_month(today)
    allp = {s: mt.load_symbol(cache_dir, s, allow_splits=True) for s in (*ASSETS, GOLD)}
    five = {s: allp[s] for s in ASSETS}
    six = dict(allp)
    days = common_days(five, end_before)
    start = common_start(days, end_before)
    run_days = [d for d in days if d >= start]
    last = run_days[-1]
    full, disc, conf = (start, last), (start, DISCOVERY_END), (CONFIRMATION_START, last)

    main = simulate(five, LOOKBACK, COST, end_before, start)
    ew = simulate(five, None, COST, end_before, start)
    spy = mt.simulate(allp["SPY"], None, COST, end_before, start)
    neighbours = {n: (simulate(five, n, COST, end_before, start), ew) for n in NEIGHBOURS}
    info = {n: simulate(five, n, COST, end_before, start) for n in INFO_LOOKBACKS}
    by_cost = {c: (simulate(five, LOOKBACK, c, end_before, start), simulate(five, None, c, end_before, start))
               for c in COST_LEVELS}
    verdict = evaluate(main, ew, spy, neighbours, by_cost[STRESS_COST], days, full, conf)
    six_main = simulate(six, LOOKBACK, COST, end_before, start)
    six_ew = simulate(six, None, COST, end_before, start)
    checks = data_checks(five | {GOLD: allp[GOLD]}, days, end_before)

    img = out_dir / "img" / "multi_asset_trend_equity.png"
    _chart([(spy, "SPY buy-and-hold", "#b0b0b0"), (ew, "5 assets, always held", "#d08a2c"),
            (main, "5 assets, 10-month rule", "#1f6fb2")], start, img)
    log_csv = out_dir / "multi_asset_trend_log.csv"
    _write_log(main, log_csv)

    L = [f"# Multi-Asset Monthly Trend (Faber's 5 Asset Classes) Backtest — {today.isoformat()}", "",
         "*Paper research only; not investment advice. Rules: "
         "[multi_asset_trend_preregistration.md](multi_asset_trend_preregistration.md), committed before this ran.*", "",
         f"## Verdict: **{'GO' if verdict.passed else 'NO-GO'}** "
         f"({sum(ok for _, ok, _ in verdict.bars)} of {len(verdict.bars)} bars passed)", "",
         "| Bar | Result | Detail |", "|---|---|---|"]
    L += [f"| {name} | {'PASS' if ok else '**FAIL**'} | {detail} |" for name, ok, detail in verdict.bars]
    L += ["", "## Results at 0.05% per side", ""]
    for label, (a, b) in (("Full period", full), ("Discovery", disc), ("Confirmation", conf)):
        L += [f"**{label}: {a.isoformat()} to {b.isoformat()}**", "", mt.HEADER,
              _row("5 assets, 10-month rule", stats(main, a, b)), _row("5 assets, always held", stats(ew, a, b)),
              _row("SPY buy-and-hold", stats(spy, a, b)), ""]
    L += ["*Trades counts every buy or sell of one ETF, including small monthly rebalancing trades. "
          "Time invested is the average share of the account in ETFs.*", "",
          f"![Account value and drawdown](img/{img.name})", "", "## Lookbacks (full period)", "", mt.HEADER]
    for n in sorted([LOOKBACK, *NEIGHBOURS, *INFO_LOOKBACKS]):
        r = main if n == LOOKBACK else (neighbours[n][0] if n in neighbours else info[n])
        tag = " (main)" if n == LOOKBACK else (" (bar 6)" if n in NEIGHBOURS else " (info only)")
        L.append(_row(f"{n} months{tag}", stats(r, *full)))
    L.append(_row("5 assets, always held", stats(ew, *full)))
    L += ["", "## Trading costs (full period)", "", "| Cost per side | Rule CAGR | Rule Sharpe | Hold CAGR | "
          "Hold Sharpe | Total cost paid by rule |", "|---|---|---|---|---|---|"]
    for c, (r, rb) in by_cost.items():
        a, b = stats(r, *full), stats(rb, *full)
        L.append(f"| {c:.2%} | {a.cagr:.2%} | {a.sharpe:.2f} | {b.cagr:.2%} | {b.sharpe:.2f} | "
                 f"${sum(t.cost for t in r.trades):,.0f} |")

    L += ["", "## Each asset on its own (full period, information only)", "",
          "| Asset | Timed: CAGR | Timed: drawdown | Timed: Sharpe | Held: CAGR | Held: drawdown | Held: Sharpe |"
          " Time on |", "|---|---|---|---|---|---|---|---|"]
    for s in (*ASSETS, GOLD):
        a = stats(mt.simulate(allp[s], LOOKBACK, COST, end_before, start), *full)
        b = stats(mt.simulate(allp[s], None, COST, end_before, start), *full)
        L.append(f"| {NAMES[s]} ({s}) | {a.cagr:.2%} | {a.max_dd:.1%} | {a.sharpe:.2f} | {b.cagr:.2%} | "
                 f"{b.max_dd:.1%} | {b.sharpe:.2f} | {a.invested:.0%} |")
    L += ["", "## Six assets with gold (information only)", "", mt.HEADER,
          _row("6 assets, 10-month rule", stats(six_main, *full)), _row("6 assets, always held", stats(six_ew, *full)),
          "", "## Data checks", "",
          f"- Common trading days: {len(days):,} ({days[0]} to {days[-1]}); the test runs {start} to {last}. "
          f"Last complete month used: {last:%B %Y}.",
          f"- {problems_line(verdict)}", "", "| ETF | Data from | Days dropped (not common) | Biggest one-day move | "
          "Dividends in window |", "|---|---|---|---|---|"]
    for s, c in checks.items():
        flag = " **(over 25%)**" if abs(c["biggest"][1]) > BIG_MOVE else ""
        L.append(f"| {s} | {c['first']} | {c['dropped']} | {c['biggest'][1]:+.1%} on {c['biggest'][0]}{flag} | "
                 f"{c['divs']} |")
    L += ["", f"- Monthly log (each asset's close, 10-month average, on/off, rebalance day): [{log_csv.name}]({log_csv.name}).",
          "- Cash earns FRED DTB3 (3-month T-bill), using the rate published before each day.",
          "- Taxes are ignored. Monthly rebalancing and selling would make them worse for the rule than for SPY held."]
    path = out_dir / f"multi_asset_trend_backtest_{today.isoformat()}.md"
    path.write_text("\n".join(L) + "\n")
    return path, verdict.passed


def problems_line(v: mt.Verdict) -> str:
    return "Log check: " + v.bars[0][2] + "."
