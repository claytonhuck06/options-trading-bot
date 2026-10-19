from datetime import timedelta

import pytest

from core.config import load_settings
from data.collectors.options_collector import OptionsCollector, expiry_window
from data.storage.db import MarketDataStore
from tests.fakes import NOW, FakeSource, quote


def make(source, clock=lambda: NOW):
    sleeps = []
    c = OptionsCollector(load_settings("paper"), source, MarketDataStore(":memory:"), sleep=sleeps.append, clock=clock)
    return c, sleeps


def count(c, table, where="1=1"):
    return c.store.conn.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}").fetchone()[0]


def test_expiry_window_is_7_to_30_days():
    from datetime import date
    assert expiry_window(load_settings(), date(2026, 10, 19)) == (date(2026, 10, 26), date(2026, 11, 18))


def test_single_cycle_stores_and_validates():
    src = FakeSource(chains=[[quote(), quote("SPY261030C00710000", bid=1.2, ask=1.1)]])
    c, _ = make(src)
    r = c.collect_once()
    assert (r.received, r.new, r.repeat) == (2, 2, 0)
    assert r.unusable == 1                     # one quote is both crossed and an unknown contract
    assert r.flag_counts["crossed"] == 1 and r.flag_counts["unknown_contract"] == 1
    assert count(c, "collection_cycles", "status='ok'") == 1


def test_chain_limited_to_strike_band_around_spot():
    src = FakeSource()
    c, _ = make(src)
    c.collect_once()
    assert src.last_strike_band == (630.0, 770.0)   # spot 700 ± 10%


def test_repeated_stale_quotes_counted_not_stored():
    q = quote()
    c, _ = make(FakeSource(chains=[[q], [q], [quote(bid=1.01, ts=NOW)]]))
    reports = [c.collect_once() for _ in range(3)]
    assert [r.new for r in reports] == [1, 0, 1]
    assert reports[1].repeat == 1
    assert count(c, "option_quotes") == 2


def test_contracts_refreshed_only_on_schedule():
    t = [NOW]
    src = FakeSource()
    c, _ = make(src, clock=lambda: t[0])
    c.collect_once(); c.collect_once()
    assert src.contract_calls == 1
    t[0] = NOW + timedelta(minutes=61)
    c.collect_once()
    assert src.contract_calls == 2


def test_empty_chain_is_a_failed_cycle():
    c, _ = make(FakeSource(chains=[[]]))
    with pytest.raises(RuntimeError, match="no quotes"):
        c.collect_once()
    assert count(c, "collection_cycles", "status='failed'") == 1


def test_recovers_from_disconnects_with_backoff():
    c, sleeps = make(FakeSource(fail_times=2))
    assert c.run(lambda: True, max_cycles=1) == 1
    assert sleeps[:2] == [15, 30]             # exponential backoff
    assert count(c, "system_events", "event='cycle_failed'") == 2
    assert count(c, "system_events", "event='collector_recovered'") == 1


def test_halts_after_too_many_consecutive_failures():
    c, sleeps = make(FakeSource(fail_times=10_000))
    with pytest.raises(RuntimeError, match="halted"):
        c.run(lambda: True)
    assert count(c, "system_events", "event='collector_halted'") == 1
    assert max(sleeps) <= 300


def test_stops_when_market_closed():
    src = FakeSource()
    c, _ = make(src)
    assert c.run(lambda: False) == 0
    assert src.chain_calls == 0


def test_clock_failure_does_not_collect_blindly():
    def broken():
        raise ConnectionError("clock down")
    src = FakeSource()
    c, _ = make(src)
    with pytest.raises(RuntimeError, match="halted"):
        c.run(broken)
    assert src.chain_calls == 0
