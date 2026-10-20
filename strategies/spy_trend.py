"""SPY trend call spreads: the market-timing signal and the spread builder. Offline: no orders, no P&L.

Implements the rules pre-registered in docs/research/spy_trend_preregistration.md, sections B and C1:
two warning lights (SPY below its 200-day average; 21-day realized volatility above 30%), an optional blended
momentum check, and a bull call spread chosen by delta (our own BSM delta, vendor delta only when our solver
refuses), sized so the whole debit is 1% of a fixed $100,000.

Separate from the mispricing strategy and the iron condor: this module only reuses the condor's pure helpers
(expiry choice, deltas, quote checks).
"""

from __future__ import annotations

import math
from bisect import bisect_left
from dataclasses import dataclass, field
from datetime import date

import numpy as np

from strategies.iron_condor import (
    MULTIPLIER, REFERENCE_EQUITY, DTE_TOLERANCE, ChainRow, Leg, _leg, _quote_problem, expiry_deltas, nearest_delta,
    select_expiry,
)

RISK_PER_SPREAD_PCT = 0.01
SMA_DAYS = 200
VOL_DAYS = 21
VOL_LIMIT = 0.30
MOM_LONG, MOM_MID, MOM_SHORT, MOM_SKIP = 252, 126, 63, 21

SIGNAL_LIGHTS = "lights"
SIGNAL_MOMENTUM = "lights+momentum"
SIGNAL_NONE = "none"                  # bar 8 comparison only: enter every week, no trend exit


# --- Signal ------------------------------------------------------------------------------------

@dataclass(frozen=True)
class SignalState:
    below_trend: bool | None          # L1
    too_wild: bool | None             # L2
    momentum: float | None            # average of the three returns


class TrendSignal:
    """Signal state for a day, from closes strictly before that day (section B)."""

    def __init__(self, closes: dict[date, float]):
        self.days = sorted(closes)
        c = np.array([closes[d] for d in self.days], dtype=float)
        n = len(c)
        self._sma = np.full(n, np.nan)
        self._vol = np.full(n, np.nan)
        self._mom = np.full(n, np.nan)
        cs = np.concatenate([[0.0], np.cumsum(c)])
        lr = np.diff(np.log(c)) if n > 1 else np.array([])
        for j in range(n):
            if j + 1 >= SMA_DAYS:
                self._sma[j] = (cs[j + 1] - cs[j + 1 - SMA_DAYS]) / SMA_DAYS
            if j >= VOL_DAYS:
                self._vol[j] = np.std(lr[j - VOL_DAYS:j], ddof=1) * math.sqrt(252)
            if j >= MOM_LONG:
                self._mom[j] = (c[j - MOM_SKIP] / c[j - MOM_LONG] - 1 + c[j - MOM_SKIP] / c[j - MOM_MID] - 1
                                + c[j] / c[j - MOM_SHORT] - 1) / 3
        self._close = c

    def state(self, day: date) -> SignalState:
        j = bisect_left(self.days, day) - 1
        if j < 0:
            return SignalState(None, None, None)
        sma, vol, mom = self._sma[j], self._vol[j], self._mom[j]
        return SignalState(None if np.isnan(sma) else bool(self._close[j] < sma),
                           None if np.isnan(vol) else bool(vol > VOL_LIMIT),
                           None if np.isnan(mom) else float(mom))

    def risk_on(self, day: date, variant: str) -> bool:
        """True only when every required input exists and says "risk on". Missing history counts as risk off."""
        if variant == SIGNAL_NONE:
            return True
        s = self.state(day)
        if s.below_trend is None or s.too_wild is None or s.below_trend or s.too_wild:
            return False
        if variant == SIGNAL_MOMENTUM:
            return s.momentum is not None and s.momentum > 0
        return True


# --- Spread builder ----------------------------------------------------------------------------

@dataclass(frozen=True)
class TrendParams:
    target_dte: int
    long_delta: float
    short_delta: float


@dataclass(frozen=True)
class CallSpread:
    day: date
    expiry: date
    dte: int
    long_call: Leg
    short_call: Leg
    debit: float              # $/share, executable
    contracts: int

    @property
    def legs(self) -> tuple[Leg, Leg]:
        return (self.long_call, self.short_call)

    @property
    def width(self) -> float:
        return self.short_call.strike - self.long_call.strike

    @property
    def planned_max_loss(self) -> float:
        return self.debit * MULTIPLIER * self.contracts

    @property
    def net_delta(self) -> float | None:
        if self.long_call.delta is None or self.short_call.delta is None:
            return None
        return self.long_call.delta - self.short_call.delta


@dataclass(frozen=True)
class SpreadDecision:
    day: date
    params: TrendParams
    spread: CallSpread | None
    reason: str
    code: str
    details: dict = field(default_factory=dict)


def build_call_spread(day: date, params: TrendParams, spot: float, chains: dict[date, list[ChainRow]], rate: float,
                      deltas_cache: dict | None = None, risk_budget: float = REFERENCE_EQUITY * RISK_PER_SPREAD_PCT,
                      dte_tolerance: int = DTE_TOLERANCE) -> SpreadDecision:
    """Apply C1.3–C1.7 for one day. The signal, the weekly limit and the 3-open limit live in the backtest."""
    def no(code: str, reason: str, **details) -> SpreadDecision:
        return SpreadDecision(day, params, None, reason, code, details)

    expiries = {exp: (exp - day).days for exp in chains}
    exp = select_expiry(expiries, params.target_dte, dte_tolerance)
    if exp is None:
        return no("no_expiry", f"no expiry within {dte_tolerance} days of {params.target_dte} days out")
    dte = expiries[exp]
    rows = chains[exp]
    key = (day, exp)
    if deltas_cache is not None and key in deltas_cache:
        ed = deltas_cache[key]
    else:
        ed = expiry_deltas(rows, spot, dte / 365.0, rate)
        if deltas_cache is not None:
            deltas_cache[key] = ed
    lc = nearest_delta(ed.call, params.long_delta)
    sc = nearest_delta(ed.call, params.short_delta)
    if lc is None or sc is None:
        return no("no_strike", "no call with a usable delta")
    if sc[0] <= lc[0]:
        return no("strikes_overlap", f"short call {sc[0]:g} is not above long call {lc[0]:g}")
    by_strike = {r.strike: r for r in rows}
    long_leg = _leg(by_strike[lc[0]], "C", +1, ed.call[lc[0]])
    short_leg = _leg(by_strike[sc[0]], "C", -1, ed.call[sc[0]])
    for leg in (long_leg, short_leg):
        problem = _quote_problem(leg)
        if problem:
            return no("bad_quote", f"{problem} on the {'short' if leg.side < 0 else 'long'} {leg.strike:g} call")
    debit = round(long_leg.ask - short_leg.bid, 4)
    width = sc[0] - lc[0]
    if debit <= 0 or debit >= width:
        return no("impossible_price", f"debit ${debit:.2f} is outside 0 to the ${width:g} width", debit=debit)
    contracts = math.floor(risk_budget / (debit * MULTIPLIER))
    if contracts < 1:
        return no("too_small", f"one spread costs ${debit * MULTIPLIER:,.0f}, over the ${risk_budget:,.0f} limit")
    spread = CallSpread(day, exp, dte, long_leg, short_leg, debit, contracts)
    return SpreadDecision(day, params, spread, "built", "built",
                          {"fallback_deltas": ed.fallbacks, "dividend_yield": ed.dividend_yield})
