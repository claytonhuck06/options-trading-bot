"""Pricing-residual research report for one collected trading day.

Reprices the day at regular intervals (each snapshot sees only data received
by then), then asks: when the model said a quote was mispriced, would
trading it have paid after crossing the spread and removing the move in SPY?

Writes docs/research/pricing_report_<date>.md and charts in docs/research/img/.
"""

from __future__ import annotations

import math
import statistics as st
from collections import Counter
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from core.config import PROJECT_ROOT, Settings  # noqa: E402
from data.storage.db import MarketDataStore  # noqa: E402
from pricing.engine import ContractResult, PricingResult, price_snapshot  # noqa: E402
from pricing.rates import risk_free_rate  # noqa: E402
from pricing.snapshot import load_snapshot  # noqa: E402

EASTERN = ZoneInfo("America/New_York")
HORIZONS_MIN = (5, 15)
Z_SIGNAL = 2.0


@dataclass
class Outcome:
    side: str
    z: float
    edge: float
    horizon: int
    pnl_mark: float      # delta-hedged, entry at executable price, marked at later mid
    pnl_exit: float      # delta-hedged, entry and exit both at executable prices


def _times(store: MarketDataStore, underlying: str, day: date, every_min: int) -> list[datetime]:
    start = datetime(day.year, day.month, day.day, 9, 35, tzinfo=EASTERN)
    end = datetime(day.year, day.month, day.day, 16, 0, tzinfo=EASTERN)
    rows = store.conn.execute(
        "SELECT received_at FROM underlying_quotes WHERE symbol=? AND received_at>=? AND received_at<=? ORDER BY received_at",
        (underlying, start.astimezone(timezone.utc).isoformat(), end.astimezone(timezone.utc).isoformat()),
    ).fetchall()
    out, last = [], None
    for (ts,) in rows:
        t = datetime.fromisoformat(ts)
        if last is None or t - last >= timedelta(minutes=every_min):
            out.append(t)
            last = t
    return out


def _run(settings: Settings, store: MarketDataStore, underlying: str, t: datetime) -> PricingResult | None:
    try:
        snap = load_snapshot(store, underlying, t, settings.pricing.snapshot_max_quote_age_seconds)
    except LookupError:
        return None

    def rate_for(years):
        info = risk_free_rate(settings, store, t, years)
        return info.rate, info.source

    return price_snapshot(snap, settings.pricing, rate_for)


def _later_quote(store: MarketDataStore, symbol: str, t: datetime, max_age: float):
    row = store.conn.execute(
        """SELECT bid, ask FROM option_quotes WHERE symbol=? AND received_at<=? AND last_seen_at>=? AND is_usable=1
           ORDER BY received_at DESC LIMIT 1""",
        (symbol, t.astimezone(timezone.utc).isoformat(), (t - timedelta(seconds=max_age)).astimezone(timezone.utc).isoformat()),
    ).fetchone()
    return (row[0], row[1]) if row else None


def _spot_at(store: MarketDataStore, underlying: str, t: datetime) -> float | None:
    row = store.conn.execute(
        "SELECT bid, ask, last_price FROM underlying_quotes WHERE symbol=? AND received_at<=? ORDER BY received_at DESC LIMIT 1",
        (underlying, t.astimezone(timezone.utc).isoformat()),
    ).fetchone()
    if not row:
        return None
    return (row[0] + row[1]) / 2 if row[0] and row[1] else row[2]


def _outcomes(store, underlying, result: PricingResult, max_age: float, signals_only: bool, data_end: datetime) -> list[Outcome]:
    out = []
    t0, s0 = result.snapshot.as_of, result.snapshot.spot
    for c in result.contracts:
        if not c.reliable:
            continue
        side, z, edge = ("buy", c.buy_z, c.buy_edge) if c.buy_z >= c.sell_z else ("sell", c.sell_z, c.sell_edge)
        if signals_only and z < Z_SIGNAL:
            continue
        for h in HORIZONS_MIN:
            t1 = t0 + timedelta(minutes=h)
            q1, s1 = _later_quote(store, c.option.symbol, t1, max_age), _spot_at(store, underlying, t1)
            if not q1 or s1 is None or t1 > data_end:
                continue
            hedge = c.delta * (s1 - s0)
            mid1 = (q1[0] + q1[1]) / 2
            if side == "buy":
                out.append(Outcome(side, z, edge, h, mid1 - c.option.ask - hedge, q1[0] - c.option.ask - hedge))
            else:
                out.append(Outcome(side, z, edge, h, c.option.bid - mid1 + hedge, c.option.bid - q1[1] + hedge))
    return out


