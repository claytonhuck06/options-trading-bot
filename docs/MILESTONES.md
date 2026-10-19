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

## Phase 2 — Market data (next)
- [ ] SPY options chain (7–30 DTE): contracts, bid/ask/sizes, timestamps, volume, OI
- [ ] Verify OPRA vs indicative feed access
- [ ] SQLite schema with market + receipt timestamps
- [ ] Data validation (stale, crossed, wide, missing, duplicate)
- [ ] Continuous 15s collector with reconnect and rate-limit handling
- [ ] Several clean trading sessions collected

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
