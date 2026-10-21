"""Short the small-stock spike, exactly as pre-registered.

Rules: docs/research/spike_short_preregistration.md.
  * Signal at day t's close: raw close_{t-1} in [$0.50, $8), split-adjusted close-to-close jump >= +30%,
    dollar volume on t >= $2M, >= 20 bars of history.
  * Short at t+1's open (skipped if SSR would be in force), 20% buy-stop, cover at t+1's close (primary).
  * 7.5% of equity per position (1.5% risk at a 20% stop), at most 3 open; ties broken by t's dollar volume.
  * Shortable filters: F1 (grid estimated from today's Alpaca borrow flags; primary), F2 (easy to borrow today),
    F0 (none).
"""

from __future__ import annotations

import math
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

ET = ZoneInfo("America/New_York")
DATA_START = date(2016, 1, 1)
EXCHANGES = {"NYSE", "NASDAQ", "AMEX"}
MAX_PRICE = 8.0
MIN_PRICE = 0.50
JUMP = 0.30
NEIGHBOUR_JUMPS = (0.20, 0.50)
MIN_DOLLAR_VOL = 2e6
THIN_DOLLAR_VOL = 5e6
MIN_HISTORY = 20
MAX_GAP_DAYS = 5
SSR_DROP = 0.10
STOP = 0.20
STOP_SLIPPAGE = 0.01
POSITION = 0.075
MAX_OPEN = 3
BORROW_APR = 0.20
START_EQUITY = 100_000.0
ETB_SHARE = 0.70
MIN_CELL = 20
PRICE_BANDS = (0.5, 1.0, 2.0, 3.0, 5.0, 8.0)
DV_BANDS = (0.0, 1e6, 5e6, 20e6, math.inf)
LIVE_DAYS = 20
EXTREME = 1.0

_EXCLUDE = re.compile(
    r"\b(etf|etn|fund|trust|warrants?|rights?|units?|preferred|depositary|notes?|ultra|leveraged|inverse|"
    r"bull|bear|2x|3x|-1x|-2x|-3x)\b", re.IGNORECASE)


_SYMBOL = re.compile(r"^[A-Z]{1,5}$")


def excluded(symbol: str, name: str) -> bool:
    return not _SYMBOL.match(symbol) or bool(_EXCLUDE.search(name or ""))


def cost_for(raw_price: float, dollar_vol: float) -> float:
    """Round-trip cost by raw entry price; doubled for thin names."""
    base = 0.02 if raw_price < 2 else 0.01 if raw_price < 5 else 0.006
    return base * (2 if dollar_vol < THIN_DOLLAR_VOL else 1)


# --- Data ----------------------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS raw (symbol TEXT NOT NULL, day TEXT NOT NULL, close REAL, volume REAL,
                                PRIMARY KEY (symbol, day));
CREATE TABLE IF NOT EXISTS adj (symbol TEXT NOT NULL, day TEXT NOT NULL, open REAL, high REAL, low REAL,
                                close REAL, volume REAL, PRIMARY KEY (symbol, day));
CREATE TABLE IF NOT EXISTS spy (day TEXT PRIMARY KEY, close REAL);
CREATE TABLE IF NOT EXISTS assets (symbol TEXT PRIMARY KEY, name TEXT, exchange TEXT, status TEXT,
                                   easy_to_borrow INTEGER);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


def open_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.executescript(SCHEMA)
    return conn


def last_completed_day(now: datetime | None = None) -> date:
    now = (now or datetime.now(timezone.utc)).astimezone(ET)
    return now.date() if now.hour * 60 + now.minute >= 16 * 60 + 30 else now.date() - timedelta(days=1)


