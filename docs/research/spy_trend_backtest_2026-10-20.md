# SPY Trend Call Spreads: Backtest (2026-10-20)

SPY bull call spreads, entered only when SPY is above its 200-day average and calm, closed when that breaks. Run on OptionsDX end-of-day quotes exactly as pre-registered in `docs/research/spy_trend_preregistration.md`. Fills at bid/ask, $0.05 per contract per leg per side, $100,000 fixed account, 1% risk per spread, up to 3 open, one new spread a week. Discovery = entries 2011–2018 (all 144 settings); confirmation = entries 2019–2023 (chosen setting only).

## Result: **NO-GO**: 2 of 8 bars failed

Chosen setting (highest smoothed discovery score): **lights · 60 DTE · long 0.50Δ / short 0.20Δ · target hold · stop none**

| Bar | Result | Detail |
|---|---|---|
| 1 · Profitable in both periods, t ≥ 2 | ✅ pass | discovery $95.86/trade (155), confirmation $140.76/trade (80), per-trade t = 2.77; monthly-P&L t = 1.78 (information) |
| 2 · Survives worse fills | ✅ pass | cost-stress average $99.10/trade |
| 3 · Losses stay within plan | ✅ pass | 0 trades lost more than planned |
| 4 · Drawdown under 10% | ❌ fail | worst drawdown $10,389 (10.4%) on 2016-11-04 |
| 5 · Crash periods | ✅ pass | Aug–Sep 2011: drawdown 1.7%, closed P&L $-2,021; Aug 2015–Feb 2016: drawdown 3.9%, closed P&L $-3,784; Feb 2018: drawdown 2.6%, closed P&L $1,325; Oct–Dec 2018: drawdown 2.5%, closed P&L $-1,500; COVID 2020: drawdown 2.3%, closed P&L $297; 2022 bear: drawdown 4.2%, closed P&L $-3,441 |
| 6 · Not a lucky setting | ✅ pass | 3 neighbours with ≥30 trades: 45 DTE 0.50/0.20Δ $49.94, 60 DTE 0.70/0.20Δ $61.49, 60 DTE 0.50/0.30Δ $77.69 |
| 7 · Not a few lucky trades | ✅ pass | without the best 12 trades: $60.57/trade |
| 8 · The trend signal earns its keep | ❌ fail | with signal $95.86 vs no signal $163.48 (165 trades) in discovery; $140.76 vs $179.03 (91 trades) in confirmation |

## Chosen setting, full period

- All: 235 trades, $111.15/trade, win rate 55%, worst $-951, total $26,120
- Discovery: 155 trades, $95.86/trade, win rate 54%, worst $-951, total $14,859
- Confirmation: 80 trades, $140.76/trade, win rate 59%, worst $-916, total $11,261
- Cost-stress fills: $99.10/trade · optimistic fills (information): $123.20/trade
- Exits: time_exit 170, trend_exit 56, assignment_guard_call 9
- Days with a leg failing the data check while open (carried forward): 1027
- Still open at the end of the data: 3
- Signal was "risk on" on 81% of trading days 2011–2023
- Vendor deltas used when our solver refused a quote (all built days, all strikes): 2,484
- Equity chart: `docs/research/img/spy_trend_equity_2026-10-20.png`

## Was it just market exposure? (information only)

Per trade, SPY shares with the same dollar delta at entry, held for the same days, would have made **$140.45** on average. What the spread made beyond that: **$-29.30** per trade (235 trades). A leftover at or below zero means the profit was just market exposure, before the extra option costs.

For context, SPY price change (excluding dividends): discovery +97%, confirmation +90%.

## Comparisons (information only; can't change the result)

- **Same setting, no signal (bar 8):** 256 trades, $169.01/trade, win rate 62%, worst $-1,000, total $43,267
- **Same setting, other signal variant (lights+momentum):** 226 trades, $108.29/trade, win rate 55%, worst $-937, total $24,473

### Per year

