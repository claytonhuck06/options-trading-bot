"""Forward shadow log for the checklist short, day-trade-only version. Places NO orders anywhere.

Rules: docs/research/spike_short_v4_preregistration.md section D, day-trade-only (v4's "same day" row):
short at the open, 50% stop on the day's high (+2% slippage), otherwise cover at the close; 2.7% of $100k per
short, at most 3 a day, biggest gaps first; costs = v3 tiers + 1% locate (no nights, so no borrow).

  * morning (~9:35 ET): screen today's checklist candidates (+75% open gap, prior raw close $0.50-$8, dead company,
    no news 9:30 ET t-1 to 9:30 ET t, SSR skip) from Alpaca's free SIP daily bars, and record IBKR's shortable
    list (shares available, fee) at that moment for each.
  * close (after 16:15 ET): record the simulated exit and net P&L from the day's final SIP bar.

Screening note: the free plan blocks live SIP snapshots, but today's daily bar (with the official open) is served.
The universe is pre-filtered on the raw gap; candidates are then re-checked on split-adjusted history, so a
reverse split doesn't count as a gap. A forward split on the day itself could hide a gap (rare; not handled).
"""

from __future__ import annotations

import math
import sqlite3
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pandas as pd

from research import spike_short as ss
from research import spike_short_v2 as v2
from research import spike_short_v3 as v3
from research import spike_short_v4 as v4
from research.spike_short import ET

POSITION = v4.POSITION                                    # 2.7% of equity
EQUITY = 100_000.0
MAX_PER_DAY = 3
MORNING_FROM = time(9, 31)                                # news window closes 9:30 ET
CLOSE_FROM = time(16, 15)
HISTORY_DAYS = 450                                        # calendar days: 253 trading days + the 200-day average
RECENT_DAYS = 7
MIN_OPEN_SYMBOLS = 100                                    # fewer bars dated today = market closed
TAKEN = ("signal", "over_cap")

SCHEMA = """
CREATE TABLE IF NOT EXISTS shadow (
    day TEXT NOT NULL, symbol TEXT NOT NULL, status TEXT NOT NULL, rank INTEGER, gap REAL, prev_raw_close REAL,
    open_am REAL, dv20 REAL, shares_needed INTEGER, ibkr_available INTEGER, ibkr_fee REAL, ibkr_stamp TEXT,
    placeable INTEGER, checked_at TEXT,
    entry REAL, high REAL, close REAL, exit REAL, stopped INTEGER, gross REAL, cost REAL, net REAL, pnl REAL,
    closed_at TEXT, PRIMARY KEY (day, symbol));
CREATE TABLE IF NOT EXISTS runs (day TEXT NOT NULL, kind TEXT NOT NULL, at TEXT, universe INTEGER, gaps INTEGER,
    logged INTEGER, note TEXT, PRIMARY KEY (day, kind));
CREATE TABLE IF NOT EXISTS news (symbol TEXT, day TEXT, articles TEXT, PRIMARY KEY (symbol, day));
"""


def open_db(path: Path | str) -> sqlite3.Connection:
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.executescript(SCHEMA)
    return conn


# --- Alpaca (read-only data) ---------------------------------------------------------------------

def active_symbols(creds) -> list[str]:
    """Active US common stocks on NYSE/NASDAQ/AMEX, same exclusions as the backtest universe."""
    import requests
    r = ss._retry(lambda: requests.get(f"{creds.base_url}/v2/assets",
                                       params={"status": "active", "asset_class": "us_equity"},
                                       headers={"APCA-API-KEY-ID": creds.key_id, "APCA-API-SECRET-KEY": creds.secret_key},
                                       timeout=60))
    r.raise_for_status()
    return sorted(a["symbol"] for a in r.json()
                  if a["exchange"] in ss.EXCHANGES and not ss.excluded(a["symbol"], a.get("name") or ""))


