"""Risk-free rates from FRED (free US Treasury yields, no account needed).

Yields are published per day after that day ends, so a snapshot only ever
uses observations dated strictly before its own date.
"""

from __future__ import annotations

import csv
import io
import math
from dataclasses import dataclass
from datetime import date, datetime

import requests

from core.config import Settings
from core.logging_setup import get_logger
from data.storage.db import MarketDataStore

log = get_logger("rates")
FRED_CSV = "https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}"
# Maturity in years for the series we know how to interpolate.
SERIES_MATURITY = {"DGS1MO": 1 / 12, "DGS3MO": 0.25, "DGS6MO": 0.5, "DGS1": 1.0}


@dataclass(frozen=True)
class RateInfo:
    rate: float              # continuously compounded, as a fraction
    source: str


def parse_fred_csv(text: str) -> list[tuple[date, float]]:
    rows = list(csv.reader(io.StringIO(text)))
    out = []
    for row in rows[1:]:
        if len(row) == 2 and row[1] not in ("", "."):
            out.append((date.fromisoformat(row[0]), float(row[1])))
    return out


def fetch_fred(series: str, timeout: float = 20.0) -> list[tuple[date, float]]:
    resp = requests.get(FRED_CSV.format(series=series), headers={"User-Agent": "options-quant/0.1"}, timeout=timeout)
    resp.raise_for_status()
    return parse_fred_csv(resp.text)


def update_rates(settings: Settings, store: MarketDataStore) -> dict[str, int]:
    counts = {}
    for series in settings.pricing.rate_series:
        obs = fetch_fred(series)[-400:]              # ~1.5 years is plenty
        counts[series] = store.upsert_rates(series, obs)
    store.log_event("INFO", "rates_updated", counts)
    return counts


def to_continuous(yield_pct: float) -> float:
    return math.log(1 + yield_pct / 100)


def risk_free_rate(settings: Settings, store: MarketDataStore, as_of: datetime, years: float) -> RateInfo:
    """Rate for a maturity of `years`, interpolated linearly between stored series."""
    points = []
    for series in settings.pricing.rate_series:
        found = store.latest_rate_before(series, as_of.date())
        if found and series in SERIES_MATURITY:
            obs_date, pct = found
            if (as_of.date() - obs_date).days <= settings.pricing.max_rate_age_days:
                points.append((SERIES_MATURITY[series], to_continuous(pct), f"{series}@{obs_date}"))
    if not points:
        fallback = settings.pricing.fallback_risk_free_rate
        log.warning("no recent stored rate before %s — using fallback %.4f", as_of.date(), fallback)
        return RateInfo(fallback, "fallback")
    points.sort()
    if years <= points[0][0]:
        return RateInfo(points[0][1], points[0][2])
    if years >= points[-1][0]:
        return RateInfo(points[-1][1], points[-1][2])
    for (t0, r0, s0), (t1, r1, s1) in zip(points, points[1:]):
        if t0 <= years <= t1:
            w = (years - t0) / (t1 - t0)
            return RateInfo(r0 + w * (r1 - r0), f"{s0}~{s1}")
    raise AssertionError("unreachable")
