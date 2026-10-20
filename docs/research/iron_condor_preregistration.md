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

### Amendment 1 (2026-10-20): becomes a volatility strategy (condor + long straddle, chosen by a volatility forecast)

*Made at the owner's request, still before any condor, straddle or volatility data was downloaded, computed or
examined. No results existed when this amendment was written.*

The condor rules in sections B–E stay exactly as they are. This amendment adds three things: (1) a volatility
forecast, (2) a long straddle for when the forecast says options are **too cheap**, and (3) an optional forecast
gate on the condor for when it says options are **too expensive**.

#### F1.1 What the forecast has to beat

The options market already makes its own volatility forecast: the implied volatility (IV) built into option
prices. A forecast is only useful for trading if it predicts **future realized volatility better than IV does**.
Being accurate isn't enough on its own. So the forecast is judged against IV, not against nothing.

#### F1.2 The forecast model (HAR-RV with implied volatility)

The model is the standard, widely replicated approach in the research literature: the *heterogeneous
autoregressive model of realized volatility* (Corsi, 2009). Volatility clusters, meaning calm periods tend to stay
calm and wild ones stay wild, so recent daily, weekly and monthly realized volatility predict the next month
well. Implied volatility and a "down days raise volatility" term are added, because published studies find each
adds information.

- **Target:** SPY realized volatility over the next 21 trading days, from the sum of squared 5-minute returns
  (regular hours) plus the squared overnight return, annualised. This uses the minute bars already stored in
  `history.db`, converted to raw prices with `raw_factors`.
- **Inputs, all known at the previous day's close:**
  - log realized volatility over the last 1, 5 and 22 trading days
  - log VIX (previous close)
  - the size of the most recent day's return if it was negative (otherwise 0)
  - the number of FOMC statements and CPI releases in the next 21 trading days
- **Fit:** ordinary least squares on the logs, refit at the start of each month on all data before that month
  (expanding window), with the first fit using ≥ 2 years. Forecasts are always out-of-sample. The minute bars start in 2016, so
  the first forecast is for January 2018. Straddle and gated-condor trades therefore start in 2018, which leaves
  4 years of discovery (2018–2021) and meets B's minimum.
- **Benchmarks:** M0 = VIX alone (scaled to realized volatility by the same expanding regression);
  M1 = HAR-RV without the implied-volatility, return and event terms; M2 = the full model above.
- **Model bar (must hold, or the forecast isn't used):** in the discovery period, M2's out-of-sample QLIKE loss
  is lower than M0's, with a Diebold-Mariano test (Newey-West, 21 lags) one-sided p < 0.05. If M2 fails this,
  the forecast adds nothing beyond what the market already prices in. In that case the straddle and the condor
  gate are reported as "no-go", and the plain condor test (sections B–E) continues unchanged.
- Confirmation-period forecast quality is reported but can't be used to change the model.

#### F1.3 The signal

**Ratio = forecast realized volatility ÷ implied volatility of the trade's own expiry** (the at-the-money IV from
the chain, from the project's pricing engine, at the entry snapshot).

- **Low ratio** (options look expensive): condor territory.
- **High ratio** (options look cheap): straddle territory.
- Options usually price in more movement than happens, so a ratio of 1.0 already means "unusually cheap".

#### F1.4 Condor gate (one extra setting on the chosen condor)

After the condor setting is chosen exactly as in D3 (gate off), the gate is tried on that setting only. With the
gate on, the condor enters only if the ratio is ≤ g, with g ∈ {0.70, 0.80, 0.90}. The gate value with the best
discovery score (D3 step 1) is adopted only if it beats "gate off" in discovery and the gated version still passes
bars 1–7. Otherwise the gate stays off. This is chosen in discovery and then run once on confirmation.

#### F1.5 Long straddle rules

- **Structure:** buy one at-the-money call and one at-the-money put, same strike, same expiry. The strike is the
  one closest to the forward price. Max loss = the debit paid + fees, and no short legs exist.
- **Entry:** checked at each daily snapshot. Enter if the ratio ≥ s, there's no straddle already open, the event
  filter is passed (C1.8), and the quote checks pass (C1.6). Expiry is chosen as in C1.3.
- **Price:** debit = ask(call) + ask(put). Exit value = bid(call) + bid(put). Same fees, cost stress and
  optimistic cases as C3.
- **Exits, checked daily in this order:** stop when value ≤ (1 − L) × debit; profit target when
  value ≥ (1 + q) × debit; time exit after H trading days; hard exit at 7 days to expiry; the early-assignment
  guard doesn't apply (no short legs).
- **Size:** contracts = floor(straddle risk budget ÷ (debit × 100)). The full debit counts as risk. If that's 0,
  no entry. (See owner decision 6 below.)

**Straddle grid: 48 combinations**
| Setting | Values |
|---|---|
| Target DTE | 14, 30 |
| Entry ratio s | 1.00, 1.10, 1.20 |
| Profit target q | 25%, 50% |
| Stop L | 25%, 50% of debit |
| Time exit H | 5, 10 trading days |

Settings are chosen exactly as in D3, with **at least 30 discovery trades** required. The same 7 bars apply
(E1–E7), where "planned max loss" is the full debit plus fees. If the straddle rarely triggers, it fails the
trade-count requirement. That's a valid finding, not something to fix by loosening s.

#### F1.6 Combined portfolio

If both the condor and the straddle pass on their own, they're run together on the confirmation period under the
shared risk caps. The combination must also pass bar 4 (drawdown < 10%) and bar 5 (crash periods).

#### F1.7 Owner decision 6: straddle size (pending)

An at-the-money 30-day SPY straddle costs about $2,000–3,500 per contract, and the whole debit is at risk, so the
1% condor budget ($1,000) can't buy even one. **Proposed:** a straddle may use up to **3%** of equity (the
existing multi-day allowance), one straddle at a time. Total planned risk across all open condors plus a
straddle is capped at **4%**, so a straddle can open only while ≤ 1% of condor risk is open.