def daily_bars(client, symbols: list[str], start: date, end: date, adjustment: str = "raw",
               batch: int = 500) -> pd.DataFrame:
    """SIP daily bars (symbol, day, open, high, low, close, volume); today's bar comes with the official open."""
    from alpaca.data.enums import Adjustment
    adj = {"raw": Adjustment.RAW, "split": Adjustment.SPLIT}[adjustment]
    rows = []
    for i in range(0, len(symbols), batch):
        part = symbols[i:i + batch]
        rows += ss._retry(lambda: [(s, pd.Timestamp(d), b.open, b.high, b.low, b.close, b.volume)
                                   for s, d, b in ss._bars(client, part, start, end, adj)])
    return pd.DataFrame(rows, columns=["symbol", "day", "open", "high", "low", "close", "volume"])


# --- Screen --------------------------------------------------------------------------------------

def raw_gappers(recent: pd.DataFrame, today: date, gap: float = v2.GAP) -> pd.DataFrame:
    """From recent raw daily bars: symbols whose open today is >= +gap over the last close, prior close $0.50-$8."""
    today_ts = pd.Timestamp(today)
    recent = recent.sort_values(["symbol", "day"])
    now = recent[recent["day"] == today_ts].set_index("symbol")
    prev = recent[recent["day"] < today_ts].groupby("symbol").last()
    j = now[["open"]].join(prev[["close"]].rename(columns={"close": "prev_close"}), how="inner")
    j["gap"] = j["open"] / j["prev_close"] - 1
    ok = (j["gap"] >= gap) & (j["prev_close"] >= ss.MIN_PRICE) & (j["prev_close"] < ss.MAX_PRICE)
    return j[ok].reset_index()


def features_today(adj: pd.DataFrame, raw: pd.DataFrame, today: date) -> pd.DataFrame:
    """The backtest's feature rows for `today`, built from split-adjusted history plus raw closes."""
    df = adj.merge(raw[["symbol", "day", "close"]].rename(columns={"close": "raw_close"}), on=["symbol", "day"])
    df = v3.add_features(df.sort_values(["symbol", "day"], ignore_index=True))
    return df[df["day"] == pd.Timestamp(today)]


def classify(feats: pd.DataFrame, news_fn) -> list[dict]:
    """Checklist rows (gap, price, dead passed) with a status: signal / over_cap / news / ssr, biggest gaps first."""
    out = []
    for r in v2.screen(feats).sort_values("gap", ascending=False).itertuples():
        row = {"symbol": r.symbol, "day": r.day.date(), "prev_day": r.prev_day.date(), "gap": float(r.gap),
               "prev_raw_close": float(r.prev_raw_close), "open_am": float(r.open),
               "dv20": None if r.dv20_before != r.dv20_before else float(r.dv20_before)}
        if news_fn(r.symbol, row["day"], row["prev_day"]):
            row["status"] = "news"
        elif r.ssr_at_open:
            row["status"] = "ssr"
        else:
            row["status"] = "signal"
        out.append(row)
    rank = 0
    for row in out:
        if row["status"] == "signal":
            rank += 1
            row["rank"] = rank
            if rank > MAX_PER_DAY:
                row["status"] = "over_cap"
    return out


def shares_needed(open_price: float) -> int:
    return int(math.ceil(POSITION * EQUITY / open_price))


# --- The trade -----------------------------------------------------------------------------------

def settle(entry: float, high: float, close: float, dv20: float | None, day: date) -> dict:
    """v4's plan with no extra days: stop at entry x 1.5 on the day's high, else the close. Net of costs."""
    e = {"path": [(pd.Timestamp(day), entry, high, close)], "raw_factor": 1.0,
         "dv20": dv20 if dv20 is not None else 0.0}
    marks, stopped, cost = v4.plan(e, extra=0)
    exit_ = marks[-1][1]
    gross = (entry - exit_) / entry
    return {"entry": entry, "high": high, "close": close, "exit": exit_, "stopped": int(stopped), "gross": gross,
            "cost": cost, "net": gross - cost, "pnl": POSITION * EQUITY * (gross - cost)}


# --- Runs ----------------------------------------------------------------------------------------

