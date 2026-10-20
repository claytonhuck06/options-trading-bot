# Short the Small-Stock Spike: Pre-registered Rules and Pass/Fail Bars

*Committed 2026-10-20, before any price data was fetched for this study. Paper research only; not investment
advice.*

**The idea (owner, 2026-10-20).** Find stocks under $8 that jumped a lot in one day, short them, and profit as the
jump fades. The owner's earlier backtest of this looked good, but in real life most of these stocks could not be
shorted: the broker had no shares to lend. This test asks whether the idea still makes money **when you only take
the trades you could realistically have shorted**.

**Why it might be real.** Big one-day jumps in small stocks are often driven by hype, thin trading and short
squeezes rather than news that changes the company's value. Research on "lottery" stocks and on overreaction finds
these jumps tend to partly reverse.

**Why it might not be tradeable.**

1. **No shares to borrow.** The stocks that fall hardest afterwards are usually the ones nobody will lend. If the
   shortable ones don't fade, there is no strategy.
2. **Costs.** These stocks have wide bid/ask spreads. A 1–2% round-trip cost is normal.
3. **The short-sale rule (SEC Rule 201, "SSR").** After a stock falls 10% from the prior close, for the rest of
   that day and all of the next, you can only short above the current best bid. The test skips any trade where
   SSR would have been in force.
4. **Squeezes.** One stock tripling against you can wipe out many winners. The test uses a stop.

Rules for changing anything in this file once results have been seen: you can't. Problems get written down here,
dated, as amendments that say whether any results had been seen.

It lives in its own file (`research/spike_short.py`). It changes nothing in the live trader, the collector,
`settings.yaml` or `config/risk_limits.yaml`, and it places no orders.

---

## A. Data

