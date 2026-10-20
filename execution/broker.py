"""Read-only Alpaca access.

This class deliberately exposes NO way to submit, replace, cancel or close
orders or positions. The underlying Alpaca client is private. Order
submission arrives in Phase 6 as a separate, risk-gated component.
"""

from __future__ import annotations

from dataclasses import dataclass

from alpaca.data.enums import DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockLatestTradeRequest
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import QueryOrderStatus
from alpaca.trading.requests import GetCalendarRequest, GetOptionContractsRequest, GetOrdersRequest

from core.config import ConfigError, Settings
from core.secrets import AlpacaCredentials


@dataclass(frozen=True)
class AccountSummary:
    status: str
    equity: float
    last_equity: float
    buying_power: float
    options_buying_power: float | None
    options_approved_level: int | None
    options_trading_level: int | None
    pattern_day_trader: bool
    trading_blocked: bool
    account_blocked: bool


def _f(value) -> float | None:
    return None if value is None else float(value)


class ReadOnlyBroker:
    def __init__(self, settings: Settings, creds: AlpacaCredentials):
        if settings.broker.account_type == "paper" and not creds.is_paper:
            raise ConfigError("refusing to connect: paper settings with live credentials")
        self.__creds = creds
        self.reconnect()
        self._stock_feed = DataFeed.SIP if settings.market_data.stock_feed == "sip" else DataFeed.IEX
        self.is_paper = creds.is_paper

    def reconnect(self) -> None:
        """Fresh API clients, so no connection opened on a network that has since gone away is reused."""
        self.__trading = TradingClient(self.__creds.key_id, self.__creds.secret_key, paper=self.__creds.is_paper)
        self.__data = StockHistoricalDataClient(self.__creds.key_id, self.__creds.secret_key)

    def account_summary(self) -> AccountSummary:
        a = self.__trading.get_account()
        return AccountSummary(
            status=str(getattr(a.status, "value", a.status)),
            equity=float(a.equity),
            last_equity=float(a.last_equity),
            buying_power=float(a.buying_power),
            options_buying_power=_f(a.options_buying_power),
            options_approved_level=a.options_approved_level,
            options_trading_level=a.options_trading_level,
            pattern_day_trader=bool(a.pattern_day_trader),
            trading_blocked=bool(a.trading_blocked),
            account_blocked=bool(a.account_blocked),
        )

    def positions(self) -> list[dict]:
        return [
            {"symbol": p.symbol, "qty": float(p.qty), "asset_class": str(getattr(p.asset_class, "value", p.asset_class))}
            for p in self.__trading.get_all_positions()
        ]

    def open_orders(self) -> list[dict]:
        orders = self.__trading.get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN))
        return [{"id": str(o.id), "symbol": o.symbol, "status": str(getattr(o.status, "value", o.status))} for o in orders]

    def market_clock(self) -> dict:
        c = self.__trading.get_clock()
        return {"is_open": c.is_open, "next_open": c.next_open.isoformat(), "next_close": c.next_close.isoformat()}

    def trading_days(self, start, end) -> list:
        """Exchange trading days (dates) from start to end inclusive."""
        return [c.date for c in self.__trading.get_calendar(GetCalendarRequest(start=start, end=end))]

    def latest_underlying_trade(self, symbol: str) -> dict:
        trade = self.__data.get_stock_latest_trade(StockLatestTradeRequest(symbol_or_symbols=symbol, feed=self._stock_feed))[symbol]
        return {"symbol": symbol, "price": float(trade.price), "timestamp": trade.timestamp.isoformat(), "feed": self._stock_feed.value}

    def option_contracts(self, underlying: str, expiration_gte, expiration_lte) -> list:
        """All active option contracts for an underlying in an expiry window (follows pagination)."""
        contracts, token = [], None
        while True:
            page = self.__trading.get_option_contracts(GetOptionContractsRequest(
                underlying_symbols=[underlying], expiration_date_gte=expiration_gte,
                expiration_date_lte=expiration_lte, limit=1000, page_token=token,
            ))
            contracts.extend(page.option_contracts or [])
            token = page.next_page_token
            if not token:
                return contracts
