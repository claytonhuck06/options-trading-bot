"""Dual momentum, PAPER only: once a month hold the top 2 of SPY/EFA/IEF/VNQ/DBC by 12-month total return, each
only while it's above its 10-month average; a fund that fails leaves its half in cash. Owner decision 2026-10-20 to
paper-trade it despite the NO-GO in docs/research/dual_momentum_backtest_2026-10-20.md.

  * Signal: exactly the backtested rule (research/dual_momentum.py), on fresh free Yahoo daily data, using the
    last month-end close. The month-end must be the exchange's real last trading day of that month.
  * Instrument (owner, 2026-10-21): a deep in-the-money call (delta ~0.80, first monthly expiry 75+ days out)
    for each fund held, about 10% of equity spent on premium per fund (SLEEVE = 20% for two). That is several
    times the exposure of the same money in shares; the backtest was on shares, so this version is untested.
    DBC options barely trade, so DBC is always shares; any other fund whose call quote is wider than 4% of its
    price falls back to shares for that month.
  * Calls held for a fund that stays selected are kept until fewer than 40 days are left (then rolled at the
    month's rebalance); any call with 14 days or less left is rolled at the next run, whatever the date.
  * When: the first weekday run of each month that's a trading day. Shares: opening-auction orders before
    9:28 ET, else market orders. Options: limit orders from 9:45 ET, starting at the mid and stepping to the
    ask/bid over a few minutes, re-quoted each step. A missed or unfinished month is retried at the next run.
  * Sells always go; buys wait if the kill switch isn't NORMAL or a STOP file exists.
  * Kept out of the account kill switch (owner, 2026-10-21): the trader subtracts this strategy's P&L
    (`pnl_now` / `pnl_at_prev_close`) before checking its daily and drawdown limits. Instead an alert fires
    if the positions' value falls LOSS_ALERT below its value after the month's rebalance.
  * Order/holding records reuse the overnight basket's bookkeeping (own database, `oqm-` order ids). The options
    trader asks this module what shares, contracts and open orders to expect.
"""

from __future__ import annotations

import json
import math
import sqlite3
import time as _time
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Callable

from core.config import PROJECT_ROOT
from core.logging_setup import get_logger
from execution.broker_api import BrokerAPI, BrokerPosition, OrderStatus, SubmitTimeout
from execution.overnight_basket import ET, OPG_CUTOFF, OvernightBasket, Result, now_utc  # noqa: F401
from execution.overnight_basket import open_db as _open_basket_db
from research.dual_momentum import ASSETS, TREND, dual_rule
from research.monthly_trend import MarketData, first_of_month, month_ends, parse_yahoo, signals
from research.multi_asset_trend import common_days

log = get_logger("dual_momentum_paper")
DB_PATH = PROJECT_ROOT / "data" / "storage" / "dual_momentum.db"
STOP_FILE = PROJECT_ROOT / "STOP"
PREFIX = "oqm-"
SLEEVE = 0.20                         # share of equity spent when both funds are held (owner, 2026-10-21)
SHARES_ONLY = frozenset({"DBC"})      # options too thin to trade (open interest < 100 per strike in Oct 2026)
TARGET_DELTA = 0.80
MIN_DTE, ROLL_DTE, FORCE_DTE = 75, 40, 14
MAX_SPREAD = 0.04                     # (ask - bid) / mid, else shares for that fund this month
MAX_OVER_BUDGET = 1.5                 # one contract may cost up to 1.5x the fund's budget
OPTIONS_FROM = time(9, 45)            # ET: let option quotes settle after the open
CHASE_STEPS, CHASE_WAIT = 5, 30.0     # mid -> ask in 5 steps, 30 s each
LOSS_ALERT = 5_000.0                  # $ below the value after the month's rebalance (25% of $20k)

SCHEMA = """
CREATE TABLE IF NOT EXISTS months (
    signal_month_end TEXT PRIMARY KEY, decided TEXT, held TEXT, scores TEXT, trend TEXT, targets TEXT,
    equity REAL, done TEXT, note TEXT
);
CREATE TABLE IF NOT EXISTS day_marks (day TEXT PRIMARY KEY, pnl_prev_close REAL);
"""
EXTRA_MONTH_COLUMNS = {"pending": "TEXT", "start_value": "REAL", "alerted": "TEXT"}


