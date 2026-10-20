# Relative Momentum (Top 2 of 5 ETFs, 12-Month Return): Pre-registered Rules and Pass/Fail Bars

*Committed 2026-10-20, before any return for this strategy was computed. Paper research only; not
investment advice.*

**Where the idea comes from.** The five-asset 10-month trend rule was a no-go on 2026-10-20: it beat holding the
same five but lost badly to SPY ([report](multi_asset_trend_backtest_2026-10-20.md)), mostly because it sat in cash
while US stocks rose. The owner pasted a write-up on *relative* momentum (rank a fixed group by past 12-month
return, hold the top two) and asked for the backtest. Relative momentum never goes to cash, so it targets that
weakness directly.

**The question.** Does ranking five asset classes by 12-month total return and holding the top two beat (a) holding
all five equally and (b) simply holding SPY, after costs, and does it hold up in the later period and with
nearby lookbacks?

Rules for changing anything in this file once results have been seen: you can't. Problems get written down here,
dated, as amendments that say whether any results had been seen. A failed strategy is reported as failed.

Own file (`research/relative_momentum.py`); reuses the five-asset trend test's data, simulator and statistics.
Changes nothing in the live trader, the collector, `settings.yaml` or `config/risk_limits.yaml`.

---

## A. Assets, data and periods

**The same five ETFs as the trend test**, unchanged, so the basket isn't picked to make this rule look good:

| Asset class | ETF |
|---|---|
| US stocks | SPY |
| Foreign developed stocks | EFA |
| US Treasury bonds (7–10 yr) | IEF |
| US real estate (REITs) | VNQ |
| Commodities | DBC |

- The write-up's example basket used gold instead of commodities. Two versions are shown **for information only**
  and can't change the verdict: GLD in place of DBC, and all six together.
- **Prices:** the same cached Yahoo daily data as the trend test (open, close, dividends with ex-dates). Dividends
  are reinvested in the account as before.
- **Total-return index for ranking:** starting at 1, each day multiplied by (close + dividend paid that day) ÷
  previous close. So the score includes distributions, as the write-up requires.
- **Cash interest:** FRED `DTB3` (only matters for the Sharpe ratio; the strategy is always fully invested).
- **Common start:** the first trading day of the month after the first month-end on which every asset has 14
  month-end closes (so lookbacks up to 13 months all start together). With DBC starting in February 2006 that
  is **April 2007**. Every version and benchmark starts that day with $100,000.
- **Discovery:** start to 2012-12. **Confirmation:** 2013-01 to the last complete month (September 2026), the same
  split as before. Nothing is tuned on either.
- Known weakness, stated in advance: about 19½ years of data with only one big bear market (2008), and 2022, when
  stocks and bonds fell together.

## B. The rule

At each month-end, for each asset: score = total-return index at this month-end ÷ total-return index **12**
month-ends earlier − 1.

- Rank the five scores from highest to lowest. Ties go to the asset listed first in the table above.
- **Hold the top two, 50% each.** Nothing in cash, no borrowing, no short sales.
- **Rebalance every month** at the **open of the first trading day after the month-end**, back to exactly 50/50,
  even if the same two stay on top. Nothing happens between month-ends.
- On the start day, holdings come from the March 2007 month-end ranking.

## C. Benchmarks

- **Equal-weight hold (main benchmark):** 20% in each of the five, rebalanced monthly the same way. Shows whether
  ranking and rotating adds anything over owning the basket.
- **SPY buy-and-hold:** buy once on the start day, reinvest dividends, never sell.

## D. Costs

A fraction of each trade's value on every buy and sell (including rebalancing trades), for the strategy and the
benchmarks alike. **Primary: 0.05% per side.** Also reported: 0%, 0.10%, and the **stress level 0.25%**. Taxes are
ignored (the strategy sells more often, so taxes would hurt it more; stated in the report).

## E. What the report shows

Total return, CAGR, max drawdown, worst calendar year, Sharpe (monthly returns minus T-bill, × √12), trades, for
the full, discovery and confirmation periods; a chart of the strategy, equal-weight hold and SPY; lookbacks 3, 6,
9, 11, 12, 13; top 1 and top 3 instead of top 2 (information only); results by cost level; **turnover** (how many
month-ends changed at least one holding); **concentration** (share of months holding both stock funds, SPY and
EFA, together); the gold versions; and a monthly log (each asset's score and rank, the two held, rebalance day) as
CSV.

## F. Pass/fail bars (0.05% per side unless stated)

| # | Bar |
|---|---|
| 1 | **Log check:** every trade is on the first trading day after a month-end, and after each rebalance the funds held are exactly the top two by score (re-ranked independently from the log). 100% required. |
| 2 | **Ranking helps on risk-adjusted return:** full-period Sharpe ≥ the equal-weight hold's. |
| 3 | **Ranking helps on return:** full-period CAGR ≥ the equal-weight hold's. |
| 4 | **Not riskier than the basket:** full-period max drawdown ≤ the equal-weight hold's. |
| 5 | **Later period:** in the confirmation period, Sharpe ≥ **and** CAGR ≥ the equal-weight hold's. |
| 6 | **Not fragile:** lookbacks of 11 and 13 months each pass bars 2 and 3. |
| 7 | **Costs:** at 0.25% per side, the 12-month version still passes bar 2. |
| 8 | **Worth it vs. just SPY (full period):** Sharpe ≥ SPY's **and** max drawdown ≤ SPY's. |
| 9 | **Worth it vs. just SPY (later period):** confirmation-period Sharpe ≥ 0.9 × SPY's. |

**GO** = all nine pass. The next step would be the separate dual-momentum test (adding the trend-to-cash rule),
and/or a pre-registered paper trade; both owner decisions. **NO-GO** = any bar fails; the report says which and
why, nothing is built for trading, and the rule is not re-tuned (no shopping for lookbacks, baskets or number of
holdings).
