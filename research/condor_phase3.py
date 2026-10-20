"""Phase 3 runner: discovery grid → choose → confirmation → pass/fail bars → sensitivities → report.

Order matters for honesty: confirmation results are computed for the chosen setting only, after the choice.
"""

from __future__ import annotations

import math
import sqlite3
from bisect import bisect_left
from collections import defaultdict
from datetime import date
from pathlib import Path
from statistics import mean, median

import numpy as np
import pandas as pd

from pricing.black_scholes import price as bs_price
from pricing.implied_volatility import implied_vol
from research.condor_backtest import (
    CONFIRMATION_START, CRASH_WINDOWS, DISCOVERY, DROP_BEST, EQUITY, MAX_DD, MIN_QUALIFYING_NEIGHBOURS,
    MIN_TRADES_NEIGHBOUR, T_STAT_BAR, ExitParams, Market, RunResult, Setting, Trade, choose, ex_dividend_dates,
    full_grid, max_drawdown, monthly_pnl, neighbours, simulate, t_stat,
)
from research.condor_build_report import RateLookup, ensure_rates, load_day, trading_days_and_events
from strategies.iron_condor import CondorParams, Decision, build_condor

DATA_END = date(2023, 12, 29)


# --- Inputs ------------------------------------------------------------------------------------

def build_decisions(vol, tdays, events, params_list, start: date, end: date, rates: RateLookup,
                    use_events: bool = True) -> dict[CondorParams, dict[date, Decision]]:
    out: dict[CondorParams, dict[date, Decision]] = {p: {} for p in params_list}
    chain_days = {d for (d,) in vol.execute("SELECT DISTINCT quote_date FROM eod_chain")}
    for day in tdays:
        if not (start <= day <= end) or day.isoformat() not in chain_days:
            continue
        spot, chains = load_day(vol, day.isoformat())
        cache: dict = {}
        for p in params_list:
            out[p][day] = build_condor(day, p, spot, chains, rates.rate(day, p.target_dte / 365), tdays,
                                       events if use_events else set(), cache)
    return out


def index_series(vol, symbol: str) -> tuple[list[date], list[float]]:
    rows = vol.execute("SELECT day, close FROM index_daily WHERE symbol=? ORDER BY day", (symbol,)).fetchall()
    return [date.fromisoformat(d) for d, _ in rows], [c for _, c in rows]


def prev_close(series: tuple[list[date], list[float]], day: date) -> float | None:
    days, vals = series
    i = bisect_left(days, day) - 1
    return vals[i] if i >= 0 else None


def iv_rank_prev(series, day: date, lookback: int = 252) -> float | None:
    days, vals = series
    i = bisect_left(days, day) - 1
    if i < lookback - 1:
        return None
    window = vals[i - lookback + 1:i + 1]
    lo, hi = min(window), max(window)
    return 100 * (vals[i] - lo) / (hi - lo) if hi > lo else None


def intraday_extremes(history: sqlite3.Connection) -> dict[date, tuple[float, float]]:
    """Regular-hours high/low per day in real (unadjusted) prices."""
    bars = pd.read_sql_query("SELECT ts, high, low FROM minute_bars WHERE symbol='SPY' AND ts>='2016-01-01'", history)
    et = pd.to_datetime(bars["ts"], utc=True).dt.tz_convert("America/New_York")
    tod = et.dt.hour * 60 + et.dt.minute
    bars = bars[(tod >= 570) & (tod < 960)].assign(day=et.dt.date)
    agg = bars.groupby("day").agg(hi=("high", "max"), lo=("low", "min"))
    factors = dict(history.execute("SELECT day, factor FROM raw_factors WHERE symbol='SPY'").fetchall())
    return {d: (r.hi * factors.get(d.isoformat(), 1.0), r.lo * factors.get(d.isoformat(), 1.0)) for d, r in agg.iterrows()}


