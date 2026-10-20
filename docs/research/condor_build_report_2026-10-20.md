# Iron Condor Builder Report (2026-10-20)

Phase 2 of the volatility strategy: what the entry rules (pre-registration section C1) produce on every SPY trading day from 2016 to 2023 (2,012 days), for each of the 18 entry settings. Entry side only. No exits or profit/loss are computed until Phase 3.

The once-a-week and 3-open limits depend on open positions and are applied in the backtest, so the counts below are *days a condor could be entered*, not trades.

## Outcome by entry setting

| DTE | Δ | Width | Condor built | No option data | No expiry near target | No usable delta | Short strikes overlap | Wing strike not listed | Bad quote on a leg | Credit under 25% of width | FOMC/CPI within 5 days | Too big for 1% risk | Median credit | Credit ÷ width | Median contracts | Credit filter blocked |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 30 | 0.10 | $5 | 0 | 13 | 0 | 0 | 0 | 687 | 313 | 999 | 0 | 0 | – | – | – | 100% ⚠️ |
| 30 | 0.10 | $10 | 0 | 13 | 0 | 0 | 0 | 886 | 235 | 878 | 0 | 0 | – | – | – | 100% ⚠️ |
| 30 | 0.16 | $5 | 87 | 13 | 0 | 0 | 0 | 557 | 266 | 1,051 | 38 | 0 | $1.31 | 26% | 2 | 89% ⚠️ |
| 30 | 0.16 | $10 | 4 | 13 | 0 | 0 | 0 | 733 | 235 | 1,021 | 6 | 0 | $2.52 | 25% | 1.0 | 99% ⚠️ |
| 30 | 0.20 | $5 | 464 | 13 | 0 | 0 | 0 | 518 | 247 | 526 | 244 | 0 | $1.45 | 29% | 2.0 | 43% |
| 30 | 0.20 | $10 | 204 | 13 | 0 | 0 | 0 | 695 | 223 | 767 | 110 | 0 | $2.78 | 28% | 1.0 | 71% ⚠️ |
| 45 | 0.10 | $5 | 0 | 13 | 57 | 3 | 1 | 573 | 299 | 1,066 | 0 | 0 | – | – | – | 100% ⚠️ |
| 45 | 0.10 | $10 | 0 | 13 | 57 | 3 | 1 | 827 | 240 | 871 | 0 | 0 | – | – | – | 100% ⚠️ |
| 45 | 0.16 | $5 | 138 | 13 | 57 | 3 | 1 | 500 | 238 | 987 | 75 | 0 | $1.32 | 26% | 2.0 | 82% ⚠️ |
| 45 | 0.16 | $10 | 15 | 13 | 57 | 3 | 1 | 736 | 181 | 1,000 | 6 | 0 | $2.60 | 26% | 1 | 98% ⚠️ |
| 45 | 0.20 | $5 | 519 | 13 | 57 | 3 | 1 | 464 | 228 | 401 | 326 | 0 | $1.48 | 30% | 2 | 32% |
| 45 | 0.20 | $10 | 257 | 13 | 57 | 3 | 1 | 656 | 216 | 655 | 154 | 0 | $2.82 | 28% | 1 | 61% ⚠️ |
| 60 | 0.10 | $5 | 0 | 13 | 686 | 0 | 0 | 236 | 298 | 779 | 0 | 0 | – | – | – | 100% ⚠️ |
| 60 | 0.10 | $10 | 0 | 13 | 686 | 0 | 0 | 346 | 241 | 726 | 0 | 0 | – | – | – | 100% ⚠️ |
| 60 | 0.16 | $5 | 124 | 13 | 686 | 0 | 0 | 184 | 243 | 679 | 83 | 0 | $1.31 | 26% | 2.0 | 77% ⚠️ |
| 60 | 0.16 | $10 | 16 | 13 | 686 | 0 | 0 | 225 | 216 | 844 | 12 | 0 | $2.59 | 26% | 1.0 | 97% ⚠️ |
| 60 | 0.20 | $5 | 478 | 13 | 686 | 0 | 0 | 154 | 217 | 145 | 319 | 0 | $1.50 | 30% | 2.0 | 15% |
| 60 | 0.20 | $10 | 303 | 13 | 686 | 0 | 0 | 188 | 201 | 444 | 177 | 0 | $2.86 | 29% | 1 | 48% |

Credit filter blocked = share of days that got as far as the credit check (C1.7) and failed it. The pre-registration says this is reported, not changed, if it exceeds 50% (⚠️). **14 of 18 settings exceed 50%.**

Vendor deltas used where our solver refused a quote (counted on built days, all legs priced that day): 2,458.

## Examples (16-delta, $5 wings)

- **2018-02-05 · 30 DTE:** wing strike 235 isn't listed
- **2020-03-16 · 30 DTE:** FOMC or CPI on 2020-03-18, within 5 trading days
- **2022-06-13 · 30 DTE:** wing strike 335, 409 isn't listed
- **2023-07-03 · 30 DTE:** credit $0.88 is under 25% of the $5 wing width
- **2018-02-05 · 45 DTE:** credit $1.09 is under 25% of the $5 wing width
- **2020-03-16 · 45 DTE:** credit $0.36 is under 25% of the $5 wing width
- **2022-06-13 · 45 DTE:** FOMC or CPI on 2022-06-15, within 5 trading days
- **2023-07-03 · 45 DTE:** wing strike 469 isn't listed
- **2018-02-05 · 60 DTE:** no expiry within 7 days of 60 days out
- **2020-03-16 · 60 DTE:** wing strike 175, 300 isn't listed
- **2022-06-13 · 60 DTE:** FOMC or CPI on 2022-06-15, within 5 trading days
- **2023-07-03 · 60 DTE:** no expiry within 7 days of 60 days out
