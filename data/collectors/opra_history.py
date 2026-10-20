"""Download historical SPY option bid/ask (Databento OPRA, minute snapshots of the national best bid/offer).

Only the contracts the strategy could trade are requested: for each trading day, expiries
7–30 days out with strikes within ±3.5% of the PREVIOUS day's close (known before the open,
so the selection itself uses no future information). Work goes one week at a time, newest
first, and every request passes through the spending guard.

Storage: one compressed Parquet file per trading day in data/storage/opra/, plus a small
SQLite index of which days are done.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from core.config import PROJECT_ROOT, DatabentoSettings
from core.logging_setup import get_logger
from data.collectors.databento_guard import BudgetExceeded, GuardedDatabento

log = get_logger("opra_history")
EASTERN = ZoneInfo("America/New_York")
DATASET = "OPRA.PILLAR"
SYMBOLS_PER_REQUEST = 50          # smaller requests survive slow server periods; billing is by data size, not request

INDEX = """
CREATE TABLE IF NOT EXISTS opra_days (
    trading_date TEXT PRIMARY KEY,
    contracts INTEGER NOT NULL,
    quotes INTEGER NOT NULL,
    file TEXT NOT NULL,
    downloaded_at TEXT NOT NULL
);
"""


@dataclass
class DownloadReport:
    days_done: int = 0
    days_skipped_existing: int = 0
    stopped_reason: str | None = None


def trading_days(history: sqlite3.Connection, start: date, end: date) -> list[date]:
    """Trading days present in the SPY minute history (regular-hours bars)."""
    rows = history.execute("SELECT DISTINCT substr(ts, 1, 10) FROM minute_bars WHERE symbol='SPY' AND ts>=? AND ts<?",
                           (start.isoformat(), (end + timedelta(days=1)).isoformat())).fetchall()
    days = sorted({date.fromisoformat(r[0]) for r in rows})
    return [d for d in days if d.weekday() < 5]


def previous_close(history: sqlite3.Connection, day: date) -> float | None:
    """SPY's last regular-hours price before `day` opened."""
    cutoff = datetime.combine(day, time(9, 30), EASTERN).astimezone(timezone.utc)
    rows = history.execute("SELECT ts, close FROM minute_bars WHERE symbol='SPY' AND ts<? ORDER BY ts DESC LIMIT 600",
                           (cutoff.isoformat(),)).fetchall()
    for ts, close in rows:
        t = datetime.fromisoformat(ts).astimezone(EASTERN)
        if t.date() < day and t.time() < time(16, 0):
            return float(close)
    return None


def select_contracts(defs: pd.DataFrame, day: date, ref_price: float, cfg: DatabentoSettings) -> list[str]:
    exp = pd.to_datetime(defs["expiration"]).dt.tz_convert(EASTERN).dt.date
    dte = exp.map(lambda e: (e - day).days)
    lo, hi = ref_price * (1 - cfg.strike_band_pct), ref_price * (1 + cfg.strike_band_pct)
    mask = (dte >= cfg.min_days_to_expiry) & (dte <= cfg.max_days_to_expiry) & defs["strike_price"].between(lo, hi) \
        & defs["instrument_class"].isin(["C", "P"])
    return sorted(set(defs.loc[mask, "raw_symbol"]))


