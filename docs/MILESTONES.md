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
- [x] 85 automated tests
- [x] First live run: 16 clean cycles, 0 failures, ~1,590 quotes per cycle, <1% unusable
- [ ] Collect several complete trading sessions (run `main.py collect` each trading day)

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

## Phase 3 — Pricing engine
- [ ] Black-Scholes, implied volatility, Greeks (with tests vs known values)
- [ ] Volatility surface, residuals, model uncertainty

## Phase 4 — Opportunity scanner and spread builder
## Phase 5 — Risk engine, exits, kill switch
## Phase 6 — Paper execution and reconciliation
## Phase 7 — Backtesting (requires suitable historical quote data)
## Phase 8 — Observation mode, then 60–90+ days of paper trading
## Phase 9 — Qwen analyst (Ollama installed; no model pulled yet)
## Phase 10 — Pattern discovery
## Phase 12 — Monitoring, alerts, crash recovery
## Phase 14 — Live readiness review (separate approval)
