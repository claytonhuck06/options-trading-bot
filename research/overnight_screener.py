"""Overnight drift screener and its walk-forward test, exactly as pre-registered.

Rules: docs/research/overnight_screener_preregistration.md.
  * Overnight = open_t / close_{t-1} - 1; day = close_t / open_t - 1 (split- and dividend-adjusted daily bars).
  * Screener: rank the last 756 trading days by the t-stat of (overnight - tier cost), with filters.
  * Walk-forward: each year 2019..2026, pick the top 20 using only the 3 prior calendar years, hold them
    overnight only (buy at the close, sell at the next open), equal weight, tier costs.
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

ET = ZoneInfo("America/New_York")
DATA_START = date(2016, 1, 1)
WINDOW = 756                       # screener: 3 years of trading days
BLOCKS = 3                         # "years" in a window, for the overnight-beat-day count
MIN_COVERAGE = 0.90
MIN_PRICE = 5.0
MIN_DOLLAR_VOL = 20e6
LIQ_DAYS = 60
TRIM = 0.01
TOP_N = 20
NEIGHBOUR_NS = (10, 40)
TEST_YEARS = tuple(range(2019, 2027))
MIN_POSITIVE_YEARS = 5
SPY_COST = 0.0002
EXTREME = 0.50
PREFILTER_DOLLAR_VOL = 5e6         # download step only: skip full history for stocks trading < $5M/day now
EXCHANGES = {"NYSE", "NASDAQ", "ARCA", "BATS", "AMEX"}
COST_TIERS = ((1e9, 0.0002), (200e6, 0.0005), (50e6, 0.0010), (20e6, 0.0020))

_LEVERAGED = re.compile(
    r"(-?\d(\.\d+)?x\b)|\bultra|\bbull\b|\bbear\b|\binverse\b|\bleveraged\b|\bproshares short\b|"
    r"\bdaily\b.*\b(long|short)\b|\bshort (s&p|qqq|dow|russell|qqq)", re.IGNORECASE)


def is_leveraged(name: str) -> bool:
    return bool(_LEVERAGED.search(name or ""))


def cost_for(dollar_vol: float) -> float | None:
    """Round-trip cost for a liquidity level; None = not eligible (too thin)."""
    for floor, cost in COST_TIERS:
        if dollar_vol >= floor:
            return cost
    return None


# --- Data ----------------------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS bars (
    symbol TEXT NOT NULL, day TEXT NOT NULL,
    open REAL, close REAL, volume REAL,
    PRIMARY KEY (symbol, day)
);
CREATE TABLE IF NOT EXISTS assets (symbol TEXT PRIMARY KEY, name TEXT, exchange TEXT);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


def open_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.executescript(SCHEMA)
    return conn


def last_completed_day(now: datetime | None = None) -> date:
    """Today's bar is used only once the session is over (after 16:30 ET)."""
    now = (now or datetime.now(timezone.utc)).astimezone(ET)
    return now.date() if now.hour * 60 + now.minute >= 16 * 60 + 30 else now.date() - timedelta(days=1)


def fetch_assets(creds) -> list[dict]:
    """Read-only GET of the asset list (no trading client: only two files in the project may create one)."""
    import requests
    resp = requests.get(f"{creds.base_url}/v2/assets", params={"status": "active", "asset_class": "us_equity"},
                        headers={"APCA-API-KEY-ID": creds.key_id, "APCA-API-SECRET-KEY": creds.secret_key}, timeout=60)
    resp.raise_for_status()
    return [{"symbol": a["symbol"], "name": a.get("name") or "", "exchange": a["exchange"]}
            for a in resp.json() if a.get("tradable") and a["exchange"] in EXCHANGES]