- **Prices:** Alpaca daily bars, consolidated SIP feed, 2016-01-01 to the last *completed* trading day. Two
  versions: **raw** (the price actually on the screen that day, used for the "under $8" test) and
  **split-adjusted** (used for every return, so reverse splits don't look like jumps). Open, high, low, close,
  volume. Saved to `data/storage/studies/spike_bars.db` (git-ignored).
- **Universe:** every US stock on NYSE, NASDAQ and NYSE American in Alpaca's list, **active and inactive**
  (delisted). Including delisted stocks mostly fixes the survivorship bias of earlier studies. NYSE Arca and Cboe
  BZX are left out (almost all ETFs). OTC is left out (can't be shorted on Alpaca).
- **Excluded by name or symbol:** ETFs/ETNs/funds/trusts, leveraged or inverse products, warrants, rights, units,
  preferred shares, depositary shares, notes, and any symbol containing "." or "/".
- **Download step:** raw bars for the whole universe; split-adjusted bars only for symbols whose raw close was
  under $8 on at least one day (the only ones that can ever signal).
- **Ticker reuse:** a ticker can belong to different companies at different times. Any day whose previous bar is
  more than 5 calendar days earlier is skipped (no signal, no trade).

## B. The signal (known at the close of day *t*)

A stock signals on day *t* if all of these hold:

- Raw close on *t−1* is under **$8** and at least **$0.50**.
- Split-adjusted close-to-close jump: C_t / C_{t−1} − 1 ≥ **+30%** (primary).
- Dollar volume on *t* (raw close × volume) ≥ **$2 million**.
- At least 20 trading days of history before *t*.

## C. The trade

- **Short at the open of day *t+1*.** No look-ahead: everything in B is known after *t*'s close.
- **SSR skip:** no trade if *t*'s low was ≤ 90% of *t−1*'s close (SSR in force on *t+1*), or if *t+1*'s open is ≤
  90% of *t*'s close (SSR starts at the open).
- **Stop:** a buy-stop 20% above the entry price. If *t+1*'s high reaches it, the trade exits at the stop. On
  later days of a multi-day hold, if the open is already above the stop, the exit is at the open (gap risk).
- **Exit (primary):** cover at the close of *t+1*. This is a day trade: no overnight borrow fee, no overnight
  squeeze.
- **Sizing:** the project's risk pool, 1.5% of equity at risk per trade with a 20% stop, so each position is
  **7.5% of equity**, at most **3 open**. On a day with more than 3 eligible signals, take the 3 with the highest
  dollar volume on *t* (most likely to be shortable). Starting equity $100,000; the account compounds.
- **Costs (round trip, by raw entry price):** under $2: 2.0%; $2–5: 1.0%; $5–8: 0.6%. Doubled if *t*'s dollar
  volume is under $5 million. An extra 1% when the stop is hit (stops fill badly in a spike). Multi-day holds also
  pay a borrow fee of 20% a year on the position, per calendar day held.

## D. The shortable filter (the point of this test)

Alpaca reports whether a stock is easy to borrow **today** only, not in the past. So:

- **F1, history-safe estimate (primary).** From Alpaca's list *today*, take every active stock in the universe
  priced $0.50–$8 and put it in a grid of price band (0.50–1, 1–2, 2–3, 3–5, 5–8) × liquidity band (median daily
  dollar volume over the last 20 trading days: under $1M, $1–5M, $5–20M, $20M+). A grid cell is **shortable** if
  at least **70%** of its stocks are easy to borrow today (cells with fewer than 20 stocks count as not
  shortable). In the backtest, a signal is shortable if its raw *t−1* close and its median dollar volume over the
  20 days *before t* (so the spike itself doesn't count) fall in a shortable cell. The grid is built only from
  today's borrow flags, prices and volumes, never from returns, and is printed in the report.
- **F2, easy to borrow today.** Only stocks that are active and easy to borrow on Alpaca today. A check, not the
  primary: it drops every delisted stock and assumes today's status held in the past.
- **F0, no filter.** The original strategy, shown so the cost of the filter is visible. Not tradeable as tested.
- **Live check.** For every signal in the last 20 completed trading days, is the stock easy to borrow on Alpaca
  right now? This is the most direct evidence of how often the trade can actually be placed.

## E. Comparisons and reports

Reported for F0, F1 and F2: number of trades, win rate, average trade after costs, total return, CAGR, max
drawdown, Sharpe (daily, × √252, no risk-free rate), each year's return, and how often the stop was hit. Also
**SPY buy-and-hold** over the same dates, a chart, the 10 best and 10 worst trades, every trade whose return was
beyond ±100% (possible bad data), and robustness rows: jump ≥ +20% and ≥ +50%; hold to the close of *t+3* and
*t+5*; no stop.

## F. Pass/fail bars (F1, primary rules, first trade date to the last completed day)

| # | Bar |
|---|---|
| 1 | **Data check:** every trade's signal uses only data up to *t*, and its entry and exit are on *t+1* or later. 100% required. |
| 2 | **Enough trades:** at least 100. |
| 3 | **Makes money after costs:** CAGR > 0 and average trade > 0. |
| 4 | **Worth the risk:** Sharpe ≥ SPY buy-and-hold Sharpe. |
| 5 | **Survivable:** max drawdown ≤ 25%. |
| 6 | **Consistent:** positive in at least 70% of calendar years with 10 or more trades. |
| 7 | **Survives double costs:** CAGR > 0 at 2× every cost. |
| 8 | **Not fragile:** the +20% and +50% versions each pass bar 3. |
| 9 | **Actually placeable:** at least 25% of the last 20 trading days' signals (F0 signals, SSR skips removed) are easy to borrow on Alpaca today, with at least 10 such signals. |

**GO** = all nine pass. The next step would be a pre-registered paper trade on Alpaca (owner decision). **NO-GO** =
any bar fails; the report says which and why.

## G. What this does not cover (stated in the report)

- Past borrow availability is estimated, not known (D).
- Daily bars only: the open is assumed fillable for a short at $7.5k, and the stop is assumed to fill at the stop
  price plus the 1% slippage. A real stop in a halted or gapping stock can fill much worse.
- Trading halts (common in these stocks) are not modelled beyond the 5-day gap rule.
- Pattern-day-trader rule: day-trading a margin account needs $25k+ (the paper account has $100k).
- Taxes: every trade is a short-term gain or loss.

---

## Amendments

*(none yet)*
