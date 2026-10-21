"""Checklist short, enhanced: 50% squeeze cap, 1.5%-at-risk sizing, IBKR locate/borrow costs, IBKR placeability.

Rules: docs/research/spike_short_v4_preregistration.md. Same stocks and exit as v3 (short the open, cover at the
first green close within t..t+3), no Alpaca shortable filter. 50% stop (+2% slippage), 2.7% of equity per short,
at most 3 open. Costs: v3 trading tiers + 1% locate + 150%/yr borrow per night.
"""

from __future__ import annotations

import io
import sqlite3
import urllib.request
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from research import spike_short as ss
from research import spike_short_v2 as v2
from research import spike_short_v3 as v3

RISK = 0.015
STOP = 0.50
STOP_SLIP = 0.02
EST_COST = 0.03
LOCATE = 0.01
BORROW_APR = 1.50
EXTRA_DAYS = 3
MAX_OPEN = 3
ALT_STOPS = (0.30, 0.75, 1.00)
NO_STOP_POSITION = 0.075                                   # v3's size, used only for the "no stop" row
START_EQUITY = 100_000.0
FIRST = pd.Timestamp("2017-01-01")
LIVE_DAYS = 120
LIVE_SHARE = 0.50
IBKR_URL = "ftp://shortstock:@ftp2.interactivebrokers.com/usa.txt"


def position_for(stop: float | None) -> float:
    """Fraction of equity per short so that a stop hit loses about RISK of the account."""
    return NO_STOP_POSITION if stop is None else RISK / (stop + STOP_SLIP + EST_COST)


POSITION = position_for(STOP)                               # 2.7%


@dataclass
class Trade:
    symbol: str
    day: pd.Timestamp
    exit_day: pd.Timestamp
    gap: float
    entry: float
    exit: float
    stopped: bool
    gross: float
    cost: float
    net: float
    pnl: float
    account_hit: float                                     # pnl as a fraction of equity at entry


def plan(e: dict, stop: float | None = STOP, extra: int = EXTRA_DAYS, cost_mult: float = 1.0,
         locate: float = LOCATE, borrow: float = BORROW_APR):
    """Marks [(day, price)], stopped, cost (fraction of notional) for one short entered at t's open."""
    entry = e["path"][0][1]
    level = entry * (1 + stop) if stop is not None else np.inf
    marks, stopped = [], False
    for k, (d, o, h, c) in enumerate(e["path"][:extra + 1]):
        if k > 0 and o >= level:
            marks.append((d, o)); stopped = True; break
        if h >= level:
            marks.append((d, level)); stopped = True; break
        marks.append((d, c))
        if c < entry:
            break
    nights = (marks[-1][0] - e["path"][0][0]).days
    cost = (ss.cost_for(entry * e["raw_factor"], e["dv20"]) + (STOP_SLIP if stopped else 0.0) + locate
            + borrow * nights / 365) * cost_mult
    return marks, stopped, cost


def simulate(events: list[dict], calendar: pd.DatetimeIndex, stop: float | None = STOP, extra: int = EXTRA_DAYS,
             cost_mult: float = 1.0, locate: float = LOCATE, borrow: float = BORROW_APR,
             position: float | None = None) -> tuple[pd.Series, list[Trade]]:
    size = position_for(stop) if position is None else position
    by_day: dict[pd.Timestamp, list[dict]] = {}
    for e in events:
        by_day.setdefault(e["day"], []).append(e)
    equity, values, trades, open_pos = START_EQUITY, [], [], []
    for day in calendar:
        for e in sorted(by_day.get(day, []), key=lambda e: -e["gap"]):
            if len(open_pos) >= MAX_OPEN:
                break
            marks, stopped, cost = plan(e, stop, extra, cost_mult, locate, borrow)
            entry = e["path"][0][1]
            notional = size * equity
            open_pos.append({"e": e, "marks": marks, "stopped": stopped, "cost": cost, "entry": entry, "eq0": equity,
                             "shares": notional / entry, "notional": notional, "mark": entry, "pnl": 0.0})
        pnl, still = 0.0, []
        for p in open_pos:
            for d, m in p["marks"]:
                if d == day:
                    dp = p["shares"] * (p["mark"] - m)
                    p["mark"], p["pnl"], pnl = m, p["pnl"] + dp, pnl + dp
            if p["marks"][-1][0] <= day:
                fee = p["cost"] * p["notional"]
                pnl -= fee
                p["pnl"] -= fee
                x = p["marks"][-1][1]
                gross = (p["entry"] - x) / p["entry"]
                trades.append(Trade(p["e"]["symbol"], p["e"]["day"], p["marks"][-1][0], p["e"]["gap"], p["entry"], x,
                                    p["stopped"], gross, p["cost"], gross - p["cost"], p["pnl"], p["pnl"] / p["eq0"]))
            else:
                still.append(p)
        open_pos = still
        equity += pnl
        values.append(equity)
    return pd.Series(values, index=calendar), trades


