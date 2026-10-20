# Iron Condor Backtest — Phase 3 (2026-10-20)

SPY iron condors on OptionsDX end-of-day quotes, exactly as pre-registered in `docs/research/iron_condor_preregistration.md` (sections B–E, Amendments 3–4). Fills at bid/ask, $0.05 per contract per leg per side, $100,000 fixed account, 1% risk per condor, up to 3 open, one new condor a week. Discovery = entries 2016–2021 (all 162 settings); confirmation = entries 2022–2023 (chosen setting only; 2024–2026 needs the Phase 5 data).

## Result: **NO-GO** — 5 of 7 bars failed

Chosen setting (highest smoothed discovery score): **30 DTE · 0.20Δ · $5 · target 50% · stop none**

| Bar | Result | Detail |
|---|---|---|
| 1 · Profitable in both periods, t ≥ 2 | ❌ fail | discovery $-58.58/trade (130), confirmation $-19.41/trade (77), per-trade t = -3.99; monthly-P&L t = -3.06 (information) |
| 2 · Survives worse fills | ❌ fail | cost-stress average $-59.91/trade |
| 3 · Losses stay within plan | ❌ fail | 2 trades lost more than planned (worst $-1,151) |
| 4 · Drawdown under 10% | ✅ pass | worst drawdown $9,220 (9.2%) on 2023-12-01 |
| 5 · Crash periods | ✅ pass | Feb 2018: drawdown 0.0%, closed P&L $0; COVID 2020: drawdown 2.5%, closed P&L $-2,342; 2022 bear: drawdown 0.8%, closed P&L $-81; Aug 2024: no data yet (needs Phase 5); Apr 2025: no data yet (needs Phase 5) |
| 6 · Not a lucky setting | ❌ fail | 1 neighbours with ≥30 trades: 45/0.20/$5 $-105.89 |
| 7 · Not a few lucky trades | ❌ fail | without the best 11 trades: $-53.83/trade |

## Chosen setting, full period

- All: 207 trades, $-44.01/trade, win rate 47%, worst $-1,151, total $-9,110
- Discovery: 130 trades, $-58.58/trade, win rate 45%, worst $-1,151, total $-7,615
- Confirmation: 77 trades, $-19.41/trade, win rate 52%, worst $-442, total $-1,495
- Cost-stress fills: $-59.91/trade · optimistic fills (information): $-28.11/trade
- Exits: time_exit 207
- Still open at the end of the data: 1
- Equity chart: `docs/research/img/condor_equity_2026-10-20.png`

### Per year

| Year | Trades | Total P&L | Avg/trade | Worst trade |
|---|---|---|---|---|
| 2016 | 8 | $-640 | $-80.05 | $-359 |
| 2017 | 2 | $-22 | $-10.80 | $-123 |
| 2018 | 13 | $-1,562 | $-120.18 | $-1,151 |
| 2019 | 21 | $-967 | $-46.03 | $-383 |
| 2020 | 43 | $-3,003 | $-69.85 | $-747 |
| 2021 | 43 | $-1,420 | $-33.03 | $-463 |
| 2022 | 41 | $-29 | $-0.72 | $-442 |
| 2023 | 36 | $-1,465 | $-40.71 | $-383 |

## Sensitivity checks (chosen setting only; can't change the result)

- **IV Rank filter on (VIX IV Rank ≥ 30):** 84 trades, $-67.90/trade, win rate 46%, worst $-1,151, total $-5,703
- **Event filter off:** 255 trades, $-50.13/trade, win rate 47%, worst $-1,151, total $-12,783
- **Time exit at 14 days:** 205 trades, $-70.84/trade, win rate 51%, worst $-1,151, total $-14,521
- **Term-structure filter: VIX < VIX3M (Amendment 4, information only):** 189 trades, $-36.77/trade, win rate 49%, worst $-1,151, total $-6,949

## Volatility-surface splits (Amendment 4, information only)

- **Skew above its 1-year median (steep):** 116 trades, $-63.96/trade, win rate 42%, worst $-1,151, total $-7,420
- **Skew at or below its 1-year median:** 84 trades, $-16.77/trade, win rate 55%, worst $-463, total $-1,408
- **VIX ÷ VIX3M 1 or above (inverted):** 21 trades, $-103.88/trade, win rate 38%, worst $-449, total $-2,181
- **VIX ÷ VIX3M below 1 (normal):** 186 trades, $-37.25/trade, win rate 48%, worst $-1,151, total $-6,928
- **VIX9D ÷ VIX 1 or above (short-term fear):** 55 trades, $-61.22/trade, win rate 51%, worst $-449, total $-3,367
- **VIX9D ÷ VIX below 1 (normal):** 152 trades, $-37.78/trade, win rate 46%, worst $-1,151, total $-5,743

