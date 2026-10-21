# Checklist Short, Enhanced (v4): Squeeze Cap, Risk-Pool Sizing, IBKR Borrow — Pre-registered Rules and Bars

*Committed 2026-10-21, before this version's results were computed. Paper research only; not investment advice.*

**The idea (owner, 2026-10-21).** The owner re-shared the Webull checklist and asked for it to be enhanced into a new
strategy. Two versions are already tested ([v2](spike_short_v2_backtest_2026-10-21.md),
[v3](spike_short_v3_backtest_2026-10-21.md)). What they showed:

- **The stocks are right.** The checklist screen (+75% gap, dead company, no news) works: shorting at the open and
  covering at the first green close made +15.9% a trade before borrow limits (v3, no shortable filter).
- **It breaks in two places.** (1) A few squeezes lose more than the whole position (SDOT −239%, IXHL −126%), and a
  tight 20% stop kills the edge (+3.3%/trade, 45% stopped out). (2) Alpaca won't lend these shares (0 of 35 recent
  signals), but Interactive Brokers' free shortable list showed shares on 33 of 35, at high fees.

**The enhancements (v4):**

1. **Squeeze cap:** a wide **50% stop** instead of none or 20% — loose enough to sit through the normal morning
   pop, tight enough to stop a −239% day.
2. **Risk-pool sizing:** the project's 1.5%-at-risk rule ([equal risk pools]) applied to the 50% stop:
   **2.7% of equity per short**, at most 3 open. A stop hit costs about 1.5% of the account.
3. **Real hard-to-borrow costs** for a locate-capable broker (IBKR): a **1% locate fee** on every trade (even a day
   trade) and **150%/yr borrow** per night held (IBKR's recent median is ~120%).
4. **The placeable test uses IBKR's list**, not Alpaca's, because that's where it would actually trade.

**Honesty note: this is the third look at the same data.** v3's no-filter results with no stop and with a 20% stop
are already known; the 50% stop, the new sizing and the new costs have not been run. A pass is weak evidence, and
the only thing it can earn is a **forward paper (shadow) test** — logging each morning's signals and what the trade
would have done, with IBKR's real borrow availability that morning. No real money. Bar 9 was in effect already
known to pass (33 of 35 on today's list); it's kept so the report says so plainly.

Rules for changing anything in this file once results have been seen: you can't. Amendments are dated and say
whether results had been seen. Lives in `research/spike_short_v4.py`; changes nothing live, places no orders.

---

## A. Data and screen

Identical to v3: daily bars incl. delisted stocks; +75% open gap (split-adjusted open_t / close_{t−1}); raw prior
close $0.50–$8; dead company (down 50%+ in a year and below the 200-day average); no catalyst news from 9:30 ET on
*t−1* to 9:30 ET on *t*; SSR skip if *t−1*'s low was ≤ 90% of *t−2*'s close. 2017-01-01 to 2026-10-20.
**No Alpaca shortable filter** (the venue is assumed to be IBKR).

## B. The trade

- **Short at day *t*'s open.** Cover at the first close (*t*, *t+1*, *t+2*, *t+3*) below the entry; otherwise at
  *t+3*'s close. (v3's primary exit.)
- **50% stop:** level = entry × 1.50. On *t*: if the day's high ≥ level, out at the level. On later days: if the
  open ≥ level, out at the open; else if the high ≥ level, out at the level. **2% slippage** added on a stop.
- **Sizing:** **2.7% of equity** per short (1.5% ÷ (50% + 2% + ~3% costs)), at most **3 open at once**, biggest gaps
  first.
- **Costs:** v3's trading tiers (under $2: 2.0%; $2–5: 1.0%; $5+: 0.6%; doubled if 20-day median dollar volume <
  $5M), **plus 1% locate**, **plus 150%/yr borrow** per calendar night. **Stress:** all of it doubled (2% locate,
  300%/yr).

## C. Pass/fail bars

| # | Bar |
|---|---|
| 1 | **Data check:** screen uses data up to *t−1* plus *t*'s open. 100%. |
| 2 | **Enough trades:** at least 30. |
| 3 | **Makes money after costs:** total return > 0 and average trade > 0. |
| 4 | **Worth the risk:** Sharpe ≥ SPY buy-and-hold. |
| 5 | **Survivable:** max drawdown ≤ 25%. |
| 6 | **Survives double costs** (incl. 2% locate, 300%/yr borrow): total return > 0. |
| 7 | **Not fragile:** gap ≥ +50% and ≥ +100% versions each average > 0. |
| 8 | **Squeeze cap works:** no single trade costs more than **3% of the account** (2× the planned 1.5%). |
| 9 | **Placeable at IBKR:** ≥ 50% of the last 120 trading days' signals (and ≥ 5) show at least a full position's shares available on IBKR's public shortable list today. |

**GO** = all nine → start the forward shadow test. **NO-GO** = any fails.

**Robustness rows (reported, not bars):** stop at 30% / 75% / 100% / none (each re-sized to 1.5% at risk; "none"
at v3's 7.5%); no locate fee; same-day cover only.

## D. Forward shadow log (built whatever the verdict)

Whatever the backtest says, the open question that history can't answer is **whether IBKR has shares on the spike
morning itself**. A small morning job will screen that day's checklist candidates, record IBKR's availability and
fee at that moment, log the trade the rules would take (no orders anywhere), close it by the same exit rules, and
send a phone alert. This is research data, not a trading signal, unless the backtest is GO.

---

## Amendments

*(none yet)*
