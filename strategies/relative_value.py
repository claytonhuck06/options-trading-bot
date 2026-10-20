"""Score candidate spreads and reject the ones that cannot realistically pay.

Expected P&L per spread (per share, then x100):
    convergence * entry_edge  -  exit half-spreads  -  fees
where entry_edge = model value - executable entry price. `convergence` is the
assumed share of the mispricing that closes before exit; it is an untested
hypothesis in settings.yaml, not a measured fact.

Risk over the holding period comes from the spread's net delta (SPY moves)
and net vega (implied-vol moves), giving a normal approximation for the
chance of reaching the profit target. Candidates are ranked by expected P&L
divided by maximum loss: risk-adjusted expected value after costs.

Nothing here places orders. Portfolio-level limits are the risk engine's job
(Phase 5); this module only checks that one trade fits the per-trade budget.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, time
from zoneinfo import ZoneInfo

from core.config import Settings
from pricing.black_scholes import norm_cdf
from pricing.engine import PricingResult
from risk.limits import DailyRiskBudget, take_profit_target
from strategies.spread_builder import MULTIPLIER, VerticalSpread, box_gap, build_candidates, early_exercise_allowance

EASTERN = ZoneInfo("America/New_York")
TRADING_MINUTES_PER_YEAR = 252 * 390


@dataclass
class Candidate:
    spread: VerticalSpread
    as_of: datetime
    contracts: int
    max_loss_per_spread: float          # $, including round-trip fees
    max_profit_per_spread: float        # $, after round-trip fees
    fees_per_spread: float              # $ round trip
    entry_edge: float                   # $ per spread
    edge_z: float
    expected_pnl: float                 # $ per spread
    pnl_std: float                      # $ per spread over the holding period
    profit_target: float                # $ per spread
    target_probability: float
    loss_probability: float
    score: float                        # expected_pnl / max_loss
    net_delta: float                    # per spread (x100), share-equivalents
    net_gamma: float
    net_vega: float                     # $ per 1 vol point per spread
    net_theta: float                    # $ per calendar day per spread
    reasons: list[str] = field(default_factory=list)

    @property
    def eligible(self) -> bool:
        return not self.reasons

    def explain(self) -> str:
        s = self.spread
        anchor = s.long if s.long.symbol == s.anchor else s.short
        a = anchor.contract
        rich_cheap = "cheap" if anchor.side > 0 else "rich"
        mis = a.buy_edge if anchor.side > 0 else a.sell_edge
        z = a.buy_z if anchor.side > 0 else a.sell_z
        lines = [
            s.describe(),
            f"  Why: {anchor.symbol} looks {rich_cheap} by ${mis * MULTIPLIER:.0f}/contract vs the smile (z={z:.1f}); "
            f"the {'short' if anchor.side > 0 else 'long'} leg caps the risk.",
            f"  Net {'debit' if s.net_premium >= 0 else 'credit'} ${abs(s.net_premium) * MULTIPLIER:.0f}, max loss ${self.max_loss_per_spread:.0f}, "
            f"max profit ${self.max_profit_per_spread:.0f}, fees ${self.fees_per_spread:.2f} (per spread)",
            f"  Model edge ${self.entry_edge:.2f} (z={self.edge_z:.1f}); expected after costs ${self.expected_pnl:+.2f} "
            f"± ${self.pnl_std:.2f}; target ${self.profit_target:.2f} reached ~{self.target_probability:.0%}",
            f"  Exposure per spread: delta {self.net_delta:+.1f} sh, vega ${self.net_vega:+.2f}/vol pt, theta ${self.net_theta:+.2f}/day; "
            f"size {self.contracts} spread(s)",
        ]
        lines.append("  ELIGIBLE" if self.eligible else "  Rejected: " + ", ".join(self.reasons))
        return "\n".join(lines)


def _in_entry_window(as_of: datetime, settings: Settings) -> bool:
    t = as_of.astimezone(EASTERN).time()
    start = time.fromisoformat(settings.schedule.scan_start)
    stop = time.fromisoformat(settings.schedule.no_new_positions_after)
    return start <= t < stop


def score(spread: VerticalSpread, result: PricingResult, settings: Settings, budget: DailyRiskBudget,
          enforce_window: bool = True) -> Candidate:
    cfg, as_of = settings.strategy, result.snapshot.as_of
    reasons: list[str] = []

    fees = cfg.fee_per_contract * 2 * 2                         # 2 legs, open and close
    max_loss = spread.max_loss * MULTIPLIER + fees
    max_profit = spread.max_profit * MULTIPLIER - fees
    entry_edge = spread.entry_edge * MULTIPLIER
    unc = spread.uncertainty * MULTIPLIER
    edge_z = entry_edge / unc if unc > 0 else 0.0
    expected = cfg.assumed_convergence * entry_edge - spread.exit_half_spreads * MULTIPLIER - fees

    anchor_iv = next(l.contract.fair_iv for l in spread.legs if l.symbol == spread.anchor)
    h_years = cfg.holding_period_minutes / TRADING_MINUTES_PER_YEAR
    delta = spread.net_greek("delta") * MULTIPLIER
    vega_pt = spread.net_greek("vega") * MULTIPLIER / 100           # $ per vol point
    move_std = result.snapshot.spot * anchor_iv * math.sqrt(h_years)
    vol_std_pts = cfg.vol_change_std_per_hour * 100 * math.sqrt(cfg.holding_period_minutes / 60)
    pnl_std = math.hypot(delta * move_std, vega_pt * vol_std_pts)

    target = take_profit_target(settings.risk, max_loss) if max_loss > 0 else float("nan")
    if pnl_std > 0 and max_loss > 0:
        p_target = 1 - norm_cdf((target - expected) / pnl_std)
        p_loss = norm_cdf(-expected / pnl_std)
    else:
        p_target, p_loss = (1.0 if expected >= target else 0.0), (1.0 if expected < 0 else 0.0)

    contracts = int(budget.max_risk_per_trade // max_loss) if max_loss > 0 else 0

    # --- rejection rules (Step 16) ---------------------------------------
    if enforce_window and not _in_entry_window(as_of, settings):
        reasons.append("outside_entry_window")
    dte = (spread.expiration - as_of.astimezone(EASTERN).date()).days
    if dte < settings.market_data.min_days_to_expiry:
        reasons.append("expiry_too_close")
    for leg in spread.legs:
        o = leg.contract.option
        if (as_of - o.quote_ts).total_seconds() > cfg.max_quote_age_seconds:
            reasons.append("stale_quote")
        size = o.ask_size if leg.side > 0 else o.bid_size
        if size is None or size < cfg.min_quote_size:
            reasons.append("thin_quote")
        if (o.ask - o.bid) / o.mid > cfg.max_leg_relative_spread:
            reasons.append("wide_leg_spread")
    if not (max_loss > 0 and math.isfinite(max_loss)):
        reasons.append("max_loss_not_positive")          # only possible if the quotes themselves allow arbitrage
    elif spread.max_loss < cfg.min_risk_fraction_of_width * spread.width:
        reasons.append("too_good_to_be_true")            # near-riskless payoff: a stale or glitched quote
    gap = box_gap(spread, result)
    if gap is None:
        reasons.append("parity_unverifiable")            # no same-strike quotes of the other type to cross-check
    elif gap > cfg.max_box_gap + early_exercise_allowance(spread, result):
        reasons.append("inconsistent_with_parity")       # quotes imply a riskless box: at least one is wrong
    if max_loss > 0 and contracts < 1:
        reasons.append("too_large_for_risk_budget")
    if max_profit < cfg.min_max_profit:
        reasons.append("reward_too_small")
    if entry_edge <= 0:
        reasons.append("no_edge_at_executable_prices")
    elif edge_z < cfg.min_edge_z:
        reasons.append("edge_within_model_uncertainty")
    if expected <= 0:
        reasons.append("costs_exceed_expected_edge")
    if p_target < cfg.min_target_probability:
        reasons.append("target_unlikely_in_holding_period")

    return Candidate(
        spread=spread, as_of=as_of, contracts=max(contracts, 0),
        max_loss_per_spread=max_loss, max_profit_per_spread=max_profit, fees_per_spread=fees,
        entry_edge=entry_edge, edge_z=edge_z, expected_pnl=expected, pnl_std=pnl_std,
        profit_target=target, target_probability=p_target, loss_probability=p_loss,
        score=expected / max_loss if max_loss > 0 else float("-inf"),
        net_delta=delta, net_gamma=spread.net_greek("gamma") * MULTIPLIER, net_vega=vega_pt,
        net_theta=spread.net_greek("theta") * MULTIPLIER / 365,
        reasons=list(dict.fromkeys(reasons)),
    )


def scan(result: PricingResult, settings: Settings, budget: DailyRiskBudget, enforce_window: bool = True) -> list[Candidate]:
    """All candidates: eligible ones first by score, then rejected ones by how few rules they failed."""
    cands = [score(s, result, settings, budget, enforce_window) for s in build_candidates(result, settings.strategy)]
    cands.sort(key=lambda c: (len(c.reasons), -c.score))
    return cands
