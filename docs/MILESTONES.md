# Milestones

A phase is done only when its completion requirement is met, not when its week ends.

## Phase 1 — Foundation ✅ (2026-10-19)
Requirement: starts reliably, reads config, connects to Alpaca, cannot submit trades.
- [x] Python 3.13 virtual environment, pinned dependencies
- [x] Folder structure for all planned modules
- [x] One validated configuration; five environments (dev, backtest, observation, paper, live)
- [x] Risk limits in a single version-controlled file, with policy checks
- [x] Live mode requires explicit authorization and is refused in this build
- [x] Credentials read from environment only; never printed or logged
- [x] Console + JSON-lines audit log with secret redaction
- [x] Read-only Alpaca connection (account, positions, orders, clock, SPY price)
- [x] Automated tests (55 passing), including "no order code exists" check
- [ ] Git repository initialised — first commit pending your go-ahead

Findings:
- Paper account equity is $100,000 (plan assumes $20,000) → limits compute 5× larger.
  Owner's decision (2026-10-19): keep $100,000 for paper trading.
- Paper account has options level 3 (spreads allowed).
- Configured feeds are the free ones (IEX stocks, indicative options). Whether your plan
  includes OPRA options quotes is checked in Phase 2.

## Phase 2 — Market data 🟡 (code done 2026-10-19; needs several full sessions)
Requirement: several complete sessions of usable SPY options data, no silent gaps or corrupted records.
- [x] Contract list (7–30 DTE) with type, style, strike, expiry, multiplier, open interest
- [x] Full chain snapshot each cycle: bid/ask, sizes, exchanges, quote time, last trade, IV, Greeks
- [x] SPY bid/ask and last trade each cycle
- [x] SQLite schema: collection_cycles, underlying_quotes, option_contracts, option_quotes, system_events
- [x] Market timestamp + local receipt timestamp on every observation
- [x] Validation flags: missing bid/ask, no bid, crossed, locked, wide spread, zero size, stale, bad/missing timestamp, unknown contract
- [x] Repeated unchanged quotes counted, not stored as fresh observations
- [x] 15s loop, market hours only, exponential backoff, halts after 20 failures in a row
- [x] Strike band (±10% of spot) to keep storage ~1 GB per trading day
- [x] 87 automated tests
- [x] First live run: 16 clean cycles, 0 failures, ~1,590 quotes per cycle, <1% unusable
- [x] Weekday auto-start (launchd, 8:15 CT), waits for open, skips holidays, single instance
- [ ] Collect several complete trading sessions (auto-collecting from 2026-10-20)

Findings:
- **OPRA feed refused** ("OPRA agreement is not signed"). Running on Alpaca's free
  *indicative* feed, which is derived from, but is not, real exchange quotes. Fine for
  building and testing; NOT a sound basis for judging whether trades are executable.
  Real OPRA quotes need a paid Alpaca data plan (decision for later; nothing purchased).
- Alpaca supplies IV and Greeks for ~90% of quotes (all near-the-money). We'll compute our own in Phase 3.
- Open interest is present for ~60% of contracts (newly listed ones have none yet).
- Alpaca lists daily expiries about two weeks out, then weekly ones, so the 7–30 day window
  holds about 8 expiry dates, not 20.
- Near-the-money spreads average ~$0.12 (~1.5%); far strikes ~12%.
- Interest rates / dividend assumptions are deferred to Phase 3, where they are used.

## Phase 3 — Pricing engine 🟡 (built 2026-10-19; stability needs more days of data)
Requirement: pricing passes numerical tests, volatility estimates are stable, the model
does not systematically invent opportunities from bad quotes.
- [x] Black-Scholes-Merton prices and analytic Greeks (Hull textbook values, finite-difference checks)
- [x] Implied volatility solver that refuses impossible prices instead of guessing
- [x] Binomial American pricer; early-exercise premium measured and flagged
- [x] Risk-free rates from FRED (free), previous-day only (no look-ahead), fallback if offline
- [x] Forward and dividend yield implied from put-call parity per expiry
- [x] No-arbitrage quote filters: monotonic, vertical, butterfly, parity
- [x] Per-expiry smile fit (weighted, outlier-robust) with leave-one-out fair values
- [x] Model uncertainty per contract (smile error + model floor + forward noise)
- [x] Edges measured against executable bid/ask, not mid
- [x] Point-in-time snapshots that cannot see future data (tested)
- [x] Results stored: pricing_runs, volatility_surface_fits, pricing_residuals
- [x] Research report: `main.py report` → docs/research/
- [x] Stability through the day and cross-expiry (calendar) consistency in the report
- [x] 140 automated tests
- [x] First report: docs/research/pricing_report_2026-10-19.md (afternoon only: 15:27–16:00 ET)
- [ ] Check smile stability across several full sessions
- [ ] Trading-time (not calendar-time) clock for short expiries

