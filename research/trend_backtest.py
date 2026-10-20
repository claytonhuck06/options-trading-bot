"""SPY trend call spreads: backtest on OptionsDX end-of-day chains, exactly as pre-registered.

Rules: docs/research/spy_trend_preregistration.md.
  * All 144 settings run on discovery entries (2011–2018) only; one is chosen by smoothed score (condor D3 method);
    only that one is run through the confirmation period (2019–2023).
  * Fills at bid/ask (+ cost-stress and optimistic variants), $0.05 per contract per leg per side.
  * Exits at each daily 4:00 pm snapshot: stop, profit target, trend exit, time exit, hard exit, assignment guard.
  * Bar 8 reruns the chosen setting with no signal, to see whether the trend signal adds anything.
"""

from __future__ import annotations

import math
import sqlite3
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from itertools import product
from pathlib import Path
from statistics import mean

from research.condor_backtest import (
    DIVIDEND_FRACTION, EPS, FEE, MULT, OPTIMISTIC, STRESS, LegQuote, ex_dividend_dates, max_drawdown, monthly_pnl,
    t_stat,
)
from strategies.spy_trend import (
    SIGNAL_LIGHTS, SIGNAL_MOMENTUM, SIGNAL_NONE, CallSpread, SpreadDecision, TrendParams, TrendSignal,
    build_call_spread,
)

EQUITY = 100_000.0
MAX_OPEN = 3
TIME_EXIT_DTE = 14
HARD_EXIT_DTE = 7
WARMUP_START = date(2010, 1, 1)
DISCOVERY = (date(2011, 1, 3), date(2018, 12, 31))
CONFIRMATION_START = date(2019, 1, 1)
DATA_END = date(2023, 12, 29)
MIN_TRADES_TO_CHOOSE = 60
MIN_TRADES_NEIGHBOUR = 30
MIN_QUALIFYING_NEIGHBOURS = 2
MAX_DD = 0.10
T_STAT_BAR = 2.0
DROP_BEST = 0.05
CRASH_WINDOWS = [("Aug–Sep 2011", date(2011, 8, 1), date(2011, 9, 30)),
                 ("Aug 2015–Feb 2016", date(2015, 8, 1), date(2016, 2, 29)),
                 ("Feb 2018", date(2018, 2, 1), date(2018, 2, 28)),
                 ("Oct–Dec 2018", date(2018, 10, 1), date(2018, 12, 31)),
                 ("COVID 2020", date(2020, 2, 1), date(2020, 4, 30)),
                 ("2022 bear", date(2022, 1, 1), date(2022, 12, 31))]

GRID_SIGNAL = (SIGNAL_LIGHTS, SIGNAL_MOMENTUM)
GRID_DTE = (30, 45, 60)
GRID_LONG = (0.50, 0.70)
GRID_SHORT = (0.20, 0.30)
GRID_PT = (0.50, 1.00, None)
GRID_STOP = (0.50, None)


@dataclass(frozen=True)
class ExitParams:
    profit_target: float | None       # q: close when value ≥ (1 + q) × debit
    stop_loss: float | None           # L: close when value ≤ (1 − L) × debit


@dataclass(frozen=True)
class Setting:
    signal: str
    entry: TrendParams
    exit: ExitParams

    def label(self) -> str:
        pt = "hold" if self.exit.profit_target is None else f"+{self.exit.profit_target:.0%}"
        st = "none" if self.exit.stop_loss is None else f"−{self.exit.stop_loss:.0%}"
        return (f"{self.signal} · {self.entry.target_dte} DTE · long {self.entry.long_delta:.2f}Δ / "
                f"short {self.entry.short_delta:.2f}Δ · target {pt} · stop {st}")


def full_grid() -> list[Setting]:
    return [Setting(sig, TrendParams(d, l, s), ExitParams(p, st))
            for sig, d, l, s, p, st in product(GRID_SIGNAL, GRID_DTE, GRID_LONG, GRID_SHORT, GRID_PT, GRID_STOP)]