def _download(client, conn: sqlite3.Connection, symbols: list[str], start: date, end: date, batch: int) -> int:
    from alpaca.data.enums import Adjustment, DataFeed
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame
    total = 0
    for i in range(0, len(symbols), batch):
        chunk = symbols[i:i + batch]
        req = StockBarsRequest(symbol_or_symbols=chunk, timeframe=TimeFrame.Day, feed=DataFeed.SIP,
                               adjustment=Adjustment.ALL,
                               start=datetime(start.year, start.month, start.day, tzinfo=timezone.utc),
                               end=datetime(end.year, end.month, end.day, 23, tzinfo=timezone.utc))
        data = client.get_stock_bars(req).data
        rows = [(s, b.timestamp.astimezone(ET).date().isoformat(), b.open, b.close, b.volume)
                for s, bars in data.items() for b in bars
                if b.timestamp.astimezone(ET).date() <= end]
        conn.executemany("INSERT OR REPLACE INTO bars VALUES (?,?,?,?,?)", rows)
        conn.commit()
        total += len(rows)
        print(f"  bars {i + len(chunk):,}/{len(symbols):,} symbols ({total:,} rows)", flush=True)
    return total


def download(creds, conn: sqlite3.Connection, end: date) -> None:
    """Assets, then a 90-day liquidity pre-scan, then full history (2016+) for stocks trading ≥ $5M/day now.

    Adjusted prices change when a new split or dividend happens, so a refresh re-downloads everything.
    """
    from alpaca.data.historical import StockHistoricalDataClient
    client = StockHistoricalDataClient(creds.key_id, creds.secret_key)
    assets = fetch_assets(creds)
    conn.execute("DELETE FROM assets")
    conn.execute("DELETE FROM bars")
    conn.executemany("INSERT INTO assets VALUES (?,?,?)", [(a["symbol"], a["name"], a["exchange"]) for a in assets])
    conn.commit()
    print(f"{len(assets):,} tradable US-listed stocks and ETFs; liquidity pre-scan...", flush=True)
    syms = sorted(a["symbol"] for a in assets)
    _download(client, conn, syms, end - timedelta(days=90), end, batch=200)
    liq = dict(conn.execute("""SELECT symbol, AVG(close * volume) FROM bars GROUP BY symbol"""))
    keep = sorted(s for s in syms if (liq.get(s) or 0) >= PREFILTER_DOLLAR_VOL)
    print(f"{len(keep):,} trade ≥ ${PREFILTER_DOLLAR_VOL / 1e6:.0f}M/day; full history from {DATA_START}...", flush=True)
    conn.execute("DELETE FROM bars")
    _download(client, conn, keep, DATA_START, end, batch=50)
    conn.execute("INSERT OR REPLACE INTO meta VALUES ('end', ?)", (end.isoformat(),))
    conn.execute("INSERT OR REPLACE INTO meta VALUES ('fetched_at', ?)", (datetime.now(timezone.utc).isoformat(),))
    conn.commit()


@dataclass
class Panel:
    open: pd.DataFrame            # index: trading days (SPY's calendar), columns: symbols
    close: pd.DataFrame
    volume: pd.DataFrame
    names: dict[str, str]

    @property
    def overnight(self) -> pd.DataFrame:
        return self.open / self.close.shift(1) - 1

    @property
    def day(self) -> pd.DataFrame:
        return self.close / self.open - 1


def load_panel(conn: sqlite3.Connection, end: date) -> Panel:
    df = pd.read_sql("SELECT symbol, day, open, close, volume FROM bars WHERE day <= ?", conn, params=(end.isoformat(),))
    df["day"] = pd.to_datetime(df["day"])
    bad = (df["open"] <= 0) | (df["close"] <= 0)
    df.loc[bad, ["open", "close"]] = np.nan
    cal = pd.DatetimeIndex(sorted(df.loc[df["symbol"] == "SPY", "day"].unique()))
    wide = {f: df.pivot(index="day", columns="symbol", values=f).reindex(cal) for f in ("open", "close", "volume")}
    names = dict(conn.execute("SELECT symbol, name FROM assets"))
    return Panel(wide["open"], wide["close"], wide["volume"], names)


# --- Scoring -------------------------------------------------------------------------------------

