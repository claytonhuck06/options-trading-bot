"""Short the overextended "dead company" spike (owner's Webull checklist), exactly as pre-registered.

Rules: docs/research/spike_short_v2_preregistration.md.
  * Screen: open >= +75% over the prior close (raw prior close $0.50-$8), down 50%+ over a year and below the
    200-day average, no catalyst news 9:30 ET t-1 to 9:30 ET t.
  * Entry: first red 5-minute bar (ending 9:45-15:00) closing >= 10% below the session high, after $2M traded.
  * Exit: 10% stop, or a 5-minute close 10% above the post-entry low once down 10%, or the 15:55 bar's close.
  * 15% of equity per trade, at most 3 a day. Shortable filters F1 (primary), F2, F0 as in the first test.
"""

from __future__ import annotations

import json
import math
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from research import gap_puts as gp
from research import spike_short as ss
from research.spike_short import ET

GAP = 0.75
NEIGHBOUR_GAPS = (0.50, 1.00)
YEAR = 252
MA = 200
DEAD_RATIO = 0.5
NEWS_FROM, NEWS_TO = time(9, 30), time(9, 30)
FIRST_ENTRY_END, LAST_ENTRY_END = time(9, 45), time(15, 0)
ROLLOVER = 0.10
MIN_TRADED = 2e6
STOP = 0.10
TRAIL = 0.10
LAST_BAR = time(15, 55)
POSITION = 0.15
MAX_PER_DAY = 3
LIVE_DAYS = 120
START_EQUITY = 100_000.0

MINUTES_SCHEMA = "CREATE TABLE IF NOT EXISTS minutes (symbol TEXT, day TEXT, bars TEXT, PRIMARY KEY (symbol, day));"


def open_db(path: Path) -> sqlite3.Connection:
    conn = gp.open_db(path)            # reuses the news cache table
    conn.executescript(MINUTES_SCHEMA)
    return conn


# --- Screen --------------------------------------------------------------------------------------

def add_features(df: pd.DataFrame) -> pd.DataFrame:
    """spike_short features plus the gap and the dead-company test, all from data up to t-1 (and t's open)."""
    df = ss.add_features(df)
    g = df.groupby("symbol", sort=False)
    df["gap"] = df["open"] / df["prev_close"] - 1
    df["close_year_before"] = g["close"].shift(YEAR + 1)                     # 252 trading days before t-1
    df["ma200_prev"] = g["close"].transform(lambda s: s.rolling(MA, min_periods=MA).mean().shift(1))
    df["dead"] = (df["prev_close"] <= DEAD_RATIO * df["close_year_before"]) & (df["prev_close"] < df["ma200_prev"])
    return df


def screen(df: pd.DataFrame, gap: float = GAP, require_dead: bool = True) -> pd.DataFrame:
    ok = ((df["gap"] >= gap) & (df["prev_raw_close"] >= ss.MIN_PRICE) & (df["prev_raw_close"] < ss.MAX_PRICE)
          & (df["n_before"] >= YEAR + 1) & ((df["day"] - df["prev_day"]).dt.days <= ss.MAX_GAP_DAYS))
    if require_dead:
        ok &= df["dead"]
    return df[ok].copy()


def in_news_window(article: dict, day: date, prev_day: date) -> bool:
    t = datetime.fromisoformat(article["created_at"].replace("Z", "+00:00"))
    return datetime.combine(prev_day, NEWS_FROM, tzinfo=ET) <= t <= datetime.combine(day, NEWS_TO, tzinfo=ET)


def has_news(creds, conn, symbol: str, day: date, prev_day: date) -> bool:
    arts = [a for a in gp.fetch_news(creds, conn, symbol, day, prev_day) if in_news_window(a, day, prev_day)]
    return any(gp.is_catalyst(a, symbol) for a in arts)


# --- Minute bars ---------------------------------------------------------------------------------