# --- IBKR shortable list (free, public) ------------------------------------------------------------

def parse_ibkr(text: str) -> dict[str, tuple[int, float]]:
    """{symbol: (shares available, annual fee %)} from IBKR's pipe-delimited usa.txt. '>10000000' counts as 10M."""
    out = {}
    for line in io.StringIO(text):
        if line.startswith("#"):
            continue
        f = line.rstrip("\n").split("|")
        if len(f) < 8 or not f[0]:
            continue
        avail = f[7].strip().lstrip(">")
        try:
            out[f[0].strip()] = (int(avail or 0), float(f[6] or "nan"))
        except ValueError:
            continue
    return out


def fetch_ibkr(cache: Path | None = None, timeout: float = 90.0) -> tuple[dict[str, tuple[int, float]], str]:
    """Download IBKR's list (or read today's cached copy). Returns (table, stamp line)."""
    if cache is not None and cache.exists() and cache.stat().st_size > 0:
        text = cache.read_text()
    else:
        with urllib.request.urlopen(IBKR_URL, timeout=timeout) as r:
            text = r.read().decode("latin-1")
        if cache is not None:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(text)
    stamp = text.splitlines()[0].lstrip("#BOF|").replace("|", " ") if text else "?"
    return parse_ibkr(text), stamp


# --- Run -----------------------------------------------------------------------------------------

def _stats(eq, trades) -> v2.Stats:
    return v3._stats(eq, trades)


