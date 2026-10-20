# Intraday volatility-pattern study — 2024-10-21 to 2026-10-16

Real SPY option quotes (Databento OPRA consolidated best bid/offer, 1-minute), 499 trading days. Discovery = days before 2026-02-19; confirmation = 2026-02-19 onward (never used to pick anything). Research only — no trading rule has been changed.

> **Bottom line.** No entry/exit combination made money after real bid/ask costs in both periods (0 of 80 looked significant in discovery alone; by chance alone roughly 2 would). The intraday pattern, if any, is smaller than the cost of trading it with these structures.

## 1. How implied volatility moves during the day

Average change in 14-day at-the-money implied volatility since 9:45 ET, in vol points:

| Period | 10:30 | 12:00 | 13:00 | 14:00 | 15:00 | 15:30 | 15:55 |
|---|---:|---:|---:|---:|---:|---:|---:|
| discovery | -0.05 (±0.08) | -0.10 (±0.13) | -0.08 (±0.15) | -0.09 (±0.15) | -0.21 (±0.16) | -0.14 (±0.18) | -0.15 (±0.19) |
| confirmation | -0.14 (±0.10) | -0.22 (±0.15) | -0.21 (±0.16) | -0.23 (±0.17) | -0.22 (±0.18) | -0.21 (±0.19) | -0.23 (±0.19) |

![intraday IV](img/intraday_iv_path.png)

A curve that drifts the same way in both periods, with its shaded band clear of zero, is a real tendency. Whether it pays depends on section 2. Note: implied vol here uses calendar time, which counts the hours after the close; that alone pushes measured IV slightly up through the day, so a gentle rise is partly a measurement effect.

## 2. Could you have traded it? Iron butterflies at real bid/ask

Short fly = sell the at-the-money call and put, buy wings $5 or $20 away (profits if options lose value). Long fly = the reverse. Nearest expiry at least 7 days out. Entered and exited at executable prices, $0.40 fees per round trip, per 1 fly ($ per contract set). *Gross* is the short fly at mid prices, before costs.

