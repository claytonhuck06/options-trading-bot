from datetime import date

from data.validators.arbitrage import find_violations, parity_violations
from pricing.snapshot import SnapOption
from tests.fakes import NOW

EXP = date(2026, 10, 30)


def opt(kind, K, bid, ask):
    return SnapOption(f"{kind[0]}{K}", kind, K, EXP, bid, ask, 10, 10, NOW, NOW)


def test_clean_chain_has_no_violations():
    chain = [opt("call", 760, 10.0, 10.1), opt("call", 765, 6.5, 6.6), opt("call", 770, 3.8, 3.9),
             opt("put", 760, 2.0, 2.1), opt("put", 765, 3.4, 3.5), opt("put", 770, 5.6, 5.7)]
    assert find_violations(chain, 0.03, 0.04) == {}


def test_monotonic_violation():
    chain = [opt("call", 760, 10.0, 10.1), opt("call", 765, 10.2, 10.4)]
    assert find_violations(chain, 0.03, 0.04) == {"c760": {"monotonic"}, "c765": {"monotonic"}}


def test_vertical_too_wide():
    chain = [opt("call", 760, 16.0, 16.1), opt("call", 765, 10.0, 10.1)]   # 5-wide spread bid at 5.9
    assert "vertical" in find_violations(chain, 0.03, 0.04)["c760"]


def test_butterfly_credit():
    chain = [opt("call", 760, 10.0, 10.1), opt("call", 765, 7.5, 7.6), opt("call", 770, 4.0, 4.1)]
    v = find_violations(chain, 0.03, 0.04)
    assert all("butterfly" in v[s] for s in ("c760", "c765", "c770"))


def test_parity_violation():
    chain = [opt("call", 765, 6.5, 6.6), opt("put", 765, 3.4, 3.5)]
    # implied C-P at F=768.1 is ~3.1, inside [3.0, 3.2]
    assert parity_violations(chain, 768.1, 0.03, 0.0) == {}
    assert parity_violations(chain, 770.0, 0.03, 0.0) == {"c765": {"parity"}, "p765": {"parity"}}
