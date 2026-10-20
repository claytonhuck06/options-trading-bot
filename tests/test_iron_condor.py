"""Iron condor builder (Phase 2): every pre-registered entry rule, on synthetic Black-Scholes chains."""

from dataclasses import replace
from datetime import date, timedelta

import pytest

from pricing.black_scholes import greeks, price
from strategies.iron_condor import (
    ChainRow, CondorParams, build_condor, event_ahead, nearest_delta, select_expiry,
)

DAY = date(2019, 6, 3)
SPOT, RATE, Q, VOL = 280.0, 0.02, 0.018, 0.30   # high enough vol that 10-20 delta credits clear 25% of width
HALF_SPREAD = 0.02
TDAYS = [DAY + timedelta(days=i) for i in range(60) if (DAY + timedelta(days=i)).weekday() < 5]


def _chain(expiry: date, vol: float = VOL, strikes=None, half=HALF_SPREAD) -> list[ChainRow]:
    T = (expiry - DAY).days / 365
    rows = []
    for k in strikes or range(200, 361):
        c, p = price(SPOT, k, T, RATE, Q, vol, "call"), price(SPOT, k, T, RATE, Q, vol, "put")
        rows.append(ChainRow(float(k), max(0.0, round(c - half, 2)), round(c + half, 2), vol,
                             greeks(SPOT, k, T, RATE, Q, vol, "call").delta,
                             max(0.0, round(p - half, 2)), round(p + half, 2), vol,
                             greeks(SPOT, k, T, RATE, Q, vol, "put").delta))
    return rows


def _build(params, chains, events=frozenset()):
    return build_condor(DAY, params, SPOT, chains, RATE, TDAYS, set(events))


def test_select_expiry_closest_within_tolerance_tie_to_earlier():
    e = {date(2019, 6, 28): 25, date(2019, 7, 8): 35, date(2019, 7, 19): 46}
    assert select_expiry(e, 30) == date(2019, 6, 28)          # 5 vs 5 days away: earlier wins
    assert select_expiry(e, 45) == date(2019, 7, 19)
    assert select_expiry({date(2019, 8, 30): 88}, 60) is None


def test_nearest_delta_tie_goes_farther_out():
    puts = {270.0: (-0.5, "own"), 265.0: (-0.375, "own"), 260.0: (-0.125, "own")}
    assert nearest_delta(puts, -0.25)[0] == 260.0              # equally close -> lower strike (farther out)
    calls = {290.0: (0.375, "own"), 295.0: (0.125, "own")}
    assert nearest_delta(calls, 0.25)[0] == 295.0


def test_builds_condor_at_target_delta_with_exact_wings():
    exp = DAY + timedelta(days=45)
    d = _build(CondorParams(45, 0.16, 5.0), {exp: _chain(exp)})
    assert d.code == "built", d.reason
    c = d.condor
    assert c.short_put.delta == pytest.approx(-0.16, abs=0.02) and c.short_call.delta == pytest.approx(0.16, abs=0.02)
    assert c.short_put.delta_source == "own"
    assert c.long_put.strike == c.short_put.strike - 5 and c.long_call.strike == c.short_call.strike + 5
    expected = c.short_put.bid + c.short_call.bid - c.long_put.ask - c.long_call.ask
    assert c.credit == pytest.approx(expected)                  # executable prices, never mid
    assert c.contracts == int(1000 // ((5 - c.credit) * 100)) and c.planned_max_loss <= 1000


def test_own_delta_matches_true_delta_even_when_vendor_delta_is_wrong():
    exp = DAY + timedelta(days=45)
    rows = [replace(r, p_delta=-0.5, c_delta=0.5) for r in _chain(exp)]   # vendor deltas garbage
    d = _build(CondorParams(45, 0.16, 5.0), {exp: rows})
    assert d.code == "built" and d.condor.short_put.delta == pytest.approx(-0.16, abs=0.02)


def test_rejections_in_rule_order():
    exp = DAY + timedelta(days=45)
    chain = _chain(exp)
    assert _build(CondorParams(60, 0.16, 5.0), {exp: chain}).code == "no_expiry"
    thin = [r for r in chain if r.strike % 10 == 0]                  # $10 strikes only: $5 wings don't exist
    assert _build(CondorParams(45, 0.16, 5.0), {exp: thin}).code == "no_wing_strike"
    d = _build(CondorParams(45, 0.16, 5.0), {exp: chain})
    sp = d.condor.short_put.strike
    bad = [replace(r, p_bid=0.0) if r.strike == sp else r for r in chain]
    out = _build(CondorParams(45, 0.16, 5.0), {exp: bad})
    assert out.code in ("bad_quote",) or out.condor.short_put.strike != sp
    wide = _chain(exp, half=0.90)                                      # wide markets eat the credit
    assert _build(CondorParams(45, 0.10, 5.0), {exp: wide}).code == "credit_too_small"


def test_crossed_quote_on_a_leg_is_refused():
    exp = DAY + timedelta(days=45)
    chain = _chain(exp)
    built = _build(CondorParams(45, 0.16, 5.0), {exp: chain}).condor
    lc = built.long_call.strike
    crossed = [replace(r, c_bid=r.c_ask + 0.05) if r.strike == lc else r for r in chain]
    d = _build(CondorParams(45, 0.16, 5.0), {exp: crossed})
    assert d.code == "bad_quote" and "crossed" in d.reason


def test_event_filter_counts_entry_day_and_next_four_trading_days():
    assert event_ahead(DAY, TDAYS, {DAY}) == DAY
    assert event_ahead(DAY, TDAYS, {TDAYS[4]}) == TDAYS[4]
    assert event_ahead(DAY, TDAYS, {TDAYS[5]}) is None
    exp = DAY + timedelta(days=45)
    assert _build(CondorParams(45, 0.16, 5.0), {exp: _chain(exp)}, {TDAYS[2]}).code == "event_ahead"


def test_no_uncovered_short_leg_is_possible():
    exp = DAY + timedelta(days=45)
    built = 0
    for delta in (0.10, 0.16, 0.20):
        for width in (5.0, 10.0):
            d = _build(CondorParams(45, delta, width), {exp: _chain(exp)})
            if d.condor is None:
                assert d.code == "credit_too_small"            # far-out wings can't collect 25% of the width
                continue
            built += 1
            c = d.condor
            assert sorted(l.side for l in c.legs) == [-1, -1, 1, 1]
            assert c.long_put.strike < c.short_put.strike < c.short_call.strike < c.long_call.strike
    assert built >= 3
