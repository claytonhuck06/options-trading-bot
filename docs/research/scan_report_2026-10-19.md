# Scanner replay — 2026-10-19

`main.py scan-report`. 32 snapshots every 1 min (15:27–15:59 ET). Risk budget $100 per trade (reference equity $20,000). The entry-time window is **ignored** in this replay so every snapshot is judged on its merits. No orders.

> **Bottom line.** 444 distinct spreads were built around apparent mispricings; **0** passed every rule. Zero trades is a legitimate outcome: nothing in this data was good enough after costs and uncertainty.

## 1. Why candidates were rejected

A candidate can fail several rules; each failure is counted.

| Rule | Failures | What it protects against |
|---|---:|---|
| `costs_exceed_expected_edge` | 559 | the assumed captured edge is smaller than exit spreads plus fees |
| `target_unlikely_in_holding_period` | 552 | under 25% modelled chance of reaching the profit target in 90 min |
| `no_edge_at_executable_prices` | 420 | the mispriced leg looks good, but crossing the other leg's spread erases it |
| `too_large_for_risk_budget` | 306 | even one spread risks more than the per-trade limit |
| `edge_within_model_uncertainty` | 219 | the whole-spread edge is under 2x the model's own uncertainty |
| `reward_too_small` | 210 | maximum profit under $5 per spread |
| `thin_quote` | 205 | fewer than 5 contracts shown on the side we would trade |
| `too_good_to_be_true` | 163 | near-riskless payoff: a glitched or stale quote |
| `wide_leg_spread` | 89 | a leg's bid-ask is over 10% of its price |
| `max_loss_not_positive` | 28 | quotes imply a riskless profit; the data is wrong |
| `stale_quote` | 19 | a leg's quote older than 30 seconds |

## 2. What candidates were worth later (by first rejection reason)

Mean P&L per spread if entered at executable prices on first appearance and closed at executable prices later, after fees. *Δ-adj* removes the profit or loss explained by SPY's move. Small groups are noise.

| Group | Spreads | 5 min (Δ-adj) | 15 min (Δ-adj) | 30 min (Δ-adj) | 90 min (Δ-adj) |
|---|---:|---:|---:|---:|---:|
| thin_quote | 131 | -210.96 (-210.98), n=105 | -242.48 (-241.83), n=47 | -168.45 (-167.91), n=4 | – |
| too_large_for_risk_budget | 100 | -125.28 (-125.20), n=91 | -117.75 (-117.01), n=53 | -157.20 (-154.06), n=2 | – |
| no_edge_at_executable_prices | 56 | -97.68 (-97.89), n=52 | -133.72 (-134.43), n=33 | -98.20 (-100.42), n=2 | – |
| too_good_to_be_true | 55 | -8.34 (-8.31), n=49 | -17.20 (-17.25), n=28 | -6.45 (-2.42), n=4 | – |
| wide_leg_spread | 52 | -4.40 (-4.37), n=40 | -4.52 (-4.03), n=19 | -2.20 (-0.35), n=1 | – |
| edge_within_model_uncertainty | 19 | -37.73 (-36.24), n=17 | -113.11 (-114.84), n=11 | -40.20 (-31.97), n=2 | – |
| stale_quote | 18 | -270.20 (-269.46), n=17 | -265.65 (-264.90), n=11 | – | – |
| max_loss_not_positive | 12 | -32.70 (-32.80), n=12 | -142.40 (-142.93), n=10 | -5.20 (-6.58), n=1 | – |
| reward_too_small | 1 | – | -28.20 (-28.10), n=1 | – | – |

If the rules work, rejected groups should lose money on average (they were rejected for good reason) and eligible spreads should do better. With one partial day of indicative data, treat every number here as a test of the machinery, not evidence.

## 3. Eligible candidates

None.

Closest misses:

```
15:36:11 ET
bearish put debit spread Oct 27: buy 710P @ 0.28 / sell 700P @ 0.26
  Why: SPY261027P00700000 looks rich by $5/contract vs the smile (z=2.0); the long leg caps the risk.
  Net debit $2, max loss $2, max profit $998, fees $0.20 (per spread)
  Model edge $7.55 (z=2.3); expected after costs $+2.58 ± $5.96; target $0.44 reached ~64%
  Exposure per spread: delta -0.8 sh, vega $+1.94/vol pt, theta $-2.59/day; size 45 spread(s)
  Rejected: too_good_to_be_true
```

```
15:30:09 ET
bearish put debit spread Nov 13: buy 705P @ 1.66 / sell 700P @ 1.54
  Why: SPY261113P00700000 looks rich by $20/contract vs the smile (z=2.5); the long leg caps the risk.
  Net debit $12, max loss $12, max profit $488, fees $0.20 (per spread)
  Model edge $23.95 (z=2.0); expected after costs $+10.27 ± $8.13; target $2.44 reached ~83%
  Exposure per spread: delta -1.4 sh, vega $+4.20/vol pt, theta $-1.85/day; size 8 spread(s)
  Rejected: too_good_to_be_true
```

```
15:51:16 ET
bearish put debit spread Oct 29: buy 710P @ 0.56 / sell 700P @ 0.48
  Why: SPY261029P00700000 looks rich by $12/contract vs the smile (z=3.2); the long leg caps the risk.
  Net debit $8, max loss $8, max profit $992, fees $0.20 (per spread)
  Model edge $14.08 (z=2.6); expected after costs $+5.84 ± $10.25; target $1.64 reached ~66%
  Exposure per spread: delta -1.5 sh, vega $+3.52/vol pt, theta $-4.11/day; size 12 spread(s)
  Rejected: too_good_to_be_true
```

## 4. Assumptions to test before trusting this scanner

* `assumed_convergence = 0.5`: half the mispricing is assumed to close before exit. Section 2, over many days, is how this gets measured instead of assumed.
* Profit-target probability uses a normal approximation with SPY moving at the option's implied vol and implied vol itself moving ~0.35 pts per hour (from the 2026-10-19 stability table). Real moves have fatter tails.
* Leg uncertainties are combined as if independent; the shared forward error partly cancels inside a vertical, so spread uncertainty is somewhat overstated (conservative).
* Fees: $0.05 per contract per side, an allowance, not Alpaca's exact fee schedule.
* Sizing uses the configured $20,000 reference equity; the risk engine (Phase 5) will use live account equity and portfolio-level limits.
