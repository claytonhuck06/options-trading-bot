"""Local record of every order, fill and position the system has handled.

Every order row is written BEFORE it is sent, so after a crash the system
knows which client order ids it may have sent and asks the broker about them
instead of sending again. The broker's records remain the source of truth.
"""

from __future__ import annotations

import json
from datetime import date, datetime

from data.storage.db import MarketDataStore, _iso, utcnow
from execution.broker_api import BrokerOrder, OrderLeg, OrderStatus

SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
    client_order_id TEXT PRIMARY KEY,
    broker_id TEXT,
    plan_key TEXT NOT NULL,           -- risk request id (entries) or position id + reason (exits)
    position_id TEXT NOT NULL,
    intent TEXT NOT NULL,             -- open | close
    legs TEXT NOT NULL,               -- JSON
    qty INTEGER NOT NULL,
    limit_price REAL NOT NULL,        -- signed: + debit, - credit
    mid_price REAL,                   -- signed net mid when sent (for cost tracking)
    status TEXT NOT NULL,             -- pending_submit | working | partial | filled | canceled | rejected | unknown | submit_failed
    filled_qty INTEGER NOT NULL DEFAULT 0,
    net_fill_price REAL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    note TEXT
);
CREATE INDEX IF NOT EXISTS ix_orders_plan ON orders(plan_key);