def _fmt_money(x: float) -> str:
    return f"{'-' if x < 0 else '+'}${abs(x) * 100:,.2f}"


def _summ(outcomes: list[Outcome], h: int, field: str) -> tuple[int, float, float, float]:
    xs = [getattr(o, field) for o in outcomes if o.horizon == h]
    if not xs:
        return 0, float("nan"), float("nan"), float("nan")
    return len(xs), st.mean(xs), st.median(xs), sum(x > 0 for x in xs) / len(xs)


def _smile_chart(e, path: Path) -> None:
    rows = [c for c in e.contracts if c.iv_mid is not None and c.log_moneyness is not None]
    fig, ax = plt.subplots(figsize=(8, 4.2))
    for c in rows:
        lo = c.iv_bid if c.iv_bid is not None else c.iv_mid
        hi = c.iv_ask if c.iv_ask is not None else c.iv_mid
        color = "#2b6cb0" if c.reliable else "#c53030"
        ax.plot([c.log_moneyness, c.log_moneyness], [lo * 100, hi * 100], color=color, alpha=0.55, lw=1.4)
    ks = [e.fit.k_min + i * (e.fit.k_max - e.fit.k_min) / 100 for i in range(101)]
    ax.plot(ks, [e.fit.iv_at(k) * 100 for k in ks], color="#1a202c", lw=2, label="fitted smile")
    ax.plot([], [], color="#2b6cb0", label="bid–ask IV, usable")
    ax.plot([], [], color="#c53030", label="bid–ask IV, flagged")
    ax.set_xlabel("log-moneyness  ln(K / forward)")
    ax.set_ylabel("implied volatility (%)")
    ax.set_title(f"SPY {e.expiration} smile ({e.years * 365:.0f} days)")
    ax.set_ylim(0, min(60, max((c.iv_ask or c.iv_mid) * 100 for c in rows) + 3))
    ax.legend(loc="upper right", fontsize=8)
    ax.grid(alpha=0.25)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def _z_chart(zs: list[float], path: Path) -> None:
    fig, ax = plt.subplots(figsize=(8, 3.6))
    ax.hist([max(min(z, 6), -12) for z in zs], bins=72, color="#4a5568")
    ax.axvline(Z_SIGNAL, color="#c53030", ls="--", lw=1.2, label=f"z = {Z_SIGNAL:.0f} (apparent signal)")
    ax.axvline(0, color="#1a202c", lw=0.8)
    ax.set_xlabel("best-side edge ÷ model uncertainty (z)   — clipped to [-12, 6]")
    ax.set_ylabel("contract-snapshots")
    ax.set_title("Apparent edge vs. executable prices, all usable contracts")
    ax.legend(fontsize=8)
    ax.grid(alpha=0.25)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def build_report(settings: Settings, store: MarketDataStore, day: date, every_min: int = 5, out_dir: Path | None = None) -> Path:
    underlying = settings.market_data.primary_underlying
    out_dir = out_dir or PROJECT_ROOT / "docs" / "research"
    img_dir = out_dir / "img"
    img_dir.mkdir(parents=True, exist_ok=True)

    times = _times(store, underlying, day, every_min)
    if not times:
        raise LookupError(f"no {underlying} data collected on {day}")
    results = [r for r in (_run(settings, store, underlying, t) for t in times) if r]
    max_age = settings.pricing.snapshot_max_quote_age_seconds

    all_c: list[ContractResult] = [c for r in results for c in r.contracts]
    reliable = [c for c in all_c if c.reliable]
    flags = Counter(f for c in all_c for f in c.flags)
    best_z = [max(c.buy_z, c.sell_z) for c in reliable]
    raw_pos = sum(max(c.buy_edge, c.sell_edge) > 0 for c in reliable)
    sig = sum(z >= Z_SIGNAL for z in best_z)

    data_end = times[-1]
    signal_out, base_out = [], []
    for r in results:
        signal_out += _outcomes(store, underlying, r, max_age, True, data_end)
    for r in results[:: max(1, len(results) // 6)]:           # a sample of snapshots for the baseline
        base_out += _outcomes(store, underlying, r, max_age, False, data_end)

    last = results[-1]
    fits = [e for e in last.expiries if e.fit and e.fit.status == "ok"]
    half_spread_iv = [((c.iv_ask - c.iv_bid) / 2) for c in last.contracts
                      if c.reliable and c.iv_ask is not None and c.iv_bid is not None]
    med_hs = st.median(half_spread_iv) if half_spread_iv else float("nan")
    inside = [abs(c.iv_mid - c.fair_iv) <= (c.iv_ask - c.iv_bid) / 2 for c in reliable
              if c.iv_ask is not None and c.iv_bid is not None and c.fair_iv is not None]
    share_inside = sum(inside) / len(inside) if inside else float("nan")

    chart_exps = sorted(fits, key=lambda e: e.years)
    chosen = [chart_exps[0], chart_exps[-1]] if len(chart_exps) > 1 else chart_exps
    smile_files = []
    for e in chosen:
        name = f"smile_{day}_{e.expiration}.png"
        _smile_chart(e, img_dir / name)
        smile_files.append(name)
    z_name = f"zscores_{day}.png"
    _z_chart(best_z, img_dir / z_name)

    first_t = results[0].snapshot.as_of.astimezone(EASTERN).strftime("%H:%M")
    last_t = last.snapshot.as_of.astimezone(EASTERN).strftime("%H:%M")
    feed = store.conn.execute("SELECT feed FROM collection_cycles ORDER BY id DESC LIMIT 1").fetchone()[0]

    L: list[str] = []
    L += [f"# SPY pricing residuals — {day}", ""]
    L += [f"Research output from `main.py report`. {len(results)} snapshots, every {every_min} min from {first_t} to {last_t} ET. "
          f"Options feed: **{feed}**. Each snapshot uses only data received by its own timestamp.", ""]
    L += ["> **Bottom line.** " + _bottom_line(raw_pos, len(reliable), sig, signal_out), ""]

    L += ["## 1. Inputs the model derived", ""]
    L += [f"Risk-free rate: {last.rate_source} (FRED, previous day's published yield). "
          "Forward and dividend yield implied from put-call parity near the money.", ""]
    L += ["| Expiry | Days | Forward | Implied div. yield | Forward noise | ATM IV | Fit error | Points (dropped) |",
          "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for e in last.expiries:
        if e.fit and e.fit.status == "ok":
            L.append(f"| {e.expiration} | {e.years * 365:.1f} | {e.forward.forward:.2f} | {e.forward.implied_dividend_yield * 100:.2f}% "
                     f"| ±{(e.forward.dispersion or 0) / 2:.2f} | {e.fit.atm_iv * 100:.2f}% | {e.fit.rmse_iv * 100:.2f} vol pts "
                     f"| {e.fit.points_used} ({e.fit.points_excluded}) |")
    L += ["", f"Spot at last snapshot: {last.snapshot.spot:.2f}. A slightly negative implied dividend yield is expected here: "
          "no SPY ex-dividend date falls inside these expiries, and the market's implied financing rate typically sits a little "
          "above Treasury bills.", ""]
    L += [f"Median half bid-ask spread of usable contracts, in vol terms: **{med_hs * 100:.2f} vol points**. "
          f"Across all usable contract-snapshots, the fitted fair value fell inside the quote's own bid–ask range "
          f"(in vol terms) **{share_inside:.0%}** of the time.", ""]
    for f in smile_files:
        L += [f"![smile](img/{f})", ""]

    L += _stability_section(results)

    L += ["## 2. How much of the data can be trusted", ""]
    L += [f"{len(all_c):,} contract-snapshots priced; **{len(reliable):,} ({len(reliable) / len(all_c):.0%})** carried no warning flag.", ""]
    L += ["| Flag | Count | Meaning |", "|---|---:|---|"]
    meaning = {
        "butterfly": "three strikes whose quotes allow a riskless profit, so at least one is wrong",
        "monotonic": "a higher-strike call (lower-strike put) quoted above its neighbour",
        "vertical": "a vertical spread quoted for more than its maximum payoff",
        "parity": "call and put at the same strike inconsistent with the forward",
        "early_exercise": "in the money enough that American early exercise matters; Black-Scholes unreliable",
        "extrapolated": "outside the strike range the smile was fitted on",
        "low_price": "quoted under the minimum price; tick size dominates its implied vol",
        "smile_outlier": "too far from the smile to be used in fitting it",
        "no_iv": "no volatility reproduces the price (e.g. below intrinsic value)",
        "no_fit": "too few clean quotes in this expiry to fit a smile",
        "no_forward": "no clean call/put pairs to imply a forward",
    }
    for f, n in flags.most_common():
        L.append(f"| `{f}` | {n:,} | {meaning.get(f, '')} |")
    L += ["", "The no-arbitrage flags (`butterfly`, `monotonic`, `vertical`, `parity`) are evidence about the data feed itself: "
          "real executable exchange quotes almost never allow riskless profits. The indicative feed does, regularly.", ""]

    L += ["## 3. Apparent discrepancies vs. what survives", ""]
    L += [f"* Usable contract-snapshots where the model's fair value sits **outside** the bid–ask (buying at the ask or selling at "
          f"the bid looks profitable on paper): **{raw_pos:,} of {len(reliable):,} ({raw_pos / max(len(reliable), 1):.0%})**.",
          f"* Of those, edges larger than {Z_SIGNAL:.0f}× the model's own uncertainty: **{sig:,} ({sig / max(len(reliable), 1):.1%})**.", ""]
    L += [f"![z-scores](img/{z_name})", ""]

    L += ["## 4. Did the apparent edges pay?", ""]
    L += [f"For every usable contract with z ≥ {Z_SIGNAL:.0f}: enter at the executable price (ask to buy, bid to sell), hedge the "
          "SPY move with the option's delta, then look 5 and 15 minutes later. **Marked** values the position at the later mid; "
          "**exit** closes it at the later executable price. Per-contract dollars (×100). Commissions not included.", ""]
    L += ["| Group | Horizon | Trades | Mean (mark) | Mean (exit) | Median (exit) | Share profitable (exit) |", "|---|---:|---:|---:|---:|---:|---:|"]
    for label, outs in (("z ≥ 2 signals", signal_out), ("all usable (baseline)", base_out)):
        for h in HORIZONS_MIN:
            n, m_mark, _, _ = _summ(outs, h, "pnl_mark")
            _, m_exit, med_exit, win = _summ(outs, h, "pnl_exit")
            if n:
                L.append(f"| {label} | {h} min | {n:,} | {_fmt_money(m_mark)} | {_fmt_money(m_exit)} | {_fmt_money(med_exit)} | {win:.0%} |")
            else:
                L.append(f"| {label} | {h} min | 0 | – | – | – | – |")
    L += ["", f"Signal sample size: {len({(o.z, o.edge) for o in signal_out}):,} distinct signals from one day — far too few to "
          "conclude anything about profitability. This table is here to establish the method, not a result.", ""]

    L += ["## 5. Why an apparent discrepancy may not be a profitable opportunity", ""]
    L += _why_section(flags, len(all_c), med_hs, share_inside)

    L += ["## 6. Known limitations of this engine", "",
          "* **European model, American options.** Black-Scholes ignores early exercise. Mitigated by fitting only out-of-the-money "
          "options and adding a binomial early-exercise premium for in-the-money ones, but the binomial model treats SPY's quarterly "
          "dividend as a continuous yield, which understates call early exercise just before an ex-dividend date.",
          "* **Calendar time.** Time to expiry counts weekends and holidays fully; real variance concentrates in trading hours, so "
          "short-dated vols on Fridays and before holidays are distorted.",
          "* **Per-expiry smiles.** Each expiry is fitted on its own; there is no calendar-arbitrage check across expiries yet.",
          "* **Polynomial smile.** A cubic in log-moneyness is fine near the money but has thin tails; that is why far-out-of-the-money "
          "contracts are flagged `extrapolated` or `low_price` rather than priced with confidence.",
          "* **Uncertainty is approximate.** Smile prediction error, a 0.25 vol-point model-error floor and forward noise are combined; "
          "it is a reasonable yardstick, not a calibrated probability.",
          f"* **Data feed.** Results use the `{feed}` feed, not consolidated OPRA quotes.",
          "* **Spot price.** SPY's price comes from the free IEX feed (one exchange), not the consolidated market. In fast markets "
          "and around the 4:00 p.m. close it can lag the options, which shows up as a jump in the implied dividend yield.", ""]
    path = out_dir / f"pricing_report_{day}.md"
    path.write_text("\n".join(L))
    return path


def _bottom_line(raw_pos: int, n: int, sig: int, signal_out: list[Outcome]) -> str:
    s = (f"On paper, {raw_pos / max(n, 1):.0%} of usable quotes look mispriced against executable prices. After allowing for model "
         f"uncertainty, {sig / max(n, 1):.1%} remain. ")
    _, m_exit, _, win = _summ(signal_out, 15, "pnl_exit")
    if not math.isnan(m_exit):
        s += (f"Those remaining signals, traded at executable prices and hedged, averaged {_fmt_money(m_exit)} per contract after "
              f"15 minutes ({win:.0%} profitable) before commissions. ")
    s += "One day of indicative data cannot show an edge; it shows the method works and where the traps are."
    return s


def _why_section(flags: Counter, total: int, med_hs: float, share_inside: float) -> list[str]:
    bad_quotes = sum(flags[f] for f in ("butterfly", "monotonic", "vertical", "parity"))
    return [
        f"1. **The quote is wrong, not the market.** {bad_quotes:,} flags in this run came from quotes that break no-arbitrage "
        "rules. A model compared against a bad quote will always find an 'opportunity'.",
        "2. **The spread eats it.** The model value must clear the *ask* to buy or the *bid* to sell, and closing costs the spread "
        f"again. A typical half-spread here is about {med_hs * 100:.2f} vol points, and the model's fair value sat inside the "
        f"quoted bid–ask {share_inside:.0%} of the time.",
        "3. **The model is wrong.** A smooth smile is an assumption. Systematic shapes it misses (wings, skew kinks, event days) "
        "show up as persistent 'mispricings' that never converge.",
        "4. **Early exercise and dividends.** In-the-money American options are worth more than Black-Scholes says; ignoring that "
        "makes them look rich.",
        "5. **It may never converge in time.** A relative mispricing can persist to expiry. The holding period here is minutes to "
        "hours, so only discrepancies that close quickly matter.",
        "6. **Hidden directional risk.** Without a delta hedge, P&L is dominated by SPY's own move, not the mispricing.",
        "7. **Costs.** Alpaca advertises commission-free options, but per-contract regulatory and clearing fees still apply, and "
        "many other brokers charge around $0.65 per contract. None of that is in these numbers; on small edges it matters.",
        "8. **Selection bias.** Scanning ~1,500 contracts every 15 seconds guarantees some extreme z-scores by chance alone.",
        "",
    ]


def _stability_section(results: list[PricingResult]) -> list[str]:
    """How much each expiry's at-the-money vol and skew move between consecutive snapshots."""
    series: dict = {}
    for r in results:
        for e in r.expiries:
            if e.fit and e.fit.status == "ok":
                series.setdefault(e.expiration, []).append((e.fit.iv_at(0.0), e.fit.coefficients[1], e.fit.rmse_iv))
    L = ["### Stability through the day", "",
         "Change between consecutive snapshots. Real volatility moves with the market, so some change is expected; "
         "jumps far larger than the bid–ask width would mean the fit, not the market, is moving.", "",
         "| Expiry | Snapshots | ATM IV range | Median Δ ATM IV | Max Δ ATM IV | Median Δ skew slope | Fit error range |",
         "|---|---:|---:|---:|---:|---:|---:|"]
    for exp in sorted(series):
        pts = series[exp]
        atm = [p[0] for p in pts]
        d_atm = [abs(b - a) for a, b in zip(atm, atm[1:])]
        d_skew = [abs(b[1] - a[1]) for a, b in zip(pts, pts[1:])]
        rm = [p[2] for p in pts]
        if d_atm:
            L.append(f"| {exp} | {len(pts)} | {min(atm) * 100:.2f}–{max(atm) * 100:.2f}% | {st.median(d_atm) * 100:.2f} pts "
                     f"| {max(d_atm) * 100:.2f} pts | {st.median(d_skew):.3f} | {min(rm) * 100:.2f}–{max(rm) * 100:.2f} pts |")
    cal = [(r.snapshot.as_of, v) for r in results for v in r.calendar_violations()]
    L += ["", f"Cross-expiry check: at-the-money total variance fell with maturity (a term-structure inconsistency) in "
          f"**{len(cal)}** expiry pair(s) across {len(results)} snapshots."]
    if cal:
        pairs = Counter(v for _, v in cal).most_common(5)
        L.append("Most frequent: " + ", ".join(f"{a} → {b} ({n}×)" for (a, b), n in pairs) + ".")
    L.append("")
    return L
