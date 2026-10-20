"""Overnight drift screener: definitions, eligibility, costs, ranking and the no-peeking walk-forward."""

from datetime import date, datetime, timezone

import numpy as np
import pandas as pd
import pytest

from research import overnight_screener as ov


def make_panel(n_days=1100, start="2016-01-04", seed=1, drift=None, day_drift=None, price=50.0, volume=2e6):
    """Synthetic stocks: SPY plus A..E. drift = overnight mean per symbol, day_drift = daytime mean."""
    rng = np.random.default_rng(seed)
    cal = pd.bdate_range(start, periods=n_days)
    drift = drift or {"SPY": 0.0003, "A": 0.002, "B": 0.0, "C": -0.001, "D": 0.001, "E": 0.0015}
    day_drift = day_drift or {s: 0.0 for s in drift}
    opens, closes = {}, {}
    for s, mu in drift.items():
        on = mu + rng.normal(0, 0.002, n_days)
        dy = day_drift[s] + rng.normal(0, 0.002, n_days)
        c = np.empty(n_days)
        o = np.empty(n_days)
        prev = price
        for i in range(n_days):
            o[i] = prev * (1 + on[i])
            c[i] = o[i] * (1 + dy[i])
            prev = c[i]
        opens[s], closes[s] = o, c
    o = pd.DataFrame(opens, index=cal)
    c = pd.DataFrame(closes, index=cal)
    v = pd.DataFrame(volume, index=cal, columns=o.columns)
    names = {s: f"{s} Corp" for s in drift}
    return ov.Panel(o, c, v, names)


def test_definitions():
    p = make_panel(n_days=5)
    on, dy = p.overnight, p.day
    assert np.isnan(on.iloc[0, 0])
    assert on.iloc[2, 0] == pytest.approx(p.open.iloc[2, 0] / p.close.iloc[1, 0] - 1)
    assert dy.iloc[2, 0] == pytest.approx(p.close.iloc[2, 0] / p.open.iloc[2, 0] - 1)
    # overnight and day compound to close-to-close
    cc = p.close.iloc[2, 0] / p.close.iloc[1, 0] - 1
    assert (1 + on.iloc[2, 0]) * (1 + dy.iloc[2, 0]) - 1 == pytest.approx(cc)


@pytest.mark.parametrize("dv,cost", [(2e9, 0.0002), (1e9, 0.0002), (500e6, 0.0005), (100e6, 0.0010),
                                     (25e6, 0.0020), (19.9e6, None)])
def test_cost_tiers(dv, cost):
    assert ov.cost_for(dv) == cost


@pytest.mark.parametrize("name,flag", [
    ("ProShares UltraPro QQQ", True), ("Direxion Daily Semiconductor Bull 3X Shares", True),
    ("ProShares Short S&P500", True), ("T-Rex 2X Long NVIDIA Daily Target ETF", True),
    ("iShares Short Treasury Bond ETF", False), ("Apple Inc. Common Stock", False),
    ("SPDR S&P 500 ETF Trust", False), ("Bullfrog AI Holdings", False)])
def test_leveraged_flag(name, flag):
    assert ov.is_leveraged(name) is flag


def test_screener_ranks_overnight_drifters_and_filters():
    # A (+0.20%/night) clears its cost tier; B (flat) and C (negative) don't.
    p = make_panel()
    s = ov.score_window(p, p.open.index[-ov.WINDOW:])
    assert s.loc["A", "cost"] == ov.cost_for(s.loc["A", "dollar_vol"])
    assert s.index[0] == "A"
    assert bool(s.loc["A", "ranked"]) and s.loc["A", "t_stat"] > 2
    assert not s.loc["C", "ranked"]                  # negative overnight drift
    assert s.loc["A", "years_beat"] == 3
    assert s.loc["A", "break_even"] == pytest.approx(s.loc["A", "on_mean"])
    assert ov.pick(s, 2)[0] == "A"


def test_eligibility_price_liquidity_coverage():
    p = make_panel()
    p.close.loc[:, "B"] = p.close["B"] / 20                  # price ~$2.50
    p.volume.loc[:, "C"] = 100                               # illiquid
    p.open.iloc[-400:, p.open.columns.get_loc("D")] = np.nan # big data hole
    s = ov.score_window(p, p.open.index[-ov.WINDOW:])
    assert "B" not in s.index and "C" not in s.index and "D" not in s.index
    assert "A" in s.index


