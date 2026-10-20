# Dual Momentum (Top 2 of 5, Each Must Also Pass the 10-Month Trend Test): Pre-registered Rules and Pass/Fail Bars

*Committed 2026-10-20, before any return for this strategy was computed. Paper research only; not
investment advice.*

**Where the idea comes from.** Relative momentum (top 2 of 5 by 12-month return) was a no-go on 2026-10-20
([report](relative_momentum_backtest_2026-10-20.md)): it beat the equal-weight basket on every bar but lost to SPY,
and its worst drop (34%, 2008) came from holding a former leader (commodities) all the way down. The write-up the
owner pasted named the next experiment: keep the ranking, but hold a selected fund only if it also passes a
separate trend test, otherwise hold cash. That's "dual momentum". The owner asked for it.

**The question.** Does adding the trend-to-cash rule improve relative momentum (better risk-adjusted return, a
clearly smaller worst drop, without giving up much return), and does the result then beat holding SPY?

Rules for changing anything in this file once results have been seen: you can't. Problems get written down here,
dated, as amendments that say whether any results had been seen. A failed strategy is reported as failed.

Own file (`research/dual_momentum.py`); reuses the relative momentum test's data, ranking, simulator and
statistics. Changes nothing in the live trader, the collector, `settings.yaml` or `config/risk_limits.yaml`.

---

## A. Assets, data and periods

Exactly as the relative momentum test: SPY, EFA, IEF, VNQ, DBC; the same cached Yahoo data, total-return index for
ranking, FRED `DTB3` cash rates; **the same start (2 April 2007)** with $100,000; discovery to 2012-12,
confirmation 2013-01 to September 2026. Gold versions are information only, as before.

## B. The rule

At each month-end:

1. **Rank** the five by 12-month total return, exactly as the relative momentum test (ties to the first-listed).
2. **Trend test** each of the top two, exactly as the trend tests: on if its month-end close is **above** the
   average of its last **10** month-end closes (prices only; ties are off).
3. Each of the top two that passes gets **50%**. Each one that fails leaves its 50% in **cash** at T-bill
   rates. It is **not** replaced by the third-ranked fund. So the account is 100%, 50% or 0% invested.
4. Rebalance every month at the next trading day's open, back to exactly those targets. No borrowing, no shorts.

## C. Comparisons

- **Relative momentum (main comparison):** the top-2 rule without the cash test, same start and costs. Shows
  whether the extra rule helps, which is what the write-up said to measure.
- **SPY buy-and-hold** and the **equal-weight basket**, as before.
- **Information only (can't change the verdict):** Antonacci's absolute test in place of the 10-month average (a
  top-two fund is held only if its 12-month total return beats T-bills over the same 12 months); the gold versions.

## D. Costs

On every buy and sell, for all versions alike. **Primary 0.05% per side**; also 0%, 0.10% and the **stress level
0.25%**. Taxes ignored (stated in the report).

## E. What the report shows

The same tables as the relative momentum report (full, discovery, confirmation periods; chart; costs; gold), plus
the trend lookbacks 8–12, the share of time in cash, and how many months each fund was blocked by the trend test.
The monthly log (scores, ranks, trend on/off for each fund, what was held, rebalance day) is saved as CSV.

## F. Pass/fail bars (0.05% per side unless stated)

| # | Bar |
|---|---|
| 1 | **Log check:** every trade is on the first trading day after a month-end, and after each rebalance the funds held are exactly the top two that pass the trend test (both re-computed independently). 100% required. |
| 2 | **Extra rule helps on risk-adjusted return:** full-period Sharpe ≥ relative momentum's. |
| 3 | **Clearly smaller worst drop:** full-period max drawdown ≤ 0.8 × relative momentum's. |
| 4 | **Not much less return:** full-period CAGR ≥ relative momentum's − 1.5 percentage points. |
| 5 | **Later period:** confirmation-period Sharpe ≥ relative momentum's. |
| 6 | **Not fragile:** trend lookbacks of 9 and 11 months each pass bars 2 and 3. |
| 7 | **Costs:** at 0.25% per side (both versions), Sharpe ≥ relative momentum's. |
| 8 | **Worth it vs. just SPY (full period):** Sharpe ≥ SPY's **and** max drawdown ≤ SPY's. |
| 9 | **Worth it vs. just SPY (later period):** confirmation-period Sharpe ≥ 0.9 × SPY's. |

**GO** = all nine pass. The next step would be a pre-registered paper trade (one decision a month), an owner
decision. **NO-GO** = any bar fails; the report says which and why, nothing is built for trading, and no further
filters, baskets or lookbacks are tried to rescue it.