CREATE TABLE IF NOT EXISTS fills (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    client_order_id TEXT NOT NULL REFERENCES orders(client_order_id),
    recorded_at TEXT NOT NULL,
    qty INTEGER NOT NULL,
    net_price REAL NOT NULL,
    leg_prices TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS positions_book (
    position_id TEXT PRIMARY KEY,
    underlying TEXT NOT NULL,
    status TEXT NOT NULL,             -- open | closed
    legs TEXT NOT NULL,               -- JSON of risk.positions.OptionLeg (signed quantities, actual fill prices)
    max_loss REAL NOT NULL, profit_target REAL NOT NULL, stop_loss REAL NOT NULL,
    anchor_symbol TEXT,
    entry_fees REAL NOT NULL, exit_fees REAL NOT NULL,
    opened_at TEXT NOT NULL,
    closed_at TEXT,
    exit_reason TEXT,
    realized_pnl REAL,
    hold_overnight INTEGER NOT NULL DEFAULT 0,
    strategy TEXT NOT NULL DEFAULT 'mispricing'
);

CREATE TABLE IF NOT EXISTS execution_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    kind TEXT NOT NULL,
    detail TEXT
);
"""


class OrderStore:
    def __init__(self, store: MarketDataStore):
        self.conn = store.conn
        self.conn.executescript(SCHEMA)
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(positions_book)")}
        if "hold_overnight" not in cols:          # databases created before multi-day support
            self.conn.execute("ALTER TABLE positions_book ADD COLUMN hold_overnight INTEGER NOT NULL DEFAULT 0")
        if "strategy" not in cols:                # databases created before the weekly call spread
            self.conn.execute("ALTER TABLE positions_book ADD COLUMN strategy TEXT NOT NULL DEFAULT 'mispricing'")
        self.conn.commit()

    def event(self, kind: str, detail: dict | None = None) -> None:
        self.conn.execute("INSERT INTO execution_events(ts, kind, detail) VALUES (?,?,?)",
                          (_iso(utcnow()), kind, json.dumps(detail, default=str) if detail else None))
        self.conn.commit()

    # --- orders ------------------------------------------------------------------
    def create(self, client_order_id: str, plan_key: str, position_id: str, intent: str, legs: list[OrderLeg],
               qty: int, limit_price: float, mid_price: float | None) -> None:
        now = _iso(utcnow())
        self.conn.execute(
            "INSERT INTO orders(client_order_id, plan_key, position_id, intent, legs, qty, limit_price, mid_price, status, "
            "created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (client_order_id, plan_key, position_id, intent, json.dumps([l.__dict__ for l in legs]), qty, limit_price,
             mid_price, "pending_submit", now, now))
        self.conn.commit()

    def update(self, client_order_id: str, status: str, broker: BrokerOrder | None = None,
               net_fill_price: float | None = None, note: str | None = None) -> None:
        self.conn.execute(
            "UPDATE orders SET status=?, broker_id=COALESCE(?, broker_id), filled_qty=COALESCE(?, filled_qty), "
            "net_fill_price=COALESCE(?, net_fill_price), note=COALESCE(?, note), updated_at=? WHERE client_order_id=?",
            (status, broker.broker_id if broker and broker.broker_id else None, broker.filled_qty if broker else None,
             net_fill_price, note, _iso(utcnow()), client_order_id))
        self.conn.commit()

    def record_fill(self, client_order_id: str, qty: int, net_price: float, leg_prices: dict[str, float]) -> None:
        self.conn.execute("INSERT INTO fills(client_order_id, recorded_at, qty, net_price, leg_prices) VALUES (?,?,?,?,?)",
                          (client_order_id, _iso(utcnow()), qty, net_price, json.dumps(leg_prices)))
        self.conn.commit()

    def orders_for_plan(self, plan_key: str) -> list[dict]:
        return [dict(r) for r in self.conn.execute("SELECT * FROM orders WHERE plan_key=? ORDER BY created_at", (plan_key,))]

    def has_live_orders(self, position_id: str) -> bool:
        """True if any order for this position might still be working at the broker."""
        return self.conn.execute(
            "SELECT 1 FROM orders WHERE position_id=? AND status IN ('pending_submit','working','partial','unknown') LIMIT 1",
            (position_id,)).fetchone() is not None

    def plan_count(self, position_id: str, intent: str) -> int:
        return self.conn.execute("SELECT COUNT(DISTINCT plan_key) FROM orders WHERE position_id=? AND intent=?",
                                 (position_id, intent)).fetchone()[0]

    def unresolved(self) -> list[dict]:
        """Orders whose outcome we do not know for sure (crash recovery checks these with the broker)."""
        return [dict(r) for r in self.conn.execute(
            "SELECT * FROM orders WHERE status IN ('pending_submit','working','partial','unknown')")]

    def todays_costs(self, day: date) -> float:
        """$ paid versus mid on filled orders today (spread cost)."""
        row = self.conn.execute(
            "SELECT COALESCE(SUM(ABS(net_fill_price - mid_price) * filled_qty * 100), 0) FROM orders "
            "WHERE filled_qty > 0 AND mid_price IS NOT NULL AND substr(created_at, 1, 10) = ?", (day.isoformat(),)).fetchone()
        return float(row[0])

    # --- positions -----------------------------------------------------------------
    def open_position(self, pos) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO positions_book(position_id, underlying, status, legs, max_loss, profit_target, stop_loss, "
            "anchor_symbol, entry_fees, exit_fees, opened_at, hold_overnight, strategy) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (pos.position_id, pos.underlying, "open", _legs_json(pos.legs), pos.max_loss, pos.profit_target, pos.stop_loss,
             pos.anchor_symbol, pos.entry_fees, pos.exit_fees, _iso(pos.opened_at), int(pos.hold_overnight), pos.strategy))
        self.conn.commit()

    def update_position_legs(self, pos) -> None:
        self.conn.execute("UPDATE positions_book SET legs=?, max_loss=?, profit_target=?, stop_loss=?, entry_fees=?, exit_fees=? "
                          "WHERE position_id=?",
                          (_legs_json(pos.legs), pos.max_loss, pos.profit_target, pos.stop_loss, pos.entry_fees, pos.exit_fees,
                           pos.position_id))
        self.conn.commit()

    def mark_position_closed(self, position_id: str, reason: str, realized_pnl: float) -> None:
        self.conn.execute("UPDATE positions_book SET status='closed', closed_at=?, exit_reason=?, realized_pnl=? WHERE position_id=?",
                          (_iso(utcnow()), reason, realized_pnl, position_id))
        self.conn.commit()

    def open_positions(self):
        from risk.positions import Position
        out = []
        for r in self.conn.execute("SELECT * FROM positions_book WHERE status='open'"):
            out.append(Position(r["position_id"], r["underlying"], _legs_from_json(r["legs"]), datetime.fromisoformat(r["opened_at"]),
                                r["max_loss"], r["profit_target"], r["stop_loss"], r["anchor_symbol"], r["entry_fees"], r["exit_fees"],
                                bool(r["hold_overnight"]), r["strategy"]))
        return out

    def strategy_opened_since(self, strategy: str, since: datetime) -> int:
        """Positions of a strategy opened at or after `since` (open or closed): the weekly spread's once-a-week rule."""
        return self.conn.execute("SELECT COUNT(*) FROM positions_book WHERE strategy=? AND opened_at>=?",
                                 (strategy, _iso(since))).fetchone()[0]


def _legs_json(legs) -> str:
    return json.dumps([{**l.__dict__, "expiration": l.expiration.isoformat()} for l in legs])


def _legs_from_json(text: str):
    from risk.positions import OptionLeg
    return [OptionLeg(**{**d, "expiration": date.fromisoformat(d["expiration"])}) for d in json.loads(text)]
