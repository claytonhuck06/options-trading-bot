# Overnight Drift Screener: Pre-registered Rules and Pass/Fail Bars

*Committed 2026-10-20, before any overnight return for any stock was computed. Paper research only; not
investment advice.*

**The idea (owner, 2026-10-20).** Find stocks that rise more *overnight* (close to next open) than they do
*during the day* (open to close). For those stocks, the trade is: buy near the close, sell just after the next
open, and be in cash all day. Repeat every trading day.

**Why it might be real.** This is a well-documented pattern, often called the "overnight drift". Studies of US
stocks over decades (e.g. Cliff, Cooper & Gulen 2008; Lou, Polk & Skouras 2019) found that most of the stock
market's long-run gain arrived overnight, with daytime returns near zero. Explanations include retail buying at
the open, institutions trading during the day, and the risk of holding through news released while the market is
shut.

**Why it might not be tradeable.** Three honest problems, which the test below is built around:

1. **Costs.** This trades twice a day, about 250 days a year: 500 trades. A cost of 0.05% per round trip is
   0.05% × 250 ≈ **12.5% a year** of drag. A typical stock's average overnight gain is a few hundredths of a
   percent. Costs can easily eat it all.
2. **Does it last?** A stock that was "overnight-heavy" over the past three years is only useful if it *stays*
   that way. Looking back and finding stocks that did it is easy; picking them in advance is the real test.
3. **Earnings and news.** Companies announce results after the close, so the biggest overnight moves are
   earnings gaps. A stock's overnight average can be driven by a few big nights rather than a steady drift.