def open_db(path: Path = DB_PATH) -> sqlite3.Connection:
    conn = _open_basket_db(path)
    conn.executescript(SCHEMA)
    have = {r[1] for r in conn.execute("PRAGMA table_info(months)")}
    for col, typ in EXTRA_MONTH_COLUMNS.items():
        if col not in have:
            conn.execute(f"ALTER TABLE months ADD COLUMN {col} {typ}")
    conn.commit()
    return conn


# --- Contract symbols ----------------------------------------------------------------------------

def is_option(symbol: str) -> bool:
    return len(symbol) > 15 and symbol[-9] in "CP"


def multiplier(symbol: str) -> int:
    return 100 if is_option(symbol) else 1


def underlying_of(symbol: str) -> str:
    return symbol[:-15] if is_option(symbol) else symbol


def expiry_of(symbol: str) -> date:
    return datetime.strptime(symbol[-15:-9], "%y%m%d").date()


def tick(price: float, symbol: str, up: bool | None = None) -> float:
    """Round to a price increment every listed option accepts: $0.01 for SPY, else $0.05 under $3 and $0.10
    above (the coarse non-penny grid, valid for penny-program classes too). up=None rounds to nearest."""
    t = 0.01 if underlying_of(symbol) == "SPY" else (0.05 if price < 3 else 0.10)
    n = price / t
    n = math.ceil(n - 1e-9) if up else (math.floor(n + 1e-9) if up is False else round(n))
    return round(max(n, 1) * t, 2)


# --- Signal --------------------------------------------------------------------------------------

def next_month_start(d: date) -> date:
    return first_of_month(d.replace(day=28) + timedelta(days=4))


@dataclass(frozen=True)
class Decision:
    month_end: date
    scores: dict[str, float]
    trend_on: dict[str, bool]
    held: list[str]
    weights: dict[str, float]


def decide(datas: dict[str, MarketData], month_end: date) -> Decision:
    """The backtested rule at `month_end`. Refuses if any fund lacks a bar on that day (stale or bad data)."""
    missing = [s for s, d in datas.items() if not any(b.day == month_end for b in d.bars)]
    if missing:
        raise ValueError(f"no {month_end} close yet for {', '.join(missing)}")
    end_before = next_month_start(month_end)
    out = dual_rule(datas, TREND, end_before)(month_end)
    if out is None:
        raise ValueError(f"not enough history for a decision at {month_end}")
    scores, held, weights = out
    ends = month_ends(common_days(datas, end_before), end_before)
    on = {s: signals(ends, {b.day: b.close for b in d.bars}, TREND)[month_end][1] for s, d in datas.items()}
    return Decision(month_end, scores, on, [s for s in datas if held[s]], weights)


def fund_budget(decision: Decision, fund: str, equity: float) -> float:
    return decision.weights[fund] * SLEEVE * equity


def target_shares(decision: Decision, equity: float, prices: dict[str, float]) -> dict[str, int]:
    """Whole shares per fund: weight (0.5) x SLEEVE (20%) x equity / price, rounded to the nearest share."""
    return {s: round(fund_budget(decision, s, equity) / prices[s]) for s in decision.held}


# --- Choosing the call ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CallPick:
    symbol: str
    qty: int
    bid: float
    ask: float
    delta: float | None


def third_friday(d: date) -> bool:
    return d.weekday() == 4 and 15 <= d.day <= 21


def pick_call(quotes: list, spot: float, budget: float, expiry: date) -> CallPick | str:
    """From one expiry's call quotes: delta closest to 0.80 (or strike nearest 88% of spot when no deltas),
    spread within MAX_SPREAD, contracts = budget / cost rounded (at least one if it costs <= 1.5x budget)."""
    calls = [q for q in quotes if is_option(q.symbol) and q.symbol[-9] == "C" and expiry_of(q.symbol) == expiry
             and q.bid and q.ask and q.ask >= q.bid > 0]
    if not calls:
        return "no two-sided call quotes"
    with_delta = [q for q in calls if q.delta is not None]
    if with_delta:
        best = min(with_delta, key=lambda q: abs(q.delta - TARGET_DELTA))
    else:
        best = min(calls, key=lambda q: abs(int(q.symbol[-8:]) / 1000 - 0.88 * spot))
    mid = (best.bid + best.ask) / 2
    if (best.ask - best.bid) / mid > MAX_SPREAD:
        return f"quote too wide ({best.bid:.2f} x {best.ask:.2f})"
    cost = best.ask * 100
    qty = round(budget / cost)
    if qty == 0 and cost <= MAX_OVER_BUDGET * budget:
        qty = 1
    if qty == 0:
        return f"one contract (${cost:,.0f}) is over 1.5x the ${budget:,.0f} budget"
    return CallPick(best.symbol, qty, best.bid, best.ask, best.delta)


