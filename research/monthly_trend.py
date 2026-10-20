"""SPY monthly trend (10-month average): backtest against buy-and-hold, exactly as pre-registered.

Rules: docs/research/monthly_trend_preregistration.md.
  * Month-end close above its N-month average -> hold SPY next month, else cash. Trades at the next open.
  * Fractional shares, dividends reinvested on the ex-date, cash earns FRED DTB3, cost as a fraction per side.
  * Buy-and-hold is the same simulation with the signal always "own SPY".
"""

from __future__ import annotations

import bisect
import csv
import json
import math
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from statistics import mean, median, stdev
from zoneinfo import ZoneInfo

import requests

from pricing.rates import fetch_fred

YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
EQUITY = 100_000.0
START = date(1994, 1, 1)                 # first trading day on/after this
DISCOVERY = (date(1994, 1, 1), date(2012, 12, 31))
CONFIRMATION_START = date(2013, 1, 1)
LOOKBACK = 10
NEIGHBOURS = (9, 11)
INFO_LOOKBACKS = (8, 12)
COST = 0.0005
COST_LEVELS = (0.0, 0.0005, 0.0010, 0.0025)
STRESS_COST = 0.0025
DD_RATIO_BAR = 0.6
CAGR_GAP_BAR = 0.02
WHIPSAW_DAYS = 62
ET = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class Bar:
    day: date
    open: float
    close: float


@dataclass(frozen=True)
class MarketData:
    bars: list[Bar]
    dividends: dict[date, float]                    # ex-date -> $/share
    rates: list[tuple[date, float]]                 # DTB3 observations, percent


# --- Data ----------------------------------------------------------------------------------------