| Wings | Entry → exit | Side | Discovery n | Mean $ | t | Confirmation n | Mean $ | t | Gross (mid) $ | Holds in both? |
|---:|---|---|---:|---:|---:|---:|---:|---:|---:|:---:|
| $5 | 09:45 → 10:30 | short | 318 | -31.91 | -9.3 | 161 | -25.72 | -7.6 | +0.32 | – |
| $5 | 09:45 → 10:30 | long | 318 | -32.77 | -9.6 | 161 | -25.92 | -8.4 | +0.32 | – |
| $5 | 09:45 → 12:00 | short | 318 | -31.03 | -9.1 | 161 | -27.20 | -8.0 | +0.33 | – |
| $5 | 09:45 → 12:00 | long | 318 | -32.15 | -9.6 | 161 | -26.93 | -8.6 | +0.33 | – |
| $5 | 09:45 → 13:00 | short | 318 | -32.02 | -9.4 | 161 | -27.83 | -8.0 | +0.39 | – |
| $5 | 09:45 → 13:00 | long | 318 | -33.57 | -9.9 | 161 | -27.12 | -8.7 | +0.39 | – |
| $5 | 09:45 → 14:00 | short | 313 | -45.02 | -8.9 | 161 | -37.19 | -7.9 | +0.32 | – |
| $5 | 09:45 → 14:00 | long | 313 | -46.81 | -8.8 | 161 | -35.57 | -7.9 | +0.32 | – |
| $5 | 09:45 → 15:00 | short | 313 | -33.12 | -9.5 | 161 | -29.62 | -8.7 | -0.28 | – |
| $5 | 09:45 → 15:00 | long | 313 | -33.23 | -10.0 | 161 | -27.73 | -8.9 | -0.28 | – |
| $5 | 09:45 → 15:30 | short | 313 | -32.70 | -9.5 | 161 | -29.56 | -8.5 | -0.23 | – |
| $5 | 09:45 → 15:30 | long | 313 | -33.12 | -10.0 | 161 | -27.41 | -8.8 | -0.23 | – |
| $5 | 10:30 → 12:00 | short | 319 | -15.64 | -19.4 | 161 | -17.52 | -23.0 | +0.50 | – |
| $5 | 10:30 → 12:00 | long | 319 | -17.19 | -18.3 | 161 | -17.44 | -25.6 | +0.50 | – |
| $5 | 10:30 → 13:00 | short | 319 | -16.95 | -15.2 | 161 | -18.11 | -22.7 | +0.19 | – |
| $5 | 10:30 → 13:00 | long | 319 | -18.01 | -16.8 | 161 | -17.12 | -26.3 | +0.19 | – |
| $5 | 10:30 → 14:00 | short | 314 | -30.94 | -7.6 | 161 | -27.44 | -7.6 | +0.04 | – |
| $5 | 10:30 → 14:00 | long | 314 | -32.33 | -7.9 | 161 | -24.95 | -7.3 | +0.04 | – |
| $5 | 10:30 → 15:00 | short | 314 | -18.36 | -16.4 | 161 | -20.23 | -21.5 | -0.19 | – |
| $5 | 10:30 → 15:00 | long | 314 | -18.74 | -19.3 | 161 | -18.36 | -21.9 | -0.19 | – |
| $5 | 10:30 → 15:30 | short | 314 | -17.90 | -16.9 | 161 | -20.41 | -19.3 | -0.36 | – |
| $5 | 10:30 → 15:30 | long | 314 | -17.82 | -19.3 | 161 | -18.42 | -21.1 | -0.36 | – |
| $5 | 12:00 → 13:00 | short | 320 | -16.46 | -15.2 | 162 | -15.71 | -27.5 | -0.07 | – |
| $5 | 12:00 → 13:00 | long | 320 | -16.52 | -15.2 | 162 | -15.17 | -28.5 | -0.07 | – |
| $5 | 12:00 → 14:00 | short | 315 | -30.35 | -7.6 | 162 | -25.43 | -7.0 | -0.05 | – |
| $5 | 12:00 → 14:00 | long | 315 | -30.48 | -7.6 | 162 | -24.86 | -6.6 | -0.05 | – |
| $5 | 12:00 → 15:00 | short | 315 | -18.94 | -13.3 | 162 | -19.46 | -19.0 | -0.88 | – |
| $5 | 12:00 → 15:00 | long | 315 | -17.42 | -17.5 | 162 | -17.26 | -22.6 | -0.88 | – |
| $5 | 12:00 → 15:30 | short | 315 | -18.58 | -13.6 | 162 | -18.59 | -20.6 | -0.60 | – |
| $5 | 12:00 → 15:30 | long | 315 | -17.35 | -15.4 | 162 | -17.45 | -21.0 | -0.60 | – |
| $5 | 13:00 → 14:00 | short | 312 | -32.58 | -7.1 | 161 | -24.21 | -6.9 | +0.34 | – |
| $5 | 13:00 → 14:00 | long | 312 | -33.73 | -6.8 | 161 | -23.98 | -6.7 | +0.34 | – |
| $5 | 13:00 → 15:00 | short | 312 | -18.29 | -12.8 | 161 | -17.17 | -22.7 | -0.60 | – |
| $5 | 13:00 → 15:00 | long | 312 | -17.10 | -16.8 | 161 | -15.95 | -25.3 | -0.60 | – |
| $5 | 13:00 → 15:30 | short | 312 | -17.95 | -14.0 | 161 | -16.65 | -21.1 | -0.37 | – |
| $5 | 13:00 → 15:30 | long | 312 | -17.01 | -15.3 | 161 | -16.31 | -24.0 | -0.37 | – |
| $5 | 14:00 → 15:00 | short | 313 | -29.93 | -7.5 | 161 | -24.52 | -6.2 | -0.01 | – |
| $5 | 14:00 → 15:00 | long | 313 | -30.33 | -7.4 | 161 | -23.65 | -6.0 | -0.01 | – |
| $5 | 14:00 → 15:30 | short | 313 | -29.64 | -7.8 | 161 | -24.58 | -6.0 | +0.14 | – |
| $5 | 14:00 → 15:30 | long | 313 | -30.20 | -7.5 | 161 | -24.33 | -6.0 | +0.14 | – |
| $20 | 09:45 → 10:30 | short | 124 | -26.06 | -6.8 | 103 | -13.45 | -5.0 | +0.44 | – |
| $20 | 09:45 → 10:30 | long | 124 | -21.28 | -6.1 | 103 | -21.17 | -8.0 | +0.44 | – |
| $20 | 09:45 → 12:00 | short | 122 | -20.97 | -4.6 | 103 | -12.66 | -3.0 | +3.51 | – |
| $20 | 09:45 → 12:00 | long | 122 | -25.33 | -6.2 | 103 | -22.85 | -6.2 | +3.51 | – |
| $20 | 09:45 → 13:00 | short | 124 | -23.12 | -4.5 | 103 | -16.89 | -3.4 | +1.19 | – |
| $20 | 09:45 → 13:00 | long | 124 | -25.16 | -5.6 | 103 | -19.68 | -4.2 | +1.19 | – |
| $20 | 09:45 → 14:00 | short | 116 | -21.68 | -3.7 | 102 | -23.74 | -3.7 | +2.06 | – |
| $20 | 09:45 → 14:00 | long | 116 | -28.24 | -5.5 | 102 | -25.10 | -3.9 | +2.06 | – |
| $20 | 09:45 → 15:00 | short | 123 | -24.87 | -3.7 | 103 | -22.72 | -3.6 | -0.62 | – |
| $20 | 09:45 → 15:00 | long | 123 | -27.50 | -5.2 | 103 | -16.86 | -2.8 | -0.62 | – |
| $20 | 09:45 → 15:30 | short | 123 | -29.94 | -4.0 | 103 | -23.03 | -3.4 | -3.93 | – |
| $20 | 09:45 → 15:30 | long | 123 | -22.21 | -3.6 | 103 | -15.02 | -2.4 | -3.93 | – |
| $20 | 10:30 → 12:00 | short | 128 | -11.70 | -5.0 | 109 | -12.17 | -4.3 | +2.52 | – |
| $20 | 10:30 → 12:00 | long | 128 | -18.97 | -8.8 | 109 | -14.58 | -5.7 | +2.52 | – |
| $20 | 10:30 → 13:00 | short | 129 | -16.92 | -5.2 | 109 | -14.64 | -4.2 | -0.55 | – |
| $20 | 10:30 → 13:00 | long | 129 | -16.04 | -5.7 | 109 | -13.26 | -4.3 | -0.55 | – |
| $20 | 10:30 → 14:00 | short | 125 | -16.30 | -4.6 | 108 | -21.18 | -4.3 | +0.37 | – |
| $20 | 10:30 → 14:00 | long | 125 | -18.79 | -4.6 | 108 | -19.90 | -4.1 | +0.37 | – |
| $20 | 10:30 → 15:00 | short | 129 | -18.57 | -3.6 | 109 | -17.24 | -4.1 | -1.22 | – |
| $20 | 10:30 → 15:00 | long | 129 | -17.16 | -4.2 | 109 | -13.57 | -3.4 | -1.22 | – |
| $20 | 10:30 → 15:30 | short | 129 | -21.35 | -3.5 | 109 | -16.91 | -3.7 | -2.94 | – |
| $20 | 10:30 → 15:30 | long | 129 | -12.95 | -2.6 | 109 | -14.01 | -3.4 | -2.94 | – |
| $20 | 12:00 → 13:00 | short | 129 | -17.93 | -9.7 | 104 | -14.04 | -7.8 | -2.05 | – |
| $20 | 12:00 → 13:00 | long | 129 | -13.51 | -7.4 | 104 | -10.35 | -6.1 | -2.05 | – |
| $20 | 12:00 → 14:00 | short | 126 | -25.80 | -4.4 | 103 | -24.32 | -5.2 | -2.38 | – |
| $20 | 12:00 → 14:00 | long | 126 | -19.61 | -4.1 | 103 | -21.32 | -4.5 | -2.38 | – |
| $20 | 12:00 → 15:00 | short | 128 | -24.49 | -4.2 | 104 | -19.15 | -6.1 | -4.91 | – |
| $20 | 12:00 → 15:00 | long | 128 | -13.31 | -3.2 | 104 | -10.98 | -3.8 | -4.91 | – |
| $20 | 12:00 → 15:30 | short | 129 | -27.97 | -4.1 | 104 | -18.58 | -5.4 | -6.88 | – |
| $20 | 12:00 → 15:30 | long | 129 | -9.63 | -1.8 | 104 | -10.49 | -3.5 | -6.88 | – |
| $20 | 13:00 → 14:00 | short | 117 | -14.33 | -7.2 | 96 | -22.67 | -4.9 | -0.12 | – |
| $20 | 13:00 → 14:00 | long | 117 | -14.96 | -8.2 | 96 | -21.39 | -4.9 | -0.12 | – |
| $20 | 13:00 → 15:00 | short | 121 | -19.80 | -3.5 | 97 | -14.81 | -5.9 | -1.58 | – |
| $20 | 13:00 → 15:00 | long | 121 | -16.06 | -4.1 | 97 | -12.38 | -5.8 | -1.58 | – |
| $20 | 13:00 → 15:30 | short | 122 | -23.59 | -3.5 | 97 | -13.51 | -4.7 | -3.45 | – |
| $20 | 13:00 → 15:30 | long | 122 | -12.10 | -2.2 | 97 | -12.41 | -5.0 | -3.45 | – |
| $20 | 14:00 → 15:00 | short | 119 | -12.35 | -6.6 | 96 | -24.90 | -4.8 | +0.43 | – |
| $20 | 14:00 → 15:00 | long | 119 | -17.05 | -9.5 | 96 | -20.98 | -4.2 | +0.43 | – |
| $20 | 14:00 → 15:30 | short | 119 | -15.97 | -7.5 | 96 | -23.58 | -4.3 | -0.40 | – |
| $20 | 14:00 → 15:30 | long | 119 | -15.54 | -7.2 | 96 | -22.30 | -4.5 | -0.40 | – |

