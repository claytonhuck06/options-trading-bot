# Iron Condor Strategy — Project Plan

*Written 2026-10-20. Nothing in this plan has been built yet. Paper trading only; not investment advice.*

The current system looks for mispriced SPY options and has found no tradeable edge. This plan changes the
question to a simpler one, which the existing system is already well equipped to answer:

> **Do SPY options usually price in a bigger move than SPY actually makes, by enough to pay for four legs of
> bid/ask, fees, and the occasional crash?** If yes, trade it on paper with strict limits. If no, say so and don't trade.

The same rules as before apply: tell the truth instead of chasing a nice-looking result, use only data available
at the time of each trade, price fills at the real bid/ask, write every rule and pass/fail bar *before* testing,
and refuse to trade when the evidence isn't there.

---

## 1. How an iron condor works (one paragraph)

Sell a put spread below the price and a call spread above it, all in the same expiry. You collect a credit up
front. If SPY finishes between the two short strikes, you keep it. If SPY moves past either spread, you lose up
to **wing width − credit** (for example, $5 wings with a $1.60 credit risk $340 to make $160). The risk is fully
defined, and the four legs go in as one order. The strategy profits from time passing and from options being
"too expensive". It loses on large, fast moves.

## 2. Hard rules (unchanged from the current project)

1. **Paper only.** Live trading stays refused in code. Going live is a separate, later review (Phase 10).
2. **Defined risk only.** No order may ever leave an uncovered short leg. A partial fill that would leave one halts the system.
3. **No money spent without the owner's OK.** Free data comes first. Any paid download gets a written price quote
   from the existing Databento spend guard, plus owner approval, before it runs.
4. **Local-first.** Runs on this Mac, uses SQLite and the existing schedule jobs, sends free phone alerts (ntfy).
5. **Rules before results.** Strategy rules, parameter grid, and pass/fail bars are written down and committed
   before the backtest runs. Every combination tried is reported, not only the best one.
6. **No look-ahead.** Entry and exit decisions use only data known at that moment (previous close for strike
   ranges, the event calendar as published at the time, and so on).
7. **The broker is the source of truth** for positions and fills, reconciled every cycle (existing behavior).

## 3. Strategy definition (starting rules to test, not conclusions)

| Item | Starting rule | Variants allowed in the test grid |
|---|---|---|
| Underlying | SPY | QQQ, IWM later, only if SPY passes |
| Days to expiry at entry | ~45 | 30, 45, 60 |
| Short strikes | ~16 delta each side | 10, 16, 20 delta |
| Wing width | $5 | $5, $10 |
| Minimum credit | ≥ 25% of wing width, at executable prices | — (below this the trade is skipped) |
| Entry timing | One fixed time daily (for example, 10:30 ET) | — |
| Entry frequency | At most one new condor per week, staggered expiries ("ladder") | — |
| Volatility filter | IV Rank ≥ 30 *or* no filter | on / off |
| Event filter | Skip entries with FOMC or CPI before the first check-in | on / off |
| Profit target | Close at 50% of the credit | 25%, 50%, hold |
| Stop loss | Close at 2× the credit lost | 1.5×, 2×, none (max loss only) |
| Time exit | Close at 21 days to expiry | 21, 14 |
| Hard exit | Never hold into the final 7 days (existing multi-day rule) | — |
| Early-assignment guard | Close a short call that's in the money before ex-dividend (existing logic) | — |

The grid stays small on purpose (roughly 100–200 combinations at most). A large grid finds lucky noise.

**Things to note, not build yet:** SPX/XSP index options (cash-settled, no early assignment) are often preferred
for condors. Whether Alpaca supports them needs to be checked; if not, SPY is fine.

## 4. Risk sizing

- Max loss per condor = (wing width − credit) × 100 × contracts. Sizing is always based on this number.
- The existing multi-day risk pool allows **3% of equity per trade, 1 open position**. A laddered condor needs
  2–4 open at once, so this needs an owner decision (see section 9). Suggested: **1% per condor, up to 3 open,
  3% total**, which keeps the worst case the same as today.
- On the $100,000 paper account, 1% = $1,000, or about 2–3 contracts of a $5-wide condor.
- Portfolio limits stay as they are: net delta, net vega, daily loss limit, 10% drawdown shutdown, kill switch.
- Stress test before every entry: what does every open condor lose on the worst historical moves already
  calibrated (for example, −5.8% in 2 hours on 2025-04-07, plus overnight gaps)?

## 5. Data plan (cheapest first)

