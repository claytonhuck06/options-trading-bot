# SPY Monthly Trend (10-Month Average): Pre-registered Rules and Pass/Fail Bars

*Committed 2026-10-20, before any return for this strategy was computed. Paper research only; not
investment advice.*

**Where the idea comes from.** The owner pasted a write-up of Meb Faber's monthly trend rule: at each month-end,
own SPY if its close is above its 10-month average, otherwise hold cash. The question is not "did it beat SPY?"
but **did it give a better mix of return and drawdown than simply holding SPY, after costs, and does that
survive small changes and a later period?**

**How this differs from the 2026-10-20 trend test (no-go).** That test bought SPY *call spreads* weekly, gated by
a daily 200-day average and a volatility light. This one holds SPY *shares* or cash, checks only once a month, and
compares against holding SPY shares. It's a different question, so the earlier no-go doesn't decide it.

Rules for changing anything in this file once results have been seen: you can't. Problems get written down here,
dated, as amendments that say whether any results had been seen. A failed strategy is reported as failed.

It lives on its own branch (`monthly-trend`) and its own file (`research/monthly_trend.py`). It changes nothing in
the live trader, the collector, `settings.yaml` or `config/risk_limits.yaml`.

---

## A. Data and periods

- **SPY daily prices:** Yahoo Finance's free daily chart data (open, close, dividends with ex-dates), 1993-01-29
  onward. SPY has never split, so the raw close needs no split adjustment. Saved once to
  `data/storage/studies/` (git-ignored) so every run uses the same numbers.
- **Check on the data:** Yahoo's closes from 2016 on are compared with the SPY minute bars already stored in
  `history.db` (last regular-session bar of each day, turned back into a raw price with the stored factor). The
  median gap and any day off by more than 0.5% are reported. Dividends are checked to be four a year, each
  within a week of the third Friday of March, June, September or December (SPY's schedule).
- **Cash interest:** FRED `DTB3` (3-month Treasury bill, free). Each day's cash earns the latest rate published
  strictly before that day, compounded daily over 252 days a year. (It's a discount-basis yield; the difference is
  tiny and is ignored.)
- **Month-end:** the last trading day of each calendar month in the price data. The current, unfinished month
  is never used.
- **Common start:** the first trading day of **January 1994**. That is the first day on which every lookback
  in the robustness check (8–12 months) has enough history, so all versions and buy-and-hold start the same day
  with the same $100,000.
- **Discovery period:** 1994-01 to 2012-12 (includes the 2000–02 and 2008 bear markets).
- **Confirmation period:** 2013-01 to the last complete month (September 2026). Nothing is tuned on it; the rule
  is fixed in advance, so this is simply the later out-of-sample check the write-up asks for.
- **Full period:** 1994-01 to the last complete month.

## B. The rule

At each month-end *t*, with *P* the SPY closing price (price only, not dividend-adjusted):

- Average_t = mean of the last **N = 10** month-end closes, including *P_t*.
- If *P_t* > Average_t, the target for next month is **SPY**. If *P_t* ≤ Average_t, the target is **cash**.
- If the target differs from what's held, trade at the **open of the next trading day**. Nothing else happens until
  the next month-end. A drop in the middle of the month doesn't trigger anything.
- On the start day, the position is set by the December 1993 month-end signal (bought at the open if it says SPY).
- All in or all out. No borrowing, no shorting, no fractional sizing rules beyond "the whole account".

## C. How the account is valued

- **Holding SPY:** fractional shares. Value = shares × close. On each ex-dividend day the dividend is paid on the
  shares held and immediately reinvested at that day's close (total return).
- **Holding cash:** earns the T-bill rate as in A.
- **Trading cost:** a fraction of the trade's value on every buy and every sell. **Primary: 0.05% per side.**
  (SPY's actual bid/ask spread is about one cent, so this is deliberately cautious.) Also reported: 0%, 0.10%,
  and the **stress level 0.25% per side**.
- **Buy-and-hold:** buys SPY at the open on the start day (paying the same cost once), reinvests dividends, never
  sells.
- Taxes are ignored (they'd hurt the trend rule more, because it sells; this is stated in the report).

## D. What the report shows

For the strategy and buy-and-hold, over the full, discovery and confirmation periods: total return, annual return
(CAGR), maximum drawdown (from daily values), worst calendar year, Sharpe ratio (monthly returns minus T-bill,
× √12), number of trades, share of days invested, and results at each cost level. Also: a chart of both account
values and their drawdowns, lookbacks 8, 9, 11 and 12 months, a count of whipsaws (round trips of 2 months or
less that lost money), and a monthly log (date, close, average, signal, intended trade, fill price, reason for any
gap), saved as a CSV.

## E. Pass/fail bars (all at 0.05% per side unless stated)

| # | Bar |
|---|---|
| 1 | **Log check:** every trade is on the first trading day after a month-end whose signal changed, and every signal change produced exactly one trade. 100% required. |
| 2 | **Better risk-adjusted return:** full-period Sharpe of the strategy ≥ buy-and-hold's. |
| 3 | **Much smaller drawdown:** full-period maximum drawdown ≤ 0.6 × buy-and-hold's. |
| 4 | **Not much less return:** full-period CAGR ≥ buy-and-hold's CAGR − 2.0 percentage points. |
| 5 | **Later period:** in the confirmation period, Sharpe ≥ buy-and-hold's **and** maximum drawdown < buy-and-hold's. |
| 6 | **Not fragile:** lookbacks of 9 and 11 months each pass bars 2 and 3. |
| 7 | **Costs:** at 0.25% per side, the 10-month rule still passes bar 2. |

**GO** = all seven pass. The next step would then be a pre-registered paper trade of the fixed rule (one decision a
month), which is an owner decision. **NO-GO** = any bar fails; the report says which and why, and nothing is
built for trading.

Lookbacks 8 and 12 are shown for information only and can't change the verdict.
