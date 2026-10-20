"""Risks that only exist when a position is held overnight or for several days.

Early assignment: whoever holds the other side of one of our short options may exercise it
before expiry. They only gain from that when the option has almost no time value left:
  * a short CALL in the money the day before SPY goes ex-dividend, if its remaining time value
    is smaller than the dividend (exercising captures the dividend), and
  * any short option deep in the money whose time value has all but disappeared.
Positions in either state are closed before it can happen. If an assignment happens anyway,
execution/assignment.py recognises it and cleans it up.

Expiry: multi-day positions are never held into an option's final week.
"""

from __future__ import annotations

from datetime import date, timedelta

from core.config import MultiDayLimits
from risk.positions import LegQuote, Position


def third_friday(year: int, month: int) -> date:
    first = date(year, month, 1)
    return first + timedelta(days=(4 - first.weekday()) % 7 + 14)


def ex_dividend_dates(year: int, limits: MultiDayLimits) -> list[date]:
    """SPY goes ex-dividend quarterly, on the third Friday of March, June, September and December
    (configured dates, when given, replace this rule)."""
    explicit = [date.fromisoformat(d) for d in limits.ex_dividend_dates]
    if explicit:
        return sorted(d for d in explicit if d.year == year)
    return [third_friday(year, m) for m in (3, 6, 9, 12)]


def next_ex_dividend(today: date, limits: MultiDayLimits) -> date | None:
    """The next ex-dividend date strictly after today (None when only explicit dates are configured and none remain)."""
    candidates = [d for y in (today.year, today.year + 1) for d in ex_dividend_dates(y, limits) if d > today]
    return min(candidates) if candidates else None


def _next_weekday(d: date) -> date:
    d += timedelta(days=1)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def intrinsic(option_type: str, strike: float, spot: float) -> float:
    return max(spot - strike, 0.0) if option_type == "call" else max(strike - spot, 0.0)


def assignment_risk(pos: Position, quotes: dict[str, LegQuote], spot: float, today: date, limits: MultiDayLimits) -> str | None:
    """Reason to close now because a short leg is likely to be assigned early, or None."""
    ex_div = next_ex_dividend(today, limits)
    ex_div_tomorrow = ex_div is not None and ex_div == _next_weekday(today)
    for leg in pos.legs:
        if leg.quantity >= 0 or leg.symbol not in quotes:
            continue
        value = intrinsic(leg.option_type, leg.strike, spot)
        if value <= 0:
            continue
        time_value = quotes[leg.symbol].mid - value
        if leg.option_type == "call" and ex_div_tomorrow and time_value < limits.dividend_estimate:
            return "dividend_assignment_risk"
        if time_value < limits.assignment_extrinsic_threshold:
            return "early_assignment_risk"
    return None


def near_expiry(pos: Position, today: date, limits: MultiDayLimits) -> bool:
    return (pos.expiration - today).days <= limits.close_days_before_expiry
