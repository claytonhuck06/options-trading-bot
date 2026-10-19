"""Price a point-in-time snapshot: fair value, edge and uncertainty per contract.

For each expiry:
  1. Flag quotes that break no-arbitrage rules (data/validators/arbitrage.py).
  2. Imply the forward (and dividend yield) from clean near-the-money pairs.
  3. Flag call/put pairs inconsistent with that forward.
  4. Compute implied vols at bid, mid and ask.
  5. Fit the smile on clean out-of-the-money mids.
  6. Fair value = leave-one-out smile vol -> Black-Scholes, plus the
     binomial early-exercise premium for in-the-money contracts.
  7. Edge vs EXECUTABLE prices: buy_edge = fair - ask, sell_edge = bid - fair,
     and each edge divided by model uncertainty (a z-score).

This produces research numbers only. A positive edge is NOT a trade signal by
itself; see docs/research for why.
"""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Callable

from core.config import PricingSettings
from data.validators.arbitrage import find_violations, parity_violations
from pricing.american import early_exercise_premium
from pricing.black_scholes import greeks, price
from pricing.implied_volatility import implied_vol
from pricing.snapshot import Snapshot, SnapOption, years_to_expiry
from pricing.volatility_surface import ForwardEstimate, SmileFit, calendar_violations, fit_smile, implied_forward

PARITY_TOLERANCE = 0.01          # $/share slack for rounding in the parity check
STRUCTURE_TOLERANCE = 0.01       # $/share slack (one tick) in the monotonic/vertical/butterfly checks
EXTRAPOLATION_MARGIN = 0.01      # log-moneyness beyond the fitted range still trusted


@dataclass
class ContractResult:
    option: SnapOption
    years: float
    log_moneyness: float | None = None
    iv_bid: float | None = None
    iv_mid: float | None = None
    iv_ask: float | None = None
    fair_iv: float | None = None
    fair_price: float | None = None
    buy_edge: float | None = None
    sell_edge: float | None = None
    price_uncertainty: float | None = None
    buy_z: float | None = None
    sell_z: float | None = None
    delta: float | None = None
    gamma: float | None = None
    theta: float | None = None
    vega: float | None = None
    rho: float | None = None
    early_exercise_premium: float = 0.0
    in_fit: bool = False
    flags: set[str] = field(default_factory=set)

    @property
    def reliable(self) -> bool:
        return self.fair_price is not None and not self.flags


@dataclass
class ExpiryResult:
    expiration: date
    years: float
    rate: float
    forward: ForwardEstimate
    fit: SmileFit | None
    contracts: list[ContractResult]


@dataclass
class PricingResult:
    snapshot: Snapshot
    expiries: list[ExpiryResult]
    rate_source: str

    @property
    def contracts(self) -> list[ContractResult]:
        return [c for e in self.expiries for c in e.contracts]

    def calendar_violations(self) -> list[tuple[str, str]]:
        """Pairs of expiries whose at-the-money total variance decreases with maturity."""
        pts = [(str(e.expiration), e.years, e.fit.iv_at(0.0)) for e in self.expiries if e.fit and e.fit.status == "ok"]
        return calendar_violations(pts)


def price_snapshot(snap: Snapshot, cfg: PricingSettings, rate_for: Callable[[float], tuple[float, str]]) -> PricingResult:
    by_exp: dict[date, list[SnapOption]] = defaultdict(list)
    for o in snap.options:
        by_exp[o.expiration].append(o)

    expiries, sources = [], set()
    for exp in sorted(by_exp):
        T = years_to_expiry(exp, snap.as_of)
        r, src = rate_for(T)
        sources.add(src)
        expiries.append(_price_expiry(snap.spot, exp, T, r, by_exp[exp], cfg))
    return PricingResult(snap, expiries, ",".join(sorted(sources)))


