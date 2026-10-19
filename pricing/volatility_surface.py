"""Implied forward and per-expiry volatility smile fits.

Forward. Instead of assuming a dividend yield, each expiry's forward price is
implied from put-call parity using the pairs nearest the money:
F = K + e^{rT} (C_mid - P_mid). The implied dividend yield follows from
q = r - ln(F/S)/T. Early exercise makes parity inexact for American options,
which is why only near-the-money pairs are used.

Smile. Implied volatility is fitted as a polynomial in log-moneyness
k = ln(K/F) by weighted least squares. Only out-of-the-money options are
used (calls above F, puts below F), since they carry little early-exercise
value. Tighter markets get more weight. Outliers are removed robustly.

Fair value for a contract uses its leave-one-out fit: the smile refitted
without that contract, so a mispriced quote cannot drag the fit toward
itself. The leave-one-out residual comes from the hat matrix, so no refit is
needed: e_loo = e / (1 - h).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np


@dataclass(frozen=True)
class ForwardEstimate:
    forward: float | None
    implied_dividend_yield: float | None
    pairs_used: int
    dispersion: float | None        # max-min of per-pair forwards: how noisy the estimate is


def implied_forward(pairs: list[tuple[float, float, float]], spot: float, years: float, rate: float, n_pairs: int) -> ForwardEstimate:
    """pairs: (strike, call_mid, put_mid). Uses the n_pairs strikes nearest spot."""
    if years <= 0 or not pairs:
        return ForwardEstimate(None, None, 0, None)
    growth = math.exp(rate * years)
    nearest = sorted(pairs, key=lambda p: abs(p[0] - spot))[:n_pairs]
    fwds = [k + growth * (c - p) for k, c, p in nearest]
    f = float(np.median(fwds))
    q = rate - math.log(f / spot) / years
    return ForwardEstimate(f, q, len(fwds), max(fwds) - min(fwds))


@dataclass
class SmileFit:
    degree: int
    coefficients: list[float]          # IV(k) = sum c_i k^i
    points_used: int
    points_excluded: int
    rmse_iv: float                     # weighted RMS residual, vol units
    status: str
    k_min: float = 0.0
    k_max: float = 0.0
    loo_iv: dict[int, float] = field(default_factory=dict)      # index -> leave-one-out fitted IV
    loo_std: dict[int, float] = field(default_factory=dict)     # index -> std of that prediction
    excluded: set[int] = field(default_factory=set)
    sigma2: float = float("nan")                                # variance of a weight-1 residual
    xtwx_inv: np.ndarray | None = None

    def predict_std(self, k: float, weight: float) -> float:
        """Std of (observed IV - fitted IV) for a new point at k with the given weight."""
        x = _design(np.array([k]), self.degree)[0]
        h = float(x @ self.xtwx_inv @ x) * weight
        return math.sqrt(self.sigma2 / weight * (1 + h))

    def iv_at(self, k: float) -> float:
        return float(sum(c * k ** i for i, c in enumerate(self.coefficients)))

    @property
    def atm_iv(self) -> float:
        return self.coefficients[0] if self.coefficients else float("nan")


def _design(k: np.ndarray, degree: int) -> np.ndarray:
    return np.vander(k, degree + 1, increasing=True)


def fit_smile(k: list[float], iv: list[float], weights: list[float], degree: int, min_points: int, mad_threshold: float) -> SmileFit:
    k_arr, iv_arr, w_arr = np.asarray(k, float), np.asarray(iv, float), np.asarray(weights, float)
    n = len(k_arr)
    if n < min_points:
        return SmileFit(degree, [], n, 0, float("nan"), "too_few_points")

    keep = np.ones(n, bool)
    for _ in range(5):
        X, W = _design(k_arr[keep], degree), w_arr[keep]
        sw = np.sqrt(W)
        coef, *_ = np.linalg.lstsq(X * sw[:, None], iv_arr[keep] * sw, rcond=None)
        resid_all = iv_arr - _design(k_arr, degree) @ coef
        scaled = resid_all * np.sqrt(w_arr / w_arr[keep].mean())
        mad = np.median(np.abs(scaled[keep] - np.median(scaled[keep]))) * 1.4826
        if mad <= 0:
            break
        new_keep = np.abs(scaled) <= mad_threshold * mad
        if new_keep.sum() < min_points or np.array_equal(new_keep, keep):
            break
        keep = new_keep

    X, W = _design(k_arr[keep], degree), w_arr[keep]
    sw = np.sqrt(W)
    Xw = X * sw[:, None]
    coef, *_ = np.linalg.lstsq(Xw, iv_arr[keep] * sw, rcond=None)
    resid = iv_arr[keep] - X @ coef
    dof = max(int(keep.sum()) - (degree + 1), 1)
    sigma2 = float((W * resid ** 2).sum() / dof)          # variance of a weight-1 observation
    rmse = math.sqrt(float((W * resid ** 2).sum() / W.sum()))
    xtwx_inv = np.linalg.pinv(Xw.T @ Xw)

    fit = SmileFit(degree, [float(c) for c in coef], int(keep.sum()), int(n - keep.sum()), rmse, "ok",
                   float(k_arr[keep].min()), float(k_arr[keep].max()), sigma2=sigma2, xtwx_inv=xtwx_inv)
    kept_idx = np.flatnonzero(keep)
    hat = np.einsum("ij,jk,ik->i", Xw, xtwx_inv, Xw)       # leverage of each kept point
    for j, i in enumerate(kept_idx):
        h = min(float(hat[j]), 0.99)
        fitted = float(X[j] @ coef)
        fit.loo_iv[int(i)] = iv_arr[i] - (iv_arr[i] - fitted) / (1 - h)
        fit.loo_std[int(i)] = math.sqrt(sigma2 / W[j] * (1 + h / (1 - h)))
    for i in np.flatnonzero(~keep):
        fit.loo_iv[int(i)] = fit.iv_at(float(k_arr[i]))         # already excluded from the fit
        fit.loo_std[int(i)] = fit.predict_std(float(k_arr[i]), float(w_arr[i]))
        fit.excluded.add(int(i))
    return fit


def calendar_violations(points: list[tuple[str, float, float]], tolerance: float = 0.0) -> list[tuple[str, str]]:
    """points: (label, years, at-the-money IV). Total variance sigma^2 * T must not fall as
    expiry lengthens; a fall means the term structure itself allows arbitrage (or the fits are off)."""
    ordered = sorted(points, key=lambda p: p[1])
    bad = []
    for (l1, t1, v1), (l2, t2, v2) in zip(ordered, ordered[1:]):
        if v2 * v2 * t2 < v1 * v1 * t1 - tolerance:
            bad.append((l1, l2))
    return bad
