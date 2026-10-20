"""Weekly SPY call spread: its own risk pool, its exit rules, the order book tag, and the trader's once-a-day entry."""

from datetime import date, datetime, timedelta, timezone

import pytest

import execution.trader as trader_mod
from core.config import load_settings
from data.models import OptionQuote, QualityResult, UnderlyingQuote
from data.storage.db import MarketDataStore
from execution.broker_api import SimulatedBroker
from execution.order_store import OrderStore
from execution.trader import TradingSession
from pricing.black_scholes import price as bs_price
from risk.exits import evaluate_exit
from risk.positions import LegQuote, OptionLeg, Portfolio, Position, max_defined_loss
from risk.risk_engine import WEEKLY, TradeRequest, approve
from risk.state import Level, RiskStateStore
from tests.test_risk_engine import ctx, leg, open_position

SPOT = 768.0
TODAY = date(2026, 10, 20)                                       # a Tuesday
EXP = date(2026, 12, 18)                                         # 59 days out
AT_1435 = datetime(2026, 10, 20, 18, 35, tzinfo=timezone.utc)    # 14:35 ET
AT_1100 = datetime(2026, 10, 20, 15, 0, tzinfo=timezone.utc)     # 11:00 ET


def occ(kind: str, k: float, exp: date = EXP) -> str:
    return f"SPY{exp:%y%m%d}{kind}{int(round(k * 1000)):08d}"


# --- risk pool ----------------------------------------------------------------------

def weekly_request(rid="w1", n=10):
    return TradeRequest(rid, "SPY", (leg(770, 1), leg(775, -1)), 0.20, n, strategy=WEEKLY)


def weekly_position(pid, opened=AT_1100, exp=EXP):
    p = Position(pid, "SPY", [OptionLeg(occ("C", 770, exp), "SPY", "call", 770, exp, 1, 20.0),
                              OptionLeg(occ("C", 800, exp), "SPY", "call", 800, exp, -1, 8.0)],
                 opened, 0, 0, 0, entry_fees=0.1, exit_fees=0.1, hold_overnight=True, strategy=WEEKLY)
    p.max_loss = max_defined_loss(p)
    p.profit_target = p.stop_loss = p.max_loss
    return p


def test_weekly_spread_has_its_own_pool_of_the_same_size():
    st = RiskStateStore(MarketDataStore(":memory:"))
    full_same_day = Portfolio([open_position(f"p{i}", 770 + i, 771 + i, 1) for i in range(3)])
    d = approve(weekly_request(), ctx(st, portfolio=full_same_day))
    assert d.approved, d.reasons
    assert d.position.max_loss <= 0.015 * 20_000 + 1e-9                  # 1.5%, like every strategy
    assert d.position.hold_overnight and d.position.strategy == WEEKLY


def test_weekly_spread_limited_to_three_open():
    st = RiskStateStore(MarketDataStore(":memory:"))
    held = [open_position(f"w{i}", 770 + i, 771 + i, 1) for i in range(3)]
    for p in held:
        p.strategy, p.hold_overnight = WEEKLY, True
    d = approve(weekly_request("w9"), ctx(st, portfolio=Portfolio(held)))
    assert not d.approved and "max_concurrent_positions" in d.reasons


# --- exits ----------------------------------------------------------------------------

def _quotes(p: Position, long_bid=1.0) -> dict[str, LegQuote]:
    long_sym, short_sym = p.legs[0].symbol, p.legs[1].symbol
    return {long_sym: LegQuote(long_bid, long_bid + 0.1, 0.16, AT_1100), short_sym: LegQuote(0.5, 0.6, 0.16, AT_1100)}


def test_weekly_spread_has_no_stop_and_no_target():
    settings = load_settings()
    p = weekly_position("w")
    losing = evaluate_exit(p, _quotes(p, long_bid=1.0), AT_1435, settings, spot=SPOT)       # down ~$1,100
    assert not losing.close and losing.pnl < -1000
    winning = evaluate_exit(p, _quotes(p, long_bid=40.0), AT_1435, settings, spot=SPOT)
    assert not winning.close


def test_weekly_time_exit_at_the_daily_check_once_within_14_days():
    settings = load_settings()
    p = weekly_position("w", exp=TODAY + timedelta(days=14))
    assert not evaluate_exit(p, _quotes(p), AT_1100, settings, spot=SPOT).close            # before 14:30
    d = evaluate_exit(p, _quotes(p), AT_1435, settings, spot=SPOT)
    assert d.close and d.reason == "time_exit"
    far = weekly_position("f", exp=TODAY + timedelta(days=15))
    assert not evaluate_exit(far, _quotes(far), AT_1435, settings, spot=SPOT).close


