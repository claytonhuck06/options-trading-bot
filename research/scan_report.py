"""Replay the scanner over a collected day and check what its candidates were worth later.

For each snapshot the scanner sees only data received by then. Each distinct
spread is counted once, at its first appearance. Its later value is what
closing it would have fetched at executable prices (sell the long leg at the
bid, buy back the short leg at the ask), minus fees. Spreads are not hedged,
because the strategy does not hedge them; a delta-adjusted column separates
the mispricing from SPY's own move.

Writes docs/research/scan_report_<date>.md.
"""

from __future__ import annotations

import statistics as st
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

from core.config import PROJECT_ROOT, Settings
from data.storage.db import MarketDataStore
from research.pricing_report import EASTERN, _later_quote, _run, _spot_at, _times
from risk.limits import compute_daily_budget
from strategies.relative_value import Candidate, scan

HORIZONS_MIN = (5, 15, 30, 90)


def _exit_pnl(store, settings, c: Candidate, t1: datetime, data_end: datetime) -> tuple[float, float] | None:
    if t1 > data_end:
        return None
    age = settings.pricing.snapshot_max_quote_age_seconds
    ql, qs = _later_quote(store, c.spread.long.symbol, t1, age), _later_quote(store, c.spread.short.symbol, t1, age)
    s1 = _spot_at(store, settings.market_data.primary_underlying, t1)
    if not ql or not qs or s1 is None:
        return None
    exit_value = ql[0] - qs[1]                                  # sell long at bid, buy short at ask
    pnl = (exit_value - c.spread.net_premium) * 100 - c.fees_per_spread
    s0 = _spot_at(store, settings.market_data.primary_underlying, c.as_of)
    hedged = pnl - c.net_delta * (s1 - s0)
    return pnl, hedged


