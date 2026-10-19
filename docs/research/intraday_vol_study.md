# Intraday volatility-pattern study — 2026-03-09 to 2026-10-16

Real SPY option quotes (Databento OPRA consolidated best bid/offer, 1-minute), 155 trading days. Discovery = days before 2026-08-05; confirmation = 2026-08-05 onward (never used to pick anything). Research only — no trading rule has been changed.

> **Bottom line.** No entry/exit combination made money after real bid/ask costs in both periods (0 of 80 looked significant in discovery alone; by chance alone roughly 2 would). The intraday pattern, if any, is smaller than the cost of trading it with these structures.

## 1. How implied volatility moves during the day

Average change in 14-day at-the-money implied volatility since 9:45 ET, in vol points:

| Period | 10:30 | 12:00 | 13:00 | 14:00 | 15:00 | 15:30 | 15:55 |
|---|---:|---:|---:|---:|---:|---:|---:|
| discovery | -0.15 (±0.12) | -0.16 (±0.20) | -0.20 (±0.22) | -0.20 (±0.21) | -0.18 (±0.23) | -0.17 (±0.26) | -0.20 (±0.26) |
| confirmation | -0.08 (±0.08) | -0.15 (±0.12) | -0.13 (±0.15) | -0.11 (±0.22) | -0.10 (±0.22) | -0.13 (±0.20) | -0.11 (±0.20) |

![intraday IV](img/intraday_iv_path.png)

A curve that drifts the same way in both periods, with its shaded band clear of zero, is a real tendency. Whether it pays depends on section 2. Note: implied vol here uses calendar time, which counts the hours after the close; that alone pushes measured IV slightly up through the day, so a gentle rise is partly a measurement effect.

## 2. Could you have traded it? Iron butterflies at real bid/ask

Short fly = sell the at-the-money call and put, buy wings $5 or $20 away (profits if options lose value). Long fly = the reverse. Nearest expiry at least 7 days out. Entered and exited at executable prices, $0.40 fees per round trip, per 1 fly ($ per contract set). *Gross* is the short fly at mid prices, before costs.

