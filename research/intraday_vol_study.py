"""Intraday volatility-pattern study on real SPY option quotes (Databento CBBO-1m).

Two measurements per trading day, every 5 minutes, using only data known at that minute:

1. At-the-money implied volatility at a constant 14-day maturity (from mid prices; forward
   implied by put-call parity; total variance interpolated between the two bracketing expiries).
2. A directly tradable test: a short iron butterfly (sell the ATM call and put, buy wings
   $5 away; nearest expiry at least 7 days out) opened at a fixed time A and closed at a later
   time B, priced at EXECUTABLE quotes (sell at bid, buy at ask) plus fees. The long iron
   butterfly is the mirror image and is reported too.

To guard against false discoveries, every pattern is measured on the first ~2/3 of the days
(discovery) and must hold on the remaining ~1/3 (confirmation), which the selection never sees.
This is research only: it changes no trading rule.
"""

from __future__ import annotations

import math
import os
import sqlite3
import statistics as st
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from core.config import PROJECT_ROOT, Settings
from pricing.implied_volatility import implied_vol
from pricing.snapshot import years_to_expiry

EASTERN = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")
TARGET_DAYS = 14
RATE = 0.04                      # financing assumption; the forward comes from parity so this barely matters
WINGS = (5.0, 20.0)              # $ distance of the protective wings; narrow and wide versions
ENTRY_TIMES = [time(9, 45), time(10, 30), time(12, 0), time(13, 0), time(14, 0)]
EXIT_TIMES = [time(10, 30), time(12, 0), time(13, 0), time(14, 0), time(15, 0), time(15, 30)]
STUDY_DIR = PROJECT_ROOT / "data" / "storage" / "studies"


def _grid(day: date) -> list[datetime]:
    t = datetime.combine(day, time(9, 35), EASTERN)
    out = []
    while t.time() <= time(15, 55):
        out.append(t.astimezone(UTC))
        t += timedelta(minutes=5)
    return out


def _atm_iv(quotes: dict[tuple, tuple[float, float]], strikes: list[float], exp: date, spot: float, now: datetime):
    """(forward, ATM IV) for one expiry from mid prices, or None."""
    T = years_to_expiry(exp, now)
    if T <= 0:
        return None
    near = sorted((k for k in strikes if ("call", k) in quotes and ("put", k) in quotes), key=lambda k: abs(k - spot))[:3]
    if len(near) < 2:
        return None
    growth = math.exp(RATE * T)
    fwd = float(np.median([k + growth * (sum(quotes[("call", k)]) / 2 - sum(quotes[("put", k)]) / 2) for k in near]))
    q = RATE - math.log(fwd / spot) / T
    pts = []
    below = [k for k in strikes if k < fwd and ("put", k) in quotes]
    above = [k for k in strikes if k >= fwd and ("call", k) in quotes]
    for k, kind in ([(max(below), "put")] if below else []) + ([(min(above), "call")] if above else []):
        mid = sum(quotes[(kind, k)]) / 2
        r = implied_vol(mid, spot, k, T, RATE, q, kind)
        if r.ok:
            pts.append((math.log(k / fwd), r.iv))
    if len(pts) < 2:
        return None
    (k0, v0), (k1, v1) = pts
    iv = v0 + (v1 - v0) * (0 - k0) / (k1 - k0) if k1 != k0 else (v0 + v1) / 2
    return fwd, iv


