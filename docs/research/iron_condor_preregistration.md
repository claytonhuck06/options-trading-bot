# Iron Condor — Pre-registered Rules and Pass/Fail Bars

*Committed 2026-10-20, before any condor data was downloaded or examined. Phase 0 of `docs/IRON_CONDOR_PLAN.md`.*
*Machine-readable copy: `docs/research/iron_condor_rules.yaml`. If the two ever disagree, this file wins.*

Rules for changing anything in this file once data has been seen: you can't. If a rule turns out to be
unworkable (for example, the data can't support it), the problem gets written down here, with the date, as an
**amendment**, together with whether any results had been seen at that point. A strategy that fails is reported
as failed. A changed strategy becomes a new, separately pre-registered test.

---

## A. Owner decisions (2026-10-20)

| # | Decision | Value |
|---|---|---|
| 1 | Condor risk | **1% of equity per condor, up to 3 open, 3% total** (replaces the 1 × 3% multi-day pool for condors; same worst case) |
| 2 | Existing mispricing strategy | **Keeps running exactly as it is**, alongside this project, untouched |
| 3 | Pass/fail bars | **As proposed** (section E) |
| 4 | Databento spending | **Up to $20 without asking again**, only in Phase 5, only after Phase 4 is a "go" |
| 5 | Underlyings | **SPY only** |

Decision 1 isn't applied to `config/risk_limits.yaml` yet. It goes in during Phase 6, when the condor risk pool
is built, so the running system isn't affected now.

## B. Data and periods

- **Underlying:** SPY. **Options:** SPY listed options, American-style, $100 multiplier.
- **Discovery period** (used to choose settings): entries from 2016-01-01 to 2021-12-31.
- **Confirmation period** (run once, never used for tuning): entries from 2022-01-01 to the last available day.
- A trade belongs to the period of its **entry date**.
- If the free data doesn't cover all of 2016 onward, the earliest available day starts discovery, provided it
  leaves ≥ 4 years of discovery. This is decided in Phase 1 from coverage alone, before any profit or loss is computed.
- **Snapshot time:** entries and daily exit checks use one snapshot per trading day. For an end-of-day dataset,
  that's whatever time the dataset records, which is documented in the Phase 1 data report. With intraday data
  (Phase 5) it's 10:30 ET.
- Days that fail the data checks (crossed quotes, missing chain, etc.) are skipped for entries. For open
  positions, the last valid mark is carried forward, the check happens the next valid day, and the count of
  such days is reported.

## C. Strategy rules

### C1. Entry
1. **When:** at most one new condor per calendar week. Starting on the week's first trading day, try each trading
   day in turn until a condor is entered or the week ends.
2. **Capacity:** skip if 3 condors are already open.
3. **Expiry:** the listed expiry whose days to expiry (calendar days) is closest to the target DTE, within ±7
   days. A tie goes to the earlier expiry. If none falls within ±7 days, there's no entry today.
4. **Short strikes:** the put whose delta is closest to −Δ and the call whose delta is closest to +Δ. Delta is
   computed by the project's own pricing engine from the quote's mid implied volatility (Black-Scholes-Merton
   with the project's rate and dividend inputs). If the solver refuses a quote, the vendor's delta is used, and
   how often that happened is reported.
5. **Long strikes (wings):** short put − W and short call + W, at exactly that strike. If either strike isn't
   listed, there's no entry today.
6. **Quote checks:** all four legs must pass the existing quote checks (no missing bid, not crossed or locked,
   bid > 0 on the short legs).
7. **Credit:** executable credit = bid(short put) + bid(short call) − ask(long put) − ask(long call). Enter only
   if credit ≥ **25% of W**. If this filter blocks more than half of the otherwise valid entries, that's reported;
   it isn't changed.
8. **Event filter:** skip if an FOMC statement or a CPI release falls within the next 5 trading days, counting the
   entry day.
9. **Size:** contracts = floor(1% × $100,000 ÷ ((W − credit) × 100)). If that's 0, no entry. Sizing uses a fixed
   $100,000 (no compounding) so results are easy to compare.

### C2. Exits (checked at each daily snapshot, in this order)
1. **Stop loss:** loss ≥ k × credit, i.e. cost to close ≥ (1 + k) × credit. When "no stop" is used, the position is held to the other exits.
2. **Profit target:** cost to close ≤ (1 − p) × credit. When "hold" is used, there's no profit target.
3. **Time exit:** days to expiry ≤ T.
4. **Hard exit:** never hold into the final 7 days (this never binds when T ≥ 14, but stays as a guard).
5. **Early-assignment guard:** close the whole condor if a short call is in the money and its time value is
   below the next SPY dividend on the trading day before ex-dividend (existing project logic). Same for a short
   put in the money with time value under $0.05.

**Cost to close** = ask(short put) + ask(short call) − bid(long put) − bid(long call), floored at 0.
Positions are marked daily at this cost to close. Mid prices are never used.

### C3. Costs
- Base case: fills at bid (sells) and ask (buys) as above, plus **$0.05 per contract per leg per side** (the
  project's existing `fee_per_contract`).
- Cost stress case: every leg filled a further **25% of its bid-ask spread** worse, on both entry and exit.
- Reported only, never used to choose settings: an optimistic case filled 25% of the way from bid/ask toward mid.

## D. Test grid and how settings are chosen

### D1. Main grid: 162 combinations (used for selection)
| Setting | Values |
|---|---|
| Target DTE | 30, 45, 60 |
| Short delta Δ | 0.10, 0.16, 0.20 |
| Wing width W | $5, $10 |
| Profit target p | 25%, 50%, hold |
| Stop k | 1.5×, 2×, none |

These stay fixed in the main grid: IV Rank filter **off**, event filter **on**, time exit T = **21**.

### D2. Sensitivity checks: reported only, can't change the choice
Run on the chosen setting only:
- IV Rank filter on: enter only if VIX IV Rank ≥ 30. IV Rank = where the previous day's VIX close sits in its
  trailing 252-trading-day range, from 0 to 100.
- Event filter off.
- Time exit T = 14.
- Intraday stop estimate: stops checked with SPY minute bars and model option prices, labelled "estimated".

### D3. Choosing a setting (discovery period only)
1. Score each combination by its **average profit per trade after costs, as a % of max loss** (so $5 and $10
   wings can be compared).
2. **Smoothed score** = the average of the combination's score and the scores of its grid neighbours (those that
   differ by one step in exactly one setting).
3. The combination with the highest smoothed score is chosen. Combinations with fewer than **60 discovery
   trades** can't be chosen.
4. The chosen combination is then run **once** on the confirmation period.

## E. Pass/fail bars (all must hold)

| # | Bar | Exact test |
|---|---|---|
| 1 | Profitable in both periods, and not by chance | Average profit per trade after costs > 0 in discovery **and** > 0 in confirmation, **and** per-trade t-stat ≥ 2.0 over both periods together. A t-stat on monthly P&L is also reported for information, because up to 3 overlapping trades make the per-trade t-stat look stronger than it is. |
| 2 | Survives worse fills | Average profit per trade > 0 over both periods under the cost stress case (C3) |
| 3 | Losses stay within plan | No trade loses more than its planned max loss + fees + $0.05/share early-assignment slippage |
| 4 | Drawdown | Worst peak-to-trough drop of the $100,000 account at the planned sizing < **10%** (the existing shutdown level) |
| 5 | Crash periods | Reported one by one: Feb 2018, Feb–Apr 2020, 2022, Aug 2024, Apr 2025. None may by itself cause a 10% drawdown |
| 6 | Not a lucky setting | Every grid neighbour of the chosen setting that differs in Δ, DTE or W has a discovery average profit per trade > 0 |
| 7 | Not a few lucky trades | With the best 5% of trades (by profit) removed, the average profit per trade over both periods is still > 0 |

**Result:** if all 7 hold, it's a "go" for Phase 5 (real-quote confirmation, $20 cap). If any fails, it's a "no-go".
The report says which bars failed and by how much, and nothing gets built for trading.

## F. Amendments

*None yet.*
