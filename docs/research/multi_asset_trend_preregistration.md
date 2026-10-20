# Multi-Asset Monthly Trend (Faber's 5 Asset Classes): Pre-registered Rules and Pass/Fail Bars

*Committed 2026-10-20, before any return for this strategy was computed. Paper research only; not
investment advice.*

**Where the idea comes from.** The SPY-only 10-month rule was a no-go on 2026-10-20: it failed the 2013–2026
check ([report](monthly_trend_backtest_2026-10-20.md)). The write-up the owner pasted suggested the next step:
apply the **same, unchanged rule** to several asset classes, as in Meb Faber's research, instead of adding
indicators. The owner chose that.

**The question.** Does applying the 10-month rule separately to five asset classes give a better mix of return and
drawdown than (a) holding the same five without timing and (b) simply holding SPY, after costs, and does it
survive small changes and the later period?

Rules for changing anything in this file once results have been seen: you can't. Problems get written down here,
dated, as amendments that say whether any results had been seen. A failed strategy is reported as failed.

Own file (`research/multi_asset_trend.py`); reuses the SPY test's code for data, signal and statistics. Changes
nothing in the live trader, the collector, `settings.yaml` or `config/risk_limits.yaml`.

---

## A. Assets, data and periods

Faber's five asset classes, each through one well-known ETF:

| Asset class | ETF | Data from |
|---|---|---|
| US stocks | SPY | 1993 |
| Foreign developed stocks | EFA | 2001 |
| US Treasury bonds (7–10 yr) | IEF | 2002 |
| US real estate (REITs) | VNQ | 2004 |
| Commodities | DBC | 2006-02 |

- Gold isn't one of Faber's five. A six-asset version with GLD added is shown **for information only** and can't
  change the verdict.
- **Prices:** Yahoo Finance free daily data (open, close, dividends with ex-dates), saved once to
  `data/storage/studies/` (git-ignored). Yahoo's close is already split-adjusted (EFA's 2005 3-for-1 split is
  before the start anyway). Dividends are reinvested as in the SPY test.
- **Data checks (reported):** no ETF has a one-day close move larger than 25% (that would point to an
  unadjusted split or a bad price); trading days are the days on which **all** assets have a price, and the number
  of days dropped for that is reported.
- **Cash interest:** FRED `DTB3`, the rate published before each day, as in the SPY test.
- **Common start:** the first trading day of the month after the first month-end on which **every** asset has 12
  month-end closes (so lookbacks 8–12 all start together). With DBC starting in February 2006 that is
  **February 2007**. Every version and every benchmark starts that day with $100,000.
- **Discovery period:** start to 2012-12. **Confirmation period:** 2013-01 to the last complete month
  (September 2026), the same split as the SPY test. Nothing is tuned on either; the rule is fixed.
- Known weakness, stated in advance: about 20 years of data, with only one large bear market (2008) in it.

## B. The rule (unchanged from the SPY test, applied to each asset on its own)

At each month-end, for each asset: if its close is **above** the average of its last **10** month-end closes
(including this one), it is "on"; otherwise "off" (ties are off). Prices only, not dividend-adjusted.

- **Target weights:** each asset that is on gets **20%** of the account (1/5). The share of an asset that is off
  sits in cash and earns T-bill interest. Never borrowing; never above 100% invested.
- **Rebalance every month** at the **open of the first trading day after the month-end**, back to exactly those
  targets (Faber rebalances monthly). Nothing happens between month-ends.
- On the start day, targets come from the January 2007 month-end signals.

## C. Benchmarks

- **Equal-weight hold (main benchmark):** 20% in each of the five, always on, rebalanced monthly the same way.
  This shows whether the *timing* adds anything on the same assets.
- **SPY buy-and-hold:** buy once on the start day, reinvest dividends, never sell. This is the owner's realistic
  alternative.
- For information: each asset timed on its own vs. that asset held on its own.

## D. Costs

A fraction of each trade's value, on every buy and sell (including rebalancing trades), for the strategy and the
benchmarks alike. **Primary: 0.05% per side.** Also reported: 0%, 0.10%, and the **stress level 0.25%**. Taxes are
ignored (monthly rebalancing and selling would make them worse for the strategy; stated in the report).

## E. What the report shows

Total return, CAGR, max drawdown (daily values), worst calendar year, Sharpe (monthly returns minus T-bill, × √12),
number of trades, average share invested, for the full, discovery and confirmation periods; a chart of the
strategy, the equal-weight hold and SPY; lookbacks 8–12; results by cost level; each asset timed alone; the
six-asset version with gold; and a monthly log (month-end, each asset's close, average, on/off, target weights,
rebalance day) saved as CSV.

## F. Pass/fail bars (0.05% per side unless stated)

| # | Bar |
|---|---|
| 1 | **Log check:** every trade is on the first trading day after a month-end, and after each rebalance the assets held are exactly the assets that were on. 100% required. |
| 2 | **Timing helps:** full-period Sharpe ≥ the equal-weight hold's. |
| 3 | **Much smaller drawdown:** full-period max drawdown ≤ 0.6 × the equal-weight hold's. |
| 4 | **Not much less return:** full-period CAGR ≥ the equal-weight hold's − 2.0 percentage points. |
| 5 | **Later period:** in the confirmation period, Sharpe ≥ the equal-weight hold's **and** max drawdown < the equal-weight hold's. |
| 6 | **Not fragile:** lookbacks of 9 and 11 months each pass bars 2 and 3. |
| 7 | **Costs:** at 0.25% per side, the 10-month version still passes bar 2. |
| 8 | **Worth it vs. just SPY:** full-period Sharpe ≥ SPY buy-and-hold's **and** full-period max drawdown ≤ 0.6 × SPY's. |

**GO** = all eight pass. The next step would then be a pre-registered paper trade (one decision a month), which is
an owner decision. **NO-GO** = any bar fails; the report says which and why, and nothing is built for trading.
