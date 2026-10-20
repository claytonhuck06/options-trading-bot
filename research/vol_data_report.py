"""Phase 1 data quality report for the volatility strategy. Coverage and data checks only.

By design this never computes option P&L, realized-vs-implied comparisons, or anything else that
would let results leak into the pre-registered rules (docs/research/iron_condor_preregistration.md).
"""

from __future__ import annotations

import sqlite3
from collections import Counter
from datetime import date
from pathlib import Path

# Calendar-day DTE targets from the pre-registration (condor 30/45/60, straddle 14/30), ±7 day tolerance.
DTE_TARGETS = (14, 30, 45, 60)
DTE_TOLERANCE = 7


def spy_trading_days(history: sqlite3.Connection, start: str) -> list[str]:
    rows = history.execute("SELECT DISTINCT substr(ts, 1, 10) FROM minute_bars WHERE symbol='SPY' AND ts>=? "
                           "AND substr(ts, 12, 2) BETWEEN '14' AND '20' ORDER BY 1", (start,))
    return [r[0] for r in rows]


def _index_section(vol: sqlite3.Connection, spy_days: set[str]) -> list[str]:
    out = ["## Volatility indices (CBOE, free)", "",
           "| Index | First day | Last day | Days | SPY trading days since 2016 missing | Suspicious values |",
           "|---|---|---|---|---|---|"]
    for sym, first, last, n in vol.execute(
            "SELECT symbol, MIN(day), MAX(day), COUNT(*) FROM index_daily GROUP BY symbol ORDER BY symbol"):
        days = {r[0] for r in vol.execute("SELECT day FROM index_daily WHERE symbol=?", (sym,))}
        missing = sorted(d for d in spy_days if d >= first and d not in days)
        closes = vol.execute("SELECT day, close FROM index_daily WHERE symbol=? ORDER BY day", (sym,)).fetchall()
        odd = [d for d, c in closes if c < 5 or c > 150]
        jumps = [closes[i][0] for i in range(1, len(closes)) if closes[i][1] / closes[i - 1][1] > 2.5]
        flag = ", ".join(filter(None, [f"{len(odd)} outside 5–150" if odd else "",
                                       f"{len(jumps)} one-day jumps >150%: {', '.join(jumps[:5])}" if jumps else ""]))
        sample = f" ({', '.join(missing[:5])}{'…' if len(missing) > 5 else ''})" if missing else ""
        out.append(f"| {sym} | {first} | {last} | {n:,} | {len(missing)}{sample} | {flag or 'none'} |")
    return out + [""]


def _events_section(vol: sqlite3.Connection) -> list[str]:
    out = ["## Event calendar", "",
           "Only **scheduled** FOMC meetings (and the scheduled-then-cancelled March 2020 one) go in the event "
           "filter, because unscheduled meetings weren't known in advance. Unscheduled meetings and notation "
           "votes are stored for reference only.", "",
           "| Year | FOMC scheduled | FOMC cancelled | FOMC unscheduled | FOMC notation votes | CPI releases |",
           "|---|---|---|---|---|---|"]
    counts = Counter((d[:4], e, k) for d, e, k in vol.execute("SELECT day, event, kind FROM events"))
    years = sorted({y for y, _, _ in counts})
    for y in years:
        out.append(f"| {y} | {counts[(y, 'FOMC', 'scheduled')]} | {counts[(y, 'FOMC', 'cancelled')]} | "
                   f"{counts[(y, 'FOMC', 'unscheduled')]} | {counts[(y, 'FOMC', 'notation')]} | "
                   f"{counts[(y, 'CPI', 'scheduled')]} |")
    out += ["", "Expected: 8 scheduled FOMC meetings a year (2020: 7 plus the cancelled March meeting); "
            "12 CPI releases a year (2025: 11, because October 2025 CPI was never published during the government "
            "shutdown). CPI dates come from the BLS release archive and all appear in FRED/ALFRED's release history.",
            ""]
    return out


