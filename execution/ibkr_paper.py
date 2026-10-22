"""IBKR PAPER orders for the spike-short shadow log. Refuses to run against anything but a paper account.

Connects to IB Gateway or TWS running on this Mac (logged in by the owner to the PAPER account; this code never
handles a password). Paper guard: live ports (4001, 7496) are refused, and every managed account must be an IBKR
paper account (id starting "DU"), else it disconnects without placing anything.

  * place(rows): for each taken shadow row and each size ("27" = 2.7% account, "40" = 40% account, owner
    2026-10-21), SELL SHORT that account's share count at market, wait for the fill, then a BUY STOP at fill x 1.50
    (the rules' 50% stop). IBKR's answer (filled, or rejected: e.g. no shares to borrow) is recorded either way.
  * cover(day): ~15:50 ET, cancel the day's stops and BUY back what each size still has short at market, then
    record every exit fill (stop or cover) from IBKR's executions.

Both sizes share ONE IBKR paper account, so a stock taken by both shows as one combined short in IBKR; each size's
orders carry their own orderRef ("spike-shadow-27" / "spike-shadow-40") and are booked separately here.
Settings: config/ibkr_paper.yaml (enabled, host, port, client_id, sizes).
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
SIZES = ("27", "40")
QTY_FIELD = {"27": "shares_needed", "40": "shares_needed_40"}
STOP = 1.50
LIVE_PORTS = {4001, 7496}
FILL_WAIT = 60.0

SCHEMA = """
CREATE TABLE IF NOT EXISTS ibkr_orders (
    day TEXT, symbol TEXT, qty INTEGER, status TEXT, message TEXT, entry_fill REAL, filled INTEGER,
    entry_at TEXT, stop_price REAL, stop_order_id INTEGER, exit_fill REAL, exit_how TEXT, exit_at TEXT,
    account TEXT, size TEXT NOT NULL DEFAULT '27', PRIMARY KEY (day, symbol, size));
"""


class PaperGuardError(RuntimeError):
    pass


@dataclass
class Config:
    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 4002                                       # IB Gateway paper; TWS paper is 7497
    client_id: int = 17
    sizes: tuple[str, ...] = SIZES


def load_config(path: Path) -> Config:
    if not path.exists():
        return Config()
    d = yaml.safe_load(path.read_text()) or {}
    sizes = tuple(str(x) for x in d.get("sizes", SIZES) if str(x) in SIZES)
    return Config(bool(d.get("enabled", False)), str(d.get("host", "127.0.0.1")), int(d.get("port", 4002)),
                  int(d.get("client_id", 17)), sizes)


def ref(size: str) -> str:
    return f"{REF}-{size}"


def open_db(conn: sqlite3.Connection) -> sqlite3.Connection:
    cols = [r[1] for r in conn.execute("PRAGMA table_info(ibkr_orders)")]
    if cols and "size" not in cols:                        # pre-2-size table: only ever created empty
        if conn.execute("SELECT COUNT(*) FROM ibkr_orders").fetchone()[0]:
            raise RuntimeError("ibkr_orders has rows from the one-size version; migrate by hand")
        conn.execute("DROP TABLE ibkr_orders")
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


def _order(kind: str, action: str, qty: int, price: float | None = None, order_ref: str = REF):
    from ib_async import MarketOrder, StopOrder
    o = MarketOrder(action, qty) if kind == "MKT" else StopOrder(action, qty, price)
    o.orderRef, o.tif, o.outsideRth = order_ref, "DAY", False
    return o


def _wait(ib, trade, seconds: float = FILL_WAIT) -> None:
    waited = 0.0
    while not trade.isDone() and waited < seconds:
        ib.sleep(1)
        waited += 1


def _messages(trade) -> str:
    return "; ".join(e.message for e in getattr(trade, "log", []) if getattr(e, "message", ""))[:500]


def place(ib, conn: sqlite3.Connection, day: date, rows: list[dict], sizes=SIZES, make_contract=_stock,
          make_order=_order) -> list[dict]:
    """Short each taken row's share count for each size, then its 50% stop. Records every outcome; returns them."""
    account = ib.managedAccounts()[0]
    out = []
    for r in rows:
        if r.get("status") != "signal":
            continue
        sym = r["symbol"]
        contract = None
        for size in sizes:
            qty = int(r.get(QTY_FIELD[size]) or 0)
            if qty <= 0 or conn.execute("SELECT 1 FROM ibkr_orders WHERE day=? AND symbol=? AND size=?",
                                        (day.isoformat(), sym, size)).fetchone():
                continue
            rec = {"day": day.isoformat(), "symbol": sym, "size": size, "qty": qty, "account": account,
                   "entry_fill": None, "filled": 0, "stop_price": None, "stop_order_id": None, "message": ""}
            contract = contract or make_contract(ib, sym)
            if contract is None:
                rec.update(status="no contract", message="IBKR could not find the stock")
            else:
                trade = ib.placeOrder(contract, make_order("MKT", "SELL", qty, None, ref(size)))
                _wait(ib, trade)
                st = trade.orderStatus
                rec.update(filled=int(st.filled or 0), message=_messages(trade))
                if rec["filled"] > 0:
                    rec["entry_fill"] = float(st.avgFillPrice)
                    rec["stop_price"] = stop_price(rec["entry_fill"])
                    stop = ib.placeOrder(contract, make_order("STP", "BUY", rec["filled"], rec["stop_price"], ref(size)))
                    rec["stop_order_id"] = stop.order.orderId
                    rec["status"] = "filled" if rec["filled"] == qty else "partial"
                else:
                    rec["status"] = st.status.lower() if st.status else "no fill"
                if not trade.isDone():
                    ib.cancelOrder(trade.order)                 # don't leave the rest working
            rec["entry_at"] = datetime.now(ET).isoformat(timespec="seconds")
            conn.execute("INSERT OR REPLACE INTO ibkr_orders (day, symbol, size, qty, status, message, entry_fill, "
                         "filled, entry_at, stop_price, stop_order_id, account) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                         (rec["day"], sym, size, qty, rec["status"], rec["message"], rec["entry_fill"], rec["filled"],
                          rec["entry_at"], rec["stop_price"], rec["stop_order_id"], account))
            conn.commit()
            out.append(rec)
    return out


