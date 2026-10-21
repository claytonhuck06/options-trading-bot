# Buy Puts on Unexplained Overnight Gaps: Pre-registered Rules and Pass/Fail Bars

*Committed 2026-10-21, before any data was fetched for this study. Paper research only; not investment advice.*

**The idea (owner, 2026-10-21).** Follow-up to the short-the-spike test
([spike_short_backtest_2026-10-21.md](spike_short_backtest_2026-10-21.md), NO-GO because the fading stocks can't
be borrowed). Two changes:

1. **Buy puts instead of shorting.** No borrowing is needed and the most you can lose is what you paid.
2. **Only unexplained jumps.** Screen every stock that opened 30%+ above its previous close, and keep only those
   with **no news** that could explain the jump (earnings, FDA decision, contract, deal, etc.). The theory is that
   a jump with no reason behind it is hype and should fade; a jump with real news may be deserved.

**Why it might not work.**

1. **Puts on these stocks are expensive.** After a 30% jump, option prices build in big future moves. The stock
   has to fall a lot, and fast, for the put to pay.
2. **Wide bid/ask spreads.** Small-stock options often have spreads of 10–30% of the price.
3. **Many of these stocks have no options**, or none that trade.
4. **Short history.** Free historical option prices (Alpaca) begin in February 2024, so the test covers about
   2.7 years. Conclusions will be less certain than the 10-year tests.

Rules for changing anything in this file once results have been seen: you can't. Problems get written down here,
dated, as amendments that say whether any results had been seen.

It lives in its own file (`research/gap_puts.py`). It changes nothing in the live trader, the collector,
`settings.yaml` or `config/risk_limits.yaml`, and it places no orders.

---

## A. Data (all free, Alpaca)

- **Stock prices:** daily bars, SIP feed, raw and split-adjusted, 2023-12-01 to the last completed trading day.
  Universe: every US stock on NYSE, NASDAQ and NYSE American, **active and delisted**, with the same exclusions
  as the spike test (ETFs, funds, trusts, warrants, units, rights, preferreds, leveraged products; symbols must be
  1–5 capital letters).
- **News:** Alpaca's news archive (Benzinga): headline, summary, time and the list of tagged symbols.
- **Options:** Alpaca's option contract list (active and expired) and option trade bars (minute and daily).
  Trades only; historical bid/ask quotes are not free, so spreads are modelled (section D).
- **Benchmark:** SPY, dividends included.

## B. The screen (known by 9:45 am ET on gap day *t*)

A stock is a **gap** on day *t* if:

- Split-adjusted open_t / close_{t−1} − 1 ≥ **+30%** (primary).
- Raw open on *t* ≥ **$1**.
- Median daily dollar volume over the 20 days before *t* ≥ **$1 million**.
- At least 20 trading days of history; the previous bar no more than 5 calendar days earlier.
- First gap day on or after **2024-02-15** (option data start plus a margin).

## C. "No apparent reason" (the news filter)

News window: **9:30 am ET on *t−1* to 9:45 am ET on *t*** (the previous session, the overnight, and the
pre-market, all before the trade).

An article in the window is a **catalyst** if both:

1. It tags **3 or fewer** symbols, including this one (lists of "stocks moving today" tag many symbols and are not
   about the company); and
2. Its headline is **not** a pure price-move headline, **or** its headline or summary contains a catalyst word.
   - *Price-move headlines* match: moving, movers, gainers, losers, trading higher, trading lower, shares are
     trading, shares up, shares down, halted, halt, resume, circuit breaker, session high, session low,
     pre-market, premarket, after-hours, mid-day, midday, spikes, soars, jumps, surges, rallies, climbs, rises,
     falls, tumbles, plunges, sinks, unusual options, volume.
   - *Catalyst words:* announce, announces, announced, earnings, results, revenue, guidance, quarter, FDA,
     approval, approves, approved, clearance, trial, data, study, patent, contract, award, order, agreement,
     partnership, partner, collaboration, acquire, acquisition, merger, merge, buyout, takeover, offer, deal,
     license, launch, launches, upgrade, downgrade, price target, initiates, raises, buyback, repurchase,
     dividend, split, listing, compliance, Nasdaq notice, bankruptcy, offering, financing, private placement,
     investment, stake, 13D, 13G, crypto, bitcoin, treasury, AI, artificial intelligence.