def run(creds, bars_conn: sqlite3.Connection, conn: sqlite3.Connection, out_dir: Path, today: date,
        ibkr_cache: Path | None = None) -> tuple[Path, bool]:
    df = v3.add_features(ss.load_bars(bars_conn))
    spy = pd.read_sql("SELECT day, close FROM spy", bars_conn, parse_dates=["day"]).set_index("day")["close"].sort_index()

    cand = v2.screen(df, min(v2.NEIGHBOUR_GAPS))
    events, problems, ssr = [], [], 0
    for r in cand.itertuples():
        if r.day < FIRST:
            continue
        d, p = r.day.date(), r.prev_day.date()
        if v2.has_news(creds, conn, r.symbol, d, p):
            continue
        if r.ssr_at_open:
            ssr += 1
            continue
        if not (r.prev_day < r.day):
            problems.append(f"{r.symbol} {d}: screen uses a bar from the trade day")
        events.append({"symbol": r.symbol, "day": r.day, "gap": r.gap, "path": v3.path_of(r),
                       "raw_factor": r.raw_close / r.close, "dv20": r.dv20_before})

    cal = spy.index[spy.index >= FIRST]
    spy_eq = spy.loc[cal] / spy.loc[cal].iloc[0] * START_EQUITY
    spy_s = v2.stats(spy_eq, [])

    def pick(gap=v2.GAP):
        return [e for e in events if e["gap"] >= gap]

    runs = {"main": simulate(pick(), cal), "double": simulate(pick(), cal, cost_mult=2.0),
            "g50": simulate(pick(0.50), cal), "g100": simulate(pick(1.00), cal),
            "no_locate": simulate(pick(), cal, locate=0.0), "same_day": simulate(pick(), cal, extra=0),
            "no_stop": simulate(pick(), cal, stop=None)}
    for s in ALT_STOPS:
        runs[f"stop{int(s * 100)}"] = simulate(pick(), cal, stop=s)
    st = {k: _stats(*v) for k, v in runs.items()}

    ibkr, stamp = fetch_ibkr(ibkr_cache)
    live_days = spy.index[-LIVE_DAYS:]
    live = [e for e in events if e["day"] in live_days and e["gap"] >= v2.GAP]
    rows = []
    for e in live:
        raw_entry = e["path"][0][1] * e["raw_factor"]
        need = int(np.ceil(POSITION * START_EQUITY / raw_entry))
        avail, fee = ibkr.get(e["symbol"], (0, float("nan")))
        rows.append((e, need, avail, fee, avail >= need))
    placeable = sum(r[4] for r in rows)
    share = placeable / len(rows) if rows else 0.0

    m = st["main"]
    worst = min(runs["main"][1], key=lambda t: t.account_hit, default=None)
    worst_hit = worst.account_hit if worst else 0.0
    bars = [
        ("Data check", not problems, "no problems" if not problems else "; ".join(problems[:5])),
        ("Enough trades (≥ 30)", m.trades >= 30, f"{m.trades} trades"),
        ("Makes money after costs", m.total > 0 and m.avg > 0, f"total {m.total:+.1%}, average trade {m.avg:+.2%}"),
        ("Worth the risk (Sharpe ≥ SPY)", m.sharpe >= spy_s.sharpe, f"{m.sharpe:.2f} vs {spy_s.sharpe:.2f}"),
        ("Survivable (worst drop ≤ 25%)", m.max_dd <= 0.25, f"{m.max_dd:.1%}"),
        ("Survives double costs (2% locate, 300%/yr borrow)", st["double"].total > 0, f"total {st['double'].total:+.1%}"),
        ("Not fragile (+50% and +100% gaps average > 0)", st["g50"].avg > 0 and st["g100"].avg > 0,
         f"+50%: {st['g50'].avg:+.2%} ({st['g50'].trades}); +100%: {st['g100'].avg:+.2%} ({st['g100'].trades})"),
        ("Squeeze cap works (no trade costs > 3% of the account)", worst_hit >= -0.03,
         f"worst {worst_hit:+.2%} of the account" + (f" ({worst.symbol} {worst.day.date()})" if worst else "")),
        ("Placeable at IBKR (≥ 50% of last 120 days' signals, ≥ 5, have a full position to borrow today)",
         share >= LIVE_SHARE and placeable >= 5, f"{placeable} of {len(rows)} ({share:.0%})"),
    ]
    go = all(ok for _, ok, _ in bars)

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(10, 5))
    for k, label, c in (("main", "v4: 50% stop, 2.7% size, IBKR costs (primary)", "#2563eb"),
                        ("no_stop", "No stop, 7.5% size (v3-like, IBKR costs)", "#9ca3af")):
        ax.plot(runs[k][0], label=label, color=c)
    ax.plot(spy_eq, label="SPY buy-and-hold", color="#111827")
    ax.set_yscale("log")
    ax.set_ylabel("Account value ($, log scale)")
    ax.set_title("Checklist short, enhanced (v4)")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8)
    fig.tight_layout()
    (out_dir / "img").mkdir(parents=True, exist_ok=True)
    fig.savefig(out_dir / "img" / "spike_short_v4_equity.png", dpi=120)
    plt.close(fig)
    csv = out_dir / "spike_short_v4_trades.csv"
    pd.DataFrame([t.__dict__ for t in runs["main"][1]]).to_csv(csv, index=False, float_format="%.6g")

    labels = {"main": "**50% stop, 2.7% size, 1% locate + 150%/yr (primary)**",
              "double": "Primary, double costs", "no_locate": "No locate fee", "same_day": "Always cover same day",
              "stop30": "30% stop (re-sized)", "stop75": "75% stop (re-sized)", "stop100": "100% stop (re-sized)",
              "no_stop": "No stop, 7.5% size", "g50": "Gap ≥ +50%", "g100": "Gap ≥ +100%"}
    md = [f"# Checklist Short, Enhanced (v4) — {today}", "",
          "*Paper research only; not investment advice. Rules: "
          "[spike_short_v4_preregistration.md](spike_short_v4_preregistration.md). **Third look at the same data**: "
          "a pass is weak evidence and can only earn a forward paper (shadow) test.*", "",
          f"## Verdict: **{'GO' if go else 'NO-GO'}** ({sum(ok for _, ok, _ in bars)} of {len(bars)} bars passed)", "",
          "| Bar | Result | Detail |", "|---|---|---|"]
    md += [f"| {i} · {n} | {'PASS' if ok else '**FAIL**'} | {d} |" for i, (n, ok, d) in enumerate(bars, 1)]
    md += ["", f"## Results ({cal[0].date()} to {cal[-1].date()}; at most 3 open)", "",
           f"Signals skipped for the short-sale rule at the open: {ssr}. No Alpaca shortable filter (venue: IBKR).", "",
           "| | Trades | Winners | Avg trade | Total | Annual | Worst drop | Sharpe | Stopped |",
           "|---|---|---|---|---|---|---|---|---|"]
    md += [f"| {labels[k]} | {st[k].trades} | {st[k].win:.0%} | {st[k].avg:+.2%} | {st[k].total:+.1%} | "
           f"{st[k].cagr:+.1%} | {st[k].max_dd:.1%} | {st[k].sharpe:.2f} | {st[k].stops:.0%} |" for k in labels]
    md += [f"| SPY buy-and-hold | — | — | — | {spy_s.total:+.1%} | {spy_s.cagr:+.1%} | {spy_s.max_dd:.1%} | "
           f"{spy_s.sharpe:.2f} | — |", "", "**Primary, year by year (account return)**", "",
           "| Year | Trades | v4 | SPY |", "|---|---|---|---|"]
    eq = runs["main"][0]
    spy_r = spy_eq.pct_change().fillna(0)
    for y in sorted(set(eq.index.year)):
        yr = eq[eq.index.year == y]
        prev = eq[eq.index.year < y]
        base = prev.iloc[-1] if len(prev) else START_EQUITY
        n = sum(1 for t in runs["main"][1] if t.exit_day.year == y)
        md.append(f"| {y} | {n} | {yr.iloc[-1] / base - 1:+.1%} | {(1 + spy_r[spy_r.index.year == y]).prod() - 1:+.1%} |")
    tr = sorted(runs["main"][1], key=lambda t: t.account_hit)
    md += ["", "![Account values](img/spike_short_v4_equity.png)", "",
           "## The 10 worst trades", "",
           "| Entry day | Exit day | Symbol | Gap | Entry | Exit | Stopped | Trade | Account |",
           "|---|---|---|---|---|---|---|---|---|"]
    md += [f"| {t.day.date()} | {t.exit_day.date()} | {t.symbol} | {t.gap:+.0%} | ${t.entry:.2f} | ${t.exit:.2f} | "
           f"{'yes' if t.stopped else ''} | {t.net:+.0%} | {t.account_hit:+.2%} |" for t in tr[:10]]
    md += ["", f"## Placeable at IBKR: last {LIVE_DAYS} trading days", "",
           f"IBKR list stamp: {stamp}. A full position = {POSITION:.1%} of $100,000.", "",
           "| Day | Symbol | Shares needed | IBKR available | Fee %/yr | OK |", "|---|---|---|---|---|---|"]
    md += [f"| {e['day'].date()} | {e['symbol']} | {need:,} | {avail:,} | "
           f"{'' if fee != fee else f'{fee:.0f}'} | {'yes' if ok else 'no'} |"
           for e, need, avail, fee, ok in sorted(rows, key=lambda r: r[0]["day"])]
    md += ["", "Caveat: this is availability **today**, not on each spike morning. The forward shadow log measures "
           "that.", "", f"Every trade: [{csv.name}]({csv.name}).", ""]
    path = out_dir / f"spike_short_v4_backtest_{today}.md"
    path.write_text("\n".join(md))
    return path, go