def test_leveraged_never_picked():
    p = make_panel()
    p.names["A"] = "Direxion Daily A Bull 3X Shares"
    s = ov.score_window(p, p.open.index[-ov.WINDOW:])
    assert s.loc["A", "leveraged"]
    assert "A" not in ov.pick(s, 5)


def test_walk_forward_no_peeking_and_costs():
    p = make_panel(n_days=1400)                    # 2016-01 .. mid 2021
    end = p.open.index[-1].date()
    wf = ov.walk_forward(p, end, years=(2019, 2020, 2021), ns=(2, 1, 3))
    assert not wf.problems
    assert wf.picks[2][2019][0] == "A"
    top = wf.baskets["top2"]
    assert top.index[0].year == 2019 and top.index.is_monotonic_increasing
    # cost applied: the free basket beats the costed one by the picks' average tier cost, every night
    gap = wf.baskets["top2_free"] - top
    for _, g in gap.groupby(gap.index.year):
        assert g.min() == pytest.approx(g.max()) and min(c for _, c in ov.COST_TIERS) <= g.iloc[0] <= 0.002
    assert (wf.baskets["top2_2x"] < top).all()


def test_walk_forward_rank_window_excludes_holding_year(monkeypatch):
    p = make_panel(n_days=1400)
    seen = []
    real = ov.score_window

    def spy(panel, days, *rest):
        seen.append(days)
        return real(panel, days, *rest)
    monkeypatch.setattr(ov, "score_window", spy)
    ov.walk_forward(p, p.open.index[-1].date(), years=(2019, 2020), ns=(2,))
    for days, y in zip(seen, (2019, 2020)):
        assert days[-1].year == y - 1 and days[0].year == y - 3
        # the last trading day of y-1 is when the first buy happens, so it is not in the ranking window
        last_prev = p.open.index[p.open.index.year == y - 1][-1]
        assert days[-1] < last_prev


def test_missing_bar_sits_in_cash():
    p = make_panel(n_days=1400)
    syms = ["A", "D"]
    on = p.overnight.loc[p.open.index.year == 2020]
    on.iloc[5, on.columns.get_loc("A")] = np.nan
    costs = pd.Series({"A": 0.001, "D": 0.001})
    b = ov._basket(on, syms, costs, 1.0)
    assert b.iloc[5] == pytest.approx((on["D"].iloc[5] - 0.001) / 2)


def test_stats_and_verdict_shape():
    idx = pd.bdate_range("2019-01-02", periods=504)
    r = pd.Series(0.0004, index=idx)
    st = ov.stats(r)
    assert st.cagr > 0 and st.max_dd == 0 and set(st.yearly) == {2019, 2020, 2021} - ({2021} if idx[-1].year < 2021 else set())
    p = make_panel(n_days=1400)
    wf = ov.walk_forward(p, p.open.index[-1].date(), years=(2019, 2020, 2021))
    v = ov.evaluate(wf)
    assert len(v.bars) == 7


def test_last_completed_day():
    assert ov.last_completed_day(datetime(2026, 10, 20, 18, 0, tzinfo=timezone.utc)) == date(2026, 10, 19)  # 14:00 ET
    assert ov.last_completed_day(datetime(2026, 10, 20, 21, 0, tzinfo=timezone.utc)) == date(2026, 10, 20)  # 17:00 ET


def test_liquidity_floor_restricts_universe():
    p = make_panel()
    p.volume.loc[:, "A"] = 1e9                                   # A: tens of $B a day; the rest ~$100M
    s = ov.score_window(p, p.open.index[-ov.WINDOW:], min_dv=1e9)
    assert list(s.index) == ["A"] and s.loc["A", "cost"] == 0.0002


def test_shortfall_reported_separately_when_allowed():
    p = make_panel(n_days=1400)
    wf = ov.walk_forward(p, p.open.index[-1].date(), years=(2019,), ns=(2, 10), shortfall_is_problem=False)
    assert not wf.problems and any("top 10" in m for m in wf.shortfalls)
    wf = ov.walk_forward(p, p.open.index[-1].date(), years=(2019,), ns=(2, 10))
    assert any("top 10" in m for m in wf.problems)
