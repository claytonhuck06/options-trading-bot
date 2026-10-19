"""The paper-trading session: one cycle every few seconds during market hours.

Each cycle, in order:
  1. Read account equity; take the start-of-day snapshot on the first cycle.
  2. Reconcile our book with the broker (the broker is the truth).
  3. Evaluate the kill switch (losses, drawdown, stale data, reconciliation,
     execution failures, uncovered shorts, manual STOP file).
  4. Manage open positions: emergency flatten, or the normal exit rules.
  5. Only if everything is normal: price the latest snapshot, scan, ask the risk
     engine, and send the single best approved trade.
Market data comes from the collector's database (the collector runs separately).
"""

from __future__ import annotations

import math
import time as _time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from core.config import PROJECT_ROOT, Settings
from core.logging_setup import get_logger
from data.storage.db import MarketDataStore
from execution.broker_api import BrokerAPI, OrderLeg
from execution.order_store import OrderStore
from execution.orders import OrderManager
from execution.reconciliation import reconcile
from execution.recovery import recover
from pricing.engine import PricingResult, price_snapshot
from pricing.persistence import save_candidates, save_pricing
from pricing.rates import risk_free_rate
from pricing.snapshot import load_snapshot
from risk.exits import evaluate_exit
from risk.kill_switch import HealthInputs, evaluate
from risk.positions import LegQuote, OptionLeg, Portfolio, Position, max_defined_loss
from risk.risk_engine import RiskContext, TradeRequest, approval_is_valid, approve
from risk.state import Level, RiskStateStore
from risk.stress import load as load_stress
from strategies.relative_value import scan

log = get_logger("trader")
EASTERN = ZoneInfo("America/New_York")
STOP_FILE = PROJECT_ROOT / "STOP"


@dataclass
class CycleReport:
    at: datetime
    level: str
    triggers: list[str] = field(default_factory=list)
    reconciled: bool = True
    exits: list[str] = field(default_factory=list)
    entry: str | None = None
    notes: list[str] = field(default_factory=list)


