"""Webull checklist, simplified: short at the open; cover at the close if in profit, else hold up to 3 more days.

Rules: docs/research/spike_short_v3_preregistration.md (incl. the 2026-10-21 amendment). Same screen as v2
(+75% gap, dead company, no news). Cover at the first close below the entry (t, t+1, t+2 or t+3), else at t+3's
close. No stop (20% stop as robustness). 7.5% of equity per position, at most 3 open.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

from research import spike_short as ss
from research import spike_short_v2 as v2

POSITION = 0.075
MAX_OPEN = 3
EXTRA_DAYS = 3
ALT_EXTRA = (0, 2)
STOP = 0.20
BORROW_APR = 0.20
START_EQUITY = 100_000.0
FIRST = pd.Timestamp("2017-01-01")
LIVE_DAYS = 120


def add_features(df: pd.DataFrame) -> pd.DataFrame:
    df = v2.add_features(df)
    g = df.groupby("symbol", sort=False)
    df["prev_low"] = g["low"].shift(1)
    df["prev_prev_close"] = g["close"].shift(2)
    df["ssr_at_open"] = df["prev_low"] <= (1 - ss.SSR_DROP) * df["prev_prev_close"]
    for k in range(1, EXTRA_DAYS + 1):
        for f in ("day", "open", "high", "close"):
            df[f"{f}_{k}"] = g[f].shift(-k)
    return df


def path_of(r) -> list[tuple[pd.Timestamp, float, float, float]]:
    """(day, open, high, close) for t..t+3, stopping at missing data or a gap of more than 5 calendar days."""
    out = [(r.day, r.open, r.high, r.close)]
    for k in range(1, EXTRA_DAYS + 1):
        d = getattr(r, f"day_{k}")
        if d != d or (d - out[-1][0]).days > ss.MAX_GAP_DAYS:
            break
        out.append((d, getattr(r, f"open_{k}"), getattr(r, f"high_{k}"), getattr(r, f"close_{k}")))
    return out


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


def plan(e: dict, stop: float | None, extra: int, cost_mult: float = 1.0):
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
            break                                      # in the green: cover
    nights = (marks[-1][0] - e["path"][0][0]).days
    cost = (ss.cost_for(entry * e["raw_factor"], e["dv20"]) + (ss.STOP_SLIPPAGE if stopped else 0.0)
            + BORROW_APR * nights / 365) * cost_mult
    return marks, stopped, cost


def simulate(events: list[dict], calendar: pd.DatetimeIndex, stop: float | None = None, extra: int = EXTRA_DAYS,
             cost_mult: float = 1.0) -> tuple[pd.Series, list[Trade]]:
    by_day: dict[pd.Timestamp, list[dict]] = {}
    for e in events:
        by_day.setdefault(e["day"], []).append(e)
    equity, values, trades, open_pos = START_EQUITY, [], [], []
    for day in calendar:
        for e in sorted(by_day.get(day, []), key=lambda e: -e["gap"]):
            if len(open_pos) >= MAX_OPEN:
                break
            marks, stopped, cost = plan(e, stop, extra, cost_mult)
            entry = e["path"][0][1]
            notional = POSITION * equity
            open_pos.append({"e": e, "marks": marks, "stopped": stopped, "cost": cost, "entry": entry,
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
                                    p["stopped"], gross, p["cost"], gross - p["cost"], p["pnl"]))
            else:
                still.append(p)
        open_pos = still
        equity += pnl
        values.append(equity)
    return pd.Series(values, index=calendar), trades


def _stats(eq, trades) -> v2.Stats:
    s = v2.stats(eq, [])
    r = np.array([t.net for t in trades])
    s.trades = len(r)
    s.win = float((r > 0).mean()) if len(r) else 0.0
    s.avg = float(r.mean()) if len(r) else 0.0
    s.stops = float(np.mean([t.stopped for t in trades])) if trades else 0.0
    return s


def run(creds, bars_conn: sqlite3.Connection, conn: sqlite3.Connection, out_dir: Path, today: date) -> tuple[Path, bool]:
    df = add_features(ss.load_bars(bars_conn))
    spy = pd.read_sql("SELECT day, close FROM spy", bars_conn, parse_dates=["day"]).set_index("day")["close"].sort_index()
    grid = ss.etb_grid(bars_conn)
    etb_today = dict(bars_conn.execute("SELECT symbol, easy_to_borrow FROM assets"))

    cand = v2.screen(df, min(v2.NEIGHBOUR_GAPS))            # dead companies, gap >= +50%
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
        events.append({"symbol": r.symbol, "day": r.day, "gap": r.gap, "path": path_of(r),
                       "raw_factor": r.raw_close / r.close, "dv20": r.dv20_before,
                       "f1": ss.in_grid(grid, r.prev_raw_close, r.dv20_before), "f2": bool(etb_today.get(r.symbol))})

    cal = spy.index[spy.index >= FIRST]
    spy_eq = spy.loc[cal] / spy.loc[cal].iloc[0] * START_EQUITY
    spy_s = v2.stats(spy_eq, [])

    def pick(gap=v2.GAP, filt="f1"):
        return [e for e in events if e["day"] >= FIRST and e["gap"] >= gap and (filt is None or e[filt])]

    runs = {"main": simulate(pick(), cal), "double": simulate(pick(), cal, cost_mult=2.0),
            "f2": simulate(pick(filt="f2"), cal), "f0": simulate(pick(filt=None), cal),
            "f0_stop": simulate(pick(filt=None), cal, stop=STOP), "stop": simulate(pick(), cal, stop=STOP),
            "g50": simulate(pick(0.50), cal), "g100": simulate(pick(1.00), cal),
            "same_day": simulate(pick(), cal, extra=0), "hold2": simulate(pick(), cal, extra=2),
            "f0_same_day": simulate(pick(filt=None), cal, extra=0),
            "f0_free": simulate(pick(filt=None), cal, cost_mult=0.0)}
    st = {k: _stats(*v) for k, v in runs.items()}

    live_days = spy.index[-LIVE_DAYS:]
    live = [e for e in events if e["day"] in live_days and e["gap"] >= v2.GAP]
    live_etb = sum(e["f2"] for e in live)
    share = live_etb / len(live) if live else 0.0
    m = st["main"]
    worst = min((t.net for t in runs["main"][1]), default=0.0)
    bars = [
        ("Data check", not problems, "no problems" if not problems else "; ".join(problems[:5])),
        ("Enough trades (≥ 30)", m.trades >= 30, f"{m.trades} trades"),
        ("Makes money after costs", m.total > 0 and m.avg > 0, f"total {m.total:+.1%}, average trade {m.avg:+.2%}"),
        ("Worth the risk (Sharpe ≥ SPY)", m.sharpe >= spy_s.sharpe, f"{m.sharpe:.2f} vs {spy_s.sharpe:.2f}"),
        ("Survivable (worst drop ≤ 25%)", m.max_dd <= 0.25, f"{m.max_dd:.1%}"),
        ("Survives double costs", st["double"].total > 0, f"total {st['double'].total:+.1%}"),
        ("Not fragile (+50% and +100% gaps average > 0)", st["g50"].avg > 0 and st["g100"].avg > 0,
         f"+50%: {st['g50'].avg:+.2%} ({st['g50'].trades}); +100%: {st['g100'].avg:+.2%} ({st['g100'].trades})"),
        ("Squeeze-proof enough (worst trade ≥ −100% of position)", worst >= -1.0, f"worst {worst:+.0%}"),
        ("Actually placeable (≥ 25% of last 120 days' signals easy to borrow today, ≥ 5)",
         share >= 0.25 and live_etb >= 5, f"{live_etb} of {len(live)} ({share:.0%})"),
    ]
    go = all(ok for _, ok, _ in bars)

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(10, 5))
    for k, label, c in (("main", "Shortable estimate F1 (primary)", "#2563eb"), ("f0", "No shortable filter (F0)", "#9ca3af"),
                        ("f2", "Easy to borrow today (F2)", "#f59e0b")):
        ax.plot(runs[k][0], label=label, color=c)
    ax.plot(spy_eq, label="SPY buy-and-hold", color="#111827")
    ax.set_yscale("log")
    ax.set_ylabel("Account value ($, log scale)")
    ax.set_title("Checklist stocks: short at the open, cover when green (up to 3 extra days)")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8)
    fig.tight_layout()
    (out_dir / "img").mkdir(parents=True, exist_ok=True)
    fig.savefig(out_dir / "img" / "spike_short_v3_equity.png", dpi=120)
    plt.close(fig)
    csv = out_dir / "spike_short_v3_trades.csv"
    pd.DataFrame([{**t.__dict__, "variant": k} for k in ("main", "f0") for t in runs[k][1]]).to_csv(
        csv, index=False, float_format="%.6g")

    labels = {"main": "**F1 shortable estimate, hold up to 3 more days if red (primary)**",
              "same_day": "F1, always cover same day", "hold2": "F1, hold up to 2 more days",
              "f0_same_day": "F0, always cover same day", "double": "Primary, double costs",
              "stop": "F1, 20% stop", "f2": "Easy to borrow today (F2)", "f0": "No shortable filter (F0)",
              "f0_stop": "F0, 20% stop", "f0_free": "F0, no costs (reference)", "g50": "F1, gap ≥ +50%",
              "g100": "F1, gap ≥ +100%"}
    md = [f"# Checklist Stocks: Short at the Open, Cover When Green — {today}", "",
          "*Paper research only; not investment advice. Rules: "
          "[spike_short_v3_preregistration.md](spike_short_v3_preregistration.md). **Post-hoc follow-up**: this "
          "version was suggested by data already seen in the checklist test, so a pass would be weak evidence.*", "",
          f"## Verdict: **{'GO' if go else 'NO-GO'}** ({sum(ok for _, ok, _ in bars)} of {len(bars)} bars passed)", "",
          "| Bar | Result | Detail |", "|---|---|---|"]
    md += [f"| {i} · {n} | {'PASS' if ok else '**FAIL**'} | {d} |" for i, (n, ok, d) in enumerate(bars, 1)]
    md += ["", f"## Results ({cal[0].date()} to {cal[-1].date()}; 7.5% of the account per trade, at most 3 a day)", "",
           f"Signals skipped for the short-sale rule at the open: {ssr}. Overnight holds pay a 20%/yr borrow fee; "
           "real fees on hard-to-borrow stocks are often much higher.", "",
           "| | Trades | Winners | Avg trade | Total | Annual | Worst drop | Sharpe | Stopped |",
           "|---|---|---|---|---|---|---|---|---|"]
    md += [f"| {labels[k]} | {st[k].trades} | {st[k].win:.0%} | {st[k].avg:+.2%} | {st[k].total:+.1%} | "
           f"{st[k].cagr:+.1%} | {st[k].max_dd:.1%} | {st[k].sharpe:.2f} | {st[k].stops:.0%} |" for k in labels]
    md += [f"| SPY buy-and-hold | — | — | — | {spy_s.total:+.1%} | {spy_s.cagr:+.1%} | {spy_s.max_dd:.1%} | "
           f"{spy_s.sharpe:.2f} | — |", "", "**F0 year by year (account return)**", "", "| Year | Trades | F0 | SPY |",
           "|---|---|---|---|"]
    f0eq = runs["f0"][0]
    spy_r = spy_eq.pct_change().fillna(0)
    for y in sorted(set(f0eq.index.year)):
        yr = f0eq[f0eq.index.year == y]
        prev = f0eq[f0eq.index.year < y]
        base = prev.iloc[-1] if len(prev) else START_EQUITY
        n = sum(1 for t in runs["f0"][1] if t.exit_day.year == y)
        md.append(f"| {y} | {n} | {yr.iloc[-1] / base - 1:+.1%} | {(1 + spy_r[spy_r.index.year == y]).prod() - 1:+.1%} |")
    tr = sorted(runs["f0"][1], key=lambda t: t.net)
    md += ["", "![Account values](img/spike_short_v3_equity.png)", "",
           "## F0: the 10 worst trades (the squeezes)", "", "| Entry day | Exit day | Symbol | Gap | Entry | Exit | After costs |",
           "|---|---|---|---|---|---|---|"]
    md += [f"| {t.day.date()} | {t.exit_day.date()} | {t.symbol} | {t.gap:+.0%} | ${t.entry:.2f} | ${t.exit:.2f} | "
           f"{t.net:+.0%} |" for t in tr[:10]]
    md += ["", f"## Live check: last {LIVE_DAYS} trading days", "",
           f"{len(live)} signals; **{live_etb}** easy to borrow on Alpaca today.", "",
           f"Every trade: [{csv.name}]({csv.name}).", ""]
    path = out_dir / f"spike_short_v3_backtest_{today}.md"
    path.write_text("\n".join(md))
    return path, go