Typical round-trip cost of one fly (bid-ask paid on 4 legs, twice, plus fees): about $13.90 with $5 wings, about $11.90 with $20 wings. A pattern has to move option prices by more than that to be worth trading.

## 3. Notes and limits

* Data: 499 days so far. Each additional month of history makes these estimates firmer; the study is re-run with `main.py vol-study` as more data arrives.
* A t-statistic above 2 in both periods is the bar. Testing many combinations means a few will clear 2 in one period by luck; requiring both periods guards against that.
* Short flies lose on large SPY moves (they are short gamma). Their results are always shown at executable prices so this risk is fully in the numbers.
* The no-overnight rule is respected in sections 1–2: every trade opens and closes the same day.

## 4. Outside the current rules: holding overnight or for several days

**Research only.** The current risk limits forbid overnight positions; nothing here changes that. It shows whether longer holds change the cost picture, so the owner can decide with real numbers. Same iron butterflies, real bid/ask, expiry still at least 8 days out when closed. Per fly; the most one can lose is the wing width × 100 ($500 or $2,000).

| Wings | Hold | Side | Discovery n | Mean $ | t | Win % | Confirmation n | Mean $ | t | Win % |
|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| $5 | overnight | short | 304 | -19.30 | -14.5 | 3% | 160 | -18.99 | -17.4 | 3% |
| $5 | overnight | long | 304 | -20.13 | -13.8 | 6% | 160 | -23.52 | -24.5 | 1% |
| $5 | 1 day | short | 305 | -19.37 | -15.3 | 6% | 160 | -20.78 | -16.0 | 1% |
| $5 | 1 day | long | 305 | -21.13 | -16.0 | 6% | 160 | -24.38 | -27.5 | 0% |
| $5 | 3 days | short | 270 | -23.91 | -14.2 | 16% | 140 | -23.18 | -13.0 | 6% |
| $5 | 3 days | long | 270 | -26.76 | -16.3 | 10% | 140 | -35.55 | -15.3 | 2% |
| $5 | 5 days | short | 311 | -24.81 | -14.6 | 17% | 155 | -24.94 | -14.7 | 13% |
| $5 | 5 days | long | 311 | -29.37 | -16.0 | 11% | 155 | -37.34 | -19.0 | 2% |
| $20 | overnight | short | 114 | -20.23 | -3.3 | 43% | 97 | -4.69 | -0.8 | 57% |
| $20 | overnight | long | 114 | -18.23 | -3.2 | 24% | 97 | -29.78 | -5.4 | 18% |
| $20 | 1 day | short | 119 | -31.42 | -3.2 | 50% | 106 | +0.84 | +0.1 | 63% |
| $20 | 1 day | long | 119 | -7.11 | -0.8 | 35% | 106 | -36.92 | -4.7 | 18% |
| $20 | 3 days | short | 99 | -32.71 | -2.0 | 56% | 97 | +33.71 | +2.0 | 70% |
| $20 | 3 days | long | 99 | -17.04 | -1.1 | 35% | 97 | -81.52 | -5.4 | 23% |
| $20 | 5 days | short | 118 | -49.38 | -2.7 | 47% | 106 | +35.11 | +2.0 | 72% |
| $20 | 5 days | long | 118 | -4.93 | -0.3 | 41% | 106 | -86.70 | -5.1 | 24% |

