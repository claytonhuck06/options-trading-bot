"""Buy puts on unexplained overnight gaps, exactly as pre-registered.

Rules: docs/research/gap_puts_preregistration.md.
  * Gap: split-adjusted open_t / close_{t-1} >= +30%, raw open >= $1, 20-day median $ volume >= $1M, from 2024-02-15.
  * Unexplained: no catalyst article (Alpaca/Benzinga news) from 9:30 ET on t-1 to 9:45 ET on t.
  * Put: nearest expiry 7-45 days out, strike nearest the open; buy at the 9:45-10:30 VWAP + 5%, sell at the
    t+5 VWAP - 5% (intrinsic if no trade that day). 1.5% of equity per trade, at most 3 open.
"""

from __future__ import annotations

import json
import math
import re
import sqlite3
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from research.spike_short import ET, _bars, _retry, fetch_assets, last_completed_day  # noqa: F401  (shared helpers)

DATA_START = date(2023, 12, 1)
FIRST_GAP = date(2024, 2, 15)
GAP = 0.30
NEIGHBOUR_GAPS = (0.20, 0.50)
MIN_OPEN = 1.0
MIN_DV20 = 1e6
MIN_HISTORY = 20
MAX_GAP_DAYS = 5
NEWS_FROM = time(9, 30)            # on t-1
NEWS_TO = time(9, 45)              # on t
ENTRY_FROM, ENTRY_TO = time(9, 45), time(10, 30)
MIN_DTE, MAX_DTE = 7, 45
HOLD = 5
ALT_HOLDS = (1, 10)
SPREAD = 0.05
FEE = 0.65
RISK = 0.015
MAX_OPEN = 3
START_EQUITY = 100_000.0
MAX_SYMBOLS = 3
DATA = "https://data.alpaca.markets"

MOVE_WORDS = (r"moving|movers|gainers|losers|trading higher|trading lower|shares are trading|shares up|shares down|"
              r"halted|halt|resume[sd]?|circuit breaker|session high|session low|pre-market|premarket|after-hours|"
              r"mid-day|midday|spikes?|soars?|jumps?|surges?|rall(?:y|ies)|climbs?|rises?|falls?|tumbles?|plunges?|"
              r"sinks?|unusual options|volume")
CATALYST_WORDS = (r"announce[sd]?|earnings|results|revenue|guidance|quarter|fda|approval|approves|approved|clearance|"
                  r"trial|data|study|patent|contract|award|order|agreement|partnership|partner|collaboration|"
                  r"acquire|acquisition|merger|merge|buyout|takeover|offer|deal|license|launch(?:es)?|upgrade|"
                  r"downgrade|price target|initiates|raises|buyback|repurchase|dividend|split|listing|compliance|"
                  r"nasdaq notice|bankruptcy|offering|financing|private placement|investment|stake|13d|13g|crypto|"
                  r"bitcoin|treasury|ai|artificial intelligence")
_MOVE = re.compile(rf"\b(?:{MOVE_WORDS})\b", re.IGNORECASE)
_CATALYST = re.compile(rf"\b(?:{CATALYST_WORDS})\b", re.IGNORECASE)


def is_catalyst(article: dict, symbol: str) -> bool:
    syms = article.get("symbols") or []
    if symbol not in syms or len(syms) > MAX_SYMBOLS:
        return False
    head = article.get("headline") or ""
    text = head + " " + (article.get("summary") or "")
    return not _MOVE.search(head) or bool(_CATALYST.search(text))


# --- Storage -------------------------------------------------------------------------------------

