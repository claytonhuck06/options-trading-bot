"""Phase 1 data loaders for the volatility strategy: CBOE indices, FOMC and CPI dates, OptionsDX chains."""

import zipfile
from datetime import date

import pytest

from data.collectors.vol_data import (
    ChainFormatError, fomc_statement_day, load_cpi_dates, load_optionsdx_folder, open_vol_db,
    parse_cboe_csv, parse_fomc_current, parse_fomc_historical, parse_optionsdx,
)


def test_cboe_csv_parses_and_skips_bad_rows():
    text = "DATE,OPEN,HIGH,LOW,CLOSE\n01/02/1990,17.24,17.24,17.24,17.24\nbad,1,1,1,1\n01/03/1990,0,0,0,18.19\n"
    rows = parse_cboe_csv(text)
    assert rows[0] == ("1990-01-02", 17.24, 17.24, 17.24, 17.24)
    assert rows[1] == ("1990-01-03", None, None, None, 18.19)       # zero OHLC (old VIX rows) -> missing, close kept
    assert len(rows) == 2


@pytest.mark.parametrize("year,month,days,expected,kind", [
    (2026, "January", "27-28", date(2026, 1, 28), "scheduled"),
    (2026, "March", "17-18*", date(2026, 3, 18), "scheduled"),
    (2024, "Apr/May", "30-1", date(2024, 5, 1), "scheduled"),
    (2017, "Jan/Feb", "31-1", date(2017, 2, 1), "scheduled"),
    (2025, "August", "22 (notation vote)", date(2025, 8, 22), "notation"),
    (2020, "March", "15 (unscheduled)", date(2020, 3, 15), "unscheduled"),
    (2020, "March", "17-18 (cancelled)", date(2020, 3, 18), "cancelled"),
])
def test_fomc_statement_day(year, month, days, expected, kind):
    assert fomc_statement_day(year, month, days) == (expected, kind)


def test_fomc_pages_parse():
    current = ('<a id="1">2024 FOMC Meetings</a>'
               '<div class="fomc-meeting__month col"><strong>Apr/May</strong></div>\n'
               '<div class="fomc-meeting__date col">30-1</div>'
               '<a id="2">2025 FOMC Meetings</a>'
               '<div class="fomc-meeting__month col"><strong>January</strong></div>\n'
               '<div class="fomc-meeting__date col">28-29</div>')
    assert [d for d, _, _ in parse_fomc_current(current)] == [date(2024, 5, 1), date(2025, 1, 29)]
    hist = ('<h5 class="x">January 28-29 Meeting - 2020</h5><h5 class="x">March 2 (unscheduled) Meeting - 2020</h5>'
            '<h5 class="x">March 19 (notation vote) - 2020</h5><h5>Other heading</h5>')
    got = parse_fomc_historical(hist, 2020)
    assert [(d, k) for d, k, _ in got] == [(date(2020, 1, 29), "scheduled"), (date(2020, 3, 2), "unscheduled"),
                                           (date(2020, 3, 19), "notation")]


def test_bundled_cpi_dates_are_monthly_weekdays():
    rows = load_cpi_dates()
    days = [d for d, _, _ in rows]
    assert days == sorted(days) and len(days) == len(set(days))
    assert all(d.weekday() < 5 for d in days)
    assert date(2022, 6, 10) in days                       # May 2022 CPI (well-known release)
    per_year = {y: sum(d.year == y for d in days) for y in range(2016, 2025)}
    assert set(per_year.values()) == {12}


HEADER = ("[QUOTE_UNIXTIME], [QUOTE_READTIME], [QUOTE_DATE], [QUOTE_TIME_HOURS], [UNDERLYING_LAST], [EXPIRE_DATE],"
          " [EXPIRE_UNIX], [DTE], [C_DELTA], [C_GAMMA], [C_VEGA], [C_THETA], [C_RHO], [C_IV], [C_VOLUME], [C_LAST],"
          " [C_SIZE], [C_BID], [C_ASK], [STRIKE], [P_BID], [P_ASK], [P_SIZE], [P_LAST], [P_DELTA], [P_GAMMA],"
          " [P_VEGA], [P_THETA], [P_RHO], [P_IV], [P_VOLUME], [STRIKE_DISTANCE], [STRIKE_DISTANCE_PCT]")


def _row(strike, dte=30.0, c_bid="1.10", und="400.0"):
    return (f"1, x, 2020-01-02, 16.0, {und}, 2020-02-01, 2, {dte}, 0.5, 0.01, 0.4, -0.05, 0.1, 0.15, 10, 1.2,"
            f" 1 x 1, {c_bid}, 1.20, {strike}, 1.00, 1.10, 1 x 1, 1.05, -0.5, 0.01, 0.4, -0.05, -0.1, 0.16, 5, 0, 0")


def test_optionsdx_parse_filters_and_keeps_blanks_missing():
    text = "\n".join([HEADER, _row(400), _row(600), _row(400, dte=150), _row(395, c_bid=" ")]) + "\n"
    rows, read = parse_optionsdx(text)
    assert read == 4 and len(rows) == 2                        # far strike and long-dated rows dropped
    first = rows[0]
    assert first[0] == "2020-01-02" and first[3] == "2020-02-01" and first[5] == 400.0
    assert first[6] == 1.10 and first[16] == 1.10              # c_bid, p_ask
    assert rows[1][6] is None                                  # blank bid stays missing, never zero


def test_optionsdx_unknown_layout_refused():
    with pytest.raises(ChainFormatError):
        parse_optionsdx("DATE,STRIKE,BID\n2020-01-02,400,1\n")


def test_load_folder_reads_zips_once(tmp_path):
    with zipfile.ZipFile(tmp_path / "spy_2020.zip", "w") as z:
        z.writestr("spy_eod_202001.txt", "\n".join([HEADER, _row(400), _row(405)]) + "\n")
    conn = open_vol_db(":memory:")
    assert load_optionsdx_folder(conn, tmp_path) == [("spy_2020.zip:spy_eod_202001.txt", 2, 2)]
    assert load_optionsdx_folder(conn, tmp_path) == []          # already loaded
    assert conn.execute("SELECT COUNT(*) FROM eod_chain").fetchone()[0] == 2
