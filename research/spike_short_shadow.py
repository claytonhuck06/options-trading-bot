"""Forward shadow log for the checklist short, day-trade-only version. Places NO orders anywhere.

Rules: docs/research/spike_short_v4_preregistration.md section D, day-trade-only (v4's "same day" row):
short at the open, 50% stop on the day's high (+2% slippage), otherwise cover at the close; 2.7% of $100k per
short, at most 3 a day, biggest gaps first; costs = v3 tiers + 1% locate (no nights, so no borrow).
Two simulated paper accounts, each from $100,000 and compounding: 2.7% per short (the rules) and 40% per short
(owner request 2026-10-21, information only: the backtest's worst drop at 40% was 68%). Same trades, exits, costs.

  * morning (~9:35 ET): screen today's checklist candidates (+75% open gap, prior raw close $0.50-$8, dead company,
    no news 9:30 ET t-1 to 9:30 ET t, SSR skip) from Alpaca's free SIP daily bars, and record IBKR's shortable
    list (shares available, fee) at that moment for each.
  * close (after 16:15 ET): record the simulated exit and net P&L from the day's final SIP bar, and the stock's
    dollar volume in the first 5 and 15 minutes and the whole session (SIP minute bars). A size is "fillable" if
    it is at most 10% of the first 15 minutes' dollar volume.

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
BIG = 0.40                                                # owner's 40% account (information only)
SIZES = {"27": POSITION, "40": BIG}                       # column suffix -> fraction of that account per short
EQUITY = 100_000.0                                        # each account's start
FILL_SHARE = 0.10                                         # fillable: position <= 10% of first-15-min $ volume
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
CREATE TABLE IF NOT EXISTS minutes (symbol TEXT, day TEXT, bars TEXT, PRIMARY KEY (symbol, day));
"""
# Added for the two-account version; ALTERed into older files. pnl/shares_needed/placeable are the 2.7% account.
EXTRA_COLUMNS = {"equity_27": "REAL", "notional_27": "REAL", "fillable_27": "INTEGER",
                 "equity_40": "REAL", "notional_40": "REAL", "pnl_40": "REAL", "shares_needed_40": "INTEGER",
                 "placeable_40": "INTEGER", "fillable_40": "INTEGER",
                 "dv_first5": "REAL", "dv_first15": "REAL", "dv_session": "REAL"}


def open_db(path: Path | str) -> sqlite3.Connection:
    if str(path) != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.executescript(SCHEMA)
    have = {r[1] for r in conn.execute("PRAGMA table_info(shadow)")}
    for col, kind in EXTRA_COLUMNS.items():
        if col not in have:
            conn.execute(f"ALTER TABLE shadow ADD COLUMN {col} {kind}")
    conn.commit()
    return conn


def equity(conn: sqlite3.Connection, day: date, acct: str) -> float:
    """Account value at the start of `day`: $100k plus every settled signal's P&L from earlier days."""
    col = "pnl" if acct == "27" else f"pnl_{acct}"
    got = conn.execute(f"SELECT COALESCE(SUM({col}), 0) FROM shadow WHERE status='signal' AND {col} IS NOT NULL "
                       "AND day < ?", (day.isoformat(),)).fetchone()[0]
    return EQUITY + got


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


def shares_needed(open_price: float, notional: float = POSITION * EQUITY) -> int:
    return int(math.ceil(notional / open_price))


def dollar_volumes(m: pd.DataFrame) -> tuple[float, float, float]:
    """$ traded in the first 5 and 15 minutes and the whole session, from v2.minutes_frame rows."""
    if m.empty:
        return 0.0, 0.0, 0.0
    dv = m["close"] * m["volume"]
    open_min = 9 * 60 + 30
    return (float(dv[m["minute"] < open_min + 5].sum()), float(dv[m["minute"] < open_min + 15].sum()),
            float(dv.sum()))


# --- The trade -----------------------------------------------------------------------------------

