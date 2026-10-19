"""End-to-end: synthetic market from a known smile -> engine recovers it,
finds an injected mispricing, and snapshots never see the future."""

import math
from datetime import date, datetime, timedelta, timezone

import pytest

from core.config import load_settings
from data.models import QualityResult
from data.storage.db import MarketDataStore
from pricing.black_scholes import price
from pricing.engine import price_snapshot
from pricing.persistence import save_pricing
from pricing.snapshot import SnapOption, Snapshot, load_snapshot, years_to_expiry
from tests.fakes import contract, quote, underlying

AS_OF = datetime(2026, 10, 19, 18, 0, tzinfo=timezone.utc)
EXP = date(2026, 11, 6)
S, R, Q = 768.0, 0.042, 0.012


def true_iv(K, F):
    k = math.log(K / F)
    return 0.15 - 0.35 * k + 2.0 * k * k


def synthetic_chain(bump=None):
    T = years_to_expiry(EXP, AS_OF)
    F = S * math.exp((R - Q) * T)
    opts = []
    for K in range(700, 841, 2):
        for kind in ("call", "put"):
            fair = price(S, K, T, R, Q, true_iv(K, F), kind)
            if bump and bump == (kind, K):
                fair += 0.60
            half = max(0.01, round(0.01 + 0.004 * fair, 2))
            bid, ask = max(round(fair - half, 2), 0.01), round(fair + half, 2)
            if bid <= 0.01:
                continue
            opts.append(SnapOption(f"SPY{kind[0].upper()}{K}", kind, float(K), EXP, bid, ask, 10, 10, AS_OF, AS_OF))
    return opts


def run(opts):
    cfg = load_settings().pricing
    return price_snapshot(Snapshot(AS_OF, "SPY", S, AS_OF, opts), cfg, lambda T: (R, "test"))


def test_engine_recovers_forward_dividend_and_smile():
    res = run(synthetic_chain())
    e = res.expiries[0]
    T = e.years
    assert e.forward.forward == pytest.approx(S * math.exp((R - Q) * T), abs=0.05)
    assert e.forward.implied_dividend_yield == pytest.approx(Q, abs=0.01)
    assert e.fit.status == "ok" and e.fit.rmse_iv < 0.005
    atm = [c for c in e.contracts if c.option.strike == 768 and c.option.option_type == "call"][0]
    assert atm.fair_iv == pytest.approx(true_iv(768, e.forward.forward), abs=0.003)


def test_engine_flags_injected_mispricing_and_not_neighbours():
    res = run(synthetic_chain(bump=("call", 790)))
    by = {c.option.symbol: c for c in res.contracts}
    bumped = by["SPYC790"]
    assert bumped.sell_z is not None and bumped.sell_z > 3          # quote is too rich: sell side
    clean = [c for c in res.contracts if c.reliable and c.option.symbol != "SPYC790" and c.option.option_type == "call"]
    assert max(c.sell_z for c in clean) < bumped.sell_z


def test_no_apparent_edge_on_fair_market():
    res = run(synthetic_chain())
    reliable = [c for c in res.contracts if c.reliable]
    assert len(reliable) > 80                 # cheap wings are excluded by min_option_price
    # Quotes straddle fair value, so buying at the ask / selling at the bid should never look profitable
    assert max(c.buy_edge for c in reliable) < 0.02
    assert max(c.sell_edge for c in reliable) < 0.02


def test_deep_itm_puts_flagged_for_early_exercise():
    res = run(synthetic_chain())
    deep = [c for c in res.contracts if c.option.option_type == "put" and c.option.strike >= 830]
    assert deep and all("early_exercise" in c.flags for c in deep)


def test_results_saved(tmp_path):
    store = MarketDataStore(":memory:")
    run_id = save_pricing(store, run(synthetic_chain()))
    assert store.conn.execute("SELECT COUNT(*) FROM pricing_residuals WHERE run_id=?", (run_id,)).fetchone()[0] > 100
    assert store.conn.execute("SELECT status FROM volatility_surface_fits").fetchone()[0] == "ok"


# --- look-ahead protection ---------------------------------------------------

def test_snapshot_never_uses_future_quotes():
    store = MarketDataStore(":memory:")
    store.upsert_contracts([contract()])
    t0 = AS_OF - timedelta(seconds=30)
    c1 = store.start_cycle("SPY", "indicative")
    store.insert_underlying_quote(c1, underlying(700, received=t0), QualityResult())
    store.insert_option_quotes(c1, [(quote(bid=1.00, ask=1.05, ts=t0, received=t0), QualityResult())])
    c2 = store.start_cycle("SPY", "indicative")
    later = AS_OF + timedelta(seconds=15)
    store.insert_underlying_quote(c2, underlying(710, received=later), QualityResult())
    store.insert_option_quotes(c2, [(quote(bid=2.00, ask=2.05, ts=later, received=later), QualityResult())])

    snap = load_snapshot(store, "SPY", AS_OF, 60)
    assert snap.spot == pytest.approx(700) and [o.bid for o in snap.options] == [1.00]


def test_repeated_quote_stays_in_snapshot_but_expires_when_not_reconfirmed():
    store = MarketDataStore(":memory:")
    store.upsert_contracts([contract()])
    t0 = AS_OF - timedelta(minutes=5)
    q = quote(ts=t0, received=t0)
    for i in range(0, 6):                      # same quote seen every minute until AS_OF
        rt = t0 + timedelta(minutes=i)
        cid = store.start_cycle("SPY", "indicative")
        store.insert_underlying_quote(cid, underlying(700, received=rt), QualityResult())
        store.insert_option_quotes(cid, [(quote(ts=t0, received=rt), QualityResult())])
    assert len(load_snapshot(store, "SPY", AS_OF, 60).options) == 1
    # Ten minutes later nothing has reconfirmed it (and no underlying either)
    with pytest.raises(LookupError):
        load_snapshot(store, "SPY", AS_OF + timedelta(minutes=10), 60)


def test_rates_only_use_prior_days():
    from pricing.rates import risk_free_rate, to_continuous
    store = MarketDataStore(":memory:")
    store.upsert_rates("DGS1MO", [(date(2026, 10, 16), 4.10), (date(2026, 10, 19), 9.99)])
    store.upsert_rates("DGS3MO", [(date(2026, 10, 16), 4.30)])
    info = risk_free_rate(load_settings(), store, AS_OF, 1 / 12)
    assert info.rate == pytest.approx(to_continuous(4.10)) and "2026-10-16" in info.source


def test_rate_fallback_when_missing():
    from pricing.rates import risk_free_rate
    info = risk_free_rate(load_settings(), MarketDataStore(":memory:"), AS_OF, 0.1)
    assert info.source == "fallback" and info.rate == 0.04


def test_fred_csv_parsing_skips_missing_values():
    from pricing.rates import parse_fred_csv
    assert parse_fred_csv("observation_date,DGS1MO\n2026-10-15,4.17\n2026-10-16,.\n") == [(date(2026, 10, 15), 4.17)]