def _bought(ib, symbols: set[str]) -> dict[tuple[str, str], list[tuple[float, float]]]:
    """(symbol, orderRef) -> [(shares, price)] of today's BUY executions tagged by this module."""
    out: dict[tuple[str, str], list[tuple[float, float]]] = {}
    for f in ib.reqExecutions():
        e = f.execution
        if e.side == "BOT" and str(e.orderRef).startswith(REF) and f.contract.symbol in symbols:
            out.setdefault((f.contract.symbol, e.orderRef), []).append((float(e.shares), float(e.price)))
    return out


def cover(ib, conn: sqlite3.Connection, day: date, make_contract=_stock, make_order=_order) -> list[dict]:
    """Cancel the day's stops, buy back what each size still has short, then record each exit from executions."""
    open_rows = conn.execute("SELECT symbol, size, filled FROM ibkr_orders WHERE day=? AND filled > 0 "
                             "AND exit_fill IS NULL", (day.isoformat(),)).fetchall()
    if not open_rows:
        return []
    symbols = {s for s, _, _ in open_rows}
    for t in ib.openTrades():
        if str(t.order.orderRef).startswith(REF) and t.contract.symbol in symbols and t.order.action == "BUY":
            ib.cancelOrder(t.order)
    ib.sleep(2)
    short = {p.contract.symbol: -int(p.position) for p in ib.positions() if p.position < 0}
    done = _bought(ib, symbols)
    covers, covered = [], set()
    for sym, size, filled in open_rows:
        left = min(int(filled) - int(sum(q for q, _ in done.get((sym, ref(size)), []))), short.get(sym, 0))
        if left > 0:
            contract = make_contract(ib, sym)
            if contract is not None:
                covers.append(ib.placeOrder(contract, make_order("MKT", "BUY", left, None, ref(size))))
                short[sym] -= left
                covered.add((sym, size))
    for t in covers:
        _wait(ib, t)
    buys = _bought(ib, symbols)
    out = []
    at = datetime.now(ET).isoformat(timespec="seconds")
    for sym, size, _ in open_rows:
        b = buys.get((sym, ref(size)))
        if not b:
            continue
        qty = sum(q for q, _ in b)
        px = sum(q * p for q, p in b) / qty
        how = "cover" if (sym, size) in covered else "stop"
        conn.execute("UPDATE ibkr_orders SET exit_fill=?, exit_how=?, exit_at=? WHERE day=? AND symbol=? AND size=?",
                     (px, how, at, day.isoformat(), sym, size))
        out.append({"symbol": sym, "size": size, "exit_fill": px, "exit_how": how})
    conn.commit()
    return out


LABEL = {"27": "2.7%", "40": "40%"}


def message(placed: list[dict]) -> str:
    lines = []
    for r in placed:
        if r["filled"]:
            lines.append(f"{r['symbol']} {LABEL[r['size']]}: shorted {r['filled']:,} at ${r['entry_fill']:.4g}, "
                         f"stop ${r['stop_price']:.4g}")
        else:
            lines.append(f"{r['symbol']} {LABEL[r['size']]}: NOT shorted ({r['status']}) {r['message'][:120]}")
    return "IBKR paper: " + "; ".join(lines) if lines else "IBKR paper: nothing to place"
