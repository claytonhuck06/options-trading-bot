"""Phase 2 report: what the condor builder does on every historical day, for each entry setting.

Entry-side only: how often a condor can be built, why it can't, credits and sizes at entry. No exits,
no marks, no P&L (those are Phase 3), so nothing here can feed back into the pre-registered rules.

Spot = the vendor's UNDERLYING_LAST, taken at the same 16:00 snapshot as the option quotes (it matches
our own SPY close to a median 0.006%). Rates = FRED 1- and 3-month Treasury yields from before the day.
"""

from __future__ import annotations

import math
import sqlite3
from bisect import bisect_left
from collections import Counter, defaultdict
from datetime import date
from itertools import product
from pathlib import Path
from statistics import median

from pricing.rates import fetch_fred, to_continuous
from strategies.iron_condor import ChainRow, CondorParams, Decision, build_condor

GRID_DTE = (30, 45, 60)
GRID_DELTA = (0.10, 0.16, 0.20)
GRID_WIDTH = (5.0, 10.0)
START, END = "2016-01-01", "2023-12-31"
MAX_DTE_LOADED = 70
RATE_SERIES = {"DGS1MO": 1 / 12, "DGS3MO": 0.25}
EXAMPLE_DAYS = ("2018-02-05", "2020-03-16", "2022-06-13", "2023-07-03")


def entry_grid() -> list[CondorParams]:
    return [CondorParams(d, x, w) for d, x, w in product(GRID_DTE, GRID_DELTA, GRID_WIDTH)]


# --- Inputs ------------------------------------------------------------------------------------

def ensure_rates(vol: sqlite3.Connection) -> None:
    vol.execute("CREATE TABLE IF NOT EXISTS rates (series TEXT NOT NULL, day TEXT NOT NULL, pct REAL NOT NULL, "
                "PRIMARY KEY (series, day))")
    have = vol.execute("SELECT COUNT(*) FROM rates WHERE day < '2016-01-01'").fetchone()[0]
    if not have:
        for series in RATE_SERIES:
            vol.executemany("INSERT OR REPLACE INTO rates VALUES (?,?,?)",
                            [(series, d.isoformat(), pct) for d, pct in fetch_fred(series)])
        vol.commit()


class RateLookup:
    """Continuously compounded rate for a maturity, from observations strictly before the day (no look-ahead)."""

    def __init__(self, vol: sqlite3.Connection):
        self.obs = {}
        for series in RATE_SERIES:
            rows = vol.execute("SELECT day, pct FROM rates WHERE series=? ORDER BY day", (series,)).fetchall()
            self.obs[series] = ([r[0] for r in rows], [r[1] for r in rows])

    def _before(self, series: str, day: date) -> float | None:
        days, vals = self.obs[series]
        i = bisect_left(days, day.isoformat()) - 1
        return vals[i] if i >= 0 else None

    def rate(self, day: date, years: float) -> float:
        pts = [(m, self._before(s, day)) for s, m in RATE_SERIES.items()]
        pts = [(m, to_continuous(v)) for m, v in pts if v is not None]
        if not pts:
            return 0.0
        pts.sort()
        if years <= pts[0][0] or len(pts) == 1:
            return pts[0][1]
        if years >= pts[-1][0]:
            return pts[-1][1]
        (t0, r0), (t1, r1) = pts
        return r0 + (years - t0) / (t1 - t0) * (r1 - r0)


def load_day(vol: sqlite3.Connection, day: str) -> tuple[float | None, dict[date, list[ChainRow]]]:
    chains: dict[date, list[ChainRow]] = defaultdict(list)
    spot = None
    for (und, exp, k, cb, ca, civ, cd, pb, pa, piv, pd_) in vol.execute(
            "SELECT underlying, expiry, strike, c_bid, c_ask, c_iv, c_delta, p_bid, p_ask, p_iv, p_delta "
            "FROM eod_chain WHERE quote_date=? AND dte<=? ORDER BY expiry, strike", (day, MAX_DTE_LOADED)):
        spot = und
        chains[date.fromisoformat(exp)].append(ChainRow(k, cb, ca, civ, cd, pb, pa, piv, pd_))
    return spot, dict(chains)


def trading_days_and_events(history: sqlite3.Connection, vol: sqlite3.Connection) -> tuple[list[date], set[date]]:
    from research.vol_data_report import spy_trading_days
    days = [date.fromisoformat(d) for d in spy_trading_days(history, "2015-12-01")]
    events = {date.fromisoformat(d) for (d,) in vol.execute(
        "SELECT day FROM events WHERE kind IN ('scheduled','cancelled') AND event IN ('FOMC','CPI')")}
    return days, events


# --- Run and report ----------------------------------------------------------------------------

