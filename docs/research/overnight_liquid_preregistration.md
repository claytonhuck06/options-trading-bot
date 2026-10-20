# Overnight Drift, Big Liquid Stocks Only: Pre-registered Rules and Pass/Fail Bars

*Committed 2026-10-20, before this version was run. Paper research only; not investment advice.*

**Why this version.** The first overnight test ([overnight_screener_2026-10-20.md](overnight_screener_2026-10-20.md),
NO-GO, 4 of 7 bars) made +26% a year before costs but +6.6% after them, and lost money at double costs. Most of
its picks were small, expensive-to-trade stocks. The owner asked (2026-10-20) to try only the biggest, most heavily
traded names, where costs are lowest.

**What has been seen.** The first test's results, including its screener list, *have* been seen. That list showed
large names (NVDA, TSM, AVGO, SMH, MU) with strong 3-year overnight averages. This is a follow-up on a pattern
already noticed, so a pass here is weaker evidence than a pass on a fresh idea, and the report must say so. To
limit the damage, **everything except the universe is copied unchanged** from
[overnight_screener_preregistration.md](overnight_screener_preregistration.md): same data, definitions, score,
filters, walk-forward years, top 20, comparisons and bars.

## The only change: the universe

- A stock or ETF is eligible only if its **median daily dollar volume over the last 60 trading days of the ranking
  window is at least $1 billion** (measured with data before the holding year, as before). Every eligible name is
  therefore in the 0.02% round-trip cost tier; double costs = 0.04%.
- Leveraged and inverse ETFs stay excluded from the basket.
- The **no-selection basket** becomes every eligible ($1B+) non-leveraged name, equal weight, overnight only.
- If fewer than 40 names qualify in some year, the top 40 basket holds what there is and the report says so (this
  is not a data-check failure for this version; bar 1 still requires no look-ahead).

## Bars (identical to the first test)

| # | Bar |
|---|---|
| 1 | **Data check:** no pick uses data from its holding year; extreme nights (beyond ±50%) listed. |
| 2 | **Makes money after costs:** top-20 basket CAGR > 0. |
| 3 | **Worth the risk:** basket Sharpe ≥ SPY buy-and-hold Sharpe. |
| 4 | **Picking adds value:** basket CAGR > the $1B+ no-selection basket CAGR. |
| 5 | **Consistent:** positive after costs in at least 5 of the 8 years (2019–2026). |
| 6 | **Survives double costs:** at 0.04% round trip, basket CAGR > 0. |
| 7 | **Not fragile:** top 10 and top 40 each pass bars 2 and 3. |

**GO** = all seven. Next step would be a pre-registered paper trade, an owner decision. **NO-GO** = any bar
fails, and the overnight idea is closed unless the owner asks for something new.

**Information only (cannot change the verdict):** the same test with a **$500 million** floor (which mixes the
0.02% and 0.05% tiers), to show how sensitive the result is to where the line is drawn.