def fetch_minutes(client, conn: sqlite3.Connection, symbol: str, day: date) -> pd.DataFrame:
    key = (symbol, day.isoformat())
    hit = conn.execute("SELECT bars FROM minutes WHERE symbol=? AND day=?", key).fetchone()
    if hit is None:
        from alpaca.data.enums import Adjustment, DataFeed
        from alpaca.data.requests import StockBarsRequest
        from alpaca.data.timeframe import TimeFrame
        req = StockBarsRequest(symbol_or_symbols=symbol, timeframe=TimeFrame.Minute, feed=DataFeed.SIP,
                               adjustment=Adjustment.RAW,
                               start=datetime.combine(day, time(9, 30), tzinfo=ET),
                               end=datetime.combine(day, time(16, 0), tzinfo=ET))
        data = ss._retry(lambda: client.get_stock_bars(req).data.get(symbol, []))
        rows = [[b.timestamp.astimezone(ET).strftime("%H:%M"), b.open, b.high, b.low, b.close, b.volume] for b in data]
        conn.execute("INSERT OR REPLACE INTO minutes VALUES (?,?,?)", (*key, json.dumps(rows)))
        conn.commit()
    else:
        rows = json.loads(hit[0])
    return minutes_frame(rows)


def minutes_frame(rows: list) -> pd.DataFrame:
    m = pd.DataFrame(rows, columns=["hm", "open", "high", "low", "close", "volume"])
    m = m[(m["hm"] >= "09:30") & (m["hm"] < "16:00")].reset_index(drop=True)
    m["minute"] = [int(h[:2]) * 60 + int(h[3:]) for h in m["hm"]]
    return m


# --- The intraday trade --------------------------------------------------------------------------

@dataclass
class DayTrade:
    entry_time: str
    entry: float
    exit_time: str
    exit: float
    how: str                   # stop | stalled | close
    traded_before: float       # $ volume before entry


def _hm(minute: int) -> str:
    return f"{minute // 60:02d}:{minute % 60:02d}"


