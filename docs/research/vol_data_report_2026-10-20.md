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

**Not loaded yet.** The free files need a free checkout on optionsdx.com, which the owner does by hand. Then: `python main.py vol-data load --path <folder>` and re-run this report.

