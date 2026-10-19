"""Persistent account-risk state: daily equity snapshot, high-water mark,
kill-switch level and processed request ids. Lives in the main SQLite store so
it survives restarts.

Daily limits use the start-of-day equity snapshot. Drawdown uses the
high-water mark, which moves with deposits and withdrawals so cash flows are
not mistaken for profits or losses.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime
from enum import IntEnum

from data.storage.db import MarketDataStore, _iso, utcnow

SCHEMA = """
CREATE TABLE IF NOT EXISTS risk_snapshots (
    trading_date TEXT PRIMARY KEY,
    start_equity REAL NOT NULL,
    high_water_mark REAL NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS risk_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS kill_switch_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts TEXT NOT NULL,
    level TEXT NOT NULL,
    trigger TEXT NOT NULL,
    detail TEXT
);
CREATE TABLE IF NOT EXISTS risk_decisions (
    request_id TEXT PRIMARY KEY,      -- each trade request is decided once; repeats are refused
    decided_at TEXT NOT NULL,
    approved INTEGER NOT NULL,
    contracts INTEGER NOT NULL,
    reasons TEXT NOT NULL,
    valid_until TEXT
);
"""


class Level(IntEnum):
    NORMAL = 0
    NO_NEW_ENTRIES = 1      # drawdown warning: hold and manage existing positions only
    FLATTEN_TODAY = 2       # daily loss or profit ceiling: close everything, resume next trading day
    HALTED = 3              # hard stop: close everything; restart needs manual authorization


@dataclass(frozen=True)
class DaySnapshot:
    trading_date: date
    start_equity: float
    high_water_mark: float


class RiskStateStore:
    def __init__(self, store: MarketDataStore):
        self.conn = store.conn
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # --- key/value -------------------------------------------------------
    def _get(self, key: str, default=None):
        row = self.conn.execute("SELECT value FROM risk_state WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def _set(self, key: str, value) -> None:
        self.conn.execute("INSERT OR REPLACE INTO risk_state VALUES (?,?,?)", (key, json.dumps(value, default=str), _iso(utcnow())))
        self.conn.commit()

    # --- equity snapshots --------------------------------------------------
    def start_day(self, trading_date: date, equity: float) -> DaySnapshot:
        """Take (or return the existing) start-of-day snapshot. The high-water mark only ever rises
        with equity, except when adjusted for withdrawals."""
        row = self.conn.execute("SELECT start_equity, high_water_mark FROM risk_snapshots WHERE trading_date=?",
                                (trading_date.isoformat(),)).fetchone()
        if row:
            return DaySnapshot(trading_date, row[0], row[1])
        hwm = max(self.high_water_mark() or equity, equity)
        self.conn.execute("INSERT INTO risk_snapshots VALUES (?,?,?,?)", (trading_date.isoformat(), equity, hwm, _iso(utcnow())))
        self._set("high_water_mark", hwm)
        return DaySnapshot(trading_date, equity, hwm)

    def high_water_mark(self) -> float | None:
        return self._get("high_water_mark")

    def update_high_water_mark(self, equity: float) -> float:
        hwm = max(self.high_water_mark() or equity, equity)
        self._set("high_water_mark", hwm)
        return hwm

    def record_cash_flow(self, amount: float) -> float:
        """Deposits (+) raise and withdrawals (-) lower the high-water mark by the same amount."""
        hwm = (self.high_water_mark() or 0.0) + amount
        self._set("high_water_mark", hwm)
        return hwm

    # --- kill switch -------------------------------------------------------
    def level(self, today: date) -> Level:
        state = self._get("kill_switch", {"level": 0})
        lvl = Level(state["level"])
        # Day-scoped stops expire at the next trading day; HALTED never expires on its own.
        if lvl == Level.FLATTEN_TODAY and state.get("date") != today.isoformat():
            return Level.NORMAL
        return lvl

    def escalate(self, level: Level, trigger: str, today: date, detail: str | None = None) -> Level:
        current = self.level(today)
        if level <= current:
            return current
        self._set("kill_switch", {"level": int(level), "date": today.isoformat(), "trigger": trigger})
        self.conn.execute("INSERT INTO kill_switch_events(ts, level, trigger, detail) VALUES (?,?,?,?)",
                          (_iso(utcnow()), level.name, trigger, detail))
        self.conn.commit()
        return level

    def acknowledge_warning(self, today: date) -> None:
        """A person has reviewed the drawdown and allows new entries for the rest of this trading day.
        While the account stays below the warning line, this review is needed again each day."""
        self._set("warning_ack_date", today.isoformat())
        if self.level(today) == Level.NO_NEW_ENTRIES:
            self._set("kill_switch", {"level": 0, "date": today.isoformat(), "trigger": "warning_acknowledged"})
        self.conn.execute("INSERT INTO kill_switch_events(ts, level, trigger, detail) VALUES (?,?,?,?)",
                          (_iso(utcnow()), "NORMAL", "warning_acknowledged", None))
        self.conn.commit()

    def warning_acknowledged(self, today: date) -> bool:
        return self._get("warning_ack_date") == today.isoformat()

    def relax_warning(self, today: date) -> None:
        """NO_NEW_ENTRIES clears automatically once its condition clears."""
        if self.level(today) == Level.NO_NEW_ENTRIES:
            self._set("kill_switch", {"level": 0, "date": today.isoformat(), "trigger": "warning_cleared"})

    def manual_reset(self, authorization: str, today: date) -> None:
        if authorization != RESET_PHRASE:
            raise PermissionError("kill switch reset requires the exact authorization phrase")
        self._set("kill_switch", {"level": 0, "date": today.isoformat(), "trigger": "manual_reset"})
        self.conn.execute("INSERT INTO kill_switch_events(ts, level, trigger, detail) VALUES (?,?,?,?)",
                          (_iso(utcnow()), "NORMAL", "manual_reset", None))
        self.conn.commit()

    def last_trigger(self) -> str | None:
        return self._get("kill_switch", {}).get("trigger")

    # --- request de-duplication ---------------------------------------------
    def already_decided(self, request_id: str) -> bool:
        return self.conn.execute("SELECT 1 FROM risk_decisions WHERE request_id=?", (request_id,)).fetchone() is not None

    def record_decision(self, request_id: str, approved: bool, contracts: int, reasons: list[str], valid_until: datetime | None) -> None:
        self.conn.execute("INSERT INTO risk_decisions VALUES (?,?,?,?,?,?)",
                          (request_id, _iso(utcnow()), int(approved), contracts, json.dumps(reasons), _iso(valid_until)))
        self.conn.commit()


RESET_PHRASE = "I HAVE REVIEWED THE HALT AND AUTHORIZE RESTART"
