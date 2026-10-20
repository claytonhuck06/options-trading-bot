"""Phase 3: iron condor backtest on OptionsDX end-of-day chains, exactly as pre-registered.

Rules: docs/research/iron_condor_preregistration.md (sections B–E, Amendments 3–4, Phase 3 implementation notes).
  * All 162 settings run on discovery entries (2016–2021) only; one is chosen by smoothed score;
    only that one is run through the confirmation period (2022–2023 with the free data).
  * Fills at bid/ask (+ cost-stress and optimistic variants), $0.05 per contract per leg per side.
  * Exits at each daily 16:00 snapshot: stop, profit target, time exit, hard exit, early-assignment guard.
    A leg failing the data check means no exit that day and the last valid mark is carried forward.
"""

from __future__ import annotations

import math
import sqlite3
from bisect import bisect_left
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from itertools import product
from statistics import mean, median, pstdev

from strategies.iron_condor import Condor, CondorParams, Decision

MULT = 100
FEE = 0.05                       # $ per contract per leg per side
STRESS = 0.25                    # extra fraction of each leg's spread (worse)
OPTIMISTIC = 0.25                # fraction of each leg's spread toward mid (better), report only
HARD_EXIT_DTE = 7
ASSIGN_PUT_EXTRINSIC = 0.05
DIVIDEND_FRACTION = 0.0035       # quarterly SPY dividend ≈ 0.35% of spot (Phase 3 note)
EQUITY = 100_000.0
MAX_OPEN = 3
EPS = 1e-9                       # prices are in cents; a price exactly at a trigger counts as reaching it
DISCOVERY = (date(2016, 1, 1), date(2021, 12, 31))
CONFIRMATION_START = date(2022, 1, 1)
MIN_TRADES_TO_CHOOSE = 60
MIN_TRADES_NEIGHBOUR = 30
MIN_QUALIFYING_NEIGHBOURS = 2
MAX_DD = 0.10
T_STAT_BAR = 2.0
DROP_BEST = 0.05
CRASH_WINDOWS = [("Feb 2018", date(2018, 2, 1), date(2018, 2, 28)),
                 ("COVID 2020", date(2020, 2, 1), date(2020, 4, 30)),
                 ("2022 bear", date(2022, 1, 1), date(2022, 12, 31)),
                 ("Aug 2024", date(2024, 8, 1), date(2024, 8, 31)),
                 ("Apr 2025", date(2025, 4, 1), date(2025, 4, 30))]

GRID_DTE = (30, 45, 60)
GRID_DELTA = (0.10, 0.16, 0.20)
GRID_WIDTH = (5.0, 10.0)
GRID_PT = (0.25, 0.50, None)
GRID_STOP = (1.5, 2.0, None)


@dataclass(frozen=True)
class ExitParams:
    profit_target: float | None
    stop_multiple: float | None
    time_exit_dte: int = 21


@dataclass(frozen=True)
class Setting:
    entry: CondorParams
    exit: ExitParams

    def label(self) -> str:
        pt = "hold" if self.exit.profit_target is None else f"{self.exit.profit_target:.0%}"
        st = "none" if self.exit.stop_multiple is None else f"{self.exit.stop_multiple:g}×"
        return (f"{self.entry.target_dte} DTE · {self.entry.short_delta:.2f}Δ · ${self.entry.width:g} · "
                f"target {pt} · stop {st}")


def full_grid() -> list[Setting]:
    return [Setting(CondorParams(d, x, w), ExitParams(p, s))
            for d, x, w, p, s in product(GRID_DTE, GRID_DELTA, GRID_WIDTH, GRID_PT, GRID_STOP)]


# --- Market data access ------------------------------------------------------------------------

@dataclass(frozen=True)
class LegQuote:
    bid: float | None
    ask: float | None

    def ok(self) -> bool:
        """Data check for marking and exits: bid and ask present, not crossed, not locked. A long leg with no bid
        is fine (it's sold at 0); the short-leg bid rule applies only at entry (C1.6)."""
        if self.bid is None or self.ask is None or self.ask <= 0 or self.bid < 0:
            return False
        return not (self.bid > self.ask or (self.bid == self.ask and self.bid > 0))

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2

    @property
    def spread(self) -> float:
        return self.ask - self.bid


