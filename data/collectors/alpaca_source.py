"""Alpaca market data, normalized into data.models records.

Feeds:
  * options "indicative" — free. Alpaca derives these from OPRA; they are NOT
    the actual consolidated exchange quotes, so they are not a reliable
    estimate of executable prices.
  * options "opra" — real consolidated quotes; requires a signed OPRA agreement
    and an Alpaca market-data subscription.
"""

from __future__ import annotations

from datetime import date

from alpaca.data.enums import DataFeed, OptionsFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.historical.option import OptionHistoricalDataClient
from alpaca.data.requests import OptionChainRequest, StockLatestQuoteRequest, StockLatestTradeRequest

from core.config import Settings
from core.secrets import AlpacaCredentials
from data.models import OptionContract, OptionQuote, UnderlyingQuote
from data.storage.db import utcnow
from execution.broker import ReadOnlyBroker


def _enum(v) -> str:
    return str(getattr(v, "value", v))


def _num(v) -> float | None:
    return None if v is None else float(v)


class AlpacaMarketSource:
    def __init__(self, settings: Settings, creds: AlpacaCredentials, broker: ReadOnlyBroker | None = None):
        self.broker = broker or ReadOnlyBroker(settings, creds)
        self._creds = creds
        self._new_clients()
        self.option_feed = settings.market_data.option_feed
        self.stock_feed = settings.market_data.stock_feed
        self._ofeed = OptionsFeed.OPRA if self.option_feed == "opra" else OptionsFeed.INDICATIVE
        self._sfeed = DataFeed.SIP if self.stock_feed == "sip" else DataFeed.IEX

    def _new_clients(self) -> None:
        self._options = OptionHistoricalDataClient(self._creds.key_id, self._creds.secret_key)
        self._stocks = StockHistoricalDataClient(self._creds.key_id, self._creds.secret_key)

    def reconnect(self) -> None:
        """Drop every open connection and start fresh. After the Mac changes network (wifi lost and back), pooled
        connections still point at the old address and fail forever ("Can't assign requested address")."""
        self._new_clients()
        self.broker.reconnect()

    def fetch_contracts(self, underlying: str, gte: date, lte: date) -> list[OptionContract]:
        out = []
        for c in self.broker.option_contracts(underlying, gte, lte):
            out.append(OptionContract(
                symbol=c.symbol, underlying=c.underlying_symbol, root=c.root_symbol,
                option_type=_enum(c.type), style=_enum(c.style), strike=float(c.strike_price),
                expiration=c.expiration_date, multiplier=int(float(c.size)), status=_enum(c.status),
                tradable=bool(c.tradable),
                open_interest=None if c.open_interest is None else int(float(c.open_interest)),
                open_interest_date=c.open_interest_date, close_price=_num(c.close_price),
                close_price_date=c.close_price_date,
            ))
        return out

    def fetch_underlying(self, symbol: str) -> UnderlyingQuote:
        quote = self._stocks.get_stock_latest_quote(StockLatestQuoteRequest(symbol_or_symbols=symbol, feed=self._sfeed))[symbol]
        trade = self._stocks.get_stock_latest_trade(StockLatestTradeRequest(symbol_or_symbols=symbol, feed=self._sfeed))[symbol]
        received = utcnow()
        return UnderlyingQuote(
            symbol=symbol, feed=self.stock_feed,
            bid=_num(quote.bid_price), ask=_num(quote.ask_price),
            bid_size=_num(quote.bid_size), ask_size=_num(quote.ask_size), quote_ts=quote.timestamp,
            last_price=_num(trade.price), last_trade_ts=trade.timestamp, received_at=received,
        )

    def fetch_chain(self, underlying: str, gte: date, lte: date,
                    strike_gte: float | None = None, strike_lte: float | None = None) -> list[OptionQuote]:
        chain = self._options.get_option_chain(OptionChainRequest(
            underlying_symbol=underlying, feed=self._ofeed, expiration_date_gte=gte, expiration_date_lte=lte,
            strike_price_gte=strike_gte, strike_price_lte=strike_lte,
        ))
        received = utcnow()
        out = []
        for symbol, snap in chain.items():
            q, t, g = snap.latest_quote, snap.latest_trade, snap.greeks
            out.append(OptionQuote(
                symbol=symbol, feed=self.option_feed,
                bid=_num(q.bid_price) if q else None, ask=_num(q.ask_price) if q else None,
                bid_size=_num(q.bid_size) if q else None, ask_size=_num(q.ask_size) if q else None,
                bid_exchange=q.bid_exchange if q else None, ask_exchange=q.ask_exchange if q else None,
                quote_ts=q.timestamp if q else None, received_at=received,
                last_trade_price=_num(t.price) if t else None, last_trade_size=_num(t.size) if t else None,
                last_trade_ts=t.timestamp if t else None,
                implied_volatility=_num(snap.implied_volatility),
                delta=_num(g.delta) if g else None, gamma=_num(g.gamma) if g else None,
                theta=_num(g.theta) if g else None, vega=_num(g.vega) if g else None, rho=_num(g.rho) if g else None,
            ))
        return out