# --- Market data -----------------------------------------------------------------------------------

class Market:
    """Leg quotes and spot from vol_history.db, with caching."""

    def __init__(self, vol: sqlite3.Connection, trading_days: list[date]):
        self.vol = vol
        self.days = trading_days
        self.spot = {date.fromisoformat(d): u for d, u in vol.execute(
            "SELECT quote_date, MAX(underlying) FROM eod_chain GROUP BY quote_date")}
        self._paths: dict = {}

    def path(self, s: CallSpread) -> dict[date, tuple[LegQuote, LegQuote]]:
        """(long call, short call) quotes on every day from entry to expiry."""
        key = (s.expiry, s.long_call.strike, s.short_call.strike)
        if key in self._paths:
            return self._paths[key]
        rows = self.vol.execute(
            "SELECT quote_date, strike, c_bid, c_ask FROM eod_chain WHERE expiry=? AND quote_date>=? "
            "AND quote_date<=? AND strike IN (?,?)",
            (s.expiry.isoformat(), s.day.isoformat(), s.expiry.isoformat(), key[1], key[2])).fetchall()
        by_day: dict[date, dict[float, tuple]] = defaultdict(dict)
        for d, k, cb, ca in rows:
            by_day[date.fromisoformat(d)][k] = (cb, ca)
        out = {}
        for d, ks in by_day.items():
            if key[1] in ks and key[2] in ks:
                out[d] = (LegQuote(*ks[key[1]]), LegQuote(*ks[key[2]]))
        self._paths[key] = out
        return out


def value_to_close(q: tuple[LegQuote, LegQuote]) -> tuple[float, float] | None:
    """Sell the long call at the bid, buy back the short at the ask. None if either leg fails the data check."""
    lc, sc = q
    if not (lc.ok() and sc.ok()):
        return None
    return max(0.0, lc.bid - sc.ask), lc.spread + sc.spread


# --- One position ----------------------------------------------------------------------------------

@dataclass
class Trade:
    setting: Setting
    spread: CallSpread
    exit_day: date | None = None
    exit_reason: str | None = None
    exit_value: float | None = None
    pnl: float = 0.0
    pnl_stress: float = 0.0
    pnl_optimistic: float = 0.0
    gap_days: int = 0

    @property
    def n(self) -> int:
        return self.spread.contracts

    @property
    def entry_spreads(self) -> float:
        return sum(l.ask - l.bid for l in self.spread.legs)

    @property
    def fees_one_side(self) -> float:
        return FEE * 2 * self.n

    @property
    def planned_max_loss(self) -> float:
        return self.spread.planned_max_loss + 2 * self.fees_one_side

    @property
    def loss_allowance(self) -> float:
        return self.planned_max_loss + 0.05 * MULT * self.n

    def close(self, day: date, reason: str, value: float, spreads: float) -> None:
        s, n = self.spread, self.n
        self.exit_day, self.exit_reason, self.exit_value = day, reason, value
        fees = 2 * self.fees_one_side
        self.pnl = (value - s.debit) * MULT * n - fees
        self.pnl_stress = (max(0.0, value - STRESS * spreads) - (s.debit + STRESS * self.entry_spreads)) * MULT * n - fees
        self.pnl_optimistic = ((value + OPTIMISTIC * spreads) - max(0.0, s.debit - OPTIMISTIC * self.entry_spreads)
                               ) * MULT * n - fees

    def unrealized(self, mark: float) -> float:
        return (mark - self.spread.debit) * MULT * self.n - self.fees_one_side


@dataclass
class RunResult:
    setting: Setting
    trades: list[Trade]
    equity: dict[date, float] = field(default_factory=dict)
    unfinished: int = 0


