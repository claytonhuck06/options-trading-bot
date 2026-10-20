# Crash test: 5-day $20-wing short iron butterfly — 2026-10-19

**Verdict: failed. The multi-day strategy was not built.**

## In plain English

The short butterfly held for about 5 days looked like our one real edge (+$127 a trade, 90% winners). That
result came from two mistakes in the data. Once both are fixed it makes roughly nothing on average, and in
spring 2025 (the April tariff crash) it lost steadily. The crash itself did not wipe the account, because the
wings cap the loss. But the strategy does not earn enough to justify the risk, so building it would only
automate a loss.

## What was wrong with the earlier numbers

1. **The SPY price history was dividend-adjusted.** The free price feed was stored with every later dividend
   subtracted, so SPY looked about $10–13 lower in 2024–25 than it really traded. Option strikes are real
   prices, so the "at-the-money" butterflies were actually built about 2% away from the money. The strike
   ranges downloaded from Databento were also centred too low.
   *Fixed:* `main.py history` now also stores a per-day factor that converts to the real price
   (`raw_factors` in history.db). The backtest data loader and the Databento strike selection use it. The stored
   bars are unchanged, so the stress tests (which need smooth returns) are unaffected.
2. **Trades where SPY moved a lot were silently dropped.** If a leg's quote was missing at the close (because
   SPY had moved outside the downloaded strikes), the study skipped the trade. For a short butterfly those are
   exactly the losing trades, so the sample kept mostly winners.
   *Fixed:* a missing leg is now priced from the nearest real quotes (implied vol of the nearest
   out-of-the-money strikes, that strike's bid/ask spread, plus $0.05 against us per side). Checked on 825
   real quotes hidden on purpose: typical error $0.01–0.19 per contract. Closing a fly is also capped at
   wing width + $0.20 (one 4-leg order).

## Numbers after the fixes (real bid/ask, $0.40 fees, held 5 trading days, closed 8+ days before expiry)

Per 1 butterfly (the most one can lose is about $2,000 minus the ~$1,300 credit, so about $700):

| | Before fixes | After fixes |
|---|---:|---:|
| Trades measured | 78 | 191 (2025-01-21 to 2026-10-09) |
| Average per trade | +$76 / +$128 | **+$2.57** (t = 0.2, not distinguishable from zero) |
| Discovery / confirmation periods | +$76 / +$128 | **−$35.71** / +$33.94 (fails "positive in both") |
| Win rate | 76–90% | 61% |
| Worst trade | not shown | −$523 (Jul 29 → Aug 5 2026, SPY +5.4%) |
| Spring 2025 (opened Mar 17–May 16) | not covered | 30 trades, **−$3,795 total**, 30% winners |
| Worst crash trade | not covered | −$473 (Apr 2 → Apr 9 2025, SPY −11%) |

At the owner's 3% risk per trade ($3,000 on $100k) the bot would sell 3–7 butterflies (usually 4–5), one
position at a time:

| | Result |
|---|---:|
| Worst single trade | −$2,614 (2.6% of the account) |
| April 2025 crash trades | −$1,709, −$1,915, −$2,366 (opened Mar 28, Mar 31, Apr 2) |
| Worst drawdown, one position at a time | about −$4,500 to −$5,050 (≈5%) |
| Net over Jan 2025 – Oct 2026 | −$2,913 to +$106 depending on start day |

Not reached: the multi-day backtest engine and the daily-loss-limit question (how often the 1.5% account
limit would force an exit). With no edge there is nothing to protect. Under the rule settled in commit f40964c
(daily trigger = 1.5% of equity + the open position's planned max loss, i.e. about $1,500 + $3,000), none of the
losses above (worst −$2,614 at close) would have fired it; the daily, marked-to-market path was not simulated.

## Limits of this test

* Most exits (104 of 191) had at least one leg priced from nearby quotes rather than quoted directly. The
  check above shows the estimate is accurate, but it is still an estimate.
* Oct 2024 – Jan 2025 is not in the sample yet: those days still have only the old, off-centre strike range.
  The overnight download is still filling them, and a queued re-run will refresh the study by itself
  (`logs/opra_widen_rerun.out`, `logs/vol_study_rerun.out`). Spring 2025 is fully downloaded.
* No stop loss was applied. A stop would cut the crash losses, but it cannot create an edge that the
  hold-to-day-5 version does not have.
* The same-day results (sections 1–3 of `intraday_vol_study.md`) were re-run with the corrected prices.
  Their conclusion is unchanged: nothing makes money after costs.

Full tables: section 4 and 4a of [intraday_vol_study.md](intraday_vol_study.md).