## All 162 settings, discovery period (2016–2021)

Score = average profit per trade ÷ planned max loss; settings with under 60 trades get no score.

| Setting | Trades | Avg P&L | Win rate | Score | Smoothed |
|---|---|---|---|---|---|
| 30 DTE · 0.10Δ · $5 · target 25% · stop 1.5× | 0 | – | – | – | – |
| 30 DTE · 0.10Δ · $5 · target 25% · stop 2× | 0 | – | – | – | – |
| 30 DTE · 0.10Δ · $5 · target 25% · stop none | 0 | – | – | – | – |
| 30 DTE · 0.10Δ · $5 · target 50% · stop 1.5× | 0 | – | – | – | – |
| 30 DTE · 0.10Δ · $5 · target 50% · stop 2× | 0 | – | – | – | – |
| 30 DTE · 0.10Δ · $5 · target 50% · stop none | 0 | – | – | – | – |
| 30 DTE · 0.10Δ · $5 · target hold · stop 1.5× | 0 | – | – | – | – |
| 30 DTE · 0.10Δ · $5 · target hold · stop 2× | 0 | – | – | – | – |
| 30 DTE · 0.10Δ · $5 · target hold · stop none | 0 | – | – | – | – |
| 30 DTE · 0.10Δ · $10 · target 25% · stop 1.5× | 0 | – | – | – | – |
| 30 DTE · 0.10Δ · $10 · target 25% · stop 2× | 0 | – | – | – | – |
| 30 DTE · 0.10Δ · $10 · target 25% · stop none | 0 | – | – | – | – |
| 30 DTE · 0.10Δ · $10 · target 50% · stop 1.5× | 0 | – | – | – | – |
| 30 DTE · 0.10Δ · $10 · target 50% · stop 2× | 0 | – | – | – | – |
| 30 DTE · 0.10Δ · $10 · target 50% · stop none | 0 | – | – | – | – |
| 30 DTE · 0.10Δ · $10 · target hold · stop 1.5× | 0 | – | – | – | – |
| 30 DTE · 0.10Δ · $10 · target hold · stop 2× | 0 | – | – | – | – |
| 30 DTE · 0.10Δ · $10 · target hold · stop none | 0 | – | – | – | – |
| 30 DTE · 0.16Δ · $5 · target 25% · stop 1.5× | 15 | $15.20 | 73% | – | – |
| 30 DTE · 0.16Δ · $5 · target 25% · stop 2× | 15 | $15.20 | 73% | – | – |
| 30 DTE · 0.16Δ · $5 · target 25% · stop none | 15 | $15.20 | 73% | – | – |
| 30 DTE · 0.16Δ · $5 · target 50% · stop 1.5× | 15 | $11.73 | 60% | – | – |
| 30 DTE · 0.16Δ · $5 · target 50% · stop 2× | 15 | $11.73 | 60% | – | – |
| 30 DTE · 0.16Δ · $5 · target 50% · stop none | 15 | $11.73 | 60% | – | – |
| 30 DTE · 0.16Δ · $5 · target hold · stop 1.5× | 15 | $11.33 | 60% | – | – |
| 30 DTE · 0.16Δ · $5 · target hold · stop 2× | 15 | $11.33 | 60% | – | – |
| 30 DTE · 0.16Δ · $5 · target hold · stop none | 15 | $11.33 | 60% | – | – |
| 30 DTE · 0.16Δ · $10 · target 25% · stop 1.5× | 3 | $50.60 | 67% | – | – |
| 30 DTE · 0.16Δ · $10 · target 25% · stop 2× | 3 | $50.60 | 67% | – | – |
| 30 DTE · 0.16Δ · $10 · target 25% · stop none | 3 | $50.60 | 67% | – | – |
| 30 DTE · 0.16Δ · $10 · target 50% · stop 1.5× | 3 | $30.27 | 67% | – | – |
| 30 DTE · 0.16Δ · $10 · target 50% · stop 2× | 3 | $30.27 | 67% | – | – |
| 30 DTE · 0.16Δ · $10 · target 50% · stop none | 3 | $30.27 | 67% | – | – |
| 30 DTE · 0.16Δ · $10 · target hold · stop 1.5× | 3 | $30.27 | 67% | – | – |
| 30 DTE · 0.16Δ · $10 · target hold · stop 2× | 3 | $30.27 | 67% | – | – |
| 30 DTE · 0.16Δ · $10 · target hold · stop none | 3 | $30.27 | 67% | – | – |
| 30 DTE · 0.20Δ · $5 · target 25% · stop 1.5× | 130 | $-62.95 | 46% | -0.089 | -0.108 |
| 30 DTE · 0.20Δ · $5 · target 25% · stop 2× | 130 | $-60.46 | 47% | -0.085 | -0.098 |
| 30 DTE · 0.20Δ · $5 · target 25% · stop none | 130 | $-54.97 | 47% | -0.078 | -0.098 |
| 30 DTE · 0.20Δ · $5 · target 50% · stop 1.5× | 130 | $-66.56 | 44% | -0.094 | -0.103 |
| 30 DTE · 0.20Δ · $5 · target 50% · stop 2× | 130 | $-64.07 | 45% | -0.090 | -0.096 |
| 30 DTE · 0.20Δ · $5 · target 50% · stop none **← chosen** | 130 | $-58.58 | 45% | -0.083 | -0.095 |
| 30 DTE · 0.20Δ · $5 · target hold · stop 1.5× | 130 | $-66.56 | 44% | -0.094 | -0.106 |
| 30 DTE · 0.20Δ · $5 · target hold · stop 2× | 130 | $-64.07 | 45% | -0.090 | -0.098 |
| 30 DTE · 0.20Δ · $5 · target hold · stop none | 130 | $-58.58 | 45% | -0.083 | -0.098 |
| 30 DTE · 0.20Δ · $10 · target 25% · stop 1.5× | 28 | $-38.90 | 43% | – | – |
| 30 DTE · 0.20Δ · $10 · target 25% · stop 2× | 28 | $-38.54 | 43% | – | – |
| 30 DTE · 0.20Δ · $10 · target 25% · stop none | 28 | $-38.54 | 43% | – | – |
| 30 DTE · 0.20Δ · $10 · target 50% · stop 1.5× | 28 | $-41.51 | 43% | – | – |
| 30 DTE · 0.20Δ · $10 · target 50% · stop 2× | 28 | $-41.15 | 43% | – | – |
| 30 DTE · 0.20Δ · $10 · target 50% · stop none | 28 | $-41.15 | 43% | – | – |
| 30 DTE · 0.20Δ · $10 · target hold · stop 1.5× | 28 | $-41.51 | 43% | – | – |
| 30 DTE · 0.20Δ · $10 · target hold · stop 2× | 28 | $-41.15 | 43% | – | – |
| 30 DTE · 0.20Δ · $10 · target hold · stop none | 28 | $-41.15 | 43% | – | – |
| 45 DTE · 0.10Δ · $5 · target 25% · stop 1.5× | 0 | – | – | – | – |
| 45 DTE · 0.10Δ · $5 · target 25% · stop 2× | 0 | – | – | – | – |
| 45 DTE · 0.10Δ · $5 · target 25% · stop none | 0 | – | – | – | – |
| 45 DTE · 0.10Δ · $5 · target 50% · stop 1.5× | 0 | – | – | – | – |
| 45 DTE · 0.10Δ · $5 · target 50% · stop 2× | 0 | – | – | – | – |
| 45 DTE · 0.10Δ · $5 · target 50% · stop none | 0 | – | – | – | – |
| 45 DTE · 0.10Δ · $5 · target hold · stop 1.5× | 0 | – | – | – | – |
| 45 DTE · 0.10Δ · $5 · target hold · stop 2× | 0 | – | – | – | – |
| 45 DTE · 0.10Δ · $5 · target hold · stop none | 0 | – | – | – | – |
| 45 DTE · 0.10Δ · $10 · target 25% · stop 1.5× | 0 | – | – | – | – |
| 45 DTE · 0.10Δ · $10 · target 25% · stop 2× | 0 | – | – | – | – |
| 45 DTE · 0.10Δ · $10 · target 25% · stop none | 0 | – | – | – | – |
| 45 DTE · 0.10Δ · $10 · target 50% · stop 1.5× | 0 | – | – | – | – |
| 45 DTE · 0.10Δ · $10 · target 50% · stop 2× | 0 | – | – | – | – |
| 45 DTE · 0.10Δ · $10 · target 50% · stop none | 0 | – | – | – | – |
| 45 DTE · 0.10Δ · $10 · target hold · stop 1.5× | 0 | – | – | – | – |
| 45 DTE · 0.10Δ · $10 · target hold · stop 2× | 0 | – | – | – | – |
| 45 DTE · 0.10Δ · $10 · target hold · stop none | 0 | – | – | – | – |
| 45 DTE · 0.16Δ · $5 · target 25% · stop 1.5× | 23 | $-72.02 | 52% | – | – |
| 45 DTE · 0.16Δ · $5 · target 25% · stop 2× | 23 | $-89.67 | 52% | – | – |
| 45 DTE · 0.16Δ · $5 · target 25% · stop none | 22 | $-144.16 | 45% | – | – |
| 45 DTE · 0.16Δ · $5 · target 50% · stop 1.5× | 20 | $-82.50 | 35% | – | – |
| 45 DTE · 0.16Δ · $5 · target 50% · stop 2× | 20 | $-88.60 | 35% | – | – |
| 45 DTE · 0.16Δ · $5 · target 50% · stop none | 20 | $-149.00 | 30% | – | – |
| 45 DTE · 0.16Δ · $5 · target hold · stop 1.5× | 20 | $-89.60 | 35% | – | – |
| 45 DTE · 0.16Δ · $5 · target hold · stop 2× | 20 | $-95.70 | 35% | – | – |
| 45 DTE · 0.16Δ · $5 · target hold · stop none | 20 | $-156.10 | 30% | – | – |
| 45 DTE · 0.16Δ · $10 · target 25% · stop 1.5× | 5 | $-66.40 | 80% | – | – |
| 45 DTE · 0.16Δ · $10 · target 25% · stop 2× | 5 | $-66.40 | 80% | – | – |
| 45 DTE · 0.16Δ · $10 · target 25% · stop none | 5 | $-66.40 | 80% | – | – |
| 45 DTE · 0.16Δ · $10 · target 50% · stop 1.5× | 5 | $-9.80 | 80% | – | – |
| 45 DTE · 0.16Δ · $10 · target 50% · stop 2× | 5 | $-9.80 | 80% | – | – |
| 45 DTE · 0.16Δ · $10 · target 50% · stop none | 5 | $-9.80 | 80% | – | – |
| 45 DTE · 0.16Δ · $10 · target hold · stop 1.5× | 4 | $-10.65 | 75% | – | – |
| 45 DTE · 0.16Δ · $10 · target hold · stop 2× | 4 | $-10.65 | 75% | – | – |
| 45 DTE · 0.16Δ · $10 · target hold · stop none | 4 | $-10.65 | 75% | – | – |
| 45 DTE · 0.20Δ · $5 · target 25% · stop 1.5× | 142 | $-119.34 | 53% | -0.164 | -0.126 |
| 45 DTE · 0.20Δ · $5 · target 25% · stop 2× | 141 | $-107.00 | 53% | -0.148 | -0.125 |
| 45 DTE · 0.20Δ · $5 · target 25% · stop none | 139 | $-105.74 | 55% | -0.145 | -0.116 |
| 45 DTE · 0.20Δ · $5 · target 50% · stop 1.5× | 132 | $-109.31 | 48% | -0.147 | -0.132 |
| 45 DTE · 0.20Δ · $5 · target 50% · stop 2× | 132 | $-98.14 | 48% | -0.134 | -0.128 |
| 45 DTE · 0.20Δ · $5 · target 50% · stop none | 130 | $-105.89 | 48% | -0.139 | -0.123 |
| 45 DTE · 0.20Δ · $5 · target hold · stop 1.5× | 132 | $-108.32 | 48% | -0.145 | -0.128 |
| 45 DTE · 0.20Δ · $5 · target hold · stop 2× | 132 | $-96.95 | 48% | -0.132 | -0.126 |
| 45 DTE · 0.20Δ · $5 · target hold · stop none | 130 | $-104.89 | 48% | -0.138 | -0.121 |
| 45 DTE · 0.20Δ · $10 · target 25% · stop 1.5× | 46 | $-64.53 | 57% | – | – |
| 45 DTE · 0.20Δ · $10 · target 25% · stop 2× | 46 | $-65.73 | 57% | – | – |
| 45 DTE · 0.20Δ · $10 · target 25% · stop none | 46 | $-64.47 | 57% | – | – |
| 45 DTE · 0.20Δ · $10 · target 50% · stop 1.5× | 40 | $-91.48 | 52% | – | – |
| 45 DTE · 0.20Δ · $10 · target 50% · stop 2× | 40 | $-92.85 | 52% | – | – |
| 45 DTE · 0.20Δ · $10 · target 50% · stop none | 40 | $-91.40 | 52% | – | – |
| 45 DTE · 0.20Δ · $10 · target hold · stop 1.5× | 40 | $-93.85 | 52% | – | – |
| 45 DTE · 0.20Δ · $10 · target hold · stop 2× | 40 | $-95.23 | 52% | – | – |
| 45 DTE · 0.20Δ · $10 · target hold · stop none | 40 | $-93.78 | 52% | – | – |
| 60 DTE · 0.10Δ · $5 · target 25% · stop 1.5× | 0 | – | – | – | – |
| 60 DTE · 0.10Δ · $5 · target 25% · stop 2× | 0 | – | – | – | – |
| 60 DTE · 0.10Δ · $5 · target 25% · stop none | 0 | – | – | – | – |
| 60 DTE · 0.10Δ · $5 · target 50% · stop 1.5× | 0 | – | – | – | – |
| 60 DTE · 0.10Δ · $5 · target 50% · stop 2× | 0 | – | – | – | – |
| 60 DTE · 0.10Δ · $5 · target 50% · stop none | 0 | – | – | – | – |
| 60 DTE · 0.10Δ · $5 · target hold · stop 1.5× | 0 | – | – | – | – |
| 60 DTE · 0.10Δ · $5 · target hold · stop 2× | 0 | – | – | – | – |
| 60 DTE · 0.10Δ · $5 · target hold · stop none | 0 | – | – | – | – |
| 60 DTE · 0.10Δ · $10 · target 25% · stop 1.5× | 0 | – | – | – | – |
| 60 DTE · 0.10Δ · $10 · target 25% · stop 2× | 0 | – | – | – | – |
| 60 DTE · 0.10Δ · $10 · target 25% · stop none | 0 | – | – | – | – |
| 60 DTE · 0.10Δ · $10 · target 50% · stop 1.5× | 0 | – | – | – | – |
| 60 DTE · 0.10Δ · $10 · target 50% · stop 2× | 0 | – | – | – | – |
| 60 DTE · 0.10Δ · $10 · target 50% · stop none | 0 | – | – | – | – |
| 60 DTE · 0.10Δ · $10 · target hold · stop 1.5× | 0 | – | – | – | – |
| 60 DTE · 0.10Δ · $10 · target hold · stop 2× | 0 | – | – | – | – |
| 60 DTE · 0.10Δ · $10 · target hold · stop none | 0 | – | – | – | – |
| 60 DTE · 0.16Δ · $5 · target 25% · stop 1.5× | 15 | $-124.80 | 53% | – | – |
| 60 DTE · 0.16Δ · $5 · target 25% · stop 2× | 15 | $-99.33 | 60% | – | – |
| 60 DTE · 0.16Δ · $5 · target 25% · stop none | 15 | $-99.33 | 60% | – | – |
| 60 DTE · 0.16Δ · $5 · target 50% · stop 1.5× | 14 | $-136.23 | 50% | – | – |
| 60 DTE · 0.16Δ · $5 · target 50% · stop 2× | 14 | $-108.94 | 57% | – | – |
| 60 DTE · 0.16Δ · $5 · target 50% · stop none | 14 | $-108.94 | 57% | – | – |
| 60 DTE · 0.16Δ · $5 · target hold · stop 1.5× | 14 | $-151.94 | 50% | – | – |
| 60 DTE · 0.16Δ · $5 · target hold · stop 2× | 13 | $-84.65 | 62% | – | – |
| 60 DTE · 0.16Δ · $5 · target hold · stop none | 13 | $-84.65 | 62% | – | – |
| 60 DTE · 0.16Δ · $10 · target 25% · stop 1.5× | 5 | $-83.40 | 80% | – | – |
| 60 DTE · 0.16Δ · $10 · target 25% · stop 2× | 5 | $-83.40 | 80% | – | – |
| 60 DTE · 0.16Δ · $10 · target 25% · stop none | 5 | $-83.40 | 80% | – | – |
| 60 DTE · 0.16Δ · $10 · target 50% · stop 1.5× | 4 | $-120.90 | 50% | – | – |
| 60 DTE · 0.16Δ · $10 · target 50% · stop 2× | 4 | $-120.90 | 50% | – | – |
| 60 DTE · 0.16Δ · $10 · target 50% · stop none | 4 | $-120.90 | 50% | – | – |
| 60 DTE · 0.16Δ · $10 · target hold · stop 1.5× | 4 | $-103.15 | 50% | – | – |
| 60 DTE · 0.16Δ · $10 · target hold · stop 2× | 4 | $-103.15 | 50% | – | – |
| 60 DTE · 0.16Δ · $10 · target hold · stop none | 4 | $-103.15 | 50% | – | – |
| 60 DTE · 0.20Δ · $5 · target 25% · stop 1.5× | 115 | $-57.95 | 67% | -0.081 | -0.106 |
| 60 DTE · 0.20Δ · $5 · target 25% · stop 2× | 113 | $-53.44 | 67% | -0.074 | -0.096 |
| 60 DTE · 0.20Δ · $5 · target 25% · stop none | 112 | $-49.46 | 67% | -0.069 | -0.096 |
| 60 DTE · 0.20Δ · $5 · target 50% · stop 1.5× | 96 | $-80.64 | 51% | -0.107 | -0.112 |
| 60 DTE · 0.20Δ · $5 · target 50% · stop 2× | 95 | $-79.78 | 51% | -0.106 | -0.106 |
| 60 DTE · 0.20Δ · $5 · target 50% · stop none | 95 | $-73.76 | 51% | -0.097 | -0.104 |
| 60 DTE · 0.20Δ · $5 · target hold · stop 1.5× | 90 | $-91.03 | 48% | -0.121 | -0.123 |
| 60 DTE · 0.20Δ · $5 · target hold · stop 2× | 90 | $-89.88 | 48% | -0.119 | -0.118 |
| 60 DTE · 0.20Δ · $5 · target hold · stop none | 90 | $-84.52 | 48% | -0.112 | -0.117 |
| 60 DTE · 0.20Δ · $10 · target 25% · stop 1.5× | 56 | $-37.92 | 70% | – | – |
| 60 DTE · 0.20Δ · $10 · target 25% · stop 2× | 55 | $-24.29 | 71% | – | – |
| 60 DTE · 0.20Δ · $10 · target 25% · stop none | 55 | $-23.73 | 71% | – | – |
| 60 DTE · 0.20Δ · $10 · target 50% · stop 1.5× | 47 | $-56.91 | 55% | – | – |
| 60 DTE · 0.20Δ · $10 · target 50% · stop 2× | 47 | $-42.63 | 55% | – | – |
| 60 DTE · 0.20Δ · $10 · target 50% · stop none | 47 | $-41.97 | 55% | – | – |
| 60 DTE · 0.20Δ · $10 · target hold · stop 1.5× | 44 | $-54.49 | 52% | – | – |
| 60 DTE · 0.20Δ · $10 · target hold · stop 2× | 44 | $-41.10 | 52% | – | – |
| 60 DTE · 0.20Δ · $10 · target hold · stop none | 44 | $-30.40 | 52% | – | – |


