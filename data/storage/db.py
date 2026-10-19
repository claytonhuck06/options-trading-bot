"""SQLite storage built for historical replay.

Every observation carries the market timestamp (quote_ts) and the local
receipt time (received_at). A quote is stored once per distinct
(symbol, feed, quote_ts); unchanged quotes seen again in later cycles are
counted as repeats rather than stored as fresh observations. To replay,
take each symbol's latest quote with received_at <= T.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

from data.models import OptionContract, OptionQuote, QualityResult, UnderlyingQuote

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS schema_info (version INTEGER NOT NULL);

CREATE TABLE IF NOT EXISTS collection_cycles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    underlying TEXT NOT NULL,
    feed TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL DEFAULT 'running',     -- running | ok | failed
    quotes_received INTEGER DEFAULT 0,
    quotes_new INTEGER DEFAULT 0,
    quotes_repeat INTEGER DEFAULT 0,
    quotes_unusable INTEGER DEFAULT 0,
    error TEXT
);

CREATE TABLE IF NOT EXISTS underlying_quotes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle_id INTEGER REFERENCES collection_cycles(id),
    symbol TEXT NOT NULL,
    feed TEXT NOT NULL,
    bid REAL, ask REAL, bid_size REAL, ask_size REAL,
    quote_ts TEXT,
    last_price REAL,
    last_trade_ts TEXT,
    received_at TEXT NOT NULL,
    quality_flags TEXT NOT NULL,
    is_usable INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_uq_symbol_time ON underlying_quotes(symbol, received_at);

CREATE TABLE IF NOT EXISTS option_contracts (
    symbol TEXT PRIMARY KEY,
    underlying TEXT NOT NULL,
    root TEXT NOT NULL,
    option_type TEXT NOT NULL,
    style TEXT NOT NULL,
    strike REAL NOT NULL,
    expiration TEXT NOT NULL,
    multiplier INTEGER NOT NULL,
    status TEXT NOT NULL,
    tradable INTEGER NOT NULL,
    open_interest INTEGER,
    open_interest_date TEXT,
    close_price REAL,
    close_price_date TEXT,
    first_seen_at TEXT NOT NULL,
    last_updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_oc_underlying_exp ON option_contracts(underlying, expiration, strike);

CREATE TABLE IF NOT EXISTS option_quotes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    cycle_id INTEGER REFERENCES collection_cycles(id),
    symbol TEXT NOT NULL,
    feed TEXT NOT NULL,
    bid REAL, ask REAL, bid_size REAL, ask_size REAL,
    bid_exchange TEXT, ask_exchange TEXT,
    quote_ts TEXT,
    received_at TEXT NOT NULL,
    last_trade_price REAL, last_trade_size REAL, last_trade_ts TEXT,
    implied_volatility REAL,
    delta REAL, gamma REAL, theta REAL, vega REAL, rho REAL,
    quality_flags TEXT NOT NULL,
    is_usable INTEGER NOT NULL,
    UNIQUE(symbol, feed, quote_ts)
);
CREATE INDEX IF NOT EXISTS ix_oq_symbol_time ON option_quotes(symbol, received_at);
CREATE INDEX IF NOT EXISTS ix_oq_cycle ON option_quotes(cycle_id);

CREATE TABLE IF NOT EXISTS system_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    level TEXT NOT NULL,
    event TEXT NOT NULL,
    detail TEXT
);
"""


