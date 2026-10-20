"""Phase 1b: SPY volatility forecast (HAR-RV with implied volatility), judged against VIX out-of-sample.

Pre-registered in docs/research/iron_condor_preregistration.md, Amendment 1 (F1.2):
  target   realized volatility over the next 21 trading days (5-minute squared returns, regular hours,
           plus the squared overnight return), annualised
  inputs   log RV over the last 1, 5, 22 days; log VIX close; size of the day's return if negative;
           number of scheduled FOMC statements and CPI releases in the next 21 trading days
  fit      OLS on logs, refit at the start of each month on rows whose target window had already ended
  models   M0 = VIX only, M1 = HAR-RV only, M2 = full model
  bar      in discovery (forecasts made 2018–2021), M2's QLIKE loss beats M0's with a one-sided
           Diebold-Mariano test (Newey-West, 21 lags), p < 0.05

Everything is computed from data available at the close of the forecast day. No option prices are used,
so no trade results can leak into the rules.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import norm

HORIZON = 21
ANNUAL = 252
DM_LAGS = 21
MODELS = {
    "M0": ["log_vix"],
    "M1": ["log_rv_1d", "log_rv_5d", "log_rv_22d"],
    "M2": ["log_rv_1d", "log_rv_5d", "log_rv_22d", "log_vix", "neg_ret", "events"],
}
DISCOVERY = ("2018-01-01", "2021-12-31")
CONFIRMATION_START = "2022-01-01"


# --- Realized variance from minute bars --------------------------------------------------------

def daily_variance(minutes: pd.DataFrame) -> pd.DataFrame:
    """minutes: columns ts (UTC), close, open; dividend-adjusted. Returns per day: var_intraday, var_overnight,
    close (16:00), ret (close-to-close).

    Adjusted prices are used on purpose: an ex-dividend drop is not volatility (Amendment 2).
    """
    df = minutes.copy()
    et = pd.to_datetime(df["ts"], utc=True).dt.tz_convert("America/New_York")
    df["day"] = et.dt.date
    df["tod"] = et.dt.hour * 60 + et.dt.minute
    df = df[(df["tod"] >= 9 * 60 + 30) & (df["tod"] < 16 * 60)]
    rows = []
    for day, g in df.groupby("day", sort=True):
        g = g.sort_values("tod")
        first_open = g["open"].iloc[0]
        # 5-minute grid: price at the end of each 5-minute bucket = last close seen by then.
        bucket = (g["tod"] - 570) // 5
        p = np.concatenate([[first_open], g.groupby(bucket)["close"].last().to_numpy()])
        r5 = np.diff(np.log(p))
        rows.append((day, float(np.sum(r5 ** 2)), float(g["close"].iloc[-1]), float(first_open), len(g)))
    out = pd.DataFrame(rows, columns=["day", "var_intraday", "close", "open", "minutes"]).set_index("day")
    prev_close = out["close"].shift(1)
    out["var_overnight"] = np.log(out["open"] / prev_close) ** 2
    out["ret"] = np.log(out["close"] / prev_close)
    out = out.iloc[1:]                                         # first day has no overnight return
    out["var"] = out["var_intraday"] + out["var_overnight"]
    return out


def build_features(daily: pd.DataFrame, vix: pd.Series, event_days: set[date]) -> pd.DataFrame:
    """One row per day t: inputs known at t's close and the target over days t+1..t+21."""
    d = daily.copy()
    v = d["var"]
    d["log_rv_1d"] = 0.5 * np.log(ANNUAL * v)
    d["log_rv_5d"] = 0.5 * np.log(ANNUAL * v.rolling(5).mean())
    d["log_rv_22d"] = 0.5 * np.log(ANNUAL * v.rolling(22).mean())
    d["log_vix"] = np.log(vix.reindex(d.index) / 100.0)
    d["neg_ret"] = (-d["ret"]).clip(lower=0.0)
    days = list(d.index)
    is_event = np.array([day in event_days for day in days], dtype=float)
    fwd_events = np.full(len(days), np.nan)
    fwd_var = np.full(len(days), np.nan)
    for i in range(len(days) - HORIZON):
        fwd_events[i] = is_event[i + 1:i + 1 + HORIZON].sum()
        fwd_var[i] = v.iloc[i + 1:i + 1 + HORIZON].mean()
    d["events"] = fwd_events
    d["target_var"] = ANNUAL * fwd_var                          # annualised variance over the next 21 days
    d["log_target"] = 0.5 * np.log(d["target_var"])
    d["target_end"] = [days[i + HORIZON] if i + HORIZON < len(days) else None for i in range(len(days))]
    # Events are scheduled in advance, so the count is known even near the end of the data.
    for i in range(max(0, len(days) - HORIZON), len(days)):
        d.iloc[i, d.columns.get_loc("events")] = is_event[i + 1:].sum()
    return d.dropna(subset=["log_rv_22d", "log_vix"])


