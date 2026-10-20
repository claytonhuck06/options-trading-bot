"""Real (unadjusted) SPY prices for option studies, and pricing of a contract with no quote."""

from datetime import date, datetime, timezone

import pandas as pd
import pytest

from backtesting.data import load_day
from data.collectors.historical import open_history, raw_factor
from data.collectors.opra_history import previous_close
from pricing.black_scholes import price as bs_price
from research.intraday_vol_study import RATE, _estimate_leg, sequential_runs
from pricing.snapshot import years_to_expiry


def _history_with_factor(factor: float | None):
    conn = open_history(":memory:")
    conn.executemany("INSERT INTO minute_bars VALUES ('SPY',?,?,?,?,?,?,?)",
                     [("2025-05-19T19:58:00+00:00", 580, 580, 580, 580, 1, 580),
                      ("2025-05-20T14:29:00+00:00", 583, 583, 583, 583, 1, 583)])
    if factor is not None:
        conn.executemany("INSERT INTO raw_factors VALUES ('SPY',?,?)", [("2025-05-19", factor), ("2025-05-20", factor)])
    return conn


def test_raw_factor_defaults_to_one_and_converts_prices(tmp_path):
    assert raw_factor(_history_with_factor(None), date(2025, 5, 20)) == 1.0
    conn = _history_with_factor(1.02)
    assert raw_factor(conn, date(2025, 5, 20)) == 1.02
    assert previous_close(conn, date(2025, 5, 20)) == pytest.approx(580 * 1.02)     # strike band centred on the real price
    pd.DataFrame({"ts": [pd.Timestamp("2025-05-20T14:30:00Z")], "symbol": ["SPY250528C00590000"],
                  "bid": [7.2], "ask": [7.3], "bid_size": [1.0], "ask_size": [1.0]}).to_parquet(tmp_path / "2025-05-20.parquet")
    d = load_day(date(2025, 5, 20), conn, tmp_path)
    assert d.spot_at(datetime(2025, 5, 20, 14, 30, tzinfo=timezone.utc)) == pytest.approx(583 * 1.02)


def test_missing_leg_priced_from_nearest_out_of_the_money_vol():
    now = datetime(2025, 5, 20, 14, 30, tzinfo=timezone.utc)
    e, spot, T = date(2025, 5, 28), 590.0, years_to_expiry(date(2025, 5, 28), now)
    book = {}
    for k in (560, 565, 570, 575, 580, 585):
        mid = bs_price(spot, k, T, RATE, 0.0, 0.25, "put")
        book[(e, "put", float(k))] = (mid - 0.02, mid + 0.02)
    bid, ask = _estimate_leg(book, e, "put", 550.0, spot, now)       # further out than anything quoted
    true = bs_price(spot, 550, T, RATE, 0.0, 0.25, "put")
    assert bid < true < ask and ask - bid == pytest.approx(0.14)
    bid, ask = _estimate_leg(book, e, "call", 560.0, spot, now)      # in-the-money call: vol from the 560 put
    assert bid < bs_price(spot, 560, T, RATE, 0.0, 0.25, "call") < ask and bid >= 30 - 0.07


def test_missing_leg_without_nearby_quotes_falls_back_to_intrinsic():
    now = datetime(2025, 5, 20, 14, 30, tzinfo=timezone.utc)
    assert _estimate_leg({}, date(2025, 5, 28), "put", 600.0, 590.0, now) == pytest.approx((9.9, 10.1))


def test_sequential_runs_reopen_on_the_closing_day_or_next_available():
    d = [date(2025, 1, i) for i in (6, 7, 13, 14, 21)]
    t = pd.DataFrame({"day": d, "exit_day": [d[2], d[3], date(2025, 1, 20), d[4], date(2025, 1, 28)],
                      "short_fly_exec": [10.0, 20.0, -5.0, 1.0, 7.0]})
    first = sequential_runs(t)[0]
    assert list(first["day"]) == [d[0], d[2], d[4]] and first["pnl"].sum() == 12.0
