"""Data-quality checks for incoming quotes.

Every quote gets a list of flags. "Hard" flags (see QualityResult.HARD)
mark the quote unusable; soft flags (wide_spread, locked, zero_size)
are kept for analysis but do not by themselves reject the quote.
"""

from __future__ import annotations

from core.config import DataQualitySettings
from data.models import OptionQuote, QualityResult, UnderlyingQuote


def check_quote(quote: OptionQuote | UnderlyingQuote, rules: DataQualitySettings, known_contract: bool = True) -> QualityResult:
    flags: list[str] = []
    bid, ask = quote.bid, quote.ask

    if not known_contract:
        flags.append("unknown_contract")

    if bid is None:
        flags.append("missing_bid")
    if ask is None or ask <= 0:
        flags.append("missing_ask")

    if bid is not None and ask is not None and ask > 0:
        if bid == 0:
            flags.append("no_bid")             # nobody willing to buy: no real market
        elif bid > ask:
            flags.append("crossed")
        elif bid == ask:
            flags.append("locked")
        if 0 < bid <= ask:
            spread = ask - bid
            mid = (ask + bid) / 2
            if spread / mid > rules.max_relative_spread and spread >= rules.min_absolute_spread_for_wide:
                flags.append("wide_spread")

    if quote.bid_size == 0 or quote.ask_size == 0:
        flags.append("zero_size")

    if quote.quote_ts is None:
        flags.append("missing_timestamp")
    else:
        age = (quote.received_at - quote.quote_ts).total_seconds()
        if age < -rules.max_clock_skew_seconds:
            flags.append("bad_timestamp")
        elif age > rules.max_quote_age_seconds:
            flags.append("stale")

    return QualityResult(flags)
