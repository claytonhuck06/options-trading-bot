# Intraday volatility-pattern study — 2024-10-21 to 2026-10-16

Real SPY option quotes (Databento OPRA consolidated best bid/offer, 1-minute), 496 trading days. Discovery = days before 2026-02-20; confirmation = 2026-02-20 onward (never used to pick anything). Research only — no trading rule has been changed.

> **Bottom line.** No entry/exit combination made money after real bid/ask costs in both periods (0 of 80 looked significant in discovery alone; by chance alone roughly 2 would). The intraday pattern, if any, is smaller than the cost of trading it with these structures.

## 1. How implied volatility moves during the day

Average change in 14-day at-the-money implied volatility since 9:45 ET, in vol points:

| Period | 10:30 | 12:00 | 13:00 | 14:00 | 15:00 | 15:30 | 15:55 |
|---|---:|---:|---:|---:|---:|---:|---:|
| discovery | -0.07 (±0.08) | -0.10 (±0.13) | -0.09 (±0.15) | -0.07 (±0.15) | -0.24 (±0.15) | -0.11 (±0.16) | -0.18 (±0.16) |
| confirmation | -0.14 (±0.10) | -0.21 (±0.15) | -0.21 (±0.16) | -0.22 (±0.17) | -0.22 (±0.18) | -0.21 (±0.19) | -0.22 (±0.19) |

![intraday IV](img/intraday_iv_path.png)

A curve that drifts the same way in both periods, with its shaded band clear of zero, is a real tendency. Whether it pays depends on section 2. Note: implied vol here uses calendar time, which counts the hours after the close; that alone pushes measured IV slightly up through the day, so a gentle rise is partly a measurement effect.

## 2. Could you have traded it? Iron butterflies at real bid/ask

Short fly = sell the at-the-money call and put, buy wings $5 or $20 away (profits if options lose value). Long fly = the reverse. Nearest expiry at least 7 days out. Entered and exited at executable prices, $0.40 fees per round trip, per 1 fly ($ per contract set). *Gross* is the short fly at mid prices, before costs.