## Phase 4 — Opportunity scanner and spread builder 🟡 (built 2026-10-19; needs full days of data)
Requirement: explainable trade candidates; rejects opportunities that can't realistically be executed profitably.
- [x] Candidates anchored on mispriced contracts (z ≥ 2), paired with clean neighbouring strikes
- [x] Defined-risk vertical spreads only (debit and credit); no uncovered short leg is possible
- [x] Max loss / max profit computed from executable prices plus round-trip fees, before scoring
- [x] Score = expected P&L after convergence haircut, exit spreads and fees ÷ max loss
- [x] Probability of reaching the 20% profit target in the holding period (delta + vega risk)
- [x] Net Greeks per spread; per-trade sizing against the $100 risk budget
- [x] 13 rejection rules, each with a plain-English reason; plain-English explanation per candidate
- [x] Candidates stored in `candidate_trades`; `main.py scan` and `main.py scan-report`
- [x] Replay of 2026-10-19 afternoon: 444 spreads built, 0 eligible; every rejected group lost money later
- [x] 164 automated tests
- [ ] Re-run scan-report over several full days; measure `assumed_convergence` instead of assuming it
- [ ] Correlation with open positions (needs the risk engine, Phase 5)

Note on older data: Alpaca's free history has option trades and 1-minute bars back to Feb 2024, but no historical
bid/ask quotes. The scanner needs bid/ask, so its history comes only from our own collector (or a paid vendor later).

## Phase 5 — Risk engine, exits, kill switch 🟡 (built 2026-10-19; replayed on 10 years of history)
Requirement: tests show the system cannot exceed configured limits and responds correctly to simulated failures.
- [x] All limits in config/risk_limits.yaml (exits, exposure, hedging, kill switch, approval expiry)
- [x] Start-of-day equity snapshot, high-water mark (adjusted for deposits/withdrawals)
- [x] Percentage sizing against per-trade and remaining aggregate budgets; never rounds up
- [x] Risk engine: one decision per request id, approvals expire after 30 s, shrinks or refuses
- [x] Checks: kill switch, entry window, stale data, concurrency, uncovered shorts, daily loss/profit,
      per-trade and aggregate budgets, beta-weighted delta, vega, historical stress loss vs today's allowance
- [x] Exit plan before entry: emergency, market close, stop loss, profit target, mispricing gone, time
- [x] Hedge evaluation: cheapest of SPY-share hedge / partial reduce / close; never raises limits
- [x] Kill switch with 4 levels; HALTED needs an exact authorization phrase to reset
- [x] Drawdown warning pauses entries until a person OKs each day (`main.py killswitch ack-warning`)
- [x] Partial fill leaving a naked short is detected and halts the system
- [x] Historical SPY minute bars 2016–2026 (free SIP feed), `main.py history`
- [x] Stress scenarios calibrated on 2,714 days (worst 2-hour drop −5.8%, 2025-04-07)
- [x] 10-year replay (`main.py risk-replay`): 10,559 trades, none lost more than planned,
      worst day −$94, daily loss limit never reached, engine stood aside on crash days
- [x] 201 automated tests passing (6 skipped edge cases)
- [ ] Replace modelled option prices with real quotes in the replay once a quote history exists

Findings:
- With no edge, ~1,000 trades a year lose ~$6,000 (bid-ask and fees). Frequency does not create profit.
- With 3 positions × ~$100 risk and 50% stops, normal days lose far less than the $300 daily limit;
  that limit is a backstop for gaps and failures.

