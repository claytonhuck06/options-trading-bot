"""The paper-trading session: one cycle every few seconds during market hours.

Each cycle, in order:
  1. Read account equity; take the start-of-day snapshot on the first cycle. The snapshot is the
     equity at the previous close, so an overnight gap counts against today's loss limit.
  2. Recognise any early assignment (execution/assignment.py), then reconcile our book with the
     broker (the broker is the truth).
  3. Evaluate the kill switch (losses, drawdown, stale data, reconciliation,
     execution failures, uncovered shorts, manual STOP file).
  4. Manage open positions: emergency flatten, or the normal exit rules.
  5. Only if everything is normal: price the latest snapshot, scan, ask the risk
     engine, and send the single best approved trade.
Market data comes from the collector's database (the collector runs separately).

Right after the open, quotes in the database are still yesterday's until the collector's first
cycles arrive. For the first few minutes (multiday.open_settle_minutes) stale data means "wait",
not "halt", and positions held overnight are not acted on until quotes have settled, except in
an emergency. The same grace applies when the session starts late or resumes after a pause (the
Mac slept, e.g. closed for a drive): the collector needs a moment to fetch fresh quotes, so old
data then is expected, not a failure. Data still stale once the grace has passed halts as usual.
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
from execution.assignment import apply_to_position, detect_assignments
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
from risk.risk_engine import WEEKLY, RiskContext, TradeRequest, approval_is_valid, approve
from risk.state import Level, RiskStateStore
from risk.stress import load as load_stress
from monitoring import alerts
from strategies.relative_value import scan

log = get_logger("trader")
EASTERN = ZoneInfo("America/New_York")
STOP_FILE = PROJECT_ROOT / "STOP"
PAUSE_SECONDS = 120          # no cycle for this long (normal: every 15 s) = the Mac slept or the program stalled


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
                 now=lambda: datetime.now(timezone.utc), sleep=_time.sleep, source=None, basket=None, momentum=None):
        self.s, self.store, self.broker = settings, store, broker
        self.basket = basket                     # overnight share basket (execution/overnight_basket.py), same account
        self.momentum = momentum                 # monthly dual momentum ETFs (execution/dual_momentum_paper.py), same account
        self.source = source                     # live market data for the weekly spread (outside the collector's range)
        self._live_symbols: set[str] = set()     # contracts quoted live instead of from the collector's database
        self._live_cache: dict[str, LegQuote] = {}
        self._live_seen: dict[str, datetime] = {}
        self.book = OrderStore(store)
        self.state = RiskStateStore(store)
        self.orders = OrderManager(broker, self.book, settings.execution, sleep=sleep)
        self.stress = load_stress(PROJECT_ROOT / "config" / "stress_calibration.json")
        self.now = now
        self.underlying = settings.market_data.primary_underlying
        self._last_cycle_end: datetime | None = None
        self._grace_until: datetime | None = None

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
        """Latest stored quote per symbol and the age of the oldest one. The weekly spread's contracts are
        quoted live from the broker's data feed (the collector only covers 7-30 days to expiry)."""
        quotes, oldest = {}, 0.0
        live = {sym for sym in symbols if sym in self._live_symbols}
        if live:
            got, age = self._live_quotes(live, now, fallback_iv)
            quotes.update(got)
            if age is None:
                return quotes, None
            oldest = age
        for sym in symbols - live:
            r = self.store.conn.execute(
                "SELECT bid, ask, implied_volatility, quote_ts, last_seen_at FROM option_quotes WHERE symbol=? "
                "ORDER BY received_at DESC LIMIT 1", (sym,)).fetchone()
            if not r or r[0] is None or r[1] is None:
                return quotes, None
            seen = datetime.fromisoformat(r[4] or r[3])
            oldest = max(oldest, (now - seen).total_seconds())
            quotes[sym] = LegQuote(r[0], r[1], r[2] or fallback_iv, datetime.fromisoformat(r[3]))
        return quotes, oldest

    def _live_quotes(self, symbols: set[str], now: datetime, fallback_iv: float) -> tuple[dict[str, LegQuote], float | None]:
        """Fetch live quotes. A network error propagates (the cycle is retried, never fatal); a contract with no
        usable quote in the reply keeps its last good quote, whose age then counts like any stale data."""
        if self.source is None:
            return {}, None
        for q in self.source.fetch_option_quotes(sorted(symbols)):
            if q.bid is None or q.ask is None or q.ask <= 0 or q.bid > q.ask:
                continue
            self._live_cache[q.symbol] = LegQuote(q.bid, q.ask, q.implied_volatility or fallback_iv, q.quote_ts or q.received_at)
            self._live_seen[q.symbol] = q.received_at
        if any(sym not in self._live_cache for sym in symbols):
            return {sym: self._live_cache[sym] for sym in symbols if sym in self._live_cache}, None
        oldest = max((now - self._live_seen[sym]).total_seconds() for sym in symbols)
        return {sym: self._live_cache[sym] for sym in symbols}, max(oldest, 0.0)

    def _price_now(self, now: datetime) -> tuple[PricingResult, int]:
        snap = load_snapshot(self.store, self.underlying, now, self.s.pricing.snapshot_max_quote_age_seconds)

        def rate_for(years):
            info = risk_free_rate(self.s, self.store, now, years)
            return info.rate, info.source

        result = price_snapshot(snap, self.s.pricing, rate_for)
        return result, save_pricing(self.store, result)

    def _share_books(self) -> tuple[dict[str, int], set[str]]:
        """Shares and open orders of the share strategies in this account (overnight basket, dual momentum)."""
        held: dict[str, int] = {}
        orders: set[str] = set()
        for book in (self.basket, self.momentum):
            if book is not None:
                h, o = book.expectations(self.broker)
                for sym, q in h.items():
                    held[sym] = held.get(sym, 0) + q
                orders |= o
        return held, orders

    def _momentum_pnl(self, today, broker_positions) -> tuple[float, float]:
        """Dual momentum's all-time P&L now and at the previous close (0, 0 without it or if it can't be read,
        which leaves the kill switch counting everything: the safe side)."""
        if self.momentum is None:
            return 0.0, 0.0
        try:
            self.momentum.sync(self.broker)
            return self.momentum.pnl_now(broker_positions), self.momentum.pnl_at_prev_close(today, broker_positions)
        except Exception as exc:
            log.warning("dual momentum P&L unavailable; kill switch counts it today: %s", exc)
            return 0.0, 0.0

    def _expected_extras(self, assignment_shares: int) -> tuple[dict[str, int] | None, set[str]]:
        """Shares and open orders that belong to us but not to the options book: assignment shares being
        unwound, and the share strategies' holdings and orders."""
        hedge = {self.underlying: assignment_shares} if assignment_shares else {}
        held, orders = self._share_books()
        for sym, q in held.items():
            hedge[sym] = hedge.get(sym, 0) + q
        return hedge or None, orders

    # --- lifecycle -------------------------------------------------------------------
    def startup(self):
        """Morning check before the day starts: settle unknown orders, recognise overnight assignments,
        reconcile with the broker and list what is being carried into the day."""
        now = self.now()
        today = now.astimezone(EASTERN).date()
        if self.source is not None and hasattr(self.source, "reconnect"):
            try:
                self.source.reconnect()              # after a network change, pooled data connections go dead
            except Exception as exc:
                log.warning("market data reconnect failed at startup: %s", exc)
        positions = self.book.open_positions()
        try:
            self._handle_assignments(positions, self.broker.positions(), [])
        except Exception as exc:                 # never let the check itself stop the reconciliation below
            log.warning("assignment check failed at startup: %s", exc)
        hedge, extra_orders = self._expected_extras(self._assignment_shares())
        rep = recover(self.broker, self.book, hedge_shares=hedge, expected_open_orders=extra_orders)
        if not rep.safe_to_resume:
            self.state.escalate(Level.HALTED, "startup_recovery_unsafe", today, rep.recon.summary() if rep.recon else None)
            alerts.urgent("Trading halted at startup", "The broker's positions do not match our records: "
                          f"{rep.recon.summary() if rep.recon else 'unknown'}. Nothing will trade until a person checks it.")
        carried = self.book.open_positions()
        stale = [p.position_id for p in carried if not p.hold_overnight and p.opened_at.astimezone(EASTERN).date() < today]
        if stale:
            alerts.urgent("Same-day position left open overnight",
                          f"{', '.join(stale)} should have closed yesterday. It will be closed at the open.")
        rep.carried = [f"{p.position_id} (expires {p.expiration})" for p in carried]
        log.info("startup recovery: %s; carried into the day: %s", rep.recon.summary() if rep.recon else "no reconciliation",
                 rep.carried or "nothing")
        return rep

    def _settling(self, now: datetime) -> bool:
        local = now.astimezone(EASTERN)
        opened = datetime.combine(local.date(), datetime.strptime("09:30", "%H:%M").time(), EASTERN)
        return opened <= local < opened + timedelta(minutes=self.s.risk.multiday.open_settle_minutes)

    def cycle(self) -> CycleReport:
        now = self.now()
        grace = timedelta(minutes=self.s.risk.multiday.open_settle_minutes)
        paused = None if self._last_cycle_end is None else (now - self._last_cycle_end).total_seconds()
        if paused is None or paused > PAUSE_SECONDS:
            self._grace_until = now + grace          # first cycle (possibly a late start) or resuming after a pause
        try:
            rep = self._cycle(now)
        finally:
            self._last_cycle_end = self.now()
        if paused is not None and paused > PAUSE_SECONDS:
            rep.notes.append(f"resumed after {paused / 60:.0f} minutes without running")
            held = len(self.book.open_positions())
            alerts.send("Bot resumed after a pause",
                        f"It did not run for {paused / 60:.0f} minutes (Mac asleep or offline). "
                        + (f"{held} position(s) were open and unwatched; checking them now." if held else "No positions were open.")
                        + " Trading continues normally once fresh prices arrive.", "high" if held else "default")
        return rep

    def _cycle(self, now: datetime) -> CycleReport:
        today = now.astimezone(EASTERN).date()
        rep = CycleReport(now, "NORMAL")
        positions = self.book.open_positions()
        self._live_symbols = {l.symbol for p in positions if p.strategy == WEEKLY for l in p.legs}

        # 1-2. account, assignments and reconciliation
        broker_positions = self.broker.positions()
        mom_now, mom_prev = self._momentum_pnl(today, broker_positions)
        # Dual momentum is kept out of the kill switch (owner, 2026-10-21): its P&L is taken out of both sides.
        equity = self.broker.equity() - mom_now
        day = self.state.start_day(today, self.broker.last_equity() - mom_prev)
        positions = self._handle_assignments(positions, broker_positions, rep.notes)
        shares = self._assignment_shares()
        hedge, extra_orders = self._expected_extras(shares)
        recon = reconcile(positions, broker_positions, self.broker.open_orders(),
                          hedge_shares=hedge, expected_open_orders=extra_orders)
        rep.reconciled = recon.ok
        if not recon.ok:
            rep.notes.append(recon.summary())

        # 3. market data + kill switch
        spot, spot_age = self._spot(now)
        legs = {l.symbol for p in positions for l in p.legs}
        quotes, quote_age = self._leg_quotes(legs, now) if legs else ({}, 0.0)
        data_age = None if spot_age is None or (legs and quote_age is None) else max(spot_age, quote_age or 0.0)
        settling = self._settling(now)
        resuming = self._grace_until is not None and now < self._grace_until
        waiting = (settling or resuming) and (data_age is None or data_age > self.s.risk.max_quote_age_with_positions_seconds)
        # Portfolio risk can only be calculated if we have a quote for every leg we hold.
        risk_ok = waiting or not legs or (quote_age is not None and len(quotes) == len(legs))
        previous = self.state.level(today)
        ks = evaluate(HealthInputs(equity, 0 if waiting else len(positions), data_age, recon.ok, self.orders.consecutive_failures,
                                   list(recon.unexpected_at_broker), recon.uncovered_shorts, risk_ok, STOP_FILE.exists(),
                                   self._planned_overnight_risk(positions)),
                      day, self.s.risk, self.state, today)
        rep.level, rep.triggers = ks.level.name, ks.triggers
        if ks.level > previous:
            self._alert_level(ks.level, ks.triggers, equity - day.start_equity)
        if waiting:
            rep.notes.append("waiting for fresh market data after the open" if settling else "waiting for fresh market data after a pause")
            return rep

        # 4. exits (assignment shares first, then positions)
        if shares:
            rep.exits.append(self._unwind_shares(shares))
        force = set(self.state._get("force_close_positions", []))
        for pos in positions:
            if self.book.has_live_orders(pos.position_id):
                rep.notes.append(f"{pos.position_id}: an order is still working; waiting")
                continue
            if any(l.symbol not in quotes for l in pos.legs):
                rep.notes.append(f"no quotes for {pos.position_id}; cannot evaluate exit")
                continue
            if pos.position_id in force:
                rep.exits.append(self._close(pos, "early_assignment", quotes, emergency=ks.level >= Level.FLATTEN_TODAY))
                continue
            if pos.hold_overnight and settling and ks.level < Level.FLATTEN_TODAY:
                rep.notes.append(f"{pos.position_id}: held overnight; waiting for the open to settle")
                continue
            decision = evaluate_exit(pos, quotes, now, self.s, ks.level, self._anchor_z(pos, quotes, spot, now), spot)
            if decision.close:
                rep.exits.append(self._close(pos, decision.reason, quotes, emergency=ks.level >= Level.FLATTEN_TODAY))
                if decision.reason in ("gap_stop", "dividend_assignment_risk", "early_assignment_risk", "near_expiry"):
                    alerts.send(f"Closing {pos.position_id}: {decision.reason.replace('_', ' ')}",
                                f"{rep.exits[-1]}. Loss/profit at decision: ${decision.pnl:,.0f}.", "high")

        # 5. entries
        if ks.level != Level.NORMAL or not recon.ok or not self.s.can_submit_orders or shares:
            return rep
        if self.book.todays_costs(today) >= self.s.execution.max_daily_transaction_costs:
            rep.notes.append("daily transaction-cost cap reached")
            return rep
        weekly = self._weekly_entry(now, today, equity, day, ks.level, positions)
        if weekly:
            rep.entry = weekly
            positions = self.book.open_positions()
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

    def _planned_overnight_risk(self, positions: list[Position]) -> float:
        """Max loss of open multi-day positions; the daily loss trigger makes room for it (risk/kill_switch.py)."""
        if not self.s.risk.allow_overnight_positions:
            return 0.0
        return sum(p.max_loss for p in positions if p.hold_overnight)

    # --- early assignment ------------------------------------------------------------------
    def _assignment_shares(self) -> int:
        """Shares delivered by assignments that have not been sold / bought back yet."""
        return sum(b["shares"] for b in self.state._get("assignment_batches", []))

    def _handle_assignments(self, positions: list[Position], broker_positions, notes: list[str]) -> list[Position]:
        known = self._assignment_shares() + self._share_books()[0].get(self.underlying, 0)   # e.g. momentum's SPY
        found = detect_assignments(positions, broker_positions, self.underlying, known)
        if not found:
            return positions
        batches = self.state._get("assignment_batches", [])
        extra = self.state._get("assignment_pnl", {})
        force = set(self.state._get("force_close_positions", []))
        for pos in positions:
            mine = [a for a in found if a.position_id == pos.position_id]
            if not mine:
                continue
            for a in mine:
                leg = next(l for l in pos.legs if l.symbol == a.symbol)
                # The short option's premium is kept; the shares were delivered at the strike price.
                extra[pos.position_id] = extra.get(pos.position_id, 0.0) + leg.entry_price * 100 * a.contracts
                batches.append({"position_id": pos.position_id, "shares": a.shares, "strike": a.strike})
            apply_to_position(pos, mine)
            self.book.update_position_legs(pos)
            force.add(pos.position_id)
        self.state._set("assignment_batches", batches)
        self.state._set("assignment_pnl", extra)
        self.state._set("force_close_positions", sorted(force))
        self.book.event("early_assignment", {"assignments": [a.__dict__ for a in found]})
        text = "; ".join(f"{a.contracts} {a.option_type} {a.strike:g} in {a.position_id} -> {a.shares:+d} {self.underlying} shares"
                         for a in found)
        notes.append(f"early assignment: {text}")
        alerts.urgent("Early assignment", f"{text}. The bot is unwinding the shares and closing the rest of the position.")
        return self.book.open_positions()

    def _unwind_shares(self, shares: int) -> str:
        if self.book.has_live_orders("assignment"):
            return "assignment shares: an order is still working"
        def quote() -> dict[str, LegQuote]:
            r = self.store.conn.execute("SELECT bid, ask FROM underlying_quotes WHERE symbol=? AND is_usable=1 "
                                        "ORDER BY received_at DESC LIMIT 1", (self.underlying,)).fetchone()
            return {self.underlying: LegQuote(r[0], r[1], 0.2, self.now())} if r and r[0] and r[1] else {}

        q = quote().get(self.underlying)
        if q is None:
            return "assignment shares: no usable share quote yet"
        side = "sell" if shares > 0 else "buy"
        exe = q.ask if side == "buy" else -q.bid
        key = f"assignment-{self.now():%Y%m%d}-{self.book.plan_count('assignment', 'close')}"
        res = self.orders.execute(key, "assignment", "close", [OrderLeg(self.underlying, side, 1, "stock")], abs(shares),
                                  quote, exe + self.s.risk.multiday.gap_exit_slack)
        if res.filled_qty:
            price = abs(res.avg_net_price)
            frac = res.filled_qty / abs(shares)
            extra = self.state._get("assignment_pnl", {})
            left = []
            for b in self.state._get("assignment_batches", []):
                done = round(b["shares"] * frac)
                extra[b["position_id"]] = extra.get(b["position_id"], 0.0) + done * (price - b["strike"])
                if b["shares"] - done:
                    left.append({**b, "shares": b["shares"] - done})
            self.state._set("assignment_pnl", extra)
            self.state._set("assignment_batches", left)
        return f"assignment shares {side} {abs(shares)}: {res.status} {res.filled_qty}" + (f" ({res.reason})" if res.reason else "")

    def _alert_level(self, level: Level, triggers: list[str], pnl_today: float) -> None:
        what = {Level.NO_NEW_ENTRIES: "No new trades (drawdown warning)", Level.FLATTEN_TODAY: "Closing everything for today",
                Level.HALTED: "Trading HALTED"}[level]
        reasons = ", ".join(t.replace("_", " ") for t in triggers) or "manual"
        msg = f"Kill switch: {reasons}. Today's P&L ${pnl_today:,.0f}."
        if level == Level.HALTED:
            msg += " Restart needs a person: main.py killswitch reset."
        elif level == Level.NO_NEW_ENTRIES:
            msg += " To allow trading today: main.py killswitch ack-warning."
        (alerts.urgent if level >= Level.FLATTEN_TODAY else lambda t, m: alerts.send(t, m, "high"))(what, msg)

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
        held_live = {l.symbol for p in positions for l in p.legs} & self._live_symbols
        if held_live:                                # weekly spread legs are outside the pricing snapshot
            quotes.update(self._leg_quotes(held_live, now)[0])
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
            approved_net + self.s.execution.price_slack(approved_net),
            still_approved=lambda: approval_is_valid(dec, self.now()), description=sp.describe())
        if res.filled_qty:
            self.book.open_position(self._filled_position(dec, res))
        return f"{sp.describe()}: {res.status} {res.filled_qty}/{dec.contracts}" + (f" ({res.reason})" if res.reason else "")

    def _filled_position(self, dec, res) -> Position:
        """The approved position scaled to what actually filled, at the actual leg prices."""
        pos = dec.position
        pos.legs = [OptionLeg(l.symbol, l.underlying, l.option_type, l.strike, l.expiration,
                              l.quantity // dec.contracts * res.filled_qty, res.leg_prices.get(l.symbol, l.entry_price))
                    for l in pos.legs]
        per = res.filled_qty / dec.contracts
        pos.entry_fees, pos.exit_fees = pos.entry_fees * per, pos.exit_fees * per
        pos.max_loss = max_defined_loss(pos)
        if pos.strategy == WEEKLY:
            # No profit target and no stop (as tested); its exits are in risk/exits.py. Recorded as the
            # whole max loss so nothing else reads them as a trigger.
            pos.profit_target = pos.stop_loss = pos.max_loss
        else:
            pos.profit_target = self.s.risk.take_profit_pct_of_risk * pos.max_loss
            pos.stop_loss = self.s.risk.stop_loss_pct_of_max_loss * pos.max_loss
        return pos

    # --- weekly call spread ----------------------------------------------------------------
    def _weekly_entry(self, now: datetime, today, equity, day, level, positions: list[Position]) -> str | None:
        """From weekly_spread.entry_start (or run_at): if no weekly spread was opened this week, build one from a live chain and send
        it through the risk engine. If that doesn't produce a trade, try again every retry_minutes until the entry
        cutoff (retry_minutes 0 = once a day). Returns a description if an order was attempted."""
        ws = self.s.weekly_spread
        local = now.astimezone(EASTERN)
        first_try = datetime.strptime(ws.entry_start or ws.run_at, "%H:%M").time()
        if not ws.enabled or self.source is None or local.time() < first_try:
            return None
        if local.time() >= datetime.strptime(ws.entry_end or self.s.schedule.no_new_positions_after, "%H:%M").time():
            return None
        monday = datetime.combine(today - timedelta(days=today.weekday()), datetime.min.time(), EASTERN)
        if self.book.strategy_opened_since(WEEKLY, monday):
            return None
        last = self.state._get("weekly_spread_last_attempt", None)
        if last:
            last_t = datetime.fromisoformat(last)
            if last_t.astimezone(EASTERN).date() == today and (
                    ws.retry_minutes == 0 or now - last_t < timedelta(minutes=ws.retry_minutes)):
                return None
        self.state._set("weekly_spread_last_attempt", now.isoformat())
        from strategies.spy_trend import TrendParams, build_call_spread
        spot, spot_age = self._spot(now)
        if spot is None:
            return "weekly spread: no SPY price"
        lo, hi = today + timedelta(days=ws.target_dte - ws.dte_tolerance), today + timedelta(days=ws.target_dte + ws.dte_tolerance)
        chain = self.source.fetch_chain(self.underlying, lo, hi, spot * ws.strike_band_low, spot * ws.strike_band_high)
        chains, symbols = _chain_rows(chain)
        years = ws.target_dte / 365
        rate = risk_free_rate(self.s, self.store, now, years).rate
        budget = self.s.risk.weekly_spread.per_trade_risk_pct * day.start_equity
        dec = build_call_spread(today, TrendParams(ws.target_dte, ws.long_delta, ws.short_delta), spot, chains, rate,
                                risk_budget=budget, dte_tolerance=ws.dte_tolerance)
        if dec.spread is None:
            log.info("weekly spread not built: %s", dec.reason)
            return f"weekly spread not built: {dec.reason}"
        sp = dec.spread
        lsym, ssym = symbols[(sp.expiry, sp.long_call.strike)], symbols[(sp.expiry, sp.short_call.strike)]
        legs = (OptionLeg(lsym, self.underlying, "call", sp.long_call.strike, sp.expiry, 1, sp.long_call.ask),
                OptionLeg(ssym, self.underlying, "call", sp.short_call.strike, sp.expiry, -1, sp.short_call.bid))
        self._live_symbols |= {lsym, ssym}
        quotes, _ = self._leg_quotes({l.symbol for p in positions for l in p.legs} | {lsym, ssym}, now)
        req = TradeRequest(f"weekly-{now.astimezone(EASTERN):%Y%m%d-%H%M}", self.underlying, legs,
                           self.s.strategy.fee_per_contract * 4,
                           sp.contracts, strategy=WEEKLY)
        ctx = RiskContext(now, self.s, self.state, day, equity, level, Portfolio(positions), quotes,
                          {self.underlying: spot}, rate, self.stress, spot_age)
        approval = approve(req, ctx)
        what = (f"weekly call spread {sp.expiry} {sp.long_call.strike:g}/{sp.short_call.strike:g} "
                f"debit ${sp.debit:.2f}")
        if not approval.approved:
            return f"{what}: refused by risk engine: {', '.join(approval.reasons)}"
        order_legs = [OrderLeg(lsym, "buy", 1, "buy_to_open"), OrderLeg(ssym, "sell", 1, "sell_to_open")]
        res = self.orders.execute(
            req.request_id, req.request_id, "open", order_legs, approval.contracts,
            lambda: self._leg_quotes({lsym, ssym}, self.now())[0],
            sp.debit + self.s.execution.price_slack(sp.debit),
            still_approved=lambda: approval_is_valid(approval, self.now()), description=what)
        if res.filled_qty:
            self.book.open_position(self._filled_position(approval, res))
            alerts.send("Weekly call spread opened", f"{what}, {res.filled_qty} contract(s).")
        return f"{what}: {res.status} {res.filled_qty}/{approval.contracts}" + (f" ({res.reason})" if res.reason else "")

    def _close(self, pos: Position, reason: str, quotes: dict[str, LegQuote], emergency: bool) -> str:
        n = max(abs(l.quantity) for l in pos.legs)
        unit = [(l, l.quantity // n) for l in pos.legs]
        order_legs = [OrderLeg(l.symbol, "sell" if q > 0 else "buy", abs(q), "sell_to_close" if q > 0 else "buy_to_close")
                      for l, q in unit]
        from execution.orders import net_prices
        _, exe = net_prices(order_legs, quotes)
        if emergency:
            slack = 0.25
        elif reason in ("gap_stop", "early_assignment"):
            slack = self.s.risk.multiday.gap_exit_slack           # the market jumped: getting out matters more than price
        else:
            slack = self.s.execution.price_slack(exe)
        key = f"{pos.position_id}-close-{self.book.plan_count(pos.position_id, 'close')}"
        res = self.orders.execute(key, pos.position_id, "close", order_legs, n,
                                  lambda: self._leg_quotes({l.symbol for l in pos.legs}, self.now())[0], exe + slack)
        if res.filled_qty:
            entry_net = sum(q * l.entry_price for l, q in unit)            # signed per spread
            realized = -(entry_net + res.avg_net_price) * 100 * res.filled_qty - (pos.entry_fees + pos.exit_fees) * res.filled_qty / n
            if res.filled_qty >= n:
                extra = self.state._get("assignment_pnl", {})
                realized += extra.pop(pos.position_id, 0.0)          # premium kept + share unwind, after an assignment
                self.state._set("assignment_pnl", extra)
                self.state._set("force_close_positions",
                                [p for p in self.state._get("force_close_positions", []) if p != pos.position_id])
                self.book.mark_position_closed(pos.position_id, reason, realized)
            else:
                pos.legs = [OptionLeg(l.symbol, l.underlying, l.option_type, l.strike, l.expiration,
                                      q * (n - res.filled_qty), l.entry_price) for l, q in unit]
                self.book.update_position_legs(pos)
        return f"{pos.position_id} {reason}: {res.status} {res.filled_qty}/{n}" + (f" ({res.reason})" if res.reason else "")


def _chain_rows(chain) -> tuple[dict, dict]:
    """Alpaca chain snapshot -> {expiry: [ChainRow]} and {(expiry, strike): call symbol}."""
    from collections import defaultdict
    from datetime import date as _date

    from strategies.iron_condor import ChainRow
    calls, puts = defaultdict(dict), defaultdict(dict)
    for q in chain:
        occ = q.symbol[-15:]                     # YYMMDD + C/P + strike x 1000 (8 digits)
        exp = _date(2000 + int(occ[:2]), int(occ[2:4]), int(occ[4:6]))
        strike = int(occ[7:]) / 1000
        (calls if occ[6] == "C" else puts)[exp][strike] = q
    rows, symbols = {}, {}
    for exp, by_strike in calls.items():
        out = []
        for k in sorted(by_strike):
            c, p = by_strike[k], puts[exp].get(k)
            out.append(ChainRow(k, c.bid, c.ask, c.implied_volatility, c.delta,
                                p.bid if p else None, p.ask if p else None,
                                p.implied_volatility if p else None, p.delta if p else None))
            symbols[(exp, k)] = c.symbol
        rows[exp] = out
    return rows, symbols
