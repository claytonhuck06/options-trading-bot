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
  Reset the paper account to $20,000 in the Alpaca dashboard, or we add an equity cap.
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

## Phase 6 — Paper execution and reconciliation
## Phase 7 — Backtesting (requires suitable historical quote data)
## Phase 8 — Observation mode, then 60–90+ days of paper trading
## Phase 9 — Qwen analyst (Ollama installed; no model pulled yet)
## Phase 10 — Pattern discovery
## Phase 12 — Monitoring, alerts, crash recovery
## Phase 14 — Live readiness review (separate approval)