def study_day(day: date, history_path: str, opra_dir: str | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(iv_path, iron_fly_trades) for one day."""
    from backtesting.data import load_day                         # imported here for worker processes
    hist = sqlite3.connect(history_path)
    data = load_day(day, hist, Path(opra_dir) if opra_dir else None)
    hist.close()
    by_exp: dict[date, list[float]] = {}
    for exp, kind, k in data.meta.values():
        by_exp.setdefault(exp, []).append(k)
    for e in by_exp:
        by_exp[e] = sorted(set(by_exp[e]))

    iv_rows, books = [], {}
    for t in _grid(day):
        rows, spot = data.quotes_at(t), data.spot_at(t)
        if rows is None or spot is None:
            continue
        book = {}
        for sym, b, a in rows[["symbol", "bid", "ask"]].itertuples(index=False):
            if b > 0 and a >= b:
                exp, kind, k = data.meta[sym]
                book[(exp, kind, k)] = (float(b), float(a))
        books[t] = (spot, book)
        exps = sorted(e for e in by_exp if (e - day).days >= 7)
        per = {}
        for e in exps:
            qe = {(kind, k): v for (ee, kind, k), v in book.items() if ee == e}
            got = _atm_iv(qe, by_exp[e], e, spot, t)
            if got:
                per[e] = got[1]
        if not per:
            continue
        # constant-maturity ATM IV: interpolate total variance between the expiries bracketing TARGET_DAYS
        pts = sorted((years_to_expiry(e, t), v) for e, v in per.items())
        target = TARGET_DAYS / 365
        lo = [p for p in pts if p[0] <= target]
        hi = [p for p in pts if p[0] >= target]
        if lo and hi:
            (t0, v0), (t1, v1) = lo[-1], hi[0]
            w = 0.0 if t1 == t0 else (target - t0) / (t1 - t0)
            iv = math.sqrt(max((v0 * v0 * t0) + w * (v1 * v1 * t1 - v0 * v0 * t0), 1e-12) / target)
        else:
            iv = lo[-1][1] if lo else hi[0][1]          # only one side available: nearest expiry's ATM vol
        local = t.astimezone(EASTERN)
        iv_rows.append({"day": day, "minute": local.hour * 60 + local.minute, "spot": spot, "atm_iv_14d": iv})

    trades = []
    for WING in WINGS:
     for a in ENTRY_TIMES:
        ta = datetime.combine(day, a, EASTERN).astimezone(UTC)
        if ta not in books:
            continue
        spot, book = books[ta]
        exps = sorted({e for (e, _, _) in book if (e - day).days >= 7})
        if not exps:
            continue
        e = exps[0]
        k = round(spot)
        legs = {("call", k): -1, ("put", k): -1, ("call", k + WING): 1, ("put", k - WING): 1}
        if not all((e, kind, kk) in book for (kind, kk) in legs):
            continue
        def px(bk, kind, kk, side):                       # side: "bid" or "ask"
            return bk[(e, kind, kk)][0 if side == "bid" else 1]

        # Short fly: sell the centre (at bid), buy the wings (at ask). Long fly: the reverse.
        short_entry = sum(px(book, kind, kk, "bid") if q < 0 else -px(book, kind, kk, "ask") for (kind, kk), q in legs.items())
        long_entry = sum(px(book, kind, kk, "ask") if q < 0 else -px(book, kind, kk, "bid") for (kind, kk), q in legs.items())
        entry_mid = sum(-q * sum(book[(e, kind, kk)]) / 2 for (kind, kk), q in legs.items())
        for b in EXIT_TIMES:
            if b <= a:
                continue
            tb = datetime.combine(day, b, EASTERN).astimezone(UTC)
            if tb not in books:
                continue
            spot_b, book_b = books[tb]
            if not all((e, kind, kk) in book_b for (kind, kk) in legs):
                continue
            short_exit = sum(px(book_b, kind, kk, "ask") if q < 0 else -px(book_b, kind, kk, "bid") for (kind, kk), q in legs.items())
            long_exit = sum(px(book_b, kind, kk, "bid") if q < 0 else -px(book_b, kind, kk, "ask") for (kind, kk), q in legs.items())
            exit_mid = sum(-q * sum(book_b[(e, kind, kk)]) / 2 for (kind, kk), q in legs.items())
            fees = 0.05 * 4 * 2                          # 4 legs, open and close
            trades.append({
                "day": day, "wing": WING, "entry": a.strftime("%H:%M"), "exit": b.strftime("%H:%M"), "dte": (e - day).days,
                "short_fly_exec": (short_entry - short_exit) * 100 - fees,   # credit received minus debit to close
                "long_fly_exec": (long_exit - long_entry) * 100 - fees,     # credit on close minus debit paid
                "short_fly_mid": (entry_mid - exit_mid) * 100,              # gross, at mid, before costs
                "spot_move_pct": spot_b / spot - 1,
            })
    return pd.DataFrame(iv_rows), pd.DataFrame(trades)


def run_study(settings: Settings, days: list[date], workers: int | None = None) -> tuple[pd.DataFrame, pd.DataFrame]:
    STUDY_DIR.mkdir(parents=True, exist_ok=True)
    hp = str(PROJECT_ROOT / settings.storage.history_path)
    ivs, trs = [], []
    with ProcessPoolExecutor(max_workers=workers or max(os.cpu_count() - 2, 1)) as pool:
        for iv, tr in pool.map(study_day, days, [hp] * len(days)):
            ivs.append(iv)
            trs.append(tr)
    return pd.concat(ivs, ignore_index=True), pd.concat(trs, ignore_index=True)


# --- report -------------------------------------------------------------------------------

def _t_stat(x: pd.Series) -> float:
    x = x.dropna()
    if len(x) < 3 or x.std(ddof=1) == 0:
        return float("nan")
    return float(x.mean() / (x.std(ddof=1) / math.sqrt(len(x))))


def build_report(iv: pd.DataFrame, trades: pd.DataFrame, out_dir: Path | None = None, multi_day: pd.DataFrame | None = None) -> Path:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    out_dir = out_dir or PROJECT_ROOT / "docs" / "research"
    img = out_dir / "img"
    img.mkdir(parents=True, exist_ok=True)
    days = sorted(iv["day"].unique())
    cut = days[int(len(days) * 2 / 3)] if len(days) >= 9 else days[-1]
    first, last = days[0], days[-1]

    # 1. average intraday IV path relative to 9:45
    base = iv[iv["minute"] == 9 * 60 + 45].set_index("day")["atm_iv_14d"]
    iv = iv[iv["day"].isin(base.index)].copy()
    iv["chg"] = (iv["atm_iv_14d"] - iv["day"].map(base)) * 100                  # vol points
    iv["half"] = np.where(iv["day"] < cut, "discovery", "confirmation")
    g = iv.groupby(["half", "minute"])["chg"].agg(["mean", "std", "count"]).reset_index()
    fig, ax = plt.subplots(figsize=(9, 4))
    for half, color in (("discovery", "#2b6cb0"), ("confirmation", "#c05621")):
        h = g[g["half"] == half]
        if h.empty:
            continue
        x = h["minute"] / 60
        se = h["std"] / np.sqrt(h["count"])
        ax.plot(x, h["mean"], color=color, label=f"{half} ({iv[iv['half'] == half]['day'].nunique()} days)")
        ax.fill_between(x, h["mean"] - 2 * se, h["mean"] + 2 * se, color=color, alpha=0.15)
    ax.axhline(0, color="#1a202c", lw=0.8)
    ax.set_xlabel("time of day (ET, hours)")
    ax.set_ylabel("change in 14-day ATM IV since 9:45 (vol pts)")
    ax.set_title("Average intraday path of SPY implied volatility (shaded: ±2 standard errors)")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.25)
    fig.tight_layout()
    fig.savefig(img / "intraday_iv_path.png", dpi=130)
    plt.close(fig)

    marks = [10 * 60 + 30, 12 * 60, 13 * 60, 14 * 60, 15 * 60, 15 * 60 + 30, 15 * 60 + 55]
    L = [f"# Intraday volatility-pattern study — {first} to {last}", "",
         f"Real SPY option quotes (Databento OPRA consolidated best bid/offer, 1-minute), {len(days)} trading days. "
         f"Discovery = days before {cut}; confirmation = {cut} onward (never used to pick anything). "
         "Research only — no trading rule has been changed.", ""]

    # 2. iron butterfly table
    tr = trades.copy()
    tr["half"] = np.where(tr["day"] < cut, "discovery", "confirmation")
    rows, survivors = [], []
    for (wing, a, b), grp in tr.groupby(["wing", "entry", "exit"]):
        d, c = grp[grp["half"] == "discovery"], grp[grp["half"] == "confirmation"]
        for side in ("short_fly_exec", "long_fly_exec"):
            td, tc = _t_stat(d[side]), _t_stat(c[side])
            ok = (d[side].mean() > 0 and td > 2 and c[side].mean() > 0 and tc > 2)
            rows.append((wing, a, b, side, len(d), d[side].mean(), td, len(c), c[side].mean() if len(c) else float("nan"), tc,
                         grp["short_fly_mid"].mean(), ok))
            if ok:
                survivors.append((wing, a, b, side))

    # 3. bottom line
    pos_disc = [r for r in rows if r[5] > 0 and r[6] > 2]
    L += ["> **Bottom line.** " + (
        f"{len(survivors)} entry/exit combination(s) made money after real bid/ask costs in BOTH the discovery and the "
        f"confirmation period: {', '.join(f'\${w:.0f}-wing {s.split(chr(95))[0]} fly {a}→{b}' for w, a, b, s in survivors)}. "
        "Candidates for a forward test, not proven strategies." if survivors else
        f"No entry/exit combination made money after real bid/ask costs in both periods "
        f"({len(pos_disc)} of {len(rows)} looked significant in discovery alone; by chance alone roughly "
        f"{max(round(len(rows) * 0.025), 1)} would). The intraday pattern, if any, is smaller than the cost of trading it "
        "with these structures."), ""]

    L += ["## 1. How implied volatility moves during the day", "",
          "Average change in 14-day at-the-money implied volatility since 9:45 ET, in vol points:", "",
          "| Period | " + " | ".join(f"{m // 60}:{m % 60:02d}" for m in marks) + " |", "|---|" + "---:|" * len(marks)]
    for half in ("discovery", "confirmation"):
        h = g[g["half"] == half].set_index("minute")
        if h.empty:
            continue
        cells = []
        for m in marks:
            if m in h.index:
                se = h.loc[m, "std"] / math.sqrt(h.loc[m, "count"])
                cells.append(f"{h.loc[m, 'mean']:+.2f} (±{2 * se:.2f})")
            else:
                cells.append("–")
        L.append(f"| {half} | " + " | ".join(cells) + " |")
    L += ["", "![intraday IV](img/intraday_iv_path.png)", "",
          "A curve that drifts the same way in both periods, with its shaded band clear of zero, is a real tendency. "
          "Whether it pays depends on section 2. Note: implied vol here uses calendar time, which counts the hours "
          "after the close; that alone pushes measured IV slightly up through the day, so a gentle rise is partly "
          "a measurement effect.", ""]

    L += ["## 2. Could you have traded it? Iron butterflies at real bid/ask", "",
          f"Short fly = sell the at-the-money call and put, buy wings $5 or $20 away (profits if options lose value). "
          "Long fly = the reverse. Nearest expiry at least 7 days out. Entered and exited at executable prices, "
          "$0.40 fees per round trip, per 1 fly ($ per contract set). *Gross* is the short fly at mid prices, before costs.", "",
          "| Wings | Entry → exit | Side | Discovery n | Mean $ | t | Confirmation n | Mean $ | t | Gross (mid) $ | Holds in both? |",
          "|---:|---|---|---:|---:|---:|---:|---:|---:|---:|:---:|"]
    for wing, a, b, side, nd, md, td, nc, mc, tc, gross, ok in rows:
        L.append(f"| ${wing:.0f} | {a} → {b} | {side.split('_')[0]} | {nd} | {md:+.2f} | {td:+.1f} | {nc} | {mc:+.2f} | {tc:+.1f} | "
                 f"{gross:+.2f} | {'**yes**' if ok else '–'} |")
    costs = tr.assign(cost=-(tr['short_fly_exec'] + tr['long_fly_exec']) / 2).groupby('wing')['cost'].median()
    L += ["", "Typical round-trip cost of one fly (bid-ask paid on 4 legs, twice, plus fees): "
          + ", ".join(f"about ${c:.2f} with ${w:.0f} wings" for w, c in costs.items()) + ". "
          "A pattern has to move option prices by more than that to be worth trading.", ""]

    L += ["## 3. Notes and limits", "",
          f"* Data: {len(days)} days so far. Each additional month of history makes these estimates firmer; "
          "the study is re-run with `main.py vol-study` as more data arrives.",
          "* A t-statistic above 2 in both periods is the bar. Testing many combinations means a few will clear 2 "
          "in one period by luck; requiring both periods guards against that.",
          "* Short flies lose on large SPY moves (they are short gamma). Their results are always shown at "
          "executable prices so this risk is fully in the numbers.",
          "* The no-overnight rule is respected in sections 1–2: every trade opens and closes the same day.", ""]
    if multi_day is not None:
        L += multi_day_section(multi_day, cut)
    path = out_dir / "intraday_vol_study.md"
    path.write_text("\n".join(L))
    return path


# --- research outside the current rules: holding overnight / for days ------------------------------

HOLDS = [("overnight", time(15, 30), 1, time(10, 30)), ("1 day", time(10, 30), 1, time(10, 30)),
         ("3 days", time(10, 30), 3, time(10, 30)), ("5 days", time(10, 30), 5, time(10, 30))]


def _book_at(data, t):
    rows = data.quotes_at(t)
    if rows is None:
        return None
    book = {}
    for sym, b, a in rows[["symbol", "bid", "ask"]].itertuples(index=False):
        if b > 0 and a >= b:
            book[data.meta[sym]] = (float(b), float(a))
    return book


def multi_day_pair(start_day: date, later: dict[int, date], history_path: str) -> list[dict]:
    """Short/long iron fly opened on start_day and closed N trading days later, at executable prices."""
    from backtesting.data import load_day
    hist = sqlite3.connect(history_path)
    d0 = load_day(start_day, hist)
    loaded = {n: load_day(d, hist) for n, d in later.items()}
    hist.close()
    out = []
    for WING in WINGS:
     for name, t_in, n, t_out in HOLDS:
        if n not in loaded:
            continue
        ta = datetime.combine(start_day, t_in, EASTERN).astimezone(UTC)
        d1 = loaded[n]
        tb = datetime.combine(d1.day, t_out, EASTERN).astimezone(UTC)
        b0, b1, spot = _book_at(d0, ta), _book_at(d1, tb), d0.spot_at(ta)
        if not b0 or not b1 or spot is None:
            continue
        # expiry still 8+ days away when closed (one day of margin inside the downloaded 7-30 day window)
        exps = sorted({e for (e, _, _) in b0 if (e - d1.day).days >= 8})
        if not exps:
            continue
        e, k = exps[0], round(spot)
        legs = {("call", k): -1, ("put", k): -1, ("call", k + WING): 1, ("put", k - WING): 1}
        if not all((e, kind, kk) in b0 and (e, kind, kk) in b1 for (kind, kk) in legs):
            continue

        def leg(bk, kind, kk, i):
            return bk[(e, kind, kk)][i]
        short_pnl = (sum(leg(b0, kd, kk, 0) if q < 0 else -leg(b0, kd, kk, 1) for (kd, kk), q in legs.items())
                     - sum(leg(b1, kd, kk, 1) if q < 0 else -leg(b1, kd, kk, 0) for (kd, kk), q in legs.items())) * 100 - 0.40
        long_pnl = (sum(leg(b1, kd, kk, 0) if q < 0 else -leg(b1, kd, kk, 1) for (kd, kk), q in legs.items())
                    - sum(leg(b0, kd, kk, 1) if q < 0 else -leg(b0, kd, kk, 0) for (kd, kk), q in legs.items())) * 100 - 0.40
        wing_risk = WING * 100                                  # max loss is below the wing width for either fly
        out.append({"day": start_day, "wing": WING, "hold": name, "short_fly_exec": short_pnl, "long_fly_exec": long_pnl,
                    "max_risk": wing_risk})
    return out


def run_multi_day(settings: Settings, days: list[date], workers: int | None = None) -> pd.DataFrame:
    hp = str(PROJECT_ROOT / settings.storage.history_path)
    idx = {d: i for i, d in enumerate(days)}
    jobs = []
    for d in days:
        later = {n: days[idx[d] + n] for n in (1, 3, 5) if idx[d] + n < len(days)}
        if later:
            jobs.append((d, later))
    rows = []
    with ProcessPoolExecutor(max_workers=workers or max(os.cpu_count() - 2, 1)) as pool:
        for r in pool.map(multi_day_pair, [j[0] for j in jobs], [j[1] for j in jobs], [hp] * len(jobs)):
            rows += r
    return pd.DataFrame(rows)


def multi_day_section(md: pd.DataFrame, cut: date) -> list[str]:
    if md.empty:
        return []
    md = md.copy()
    md["half"] = np.where(md["day"] < cut, "discovery", "confirmation")
    L = ["## 4. Outside the current rules: holding overnight or for several days", "",
         "**Research only.** The current risk limits forbid overnight positions; nothing here changes that. It shows "
         "whether longer holds change the cost picture, so the owner can decide with real numbers. Same iron "
         "butterflies, real bid/ask, expiry still at least 8 days out when closed. Per fly; the most one can lose "
         "is the wing width × 100 ($500 or $2,000).", "",
         "| Wings | Hold | Side | Discovery n | Mean $ | t | Win % | Confirmation n | Mean $ | t | Win % |",
         "|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for wing in sorted(md["wing"].unique()):
     for name, *_ in HOLDS:
        for side in ("short_fly_exec", "long_fly_exec"):
            d = md[(md["wing"] == wing) & (md["hold"] == name) & (md["half"] == "discovery")][side]
            c = md[(md["wing"] == wing) & (md["hold"] == name) & (md["half"] == "confirmation")][side]
            if d.empty:
                continue
            L.append(f"| ${wing:.0f} | {name} | {side.split('_')[0]} | {len(d)} | {d.mean():+.2f} | {_t_stat(d):+.1f} | {(d > 0).mean():.0%} | "
                     f"{len(c)} | {c.mean() if len(c) else float('nan'):+.2f} | {_t_stat(c):+.1f} | "
                     f"{(c > 0).mean() if len(c) else float('nan'):.0%} |")
    held = []
    for wing in sorted(md["wing"].unique()):
        for name, *_ in HOLDS:
            for side in ("short_fly_exec", "long_fly_exec"):
                d = md[(md["wing"] == wing) & (md["hold"] == name) & (md["half"] == "discovery")][side]
                c = md[(md["wing"] == wing) & (md["hold"] == name) & (md["half"] == "confirmation")][side]
                if len(d) > 2 and len(c) > 2 and d.mean() > 0 and c.mean() > 0 and _t_stat(d) > 2 and _t_stat(c) > 2:
                    held.append(f"${wing:.0f}-wing {side.split('_')[0]} fly held {name}")
    L += ["", "**Positive after costs with t > 2 in both periods:** " + (", ".join(held) if held else "none") + "."]
    L += ["", "Holds of several days overlap (a 5-day hold starts every day), so their t-statistics overstate certainty "
          "somewhat. Short flies carry crash risk that a few months of data may not contain.", ""]
    return L
