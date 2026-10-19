"""Replay the risk controls through ten years of real SPY trading days.

What is real: SPY's minute-by-minute path (consolidated SIP bars since 2016),
and the production risk engine, exit rules and kill switch, called exactly as
the live system will call them.

What is modelled: option prices. Free historical option quotes do not exist,
so legs are priced with Black-Scholes at an implied vol set from SPY's trailing
20-day realized volatility, with vol rising as SPY falls intraday and a fixed
bid-ask. Trades are NEUTRAL (alternating bullish call and bearish put debit
spreads, $2 wide, one strike out of the money) with no assumed edge, because the question is whether the
controls hold, not whether a strategy makes money.

Each calendar quarter starts fresh at $20,000 so every period of history gets
traded; within a quarter every control applies normally, and a hard halt stops
trading for the rest of that quarter (a person would have to review it).

Writes docs/research/risk_replay.md and charts.
"""

from __future__ import annotations

import math
import sqlite3
import statistics as st
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from core.config import PROJECT_ROOT, Settings  # noqa: E402
from data.storage.db import MarketDataStore  # noqa: E402
from pricing.black_scholes import price as bs_price  # noqa: E402
from risk.exits import evaluate_exit  # noqa: E402
from risk.kill_switch import HealthInputs, evaluate  # noqa: E402
from risk.positions import LegQuote, OptionLeg, Portfolio, Position, unrealized_pnl, years_left  # noqa: E402
from risk.risk_engine import RiskContext, TradeRequest, approve  # noqa: E402
from risk.state import Level, RiskStateStore  # noqa: E402
from risk.stress import StressCalibration  # noqa: E402

EASTERN = ZoneInfo("America/New_York")
KNOWN_STRESS_DAYS = {
    date(2016, 6, 24): "Brexit vote",
    date(2018, 2, 5): "Volmageddon",
    date(2018, 12, 24): "Christmas Eve selloff",
    date(2020, 3, 9): "COVID limit-down open",
    date(2020, 3, 12): "COVID crash",
    date(2020, 3, 16): "COVID worst day",
    date(2022, 9, 13): "hot CPI print",
    date(2024, 8, 5): "yen carry unwind",
    date(2025, 4, 3): "tariff announcement",
    date(2025, 4, 4): "tariff selloff",
    date(2025, 4, 7): "tariff whipsaw",
    date(2025, 4, 9): "tariff pause rally",
}
ENTRY_TIMES = [time(10, 0), time(11, 0), time(12, 0), time(13, 0), time(14, 0)]
RATE = 0.03
SPREAD_WIDTH = 2          # debit spreads one strike out of the money: ~$80-95 of risk, the full per-trade budget
VOL_SPOT_BETA = 2.5        # vol points per 1% intraday SPY drop (same rule as the stress scenarios)
REQUESTED_CONTRACTS = 10   # ask for more than allowed so the risk engine's sizing is exercised


@dataclass
class DayResult:
    day: date
    start_equity: float
    end_equity: float
    min_equity: float
    trades: int
    level: str
    triggers: list[str] = field(default_factory=list)


@dataclass
class TradeResult:
    day: date
    opened: datetime
    closed: datetime
    contracts: int
    max_loss: float
    stop_loss: float
    pnl: float
    reason: str


def _load_days(conn: sqlite3.Connection, start: date | None, end: date | None) -> dict[date, tuple[list[datetime], np.ndarray]]:
    days: dict[date, list] = defaultdict(list)
    for ts, close in conn.execute("SELECT ts, close FROM minute_bars WHERE symbol='SPY' ORDER BY ts"):
        t = datetime.fromisoformat(ts).astimezone(EASTERN)
        if time(9, 30) <= t.time() < time(16, 0) and (start is None or t.date() >= start) and (end is None or t.date() <= end):
            days[t.date()].append((t, close))
    return {d: ([t for t, _ in v], np.array([c for _, c in v])) for d, v in days.items() if len(v) >= 300}


def _expiry(d: date) -> date:
    e = d + timedelta(days=7)
    return e + timedelta(days=(4 - e.weekday()) % 7)          # the Friday at least a week out


class _Pricer:
    def __init__(self, iv0: float, open_px: float):
        self.iv0, self.open_px = iv0, open_px

    def iv(self, spot: float) -> float:
        move = spot / self.open_px - 1
        return max(self.iv0 - VOL_SPOT_BETA * move if move < 0 else self.iv0 - 0.8 * move, 0.05)

    def quote(self, leg: OptionLeg, spot: float, now: datetime) -> LegQuote:
        iv = self.iv(spot)
        p = bs_price(spot, leg.strike, years_left(leg.expiration, now), RATE, 0.0, iv, leg.option_type)
        half = min(max(0.01, 0.005 * p), 0.05)
        return LegQuote(max(round(p - half, 2), 0.0), round(p + half, 2), iv, now)


