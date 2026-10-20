"""Phase 1b volatility forecast: realized variance, no look-ahead in fitting, loss and test math."""

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from research.vol_forecast import (
    HORIZON, build_features, daily_variance, diebold_mariano, oos_forecasts, qlike,
)


def _minutes(day: date, prices: list[float], first_open: float) -> list[dict]:
    """Bars at 9:30, 9:31, ... ET (EDT, UTC-4) with the given closes."""
    start = pd.Timestamp(day.isoformat() + " 13:30", tz="UTC")
    return [{"ts": (start + pd.Timedelta(minutes=i)).isoformat(), "open": first_open if i == 0 else p, "close": p}
            for i, p in enumerate(prices)]


def test_daily_variance_five_minute_grid_and_overnight():
    d1, d2 = date(2024, 7, 1), date(2024, 7, 2)
    rows = _minutes(d1, [100.0] * 390, 100.0) + _minutes(d2, [102.0] * 5 + [101.0] * 385, 101.0)
    rows.append({"ts": pd.Timestamp("2024-07-02 21:30", tz="UTC").isoformat(), "open": 50.0, "close": 50.0})  # after hours
    out = daily_variance(pd.DataFrame(rows))
    assert list(out.index) == [d2]                                  # first day only provides the prior close
    r = out.loc[d2]
    assert r["var_overnight"] == pytest.approx(np.log(101 / 100) ** 2)
    # open 101 -> 102 in the first bucket, then 102 -> 101 in the second; after-hours bar ignored
    assert r["var_intraday"] == pytest.approx(np.log(102 / 101) ** 2 + np.log(101 / 102) ** 2)
    assert r["ret"] == pytest.approx(np.log(101 / 100))


def _synthetic(n=900, seed=1):
    rng = np.random.default_rng(seed)
    days = [date(2016, 1, 1) + timedelta(days=i) for i in range(n)]
    logvol = np.zeros(n)
    for i in range(1, n):
        logvol[i] = 0.97 * logvol[i - 1] + 0.1 * rng.standard_normal()
    var = (0.15 * np.exp(logvol)) ** 2 / 252 * rng.chisquare(5, n) / 5
    daily = pd.DataFrame({"var": var, "ret": rng.standard_normal(n) * np.sqrt(var)}, index=days)
    vix = pd.Series(100 * 0.17 * np.exp(logvol), index=days)
    return daily, vix


def test_features_target_is_strictly_future():
    daily, vix = _synthetic(200)
    feat = build_features(daily, vix, {daily.index[50]})
    i = 30
    day = feat.index[i]
    pos = list(daily.index).index(day)
    assert feat.loc[day, "target_var"] == pytest.approx(252 * daily["var"].iloc[pos + 1:pos + 1 + HORIZON].mean())
    assert feat.loc[day, "target_end"] == daily.index[pos + HORIZON]
    assert feat.loc[daily.index[40], "events"] == 1 and feat.loc[daily.index[50], "events"] == 0


def test_oos_forecasts_only_use_finished_targets():
    daily, vix = _synthetic(900)
    feat = build_features(daily, vix, set())
    fc = oos_forecasts(feat, first_forecast="2017-06-01")
    first_month = fc[pd.to_datetime(pd.Index(fc.index)) < pd.Timestamp("2017-07-01")]
    ends = pd.to_datetime(feat["target_end"])
    expected = int(((ends < pd.Timestamp("2017-06-01")) & feat["log_target"].notna()).sum())
    assert set(first_month["train_rows"]) == {expected}
    assert (fc[["M0", "M1", "M2"]] > 0).all().all()


def test_qlike_and_dm():
    assert qlike(np.array([0.04]), np.array([0.04]))[0] == pytest.approx(0.0)
    assert qlike(np.array([0.04]), np.array([0.02]))[0] > 0
    rng = np.random.default_rng(0)
    good = rng.random(500) * 0.1
    bad = good + 0.05 + rng.random(500) * 0.01
    t, p = diebold_mariano(good, bad)
    assert t < 0 and p < 0.01
    t2, p2 = diebold_mariano(bad, good)
    assert p2 > 0.99