def fetch_assets(creds) -> list[dict]:
    """Read-only GET of active and inactive assets (no trading client)."""
    import requests
    out: dict[str, dict] = {}
    for status in ("inactive", "active"):          # active last, so a reused ticker keeps today's flags
        resp = requests.get(f"{creds.base_url}/v2/assets", params={"status": status, "asset_class": "us_equity"},
                            headers={"APCA-API-KEY-ID": creds.key_id, "APCA-API-SECRET-KEY": creds.secret_key},
                            timeout=60)
        resp.raise_for_status()
        for a in resp.json():
            if a["exchange"] in EXCHANGES and not excluded(a["symbol"], a.get("name") or ""):
                out[a["symbol"]] = {"symbol": a["symbol"], "name": a.get("name") or "", "exchange": a["exchange"],
                                    "status": status, "easy_to_borrow": int(bool(a.get("easy_to_borrow"))
                                                                            and status == "active")}
    return list(out.values())


def _bars(client, symbols, start: date, end: date, adjustment):
    from alpaca.data.enums import DataFeed
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
    req = StockBarsRequest(symbol_or_symbols=symbols, timeframe=TimeFrame.Day, feed=DataFeed.SIP, adjustment=adjustment,
                           start=datetime(start.year, start.month, start.day, tzinfo=timezone.utc),
                           end=min(datetime(end.year, end.month, end.day, 23, tzinfo=timezone.utc),
                                   datetime.now(timezone.utc) - timedelta(minutes=16)))   # free SIP: 15-min delay
    for s, bars in client.get_stock_bars(req).data.items():
        for b in bars:
            d = b.timestamp.astimezone(ET).date()
            if d <= end:
                yield s, d.isoformat(), b


def _retry(fn, tries: int = 5, wait: float = 20.0):
    import time
    for k in range(tries):
        try:
            return fn()
        except Exception as e:                       # network drops on a long free download
            if k == tries - 1:
                raise
            print(f"  retrying after {type(e).__name__} ({k + 1}/{tries - 1})", flush=True)
            time.sleep(wait)


def download(creds, conn: sqlite3.Connection, end: date, batch: int = 100) -> None:
    """Assets; raw bars for everything (kept only for symbols that ever closed under $8); split-adjusted
    bars for those symbols; SPY with dividends for the benchmark. Resumes after an interruption."""
    from alpaca.data.enums import Adjustment
    from alpaca.data.historical import StockHistoricalDataClient
    client = StockHistoricalDataClient(creds.key_id, creds.secret_key)
    meta = dict(conn.execute("SELECT key, value FROM meta"))
    if meta.get("raw_done") != end.isoformat():
        assets = _retry(lambda: fetch_assets(creds))
        for t in ("assets", "raw", "adj", "spy", "meta"):
            conn.execute(f"DELETE FROM {t}")
        conn.executemany("INSERT INTO assets VALUES (:symbol,:name,:exchange,:status,:easy_to_borrow)", assets)
        conn.commit()
        syms = sorted(a["symbol"] for a in assets)
        print(f"{len(syms):,} stocks (active + delisted); raw bars from {DATA_START}...", flush=True)
        kept = 0
        for i in range(0, len(syms), batch):
            rows = _retry(lambda: [(s, d, b.close, b.volume) for s, d, b in
                                   _bars(client, syms[i:i + batch], DATA_START, end, Adjustment.RAW)])
            cheap = {s for s, _, c, _ in rows if c is not None and c < MAX_PRICE}
            conn.executemany("INSERT OR REPLACE INTO raw VALUES (?,?,?,?)", [r for r in rows if r[0] in cheap])
            conn.commit()
            kept += len(cheap)
            print(f"  raw {min(i + batch, len(syms)):,}/{len(syms):,} ({kept:,} ever under ${MAX_PRICE:.0f})", flush=True)
        conn.execute("INSERT OR REPLACE INTO meta VALUES ('raw_done', ?)", (end.isoformat(),))
        conn.commit()
    keep = [r[0] for r in conn.execute("SELECT DISTINCT symbol FROM raw ORDER BY symbol")]
    done = {r[0] for r in conn.execute("SELECT DISTINCT symbol FROM adj")}
    todo = [s for s in keep if s not in done]
    print(f"split-adjusted bars: {len(done):,} done, {len(todo):,} to go", flush=True)
    for i in range(0, len(todo), batch):
        rows = _retry(lambda: [(s, d, b.open, b.high, b.low, b.close, b.volume)
                               for s, d, b in _bars(client, todo[i:i + batch], DATA_START, end, Adjustment.SPLIT)])
        conn.executemany("INSERT OR REPLACE INTO adj VALUES (?,?,?,?,?,?,?)", rows)
        conn.commit()
        print(f"  split-adjusted {min(i + batch, len(todo)):,}/{len(todo):,}", flush=True)
    conn.executemany("INSERT OR REPLACE INTO spy VALUES (?,?)",
                     _retry(lambda: [(d, b.close) for _, d, b in _bars(client, ["SPY"], DATA_START, end, Adjustment.ALL)]))
    conn.execute("INSERT OR REPLACE INTO meta VALUES ('end', ?)", (end.isoformat(),))
    conn.execute("INSERT OR REPLACE INTO meta VALUES ('fetched_at', ?)", (datetime.now(timezone.utc).isoformat(),))
    conn.commit()