# --- Out-of-sample forecasts -------------------------------------------------------------------

def _ols(X: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, float]:
    A = np.column_stack([np.ones(len(X)), X])
    beta, *_ = np.linalg.lstsq(A, y, rcond=None)
    resid = y - A @ beta
    return beta, float(np.var(resid, ddof=A.shape[1]))


def oos_forecasts(feat: pd.DataFrame, first_forecast: str = DISCOVERY[0]) -> pd.DataFrame:
    """Monthly expanding-window refits. A row can be used for fitting only once its target window has ended
    before the refit date. Forecast variance = exp(2·(fit + s²/2)): the usual log-normal bias correction,
    applied the same way to every model."""
    feat = feat.copy()
    idx = pd.to_datetime(pd.Index(feat.index))
    months = sorted({(t.year, t.month) for t in idx if t >= pd.Timestamp(first_forecast)})
    out = []
    for y, m in months:
        start = pd.Timestamp(y, m, 1)
        nxt = pd.Timestamp(y + (m == 12), m % 12 + 1, 1)
        ends = pd.to_datetime(feat["target_end"])
        train = feat[(ends < start) & feat["log_target"].notna()]
        test = feat[(idx >= start) & (idx < nxt)]
        if test.empty:
            continue
        rec = pd.DataFrame(index=test.index)
        for name, cols in MODELS.items():
            tr = train.dropna(subset=cols)
            beta, s2 = _ols(tr[cols].to_numpy(), tr["log_target"].to_numpy())
            pred = beta[0] + test[cols].to_numpy() @ beta[1:]
            rec[name] = np.exp(2 * (pred + s2 / 2))             # annualised variance forecast
        rec["train_rows"] = len(train)
        rec["realized"] = test["target_var"]
        out.append(rec)
    return pd.concat(out)


def qlike(realized: np.ndarray, forecast: np.ndarray) -> np.ndarray:
    ratio = realized / forecast
    return ratio - np.log(ratio) - 1.0


def diebold_mariano(loss_a: np.ndarray, loss_b: np.ndarray, lags: int = DM_LAGS) -> tuple[float, float]:
    """H1: model A has lower loss than B. Returns (t, one-sided p). Newey-West (Bartlett) long-run variance."""
    d = np.asarray(loss_a) - np.asarray(loss_b)
    n = len(d)
    u = d - d.mean()
    lrv = u @ u / n
    for k in range(1, lags + 1):
        lrv += 2 * (1 - k / (lags + 1)) * (u[k:] @ u[:-k]) / n
    t = d.mean() / np.sqrt(lrv / n)
    return float(t), float(norm.cdf(t))


@dataclass
class PeriodResult:
    label: str
    n: int
    qlike: dict[str, float]
    dm_m2_vs_m0: tuple[float, float]
    dm_m2_vs_m1: tuple[float, float]


def evaluate(fc: pd.DataFrame, start: str, end: str | None, label: str) -> PeriodResult:
    idx = pd.to_datetime(pd.Index(fc.index))
    sel = fc[(idx >= pd.Timestamp(start)) & ((idx <= pd.Timestamp(end)) if end else True)].dropna(subset=["realized"])
    losses = {m: qlike(sel["realized"].to_numpy(), sel[m].to_numpy()) for m in MODELS}
    return PeriodResult(label, len(sel), {m: float(v.mean()) for m, v in losses.items()},
                        diebold_mariano(losses["M2"], losses["M0"]), diebold_mariano(losses["M2"], losses["M1"]))


# --- Loading and the report --------------------------------------------------------------------

def load_inputs(history: sqlite3.Connection, vol: sqlite3.Connection) -> tuple[pd.DataFrame, pd.Series, set[date]]:
    minutes = pd.read_sql_query("SELECT ts, open, close FROM minute_bars WHERE symbol='SPY' ORDER BY ts", history)
    vix = pd.read_sql_query("SELECT day, close FROM index_daily WHERE symbol='VIX'", vol)
    vix_s = pd.Series(vix["close"].to_numpy(), index=[date.fromisoformat(d) for d in vix["day"]])
    events = {date.fromisoformat(d) for (d,) in vol.execute(
        "SELECT day FROM events WHERE kind IN ('scheduled','cancelled') AND event IN ('FOMC','CPI')")}
    return minutes, vix_s, events