## Phase 6 — Paper execution and reconciliation 🟡 (built 2026-10-19; live paper demo runs 2026-10-20)
Requirement: orders placed, modified, cancelled, filled and reconciled correctly in paper, including failures.
- [x] One module (`execution/alpaca_paper.py`) may send/cancel orders; hard-wired to the PAPER endpoint and paper keys
- [x] Orders only possible in the paper environment with both trading switches on (now on, per owner)
- [x] Spreads sent as single multi-leg orders (legs fill together); signed net price (+ debit / − credit,
      per Alpaca's order API reference)
- [x] Fresh risk approval re-checked right before every opening order; abandoned if expired
- [x] Limit pricing from mid toward executable, cancel-and-resubmit (no in-place modify), never past the worst price
- [x] Orders written to the database before sending; ambiguous sends looked up, never re-sent
- [x] Partial fills, rejections, lost responses, fill/cancel races, legged fills handled and tested
- [x] Daily transaction-cost cap; consecutive execution failures feed the kill switch
- [x] Reconciliation against broker positions and open orders (broker is the truth); uncovered-short detection
- [x] Crash recovery: check unresolved orders with the broker, reconcile, halt if anything disagrees
- [x] Trading session cycle: reconcile → kill switch → exits (incl. emergency flatten, STOP file) → entries
- [x] Orders go through automatically (no confirmation prompt, per owner); prompt available in settings
- [x] Weekday auto-start at 8:40 CT (`scripts/run_trader.sh`); first run executes the paper demo and only
      trades if it passes
- [x] 236 automated tests passing
- [ ] Paper demo on the real paper account (scheduled for the 2026-10-20 session)

## Owner decisions — 2026-10-19
- Overnight holds are allowed (`allow_overnight_positions: true`), for paper trading only; the config refuses it in live.
  Only positions opened by a multi-day strategy may be held; every other position still closes the same day.
- A multi-day trade may risk up to **3%** of start-of-day equity (`multiday.per_trade_risk_pct: 0.03`), from its own
  risk pool, one position at a time. Same-day trades keep their 0.5% / 2.5% limits.
- Daily loss limit vs. multi-day risk (settled 2026-10-19, owner delegated the call): while a multi-day position is
  open, the account-wide daily loss trigger is 1.5% **plus that position's planned max loss**. Its loss is already
  capped by its defined risk and handled by its own stop/gap-stop, so a bad day for it alone no longer forces
  everything closed at the worst moment. Any other loss on top still trips the 1.5% limit, and the 10% drawdown
  shutdown is unchanged. Worst planned single day: about 4.5% of equity.

## Multi-day readiness (built 2026-10-19; strategy itself waits for the April 2025 crash test)
- [x] Separate multi-day risk pool in the risk engine (3% per trade, 1 open position); positions flagged `hold_overnight`
- [x] Overnight exit rules: never into the final week before expiry; close a short leg likely to be exercised early
      (in the money with less time value than the dividend the day before SPY goes ex-dividend, or with ~no time value)
- [x] Early assignment recognised and handled: assigned leg removed from the book, shares bought/sold back, rest of the
      position closed, P&L kept (premium + share unwind); anything that is not exactly an assignment still halts
- [x] Overnight gaps: today's loss limit is measured from yesterday's close (broker `last_equity`), so a gap counts;
      a position already past its stop at the open is closed as `gap_stop` with a wider allowed price
- [x] Open: stale quotes in the first 5 minutes mean "wait", not "halt"; held positions untouched until the open settles
- [x] Mac asleep (e.g. lid closed for a drive) or a late start: on resuming, old quotes mean "wait 5 minutes for the
      collector", not "halt"; an alert says how long it was away. Network errors are retried instead of crashing the
      trader; after 3 minutes without a connection the owner is alerted, and on reconnect orders and positions are
      re-checked with the broker before trading resumes. Data still stale after the grace period halts as before
- [x] Morning check before the open (trader now starts 8:20 CT): unknown orders settled, overnight assignments
      recognised, broker vs our records, positions carried into the day, missed trading days
- [x] Walk-forward testing (`backtesting/walk_forward.py`) and the standard report (`backtesting/metrics.py`):
      win rate, profit factor, average per trade with t-stat, worst drawdown ($, %, days), Sharpe, Sortino;
      `main.py backtest --walk-forward TRAIN,TEST --grid strategy.KEY=V1,V2`
- [x] Phone alerts via ntfy (free): kill switch, early assignment, gap stop, startup halt, failed demo, collector or
      trader stopping, missed days; one daily summary after the close (`main.py after-close`, launchd 15:20 CT)
- [x] Live-data storage cleanup: newest 5 trading days full detail, then one quote per contract per minute, after 30
      days moved to compressed files (restorable: `main.py storage restore --date D`). Paid Databento data untouched
- [x] 288 automated tests passing
- [ ] Multi-day strategy ($20-wing short iron butterfly, ~5-day hold) — build and backtest only if the crash test passes
- [ ] Multi-day support inside the backtest engine (carry positions across days) — part of the strategy build

## Phase 7 — Backtesting (requires suitable historical quote data)
## Phase 8 — Observation mode, then 60–90+ days of paper trading
## Phase 9 — Qwen analyst (Ollama installed; no model pulled yet)
## Phase 10 — Pattern discovery
## Phase 12 — Monitoring, alerts, crash recovery
## Phase 14 — Live readiness review (separate approval)
