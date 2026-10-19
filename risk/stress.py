"""Stress scenarios calibrated on real SPY history.

From regular-hours one-minute bars, measure how far SPY moved within a
holding window (the largest move away from the entry price at any point in
the window, not just where it ended). Scenario shocks are the 99th, 99.9th
percentile and the worst such move ever recorded in the data, in both
directions, paired with implied-vol shocks (vol rises when the market falls).

If no history is available, conservative defaults are used and marked so.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

EASTERN = ZoneInfo("America/New_York")
OPEN, CLOSE = time(9, 30), time(16, 0)


@dataclass(frozen=True)
class Scenario:
    name: str
    spot_shift: float      # fraction, e.g. -0.02
    vol_shift: float       # absolute, e.g. +0.05 = +5 vol points


@dataclass(frozen=True)
class StressCalibration:
    window_minutes: int
    days: int
    p99_down: float
    p99_up: float
    p999_down: float
    p999_up: float
    worst_down: float
    worst_up: float
    worst_down_date: str
    worst_up_date: str
    source: str            # "history" | "default"

    def scenarios(self) -> list[Scenario]:
        # Vol responds to price: roughly +2.5 vol points per -1% for sharp drops, a smaller fall on rallies.
        def vol(move: float) -> float:
            return -2.5 * move if move < 0 else -0.8 * move
        out = []
        for label, d, u in (("p99", self.p99_down, self.p99_up), ("p99.9", self.p999_down, self.p999_up),
                            ("worst", self.worst_down, self.worst_up)):
            out.append(Scenario(f"{label} drop", d, vol(d)))
            out.append(Scenario(f"{label} rally", u, vol(u)))
        out.append(Scenario("vol spike, flat", 0.0, 0.05))
        out.append(Scenario("vol crush, flat", 0.0, -0.03))
        return out


DEFAULT = StressCalibration(120, 0, -0.015, 0.012, -0.03, 0.025, -0.06, 0.05, "", "", "default")


def calibrate(history: sqlite3.Connection, symbol: str = "SPY", window_minutes: int = 120) -> StressCalibration:
    rows = history.execute("SELECT ts, high, low, close FROM minute_bars WHERE symbol=? ORDER BY ts", (symbol,)).fetchall()
    days: dict[str, list[tuple[float, float, float]]] = {}
    for ts, hi, lo, cl in rows:
        t = datetime.fromisoformat(ts).astimezone(EASTERN)
        if OPEN <= t.time() < CLOSE:
            days.setdefault(t.date().isoformat(), []).append((hi, lo, cl))
    downs, ups, down_day, up_day = [], [], [], []
    for d, bars in days.items():
        if len(bars) < window_minutes + 10:            # skip half days with too little data
            continue
        a = np.array(bars)
        hi, lo, cl = a[:, 0], a[:, 1], a[:, 2]
        # For each entry minute, the most adverse excursion over the next `window_minutes` bars.
        n = len(cl) - window_minutes
        from numpy.lib.stride_tricks import sliding_window_view
        win_lo = sliding_window_view(lo[1:], window_minutes)[:n].min(axis=1)
        win_hi = sliding_window_view(hi[1:], window_minutes)[:n].max(axis=1)
        entry = cl[:n]
        dn, up = win_lo / entry - 1, win_hi / entry - 1
        downs.append(dn)
        ups.append(up)
        down_day += [d] * len(dn)
        up_day += [d] * len(up)
    if not downs:
        return DEFAULT
    dn, up = np.concatenate(downs), np.concatenate(ups)
    i_dn, i_up = int(dn.argmin()), int(up.argmax())
    return StressCalibration(
        window_minutes=window_minutes, days=len(downs),
        p99_down=float(np.quantile(dn, 0.01)), p99_up=float(np.quantile(up, 0.99)),
        p999_down=float(np.quantile(dn, 0.001)), p999_up=float(np.quantile(up, 0.999)),
        worst_down=float(dn[i_dn]), worst_up=float(up[i_up]),
        worst_down_date=down_day[i_dn], worst_up_date=up_day[i_up], source="history",
    )


def save(cal: StressCalibration, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(cal), indent=2))


def load(path: Path) -> StressCalibration:
    if not path.is_file():
        return DEFAULT
    return StressCalibration(**json.loads(path.read_text()))
