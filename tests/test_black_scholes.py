import math

import pytest

from pricing.american import american_price, early_exercise_premium
from pricing.black_scholes import greeks, no_arbitrage_bounds, price
from pricing.implied_volatility import implied_vol


def test_hull_textbook_values():
    # Hull, Options Futures & Other Derivatives: S=42 K=40 r=10% sigma=20% T=0.5
    assert price(42, 40, 0.5, 0.10, 0, 0.20, "call") == pytest.approx(4.7594, abs=1e-4)
    assert price(42, 40, 0.5, 0.10, 0, 0.20, "put") == pytest.approx(0.8086, abs=1e-4)


@pytest.mark.parametrize("S,K,T,r,q,s", [(100, 100, 1, 0.05, 0.02, 0.2), (768, 700, 20 / 365, 0.042, 0.012, 0.18), (50, 80, 0.1, 0.0, 0.0, 0.6)])
def test_put_call_parity(S, K, T, r, q, s):
    c, p = price(S, K, T, r, q, s, "call"), price(S, K, T, r, q, s, "put")
    assert c - p == pytest.approx(S * math.exp(-q * T) - K * math.exp(-r * T), abs=1e-10)


@pytest.mark.parametrize("kind", ["call", "put"])
@pytest.mark.parametrize("K", [700, 768, 830])
def test_greeks_match_finite_differences(kind, K):
    S, T, r, q, s = 768.0, 20 / 365, 0.042, 0.012, 0.15
    g = greeks(S, K, T, r, q, s, kind)
    f = lambda **kw: price(**{**dict(S=S, K=K, T=T, r=r, q=q, sigma=s, kind=kind), **kw})
    h = 1e-3
    assert g.delta == pytest.approx((f(S=S + h) - f(S=S - h)) / (2 * h), abs=1e-6)
    assert g.gamma == pytest.approx((f(S=S + h) - 2 * f() + f(S=S - h)) / h**2, rel=1e-3, abs=1e-6)
    assert g.vega == pytest.approx((f(sigma=s + 1e-5) - f(sigma=s - 1e-5)) / 2e-5, rel=1e-5)
    assert g.rho == pytest.approx((f(r=r + 1e-6) - f(r=r - 1e-6)) / 2e-6, rel=1e-4)
    assert g.theta == pytest.approx(-(f(T=T + 1e-7) - f(T=T - 1e-7)) / 2e-7, rel=1e-4)


def test_edge_cases():
    assert price(110, 100, 0, 0.05, 0, 0.2, "call") == 10          # expired: intrinsic
    assert price(90, 100, 0, 0.05, 0, 0.2, "call") == 0
    assert price(110, 100, 1, 0.0, 0.0, 0.0, "call") == pytest.approx(10)
    assert price(100, 1000, 0.05, 0.04, 0, 0.15, "call") < 1e-12   # far out of the money
    with pytest.raises(ValueError):
        price(100, 100, 1, 0.05, 0, 0.2, "straddle")
    with pytest.raises(ValueError):
        price(-1, 100, 1, 0.05, 0, 0.2, "call")


@pytest.mark.parametrize("kind", ["call", "put"])
@pytest.mark.parametrize("sigma", [0.05, 0.15, 0.6, 2.0])
@pytest.mark.parametrize("K", [650, 768, 880])
def test_implied_vol_round_trip(kind, sigma, K):
    S, T, r, q = 768, 15 / 365, 0.042, 0.012
    p = price(S, K, T, r, q, sigma, kind)
    lo, _ = no_arbitrage_bounds(S, K, T, r, q, kind)
    if p - lo < 1e-6:
        pytest.skip("price indistinguishable from intrinsic; vol not identifiable")
    res = implied_vol(p, S, K, T, r, q, kind)
    assert res.ok and res.iv == pytest.approx(sigma, rel=1e-5)


def test_implied_vol_refuses_impossible_prices():
    assert implied_vol(9.0, 110, 100, 0.5, 0.05, 0, "call").status == "below_intrinsic"
    assert implied_vol(200.0, 110, 100, 0.5, 0.05, 0, "call").status == "above_max"
    assert implied_vol(0.0, 110, 100, 0.5, 0.05, 0, "call").status == "bad_price"
    assert implied_vol(1.0, 110, 100, 0.0, 0.05, 0, "call").status == "no_time"


def test_american_put_matches_hull_converged_value():
    # Hull example 21.1 (S=K=50, r=10%, sigma=40%, 5 months): converges to ~4.28
    assert american_price(50, 50, 5 / 12, 0.10, 0, 0.40, "put", 500) == pytest.approx(4.28, abs=0.01)


def test_american_call_without_dividends_equals_european():
    assert early_exercise_premium(100, 90, 0.5, 0.05, 0.0, 0.3, "call", 300) == pytest.approx(0, abs=1e-9)


def test_deep_itm_put_has_material_early_exercise_premium():
    assert early_exercise_premium(700, 770, 20 / 365, 0.042, 0.012, 0.15, "put") > 1.0
    assert early_exercise_premium(768, 700, 20 / 365, 0.042, 0.012, 0.15, "put") < 1e-3   # out of the money: negligible