def simulate(setting: Setting, decisions: dict[date, SpreadDecision], market: Market, signal: TrendSignal,
             entry_from: date, entry_to: date, ex_div: set[date]) -> RunResult:
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
        risk_on = signal.risk_on(day, setting.signal)
        # 1) exits
        for t in list(open_):
            s = t.spread
            if day <= s.day:
                continue
            q = market.path(s).get(day)
            res = value_to_close(q) if q else None
            dte = (s.expiry - day).days
            if res is None:
                if day >= s.expiry:
                    spot = market.spot.get(day) or market.spot.get(s.expiry)
                    if spot is not None:
                        intrinsic = min(max(0.0, spot - s.long_call.strike), s.width)
                        t.close(day, "expiry_settlement", intrinsic, 0.0)
                        realized += t.pnl
                        open_.remove(t)
                        done.append(t)
                        continue
                t.gap_days += 1
                continue
            value, spreads = res
            reason = None
            if ex.stop_loss is not None and value <= (1 - ex.stop_loss) * s.debit + EPS:
                reason = "stop"
            elif ex.profit_target is not None and value >= (1 + ex.profit_target) * s.debit - EPS:
                reason = "profit_target"
            elif not risk_on:
                reason = "trend_exit"
            elif dte <= TIME_EXIT_DTE:
                reason = "time_exit"
            elif dte <= HARD_EXIT_DTE:
                reason = "hard_exit"
            else:
                spot = market.spot.get(day)
                sc = q[1]
                if spot is not None and day in pre_ex_div and spot > s.short_call.strike and \
                        sc.mid - (spot - s.short_call.strike) < DIVIDEND_FRACTION * spot:
                    reason = "assignment_guard_call"
            if reason:
                t.close(day, reason, value, spreads)
                realized += t.pnl
                open_.remove(t)
                done.append(t)
            else:
                last_mark[id(t)] = value
        # 2) entry
        wk = day.isocalendar()[:2]
        if entry_from <= day <= entry_to and risk_on and len(open_) < MAX_OPEN and wk not in weeks_used:
            d = decisions.get(day)
            if d is not None and d.spread is not None:
                t = Trade(setting, d.spread)
                open_.append(t)
                q0 = market.path(d.spread).get(day)
                r0 = value_to_close(q0) if q0 else None
                last_mark[id(t)] = r0[0] if r0 else d.spread.debit
                weeks_used.add(wk)
        equity[day] = EQUITY + realized + sum(t.unrealized(last_mark[id(t)]) for t in open_)
    return RunResult(setting, done, equity, unfinished=len(open_))


# --- Choosing a setting ---------------------------------------------------------------------------------

def _closed(trades: list[Trade]) -> list[Trade]:
    return [t for t in trades if t.exit_day is not None]


def score(trades: list[Trade]) -> float | None:
    tr = _closed(trades)
    if len(tr) < MIN_TRADES_TO_CHOOSE:
        return None
    return mean(t.pnl / t.planned_max_loss for t in tr)


_GRIDS = {"signal": GRID_SIGNAL, "dte": GRID_DTE, "long": GRID_LONG, "short": GRID_SHORT, "pt": GRID_PT,
          "stop": GRID_STOP}


def neighbours(s: Setting, dims: tuple[str, ...]) -> list[Setting]:
    vals = {"signal": s.signal, "dte": s.entry.target_dte, "long": s.entry.long_delta, "short": s.entry.short_delta,
            "pt": s.exit.profit_target, "stop": s.exit.stop_loss}
    out = []
    for dim in dims:
        g = _GRIDS[dim]
        i = g.index(vals[dim])
        for j in (i - 1, i + 1):
            if 0 <= j < len(g):
                v = dict(vals, **{dim: g[j]})
                out.append(Setting(v["signal"], TrendParams(v["dte"], v["long"], v["short"]),
                                   ExitParams(v["pt"], v["stop"])))
    return out


def choose(discovery: dict[Setting, RunResult]):
    raw = {s: score(r.trades) for s, r in discovery.items()}
    smooth = {}
    for s, v in raw.items():
        if v is None:
            smooth[s] = None
            continue
        vals = [v] + [raw[n] for n in neighbours(s, tuple(_GRIDS)) if raw.get(n) is not None]
        smooth[s] = mean(vals)
    eligible = [s for s, v in smooth.items() if v is not None]
    best = max(eligible, key=lambda s: smooth[s]) if eligible else None
    return best, raw, smooth