class Market:
    """Reads leg quotes and spot from vol_history.db, with caching."""

    def __init__(self, vol: sqlite3.Connection, trading_days: list[date]):
        self.vol = vol
        self.days = trading_days
        self.spot = {date.fromisoformat(d): u for d, u in vol.execute(
            "SELECT quote_date, MAX(underlying) FROM eod_chain GROUP BY quote_date")}
        self._paths: dict = {}

    def path(self, c: Condor) -> dict[date, tuple[LegQuote, LegQuote, LegQuote, LegQuote]]:
        """Quotes of (short put, long put, short call, long call) on every day from entry to expiry."""
        key = (c.expiry, c.short_put.strike, c.long_put.strike, c.short_call.strike, c.long_call.strike)
        if key in self._paths:
            return self._paths[key]
        rows = self.vol.execute(
            "SELECT quote_date, strike, c_bid, c_ask, p_bid, p_ask FROM eod_chain WHERE expiry=? AND quote_date>=? "
            "AND quote_date<=? AND strike IN (?,?,?,?)",
            (c.expiry.isoformat(), c.day.isoformat(), c.expiry.isoformat(), *key[1:])).fetchall()
        by_day: dict[date, dict[float, tuple]] = defaultdict(dict)
        for d, k, cb, ca, pb, pa in rows:
            by_day[date.fromisoformat(d)][k] = (cb, ca, pb, pa)
        out = {}
        for d, ks in by_day.items():
            if not all(k in ks for k in key[1:]):
                continue
            sp, lp, sc, lc = ks[key[1]], ks[key[2]], ks[key[3]], ks[key[4]]
            out[d] = (LegQuote(sp[2], sp[3]), LegQuote(lp[2], lp[3]), LegQuote(sc[0], sc[1]), LegQuote(lc[0], lc[1]))
        self._paths[key] = out
        return out


def ex_dividend_dates(years: range) -> set[date]:
    """Third Friday of Mar/Jun/Sep/Dec (the existing project rule)."""
    out = set()
    for y in years:
        for m in (3, 6, 9, 12):
            d = date(y, m, 15)
            while d.weekday() != 4:
                d += timedelta(days=1)
            out.add(d)
    return out


# --- One position --------------------------------------------------------------------------------

@dataclass
class Trade:
    setting: Setting
    condor: Condor
    exit_day: date | None = None
    exit_reason: str | None = None
    exit_cost: float | None = None            # $/share, executable
    exit_spreads: float = 0.0
    pnl: float = 0.0
    pnl_stress: float = 0.0
    pnl_optimistic: float = 0.0
    skew: float | None = None
    estimated: bool = False

    @property
    def n(self) -> int:
        return self.condor.contracts

    @property
    def entry_spreads(self) -> float:
        return sum(l.ask - l.bid for l in self.condor.legs)

    @property
    def fees_one_side(self) -> float:
        return FEE * 4 * self.n

    @property
    def planned_max_loss(self) -> float:
        return self.condor.planned_max_loss + 2 * self.fees_one_side

    @property
    def loss_allowance(self) -> float:
        """Bar 3: planned max loss + fees + $0.05/share early-assignment slippage."""
        return self.planned_max_loss + 0.05 * MULT * self.n

    def close(self, day: date, reason: str, cost: float, spreads: float) -> None:
        c, n = self.condor, self.n
        self.exit_day, self.exit_reason, self.exit_cost, self.exit_spreads = day, reason, cost, spreads
        fees = 2 * self.fees_one_side
        self.pnl = (c.credit - cost) * MULT * n - fees
        credit_s = c.credit - STRESS * self.entry_spreads
        cost_s = cost + STRESS * spreads
        self.pnl_stress = (credit_s - cost_s) * MULT * n - fees
        credit_o = c.credit + OPTIMISTIC * self.entry_spreads
        cost_o = max(0.0, cost - OPTIMISTIC * spreads)
        self.pnl_optimistic = (credit_o - cost_o) * MULT * n - fees

    def unrealized(self, mark: float) -> float:
        return (self.condor.credit - mark) * MULT * self.n - self.fees_one_side