def _iso(value: datetime | date | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc).isoformat()
    return value.isoformat()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class MarketDataStore:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        if str(path) != ":memory:":
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(SCHEMA)
        if self.conn.execute("SELECT COUNT(*) FROM schema_info").fetchone()[0] == 0:
            self.conn.execute("INSERT INTO schema_info VALUES (?)", (SCHEMA_VERSION,))
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # --- events -----------------------------------------------------------
    def log_event(self, level: str, event: str, detail: dict | str | None = None) -> None:
        text = detail if isinstance(detail, str) or detail is None else json.dumps(detail, default=str)
        self.conn.execute("INSERT INTO system_events(ts, level, event, detail) VALUES (?,?,?,?)", (_iso(utcnow()), level, event, text))
        self.conn.commit()

    # --- cycles -----------------------------------------------------------
    def start_cycle(self, underlying: str, feed: str) -> int:
        cur = self.conn.execute("INSERT INTO collection_cycles(underlying, feed, started_at) VALUES (?,?,?)", (underlying, feed, _iso(utcnow())))
        self.conn.commit()
        return int(cur.lastrowid)

    def finish_cycle(self, cycle_id: int, status: str, counts: dict[str, int] | None = None, error: str | None = None) -> None:
        c = counts or {}
        self.conn.execute(
            """UPDATE collection_cycles SET finished_at=?, status=?, quotes_received=?, quotes_new=?,
               quotes_repeat=?, quotes_unusable=?, error=? WHERE id=?""",
            (_iso(utcnow()), status, c.get("received", 0), c.get("new", 0), c.get("repeat", 0), c.get("unusable", 0), error, cycle_id),
        )
        self.conn.commit()

    # --- contracts --------------------------------------------------------
    def upsert_contracts(self, contracts: list[OptionContract]) -> int:
        now = _iso(utcnow())
        rows = [
            (c.symbol, c.underlying, c.root, c.option_type, c.style, c.strike, _iso(c.expiration), c.multiplier, c.status,
             int(c.tradable), c.open_interest, _iso(c.open_interest_date), c.close_price, _iso(c.close_price_date), now, now)
            for c in contracts
        ]
        self.conn.executemany(
            """INSERT INTO option_contracts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(symbol) DO UPDATE SET status=excluded.status, tradable=excluded.tradable,
                 open_interest=excluded.open_interest, open_interest_date=excluded.open_interest_date,
                 close_price=excluded.close_price, close_price_date=excluded.close_price_date,
                 last_updated_at=excluded.last_updated_at""",
            rows,
        )
        self.conn.commit()
        return len(rows)

    def known_contract_symbols(self, underlying: str) -> set[str]:
        return {r[0] for r in self.conn.execute("SELECT symbol FROM option_contracts WHERE underlying=?", (underlying,))}

    # --- quotes -----------------------------------------------------------
    def insert_underlying_quote(self, cycle_id: int, q: UnderlyingQuote, quality: QualityResult) -> None:
        self.conn.execute(
            """INSERT INTO underlying_quotes(cycle_id, symbol, feed, bid, ask, bid_size, ask_size, quote_ts, last_price,
               last_trade_ts, received_at, quality_flags, is_usable) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (cycle_id, q.symbol, q.feed, q.bid, q.ask, q.bid_size, q.ask_size, _iso(q.quote_ts), q.last_price,
             _iso(q.last_trade_ts), _iso(q.received_at), json.dumps(quality.flags), int(quality.usable)),
        )
        self.conn.commit()

    def insert_option_quotes(self, cycle_id: int, items: list[tuple[OptionQuote, QualityResult]]) -> int:
        """Insert quotes; returns how many were new (not repeats of an already-stored quote)."""
        rows = [
            (cycle_id, q.symbol, q.feed, q.bid, q.ask, q.bid_size, q.ask_size, q.bid_exchange, q.ask_exchange,
             _iso(q.quote_ts), _iso(q.received_at), q.last_trade_price, q.last_trade_size, _iso(q.last_trade_ts),
             q.implied_volatility, q.delta, q.gamma, q.theta, q.vega, q.rho, json.dumps(r.flags), int(r.usable))
            for q, r in items
        ]
        before = self.conn.total_changes
        self.conn.executemany(
            """INSERT OR IGNORE INTO option_quotes(cycle_id, symbol, feed, bid, ask, bid_size, ask_size, bid_exchange,
               ask_exchange, quote_ts, received_at, last_trade_price, last_trade_size, last_trade_ts, implied_volatility,
               delta, gamma, theta, vega, rho, quality_flags, is_usable) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            rows,
        )
        self.conn.commit()
        return self.conn.total_changes - before
