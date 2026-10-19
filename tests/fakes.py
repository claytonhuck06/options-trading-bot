from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

from data.models import OptionContract, OptionQuote, UnderlyingQuote

NOW = datetime(2026, 10, 19, 15, 0, tzinfo=timezone.utc)


def contract(symbol="SPY261030C00700000", strike=700.0, exp=date(2026, 10, 30)):
    return OptionContract(symbol=symbol, underlying="SPY", root="SPY", option_type="call", style="american",
                          strike=strike, expiration=exp, multiplier=100, status="active", tradable=True)


def quote(symbol="SPY261030C00700000", bid=1.00, ask=1.05, ts=NOW - timedelta(seconds=2), received=NOW, **kw):
    return OptionQuote(symbol=symbol, feed="indicative", bid=bid, ask=ask, bid_size=10, ask_size=10,
                       bid_exchange="Q", ask_exchange="Q", quote_ts=ts, received_at=received, **kw)


def underlying(price=700.0, received=NOW):
    return UnderlyingQuote(symbol="SPY", feed="iex", bid=price - 0.01, ask=price + 0.01, bid_size=1, ask_size=1,
                           quote_ts=received - timedelta(seconds=1), last_price=price, last_trade_ts=received, received_at=received)


class FakeSource:
    option_feed = "indicative"
    stock_feed = "iex"

    def __init__(self, contracts=None, chains=None, fail_times=0):
        self.contracts = contracts if contracts is not None else [contract()]
        self.chains = list(chains) if chains is not None else [[quote()]]
        self.fail_times = fail_times
        self.contract_calls = 0
        self.chain_calls = 0

    def fetch_contracts(self, underlying, gte, lte):
        self.contract_calls += 1
        return self.contracts

    def fetch_underlying(self, symbol):
        return underlying()

    def fetch_chain(self, underlying, gte, lte, strike_gte=None, strike_lte=None):
        self.chain_calls += 1
        self.last_strike_band = (strike_gte, strike_lte)
        if self.fail_times > 0:
            self.fail_times -= 1
            raise ConnectionError("simulated disconnect")
        return self.chains[min(self.chain_calls - 1, len(self.chains) - 1)]