def settle(entry: float, high: float, close: float, dv20: float | None, day: date,
           notional: float = POSITION * EQUITY) -> dict:
    """v4's plan with no extra days: stop at entry x 1.5 on the day's high, else the close. Net of costs;
    pnl is for a short of `notional` dollars."""
    e = {"path": [(pd.Timestamp(day), entry, high, close)], "raw_factor": 1.0,
         "dv20": dv20 if dv20 is not None else 0.0}
    marks, stopped, cost = v4.plan(e, extra=0)
    exit_ = marks[-1][1]
    gross = (entry - exit_) / entry
    return {"entry": entry, "high": high, "close": close, "exit": exit_, "stopped": int(stopped), "gross": gross,
            "cost": cost, "net": gross - cost, "pnl": notional * (gross - cost)}


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
        eq = {a: equity(conn, today, a) for a in SIZES}
        for r in rows:
            avail, fee = table.get(r["symbol"], (0, float("nan")))
            r.update(ibkr_available=avail, ibkr_fee=None if fee != fee else fee, ibkr_stamp=stamp, checked_at=at,
                     equity_27=eq["27"], equity_40=eq["40"],
                     shares_needed=shares_needed(r["open_am"], SIZES["27"] * eq["27"]),
                     shares_needed_40=shares_needed(r["open_am"], SIZES["40"] * eq["40"]))
            r["placeable"] = int(avail >= r["shares_needed"])
            r["placeable_40"] = int(avail >= r["shares_needed_40"])
            conn.execute("""INSERT OR REPLACE INTO shadow (day, symbol, status, rank, gap, prev_raw_close, open_am, dv20,
                            shares_needed, ibkr_available, ibkr_fee, ibkr_stamp, placeable, checked_at,
                            equity_27, equity_40, shares_needed_40, placeable_40)
                            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                         (today.isoformat(), r["symbol"], r["status"], r.get("rank"), r["gap"], r["prev_raw_close"],
                          r["open_am"], r["dv20"], r["shares_needed"], avail, r["ibkr_fee"], stamp, r["placeable"], at,
                          eq["27"], eq["40"], r["shares_needed_40"], r["placeable_40"]))
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


def close(conn: sqlite3.Connection, today: date, now: datetime, bar_fn, minutes_fn=None) -> list[dict]:
    """Settle every open shadow trade, oldest day first, sizing each account off its value at the start of that
    day. bar_fn(symbol, day) -> (open, high, close) raw SIP daily bar or None; minutes_fn(symbol, day) -> minute
    frame (v2.minutes_frame) for the dollar-volume check, or None to skip it."""
    done = []
    for day, sym, dv20 in open_trades(conn, today, now):
        d = date.fromisoformat(day)
        bar = bar_fn(sym, d)
        if bar is None:
            continue
        eq = {a: equity(conn, d, a) for a in SIZES}
        notional = {a: SIZES[a] * eq[a] for a in SIZES}
        s = settle(*bar, dv20, d, notional["27"])
        s.update(equity_27=eq["27"], equity_40=eq["40"], notional_27=notional["27"], notional_40=notional["40"],
                 pnl_40=notional["40"] * s["net"])
        dv = dollar_volumes(minutes_fn(sym, d)) if minutes_fn else (None, None, None)
        s.update(dv_first5=dv[0], dv_first15=dv[1], dv_session=dv[2])
        for a in SIZES:
            s[f"fillable_{a}"] = None if dv[1] is None else int(notional[a] <= FILL_SHARE * dv[1])
        conn.execute("""UPDATE shadow SET entry=?, high=?, close=?, exit=?, stopped=?, gross=?, cost=?, net=?, pnl=?,
                        closed_at=?, equity_27=?, equity_40=?, notional_27=?, notional_40=?, pnl_40=?,
                        dv_first5=?, dv_first15=?, dv_session=?, fillable_27=?, fillable_40=?
                        WHERE day=? AND symbol=?""",
                     (s["entry"], s["high"], s["close"], s["exit"], s["stopped"], s["gross"], s["cost"], s["net"],
                      s["pnl"], now.astimezone(ET).isoformat(timespec="seconds"), s["equity_27"], s["equity_40"],
                      s["notional_27"], s["notional_40"], s["pnl_40"], s["dv_first5"], s["dv_first15"],
                      s["dv_session"], s["fillable_27"], s["fillable_40"], day, sym))
        conn.commit()                                     # later days size off this one
        status = conn.execute("SELECT status FROM shadow WHERE day=? AND symbol=?", (day, sym)).fetchone()[0]
        done.append({"day": day, "symbol": sym, "status": status, **s})
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
    signals = conn.execute("SELECT COUNT(*), COALESCE(SUM(placeable), 0), COALESCE(SUM(placeable_40), 0) "
                           "FROM shadow WHERE status='signal'").fetchone()
    big = conn.execute("""SELECT COALESCE(SUM(pnl_40), 0), COALESCE(SUM(fillable_27), 0), COALESCE(SUM(fillable_40), 0),
                          COUNT(dv_first15), COALESCE(MIN(pnl_40 / equity_40), 0)
                          FROM shadow WHERE status='signal' AND exit IS NOT NULL""").fetchone()
    return {"days": days, "signals": signals[0], "placeable": signals[1], "settled": r[0], "pnl": r[1], "wins": r[2],
            "settled_placeable": r[3], "pnl_placeable": r[4], "stops": r[5],
            "placeable_40": signals[2], "pnl_40": big[0], "fillable_27": big[1], "fillable_40": big[2],
            "volume_checked": big[3], "worst_40": big[4],
            "equity_27": EQUITY + r[1], "equity_40": EQUITY + big[0]}


# --- Messages ------------------------------------------------------------------------------------

def morning_message(rows: list[dict]) -> str:
    def line(r):
        fee = "" if r["ibkr_fee"] is None else f", fee {r['ibkr_fee']:.0f}%/yr"
        ok = "OK" if r["placeable"] else "NOT enough"
        ok40 = "OK" if r["placeable_40"] else "NOT enough"
        return (f"{r['symbol']} +{r['gap']:.0%} open ${r['open_am']:.2f}: IBKR {r['ibkr_available']:,} shares{fee}. "
                f"2.7% needs {r['shares_needed']:,} {ok}; 40% needs {r['shares_needed_40']:,} {ok40}")
    taken = [r for r in rows if r["status"] == "signal"]
    other = [r for r in rows if r["status"] != "signal"]
    msg = [f"{len(taken)} checklist short(s) today."] + [line(r) for r in taken]
    if other:
        msg.append("Skipped: " + ", ".join(f"{r['symbol']} ({r['status'].replace('_', ' ')})" for r in other))
    return "\n".join(msg)


def _fill(d: dict, a: str) -> str:
    f = d.get(f"fillable_{a}")
    return "" if f is None else (" fillable" if f else " NOT fillable")


def close_message(done: list[dict], t: dict) -> str:
    msg = []
    for d in done:
        line = (f"{d['symbol']} {d['day']}: short ${d['entry']:.2f} -> ${d['exit']:.2f}"
                f"{' (stopped)' if d['stopped'] else ''}, {d['net']:+.1%} after costs. "
                f"2.7%: ${d['pnl']:+,.0f}{_fill(d, '27')}; 40%: ${d['pnl_40']:+,.0f}{_fill(d, '40')}")
        if d.get("dv_first15") is not None:
            line += (f". $ traded: first 5 min ${d['dv_first5']:,.0f}, first 15 ${d['dv_first15']:,.0f}, "
                     f"day ${d['dv_session']:,.0f}")
        if d.get("status") != "signal":
            line += " (over the 3-a-day cap; not in the accounts)"
        msg.append(line)
    msg.append(summary_line(t))
    return "\n".join(msg)


def day_summary(conn: sqlite3.Connection, today: date, done: list[dict], t: dict, ibkr_lines: list[str],
                ibkr_status: str) -> str:
    """The owner's end-of-day summary (sent every weekday after the close)."""
    ran = conn.execute("SELECT 1 FROM runs WHERE day=? AND kind='morning'", (today.isoformat(),)).fetchone()
    sig = [r[0] for r in conn.execute("SELECT symbol FROM shadow WHERE day=? AND status='signal' ORDER BY rank",
                                      (today.isoformat(),))]
    if ran is None:
        head = "Morning screen didn't run (market holiday, or the Mac/bot was off)."
    else:
        head = f"Signals today: {', '.join(sig) if sig else 'none'}."
    msg = [head, "IBKR paper: " + ("; ".join(ibkr_lines) if ibkr_lines else "no trades today"), ibkr_status]
    msg += [f"Shadow {d['symbol']} {d['day']}: {d['net']:+.1%} after costs (2.7% ${d['pnl']:+,.0f}, "
            f"40% ${d['pnl_40']:+,.0f})" for d in done]
    msg.append(f"Shadow accounts: 2.7% ${t['equity_27']:,.0f} ({t['equity_27'] / EQUITY - 1:+.1%}), "
               f"40% ${t['equity_40']:,.0f} ({t['equity_40'] / EQUITY - 1:+.1%}); {t['settled']} trades over "
               f"{t['days']} days.")
    return "\n".join(msg)


def summary_line(t: dict) -> str:
    return (f"Shadow accounts after {t['settled']} trades over {t['days']} days: "
            f"2.7% account ${t['equity_27']:,.0f} ({t['equity_27'] / EQUITY - 1:+.1%}), "
            f"40% account ${t['equity_40']:,.0f} ({t['equity_40'] / EQUITY - 1:+.1%}). "
            f"Enough IBKR shares: {t['placeable']} of {t['signals']} at 2.7%, {t['placeable_40']} at 40%. "
            f"Fillable (<=10% of first-15-min $ volume): {t['fillable_27']} of {t['volume_checked']} at 2.7%, "
            f"{t['fillable_40']} at 40%.")


# --- Live wiring (used by main.py) ---------------------------------------------------------------

def live_fns(creds, conn: sqlite3.Connection, today: date):
    """Real Alpaca/IBKR fetchers for morning() and close() (bar, minutes). Read-only data; no trading client."""
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

    def minutes(symbol, day):
        return v2.fetch_minutes(client, conn, symbol, day)

    def recent():
        syms = active_symbols(creds)
        return syms, daily_bars(client, syms, today - timedelta(days=RECENT_DAYS), today, "raw")

    return recent, history, news, ibkr, bar, minutes