# --- Bars ------------------------------------------------------------------------------------------

def _period_means(trades: list[Trade]) -> tuple[float, float, int, int]:
    disc = [t.pnl for t in trades if t.spread.day <= DISCOVERY[1]]
    conf = [t.pnl for t in trades if t.spread.day >= CONFIRMATION_START]
    nan = float("nan")
    return (mean(disc) if disc else nan), (mean(conf) if conf else nan), len(disc), len(conf)


def bars(chosen: Setting, full: RunResult, no_signal: RunResult, discovery_results: dict[Setting, RunResult]
         ) -> list[tuple[str, bool, str]]:
    tr = _closed(full.trades)
    allp = [t.pnl for t in tr]
    md, mc, nd, nc = _period_means(tr)
    ts = t_stat(allp)
    out = [("1 · Profitable in both periods, t ≥ 2", md > 0 and mc > 0 and ts >= T_STAT_BAR,
            f"discovery ${md:,.2f}/trade ({nd}), confirmation ${mc:,.2f}/trade ({nc}), per-trade t = {ts:.2f}; "
            f"monthly-P&L t = {t_stat(monthly_pnl(full.equity)):.2f} (information)")]
    ms = mean(t.pnl_stress for t in tr) if tr else float("nan")
    out.append(("2 · Survives worse fills", ms > 0, f"cost-stress average ${ms:,.2f}/trade"))
    over = [t for t in tr if -t.pnl > t.loss_allowance + 1e-6]
    out.append(("3 · Losses stay within plan", not over, f"{len(over)} trades lost more than planned"))
    dd, when = max_drawdown(full.equity)
    out.append(("4 · Drawdown under 10%", dd < MAX_DD * EQUITY, f"worst drawdown ${dd:,.0f} ({dd / EQUITY:.1%}) on {when}"))
    parts, ok5 = [], True
    for name, a, b in CRASH_WINDOWS:
        wdd, _ = max_drawdown(full.equity, a, b)
        wp = sum(t.pnl for t in tr if a <= t.exit_day <= b)
        ok5 &= wdd < MAX_DD * EQUITY
        parts.append(f"{name}: drawdown {wdd / EQUITY:.1%}, closed P&L ${wp:,.0f}")
    out.append(("5 · Crash periods", ok5, "; ".join(parts)))
    nb = [n for n in neighbours(chosen, ("dte", "long", "short")) if n in discovery_results]
    qual = [(n, _closed(discovery_results[n].trades)) for n in nb]
    qual = [(n, t) for n, t in qual if len(t) >= MIN_TRADES_NEIGHBOUR]
    ok6 = len(qual) >= MIN_QUALIFYING_NEIGHBOURS and all(mean(x.pnl for x in t) > 0 for _, t in qual)
    out.append(("6 · Not a lucky setting", ok6,
                f"{len(qual)} neighbours with ≥{MIN_TRADES_NEIGHBOUR} trades: " +
                ", ".join(f"{n.entry.target_dte} DTE {n.entry.long_delta:.2f}/{n.entry.short_delta:.2f}Δ "
                          f"${mean(x.pnl for x in t):,.2f}" for n, t in qual)))
    k = math.ceil(DROP_BEST * len(allp))
    trimmed = sorted(allp)[:-k] if k else allp
    m7 = mean(trimmed) if trimmed else float("nan")
    out.append(("7 · Not a few lucky trades", m7 > 0, f"without the best {k} trades: ${m7:,.2f}/trade"))
    nd_, nc_, nnd, nnc = _period_means(_closed(no_signal.trades))
    out.append(("8 · The trend signal earns its keep", md > nd_ and mc > nc_,
                f"with signal ${md:,.2f} vs no signal ${nd_:,.2f} ({nnd} trades) in discovery; "
                f"${mc:,.2f} vs ${nc_:,.2f} ({nnc} trades) in confirmation"))
    return out