def _trimmed(col: pd.Series) -> float:
    v = np.sort(col.dropna().to_numpy())
    k = int(len(v) * TRIM)
    return float(v[k:len(v) - k].mean()) if len(v) > 2 * k else float("nan")


def _max_dd(rets: np.ndarray) -> float:
    eq = np.cumprod(1 + rets)
    return float((1 - eq / np.maximum.accumulate(eq)).max()) if len(eq) else 0.0


def score_window(panel: Panel, days: pd.DatetimeIndex, min_dv: float = MIN_DOLLAR_VOL) -> pd.DataFrame:
    """Every statistic in section D for the given window of trading days, one row per stock."""
    on = panel.overnight.loc[days]
    dy = panel.day.loc[days]
    valid = on.notna() & dy.notna()
    on, dy = on.where(valid), dy.where(valid)
    n = len(days)
    coverage = valid.sum() / n
    last_close = panel.close.loc[:days[-1]].iloc[-5:].ffill().iloc[-1]
    liq = (panel.close * panel.volume).loc[days[-LIQ_DAYS:]].median()
    weekend = pd.Series(days.to_series().diff().dt.days.fillna(1).to_numpy() > 1, index=days)

    out = pd.DataFrame({
        "name": [panel.names.get(s, "") for s in on.columns],
        "price": last_close, "dollar_vol": liq, "coverage": coverage,
        "on_mean": on.mean(), "day_mean": dy.mean(), "on_win": (on > 0).sum() / valid.sum(),
        "on_median": on.median(),
        "on_weeknight": on.loc[~weekend.to_numpy()].mean(), "on_weekend": on.loc[weekend.to_numpy()].mean(),
        "on_last_year": on.iloc[-252:].mean(),
        "growth_on": (1 + on.fillna(0)).prod(), "growth_day": (1 + dy.fillna(0)).prod(),
    }, index=on.columns)
    out["leveraged"] = [is_leveraged(n_) for n_ in out["name"]]
    out["cost"] = [cost_for(v) if v == v else None for v in out["dollar_vol"]]
    out["eligible"] = ((out["coverage"] >= MIN_COVERAGE) & (out["price"] >= MIN_PRICE) & out["cost"].notna()
                       & (out["dollar_vol"] >= min_dv))
    out = out[out["eligible"]].copy()
    on, dy = on[out.index], dy[out.index]

    cost = out["cost"].astype(float)
    net = on - cost                                   # NaN nights stay NaN (not traded)
    cnt = net.notna().sum()
    out["t_stat"] = net.mean() / (net.std() / np.sqrt(cnt))
    out["on_trimmed"] = [_trimmed(on[s]) for s in out.index]
    blocks = np.array_split(np.arange(n), BLOCKS)
    beat = sum(((1 + on.iloc[b].fillna(0)).prod() > (1 + dy.iloc[b].fillna(0)).prod()).astype(int) for b in blocks)
    out["years_beat"] = beat
    netf = net.fillna(0)
    out["after_cost_annual"] = (1 + netf).prod() ** (252 / n) - 1
    out["max_dd"] = [_max_dd(netf[s].to_numpy()) for s in out.index]
    out["break_even"] = out["on_mean"]
    out["ranked"] = (out["on_trimmed"] > 0) & (out["years_beat"] >= 2)
    return out.sort_values(["ranked", "t_stat"], ascending=[False, False])


def pick(scores: pd.DataFrame, n: int) -> list[str]:
    ok = scores[scores["ranked"] & ~scores["leveraged"]]
    return list(ok.sort_values("t_stat", ascending=False).index[:n])


# --- Walk-forward --------------------------------------------------------------------------------

@dataclass
class Stats:
    total: float
    cagr: float
    max_dd: float
    sharpe: float
    win: float
    yearly: dict[int, float]