def make_intraday(market: Market, extremes, rates: RateLookup, qmap: dict):
    """D2 estimate: reprice legs at the day's SPY high and low with the previous snapshot's IVs (sticky strike)."""
    def est(t: Trade, day: date) -> float | None:
        c = t.condor
        path = market.path(c)
        prev = [d for d in path if d < day and all(x.ok() for x in path[d])]
        if not prev or day not in extremes:
            return None
        p = max(prev)
        s_p = market.spot.get(p)
        if s_p is None:
            return None
        q = qmap.get(id(c), 0.0)
        legs = list(zip(path[p], (c.short_put, c.long_put, c.short_call, c.long_call)))
        T_p = (c.expiry - p).days / 365
        T_d = max((c.expiry - day).days, 1) / 365
        r = rates.rate(p, T_p)
        ivs = []
        for quote, leg in legs:
            kind = "put" if leg.kind == "P" else "call"
            iv = implied_vol(quote.mid, s_p, leg.strike, T_p, r, q, kind)
            if not iv.ok:
                return None
            ivs.append(iv.iv)
        worst = 0.0
        for s in extremes[day]:
            cost = 0.0
            for (quote, leg), iv in zip(legs, ivs):
                kind = "put" if leg.kind == "P" else "call"
                m = bs_price(s, leg.strike, T_d, r, q, iv, kind)
                half = quote.spread / 2
                cost += (m + half) if leg.side < 0 else -(max(0.0, m - half))
            worst = max(worst, cost)
        return worst
    return est


# --- Bars --------------------------------------------------------------------------------------

def _closed(trades):
    return [t for t in trades if t.exit_day is not None]


def bars(chosen: Setting, full: RunResult, discovery_results: dict[Setting, RunResult]) -> list[tuple[str, bool, str]]:
    tr = _closed(full.trades)
    disc = [t for t in tr if t.condor.day <= DISCOVERY[1]]
    conf = [t for t in tr if t.condor.day >= CONFIRMATION_START]
    allp = [t.pnl for t in tr]
    out = []
    md, mc = (mean(t.pnl for t in disc) if disc else float("nan")), (mean(t.pnl for t in conf) if conf else float("nan"))
    ts = t_stat(allp)
    out.append(("1 · Profitable in both periods, t ≥ 2", md > 0 and mc > 0 and ts >= T_STAT_BAR,
                f"discovery ${md:,.2f}/trade ({len(disc)}), confirmation ${mc:,.2f}/trade ({len(conf)}), "
                f"per-trade t = {ts:.2f}; monthly-P&L t = {t_stat(monthly_pnl(full.equity)):.2f} (information)"))
    ms = mean(t.pnl_stress for t in tr) if tr else float("nan")
    out.append(("2 · Survives worse fills", ms > 0, f"cost-stress average ${ms:,.2f}/trade"))
    over = [t for t in tr if -t.pnl > t.loss_allowance + 1e-6]
    out.append(("3 · Losses stay within plan", not over,
                f"{len(over)} trades lost more than planned" + (f" (worst ${min(t.pnl for t in over):,.0f})" if over else "")))
    dd, when = max_drawdown(full.equity)
    out.append(("4 · Drawdown under 10%", dd < MAX_DD * EQUITY, f"worst drawdown ${dd:,.0f} ({dd / EQUITY:.1%}) on {when}"))
    parts, ok5 = [], True
    for name, a, b in CRASH_WINDOWS:
        if a > DATA_END:
            parts.append(f"{name}: no data yet (needs Phase 5)")
            continue
        wdd, _ = max_drawdown(full.equity, a, b)
        wp = sum(t.pnl for t in tr if t.exit_day and a <= t.exit_day <= b)
        ok5 &= wdd < MAX_DD * EQUITY
        parts.append(f"{name}: drawdown {wdd / EQUITY:.1%}, closed P&L ${wp:,.0f}")
    out.append(("5 · Crash periods", ok5, "; ".join(parts)))
    nb = [n for n in neighbours(chosen, ("dte", "delta", "width")) if n in discovery_results]
    qual = [(n, _closed(discovery_results[n].trades)) for n in nb]
    qual = [(n, t) for n, t in qual if len(t) >= MIN_TRADES_NEIGHBOUR]
    ok6 = len(qual) >= MIN_QUALIFYING_NEIGHBOURS and all(mean(x.pnl for x in t) > 0 for _, t in qual)
    out.append(("6 · Not a lucky setting", ok6,
                f"{len(qual)} neighbours with ≥{MIN_TRADES_NEIGHBOUR} trades: " +
                ", ".join(f"{n.entry.target_dte}/{n.entry.short_delta:.2f}/${n.entry.width:g} ${mean(x.pnl for x in t):,.2f}"
                          for n, t in qual)))
    k = math.ceil(DROP_BEST * len(allp))
    trimmed = sorted(allp)[:-k] if k else allp
    m7 = mean(trimmed) if trimmed else float("nan")
    out.append(("7 · Not a few lucky trades", m7 > 0, f"without the best {k} trades: ${m7:,.2f}/trade"))
    return out