A gap with **no catalyst article** is **unexplained** and is traded. Gaps with a catalyst are tested separately
for comparison only.

**Classifier check (before any option price or stock return is computed):** 40 randomly chosen gaps (seed 1) are
printed with their articles and classification and read by hand. If the rule clearly misclassifies (more than 6
of 40 wrong), the word lists may be fixed once, by dated amendment, still before any returns are seen.

## D. The trade

- **Contract:** puts on the stock expiring **7 to 45 calendar days** after *t* (nearest expiry in that range),
  strike **closest to the open on *t*** (ties: the lower strike). Chosen from Alpaca's contract list using only the
  open price.
- **Entry:** the volume-weighted average price of the contract's trades **9:45–10:30 am ET on *t***, **plus 5%**
  (paying the ask). No trades in that window: no trade (counted and reported as "no tradeable put").
- **Exit (primary):** close of the 5th trading day after *t*: the contract's volume-weighted price that day,
  **minus 5%**. If it didn't trade that day: its intrinsic value (strike − stock close, at least 0) minus 5%
  (a put is worth at least that, so this is conservative). If expiry comes first: intrinsic value at the expiry
  close, no spread charge.
- **Costs:** the 5% each side above (about 10% round trip) plus $0.65 a contract each way. Stress test: 10% each
  side.
- **Sizing:** the project's risk pool: the premium is the whole risk, so each trade spends up to **1.5% of equity**
  (whole contracts; skipped if one contract costs more), at most **3 open**. More than 3 gaps on one day: take the
  biggest gaps first. Starting equity $100,000; profit and loss is booked on the exit day.

## E. Reported

For unexplained gaps (primary) and gaps with news: number of gaps, how many had a tradeable put, trades, win
rate, average and median trade after costs (as % of premium), t-statistic of the average, total return, CAGR, max
drawdown, Sharpe (daily, × √252), and stock-only returns (gap-day open to *t+5* close) for context. Robustness:
exits at *t+1* and *t+10*; gap ≥ +20% and ≥ +50%; double costs. SPY buy-and-hold over the same dates. Every trade
in a CSV; the 10 best and worst; the classifier sample.

## F. Pass/fail bars (unexplained gaps, primary rules)

| # | Bar |
|---|---|
| 1 | **Data check:** for every trade, the news window ends and the contract is chosen before the entry window starts, and the exit is after the entry. 100% required. |
| 2 | **Enough trades:** at least 50. |
| 3 | **Makes money after costs:** total return > 0 and average trade > 0. |
| 4 | **Not luck:** t-statistic of the average trade ≥ 2. |
| 5 | **Worth the risk:** Sharpe ≥ SPY buy-and-hold Sharpe over the same dates. |
| 6 | **Survivable:** max drawdown ≤ 25%. |
| 7 | **Survives double spreads:** total return > 0 at 10% each side. |
| 8 | **Not fragile:** the *t+1* and *t+10* exits each have average trade > 0. |
| 9 | **The news filter matters:** average trade on unexplained gaps > average trade on gaps with news. |

**GO** = all nine pass; the next step would be a pre-registered paper trade (owner decision). **NO-GO** = any bar
fails; the report says which and why.

## G. What this does not cover

- Only about 2.7 years of option history.
- Bid/ask spreads are modelled, not measured; real spreads on these puts may be wider than 5% each side.
- The news filter only sees Benzinga's coverage. Social-media hype, SEC filings that weren't written up, or news
  outside the window aren't seen. A keyword rule will misread some articles.
- Option trades in the entry window may be few; the fill assumes $1,500 of puts could be bought near that price.
- Taxes ignored.

---

## Amendments

*(none yet)*