SCHEMA = """
CREATE TABLE IF NOT EXISTS adj (symbol TEXT NOT NULL, day TEXT NOT NULL, open REAL, high REAL, low REAL,
                                close REAL, volume REAL, raw_close REAL, PRIMARY KEY (symbol, day));
CREATE TABLE IF NOT EXISTS spy (day TEXT PRIMARY KEY, close REAL);
CREATE TABLE IF NOT EXISTS assets (symbol TEXT PRIMARY KEY, name TEXT, exchange TEXT, status TEXT,
                                   easy_to_borrow INTEGER);
CREATE TABLE IF NOT EXISTS done (symbol TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS news (symbol TEXT, day TEXT, articles TEXT, PRIMARY KEY (symbol, day));
CREATE TABLE IF NOT EXISTS contracts (symbol TEXT, day TEXT, contracts TEXT, PRIMARY KEY (symbol, day));
CREATE TABLE IF NOT EXISTS opt_bars (contract TEXT, kind TEXT, day TEXT, bars TEXT, PRIMARY KEY (contract, kind, day));
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


def open_db(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.executescript(SCHEMA)
    return conn


def _headers(creds) -> dict:
    return {"APCA-API-KEY-ID": creds.key_id, "APCA-API-SECRET-KEY": creds.secret_key}


def _get(creds, url: str, params: dict) -> dict:
    import requests

    def call():
        r = requests.get(url, params=params, headers=_headers(creds), timeout=60)
        if r.status_code == 429:
            raise RuntimeError("rate limited")
        r.raise_for_status()
        return r.json()
    return _retry(call, tries=8, wait=15.0)


def download_bars(creds, conn: sqlite3.Connection, end: date, batch: int = 100) -> None:
    """Every stock's raw + split-adjusted daily bars from DATA_START, and SPY. Resumes after an interruption."""
    from alpaca.data.enums import Adjustment
    from alpaca.data.historical import StockHistoricalDataClient
    client = StockHistoricalDataClient(creds.key_id, creds.secret_key)
    if not conn.execute("SELECT 1 FROM assets LIMIT 1").fetchone():
        conn.executemany("INSERT INTO assets VALUES (:symbol,:name,:exchange,:status,:easy_to_borrow)",
                         _retry(lambda: fetch_assets(creds)))
        conn.commit()
    syms = [r[0] for r in conn.execute("SELECT symbol FROM assets WHERE symbol NOT IN (SELECT symbol FROM done) "
                                       "ORDER BY symbol")]
    print(f"daily bars: {len(syms):,} symbols to go", flush=True)
    for i in range(0, len(syms), batch):
        chunk = syms[i:i + batch]
        raw = _retry(lambda: {(s, d): b.close for s, d, b in _bars(client, chunk, DATA_START, end, Adjustment.RAW)})
        rows = _retry(lambda: [(s, d, b.open, b.high, b.low, b.close, b.volume, raw.get((s, d)))
                               for s, d, b in _bars(client, chunk, DATA_START, end, Adjustment.SPLIT)])
        conn.executemany("INSERT OR REPLACE INTO adj VALUES (?,?,?,?,?,?,?,?)", rows)
        conn.executemany("INSERT OR REPLACE INTO done VALUES (?)", [(s,) for s in chunk])
        conn.commit()
        print(f"  {min(i + batch, len(syms)):,}/{len(syms):,}", flush=True)
    conn.execute("DELETE FROM spy")
    conn.executemany("INSERT INTO spy VALUES (?,?)",
                     _retry(lambda: [(d, b.close) for _, d, b in _bars(client, ["SPY"], DATA_START, end, Adjustment.ALL)]))
    conn.execute("INSERT OR REPLACE INTO meta VALUES ('end', ?)", (end.isoformat(),))
    conn.commit()


# --- The screen ----------------------------------------------------------------------------------

def load_bars(conn: sqlite3.Connection) -> pd.DataFrame:
    df = pd.read_sql("SELECT * FROM adj WHERE raw_close IS NOT NULL", conn)
    df["day"] = pd.to_datetime(df["day"])
    return df.sort_values(["symbol", "day"], ignore_index=True)


