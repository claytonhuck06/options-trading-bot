"""Spread construction, risk math, scoring and every rejection rule."""

import math
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone

import pytest

from core.config import load_settings
from pricing.engine import ContractResult, ExpiryResult, PricingResult
from pricing.snapshot import SnapOption, Snapshot
from pricing.volatility_surface import ForwardEstimate
from risk.limits import compute_daily_budget
from strategies.relative_value import score, scan
from strategies.spread_builder import Leg, VerticalSpread, box_gap, build_candidates

AS_OF = datetime(2026, 10, 19, 15, 0, tzinfo=timezone.utc)      # 11:00 ET, inside the entry window
EXP = date(2026, 11, 6)
SETTINGS = load_settings()
BUDGET = compute_daily_budget(SETTINGS.risk, 20_000)


def cr(kind, K, bid, ask, fair, unc=0.02, delta=0.3, vega=0.5, size=50, age=2, iv=0.14):
    o = SnapOption(f"SPY{kind[0].upper()}{K}", kind, float(K), EXP, bid, ask, size, size,
                   AS_OF - timedelta(seconds=age), AS_OF)
    c = ContractResult(o, 18 / 365, fair_iv=iv, fair_price=fair, price_uncertainty=unc,
                       delta=delta, gamma=0.01, theta=-20.0, vega=vega, rho=0.1)
    c.buy_edge, c.sell_edge = fair - ask, bid - fair
    c.buy_z, c.sell_z = c.buy_edge / unc, c.sell_edge / unc
    return c


def result(*contracts, spot=768.0):
    snap = Snapshot(AS_OF, "SPY", spot, AS_OF, [c.option for c in contracts])
    return PricingResult(snap, [ExpiryResult(EXP, 18 / 365, 0.04, ForwardEstimate(769, 0, 6, 0.1), None, list(contracts))], "test")


def spread(long_c, short_c, anchor=None):
    return VerticalSpread(Leg(long_c, 1), Leg(short_c, -1), anchor or long_c.option.symbol)


# --- structure and risk math -------------------------------------------------

@pytest.mark.parametrize("kind,kl,ks,structure,direction", [
    ("call", 770, 775, "debit", "bullish"),
    ("call", 775, 770, "credit", "bearish"),
    ("put", 770, 765, "debit", "bearish"),
    ("put", 765, 770, "credit", "bullish"),
])
def test_labels(kind, kl, ks, structure, direction):
    s = spread(cr(kind, kl, 1, 1.1, 1.05), cr(kind, ks, 1, 1.1, 1.05))
    assert (s.structure, s.direction) == (structure, direction)


def test_debit_spread_risk():
    s = spread(cr("call", 770, 5.00, 5.10, 5.05), cr("call", 775, 2.40, 2.50, 2.45))
    assert s.net_premium == pytest.approx(2.70)           # pay 5.10, receive 2.40
    assert s.max_loss == pytest.approx(2.70)
    assert s.max_profit == pytest.approx(2.30)


def test_credit_spread_risk():
    s = spread(cr("put", 760, 1.00, 1.05, 1.02), cr("put", 765, 2.00, 2.05, 2.02))
    assert s.net_premium == pytest.approx(-0.95)           # receive 2.00, pay 1.05
    assert s.max_loss == pytest.approx(4.05)               # width 5 minus credit
    assert s.max_profit == pytest.approx(0.95)


def test_edge_uses_executable_prices():
    s = spread(cr("call", 770, 5.00, 5.10, 5.30), cr("call", 775, 2.40, 2.50, 2.45))
    assert s.entry_edge == pytest.approx((5.30 - 2.45) - (5.10 - 2.40))


def test_invalid_verticals_refused():
    with pytest.raises(ValueError):
        spread(cr("call", 770, 1, 1.1, 1), cr("put", 775, 1, 1.1, 1))
    with pytest.raises(ValueError):
        VerticalSpread(Leg(cr("call", 770, 1, 1.1, 1), -1), Leg(cr("call", 775, 1, 1.1, 1), 1), "x")


# --- candidate generation ----------------------------------------------------

def test_only_mispriced_anchors_generate_candidates():
    cheap = cr("call", 770, 5.00, 5.10, 5.30)               # buy_z = 10
    n1, n2 = cr("call", 765, 8.00, 8.10, 8.05), cr("call", 775, 2.40, 2.50, 2.45)
    cands = build_candidates(result(cheap, n1, n2), SETTINGS.strategy)
    assert {(c.long.strike, c.short.strike) for c in cands} == {(770, 765), (770, 775)}
    assert all(c.anchor == cheap.option.symbol for c in cands)
    assert build_candidates(result(n1, n2), SETTINGS.strategy) == []


def test_unreliable_legs_never_used():
    cheap = cr("call", 770, 5.00, 5.10, 5.30)
    bad = cr("call", 775, 2.40, 2.50, 2.45)
    bad.flags.add("butterfly")
    assert build_candidates(result(cheap, bad), SETTINGS.strategy) == []