def load_bars(conn: sqlite3.Connection) -> pd.DataFrame:
    """Long table, one row per symbol-day, sorted: adjusted OHLCV plus the raw close."""
    df = pd.read_sql("""SELECT a.symbol, a.day, a.open, a.high, a.low, a.close, a.volume, r.close AS raw_close
                        FROM adj a JOIN raw r ON r.symbol = a.symbol AND r.day = a.day""", conn)
    df["day"] = pd.to_datetime(df["day"])
    return df.sort_values(["symbol", "day"], ignore_index=True)


# --- Signals -------------------------------------------------------------------------------------

def add_features(df: pd.DataFrame) -> pd.DataFrame:
    """Everything a signal on day t needs, using only rows up to t, plus the next bar's open/date."""
    df = df.copy()
    g = df.groupby("symbol", sort=False)
    df["dollar_vol"] = df["close"] * df["volume"]          # split-invariant
    df["prev_close"] = g["close"].shift(1)
    df["prev_raw_close"] = g["raw_close"].shift(1)
    df["prev_day"] = g["day"].shift(1)
    df["n_before"] = g.cumcount()
    df["dv20_before"] = g["dollar_vol"].transform(lambda s: s.shift(1).rolling(20, min_periods=20).median())
    df["jump"] = df["close"] / df["prev_close"] - 1
    df["next_day"] = g["day"].shift(-1)
    df["next_open"] = g["open"].shift(-1)
    df["row"] = np.arange(len(df))
    return df


def signals(df: pd.DataFrame, jump: float = JUMP) -> pd.DataFrame:
    """F0 signals (no shortable filter), each with an SSR flag. df must come from add_features."""
    ok = ((df["prev_raw_close"] >= MIN_PRICE) & (df["prev_raw_close"] < MAX_PRICE) & (df["jump"] >= jump)
          & (df["dollar_vol"] >= MIN_DOLLAR_VOL) & (df["n_before"] >= MIN_HISTORY)
          & ((df["day"] - df["prev_day"]).dt.days <= MAX_GAP_DAYS))
    sig = df[ok].copy()
    sig["ssr_today"] = sig["low"] <= (1 - SSR_DROP) * sig["prev_close"]
    return sig


def etb_grid(conn: sqlite3.Connection) -> pd.DataFrame:
    """F1 grid from today's borrow flags: share of active $0.50-$8 stocks that are easy to borrow, by cell."""
    raw = pd.read_sql("""SELECT r.symbol, r.day, r.close, r.volume, a.easy_to_borrow FROM raw r
                         JOIN assets a ON a.symbol = r.symbol WHERE a.status = 'active'""", conn)
    last = raw["day"].max()
    days = sorted(raw["day"].unique())[-20:]
    recent = raw[raw["day"].isin(days)].copy()
    recent["dv"] = recent["close"] * recent["volume"]
    per = recent.groupby("symbol").agg(n=("day", "size"), dv=("dv", "median"), etb=("easy_to_borrow", "first"))
    per["price"] = recent[recent["day"] == last].set_index("symbol")["close"]
    per = per[(per["n"] >= 15) & (per["price"] >= MIN_PRICE) & (per["price"] < MAX_PRICE)]
    per["pband"] = pd.cut(per["price"], PRICE_BANDS, right=False)
    per["dband"] = pd.cut(per["dv"], DV_BANDS, right=False)
    grid = per.groupby(["pband", "dband"], observed=False)["etb"].agg(["size", "mean"]).rename(
        columns={"size": "stocks", "mean": "etb_share"})
    grid["shortable"] = (grid["stocks"] >= MIN_CELL) & (grid["etb_share"] >= ETB_SHARE)
    return grid