def add_features(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    g = df.groupby("symbol", sort=False)
    df["dollar_vol"] = df["close"] * df["volume"]
    df["prev_close"] = g["close"].shift(1)
    df["prev_day"] = g["day"].shift(1)
    df["n_before"] = g.cumcount()
    df["dv20_before"] = g["dollar_vol"].transform(lambda s: s.shift(1).rolling(20, min_periods=20).median())
    df["gap"] = df["open"] / df["prev_close"] - 1
    df["raw_open"] = df["open"] * df["raw_close"] / df["close"]
    for k in sorted({HOLD, *ALT_HOLDS}):
        df[f"close_{k}"] = g["close"].shift(-k)
        df[f"day_{k}"] = g["day"].shift(-k)
    return df


def gaps(df: pd.DataFrame, gap: float = GAP) -> pd.DataFrame:
    ok = ((df["gap"] >= gap) & (df["raw_open"] >= MIN_OPEN) & (df["dv20_before"] >= MIN_DV20)
          & (df["n_before"] >= MIN_HISTORY) & ((df["day"] - df["prev_day"]).dt.days <= MAX_GAP_DAYS)
          & (df["day"] >= pd.Timestamp(FIRST_GAP)))
    return df[ok].copy()


def _et(d: date, t: time) -> datetime:
    return datetime.combine(d, t, tzinfo=ET)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- News ----------------------------------------------------------------------------------------

def fetch_news(creds, conn: sqlite3.Connection, symbol: str, day: date, prev_day: date) -> list[dict]:
    key = (symbol, day.isoformat())
    hit = conn.execute("SELECT articles FROM news WHERE symbol=? AND day=?", key).fetchone()
    if hit:
        return json.loads(hit[0])
    out, token = [], None
    while True:
        params = {"symbols": symbol, "start": _iso(_et(prev_day, NEWS_FROM)), "end": _iso(_et(day, NEWS_TO)),
                  "limit": 50, "sort": "asc"}
        if token:
            params["page_token"] = token
        j = _get(creds, f"{DATA}/v1beta1/news", params)
        out += [{"headline": a.get("headline"), "summary": a.get("summary"), "symbols": a.get("symbols"),
                 "created_at": a.get("created_at")} for a in j.get("news", [])]
        token = j.get("next_page_token")
        if not token:
            break
    conn.execute("INSERT OR REPLACE INTO news VALUES (?,?,?)", (*key, json.dumps(out)))
    conn.commit()
    return out


def in_window(article: dict, day: date, prev_day: date) -> bool:
    t = datetime.fromisoformat(article["created_at"].replace("Z", "+00:00"))
    return _et(prev_day, NEWS_FROM) <= t <= _et(day, NEWS_TO)


# --- Options -------------------------------------------------------------------------------------

def fetch_contracts(creds, conn: sqlite3.Connection, symbol: str, day: date) -> list[dict]:
    key = (symbol, day.isoformat())
    hit = conn.execute("SELECT contracts FROM contracts WHERE symbol=? AND day=?", key).fetchone()
    if hit:
        return json.loads(hit[0])
    out = []
    for status in ("inactive", "active"):
        token = None
        while True:
            params = {"underlying_symbols": symbol, "status": status, "type": "put", "limit": 10000,
                      "expiration_date_gte": (day + timedelta(days=MIN_DTE)).isoformat(),
                      "expiration_date_lte": (day + timedelta(days=MAX_DTE)).isoformat()}
            if token:
                params["page_token"] = token
            j = _get(creds, f"{creds.base_url}/v2/options/contracts", params)
            out += [{"symbol": c["symbol"], "expiry": c["expiration_date"], "strike": float(c["strike_price"])}
                    for c in j.get("option_contracts") or []]
            token = j.get("next_page_token")
            if not token:
                break
    conn.execute("INSERT OR REPLACE INTO contracts VALUES (?,?,?)", (*key, json.dumps(out)))
    conn.commit()
    return out


def choose_contract(contracts: list[dict], open_price: float) -> dict | None:
    """Nearest expiry 7-45 days out, then the strike closest to the open (ties: lower strike)."""
    if not contracts:
        return None
    expiry = min(c["expiry"] for c in contracts)
    same = [c for c in contracts if c["expiry"] == expiry]
    return min(same, key=lambda c: (abs(c["strike"] - open_price), c["strike"]))


def fetch_opt_bars(creds, conn: sqlite3.Connection, contract: str, kind: str, start: datetime, end: datetime) -> list[dict]:
    key = (contract, kind, start.date().isoformat())
    hit = conn.execute("SELECT bars FROM opt_bars WHERE contract=? AND kind=? AND day=?", key).fetchone()
    if hit:
        return json.loads(hit[0])
    out, token = [], None
    while True:
        params = {"symbols": contract, "timeframe": "1Min" if kind == "min" else "1Day",
                  "start": _iso(start), "end": _iso(end), "limit": 10000}
        if token:
            params["page_token"] = token
        j = _get(creds, f"{DATA}/v1beta1/options/bars", params)
        out += (j.get("bars") or {}).get(contract, [])
        token = j.get("next_page_token")
        if not token:
            break
    conn.execute("INSERT OR REPLACE INTO opt_bars VALUES (?,?,?,?)", (*key, json.dumps(out)))
    conn.commit()
    return out


def vwap(bars: list[dict]) -> float | None:
    v = sum(b["v"] for b in bars)
    return sum(b["vw"] * b["v"] for b in bars) / v if v else None


def opt_day(contract_symbol: str) -> date:
    m = re.search(r"(\d{6})[CP]\d{8}$", contract_symbol)
    return datetime.strptime(m.group(1), "%y%m%d").date()


# --- Building trades -----------------------------------------------------------------------------

@dataclass
class Candidate:
    symbol: str
    day: pd.Timestamp
    gap: float
    open: float                 # split-adjusted
    raw_open: float
    explained: bool
    catalysts: list[str]
    articles: int
    stock_ret: dict[int, float]                 # short-side context: 1 - close_{t+k}/open_t
    contract: str | None = None
    strike_raw: float | None = None
    entry: float | None = None                  # option price paid (before the 5% spread)
    exits: dict[int, tuple[pd.Timestamp, float, bool]] = field(default_factory=dict)  # hold -> (day, price, pay spread?)
    reason: str = ""


def collect(creds, conn: sqlite3.Connection, df: pd.DataFrame, gp: pd.DataFrame, with_options: bool = True,
            log_every: int = 100) -> list[Candidate]:
    """News for every gap, and (with_options) the put, its entry VWAP and exit prices. Everything is cached."""
    by_sym = {s: g.set_index("day") for s, g in df.groupby("symbol")}
    out: list[Candidate] = []
    for n, r in enumerate(gp.itertuples(), 1):
        d, pd_ = r.day.date(), r.prev_day.date()
        arts = [a for a in fetch_news(creds, conn, r.symbol, d, pd_) if in_window(a, d, pd_)]
        cats = [a["headline"] for a in arts if is_catalyst(a, r.symbol)]
        c = Candidate(r.symbol, r.day, r.gap, r.open, r.raw_open, bool(cats), cats, len(arts),
                      {k: 1 - getattr(r, f"close_{k}") / r.open for k in (HOLD, *ALT_HOLDS)})
        out.append(c)
        if n % log_every == 0:
            print(f"  {n:,}/{len(gp):,} gaps", flush=True)
        if not with_options:
            continue
        contract = choose_contract(fetch_contracts(creds, conn, r.symbol, d), r.raw_open)
        if contract is None:
            c.reason = "no listed put"
            continue
        c.contract, c.strike_raw = contract["symbol"], contract["strike"]
        mins = fetch_opt_bars(creds, conn, c.contract, "min", _et(d, ENTRY_FROM), _et(d, ENTRY_TO))
        mins = [b for b in mins if _et(d, ENTRY_FROM) <= datetime.fromisoformat(b["t"].replace("Z", "+00:00"))
                < _et(d, ENTRY_TO)]
        c.entry = vwap(mins)
        if c.entry is None:
            c.reason = "put didn't trade 9:45-10:30"
            continue
        expiry = opt_day(c.contract)
        daily = fetch_opt_bars(creds, conn, c.contract, "day", _et(d, time(0)), _et(expiry, time(23)))
        dmap = {datetime.fromisoformat(b["t"].replace("Z", "+00:00")).astimezone(ET).date(): b for b in daily}
        hist = by_sym[r.symbol]
        factor = r.raw_open / r.open                      # adjusted -> raw on the gap day
        for k in (HOLD, *ALT_HOLDS):
            xd = getattr(r, f"day_{k}")
            if xd != xd or xd.date() > expiry:
                # expiry first (or data ends): intrinsic at the last close on or before expiry
                upto = hist.loc[(hist.index > r.day) & (hist.index.date <= expiry)]
                if upto.empty:
                    continue
                last = upto.iloc[-1]
                c.exits[k] = (upto.index[-1], max(c.strike_raw - last["close"] * factor, 0.0), False)
                continue
            b = dmap.get(xd.date())
            if b and b.get("v"):
                c.exits[k] = (xd, b["vw"], True)
            else:
                c.exits[k] = (xd, max(c.strike_raw - getattr(r, f"close_{k}") * factor, 0.0), True)
    return out


# --- Simulation ----------------------------------------------------------------------------------

@dataclass
class Trade:
    symbol: str
    day: pd.Timestamp
    exit_day: pd.Timestamp
    contract: str
    gap: float
    explained: bool
    contracts: int
    paid: float
    received: float
    ret: float                 # on premium paid, after costs
    pnl: float


def trade_value(c: Candidate, hold: int, spread: float) -> tuple[float, float, pd.Timestamp]:
    """(price paid, price received, exit day) per share; no spread at expiry (settles at intrinsic)."""
    xd, px, charge = c.exits[hold]
    return c.entry * (1 + spread), max(px * (1 - spread) if charge else px, 0.0), xd


def simulate(cands: list[Candidate], calendar: pd.DatetimeIndex, explained: bool = False, hold: int = HOLD,
             spread: float = SPREAD) -> tuple[pd.Series, list[Trade], list[str]]:
    pool = sorted([c for c in cands if c.explained == explained and c.entry is not None and hold in c.exits],
                  key=lambda c: (c.day, -c.gap))
    by_day: dict[pd.Timestamp, list[Candidate]] = {}
    for c in pool:
        by_day.setdefault(c.day, []).append(c)
    equity, open_pos, trades, values, problems = START_EQUITY, [], [], [], []
    for day in calendar:
        for c in by_day.get(day, []):
            if len(open_pos) >= MAX_OPEN:
                continue
            buy, sell, xd = trade_value(c, hold, spread)
            if not xd > day:
                problems.append(f"{c.symbol} {day.date()}: exit not after entry")
                continue
            per = buy * 100 + FEE
            n = int(RISK * equity // per)
            if n < 1:
                continue
            paid = n * per
            received = n * (sell * 100 - FEE) if sell > 0 else 0.0
            open_pos.append(Trade(c.symbol, day, xd, c.contract, c.gap, c.explained, n, paid, received,
                                  received / paid - 1, received - paid))
        still = []
        for t in open_pos:
            if t.exit_day <= day:
                equity += t.pnl
                trades.append(t)
            else:
                still.append(t)
        open_pos = still
        values.append(equity)
    return pd.Series(values, index=calendar), trades, problems


@dataclass
class Stats:
    trades: int
    win: float
    avg: float
    median: float
    t_stat: float
    total: float
    cagr: float
    max_dd: float
    sharpe: float


def stats(eq: pd.Series, trades: list[Trade]) -> Stats:
    r = np.array([t.ret for t in trades])
    rets = eq.pct_change().fillna(0)
    years = (eq.index[-1] - eq.index[0]).days / 365.25
    total = eq.iloc[-1] / START_EQUITY - 1
    sd = rets.std()
    dd = float((1 - eq / np.maximum(eq.cummax(), START_EQUITY)).max())
    t_stat = float(r.mean() / (r.std(ddof=1) / math.sqrt(len(r)))) if len(r) > 1 and r.std() > 0 else 0.0
    return Stats(len(r), float((r > 0).mean()) if len(r) else 0.0, float(r.mean()) if len(r) else 0.0,
                 float(np.median(r)) if len(r) else 0.0, t_stat, float(total),
                 float((1 + total) ** (1 / years) - 1) if years > 0 else 0.0, dd,
                 float(rets.mean() / sd * math.sqrt(252)) if sd > 0 else 0.0)


# --- Verdict and report --------------------------------------------------------------------------

def evaluate(main: Stats, double: Stats, alt: dict[int, Stats], news: Stats, spy_sharpe: float,
             problems: list[str]) -> list[tuple[str, bool, str]]:
    return [
        ("Data check", not problems, "no problems" if not problems else "; ".join(problems[:5])),
        ("Enough trades (≥ 50)", main.trades >= 50, f"{main.trades} trades"),
        ("Makes money after costs", main.total > 0 and main.avg > 0,
         f"total {main.total:+.1%}, average trade {main.avg:+.1%} of premium"),
        ("Not luck (t-stat ≥ 2)", main.t_stat >= 2, f"t = {main.t_stat:.2f}"),
        ("Worth the risk (Sharpe ≥ SPY)", main.sharpe >= spy_sharpe, f"{main.sharpe:.2f} vs {spy_sharpe:.2f}"),
        ("Survivable (worst drop ≤ 25%)", main.max_dd <= 0.25, f"{main.max_dd:.1%}"),
        ("Survives double spreads", double.total > 0, f"total {double.total:+.1%}"),
        ("Not fragile (t+1 and t+10 exits average > 0)", all(s.avg > 0 for s in alt.values()),
         "; ".join(f"t+{k}: avg {s.avg:+.1%} ({s.trades} trades)" for k, s in sorted(alt.items()))),
        ("News filter matters (unexplained beats with-news)", main.avg > news.avg,
         f"{main.avg:+.1%} vs {news.avg:+.1%}"),
    ]


def classifier_sample(cands: list[Candidate], conn: sqlite3.Connection, n: int = 40, seed: int = 1) -> str:
    rng = np.random.default_rng(seed)
    pick = rng.choice(len(cands), size=min(n, len(cands)), replace=False)
    lines = []
    for i in sorted(pick):
        c = cands[i]
        prev = conn.execute("SELECT articles FROM news WHERE symbol=? AND day=?",
                            (c.symbol, c.day.date().isoformat())).fetchone()
        arts = json.loads(prev[0]) if prev else []
        lines.append(f"### {c.day.date()} {c.symbol} gap {c.gap:+.0%} → {'EXPLAINED' if c.explained else 'unexplained'}")
        for a in arts:
            tag = "CATALYST" if is_catalyst(a, c.symbol) else "ignored"
            lines.append(f"- [{tag}] ({len(a.get('symbols') or [])} syms, {a['created_at'][:16]}) {a['headline']}")
        if not arts:
            lines.append("- (no articles)")
        lines.append("")
    return "\n".join(lines)


def _row(label: str, s: Stats) -> str:
    return (f"| {label} | {s.trades} | {s.win:.0%} | {s.avg:+.1%} | {s.median:+.1%} | {s.t_stat:.2f} | {s.total:+.1%} "
            f"| {s.cagr:+.1%} | {s.max_dd:.1%} | {s.sharpe:.2f} |")


def run(creds, conn: sqlite3.Connection, out_dir: Path, today: date) -> tuple[Path, bool]:
    df = add_features(load_bars(conn))
    spy = pd.read_sql("SELECT day, close FROM spy", conn, parse_dates=["day"]).set_index("day")["close"].sort_index()
    gp = gaps(df, min(NEIGHBOUR_GAPS))
    print(f"{len(gp):,} gaps of +{min(NEIGHBOUR_GAPS):.0%} or more; fetching news and puts (cached)...", flush=True)
    allc = collect(creds, conn, df, gp)
    cal = spy.index[spy.index >= pd.Timestamp(FIRST_GAP)]
    spy_eq = spy.loc[cal] / spy.loc[cal].iloc[0] * START_EQUITY
    spy_s = stats(spy_eq, [])

    def sub(g):
        return [c for c in allc if c.gap >= g]

    main_c = sub(GAP)
    res = {}
    for name, cands, kw in (("main", main_c, {}), ("double", main_c, {"spread": 2 * SPREAD}),
                            ("news", main_c, {"explained": True}),
                            ("t1", main_c, {"hold": 1}), ("t10", main_c, {"hold": 10}),
                            ("g20", sub(0.20), {}), ("g50", sub(0.50), {}), ("free", main_c, {"spread": 0.0})):
        eq, tr, pr = simulate(cands, cal, **kw)
        res[name] = (eq, tr, pr, stats(eq, tr))
    st = {k: v[3] for k, v in res.items()}
    bars = evaluate(st["main"], st["double"], {1: st["t1"], 10: st["t10"]}, st["news"], spy_s.sharpe, res["main"][2])
    go = all(ok for _, ok, _ in bars)

    une = [c for c in main_c if not c.explained]
    exp = [c for c in main_c if c.explained]

    def funnel(cs):
        return (len(cs), sum(c.reason == "no listed put" for c in cs), sum(c.reason.startswith("put didn't") for c in cs),
                sum(c.entry is not None for c in cs))

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(10, 5))
    ax.plot(res["main"][0], label="Puts on unexplained gaps (primary)", color="#2563eb")
    ax.plot(res["news"][0], label="Puts on gaps with news", color="#9ca3af")
    ax.plot(spy_eq, label="SPY buy-and-hold", color="#111827")
    ax.set_ylabel("Account value ($)")
    ax.set_title("Buying puts after a 30%+ overnight gap")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8)
    fig.tight_layout()
    (out_dir / "img").mkdir(parents=True, exist_ok=True)
    fig.savefig(out_dir / "img" / "gap_puts_equity.png", dpi=120)
    plt.close(fig)

    rows = [{"symbol": c.symbol, "day": c.day.date(), "gap": c.gap, "explained": c.explained, "articles": c.articles,
             "catalysts": " | ".join(c.catalysts)[:300], "contract": c.contract, "entry_vwap": c.entry,
             "reason": c.reason, **{f"stock_short_t{k}": v for k, v in c.stock_ret.items()},
             **{f"exit_t{k}": c.exits[k][1] for k in c.exits}} for c in main_c]
    csv = out_dir / "gap_puts_candidates.csv"
    pd.DataFrame(rows).to_csv(csv, index=False, float_format="%.6g")

    md = [f"# Buy Puts on Unexplained Overnight Gaps — {today}", "",
          "*Paper research only; not investment advice. Rules: [gap_puts_preregistration.md](gap_puts_preregistration.md), "
          "committed before any data was fetched.*", "",
          f"## Verdict: **{'GO' if go else 'NO-GO'}** ({sum(ok for _, ok, _ in bars)} of {len(bars)} bars passed)", "",
          "| Bar | Result | Detail |", "|---|---|---|"]
    md += [f"| {i} · {n} | {'PASS' if ok else '**FAIL**'} | {d} |" for i, (n, ok, d) in enumerate(bars, 1)]
    md += ["", f"## The funnel ({FIRST_GAP} to {cal[-1].date()}, gaps of +30% or more)", "",
           "| | Gaps | No listed put | Put didn't trade 9:45–10:30 | Tradeable |", "|---|---|---|---|---|"]
    for label, cs in (("Unexplained (no news)", une), ("With news", exp)):
        a, b, c_, d = funnel(cs)
        md.append(f"| {label} | {a} | {b} | {c_} | {d} |")
    md += ["", "## Results (returns are % of the premium paid, after costs)", "",
           "| | Trades | Winners | Average | Median | t-stat | Total | Annual | Worst drop | Sharpe |",
           "|---|---|---|---|---|---|---|---|---|---|"]
    labels = {"main": "**Unexplained gaps, hold to t+5 (primary)**", "double": "Primary, double spreads",
              "free": "Primary, no spreads (reference)", "news": "Gaps with news", "t1": "Unexplained, exit t+1",
              "t10": "Unexplained, exit t+10", "g20": "Unexplained, gap ≥ +20%", "g50": "Unexplained, gap ≥ +50%"}
    md += [_row(labels[k], st[k]) for k in labels]
    md += [f"| SPY buy-and-hold | — | — | — | — | — | {spy_s.total:+.1%} | {spy_s.cagr:+.1%} | {spy_s.max_dd:.1%} | "
           f"{spy_s.sharpe:.2f} |", "",
           "**Context — what the stock itself did** (a short from the gap-day open, no costs, no borrow limits; "
           "positive = the stock fell):", "",
           "| | Gaps | Avg to t+1 close | Median | Avg to t+5 close | Median | Avg to t+10 close | Median |",
           "|---|---|---|---|---|---|---|---|"]
    for label, cs in (("Unexplained", une), ("With news", exp)):
        vals = []
        for k in (1, 5, 10):
            v = np.array([c.stock_ret[k] for c in cs if c.stock_ret[k] == c.stock_ret[k]])
            vals += [f"{v.mean():+.1%}" if len(v) else "—", f"{np.median(v):+.1%}" if len(v) else "—"]
        md.append(f"| {label} | {len(cs)} | " + " | ".join(vals) + " |")
    md += ["", "![Account values](img/gap_puts_equity.png)", ""]
    tr = sorted(res["main"][1], key=lambda t: -t.ret)
    if tr:
        md += ["## Primary: 10 best and 10 worst trades", "", "| Day | Symbol | Gap | Put | Contracts | Paid | Back | Return |",
               "|---|---|---|---|---|---|---|---|"]
        md += [f"| {t.day.date()} | {t.symbol} | {t.gap:+.0%} | {t.contract} | {t.contracts} | ${t.paid:,.0f} | "
               f"${t.received:,.0f} | {t.ret:+.0%} |" for t in (tr[:10] + tr[-10:] if len(tr) > 20 else tr)]
    md += ["", f"Every gap with its news, put and prices: [{csv.name}]({csv.name}). Classifier sample: "
           "[gap_puts_classifier_sample.md](gap_puts_classifier_sample.md).", "",
           "## Limits (from the rules, section G)", "",
           "- About 2.7 years of option history only.",
           "- Bid/ask spreads are modelled (5% each side), not measured.",
           "- The news filter sees only Benzinga's coverage and uses a keyword rule.",
           "- Fills assume $1,500 of puts could be bought near the 9:45–10:30 average price.", ""]
    path = out_dir / f"gap_puts_backtest_{today}.md"
    path.write_text("\n".join(md))
    return path, go