class TradingSession:
    def __init__(self, settings: Settings, store: MarketDataStore, broker: BrokerAPI,
                 now=lambda: datetime.now(timezone.utc), sleep=_time.sleep):
        self.s, self.store, self.broker = settings, store, broker
        self.book = OrderStore(store)
        self.state = RiskStateStore(store)
        self.orders = OrderManager(broker, self.book, settings.execution, sleep=sleep)
        self.stress = load_stress(PROJECT_ROOT / "config" / "stress_calibration.json")
        self.now = now
        self.underlying = settings.market_data.primary_underlying

    # --- market data from the collector's database ----------------------------------
    def _spot(self, now: datetime) -> tuple[float | None, float | None]:
        row = self.store.conn.execute(
            "SELECT bid, ask, last_price, received_at FROM underlying_quotes WHERE symbol=? AND is_usable=1 "
            "ORDER BY received_at DESC LIMIT 1", (self.underlying,)).fetchone()
        if not row:
            return None, None
        spot = (row[0] + row[1]) / 2 if row[0] and row[1] else row[2]
        return spot, (now - datetime.fromisoformat(row[3])).total_seconds()

    def _leg_quotes(self, symbols: set[str], now: datetime, fallback_iv: float = 0.20) -> tuple[dict[str, LegQuote], float | None]:
        """Latest stored quote per symbol and the age of the oldest one."""
        quotes, oldest = {}, 0.0
        for sym in symbols:
            r = self.store.conn.execute(
                "SELECT bid, ask, implied_volatility, quote_ts, last_seen_at FROM option_quotes WHERE symbol=? "
                "ORDER BY received_at DESC LIMIT 1", (sym,)).fetchone()
            if not r or r[0] is None or r[1] is None:
                return quotes, None
            seen = datetime.fromisoformat(r[4] or r[3])
            oldest = max(oldest, (now - seen).total_seconds())
            quotes[sym] = LegQuote(r[0], r[1], r[2] or fallback_iv, datetime.fromisoformat(r[3]))
        return quotes, oldest

    def _price_now(self, now: datetime) -> tuple[PricingResult, int]:
        snap = load_snapshot(self.store, self.underlying, now, self.s.pricing.snapshot_max_quote_age_seconds)

        def rate_for(years):
            info = risk_free_rate(self.s, self.store, now, years)
            return info.rate, info.source

        result = price_snapshot(snap, self.s.pricing, rate_for)
        return result, save_pricing(self.store, result)

    # --- lifecycle -------------------------------------------------------------------
    def startup(self):
        rep = recover(self.broker, self.book)
        today = self.now().astimezone(EASTERN).date()
        if not rep.safe_to_resume:
            self.state.escalate(Level.HALTED, "startup_recovery_unsafe", today, rep.recon.summary() if rep.recon else None)
        log.info("startup recovery: %s", rep.recon.summary() if rep.recon else "no reconciliation")
        return rep

    def cycle(self) -> CycleReport:
        now = self.now()
        today = now.astimezone(EASTERN).date()
        rep = CycleReport(now, "NORMAL")
        positions = self.book.open_positions()

        # 1-2. account and reconciliation
        equity = self.broker.equity()
        day = self.state.start_day(today, equity)
        broker_positions = self.broker.positions()
        recon = reconcile(positions, broker_positions, self.broker.open_orders(), expected_open_orders=set())
        rep.reconciled = recon.ok
        if not recon.ok:
            rep.notes.append(recon.summary())

        # 3. market data + kill switch
        spot, spot_age = self._spot(now)
        legs = {l.symbol for p in positions for l in p.legs}
        quotes, quote_age = self._leg_quotes(legs, now) if legs else ({}, 0.0)
        data_age = None if spot_age is None or (legs and quote_age is None) else max(spot_age, quote_age or 0.0)
        # Portfolio risk can only be calculated if we have a quote for every leg we hold.
        risk_ok = not legs or (quote_age is not None and len(quotes) == len(legs))
        ks = evaluate(HealthInputs(equity, len(positions), data_age, recon.ok, self.orders.consecutive_failures,
                                   list(recon.unexpected_at_broker), recon.uncovered_shorts, risk_ok, STOP_FILE.exists()),
                      day, self.s.risk, self.state, today)
        rep.level, rep.triggers = ks.level.name, ks.triggers

        # 4. exits
        for pos in positions:
            if self.book.has_live_orders(pos.position_id):
                rep.notes.append(f"{pos.position_id}: an order is still working; waiting")
                continue
            if any(l.symbol not in quotes for l in pos.legs):
                rep.notes.append(f"no quotes for {pos.position_id}; cannot evaluate exit")
                continue
            decision = evaluate_exit(pos, quotes, now, self.s, ks.level, self._anchor_z(pos, quotes, spot, now))
            if decision.close:
                rep.exits.append(self._close(pos, decision.reason, quotes, emergency=ks.level >= Level.FLATTEN_TODAY))

        # 5. entries
        if ks.level != Level.NORMAL or not recon.ok or not self.s.can_submit_orders:
            return rep
        if self.book.todays_costs(today) >= self.s.execution.max_daily_transaction_costs:
            rep.notes.append("daily transaction-cost cap reached")
            return rep
        try:
            result, run_id = self._price_now(now)
        except LookupError as exc:
            rep.notes.append(f"no snapshot: {exc}")
            return rep
        budget_eq = day.start_equity
        from risk.limits import compute_daily_budget
        budget = compute_daily_budget(self.s.risk, budget_eq, self.state.high_water_mark())
        cands = scan(result, self.s, budget)
        save_candidates(self.store, run_id, cands)
        best = next((c for c in cands if c.eligible), None)
        if best is None:
            return rep
        rep.entry = self._open(best, run_id, result, equity, day, ks.level, positions, now)
        return rep

    # --- helpers ------------------------------------------------------------------------
    def _anchor_z(self, pos: Position, quotes: dict[str, LegQuote], spot, now) -> float | None:
        if not pos.anchor_symbol:
            return None
        row = self.store.conn.execute(
            "SELECT r.fair_price, r.price_uncertainty FROM pricing_residuals r JOIN pricing_runs p ON p.id=r.run_id "
            "WHERE r.symbol=? ORDER BY p.as_of DESC LIMIT 1", (pos.anchor_symbol,)).fetchone()
        if not row or row[0] is None or not row[1] or pos.anchor_symbol not in quotes:
            return None
        anchor = next(l for l in pos.legs if l.symbol == pos.anchor_symbol)
        mid = quotes[pos.anchor_symbol].mid
        gap = (row[0] - mid) if anchor.quantity > 0 else (mid - row[0])
        return gap / row[1]

    def _open(self, cand, run_id, result: PricingResult, equity, day, level, positions, now) -> str:
        sp = cand.spread
        legs = tuple(OptionLeg(l.symbol, self.underlying, l.contract.option.option_type, l.strike,
                               l.contract.option.expiration, l.side, l.price) for l in sp.legs)
        req = TradeRequest(f"{run_id}-{sp.long.symbol}-{sp.short.symbol}", self.underlying, legs,
                           cand.fees_per_spread, cand.contracts, sp.anchor)
        quotes = {c.option.symbol: LegQuote(c.option.bid, c.option.ask, c.fair_iv or 0.2, c.option.quote_ts)
                  for c in result.contracts if c.option.symbol in {l.symbol for l in legs}
                  or c.option.symbol in {l.symbol for p in positions for l in p.legs}}
        ctx = RiskContext(now, self.s, self.state, day, equity, level, Portfolio(positions), quotes,
                          {self.underlying: result.snapshot.spot}, result.expiries[0].rate if result.expiries else 0.04,
                          self.stress, (now - result.snapshot.spot_received_at).total_seconds())
        dec = approve(req, ctx)
        if not dec.approved:
            return f"refused by risk engine: {', '.join(dec.reasons)}"
        order_legs = [OrderLeg(l.symbol, "buy" if l.quantity > 0 else "sell", 1,
                               "buy_to_open" if l.quantity > 0 else "sell_to_open") for l in legs]
        approved_net = sum(l.quantity * l.entry_price for l in legs)
        res = self.orders.execute(
            req.request_id, req.request_id, "open", order_legs, dec.contracts,
            lambda: self._leg_quotes({l.symbol for l in legs}, self.now())[0],
            approved_net + self.s.execution.max_price_deterioration,
            still_approved=lambda: approval_is_valid(dec, self.now()), description=sp.describe())
        if res.filled_qty:
            pos = dec.position
            pos.legs = [OptionLeg(l.symbol, l.underlying, l.option_type, l.strike, l.expiration,
                                  l.quantity // dec.contracts * res.filled_qty, res.leg_prices.get(l.symbol, l.entry_price))
                        for l in pos.legs]
            per = res.filled_qty / dec.contracts
            pos.entry_fees, pos.exit_fees = pos.entry_fees * per, pos.exit_fees * per
            pos.max_loss = max_defined_loss(pos)
            pos.profit_target = self.s.risk.take_profit_pct_of_risk * pos.max_loss
            pos.stop_loss = self.s.risk.stop_loss_pct_of_max_loss * pos.max_loss
            self.book.open_position(pos)
        return f"{sp.describe()}: {res.status} {res.filled_qty}/{dec.contracts}" + (f" ({res.reason})" if res.reason else "")

    def _close(self, pos: Position, reason: str, quotes: dict[str, LegQuote], emergency: bool) -> str:
        n = max(abs(l.quantity) for l in pos.legs)
        unit = [(l, l.quantity // n) for l in pos.legs]
        order_legs = [OrderLeg(l.symbol, "sell" if q > 0 else "buy", abs(q), "sell_to_close" if q > 0 else "buy_to_close")
                      for l, q in unit]
        from execution.orders import net_prices
        _, exe = net_prices(order_legs, quotes)
        slack = 0.25 if emergency else self.s.execution.max_price_deterioration
        key = f"{pos.position_id}-close-{self.book.plan_count(pos.position_id, 'close')}"
        res = self.orders.execute(key, pos.position_id, "close", order_legs, n,
                                  lambda: self._leg_quotes({l.symbol for l in pos.legs}, self.now())[0], exe + slack)
        if res.filled_qty:
            entry_net = sum(q * l.entry_price for l, q in unit)            # signed per spread
            realized = -(entry_net + res.avg_net_price) * 100 * res.filled_qty - (pos.entry_fees + pos.exit_fees) * res.filled_qty / n
            if res.filled_qty >= n:
                self.book.mark_position_closed(pos.position_id, reason, realized)
            else:
                pos.legs = [OptionLeg(l.symbol, l.underlying, l.option_type, l.strike, l.expiration,
                                      q * (n - res.filled_qty), l.entry_price) for l, q in unit]
                self.book.update_position_legs(pos)
        return f"{pos.position_id} {reason}: {res.status} {res.filled_qty}/{n}" + (f" ({res.reason})" if res.reason else "")