| Wings | Entry → exit | Side | Discovery n | Mean $ | t | Confirmation n | Mean $ | t | Gross (mid) $ | Holds in both? |
|---:|---|---|---:|---:|---:|---:|---:|---:|---:|:---:|
| $5 | 09:45 → 10:30 | short | 101 | -41.94 | -8.3 | 51 | -25.22 | -4.5 | +0.67 | – |
| $5 | 09:45 → 10:30 | long | 101 | -41.46 | -9.3 | 51 | -30.18 | -5.7 | +0.67 | – |
| $5 | 09:45 → 12:00 | short | 101 | -43.98 | -8.6 | 51 | -26.69 | -4.7 | +0.22 | – |
| $5 | 09:45 → 12:00 | long | 101 | -42.14 | -9.4 | 51 | -31.67 | -6.1 | +0.22 | – |
| $5 | 09:45 → 13:00 | short | 101 | -43.63 | -8.3 | 51 | -26.58 | -4.8 | +0.17 | – |
| $5 | 09:45 → 13:00 | long | 101 | -42.46 | -9.3 | 51 | -29.91 | -5.7 | +0.17 | – |
| $5 | 09:45 → 14:00 | short | 101 | -56.68 | -7.8 | 51 | -31.87 | -4.8 | +0.24 | – |
| $5 | 09:45 → 14:00 | long | 101 | -54.36 | -7.7 | 51 | -37.87 | -5.3 | +0.24 | – |
| $5 | 09:45 → 15:00 | short | 101 | -46.06 | -8.9 | 51 | -27.52 | -4.9 | -0.97 | – |
| $5 | 09:45 → 15:00 | long | 101 | -41.63 | -9.4 | 51 | -30.54 | -5.8 | -0.97 | – |
| $5 | 09:45 → 15:30 | short | 101 | -44.72 | -8.9 | 51 | -26.95 | -4.7 | -0.39 | – |
| $5 | 09:45 → 15:30 | long | 101 | -41.57 | -9.3 | 51 | -30.83 | -5.9 | -0.39 | – |
| $5 | 10:30 → 12:00 | short | 101 | -27.44 | -29.5 | 50 | -21.66 | -11.8 | -0.14 | – |
| $5 | 10:30 → 12:00 | long | 101 | -27.02 | -31.1 | 50 | -21.68 | -13.3 | -0.14 | – |
| $5 | 10:30 → 13:00 | short | 101 | -28.16 | -24.1 | 50 | -21.66 | -13.1 | -0.70 | – |
| $5 | 10:30 → 13:00 | long | 101 | -26.91 | -27.7 | 50 | -19.96 | -13.8 | -0.70 | – |
| $5 | 10:30 → 14:00 | short | 101 | -41.60 | -7.3 | 50 | -26.58 | -6.4 | -0.39 | – |
| $5 | 10:30 → 14:00 | long | 101 | -39.76 | -6.6 | 50 | -27.92 | -5.1 | -0.39 | – |
| $5 | 10:30 → 15:00 | short | 101 | -29.68 | -15.2 | 50 | -22.18 | -11.7 | -0.92 | – |
| $5 | 10:30 → 15:00 | long | 101 | -27.74 | -18.0 | 50 | -20.52 | -12.4 | -0.92 | – |
| $5 | 10:30 → 15:30 | short | 101 | -28.62 | -21.4 | 50 | -21.60 | -13.1 | -0.94 | – |
| $5 | 10:30 → 15:30 | long | 101 | -26.03 | -27.3 | 50 | -21.16 | -12.5 | -0.94 | – |
| $5 | 12:00 → 13:00 | short | 102 | -26.12 | -25.7 | 51 | -20.60 | -13.8 | -0.31 | – |
| $5 | 12:00 → 13:00 | long | 102 | -26.07 | -31.4 | 51 | -18.81 | -13.1 | -0.31 | – |
| $5 | 12:00 → 14:00 | short | 102 | -41.97 | -6.9 | 51 | -26.09 | -6.9 | -0.98 | – |
| $5 | 12:00 → 14:00 | long | 102 | -39.14 | -6.8 | 51 | -25.87 | -6.0 | -0.98 | – |
| $5 | 12:00 → 15:00 | short | 102 | -29.34 | -15.9 | 51 | -21.99 | -14.4 | -0.67 | – |
| $5 | 12:00 → 15:00 | long | 102 | -28.27 | -17.2 | 51 | -20.13 | -12.3 | -0.67 | – |
| $5 | 12:00 → 15:30 | short | 102 | -28.15 | -21.3 | 51 | -21.24 | -15.0 | -0.50 | – |
| $5 | 12:00 → 15:30 | long | 102 | -27.17 | -24.1 | 51 | -20.20 | -12.7 | -0.50 | – |
| $5 | 13:00 → 14:00 | short | 101 | -40.95 | -6.4 | 50 | -23.06 | -7.9 | -0.06 | – |
| $5 | 13:00 → 14:00 | long | 101 | -38.56 | -6.9 | 50 | -27.56 | -5.6 | -0.06 | – |
| $5 | 13:00 → 15:00 | short | 101 | -27.58 | -14.3 | 50 | -19.66 | -14.3 | +0.26 | – |
| $5 | 13:00 → 15:00 | long | 101 | -27.99 | -13.5 | 50 | -20.38 | -12.0 | +0.26 | – |
| $5 | 13:00 → 15:30 | short | 101 | -26.35 | -21.4 | 50 | -19.02 | -14.7 | +0.20 | – |
| $5 | 13:00 → 15:30 | long | 101 | -25.91 | -23.7 | 50 | -21.12 | -12.4 | +0.20 | – |
| $5 | 14:00 → 15:00 | short | 101 | -40.73 | -6.8 | 49 | -26.97 | -5.4 | -0.86 | – |
| $5 | 14:00 → 15:00 | long | 101 | -39.93 | -6.6 | 49 | -23.36 | -6.8 | -0.86 | – |
| $5 | 14:00 → 15:30 | short | 101 | -39.39 | -6.8 | 49 | -26.83 | -5.3 | -0.20 | – |
| $5 | 14:00 → 15:30 | long | 101 | -39.98 | -6.6 | 49 | -24.40 | -6.6 | -0.20 | – |
| $20 | 09:45 → 10:30 | short | 74 | -34.62 | -6.3 | 48 | -13.23 | -2.1 | +4.36 | – |
| $20 | 09:45 → 10:30 | long | 74 | -35.36 | -6.8 | 48 | -34.23 | -4.8 | +4.36 | – |
| $20 | 09:45 → 12:00 | short | 74 | -40.93 | -5.0 | 48 | -13.23 | -1.7 | +0.48 | – |
| $20 | 09:45 → 12:00 | long | 74 | -27.63 | -3.9 | 48 | -36.19 | -4.7 | +0.48 | – |
| $20 | 09:45 → 13:00 | short | 74 | -40.97 | -4.7 | 48 | -15.00 | -1.8 | -0.81 | – |
| $20 | 09:45 → 13:00 | long | 74 | -26.59 | -3.2 | 48 | -33.05 | -3.8 | -0.81 | – |
| $20 | 09:45 → 14:00 | short | 74 | -60.99 | -5.6 | 48 | -17.36 | -1.8 | -4.43 | – |
| $20 | 09:45 → 14:00 | long | 74 | -32.32 | -2.9 | 48 | -39.03 | -3.8 | -4.43 | – |
| $20 | 09:45 → 15:00 | short | 74 | -50.52 | -5.0 | 48 | -15.21 | -1.5 | -5.16 | – |
| $20 | 09:45 → 15:00 | long | 74 | -21.43 | -2.2 | 48 | -33.82 | -3.2 | -5.16 | – |
| $20 | 09:45 → 15:30 | short | 74 | -52.74 | -4.9 | 48 | -13.11 | -1.3 | -6.54 | – |
| $20 | 09:45 → 15:30 | long | 74 | -16.78 | -1.6 | 48 | -35.32 | -3.3 | -6.54 | – |
| $20 | 10:30 → 12:00 | short | 62 | -26.21 | -5.1 | 45 | -18.93 | -3.5 | -2.38 | – |
| $20 | 10:30 → 12:00 | long | 62 | -19.58 | -4.0 | 45 | -16.73 | -3.3 | -2.38 | – |
| $20 | 10:30 → 13:00 | short | 62 | -25.71 | -4.3 | 45 | -18.87 | -3.5 | -2.79 | – |
| $20 | 10:30 → 13:00 | long | 62 | -18.63 | -3.2 | 45 | -15.33 | -2.8 | -2.79 | – |
| $20 | 10:30 → 14:00 | short | 62 | -38.25 | -4.4 | 45 | -21.98 | -3.1 | -3.81 | – |
| $20 | 10:30 → 14:00 | long | 62 | -25.88 | -2.8 | 45 | -20.89 | -3.0 | -3.81 | – |
| $20 | 10:30 → 15:00 | short | 62 | -29.69 | -3.7 | 45 | -18.58 | -2.7 | -4.29 | – |
| $20 | 10:30 → 15:00 | long | 62 | -16.46 | -2.3 | 45 | -16.40 | -2.4 | -4.29 | – |
| $20 | 10:30 → 15:30 | short | 62 | -30.59 | -3.6 | 45 | -15.87 | -2.6 | -4.51 | – |
| $20 | 10:30 → 15:30 | long | 62 | -12.95 | -1.6 | 45 | -18.71 | -3.0 | -4.51 | – |
| $20 | 12:00 → 13:00 | short | 61 | -22.66 | -7.1 | 47 | -19.02 | -6.7 | -1.54 | – |
| $20 | 12:00 → 13:00 | long | 61 | -21.30 | -7.0 | 47 | -13.72 | -4.1 | -1.54 | – |
| $20 | 12:00 → 14:00 | short | 61 | -36.01 | -5.0 | 46 | -20.70 | -4.6 | -2.29 | – |
| $20 | 12:00 → 14:00 | long | 61 | -27.56 | -3.7 | 46 | -21.27 | -4.3 | -2.29 | – |
| $20 | 12:00 → 15:00 | short | 61 | -27.60 | -4.5 | 47 | -18.34 | -4.2 | -2.13 | – |
| $20 | 12:00 → 15:00 | long | 61 | -20.88 | -3.8 | 47 | -17.27 | -3.5 | -2.13 | – |
| $20 | 12:00 → 15:30 | short | 61 | -26.33 | -4.0 | 47 | -16.31 | -3.9 | -1.72 | – |
| $20 | 12:00 → 15:30 | long | 61 | -19.04 | -3.0 | 47 | -17.87 | -3.8 | -1.72 | – |
| $20 | 13:00 → 14:00 | short | 61 | -34.74 | -4.7 | 44 | -15.17 | -4.9 | +0.23 | – |
| $20 | 13:00 → 14:00 | long | 61 | -28.17 | -4.1 | 44 | -25.40 | -6.0 | +0.23 | – |
| $20 | 13:00 → 15:00 | short | 61 | -24.40 | -4.1 | 45 | -12.60 | -3.9 | +1.69 | – |
| $20 | 13:00 → 15:00 | long | 61 | -23.76 | -5.7 | 45 | -21.42 | -5.7 | +1.69 | – |
| $20 | 13:00 → 15:30 | short | 61 | -23.51 | -4.1 | 45 | -10.78 | -3.4 | +1.68 | – |
| $20 | 13:00 → 15:30 | long | 61 | -20.61 | -3.9 | 45 | -22.62 | -6.1 | +1.68 | – |
| $20 | 14:00 → 15:00 | short | 60 | -34.07 | -4.6 | 42 | -20.78 | -5.6 | -0.38 | – |
| $20 | 14:00 → 15:00 | long | 60 | -33.37 | -5.0 | 42 | -19.95 | -4.8 | -0.38 | – |
| $20 | 14:00 → 15:30 | short | 60 | -32.10 | -4.5 | 42 | -19.80 | -5.0 | +1.09 | – |
| $20 | 14:00 → 15:30 | long | 60 | -34.72 | -4.8 | 42 | -21.38 | -5.4 | +1.09 | – |

