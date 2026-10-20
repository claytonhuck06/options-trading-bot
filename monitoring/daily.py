"""The after-close daily summary and the missed-day check.

One push notification per trading day: the day's P&L, trades opened and closed, what is held
overnight, any kill-switch events, and whether the collector and trader actually ran. Problems
(a day with no data, a trader that never started) raise the notification's priority.

A missed day is a trading day on the exchange calendar with no collector data, which is what
happens when the Mac was asleep, off, or the job failed.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from datetime import date


@dataclass
class DailySummary:
    day: date
    lines: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def title(self) -> str:
        return f"Bot {self.day:%a %b %d}: " + ("NEEDS ATTENTION" if self.problems else "OK")

    def text(self) -> str:
        return "\n".join([f"! {p}" for p in self.problems] + self.lines)


def _rows(conn: sqlite3.Connection, sql: str, args=()) -> list[tuple]:
    try:
        return conn.execute(sql, args).fetchall()
    except sqlite3.OperationalError:          # table not created yet (e.g. the trader never ran)
        return []


def missed_days(conn: sqlite3.Connection, trading_days: list[date]) -> list[date]:
    """Trading days with no collector data at all."""
    have = {r[0] for r in _rows(conn, "SELECT DISTINCT substr(started_at, 1, 10) FROM collection_cycles WHERE status='ok'")}
    if not have:
        return []
    first = min(have)                          # days before the collector was ever set up are not "missed"
    return [d for d in trading_days if d.isoformat() >= first and d.isoformat() not in have]


def build_summary(conn: sqlite3.Connection, day: date, equity_now: float | None, recent_trading_days: list[date],
                  is_trading_day: bool = True) -> DailySummary:
    s = DailySummary(day)
    iso = day.isoformat()

    # Account
    snap = _rows(conn, "SELECT start_equity FROM risk_snapshots WHERE trading_date=?", (iso,))
    if equity_now is not None and snap:
        pnl = equity_now - snap[0][0]
        s.lines.append(f"P&L today ${pnl:+,.0f} (equity ${equity_now:,.0f})")
    elif equity_now is not None:
        s.lines.append(f"Equity ${equity_now:,.0f}")

    # Trades
    opened = _rows(conn, "SELECT position_id FROM positions_book WHERE substr(opened_at, 1, 10)=?", (iso,))
    closed = _rows(conn, "SELECT position_id, exit_reason, realized_pnl FROM positions_book WHERE status='closed' "
                         "AND substr(closed_at, 1, 10)=?", (iso,))
    held = _rows(conn, "SELECT position_id, legs FROM positions_book WHERE status='open'")
    s.lines.append(f"Trades: {len(opened)} opened, {len(closed)} closed")
    for pid, reason, pnl in closed:
        s.lines.append(f"  closed {pid}: {reason} ${pnl or 0:+,.0f}")
    if closed:
        s.lines.append(f"  realized total ${sum(r[2] or 0 for r in closed):+,.0f}")
    if held:
        s.lines.append(f"Held overnight: {', '.join(r[0] for r in held)}")

    # Kill switch
    events = _rows(conn, "SELECT level, trigger FROM kill_switch_events WHERE substr(ts, 1, 10)=?", (iso,))
    for level, trigger in events:
        line = f"Kill switch {level}: {trigger.replace('_', ' ')}"
        (s.problems if level in ("HALTED", "FLATTEN_TODAY") else s.lines).append(line)
    for (detail,) in _rows(conn, "SELECT detail FROM execution_events WHERE kind='early_assignment' AND substr(ts, 1, 10)=?", (iso,)):
        s.problems.append("Early assignment happened today (see execution_events)")

    # Did the jobs run?
    if is_trading_day:
        ok, failed = (_rows(conn, "SELECT COUNT(*) FILTER (WHERE status='ok'), COUNT(*) FILTER (WHERE status='failed') "
                                  "FROM collection_cycles WHERE substr(started_at, 1, 10)=?", (iso,)) or [(0, 0)])[0]
        if not ok:
            s.problems.append("Collector did not run today (Mac asleep/off, or the job failed)")
        else:
            s.lines.append(f"Collector: {ok} cycles" + (f", {failed} failed" if failed else ""))
        if not snap:
            s.problems.append("Trader did not run today")
    missed = [d for d in missed_days(conn, recent_trading_days) if d != day]
    if missed:
        s.problems.append("Missed trading days: " + ", ".join(f"{d:%a %b %d}" for d in missed))
    return s
