"""Dual momentum, PAPER only: once a month hold the top 2 of SPY/EFA/IEF/VNQ/DBC by 12-month total return, each
only while it's above its 10-month average; a fund that fails leaves its half in cash. Owner decision 2026-10-20 to
paper-trade it despite the NO-GO in docs/research/dual_momentum_backtest_2026-10-20.md.

  * Signal: exactly the backtested rule (research/dual_momentum.py), on fresh free Yahoo daily data, using the
    last month-end close. The month-end must be the exchange's real last trading day of that month.
  * Size: 20% of equity when both funds are held, 10% per fund, whole shares (owner decision 2026-10-21, up
    from one equal-risk trade of ~6%). At the backtest's worst drop (20.9%) that's ~4% of the account.
  * When: the first weekday run of each month that's a trading day. Opening-auction orders before 9:28 ET;
    after the open (the Mac slept), plain market orders. Each month is done once; a missed or partly failed
    month is retried on the next weekday run.
  * Sells and trims always go; buys wait if the kill switch isn't NORMAL or a STOP file exists.
  * Order/holding records reuse the overnight basket's bookkeeping (own database, `oqm-` order ids). The options
    trader asks this module what shares and open orders to expect, including SPY shares.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from core.config import PROJECT_ROOT
from core.logging_setup import get_logger
from execution.broker_api import BrokerAPI, OrderStatus, SubmitTimeout
from execution.overnight_basket import ET, OPG_CUTOFF, OvernightBasket, Result, now_utc  # noqa: F401
from execution.overnight_basket import open_db as _open_basket_db
from research.dual_momentum import ASSETS, TREND, dual_rule
from research.monthly_trend import MarketData, first_of_month, month_ends, parse_yahoo, signals
from research.multi_asset_trend import common_days

log = get_logger("dual_momentum_paper")
DB_PATH = PROJECT_ROOT / "data" / "storage" / "dual_momentum.db"
STOP_FILE = PROJECT_ROOT / "STOP"
PREFIX = "oqm-"
SLEEVE = 0.20                                         # share of equity when both funds are held (owner, 2026-10-21)

SCHEMA = """
CREATE TABLE IF NOT EXISTS months (
    signal_month_end TEXT PRIMARY KEY, decided TEXT, held TEXT, scores TEXT, trend TEXT, targets TEXT,
    equity REAL, done TEXT, note TEXT
);
"""


def open_db(path: Path = DB_PATH) -> sqlite3.Connection:
    conn = _open_basket_db(path)
    conn.executescript(SCHEMA)
    return conn


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


def target_shares(decision: Decision, equity: float, prices: dict[str, float]) -> dict[str, int]:
    """Whole shares per fund held: weight (0.5) x SLEEVE (20%) x equity / price, rounded to the nearest share."""
    return {s: round(decision.weights[s] * SLEEVE * equity / prices[s]) for s in decision.held}


class DualMomentumBook(OvernightBasket):
    """Records (orders, holdings, fills) via the overnight basket's bookkeeping, in this strategy's own database."""

    def month(self, month_end: date) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM months WHERE signal_month_end=?", (month_end.isoformat(),)).fetchone()

    def rebalance(self, broker: BrokerAPI, now: datetime, decision: Decision, prices: dict[str, float],
                  market_open: bool, kill_level: int, stop_file: bool, resize: bool = False) -> Result:
        """Trade holdings to this month's targets. Targets are fixed when the month is first traded (so retries
        don't chase prices); resize=True recomputes them from today's equity and prices (after a size change)."""
        res = Result("rebalance")
        local = now.astimezone(ET)
        today = local.date()
        res.notes += self.sync(broker)
        row = self.month(decision.month_end)
        if row is not None and row["done"] and not resize:
            res.notes.append(f"{decision.month_end:%B} rebalance already done")
            return res
        if self.working():
            res.notes.append("orders from an earlier attempt are still working")
            return res
        if local.time() <= OPG_CUTOFF and not market_open:
            auction = "open"
        elif market_open:
            auction = "now"
        else:
            res.notes.append(f"market closed and past the opening-auction cutoff ({local:%H:%M} ET); will retry")
            return res

        equity = broker.equity()
        target = target_shares(decision, equity, prices)
        if row is None:
            self.conn.execute("INSERT INTO months VALUES (?,?,?,?,?,?,?,?,?)",
                              (decision.month_end.isoformat(), today.isoformat(), json.dumps(decision.held),
                               json.dumps({s: round(v, 4) for s, v in decision.scores.items()}),
                               json.dumps(decision.trend_on), json.dumps(target), equity, None, None))
            self.conn.commit()
        elif resize:
            self.conn.execute("UPDATE months SET targets=?, equity=?, done=NULL, note=? WHERE signal_month_end=?",
                              (json.dumps(target), equity, f"resized {today.isoformat()}", decision.month_end.isoformat()))
            self.conn.commit()
        else:                                        # a retry: keep the month's original targets
            target = json.loads(row["targets"])
        held = self.holdings()
        actual = {p.symbol: p.qty for p in broker.positions()}
        deltas = {s: target.get(s, 0) - held.get(s, 0) for s in sorted(set(target) | set(held))}
        buys_blocked = kill_level != 0 or stop_file
        if buys_blocked and any(d > 0 for d in deltas.values()):
            res.notes.append("not buying: " + ("STOP file present" if stop_file else f"kill switch level {kill_level}"))
        for sym, d in sorted(deltas.items(), key=lambda kv: kv[1]):        # sells first
            if d == 0 or (d > 0 and buys_blocked):
                continue
            if d < 0:
                d = -min(-d, max(actual.get(sym, 0), 0))
                if d == 0:
                    res.skipped.append(f"{sym} (broker shows no shares)")
                    continue
            side = "buy" if d > 0 else "sell"
            n = self.conn.execute("SELECT COUNT(*) FROM orders WHERE day=? AND side=? AND symbol=?",
                                  (today.isoformat(), side, sym)).fetchone()[0]
            cid = f"{PREFIX}{today:%Y%m%d}-{side}-{sym}" + (f"-{n + 1}" if n else "")
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
        if not res.submitted and not buys_blocked and not self.working():
            self.finish(decision.month_end, today)
        return res

    def finish(self, month_end: date, today: date) -> bool:
        """Mark the month done once nothing is working and holdings match its targets."""
        row = self.month(month_end)
        if row is None or self.working():
            return False
        target = {s: q for s, q in json.loads(row["targets"]).items() if q}
        if self.holdings() != target:
            return False
        self.conn.execute("UPDATE months SET done=? WHERE signal_month_end=?", (today.isoformat(), month_end.isoformat()))
        self.conn.commit()
        return True

    def realized(self) -> float:
        return sum(r["qty"] * ((r["sell_price"] or 0) - r["buy_price"]) for r in self.conn.execute("SELECT * FROM lots"))

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

