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
from zoneinfo import ZoneInfo

from alpaca.data.enums import Adjustment, DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame

from core.logging_setup import get_logger
from core.secrets import AlpacaCredentials

log = get_logger("history")
EASTERN = ZoneInfo("America/New_York")

SCHEMA = """
CREATE TABLE IF NOT EXISTS minute_bars (
    symbol TEXT NOT NULL,
    ts TEXT NOT NULL,                 -- bar start, UTC ISO
    open REAL, high REAL, low REAL, close REAL,
    volume REAL, vwap REAL,
    PRIMARY KEY (symbol, ts)
);
-- minute_bars are dividend-adjusted (smooth returns for stress tests). Option strikes are in real prices,
-- so anything comparing SPY with strikes multiplies by this per-day factor: raw close / adjusted close.
CREATE TABLE IF NOT EXISTS raw_factors (
    symbol TEXT NOT NULL, day TEXT NOT NULL, factor REAL NOT NULL,
    PRIMARY KEY (symbol, day)
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


def download_raw_factors(creds: AlpacaCredentials, conn: sqlite3.Connection, symbol: str = "SPY",
                         start: date = date(2016, 1, 1), end: date | None = None) -> int:
    """Per-day raw/adjusted price ratio from daily bars fetched both ways (two requests; free feed)."""
    client = StockHistoricalDataClient(creds.key_id, creds.secret_key)
    end_dt = datetime.now(timezone.utc) - timedelta(minutes=16) if end is None else \
        datetime(end.year, end.month, end.day, tzinfo=timezone.utc) + timedelta(days=1)
    closes = {}
    for adj in (Adjustment.RAW, Adjustment.ALL):
        req = StockBarsRequest(symbol_or_symbols=symbol, timeframe=TimeFrame.Day, feed=DataFeed.SIP, adjustment=adj,
                               start=datetime(start.year, start.month, start.day, tzinfo=timezone.utc), end=end_dt)
        closes[adj] = {b.timestamp.astimezone(EASTERN).date().isoformat(): b.close
                       for b in client.get_stock_bars(req).data.get(symbol, [])}
    rows = [(symbol, d, closes[Adjustment.RAW][d] / c) for d, c in closes[Adjustment.ALL].items()
            if c and d in closes[Adjustment.RAW]]
    conn.executemany("INSERT OR REPLACE INTO raw_factors VALUES (?,?,?)", rows)
    conn.commit()
    log.info("raw price factors %s: %d days", symbol, len(rows))
    return len(rows)


def raw_factor(conn: sqlite3.Connection, day: date, symbol: str = "SPY") -> float:
    """Multiply an adjusted price from `day` by this to get the real traded price (1.0 if unknown)."""
    try:
        row = conn.execute("SELECT factor FROM raw_factors WHERE symbol=? AND day=?", (symbol, day.isoformat())).fetchone()
    except sqlite3.OperationalError:                  # table not created yet
        return 1.0
    return float(row[0]) if row else 1.0