| Need | Source | Cost | Status |
|---|---|---|---|
| SPY price history, minute bars, 2016–2026 | Already in `history.db` (dividend-adjusted, with raw-price factors) | Free | ✅ Have it |
| Interest rates | FRED (existing) | Free | ✅ Have it |
| **Daily SPY option chains, 10+ years** (bid, ask, IV, delta, all strikes, 20–70 DTE) | OptionsDX free SPY end-of-day files (**check it's still free**); fallback: CBOE DataShop end-of-day (paid, quote first) | Free → paid only with approval | ❌ Needed |
| Real intraday bid/ask, last 2 years, 25–65 DTE, strikes ±12% | Databento OPRA `cbbo-1m`, **only a 15-minute window around entry time** each day | Small; get an exact quote first | ❌ Needed (current files only cover 7–30 DTE, ±3.5%) |
| VIX history (regime and IV Rank cross-check) | CBOE / FRED (`VIXCLS`) | Free | ❌ Needed |
| FOMC dates | federalreserve.gov calendar, historical | Free | ❌ Needed |
| CPI release dates | BLS release schedule archive | Free | ❌ Needed |
| SPY ex-dividend dates | Existing config rule (third Friday of Mar/Jun/Sep/Dec), checked against history | Free | 🟡 Approximate |
| Live data going forward | Existing collector, with expiry window widened to ~70 DTE | Free | 🟡 Small change |

Data checks before anything is tested (same standard as the current project): crossed or locked quotes,
missing deltas, gaps in trading days, strikes far from the price, early closes and holidays, and a spot check
of 20 random days against a second source.

## 6. Backtest design

- **Engine:** a daily condor engine with optional intraday stop checks using minute bars, reusing
  `backtesting/walk_forward.py` and `backtesting/metrics.py`.
- **Fills:** sell at the bid and buy at the ask by default. Also report "25% of the way from mid" as an
  optimistic case. Never mid alone. Fees per contract per leg (the current model uses $0.40), on entry and exit.
- **Marking:** each open condor valued daily at the cost to close it (executable), not at mid.
- **Stops between daily snapshots:** estimate intraday stops with minute bars and model prices. Label these
  results "estimated" until the Databento intraday data confirms them.
- **Assignment and pin risk:** model early assignment before ex-dividend, and the final-week exit.
- **Periods that must be included:** Feb 2018 (volatility blow-up), Mar 2020 (COVID crash), 2022 (bear market),
  Aug 2024 (volatility spike), Apr 2025 (tariff crash).
- **Walk-forward:** discovery period 2016–2021, confirmation period 2022–2026, never tuned on the confirmation period.

### Pass/fail bars (to be confirmed and committed before the test runs)

A rule set passes only if **all** of these hold:

1. Average profit per trade after costs is above zero in **both** the discovery and the confirmation period, with t-stat ≥ 2 overall.
2. Still profitable with fills 25% worse than bid/ask (a cost stress test).
3. No single trade loses more than its planned max loss (should be guaranteed; it checks the engine).
4. Worst drawdown at the planned sizing is under **10%** of equity (stays inside the existing shutdown).
5. Survives every crash period above without hitting the 10% drawdown shutdown.
6. Neighboring settings also make money (for example, 16 delta passes *and* 10 and 20 delta are close), so the
   result isn't a single lucky combination.
7. Profit isn't concentrated in a few months: removing the best 5% of trades still leaves it above zero.

If nothing passes, the report says so plainly and the strategy isn't built, the same as the multi-day crash test.

## 7. What gets reused vs. what's new

**Reuse (already built and tested):**
- Collector, quote checks, SQLite storage, storage cleanup → widen the expiry window only
- Pricing engine: implied volatility, Greeks, smile fit → used to pick strikes by delta and compute IV Rank
- Risk engine, sizing, stress tests, kill switch, daily limits, multi-day pool, overnight and gap handling
- Paper execution: multi-leg orders, limit pricing, cancel/resubmit, reconciliation, crash recovery
- Early-assignment handling, ex-dividend guard, final-week exit
- Walk-forward framework, metrics report, research report format
- Phone alerts, daily summary, midday check-in, schedule jobs

**New:**
- Daily option chain loader for the free end-of-day data (with data checks)
- Event calendar (FOMC, CPI) and VIX/IV Rank feature table
- `strategies/iron_condor.py`: picks strikes by delta, checks minimum credit, builds the 4-leg order
- 4-leg support across the spread builder, risk checks, and the "no uncovered short" guard (currently 2-leg verticals)
- Condor exit rules: % of credit profit, multiple-of-credit stop, DTE time exit, short-strike-touched alert
- Condor backtest engine plus research report `docs/research/iron_condor_backtest_<date>.md`
- Ladder management: how many open, staggered expiries, total risk across them
- Paper vs. backtest tracking: each paper trade compared with what the backtest would have done

**Pause (keep the code, stop the daily scans):** the relative-value mispricing scanner. It can stay running in
observation-only mode at no cost, or be switched off to simplify the daily summary.

## 8. Phases (each is done only when its requirement is met)

### Phase 0 — Decisions and rules written down ✅ (2026-10-20)
Requirement: section 9 answered by the owner; strategy rules, grid, and pass/fail bars committed to git before any data is examined.

### Phase 1 — Free historical data
Requirement: 10+ years of daily SPY chains loaded, checked, and spot-checked; VIX, FOMC, and CPI tables
filled; a data quality report written. Cost: $0.

### Phase 2 — Condor builder (offline)
Requirement: given any historical day, it builds the condor the rules call for, or explains in plain English why
not (no 16-delta strike, credit too small, event inside window, IV Rank too low). Tests cover strike selection,
max loss math, and the 4-leg uncovered-short guard.

### Phase 3 — Backtest and crash tests
Requirement: full grid run on the discovery period, best *region* picked by the pre-set rules, then run once on
the confirmation period. Report written with every pass/fail bar and an equity curve. Crash periods shown individually.

### Phase 4 — Go / no-go
Requirement: owner reads a short plain-English verdict.
- **No-go:** stop here. Write up what was learned. Optionally try QQQ/IWM or a different exit set as a new,
  separately pre-registered test (not as a tweak to the failed one).
- **Go:** continue to Phase 5.

### Phase 5 — Confirm with real intraday quotes (only if Phase 4 is a go)
Requirement: Databento price quote approved by the owner; a 2-year narrow-window download; the backtest re-run
with real executable entry prices and checked stops. The result must still pass the bars. Cost: small, approved first.

### Phase 6 — Risk engine and execution for 4 legs
Requirement: tests show a condor can't exceed its limits; a 4-leg paper order is placed, filled, cancelled,
partially filled (simulated), and reconciled correctly; a legged fill leaving a naked short halts the system;
early assignment of one short leg is handled. Includes a paper demo like the 2026-10-20 one.

### Phase 7 — Observation mode (2–4 weeks)
Requirement: the system runs live, decides each day what it *would* trade, and logs it without ordering. Daily
summary shows each decision and the reason. Decisions match what the backtest would have done on the same days.

### Phase 8 — Paper trading (60–90+ days, with at least ~10 completed condors)
Requirement: orders run automatically on the $100,000 paper account (per owner preference); each trade is
compared with its backtest twin (fill price, exit, P&L); any gap over a set tolerance is investigated. Kill
switch, phone alerts, gap stops, and assignment handling are exercised at least once in a drill.

### Phase 9 — Monitoring and reporting
Requirement: the daily summary shows open condors (distance to each short strike, P&L vs. credit, days left,
next exit trigger) and alerts when a short strike is touched, a stop or target hits, or an event is approaching.

### Phase 10 — Live readiness review (separate approval, not part of this plan)
Requirement: a written review of paper results vs. backtest, worst cases, and costs. Live trading stays refused
in code until the owner explicitly approves a separate change.

## 9. Owner decisions (answered 2026-10-20)

1. **Condor risk:** up to 3 open at 1% each (3% total). Applied to `risk_limits.yaml` in Phase 6.
2. **Mispricing strategy:** keeps running exactly as it is, alongside this project.
3. **Pass/fail bars:** accepted as written (exact tests in the pre-registration).
4. **Databento cap:** up to $20 in Phase 5 without asking again.
5. **Underlyings:** SPY only.

Exact rules, grid and bars: `docs/research/iron_condor_preregistration.md` (+ `iron_condor_rules.yaml`).

## 10. Honest expectations

- Selling options has historically earned a small premium on average, because options tend to price in more
  movement than happens. But simple, rule-based condors often come out near break-even after four legs of
  bid/ask and fees, and their losses arrive in clumps during crashes.
- Expect most months to be small wins and a few months to be large losses. The backtest must show the strategy
  surviving those months at the planned size, not just averaging positive.
- A "no-go" in Phase 4 is a valid, useful result. The system's job is to find out, not to force a yes.

---

## Ready-to-use build prompt

> Pivot the Options Quant project to test, and only if it passes, paper-trade a rules-based SPY iron condor
> strategy, following `docs/IRON_CONDOR_PLAN.md`. Work inside the existing project and stack (Python 3.13,
> SQLite, Alpaca paper, launchd jobs, ntfy alerts) and reuse existing modules wherever possible. Keep every hard
> rule: paper only, defined risk only, no money spent without my approval and a price quote, no look-ahead, real
> bid/ask fills, rules and pass/fail bars committed before testing. Work phase by phase; a phase is done only
> when its requirement is met. Start with Phase 0: bring me the decisions in section 9 in plain English with your
> recommendation for each, then commit the rules. Stop after Phase 4 and give me a plain-English go/no-go before
> building anything for live paper trading.