def summary(trades: list[Trade]) -> str:
    tr = _closed(trades)
    if not tr:
        return "0 trades"
    p = [t.pnl for t in tr]
    return (f"{len(tr)} trades, ${mean(p):,.2f}/trade, win rate {sum(x > 0 for x in p) / len(p):.0%}, "
            f"worst ${min(p):,.0f}, total ${sum(p):,.0f}")


# --- Main ----------------------------------------------------------------------------------------

def run(vol: sqlite3.Connection, history: sqlite3.Connection, out_dir: Path, today: date, log=print) -> tuple[Path, bool]:
    ensure_rates(vol)
    rates = RateLookup(vol)
    tdays, events = trading_days_and_events(history, vol)
    tdays = [d for d in tdays if d <= DATA_END]
    market = Market(vol, tdays)
    ex_div = ex_dividend_dates(range(2015, 2025))
    grid = full_grid()
    entry_params = sorted({s.entry for s in grid}, key=lambda p: (p.target_dte, p.short_delta, p.width))

    log("building entry decisions 2016–2023 …")
    decisions = build_decisions(vol, tdays, events, entry_params, DISCOVERY[0], DATA_END, rates)

    log("discovery: 162 settings …")
    disc = {s: simulate(s, decisions[s.entry], market, DISCOVERY[0], DISCOVERY[1], ex_div) for s in grid}
    chosen, raw, smooth = choose(disc)
    if chosen is None:
        text = _report_header(today) + ["**No setting had the 60 discovery trades needed to be chosen. Result: no-go.**"]
        text += _grid_table(disc, raw, smooth, None)
        path = out_dir / f"condor_backtest_{today.isoformat()}.md"
        path.write_text("\n".join(text) + "\n")
        return path, False

    log(f"chosen: {chosen.label()} — running full period …")
    full = simulate(chosen, decisions[chosen.entry], market, DISCOVERY[0], DATA_END, ex_div)
    d1 = [(t.condor.day, round(t.pnl, 6)) for t in _closed(disc[chosen].trades)]
    d2 = [(t.condor.day, round(t.pnl, 6)) for t in _closed(full.trades) if t.condor.day <= DISCOVERY[1]]
    assert d1 == d2, "discovery trades changed between runs"
    results = bars(chosen, full, disc)
    passed = all(ok for _, ok, _ in results)

    log("sensitivities …")
    vix, vix9d, vix3m = index_series(vol, "VIX"), index_series(vol, "VIX9D"), index_series(vol, "VIX3M")
    sens = []
    ivr = lambda d: (iv_rank_prev(vix, d) or 0) >= 30
    sens.append(("IV Rank filter on (VIX IV Rank ≥ 30)", simulate(chosen, decisions[chosen.entry], market,
                                                                   DISCOVERY[0], DATA_END, ex_div, entry_filter=ivr)))
    no_ev = build_decisions(vol, tdays, events, [chosen.entry], DISCOVERY[0], DATA_END, rates, use_events=False)
    sens.append(("Event filter off", simulate(chosen, no_ev[chosen.entry], market, DISCOVERY[0], DATA_END, ex_div)))
    t14 = Setting(chosen.entry, ExitParams(chosen.exit.profit_target, chosen.exit.stop_multiple, 14))
    sens.append(("Time exit at 14 days", simulate(t14, decisions[chosen.entry], market, DISCOVERY[0], DATA_END, ex_div)))
    if chosen.exit.stop_multiple is not None:
        qmap = {id(d.condor): d.details.get("dividend_yield", 0.0) for d in decisions[chosen.entry].values() if d.condor}
        est = make_intraday(market, intraday_extremes(history), rates, qmap)
        sens.append(("Intraday stop estimate (estimated)", simulate(chosen, decisions[chosen.entry], market,
                                                                    DISCOVERY[0], DATA_END, ex_div, intraday=est)))
    term = lambda d: (prev_close(vix, d) or 0) < (prev_close(vix3m, d) or 0)
    sens.append(("Term-structure filter: VIX < VIX3M (Amendment 4, information only)",
                 simulate(chosen, decisions[chosen.entry], market, DISCOVERY[0], DATA_END, ex_div, entry_filter=term)))

    # Amendment 4 splits
    skew_hist = sorted((d, x.details.get("skew_25d")) for d, x in decisions[chosen.entry].items()
                       if x.details.get("skew_25d") is not None)
    sk_days = [d for d, _ in skew_hist]
    sk_vals = [v for _, v in skew_hist]

    def skew_median_before(day):
        i = bisect_left(sk_days, day)
        past = sk_vals[max(0, i - 252):i]
        return median(past) if len(past) >= 60 else None

    splits = defaultdict(list)
    for t in _closed(full.trades):
        d = t.condor.day
        a, b = prev_close(vix9d, d), prev_close(vix, d)
        if a and b:
            splits["VIX9D ÷ VIX below 1 (normal)" if a / b < 1 else "VIX9D ÷ VIX 1 or above (short-term fear)"].append(t)
        b3 = prev_close(vix3m, d)
        if b and b3:
            splits["VIX ÷ VIX3M below 1 (normal)" if b / b3 < 1 else "VIX ÷ VIX3M 1 or above (inverted)"].append(t)
        med = skew_median_before(d)
        if t.skew is not None and med is not None:
            splits["Skew above its 1-year median (steep)" if t.skew > med else "Skew at or below its 1-year median"].append(t)

    _equity_chart(full, out_dir / "img" / f"condor_equity_{today.isoformat()}.png", chosen)
    text = _report(today, chosen, raw, smooth, disc, full, results, passed, sens, splits)
    path = out_dir / f"condor_backtest_{today.isoformat()}.md"
    path.write_text(text)
    return path, passed


