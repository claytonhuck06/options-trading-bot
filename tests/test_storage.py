import json

from data.models import QualityResult
from data.storage.db import MarketDataStore
from tests.fakes import NOW, contract, quote, underlying


def store():
    return MarketDataStore(":memory:")


def test_schema_and_contract_upsert():
    s = store()
    assert s.upsert_contracts([contract(), contract("SPY261030P00700000")]) == 2
    s.upsert_contracts([contract()])  # upsert, not duplicate
    assert s.known_contract_symbols("SPY") == {"SPY261030C00700000", "SPY261030P00700000"}


def test_repeated_quote_not_stored_twice():
    s = store()
    c1 = s.start_cycle("SPY", "indicative")
    q = quote()
    assert s.insert_option_quotes(c1, [(q, QualityResult())]) == 1
    c2 = s.start_cycle("SPY", "indicative")
    assert s.insert_option_quotes(c2, [(q, QualityResult())]) == 0
    assert s.conn.execute("SELECT COUNT(*) FROM option_quotes").fetchone()[0] == 1


def test_both_timestamps_and_flags_persisted():
    s = store()
    c = s.start_cycle("SPY", "indicative")
    s.insert_option_quotes(c, [(quote(bid=2, ask=4), QualityResult(["wide_spread"]))])
    s.insert_underlying_quote(c, underlying(), QualityResult())
    row = s.conn.execute("SELECT * FROM option_quotes").fetchone()
    assert row["quote_ts"] and row["received_at"] == NOW.isoformat()
    assert json.loads(row["quality_flags"]) == ["wide_spread"] and row["is_usable"] == 1
    assert s.conn.execute("SELECT COUNT(*) FROM underlying_quotes").fetchone()[0] == 1


def test_cycle_and_event_records():
    s = store()
    c = s.start_cycle("SPY", "indicative")
    s.finish_cycle(c, "ok", {"received": 5, "new": 3, "repeat": 2, "unusable": 1})
    s.log_event("INFO", "unit", {"a": 1})
    row = s.conn.execute("SELECT * FROM collection_cycles").fetchone()
    assert (row["status"], row["quotes_new"], row["quotes_repeat"]) == ("ok", 3, 2)
    assert s.conn.execute("SELECT event FROM system_events").fetchone()[0] == "unit"


def test_file_database_survives_reopen(tmp_path):
    path = tmp_path / "x.db"
    s = MarketDataStore(path); s.upsert_contracts([contract()]); s.close()
    assert MarketDataStore(path).known_contract_symbols("SPY") == {"SPY261030C00700000"}