def exposure_split(trades: list[Trade], spot: dict[date, float]) -> tuple[float, float, int]:
    """D3: average P&L that SPY shares with the same dollar delta would have made, and what's left over."""
    beta, rest = [], []
    for t in trades:
        s = t.spread
        a, b = spot.get(s.day), spot.get(t.exit_day)
        if s.net_delta is None or a is None or b is None:
            continue
        x = s.net_delta * MULT * t.n * (b - a)
        beta.append(x)
        rest.append(t.pnl - x)
    return (mean(beta) if beta else float("nan")), (mean(rest) if rest else float("nan")), len(beta)


def summary(trades: list[Trade]) -> str:
    tr = _closed(trades)
    if not tr:
        return "0 trades"
    p = [t.pnl for t in tr]
    return (f"{len(tr)} trades, ${mean(p):,.2f}/trade, win rate {sum(x > 0 for x in p) / len(p):.0%}, "
            f"worst ${min(p):,.0f}, total ${sum(p):,.0f}")


# --- Inputs ------------------------------------------------------------------------------------------

def trading_days(history: sqlite3.Connection, vol: sqlite3.Connection) -> list[date]:
    from research.vol_data_report import spy_trading_days
    minute = [date.fromisoformat(d) for d in spy_trading_days(history, "2015-12-01")]
    first = minute[0] if minute else DATA_END
    early = [date.fromisoformat(d) for (d,) in vol.execute(
        "SELECT DISTINCT quote_date FROM eod_chain WHERE quote_date<? ORDER BY quote_date", (first.isoformat(),))]
    return sorted(set(early) | set(minute))


def build_decisions(vol, tdays, params_list, start: date, end: date, rates) -> dict[TrendParams, dict[date, SpreadDecision]]:
    from research.condor_build_report import load_day
    out: dict[TrendParams, dict[date, SpreadDecision]] = {p: {} for p in params_list}
    chain_days = {d for (d,) in vol.execute("SELECT DISTINCT quote_date FROM eod_chain")}
    for day in tdays:
        if not (start <= day <= end) or day.isoformat() not in chain_days:
            continue
        spot, chains = load_day(vol, day.isoformat())
        cache: dict = {}
        for p in params_list:
            out[p][day] = build_call_spread(day, p, spot, chains, rates.rate(day, p.target_dte / 365), cache)
    return out


# --- Main ------------------------------------------------------------------------------------------

def run(vol: sqlite3.Connection, history: sqlite3.Connection, out_dir: Path, today: date, log=print) -> tuple[Path, bool]:
    from research.condor_build_report import RateLookup, ensure_rates
    ensure_rates(vol)
    rates = RateLookup(vol)
    tdays = [d for d in trading_days(history, vol) if d <= DATA_END]
    market = Market(vol, tdays)
    closes = {d: market.spot[d] for d in tdays if d in market.spot and d >= WARMUP_START}
    signal = TrendSignal(closes)
    ex_div = ex_dividend_dates(range(2010, 2025))
    grid = full_grid()
    entry_params = sorted({s.entry for s in grid}, key=lambda p: (p.target_dte, p.long_delta, p.short_delta))

    log("building entry decisions 2011–2023 …")
    decisions = build_decisions(vol, tdays, entry_params, DISCOVERY[0], DATA_END, rates)

    log(f"discovery: {len(grid)} settings …")
    disc = {s: simulate(s, decisions[s.entry], market, signal, DISCOVERY[0], DISCOVERY[1], ex_div) for s in grid}
    chosen, raw, smooth = choose(disc)
    path = out_dir / f"spy_trend_backtest_{today.isoformat()}.md"
    if chosen is None:
        text = _header(today) + ["**No setting had the 60 discovery trades needed to be chosen. Result: no-go.**", ""]
        text += _grid_table(disc, raw, smooth, None)
        path.write_text("\n".join(text) + "\n")
        return path, False

    log(f"chosen: {chosen.label()} — running full period …")
    full = simulate(chosen, decisions[chosen.entry], market, signal, DISCOVERY[0], DATA_END, ex_div)
    d1 = [(t.spread.day, round(t.pnl, 6)) for t in _closed(disc[chosen].trades)]
    d2 = [(t.spread.day, round(t.pnl, 6)) for t in _closed(full.trades) if t.spread.day <= DISCOVERY[1]]
    assert d1 == d2, "discovery trades changed between runs"
    ns_setting = Setting(SIGNAL_NONE, chosen.entry, chosen.exit)
    no_sig = simulate(ns_setting, decisions[chosen.entry], market, signal, DISCOVERY[0], DATA_END, ex_div)
    results = bars(chosen, full, no_sig, disc)
    passed = all(ok for _, ok, _ in results)

    other = SIGNAL_MOMENTUM if chosen.signal == SIGNAL_LIGHTS else SIGNAL_LIGHTS
    alt = simulate(Setting(other, chosen.entry, chosen.exit), decisions[chosen.entry], market, signal,
                   DISCOVERY[0], DATA_END, ex_div)
    _equity_chart(full, no_sig, out_dir / "img" / f"spy_trend_equity_{today.isoformat()}.png", chosen)
    text = _report(today, chosen, raw, smooth, disc, full, no_sig, alt, results, passed, market, decisions[chosen.entry],
                   signal)
    path.write_text(text)
    return path, passed


