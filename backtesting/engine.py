"""Event-driven backtest on real historical bid/ask, reusing the production modules.

For each trading day, minute by minute:
  1. Orders decided in the previous minute fill now, at THIS minute's executable prices
     (buy at the ask, sell at the bid) plus fees. An entry is skipped if the price has
     moved past the approved price plus the allowed deterioration.
  2. Open positions are marked at executable prices; the kill switch is evaluated.
  3. Exit rules run; any exit fills next minute.
  4. At decision times (every N minutes in the entry window) the snapshot is priced,
     scanned, and the best eligible spread goes to the risk engine; if approved it
     fills next minute.
Pricing is the slow part, so each day's pricing results are computed once and cached.
"""

from __future__ import annotations

import os
import pickle
import sqlite3
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from core.config import PROJECT_ROOT, Settings
from data.storage.db import MarketDataStore
from pricing.engine import PricingResult, price_snapshot
from pricing.rates import risk_free_rate
from risk.exits import evaluate_exit
from risk.kill_switch import HealthInputs, evaluate
from risk.limits import compute_daily_budget
from risk.positions import LegQuote, OptionLeg, Portfolio, Position, max_defined_loss, unrealized_pnl
from risk.risk_engine import RiskContext, TradeRequest, approve
from risk.state import Level, RiskStateStore
from risk.stress import StressCalibration
from strategies.relative_value import scan

from backtesting.data import EASTERN, DayData, load_day

CACHE_DIR = PROJECT_ROOT / "data" / "storage" / "bt_cache"


# --- pricing cache ------------------------------------------------------------------

def _decision_times(day: date, settings: Settings, every: int) -> list[datetime]:
    start = datetime.combine(day, time.fromisoformat(settings.schedule.scan_start), EASTERN)
    stop = datetime.combine(day, time.fromisoformat(settings.schedule.no_new_positions_after), EASTERN)
    out, t = [], start
    while t < stop:
        out.append(t.astimezone(ZoneInfo("UTC")))
        t += timedelta(minutes=every)
    return out


def price_day(day: date, settings: Settings, every: int, history_path: str, rates_path: str,
              opra_dir: str | None = None) -> dict[datetime, PricingResult]:
    """Price every decision time of one day (cached on disk)."""
    cache = CACHE_DIR / f"{day.isoformat()}_{every}m.pkl"
    if cache.is_file():
        return pickle.loads(cache.read_bytes())
    history = sqlite3.connect(history_path)
    rates = MarketDataStore(rates_path)
    data = load_day(day, history, Path(opra_dir) if opra_dir else None)
    out = {}
    for t in _decision_times(day, settings, every):
        snap = data.snapshot(t)
        if snap is None or not snap.options:
            continue

        def rate_for(years, t=t):
            info = risk_free_rate(settings, rates, t, years)
            return info.rate, info.source

        out[t] = price_snapshot(snap, settings.pricing, rate_for)
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(pickle.dumps(out))
    history.close()
    rates.close()
    return out


def build_cache(days: list[date], settings: Settings, every: int, workers: int | None = None) -> None:
    history_path = str(PROJECT_ROOT / settings.storage.history_path)
    rates_path = str(PROJECT_ROOT / settings.storage.sqlite_path)
    todo = [d for d in days if not (CACHE_DIR / f"{d.isoformat()}_{every}m.pkl").is_file()]
    with ProcessPoolExecutor(max_workers=workers or max(os.cpu_count() - 1, 1)) as pool:
        list(pool.map(price_day, todo, [settings] * len(todo), [every] * len(todo),
                      [history_path] * len(todo), [rates_path] * len(todo)))


# --- simulation -------------------------------------------------------------------------

@dataclass
class Trade:
    day: date
    opened: datetime
    closed: datetime | None
    description: str
    structure: str
    contracts: int
    entry_net: float               # per share, signed (+ debit)
    entry_mid: float
    exit_net: float | None = None
    exit_mid: float | None = None
    pnl: float | None = None       # $ after fees
    fees: float = 0.0
    reason: str | None = None
    edge_z: float = 0.0
    expected_pnl: float = 0.0
    max_loss: float = 0.0


@dataclass
class BacktestResult:
    trades: list[Trade] = field(default_factory=list)
    daily: list[tuple[date, float, float]] = field(default_factory=list)    # (day, start equity, end equity)
    refusals: dict[str, int] = field(default_factory=dict)
    skipped_fills: int = 0
    kill_switch_days: dict[str, int] = field(default_factory=dict)