def in_grid(grid: pd.DataFrame, price: float, dv: float) -> bool:
    if not (price == price and dv == dv) or not (PRICE_BANDS[0] <= price < PRICE_BANDS[-1]):
        return False
    p = pd.cut([price], PRICE_BANDS, right=False)[0]
    d = pd.cut([dv], DV_BANDS, right=False)[0]
    return bool(grid.loc[(p, d), "shortable"])


# --- Simulation ----------------------------------------------------------------------------------

@dataclass
class Trade:
    symbol: str
    signal_day: pd.Timestamp
    entry_day: pd.Timestamp
    exit_day: pd.Timestamp
    entry: float
    exit: float
    stopped: bool
    cost: float                        # fraction of notional (round trip + stop slippage + borrow)
    gross: float                       # (entry - exit) / entry
    net: float
    notional: float = 0.0
    pnl: float = 0.0


@dataclass
class Result:
    equity: pd.Series                  # daily account value, SPY calendar
    trades: list[Trade]
    problems: list[str] = field(default_factory=list)
    skipped_ssr: int = 0
    skipped_slots: int = 0


def _trade_path(df: pd.DataFrame, sig_row: int, hold: int, stop: float | None, cost_mult: float):
    """Exit for a short entered at the next bar's open. Returns (path of (day, mark)), stopped, cost, or None."""
    s = df.iloc[sig_row]
    first = sig_row + 1
    if first >= len(df) or df.at[first, "symbol"] != s["symbol"]:
        return None
    if (df.at[first, "day"] - s["day"]).days > MAX_GAP_DAYS:
        return None
    entry = df.at[first, "open"]
    if not entry or entry <= 0 or entry != entry:
        return None
    raw_entry = entry * df.at[first, "raw_close"] / df.at[first, "close"]
    cost = cost_for(raw_entry, s["dollar_vol"]) * cost_mult
    level = entry * (1 + stop) if stop is not None else math.inf
    path, stopped = [], False
    prev_day = s["day"]
    for k in range(hold):
        i = first + k
        if i >= len(df) or df.at[i, "symbol"] != s["symbol"] or (df.at[i, "day"] - prev_day).days > MAX_GAP_DAYS:
            if not path:
                return None
            break                        # data ends or halt: out at the last mark
        o, h, c = df.at[i, "open"], df.at[i, "high"], df.at[i, "close"]
        if k > 0 and o >= level:
            path.append((df.at[i, "day"], o)); stopped = True; break
        if h >= level:
            path.append((df.at[i, "day"], level)); stopped = True; break
        path.append((df.at[i, "day"], c))
        prev_day = df.at[i, "day"]
    days_held = (path[-1][0] - df.at[first, "day"]).days
    cost += (STOP_SLIPPAGE * cost_mult if stopped else 0.0) + BORROW_APR * days_held / 365 * cost_mult
    return entry, path, stopped, cost


