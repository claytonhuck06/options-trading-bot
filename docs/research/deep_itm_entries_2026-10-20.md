# Why the trader tried to buy impossible deep in-the-money spreads (2026-10-20)

Sources: `data/storage/options_quant.db` (`orders`, `candidate_trades`, `pricing_residuals`, `option_quotes`) and `logs/trader_2026-10-20.log`. SPY was about 769.4 all session. Paper account only. Nothing filled and no money moved.

> **Bottom line.** All four entries rested on **bad quotes for in-the-money calls** from the free "indicative" option feed. The scanner never compared those calls against the matching puts. If it had, every one of the four would have shown up as impossible pricing. A new screening rule, `inconsistent_with_parity`, makes that comparison. On today's stored data it blocks **4 of 4** of today's eligible trades, and it does not block a fairly priced spread. The fix is on branch `claude/jovial-meitner-34ec25` with tests, and it is **not deployed**. The running trader is unchanged.

## 1. What the scanner saw

All four eligible candidates of the day are listed below. Each one was "anchored" on an in-the-money call that the model thought was rich or cheap.

| Time (UTC) | Trade | Anchor (z) | Our price | What the spread is really worth | Model edge / spread |
|---|---|---|---:|---:|---:|
| 15:27 | buy 698C / sell 700C, Oct 30 | 700C looks rich (sell z 3.25) | 0.52 debit | ≈ 1.98 (SPY is 69 points above both strikes) | $146 |
| 15:52 | buy 730C / sell 732C, Nov 13 | 730C looks cheap (buy z 2.46) | 0.83 debit | ≈ 1.97 | $101 |
| 17:23 | buy 693C / sell 695C, Oct 30 | 695C looks rich (sell z 4.41) | 0.85 debit | ≈ 1.98 | $114 |
| 17:35 | sell 745C / buy 755C, Nov 02 | 745C looks rich (sell z 2.44) | 9.33 credit | ≈ 8.75 (model) | $58 |

**The three debit spreads.** These were $2-wide call spreads with both strikes far below SPY. Spreads like that are worth almost exactly $2. The quotes said we could buy them for 52–85 cents. That only happens when one quote is wrong. The 700C was bid at 71.94, which is $2.51 above its intrinsic value. The 698C right next to it was only $1.03 above intrinsic. The 700C's implied vol was 42%, against 24% for its neighbors. The model read that phantom bid as "the 700C is overpriced, sell it," and paired it with the 698C. The 693/695 pair shows how unstable these quotes were. The 693C was quoted 75.76/79.25 fifteen seconds before the scan, then 77.55/77.59 at the scan, then 76.14/76.67 fifteen seconds after. Quotes on deep in-the-money options from the indicative feed were already known to be unreliable (docs/MILESTONES.md, Phase 2).

**The credit spread** (745/755) was not below intrinsic value. A 745/755 call spread can legitimately be worth less than its $10 intrinsic value, and the model's 8.75 is about right. It was still a bad quote, though. The 745C was bid about 1.5 vol points above the smile. Together with the matching puts, the quotes implied a "box" (explained below) that we could sell for $10.51 when it pays out exactly $10.

**Why no existing rule caught them:**
- The pricing engine checks put-call parity only within ±3% of SPY (`parity_check_moneyness: 0.03`). All four anchors were 3–10% in the money, so parity was never checked for them.
- The smile is fitted only to out-of-the-money options. In-the-money calls are priced *from* that smile, so a bad in-the-money call quote can never look like an outlier. It just looks like a mispricing.
- `too_good_to_be_true` rejects a trade only when the maximum loss is under 5% of the width. These trades risked 26–43% (debit) and 6.7% (credit), so they passed.
- `wide_leg_spread` measures the bid/ask gap against the option's price. For a $72 option, 10% is $7, so a $3.50-wide quote still passes.
- No rule compared the debit with the spread's intrinsic value or with the matching puts.

**Wider pattern:** 97.5% of all 7,700+ stored candidates have a leg more than 3% away from SPY. The scanner mostly finds "mispricings" in exactly the area where the free feed is least trustworthy.

## 2. The 0.75 → 0.19 limit change: expected behavior, but on garbage quotes

