# SPY Trend Call Spreads: Pre-registered Rules and Pass/Fail Bars

*Committed 2026-10-20, before any profit or loss for this strategy was computed. Paper research only; not
investment advice.*

**Where the idea comes from.** The owner asked whether the market-timing part of the open-source project
`yingwang/trade` could be used with options. That project picks momentum stocks and holds them with leverage.
Its "trend" book also uses three **market warning lights** that cut exposure when the market looks unhealthy.
Its own attribution says most of its edge came from market and sector exposure, not from stock picking. So this
test copies only the market-timing part, and applies it to SPY:

> **When SPY is in an uptrend and calm, buy a SPY bull call spread. Close it when the trend breaks.**
> Does this make money after real bid/ask costs, and does the trend signal do better than buying the same
> spread every week regardless?

The second question matters most. SPY rose a lot from 2011 to 2023, so almost any bullish bet made money. A
strategy that only made money because the market went up isn't an edge; it's market exposure with extra costs.
Bar 8 tests this directly.

Rules for changing anything in this file once results have been seen: you can't. Problems get written down here,
dated, as amendments that say whether any results had been seen. A failed strategy is reported as failed.

This project is **separate** from the running mispricing strategy and from the iron condor work. It lives on its
own git branch (`spy-trend`), uses its own files (`strategies/spy_trend.py`, `research/trend_backtest.py`), and
changes nothing in the live trader, the collector, or `config/risk_limits.yaml`.

---

## A. Data and periods

- **Underlying:** SPY. **Options:** SPY listed calls, American-style, $100 multiplier.
- **Option prices:** OptionsDX end-of-day chains already on disk (`vol_history.db`, table `eod_chain`),
  2010-01-04 to 2023-12-29, 4:00 pm ET snapshot, real bid/ask. Free; nothing is downloaded or bought.
- **SPY price for the signal:** the SPY price recorded in the same chain files (checked against SPY's real
  4:00 pm close in the Phase 1 data report: median gap 0.006%). Days with no chain are simply missing from the
  series; nothing is filled in.
- **Discovery period** (used to choose settings): entries from **2011-01-03 to 2018-12-31**.
  2010 is used only to warm up the 200-day average and the 12-month momentum.
- **Confirmation period** (run once, never used for tuning): entries from **2019-01-01 to 2023-12-29**.
  It includes the COVID crash and the 2022 bear market.
- 2024–2026 isn't in the free end-of-day data. A later, separate check on the 2024–2026 OPRA quotes already on
  disk can be pre-registered if this test passes. It can't rescue a failure.
- A trade belongs to the period of its **entry date**. Trades open at the end of 2018 finish normally.
- **Trading days:** SPY trading days from the stored minute history (2016 onward). Before 2016, the chain's
  quote days are used (the Phase 1 report found no non-trading quote days before 2018).

## B. The signal (known at the previous day's close)

Every value uses SPY prices up to and including the **previous trading day's** close. Nothing from the entry
day itself is used, so there's no look-ahead.

| Light | Turns on when |
|---|---|
| **L1 · Below trend** | SPY's close is below its 200-day simple average (the average of the last 200 available closes) |
| **L2 · Too wild** | SPY's 21-day realized volatility is above 30% a year. That's the standard deviation of the last 21 daily log returns × √252 |

The source project's third light (fewer than 30% of its stocks above their 50-day averages) needs a stock
universe. That doesn't exist in a SPY-only system, so it's dropped. That's a deliberate change, written down
here before testing.

**Momentum (only in signal variant "lights + momentum"):** the average of three SPY returns must be above 0:
12 months skipping the last month (252 → 21 trading days ago), 6 months skipping the last month (126 → 21),
and the last 3 months (63 → 0). This copies the source project's blended momentum, applied to SPY alone.

- **"Risk on"** = no light is on (and, in the momentum variant, momentum > 0).
- **"Risk off"** = otherwise.

## C. Strategy rules

### C1. Entry
1. **When:** at most one new spread per calendar week. Starting on the week's first trading day, try each trading
   day in turn until a spread is entered or the week ends. Only on "risk on" days.
