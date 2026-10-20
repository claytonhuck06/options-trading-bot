"""The after-close daily summary and the missed-day check.

One push notification per trading day: the day's P&L, trades opened and closed, what is held
overnight, what the screener looked at and why nothing passed, any kill-switch events, and whether
the collector and trader actually ran. A shorter midday check-in says whether the jobs are alive. Problems
(a day with no data, a trader that never started) raise the notification's priority.

A missed day is a trading day on the exchange calendar with no collector data, which is what
happens when the Mac was asleep, off, or the job failed.
"""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

# Screener rejection codes (strategies/relative_value.py) in words for the phone.
REASON_WORDS = {
    "outside_entry_window": "outside trading hours for entries",
    "expiry_too_close": "expiry too close",
    "stale_quote": "stale quote",
    "thin_quote": "quote too thin",
    "wide_leg_spread": "bid/ask too wide",
    "max_loss_not_positive": "quotes inconsistent",
    "too_good_to_be_true": "too good to be true (likely a bad quote)",
    "too_large_for_risk_budget": "too big for the risk budget",
    "reward_too_small": "reward too small",
    "no_edge_at_executable_prices": "no edge at real prices",
    "edge_within_model_uncertainty": "edge within model error",
    "costs_exceed_expected_edge": "costs bigger than the edge",
    "target_unlikely_in_holding_period": "profit target unlikely",
}


@dataclass
class DailySummary:
    day: date
    lines: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    label: str = ""                            # e.g. "midday"

    @property
    def title(self) -> str:
        return f"Bot {self.label + ' ' if self.label else ''}{self.day:%a %b %d}: " + ("NEEDS ATTENTION" if self.problems else "OK")

    def text(self) -> str:
        return "\n".join([f"! {p}" for p in self.problems] + self.lines)


def _rows(conn: sqlite3.Connection, sql: str, args=()) -> list[tuple]:
    try:
        return conn.execute(sql, args).fetchall()
    except sqlite3.OperationalError:          # table not created yet (e.g. the trader never ran)
        return []


def screener_lines(conn: sqlite3.Connection, day: date, top: int = 3) -> list[str]:
    """What the screener built today, how many passed every rule, and the commonest reasons the rest failed."""
    iso = day.isoformat()
    rows = _rows(conn, "SELECT run_id, eligible, reasons FROM candidate_trades WHERE substr(as_of, 1, 10)=?", (iso,))
    if not rows:
        return ["Screener: no spreads built today"]
    passed = sum(1 for r in rows if r[1])
    reasons: Counter[str] = Counter()
    for _, eligible, text in rows:
        if not eligible:
            try:
                reasons.update(json.loads(text or "[]"))
            except ValueError:
                pass
    lines = [f"Screener: {len(rows):,} spreads checked in {len({r[0] for r in rows})} scans, {passed} passed every rule"]
    if reasons:
        lines.append("  top reasons: " + "; ".join(f"{REASON_WORDS.get(k, k.replace('_', ' '))} ({n:,})"
                                                   for k, n in reasons.most_common(top)))
    if not passed:
        days = _rows(conn, "SELECT substr(as_of, 1, 10) d, MAX(eligible) FROM candidate_trades WHERE substr(as_of, 1, 10)<=? "
                           "GROUP BY d ORDER BY d DESC", (iso,))
        streak = 0
        for _, any_passed in days:
            if any_passed:
                break
            streak += 1
        if streak > 1:
            lines.append(f"  {streak} screener days in a row with nothing passing")
    return lines


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

    s.lines += screener_lines(conn, day)

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


def build_midday(conn: sqlite3.Connection, day: date, collector_running: bool, trader_running: bool,
                 now: datetime | None = None) -> DailySummary:
    """Midday check-in: are the jobs alive and collecting, what has the screener seen, any trades so far."""
    now = now or datetime.now(timezone.utc)
    s = DailySummary(day, label="midday")
    iso = day.isoformat()
    last = (_rows(conn, "SELECT MAX(started_at) FROM collection_cycles WHERE status='ok' AND substr(started_at, 1, 10)=?",
                  (iso,)) or [(None,)])[0][0]
    fresh = last is not None and now - datetime.fromisoformat(last) < timedelta(minutes=10)
    if not fresh:                              # fresh data is proof enough that the collector is alive
        s.problems.append("Collector is running but has no fresh data in 10+ minutes (network?)" if collector_running
                          else "Collector is not running (no data in 10+ minutes)")
    if not trader_running:
        s.problems.append("Trader is not running")
    if not s.problems:
        s.lines.append("Collector and trader running; data is fresh")
    opened = _rows(conn, "SELECT position_id FROM positions_book WHERE substr(opened_at, 1, 10)=? "
                         "AND position_id NOT LIKE 'demo-%'", (iso,))               # the morning order test is not a trade
    held = _rows(conn, "SELECT position_id FROM positions_book WHERE status='open'")
    s.lines.append(f"Trades so far: {len(opened)} opened" + (f", holding {len(held)}" if held else ""))
    s.lines += screener_lines(conn, day)
    return s
