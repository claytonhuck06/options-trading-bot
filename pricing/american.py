"""Cox-Ross-Rubinstein binomial tree for American options.

Used to measure the early-exercise premium (American minus European value),
i.e. how wrong plain Black-Scholes is for a given SPY contract. Dividends are
modelled as a continuous yield, which ignores the discrete quarterly SPY
dividend; near an ex-dividend date this understates call early exercise.
"""

from __future__ import annotations

import math

import numpy as np

from pricing.black_scholes import CALL, PUT, price as bs_price


def _tree(S: float, K: float, T: float, r: float, q: float, sigma: float, kind: str, steps: int, american: bool) -> float:
    if kind not in (CALL, PUT):
        raise ValueError("kind must be 'call' or 'put'")
    if T <= 0 or sigma <= 0:
        return bs_price(S, K, max(T, 0.0), r, q, max(sigma, 0.0), kind)
    dt = T / steps
    u = math.exp(sigma * math.sqrt(dt))
    d = 1 / u
    p = (math.exp((r - q) * dt) - d) / (u - d)
    if not 0 < p < 1:
        raise ValueError("tree unstable: increase steps")
    disc = math.exp(-r * dt)
    j = np.arange(steps + 1)
    spots = S * u ** (steps - j) * d ** j
    sign = 1.0 if kind == CALL else -1.0
    values = np.maximum(sign * (spots - K), 0.0)
    for n in range(steps - 1, -1, -1):
        values = disc * (p * values[: n + 1] + (1 - p) * values[1 : n + 2])
        if american:
            spots = spots[: n + 1] / u
            values = np.maximum(values, sign * (spots - K))
    return float(values[0])


def american_price(S: float, K: float, T: float, r: float, q: float, sigma: float, kind: str, steps: int = 200) -> float:
    return _tree(S, K, T, r, q, sigma, kind, steps, american=True)


def early_exercise_premium(S: float, K: float, T: float, r: float, q: float, sigma: float, kind: str, steps: int = 200) -> float:
    """American minus European value (per share), floored at zero. Both values come
    from the same tree so its discretisation error cancels out."""
    a = _tree(S, K, T, r, q, sigma, kind, steps, american=True)
    e = _tree(S, K, T, r, q, sigma, kind, steps, american=False)
    return max(a - e, 0.0)