def stats(rets: pd.Series) -> Stats:
    eq = (1 + rets).cumprod()
    years = (rets.index[-1] - rets.index[0]).days / 365.25 + 1 / 252
    sd = rets.std()
    yearly = {int(y): float((1 + g).prod() - 1) for y, g in rets.groupby(rets.index.year)}
    return Stats(float(eq.iloc[-1] - 1), float(eq.iloc[-1] ** (1 / years) - 1), _max_dd(rets.to_numpy()),
                 float(rets.mean() / sd * math.sqrt(252)) if sd > 0 else 0.0, float((rets > 0).mean()), yearly)


@dataclass
class WalkForward:
    baskets: dict[str, pd.Series]       # name -> nightly returns
    picks: dict[int, dict[int, list[str]]]   # n -> year -> symbols
    problems: list[str]
    extremes: list[tuple[str, str, float]]
    shortfalls: list[str]                    # years where fewer names qualified than the basket size


def _basket(on: pd.DataFrame, syms: list[str], costs: pd.Series, mult: float) -> pd.Series:
    sub = on[syms]
    net = (sub - costs[syms].astype(float) * mult).fillna(0)     # missing bar: slot sits in cash, no cost
    return net.mean(axis=1) if syms else pd.Series(0.0, index=on.index)


def walk_forward(panel: Panel, end: date, years=TEST_YEARS, ns=(TOP_N, *NEIGHBOUR_NS), min_dv: float = MIN_DOLLAR_VOL,
                 shortfall_is_problem: bool = True) -> WalkForward:
    on_all = panel.overnight
    cal = on_all.index
    out: dict[str, list[pd.Series]] = {}
    picks: dict[int, dict[int, list[str]]] = {n: {} for n in ns}
    problems, extremes, shortfalls = [], [], []
    for y in years:
        hold = cal[(cal.year == y) & (cal <= pd.Timestamp(end))]
        if len(hold) == 0:
            continue
        # Rank window: 3 prior calendar years, ending the day BEFORE the last close of y-1 (that close is
        # when the first buy happens, so its own day return isn't known yet).
        rank_days = cal[(cal.year >= y - 3) & (cal.year <= y - 1)][:-1]
        scores = score_window(panel, rank_days, min_dv)
        costs = scores["cost"]
        if rank_days[-1] >= hold[0]:
            problems.append(f"{y}: ranking window reaches into the holding year")
        on = on_all.loc[hold]
        for n in ns:
            syms = pick(scores, n)
            picks[n][y] = syms
            if len(syms) < n:
                (problems if shortfall_is_problem else shortfalls).append(f"{y}: only {len(syms)} stocks qualified for top {n}")
            out.setdefault(f"top{n}", []).append(_basket(on, syms, costs, 1.0))
            if n == ns[0]:
                out.setdefault(f"top{n}_2x", []).append(_basket(on, syms, costs, 2.0))
                out.setdefault(f"top{n}_free", []).append(_basket(on, syms, costs, 0.0))
                for d, s in on[syms].stack().items():
                    if abs(s) > EXTREME:
                        extremes.append((d[0].date().isoformat(), d[1], float(s)))
        everyone = list(scores[~scores["leveraged"]].index)
        out.setdefault("no_selection", []).append(_basket(on, everyone, costs, 1.0))
        out.setdefault("spy_overnight", []).append(on["SPY"].fillna(0) - SPY_COST)
        out.setdefault("spy_hold", []).append((panel.close["SPY"].pct_change()).loc[hold].fillna(0))
    return WalkForward({k: pd.concat(v) for k, v in out.items()}, picks, problems, extremes, shortfalls)


@dataclass
class Verdict:
    bars: list[tuple[str, bool, str]]

    @property
    def go(self) -> bool:
        return all(ok for _, ok, _ in self.bars)


