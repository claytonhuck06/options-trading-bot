"""Databento spending guard and contract selection. No network: the client is faked."""

import sqlite3
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from core.config import load_settings
from data.collectors.databento_guard import BudgetExceeded, GuardedDatabento
from data.collectors.opra_history import _weeks, select_contracts

ROOT = Path(__file__).resolve().parent.parent
CFG = load_settings().databento


class FakeClient:
    def __init__(self, cost=1.0, fail=False):
        self.cost, self.fail, self.downloads = cost, fail, 0
        outer = self

        class Meta:
            def get_cost(self, **kw):
                return outer.cost

            def get_billable_size(self, **kw):
                return 1000

        class TS:
            def get_range(self, **kw):
                outer.downloads += 1
                if outer.fail:
                    raise RuntimeError("network")
                return "data"

        self.metadata, self.timeseries = Meta(), TS()


def guard(cost=1.0, fail=False, cfg=CFG):
    client = FakeClient(cost, fail)
    return GuardedDatabento(cfg, sqlite3.connect(":memory:"), client), client


REQ = dict(dataset="OPRA.PILLAR", schema="cbbo-1m", symbols=["X"], stype_in="raw_symbol", start="2026-10-16", end="2026-10-17")


def test_download_recorded_in_ledger():
    g, c = guard(0.50)
    assert g.get_range(**REQ) == "data" and c.downloads == 1
    s = g.summary()
    assert s.spent_usd == pytest.approx(0.50) and s.requests == 1 and s.remaining_usd == pytest.approx(99.50)


def test_per_request_cap_blocks_before_download():
    g, c = guard(CFG.max_request_usd + 0.01)
    with pytest.raises(BudgetExceeded, match="per-request cap"):
        g.get_range(**REQ)
    assert c.downloads == 0 and g.summary().requests == 0


def test_total_budget_blocks_before_download():
    cfg = CFG.model_copy(update={"total_budget_usd": 2.0})
    g, c = guard(0.90, cfg=cfg)
    g.get_range(**REQ)
    g.get_range(**REQ)
    with pytest.raises(BudgetExceeded, match="owner's decision"):
        g.get_range(**REQ)                                   # 0.90 * 3 = 2.70 > 2.00
    assert c.downloads == 2 and g.summary().spent_usd == pytest.approx(1.80)


def test_failed_download_still_counts_against_budget():
    g, c = guard(0.40, fail=True)
    with pytest.raises(RuntimeError):
        g.get_range(**REQ)
    assert g.summary().spent_usd == pytest.approx(0.40)    # conservative: may have been billed


def test_budget_cannot_be_configured_above_free_credit(config_dir, edit_yaml):
    from core.config import ConfigError
    edit_yaml(config_dir / "settings.yaml", databento__total_budget_usd=500)
    with pytest.raises(ConfigError):
        load_settings("development", config_dir=config_dir)


def test_only_the_guard_makes_billable_calls():
    offenders = []
    for path in ROOT.rglob("*.py"):
        rel = path.relative_to(ROOT)
        if rel.parts[0] in (".venv", "tests") or rel == Path("data/collectors/databento_guard.py"):
            continue
        text = path.read_text()
        if "timeseries.get_range" in text or "db.Historical(" in text or "databento.Historical(" in text:
            offenders.append(str(rel))
    assert offenders == []


def test_contract_selection_band_and_expiry():
    defs = pd.DataFrame({
        "raw_symbol": ["A", "B", "C", "D", "E"],
        "expiration": pd.to_datetime(["2026-10-30 20:00", "2026-10-30 20:00", "2026-10-21 20:00", "2026-12-18 21:00",
                                      "2026-10-30 20:00"], utc=True),
        "strike_price": [770.0, 700.0, 770.0, 770.0, 790.0],
        "instrument_class": ["C", "P", "C", "C", "P"],
    })
    # ref 770: band ±3.5% = 743.05..796.95; DTE 7..30 from 2026-10-19
    assert select_contracts(defs, date(2026, 10, 19), 770.0, CFG) == ["A", "E"]


def test_weeks_grouping():
    days = [date(2026, 10, 12), date(2026, 10, 13), date(2026, 10, 19), date(2026, 10, 20)]
    assert [len(w) for w in _weeks(days)] == [2, 2]