def simulate(df: pd.DataFrame, sig: pd.DataFrame, calendar: pd.DatetimeIndex, eligible=None, hold: int = 1,
             stop: float | None = STOP, cost_mult: float = 1.0) -> Result:
    """eligible: function(signal row) -> bool (the shortable filter); None = F0."""
    sig = sig[~sig["ssr_today"]]
    if eligible is not None:
        sig = sig[[eligible(r) for r in sig.itertuples()]]
    by_entry: dict[pd.Timestamp, list] = {}
    skipped_ssr = 0
    problems: list[str] = []
    for r in sig.itertuples():
        nd, no = r.next_day, r.next_open
        if nd != nd or no != no:
            continue
        if no <= (1 - SSR_DROP) * r.close:
            skipped_ssr += 1
            continue
        by_entry.setdefault(nd, []).append(r)

    equity = START_EQUITY
    open_pos: list[dict] = []
    trades: list[Trade] = []
    values = []
    skipped_slots = 0
    for day in calendar:
        pnl_today = 0.0
        # entries at today's open
        cands = sorted(by_entry.get(day, []), key=lambda r: -r.dollar_vol)
        for r in cands:
            if len(open_pos) >= MAX_OPEN:
                skipped_slots += 1
                continue
            got = _trade_path(df, r.row, hold, stop, cost_mult)
            if got is None:
                continue
            entry, path, stopped, cost = got
            if path[0][0] != day or not (r.day < day):
                problems.append(f"{r.symbol} {r.day.date()}: entry not on the next bar")
                continue
            notional = POSITION * equity
            open_pos.append({"r": r, "entry": entry, "path": path, "mark": entry, "shares": notional / entry,
                             "stopped": stopped, "cost": cost, "notional": notional, "pnl": 0.0})
        still = []
        for p in open_pos:
            for d, m in p["path"]:
                if d == day:
                    dp = p["shares"] * (p["mark"] - m)
                    p["mark"] = m
                    p["pnl"] += dp
                    pnl_today += dp
            if p["path"][-1][0] <= day:
                fee = p["cost"] * p["notional"]
                pnl_today -= fee
                p["pnl"] -= fee
                exit_ = p["path"][-1][1]
                gross = (p["entry"] - exit_) / p["entry"]
                trades.append(Trade(p["r"].symbol, p["r"].day, p["path"][0][0], p["path"][-1][0], p["entry"], exit_,
                                    p["stopped"], p["cost"], gross, gross - p["cost"], p["notional"], p["pnl"]))
            else:
                still.append(p)
        open_pos = still
        equity += pnl_today
        values.append(equity)
    return Result(pd.Series(values, index=calendar), trades, problems, skipped_ssr, skipped_slots)


# --- Stats and verdict ---------------------------------------------------------------------------

@dataclass
class Stats:
    trades: int
    win: float
    avg: float
    total: float
    cagr: float
    max_dd: float
    sharpe: float
    stop_rate: float
    yearly: dict[int, float]
    yearly_trades: dict[int, int]


def _max_dd(eq: pd.Series) -> float:
    return float((1 - eq / eq.cummax()).max()) if len(eq) else 0.0


def equity_stats(eq: pd.Series) -> tuple[float, float, float, float, dict[int, float]]:
    rets = eq.pct_change().fillna(eq.iloc[0] / START_EQUITY - 1)
    years = (eq.index[-1] - eq.index[0]).days / 365.25 + 1 / 252
    total = eq.iloc[-1] / START_EQUITY - 1
    sd = rets.std()
    yearly = {int(y): float((1 + g).prod() - 1) for y, g in rets.groupby(rets.index.year)}
    return (float(total), float((1 + total) ** (1 / years) - 1), _max_dd(pd.concat([pd.Series([START_EQUITY]), eq])),
            float(rets.mean() / sd * math.sqrt(252)) if sd > 0 else 0.0, yearly)


def stats(res: Result) -> Stats:
    total, cagr, dd, sharpe, yearly = equity_stats(res.equity)
    nets = np.array([t.net for t in res.trades])
    yt: dict[int, int] = {}
    for t in res.trades:
        yt[t.entry_day.year] = yt.get(t.entry_day.year, 0) + 1
    return Stats(len(nets), float((nets > 0).mean()) if len(nets) else 0.0, float(nets.mean()) if len(nets) else 0.0,
                 total, cagr, dd, sharpe, float(np.mean([t.stopped for t in res.trades])) if res.trades else 0.0,
                 yearly, yt)


def spy_stats(spy: pd.Series) -> tuple[float, float, float, float, dict[int, float]]:
    return equity_stats(spy / spy.iloc[0] * START_EQUITY)


@dataclass
class Verdict:
    bars: list[tuple[str, bool, str]]

    @property
    def go(self) -> bool:
        return all(ok for _, ok, _ in self.bars)


