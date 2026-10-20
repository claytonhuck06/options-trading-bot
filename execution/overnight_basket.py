"""Overnight basket, PAPER only: buy the year's 20 picks in the closing auction, sell them in the next
opening auction. Owner decision 2026-10-20 to paper-trade it despite the NO-GO in
docs/research/overnight_screener_liquid_2026-10-20.md.

  * Picks: fixed for the calendar year by the tested rule ($1B+/day names, top 20 by the 3 prior years),
    stored in config/overnight_picks.json (`python main.py overnight-basket pick`).
  * Size: the whole basket is one trade in the equal risk pool: 1.5% of equity at risk, measured against a
    12% overnight gap (the worst basket night in the test was -11.8%, 2020-03-16). So ~12.5% of equity,
    split equally, whole shares.
  * Buy (15:30-15:49 ET): market-on-close orders. Only on full-length days, only if the kill switch is
    NORMAL, there's no STOP file, and nothing is still held from the night before.
  * Sell (before 9:28 ET): market-on-open orders for everything held. If that window was missed (the Mac
    slept), a plain market order once the market is open. Selling never waits on the kill switch.
  * Records live in data/storage/overnight_basket.db. The options trader asks this module what shares and
    open orders to expect (`expectations`), so the basket never looks like an unknown position to it.
"""

from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, time, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from core.config import PROJECT_ROOT
from core.logging_setup import get_logger
from execution.broker_api import BrokerAPI, OrderStatus, SubmitTimeout

log = get_logger("overnight_basket")
ET = ZoneInfo("America/New_York")
DB_PATH = PROJECT_ROOT / "data" / "storage" / "overnight_basket.db"
PICKS_PATH = PROJECT_ROOT / "config" / "overnight_picks.json"
STOP_FILE = PROJECT_ROOT / "STOP"
PREFIX = "oqn-"
RISK_PCT = 0.015
STRESS_GAP = 0.12
BUY_FROM, BUY_CUTOFF = time(15, 30), time(15, 49)     # Alpaca takes market-on-close orders until 15:50
OPG_CUTOFF = time(9, 27)                               # ... and market-on-open orders until 9:28
EXCLUDED = {"SPY"}                                     # the options underlying: its shares mean "assignment" to the trader

SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
    client_id TEXT PRIMARY KEY, broker_id TEXT, day TEXT, side TEXT, symbol TEXT, qty INTEGER,
    status TEXT, filled_qty INTEGER DEFAULT 0, fill_price REAL, applied INTEGER DEFAULT 0, note TEXT
);
CREATE TABLE IF NOT EXISTS holdings (symbol TEXT PRIMARY KEY, qty INTEGER, cost REAL, bought TEXT);
CREATE TABLE IF NOT EXISTS nights (
    sold TEXT PRIMARY KEY, bought TEXT, names INTEGER, invested REAL, proceeds REAL, pnl REAL, ret REAL
);
CREATE TABLE IF NOT EXISTS lots (bought TEXT, symbol TEXT, qty INTEGER, buy_price REAL, sell_price REAL, sold TEXT);
"""


def open_db(path: Path = DB_PATH) -> sqlite3.Connection:
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn


def load_picks(year: int, path: Path = PICKS_PATH) -> list[str]:
    try:
        return [s for s in json.loads(path.read_text()).get(str(year), []) if s not in EXCLUDED]
    except FileNotFoundError:
        return []


def save_picks(year: int, symbols: list[str], path: Path = PICKS_PATH) -> None:
    data = json.loads(path.read_text()) if path.exists() else {}
    data[str(year)] = symbols
    path.write_text(json.dumps(data, indent=2) + "\n")


def size(equity: float, prices: dict[str, float]) -> dict[str, int]:
    """Whole shares per name: equal dollars, total ≈ RISK_PCT / STRESS_GAP of equity. A name whose single share
    costs more than twice its slot is skipped; otherwise round to the nearest share (at least 1)."""
    if not prices:
        return {}
    slot = equity * RISK_PCT / STRESS_GAP / len(prices)
    out = {}
    for sym, px in prices.items():
        if px <= 0 or px > 2 * slot:
            continue
        out[sym] = max(1, round(slot / px))
    return out


@dataclass
class Result:
    action: str
    submitted: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def summary(self) -> str:
        parts = [f"{self.action}: {len(self.submitted)} order(s)"]
        if self.skipped:
            parts.append("skipped " + ", ".join(self.skipped))
        parts += self.notes
        return "; ".join(parts)


class OvernightBasket:
    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn

    # --- records -------------------------------------------------------------------------------
    def holdings(self) -> dict[str, int]:
        return {r["symbol"]: r["qty"] for r in self.conn.execute("SELECT symbol, qty FROM holdings WHERE qty != 0")}

    def working(self) -> list[sqlite3.Row]:
        return list(self.conn.execute("SELECT * FROM orders WHERE status IN ('working', 'partial', 'unknown')"))

    def _record(self, client_id: str, day: date, side: str, symbol: str, qty: int, order) -> None:
        self.conn.execute("INSERT OR REPLACE INTO orders (client_id, broker_id, day, side, symbol, qty, status) "
                          "VALUES (?,?,?,?,?,?,?)",
                          (client_id, order.broker_id if order else None, day.isoformat(), side, symbol, qty,
                           order.status.value if order else "unknown"))
        self.conn.commit()

    def sync(self, broker: BrokerAPI) -> list[str]:
        """Ask the broker about every order still open in our records and apply new fills to holdings."""
        notes = []
        for row in self.working():
            order = broker.get_order(row["broker_id"]) if row["broker_id"] else broker.find_order(row["client_id"])
            if order is None:
                self.conn.execute("UPDATE orders SET status='rejected', note='not found at broker' WHERE client_id=?",
                                  (row["client_id"],))
                notes.append(f"{row['symbol']} {row['side']} order never reached the broker")
                continue
            new = order.filled_qty - row["applied"]
            price = order.leg_fill_prices.get(row["symbol"])
            if new > 0:
                self._apply(row, new, price)
            self.conn.execute("UPDATE orders SET broker_id=?, status=?, filled_qty=?, fill_price=?, applied=? "
                              "WHERE client_id=?", (order.broker_id, order.status.value, order.filled_qty, price,
                                                    order.filled_qty, row["client_id"]))
            if order.status in (OrderStatus.REJECTED, OrderStatus.CANCELED) and order.filled_qty < row["qty"]:
                notes.append(f"{row['symbol']} {row['side']} {order.status.value} "
                             f"({order.filled_qty} of {row['qty']} filled)")
        self.conn.commit()
        return notes

    def _apply(self, row: sqlite3.Row, qty: int, price: float | None) -> None:
        sym, day = row["symbol"], row["day"]
        cur = self.conn.execute("SELECT qty, cost, bought FROM holdings WHERE symbol=?", (sym,)).fetchone()
        if row["side"] == "buy":
            q0, c0 = (cur["qty"], cur["cost"]) if cur else (0, 0.0)
            self.conn.execute("INSERT OR REPLACE INTO holdings VALUES (?,?,?,?)",
                              (sym, q0 + qty, (c0 * q0 + (price or 0) * qty) / (q0 + qty), day))
        else:
            if not cur:
                return
            self.conn.execute("INSERT INTO lots VALUES (?,?,?,?,?,?)", (cur["bought"], sym, qty, cur["cost"], price, day))
            left = cur["qty"] - qty
            if left > 0:
                self.conn.execute("UPDATE holdings SET qty=? WHERE symbol=?", (left, sym))
            else:
                self.conn.execute("DELETE FROM holdings WHERE symbol=?", (sym,))

    def expectations(self, broker: BrokerAPI) -> tuple[dict[str, int], set[str]]:
        """For the options trader's reconciliation: the shares we hold and the client ids of our open orders."""
        try:
            self.sync(broker)
        except Exception as exc:                         # a failed check must not hide our records
            log.warning("overnight basket sync failed: %s", exc)
        return self.holdings(), {r["client_id"] for r in self.working()}

    # --- actions -------------------------------------------------------------------------------
    def buy(self, broker: BrokerAPI, now: datetime, prices: dict[str, float], picks: list[str],
            full_day: bool, kill_level: int, stop_file: bool) -> Result:
        res = Result("buy")
        local = now.astimezone(ET)
        today = local.date()
        self.sync(broker)
        held = self.holdings()
        blockers = [
            (not picks, f"no picks for {today.year} (run: python main.py overnight-basket pick)"),
            (not full_day, "not a full trading day"),
            (not (BUY_FROM <= local.time() <= BUY_CUTOFF), f"outside the buy window ({local:%H:%M} ET)"),
            (kill_level != 0, f"kill switch level {kill_level}"),
            (stop_file, "STOP file present"),
            (bool(held), f"still holding {', '.join(sorted(held))} from before"),
            (bool(self.conn.execute("SELECT 1 FROM orders WHERE day=? AND side='buy'", (today.isoformat(),)).fetchone()),
             "already bought today"),
        ]
        for bad, why in blockers:
            if bad:
                res.notes.append(f"not buying: {why}")
                return res
        missing = [s for s in picks if s not in prices]
        res.skipped += [f"{s} (no price)" for s in missing]
        qty = size(broker.equity(), {s: prices[s] for s in picks if s in prices})
        res.skipped += [f"{s} (one share costs more than twice its slot)" for s in picks if s in prices and s not in qty]
        for sym, q in qty.items():
            cid = f"{PREFIX}{today:%Y%m%d}-buy-{sym}"
            try:
                order = broker.submit_auction(cid, sym, "buy", q, "close")
            except SubmitTimeout as exc:
                order = None
                res.notes.append(f"{sym}: no answer from broker ({exc}); will check")
            self._record(cid, today, "buy", sym, q, order)
            if order is not None and order.status == OrderStatus.REJECTED:
                res.skipped.append(f"{sym} (rejected)")
            else:
                res.submitted.append(f"{q} {sym}")
        return res

    def sell(self, broker: BrokerAPI, now: datetime, market_open: bool, trading_day: bool = True) -> Result:
        res = Result("sell")
        local = now.astimezone(ET)
        today = local.date()
        res.notes += self.sync(broker)
        if not trading_day:
            res.notes.append("market closed today; selling at the next open")
            return res
        pending = {r["symbol"] for r in self.working() if r["side"] == "sell"}
        held = {s: q for s, q in self.holdings().items() if s not in pending}
        if not held:
            if not pending:
                res.notes.append("nothing to sell")
            return res
        if local.time() <= OPG_CUTOFF and not market_open:
            auction = "open"
        elif market_open:
            auction = "now"
        else:
            res.notes.append(f"market closed and past the opening-auction cutoff ({local:%H:%M} ET); will retry")
            return res
        actual = {p.symbol: p.qty for p in broker.positions()}
        for sym, q in sorted(held.items()):
            q = min(q, max(actual.get(sym, 0), 0))
            if q <= 0:
                res.skipped.append(f"{sym} (broker shows no shares)")
                continue
            n = self.conn.execute("SELECT COUNT(*) FROM orders WHERE day=? AND side='sell' AND symbol=?",
                                  (today.isoformat(), sym)).fetchone()[0]
            cid = f"{PREFIX}{today:%Y%m%d}-sell-{sym}" + (f"-{n + 1}" if n else "")
            try:
                order = broker.submit_auction(cid, sym, "sell", q, auction)
            except SubmitTimeout as exc:
                order = None
                res.notes.append(f"{sym}: no answer from broker ({exc}); will check")
            self._record(cid, today, "sell", sym, q, order)
            if order is not None and order.status == OrderStatus.REJECTED:
                res.notes.append(f"{sym} sell rejected")
            else:
                res.submitted.append(f"{q} {sym} ({'opening auction' if auction == 'open' else 'market'})")
        return res

    def settle_night(self, today: date) -> dict | None:
        """Once everything bought on the last buy day is sold, write that night's result."""
        if self.holdings() or any(r["side"] == "sell" for r in self.working()):
            return None
        rows = list(self.conn.execute("SELECT * FROM lots WHERE sold=?", (today.isoformat(),)))
        if not rows or self.conn.execute("SELECT 1 FROM nights WHERE sold=?", (today.isoformat(),)).fetchone():
            return None
        invested = sum(r["qty"] * r["buy_price"] for r in rows)
        proceeds = sum(r["qty"] * (r["sell_price"] or 0) for r in rows)
        night = {"sold": today.isoformat(), "bought": rows[0]["bought"], "names": len({r["symbol"] for r in rows}),
                 "invested": invested, "proceeds": proceeds, "pnl": proceeds - invested,
                 "ret": proceeds / invested - 1 if invested else 0.0}
        self.conn.execute("INSERT INTO nights VALUES (:sold,:bought,:names,:invested,:proceeds,:pnl,:ret)", night)
        self.conn.commit()
        return night

    def totals(self) -> dict:
        r = self.conn.execute("SELECT COUNT(*) n, COALESCE(SUM(pnl),0) pnl, COALESCE(SUM(invested),0) inv FROM nights").fetchone()
        rets = [x[0] for x in self.conn.execute("SELECT ret FROM nights ORDER BY sold")]
        growth = math.prod(1 + x for x in rets) if rets else 1.0
        return {"nights": r["n"], "pnl": r["pnl"], "compounded": growth - 1,
                "up": sum(1 for x in rets if x > 0)}


def full_trading_day(clock_next_close: str, now: datetime) -> bool:
    """True when the market is open now and closes at 16:00 ET today (not a half day)."""
    close = datetime.fromisoformat(clock_next_close).astimezone(ET)
    return close.date() == now.astimezone(ET).date() and close.time() == time(16, 0)


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