| Wings | Entry → exit | Side | Discovery n | Mean $ | t | Confirmation n | Mean $ | t | Gross (mid) $ | Holds in both? |
|---:|---|---|---:|---:|---:|---:|---:|---:|---:|:---:|
| $5 | 09:45 → 10:30 | short | 308 | -56.74 | -11.4 | 163 | -36.58 | -10.0 | +0.99 | – |
| $5 | 09:45 → 10:30 | long | 308 | -59.03 | -11.1 | 163 | -37.95 | -11.6 | +0.99 | – |
| $5 | 09:45 → 12:00 | short | 308 | -54.49 | -11.2 | 163 | -38.14 | -10.3 | +1.03 | – |
| $5 | 09:45 → 12:00 | long | 308 | -57.39 | -11.2 | 163 | -38.61 | -11.9 | +1.03 | – |
| $5 | 09:45 → 13:00 | short | 308 | -55.13 | -11.4 | 163 | -37.79 | -10.1 | +1.17 | – |
| $5 | 09:45 → 13:00 | long | 308 | -58.47 | -11.3 | 163 | -38.22 | -11.6 | +1.17 | – |
| $5 | 09:45 → 14:00 | short | 299 | -69.51 | -10.0 | 163 | -47.55 | -9.4 | +0.82 | – |
| $5 | 09:45 → 14:00 | long | 299 | -71.78 | -10.1 | 163 | -48.02 | -9.7 | +0.82 | – |
| $5 | 09:45 → 15:00 | short | 303 | -55.79 | -11.2 | 163 | -39.79 | -10.6 | +0.10 | – |
| $5 | 09:45 → 15:00 | long | 303 | -57.12 | -11.1 | 163 | -37.90 | -11.7 | +0.10 | – |
| $5 | 09:45 → 15:30 | short | 302 | -54.40 | -11.0 | 163 | -38.55 | -10.5 | +0.76 | – |
| $5 | 09:45 → 15:30 | long | 302 | -57.13 | -11.2 | 163 | -37.84 | -11.6 | +0.76 | – |
| $5 | 10:30 → 12:00 | short | 310 | -32.11 | -16.7 | 162 | -25.96 | -28.9 | +0.01 | – |
| $5 | 10:30 → 12:00 | long | 310 | -32.45 | -16.6 | 162 | -25.33 | -32.5 | +0.01 | – |
| $5 | 10:30 → 13:00 | short | 310 | -32.15 | -17.4 | 162 | -26.31 | -27.0 | +0.01 | – |
| $5 | 10:30 → 13:00 | long | 310 | -32.93 | -17.4 | 162 | -24.88 | -30.7 | +0.01 | – |
| $5 | 10:30 → 14:00 | short | 302 | -48.58 | -9.6 | 162 | -36.25 | -9.4 | +0.15 | – |
| $5 | 10:30 → 14:00 | long | 302 | -49.58 | -9.7 | 162 | -35.23 | -8.5 | +0.15 | – |
| $5 | 10:30 → 15:00 | short | 305 | -32.93 | -17.1 | 162 | -27.57 | -19.6 | -0.28 | – |
| $5 | 10:30 → 15:00 | long | 305 | -33.17 | -17.4 | 162 | -25.50 | -22.7 | -0.28 | – |
| $5 | 10:30 → 15:30 | short | 305 | -32.51 | -16.8 | 162 | -26.58 | -25.2 | -0.33 | – |
| $5 | 10:30 → 15:30 | long | 305 | -32.60 | -16.9 | 162 | -24.49 | -29.9 | -0.33 | – |
| $5 | 12:00 → 13:00 | short | 312 | -29.67 | -17.2 | 164 | -24.25 | -29.7 | -0.25 | – |
| $5 | 12:00 → 13:00 | long | 312 | -29.26 | -17.5 | 164 | -23.58 | -32.1 | -0.25 | – |
| $5 | 12:00 → 14:00 | short | 303 | -46.02 | -9.3 | 164 | -35.83 | -9.0 | -0.54 | – |
| $5 | 12:00 → 14:00 | long | 303 | -45.40 | -9.4 | 164 | -33.89 | -8.8 | -0.54 | – |
| $5 | 12:00 → 15:00 | short | 307 | -29.90 | -19.3 | 164 | -26.77 | -21.0 | -0.48 | – |
| $5 | 12:00 → 15:00 | long | 307 | -29.18 | -18.8 | 164 | -25.35 | -21.4 | -0.48 | – |
| $5 | 12:00 → 15:30 | short | 307 | -29.24 | -17.3 | 164 | -25.77 | -26.4 | -0.28 | – |
| $5 | 12:00 → 15:30 | long | 307 | -29.01 | -17.7 | 164 | -24.61 | -27.4 | -0.28 | – |
| $5 | 13:00 → 14:00 | short | 304 | -44.85 | -10.0 | 162 | -34.33 | -8.3 | +0.05 | – |
| $5 | 13:00 → 14:00 | long | 304 | -45.06 | -9.8 | 162 | -34.21 | -9.0 | +0.05 | – |
| $5 | 13:00 → 15:00 | short | 308 | -30.89 | -20.1 | 162 | -25.06 | -19.1 | -0.13 | – |
| $5 | 13:00 → 15:00 | long | 308 | -30.30 | -18.9 | 162 | -25.46 | -17.9 | -0.13 | – |
| $5 | 13:00 → 15:30 | short | 307 | -29.68 | -19.6 | 162 | -24.02 | -26.4 | +0.27 | – |
| $5 | 13:00 → 15:30 | long | 307 | -30.39 | -18.4 | 162 | -24.26 | -27.5 | +0.27 | – |
| $5 | 14:00 → 15:00 | short | 299 | -44.61 | -9.2 | 161 | -35.46 | -8.7 | -0.49 | – |
| $5 | 14:00 → 15:00 | long | 299 | -43.99 | -9.3 | 161 | -33.79 | -8.5 | -0.49 | – |
| $5 | 14:00 → 15:30 | short | 299 | -43.90 | -9.4 | 161 | -34.49 | -8.7 | -0.06 | – |
| $5 | 14:00 → 15:30 | long | 299 | -43.97 | -9.7 | 161 | -34.00 | -8.5 | -0.06 | – |
| $20 | 09:45 → 10:30 | short | 121 | -18.87 | -3.3 | 128 | -26.19 | -6.1 | +6.84 | – |
| $20 | 09:45 → 10:30 | long | 121 | -37.36 | -7.1 | 128 | -35.31 | -8.6 | +6.84 | – |
| $20 | 09:45 → 12:00 | short | 119 | -18.96 | -2.7 | 128 | -29.42 | -5.0 | +4.41 | – |
| $20 | 09:45 → 12:00 | long | 119 | -34.93 | -5.3 | 128 | -31.59 | -6.2 | +4.41 | – |
| $20 | 09:45 → 13:00 | short | 121 | -19.48 | -2.5 | 128 | -29.82 | -4.8 | +3.56 | – |
| $20 | 09:45 → 13:00 | long | 121 | -33.95 | -4.6 | 128 | -30.01 | -5.1 | +3.56 | – |
| $20 | 09:45 → 14:00 | short | 114 | -25.95 | -2.9 | 128 | -42.56 | -5.6 | +0.52 | – |
| $20 | 09:45 → 14:00 | long | 114 | -36.38 | -4.0 | 128 | -35.26 | -4.6 | +0.52 | – |
| $20 | 09:45 → 15:00 | short | 119 | -19.77 | -2.1 | 128 | -35.77 | -4.9 | +0.95 | – |
| $20 | 09:45 → 15:00 | long | 119 | -33.31 | -3.4 | 128 | -26.86 | -3.8 | +0.95 | – |
| $20 | 09:45 → 15:30 | short | 119 | -16.01 | -1.6 | 128 | -36.14 | -4.7 | +1.41 | – |
| $20 | 09:45 → 15:30 | long | 119 | -34.01 | -3.4 | 128 | -24.86 | -3.3 | +1.41 | – |
| $20 | 10:30 → 12:00 | short | 121 | -22.80 | -4.8 | 109 | -24.13 | -6.5 | -3.66 | – |
| $20 | 10:30 → 12:00 | long | 121 | -14.52 | -3.0 | 109 | -17.89 | -5.1 | -3.66 | – |
| $20 | 10:30 → 13:00 | short | 122 | -21.51 | -3.8 | 109 | -23.92 | -5.8 | -3.30 | – |
| $20 | 10:30 → 13:00 | long | 122 | -15.38 | -2.8 | 109 | -16.80 | -4.2 | -3.30 | – |
| $20 | 10:30 → 14:00 | short | 118 | -28.92 | -4.1 | 109 | -32.27 | -5.6 | -5.00 | – |
| $20 | 10:30 → 14:00 | long | 118 | -18.01 | -2.4 | 109 | -23.23 | -3.9 | -5.00 | – |
| $20 | 10:30 → 15:00 | short | 120 | -24.00 | -3.3 | 109 | -26.32 | -4.8 | -5.32 | – |
| $20 | 10:30 → 15:00 | long | 120 | -13.45 | -1.8 | 109 | -15.57 | -3.1 | -5.32 | – |
| $20 | 10:30 → 15:30 | short | 120 | -23.56 | -3.1 | 109 | -25.48 | -4.6 | -5.45 | – |
| $20 | 10:30 → 15:30 | long | 120 | -12.44 | -1.6 | 109 | -14.81 | -2.8 | -5.45 | – |
| $20 | 12:00 → 13:00 | short | 97 | -17.05 | -4.8 | 109 | -21.30 | -9.8 | -1.02 | – |
| $20 | 12:00 → 13:00 | long | 97 | -16.43 | -4.5 | 109 | -18.01 | -8.0 | -1.02 | – |
| $20 | 12:00 → 14:00 | short | 94 | -31.40 | -4.9 | 108 | -29.18 | -6.4 | -3.07 | – |
| $20 | 12:00 → 14:00 | long | 94 | -22.80 | -3.3 | 108 | -25.16 | -5.3 | -3.07 | – |
| $20 | 12:00 → 15:00 | short | 96 | -28.48 | -4.3 | 109 | -24.01 | -6.1 | -6.19 | – |
| $20 | 12:00 → 15:00 | long | 96 | -7.75 | -1.2 | 109 | -19.00 | -5.1 | -6.19 | – |
| $20 | 12:00 → 15:30 | short | 96 | -27.45 | -4.0 | 109 | -22.01 | -5.3 | -5.53 | – |
| $20 | 12:00 → 15:30 | long | 96 | -7.71 | -1.1 | 109 | -18.59 | -4.5 | -5.53 | – |
| $20 | 13:00 → 14:00 | short | 90 | -28.24 | -5.0 | 107 | -26.44 | -5.9 | -0.78 | – |
| $20 | 13:00 → 14:00 | long | 90 | -24.17 | -4.2 | 107 | -27.00 | -6.3 | -0.78 | – |
| $20 | 13:00 → 15:00 | short | 92 | -25.15 | -4.6 | 108 | -19.81 | -5.4 | -2.68 | – |
| $20 | 13:00 → 15:00 | long | 92 | -10.35 | -1.8 | 108 | -22.47 | -8.0 | -2.68 | – |
| $20 | 13:00 → 15:30 | short | 92 | -21.97 | -3.6 | 108 | -18.32 | -5.2 | -1.53 | – |
| $20 | 13:00 → 15:30 | long | 92 | -11.70 | -1.9 | 108 | -21.39 | -6.3 | -1.53 | – |
| $20 | 14:00 → 15:00 | short | 91 | -31.00 | -4.6 | 103 | -29.21 | -6.3 | -2.53 | – |
| $20 | 14:00 → 15:00 | long | 91 | -22.61 | -3.7 | 103 | -27.08 | -6.3 | -2.53 | – |
| $20 | 14:00 → 15:30 | short | 91 | -29.77 | -4.1 | 103 | -27.18 | -6.1 | -0.96 | – |
| $20 | 14:00 → 15:30 | long | 91 | -23.75 | -4.0 | 103 | -28.88 | -6.4 | -0.96 | – |