def cost_to_close(q: tuple[LegQuote, LegQuote, LegQuote, LegQuote]) -> tuple[float, float] | None:
    """Buy back shorts at the ask, sell longs at the bid. None if any leg fails the data check."""
    sp, lp, sc, lc = q
    if not all(x.ok() for x in q):
        return None
    return max(0.0, sp.ask + sc.ask - lp.bid - lc.bid), sum(x.spread for x in q)


# --- Simulation ----------------------------------------------------------------------------------

@dataclass
class RunResult:
    setting: Setting
    trades: list[Trade]
    equity: dict[date, float] = field(default_factory=dict)
    unfinished: int = 0


def simulate(setting: Setting, decisions: dict[date, Decision], market: Market, entry_from: date, entry_to: date,
             ex_div: set[date], entry_filter=None, intraday=None) -> RunResult:
    """Run one setting. `decisions`: the builder's decision per day for this setting's entry params.
    `entry_filter(day) -> bool` adds a report-only filter. `intraday(trade, day, prev_quotes) -> cost|None`
    estimates an intraday stop (D2)."""
    days = [d for d in market.days if d >= entry_from]
    ex = setting.exit
    open_: list[Trade] = []
    last_mark: dict[int, float] = {}
    done: list[Trade] = []
    weeks_used: set = set()
    realized = 0.0
    equity: dict[date, float] = {}
    pre_ex_div = {market.days[i - 1] for i in range(1, len(market.days)) if market.days[i] in ex_div}
    for day in days:
        if not open_ and day > entry_to:
            break
        # 1) exits
        for t in list(open_):
            c = t.condor
            if day <= c.day:
                continue
            path = market.path(c)
            q = path.get(day)
            res = cost_to_close(q) if q else None
            dte = (c.expiry - day).days
            if res is None:
                if day >= c.expiry:                       # no usable quote to the end: settle at intrinsic
                    s = market.spot.get(day) or market.spot.get(c.expiry)
                    if s is not None:
                        intrinsic = max(0.0, c.short_put.strike - s) - max(0.0, c.long_put.strike - s) \
                            + max(0.0, s - c.short_call.strike) - max(0.0, s - c.long_call.strike)
                        t.close(day, "expiry_settlement", intrinsic, 0.0)
                        realized += t.pnl
                        open_.remove(t)
                        done.append(t)
                continue
            cost, spreads = res
            if intraday is not None and ex.stop_multiple is not None:
                est = intraday(t, day)
                if est is not None and est >= (1 + ex.stop_multiple) * c.credit - EPS:
                    stop_cost = (1 + ex.stop_multiple) * c.credit
                    t.close(day, "stop_estimated_intraday", stop_cost, spreads)
                    t.estimated = True
                    realized += t.pnl
                    open_.remove(t)
                    done.append(t)
                    continue
            reason = None
            if ex.stop_multiple is not None and cost >= (1 + ex.stop_multiple) * c.credit - EPS:
                reason = "stop"
            elif ex.profit_target is not None and cost <= (1 - ex.profit_target) * c.credit + EPS:
                reason = "profit_target"
            elif dte <= ex.time_exit_dte:
                reason = "time_exit"
            elif dte <= HARD_EXIT_DTE:
                reason = "hard_exit"
            else:
                s = market.spot.get(day)
                sp, _, sc, _ = q
                if s is not None:
                    if day in pre_ex_div and s > c.short_call.strike and \
                            sc.mid - (s - c.short_call.strike) < DIVIDEND_FRACTION * s:
                        reason = "assignment_guard_call"
                    elif s < c.short_put.strike and sp.mid - (c.short_put.strike - s) < ASSIGN_PUT_EXTRINSIC:
                        reason = "assignment_guard_put"
            if reason:
                t.close(day, reason, cost, spreads)
                realized += t.pnl
                open_.remove(t)
                done.append(t)
            else:
                last_mark[id(t)] = cost
        # 2) entry
        wk = day.isocalendar()[:2]
        if entry_from <= day <= entry_to and len(open_) < MAX_OPEN and wk not in weeks_used:
            d = decisions.get(day)
            if d is not None and d.condor is not None and (entry_filter is None or entry_filter(day)):
                t = Trade(setting, d.condor, skew=d.details.get("skew_25d"))
                open_.append(t)
                # entry-day mark: what it would cost to close right away
                q0 = market.path(d.condor).get(day)
                r0 = cost_to_close(q0) if q0 else None
                last_mark[id(t)] = r0[0] if r0 else d.condor.credit
                weeks_used.add(wk)
        equity[day] = EQUITY + realized + sum(t.unrealized(last_mark[id(t)]) for t in open_)
    return RunResult(setting, done, equity, unfinished=len(open_))