def morning(conn: sqlite3.Connection, today: date, universe: list[str], recent: pd.DataFrame, history_fn, news_fn,
            ibkr_fn) -> tuple[list[dict], str]:
    """Screen today and record IBKR availability. history_fn(symbols) -> (adj, raw) frames; ibkr_fn() -> (table,
    stamp). Returns (logged rows, note). Does nothing if today's morning run is already recorded."""
    if conn.execute("SELECT 1 FROM runs WHERE day=? AND kind='morning'", (today.isoformat(),)).fetchone():
        return [], "already recorded today"
    n_open = int((recent["day"] == pd.Timestamp(today)).sum())
    if n_open < MIN_OPEN_SYMBOLS:
        return [], f"market closed (only {n_open} stocks have a bar today)"
    gaps = raw_gappers(recent, today)
    rows: list[dict] = []
    stamp = ""
    if len(gaps):
        adj, raw = history_fn(sorted(gaps["symbol"]))
        rows = classify(features_today(adj, raw, today), news_fn)
    if rows:
        table, stamp = ibkr_fn()
        at = datetime.now(ET).isoformat(timespec="seconds")
        for r in rows:
            avail, fee = table.get(r["symbol"], (0, float("nan")))
            r.update(shares_needed=shares_needed(r["open_am"]), ibkr_available=avail,
                     ibkr_fee=None if fee != fee else fee, ibkr_stamp=stamp, checked_at=at)
            r["placeable"] = int(avail >= r["shares_needed"])
            conn.execute("""INSERT OR REPLACE INTO shadow (day, symbol, status, rank, gap, prev_raw_close, open_am, dv20,
                            shares_needed, ibkr_available, ibkr_fee, ibkr_stamp, placeable, checked_at)
                            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                         (today.isoformat(), r["symbol"], r["status"], r.get("rank"), r["gap"], r["prev_raw_close"],
                          r["open_am"], r["dv20"], r["shares_needed"], avail, r["ibkr_fee"], stamp, r["placeable"], at))
    note = f"{len(universe)} stocks, {len(gaps)} raw +75% gaps, {len(rows)} checklist rows"
    conn.execute("INSERT OR REPLACE INTO runs VALUES (?,?,?,?,?,?,?)",
                 (today.isoformat(), "morning", datetime.now(ET).isoformat(timespec="seconds"), len(universe),
                  len(gaps), len(rows), stamp))
    conn.commit()
    return rows, note


def open_trades(conn: sqlite3.Connection, today: date, now: datetime) -> list[tuple[str, str, float | None]]:
    """(day, symbol, dv20) of taken rows still without an exit; today's only once the close is final."""
    rows = conn.execute(f"SELECT day, symbol, dv20 FROM shadow WHERE exit IS NULL AND status IN {TAKEN} "
                        "ORDER BY day, rank").fetchall()
    final_today = now.astimezone(ET).time() >= CLOSE_FROM
    return [r for r in rows if r[0] < today.isoformat() or (r[0] == today.isoformat() and final_today)]


def close(conn: sqlite3.Connection, today: date, now: datetime, bar_fn) -> list[dict]:
    """Settle every open shadow trade. bar_fn(symbol, day) -> (open, high, close) raw SIP daily bar or None."""
    done = []
    for day, sym, dv20 in open_trades(conn, today, now):
        bar = bar_fn(sym, date.fromisoformat(day))
        if bar is None:
            continue
        s = settle(*bar, dv20, date.fromisoformat(day))
        conn.execute("""UPDATE shadow SET entry=?, high=?, close=?, exit=?, stopped=?, gross=?, cost=?, net=?, pnl=?,
                        closed_at=? WHERE day=? AND symbol=?""",
                     (s["entry"], s["high"], s["close"], s["exit"], s["stopped"], s["gross"], s["cost"], s["net"],
                      s["pnl"], now.astimezone(ET).isoformat(timespec="seconds"), day, sym))
        done.append({"day": day, "symbol": sym, **s})
    conn.execute("INSERT OR REPLACE INTO runs VALUES (?,?,?,?,?,?,?)",
                 (today.isoformat(), "close", now.astimezone(ET).isoformat(timespec="seconds"), None, None, len(done), ""))
    conn.commit()
    return done


def totals(conn: sqlite3.Connection) -> dict:
    """Running totals over settled 'signal' rows (the trades the rules would take)."""
    r = conn.execute("""SELECT COUNT(*), COALESCE(SUM(pnl), 0), COALESCE(SUM(net > 0), 0), COALESCE(SUM(placeable), 0),
                        COALESCE(SUM(CASE WHEN placeable = 1 THEN pnl END), 0), COALESCE(SUM(stopped), 0)
                        FROM shadow WHERE status='signal' AND exit IS NOT NULL""").fetchone()
    days = conn.execute("SELECT COUNT(*) FROM runs WHERE kind='morning'").fetchone()[0]
    signals = conn.execute("SELECT COUNT(*), COALESCE(SUM(placeable), 0) FROM shadow WHERE status='signal'").fetchone()
    return {"days": days, "signals": signals[0], "placeable": signals[1], "settled": r[0], "pnl": r[1], "wins": r[2],
            "settled_placeable": r[3], "pnl_placeable": r[4], "stops": r[5]}


# --- Messages ------------------------------------------------------------------------------------

def morning_message(rows: list[dict]) -> str:
    def line(r):
        fee = "" if r["ibkr_fee"] is None else f", fee {r['ibkr_fee']:.0f}%/yr"
        ok = "OK" if r["placeable"] else "NOT enough"
        return (f"{r['symbol']} +{r['gap']:.0%} open ${r['open_am']:.2f}: IBKR {r['ibkr_available']:,} shares "
                f"(needs {r['shares_needed']:,}{fee}) {ok}")
    taken = [r for r in rows if r["status"] == "signal"]
    other = [r for r in rows if r["status"] != "signal"]
    msg = [f"{len(taken)} shadow short(s), no orders placed."] + [line(r) for r in taken]
    if other:
        msg.append("Skipped: " + ", ".join(f"{r['symbol']} ({r['status'].replace('_', ' ')})" for r in other))
    return "\n".join(msg)


def close_message(done: list[dict], t: dict) -> str:
    msg = [f"{d['symbol']} {d['day']}: short ${d['entry']:.2f} -> ${d['exit']:.2f}"
           f"{' (stopped)' if d['stopped'] else ''}, {d['net']:+.1%} after costs, ${d['pnl']:+,.0f}" for d in done]
    msg.append(f"Shadow log: {t['settled']} trades over {t['days']} days, ${t['pnl']:+,.0f} total; "
               f"{t['placeable']} of {t['signals']} signals had enough IBKR shares (those: ${t['pnl_placeable']:+,.0f}).")
    return "\n".join(msg)


# --- Live wiring (used by main.py) ---------------------------------------------------------------

def live_fns(creds, conn: sqlite3.Connection, today: date):
    """Real Alpaca/IBKR fetchers for morning() and close(). Read-only data endpoints; no trading client."""
    from alpaca.data.historical import StockHistoricalDataClient
    client = StockHistoricalDataClient(creds.key_id, creds.secret_key)

    def history(symbols):
        start = today - timedelta(days=HISTORY_DAYS)
        return (daily_bars(client, symbols, start, today, "split"), daily_bars(client, symbols, start, today, "raw"))

    def news(symbol, day, prev_day):
        return v2.has_news(creds, conn, symbol, day, prev_day)

    def ibkr():
        return v4.fetch_ibkr(None)

    def bar(symbol, day):
        b = daily_bars(client, [symbol], day, day, "raw")
        b = b[b["day"] == pd.Timestamp(day)]
        return None if b.empty else tuple(float(b.iloc[0][k]) for k in ("open", "high", "close"))

    def recent():
        syms = active_symbols(creds)
        return syms, daily_bars(client, syms, today - timedelta(days=RECENT_DAYS), today, "raw")

    return recent, history, news, ibkr, bar