Typical round-trip cost of one fly (bid-ask paid on 4 legs, twice, plus fees): about $22.40 with $5 wings, about $18.40 with $20 wings. A pattern has to move option prices by more than that to be worth trading.

## 3. Notes and limits

* Data: 496 days so far. Each additional month of history makes these estimates firmer; the study is re-run with `main.py vol-study` as more data arrives.
* A t-statistic above 2 in both periods is the bar. Testing many combinations means a few will clear 2 in one period by luck; requiring both periods guards against that.
* Short flies lose on large SPY moves (they are short gamma). Their results are always shown at executable prices so this risk is fully in the numbers.
* The no-overnight rule is respected in sections 1–2: every trade opens and closes the same day.

## 4. Outside the current rules: holding overnight or for several days

**Research only.** The current risk limits forbid overnight positions; nothing here changes that. It shows whether longer holds change the cost picture, so the owner can decide with real numbers. Same iron butterflies, real bid/ask, expiry still at least 8 days out when closed. Per fly; the most one can lose is the wing width × 100 ($500 or $2,000).

| Wings | Hold | Side | Discovery n | Mean $ | t | Win % | Confirmation n | Mean $ | t | Win % |
|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| $5 | overnight | short | 289 | -32.16 | -15.4 | 1% | 161 | -25.67 | -18.6 | 1% |
| $5 | overnight | long | 289 | -32.76 | -15.3 | 0% | 161 | -28.97 | -32.3 | 1% |
| $5 | 1 day | short | 294 | -33.28 | -15.7 | 3% | 161 | -28.14 | -16.0 | 1% |
| $5 | 1 day | long | 294 | -33.92 | -16.5 | 0% | 161 | -30.99 | -28.6 | 0% |
| $5 | 3 days | short | 230 | -34.96 | -14.4 | 10% | 133 | -34.29 | -9.9 | 5% |
| $5 | 3 days | long | 230 | -36.82 | -16.2 | 4% | 133 | -46.00 | -12.9 | 1% |
| $5 | 5 days | short | 278 | -32.57 | -15.2 | 9% | 144 | -29.70 | -11.6 | 8% |
| $5 | 5 days | long | 278 | -36.85 | -18.9 | 1% | 144 | -42.53 | -20.1 | 1% |
| $20 | overnight | short | 80 | -8.13 | -0.7 | 50% | 101 | -9.37 | -1.2 | 49% |
| $20 | overnight | long | 80 | -27.96 | -2.6 | 34% | 101 | -33.82 | -4.7 | 21% |
| $20 | 1 day | short | 81 | -17.51 | -1.5 | 48% | 100 | +1.49 | +0.2 | 68% |
| $20 | 1 day | long | 81 | -24.28 | -2.0 | 41% | 100 | -46.74 | -5.3 | 19% |
| $20 | 3 days | short | 34 | +33.60 | +1.9 | 62% | 59 | +80.72 | +6.2 | 80% |
| $20 | 3 days | long | 34 | -85.81 | -4.8 | 18% | 59 | -126.42 | -9.9 | 12% |
| $20 | 5 days | short | 29 | +75.74 | +3.6 | 76% | 49 | +127.50 | +9.0 | 90% |
| $20 | 5 days | long | 29 | -135.09 | -7.7 | 7% | 49 | -173.99 | -13.1 | 6% |

**Positive after costs with t > 2 in both periods:** $20-wing short fly held 5 days.

Holds of several days overlap (a 5-day hold starts every day), so their t-statistics overstate certainty somewhat. Short flies carry crash risk that a few months of data may not contain.