2. **Capacity:** skip if 3 spreads are already open.
3. **Expiry:** the listed expiry closest to the target DTE (calendar days), within ±7 days. A tie goes to the
   earlier expiry. If none falls within ±7 days, no entry today. (Same as the condor's C1.3.)
4. **Long call:** the call whose delta is closest to the long delta Δ_L. **Short call:** the call whose delta is
   closest to the short delta Δ_S. It must have a strictly higher strike than the long call, or no entry. Deltas come from the
   project's own pricing engine (Black-Scholes-Merton from each quote's mid implied volatility, with the
   project's rate and put-call-parity dividend inputs). The vendor's delta is used only when the solver refuses a
   quote, and that's counted. (Same code as the condor.)
5. **Quote checks:** both legs have a bid and ask, not crossed or locked, and the short call's bid is above 0.
6. **Price:** executable debit = ask(long call) − bid(short call). No entry if the debit is ≤ 0 or ≥ the
   strike width (impossible prices).
7. **Size:** contracts = floor(1% × $100,000 ÷ (debit × 100)). If that's 0, no entry. Fixed $100,000, no
   compounding. The whole debit is the planned max loss, so the risk is **1% per spread, up to 3 open = 3% in
   total**. That's the same pool and worst case as the owner's 3% decision for multi-day strategies.
8. No event filter (FOMC/CPI). A trend-following bet has no stated reason to avoid them, and leaving it out
   means one less setting to tune.

### C2. Exits (checked at each daily 4:00 pm snapshot, in this order)

**Value** = bid(long call) − ask(short call), floored at 0 (what selling the spread would actually get).

1. **Stop loss:** value ≤ (1 − L) × debit. With "no stop", skipped.
2. **Profit target:** value ≥ (1 + q) × debit. With "hold", skipped.
3. **Trend exit:** "risk off" (computed from the previous close, same as entry). This is the strategy's
   core rule, so it's always on.
4. **Time exit:** days to expiry ≤ 14.
5. **Hard exit:** days to expiry ≤ 7 (a guard; it can't bind before the time exit, but stays).
6. **Early-assignment guard:** on the trading day before a SPY ex-dividend date, close if the short call is in
   the money and its time value is below the dividend (existing project rule, same as the condor).

If either leg fails the data check on a day, there's no exit that day. The last valid value is carried forward,
the check happens the next valid day, and the count is reported. At expiry with no usable quote, the
spread settles at intrinsic value: min(max(0, S − K_long), K_short − K_long).

### C3. Costs
- Fills at the bid/ask as above, plus **$0.05 per contract per leg per side** (the project's fee).
- **Cost stress case:** every leg fills a further 25% of its bid-ask spread worse, on entry and on exit.
- **Optimistic case** (reported only): 25% of the way toward mid.

## D. Test grid and how settings are chosen

### D1. Main grid: 144 combinations
| Setting | Values |
|---|---|
| Signal | lights only · lights + momentum |
| Target DTE | 30, 45, 60 |
| Long call delta Δ_L | 0.50, 0.70 |
| Short call delta Δ_S | 0.20, 0.30 |
| Profit target q | 50%, 100% of debit, hold |
| Stop L | 50% of debit, none |

### D2. Choosing a setting (discovery period only)
Same as the condor's D3, with the same Amendment 3 smoothing. That way nothing new can be tuned.
1. Score = **average profit per trade after costs ÷ planned max loss** (the debit plus fees).
2. Smoothed score = the average of the setting's score and the scores of its grid neighbours that have one.
   Neighbours are settings that differ by one step in exactly one setting. Signal counts as a setting with two values.
3. The highest smoothed score wins. Settings with fewer than **60 discovery trades** can't be chosen or score.
4. The chosen setting is run **once** through the confirmation period.

### D3. Reported only (can't change the result)
- The chosen setting with the momentum part switched to the other signal variant.
- **"Market-exposure split"** (the lesson from the source project's attribution): for each trade, what SPY
  shares with the same dollar delta at entry would have made over the same holding period, and what's left
  over. If the leftover is ≤ 0, the profit was just market exposure.
- What buying and holding SPY did over each period, for context.

## E. Pass/fail bars (all must hold)

| # | Bar | Exact test |
|---|---|---|
| 1 | Profitable in both periods, and not by chance | Average profit per trade after costs > 0 in discovery **and** in confirmation, **and** per-trade t-stat ≥ 2.0 over both periods together. The monthly-P&L t-stat is also reported, for information. |
| 2 | Survives worse fills | Average profit per trade > 0 over both periods under the cost stress case |
| 3 | Losses stay within plan | No trade loses more than its debit + fees + $0.05/share assignment slippage |
| 4 | Drawdown | Worst peak-to-trough drop of the $100,000 account < **10%** (the existing shutdown level) |
| 5 | Crash periods | Reported one by one: Aug–Sep 2011, Aug 2015–Feb 2016, Feb 2018, Oct–Dec 2018, Feb–Apr 2020, 2022. None may by itself cause a 10% drawdown. (Aug 2024 and Apr 2025 need the later data.) |
| 6 | Not a lucky setting | At least 2 grid neighbours that differ in DTE, Δ_L or Δ_S and have ≥ 30 discovery trades, and every such neighbour has a discovery average profit per trade > 0 |
| 7 | Not a few lucky trades | With the best 5% of trades removed, the average profit per trade over both periods is still > 0 |
| 8 | **The trend signal earns its keep** | The chosen setting run with **no signal** (enter every week; no trend exit; everything else identical) must have a **lower** average profit per trade than the real strategy in discovery **and** in confirmation |

**Result:** all 8 pass → "go" for the next pre-registered step (the 2024–2026 real-quote check, then paper).
Any fail → "no-go": the report says which bars failed and by how much, and nothing gets built for trading.