def day_trade(m: pd.DataFrame, prev_raw_close: float) -> tuple[DayTrade | None, str]:
    """Entry and exit for one gap day from its minute bars (raw prices). Returns (trade, reason if none)."""
    if m.empty:
        return None, "no minute bars"
    open_min = 9 * 60 + 30
    first_end = FIRST_ENTRY_END.hour * 60 + FIRST_ENTRY_END.minute
    last_end = LAST_ENTRY_END.hour * 60 + LAST_ENTRY_END.minute
    m = m.assign(bucket=(m["minute"] - open_min) // 5, dv=m["close"] * m["volume"])
    entry_i = None
    for b, grp in m.groupby("bucket", sort=True):
        end = open_min + 5 * (b + 1)
        if end > last_end:
            break
        if end < first_end:
            continue
        upto = m.iloc[:grp.index[-1] + 1]
        hi, close5, open5 = upto["high"].max(), grp["close"].iloc[-1], grp["open"].iloc[0]
        if close5 <= (1 - ROLLOVER) * hi and close5 < open5 and upto["dv"].sum() >= MIN_TRADED:
            if upto["low"].min() <= (1 - ss.SSR_DROP) * prev_raw_close:
                return None, "SSR in force"
            entry_i, entry, entry_end = grp.index[-1], close5, end
            break
    if entry_i is None:
        return None, "no rollover signal"
    traded = float(m.iloc[:entry_i + 1]["dv"].sum())
    stop = entry * (1 + STOP)
    low = math.inf
    last_close_min = LAST_BAR.hour * 60 + LAST_BAR.minute + 5
    for i in range(entry_i + 1, len(m)):
        r = m.iloc[i]
        if r["high"] >= stop:
            return DayTrade(_hm(entry_end), entry, r["hm"], stop, "stop", traded), ""
        low = min(low, r["low"])
        bar_closes = (r["minute"] + 1 - open_min) % 5 == 0
        if bar_closes and low <= (1 - TRAIL) * entry and r["close"] >= (1 + TRAIL) * low:
            return DayTrade(_hm(entry_end), entry, r["hm"], r["close"], "stalled", traded), ""
        if r["minute"] + 1 >= last_close_min:
            break
    last = m[m["minute"] < last_close_min].iloc[-1]
    return DayTrade(_hm(entry_end), entry, last["hm"], last["close"], "close", traded), ""


def net_return(t: DayTrade, cost_mult: float = 1.0) -> tuple[float, float]:
    """(gross, cost) for a short, as fractions of notional."""
    cost = ss.cost_for(t.entry, t.traded_before) * cost_mult
    if t.how == "stop":
        cost += ss.STOP_SLIPPAGE * cost_mult
    return (t.entry - t.exit) / t.entry, cost


# --- Portfolio -----------------------------------------------------------------------------------

@dataclass
class Trade:
    symbol: str
    day: pd.Timestamp
    gap: float
    entry_time: str
    entry: float
    exit_time: str
    exit: float
    how: str
    gross: float
    cost: float
    net: float
    pnl: float


def simulate(events: list[dict], calendar: pd.DatetimeIndex, cost_mult: float = 1.0) -> tuple[pd.Series, list[Trade]]:
    """events: dicts with symbol, day, gap, trade (DayTrade). At most 3 per day, first entries by time."""
    by_day: dict[pd.Timestamp, list[dict]] = {}
    for e in events:
        by_day.setdefault(e["day"], []).append(e)
    equity, values, trades = START_EQUITY, [], []
    for day in calendar:
        todays = sorted(by_day.get(day, []), key=lambda e: (e["trade"].entry_time, -e["gap"]))[:MAX_PER_DAY]
        notional = POSITION * equity
        pnl = 0.0
        for e in todays:
            gross, cost = net_return(e["trade"], cost_mult)
            p = notional * (gross - cost)
            pnl += p
            t = e["trade"]
            trades.append(Trade(e["symbol"], day, e["gap"], t.entry_time, t.entry, t.exit_time, t.exit, t.how,
                                gross, cost, gross - cost, p))
        equity += pnl
        values.append(equity)
    return pd.Series(values, index=calendar), trades


@dataclass
class Stats:
    trades: int
    win: float
    avg: float
    total: float
    cagr: float
    max_dd: float
    sharpe: float
    stops: float


def stats(eq: pd.Series, trades: list[Trade]) -> Stats:
    r = np.array([t.net for t in trades])
    rets = eq.pct_change().fillna(eq.iloc[0] / START_EQUITY - 1)
    years = (eq.index[-1] - eq.index[0]).days / 365.25
    total = eq.iloc[-1] / START_EQUITY - 1
    sd = rets.std()
    return Stats(len(r), float((r > 0).mean()) if len(r) else 0.0, float(r.mean()) if len(r) else 0.0, float(total),
                 float((1 + total) ** (1 / years) - 1) if years > 0 else 0.0,
                 float((1 - eq / np.maximum(eq.cummax(), START_EQUITY)).max()),
                 float(rets.mean() / sd * math.sqrt(252)) if sd > 0 else 0.0,
                 float(np.mean([t.how == "stop" for t in trades])) if trades else 0.0)


# --- Run -----------------------------------------------------------------------------------------

def run(creds, bars_conn: sqlite3.Connection, conn: sqlite3.Connection, out_dir: Path, today: date) -> tuple[Path, bool]:
    from alpaca.data.historical import StockHistoricalDataClient
    client = StockHistoricalDataClient(creds.key_id, creds.secret_key)
    df = add_features(ss.load_bars(bars_conn))
    spy = pd.read_sql("SELECT day, close FROM spy", bars_conn, parse_dates=["day"]).set_index("day")["close"].sort_index()
    grid = ss.etb_grid(bars_conn)
    etb_today = dict(bars_conn.execute("SELECT symbol, easy_to_borrow FROM assets"))

    # Candidates: every gap >= +50% (dead or not), so all variants share one fetch.
    cand = screen(df, min(NEIGHBOUR_GAPS), require_dead=False)
    print(f"{len(cand):,} gaps of +{min(NEIGHBOUR_GAPS):.0%} or more; news and minute bars (cached)...", flush=True)
    events, problems, reasons = [], [], {}
    for n, r in enumerate(cand.itertuples(), 1):
        d, p = r.day.date(), r.prev_day.date()
        e = {"symbol": r.symbol, "day": r.day, "gap": r.gap, "dead": bool(r.dead),
             "f1": ss.in_grid(grid, r.prev_raw_close, r.dv20_before), "f2": bool(etb_today.get(r.symbol))}
        needs_news = r.dead
        e["news"] = has_news(creds, conn, r.symbol, d, p) if needs_news else None
        if r.gap >= GAP or (r.dead and e["news"] is False):
            trade, why = day_trade(fetch_minutes(client, conn, r.symbol, d), r.prev_raw_close)
            e["trade"] = trade
            reasons[why or "traded"] = reasons.get(why or "traded", 0) + 1
            if trade and trade.entry_time < "09:45":
                problems.append(f"{r.symbol} {d}: entry before 9:45")
        events.append(e)
        if n % 100 == 0:
            print(f"  {n:,}/{len(cand):,}", flush=True)

    first = pd.Timestamp("2017-01-01")
    cal = spy.index[spy.index >= first]
    spy_eq = spy.loc[cal] / spy.loc[cal].iloc[0] * START_EQUITY
    spy_s = stats(spy_eq, [])

    def pick(gap=GAP, filt="f1", checklist=True):
        return [e for e in events if e.get("trade") and e["day"] >= first and e["gap"] >= gap
                and (filt is None or e[filt]) and (not checklist or (e["dead"] and e["news"] is False))]

    variants = {"main": pick(), "f2": pick(filt="f2"), "f0": pick(filt=None), "g50": pick(0.50), "g100": pick(1.00),
                "no_checklist": pick(checklist=False), "f0_no_checklist": pick(filt=None, checklist=False)}
    res = {k: simulate(v, cal) for k, v in variants.items()}
    res["double"] = simulate(variants["main"], cal, 2.0)
    st = {k: stats(*v) for k, v in res.items()}

    live_days = spy.index[-LIVE_DAYS:]
    live = [e for e in events if e["day"] in live_days and e["gap"] >= GAP and e["dead"] and e["news"] is False]
    live_etb = sum(e["f2"] for e in live)
    share = live_etb / len(live) if live else 0.0

    m, d2 = st["main"], st["double"]
    bars = [
        ("Data check", not problems, "no problems" if not problems else "; ".join(problems[:5])),
        ("Enough trades (≥ 30)", m.trades >= 30, f"{m.trades} trades"),
        ("Makes money after costs", m.total > 0 and m.avg > 0, f"total {m.total:+.1%}, average trade {m.avg:+.2%}"),
        ("Worth the risk (Sharpe ≥ SPY)", m.sharpe >= spy_s.sharpe, f"{m.sharpe:.2f} vs {spy_s.sharpe:.2f}"),
        ("Survivable (worst drop ≤ 25%)", m.max_dd <= 0.25, f"{m.max_dd:.1%}"),
        ("Survives double costs", d2.total > 0, f"total {d2.total:+.1%}"),
        ("Not fragile (+50% and +100% gaps average > 0)", st["g50"].avg > 0 and st["g100"].avg > 0,
         f"+50%: {st['g50'].avg:+.2%} ({st['g50'].trades}); +100%: {st['g100'].avg:+.2%} ({st['g100'].trades})"),
        ("Checklist filters help (with steps 3–4 beats without)", m.avg > st["no_checklist"].avg,
         f"{m.avg:+.2%} vs {st['no_checklist'].avg:+.2%}"),
        ("Actually placeable (≥ 25% of last 120 days' signals easy to borrow today, ≥ 5)",
         share >= 0.25 and live_etb >= 5, f"{live_etb} of {len(live)} ({share:.0%})"),
    ]
    go = all(ok for _, ok, _ in bars)

    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(10, 5))
    for k, label, c in (("main", "Checklist, shortable estimate (primary)", "#2563eb"),
                        ("f0", "Checklist, no shortable filter", "#9ca3af"),
                        ("f2", "Checklist, easy to borrow today", "#f59e0b")):
        ax.plot(res[k][0], label=label, color=c)
    ax.plot(spy_eq, label="SPY buy-and-hold", color="#111827")
    ax.set_yscale("log")
    ax.set_ylabel("Account value ($, log scale)")
    ax.set_title("Webull checklist: short the overextended dead-company spike (day trade)")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8)
    fig.tight_layout()
    (out_dir / "img").mkdir(parents=True, exist_ok=True)
    fig.savefig(out_dir / "img" / "spike_short_v2_equity.png", dpi=120)
    plt.close(fig)

    csv = out_dir / "spike_short_v2_trades.csv"
    pd.DataFrame([{**t.__dict__, "variant": k} for k in ("main", "f0") for t in res[k][1]]).to_csv(
        csv, index=False, float_format="%.6g")

    n75 = [e for e in events if e["gap"] >= GAP and e["day"] >= first]
    n_dead = [e for e in n75 if e["dead"]]
    n_quiet = [e for e in n_dead if e["news"] is False]
    n_sig = [e for e in n_quiet if e.get("trade")]
    n_f1 = [e for e in n_sig if e["f1"]]
    labels = {"main": "**Checklist + shortable estimate F1 (primary)**", "double": "Primary, double costs",
              "f2": "Checklist + easy to borrow today (F2)", "f0": "Checklist, no shortable filter (F0)",
              "g50": "F1, gap ≥ +50%", "g100": "F1, gap ≥ +100%", "no_checklist": "F1, all +75% gaps (skip steps 3–4)",
              "f0_no_checklist": "F0, all +75% gaps (skip steps 3–4)"}
    md = [f"# Webull Checklist: Short the Overextended Dead-Company Spike — {today}", "",
          "*Paper research only; not investment advice. Rules: "
          "[spike_short_v2_preregistration.md](spike_short_v2_preregistration.md), committed before any data was fetched.*", "",
          f"## Verdict: **{'GO' if go else 'NO-GO'}** ({sum(ok for _, ok, _ in bars)} of {len(bars)} bars passed)", "",
          "| Bar | Result | Detail |", "|---|---|---|"]
    md += [f"| {i} · {n} | {'PASS' if ok else '**FAIL**'} | {d} |" for i, (n, ok, d) in enumerate(bars, 1)]
    md += ["", f"## The funnel ({first.date()} to {cal[-1].date()})", "",
           f"- Opened +75% or more (raw prior close $0.50–$8): **{len(n75)}**",
           f"- …and a dead company (down 50%+ in a year, below 200-day average): **{len(n_dead)}**",
           f"- …and no news: **{len(n_quiet)}**",
           f"- …and the intraday peak signal fired (red 5-min bar 10% off the high, $2M traded, no SSR): **{len(n_sig)}**",
           f"- …and likely shortable (F1): **{len(n_f1)}**", "",
           "Why gap days produced no trade (all fetched days): " +
           ", ".join(f"{k}: {v}" for k, v in sorted(reasons.items(), key=lambda kv: -kv[1])) + ".", "",
           "## Results (day trades, 15% of the account each, returns after costs)", "",
           "| | Trades | Winners | Avg trade | Total | Annual | Worst drop | Sharpe | Stopped out |",
           "|---|---|---|---|---|---|---|---|---|"]
    md += [f"| {labels[k]} | {s.trades} | {s.win:.0%} | {s.avg:+.2%} | {s.total:+.1%} | {s.cagr:+.1%} | {s.max_dd:.1%} "
           f"| {s.sharpe:.2f} | {s.stops:.0%} |" for k, s in ((k, st[k]) for k in labels)]
    md += [f"| SPY buy-and-hold | — | — | — | {spy_s.total:+.1%} | {spy_s.cagr:+.1%} | {spy_s.max_dd:.1%} | "
           f"{spy_s.sharpe:.2f} | — |", "", "![Account values](img/spike_short_v2_equity.png)", "",
           f"## Live check: last {LIVE_DAYS} trading days ({live_days[0].date()} to {live_days[-1].date()})", "",
           f"{len(live)} stocks passed steps 2–4; **{live_etb}** are easy to borrow on Alpaca today.", ""]
    md += [f"- {e['day'].date()} {e['symbol']} (+{e['gap']:.0%}): "
           f"{'easy to borrow' if e['f2'] else 'not shortable on Alpaca'}" for e in live]
    tr = sorted(res["f0"][1], key=lambda t: -t.net)
    md += ["", "## Checklist trades, no shortable filter (F0): 10 best and 10 worst", "",
           "| Day | Symbol | Gap | Entry | Exit | How | After costs |", "|---|---|---|---|---|---|---|"]
    md += [f"| {t.day.date()} | {t.symbol} | {t.gap:+.0%} | {t.entry_time} ${t.entry:.2f} | {t.exit_time} ${t.exit:.2f} "
           f"| {t.how} | {t.net:+.1%} |" for t in (tr[:10] + tr[-10:] if len(tr) > 20 else tr)]
    md += ["", f"Every trade: [{csv.name}]({csv.name}).", "", "## Limits (from the rules, section G)", "",
           "- Webull quant ratings (step 5) can't be tested; no history.",
           "- No pre-market trading; entries from 9:45.",
           "- Past borrow availability is estimated (F1); Webull's shortable list may differ from Alpaca's.",
           "- Fills at 5-minute closes plus modelled costs; halts aren't modelled.", ""]
    path = out_dir / f"spike_short_v2_backtest_{today}.md"
    path.write_text("\n".join(md))
    return path, go
