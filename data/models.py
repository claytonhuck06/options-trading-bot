"""Plain records passed between the data source, validators and storage.

All timestamps are timezone-aware UTC datetimes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime


@dataclass(frozen=True)
class OptionContract:
    symbol: str
    underlying: str
    root: str
    option_type: str            # "call" | "put"
    style: str                  # "american" | "european"
    strike: float
    expiration: date
    multiplier: int
    status: str
    tradable: bool
    open_interest: int | None = None
    open_interest_date: date | None = None
    close_price: float | None = None
    close_price_date: date | None = None


@dataclass(frozen=True)
class UnderlyingQuote:
    symbol: str
    feed: str
    bid: float | None
    ask: float | None
    bid_size: float | None
    ask_size: float | None
    quote_ts: datetime | None
    last_price: float | None
    last_trade_ts: datetime | None
    received_at: datetime


@dataclass(frozen=True)
class OptionQuote:
    symbol: str
    feed: str
    bid: float | None
    ask: float | None
    bid_size: float | None
    ask_size: float | None
    bid_exchange: str | None
    ask_exchange: str | None
    quote_ts: datetime | None
    received_at: datetime
    last_trade_price: float | None = None
    last_trade_size: float | None = None
    last_trade_ts: datetime | None = None
    implied_volatility: float | None = None
    delta: float | None = None
    gamma: float | None = None
    theta: float | None = None
    vega: float | None = None
    rho: float | None = None


@dataclass
class QualityResult:
    flags: list[str] = field(default_factory=list)

    # Flags that make a quote unusable for pricing or trading decisions.
    HARD = frozenset({"missing_bid", "missing_ask", "crossed", "stale", "bad_timestamp", "missing_timestamp", "unknown_contract", "no_bid"})

    @property
    def usable(self) -> bool:
        return not (set(self.flags) & self.HARD)