def store_forecasts(vol: sqlite3.Connection, fc: pd.DataFrame) -> None:
    vol.execute("""CREATE TABLE IF NOT EXISTS vol_forecasts (
        day TEXT PRIMARY KEY, m0_var REAL, m1_var REAL, m2_var REAL, realized_var REAL, train_rows INTEGER)""")
    vol.executemany("INSERT OR REPLACE INTO vol_forecasts VALUES (?,?,?,?,?,?)",
                    [(str(d), r.M0, r.M1, r.M2, None if pd.isna(r.realized) else float(r.realized), int(r.train_rows))
                     for d, r in fc.iterrows()])
    vol.commit()


def _full_sample_coefficients(feat: pd.DataFrame, end: str) -> dict[str, float]:
    tr = feat[(pd.to_datetime(feat["target_end"]) <= pd.Timestamp(end))].dropna(subset=MODELS["M2"] + ["log_target"])
    beta, _ = _ols(tr[MODELS["M2"]].to_numpy(), tr["log_target"].to_numpy())
    return dict(zip(["intercept"] + MODELS["M2"], beta))


def report(daily: pd.DataFrame, feat: pd.DataFrame, fc: pd.DataFrame, today: date) -> tuple[str, bool]:
    disc = evaluate(fc, *DISCOVERY, "Discovery (forecasts 2018–2021)")
    conf = evaluate(fc, CONFIRMATION_START, None, "Confirmation (2022 on, information only)")
    passed = disc.qlike["M2"] < disc.qlike["M0"] and disc.dm_m2_vs_m0[1] < 0.05
    L = [f"# Volatility Forecast Report ({today.isoformat()})", "",
         "Phase 1b of the volatility strategy. Pre-registered in `docs/research/iron_condor_preregistration.md` "
         "(Amendment 1, F1.2). No option prices are used here.", "",
         f"**Model bar: {'PASSED' if passed else 'FAILED'}.** "
         + ("The full model (M2) predicts SPY's next-month volatility measurably better than VIX alone, on data it "
            "was never fitted to. The straddle and the condor forecast gate stay in the test."
            if passed else
            "The full model (M2) did not beat VIX alone by enough. The forecast adds nothing beyond what option "
            "prices already contain, so the straddle and the condor gate are no-go; the plain condor test continues."),
         "",
         "## What was measured", "",
         f"- SPY realized volatility from {len(daily):,} trading days of 1-minute bars "
         f"({daily.index[0]} to {daily.index[-1]}): 5-minute returns in regular hours plus the overnight move.",
         "- Each month the models were refitted using only past data whose outcome was already known, then used to "
         "forecast every day of that month. M0 = VIX alone; M1 = recent realized volatility (HAR-RV); "
         "M2 = M1 + VIX + down-day size + upcoming FOMC/CPI count.",
         "- Score: QLIKE loss (lower is better; the standard way to score variance forecasts). Test: one-sided "
         "Diebold-Mariano, Newey-West with 21 lags (forecast windows overlap).", ""]
    for res in (disc, conf):
        L += [f"## {res.label}: {res.n:,} daily forecasts", "",
              "| Model | Mean QLIKE loss |", "|---|---|"]
        L += [f"| {m} | {res.qlike[m]:.4f} |" for m in MODELS]
        L += ["", f"- M2 vs M0 (VIX): t = {res.dm_m2_vs_m0[0]:.2f}, one-sided p = {res.dm_m2_vs_m0[1]:.4f}",
              f"- M2 vs M1 (HAR-RV): t = {res.dm_m2_vs_m1[0]:.2f}, one-sided p = {res.dm_m2_vs_m1[1]:.4f}", ""]
    coefs = _full_sample_coefficients(feat, DISCOVERY[1])
    L += ["## M2 coefficients (fitted on discovery data, for reading only)", "",
          "| Input | Coefficient |", "|---|---|"] + [f"| {k} | {v:+.3f} |" for k, v in coefs.items()]
    L += ["", "## Caveats", "",
          "- QLIKE scores variance forecasts. Beating VIX on QLIKE means better volatility *forecasts*; it doesn't by "
          "itself mean trades make money, which the backtest (Phases 2–3) decides.",
          "- VIX is a 30-calendar-day S&P 500 measure and the target is 21 SPY trading days; M0 is fitted by "
          "regression, so the level difference (VIX usually sits above realized volatility) is accounted for.",
          "- Confirmation-period numbers are reported for information and can't change the model (pre-registration).",
          ""]
    return "\n".join(L), passed


def run(history: sqlite3.Connection, vol: sqlite3.Connection, out_dir: Path, today: date) -> tuple[Path, bool]:
    minutes, vix, events = load_inputs(history, vol)
    daily = daily_variance(minutes)
    feat = build_features(daily, vix, events)
    fc = oos_forecasts(feat)
    store_forecasts(vol, fc)
    text, passed = report(daily, feat, fc, today)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"vol_forecast_report_{today.isoformat()}.md"
    path.write_text(text)
    return path, passed