`execution/orders.py` recomputes each limit from **fresh quotes** on every attempt: limit = mid + fraction × (executable − mid). The fraction steps 0.5 → 0.75 → 1.0. The limit is capped at the approved price + $0.05, but nothing stops it from going *down*.

| Attempt | Quotes used (693C / 695C) | Spread mid | Executable | Limit |
|---|---|---:|---:|---:|
| 0 (17:23:56) | 77.55/77.59 and 76.74/77.13 | 0.635 | 0.85 | 0.75 |
| 1 (17:24:18) | 76.14/76.67 and 76.38/76.72 | **−0.145** | 0.29 | 0.19 |
| 2 (17:24:40) | 75.08/78.05 and 75.83/76.17 | — | 2.22 > 0.90 cap | abort: `price_moved_beyond_limit` |

The arithmetic is correct, so this is **not a code bug**. In attempt 1, the lower-strike call was quoted *below* the higher-strike call, which is impossible (a negative mid for a debit spread). The order manager followed those quotes down instead of noticing they were nonsense. The abort on attempt 2 worked as designed. A sensible follow-up, not included in this branch: before each resubmit, refuse to keep working an order when the fresh quotes are themselves impossible (spread mid below 0 or above the width), and never lower a debit limit, or raise a credit limit, between attempts.

## 3. The proposed fix: a call/put "box" check (branch `claude/jovial-meitner-34ec25`)

A **box** combines our vertical with the opposite-type vertical at the same two strikes. For example, a 698/700 call spread plus a 700/698 put spread pays exactly the $2 width whatever SPY does. So, at prices we can actually trade, buying a box must cost at least the discounted width, and selling one can bring in at most that much. If the quotes say otherwise, at least one of the four quotes is wrong. This is put-call parity applied to the trade itself. It needs no model, and it works at any distance from the money.

- `strategies/spread_builder.py`: `box_gap()` measures how far the quotes put the box in our favor. `early_exercise_allowance()` adds slack for American early exercise.
- `strategies/relative_value.py`: new rejection reasons:
  - `inconsistent_with_parity` when the gap is more than `max_box_gap` plus the early-exercise allowance.
  - `parity_unverifiable` (fail closed) when the matching opposite-type quotes are missing.
- `config/settings.yaml`: `strategy.max_box_gap: 0.05` ($/share, a few ticks). `config/risk_limits.yaml` is untouched.
- The phone summary and the scan report have plain-English wording for both new reasons.

**Effect on stored data (replay of all ~7,700 stored candidates, 2026-10-19 → 10-20):**

| | Count |
|---|---:|
| Today's eligible candidates blocked | **4 of 4** (gaps $1.45, $0.94, $1.13, $0.52 per share) |
| Candidates with no matching opposite-type quotes (would hit `parity_unverifiable`) | 0 |
| All candidates failing the box check | ≈ 13% (almost all already rejected for other reasons) |

All four are blocked at any tolerance from $0.02 to $0.25, so the result does not depend on the exact setting. With this rule in place, the trader would have sent **no** entry orders today.

**Tests** (`tests/test_strategy.py`):
- the four real 2026-10-20 trades, using the actual quotes, are rejected
- a fairly priced spread worth less than its intrinsic value is *not* rejected
- put spreads are checked against calls
- missing quotes fail closed
- the early-exercise allowance widens the tolerance

Full suite: 314 passed, 6 skipped.

## 4. Other options considered (not implemented)

- **A simple "debit below intrinsic" rule.** Rejected because it is wrong near the money. A 765/770 call spread with SPY at 768 is fairly worth about $3 against $3–4 of intrinsic value. It would also have missed the 745/755 credit spread.
- **Only allow out-of-the-money anchors** (contracts in the smile fit). This would also have blocked all four. It is blunter, because it gives up every in-the-money signal, including ones whose quotes check out. It is worth considering if the box check proves too loose.
- **Measure the bid/ask gap against extrinsic value (or vega) instead of price.** This would make `wide_leg_spread` meaningful for deep in-the-money legs.

## 5. Not done, by instruction

The fix is not merged or deployed, and the running collector and trader were not restarted. `config/risk_limits.yaml` was not changed, and no paid data was used. Deploying the rule means merging the branch. The trader then picks it up at its next start.