def test_weekly_spread_closed_in_an_emergency():
    settings = load_settings()
    p = weekly_position("w")
    d = evaluate_exit(p, _quotes(p), AT_1100, settings, Level.FLATTEN_TODAY, spot=SPOT)
    assert d.close and d.reason == "emergency"


def test_order_store_keeps_the_strategy_tag():
    book = OrderStore(MarketDataStore(":memory:"))
    book.open_position(weekly_position("w", opened=AT_1100))
    (p,) = book.open_positions()
    assert p.strategy == WEEKLY and p.hold_overnight
    monday = datetime(2026, 10, 19, 4, 0, tzinfo=timezone.utc)
    assert book.strategy_opened_since(WEEKLY, monday) == 1
    assert book.strategy_opened_since(WEEKLY, AT_1435) == 0
    assert book.strategy_opened_since("mispricing", monday) == 0


# --- trader ---------------------------------------------------------------------------------

class FakeSource:
    """A 59-day SPY chain priced with Black-Scholes at 16% vol; legs quote 0.10 wide."""

    def __init__(self):
        self.chain_calls = 0
        self.fail = False
        self.book = {}
        t = (EXP - TODAY).days / 365
        for k in range(730, 885, 5):
            for kind, name in (("C", "call"), ("P", "put")):
                mid = bs_price(SPOT, k, t, 0.04, 0.012, 0.16, name)
                self.book[occ(kind, k)] = round(max(mid, 0.10), 2)

    def _q(self, sym: str, at: datetime) -> OptionQuote:
        m = self.book[sym]
        return OptionQuote(sym, "indicative", round(m - 0.05, 2), round(m + 0.05, 2), 10, 10, "Q", "Q", at, at,
                           implied_volatility=0.16)

    def fetch_chain(self, underlying, gte, lte, strike_gte=None, strike_lte=None):
        self.chain_calls += 1
        now = AT_1435
        return [self._q(s, now) for s in self.book]

    def fetch_option_quotes(self, symbols):
        if self.fail:
            raise ConnectionError("network down")
        return [self._q(s, AT_1435) for s in symbols if s in self.book]


@pytest.fixture
def settings(config_dir, edit_yaml):
    edit_yaml(config_dir / "environments" / "paper.yaml", trading__enabled=True, trading__allow_order_submission=True)
    return load_settings("paper", config_dir=config_dir)


def _spot(store, now):
    cid = store.start_cycle("SPY", "indicative")
    store.insert_underlying_quote(cid, UnderlyingQuote("SPY", "iex", SPOT - 0.01, SPOT + 0.01, 1, 1, now, SPOT, now, now),
                                  QualityResult())


def _session(settings, now, source, broker=None):
    store = MarketDataStore(":memory:")
    _spot(store, now)
    broker = broker or SimulatedBroker(equity=100_000)
    clock = {"now": now}
    s = TradingSession(settings, store, broker, now=lambda: clock["now"], sleep=lambda _: None, source=source)
    return s, broker, clock


def _at(s, clock, now):
    """Move the clock and let the collector deliver a fresh SPY price."""
    clock["now"] = now
    _spot(s.store, now)


def _once_a_day(settings):
    """The original mode: one try a day at run_at (entry_start unset, no retries)."""
    ws = settings.weekly_spread.model_copy(update={"entry_start": None, "retry_minutes": 0})
    return settings.model_copy(update={"weekly_spread": ws})


