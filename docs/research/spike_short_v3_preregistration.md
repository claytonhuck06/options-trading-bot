# Webull Checklist, Simplified: Short at the Open, Cover at the Close — Pre-registered Rules and Pass/Fail Bars

*Committed 2026-10-21, before this version's results were computed. Paper research only; not investment advice.*

**The idea (owner, 2026-10-21).** Same stocks as the checklist test
([spike_short_v2_preregistration.md](spike_short_v2_preregistration.md)), but skip the "wait for the peak" step:
short at the opening price and cover at the closing price.

**Honesty note: this was suggested by data already seen.** While explaining the checklist result, the
average open-to-close move on these stocks was computed (median +19%, mean +9%, before costs, no borrow filter, no
3-a-day limit). So this is a follow-up on a known pattern, not a fresh test; a pass here is weaker evidence than a
pass on an untouched idea, and any next step would need fresh data (a forward paper trade).

Rules for changing anything in this file once results have been seen: you can't. Amendments are dated and say
whether results had been seen. Lives in `research/spike_short_v3.py`; changes nothing live, places no orders.

---

## A. Data and screen

Identical to v2 sections A and B: daily bars incl. delisted stocks, +75% open gap, raw prior close $0.50–$8,
dead company (down 50%+ in a year and below the 200-day average), no catalyst news from 9:30 ET on *t−1* to
9:30 ET on *t*. 2017-01-01 to 2026-10-20.

## B. The trade

- **Short at day *t*'s opening price** (the daily bar's open, taken as the opening auction).
- **Cover at day *t*'s closing price.** Day trade only.
- **SSR skip:** no trade if *t−1*'s low was ≤ 90% of *t−2*'s close (the rule would still be on at *t*'s open).
- **No stop** (primary, as the owner asked). Robustness: a 20% stop on minute highs (out at entry + 20% + 1%
  slippage).
- **Sizing:** without a stop the loss isn't capped, so positions are smaller: **7.5% of equity** per trade (the
  same size the first test used with a 20% stop), at most **3 a day**; more than 3: the 3 biggest gaps.
- **Costs:** v2's tiers by raw opening price (under $2: 2.0%; $2–5: 1.0%; $5+: 0.6%), doubled if the median
  daily dollar volume over the 20 days before *t* was under $5M. Stress: double.
- **Shortable filters:** F1 (primary), F2, F0, exactly as in v2. Live check over the last 120 trading days.

## C. Pass/fail bars (F1, primary)

| # | Bar |
|---|---|
| 1 | **Data check:** screen uses data up to *t−1* plus *t*'s open; news window ends by 9:30 on *t*. 100%. |
| 2 | **Enough trades:** at least 30. |
| 3 | **Makes money after costs:** total return > 0 and average trade > 0. |
| 4 | **Worth the risk:** Sharpe ≥ SPY buy-and-hold. |
| 5 | **Survivable:** max drawdown ≤ 25%. |
| 6 | **Survives double costs:** total return > 0. |
| 7 | **Not fragile:** gap ≥ +50% and ≥ +100% versions each average > 0. |
| 8 | **Squeeze-proof enough:** worst single trade loses no more than 100% of its position (i.e. ≤ 7.5% of the account). |
| 9 | **Actually placeable:** ≥ 25% of the last 120 trading days' signals easy to borrow on Alpaca today, with ≥ 5. |

**GO** = all nine. **NO-GO** = any fails.

---

## Amendments

**2026-10-21, before any v3 result was computed (the first run was stopped during the test suite; no
numbers seen).** Owner asked: if the trade isn't in the green at the close, hold it a few more days. This becomes
the **primary exit**, replacing "cover at the close":

- At day *t*'s close, if the close is **below** the entry (the short is in profit): cover at the close.
- Otherwise hold, and cover at the **first close on *t+1*, *t+2* or *t+3*** that is below the entry; if none,
  cover at *t+3*'s close no matter what. ("2–3 days" → 3 days primary; **2 days** reported as robustness.)
- Holding overnight costs a borrow fee of **20% a year** on the position per calendar night held (doubled in the
  stress test). Real fees on hard-to-borrow stocks are often far higher; this is stated in the report.
- With a 20% stop (robustness): checked each day; if a later day opens above the stop, out at that open.
- Because positions can now last up to four days, the limit becomes **at most 3 open at once** (new ones only
  when a slot is free, biggest gaps first), matching the project's 3-open risk pool. Positions are marked to the
  daily close.
- Also reported: same-day only (the original v3 exit).

Bars are unchanged.