def _price_expiry(spot: float, exp: date, T: float, r: float, opts: list[SnapOption], cfg: PricingSettings) -> ExpiryResult:
    results = {o.symbol: ContractResult(o, T) for o in opts}
    if T <= 0:
        for c in results.values():
            c.flags.add("expired")
        return ExpiryResult(exp, T, r, ForwardEstimate(None, None, 0, None), None, list(results.values()))

    for sym, why in find_violations(opts, T, r, STRUCTURE_TOLERANCE).items():
        results[sym].flags |= why

    calls = {o.strike: o for o in opts if o.option_type == "call" and not results[o.symbol].flags}
    puts = {o.strike: o for o in opts if o.option_type == "put" and not results[o.symbol].flags}
    pairs = [(k, calls[k].mid, puts[k].mid) for k in calls.keys() & puts.keys()]
    fwd = implied_forward(pairs, spot, T, r, cfg.forward_pairs)
    if fwd.forward is None:
        for c in results.values():
            c.flags.add("no_forward")
        return ExpiryResult(exp, T, r, fwd, None, list(results.values()))
    F, q = fwd.forward, fwd.implied_dividend_yield

    # Forward uncertainty: half the spread of the per-pair estimates.
    f_std = (fwd.dispersion or 0.0) / 2
    tol = PARITY_TOLERANCE + f_std * math.exp(-r * T)
    for sym, why in parity_violations(opts, F, T, r, tol, cfg.parity_check_moneyness).items():
        results[sym].flags |= why

    # Implied vols and fit candidates.
    fit_idx, k_list, iv_list, w_list = [], [], [], []
    for o in opts:
        c = results[o.symbol]
        c.log_moneyness = math.log(o.strike / F)
        c.iv_mid = implied_vol(o.mid, spot, o.strike, T, r, q, o.option_type).iv
        c.iv_bid = implied_vol(o.bid, spot, o.strike, T, r, q, o.option_type).iv
        c.iv_ask = implied_vol(o.ask, spot, o.strike, T, r, q, o.option_type).iv
        if o.mid < cfg.min_option_price:
            c.flags.add("low_price")
        if c.iv_mid is None:
            c.flags.add("no_iv")
            continue
        otm = (o.option_type == "call" and o.strike >= F) or (o.option_type == "put" and o.strike < F)
        if otm and not c.flags:
            vega = greeks(spot, o.strike, T, r, q, c.iv_mid, o.option_type).vega
            half_spread_iv = max((o.ask - o.bid) / 2, 0.005) / max(vega, 1e-6)
            fit_idx.append(o.symbol)
            k_list.append(c.log_moneyness)
            iv_list.append(c.iv_mid)
            w_list.append(1.0 / half_spread_iv ** 2)

    fit = fit_smile(k_list, iv_list, w_list, cfg.surface_poly_degree, cfg.min_points_per_expiry, cfg.outlier_mad_threshold)
    if fit.status != "ok":
        for c in results.values():
            c.flags.add("no_fit")
        return ExpiryResult(exp, T, r, fwd, fit, list(results.values()))

    w_median = sorted(w_list)[len(w_list) // 2]
    position = {sym: i for i, sym in enumerate(fit_idx)}
    for o in opts:
        c = results[o.symbol]
        k = c.log_moneyness
        if k < fit.k_min - EXTRAPOLATION_MARGIN or k > fit.k_max + EXTRAPOLATION_MARGIN:
            c.flags.add("extrapolated")
        i = position.get(o.symbol)
        if i is not None:
            c.fair_iv, iv_std = fit.loo_iv[i], fit.loo_std[i]
            c.in_fit = i not in fit.excluded
            if not c.in_fit:
                c.flags.add("smile_outlier")
        else:
            c.fair_iv = fit.iv_at(k)
            if c.iv_mid is not None:
                vega0 = greeks(spot, o.strike, T, r, q, c.iv_mid, o.option_type).vega
                w = 1.0 / (max((o.ask - o.bid) / 2, 0.005) / max(vega0, 1e-6)) ** 2
            else:
                w = w_median
            iv_std = fit.predict_std(k, w)
        if c.fair_iv is None or c.fair_iv <= 0:
            c.flags.add("no_fit")
            continue

        intrinsic_side = (o.option_type == "call" and o.strike < spot) or (o.option_type == "put" and o.strike > spot)
        if intrinsic_side:
            c.early_exercise_premium = early_exercise_premium(spot, o.strike, T, r, q, c.fair_iv, o.option_type, cfg.american_crr_steps)
            if c.early_exercise_premium > cfg.early_exercise_flag_threshold:
                c.flags.add("early_exercise")

        g = greeks(spot, o.strike, T, r, q, c.fair_iv, o.option_type)
        c.delta, c.gamma, c.theta, c.vega, c.rho = g.delta, g.gamma, g.theta, g.vega, g.rho
        c.fair_price = price(spot, o.strike, T, r, q, c.fair_iv, o.option_type) + c.early_exercise_premium
        # Smile prediction error, a floor for model error, and forward uncertainty, combined.
        vol_unc = math.hypot(iv_std, cfg.model_vol_uncertainty_floor)
        c.price_uncertainty = max(math.hypot(g.vega * vol_unc, abs(g.delta) * math.exp(q * T) * f_std), 0.005)
        c.buy_edge = c.fair_price - o.ask
        c.sell_edge = o.bid - c.fair_price
        c.buy_z = c.buy_edge / c.price_uncertainty
        c.sell_z = c.sell_edge / c.price_uncertainty

    return ExpiryResult(exp, T, r, fwd, fit, list(results.values()))
