"""Phone alerts, the daily summary, missed-day detection and live-data storage cleanup."""

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone

import pytest

from core.config import load_settings
from data.models import OptionQuote, QualityResult, UnderlyingQuote
from data.storage.db import MarketDataStore
from data.storage.retention import archive_day, data_days, restore_day, run_retention, thin_day
from monitoring import alerts
from monitoring.daily import build_midday, build_summary, missed_days, screener_lines


# --- alerts ----------------------------------------------------------------------------

def test_alert_goes_to_the_configured_topic(alerts_sent):
    assert alerts.urgent("Trading HALTED", "manual stop")
    assert alerts_sent == [{"url": "https://ntfy.sh/test-topic", "title": "Trading HALTED", "priority": "5", "message": "manual stop"}]


def test_no_topic_means_nothing_is_sent(monkeypatch, alerts_sent):
    monkeypatch.delenv(alerts.TOPIC_VAR)
    monkeypatch.setattr(alerts, "_read_dotenv", lambda path: {})
    assert not alerts.send("x", "y") and alerts_sent == []


def test_failed_alert_is_saved_not_raised(monkeypatch, tmp_path):
    def down(*a):
        raise OSError("no network")
    monkeypatch.setattr(alerts, "_post", down)
    monkeypatch.setattr(alerts, "UNSENT", tmp_path / "unsent.jsonl")
    assert alerts.send("Kill switch", "daily loss limit") is False
    saved = json.loads((tmp_path / "unsent.jsonl").read_text())
    assert saved["title"] == "Kill switch" and saved["error"] == "OSError"


def test_alert_text_is_scrubbed_of_secrets(alerts_sent):
    alerts.send("t", "key PKABCDEFGHIJKLMNOPQR leaked")
    assert "PKABCDEFGHIJKLMNOPQR" not in alerts_sent[0]["message"]


# --- daily summary -------------------------------------------------------------------------

DAY = date(2026, 10, 21)


def _store_with(days_collected, trader_ran=True, closed_pnl=None):
    from execution.order_store import OrderStore
    from risk.state import RiskStateStore
    store = MarketDataStore(":memory:")
    book, state = OrderStore(store), RiskStateStore(store)
    for d in days_collected:
        store.conn.execute("INSERT INTO collection_cycles(underlying, feed, started_at, status) VALUES ('SPY','x',?, 'ok')",
                           (f"{d.isoformat()}T15:00:00+00:00",))
    if trader_ran:
        state.start_day(DAY, 100_000)
    if closed_pnl is not None:
        store.conn.execute("INSERT INTO positions_book(position_id, underlying, status, legs, max_loss, profit_target, stop_loss, "
                           "entry_fees, exit_fees, opened_at, closed_at, exit_reason, realized_pnl) "
                           "VALUES ('p1','SPY','closed','[]',100,20,50,0,0,?,?, 'profit_target', ?)",
                           (f"{DAY}T15:00:00+00:00", f"{DAY}T17:00:00+00:00", closed_pnl))
    store.conn.commit()
    return store


def test_quiet_day_summary():
    store = _store_with([date(2026, 10, 19), date(2026, 10, 20), DAY], closed_pnl=42.0)
    s = build_summary(store.conn, DAY, 100_250.0, [date(2026, 10, 19), date(2026, 10, 20), DAY])
    assert s.problems == [] and s.title.endswith("OK")
    text = s.text()
    assert "P&L today $+250" in text and "1 opened, 1 closed" in text and "profit_target $+42" in text


def test_summary_flags_missing_collector_and_trader():
    store = _store_with([date(2026, 10, 19)], trader_ran=False)
    s = build_summary(store.conn, DAY, None, [date(2026, 10, 19), date(2026, 10, 20), DAY])
    assert any("Collector did not run" in p for p in s.problems)
    assert any("Trader did not run" in p for p in s.problems)
    assert any("Missed trading days: Tue Oct 20" in p for p in s.problems)
    assert "NEEDS ATTENTION" in s.title


def test_days_before_the_collector_existed_are_not_missed():
    store = _store_with([date(2026, 10, 20)])
    assert missed_days(store.conn, [date(2026, 10, 16), date(2026, 10, 19), date(2026, 10, 20), DAY]) == [DAY]


def _candidates(store, day, rows):
    """rows: (run_id, eligible, reasons list)"""
    store.conn.execute("PRAGMA foreign_keys=OFF")              # no pricing_runs needed for these checks
    for run, ok, reasons in rows:
        store.conn.execute("INSERT INTO candidate_trades(run_id, as_of, structure, direction, option_type, expiration, "
                           "anchor_symbol, long_symbol, long_price, short_symbol, short_price, width, contracts, eligible, "
                           "reasons, explanation) VALUES (?,?,'debit','bullish','call','2026-11-20','A','L',1,'S',0.5,1,1,?,?,'')",
                           (run, f"{day}T15:00:00+00:00", int(ok), json.dumps(reasons)))
    store.conn.commit()


def test_summary_says_what_the_screener_saw_and_why_nothing_passed():
    store = _store_with([DAY])
    _candidates(store, date(2026, 10, 20), [(1, False, ["reward_too_small"])])
    _candidates(store, DAY, [(2, False, ["thin_quote", "no_edge_at_executable_prices"]),
                             (2, False, ["no_edge_at_executable_prices"]), (3, False, ["mystery_rule"])])
    text = build_summary(store.conn, DAY, None, [DAY]).text()
    assert "Screener: 3 spreads checked in 2 scans, 0 passed every rule" in text
    assert "top reasons: no edge at real prices (2)" in text and "mystery rule (1)" in text
    assert "2 screener days in a row with nothing passing" in text