def choose_call(source, fund: str, today: date, budget: float) -> CallPick | str:
    """Live chain from AlpacaMarketSource: first third-Friday expiry 75+ days out (else the first one)."""
    contracts = source.fetch_contracts(fund, today + timedelta(days=MIN_DTE), today + timedelta(days=MIN_DTE + 90))
    expiries = sorted({c.expiration for c in contracts if c.option_type == "call" and c.tradable})
    if not expiries:
        return "no listed expiry 75+ days out"
    expiry = next((e for e in expiries if third_friday(e)), expiries[0])
    u = source.fetch_underlying(fund)
    spot = u.last_price or ((u.bid or 0) + (u.ask or 0)) / 2
    quotes = source.fetch_chain(fund, expiry, expiry, strike_gte=0.6 * spot, strike_lte=spot)
    return pick_call(quotes, spot, budget, expiry)


# --- The book ------------------------------------------------------------------------------------

QuoteFn = Callable[[str], tuple[float, float] | None]           # symbol -> (bid, ask)
Chooser = Callable[[str, float], CallPick | str]                # fund, budget -> pick or why not


class DualMomentumBook(OvernightBasket):
    """Records (orders, holdings, fills) via the overnight basket's bookkeeping, in this strategy's own database."""

    def __init__(self, conn: sqlite3.Connection, sleep: Callable[[float], None] = _time.sleep):
        super().__init__(conn)
        self.sleep = sleep

    def month(self, month_end: date) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM months WHERE signal_month_end=?", (month_end.isoformat(),)).fetchone()

    def _save(self, month_end: date, **cols) -> None:
        sets = ", ".join(f"{k}=?" for k in cols)
        vals = [json.dumps(v) if isinstance(v, (dict, list)) else v for v in cols.values()]
        self.conn.execute(f"UPDATE months SET {sets} WHERE signal_month_end=?", (*vals, month_end.isoformat()))
        self.conn.commit()

    def _next_cid(self, today: date, side: str, sym: str) -> str:
        n = self.conn.execute("SELECT COUNT(*) FROM orders WHERE day=? AND side=? AND symbol=?",
                              (today.isoformat(), side, sym)).fetchone()[0]
        return f"{PREFIX}{today:%Y%m%d}-{side}-{sym}" + (f"-{n + 1}" if n else "")

    # --- plan --------------------------------------------------------------------------------
    def plan(self, broker: BrokerAPI, today: date, decision: Decision, prices: dict[str, float],
             resize: bool = False) -> sqlite3.Row:
        """Fix the month's targets (once; resize=True re-plans). Share funds get share counts now; option funds
        either keep a call already held (same fund, 40+ days left, not resizing) or wait in `pending` for a
        contract to be chosen once the options market is open."""
        row = self.month(decision.month_end)
        if row is not None and not resize:
            return row
        equity = broker.equity()
        held = self.holdings()
        targets: dict[str, int] = {}
        pending: list[str] = []
        for fund in decision.held:
            if fund in SHARES_ONLY:
                targets[fund] = round(fund_budget(decision, fund, equity) / prices[fund])
                continue
            keep = [s for s in held if is_option(s) and underlying_of(s) == fund
                    and (expiry_of(s) - today).days >= ROLL_DTE]
            if keep and not resize:
                targets.update({s: held[s] for s in keep})
            else:
                pending.append(fund)
        if row is None:
            self.conn.execute("INSERT INTO months (signal_month_end, decided, held, scores, trend, targets, equity, "
                              "pending) VALUES (?,?,?,?,?,?,?,?)",
                              (decision.month_end.isoformat(), today.isoformat(), json.dumps(decision.held),
                               json.dumps({s: round(v, 4) for s, v in decision.scores.items()}),
                               json.dumps(decision.trend_on), json.dumps(targets), equity, json.dumps(pending)))
            self.conn.commit()
        else:
            self._save(decision.month_end, targets=targets, pending=pending, equity=equity, done=None,
                       start_value=None, alerted=None, note=f"resized {today.isoformat()}")
        return self.month(decision.month_end)

    # --- shares --------------------------------------------------------------------------------
    def trade_shares(self, broker: BrokerAPI, now: datetime, decision: Decision, market_open: bool,
                     kill_level: int, stop_file: bool) -> Result:
        """Share part of the month's targets: auction orders before 9:28 ET, market orders once open."""
        res = Result("shares")
        local = now.astimezone(ET)
        today = local.date()
        res.notes += self.sync(broker)
        if any(not is_option(r["symbol"]) for r in self.working()):
            res.notes.append("share orders from an earlier attempt are still working")
            return res
        if local.time() <= OPG_CUTOFF and not market_open:
            auction = "open"
        elif market_open:
            auction = "now"
        else:
            res.notes.append(f"market closed and past the opening-auction cutoff ({local:%H:%M} ET); will retry")
            return res
        row = self.month(decision.month_end)
        target = {s: q for s, q in json.loads(row["targets"]).items() if not is_option(s)}
        held = {s: q for s, q in self.holdings().items() if not is_option(s)}
        actual = {p.symbol: p.qty for p in broker.positions()}
        deltas = {s: target.get(s, 0) - held.get(s, 0) for s in sorted(set(target) | set(held))}
        blocked = kill_level != 0 or stop_file
        if blocked and any(d > 0 for d in deltas.values()):
            res.notes.append("not buying: " + ("STOP file present" if stop_file else f"kill switch level {kill_level}"))
        for sym, d in sorted(deltas.items(), key=lambda kv: kv[1]):        # sells first
            if d == 0 or (d > 0 and blocked):
                continue
            if d < 0:
                d = -min(-d, max(actual.get(sym, 0), 0))
                if d == 0:
                    res.skipped.append(f"{sym} (broker shows no shares)")
                    continue
            side = "buy" if d > 0 else "sell"
            cid = self._next_cid(today, side, sym)
            try:
                order = broker.submit_auction(cid, sym, side, abs(d), auction)
            except SubmitTimeout as exc:
                order = None
                res.notes.append(f"{sym}: no answer from broker ({exc}); will check")
            self._record(cid, today, side, sym, abs(d), order)
            if order is not None and order.status == OrderStatus.REJECTED:
                res.skipped.append(f"{sym} {side} (rejected)")
            else:
                res.submitted.append(f"{side} {abs(d)} {sym} ({'opening auction' if auction == 'open' else 'market'})")
        return res

    # --- options -------------------------------------------------------------------------------
    def trade_options(self, broker: BrokerAPI, now: datetime, decision: Decision, choose: Chooser, quote: QuoteFn,
                      kill_level: int, stop_file: bool, prices: dict[str, float]) -> Result:
        """Option part, market open only: sell calls no longer wanted, choose contracts for pending funds
        (shares if no acceptable call), then buy. Each option order is chased mid -> ask (bid for sells)."""
        res = Result("options")
        today = now.astimezone(ET).date()
        res.notes += self.sync(broker)
        if any(is_option(r["symbol"]) for r in self.working()):
            res.notes.append("option orders from an earlier attempt are still working")
            return res
        row = self.month(decision.month_end)
        targets = json.loads(row["targets"])
        pending = json.loads(row["pending"] or "[]")
        held = self.holdings()
        for sym, q in sorted(held.items()):                              # sells first: unwanted or rolled calls
            if is_option(sym) and q > targets.get(sym, 0):
                self._work_option(broker, quote, today, sym, "sell", q - targets.get(sym, 0), res)
        blocked = kill_level != 0 or stop_file
        if blocked:
            if pending or any(is_option(s) and q > held.get(s, 0) for s, q in targets.items()):
                res.notes.append("not buying: " + ("STOP file present" if stop_file else f"kill switch level {kill_level}"))
            return res
        equity = row["equity"]
        for fund in list(pending):
            pick = choose(fund, fund_budget(decision, fund, equity))
            if isinstance(pick, CallPick):
                targets[pick.symbol] = pick.qty
                res.notes.append(f"{fund}: {pick.qty} x {pick.symbol} (delta {pick.delta or 0:.2f}, ask {pick.ask:.2f})")
            else:
                targets[fund] = round(fund_budget(decision, fund, equity) / prices[fund])
                res.notes.append(f"{fund}: shares instead of a call ({pick})")
            pending.remove(fund)
            self._save(decision.month_end, targets=targets, pending=pending)
        held = self.holdings()
        for sym, q in sorted(targets.items()):
            if is_option(sym) and q > held.get(sym, 0):
                self._work_option(broker, quote, today, sym, "buy", q - held.get(sym, 0), res)
        return res

    def _work_option(self, broker: BrokerAPI, quote: QuoteFn, today: date, sym: str, side: str, qty: int,
                     res: Result) -> int:
        """Limit order stepped from the mid to the ask (buys) or bid (sells), re-quoted each step; each unfilled
        step is cancelled (and confirmed cancelled) before the next. Returns contracts filled."""
        base, filled = self._filled_today(today, side, sym), 0         # earlier same-day attempts don't count
        intent = "buy_to_open" if side == "buy" else "sell_to_close"
        for step in range(CHASE_STEPS + 1):
            q = quote(sym)
            if not q or not (q[0] > 0 and q[1] >= q[0]):
                res.notes.append(f"{sym}: no usable quote; will retry")
                break
            bid, ask = q
            frac = step / CHASE_STEPS
            px = (bid + ask) / 2 + (frac if side == "buy" else -frac) * (ask - bid) / 2
            px = tick(px, sym, up=(side == "buy") if step == CHASE_STEPS else None)
            cid = self._next_cid(today, side, sym)
            try:
                order = broker.submit_option(cid, sym, side, qty - filled, px, intent)
            except SubmitTimeout as exc:
                order = None
                res.notes.append(f"{sym}: no answer from broker ({exc}); will check")
            self._record(cid, today, side, sym, qty - filled, order)
            if order is not None and order.status == OrderStatus.REJECTED:
                res.skipped.append(f"{sym} {side} at {px:.2f} (rejected)")
                break
            self.sleep(CHASE_WAIT)
            self.sync(broker)
            filled = self._filled_today(today, side, sym) - base
            if filled >= qty or order is None:
                break
            self._cancel_and_confirm(broker, order.broker_id)
            filled = self._filled_today(today, side, sym) - base
            if filled >= qty:
                break
        if filled:
            res.submitted.append(f"{side} {filled} {sym}")
        if filled < qty and not any(r["symbol"] == sym for r in self.working()):
            res.notes.append(f"{sym}: {side} {filled} of {qty} filled; will retry")
        return filled

    def _filled_today(self, today: date, side: str, sym: str) -> int:
        return self.conn.execute("SELECT COALESCE(SUM(filled_qty), 0) FROM orders WHERE day=? AND side=? AND symbol=?",
                                 (today.isoformat(), side, sym)).fetchone()[0]

    def _cancel_and_confirm(self, broker: BrokerAPI, broker_id: str) -> None:
        try:
            broker.cancel_order(broker_id)
        except Exception as exc:                          # e.g. it filled in the meantime
            log.info("cancel %s: %s", broker_id, exc)
        for _ in range(20):
            self.sync(broker)
            if not any(r["broker_id"] == broker_id for r in self.working()):
                return
            self.sleep(1.0)

    # --- daily checks ----------------------------------------------------------------------------
    def roll_due(self, today: date, decision: Decision) -> list[str]:
        """Calls with FORCE_DTE days or less left: drop them from the month's targets and re-pend their fund."""
        row = self.month(decision.month_end)
        if row is None:
            return []
        targets = json.loads(row["targets"])
        pending = json.loads(row["pending"] or "[]")
        due = [s for s in targets if is_option(s) and (expiry_of(s) - today).days <= FORCE_DTE]
        for s in due:
            del targets[s]
            if underlying_of(s) not in pending:
                pending.append(underlying_of(s))
        if due:
            self._save(decision.month_end, targets=targets, pending=pending, done=None)
        return due

    def finish(self, month_end: date, today: date, value: float | None = None) -> bool:
        """Mark the month done once nothing is pending or working and holdings match its targets."""
        row = self.month(month_end)
        if row is None or self.working() or json.loads(row["pending"] or "[]"):
            return False
        target = {s: q for s, q in json.loads(row["targets"]).items() if q}
        if self.holdings() != target:
            return False
        self._save(month_end, done=today.isoformat(), start_value=value)
        return True

    def value(self, positions: list[BrokerPosition]) -> float:
        """Market value of our holdings (our share of each broker position)."""
        held = self.holdings()
        out = 0.0
        for p in positions:
            if p.symbol in held and p.qty and p.market_value is not None:
                out += p.market_value * min(held[p.symbol] / p.qty, 1.0)
        return out

    def loss_alert(self, month_end: date, value: float) -> str | None:
        """Once per month: the message if value fell LOSS_ALERT below its value after the rebalance."""
        row = self.month(month_end)
        if row is None or row["start_value"] is None or row["alerted"] or value > row["start_value"] - LOSS_ALERT:
            return None
        self._save(month_end, alerted=now_utc().isoformat())
        return (f"Dual momentum is down ${row['start_value'] - value:,.0f} since the {month_end:%B} rebalance "
                f"(${row['start_value']:,.0f} -> ${value:,.0f}). Nothing is sold automatically.")

    # --- P&L for the kill switch -----------------------------------------------------------------
    def _flows(self, where: str = "1=1", args: tuple = ()) -> float:
        """Cash in (sells) minus cash out (buys), all fills matching `where`."""
        total = 0.0
        for r in self.conn.execute(f"SELECT side, symbol, filled_qty, fill_price FROM orders WHERE {where}", args):
            if r["filled_qty"] and r["fill_price"]:
                v = r["filled_qty"] * r["fill_price"] * multiplier(r["symbol"])
                total += v if r["side"] == "sell" else -v
        return total

    def pnl_now(self, positions: list[BrokerPosition]) -> float:
        """All-time P&L: holdings at market plus net cash from every fill."""
        return self.value(positions) + self._flows()

    def pnl_at_prev_close(self, today: date, positions: list[BrokerPosition]) -> float:
        """All-time P&L at the previous close; computed once per day and kept. Holdings at the previous close
        are today's minus today's net fills, valued at the broker's previous-close price (a position sold out
        today is valued at its sale price, the nearest number available)."""
        r = self.conn.execute("SELECT pnl_prev_close FROM day_marks WHERE day=?", (today.isoformat(),)).fetchone()
        if r is not None:
            return r[0]
        held = dict(self.holdings())
        last = {}
        for o in self.conn.execute("SELECT side, symbol, filled_qty, fill_price FROM orders WHERE day=?",
                                   (today.isoformat(),)):
            if o["filled_qty"]:
                held[o["symbol"]] = held.get(o["symbol"], 0) - (o["filled_qty"] if o["side"] == "buy" else -o["filled_qty"])
                last[o["symbol"]] = o["fill_price"]
        prev = {p.symbol: p.lastday_price for p in positions}
        value = sum(q * (prev.get(s) or last.get(s) or 0.0) * multiplier(s) for s, q in held.items() if q)
        pnl = value + self._flows("day < ?", (today.isoformat(),))
        self.conn.execute("INSERT OR REPLACE INTO day_marks VALUES (?, ?)", (today.isoformat(), pnl))
        self.conn.commit()
        return pnl

    def realized(self) -> float:
        return sum(r["qty"] * ((r["sell_price"] or 0) - r["buy_price"]) * multiplier(r["symbol"])
                   for r in self.conn.execute("SELECT * FROM lots"))

    def cost_basis(self) -> dict[str, tuple[int, float]]:
        return {r["symbol"]: (r["qty"], r["cost"]) for r in self.conn.execute("SELECT * FROM holdings WHERE qty != 0")}


def load_live(fetch=None) -> dict[str, MarketData]:
    """Fresh Yahoo daily data for the five funds (no cache: the month-end close must be in it)."""
    from research.monthly_trend import fetch_yahoo
    fetch = fetch or fetch_yahoo
    out = {}
    for s in ASSETS:
        bars, divs = parse_yahoo(fetch(s), allow_splits=True)
        out[s] = MarketData(bars, divs, [(date(1990, 1, 1), 0.0)])
    return out