def evaluate(wf: WalkForward) -> Verdict:
    s = {k: stats(v) for k, v in wf.baskets.items()}
    main, spy = s[f"top{TOP_N}"], s["spy_hold"]
    pos_years = sum(1 for r in main.yearly.values() if r > 0)
    bars = [
        ("Data check", not wf.problems,
         "no problems" if not wf.problems else "; ".join(wf.problems)),
        ("Makes money after costs", main.cagr > 0, f"CAGR {main.cagr:.2%}"),
        ("Worth the risk (Sharpe ≥ SPY buy-and-hold)", main.sharpe >= spy.sharpe,
         f"{main.sharpe:.2f} vs {spy.sharpe:.2f}"),
        ("Picking adds value (beats no-selection basket)", main.cagr > s["no_selection"].cagr,
         f"{main.cagr:.2%} vs {s['no_selection'].cagr:.2%}"),
        (f"Consistent (positive in ≥ {MIN_POSITIVE_YEARS} of {len(main.yearly)} years)", pos_years >= MIN_POSITIVE_YEARS,
         f"{pos_years} of {len(main.yearly)}"),
        ("Survives double costs", s[f"top{TOP_N}_2x"].cagr > 0, f"CAGR {s[f'top{TOP_N}_2x'].cagr:.2%}"),
    ]
    nb = []
    for n in NEIGHBOUR_NS:
        t = s[f"top{n}"]
        nb.append((n, t.cagr > 0 and t.sharpe >= spy.sharpe, f"top {n}: CAGR {t.cagr:.2%}, Sharpe {t.sharpe:.2f}"))
    bars.append(("Not fragile (top 10 and top 40 pass bars 2 and 3)", all(ok for _, ok, _ in nb),
                 "; ".join(d for _, _, d in nb)))
    return Verdict(bars)


# --- Report --------------------------------------------------------------------------------------

LABELS = {f"top{TOP_N}": f"Top {TOP_N} overnight basket", f"top{TOP_N}_2x": f"Top {TOP_N}, double costs",
          f"top{TOP_N}_free": f"Top {TOP_N}, no costs (reference)", "top10": "Top 10", "top40": "Top 40",
          "no_selection": "Every eligible stock, overnight", "spy_overnight": "SPY overnight only",
          "spy_hold": "SPY buy-and-hold"}


def _bp(x: float) -> str:
    return f"{x * 1e4:+.1f}" if x == x else "—"


def screener_table(scores: pd.DataFrame, n: int = 50) -> str:
    top = scores[scores["ranked"]].head(n)
    lines = ["| # | Symbol | Name | Price | $ vol/day | Overnight avg (bp) | Day avg (bp) | Nights up | Trimmed (bp) "
             "| t-stat | Yrs on>day | $1 overnight | $1 daytime | Cost (bp) | After-cost / yr | Worst drop | Last 12 mo (bp) |",
             "|" + "---|" * 17]
    for i, (sym, r) in enumerate(top.iterrows(), 1):
        name = (r["name"][:28] + "…") if len(r["name"]) > 29 else r["name"]
        flag = " ⚠️ leveraged" if r["leveraged"] else ""
        lines.append(
            f"| {i} | **{sym}** | {name}{flag} | ${r['price']:,.2f} | ${r['dollar_vol'] / 1e6:,.0f}M | {_bp(r['on_mean'])} "
            f"| {_bp(r['day_mean'])} | {r['on_win']:.0%} | {_bp(r['on_trimmed'])} | {r['t_stat']:.2f} | {int(r['years_beat'])}/3 "
            f"| ${r['growth_on']:.2f} | ${r['growth_day']:.2f} | {r['cost'] * 1e4:.0f} | {r['after_cost_annual']:+.1%} "
            f"| {r['max_dd']:.0%} | {_bp(r['on_last_year'])} |")
    return "\n".join(lines)


def _stats_row(name: str, st: Stats) -> str:
    return f"| {name} | {st.total:+.0%} | {st.cagr:+.2%} | {st.max_dd:.1%} | {st.sharpe:.2f} | {st.win:.0%} |"


