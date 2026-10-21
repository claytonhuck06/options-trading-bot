"""IBKR PAPER orders for the spike-short shadow log. Refuses to run against anything but a paper account.

Connects to IB Gateway or TWS running on this Mac (logged in by the owner to the PAPER account; this code never
handles a password). Paper guard: live ports (4001, 7496) are refused, and every managed account must be an IBKR
paper account (id starting "DU"), else it disconnects without placing anything.

  * place(rows): for each taken shadow row, SELL SHORT the shadow's 2.7% share count at market, wait for the fill,
    then a BUY STOP at fill x 1.50 (the rules' 50% stop). IBKR's answer (filled, or rejected: e.g. no shares to
    borrow) is recorded either way - that is the point of the test.
  * cover(day): ~15:50 ET, cancel the day's stops and BUY back whatever short is still open at market, then record
    every exit fill (stop or cover) from IBKR's executions.

Settings: config/ibkr_paper.yaml (enabled, host, port, client_id). Orders are tagged orderRef "spike-shadow".
"""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import yaml

from research.spike_short import ET

REF = "spike-shadow"
STOP = 1.50
LIVE_PORTS = {4001, 7496}
FILL_WAIT = 60.0

SCHEMA = """
CREATE TABLE IF NOT EXISTS ibkr_orders (
    day TEXT, symbol TEXT, qty INTEGER, status TEXT, message TEXT, entry_fill REAL, filled INTEGER,
    entry_at TEXT, stop_price REAL, stop_order_id INTEGER, exit_fill REAL, exit_how TEXT, exit_at TEXT,
    account TEXT, PRIMARY KEY (day, symbol));
"""


class PaperGuardError(RuntimeError):
    pass


@dataclass
class Config:
    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 4002                                       # IB Gateway paper; TWS paper is 7497
    client_id: int = 17


def load_config(path: Path) -> Config:
    if not path.exists():
        return Config()
    d = yaml.safe_load(path.read_text()) or {}
    return Config(bool(d.get("enabled", False)), str(d.get("host", "127.0.0.1")), int(d.get("port", 4002)),
                  int(d.get("client_id", 17)))


def open_db(conn: sqlite3.Connection) -> sqlite3.Connection:
    conn.executescript(SCHEMA)
    return conn


def stop_price(fill: float) -> float:
    """Fill x 1.50 rounded UP to the tick (0.0001 under $1, else 0.01)."""
    tick = 0.0001 if fill < 1 else 0.01
    return round(math.ceil(fill * STOP / tick - 1e-9) * tick, 4)


def connect(cfg: Config, ib=None):
    """Connected ib_async.IB on a paper account, or PaperGuardError."""
    if cfg.port in LIVE_PORTS:
        raise PaperGuardError(f"port {cfg.port} is a LIVE trading port; use 4002 (Gateway paper) or 7497 (TWS paper)")
    if ib is None:
        from ib_async import IB
        ib = IB()
    ib.connect(cfg.host, cfg.port, clientId=cfg.client_id, timeout=20, readonly=False)
    accounts = list(ib.managedAccounts())
    if not accounts or not all(a.startswith("DU") for a in accounts):
        ib.disconnect()
        raise PaperGuardError(f"not a paper account ({', '.join(accounts) or 'none reported'}); nothing placed")
    return ib


def _stock(ib, symbol: str):
    from ib_async import Stock
    c = Stock(symbol, "SMART", "USD")
    q = ib.qualifyContracts(c)
    return q[0] if q else None


def _order(kind: str, action: str, qty: int, price: float | None = None):
    from ib_async import MarketOrder, StopOrder
    o = MarketOrder(action, qty) if kind == "MKT" else StopOrder(action, qty, price)
    o.orderRef, o.tif, o.outsideRth = REF, "DAY", False
    return o


def _wait(ib, trade, seconds: float = FILL_WAIT) -> None:
    waited = 0.0
    while not trade.isDone() and waited < seconds:
        ib.sleep(1)
        waited += 1


def _messages(trade) -> str:
    return "; ".join(e.message for e in getattr(trade, "log", []) if getattr(e, "message", ""))[:500]


