import math
import random

import pytest

from pricing.volatility_surface import fit_smile, implied_forward


def smile(k):
    return 0.15 - 0.30 * k + 1.5 * k * k


def test_forward_recovered_from_parity():
    S, T, r, F = 768.0, 20 / 365, 0.042, 769.1
    disc = math.exp(-r * T)
    pairs = [(K, max(F - K, 0) * disc + 5, max(K - F, 0) * disc + 5) for K in range(750, 790, 2)]
    est = implied_forward(pairs, S, T, r, 6)
    assert est.forward == pytest.approx(F, abs=1e-9)
    assert est.implied_dividend_yield == pytest.approx(r - math.log(F / S) / T)


def test_fit_recovers_known_smile_and_flags_injected_mispricing():
    rng = random.Random(1)
    ks = [i / 100 for i in range(-10, 11)]
    ivs = [smile(k) + rng.gauss(0, 0.0005) for k in ks]
    bad = ks.index(0.03)
    ivs[bad] += 0.02                                     # one quote 2 vol points too rich
    fit = fit_smile(ks, ivs, [1.0] * len(ks), 3, 8, 4.0)
    assert fit.status == "ok"
    assert fit.iv_at(-0.05) == pytest.approx(smile(-0.05), abs=0.002)
    # Leave-one-out fair value ignores the bad quote itself
    assert fit.loo_iv[bad] == pytest.approx(smile(0.03), abs=0.003)
    z = (ivs[bad] - fit.loo_iv[bad]) / fit.loo_std[bad]
    assert z > 5


def test_outlier_removed_from_fit():
    ks = [i / 100 for i in range(-10, 11)]
    ivs = [smile(k) for k in ks]
    ivs[5] += 0.10
    fit = fit_smile(ks, ivs, [1.0] * len(ks), 3, 8, 4.0)
    assert 5 in fit.excluded and fit.points_excluded == 1


def test_too_few_points():
    assert fit_smile([0.0, 0.01], [0.2, 0.2], [1, 1], 3, 8, 4.0).status == "too_few_points"


def test_calendar_violation_detected():
    from pricing.volatility_surface import calendar_violations
    ok = [("a", 7 / 365, 0.12), ("b", 14 / 365, 0.13), ("c", 21 / 365, 0.13)]
    assert calendar_violations(ok) == []
    bad = [("a", 7 / 365, 0.20), ("b", 14 / 365, 0.12)]     # 0.2^2*7 > 0.12^2*14
    assert calendar_violations(bad) == [("a", "b")]