So there are two deliverables: a **screener** (tonight's ranked list, always produced) and a **walk-forward
test** (did picking from the screener in advance actually make money after costs?). The test decides GO/NO-GO.

Rules for changing anything in this file once results have been seen: you can't. Problems get written down here,
dated, as amendments that say whether any results had been seen.

It lives in its own file (`research/overnight_screener.py`). It changes nothing in the live trader, the collector,
`settings.yaml` or `config/risk_limits.yaml`, and it places no orders.

---

## A. Data

- **Prices:** Alpaca daily bars, consolidated SIP feed (free for data older than 15 minutes), adjusted for splits
  **and dividends** (`Adjustment.ALL`). Dividend adjustment is correct here: the price drops on the ex-date at the
  open, and a holder over that night receives the dividend, so the adjusted overnight return is what the holder
  actually earns. 2016-01-01 to the last *completed* trading day (today's unfinished bar is never used).
  Saved once to `data/storage/studies/overnight_bars.db` (git-ignored).
- **The daily bar's open and close** are treated as the price you could get in the opening and closing auctions.
  On SPY this was spot-checked against the stored minute bars (close within about 0.03 dollars). For smaller
  stocks the first trade can differ from the auction price; this is a stated limitation.
- **Universe:** every active, tradable US-listed stock and ETF on Alpaca (NYSE, NASDAQ, NYSE Arca, Cboe BZX,
  NYSE American). No OTC.
- **Known bias (survivorship):** the universe is *today's* listed stocks. Companies that went bankrupt or were
  delisted since 2016 are missing, which flatters any historical test. This can't be fixed with free data and is
  stated in every report. The liquidity filter below is applied using only data available at the time, which
  limits but does not remove the bias.

## B. Definitions

For trading day *t* with adjusted open *O* and close *C*:

- **Overnight return** = O_t / C_{t−1} − 1 (includes weekends and holidays: Friday close to Monday open).
- **Day return** = C_t / O_t − 1.
- A day is skipped for a stock if either bar is missing, either price is ≤ 0, or the gap between the two bars is
  more than 5 calendar days (a trading halt or data hole, not a normal weekend).
- **Liquidity** = median daily dollar volume (close × volume) over the last 60 trading days.
- **Leveraged / inverse ETFs** are detected by name (2x, 3x, Ultra, Bull, Bear, Inverse, Leveraged, -1x etc.).
  They are shown in the screener with a flag but can never enter the test basket (daily rebalancing decay and
  overnight financing make their split different in kind).

## C. Cost model (round trip: buy at close + sell at open)

Auction trades pay no bid/ask spread as such, but large orders move the auction price and there are small
regulatory fees on sales. Costs are set by liquidity tier:

| Median daily $ volume | Round-trip cost |
|---|---|
| ≥ $1 billion | 0.02% |
| $200 million – $1 billion | 0.05% |
| $50 million – $200 million | 0.10% |
| $20 million – $50 million | 0.20% |

Below $20 million or a price under $5: not eligible. The stress test doubles every cost.

## D. The screener (tonight's list)

Ranking window: the last **3 years** (756 trading days) ending at the last completed day. A stock is eligible if
it has bars on ≥ 90% of the window's trading days, last close ≥ $5 and liquidity ≥ $20 million.

For each eligible stock the screener reports:

- Average overnight and average day return (in basis points; 1 bp = 0.01%), and their difference
- Overnight win rate (share of nights up)
- **Trimmed overnight average** (biggest 1% and smallest 1% of nights removed, to blunt earnings gaps) and the
  median night
- **t-statistic** of the after-cost overnight average (how sure we can be it isn't luck; above 2 is the usual bar)
- Years where overnight beat day (out of 3)
- Growth of $1 held only overnight vs only during the day, over the 3 years
- **Break-even cost** (= average overnight return: the round-trip cost at which the trade earns nothing)
- After-cost annual return of the overnight-only trade at its tier cost, and its worst drawdown
- Weeknight vs weekend average; last 12 months' overnight average (is it fading?)
- Cost tier, price, liquidity, leveraged flag

**Score** = t-statistic of (overnight return − tier cost). To be ranked, a stock also needs a positive trimmed
average and overnight beating day in at least 2 of the 3 years. Output: the top 50 in the report, every eligible
stock in a CSV.

## E. The walk-forward test (does picking in advance work?)

- For each calendar year **Y = 2019 … 2026** (2026 runs to the last completed day): rank using only the 3 prior
  calendar years (Y−3 to Y−1), with the eligibility, liquidity and cost tier measured at the end of Y−1. Pick
  the **top 20** by score (excluding leveraged/inverse ETFs).
- During year Y, every trading night, hold the 20 in equal weight: buy at day *t−1*'s close, sell at day *t*'s
  open. Nightly basket return = average of the 20 overnight returns, minus each stock's own tier cost. A stock
  with no bar that night earns 0 in its slot (held as cash). The account compounds nightly. Cash earns nothing
  during the day (conservative).
- **Comparisons, same years:**
  - **SPY overnight-only** at 0.02% round trip
  - **SPY buy-and-hold** (dividends included, no cost)
  - **No-selection basket:** every eligible non-leveraged stock, equal weight, overnight-only at its tier cost.
    This shows whether *picking* adds anything beyond the general overnight effect.
- Reported for each: total return, CAGR, max drawdown, Sharpe (daily, × √252, no risk-free rate), each year's
  return, and the share of nights up. Also: the yearly picks, a chart, and the basket at 0× (no cost) for
  reference.

## F. Pass/fail bars (top 20, standard costs, 2019-01-02 to the last completed day)

| # | Bar |
|---|---|
| 1 | **Data check:** every pick was eligible using data before its year; no basket night uses a future price; no overnight return beyond ±50% enters the basket without being listed in the report. 100% required. |
| 2 | **Makes money after costs:** basket CAGR > 0. |
| 3 | **Worth the risk:** basket Sharpe ≥ SPY buy-and-hold Sharpe. |
| 4 | **Picking adds value:** basket CAGR > no-selection basket CAGR. |
| 5 | **Consistent:** basket return after costs is positive in at least 5 of the 8 years. |
| 6 | **Survives double costs:** at 2× every cost, basket CAGR > 0. |
| 7 | **Not fragile:** top 10 and top 40 baskets each pass bars 2 and 3. |

**GO** = all seven pass. The next step would be a pre-registered paper trade (market-on-close buys, market-on-open
sells on the Alpaca paper account), which is an owner decision. **NO-GO** = any bar fails; the report says which
and why. The screener itself is still produced either way, labelled as research.

## G. What this does not cover (stated in the report)

- Survivorship bias (A).
- Earnings dates are not removed (no free point-in-time earnings calendar); the trimmed average is the stand-in.
- Taxes: every trade is a short-term gain or loss.
- Capacity: auction prices assume orders small relative to the auction; at $100k, 20 names is $5k each.
- Execution timing: market-on-close orders must be in by 3:50 pm ET (NYSE) / 3:55 pm (Nasdaq); the test assumes
  the official close and open were obtained.