def fetch_yahoo(symbol: str = "SPY", timeout: float = 30.0) -> dict:
    resp = requests.get(YAHOO_CHART.format(symbol=symbol), params={"period1": 0, "period2": int(datetime.now(timezone.utc).timestamp()),
                                             "interval": "1d", "events": "div,split"},
                        headers={"User-Agent": "Mozilla/5.0"}, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def parse_yahoo(payload: dict, allow_splits: bool = False) -> tuple[list[Bar], dict[date, float]]:
    """Yahoo's close is split-adjusted; SPY has never split, so the SPY test refuses any split."""
    res = payload["chart"]["result"][0]
    if res.get("events", {}).get("splits") and not allow_splits:
        raise ValueError("split in the data: check the close is split-adjusted before using it")
    q = res["indicators"]["quote"][0]
    bars = []
    for ts, o, c in zip(res["timestamp"], q["open"], q["close"]):
        if o is None or c is None:
            continue
        bars.append(Bar(datetime.fromtimestamp(ts, ET).date(), float(o), float(c)))
    divs = {datetime.fromtimestamp(int(v["date"]), ET).date(): float(v["amount"])
            for v in res.get("events", {}).get("dividends", {}).values()}
    return bars, divs


def load_rates(cache_dir: Path) -> list[tuple[date, float]]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    rate_path = cache_dir / "fred_dtb3.json"
    if not rate_path.exists():
        rate_path.write_text(json.dumps([(d.isoformat(), v) for d, v in fetch_fred("DTB3")]))
    return [(date.fromisoformat(d), v) for d, v in json.loads(rate_path.read_text())]


def load_symbol(cache_dir: Path, symbol: str, allow_splits: bool = False) -> MarketData:
    """Download once (Yahoo daily + FRED DTB3) and reuse the saved copy on every later run."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"{symbol.lower()}_daily_yahoo.json"
    if not path.exists():
        path.write_text(json.dumps(fetch_yahoo(symbol)))
    bars, divs = parse_yahoo(json.loads(path.read_text()), allow_splits)
    return MarketData(bars, divs, load_rates(cache_dir))


def load_data(cache_dir: Path) -> MarketData:
    return load_symbol(cache_dir, "SPY")


def month_ends(days: list[date], before: date) -> list[date]:
    """Last trading day of each month with data before `before` (pass the 1st of the current month)."""
    out: dict[tuple[int, int], date] = {}
    for d in days:
        if d < before:
            out[(d.year, d.month)] = d
    return sorted(out.values())


def first_of_month(d: date) -> date:
    return date(d.year, d.month, 1)


# --- Signal --------------------------------------------------------------------------------------

def signals(ends: list[date], close: dict[date, float], n: int | None) -> dict[date, tuple[float | None, bool]]:
    """Month-end -> (N-month average, own SPY next month?). n=None means always own (buy-and-hold)."""
    out = {}
    for i, d in enumerate(ends):
        if n is None:
            out[d] = (None, True)
        elif i + 1 >= n:
            avg = mean(close[e] for e in ends[i + 1 - n:i + 1])
            out[d] = (avg, close[d] > avg)
    return out


# --- Simulation ----------------------------------------------------------------------------------

@dataclass(frozen=True)
class TradeRow:
    day: date
    side: str                    # "buy" / "sell"
    price: float
    value: float                 # account value traded, before cost
    cost: float
    signal_month_end: date | None


@dataclass(frozen=True)
class MonthRow:
    month_end: date
    close: float
    average: float | None
    signal: str                  # "SPY" / "cash"
    held: str
    intended: str                # "buy" / "sell" / "hold"
    fill_day: date | None
    fill_price: float | None
    note: str


@dataclass
class Result:
    equity: dict[date, float]
    invested: dict[date, bool]
    trades: list[TradeRow]
    months: list[MonthRow]
    cash_index: dict[date, float]
    lookback: int | None
    cost: float
    start_value: float = EQUITY


def rate_lookup(rates: list[tuple[date, float]]):
    obs = sorted(rates)
    days = [d for d, _ in obs]

    def before(d: date) -> float:
        i = bisect.bisect_left(days, d) - 1
        return obs[i][1] / 100 if i >= 0 else 0.0
    return before


def simulate(data: MarketData, n: int | None, cost: float, end_before: date, start: date = START) -> Result:
    bars = [b for b in data.bars if b.day < end_before]
    close = {b.day: b.close for b in bars}
    ends = month_ends([b.day for b in bars], end_before)
    sig = signals(ends, close, n)
    rate = rate_lookup(data.rates)
    end_set = set(ends)

    prior_ends = [e for e in ends if e < start]
    if not prior_ends or prior_ends[-1] not in sig:
        raise ValueError(f"not enough history before {start} for a {n}-month average")
    target, target_from = sig[prior_ends[-1]][1], prior_ends[-1]
    shares, cash, held = 0.0, EQUITY, False
    cash_idx, prev_day = 1.0, None
    res = Result({}, {}, [], [], {}, n, cost)
    pending_row: MonthRow | None = None

    for b in bars:
        if b.day < start:
            continue
        if prev_day is not None:
            growth = (1 + rate(b.day)) ** (1 / 252)
            cash *= growth
            cash_idx *= growth
        div_cash = shares * data.dividends.get(b.day, 0.0)
        if target != held:
            if target:
                value = cash
                c = value * cost
                shares, cash = (value - c) / b.open, 0.0
            else:
                value = shares * b.open
                c = value * cost
                shares, cash = 0.0, value - c
            held = target
            res.trades.append(TradeRow(b.day, "buy" if target else "sell", b.open, value, c, target_from))
            if pending_row is not None:
                gap = b.open / pending_row.close - 1
                pending_row = MonthRow(**{**pending_row.__dict__, "fill_day": b.day, "fill_price": b.open,
                                          "note": f"next open {gap:+.2%} vs month-end close"})
        if pending_row is not None:
            res.months.append(pending_row)
            pending_row = None
        if div_cash:
            if shares > 0:
                shares += div_cash / b.close
            else:
                cash += div_cash
        res.equity[b.day] = shares * b.close + cash
        res.invested[b.day] = shares > 0
        res.cash_index[b.day] = cash_idx
        prev_day = b.day

        if b.day in end_set and b.day in sig:
            avg, own = sig[b.day]
            intended = "hold" if own == held else ("buy" if own else "sell")
            pending_row = MonthRow(b.day, b.close, avg, "SPY" if own else "cash", "SPY" if held else "cash",
                                   intended, None, None, "")
            target, target_from = own, b.day
    if pending_row is not None:
        note = "" if pending_row.intended == "hold" else "trade falls after the data ends"
        res.months.append(MonthRow(**{**pending_row.__dict__, "note": note}))
    return res


# --- Metrics -------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Stats:
    total: float
    cagr: float
    max_dd: float
    max_dd_day: date | None
    worst_year: tuple[int, float] | None
    sharpe: float
    trades: int
    invested: float
    years: float


def _base(res: Result, start: date) -> tuple[float, float]:
    """Account value and cash index at the close before `start` (or the starting cash)."""
    before = [d for d in res.equity if d < start]
    if not before:
        return res.start_value, 1.0
    d = max(before)
    return res.equity[d], res.cash_index[d]


def stats(res: Result, start: date, end: date) -> Stats:
    days = [d for d in sorted(res.equity) if start <= d <= end]
    base, base_cash = _base(res, start)
    last = res.equity[days[-1]]
    years = (days[-1] - (max([d for d in res.equity if d < start], default=days[0]))).days / 365.25
    total = last / base - 1
    cagr = (last / base) ** (1 / years) - 1 if years > 0 else 0.0

    peak, worst, worst_day = base, 0.0, None
    for d in days:
        peak = max(peak, res.equity[d])
        dd = 1 - res.equity[d] / peak
        if dd > worst:
            worst, worst_day = dd, d

    year_end: dict[int, float] = {}
    for d in days:
        year_end[d.year] = res.equity[d]
    years_seen, prev, yearly = sorted(year_end), base, []
    for y in years_seen:
        whole = (y > days[0].year or start <= date(y, 1, 7)) and (y < days[-1].year or days[-1] >= date(y, 12, 24))
        if whole:
            yearly.append((y, year_end[y] / prev - 1))
        prev = year_end[y]
    worst_year = min(yearly, key=lambda t: t[1]) if yearly else None

    month_last: dict[tuple[int, int], date] = {}
    for d in days:
        month_last[(d.year, d.month)] = d
    pv, pc, excess = base, base_cash, []
    for k in sorted(month_last):
        d = month_last[k]
        v, c = res.equity[d], res.cash_index[d]
        excess.append((v / pv - 1) - (c / pc - 1))
        pv, pc = v, c
    sharpe = mean(excess) / stdev(excess) * math.sqrt(12) if len(excess) > 2 and stdev(excess) > 0 else 0.0

    trades = sum(1 for t in res.trades if start <= t.day <= end)
    invested = sum(res.invested[d] for d in days) / len(days)
    return Stats(total, cagr, worst, worst_day, worst_year, sharpe, trades, invested, years)


def round_trips(res: Result) -> list[tuple[TradeRow, TradeRow | None, float | None]]:
    """(buy, sell, return incl. costs) for every holding period; sell None if still open."""
    out, buy = [], None
    for t in res.trades:
        if t.side == "buy":
            buy = t
        elif buy is not None:
            out.append((buy, t, (t.value - t.cost) / buy.value - 1))
            buy = None
    if buy is not None:
        out.append((buy, None, None))
    return out


def whipsaws(res: Result) -> list[tuple[TradeRow, TradeRow, float]]:
    return [(b, s, r) for b, s, r in round_trips(res)
            if s is not None and r is not None and (s.day - b.day).days <= WHIPSAW_DAYS and r < 0]


def log_check(res: Result, days: list[date]) -> list[str]:
    """Bar 1: every trade is the first trading day after a month-end whose signal changed, and vice versa."""
    problems = []
    nxt = {d: days[i + 1] for i, d in enumerate(days[:-1])}
    changes = [m for m in res.months if m.intended != "hold" and m.fill_day is not None]
    by_day = {t.day: t for t in res.trades}
    first = res.trades[0] if res.trades else None
    for m in changes:
        t = by_day.get(m.fill_day)
        if t is None:
            problems.append(f"{m.month_end}: signal changed but no trade")
        elif m.fill_day != nxt.get(m.month_end):
            problems.append(f"{m.month_end}: trade on {m.fill_day}, not the next trading day")
        elif t.side != m.intended or t.signal_month_end != m.month_end:
            problems.append(f"{m.month_end}: trade side/signal mismatch")
    expected = {m.fill_day for m in changes}
    for t in res.trades:
        is_start = t is first and t.signal_month_end is not None and t.signal_month_end < min(res.equity)
        if t.day not in expected and not is_start:
            problems.append(f"{t.day}: trade with no matching month-end signal")
    held = False
    for t in res.trades:
        if (t.side == "buy") == held:
            problems.append(f"{t.day}: {t.side} while already {'in' if held else 'out'}")
        held = t.side == "buy"
    return problems


# --- Data checks ---------------------------------------------------------------------------------

def check_against_history(data: MarketData, history: sqlite3.Connection | None) -> dict:
    """Yahoo close vs our stored minute bars (last regular-session bar, back to raw price)."""
    if history is None:
        return {"days": 0}
    factors = dict(history.execute("SELECT day, factor FROM raw_factors WHERE symbol='SPY'"))
    last: dict[str, tuple[str, float]] = {}
    for ts, c in history.execute("SELECT ts, close FROM minute_bars WHERE symbol='SPY'"):
        t = datetime.fromisoformat(ts).astimezone(ET)
        if (t.hour, t.minute) > (15, 59) or (t.hour, t.minute) < (9, 30):
            continue
        k = t.date().isoformat()
        if k not in last or ts > last[k][0]:
            last[k] = (ts, c)
    gaps = []
    for b in data.bars:
        k = b.day.isoformat()
        if k in last and k in factors:
            gaps.append((b.day, abs(last[k][1] * factors[k] / b.close - 1)))
    big = [(d, g) for d, g in gaps if g > 0.005]
    return {"days": len(gaps), "median": median(g for _, g in gaps) if gaps else None,
            "big": big, "first": min(d for d, _ in gaps) if gaps else None,
            "last": max(d for d, _ in gaps) if gaps else None}


def check_dividends(data: MarketData, start: date, end: date) -> dict:
    per_year: dict[int, int] = {}
    off = []
    for d in sorted(data.dividends):
        if not start <= d <= end:
            continue
        per_year[d.year] = per_year.get(d.year, 0) + 1
        if d.month not in (3, 6, 9, 12):
            off.append(d)
            continue
        tf = date(d.year, d.month, 15)
        while tf.weekday() != 4:
            tf += timedelta(days=1)
        if abs((d - tf).days) > 7:
            off.append(d)
    full_years = range(start.year, end.year + (1 if end.month == 12 and end.day >= 20 else 0))
    wrong = {y: per_year.get(y, 0) for y in full_years if per_year.get(y, 0) != 4}
    return {"count": sum(per_year.values()), "wrong_years": wrong, "off_schedule": off}


# --- Bars ----------------------------------------------------------------------------------------

@dataclass
class Verdict:
    bars: list[tuple[str, bool, str]] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return all(ok for _, ok, _ in self.bars)


def evaluate(main: Result, bh: Result, neighbours: dict[int, tuple[Result, Result]], stress: tuple[Result, Result],
             days: list[date], full: tuple[date, date], conf: tuple[date, date]) -> Verdict:
    v = Verdict()
    problems = log_check(main, days)
    v.bars.append(("1 · Log check", not problems,
                   f"{len(main.trades)} trades, {len(problems)} problems" + (f": {problems[:3]}" if problems else "")))
    s, b = stats(main, *full), stats(bh, *full)
    v.bars.append(("2 · Sharpe ≥ buy-and-hold (full)", s.sharpe >= b.sharpe, f"{s.sharpe:.2f} vs {b.sharpe:.2f}"))
    v.bars.append(("3 · Max drawdown ≤ 0.6 × buy-and-hold (full)", s.max_dd <= DD_RATIO_BAR * b.max_dd,
                   f"{s.max_dd:.1%} vs limit {DD_RATIO_BAR * b.max_dd:.1%} (buy-and-hold {b.max_dd:.1%})"))
    v.bars.append(("4 · CAGR ≥ buy-and-hold − 2 pp (full)", s.cagr >= b.cagr - CAGR_GAP_BAR,
                   f"{s.cagr:.2%} vs {b.cagr:.2%}"))
    sc, bc = stats(main, *conf), stats(bh, *conf)
    v.bars.append(("5 · Confirmation: Sharpe ≥ and drawdown < buy-and-hold", sc.sharpe >= bc.sharpe and sc.max_dd < bc.max_dd,
                   f"Sharpe {sc.sharpe:.2f} vs {bc.sharpe:.2f}; drawdown {sc.max_dd:.1%} vs {bc.max_dd:.1%}"))
    parts, ok6 = [], True
    for n, (r, rb) in neighbours.items():
        a, c = stats(r, *full), stats(rb, *full)
        ok = a.sharpe >= c.sharpe and a.max_dd <= DD_RATIO_BAR * c.max_dd
        ok6 &= ok
        parts.append(f"{n} mo: Sharpe {a.sharpe:.2f}, drawdown {a.max_dd:.1%} ({'pass' if ok else 'fail'})")
    v.bars.append(("6 · 9 and 11 months pass bars 2 and 3", ok6, "; ".join(parts)))
    a, c = stats(stress[0], *full), stats(stress[1], *full)
    v.bars.append(("7 · At 0.25% per side, Sharpe ≥ buy-and-hold", a.sharpe >= c.sharpe, f"{a.sharpe:.2f} vs {c.sharpe:.2f}"))
    return v


# --- Report --------------------------------------------------------------------------------------

def _chart(main: Result, bh: Result, path: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 7), sharex=True, gridspec_kw={"height_ratios": [2, 1]})
    for res, label, color in ((bh, "Buy and hold SPY", "#7a7a7a"), (main, "10-month trend rule", "#1f6fb2")):
        days = sorted(res.equity)
        vals = [res.equity[d] for d in days]
        ax1.plot(days, vals, label=label, color=color, lw=1.4)
        peak, dds = -math.inf, []
        for x in vals:
            peak = max(peak, x)
            dds.append(-(1 - x / peak) * 100)
        ax2.plot(days, dds, color=color, lw=1.0)
    ax1.set_yscale("log")
    ax1.set_ylabel("Account value ($, log scale)")
    ax1.legend(loc="upper left")
    ax1.set_title("SPY 10-month trend rule vs buy-and-hold, $100k from Jan 1994 (0.05% per side)")
    ax2.set_ylabel("Drawdown (%)")
    ax2.axvline(CONFIRMATION_START, color="#bbbbbb", ls="--", lw=0.8)
    ax1.axvline(CONFIRMATION_START, color="#bbbbbb", ls="--", lw=0.8)
    for ax in (ax1, ax2):
        ax.grid(alpha=0.25)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=110)
    plt.close(fig)


def _write_csvs(main: Result, out_dir: Path) -> tuple[Path, Path]:
    months_path, trades_path = out_dir / "monthly_trend_log.csv", out_dir / "monthly_trend_trades.csv"
    with months_path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["month_end", "close", "average_10m", "signal", "held_before", "intended_trade", "fill_day",
                    "fill_price", "note"])
        for m in main.months:
            w.writerow([m.month_end, f"{m.close:.2f}", f"{m.average:.2f}" if m.average is not None else "", m.signal,
                        m.held, m.intended, m.fill_day or "", f"{m.fill_price:.2f}" if m.fill_price else "", m.note])
    with trades_path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["day", "side", "price", "account_value", "cost", "signal_month_end"])
        for t in main.trades:
            w.writerow([t.day, t.side, f"{t.price:.2f}", f"{t.value:.2f}", f"{t.cost:.2f}", t.signal_month_end or ""])
    return months_path, trades_path


def _row(name: str, s: Stats) -> str:
    wy = f"{s.worst_year[0]} ({s.worst_year[1]:+.1%})" if s.worst_year else "–"
    return (f"| {name} | {s.total:+,.0%} | {s.cagr:.2%} | {s.max_dd:.1%} | {wy} | {s.sharpe:.2f} | {s.trades} | "
            f"{s.invested:.0%} |")


HEADER = ("| | Total return | Annual (CAGR) | Max drawdown | Worst year | Sharpe | Trades | Time invested |\n"
          "|---|---|---|---|---|---|---|---|")


def run(history: sqlite3.Connection | None, out_dir: Path, today: date, cache_dir: Path) -> tuple[Path, bool]:
    data = load_data(cache_dir)
    end_before = first_of_month(today)
    days = [b.day for b in data.bars if b.day < end_before]
    last = max(days)
    full, disc, conf = (START, last), DISCOVERY, (CONFIRMATION_START, last)

    def pair(n: int | None, cost: float) -> Result:
        return simulate(data, n, cost, end_before)

    bh = pair(None, COST)
    main = pair(LOOKBACK, COST)
    neighbours = {n: (pair(n, COST), bh) for n in NEIGHBOURS}
    info = {n: pair(n, COST) for n in INFO_LOOKBACKS}
    by_cost = {c: (pair(LOOKBACK, c), pair(None, c)) for c in COST_LEVELS}
    verdict = evaluate(main, bh, neighbours, by_cost[STRESS_COST], days, full, conf)

    hist_check = check_against_history(data, history)
    div_check = check_dividends(data, date(START.year, 1, 1), last)
    img = out_dir / "img" / "monthly_trend_equity.png"
    _chart(main, bh, img)
    months_csv, trades_csv = _write_csvs(main, out_dir)

    L = [f"# SPY Monthly Trend (10-Month Average) Backtest — {today.isoformat()}", "",
         "*Paper research only; not investment advice. Rules: "
         "[monthly_trend_preregistration.md](monthly_trend_preregistration.md), committed before this ran.*", "",
         f"## Verdict: **{'GO' if verdict.passed else 'NO-GO'}** "
         f"({sum(ok for _, ok, _ in verdict.bars)} of {len(verdict.bars)} bars passed)", "",
         "| Bar | Result | Detail |", "|---|---|---|"]
    L += [f"| {name} | {'PASS' if ok else '**FAIL**'} | {detail} |" for name, ok, detail in verdict.bars]
    L += ["", "## Results at 0.05% per side", ""]
    for label, (a, b) in (("Full period", full), ("Discovery", disc), ("Confirmation", conf)):
        L += [f"**{label}: {a.isoformat()} to {b.isoformat()}**", "", HEADER,
              _row("10-month rule", stats(main, a, b)), _row("Buy and hold", stats(bh, a, b)), ""]
    L += [f"![Account value and drawdown](img/{img.name})", "", "## Lookbacks (full period)", "", HEADER]
    for n in sorted([LOOKBACK, *NEIGHBOURS, *INFO_LOOKBACKS]):
        r = main if n == LOOKBACK else (neighbours[n][0] if n in neighbours else info[n])
        tag = " (main)" if n == LOOKBACK else (" (bar 6)" if n in NEIGHBOURS else " (info only)")
        L.append(_row(f"{n} months{tag}", stats(r, *full)))
    L.append(_row("Buy and hold", stats(bh, *full)))
    L += ["", "## Trading costs (full period)", "", "| Cost per side | Rule CAGR | Rule Sharpe | Buy-and-hold CAGR | "
          "Buy-and-hold Sharpe | Total cost paid by rule |", "|---|---|---|---|---|---|"]
    for c, (r, rb) in by_cost.items():
        a, b = stats(r, *full), stats(rb, *full)
        L.append(f"| {c:.2%} | {a.cagr:.2%} | {a.sharpe:.2f} | {b.cagr:.2%} | {b.sharpe:.2f} | "
                 f"${sum(t.cost for t in r.trades):,.0f} |")

    trips = round_trips(main)
    ws = whipsaws(main)
    closed = [r for _, s, r in trips if s is not None]
    L += ["", "## Trades", "",
          f"- {len(main.trades)} trades ({len(closed)} completed holding periods"
          f"{', 1 still open' if trips and trips[-1][1] is None else ''}). "
          f"Winners {sum(r > 0 for r in closed)}, losers {sum(r <= 0 for r in closed)}.",
          f"- Whipsaws (held {WHIPSAW_DAYS} days or less and lost): {len(ws)}; their returns add up to "
          f"{sum(r for _, _, r in ws):+.1%}.",
          f"- Full trade log: [{trades_csv.name}]({trades_csv.name}). Monthly log (close, average, signal, intended "
          f"trade, fill): [{months_csv.name}]({months_csv.name}).", "",
          "| Bought | Sold | Return incl. costs |", "|---|---|---|"]
    for b_, s_, r_ in trips:
        L.append(f"| {b_.day} | {s_.day if s_ else 'still held'} | {f'{r_:+.1%}' if r_ is not None else '–'} |")

    L += ["", "## Data checks", "",
          f"- SPY daily bars: {len(days):,} days, {min(days)} to {last} (Yahoo Finance, free). "
          f"Last complete month used: {last:%B %Y}.",
          f"- Yahoo close vs our stored minute bars: {hist_check['days']:,} days compared"]
    if hist_check["days"]:
        L[-1] += (f" ({hist_check['first']} to {hist_check['last']}), median gap {hist_check['median']:.3%}, "
                  f"{len(hist_check['big'])} day(s) off by more than 0.5%"
                  + (f": {', '.join(f'{d} ({g:.2%})' for d, g in hist_check['big'][:5])}" if hist_check["big"] else "")
                  + ".")
    L += [f"- Dividends {START.year}–{last.year}: {div_check['count']}. Years without exactly 4: "
          f"{', '.join(f'{y} ({n})' for y, n in div_check['wrong_years'].items()) or 'none'}. Off SPY's schedule: "
          f"{', '.join(d.isoformat() for d in div_check['off_schedule']) or 'none'}.",
          "- Cash earns FRED DTB3 (3-month T-bill), using the rate published before each day.",
          "- Taxes are ignored. They'd hurt the rule more than buy-and-hold, because the rule sells and realizes gains."]
    path = out_dir / f"monthly_trend_backtest_{today.isoformat()}.md"
    path.write_text("\n".join(L) + "\n")
    return path, verdict.passed