def _exec_net(legs: list[OptionLeg], rows: dict, closing: bool) -> tuple[float, float] | None:
    """(executable, mid) net per spread unit at this minute. Opening: buy longs at ask, sell shorts at bid.
    Closing: sell longs at bid, buy shorts back at ask. Signed + = we pay."""
    n = max(abs(l.quantity) for l in legs)
    exe = mid = 0.0
    for l in legs:
        q = rows.get(l.symbol)
        if q is None:
            return None
        unit = l.quantity / n
        bid, ask = q
        if (unit > 0) != closing:            # buying this leg now
            exe += abs(unit) * ask
        else:
            exe -= abs(unit) * bid
        mid += (abs(unit) if (unit > 0) != closing else -abs(unit)) * (bid + ask) / 2
    return exe, mid


class Backtest:
    def __init__(self, settings: Settings, stress: StressCalibration, every: int = 15, opra_dir: Path | None = None):
        self.s, self.stress, self.every = settings, stress, every
        self.history = sqlite3.connect(str(PROJECT_ROOT / settings.storage.history_path))
        self.opra_dir = opra_dir

    def run(self, days: list[date], equity0: float = 20_000.0) -> BacktestResult:
        s = self.s
        res = BacktestResult()
        state = RiskStateStore(MarketDataStore(":memory:"))
        equity = equity0
        fee = s.strategy.fee_per_contract
        for day in days:
            pricings = price_day(day, s, self.every, str(PROJECT_ROOT / s.storage.history_path),
                                 str(PROJECT_ROOT / s.storage.sqlite_path), str(self.opra_dir) if self.opra_dir else None)
            data = load_day(day, self.history, self.opra_dir)
            dsnap = state.start_day(day, equity)
            realized = 0.0
            open_pos: list[tuple[Position, Trade]] = []
            pending_entry = None
            pending_exits: list[tuple[Position, Trade, str]] = []
            last_iv: dict[str, float] = {}
            last_pricing: PricingResult | None = None
            levels_seen = set()

            for t in data.minutes():
                rows_df = data.quotes_at(t)
                rows = {sym: (b, a) for sym, b, a in rows_df[["symbol", "bid", "ask"]].itertuples(index=False) if b > 0 and a >= b}

                # 1. fills decided last minute
                still = []
                for pos, tr, why in pending_exits:
                    px = _exec_net(pos.legs, rows, closing=True)
                    if px is None:
                        still.append((pos, tr, why))
                        continue
                    n = tr.contracts
                    exit_fees = fee * len(pos.legs) * n
                    tr.closed, tr.exit_net, tr.exit_mid, tr.reason = t, px[0], px[1], why
                    tr.fees += exit_fees
                    tr.pnl = -(tr.entry_net + px[0]) * 100 * n - tr.fees
                    realized += tr.pnl
                    open_pos = [(p, x) for p, x in open_pos if p is not pos]
                    res.trades.append(tr)
                pending_exits = still

                if pending_entry is not None:
                    dec, req, cand, approved_net = pending_entry
                    pending_entry = None
                    legs1 = [OptionLeg(l.symbol, l.underlying, l.option_type, l.strike, l.expiration, l.quantity, 0.0) for l in req.legs]
                    px = _exec_net(legs1, rows, closing=False)
                    if px is None or px[0] > approved_net + s.execution.max_price_deterioration + 1e-9:
                        res.skipped_fills += 1
                    else:
                        n = dec.contracts
                        pos = dec.position
                        fill = {sym: (rows[sym][1] if q > 0 else rows[sym][0]) for sym, q in ((l.symbol, l.quantity) for l in req.legs)}
                        pos.legs = [OptionLeg(l.symbol, l.underlying, l.option_type, l.strike, l.expiration, l.quantity, fill[l.symbol])
                                    for l in pos.legs]
                        pos.opened_at = t
                        pos.max_loss = max_defined_loss(pos)
                        pos.profit_target = s.risk.take_profit_pct_of_risk * pos.max_loss
                        pos.stop_loss = s.risk.stop_loss_pct_of_max_loss * pos.max_loss
                        tr = Trade(day, t, None, cand.spread.describe(), cand.spread.structure, n, px[0], px[1],
                                   fees=fee * len(req.legs) * n, edge_z=cand.edge_z, expected_pnl=cand.expected_pnl * n,
                                   max_loss=pos.max_loss)
                        open_pos.append((pos, tr))

                # 2. mark to market + kill switch
                pricing = pricings.get(t)
                if pricing is not None:
                    last_pricing = pricing
                    for c in pricing.contracts:
                        if c.fair_iv:
                            last_iv[c.option.symbol] = c.fair_iv
                quotes = {sym: LegQuote(b, a, last_iv.get(sym, 0.2), t) for sym, (b, a) in rows.items()}
                live = [(p, tr) for p, tr in open_pos if all(l.symbol in quotes for l in p.legs)]
                unreal = sum(unrealized_pnl(p, quotes) for p, _ in live)
                eq_now = equity + realized + unreal
                ks = evaluate(HealthInputs(eq_now, len(open_pos), 0.0), dsnap, s.risk, state, day)
                levels_seen.update(ks.triggers)

                # 3. exits (fill next minute)
                pending_ids = {id(p) for p, _, _ in pending_exits}
                for pos, tr in live:
                    if id(pos) in pending_ids:
                        continue
                    z = _anchor_z(pos, last_pricing, quotes)
                    ex = evaluate_exit(pos, quotes, t, s, ks.level, z)
                    if ex.close:
                        pending_exits.append((pos, tr, ex.reason))

                # 4. entries at decision times
                if pricing is None or pending_entry is not None or ks.level != Level.NORMAL:
                    continue
                budget = compute_daily_budget(s.risk, dsnap.start_equity, state.high_water_mark())
                cands = scan(pricing, s, budget)
                best = next((c for c in cands if c.eligible), None)
                if best is None:
                    continue
                sp = best.spread
                legs = tuple(OptionLeg(l.symbol, "SPY", l.contract.option.option_type, l.strike, l.contract.option.expiration,
                                       l.side, l.price) for l in sp.legs)
                req = TradeRequest(f"bt-{t.isoformat()}-{sp.long.symbol}-{sp.short.symbol}", "SPY", legs, best.fees_per_spread,
                                   best.contracts, sp.anchor)
                ctx_quotes = dict(quotes)
                for c in pricing.contracts:
                    if c.option.symbol in {l.symbol for l in legs}:
                        ctx_quotes[c.option.symbol] = LegQuote(c.option.bid, c.option.ask, c.fair_iv or 0.2, t)
                ctx = RiskContext(t, s, state, dsnap, eq_now, ks.level, Portfolio([p for p, _ in open_pos]), ctx_quotes,
                                  {"SPY": pricing.snapshot.spot}, pricing.expiries[0].rate if pricing.expiries else 0.04,
                                  self.stress, 0.0)
                dec = approve(req, ctx)
                if dec.approved:
                    pending_entry = (dec, req, best, sum(l.quantity * l.entry_price for l in legs))
                else:
                    for r in dec.reasons:
                        res.refusals[r] = res.refusals.get(r, 0) + 1

            # Anything still open at the last minute closes there (the market-close rule should prevent this).
            last_t = data.minutes()[-1] if data.minutes() else None
            for pos, tr in open_pos:
                rows_df = data.quotes_at(last_t)
                rows = {sym: (b, a) for sym, b, a in rows_df[["symbol", "bid", "ask"]].itertuples(index=False)}
                px = _exec_net(pos.legs, rows, closing=True) or (0.0, 0.0)
                tr.closed, tr.exit_net, tr.exit_mid, tr.reason = last_t, px[0], px[1], "end_of_day_forced"
                tr.fees += fee * len(pos.legs) * tr.contracts
                tr.pnl = -(tr.entry_net + px[0]) * 100 * tr.contracts - tr.fees
                realized += tr.pnl
                res.trades.append(tr)
            for k in levels_seen:
                res.kill_switch_days[k] = res.kill_switch_days.get(k, 0) + 1
            res.daily.append((day, equity, equity + realized))
            equity += realized
        return res


def _anchor_z(pos: Position, pricing: PricingResult | None, quotes: dict[str, LegQuote]) -> float | None:
    if pricing is None or not pos.anchor_symbol or pos.anchor_symbol not in quotes:
        return None
    c = next((c for c in pricing.contracts if c.option.symbol == pos.anchor_symbol), None)
    if c is None or c.fair_price is None or not c.price_uncertainty:
        return None
    anchor = next(l for l in pos.legs if l.symbol == pos.anchor_symbol)
    mid = quotes[pos.anchor_symbol].mid
    return ((c.fair_price - mid) if anchor.quantity > 0 else (mid - c.fair_price)) / c.price_uncertainty