# --- Report ------------------------------------------------------------------------------------------

def _header(today: date) -> list[str]:
    return [f"# SPY Trend Call Spreads: Backtest ({today.isoformat()})", "",
            "SPY bull call spreads, entered only when SPY is above its 200-day average and calm, closed when that "
            "breaks. Run on OptionsDX end-of-day quotes exactly as pre-registered in "
            "`docs/research/spy_trend_preregistration.md`. Fills at bid/ask, $0.05 per contract per leg per side, "
            "$100,000 fixed account, 1% risk per spread, up to 3 open, one new spread a week. Discovery = entries "
            "2011–2018 (all 144 settings); confirmation = entries 2019–2023 (chosen setting only).", ""]


def _grid_table(disc, raw, smooth, chosen) -> list[str]:
    L = ["## All 144 settings, discovery period (2011–2018)", "",
         "Score = average profit per trade ÷ planned max loss; settings with under 60 trades get no score.", "",
         "| Setting | Trades | Avg P&L | Win rate | Score | Smoothed |", "|---|---|---|---|---|---|"]
    for s, r in sorted(disc.items(), key=lambda kv: kv[0].label()):
        tr = _closed(r.trades)
        p = [t.pnl for t in tr]
        sc = "–" if raw[s] is None else f"{raw[s]:+.3f}"
        sm = "–" if smooth[s] is None else f"{smooth[s]:+.3f}"
        mark = " **← chosen**" if s == chosen else ""
        L.append(f"| {s.label()}{mark} | {len(tr)} | " + (f"${mean(p):,.2f} | {sum(x > 0 for x in p) / len(p):.0%}"
                                                          if p else "– | –") + f" | {sc} | {sm} |")
    return L + [""]


def _buy_hold(spot: dict[date, float], a: date, b: date) -> str:
    days = sorted(d for d in spot if a <= d <= b)
    return f"{spot[days[-1]] / spot[days[0]] - 1:+.0%}" if days else "n/a"