# --- Statistics ----------------------------------------------------------------------------------

def t_stat(xs: list[float]) -> float:
    if len(xs) < 2:
        return float("nan")
    sd = pstdev(xs) * math.sqrt(len(xs) / (len(xs) - 1))
    return mean(xs) / (sd / math.sqrt(len(xs))) if sd > 0 else float("nan")


def max_drawdown(equity: dict[date, float], start: date | None = None, end: date | None = None) -> tuple[float, date | None]:
    peak, worst, when = -math.inf, 0.0, None
    for d in sorted(equity):
        if (start and d < start) or (end and d > end):
            continue
        v = equity[d]
        peak = max(peak, v)
        if peak - v > worst:
            worst, when = peak - v, d
    return worst, when


def monthly_pnl(equity: dict[date, float]) -> list[float]:
    prev = EQUITY
    out = defaultdict(float)
    for d in sorted(equity):
        out[(d.year, d.month)] += equity[d] - prev
        prev = equity[d]
    return [out[k] for k in sorted(out)]


def score(trades: list[Trade]) -> float | None:
    if len(trades) < MIN_TRADES_TO_CHOOSE:
        return None
    return mean(t.pnl / t.planned_max_loss for t in trades)


def neighbours(s: Setting, dims: tuple[str, ...]) -> list[Setting]:
    grids = {"dte": GRID_DTE, "delta": GRID_DELTA, "width": GRID_WIDTH, "pt": GRID_PT, "stop": GRID_STOP}
    vals = {"dte": s.entry.target_dte, "delta": s.entry.short_delta, "width": s.entry.width,
            "pt": s.exit.profit_target, "stop": s.exit.stop_multiple}
    out = []
    for dim in dims:
        g = grids[dim]
        i = g.index(vals[dim])
        for j in (i - 1, i + 1):
            if 0 <= j < len(g):
                v = dict(vals, **{dim: g[j]})
                out.append(Setting(CondorParams(v["dte"], v["delta"], v["width"]), ExitParams(v["pt"], v["stop"])))
    return out


def choose(discovery: dict[Setting, RunResult]) -> tuple[Setting | None, dict[Setting, float | None], dict[Setting, float | None]]:
    """D3 with Amendment 3: raw score needs ≥60 trades; smoothing averages over neighbours that have a score."""
    raw = {s: score(r.trades) for s, r in discovery.items()}
    smooth = {}
    for s, v in raw.items():
        if v is None:
            smooth[s] = None
            continue
        vals = [v] + [raw[n] for n in neighbours(s, ("dte", "delta", "width", "pt", "stop")) if raw.get(n) is not None]
        smooth[s] = mean(vals)
    eligible = [s for s, v in smooth.items() if v is not None]
    best = max(eligible, key=lambda s: smooth[s]) if eligible else None
    return best, raw, smooth