### Amendment 2 (2026-10-20): forecast implementation details

*Written before the forecast was run on real data. No forecast results existed at this point.*

1. **Adjusted, not raw, prices for realized volatility.** F1.2 said minute bars would be converted to raw prices.
   That was a mistake: on a raw price series an ex-dividend drop shows up as a fake move. Returns are computed from
   the stored dividend-adjusted bars instead. Raw prices are still used wherever SPY is compared with strikes.
2. **Bias correction.** The models are fitted on logs, so each forecast is converted back with the standard
   log-normal correction exp(2·(fit + s²/2)), where s² is the fit's residual variance. It's applied identically to
   M0, M1 and M2.
3. **First fit.** Forecasts start in January 2018 and the first fit uses all rows whose 21-day outcome had ended
   by then (data from January 2016, i.e. two years of data, about 23 months of finished outcomes).
4. **Events counted:** scheduled FOMC statements (including the scheduled-then-cancelled March 2020 meeting) and
   CPI releases, which are published in advance. Unscheduled meetings are excluded because they weren't known ahead of time.

### Result note (2026-10-20): forecast model bar failed

`docs/research/vol_forecast_report_2026-10-20.md`: in discovery, M2 QLIKE 0.8823 vs M0 (VIX) 0.8809, one-sided DM
p = 0.54. Under F1.2 the straddle (F1.5) and the condor gate (F1.4) are **no-go** and won't be backtested. Sections
B–E (plain condor) are unaffected and continue. Any new straddle idea would be a new, separately pre-registered test.

### Amendment 3 (2026-10-20): settings with too few trades; locked quotes

*Written after the Phase 2 builder report (entry side only: which days a condor can be built, credits at entry)
and before any exit, mark or profit/loss was computed.*

The builder report (`docs/research/condor_build_report_2026-10-20.md`) shows that some settings can rarely or
never be entered: every 10-delta setting fails the 25% minimum credit on all days, and 16-delta settings were
buildable on only 45–60 days in 2016–2021. The original rules didn't say how such settings count as neighbours,
so before Phase 3:

1. **No score without trades.** A setting with fewer than 60 discovery trades gets no score (D3 step 3 already
   stops it from being chosen). In the smoothed score (D3 step 2), it's left out of the average instead of being
   counted as zero.
2. **Bar 6 (not a lucky setting)** applies to the grid neighbours (Δ, DTE or W one step away) that have **at least
   30 discovery trades**. If fewer than **2** neighbours qualify, bar 6 **fails**, because robustness can't be shown.
3. **Locked quotes** (bid = ask > 0) make up 4–16% of out-of-the-money quotes in 2016–2020 and under 0.5% from
   2021. They look like a rounding quirk in the older files. They stay rejected at entry (C1.6). For open
   positions, a leg with a locked, crossed or missing quote fails the daily data check, so the last valid mark is
   carried forward (section B).
4. Nothing else changes. The 25% credit minimum stays as written, even though it blocks most entries at 10 and 16
   delta. That's reported, not adjusted.