def place(ib, conn: sqlite3.Connection, day: date, rows: list[dict], make_contract=_stock, make_order=_order) -> list[dict]:
    """Short each taken row's 2.7% share count, then its 50% stop. Records every outcome; returns them."""
    account = ib.managedAccounts()[0]
    out = []
    for r in rows:
        if r.get("status") != "signal" or not r.get("shares_needed"):
            continue
        sym, qty = r["symbol"], int(r["shares_needed"])
        if conn.execute("SELECT 1 FROM ibkr_orders WHERE day=? AND symbol=?", (day.isoformat(), sym)).fetchone():
            continue
        rec = {"day": day.isoformat(), "symbol": sym, "qty": qty, "account": account, "entry_fill": None,
               "filled": 0, "stop_price": None, "stop_order_id": None, "message": ""}
        contract = make_contract(ib, sym)
        if contract is None:
            rec.update(status="no contract", message="IBKR could not find the stock")
        else:
            trade = ib.placeOrder(contract, make_order("MKT", "SELL", qty))
            _wait(ib, trade)
            st = trade.orderStatus
            rec.update(filled=int(st.filled or 0), message=_messages(trade))
            if rec["filled"] > 0:
                rec["entry_fill"] = float(st.avgFillPrice)
                rec["stop_price"] = stop_price(rec["entry_fill"])
                stop = ib.placeOrder(contract, make_order("STP", "BUY", rec["filled"], rec["stop_price"]))
                rec["stop_order_id"] = stop.order.orderId
                rec["status"] = "filled" if rec["filled"] == qty else "partial"
                if not trade.isDone():
                    ib.cancelOrder(trade.order)                 # don't leave the rest working
            else:
                rec["status"] = st.status.lower() if st.status else "no fill"
                if not trade.isDone():
                    ib.cancelOrder(trade.order)
        rec["entry_at"] = datetime.now(ET).isoformat(timespec="seconds")
        conn.execute("INSERT OR REPLACE INTO ibkr_orders (day, symbol, qty, status, message, entry_fill, filled, "
                     "entry_at, stop_price, stop_order_id, account) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                     (rec["day"], sym, qty, rec["status"], rec["message"], rec["entry_fill"], rec["filled"],
                      rec["entry_at"], rec["stop_price"], rec["stop_order_id"], account))
        conn.commit()
        out.append(rec)
    return out


def cover(ib, conn: sqlite3.Connection, day: date, make_contract=_stock, make_order=_order) -> list[dict]:
    """Cancel the day's stops, buy back what's still short, then record each exit fill from IBKR's executions."""
    open_rows = conn.execute("SELECT symbol, filled FROM ibkr_orders WHERE day=? AND filled > 0 AND exit_fill IS NULL",
                             (day.isoformat(),)).fetchall()
    if not open_rows:
        return []
    symbols = {s for s, _ in open_rows}
    for t in ib.openTrades():
        if t.order.orderRef == REF and t.contract.symbol in symbols and t.order.action == "BUY":
            ib.cancelOrder(t.order)
    ib.sleep(2)
    short = {p.contract.symbol: -int(p.position) for p in ib.positions() if p.position < 0}
    covers = {}
    for sym in symbols:
        if short.get(sym, 0) > 0:
            contract = make_contract(ib, sym)
            if contract is not None:
                covers[sym] = ib.placeOrder(contract, make_order("MKT", "BUY", short[sym]))
    for t in covers.values():
        _wait(ib, t)
    buys: dict[str, list[tuple[float, float]]] = {}
    for f in ib.reqExecutions():
        if f.execution.side == "BOT" and f.execution.orderRef == REF and f.contract.symbol in symbols:
            buys.setdefault(f.contract.symbol, []).append((float(f.execution.shares), float(f.execution.price)))
    out = []
    at = datetime.now(ET).isoformat(timespec="seconds")
    for sym in symbols:
        b = buys.get(sym)
        if not b:
            continue
        qty = sum(q for q, _ in b)
        px = sum(q * p for q, p in b) / qty
        how = "cover" if sym in covers else "stop"
        conn.execute("UPDATE ibkr_orders SET exit_fill=?, exit_how=?, exit_at=? WHERE day=? AND symbol=?",
                     (px, how, at, day.isoformat(), sym))
        out.append({"symbol": sym, "exit_fill": px, "exit_how": how})
    conn.commit()
    return out


def message(placed: list[dict]) -> str:
    lines = []
    for r in placed:
        if r["filled"]:
            lines.append(f"{r['symbol']}: shorted {r['filled']:,} at ${r['entry_fill']:.4g}, stop ${r['stop_price']:.4g}")
        else:
            lines.append(f"{r['symbol']}: NOT shorted ({r['status']}) {r['message'][:120]}")
    return "IBKR paper: " + "; ".join(lines) if lines else "IBKR paper: nothing to place"