def build_scan_report(settings: Settings, store: MarketDataStore, day: date, every_min: int = 1,
                      out_dir: Path | None = None) -> Path:
    underlying = settings.market_data.primary_underlying
    out_dir = out_dir or PROJECT_ROOT / "docs" / "research"
    out_dir.mkdir(parents=True, exist_ok=True)
    times = _times(store, underlying, day, every_min)
    if not times:
        raise LookupError(f"no {underlying} data collected on {day}")
    data_end = times[-1]
    budget = compute_daily_budget(settings.risk, settings.risk.reference_equity)

    first_seen: dict[tuple[str, str], Candidate] = {}
    per_snapshot, all_reasons = [], Counter()
    for t in times:
        r = _run(settings, store, underlying, t)
        if r is None:
            continue
        cands = scan(r, settings, budget, enforce_window=False)       # replay: judge every snapshot on its merits
        per_snapshot.append((t, len(cands), sum(c.eligible for c in cands)))
        for c in cands:
            all_reasons.update(c.reasons)
            first_seen.setdefault((c.spread.long.symbol, c.spread.short.symbol), c)

    groups: dict[str, list[Candidate]] = defaultdict(list)
    for c in first_seen.values():
        groups["eligible" if c.eligible else c.reasons[0]].append(c)

    L = [f"# Scanner replay — {day}", "",
         f"`main.py scan-report`. {len(per_snapshot)} snapshots every {every_min} min "
         f"({times[0].astimezone(EASTERN):%H:%M}–{data_end.astimezone(EASTERN):%H:%M} ET). "
         f"Risk budget ${budget.max_risk_per_trade:.0f} per trade (reference equity ${settings.risk.reference_equity:,.0f}). "
         "The entry-time window is **ignored** in this replay so every snapshot is judged on its merits. No orders.", ""]
    eligible = groups.get("eligible", [])
    L += [f"> **Bottom line.** {len(first_seen):,} distinct spreads were built around apparent mispricings; "
          f"**{len(eligible)}** passed every rule. "
          + ("Zero trades is a legitimate outcome: nothing in this data was good enough after costs and uncertainty."
             if not eligible else "See section 3 for what the eligible ones were worth later."), ""]

    L += ["## 1. Why candidates were rejected", "",
          "A candidate can fail several rules; each failure is counted.", "",
          "| Rule | Failures | What it protects against |", "|---|---:|---|"]
    meaning = {
        "costs_exceed_expected_edge": "the assumed captured edge is smaller than exit spreads plus fees",
        "target_unlikely_in_holding_period": "under 25% modelled chance of reaching the profit target in 90 min",
        "no_edge_at_executable_prices": "the mispriced leg looks good, but crossing the other leg's spread erases it",
        "edge_within_model_uncertainty": "the whole-spread edge is under 2x the model's own uncertainty",
        "too_large_for_risk_budget": "even one spread risks more than the per-trade limit",
        "reward_too_small": "maximum profit under $5 per spread",
        "too_good_to_be_true": "near-riskless payoff: a glitched or stale quote",
        "inconsistent_with_parity": "with the same-strike opposite-type spread it forms a riskless box: a quote is wrong",
        "parity_unverifiable": "no same-strike quotes of the other option type to cross-check against",
        "thin_quote": "fewer than 5 contracts shown on the side we would trade",
        "wide_leg_spread": "a leg's bid-ask is over 10% of its price",
        "stale_quote": "a leg's quote older than 30 seconds",
        "max_loss_not_positive": "quotes imply a riskless profit; the data is wrong",
        "expiry_too_close": "inside 7 days to expiry",
        "outside_entry_window": "outside 9:35–15:00 ET",
    }
    for r, n in all_reasons.most_common():
        L.append(f"| `{r}` | {n:,} | {meaning.get(r, '')} |")
    L += [""]

    L += ["## 2. What candidates were worth later (by first rejection reason)", "",
          "Mean P&L per spread if entered at executable prices on first appearance and closed at executable prices later, "
          "after fees. *Δ-adj* removes the profit or loss explained by SPY's move. Small groups are noise.", ""]
    L += ["| Group | Spreads | " + " | ".join(f"{h} min (Δ-adj)" for h in HORIZONS_MIN) + " |",
          "|---|---:|" + "---:|" * len(HORIZONS_MIN)]
    for name in sorted(groups, key=lambda g: (g != "eligible", -len(groups[g]))):
        cells = []
        for h in HORIZONS_MIN:
            outs = [o for c in groups[name] if (o := _exit_pnl(store, settings, c, c.as_of + timedelta(minutes=h), data_end))]
            cells.append(f"{st.mean(o[0] for o in outs):+.2f} ({st.mean(o[1] for o in outs):+.2f}), n={len(outs)}" if outs else "–")
        L.append(f"| {name} | {len(groups[name])} | " + " | ".join(cells) + " |")
    L += ["", "If the rules work, rejected groups should lose money on average (they were rejected for good reason) and "
          "eligible spreads should do better. With one partial day of indicative data, treat every number here as "
          "a test of the machinery, not evidence.", ""]

    L += ["## 3. Eligible candidates", ""]
    if eligible:
        for c in sorted(eligible, key=lambda c: c.as_of):
            L += ["```", f"{c.as_of.astimezone(EASTERN):%H:%M:%S} ET", c.explain(), "```", ""]
    else:
        L += ["None.", ""]
        closest = sorted(first_seen.values(), key=lambda c: (len(c.reasons), -c.score))[:3]
        if closest:
            L += ["Closest misses:", ""]
            for c in closest:
                L += ["```", f"{c.as_of.astimezone(EASTERN):%H:%M:%S} ET", c.explain(), "```", ""]

    L += ["## 4. Assumptions to test before trusting this scanner", "",
          f"* `assumed_convergence = {settings.strategy.assumed_convergence}`: half the mispricing is assumed to close before "
          "exit. Section 2, over many days, is how this gets measured instead of assumed.",
          f"* Profit-target probability uses a normal approximation with SPY moving at the option's implied vol and implied "
          f"vol itself moving ~{settings.strategy.vol_change_std_per_hour * 100:.2f} pts per hour (from the 2026-10-19 "
          "stability table). Real moves have fatter tails.",
          "* Leg uncertainties are combined as if independent; the shared forward error partly cancels inside a vertical, "
          "so spread uncertainty is somewhat overstated (conservative).",
          f"* Fees: ${settings.strategy.fee_per_contract:.2f} per contract per side, an allowance, not Alpaca's exact fee schedule.",
          "* Sizing uses the configured $20,000 reference equity; the risk engine (Phase 5) will use live account equity and "
          "portfolio-level limits.", ""]
    path = out_dir / f"scan_report_{day}.md"
    path.write_text("\n".join(L))
    return path

