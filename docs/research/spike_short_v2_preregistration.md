# Short the Overextended "Dead Company" Spike (Webull checklist): Pre-registered Rules and Pass/Fail Bars

*Committed 2026-10-21, before any minute bars or news were fetched for this study and before any result was
computed. Paper research only; not investment advice.*

**The idea (owner, 2026-10-21).** The owner shared a step-by-step checklist for shorting overextended penny stocks
and asked for it to be improved and tested. It adds four things the first test
([spike_short_backtest_2026-10-21.md](spike_short_backtest_2026-10-21.md)) didn't have:

1. **Only huge jumps:** up 75%+ overnight.
2. **Only "dead companies":** a long downtrend on the daily chart.
3. **No positive news** behind the jump.
4. **Wait for the peak during the day** (fading momentum, a big red candle) instead of shorting at the open, with
   a **10% stop** and an exit when the fall stalls.

The checklist's fifth step (Webull's quant ratings: growth, income, value) **can't be tested**: those scores are
Webull's own and their history isn't available. The downtrend rule is the stand-in.

**What I added to make it testable and realistic:** exact numbers for every step; the shortable filter from the
first test (most of these stocks can't be borrowed, which is why the owner couldn't trade it live); the
short-sale rule (SSR); costs; and position sizing from the project's risk pool.

Rules for changing anything in this file once results have been seen: you can't. Problems get written down here,
dated, as amendments that say whether any results had been seen.

It lives in its own file (`research/spike_short_v2.py`). It changes nothing in the live trader, the collector,
`settings.yaml` or `config/risk_limits.yaml`, and it places no orders.

---

## A. Data (all free, Alpaca)

- **Daily bars:** the first test's database (`data/storage/studies/spike_bars.db`): raw and split-adjusted, 2016 to
  2026-10-20, active **and delisted** NYSE/NASDAQ/NYSE American stocks that ever closed under $8.
- **Minute bars:** raw SIP minute bars for each candidate's gap day, 9:30–16:00 ET.
- **News:** Alpaca's news archive (Benzinga), same classifier as the gap-puts test
  ([gap_puts_preregistration.md](gap_puts_preregistration.md), section C; hand-checked 37 of 40 right).
- **Benchmark:** SPY with dividends, same dates.

## B. The screen (checklist steps 2–4, known by 9:30 am ET on day *t*)

- **Step 2 — overextended:** split-adjusted open_t / close_{t−1} − 1 ≥ **+75%**. Raw close on *t−1* from $0.50 to
  under $8.
- **Step 3 — dead company:** on *t−1*, the close is **at most half** its value 252 trading days earlier (down 50%+
  over a year) **and** below its 200-day average. Needs 252 days of history; the previous bar no more than 5
  calendar days earlier.
- **Step 4 — no news:** no catalyst article from 9:30 am ET on *t−1* to 9:30 am ET on *t*. Stricter than the
  checklist: *any* company-specific news (good or bad) disqualifies, because a keyword rule can't reliably tell
  good from bad.

## C. Entry (checklist step 7, "wait for the peak"), from minute bars on day *t*

Minute bars are grouped into 5-minute bars (9:30–9:35, 9:35–9:40, …). From the bar ending 9:45 to the bar ending
15:00, track the session high so far. **Short at the close of the first 5-minute bar** that:

- closes at least **10% below the session high so far** (the spike has rolled over), and
- is a red bar (closes below its open: selling pressure), and
- comes after at least **$2 million** traded since the open (enough to fill $15k).

No such bar by 15:00: no trade. **SSR skip:** no trade if any minute before the entry traded at or below 90% of
*t−1*'s close.

## D. Exit (checklist step 8), minute by minute after entry

First of:

1. **Stop:** a minute high ≥ **entry + 10%** → out at entry + 10%.
2. **Fall has stalled:** a 5-minute close ≥ **10% above the lowest price since entry** (only once the price has
   fallen at least 10% from entry; before that, the stop governs) → out at that close.
3. **End of day:** out at the 15:55 bar's close. Always a day trade: no overnight hold, no borrow fee.

## E. Costs, sizing, shortable filters

- **Costs (round trip, by raw entry price):** under $2: 2.0%; $2–5: 1.0%; $5–8+: 0.6%; doubled if less than $5M
  traded before entry. An extra 1% when the stop is hit. Stress test: double.
- **Sizing:** 1.5% of equity at risk with a 10% stop = **15% of equity per trade**, at most **3 trades a day**
  (first three entries by time). $100,000 start, compounding daily.
- **Shortable filters** (same as the first test, section D there): **F1** history-safe estimate from today's
  Alpaca easy-to-borrow flags by price and 20-day dollar volume (primary); **F2** easy to borrow today; **F0** no
  filter (shown for comparison; not tradeable as tested).
- **Live check:** of the F0 signals (steps 2–4) in the last **120** trading days, how many are easy to borrow on
  Alpaca today.

## F. Pass/fail bars (F1, primary rules, 2017 to 2026-10-20)

| # | Bar |
|---|---|
| 1 | **Data check:** every entry is after 9:45 on the gap day and uses only bars up to the entry; screen uses only data up to *t−1* plus *t*'s open; news window ends by 9:30 on *t*. 100% required. |
| 2 | **Enough trades:** at least 30. |
| 3 | **Makes money after costs:** total return > 0 and average trade > 0. |
| 4 | **Worth the risk:** Sharpe ≥ SPY buy-and-hold over the same dates. |
| 5 | **Survivable:** max drawdown ≤ 25%. |
| 6 | **Survives double costs:** total return > 0. |
| 7 | **Not fragile:** gap ≥ +50% and gap ≥ +100% versions each have average trade > 0. |
| 8 | **The checklist's filters help:** average trade with steps 3 and 4 > average trade without them (all +75% gaps, F1, same entry and exit). |
| 9 | **Actually placeable:** at least 25% of the last 120 trading days' F0 signals are easy to borrow on Alpaca today, with at least 5 of them. |

**GO** = all nine pass; next step would be a pre-registered paper trade (owner decision). **NO-GO** = any bar
fails; the report says which and why. Also reported: F0 and F2, each step's effect, every trade.

## G. What this does not cover

- Webull quant ratings (step 5) — no history.
- Pre-market trading (the checklist opens Webull 30 minutes early); the test trades from 9:45.
- Past borrow availability is estimated (F1). Webull's own shortable list may differ from Alpaca's.
- Minute bars are trades only; fills at a 5-minute close plus the modelled cost may be optimistic in a fast stock,
  and halts (common here) aren't modelled.

---

## Amendments

*(none yet)*