| Year | Trades | Total P&L | Avg/trade | Worst trade |
|---|---|---|---|---|
| 2011 | 13 | $-3,405 | $-261.89 | $-754 |
| 2012 | 23 | $2,039 | $88.63 | $-951 |
| 2013 | 21 | $5,425 | $258.33 | $-829 |
| 2014 | 21 | $2,346 | $111.71 | $-889 |
| 2015 | 19 | $-6,280 | $-330.52 | $-913 |
| 2016 | 18 | $1,711 | $95.08 | $-745 |
| 2017 | 21 | $12,337 | $587.46 | $-127 |
| 2018 | 19 | $686 | $36.08 | $-664 |
| 2019 | 21 | $5,452 | $259.62 | $-880 |
| 2020 | 14 | $3,627 | $259.07 | $-916 |
| 2021 | 21 | $5,435 | $258.80 | $-814 |
| 2022 | 4 | $-1,431 | $-357.70 | $-750 |
| 2023 | 20 | $-1,822 | $-91.10 | $-795 |

## All 144 settings, discovery period (2011–2018)

Score = average profit per trade ÷ planned max loss; settings with under 60 trades get no score.

| Setting | Trades | Avg P&L | Win rate | Score | Smoothed |
|---|---|---|---|---|---|
| lights · 30 DTE · long 0.50Δ / short 0.20Δ · target +100% · stop none | 350 | $40.90 | 53% | +0.043 | +0.035 |
| lights · 30 DTE · long 0.50Δ / short 0.20Δ · target +100% · stop −50% | 350 | $11.46 | 49% | +0.011 | +0.020 |
| lights · 30 DTE · long 0.50Δ / short 0.20Δ · target +50% · stop none | 350 | $28.18 | 55% | +0.028 | +0.026 |
| lights · 30 DTE · long 0.50Δ / short 0.20Δ · target +50% · stop −50% | 350 | $-0.59 | 52% | -0.003 | +0.010 |
| lights · 30 DTE · long 0.50Δ / short 0.20Δ · target hold · stop none | 350 | $45.07 | 53% | +0.047 | +0.039 |
| lights · 30 DTE · long 0.50Δ / short 0.20Δ · target hold · stop −50% | 350 | $15.63 | 49% | +0.015 | +0.025 |
| lights · 30 DTE · long 0.50Δ / short 0.30Δ · target +100% · stop none | 350 | $23.35 | 57% | +0.027 | +0.026 |
| lights · 30 DTE · long 0.50Δ / short 0.30Δ · target +100% · stop −50% | 351 | $-1.52 | 54% | -0.001 | +0.009 |
| lights · 30 DTE · long 0.50Δ / short 0.30Δ · target +50% · stop none | 350 | $16.81 | 58% | +0.018 | +0.022 |
| lights · 30 DTE · long 0.50Δ / short 0.30Δ · target +50% · stop −50% | 351 | $-8.84 | 54% | -0.010 | +0.005 |
| lights · 30 DTE · long 0.50Δ / short 0.30Δ · target hold · stop none | 350 | $24.15 | 57% | +0.028 | +0.028 |
| lights · 30 DTE · long 0.50Δ / short 0.30Δ · target hold · stop −50% | 351 | $-0.72 | 54% | +0.000 | +0.012 |
| lights · 30 DTE · long 0.70Δ / short 0.20Δ · target +100% · stop none | 347 | $27.19 | 58% | +0.039 | +0.036 |
| lights · 30 DTE · long 0.70Δ / short 0.20Δ · target +100% · stop −50% | 347 | $21.69 | 57% | +0.029 | +0.026 |
| lights · 30 DTE · long 0.70Δ / short 0.20Δ · target +50% · stop none | 347 | $24.61 | 58% | +0.034 | +0.031 |
| lights · 30 DTE · long 0.70Δ / short 0.20Δ · target +50% · stop −50% | 347 | $19.11 | 57% | +0.025 | +0.022 |
| lights · 30 DTE · long 0.70Δ / short 0.20Δ · target hold · stop none | 347 | $26.14 | 58% | +0.038 | +0.036 |
| lights · 30 DTE · long 0.70Δ / short 0.20Δ · target hold · stop −50% | 347 | $20.64 | 57% | +0.028 | +0.027 |
| lights · 30 DTE · long 0.70Δ / short 0.30Δ · target +100% · stop none | 348 | $22.56 | 60% | +0.027 | +0.029 |
| lights · 30 DTE · long 0.70Δ / short 0.30Δ · target +100% · stop −50% | 348 | $17.49 | 60% | +0.019 | +0.020 |
| lights · 30 DTE · long 0.70Δ / short 0.30Δ · target +50% · stop none | 348 | $23.78 | 60% | +0.028 | +0.027 |
| lights · 30 DTE · long 0.70Δ / short 0.30Δ · target +50% · stop −50% | 348 | $18.74 | 60% | +0.021 | +0.016 |
| lights · 30 DTE · long 0.70Δ / short 0.30Δ · target hold · stop none | 348 | $22.56 | 60% | +0.027 | +0.029 |
| lights · 30 DTE · long 0.70Δ / short 0.30Δ · target hold · stop −50% | 348 | $17.49 | 60% | +0.019 | +0.019 |
| lights · 45 DTE · long 0.50Δ / short 0.20Δ · target +100% · stop none | 249 | $42.19 | 51% | +0.056 | +0.060 |
| lights · 45 DTE · long 0.50Δ / short 0.20Δ · target +100% · stop −50% | 282 | $50.82 | 49% | +0.064 | +0.045 |
| lights · 45 DTE · long 0.50Δ / short 0.20Δ · target +50% · stop none | 281 | $31.78 | 57% | +0.041 | +0.045 |
| lights · 45 DTE · long 0.50Δ / short 0.20Δ · target +50% · stop −50% | 309 | $19.11 | 53% | +0.029 | +0.033 |
| lights · 45 DTE · long 0.50Δ / short 0.20Δ · target hold · stop none | 248 | $49.94 | 51% | +0.065 | +0.065 |
| lights · 45 DTE · long 0.50Δ / short 0.20Δ · target hold · stop −50% | 279 | $52.84 | 48% | +0.065 | +0.050 |
| lights · 45 DTE · long 0.50Δ / short 0.30Δ · target +100% · stop none | 252 | $46.21 | 55% | +0.049 | +0.049 |
| lights · 45 DTE · long 0.50Δ / short 0.30Δ · target +100% · stop −50% | 276 | $32.20 | 51% | +0.032 | +0.033 |
| lights · 45 DTE · long 0.50Δ / short 0.30Δ · target +50% · stop none | 275 | $47.33 | 60% | +0.051 | +0.043 |
| lights · 45 DTE · long 0.50Δ / short 0.30Δ · target +50% · stop −50% | 300 | $31.71 | 55% | +0.031 | +0.029 |
| lights · 45 DTE · long 0.50Δ / short 0.30Δ · target hold · stop none | 252 | $46.21 | 55% | +0.049 | +0.050 |
| lights · 45 DTE · long 0.50Δ / short 0.30Δ · target hold · stop −50% | 276 | $32.26 | 51% | +0.032 | +0.034 |
| lights · 45 DTE · long 0.70Δ / short 0.20Δ · target +100% · stop none | 241 | $30.04 | 59% | +0.046 | +0.049 |
| lights · 45 DTE · long 0.70Δ / short 0.20Δ · target +100% · stop −50% | 256 | $28.34 | 57% | +0.039 | +0.045 |
| lights · 45 DTE · long 0.70Δ / short 0.20Δ · target +50% · stop none | 249 | $23.87 | 59% | +0.037 | +0.046 |
| lights · 45 DTE · long 0.70Δ / short 0.20Δ · target +50% · stop −50% | 264 | $24.39 | 58% | +0.036 | +0.037 |
| lights · 45 DTE · long 0.70Δ / short 0.20Δ · target hold · stop none | 241 | $30.04 | 59% | +0.046 | +0.052 |
| lights · 45 DTE · long 0.70Δ / short 0.20Δ · target hold · stop −50% | 256 | $28.34 | 57% | +0.039 | +0.046 |
| lights · 45 DTE · long 0.70Δ / short 0.30Δ · target +100% · stop none | 247 | $29.97 | 61% | +0.045 | +0.045 |
| lights · 45 DTE · long 0.70Δ / short 0.30Δ · target +100% · stop −50% | 262 | $20.03 | 60% | +0.029 | +0.034 |
| lights · 45 DTE · long 0.70Δ / short 0.30Δ · target +50% · stop none | 251 | $29.44 | 61% | +0.041 | +0.041 |
| lights · 45 DTE · long 0.70Δ / short 0.30Δ · target +50% · stop −50% | 264 | $12.60 | 59% | +0.018 | +0.032 |
| lights · 45 DTE · long 0.70Δ / short 0.30Δ · target hold · stop none | 247 | $29.97 | 61% | +0.045 | +0.046 |
| lights · 45 DTE · long 0.70Δ / short 0.30Δ · target hold · stop −50% | 262 | $20.03 | 60% | +0.029 | +0.036 |
| lights · 60 DTE · long 0.50Δ / short 0.20Δ · target +100% · stop none | 158 | $87.05 | 52% | +0.111 | +0.085 |
| lights · 60 DTE · long 0.50Δ / short 0.20Δ · target +100% · stop −50% | 179 | $28.45 | 45% | +0.041 | +0.058 |
| lights · 60 DTE · long 0.50Δ / short 0.20Δ · target +50% · stop none | 194 | $65.27 | 60% | +0.084 | +0.073 |
| lights · 60 DTE · long 0.50Δ / short 0.20Δ · target +50% · stop −50% | 226 | $32.93 | 54% | +0.044 | +0.052 |
| lights · 60 DTE · long 0.50Δ / short 0.20Δ · target hold · stop none **← chosen** | 155 | $95.86 | 54% | +0.119 | +0.089 |
| lights · 60 DTE · long 0.50Δ / short 0.20Δ · target hold · stop −50% | 173 | $36.91 | 45% | +0.051 | +0.063 |
| lights · 60 DTE · long 0.50Δ / short 0.30Δ · target +100% · stop none | 157 | $77.69 | 56% | +0.083 | +0.073 |
| lights · 60 DTE · long 0.50Δ / short 0.30Δ · target +100% · stop −50% | 177 | $23.58 | 50% | +0.028 | +0.044 |
| lights · 60 DTE · long 0.50Δ / short 0.30Δ · target +50% · stop none | 185 | $62.03 | 60% | +0.067 | +0.066 |
| lights · 60 DTE · long 0.50Δ / short 0.30Δ · target +50% · stop −50% | 214 | $39.10 | 55% | +0.046 | +0.046 |
| lights · 60 DTE · long 0.50Δ / short 0.30Δ · target hold · stop none | 157 | $77.69 | 56% | +0.083 | +0.075 |
| lights · 60 DTE · long 0.50Δ / short 0.30Δ · target hold · stop −50% | 177 | $23.58 | 50% | +0.028 | +0.045 |
| lights · 60 DTE · long 0.70Δ / short 0.20Δ · target +100% · stop none | 143 | $61.49 | 59% | +0.088 | +0.084 |
| lights · 60 DTE · long 0.70Δ / short 0.20Δ · target +100% · stop −50% | 150 | $59.35 | 59% | +0.081 | +0.068 |
| lights · 60 DTE · long 0.70Δ / short 0.20Δ · target +50% · stop none | 159 | $62.66 | 61% | +0.094 | +0.076 |
| lights · 60 DTE · long 0.70Δ / short 0.20Δ · target +50% · stop −50% | 168 | $54.30 | 59% | +0.079 | +0.066 |
| lights · 60 DTE · long 0.70Δ / short 0.20Δ · target hold · stop none | 143 | $61.49 | 59% | +0.088 | +0.084 |
| lights · 60 DTE · long 0.70Δ / short 0.20Δ · target hold · stop −50% | 150 | $59.35 | 59% | +0.081 | +0.068 |
| lights · 60 DTE · long 0.70Δ / short 0.30Δ · target +100% · stop none | 154 | $55.88 | 62% | +0.080 | +0.074 |
| lights · 60 DTE · long 0.70Δ / short 0.30Δ · target +100% · stop −50% | 164 | $45.74 | 60% | +0.063 | +0.059 |
| lights · 60 DTE · long 0.70Δ / short 0.30Δ · target +50% · stop none | 158 | $50.79 | 62% | +0.070 | +0.069 |
| lights · 60 DTE · long 0.70Δ / short 0.30Δ · target +50% · stop −50% | 170 | $41.30 | 61% | +0.060 | +0.057 |
| lights · 60 DTE · long 0.70Δ / short 0.30Δ · target hold · stop none | 154 | $55.88 | 62% | +0.080 | +0.074 |
| lights · 60 DTE · long 0.70Δ / short 0.30Δ · target hold · stop −50% | 164 | $45.74 | 60% | +0.063 | +0.058 |
| lights+momentum · 30 DTE · long 0.50Δ / short 0.20Δ · target +100% · stop none | 340 | $30.95 | 52% | +0.032 | +0.030 |
| lights+momentum · 30 DTE · long 0.50Δ / short 0.20Δ · target +100% · stop −50% | 340 | $5.53 | 49% | +0.004 | +0.015 |
| lights+momentum · 30 DTE · long 0.50Δ / short 0.20Δ · target +50% · stop none | 340 | $18.91 | 55% | +0.017 | +0.020 |
| lights+momentum · 30 DTE · long 0.50Δ / short 0.20Δ · target +50% · stop −50% | 340 | $-5.60 | 51% | -0.009 | +0.004 |
| lights+momentum · 30 DTE · long 0.50Δ / short 0.20Δ · target hold · stop none | 340 | $35.25 | 52% | +0.036 | +0.034 |
| lights+momentum · 30 DTE · long 0.50Δ / short 0.20Δ · target hold · stop −50% | 340 | $9.82 | 49% | +0.009 | +0.020 |
| lights+momentum · 30 DTE · long 0.50Δ / short 0.30Δ · target +100% · stop none | 340 | $13.76 | 56% | +0.017 | +0.020 |
| lights+momentum · 30 DTE · long 0.50Δ / short 0.30Δ · target +100% · stop −50% | 341 | $-11.29 | 52% | -0.010 | +0.004 |
| lights+momentum · 30 DTE · long 0.50Δ / short 0.30Δ · target +50% · stop none | 340 | $8.21 | 56% | +0.010 | +0.016 |
| lights+momentum · 30 DTE · long 0.50Δ / short 0.30Δ · target +50% · stop −50% | 341 | $-18.30 | 52% | -0.019 | +0.001 |
| lights+momentum · 30 DTE · long 0.50Δ / short 0.30Δ · target hold · stop none | 340 | $14.58 | 56% | +0.018 | +0.023 |
| lights+momentum · 30 DTE · long 0.50Δ / short 0.30Δ · target hold · stop −50% | 341 | $-10.47 | 52% | -0.009 | +0.008 |
| lights+momentum · 30 DTE · long 0.70Δ / short 0.20Δ · target +100% · stop none | 338 | $20.81 | 57% | +0.030 | +0.030 |
| lights+momentum · 30 DTE · long 0.70Δ / short 0.20Δ · target +100% · stop −50% | 338 | $15.70 | 55% | +0.021 | +0.022 |
| lights+momentum · 30 DTE · long 0.70Δ / short 0.20Δ · target +50% · stop none | 338 | $17.44 | 56% | +0.025 | +0.025 |
| lights+momentum · 30 DTE · long 0.70Δ / short 0.20Δ · target +50% · stop −50% | 338 | $12.34 | 55% | +0.015 | +0.018 |
| lights+momentum · 30 DTE · long 0.70Δ / short 0.20Δ · target hold · stop none | 338 | $19.74 | 57% | +0.029 | +0.031 |
| lights+momentum · 30 DTE · long 0.70Δ / short 0.20Δ · target hold · stop −50% | 338 | $14.63 | 55% | +0.020 | +0.023 |
| lights+momentum · 30 DTE · long 0.70Δ / short 0.30Δ · target +100% · stop none | 339 | $14.68 | 59% | +0.018 | +0.024 |
| lights+momentum · 30 DTE · long 0.70Δ / short 0.30Δ · target +100% · stop −50% | 339 | $10.66 | 58% | +0.013 | +0.014 |
| lights+momentum · 30 DTE · long 0.70Δ / short 0.30Δ · target +50% · stop none | 339 | $15.81 | 59% | +0.019 | +0.022 |
| lights+momentum · 30 DTE · long 0.70Δ / short 0.30Δ · target +50% · stop −50% | 339 | $11.79 | 58% | +0.014 | +0.011 |
| lights+momentum · 30 DTE · long 0.70Δ / short 0.30Δ · target hold · stop none | 339 | $14.68 | 59% | +0.018 | +0.024 |
| lights+momentum · 30 DTE · long 0.70Δ / short 0.30Δ · target hold · stop −50% | 339 | $10.66 | 58% | +0.013 | +0.015 |
| lights+momentum · 45 DTE · long 0.50Δ / short 0.20Δ · target +100% · stop none | 237 | $46.32 | 51% | +0.062 | +0.057 |
| lights+momentum · 45 DTE · long 0.50Δ / short 0.20Δ · target +100% · stop −50% | 268 | $49.16 | 49% | +0.065 | +0.046 |
| lights+momentum · 45 DTE · long 0.50Δ / short 0.20Δ · target +50% · stop none | 270 | $27.03 | 57% | +0.036 | +0.042 |
| lights+momentum · 45 DTE · long 0.50Δ / short 0.20Δ · target +50% · stop −50% | 298 | $15.48 | 53% | +0.025 | +0.032 |
| lights+momentum · 45 DTE · long 0.50Δ / short 0.20Δ · target hold · stop none | 236 | $52.48 | 51% | +0.070 | +0.063 |
| lights+momentum · 45 DTE · long 0.50Δ / short 0.20Δ · target hold · stop −50% | 266 | $52.05 | 48% | +0.066 | +0.051 |
| lights+momentum · 45 DTE · long 0.50Δ / short 0.30Δ · target +100% · stop none | 243 | $48.56 | 57% | +0.051 | +0.050 |
| lights+momentum · 45 DTE · long 0.50Δ / short 0.30Δ · target +100% · stop −50% | 265 | $35.72 | 52% | +0.037 | +0.034 |
| lights+momentum · 45 DTE · long 0.50Δ / short 0.30Δ · target +50% · stop none | 266 | $46.46 | 61% | +0.050 | +0.042 |
| lights+momentum · 45 DTE · long 0.50Δ / short 0.30Δ · target +50% · stop −50% | 290 | $33.94 | 56% | +0.034 | +0.028 |
| lights+momentum · 45 DTE · long 0.50Δ / short 0.30Δ · target hold · stop none | 243 | $48.56 | 57% | +0.051 | +0.051 |
| lights+momentum · 45 DTE · long 0.50Δ / short 0.30Δ · target hold · stop −50% | 265 | $35.78 | 52% | +0.037 | +0.034 |
| lights+momentum · 45 DTE · long 0.70Δ / short 0.20Δ · target +100% · stop none | 232 | $33.43 | 59% | +0.047 | +0.049 |
| lights+momentum · 45 DTE · long 0.70Δ / short 0.20Δ · target +100% · stop −50% | 247 | $31.91 | 58% | +0.040 | +0.044 |
| lights+momentum · 45 DTE · long 0.70Δ / short 0.20Δ · target +50% · stop none | 241 | $24.43 | 60% | +0.036 | +0.042 |
| lights+momentum · 45 DTE · long 0.70Δ / short 0.20Δ · target +50% · stop −50% | 256 | $26.15 | 59% | +0.036 | +0.034 |
| lights+momentum · 45 DTE · long 0.70Δ / short 0.20Δ · target hold · stop none | 232 | $33.43 | 59% | +0.047 | +0.051 |
| lights+momentum · 45 DTE · long 0.70Δ / short 0.20Δ · target hold · stop −50% | 247 | $31.91 | 58% | +0.040 | +0.044 |
| lights+momentum · 45 DTE · long 0.70Δ / short 0.30Δ · target +100% · stop none | 239 | $29.14 | 62% | +0.046 | +0.045 |
| lights+momentum · 45 DTE · long 0.70Δ / short 0.30Δ · target +100% · stop −50% | 253 | $17.60 | 61% | +0.028 | +0.034 |
| lights+momentum · 45 DTE · long 0.70Δ / short 0.30Δ · target +50% · stop none | 242 | $28.65 | 62% | +0.040 | +0.040 |
| lights+momentum · 45 DTE · long 0.70Δ / short 0.30Δ · target +50% · stop −50% | 255 | $10.54 | 60% | +0.017 | +0.031 |
| lights+momentum · 45 DTE · long 0.70Δ / short 0.30Δ · target hold · stop none | 239 | $29.14 | 62% | +0.046 | +0.045 |
| lights+momentum · 45 DTE · long 0.70Δ / short 0.30Δ · target hold · stop −50% | 253 | $17.60 | 61% | +0.028 | +0.036 |
| lights+momentum · 60 DTE · long 0.50Δ / short 0.20Δ · target +100% · stop none | 153 | $74.87 | 52% | +0.096 | +0.082 |
| lights+momentum · 60 DTE · long 0.50Δ / short 0.20Δ · target +100% · stop −50% | 174 | $33.09 | 47% | +0.046 | +0.057 |
| lights+momentum · 60 DTE · long 0.50Δ / short 0.20Δ · target +50% · stop none | 185 | $53.42 | 59% | +0.069 | +0.067 |
| lights+momentum · 60 DTE · long 0.50Δ / short 0.20Δ · target +50% · stop −50% | 217 | $32.32 | 54% | +0.044 | +0.049 |
| lights+momentum · 60 DTE · long 0.50Δ / short 0.20Δ · target hold · stop none | 150 | $83.53 | 54% | +0.104 | +0.087 |
| lights+momentum · 60 DTE · long 0.50Δ / short 0.20Δ · target hold · stop −50% | 168 | $41.18 | 47% | +0.055 | +0.061 |
| lights+momentum · 60 DTE · long 0.50Δ / short 0.30Δ · target +100% · stop none | 152 | $81.07 | 58% | +0.085 | +0.072 |
| lights+momentum · 60 DTE · long 0.50Δ / short 0.30Δ · target +100% · stop −50% | 173 | $28.40 | 50% | +0.031 | +0.046 |
| lights+momentum · 60 DTE · long 0.50Δ / short 0.30Δ · target +50% · stop none | 175 | $57.55 | 61% | +0.062 | +0.064 |
| lights+momentum · 60 DTE · long 0.50Δ / short 0.30Δ · target +50% · stop −50% | 204 | $39.51 | 55% | +0.046 | +0.046 |
| lights+momentum · 60 DTE · long 0.50Δ / short 0.30Δ · target hold · stop none | 152 | $81.07 | 58% | +0.085 | +0.074 |
| lights+momentum · 60 DTE · long 0.50Δ / short 0.30Δ · target hold · stop −50% | 173 | $28.40 | 50% | +0.031 | +0.048 |
| lights+momentum · 60 DTE · long 0.70Δ / short 0.20Δ · target +100% · stop none | 138 | $57.15 | 59% | +0.082 | +0.079 |
| lights+momentum · 60 DTE · long 0.70Δ / short 0.20Δ · target +100% · stop −50% | 145 | $55.69 | 59% | +0.075 | +0.066 |
| lights+momentum · 60 DTE · long 0.70Δ / short 0.20Δ · target +50% · stop none | 154 | $52.65 | 60% | +0.080 | +0.071 |
| lights+momentum · 60 DTE · long 0.70Δ / short 0.20Δ · target +50% · stop −50% | 163 | $44.18 | 58% | +0.065 | +0.063 |
| lights+momentum · 60 DTE · long 0.70Δ / short 0.20Δ · target hold · stop none | 138 | $57.15 | 59% | +0.082 | +0.080 |
| lights+momentum · 60 DTE · long 0.70Δ / short 0.20Δ · target hold · stop −50% | 145 | $55.69 | 59% | +0.075 | +0.068 |
| lights+momentum · 60 DTE · long 0.70Δ / short 0.30Δ · target +100% · stop none | 149 | $60.34 | 64% | +0.081 | +0.074 |
| lights+momentum · 60 DTE · long 0.70Δ / short 0.30Δ · target +100% · stop −50% | 158 | $50.96 | 62% | +0.066 | +0.058 |
| lights+momentum · 60 DTE · long 0.70Δ / short 0.30Δ · target +50% · stop none | 153 | $52.13 | 63% | +0.068 | +0.066 |
| lights+momentum · 60 DTE · long 0.70Δ / short 0.30Δ · target +50% · stop −50% | 164 | $42.53 | 61% | +0.059 | +0.054 |
| lights+momentum · 60 DTE · long 0.70Δ / short 0.30Δ · target hold · stop none | 149 | $60.34 | 64% | +0.081 | +0.075 |
| lights+momentum · 60 DTE · long 0.70Δ / short 0.30Δ · target hold · stop −50% | 158 | $50.96 | 62% | +0.066 | +0.058 |