def _chain_section(vol: sqlite3.Connection, spy_days: set[str]) -> list[str]:
    out = ["## SPY end-of-day option chains (OptionsDX)", ""]
    n_files = vol.execute("SELECT COUNT(*) FROM loaded_files").fetchone()[0]
    if not n_files:
        return out + ["**Not loaded yet.** The free files need a free checkout on optionsdx.com, which the owner does "
                      "by hand. Then: `python main.py vol-data load --path <folder>` and re-run this report.", ""]
    first, last, n_days = vol.execute(
        "SELECT MIN(quote_date), MAX(quote_date), COUNT(DISTINCT quote_date) FROM eod_chain").fetchone()
    in_range = sorted(d for d in spy_days if first <= d <= last)
    have = {r[0] for r in vol.execute("SELECT DISTINCT quote_date FROM eod_chain")}
    missing = [d for d in in_range if d not in have]
    total, = vol.execute("SELECT COUNT(*) FROM eod_chain").fetchone()
    bad = vol.execute("""SELECT
        SUM(c_bid IS NULL OR c_ask IS NULL), SUM(p_bid IS NULL OR p_ask IS NULL),
        SUM(c_bid > c_ask), SUM(p_bid > p_ask), SUM(c_bid = c_ask AND c_bid > 0), SUM(p_bid = p_ask AND p_bid > 0),
        SUM(c_delta IS NULL), SUM(p_delta IS NULL), SUM(c_iv IS NULL OR c_iv <= 0), SUM(p_iv IS NULL OR p_iv <= 0)
        FROM eod_chain""").fetchone()
    pct = [f"{100 * (b or 0) / total:.2f}%" for b in bad]
    times = vol.execute("SELECT quote_time_hours, COUNT(DISTINCT quote_date) FROM eod_chain "
                        "GROUP BY quote_time_hours ORDER BY 2 DESC LIMIT 5").fetchall()
    out += [f"- Coverage: **{first} to {last}**, {n_days:,} quote days from {n_files} files, {total:,} strike rows "
            f"(kept: ≤ 100 DTE, strikes within ±25% of SPY).",
            f"- SPY trading days in that range with no chain: **{len(missing)}**"
            + (f" ({', '.join(missing[:10])}{'…' if len(missing) > 10 else ''})" if missing else ""),
            f"- Snapshot time (hours, ET) by number of days: "
            + ", ".join(f"{t}: {n:,}" for t, n in times), "",
            "| Check | Calls | Puts |", "|---|---|---|",
            f"| Missing bid or ask | {pct[0]} | {pct[1]} |",
            f"| Crossed (bid > ask) | {pct[2]} | {pct[3]} |",
            f"| Locked (bid = ask) | {pct[4]} | {pct[5]} |",
            f"| Missing delta | {pct[6]} | {pct[7]} |",
            f"| Missing or zero IV | {pct[8]} | {pct[9]} |", ""]
    # Can each pre-registered DTE target be found within tolerance, and do the strikes reach 10 delta?
    out += ["| Target DTE | Days with an expiry within ±7 days | Days where the chain reaches a 10-delta put and call |",
            "|---|---|---|"]
    for target in DTE_TARGETS:
        ok_exp = ok_delta = 0
        for (d,) in vol.execute("SELECT DISTINCT quote_date FROM eod_chain"):
            exp = vol.execute("SELECT expiry FROM eod_chain WHERE quote_date=? AND ABS(dte-?)<=? "
                              "GROUP BY expiry ORDER BY ABS(MAX(dte)-?), expiry LIMIT 1",
                              (d, target, DTE_TOLERANCE, target)).fetchone()
            if not exp:
                continue
            ok_exp += 1
            lo, hi = vol.execute("SELECT MAX(p_delta <= -0.10 AND p_bid > 0), MAX(c_delta >= 0 AND c_delta <= 0.10 "
                                 "AND c_bid > 0) FROM eod_chain WHERE quote_date=? AND expiry=?", (d, exp[0])).fetchone()
            ok_delta += bool(lo) and bool(hi)
        out.append(f"| {target} | {ok_exp:,} of {n_days:,} | {ok_delta:,} of {n_days:,} |")
    return out + [""]


def build_report(vol: sqlite3.Connection, history: sqlite3.Connection, today: date) -> str:
    spy_days = set(spy_trading_days(history, "2016-01-01"))
    lines = [f"# Volatility Strategy — Phase 1 Data Report ({today.isoformat()})", "",
             "Coverage and data checks only. No trade results are computed in Phase 1, so nothing here can "
             "influence the pre-registered rules.", "",
             f"SPY trading days in the stored minute history since 2016: **{len(spy_days):,}**.", ""]
    lines += _index_section(vol, spy_days) + _events_section(vol) + _chain_section(vol, spy_days)
    return "\n".join(lines) + "\n"


def write_report(vol: sqlite3.Connection, history: sqlite3.Connection, out_dir: Path, today: date) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"vol_data_report_{today.isoformat()}.md"
    path.write_text(build_report(vol, history, today))
    return path