def replay(settings: Settings, history: sqlite3.Connection, stress: StressCalibration,
           start: date | None = None, end: date | None = None, equity0: float = 20_000.0):
    days = _load_days(history, start, end)
    closes = {d: v[1][-1] for d, v in days.items()}
    ordered = sorted(days)
    state = RiskStateStore(MarketDataStore(":memory:"))
    equity = equity0
    quarter = None
    day_results, trades, halts = [], [], []
    refusals: Counter = Counter()
    review_days: list[date] = []
    req_counter = 0

    for i, d in enumerate(ordered):
        if i < 21:
            continue
        prev = [closes[x] for x in ordered[i - 21:i]]
        rv = float(np.std(np.diff(np.log(prev)), ddof=1) * math.sqrt(252))
        times, px = days[d]
        pricer = _Pricer(min(max(rv * 1.2, 0.08), 0.9), px[0])

        q = (d.year, (d.month - 1) // 3)
        if q != quarter:
            quarter, equity = q, equity0
            state = RiskStateStore(MarketDataStore(":memory:"))
            halted_quarter = False
        if state.level(d) == Level.HALTED and not halted_quarter:
            halts.append((d, state.last_trigger()))
            halted_quarter = True

        day = state.start_day(d, equity)
        hwm = state.high_water_mark() or equity
        if equity <= hwm * (1 - settings.risk.drawdown_warning_pct):
            # Below the warning line at the open: in live trading a person reviews and OKs the day.
            state.acknowledge_warning(d)
            review_days.append(d)
        portfolio = Portfolio()
        realized = 0.0
        min_eq, n_trades, triggers = equity, 0, set()
        exp = _expiry(d)
        flip = 1

        for t, spot in zip(times, px):
            now = t.astimezone(timezone.utc)
            quotes = {l.symbol: pricer.quote(l, spot, now) for p in portfolio.positions for l in p.legs}
            unreal = sum(unrealized_pnl(p, quotes) for p in portfolio.positions)
            cur_eq = equity + realized + unreal
            min_eq = min(min_eq, cur_eq)

            ks = evaluate(HealthInputs(cur_eq, len(portfolio.positions), 0.0), day, settings.risk, state, d)
            triggers.update(ks.triggers)

            for p in list(portfolio.positions):
                ex = evaluate_exit(p, quotes, now, settings, ks.level)
                if ex.close:
                    realized += ex.pnl
                    portfolio.positions.remove(p)
                    trades.append(TradeResult(d, p.opened_at, now, max(abs(l.quantity) for l in p.legs),
                                              p.max_loss, p.stop_loss, ex.pnl, ex.reason))

            if t.time().replace(second=0) in ENTRY_TIMES and ks.level == Level.NORMAL:
                k = round(spot)
                if flip > 0:     # bullish call debit spread
                    kind, k1, k2 = "call", k + 1, k + 1 + SPREAD_WIDTH
                else:            # bearish put debit spread
                    kind, k1, k2 = "put", k - 1, k - 1 - SPREAD_WIDTH
                flip = -flip
                legs = []
                for strike, qty in ((k1, 1), (k2, -1)):
                    tmp = OptionLeg(f"SPY{exp:%y%m%d}{kind[0].upper()}{strike}", "SPY", kind, float(strike), exp, qty, 0.0)
                    qt = pricer.quote(tmp, spot, now)
                    quotes[tmp.symbol] = qt
                    legs.append(OptionLeg(tmp.symbol, "SPY", kind, float(strike), exp, qty, qt.ask if qty > 0 else qt.bid))
                req_counter += 1
                req = TradeRequest(f"replay-{req_counter}", "SPY", tuple(legs), settings.strategy.fee_per_contract * 4,
                                   REQUESTED_CONTRACTS)
                cur_eq = equity + realized + sum(unrealized_pnl(p, quotes) for p in portfolio.positions)
                ctx = RiskContext(now, settings, state, day, cur_eq, ks.level, portfolio, quotes, {"SPY": spot}, RATE,
                                  stress, 0.0)
                dec = approve(req, ctx)
                if dec.approved:
                    portfolio.positions.append(dec.position)
                    n_trades += 1
                else:
                    refusals.update(dec.reasons)

        # Anything still open at the last bar is closed there (the market-close rule should have done it already).
        quotes = {l.symbol: pricer.quote(l, px[-1], times[-1]) for p in portfolio.positions for l in p.legs}
        for p in portfolio.positions:
            pnl = unrealized_pnl(p, quotes)
            realized += pnl
            trades.append(TradeResult(d, p.opened_at, times[-1], 0, p.max_loss, p.stop_loss, pnl, "end_of_data"))
        equity += realized
        day_results.append(DayResult(d, day.start_equity, equity, min_eq, n_trades, state.level(d).name, sorted(triggers)))
    return day_results, trades, halts, refusals, review_days


def build_risk_report(settings: Settings, history: sqlite3.Connection, stress: StressCalibration,
                      out_dir: Path | None = None, start: date | None = None, end: date | None = None) -> Path:
    out_dir = out_dir or PROJECT_ROOT / "docs" / "research"
    img = out_dir / "img"
    img.mkdir(parents=True, exist_ok=True)
    days, trades, halts, refusals, review_days = replay(settings, history, stress, start, end)
    lim = settings.risk

    pnl = [r.end_equity - r.start_equity for r in days]
    loss_days = [r for r in days if "daily_loss_limit" in r.triggers]
    overshoot = [(r, (r.end_equity - r.start_equity) + lim.daily_loss_limit_pct * r.start_equity) for r in loss_days]
    worst_overshoot = min(overshoot, key=lambda x: x[1]) if overshoot else None
    over_max = [t for t in trades if t.pnl < -t.max_loss - 0.01]
    stop_slip = [t.pnl + t.stop_loss for t in trades if t.reason == "stop_loss"]
    reasons = Counter(t.reason for t in trades)
    # Drawdown within each quarter (equity restarts at $20k each quarter).
    dd_by_q: dict = defaultdict(list)
    for r in days:
        dd_by_q[(r.day.year, (r.day.month - 1) // 3)].append(r.end_equity)
    dd = np.array([max(1 - v / max(vs[: i + 1]) for i, v in enumerate(vs)) for vs in dd_by_q.values()])
    cum = np.cumsum(pnl)
    worst_days = sorted(days, key=lambda r: r.end_equity - r.start_equity)[:8]
    years = defaultdict(list)
    for r in days:
        years[r.day.year].append(r)

    # charts
    fig, ax = plt.subplots(figsize=(9, 3.6))
    ax.plot([r.day for r in days], cum, color="#2b6cb0", lw=1.2)
    ax.set_title("Cumulative P&L of hypothetical neutral spreads under the risk controls (no assumed edge)")
    ax.set_ylabel("cumulative P&L ($)")
    ax.grid(alpha=0.25)
    fig.tight_layout()
    fig.savefig(img / "risk_replay_equity.png", dpi=130)
    plt.close(fig)
    fig, ax = plt.subplots(figsize=(9, 3.4))
    ax.hist(pnl, bins=80, color="#4a5568")
    ax.axvline(-lim.daily_loss_limit_pct * 20_000, color="#c53030", ls="--", lw=1.2, label="daily loss limit (at $20k)")
    ax.set_xlabel("daily P&L ($)")
    ax.set_ylabel("days")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.25)
    fig.tight_layout()
    fig.savefig(img / "risk_replay_daily_pnl.png", dpi=130)
    plt.close(fig)

    L = [f"# Risk-control replay — {days[0].day} to {days[-1].day}", "",
         "`main.py risk-replay`. **Real** SPY minute paths (consolidated feed) and the **production** risk engine, exit "
         "rules and kill switch. **Modelled** option prices (Black-Scholes, vol from trailing realized volatility, vol rising "
         "as SPY falls, fixed bid-ask). Trades are deliberately neutral with no assumed edge: this tests whether the "
         "controls hold on real days, not whether a strategy makes money.", ""]
    L += ["## Did the controls hold?", "",
          "| Check | Result |", "|---|---|",
          f"| Trading days replayed | {len(days):,} |",
          f"| Trades opened (approved by the risk engine) | {len(trades):,} |",
          f"| Any trade losing more than its planned max loss | **{len(over_max)}** |",
          f"| Largest trade loss vs its max loss | ${min((t.pnl for t in trades), default=0):,.2f} vs ${max((t.max_loss for t in trades), default=0):,.2f} max planned |",
          f"| Days the daily loss limit triggered | {len(loss_days)} |",
          f"| Worst overshoot past the daily limit when it triggered | "
          + (f"${-worst_overshoot[1]:,.2f} beyond the limit on {worst_overshoot[0].day}" if worst_overshoot and worst_overshoot[1] < 0 else "none") + " |",
          f"| Worst single day | ${min(pnl):,.2f} ({min(pnl) / 20_000:.2%} of $20k) |",
          f"| Days the profit ceiling triggered | {sum('daily_profit_ceiling' in r.triggers for r in days)} |",
          f"| Days the 5% drawdown warning paused new trades intraday | {sum('drawdown_warning' in r.triggers for r in days)} |",
          f"| Days that started below the warning line (a person must OK trading) | {len(review_days)} |",
          f"| Trade requests refused by the risk engine | {sum(refusals.values()):,} ("
          + ", ".join(f"{k} {v:,}" for k, v in refusals.most_common(4)) + ") |",
          f"| Quarters ending in a hard halt (10% drawdown) | {len(halts)} of {len(dd_by_q)}"
          + (f" — first on {halts[0][0]}" if halts else "") + " |",
          f"| Worst drawdown within a quarter | {dd.max():.1%} |",
          f"| Positions still open at the close | {reasons.get('end_of_data', 0)} (must be 0) |", ""]
    L += ["## Exits", "", "| Reason | Trades | Mean P&L |", "|---|---:|---:|"]
    for r, n in reasons.most_common():
        L.append(f"| `{r}` | {n:,} | ${st.mean(t.pnl for t in trades if t.reason == r):+.2f} |")
    if stop_slip:
        L += ["", f"Stop-loss slippage (loss beyond the stop level when the exit fired, because SPY jumped between minutes): "
              f"median ${st.median(stop_slip):+.2f}, worst ${min(stop_slip):+.2f} per position.", ""]
    L += ["## Worst days", "", "| Day | P&L | Lowest point intraday | Triggers |", "|---|---:|---:|---|"]
    for r in worst_days:
        L.append(f"| {r.day} | ${r.end_equity - r.start_equity:+,.2f} | ${r.min_equity - r.start_equity:+,.2f} | {', '.join(r.triggers) or '–'} |")
    L += ["", "## Known stress days", "",
          "| Day | Event | SPY open→close | Trades | P&L | Lowest point | Triggers |", "|---|---|---:|---:|---:|---:|---|"]
    by_day = {r.day: r for r in days}
    for d, name in KNOWN_STRESS_DAYS.items():
        r = by_day.get(d)
        if r is None:
            continue
        row = history.execute(
            "SELECT (SELECT close FROM minute_bars WHERE ts>=? ORDER BY ts LIMIT 1), "
            "(SELECT close FROM minute_bars WHERE ts<? ORDER BY ts DESC LIMIT 1)",
            (datetime.combine(d, time(9, 30), EASTERN).astimezone(timezone.utc).isoformat(),
             datetime.combine(d, time(16, 0), EASTERN).astimezone(timezone.utc).isoformat())).fetchone()
        move = f"{row[1] / row[0] - 1:+.1%}" if row and row[0] and row[1] else "–"
        L.append(f"| {d} | {name} | {move} | {r.trades} | ${r.end_equity - r.start_equity:+,.2f} | "
                 f"${r.min_equity - r.start_equity:+,.2f} | {', '.join(r.triggers) or '–'} |")
    L += ["", "On the most violent days the engine often opened nothing: when volatility spikes, option prices rise and these "
          "spreads stop fitting the per-trade limit, so they are refused. The limit protects the account automatically in "
          "high-volatility regimes. SPY open→close excludes the overnight gap.", "",
          "## By year", "", "| Year | Days | Trades | P&L | Worst day | Loss-limit days |", "|---|---:|---:|---:|---:|---:|"]
    for y in sorted(years):
        rs = years[y]
        L.append(f"| {y} | {len(rs)} | {sum(r.trades for r in rs):,} | ${sum(r.end_equity - r.start_equity for r in rs):+,.0f} "
                 f"| ${min(r.end_equity - r.start_equity for r in rs):+,.0f} | {sum('daily_loss_limit' in r.triggers for r in rs)} |")
    L += ["", "![equity](img/risk_replay_equity.png)", "", "![daily P&L](img/risk_replay_daily_pnl.png)", ""]
    if halts:
        L += ["## Hard halts", "", "In live trading each of these stops the system until a person reviews it and enters the "
              "restart authorization. In the replay, trading stays stopped for the rest of that quarter.", ""]
        for d, why in halts[:20]:
            L.append(f"* {d}: {why}")
        L.append("")
    L += ["## What this does and does not show", "",
          "* It shows the sizing, limits, exits and kill switch behave as designed on real price paths, including "
          "2018-02, 2020-03, 2022 and 2025-04.",
          "* The P&L is not a strategy result. With no edge, spreads lose the bid-ask and fees on average; that is expected, "
          "and the drawdown controls are what stop that bleed from going further.",
          "* Option prices are modelled. Real quotes widen sharply in a crash, so real exits on the worst days would cost "
          "more than shown here; treat overshoot numbers as a lower bound.",
          "* Minute bars hide moves within the minute; real stop slippage can be larger.",
          f"* Stress scenarios used by the engine come from this same history ({stress.days:,} days; worst 2-hour drop "
          f"{stress.worst_down:.1%} on {stress.worst_down_date}).", ""]
    path = out_dir / "risk_replay.md"
    path.write_text("\n".join(L))
    return path