Typical round-trip cost of one fly (bid-ask paid on 4 legs, twice, plus fees): about $24.90 with $5 wings, about $20.90 with $20 wings. A pattern has to move option prices by more than that to be worth trading.

## 3. Notes and limits

* Data: 155 days so far. Each additional month of history makes these estimates firmer; the study is re-run with `main.py vol-study` as more data arrives.
* A t-statistic above 2 in both periods is the bar. Testing many combinations means a few will clear 2 in one period by luck; requiring both periods guards against that.
* Short flies lose on large SPY moves (they are short gamma). Their results are always shown at executable prices so this risk is fully in the numbers.
* The no-overnight rule is respected in sections 1–2: every trade opens and closes the same day.

## 4. Outside the current rules: holding overnight or for several days

**Research only.** The current risk limits forbid overnight positions; nothing here changes that. It shows whether longer holds change the cost picture, so the owner can decide with real numbers. Same iron butterflies, real bid/ask, expiry still at least 8 days out when closed. Per fly; the most one can lose is the wing width × 100 ($500 or $2,000).

| Wings | Hold | Side | Discovery n | Mean $ | t | Win % | Confirmation n | Mean $ | t | Win % |
|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---:|
| $5 | overnight | short | 101 | -29.49 | -16.2 | 0% | 49 | -19.03 | -9.1 | 4% |
| $5 | overnight | long | 101 | -29.92 | -29.4 | 0% | 49 | -25.93 | -13.5 | 2% |
| $5 | 1 day | short | 101 | -31.71 | -12.9 | 0% | 49 | -20.11 | -8.9 | 2% |
| $5 | 1 day | long | 101 | -32.27 | -23.4 | 0% | 49 | -27.18 | -13.9 | 0% |
| $5 | 3 days | short | 80 | -42.04 | -8.5 | 2% | 43 | -21.00 | -4.4 | 12% |
| $5 | 3 days | long | 80 | -49.86 | -8.9 | 0% | 43 | -39.82 | -11.2 | 2% |
| $5 | 5 days | short | 86 | -37.05 | -10.2 | 3% | 47 | -14.78 | -4.9 | 19% |
| $5 | 5 days | long | 86 | -43.69 | -14.0 | 0% | 47 | -37.66 | -14.4 | 2% |
| $20 | overnight | short | 56 | -18.02 | -1.7 | 45% | 44 | -0.63 | -0.1 | 52% |
| $20 | overnight | long | 56 | -29.15 | -3.0 | 23% | 44 | -37.24 | -3.5 | 18% |
| $20 | 1 day | short | 54 | -2.47 | -0.2 | 69% | 44 | +3.90 | +0.3 | 66% |
| $20 | 1 day | long | 54 | -47.01 | -3.9 | 20% | 44 | -42.15 | -3.2 | 18% |
| $20 | 3 days | short | 26 | +71.25 | +3.3 | 81% | 31 | +92.44 | +5.6 | 81% |
| $20 | 3 days | long | 26 | -118.25 | -5.5 | 15% | 31 | -135.56 | -8.3 | 10% |
| $20 | 5 days | short | 18 | +96.60 | +4.2 | 89% | 30 | +147.67 | +8.3 | 90% |
| $20 | 5 days | long | 18 | -150.79 | -6.7 | 11% | 30 | -188.27 | -11.2 | 3% |

Holds of several days overlap (a 5-day hold starts every day), so their t-statistics overstate certainty somewhat. Short flies carry crash risk that a few months of data may not contain.