def test_a_passing_spread_ends_the_streak():
    store = _store_with([DAY])
    _candidates(store, date(2026, 10, 20), [(1, False, ["reward_too_small"])])
    _candidates(store, DAY, [(2, True, []), (2, False, ["thin_quote"])])
    lines = screener_lines(store.conn, DAY)
    assert lines[0].endswith("1 passed every rule") and not any("in a row" in l for l in lines)
    assert screener_lines(MarketDataStore(":memory:").conn, DAY) == ["Screener: no spreads built today"]


def test_midday_check_in():
    store = _store_with([])
    now = datetime(2026, 10, 21, 17, 0, tzinfo=timezone.utc)
    store.conn.execute("INSERT INTO collection_cycles(underlying, feed, started_at, status) VALUES ('SPY','x',?, 'ok')",
                       ((now - timedelta(minutes=1)).isoformat(),))
    ok = build_midday(store.conn, DAY, collector_running=False, trader_running=True, now=now)
    assert ok.problems == [] and ok.title == "Bot midday Wed Oct 21: OK"      # fresh data proves the collector is alive
    assert "Trades so far: 0 opened" in ok.text()
    stale = build_midday(store.conn, DAY, collector_running=True, trader_running=False, now=now + timedelta(minutes=30))
    assert any("no fresh data" in p for p in stale.problems) and any("Trader is not running" in p for p in stale.problems)
    assert "NEEDS ATTENTION" in stale.title


# --- storage retention ------------------------------------------------------------------------

def _fill(path, days, per_minute=4):
    store = MarketDataStore(path)
    for d in days:
        for minute in range(3):
            for k in range(per_minute):
                at = datetime(d.year, d.month, d.day, 15, minute, k * 15, tzinfo=timezone.utc)
                cid = store.start_cycle("SPY", "indicative")
                store.conn.execute("UPDATE collection_cycles SET started_at=?, status='ok' WHERE id=?", (at.isoformat(), cid))
                store.insert_underlying_quote(cid, UnderlyingQuote("SPY", "iex", 700, 700.02, 1, 1, at, 700.01, at, at), QualityResult())
                store.insert_option_quotes(cid, [(OptionQuote("SPY261106C00700000", "indicative", 1 + k / 100, 1.1, 5, 5, "Q", "Q", at, at),
                                                  QualityResult())])
    store.conn.commit()
    store.close()


def _count(path, day, table="option_quotes"):
    c = sqlite3.connect(path)
    n = c.execute(f"SELECT COUNT(*) FROM {table} WHERE substr(received_at,1,10)=?", (day,)).fetchone()[0]
    c.close()
    return n


def test_thinning_keeps_last_quote_per_minute(tmp_path):
    db = tmp_path / "live.db"
    _fill(db, [date(2026, 10, 1)])
    conn = sqlite3.connect(db)
    assert thin_day(conn, "2026-10-01") == 9                          # 12 quotes over 3 minutes -> 3
    bids = [r[0] for r in conn.execute("SELECT bid FROM option_quotes ORDER BY received_at")]
    assert bids == [1.03, 1.03, 1.03]                                  # the last quote of each minute survives


def test_archive_and_restore_round_trip(tmp_path):
    db = tmp_path / "live.db"
    _fill(db, [date(2026, 10, 1)])
    conn = sqlite3.connect(db)
    moved = archive_day(conn, "2026-10-01", tmp_path / "archive")
    assert moved == 36 and _count(db, "2026-10-01") == 0
    assert (tmp_path / "archive" / "2026-10-01" / "option_quotes.parquet").is_file()
    assert restore_day(conn, "2026-10-01", tmp_path / "archive") == 36
    assert _count(db, "2026-10-01") == 12 and not (tmp_path / "archive" / "2026-10-01").exists()


def test_retention_policy(tmp_path, config_dir, edit_yaml):
    edit_yaml(config_dir / "settings.yaml", storage__keep_full_days=1, storage__archive_after_days=2,
              storage__archive_path=str(tmp_path / "archive"))
    settings = load_settings("development", config_dir=config_dir)
    db = tmp_path / "live.db"
    days = [date(2026, 10, 1), date(2026, 10, 2), date(2026, 10, 5)]
    _fill(db, days)
    rep = run_retention(settings, db_path=db, vacuum=False)
    assert _count(db, "2026-10-05") == 12                              # newest day: full detail
    assert _count(db, "2026-10-02") == 3 and "2026-10-02" in rep.thinned   # thinned to one per minute
    assert _count(db, "2026-10-01") == 0 and "2026-10-01" in rep.archived  # moved to compressed files
    assert data_days(sqlite3.connect(db)) == ["2026-10-05", "2026-10-02"]
    again = run_retention(settings, db_path=db, vacuum=False)
    assert not again.thinned and not again.archived                     # nothing done twice


def test_retention_settings_must_be_ordered(config_dir, edit_yaml):
    from core.config import ConfigError
    edit_yaml(config_dir / "settings.yaml", storage__keep_full_days=10, storage__archive_after_days=5)
    with pytest.raises(ConfigError, match="archive_after_days"):
        load_settings("development", config_dir=config_dir)
