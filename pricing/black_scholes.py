"""Black-Scholes-Merton prices and analytic Greeks (European exercise,
continuous dividend yield q, continuously compounded rate r).

Units:
    T      years (calendar time)
    sigma  annualised volatility as a fraction (0.20 = 20%)
    theta  value change per YEAR (divide by 365 for per-calendar-day)
    vega   value change per 1.00 change in sigma (divide by 100 per vol point)
    rho    value change per 1.00 change in r (divide by 100 per percentage point)
All values are per share; multiply by the contract multiplier (100) for dollars.

SPY options are American-style. For calls on a dividend payer and for puts,
early exercise can make the American value exceed this European value; see
pricing/american.py, which measures that gap.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

CALL, PUT = "call", "put"
_SQRT2 = math.sqrt(2.0)
_INV_SQRT_2PI = 1.0 / math.sqrt(2.0 * math.pi)


def norm_cdf(x: float) -> float:
    return 0.5 * math.erfc(-x / _SQRT2)


def norm_pdf(x: float) -> float:
    return _INV_SQRT_2PI * math.exp(-0.5 * x * x)


def _check(kind: str) -> None:
    if kind not in (CALL, PUT):
        raise ValueError(f"kind must be 'call' or 'put', got {kind!r}")


def d1_d2(S: float, K: float, T: float, r: float, q: float, sigma: float) -> tuple[float, float]:
    vol_t = sigma * math.sqrt(T)
    d1 = (math.log(S / K) + (r - q + 0.5 * sigma * sigma) * T) / vol_t
    return d1, d1 - vol_t


def price(S: float, K: float, T: float, r: float, q: float, sigma: float, kind: str) -> float:
    _check(kind)
    if S <= 0 or K <= 0:
        raise ValueError("S and K must be positive")
    if T <= 0:
        return max(S - K, 0.0) if kind == CALL else max(K - S, 0.0)
    df_r, df_q = math.exp(-r * T), math.exp(-q * T)
    if sigma <= 0:
        fwd_intrinsic = S * df_q - K * df_r
        return max(fwd_intrinsic, 0.0) if kind == CALL else max(-fwd_intrinsic, 0.0)
    d1, d2 = d1_d2(S, K, T, r, q, sigma)
    if kind == CALL:
        return S * df_q * norm_cdf(d1) - K * df_r * norm_cdf(d2)
    return K * df_r * norm_cdf(-d2) - S * df_q * norm_cdf(-d1)


@dataclass(frozen=True)
class Greeks:
    delta: float
    gamma: float
    theta: float
    vega: float
    rho: float


def greeks(S: float, K: float, T: float, r: float, q: float, sigma: float, kind: str) -> Greeks:
    _check(kind)
    if T <= 0 or sigma <= 0:
        raise ValueError("Greeks need T > 0 and sigma > 0")
    d1, d2 = d1_d2(S, K, T, r, q, sigma)
    df_r, df_q = math.exp(-r * T), math.exp(-q * T)
    sqrt_t = math.sqrt(T)
    pdf = norm_pdf(d1)
    gamma = df_q * pdf / (S * sigma * sqrt_t)
    vega = S * df_q * pdf * sqrt_t
    decay = -S * df_q * pdf * sigma / (2 * sqrt_t)
    if kind == CALL:
        delta = df_q * norm_cdf(d1)
        theta = decay - r * K * df_r * norm_cdf(d2) + q * S * df_q * norm_cdf(d1)
        rho = K * T * df_r * norm_cdf(d2)
    else:
        delta = -df_q * norm_cdf(-d1)
        theta = decay + r * K * df_r * norm_cdf(-d2) - q * S * df_q * norm_cdf(-d1)
        rho = -K * T * df_r * norm_cdf(-d2)
    return Greeks(delta, gamma, theta, vega, rho)


def no_arbitrage_bounds(S: float, K: float, T: float, r: float, q: float, kind: str) -> tuple[float, float]:
    """(lower, upper) bounds any European option price must lie within."""
    _check(kind)
    fs, fk = S * math.exp(-q * T), K * math.exp(-r * T)
    if kind == CALL:
        return max(fs - fk, 0.0), fs
    return max(fk - fs, 0.0), fk