# --- scoring and rejection rules ----------------------------------------------

def good_spread(**kw):
    # A narrow, liquid, clearly cheap debit spread that should pass every rule.
    long_c = cr("call", 770, 1.00, 1.02, 1.40, unc=0.05, delta=0.45, vega=0.6, **kw)
    short_c = cr("call", 771, 0.70, 0.72, 0.70, unc=0.05, delta=0.40, vega=0.6, **kw)
    # Same-strike puts priced consistently with it: call spread 0.32 + put spread 0.67 ~ $1 box.
    puts = cr("put", 770, 3.00, 3.02, 3.01), cr("put", 771, 3.65, 3.67, 3.66)
    return spread(long_c, short_c), result(long_c, short_c, *puts)


def test_good_candidate_is_eligible_and_explained():
    s, r = good_spread()
    c = score(s, r, SETTINGS, BUDGET)
    assert c.eligible, c.reasons
    assert c.max_loss_per_spread == pytest.approx(32 + 0.20)
    assert c.contracts == int(300 // 32.20)              # 1.5% of $20k
    assert c.profit_target == pytest.approx(0.2 * 32.20)
    text = c.explain()
    assert "ELIGIBLE" in text and "buy 770C" in text and "sell 771C" in text


@pytest.mark.parametrize("change, reason", [
    (dict(age=120), "stale_quote"),
    (dict(size=1), "thin_quote"),
])
def test_leg_quality_rules(change, reason):
    s, r = good_spread(**change)
    assert reason in score(s, r, SETTINGS, BUDGET).reasons


def test_wide_leg_spread_rejected():
    long_c = cr("call", 770, 0.80, 1.02, 1.40, unc=0.05)
    s = spread(long_c, cr("call", 771, 0.70, 0.72, 0.70, unc=0.05))
    assert "wide_leg_spread" in score(s, result(*[l.contract for l in s.legs]), SETTINGS, BUDGET).reasons


def test_edge_inside_uncertainty_rejected():
    long_c = cr("call", 770, 1.00, 1.02, 1.08, unc=0.20)
    s = spread(long_c, cr("call", 771, 0.70, 0.72, 0.70, unc=0.20))
    reasons = score(s, result(*[l.contract for l in s.legs]), SETTINGS, BUDGET).reasons
    assert "edge_within_model_uncertainty" in reasons


def test_no_edge_and_costs_rejected():
    long_c = cr("call", 770, 1.00, 1.02, 1.01)
    s = spread(long_c, cr("call", 771, 0.70, 0.72, 0.71))
    reasons = score(s, result(*[l.contract for l in s.legs]), SETTINGS, BUDGET).reasons
    assert "no_edge_at_executable_prices" in reasons and "costs_exceed_expected_edge" in reasons


def test_too_big_for_risk_budget():
    long_c = cr("call", 760, 9.00, 9.02, 9.60, unc=0.05)
    s = spread(long_c, cr("call", 770, 3.00, 3.02, 3.00, unc=0.05))      # $602 max loss > $100 budget
    assert "too_large_for_risk_budget" in score(s, result(*[l.contract for l in s.legs]), SETTINGS, BUDGET).reasons


def test_glitch_quotes_rejected():
    long_c = cr("call", 800, 0.10, 0.11, 0.14)
    s = spread(long_c, cr("call", 802, 0.11, 0.12, 0.10))                # costs nothing, pays up to $2
    assert "too_good_to_be_true" in score(s, result(*[l.contract for l in s.legs]), SETTINGS, BUDGET).reasons


def test_entry_window_enforced():
    s, r = good_spread()
    late = replace(r, snapshot=replace(r.snapshot, as_of=datetime(2026, 10, 19, 19, 30, tzinfo=timezone.utc)))
    assert "outside_entry_window" in score(s, late, SETTINGS, BUDGET).reasons
    assert "outside_entry_window" not in score(s, late, SETTINGS, BUDGET, enforce_window=False).reasons


def test_expiry_too_close():
    s, r = good_spread()
    soon = replace(r, snapshot=replace(r.snapshot, as_of=datetime(2026, 11, 3, 15, 0, tzinfo=timezone.utc)))
    assert "expiry_too_close" in score(s, soon, SETTINGS, BUDGET).reasons


def test_scan_puts_eligible_first():
    s, r = good_spread()
    cands = scan(r, SETTINGS, BUDGET)
    assert cands and cands[0].eligible
    assert all(c.spread.long.side == 1 and c.spread.short.side == -1 for c in cands)   # never naked short


def test_candidates_saved():
    from data.storage.db import MarketDataStore
    from pricing.persistence import save_candidates, save_pricing
    s, r = good_spread()
    store = MarketDataStore(":memory:")
    run_id = save_pricing(store, r)
    assert save_candidates(store, run_id, scan(r, SETTINGS, BUDGET)) >= 1
    row = store.conn.execute("SELECT eligible, explanation FROM candidate_trades").fetchone()
    assert row[0] == 1 and "ELIGIBLE" in row[1]


# --- call/put consistency (box) check: the 2026-10-20 deep in-the-money entries -----------------

def with_puts(long_c, short_c, puts):
    """puts: {strike: (bid, ask)} for the same-strike puts that close the box."""
    return result(long_c, short_c, *[cr("put", k, b, a, (b + a) / 2) for k, (b, a) in puts.items()])


@pytest.mark.parametrize("long_q, short_q, puts, gap", [
    # buy 698C / sell 700C Oct 30 for 0.52 while the 700/698 put spread costs 0.03: a $2 box for $0.55
    ((698, 72.35, 72.46, 72.754), (700, 71.94, 72.84, 70.773), {698: (0.41, 0.42), 700: (0.43, 0.44)}, 1.45),
    # buy 730C / sell 732C Nov 13 for 0.83, put spread 0.22: a $2 box for $1.05
    ((730, 43.51, 43.52, 44.097), (732, 42.69, 42.91, 42.253), {730: (2.64, 2.71), 732: (2.83, 2.86)}, 0.94),
    # buy 693C / sell 695C Oct 30 for 0.85, put spread 0.02
    ((693, 77.55, 77.59, 77.712), (695, 76.74, 77.13, 75.722), {693: (0.33, 0.34), 695: (0.34, 0.35)}, 1.13),
    # sell 745C / buy 755C Nov 02 for a 9.33 credit plus the 755/745 put spread for 1.18: a $10 box sold for $10.51
    ((755, 18.32, 18.47, 18.615), (745, 27.80, 27.96, 27.369), {745: (1.82, 1.84), 755: (3.02, 3.05)}, 0.52),
])
def test_2026_10_20_entries_rejected_as_inconsistent(long_q, short_q, puts, gap):
    long_c = cr("call", *long_q, unc=0.3, delta=0.97, vega=0.05)
    short_c = cr("call", *short_q, unc=0.3, delta=0.96, vega=0.05)
    s, r = spread(long_c, short_c, anchor=short_c.option.symbol), with_puts(long_c, short_c, puts)
    assert box_gap(s, r) == pytest.approx(gap, abs=0.01)
    assert "inconsistent_with_parity" in score(s, r, SETTINGS, BUDGET).reasons


def test_fair_spread_below_intrinsic_is_not_rejected():
    # SPY ~768: the 765/770 call spread is $4 in the money but fairly worth ~$3 (the 770/765 puts cost ~$2).
    long_c, short_c = cr("call", 765, 13.00, 13.05, 13.30), cr("call", 770, 10.05, 10.10, 10.05)
    s, r = spread(long_c, short_c), with_puts(long_c, short_c, {765: (8.00, 8.05), 770: (10.00, 10.05)})
    assert s.net_premium == pytest.approx(3.00)
    assert box_gap(s, r) < 0
    assert "inconsistent_with_parity" not in score(s, r, SETTINGS, BUDGET).reasons


def test_put_spreads_use_calls_for_the_box():
    long_c, short_c = cr("put", 760, 1.00, 1.02, 1.20), cr("put", 765, 2.00, 2.02, 2.02)   # 0.98 credit
    calls = [cr("call", 760, 10.00, 10.05, 10.02), cr("call", 765, 6.00, 6.05, 6.02)]
    r = result(long_c, short_c, *calls)
    s = spread(long_c, short_c)
    assert s.structure == "credit"
    assert box_gap(s, r) == pytest.approx((0.98 + 10.00 - 6.05) - 5 * math.exp(-0.04 * 18 / 365))
    calls[0].option = replace(calls[0].option, bid=12.00)            # call side now sells for 5.95: box > $5
    assert "inconsistent_with_parity" in score(s, r, SETTINGS, BUDGET).reasons


def test_missing_opposite_quotes_fail_closed():
    long_c = cr("call", 770, 1.00, 1.02, 1.40, unc=0.05)
    s = spread(long_c, cr("call", 771, 0.70, 0.72, 0.70, unc=0.05))
    assert "parity_unverifiable" in score(s, result(*[l.contract for l in s.legs]), SETTINGS, BUDGET).reasons


def test_early_exercise_premium_widens_tolerance():
    long_c, short_c = cr("call", 698, 72.35, 72.46, 72.754), cr("call", 700, 71.94, 72.84, 70.773)
    r = with_puts(long_c, short_c, {698: (0.41, 0.42), 700: (0.43, 0.44)})
    s = spread(long_c, short_c)
    long_c.early_exercise_premium = 2.0
    assert "inconsistent_with_parity" not in score(s, r, SETTINGS, BUDGET).reasons
