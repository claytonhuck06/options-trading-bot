"""Download historical SPY one-minute bars (free consolidated SIP feed, back to 2016).

Used by the risk engine to calibrate stress scenarios on real market moves and
to replay positions through real intraday paths. Stored in a separate SQLite
file so the live collector database stays small. Re-running only fetches
months not already stored (the current month is always refreshed).
"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from alpaca.data.enums import Adjustment, DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

from core.logging_setup import get_logger
from core.secrets import AlpacaCredentials

log = get_logger("history")

SCHEMA = """
CREATE TABLE IF NOT EXISTS minute_bars (
    symbol TEXT NOT NULL,
    ts TEXT NOT NULL,                 -- bar start, UTC ISO
    open REAL, high REAL, low REAL, close REAL,
    volume REAL, vwap REAL,
    PRIMARY KEY (symbol, ts)
);
CREATE TABLE IF NOT EXISTS fetched_months (
    symbol TEXT NOT NULL, month TEXT NOT NULL, bars INTEGER, fetched_at TEXT,
    PRIMARY KEY (symbol, month)
);
"""


def open_history(path: str | Path) -> sqlite3.Connection:
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    return conn


def _months(start: date, end: date):
    d = date(start.year, start.month, 1)
    while d <= end:
        nxt = date(d.year + (d.month == 12), d.month % 12 + 1, 1)
        yield d, nxt
        d = nxt


def download_minute_bars(creds: AlpacaCredentials, conn: sqlite3.Connection, symbol: str = "SPY",
                         start: date = date(2016, 1, 1), end: date | None = None) -> int:
    client = StockHistoricalDataClient(creds.key_id, creds.secret_key)
    end = end or datetime.now(timezone.utc).date()
    done = {m for (m,) in conn.execute("SELECT month FROM fetched_months WHERE symbol=?", (symbol,))}
    current = date(end.year, end.month, 1).isoformat()[:7]
    total = 0
    for m0, m1 in _months(start, end):
        key = m0.isoformat()[:7]
        if key in done and key != current:
            continue
        req = StockBarsRequest(
            symbol_or_symbols=symbol, timeframe=TimeFrame.Minute, feed=DataFeed.SIP, adjustment=Adjustment.ALL,
            start=datetime(m0.year, m0.month, m0.day, tzinfo=timezone.utc),
            # The free plan cannot query the most recent 15 minutes of SIP data.
            end=min(datetime(m1.year, m1.month, m1.day, tzinfo=timezone.utc), datetime.now(timezone.utc) - timedelta(minutes=16)),
        )
        bars = client.get_stock_bars(req).data.get(symbol, [])
        conn.executemany(
            "INSERT OR REPLACE INTO minute_bars VALUES (?,?,?,?,?,?,?,?)",
            [(symbol, b.timestamp.astimezone(timezone.utc).isoformat(), b.open, b.high, b.low, b.close, b.volume, b.vwap) for b in bars],
        )
        conn.execute("INSERT OR REPLACE INTO fetched_months VALUES (?,?,?,?)",
                     (symbol, key, len(bars), datetime.now(timezone.utc).isoformat()))
        conn.commit()
        total += len(bars)
        log.info("history %s %s: %d bars", symbol, key, len(bars))
    return total
