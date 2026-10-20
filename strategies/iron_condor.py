"""Iron condor builder (volatility strategy, Phase 2). Offline: no orders, no P&L.

Implements the entry rules pre-registered in docs/research/iron_condor_preregistration.md, section C1:
expiry nearest the target DTE (±7 days, ties to the earlier one), short strikes by delta (our own BSM delta
from each quote's mid implied volatility, vendor delta only when our solver refuses), wings exactly W away,
quote checks on all four legs, credit ≥ 25% of W at executable prices, event filter, 1%-of-$100k sizing.

Every day either produces a condor or a plain-English reason why not. The once-a-week and 3-open limits
(C1.1–C1.2) depend on open positions, so they live in the backtest (Phase 3), not here.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date

from pricing.black_scholes import greeks
from pricing.implied_volatility import implied_vol
from pricing.volatility_surface import implied_forward

MULTIPLIER = 100
DTE_TOLERANCE = 7
MIN_CREDIT_PCT_OF_WIDTH = 0.25
EVENT_WINDOW_TRADING_DAYS = 5
REFERENCE_EQUITY = 100_000.0
RISK_PER_CONDOR_PCT = 0.01
FORWARD_PAIRS = 6


@dataclass(frozen=True)
class CondorParams:
    target_dte: int
    short_delta: float
    width: float


@dataclass(frozen=True)
class ChainRow:
    strike: float
    c_bid: float | None
    c_ask: float | None
    c_iv: float | None
    c_delta: float | None
    p_bid: float | None
    p_ask: float | None
    p_iv: float | None
    p_delta: float | None


@dataclass(frozen=True)
class Leg:
    kind: str                 # "C" | "P"
    strike: float
    side: int                 # +1 buy, -1 sell
    bid: float
    ask: float
    delta: float | None
    delta_source: str         # "own" | "vendor"

    @property
    def fill(self) -> float:
        """Executable price: receive the bid when selling, pay the ask when buying."""
        return self.ask if self.side > 0 else self.bid


@dataclass(frozen=True)
class Condor:
    day: date
    expiry: date
    dte: int
    short_put: Leg
    long_put: Leg
    short_call: Leg
    long_call: Leg
    width: float
    credit: float             # $/share, executable
    contracts: int

    @property
    def legs(self) -> tuple[Leg, Leg, Leg, Leg]:
        return (self.long_put, self.short_put, self.short_call, self.long_call)

    @property
    def max_loss_per_contract(self) -> float:
        return (self.width - self.credit) * MULTIPLIER

    @property
    def planned_max_loss(self) -> float:
        return self.max_loss_per_contract * self.contracts


@dataclass(frozen=True)
class Decision:
    day: date
    params: CondorParams
    condor: Condor | None
    reason: str               # "built" or why not, in plain English
    code: str                 # short machine-readable reason
    details: dict = field(default_factory=dict)


# --- Deltas from our own pricing engine --------------------------------------------------------

@dataclass(frozen=True)
class ExpiryDeltas:
    forward: float | None
    dividend_yield: float
    put: dict[float, tuple[float | None, str]]     # strike -> (delta, source)
    call: dict[float, tuple[float | None, str]]
    fallbacks: int
    put_iv: dict[float, float] = field(default_factory=dict)      # own mid IVs (solver successes only)
    call_iv: dict[float, float] = field(default_factory=dict)


def _mid(bid: float | None, ask: float | None) -> float | None:
    if bid is None or ask is None or ask <= 0 or bid < 0 or bid > ask:
        return None
    return (bid + ask) / 2


def expiry_deltas(rows: list[ChainRow], spot: float, years: float, rate: float) -> ExpiryDeltas:
    """Black-Scholes-Merton delta per contract from its mid-price implied volatility.

    The dividend yield comes from put-call parity on the strikes nearest the money (same method as the live
    pricing engine). If our solver refuses a quote, the vendor's delta is used and counted.
    """
    pairs = [(r.strike, _mid(r.c_bid, r.c_ask), _mid(r.p_bid, r.p_ask)) for r in rows]
    pairs = [(k, c, p) for k, c, p in pairs if c is not None and p is not None]
    fwd = implied_forward(pairs, spot, years, rate, FORWARD_PAIRS)
    q = fwd.implied_dividend_yield if fwd.implied_dividend_yield is not None else 0.0
    put, call, fallbacks, put_iv, call_iv = {}, {}, 0, {}, {}
    for r in rows:
        for kind, bid, ask, vendor, out, ivs in (("put", r.p_bid, r.p_ask, r.p_delta, put, put_iv),
                                                 ("call", r.c_bid, r.c_ask, r.c_delta, call, call_iv)):
            mid = _mid(bid, ask)
            iv = implied_vol(mid, spot, r.strike, years, rate, q, kind) if mid else None
            if iv is not None and iv.ok:
                ivs[r.strike] = iv.iv
                out[r.strike] = (greeks(spot, r.strike, years, rate, q, iv.iv, kind).delta, "own")
            else:
                out[r.strike] = (vendor, "vendor")
                fallbacks += vendor is not None
    return ExpiryDeltas(fwd.forward, q, put, call, fallbacks, put_iv, call_iv)


def skew_25d(ed: ExpiryDeltas) -> float | None:
    """25-delta put IV minus 25-delta call IV (own IVs). Amendment 4, report only."""
    p = nearest_delta({k: v for k, v in ed.put.items() if k in ed.put_iv and v[1] == "own"}, -0.25)
    c = nearest_delta({k: v for k, v in ed.call.items() if k in ed.call_iv and v[1] == "own"}, 0.25)
    if not p or not c:
        return None
    return ed.put_iv[p[0]] - ed.call_iv[c[0]]


# --- Rules -------------------------------------------------------------------------------------

def select_expiry(expiries: dict[date, int], target_dte: int, tolerance: int = DTE_TOLERANCE) -> date | None:
    """Closest calendar DTE to the target within ±tolerance; a tie goes to the earlier expiry (C1.3)."""
    ok = [(abs(dte - target_dte), exp) for exp, dte in expiries.items() if abs(dte - target_dte) <= tolerance]
    return min(ok)[1] if ok else None


def nearest_delta(deltas: dict[float, tuple[float | None, str]], target: float) -> tuple[float, float, str] | None:
    """Strike whose delta is closest to target. A tie goes to the strike farther from the money (the lower
    strike for puts, the higher for calls), the more conservative choice."""
    best = None
    for strike, (d, src) in deltas.items():
        if d is None:
            continue
        key = (abs(d - target), strike if target < 0 else -strike)
        if best is None or key < best[0]:
            best = (key, strike, d, src)
    return (best[1], best[2], best[3]) if best else None


def _quote_problem(leg: Leg) -> str | None:
    """C1.6: no missing bid/ask, not crossed or locked, bid > 0 on short legs."""
    if leg.bid is None or leg.ask is None or leg.ask <= 0:
        return "missing bid or ask"
    if leg.bid > leg.ask:
        return "crossed quote"
    if leg.bid == leg.ask and leg.bid > 0:
        return "locked quote"
    if leg.side < 0 and leg.bid <= 0:
        return "no bid on a short leg"
    return None


def _leg(row: ChainRow, kind: str, side: int, delta: tuple[float | None, str]) -> Leg:
    if kind == "P":
        return Leg("P", row.strike, side, row.p_bid, row.p_ask, delta[0], delta[1])
    return Leg("C", row.strike, side, row.c_bid, row.c_ask, delta[0], delta[1])


def event_ahead(day: date, trading_days: list[date], event_days: set[date],
                window: int = EVENT_WINDOW_TRADING_DAYS) -> date | None:
    """First FOMC/CPI day within the next `window` trading days, counting `day` itself (C1.8)."""
    try:
        i = trading_days.index(day)
    except ValueError:
        return None
    for d in trading_days[i:i + window]:
        if d in event_days:
            return d
    return None


def build_condor(day: date, params: CondorParams, spot: float, chains: dict[date, list[ChainRow]],
                 rate: float, trading_days: list[date], event_days: set[date],
                 deltas_cache: dict | None = None) -> Decision:
    """Apply C1.3–C1.9 for one day. `chains`: expiry -> rows for that expiry on `day`."""
    extra: dict = {}

    def no(code: str, reason: str, **details) -> Decision:
        return Decision(day, params, None, reason, code, {**extra, **details})

    expiries = {exp: (exp - day).days for exp in chains}
    exp = select_expiry(expiries, params.target_dte)
    if exp is None:
        return no("no_expiry", f"no expiry within {DTE_TOLERANCE} days of {params.target_dte} days out")
    dte = expiries[exp]
    rows = chains[exp]
    years = dte / 365.0
    key = (day, exp)
    if deltas_cache is not None and key in deltas_cache:
        ed = deltas_cache[key]
    else:
        ed = expiry_deltas(rows, spot, years, rate)
        if deltas_cache is not None:
            deltas_cache[key] = ed
    extra["skew_25d"] = skew_25d(ed)
    extra["expiry"] = exp
    sp = nearest_delta(ed.put, -params.short_delta)
    sc = nearest_delta(ed.call, params.short_delta)
    if sp is None or sc is None:
        return no("no_short_strike", "no put or call with a usable delta")
    by_strike = {r.strike: r for r in rows}
    sp_k, sc_k = sp[0], sc[0]
    if sp_k >= sc_k:
        return no("strikes_overlap", f"short put {sp_k:g} is not below short call {sc_k:g}")
    lp_k, lc_k = round(sp_k - params.width, 4), round(sc_k + params.width, 4)
    if lp_k not in by_strike or lc_k not in by_strike:
        missing = [k for k in (lp_k, lc_k) if k not in by_strike]
        return no("no_wing_strike", f"wing strike {', '.join(f'{k:g}' for k in missing)} isn't listed")
    legs = (_leg(by_strike[sp_k], "P", -1, ed.put[sp_k]), _leg(by_strike[lp_k], "P", +1, ed.put.get(lp_k, (None, "own"))),
            _leg(by_strike[sc_k], "C", -1, ed.call[sc_k]), _leg(by_strike[lc_k], "C", +1, ed.call.get(lc_k, (None, "own"))))
    for leg in legs:
        problem = _quote_problem(leg)
        if problem:
            return no("bad_quote", f"{problem} on the {'short' if leg.side < 0 else 'long'} "
                                   f"{leg.strike:g} {'put' if leg.kind == 'P' else 'call'}")
    short_put, long_put, short_call, long_call = legs
    credit = round(short_put.bid + short_call.bid - long_put.ask - long_call.ask, 4)
    if credit < MIN_CREDIT_PCT_OF_WIDTH * params.width:
        return no("credit_too_small", f"credit ${credit:.2f} is under 25% of the ${params.width:g} wing width",
                  credit=credit)
    ev = event_ahead(day, trading_days, event_days)
    if ev is not None:
        return no("event_ahead", f"FOMC or CPI on {ev.isoformat()}, within {EVENT_WINDOW_TRADING_DAYS} trading days",
                  credit=credit)
    max_loss = (params.width - credit) * MULTIPLIER
    contracts = math.floor(REFERENCE_EQUITY * RISK_PER_CONDOR_PCT / max_loss) if max_loss > 0 else 0
    if contracts < 1:
        return no("too_small", f"one condor risks ${max_loss:,.0f}, over the ${REFERENCE_EQUITY * RISK_PER_CONDOR_PCT:,.0f} limit")
    condor = Condor(day, exp, dte, short_put, long_put, short_call, long_call, params.width, credit, contracts)
    return Decision(day, params, condor, "built", "built",
                    {**extra, "fallback_deltas": ed.fallbacks, "dividend_yield": ed.dividend_yield})
