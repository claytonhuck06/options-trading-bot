"""The risk engine: the only component allowed to approve a trade.

Every request is checked against the configured limits, the current account
state, the kill switch, and the portfolio as it would look after the trade,
including historical stress scenarios. If the trade is too large it is
shrunk; if it cannot fit, it is refused. Approvals expire after a few
seconds, so each order needs a fresh one, and a request id is never
approved twice.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from core.config import Settings
from risk.position_sizing import size_contracts
from risk.positions import (
    MULTIPLIER, LegQuote, OptionLeg, Portfolio, Position, exposure, max_defined_loss, model_value,
)
from risk.state import DaySnapshot, Level, RiskStateStore
from risk.stress import StressCalibration

EASTERN = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class TradeRequest:
    request_id: str
    underlying: str
    legs: tuple[OptionLeg, ...]        # quantities for ONE spread (+1 long, -1 short)
    fees_per_spread: float             # $ round trip
    requested_contracts: int
    anchor_symbol: str | None = None
    multi_day: bool = False            # held for days: sized from the separate multi-day risk pool
    strategy: str = "mispricing"       # weekly_spread: its own risk pool (always held overnight)


WEEKLY = "weekly_spread"


def pool_key(strategy: str, overnight: bool) -> str:
    """Each strategy draws on its own risk pool: same-day, multi-day, or the weekly call spread."""
    return WEEKLY if strategy == WEEKLY else ("multiday" if overnight else "sameday")


def _pool_limits(lim, key: str) -> tuple[float, int]:
    """(per-trade risk fraction, max open) of a pool."""
    if key == WEEKLY:
        return lim.weekly_spread.per_trade_risk_pct, lim.weekly_spread.max_open_positions
    if key == "multiday":
        return lim.multiday.per_trade_risk_pct, lim.multiday.max_open_positions
    return lim.per_trade_risk_pct, lim.max_concurrent_positions


@dataclass
class RiskContext:
    now: datetime
    settings: Settings
    state: RiskStateStore
    day: DaySnapshot
    equity: float                      # current, incl. unrealised P&L
    level: Level                       # from the kill switch this cycle
    portfolio: Portfolio
    quotes: dict[str, LegQuote]        # every open leg and every requested leg
    spots: dict[str, float]            # underlying -> price
    rate: float
    stress: StressCalibration
    data_age_seconds: float | None


@dataclass
class Decision:
    request_id: str
    approved: bool
    contracts: int
    reasons: list[str] = field(default_factory=list)
    valid_until: datetime | None = None
    position: Position | None = None   # the position (with its exit plan) if executed as approved
    stress_worst: float | None = None
    stress_scenario: str | None = None


def _scaled(legs: tuple[OptionLeg, ...], n: int) -> list[OptionLeg]:
    return [OptionLeg(l.symbol, l.underlying, l.option_type, l.strike, l.expiration, l.quantity * n, l.entry_price) for l in legs]


def _proposed_position(req: TradeRequest, n: int, ctx: RiskContext) -> Position:
    lim = ctx.settings.risk
    pos = Position(req.request_id, req.underlying, _scaled(req.legs, n), ctx.now, 0.0, 0.0, 0.0, req.anchor_symbol,
                   entry_fees=req.fees_per_spread * n / 2, exit_fees=req.fees_per_spread * n / 2)
    pos.max_loss = max_defined_loss(pos)
    pos.profit_target = lim.take_profit_pct_of_risk * pos.max_loss
    pos.stop_loss = lim.stop_loss_pct_of_max_loss * pos.max_loss
    pos.hold_overnight = req.multi_day or req.strategy == WEEKLY
    pos.strategy = req.strategy
    return pos


def _portfolio_metrics(positions: list[Position], ctx: RiskContext):
    lim = ctx.settings.risk
    spy = ctx.spots.get("SPY") or next(iter(ctx.spots.values()))
    delta = vega = 0.0
    for p in positions:
        e = exposure(p, ctx.quotes, ctx.spots[p.underlying], ctx.now, ctx.rate, lim.underlying_betas.get(p.underlying, 1.0), spy)
        delta += e.delta_dollars
        vega += e.vega
    for h in ctx.portfolio.hedges:
        delta += h.shares * ctx.spots[h.symbol] * lim.underlying_betas.get(h.symbol, 1.0)

    worst, worst_name = 0.0, None
    for sc in ctx.stress.scenarios():
        pnl = 0.0
        for p in positions:
            beta = lim.underlying_betas.get(p.underlying, 1.0)
            s = ctx.spots[p.underlying]
            base = model_value(p, ctx.quotes, s, ctx.now, ctx.rate)
            pnl += model_value(p, ctx.quotes, s, ctx.now, ctx.rate, sc.spot_shift * beta, sc.vol_shift) - base
        for h in ctx.portfolio.hedges:
            pnl += h.shares * ctx.spots[h.symbol] * sc.spot_shift * lim.underlying_betas.get(h.symbol, 1.0)
        if pnl < worst:
            worst, worst_name = pnl, sc.name
    return delta, vega, -worst, worst_name


def approve(req: TradeRequest, ctx: RiskContext) -> Decision:
    s, lim = ctx.settings, ctx.settings.risk
    today = ctx.now.astimezone(EASTERN).date()
    reasons: list[str] = []

    def refuse(*why: str, contracts: int = 0) -> Decision:
        d = Decision(req.request_id, False, contracts, list(dict.fromkeys(reasons + list(why))))
        if not ctx.state.already_decided(req.request_id):
            ctx.state.record_decision(req.request_id, False, 0, d.reasons, None)
        return d

    # --- gatekeeping: these refuse outright --------------------------------
    if ctx.state.already_decided(req.request_id):
        return Decision(req.request_id, False, 0, ["duplicate_request"])
    if ctx.level >= Level.NO_NEW_ENTRIES:
        reasons.append(f"kill_switch_{ctx.level.name.lower()}")
    t = ctx.now.astimezone(EASTERN).time()
    if not (time.fromisoformat(s.schedule.scan_start) <= t < time.fromisoformat(s.schedule.no_new_positions_after)):
        reasons.append("outside_entry_window")
    if ctx.data_age_seconds is None or ctx.data_age_seconds > s.strategy.max_quote_age_seconds:
        reasons.append("market_data_stale")
    overnight = req.multi_day or req.strategy == WEEKLY
    key = pool_key(req.strategy, overnight)
    per_trade_pct, max_open = _pool_limits(lim, key)
    pool = [p for p in ctx.portfolio.positions if pool_key(p.strategy, p.hold_overnight) == key]
    if overnight and not lim.allow_overnight_positions:
        reasons.append("overnight_positions_not_allowed")
    if len(pool) >= max_open:
        reasons.append("max_concurrent_positions")
    if any(l.symbol not in ctx.quotes for l in req.legs) or req.underlying not in ctx.spots:
        reasons.append("missing_quotes")
    probe = _proposed_position(req, 1, ctx) if not reasons else None
    if probe is not None:
        if probe.uncovered_shorts():
            reasons.append("uncovered_short_option")
        if not lim.allow_overnight_positions and probe.expiration <= today:
            reasons.append("expires_today")
        if not (probe.max_loss > 0 and math.isfinite(probe.max_loss)):
            reasons.append("max_loss_undefined")
    pnl_today = ctx.equity - ctx.day.start_equity
    daily_limit = lim.daily_loss_limit_pct * ctx.day.start_equity
    if pnl_today <= -daily_limit:
        reasons.append("daily_loss_limit_reached")
    if pnl_today >= lim.daily_profit_stop_pct * ctx.day.start_equity:
        reasons.append("daily_profit_ceiling_reached")
    if reasons:
        return refuse()

    # --- sizing: shrink until every portfolio limit holds --------------------
    # Each strategy draws on its own risk pool (same-day, multi-day, weekly call spread).
    eq = ctx.day.start_equity
    used = sum(p.max_loss for p in pool)
    per_trade = per_trade_pct * eq
    if key == "sameday":
        remaining = lim.aggregate_planned_risk_pct * eq - used
    else:
        remaining = per_trade * max_open - used
    if size_contracts(probe.max_loss, per_trade, math.inf) < 1:
        return refuse("trade_larger_than_per_trade_limit")
    n = min(req.requested_contracts, size_contracts(probe.max_loss, per_trade, remaining))
    if n < 1:
        return refuse("risk_budget_exhausted")

    # A position held overnight has a worst case equal to its defined max loss, already capped by its own
    # pool's budget, so its stress loss is checked against that budget rather than today's intraday loss
    # allowance. Open overnight positions are counted the same way (as the daily loss trigger does).
    allowance = (daily_limit + min(pnl_today, 0.0)) * lim.stress_loss_pct_of_daily_allowance
    allowance += sum(p.max_loss for p in ctx.portfolio.positions if p.hold_overnight)
    if overnight:
        allowance += per_trade
    last_fail: list[str] = []
    while n >= 1:
        pos = _proposed_position(req, n, ctx)
        delta, vega, stress_loss, stress_name = _portfolio_metrics(ctx.portfolio.positions + [pos], ctx)
        fails = []
        if abs(delta) > lim.max_net_delta_pct * eq:
            fails.append("portfolio_delta_limit")
        if abs(vega) > lim.max_net_vega_pct * eq:
            fails.append("portfolio_vega_limit")
        if stress_loss > allowance:
            fails.append("stress_loss_exceeds_daily_allowance")
        if not fails:
            valid = ctx.now + timedelta(seconds=lim.approval_valid_seconds)
            ctx.state.record_decision(req.request_id, True, n, [], valid)
            notes = ["resized"] if n < req.requested_contracts else []
            return Decision(req.request_id, True, n, notes, valid, pos, stress_loss, stress_name)
        last_fail = fails
        n -= 1
    return refuse(*last_fail)


def approval_is_valid(decision: Decision, now: datetime) -> bool:
    return decision.approved and decision.valid_until is not None and now <= decision.valid_until