**Positive after costs with t > 2 in both periods:** none.

Holds of several days overlap (a 5-day hold starts every day), so their t-statistics overstate certainty somewhat. Short flies carry crash risk that a few months of data may not contain.

### 4a. Crash test: $20-wing short fly held 5 days

224 trades from 2024-10-21 to 2026-10-09; 109 closed with at least one leg outside the downloaded strikes, priced from the nearest real quotes (see `_estimate_leg`). Most one fly can lose: about $2,000 minus the credit.

* Mean $-9.40, t -0.7, win rate 58%, average credit $1286
* Worst trade $-575.40; worst 5 trades below

| Opened | Closed | SPY in | SPY out | Move | Credit $ | P&L $ | A leg estimated |
|---|---|---:|---:|---:|---:|---:|---|
| 2024-12-13 | 2024-12-20 | 605.02 | 586.98 | -3.0% | 835 | -575.40 | yes |
| 2026-07-29 | 2026-08-05 | 734.92 | 774.78 | +5.4% | 1430 | -522.73 | yes |
| 2026-07-31 | 2026-08-07 | 740.49 | 773.19 | +4.4% | 1397 | -482.31 | yes |
| 2025-01-14 | 2025-01-22 | 583.15 | 606.69 | +4.0% | 1291 | -480.40 | no |
| 2025-04-02 | 2025-04-09 | 560.86 | 498.98 | -11.0% | 1408 | -473.15 | yes |

**Spring 2025 (opened 2025-03-17 to 2025-05-16, includes the April tariff crash):** 30 trades, mean $-126.50, total $-3,794.88, worst $-473.15, win rate 30%.

One position at a time (close and reopen every ~5 trading days), one fly, five possible start days:

| Starts | Trades | Total $ | Worst trade $ | Max drawdown $ | Drawdown % of $100k | Losing trades |
|---|---:|---:|---:|---:|---:|---:|
| 2024-10-21 | 70 | -1,054 | -482 | -1,734 | -1.7% | 31 |
| 2024-10-22 | 70 | -1,064 | -482 | -1,734 | -1.7% | 31 |
| 2024-10-23 | 69 | -1,011 | -482 | -1,686 | -1.7% | 29 |
| 2024-10-29 | 69 | -1,037 | -482 | -1,734 | -1.7% | 30 |
| 2024-10-31 | 68 | -1,016 | -482 | -1,686 | -1.7% | 29 |

No stop loss is applied here: each trade is held the full period, so these are the unmanaged numbers.