def _chart(wf: WalkForward, path: Path) -> None:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(10, 5))
    for key, color in ((f"top{TOP_N}", "#2563eb"), (f"top{TOP_N}_free", "#93c5fd"), ("no_selection", "#9ca3af"),
                       ("spy_overnight", "#f59e0b"), ("spy_hold", "#111827")):
        ax.plot((1 + wf.baskets[key]).cumprod() * 100_000, label=LABELS[key], color=color, lw=1.4)
    ax.set_yscale("log")
    ax.set_ylabel("Account value ($, log scale)")
    ax.set_title("Overnight-only baskets picked in advance, vs SPY (2019 onward)")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, dpi=120)
    plt.close(fig)


LIQUID_DV = 1e9                    # follow-up version: $1B+/day names only
LIQUID_INFO_DV = 500e6             # information-only floor in that version


def run(conn: sqlite3.Connection, out_dir: Path, today: date, liquid: bool = False) -> tuple[Path, bool]:
    """liquid=False: the original screener + test. liquid=True: the $1B+/day follow-up
    (docs/research/overnight_liquid_preregistration.md)."""
    end = date.fromisoformat(conn.execute("SELECT value FROM meta WHERE key='end'").fetchone()[0])
    panel = load_panel(conn, end)
    cal = panel.open.index
    tag = "_liquid" if liquid else ""
    min_dv = LIQUID_DV if liquid else MIN_DOLLAR_VOL

    scores = score_window(panel, cal[-WINDOW:], min_dv)
    csv_path = out_dir / f"overnight_screener{tag}_{end}.csv"
    scores.to_csv(csv_path, index_label="symbol", float_format="%.6g")

    wf = walk_forward(panel, end, min_dv=min_dv, shortfall_is_problem=not liquid)
    verdict = evaluate(wf)
    st = {k: stats(v) for k, v in wf.baskets.items()}
    chart_name = f"overnight_walk_forward{tag}.png"
    _chart(wf, out_dir / "img" / chart_name)
    picks_path = out_dir / f"overnight_walk_forward{tag}_picks.csv"
    with picks_path.open("w") as f:
        f.write("year,basket,rank,symbol\n")
        for n, by_year in wf.picks.items():
            for y, syms in by_year.items():
                for i, s in enumerate(syms, 1):
                    f.write(f"{y},top{n},{i},{s}\n")

    spy = scores.loc["SPY"] if "SPY" in scores.index else None
    n_ranked = int(scores["ranked"].sum())
    years = sorted(st[f"top{TOP_N}"].yearly)
    if liquid:
        md = [f"# Overnight Drift, Big Liquid Stocks Only ($1B+/day) — {today}",
              "",
              "*Paper research only; not investment advice. Rules: "
              "[overnight_liquid_preregistration.md](overnight_liquid_preregistration.md), committed before this ran. "
              "This is a follow-up on a pattern already seen in the first test "
              f"([overnight_screener_{today}.md](overnight_screener_{today}.md)), so a pass is weaker evidence than "
              "a pass on a fresh idea.*",
              ""]
    else:
        md = [f"# Overnight Drift Screener — {today}",
              "",
              "*Paper research only; not investment advice. Rules: "
              "[overnight_screener_preregistration.md](overnight_screener_preregistration.md), committed before any data was fetched.*",
              ""]
    md += [
          f"## Verdict: **{'GO' if verdict.go else 'NO-GO'}** ({sum(ok for _, ok, _ in verdict.bars)} of {len(verdict.bars)} bars passed)",
          "",
          "| Bar | Result | Detail |", "|---|---|---|"]
    md += [f"| {i} · {name} | {'PASS' if ok else '**FAIL**'} | {detail} |" for i, (name, ok, detail) in enumerate(verdict.bars, 1)]
    md += ["",
           f"## Does picking in advance work? (walk-forward, {wf.baskets[f'top{TOP_N}'].index[0].date()} to {end})",
           "",
           "Each January the screener picks its top stocks using only the three years before. The basket then buys "
           "at every close and sells at every next open for that whole year, paying the cost in the rules.",
           "",
           "| | Total return | Annual (CAGR) | Worst drop | Sharpe | Nights up |", "|---|---|---|---|---|---|"]
    md += [_stats_row(LABELS[k], st[k]) for k in (f"top{TOP_N}", f"top{TOP_N}_2x", f"top{TOP_N}_free", "top10", "top40",
                                                   "no_selection", "spy_overnight", "spy_hold")]
    md += ["", "**Year by year**", "",
           "| Year | " + " | ".join(LABELS[k] for k in (f"top{TOP_N}", f"top{TOP_N}_free", "no_selection", "spy_overnight", "spy_hold")) + " |",
           "|---|" + "---|" * 5]
    for y in years:
        md.append(f"| {y} | " + " | ".join(f"{st[k].yearly.get(y, float('nan')):+.1%}" for k in
                                           (f"top{TOP_N}", f"top{TOP_N}_free", "no_selection", "spy_overnight", "spy_hold")) + " |")
    if wf.shortfalls:
        md += ["", "Years with fewer qualifying names than the basket size (allowed in this version): "
               + "; ".join(wf.shortfalls) + "."]
    if liquid:
        info = walk_forward(panel, end, min_dv=LIQUID_INFO_DV, shortfall_is_problem=False)
        ist = {k: stats(v) for k, v in info.baskets.items()}
        md += ["", f"**Information only: the same test with a ${LIQUID_INFO_DV / 1e6:.0f}M floor** (cannot change the verdict)", "",
               "| | Total return | Annual (CAGR) | Worst drop | Sharpe | Nights up |", "|---|---|---|---|---|---|"]
        md += [_stats_row(LABELS[k], ist[k]) for k in (f"top{TOP_N}", f"top{TOP_N}_2x", f"top{TOP_N}_free", "no_selection")]
    md += ["", f"![Walk-forward account values](img/{chart_name})", "",
           "**What the basket held each year** (top 20, in rank order):", ""]
    md += [f"- **{y}:** {', '.join(wf.picks[TOP_N][y])}" for y in sorted(wf.picks[TOP_N])]
    if wf.extremes:
        md += ["", f"Nights beyond ±{EXTREME:.0%} inside the basket ({len(wf.extremes)}):", ""]
        md += [f"- {d} {s}: {r:+.0%}" for d, s, r in wf.extremes]
    md += ["",
           f"## Tonight's screener (last {WINDOW} trading days, {cal[-WINDOW].date()} to {end})",
           "",
           f"{len(scores):,} stocks and ETFs were liquid enough to rank; **{n_ranked:,}** passed the filters "
           "(positive trimmed overnight average, overnight beat daytime in at least 2 of 3 years). Top 50 by t-stat "
           "of the after-cost overnight return. 1 bp = 0.01%. The full list is in "
           f"[{csv_path.name}]({csv_path.name}).",
           ""]
    if spy is not None:
        md += [f"For reference, **SPY**: overnight {_bp(spy['on_mean'])} bp/night, daytime {_bp(spy['day_mean'])} bp/day; "
               f"$1 held only overnight became ${spy['growth_on']:.2f}, only daytime ${spy['growth_day']:.2f}.", ""]
    md += [screener_table(scores), "",
           "**How to read it.** *Overnight avg* is the average close-to-open move. *Trimmed* drops the biggest 1% of "
           "nights each way (mostly earnings gaps), so a high trimmed number means a steady drift, not a few big "
           "jumps. *t-stat* above 2 means the after-cost average is unlikely to be luck. *Cost* is the assumed round "
           "trip; if *Overnight avg* is not comfortably above it, the trade doesn't pay. *Last 12 mo* shows whether "
           "the pattern is fading.",
           "",
           "## Limits (from the rules, section G)", "",
           "- **Survivorship bias:** only stocks listed today are included, which flatters every historical number.",
           "- Earnings nights are not removed; the trimmed average is the stand-in.",
           "- Prices are the daily bar's open and close, taken as the auction prices.",
           "- Taxes are ignored; every trade would be short-term.",
           ""]
    path = out_dir / f"overnight_screener{tag}_{today}.md"
    path.write_text("\n".join(md))
    return path, verdict.go