class OpraHistory:
    def __init__(self, cfg: DatabentoSettings, guard: GuardedDatabento, history: sqlite3.Connection, out_dir: Path | None = None):
        self.cfg, self.guard, self.history = cfg, guard, history
        self.out_dir = out_dir or PROJECT_ROOT / "data" / "storage" / "opra"
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.index = guard.ledger
        self.index.executescript(INDEX)
        self.index.commit()

    def done_days(self) -> set[date]:
        return {date.fromisoformat(r[0]) for r in self.index.execute("SELECT trading_date FROM opra_days")}

    def download(self, days: list[date]) -> DownloadReport:
        rep = DownloadReport()
        todo = sorted(set(days) - self.done_days())
        rep.days_skipped_existing = len(days) - len(todo)
        for week in reversed(list(_weeks(todo))):                            # newest week first
            try:
                self._download_week(week)
                rep.days_done += len(week)
            except BudgetExceeded as exc:
                rep.stopped_reason = str(exc)
                log.warning("stopped: %s", exc)
                break
        return rep

    def widen(self, days: list[date], band_pct: float, run_budget_usd: float) -> DownloadReport:
        """Add strikes out to ±band_pct on days already downloaded, fetching only contracts not on disk.
        Stops before a week that would take this run past run_budget_usd (the global caps still apply too)."""
        rep = DownloadReport()
        cfg = self.cfg.model_copy(update={"strike_band_pct": band_pct})
        start_spent = self.guard.summary().spent_usd
        last_week_cost = 0.0
        have = sorted(set(days) & self.done_days())
        for week in reversed(list(_weeks(have))):
            used = self.guard.summary().spent_usd - start_spent
            if used + last_week_cost > run_budget_usd:
                rep.stopped_reason = (f"this run's ${run_budget_usd:.2f} ceiling: ${used:.2f} used, next week would "
                                      f"cost about ${last_week_cost:.2f}")
                break
            before = self.guard.summary().spent_usd
            try:
                self._download_week(week, cfg=cfg, merge=True)
            except BudgetExceeded as exc:
                rep.stopped_reason = str(exc)
                break
            except Exception as exc:              # a slow or failed Databento response: skip this week, keep going
                log.warning("week %s: %s — skipped; re-run to retry", week[0], str(exc)[:200])
                rep.stopped_reason = (rep.stopped_reason or "") + f" timeout on week {week[0]};"
                continue
            last_week_cost = self.guard.summary().spent_usd - before
            rep.days_done += len(week)
        return rep

    def _download_week(self, week: list[date], cfg: DatabentoSettings | None = None, merge: bool = False) -> None:
        cfg = cfg or self.cfg
        start, end = week[0].isoformat(), (week[-1] + timedelta(days=1)).isoformat()
        defs = self.guard.get_range(dataset=DATASET, schema="definition", symbols=["SPY.OPT"], stype_in="parent",
                                    start=start, end=end).to_df().reset_index()
        defs["day"] = pd.to_datetime(defs["ts_recv"]).dt.tz_convert(EASTERN).dt.date
        per_day: dict[date, list[str]] = {}
        existing: dict[date, pd.DataFrame] = {}
        for d in week:
            ref = previous_close(self.history, d)
            day_defs = defs[defs["day"] <= d].drop_duplicates("raw_symbol", keep="last")
            per_day[d] = select_contracts(day_defs, d, ref, cfg) if ref else []
            path = self.out_dir / f"{d.isoformat()}.parquet"
            if merge and path.is_file():
                existing[d] = pd.read_parquet(path)
                on_disk = set(existing[d]["symbol"].unique())
                per_day[d] = [s for s in per_day[d] if s.replace(" ", "") not in on_disk]   # only what we lack
        symbols = sorted({s for v in per_day.values() for s in v})
        if not symbols:
            return
        parts = [self.guard.get_range(dataset=DATASET, schema="cbbo-1m", symbols=symbols[i:i + SYMBOLS_PER_REQUEST],
                                      stype_in="raw_symbol", start=start, end=end).to_df().reset_index()
                 for i in range(0, len(symbols), SYMBOLS_PER_REQUEST)]
        q = pd.concat(parts, ignore_index=True)
        q["day"] = q["ts_recv"].dt.tz_convert(EASTERN).dt.date
        for d in week:
            sel = q[(q["day"] == d) & q["symbol"].isin(per_day[d])]
            out = pd.DataFrame({
                "ts": sel["ts_recv"].dt.tz_convert("UTC"), "symbol": sel["symbol"].str.replace(" ", "", regex=False),
                "bid": sel["bid_px_00"], "ask": sel["ask_px_00"], "bid_size": sel["bid_sz_00"], "ask_size": sel["ask_sz_00"],
            })
            path = self.out_dir / f"{d.isoformat()}.parquet"
            if d in existing:
                out = (pd.concat([existing[d], out], ignore_index=True)
                       .drop_duplicates(["ts", "symbol"], keep="first").sort_values(["ts", "symbol"]))
            out.to_parquet(path, index=False, compression="zstd")
            self.index.execute("INSERT OR REPLACE INTO opra_days VALUES (?,?,?,?,?)",
                               (d.isoformat(), out["symbol"].nunique(), len(out),
                                str(path.relative_to(PROJECT_ROOT)) if path.is_relative_to(PROJECT_ROOT) else str(path),
                                datetime.now(timezone.utc).isoformat()))
            self.index.commit()
        log.info("week %s..%s: %d contracts, %d quotes", start, week[-1], len(symbols), len(q))


def _weeks(days: list[date]):
    """Group sorted days into calendar weeks (Mon-Fri)."""
    group, key = [], None
    for d in sorted(days):
        k = d.isocalendar()[:2]
        if key is not None and k != key:
            yield group
            group = []
        group.append(d)
        key = k
    if group:
        yield group