def test_trader_opens_one_weekly_spread_from_the_daily_check(settings, monkeypatch, tmp_path):
    monkeypatch.setattr(trader_mod, "STOP_FILE", tmp_path / "STOP")
    settings = _once_a_day(settings)
    src = FakeSource()
    s, broker, clock = _session(settings, AT_1100, src)
    s.cycle()
    assert src.chain_calls == 0                                   # nothing before 14:30
    _at(s, clock, AT_1435)
    s.cycle()
    weekly = [p for p in s.book.open_positions() if p.strategy == WEEKLY]
    assert len(weekly) == 1, s.book.open_positions()
    p = weekly[0]
    assert p.hold_overnight and p.expiration == EXP
    long_leg, short_leg = sorted(p.legs, key=lambda l: l.strike)
    assert long_leg.quantity > 0 > short_leg.quantity and long_leg.strike < short_leg.strike
    assert p.max_loss <= 0.015 * 100_000 + 1e-6                  # sized from the weekly pool
    assert {x.symbol for x in broker.positions()} == {l.symbol for l in p.legs}
    # the next cycle the same day neither retries nor halts: its legs are quoted live and reconcile
    _at(s, clock, AT_1435 + timedelta(seconds=15))
    rep = s.cycle()
    assert src.chain_calls == 1 and rep.level == "NORMAL" and rep.reconciled, rep
    # a later day in the same week: still only one
    _at(s, clock, AT_1435 + timedelta(days=1))
    s.cycle()
    assert len([p for p in s.book.open_positions() if p.strategy == WEEKLY]) == 1


def test_live_quote_failure_is_retried_not_a_halt(settings, monkeypatch, tmp_path):
    monkeypatch.setattr(trader_mod, "STOP_FILE", tmp_path / "STOP")
    src = FakeSource()
    s, broker, clock = _session(settings, AT_1100, src)
    p = weekly_position("held", opened=AT_1100 - timedelta(days=3))
    s.book.open_position(p)
    for l in p.legs:
        src.book.setdefault(l.symbol, 10.0)
        broker.add_position(l.symbol, l.quantity)
    assert s.cycle().level == "NORMAL"
    src.fail = True
    with pytest.raises(ConnectionError):                          # main.py's loop retries the cycle
        s.cycle()
    assert s.state.level(TODAY) == Level.NORMAL


def test_weekly_spread_off_when_disabled(config_dir, edit_yaml, monkeypatch, tmp_path):
    monkeypatch.setattr(trader_mod, "STOP_FILE", tmp_path / "STOP")
    edit_yaml(config_dir / "environments" / "paper.yaml", trading__enabled=True, trading__allow_order_submission=True)
    edit_yaml(config_dir / "settings.yaml", weekly_spread__enabled=False)
    settings = load_settings("paper", config_dir=config_dir)
    src = FakeSource()
    s, _, _ = _session(settings, AT_1435, src)
    s.cycle()
    assert src.chain_calls == 0 and not s.book.open_positions()



def test_weekly_spread_retries_through_the_day_until_a_trade(settings, monkeypatch, tmp_path):
    """Owner 2026-10-20: from entry_start, if the try doesn't produce a trade, try again every retry_minutes."""
    from execution.broker_api import SimBehaviour
    monkeypatch.setattr(trader_mod, "STOP_FILE", tmp_path / "STOP")
    assert settings.weekly_spread.entry_start == "09:35" and settings.weekly_spread.retry_minutes == 10
    src = FakeSource()
    broker = SimulatedBroker(equity=100_000)
    broker.queue(SimBehaviour("reject"))                          # first try: no trade
    s, broker, clock = _session(settings, AT_1100, src, broker)
    s.cycle()
    assert src.chain_calls == 1 and not s.book.open_positions()
    _at(s, clock, AT_1100 + timedelta(minutes=5))                 # too soon to retry
    s.cycle()
    assert src.chain_calls == 1
    _at(s, clock, AT_1100 + timedelta(minutes=10))                # retry: fills this time
    s.cycle()
    assert src.chain_calls == 2
    assert len([p for p in s.book.open_positions() if p.strategy == WEEKLY]) == 1
    _at(s, clock, AT_1100 + timedelta(minutes=30))                # one a week: no more tries
    s.cycle()
    assert src.chain_calls == 2


def test_weekly_spread_trades_after_the_same_day_cutoff_until_its_own(settings, monkeypatch, tmp_path):
    """Owner 2026-10-20: the weekly spread may open all trading day (to 15:50 ET); same-day trades still stop at 15:00."""
    monkeypatch.setattr(trader_mod, "STOP_FILE", tmp_path / "STOP")
    src = FakeSource()
    s, _, _ = _session(settings, datetime(2026, 10, 20, 19, 30, tzinfo=timezone.utc), src)    # 15:30 ET
    s.cycle()
    assert len([p for p in s.book.open_positions() if p.strategy == WEEKLY]) == 1
    src = FakeSource()
    s, _, _ = _session(settings, datetime(2026, 10, 20, 19, 50, tzinfo=timezone.utc), src)    # 15:50 ET
    s.cycle()
    assert src.chain_calls == 0
