"""IBKR paper orders: paper guard, stop rounding, place/cover bookkeeping (fake IB, no network)."""

import sqlite3
from datetime import date
from types import SimpleNamespace as NS

import pytest

from execution import ibkr_paper as ip


class FakeTrade:
    def __init__(self, order, filled, price, status="Filled", msg=""):
        self.order, self.contract = order, None
        self.orderStatus = NS(filled=filled, avgFillPrice=price, status=status)
        self.log = [NS(message=msg)] if msg else []

    def isDone(self):
        return True


class FakeIB:
    def __init__(self, accounts=("DU123",), fills=None, positions=(), executions=()):
        self.accounts, self.fills, self.placed, self.cancelled = list(accounts), fills or {}, [], []
        self._positions, self._exec, self._open, self.n = list(positions), list(executions), [], 0

    def connect(self, *a, **k): pass
    def disconnect(self): self.disconnected = True
    def managedAccounts(self): return self.accounts
    def sleep(self, s): pass
    def openTrades(self): return self._open
    def positions(self): return self._positions
    def reqExecutions(self): return self._exec
    def cancelOrder(self, o): self.cancelled.append(o)

    def placeOrder(self, contract, order):
        self.n += 1
        order.orderId = self.n
        self.placed.append((contract, order))
        if order.kind == "MKT" and order.action == "SELL":
            filled, px, st, msg = self.fills.get(contract, (order.qty, 2.0, "Filled", ""))
            return FakeTrade(order, filled, px, st, msg)
        return FakeTrade(order, 0, 0, "Submitted")


def order(kind, action, qty, price=None):
    return NS(kind=kind, action=action, qty=qty, price=price, orderRef=ip.REF, orderId=None)


def contract(ib, sym):
    return sym


def db():
    return ip.open_db(sqlite3.connect(":memory:"))


def test_guard_refuses_live_port_and_live_account():
    with pytest.raises(ip.PaperGuardError):
        ip.connect(ip.Config(port=7496), FakeIB())
    ib = FakeIB(accounts=("U999",))
    with pytest.raises(ip.PaperGuardError):
        ip.connect(ip.Config(port=4002), ib)
    assert ib.disconnected
    assert ip.connect(ip.Config(port=4002), FakeIB()).accounts == ["DU123"]


def test_stop_price_rounds_up_to_tick():
    assert ip.stop_price(2.0) == 3.0
    assert ip.stop_price(2.013) == 3.02
    assert ip.stop_price(0.5123) == 0.7685


def test_place_shorts_then_stops_and_records_rejections():
    ib = FakeIB(fills={"BBB": (0, 0, "Cancelled", "No shares available to borrow")})
    conn = db()
    rows = [{"symbol": "AAA", "status": "signal", "shares_needed": 900},
            {"symbol": "BBB", "status": "signal", "shares_needed": 500},
            {"symbol": "CCC", "status": "over_cap", "shares_needed": 100}]
    out = ip.place(ib, conn, date(2026, 10, 22), rows, contract, order)
    assert [r["symbol"] for r in out] == ["AAA", "BBB"]
    kinds = [(c, o.kind, o.action, o.qty, o.price) for c, o in ib.placed]
    assert kinds == [("AAA", "MKT", "SELL", 900, None), ("AAA", "STP", "BUY", 900, 3.0), ("BBB", "MKT", "SELL", 500, None)]
    b = conn.execute("SELECT status, message, filled FROM ibkr_orders WHERE symbol='BBB'").fetchone()
    assert b == ("cancelled", "No shares available to borrow", 0)
    assert ip.place(ib, conn, date(2026, 10, 22), rows, contract, order) == []      # never twice


def test_cover_cancels_stops_buys_back_and_records_exits():
    conn = db()
    day = date(2026, 10, 22)
    ip.place(FakeIB(), conn, day, [{"symbol": "AAA", "status": "signal", "shares_needed": 900},
                                   {"symbol": "SSS", "status": "signal", "shares_needed": 100}], contract, order)
    stop = NS(order=NS(orderRef=ip.REF, action="BUY"), contract=NS(symbol="AAA"))
    ex = [NS(contract=NS(symbol="AAA"), execution=NS(side="BOT", orderRef=ip.REF, shares=900, price=1.5)),
          NS(contract=NS(symbol="SSS"), execution=NS(side="BOT", orderRef=ip.REF, shares=100, price=3.0))]
    ib = FakeIB(positions=[NS(contract=NS(symbol="AAA"), position=-900)], executions=ex)   # SSS was stopped
    ib._open = [stop]
    out = ip.cover(ib, conn, day, contract, order)
    assert ib.cancelled == [stop.order]
    assert [(c, o.action, o.qty) for c, o in ib.placed] == [("AAA", "BUY", 900)]
    got = dict(conn.execute("SELECT symbol, exit_how FROM ibkr_orders").fetchall())
    assert got == {"AAA": "cover", "SSS": "stop"} and len(out) == 2
