"""Point-in-time market snapshots with no look-ahead.

A snapshot "as of T" contains, for each contract, the latest stored quote
first received at or before T. The quote is kept only if the collector
confirmed it was still current within the maximum age before T (repeated,
unchanged quotes are not re-stored, so `last_seen_at` carries that
confirmation). No quote first received after T can leak in.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from data.storage.db import MarketDataStore

SECONDS_PER_YEAR = 365.0 * 24 * 3600


@dataclass(frozen=True)
class SnapOption:
    symbol: str
    option_type: str
    strike: float
    expiration: date
    bid: float
    ask: float
    bid_size: float | None
    ask_size: float | None
    quote_ts: datetime
    received_at: datetime

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2


@dataclass(frozen=True)
class Snapshot:
    as_of: datetime
    underlying: str
    spot: float
    spot_received_at: datetime
    options: list[SnapOption]


def years_to_expiry(expiration: date, as_of: datetime) -> float:
    """Calendar-time years until the 4:00 p.m. New York close on the expiry date."""
    from zoneinfo import ZoneInfo
    close = datetime(expiration.year, expiration.month, expiration.day, 16, 0, tzinfo=ZoneInfo("America/New_York"))
    return max((close - as_of).total_seconds(), 0.0) / SECONDS_PER_YEAR


def _dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


def latest_receipt_time(store: MarketDataStore, underlying: str) -> datetime | None:
    row = store.conn.execute("SELECT MAX(received_at) FROM underlying_quotes WHERE symbol=?", (underlying,)).fetchone()
    return _dt(row[0]) if row and row[0] else None


def load_snapshot(store: MarketDataStore, underlying: str, as_of: datetime, max_age_seconds: float) -> Snapshot:
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")
    as_of_s = as_of.astimezone(timezone.utc).isoformat()
    oldest_s = (as_of - timedelta(seconds=max_age_seconds)).astimezone(timezone.utc).isoformat()

    u = store.conn.execute(
        """SELECT bid, ask, last_price, received_at FROM underlying_quotes
           WHERE symbol=? AND received_at<=? AND received_at>=? AND is_usable=1 ORDER BY received_at DESC LIMIT 1""",
        (underlying, as_of_s, oldest_s),
    ).fetchone()
    if not u:
        raise LookupError(f"no usable {underlying} quote within {max_age_seconds:.0f}s before {as_of_s}")
    spot = (u["bid"] + u["ask"]) / 2 if u["bid"] and u["ask"] else u["last_price"]

    rows = store.conn.execute(
        """SELECT q.symbol, c.option_type, c.strike, c.expiration, q.bid, q.ask, q.bid_size, q.ask_size,
                  q.quote_ts, q.received_at, q.is_usable
           FROM option_quotes q
           JOIN (SELECT symbol, MAX(received_at) AS rt FROM option_quotes
                 WHERE received_at<=? AND symbol IN
                   (SELECT symbol FROM option_contracts WHERE underlying=?)
                 GROUP BY symbol) last ON last.symbol=q.symbol AND last.rt=q.received_at
           JOIN option_contracts c ON c.symbol=q.symbol
           WHERE q.last_seen_at>=?""",
        (as_of_s, underlying, oldest_s),
    ).fetchall()
    options = [
        SnapOption(r["symbol"], r["option_type"], r["strike"], date.fromisoformat(r["expiration"]), r["bid"], r["ask"],
                   r["bid_size"], r["ask_size"], _dt(r["quote_ts"]), _dt(r["received_at"]))
        for r in rows if r["is_usable"]
    ]
    return Snapshot(as_of, underlying, spot, _dt(u["received_at"]), options)