# --- Report ----------------------------------------------------------------------------------------

def _report_header(today):
    return [f"# Iron Condor Backtest — Phase 3 ({today.isoformat()})", "",
            "SPY iron condors on OptionsDX end-of-day quotes, exactly as pre-registered in "
            "`docs/research/iron_condor_preregistration.md` (sections B–E, Amendments 3–4). Fills at bid/ask, "
            "$0.05 per contract per leg per side, $100,000 fixed account, 1% risk per condor, up to 3 open, one new "
            "condor a week. Discovery = entries 2016–2021 (all 162 settings); confirmation = entries 2022–2023 "
            "(chosen setting only; 2024–2026 needs the Phase 5 data).", ""]


def _grid_table(disc, raw, smooth, chosen):
    L = ["## All 162 settings, discovery period (2016–2021)", "",
         "Score = average profit per trade ÷ planned max loss; settings with under 60 trades get no score.", "",
         "| Setting | Trades | Avg P&L | Win rate | Score | Smoothed |", "|---|---|---|---|---|---|"]
    for s, r in sorted(disc.items(), key=lambda kv: (kv[0].entry.target_dte, kv[0].entry.short_delta, kv[0].entry.width,
                                                      str(kv[0].exit.profit_target), str(kv[0].exit.stop_multiple))):
        tr = _closed(r.trades)
        p = [t.pnl for t in tr]
        sc = "–" if raw[s] is None else f"{raw[s]:+.3f}"
        sm = "–" if smooth[s] is None else f"{smooth[s]:+.3f}"
        mark = " **← chosen**" if s == chosen else ""
        L.append(f"| {s.label()}{mark} | {len(tr)} | " + (f"${mean(p):,.2f} | {sum(x > 0 for x in p) / len(p):.0%}"
                                                          if p else "– | –") + f" | {sc} | {sm} |")
    return L + [""]