def evaluate(main: Result, double: Result, neighbours: dict[float, Result], spy_sharpe: float,
             live_total: int, live_etb: int) -> Verdict:
    s, d = stats(main), stats(double)
    counted = [y for y, n in s.yearly_trades.items() if n >= 10]
    pos = sum(1 for y in counted if s.yearly.get(y, 0) > 0)
    nb = [(j, stats(r)) for j, r in sorted(neighbours.items())]
    share = live_etb / live_total if live_total else 0.0
    bars = [
        ("Data check", not main.problems, "no problems" if not main.problems else "; ".join(main.problems[:5])),
        ("Enough trades (≥ 100)", s.trades >= 100, f"{s.trades} trades"),
        ("Makes money after costs", s.cagr > 0 and s.avg > 0, f"CAGR {s.cagr:+.2%}, average trade {s.avg:+.2%}"),
        ("Worth the risk (Sharpe ≥ SPY buy-and-hold)", s.sharpe >= spy_sharpe, f"{s.sharpe:.2f} vs {spy_sharpe:.2f}"),
        ("Survivable (worst drop ≤ 25%)", s.max_dd <= 0.25, f"{s.max_dd:.1%}"),
        ("Consistent (positive in ≥ 70% of years with 10+ trades)", bool(counted) and pos >= 0.7 * len(counted),
         f"{pos} of {len(counted)}"),
        ("Survives double costs", d.cagr > 0, f"CAGR {d.cagr:+.2%}"),
        ("Not fragile (+20% and +50% versions make money)", all(t.cagr > 0 and t.avg > 0 for _, t in nb),
         "; ".join(f"+{j:.0%}: CAGR {t.cagr:+.2%}, avg {t.avg:+.2%}" for j, t in nb)),
        ("Actually placeable (≥ 25% of last 20 days' signals easy to borrow today, ≥ 10 of them)",
         share >= 0.25 and live_etb >= 10, f"{live_etb} of {live_total} ({share:.0%})"),
    ]
    return Verdict(bars)


# --- Report --------------------------------------------------------------------------------------

def _chart(curves: dict[str, pd.Series], path: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(10, 5))
    colors = ["#2563eb", "#9ca3af", "#f59e0b", "#111827"]
    for (label, eq), c in zip(curves.items(), colors):
        ax.plot(eq, label=label, color=c, lw=1.4)
    ax.set_yscale("log")
    ax.set_ylabel("Account value ($, log scale)")
    ax.set_title("Short the small-stock spike (day trade, 20% stop) vs SPY")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=120)
    plt.close(fig)


def _row(label: str, st: Stats) -> str:
    return (f"| {label} | {st.trades:,} | {st.win:.0%} | {st.avg:+.2%} | {st.total:+.0%} | {st.cagr:+.2%} "
            f"| {st.max_dd:.1%} | {st.sharpe:.2f} | {st.stop_rate:.0%} |")


