# Volatility Forecast Report (2026-10-20)

Phase 1b of the volatility strategy. Pre-registered in `docs/research/iron_condor_preregistration.md` (Amendment 1, F1.2). No option prices are used here.

**Model bar: FAILED.** The full model (M2) did not beat VIX alone by enough. The forecast adds nothing beyond what option prices already contain, so the straddle and the condor gate are no-go; the plain condor test continues.

## What was measured

- SPY realized volatility from 2,713 trading days of 1-minute bars (2016-01-05 to 2026-10-19): 5-minute returns in regular hours plus the overnight move.
- Each month the models were refitted using only past data whose outcome was already known, then used to forecast every day of that month. M0 = VIX alone; M1 = recent realized volatility (HAR-RV); M2 = M1 + VIX + down-day size + upcoming FOMC/CPI count.
- Score: QLIKE loss (lower is better; the standard way to score variance forecasts). Test: one-sided Diebold-Mariano, Newey-West with 21 lags (forecast windows overlap).

## Discovery (forecasts 2018–2021): 1,008 daily forecasts

| Model | Mean QLIKE loss |
|---|---|
| M0 | 0.8809 |
| M1 | 0.9239 |
| M2 | 0.8823 |

- M2 vs M0 (VIX): t = 0.11, one-sided p = 0.5434
- M2 vs M1 (HAR-RV): t = -0.68, one-sided p = 0.2482

## Confirmation (2022 on, information only): 1,182 daily forecasts

| Model | Mean QLIKE loss |
|---|---|
| M0 | 0.1970 |
| M1 | 0.1981 |
| M2 | 0.1906 |

- M2 vs M0 (VIX): t = -1.36, one-sided p = 0.0873
- M2 vs M1 (HAR-RV): t = -0.50, one-sided p = 0.3098

## M2 coefficients (fitted on discovery data, for reading only)

| Input | Coefficient |
|---|---|
| intercept | -0.486 |
| log_rv_1d | +0.083 |
| log_rv_5d | +0.140 |
| log_rv_22d | -0.021 |
| log_vix | +0.718 |
| neg_ret | +2.309 |
| events | +0.061 |

## Caveats

- QLIKE scores variance forecasts. Beating VIX on QLIKE means better volatility *forecasts*; it doesn't by itself mean trades make money, which the backtest (Phases 2–3) decides.
- VIX is a 30-calendar-day S&P 500 measure and the target is 21 SPY trading days; M0 is fitted by regression, so the level difference (VIX usually sits above realized volatility) is accounted for.
- Confirmation-period numbers are reported for information and can't change the model (pre-registration).