def _report(today, chosen, raw, smooth, disc, full, results, passed, sens, splits) -> str:
    L = _report_header(today)
    L += [f"## Result: {'**GO** — all 7 bars passed' if passed else '**NO-GO** — ' + str(sum(not ok for _, ok, _ in results)) + ' of 7 bars failed'}",
          "", f"Chosen setting (highest smoothed discovery score): **{chosen.label()}**", "",
          "| Bar | Result | Detail |", "|---|---|---|"]
    L += [f"| {name} | {'✅ pass' if ok else '❌ fail'} | {detail} |" for name, ok, detail in results]
    tr = _closed(full.trades)
    reasons = defaultdict(int)
    for t in tr:
        reasons[t.exit_reason] += 1
    L += ["", "## Chosen setting, full period", "",
          f"- All: {summary(full.trades)}",
          f"- Discovery: {summary([t for t in tr if t.condor.day <= DISCOVERY[1]])}",
          f"- Confirmation: {summary([t for t in tr if t.condor.day >= CONFIRMATION_START])}",
          f"- Cost-stress fills: ${mean(t.pnl_stress for t in tr):,.2f}/trade · optimistic fills (information): "
          f"${mean(t.pnl_optimistic for t in tr):,.2f}/trade",
          f"- Exits: " + ", ".join(f"{k} {v}" for k, v in sorted(reasons.items(), key=lambda x: -x[1])),
          f"- Still open at the end of the data: {full.unfinished}",
          f"- Equity chart: `docs/research/img/condor_equity_{today.isoformat()}.png`", "",
          "### Per year", "", "| Year | Trades | Total P&L | Avg/trade | Worst trade |", "|---|---|---|---|---|"]
    for y in sorted({t.condor.day.year for t in tr}):
        p = [t.pnl for t in tr if t.condor.day.year == y]
        L.append(f"| {y} | {len(p)} | ${sum(p):,.0f} | ${mean(p):,.2f} | ${min(p):,.0f} |")
    L += ["", "## Sensitivity checks (chosen setting only; can't change the result)", ""]
    L += [f"- **{name}:** {summary(r.trades)}" for name, r in sens]
    L += ["", "## Volatility-surface splits (Amendment 4, information only)", ""]
    L += [f"- **{k}:** {summary(v)}" for k, v in sorted(splits.items())]
    L += [""] + _grid_table(disc, raw, smooth, chosen)
    return "\n".join(L) + "\n"


def _equity_chart(full: RunResult, path: Path, chosen: Setting) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    path.parent.mkdir(parents=True, exist_ok=True)
    days = sorted(full.equity)
    vals = [full.equity[d] for d in days]
    fig, ax = plt.subplots(figsize=(10, 4.2))
    ax.plot(days, vals, color="#2b6cb0", linewidth=1.4)
    ax.axhline(EQUITY, color="#999", linewidth=0.8, linestyle="--")
    ax.axvspan(CONFIRMATION_START, days[-1], color="#f6ad55", alpha=0.12, label="confirmation (never tuned on)")
    ax.set_title(f"Iron condor backtest: {chosen.label()}", fontsize=10)
    ax.set_ylabel("Account value ($)")
    ax.legend(loc="upper left", fontsize=8, frameon=False)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)
