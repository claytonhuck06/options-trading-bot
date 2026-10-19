# Finding: the smile-residual signal disappears on real quotes — 2026-10-19

**Data:** Databento OPRA consolidated best bid/offer, one-minute snapshots, 20 trading days
(2026-09-21 to 2026-10-16), SPY options 7–30 days to expiry within ±3.5% of the prior close.
Priced every 15 minutes with the Phase 3 engine (440 snapshots, 327,659 contract-snapshots,
222,406 with no warning flag). Cost of the data used here: about $2.

| Measure | Result |
|---|---|
| Smile fit error (median) | 0.015 vol points |
| Half bid-ask spread (median) | 0.035 vol points |
| Fair value outside the bid–ask | 17.8% of contract-snapshots, median $0.45 per contract |
| Edge ÷ model uncertainty, 99th / 99.9th percentile | 0.28 / 0.64 |
| Contract-snapshots with z ≥ 1 / 1.5 / 2 | 22 / 9 / 3 |
| Candidate spreads built (anchors need z ≥ 2) | 0 |

## What it means

On real national best bid/offer quotes, SPY option prices sit almost exactly on a smooth volatility
smile: the fit misses by less than half the bid-ask spread. When the model's fair value does fall
outside the quote, the gap (median $0.45/contract) is smaller than the cost of trading in and out.
The relative-value signal the system was built around, as currently defined, produces no trades on
real data. The candidates seen on Alpaca's free indicative feed were driven by that feed's quote
errors (it regularly showed prices that allow riskless profits; real quotes showed 1 crossed quote in
306,097 on 2026-10-16).

## Also learned

* The call/put parity check flags ~12% of real quotes near the money. Real American-style options
  deviate from European parity by more than our tolerance; the check needs calibrating on real data
  before it is used to exclude quotes.
* The pricing engine, scanner, risk engine and exit rules all ran cleanly on real data, and the
  backtest engine (next-minute fills at executable prices, no look-ahead) is built.

## Per the master plan

Phase 7's completion requirement is positive net expectancy on unseen data. This strategy cannot meet
it as defined, so the plan's instruction applies: return to research rather than proceed. Choosing a
new strategy is the owner's decision (autonomous strategy changes are prohibited).
