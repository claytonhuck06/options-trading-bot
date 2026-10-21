"""Alpaca PAPER broker. The only module allowed to send, cancel or close orders.

It refuses to construct unless the settings permit paper order submission and
the credentials point at Alpaca's paper endpoint with a paper key. There is no
live-account code path.
"""

from __future__ import annotations

from alpaca.common.exceptions import APIError
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderClass, OrderSide, OrderType, PositionIntent, QueryOrderStatus, TimeInForce
from alpaca.trading.requests import GetOrdersRequest, LimitOrderRequest, MarketOrderRequest, OptionLegRequest

from core.config import ConfigError, Settings
from core.secrets import AlpacaCredentials
from execution.broker_api import BrokerOrder, BrokerPosition, OrderLeg, OrderStatus, SubmitTimeout

_STATUS = {
    "new": OrderStatus.WORKING, "accepted": OrderStatus.WORKING, "pending_new": OrderStatus.WORKING,
    "accepted_for_bidding": OrderStatus.WORKING, "pending_replace": OrderStatus.WORKING,
    "pending_cancel": OrderStatus.WORKING, "held": OrderStatus.WORKING, "calculated": OrderStatus.WORKING,
    "partially_filled": OrderStatus.PARTIAL, "filled": OrderStatus.FILLED,
    "canceled": OrderStatus.CANCELED, "expired": OrderStatus.CANCELED, "done_for_day": OrderStatus.CANCELED,
    "replaced": OrderStatus.CANCELED, "stopped": OrderStatus.CANCELED, "suspended": OrderStatus.CANCELED,
    "rejected": OrderStatus.REJECTED,
}
_INTENT = {
    "buy_to_open": PositionIntent.BUY_TO_OPEN, "sell_to_open": PositionIntent.SELL_TO_OPEN,
    "buy_to_close": PositionIntent.BUY_TO_CLOSE, "sell_to_close": PositionIntent.SELL_TO_CLOSE,
}


def _v(x) -> str:
    return str(getattr(x, "value", x))


class AlpacaPaperBroker:
    is_paper = True

    def __init__(self, settings: Settings, creds: AlpacaCredentials):
        if not settings.can_submit_orders:
            raise ConfigError("order submission is disabled (paper environment with trading.enabled and "
                              "trading.allow_order_submission both true is required)")
        if not creds.is_paper or creds.key_id.startswith("AK"):
            raise ConfigError("refusing to trade: credentials are not for the Alpaca PAPER account")
        self.__client = TradingClient(creds.key_id, creds.secret_key, paper=True)

    def _convert(self, o) -> BrokerOrder:
        legs = {l.symbol: float(l.filled_avg_price) for l in (o.legs or []) if l.filled_avg_price is not None}
        if not o.legs and o.filled_avg_price is not None:           # a single-symbol (share) order
            legs = {o.symbol: float(o.filled_avg_price)}
        return BrokerOrder(str(o.id), o.client_order_id, _STATUS.get(_v(o.status), OrderStatus.UNKNOWN),
                           int(float(o.qty or 0)), int(float(o.filled_qty or 0)), float(o.limit_price or 0), legs)

    def submit_multileg(self, client_order_id: str, legs: list[OrderLeg], qty: int, limit_price: float) -> BrokerOrder:
        req = LimitOrderRequest(
            qty=qty, order_class=OrderClass.MLEG, type=OrderType.LIMIT, time_in_force=TimeInForce.DAY,
            limit_price=round(limit_price, 2), client_order_id=client_order_id,
            legs=[OptionLegRequest(symbol=l.symbol, ratio_qty=l.ratio,
                                   side=OrderSide.BUY if l.side == "buy" else OrderSide.SELL,
                                   position_intent=_INTENT[l.intent]) for l in legs],
        )
        return self._submit(req, client_order_id, qty, limit_price)

    def submit_stock(self, client_order_id: str, symbol: str, side: str, qty: int, limit_price: float) -> BrokerOrder:
        """Shares, e.g. to unwind an early assignment. limit_price is a positive $/share."""
        req = LimitOrderRequest(symbol=symbol, qty=qty, side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
                                type=OrderType.LIMIT, time_in_force=TimeInForce.DAY, limit_price=round(limit_price, 2),
                                client_order_id=client_order_id)
        return self._submit(req, client_order_id, qty, limit_price)

    def submit_auction(self, client_order_id: str, symbol: str, side: str, qty: int, auction: str) -> BrokerOrder:
        """Whole-share market order for the closing auction ("close", market-on-close), the opening auction
        ("open", market-on-open) or right away during the session ("now"). Used by the overnight basket."""
        tif = {"close": TimeInForce.CLS, "open": TimeInForce.OPG, "now": TimeInForce.DAY}[auction]
        req = MarketOrderRequest(symbol=symbol, qty=qty, side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
                                 time_in_force=tif, client_order_id=client_order_id)
        return self._submit(req, client_order_id, qty, 0.0)

    def submit_option(self, client_order_id: str, symbol: str, side: str, qty: int, limit_price: float,
                      intent: str) -> BrokerOrder:
        """One option contract symbol, DAY limit order at a positive $/share price. Used by dual momentum."""
        req = LimitOrderRequest(symbol=symbol, qty=qty, side=OrderSide.BUY if side == "buy" else OrderSide.SELL,
                                type=OrderType.LIMIT, time_in_force=TimeInForce.DAY, limit_price=round(limit_price, 2),
                                position_intent=_INTENT[intent], client_order_id=client_order_id)
        return self._submit(req, client_order_id, qty, limit_price)

    def _submit(self, req, client_order_id: str, qty: int, limit_price: float) -> BrokerOrder:
        try:
            return self._convert(self.__client.submit_order(req))
        except APIError as exc:
            # A clean API rejection (4xx with a message) means the order was not accepted.
            if getattr(exc, "status_code", None) and 400 <= exc.status_code < 500:
                return BrokerOrder("", client_order_id, OrderStatus.REJECTED, qty, 0, limit_price)
            raise SubmitTimeout(str(exc)) from exc
        except Exception as exc:  # network errors: the order may or may not have arrived
            raise SubmitTimeout(str(exc)) from exc

    def get_order(self, broker_id: str) -> BrokerOrder:
        return self._convert(self.__client.get_order_by_id(broker_id))

    def find_order(self, client_order_id: str) -> BrokerOrder | None:
        try:
            return self._convert(self.__client.get_order_by_client_id(client_order_id))
        except APIError as exc:
            if getattr(exc, "status_code", None) == 404:
                return None
            raise

    def cancel_order(self, broker_id: str) -> None:
        self.__client.cancel_order_by_id(broker_id)

    def open_orders(self) -> list[BrokerOrder]:
        return [self._convert(o) for o in self.__client.get_orders(GetOrdersRequest(status=QueryOrderStatus.OPEN, nested=True))]

    def positions(self) -> list[BrokerPosition]:
        out = []
        for p in self.__client.get_all_positions():
            qty = int(float(p.qty))
            if _v(p.side) == "short" and qty > 0:
                qty = -qty
            out.append(BrokerPosition(p.symbol, qty, float(p.avg_entry_price), _v(p.asset_class),
                                      None if p.market_value is None else float(p.market_value),
                                      None if p.lastday_price is None else float(p.lastday_price)))
        return out

    def equity(self) -> float:
        return float(self.__client.get_account().equity)

    def last_equity(self) -> float:
        return float(self.__client.get_account().last_equity)
