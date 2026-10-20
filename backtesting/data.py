"""Point-in-time market data for backtests, with no look-ahead.

Option quotes: Databento CBBO-1m files (data/storage/opra/<day>.parquet). A record
stamped T is the national best bid/offer as of T, so it is usable at T.
Underlying: SPY one-minute bars (history.db), converted from dividend-adjusted to real traded prices. A bar stamped S covers S..S+1min, so its
close is only known at S+1min; the spot at time T is the close of the bar stamped T-1min.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from core.config import PROJECT_ROOT
from data.collectors.historical import raw_factor
from pricing.snapshot import SnapOption, Snapshot

EASTERN = ZoneInfo("America/New_York")
OCC = re.compile(r"^SPY(\d{2})(\d{2})(\d{2})([CP])(\d{8})$")


def parse_symbol(sym: str) -> tuple[date, str, float]:
    m = OCC.match(sym)
    if not m:
        raise ValueError(f"not a SPY option symbol: {sym}")
    yy, mm, dd, cp, k = m.groups()
    return date(2000 + int(yy), int(mm), int(dd)), ("call" if cp == "C" else "put"), int(k) / 1000


@dataclass
class DayData:
    day: date
    quotes: dict[datetime, pd.DataFrame]       # minute -> rows (symbol, bid, ask, bid_size, ask_size)
    spot_bars: dict[datetime, float]           # bar START (UTC) -> close
    meta: dict[str, tuple[date, str, float]]   # symbol -> (expiration, type, strike)

    def minutes(self) -> list[datetime]:
        return sorted(self.quotes)

    def spot_at(self, t: datetime) -> float | None:
        """Last SPY price known at t: the close of the bar that ended at or before t."""
        for back in range(1, 6):
            v = self.spot_bars.get(t - timedelta(minutes=back))
            if v is not None:
                return v
        return None

    def quotes_at(self, t: datetime) -> pd.DataFrame | None:
        return self.quotes.get(t)

    def snapshot(self, t: datetime) -> Snapshot | None:
        rows, spot = self.quotes.get(t), self.spot_at(t)
        if rows is None or spot is None:
            return None
        opts = []
        for sym, bid, ask, bs, as_ in rows[["symbol", "bid", "ask", "bid_size", "ask_size"]].itertuples(index=False):
            if not (bid > 0 and ask > 0 and ask >= bid):
                continue
            exp, kind, k = self.meta[sym]
            opts.append(SnapOption(sym, kind, k, exp, float(bid), float(ask), float(bs), float(as_), t, t))
        return Snapshot(t, "SPY", spot, t, opts)


def available_days(opra_dir: Path | None = None) -> list[date]:
    d = opra_dir or PROJECT_ROOT / "data" / "storage" / "opra"
    return sorted(date.fromisoformat(p.stem) for p in d.glob("*.parquet"))


def load_day(day: date, history: sqlite3.Connection, opra_dir: Path | None = None) -> DayData:
    path = (opra_dir or PROJECT_ROOT / "data" / "storage" / "opra") / f"{day.isoformat()}.parquet"
    df = pd.read_parquet(path)
    df["ts"] = pd.to_datetime(df["ts"], utc=True)
    open_t = datetime.combine(day, time(9, 30), EASTERN).astimezone(timezone.utc)
    close_t = datetime.combine(day, time(16, 0), EASTERN).astimezone(timezone.utc)
    df = df[(df["ts"] > open_t) & (df["ts"] <= close_t)]
    quotes = {ts.to_pydatetime(): g for ts, g in df.groupby("ts")}
    meta = {s: parse_symbol(s) for s in df["symbol"].unique()}
    rows = history.execute("SELECT ts, close FROM minute_bars WHERE symbol='SPY' AND ts>=? AND ts<?",
                           (open_t.isoformat(), close_t.isoformat())).fetchall()
    f = raw_factor(history, day)                  # stored bars are dividend-adjusted; strikes are real prices
    spot = {datetime.fromisoformat(ts).astimezone(timezone.utc): float(c) * f for ts, c in rows}
    return DayData(day, quotes, spot, meta)