def run(conn: sqlite3.Connection, out_dir: Path, today: date) -> tuple[Path, bool]:
    end = date.fromisoformat(conn.execute("SELECT value FROM meta WHERE key='end'").fetchone()[0])
    df = add_features(load_bars(conn))
    spy = pd.read_sql("SELECT day, close FROM spy", conn, parse_dates=["day"]).set_index("day")["close"].sort_index()
    grid = etb_grid(conn)
    etb_today = dict(conn.execute("SELECT symbol, easy_to_borrow FROM assets"))

    def f1(r) -> bool:
        return in_grid(grid, r.prev_raw_close, r.dv20_before)

    def f2(r) -> bool:
        return bool(etb_today.get(r.symbol))

    sig = {j: signals(df, j) for j in (JUMP, *NEIGHBOUR_JUMPS)}
    first = sig[JUMP]["day"].min()
    cal = spy.index[spy.index > first]
    spy_total, spy_cagr, spy_dd, spy_sharpe, spy_yearly = spy_stats(spy.loc[cal])

    runs = {
        "F1": simulate(df, sig[JUMP], cal, f1),
        "F1_2x": simulate(df, sig[JUMP], cal, f1, cost_mult=2.0),
        "F2": simulate(df, sig[JUMP], cal, f2),
        "F0": simulate(df, sig[JUMP], cal, None),
        "F1_free": simulate(df, sig[JUMP], cal, f1, cost_mult=0.0),
        "F1_hold3": simulate(df, sig[JUMP], cal, f1, hold=3),
        "F1_hold5": simulate(df, sig[JUMP], cal, f1, hold=5),
        "F1_nostop": simulate(df, sig[JUMP], cal, f1, stop=None),
        "F0_nostop": simulate(df, sig[JUMP], cal, None, stop=None),
    }
    neighbours = {j: simulate(df, sig[j], cal, f1) for j in NEIGHBOUR_JUMPS}

    # Live check: the last 20 completed trading days' F0 signals (SSR-on-t skips removed), easy to borrow today?
    live_days = spy.index[-LIVE_DAYS:]
    live = sig[JUMP][sig[JUMP]["day"].isin(live_days) & ~sig[JUMP]["ssr_today"]]
    live_syms = list(zip(live["day"].dt.date, live["symbol"]))
    live_etb = sum(1 for _, s in live_syms if etb_today.get(s))

    verdict = evaluate(runs["F1"], runs["F1_2x"], neighbours, spy_sharpe, len(live_syms), live_etb)
    st = {k: stats(v) for k, v in runs.items()}
    nst = {j: stats(r) for j, r in neighbours.items()}

    chart = "spike_short_equity.png"
    _chart({"F1 shortable estimate (primary)": runs["F1"].equity, "F0 no filter (original idea)": runs["F0"].equity,
            "F2 easy to borrow today": runs["F2"].equity,
            "SPY buy-and-hold": spy.loc[cal] / spy.loc[cal].iloc[0] * START_EQUITY}, out_dir / "img" / chart)
    trades_path = out_dir / "spike_short_trades.csv"
    pd.DataFrame([{**t.__dict__, "filter": k} for k in ("F1", "F0") for t in runs[k].trades]).to_csv(
        trades_path, index=False, float_format="%.6g")

    labels = {"F1": "**F1 shortable estimate (primary)**", "F1_2x": "F1, double costs", "F1_free": "F1, no costs (reference)",
              "F2": "F2 easy to borrow today", "F0": "F0 no filter (original idea)", "F1_hold3": "F1, hold to close of t+3",
              "F1_hold5": "F1, hold to close of t+5", "F1_nostop": "F1, no stop", "F0_nostop": "F0, no stop"}
    md = [f"# Short the Small-Stock Spike — {today}", "",
          "*Paper research only; not investment advice. Rules: "
          "[spike_short_preregistration.md](spike_short_preregistration.md), committed before any data was fetched.*", "",
          f"## Verdict: **{'GO' if verdict.go else 'NO-GO'}** ({sum(ok for _, ok, _ in verdict.bars)} of "
          f"{len(verdict.bars)} bars passed)", "",
          "| Bar | Result | Detail |", "|---|---|---|"]
    md += [f"| {i} · {n} | {'PASS' if ok else '**FAIL**'} | {d} |" for i, (n, ok, d) in enumerate(verdict.bars, 1)]
    md += ["", f"## Results ({cal[0].date()} to {end})", "",
           "Stocks under $8 that closed up 30%+ on $2M+ volume, shorted at the next open, 20% stop, covered at "
           "that day's close. 7.5% of the account per trade, at most 3 at once, starting with $100,000. "
           "Delisted stocks are included.", "",
           "| | Trades | Winners | Avg trade after costs | Total | Annual (CAGR) | Worst drop | Sharpe | Stopped out |",
           "|---|---|---|---|---|---|---|---|---|"]
    md += [_row(labels[k], st[k]) for k in ("F1", "F1_2x", "F1_free", "F2", "F0", "F1_hold3", "F1_hold5", "F1_nostop",
                                            "F0_nostop")]
    md += [_row(f"F1, jump ≥ +{j:.0%}", nst[j]) for j in NEIGHBOUR_JUMPS]
    md += [f"| SPY buy-and-hold | — | — | — | {spy_total:+.0%} | {spy_cagr:+.2%} | {spy_dd:.1%} | {spy_sharpe:.2f} | — |",
           "", f"Trades skipped because the short-sale rule (SSR) would have been on: {runs['F0'].skipped_ssr:,} at the "
           f"open (plus those flagged on the spike day). Signals skipped because 3 trades were already open: "
           f"{runs['F1'].skipped_slots:,} (F1), {runs['F0'].skipped_slots:,} (F0).", "",
           "**Year by year (account return)**", "",
           "| Year | F1 trades | F1 | F0 | F2 | SPY |", "|---|---|---|---|---|---|"]
    for y in sorted(st["F1"].yearly):
        md.append(f"| {y} | {st['F1'].yearly_trades.get(y, 0)} | {st['F1'].yearly[y]:+.1%} | "
                  f"{st['F0'].yearly.get(y, float('nan')):+.1%} | {st['F2'].yearly.get(y, float('nan')):+.1%} | "
                  f"{spy_yearly.get(y, float('nan')):+.1%} |")
    md += ["", f"![Account values](img/{chart})", "",
           "## The shortable estimate (F1)", "",
           "Share of today's active $0.50–$8 stocks that Alpaca lists as easy to borrow, by price and 20-day median "
           f"dollar volume. Cells marked ✅ (≥ {ETB_SHARE:.0%}, ≥ {MIN_CELL} stocks) count as shortable in the backtest.", "",
           "| Price \\ $ volume/day | " + " | ".join(str(b) for b in grid.index.levels[1]) + " |",
           "|---|" + "---|" * len(grid.index.levels[1])]
    for p in grid.index.levels[0]:
        cells = []
        for dband in grid.index.levels[1]:
            g = grid.loc[(p, dband)]
            cells.append(f"{g['etb_share']:.0%} of {int(g['stocks'])}{' ✅' if g['shortable'] else ''}"
                         if g["stocks"] else "—")
        md.append(f"| {p} | " + " | ".join(cells) + " |")
    md += ["", f"## Live check: last {LIVE_DAYS} trading days ({live_days[0].date()} to {live_days[-1].date()})", "",
           f"{len(live_syms)} stocks signalled; **{live_etb}** are easy to borrow on Alpaca today.", ""]
    md += [f"- {d} {s}: {'easy to borrow' if etb_today.get(s) else 'not shortable on Alpaca'}" for d, s in live_syms]
    best = sorted(runs["F1"].trades, key=lambda t: -t.net)
    md += ["", "## F1's 10 best and 10 worst trades", "", "| Signal day | Symbol | Entry | Exit | Stopped | After costs |",
           "|---|---|---|---|---|---|"]
    md += [f"| {t.signal_day.date()} | {t.symbol} | {t.entry:.2f} | {t.exit:.2f} | {'yes' if t.stopped else ''} | "
           f"{t.net:+.1%} |" for t in best[:10] + best[-10:]]
    ext = [t for k in ("F0_nostop",) for t in runs[k].trades if abs(t.gross) > EXTREME]
    if ext:
        md += ["", f"Trades beyond ±{EXTREME:.0%} (F0, no stop; check for bad data): " +
               "; ".join(f"{t.signal_day.date()} {t.symbol} {t.gross:+.0%}" for t in ext)]
    md += ["", f"Every F1 and F0 trade: [{trades_path.name}]({trades_path.name}).", "",
           "## Limits (from the rules, section G)", "",
           "- Past borrow availability is **estimated** from today's flags (F1), not known.",
           "- Daily bars only: the open is assumed fillable and stops fill at the stop price + 1%. Real stops in "
           "halted or gapping stocks can fill much worse.",
           "- Trading halts are not modelled beyond skipping gaps longer than 5 days.",
           "- Day-trading needs a $25k+ margin account (pattern-day-trader rule). Taxes ignored.", ""]
    path = out_dir / f"spike_short_backtest_{today}.md"
    path.write_text("\n".join(md))
    return path, verdict.go
