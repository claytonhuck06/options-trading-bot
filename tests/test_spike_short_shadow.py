"""Shadow log: raw-gap prefilter, checklist classification and cap, IBKR recording, same-day exit, idempotent runs."""

from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest

from research import spike_short_shadow as sh
from research import spike_short_v4 as v4
from research.spike_short import ET

TODAY = date(2026, 10, 22)


def history(sym, gap=1.0, dead=True, ssr=False, n=300):
    """n split-adjusted daily bars ending TODAY: falling 10 -> 2 if dead (flat 2 otherwise), today opens +gap."""
    days = pd.bdate_range(end=pd.Timestamp(TODAY), periods=n)
    close = np.linspace(10.0, 2.0, n) if dead else np.full(n, 2.0)
    low = close * 0.98
    if ssr:
        low[-2] = close[-3] * 0.85
    df = pd.DataFrame({"symbol": sym, "day": days, "open": close, "high": close * 1.01, "low": low, "close": close,
                       "volume": 5e6})
    df.loc[df.index[-1], "open"] = close[-2] * (1 + gap)
    return df


def frames(*hs):
    adj = pd.concat(hs, ignore_index=True)
    return adj, adj.copy()


def no_news(*_):
    return False


def test_raw_gappers_filters_gap_and_price():
    t, y = pd.Timestamp(TODAY), pd.Timestamp("2026-10-21")
    recent = pd.DataFrame([("A", y, 1, 1, 1, 2.0, 1), ("A", t, 4.0, 4, 4, 4, 1),        # +100%: in
                           ("B", y, 1, 1, 1, 2.0, 1), ("B", t, 3.0, 3, 3, 3, 1),        # +50%: out
                           ("C", y, 1, 1, 1, 9.0, 1), ("C", t, 20.0, 20, 20, 20, 1),    # prior close $9: out
                           ("D", y, 1, 1, 1, 0.4, 1), ("D", t, 4.0, 4, 4, 4, 1)],       # $0.40 (reverse split): out
                          columns=["symbol", "day", "open", "high", "low", "close", "volume"])
    g = sh.raw_gappers(recent, TODAY)
    assert list(g["symbol"]) == ["A"] and g["gap"].iloc[0] == pytest.approx(1.0)


def test_classify_statuses_and_cap():
    adj, raw = frames(history("A", 2.0), history("B", 1.5), history("C", 1.2), history("D", 1.0),
                      history("N", 3.0), history("S", 0.9, ssr=True), history("L", 1.0, dead=False))
    rows = sh.classify(sh.features_today(adj, raw, TODAY), lambda s, d, p: s == "N")
    st = {r["symbol"]: (r["status"], r.get("rank")) for r in rows}
    assert "L" not in st                                     # not a dead company
    assert st["N"] == ("news", None) and st["S"] == ("ssr", None)
    assert st["A"] == ("signal", 1) and st["B"] == ("signal", 2) and st["C"] == ("signal", 3)
    assert st["D"] == ("over_cap", 4)


def test_reverse_split_is_not_a_gap():
    h = history("R", 0.0)                                    # adjusted: no gap at all
    rows = sh.classify(sh.features_today(*frames(h), TODAY), no_news)
    assert rows == []


def test_settle_close_and_stop():
    s = sh.settle(4.0, 4.4, 3.0, 10e6, TODAY)
    assert not s["stopped"] and s["exit"] == 3.0
    assert s["cost"] == pytest.approx(0.01 + v4.LOCATE)       # $2-5 tier, no borrow on a day trade
    assert s["pnl"] == pytest.approx(sh.POSITION * sh.EQUITY * (0.25 - s["cost"]))
    s = sh.settle(4.0, 7.0, 6.5, 10e6, TODAY)
    assert s["stopped"] and s["exit"] == pytest.approx(6.0)
    assert s["cost"] == pytest.approx(0.01 + v4.STOP_SLIP + v4.LOCATE)
    assert s["pnl"] / sh.EQUITY > -0.016                     # a stop costs about 1.5% of the account


def test_shares_needed():
    assert sh.POSITION * sh.EQUITY == pytest.approx(2727.27, abs=0.01)
    assert sh.shares_needed(2.0) == 1364


def _recent(n=150):
    t, y = pd.Timestamp(TODAY), pd.Timestamp("2026-10-21")
    rows = [(f"X{i}", d, 1, 1, 1, 1, 1) for i in range(n) for d in (y, t)]
    rows += [("A", y, 2, 2, 2, 2.0, 1), ("A", t, 6.0, 6, 6, 6, 1)]
    return pd.DataFrame(rows, columns=["symbol", "day", "open", "high", "low", "close", "volume"])


def test_morning_records_ibkr_then_close_settles():
    conn = sh.open_db(":memory:")
    calls = []

    def ibkr():
        calls.append(1)
        return {"A": (5_000, 250.0)}, "2026.10.22 09:35"
    rows, note = sh.morning(conn, TODAY, ["A"], _recent(), lambda s: frames(history("A", 2.0)), no_news, ibkr)
    assert [r["symbol"] for r in rows] == ["A"] and rows[0]["placeable"] == 1 and calls == [1]
    db = conn.execute("SELECT status, ibkr_available, ibkr_fee, shares_needed, placeable FROM shadow").fetchone()
    assert db == ("signal", 5000, 250.0, sh.shares_needed(rows[0]["open_am"]), 1)

    again, note = sh.morning(conn, TODAY, ["A"], _recent(), lambda s: frames(history("A", 2.0)), no_news, ibkr)
    assert again == [] and "already" in note and calls == [1]

    early = datetime(2026, 10, 22, 15, 0, tzinfo=ET)
    assert sh.close(conn, TODAY, early, lambda s, d: (6.0, 6.5, 4.5)) == []      # not final yet
    late = datetime(2026, 10, 22, 16, 30, tzinfo=ET)
    done = sh.close(conn, TODAY, late, lambda s, d: (6.0, 6.5, 4.5))
    assert len(done) == 1 and done[0]["exit"] == 4.5
    t = sh.totals(conn)
    assert t["settled"] == 1 and t["pnl"] == pytest.approx(done[0]["pnl"]) and t["placeable"] == 1
    assert sh.close(conn, TODAY, late, lambda s, d: (6.0, 6.5, 4.5)) == []       # settled once
    assert "1 trades over 1 days" in sh.close_message(done, t)


def test_morning_on_a_holiday_does_nothing():
    conn = sh.open_db(":memory:")
    rows, note = sh.morning(conn, TODAY, [], _recent(0).iloc[:0], None, None, None)
    assert rows == [] and "closed" in note
    assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 0


def test_no_ibkr_download_without_candidates():
    conn = sh.open_db(":memory:")
    recent = _recent()
    recent = recent[recent["symbol"] != "A"]
    rows, _ = sh.morning(conn, TODAY, [], recent, None, None, lambda: pytest.fail("should not download"))
    assert rows == []


def test_module_has_no_order_code():
    src = open(sh.__file__).read()
    assert "TradingClient" not in src and "submit_order" not in src