def _report(today, chosen, raw, smooth, disc, full, no_sig, alt, results, passed, market, decisions, signal) -> str:
    L = _header(today)
    n_fail = sum(not ok for _, ok, _ in results)
    L += [f"## Result: {'**GO**: all 8 bars passed' if passed else f'**NO-GO**: {n_fail} of 8 bars failed'}", "",
          f"Chosen setting (highest smoothed discovery score): **{chosen.label()}**", "",
          "| Bar | Result | Detail |", "|---|---|---|"]
    L += [f"| {name} | {'✅ pass' if ok else '❌ fail'} | {detail} |" for name, ok, detail in results]
    tr = _closed(full.trades)
    reasons = defaultdict(int)
    for t in tr:
        reasons[t.exit_reason] += 1
    beta, rest, nb = exposure_split(tr, market.spot)
    on_days = [d for d in market.days if DISCOVERY[0] <= d <= DATA_END]
    share_on = sum(signal.risk_on(d, chosen.signal) for d in on_days) / len(on_days) if on_days else float("nan")
    built = [d for d in decisions.values() if d.spread]
    fallbacks = sum(d.details.get("fallback_deltas", 0) for d in built)
    L += ["", "## Chosen setting, full period", "",
          f"- All: {summary(full.trades)}",
          f"- Discovery: {summary([t for t in tr if t.spread.day <= DISCOVERY[1]])}",
          f"- Confirmation: {summary([t for t in tr if t.spread.day >= CONFIRMATION_START])}",
          f"- Cost-stress fills: ${mean(t.pnl_stress for t in tr):,.2f}/trade · optimistic fills (information): "
          f"${mean(t.pnl_optimistic for t in tr):,.2f}/trade",
          f"- Exits: " + ", ".join(f"{k} {v}" for k, v in sorted(reasons.items(), key=lambda x: -x[1])),
          f"- Days with a leg failing the data check while open (carried forward): {sum(t.gap_days for t in tr)}",
          f"- Still open at the end of the data: {full.unfinished}",
          f"- Signal was \"risk on\" on {share_on:.0%} of trading days 2011–2023",
          f"- Vendor deltas used when our solver refused a quote (all built days, all strikes): {fallbacks:,}",
          f"- Equity chart: `docs/research/img/spy_trend_equity_{today.isoformat()}.png`", "",
          "## Was it just market exposure? (information only)", "",
          f"Per trade, SPY shares with the same dollar delta at entry, held for the same days, would have made "
          f"**${beta:,.2f}** on average. What the spread made beyond that: **${rest:,.2f}** per trade ({nb} trades). "
          "A leftover at or below zero means the profit was just market exposure, before the extra option costs.", "",
          f"For context, SPY price change (excluding dividends): discovery {_buy_hold(market.spot, *DISCOVERY)}, "
          f"confirmation {_buy_hold(market.spot, CONFIRMATION_START, DATA_END)}.", "",
          "## Comparisons (information only; can't change the result)", "",
          f"- **Same setting, no signal (bar 8):** {summary(no_sig.trades)}",
          f"- **Same setting, other signal variant ({alt.setting.signal}):** {summary(alt.trades)}", "",
          "### Per year", "", "| Year | Trades | Total P&L | Avg/trade | Worst trade |", "|---|---|---|---|---|"]
    for y in sorted({t.spread.day.year for t in tr}):
        p = [t.pnl for t in tr if t.spread.day.year == y]
        L.append(f"| {y} | {len(p)} | ${sum(p):,.0f} | ${mean(p):,.2f} | ${min(p):,.0f} |")
    L += [""] + _grid_table(disc, raw, smooth, chosen)
    return "\n".join(L) + "\n"


def _equity_chart(full: RunResult, no_sig: RunResult, path: Path, chosen: Setting) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    path.parent.mkdir(parents=True, exist_ok=True)
    fig, ax = plt.subplots(figsize=(10, 4.2))
    for r, color, name in ((full, "#2b6cb0", "with trend signal"), (no_sig, "#a0aec0", "same spread, no signal")):
        days = sorted(r.equity)
        ax.plot(days, [r.equity[d] for d in days], color=color, linewidth=1.4, label=name)
    ax.axhline(EQUITY, color="#999", linewidth=0.8, linestyle="--")
    ax.axvspan(CONFIRMATION_START, DATA_END, color="#f6ad55", alpha=0.12, label="confirmation (never tuned on)")
    ax.set_title(f"SPY trend call spreads: {chosen.label()}", fontsize=10)
    ax.set_ylabel("Account value ($)")
    ax.legend(loc="upper left", fontsize=8, frameon=False)
    ax.spines[["top", "right"]].set_visible(False)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)
