from datetime import timedelta

import pytest

from core.config import load_settings
from data.validators.quotes import check_quote
from tests.fakes import NOW, quote, underlying


@pytest.fixture
def rules():
    return load_settings().data_quality


def flags(q, rules, **kw):
    return check_quote(q, rules, **kw)


def test_clean_quote_is_usable(rules):
    r = flags(quote(), rules)
    assert r.flags == [] and r.usable


@pytest.mark.parametrize(
    "q, flag, usable",
    [
        (quote(bid=None), "missing_bid", False),
        (quote(ask=None), "missing_ask", False),
        (quote(ask=0), "missing_ask", False),
        (quote(bid=0, ask=0.05), "no_bid", False),
        (quote(bid=1.10, ask=1.00), "crossed", False),
        (quote(bid=1.00, ask=1.00), "locked", True),
        (quote(bid=2.00, ask=4.00), "wide_spread", True),     # the plan's $2/$4 example
        (quote(ts=NOW - timedelta(minutes=5)), "stale", False),
        (quote(ts=NOW + timedelta(minutes=1)), "bad_timestamp", False),
        (quote(ts=None), "missing_timestamp", False),
    ],
)
def test_quality_flags(rules, q, flag, usable):
    r = flags(q, rules)
    assert flag in r.flags
    assert r.usable is usable


def test_cheap_option_with_one_tick_spread_not_wide(rules):
    # 0.01 x 0.02 is 66% relative but only a penny wide — not flagged
    assert "wide_spread" not in flags(quote(bid=0.01, ask=0.02), rules).flags


def test_unknown_contract_unusable(rules):
    r = flags(quote(), rules, known_contract=False)
    assert "unknown_contract" in r.flags and not r.usable


def test_zero_size_is_soft_flag(rules):
    from dataclasses import replace
    r = flags(replace(quote(), bid_size=0), rules)
    assert "zero_size" in r.flags and r.usable


def test_underlying_quote_checked_too(rules):
    assert flags(underlying(), rules).usable