def run_builder(vol: sqlite3.Connection, history: sqlite3.Connection, start: str = START, end: str = END
                ) -> tuple[dict[CondorParams, list[Decision]], int]:
    ensure_rates(vol)
    rates = RateLookup(vol)
    tdays, events = trading_days_and_events(history, vol)
    grid = entry_grid()
    out: dict[CondorParams, list[Decision]] = {p: [] for p in grid}
    chain_days = {d for (d,) in vol.execute(
        "SELECT DISTINCT quote_date FROM eod_chain WHERE quote_date>=? AND quote_date<=?", (start, end))}
    n_days = 0
    for day in tdays:
        if not (start <= day.isoformat() <= end):
            continue
        n_days += 1
        if day.isoformat() not in chain_days:
            for p in grid:
                out[p].append(Decision(day, p, None, "no option data for this day", "no_data"))
            continue
        spot, chains = load_day(vol, day.isoformat())
        cache: dict = {}
        for p in grid:
            r = rates.rate(day, p.target_dte / 365.0)
            out[p].append(build_condor(day, p, spot, chains, r, tdays, events, cache))
    return out, n_days


REASON_ORDER = ["built", "no_data", "no_expiry", "no_short_strike", "strikes_overlap", "no_wing_strike", "bad_quote",
                "credit_too_small", "event_ahead", "too_small"]
REASON_TEXT = {"built": "Condor built", "no_data": "No option data", "no_expiry": "No expiry near target",
               "no_short_strike": "No usable delta", "strikes_overlap": "Short strikes overlap",
               "no_wing_strike": "Wing strike not listed", "bad_quote": "Bad quote on a leg",
               "credit_too_small": "Credit under 25% of width", "event_ahead": "FOMC/CPI within 5 days",
               "too_small": "Too big for 1% risk"}


def _describe(d: Decision) -> str:
    if d.condor is None:
        return d.reason
    c = d.condor
    return (f"{c.expiry} ({c.dte} days): buy {c.long_put.strike:g}P / sell {c.short_put.strike:g}P "
            f"(Δ {c.short_put.delta:+.2f}) / sell {c.short_call.strike:g}C (Δ {c.short_call.delta:+.2f}) / "
            f"buy {c.long_call.strike:g}C, credit ${c.credit:.2f}, {c.contracts} contracts, "
            f"planned max loss ${c.planned_max_loss:,.0f}")


def build_report(results: dict[CondorParams, list[Decision]], n_days: int, today: date) -> str:
    L = [f"# Iron Condor Builder Report ({today.isoformat()})", "",
         "Phase 2 of the volatility strategy: what the entry rules (pre-registration section C1) produce on every "
         f"SPY trading day from {START[:4]} to {END[:4]} ({n_days:,} days), for each of the 18 entry settings. "
         "Entry side only. No exits or profit/loss are computed until Phase 3.", "",
         "The once-a-week and 3-open limits depend on open positions and are applied in the backtest, so the counts "
         "below are *days a condor could be entered*, not trades.", "",
         "## Outcome by entry setting", "",
         "| DTE | Δ | Width | " + " | ".join(REASON_TEXT[r] for r in REASON_ORDER) +
         " | Median credit | Credit ÷ width | Median contracts | Credit filter blocked |",
         "|" + "---|" * (3 + len(REASON_ORDER) + 4)]
    for p, decs in results.items():
        cnt = Counter(d.code for d in decs)
        built = [d.condor for d in decs if d.condor]
        credits = [c.credit for c in built]
        reached_credit = cnt["built"] + cnt["credit_too_small"] + cnt["event_ahead"] + cnt["too_small"]
        blocked = cnt["credit_too_small"] / reached_credit if reached_credit else math.nan
        L.append(f"| {p.target_dte} | {p.short_delta:.2f} | ${p.width:g} | "
                 + " | ".join(f"{cnt[r]:,}" for r in REASON_ORDER)
                 + (f" | ${median(credits):.2f} | {median(credits) / p.width:.0%} | {median(c.contracts for c in built)}"
                    if built else " | – | – | –")
                 + f" | {blocked:.0%}{' ⚠️' if blocked > 0.5 else ''} |")
    flagged = [p for p, decs in results.items()
               if (lambda c: (c["credit_too_small"] / max(1, c["built"] + c["credit_too_small"] + c["event_ahead"]
                                                          + c["too_small"])) > 0.5)(Counter(d.code for d in decs))]
    L += ["", "Credit filter blocked = share of days that got as far as the credit check (C1.7) and failed it. "
          "The pre-registration says this is reported, not changed, if it exceeds 50% (⚠️)."
          + (f" **{len(flagged)} of 18 settings exceed 50%.**" if flagged else ""), ""]
    fallback = sum(d.details.get("fallback_deltas", 0) for decs in results.values() for d in decs if d.condor)
    L += [f"Vendor deltas used where our solver refused a quote (counted on built days, all legs priced that day): "
          f"{fallback:,}.", "", "## Examples (16-delta, $5 wings)", ""]
    for p in results:
        if p.short_delta != 0.16 or p.width != 5.0:
            continue
        for d in results[p]:
            if d.day.isoformat() in EXAMPLE_DAYS:
                L.append(f"- **{d.day} · {p.target_dte} DTE:** {_describe(d)}")
    return "\n".join(L) + "\n"


def write_report(vol: sqlite3.Connection, history: sqlite3.Connection, out_dir: Path, today: date) -> Path:
    results, n_days = run_builder(vol, history)
    path = out_dir / f"condor_build_report_{today.isoformat()}.md"
    path.write_text(build_report(results, n_days, today))
    return path