## Review notes (written after the run; these don't change the result)

1. **Every setting with enough trades lost money.** In discovery, 18 of the 108 settings that had any trades
   averaged a profit. All 18 were 30-day 16-delta settings with only 3–15 trades in six years. Every setting with
   60+ trades lost money.
2. **Vendor data gaps.** OptionsDX sometimes leaves strikes out of its files for days or weeks at a time (for
   example 250 and 255 for the 2018-12-26 expiry, missing from 3 to 25 December 2018). Under Amendment 3, a
   position with an unpriced leg can't exit, so its last mark is carried forward. This touched 66 of the chosen
   setting's 207 trades and caused its worst loss (−$1,151, opened 2018-11-26, stuck until expiry day).
   **The verdict doesn't depend on it:** the 141 trades with no gap days averaged −$28.62 (t = −2.73), and
   −$13.69 even with optimistic fills.
3. **Bar 3 failures:** one is the data-gap trade above. The other is genuine: on 2020-02-28 (COVID crash) closing
   cost $5.08 against $5 wings, because the bid-ask spreads widened. Closing at market can cost more than the
   theoretical max loss.
4. **No profit-target exits for the chosen setting** is expected, not a bug. With a ~30-day entry and a 21-day
   time exit, each trade is held about 9 days, too short to decay 50%. The 45- and 60-day settings did hit their
   profit targets (16–71 times).
5. **Why it loses:** each condor pays the bid-ask spread on 4 legs going in and 4 coming out, about $0.25 a share
   against a typical $1.45 credit. Over a 1–3 week hold, the premium that decays doesn't cover that cost plus the
   occasional big move.
