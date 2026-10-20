# Volatility Strategy — Phase 1 Data Report (2026-10-20)

Coverage and data checks only. No trade results are computed in Phase 1, so nothing here can influence the pre-registered rules.

SPY trading days in the stored minute history since 2016: **2,714**.

## Volatility indices (CBOE, free)

| Index | First day | Last day | Days | SPY trading days since 2016 missing | Suspicious values |
|---|---|---|---|---|---|
| VIX | 1990-01-02 | 2026-10-19 | 9,297 | 0 | none |
| VIX3M | 2009-09-18 | 2026-10-19 | 4,297 | 0 | none |
| VIX9D | 2011-01-04 | 2026-10-19 | 3,971 | 0 | 2 one-day jumps >150%: 2018-02-05, 2024-12-18 |

## Event calendar

Only **scheduled** FOMC meetings (and the scheduled-then-cancelled March 2020 one) go in the event filter, because unscheduled meetings weren't known in advance. Unscheduled meetings and notation votes are stored for reference only.

| Year | FOMC scheduled | FOMC cancelled | FOMC unscheduled | FOMC notation votes | CPI releases |
|---|---|---|---|---|---|
| 2015 | 8 | 0 | 0 | 0 | 11 |
| 2016 | 8 | 0 | 0 | 0 | 12 |
| 2017 | 8 | 0 | 0 | 0 | 12 |
| 2018 | 8 | 0 | 0 | 0 | 12 |
| 2019 | 8 | 0 | 1 | 0 | 12 |
| 2020 | 7 | 1 | 2 | 4 | 12 |
| 2021 | 8 | 0 | 0 | 0 | 12 |
| 2022 | 8 | 0 | 0 | 0 | 12 |
| 2023 | 8 | 0 | 0 | 0 | 12 |
| 2024 | 8 | 0 | 0 | 0 | 12 |
| 2025 | 8 | 0 | 0 | 1 | 11 |
| 2026 | 8 | 0 | 0 | 0 | 10 |
| 2027 | 8 | 0 | 0 | 0 | 0 |

Expected: 8 scheduled FOMC meetings a year (2020: 7 plus the cancelled March meeting); 12 CPI releases a year (2025: 11, because October 2025 CPI was never published during the government shutdown). CPI dates come from the BLS release archive and all appear in FRED/ALFRED's release history.

## SPY end-of-day option chains (OptionsDX)

- Coverage: **2010-01-04 to 2023-12-29**, 3,508 quote days from 168 files, 4,136,645 strike rows (kept: ≤ 100 DTE, strikes within ±25% of SPY).
- SPY trading days in that range with no chain: **13** (2016-04-18, 2017-09-27, 2019-03-01, 2019-05-24, 2019-06-07, 2019-09-30, 2020-06-18, 2020-11-27, 2020-12-24, 2022-05-11…)
- Quote days that were **not** SPY trading days (market holidays, stale repeated quotes): 10 (2018-12-05, 2022-01-17, 2022-02-21, 2022-04-15, 2022-05-30, 2022-06-20, 2022-07-04, 2022-09-05, 2022-11-24, 2022-12-26). The backtest uses SPY trading days only, so these are ignored.
- Snapshot time (hours, ET) by number of days: 16.0: 3,508
- SPY price in the files vs SPY's real 4:00 pm close (our minute history), 1,999 days: median gap 0.006%, 99th percentile 0.079%, days off by more than 0.5%: 2 (2018-12-24, 2020-03-24)

| Check | Calls | Puts |
|---|---|---|
| Missing bid or ask | 0.09% | 0.09% |
| Crossed (bid > ask) | 0.22% | 0.43% |
| Locked (bid = ask) | 2.10% | 3.47% |
| Missing delta | 0.02% | 0.02% |
| Missing or zero IV | 6.08% | 8.93% |

| Target DTE | Days with an expiry within ±7 days | Days where the chain reaches a 10-delta put and call |
|---|---|---|
| 14 | 3,321 of 3,508 | 3,298 of 3,508 |
| 30 | 3,233 of 3,508 | 3,217 of 3,508 |
| 45 | 3,051 of 3,508 | 3,017 of 3,508 |
| 60 | 2,235 of 3,508 | 2,217 of 3,508 |

