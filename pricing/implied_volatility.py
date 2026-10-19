"""Implied volatility: the sigma that makes Black-Scholes reproduce a price.

Returns a status instead of guessing when no valid volatility exists
(for example a price below intrinsic value, which no volatility explains).
"""

from __future__ import annotations

from dataclasses import dataclass

from scipy.optimize import brentq

from pricing.black_scholes import no_arbitrage_bounds, price

SIGMA_MIN, SIGMA_MAX = 1e-4, 5.0


@dataclass(frozen=True)
class IVResult:
    iv: float | None
    status: str          # ok | below_intrinsic | above_max | no_time | bad_price

    @property
    def ok(self) -> bool:
        return self.iv is not None


def implied_vol(target: float, S: float, K: float, T: float, r: float, q: float, kind: str, tol: float = 1e-8) -> IVResult:
    if target is None or target <= 0 or S <= 0 or K <= 0:
        return IVResult(None, "bad_price")
    if T <= 0:
        return IVResult(None, "no_time")
    lower, upper = no_arbitrage_bounds(S, K, T, r, q, kind)
    if target <= lower + 1e-10:
        return IVResult(None, "below_intrinsic")
    if target >= upper:
        return IVResult(None, "above_max")

    f = lambda s: price(S, K, T, r, q, s, kind) - target
    lo_val, hi_val = f(SIGMA_MIN), f(SIGMA_MAX)
    if lo_val > 0:
        return IVResult(None, "below_intrinsic")     # even ~0 vol is too expensive
    if hi_val < 0:
        return IVResult(None, "above_max")
    return IVResult(brentq(f, SIGMA_MIN, SIGMA_MAX, xtol=tol, maxiter=200), "ok")
