"""options-quant entry point.

Commands:
    python main.py config   Show the validated configuration and today's dollar limits.
    python main.py check    Also connect to Alpaca (read-only) and report account status.
    python main.py collect  Collect SPY options quotes into SQLite (market hours only).
        --once              Run one cycle (works when the market is closed, for testing).
        --cycles N          Stop after N cycles.
        --wait-for-open     If started before the open, wait for it (used by the daily schedule).
    python main.py price    Price the latest stored snapshot (or --as-of TIME) and save the results.
    python main.py report   Research report on pricing residuals for --date (default: latest day).
    python main.py scan     Price the latest snapshot and list candidate spreads with reasons. No orders.
        --any-time          Ignore the entry-time window (for research outside 9:35-15:00 ET).
    python main.py scan-report  Replay the scanner over --date and check candidates' later value.
    python main.py history  Download historical SPY one-minute bars (free, back to 2016).
    python main.py risk-replay  Recalibrate stress scenarios and replay the risk controls over history.
    python main.py killswitch status|stop|ack-warning|reset   Inspect or operate the kill switch.
        reset needs --authorization "<exact phrase>"; ack-warning OKs trading today during a drawdown warning.
    python main.py trade    Paper-trade until the close (needs the collector running). PAPER account only.
    python main.py paper-demo   One-time end-to-end order test on the paper account (market hours).
    python main.py opra-history --weeks N   Download N most recent weeks of SPY option bid/ask (Databento, budget-capped).
    python main.py databento-spend          Show Databento spending against the budget.
    python main.py opra-widen --from D --to D --band 0.06 --run-budget 20
                                            Add further-out strikes to already-downloaded days (budget-capped).
    python main.py vol-study                Intraday volatility-pattern study on the downloaded quote history.
    python main.py backtest --from D --to D Backtest on downloaded real quotes; standard report in docs/research/.
        --walk-forward TRAIN,TEST           Walk-forward test (window sizes in trading days).
        --grid strategy.KEY=V1,V2,...        Candidate settings the walk-forward chooses between.
    python main.py overnight [refresh|fetch|liquid]  Overnight-drift stock screener + walk-forward test (free daily bars).
    python main.py overnight-basket pick|buy|sell|status   Paper overnight share basket (buy at close, sell at open).
    python main.py gap-puts [fetch|sample]  Puts on unexplained 30%+ overnight gaps (free bars, news, option trades).
    python main.py spike-short-v2       Webull-checklist version: +75% dead-company gaps, intraday peak entry.
    python main.py spike-short-v3       Same stocks, short at the open and cover at the close.
    python main.py spike-short-v4       Enhanced: 50% squeeze stop, 1.5%-risk sizing, IBKR locate/borrow costs.
    python main.py spike-short-shadow morning|cover|close|status|ibkr-check [--dry-run]   Forward shadow log of the day-trade-only
                                        checklist short: logs signals + IBKR borrow, simulated exit. No orders.
    python main.py spike-short [fetch]  Short-the-small-stock-spike backtest, shortable stocks only (free daily bars).
    python main.py momentum plan|rebalance|resize|status   Paper dual momentum (top 2 of 5 ETFs, monthly).
    python main.py after-close              Daily summary pushed to the phone, then storage cleanup (scheduled).
    python main.py storage status|cleanup|restore [--date D]   Live-data storage: sizes, cleanup now, restore a day.
    python main.py notify --title T --message M [--priority urgent]   Send a phone alert (used by the scripts).

Phase 1 cannot submit orders: there is no order-submission code in the project.
"""

from __future__ import annotations

import argparse
import sys

from core.config import ConfigError, load_settings
from core.instance_lock import AlreadyRunning, is_running, single_instance
from core.logging_setup import get_logger, setup_logging
from core.secrets import load_alpaca_credentials
from risk.limits import compute_daily_budget


def _print_budget(title: str, budget) -> None:
    print(f"\n{title}")
    print(f"  Start-of-day equity        ${budget.start_of_day_equity:,.2f}")
    print(f"  Max risk per trade         ${budget.max_risk_per_trade:,.2f}")
    print(f"  Max aggregate planned risk ${budget.max_aggregate_planned_risk:,.2f}")
    print(f"  Daily loss trigger        -${budget.daily_loss_trigger:,.2f}")
    print(f"  Daily profit ceiling      +${budget.daily_profit_ceiling:,.2f}")
    print(f"  Drawdown warning at        ${budget.drawdown_warning_equity:,.2f}")
    print(f"  Drawdown shutdown at       ${budget.drawdown_shutdown_equity:,.2f}")
    print(f"  Max concurrent positions   {budget.max_concurrent_positions}")


def cmd_config(settings) -> int:
    print(f"Environment:        {settings.environment.value}")
    print(f"Broker account:     {settings.broker.account_type}")
    print(f"Trading enabled:    {settings.trading.enabled}")
    print(f"Can submit orders:  {settings.can_submit_orders}")
    print(f"Underlying:         {settings.market_data.primary_underlying} "
          f"({settings.market_data.min_days_to_expiry}-{settings.market_data.max_days_to_expiry} DTE)")
    print(f"Data feeds:         stock={settings.market_data.stock_feed}, options={settings.market_data.option_feed}")
    _print_budget("Risk limits at reference equity:", compute_daily_budget(settings.risk, settings.risk.reference_equity))
    return 0


def cmd_check(settings, log) -> int:
    from execution.broker import ReadOnlyBroker  # imported here so `config` works without network deps

    cmd_config(settings)
    creds = load_alpaca_credentials(settings)
    log.info("credentials loaded: %r", creds)
    broker = ReadOnlyBroker(settings, creds)

    acct = broker.account_summary()
    print(f"\nAlpaca ({'PAPER' if broker.is_paper else 'LIVE'}) — read-only connection OK")
    print(f"  Account status             {acct.status}")
    print(f"  Equity                     ${acct.equity:,.2f}")
    print(f"  Buying power               ${acct.buying_power:,.2f}")
    print(f"  Options approved level     {acct.options_approved_level}")
    print(f"  Options trading level      {acct.options_trading_level}")
    print(f"  Trading blocked            {acct.trading_blocked}")
    print(f"  Open positions             {len(broker.positions())}")
    print(f"  Open orders                {len(broker.open_orders())}")

    clock = broker.market_clock()
    print(f"  Market open now            {clock['is_open']} (next open {clock['next_open']})")
    trade = broker.latest_underlying_trade(settings.market_data.primary_underlying)
    print(f"  {trade['symbol']} last trade             ${trade['price']:,.2f} at {trade['timestamp']} ({trade['feed']} feed)")

    _print_budget("Today's limits from live account equity:", compute_daily_budget(settings.risk, acct.last_equity or acct.equity))
    log.info("read-only check complete", extra={"event": "startup_check_ok"})
    return 0


def cmd_collect(settings, log, once: bool, cycles: int | None, wait: bool = False) -> int:
    from core.config import PROJECT_ROOT
    from data.collectors.alpaca_source import AlpacaMarketSource
    from data.collectors.options_collector import OptionsCollector, wait_for_open
    from data.storage.db import MarketDataStore

    creds = load_alpaca_credentials(settings)
    source = AlpacaMarketSource(settings, creds)
    store = MarketDataStore(PROJECT_ROOT / settings.storage.sqlite_path)
    collector = OptionsCollector(settings, source, store)
    print(f"Collecting {collector.underlying} options (feed={source.option_feed}) into {settings.storage.sqlite_path}")
    if source.option_feed == "indicative":
        print("NOTE: indicative quotes are not actual OPRA exchange quotes; do not treat them as executable prices.")
    try:
        if once:
            r = collector.collect_once()
            print(f"Cycle {r.cycle_id}: {r.received} quotes, {r.new} new, {r.repeat} repeats, {r.unusable} unusable")
            print(f"Quality flags: {r.flag_counts}")
        else:
            if wait and not wait_for_open(source.broker.market_clock, store=store):
                print("Market is not opening today — exiting.")
                return 0
            n = collector.run(lambda: source.broker.market_clock()["is_open"], max_cycles=cycles)
            print(f"Completed {n} cycles")
    except KeyboardInterrupt:
        store.log_event("INFO", "collector_stopped", {"reason": "keyboard_interrupt"})
        print("Stopped.")
    finally:
        store.close()
    return 0


def _price_latest(settings, log, store, as_of_text: str | None):
    from datetime import datetime

    from pricing.engine import price_snapshot
    from pricing.persistence import save_pricing
    from pricing.rates import risk_free_rate, update_rates
    from pricing.snapshot import latest_receipt_time, load_snapshot

    try:
        update_rates(settings, store)
    except Exception as exc:  # offline is fine: stored or fallback rates are used
        log.warning("could not refresh rates from FRED: %s", exc)
    underlying = settings.market_data.primary_underlying
    if as_of_text:
        as_of = datetime.fromisoformat(as_of_text)
        if as_of.tzinfo is None:
            raise ConfigError("--as-of needs a timezone, e.g. 2026-10-19T15:00:00-04:00")
    else:
        as_of = latest_receipt_time(store, underlying)
        if as_of is None:
            return None, None
    snap = load_snapshot(store, underlying, as_of, settings.pricing.snapshot_max_quote_age_seconds)

    def rate_for(years):
        info = risk_free_rate(settings, store, as_of, years)
        return info.rate, info.source

    result = price_snapshot(snap, settings.pricing, rate_for)
    return result, save_pricing(store, result)


def cmd_scan(settings, log, as_of_text: str | None, any_time: bool) -> int:
    from collections import Counter

    from core.config import PROJECT_ROOT
    from data.storage.db import MarketDataStore
    from pricing.persistence import save_candidates
    from risk.limits import compute_daily_budget
    from strategies.relative_value import scan

    store = MarketDataStore(PROJECT_ROOT / settings.storage.sqlite_path)
    try:
        result, run_id = _price_latest(settings, log, store, as_of_text)
        if result is None:
            print("No collected data yet — run `main.py collect` first.")
            return 1
        # Phase 4 sizes against the configured reference equity; live account equity arrives with the risk engine.
        budget = compute_daily_budget(settings.risk, settings.risk.reference_equity)
        cands = scan(result, settings, budget, enforce_window=not any_time)
        save_candidates(store, run_id, cands)
    finally:
        store.close()

    eligible = [c for c in cands if c.eligible]
    print(f"Scan of pricing run {run_id} ({result.snapshot.as_of.isoformat()}): {len(cands)} candidate spreads, "
          f"{len(eligible)} eligible. Max risk per trade ${budget.max_risk_per_trade:.0f}. NO ORDERS ARE PLACED.")
    reasons = Counter(r for c in cands for r in c.reasons)
    if reasons:
        print("Rejections: " + ", ".join(f"{r} {n}" for r, n in reasons.most_common()))
    shown = eligible[: settings.strategy.max_candidates_per_scan] or cands[:3]
    if not eligible and cands:
        print("\nNo eligible trades. Closest rejected candidates:")
    for c in shown:
        print()
        print(c.explain())
    return 0


def cmd_scan_report(settings, log, day_text: str | None, every: int) -> int:
    from datetime import date

    from core.config import PROJECT_ROOT
    from data.storage.db import MarketDataStore
    from pricing.snapshot import latest_receipt_time
    from research.pricing_report import EASTERN
    from research.scan_report import build_scan_report

    store = MarketDataStore(PROJECT_ROOT / settings.storage.sqlite_path)
    try:
        if day_text:
            day = date.fromisoformat(day_text)
        else:
            latest = latest_receipt_time(store, settings.market_data.primary_underlying)
            if latest is None:
                print("No collected data yet.")
                return 1
            day = latest.astimezone(EASTERN).date()
        path = build_scan_report(settings, store, day, every)
    finally:
        store.close()
    print(f"Report written: {path.relative_to(PROJECT_ROOT)}")
    return 0


def _paper_session(settings):
    from core.config import PROJECT_ROOT
    from data.storage.db import MarketDataStore
    from execution.alpaca_paper import AlpacaPaperBroker
    from execution.trader import TradingSession

    from data.collectors.alpaca_source import AlpacaMarketSource

    store = MarketDataStore(PROJECT_ROOT / settings.storage.sqlite_path)
    creds = load_alpaca_credentials(settings)
    from execution.overnight_basket import OvernightBasket, open_db as open_basket_db
    from execution.dual_momentum_paper import DualMomentumBook, open_db as open_momentum_db
    return TradingSession(settings, store, AlpacaPaperBroker(settings, creds), source=AlpacaMarketSource(settings, creds),
                          basket=OvernightBasket(open_basket_db()), momentum=DualMomentumBook(open_momentum_db())), store


def _retrying(call, wait: float = 15.0):
    """Call until it succeeds: used for read-only clock checks that must survive a network blip."""
    import time as _t

    while True:
        try:
            return call()
        except Exception as exc:
            print(f"connection problem ({type(exc).__name__}); retrying in {wait:.0f}s", flush=True)
            _t.sleep(wait)


def cmd_trade(settings, log) -> int:
    import time as _t

    from execution.broker import ReadOnlyBroker

    from datetime import datetime, timedelta

    from monitoring import alerts
    from monitoring.daily import missed_days
    from research.pricing_report import EASTERN

    session, store = _paper_session(settings)
    clock = ReadOnlyBroker(settings, load_alpaca_credentials(settings))
    try:
        rep = session.startup()
        print(f"Morning check: {rep.recon.summary()}; carried into the day: {', '.join(rep.carried) or 'nothing'}")
        today = datetime.now(EASTERN).date()
        missed = [d for d in missed_days(store.conn, clock.trading_days(today - timedelta(days=7), today - timedelta(days=1)))]
        if missed:
            alerts.urgent("Bot missed trading days", "No market data was collected on " + ", ".join(f"{d:%a %b %d}" for d in missed)
                          + ". The Mac was probably asleep or off. Today is running normally.")
        # The daily schedule starts the trader before the open so this check happens first; wait for the bell.
        while not (c := _retrying(clock.market_clock))["is_open"]:
            next_open = datetime.fromisoformat(c["next_open"]).astimezone(EASTERN)
            if next_open.date() != today:
                print("Market closed today — nothing to do.")
                return 0
            _t.sleep(min(max((next_open - datetime.now(EASTERN)).total_seconds(), 1), 60))
        # Network errors (Wi-Fi not back yet after the Mac wakes, a broker hiccup) are retried, never fatal.
        # After 3 minutes of failures the owner is alerted; on reconnect, unknown orders are settled and the
        # book is reconciled again (session.startup) before trading resumes.
        failing_since, alerted = None, False
        while True:
            try:
                if not clock.market_clock()["is_open"]:
                    break
                if failing_since is not None:
                    session.startup()
                    if alerted:
                        alerts.send("Bot reconnected", "The connection is back; positions were re-checked against the broker.")
                    failing_since, alerted = None, False
                r = session.cycle()
            except Exception as exc:
                failing_since = failing_since or _t.monotonic()
                log.warning("cycle failed (%s); retrying", type(exc).__name__)
                print(f"{datetime.now(EASTERN):%H:%M:%S} cannot reach the broker or data ({type(exc).__name__}); retrying", flush=True)
                if not alerted and _t.monotonic() - failing_since > 180:
                    alerts.urgent("Bot cannot reach the broker", f"No connection for 3 minutes ({type(exc).__name__}). "
                                  "Open positions are not being watched. It keeps retrying.")
                    alerted = True
                _t.sleep(15)
                continue
            line = f"{r.at:%H:%M:%S} {r.level}"
            if r.triggers:
                line += f" triggers={r.triggers}"
            if r.exits:
                line += f" exits={r.exits}"
            if r.entry:
                line += f" entry={r.entry}"
            if r.notes:
                line += f" notes={r.notes}"
            print(line, flush=True)
            _t.sleep(settings.market_data.poll_interval_seconds)
        print("Market closed — trading session ended.")
    finally:
        store.close()
    return 0


def cmd_paper_demo(settings, log) -> int:
    from core.config import PROJECT_ROOT
    from execution.demo import run_demo

    session, store = _paper_session(settings)
    from execution.demo import demo_passed
    try:
        path = run_demo(settings, session)
    finally:
        store.close()
    print(f"Report written: {path.relative_to(PROJECT_ROOT)}")
    return 0 if demo_passed(path) else 1


def _databento(settings):
    from core.config import PROJECT_ROOT
    from data.collectors.databento_guard import GuardedDatabento, open_ledger

    return GuardedDatabento(settings.databento, open_ledger(PROJECT_ROOT / settings.databento.history_path))


def cmd_databento_spend(settings) -> int:
    g = _databento(settings)
    s = g.summary()
    print(f"Databento spending: ${s.spent_usd:.2f} of ${s.budget_usd:.2f} budget (${s.remaining_usd:.2f} left), "
          f"{s.requests} requests. Free credit on the account: $125.")
    for schema, n, usd, gb in g.ledger.execute(
            "SELECT schema, COUNT(*), SUM(quoted_usd), SUM(billable_bytes)/1e9 FROM databento_spend GROUP BY schema"):
        print(f"  {schema:<12} {n:>5} requests  ${usd:8.2f}  {gb:6.2f} GB")
    days = g.ledger.execute("SELECT COUNT(*), MIN(trading_date), MAX(trading_date) FROM opra_days").fetchone() \
        if g.ledger.execute("SELECT name FROM sqlite_master WHERE name='opra_days'").fetchone() else (0, None, None)
    print(f"Option quote history on disk: {days[0]} trading days ({days[1]} to {days[2]})")
    return 0


def cmd_opra_history(settings, weeks: int) -> int:
    from datetime import date, timedelta

    from core.config import PROJECT_ROOT
    from data.collectors.historical import open_history
    from data.collectors.opra_history import OpraHistory, trading_days

    g = _databento(settings)
    history = open_history(PROJECT_ROOT / settings.storage.history_path)
    end = date.today() - timedelta(days=1)
    days = trading_days(history, end - timedelta(weeks=weeks), end)
    opra = OpraHistory(settings.databento, g, history)
    todo = sorted(set(days) - opra.done_days())
    est = 0.088 * len(todo)                          # measured 2026-10-19: $0.44 for a 5-day week (quotes + contract lists)
    s = g.summary()
    print(f"{len(todo)} trading days to download (newest first). Estimated ~${est:.2f}; "
          f"spent so far ${s.spent_usd:.2f} of ${s.budget_usd:.2f}. Every request is price-checked against the caps.", flush=True)
    rep = opra.download(days)
    s = g.summary()
    print(f"Downloaded {rep.days_done} days ({rep.days_skipped_existing} already on disk). "
          f"Spent ${s.spent_usd:.2f} of ${s.budget_usd:.2f}.")
    if rep.stopped_reason:
        print(f"STOPPED: {rep.stopped_reason}")
    return 0


def cmd_vol_study(settings) -> int:
    from core.config import PROJECT_ROOT
    from backtesting.data import available_days
    from research.intraday_vol_study import STUDY_DIR, build_report, run_multi_day, run_study

    days = available_days()
    if len(days) < 5:
        print("Not enough downloaded days yet (run opra-history first).")
        return 1
    iv, trades = run_study(settings, days)
    iv.to_parquet(STUDY_DIR / "intraday_iv.parquet", index=False)
    trades.to_parquet(STUDY_DIR / "iron_fly_trades.parquet", index=False)
    md = run_multi_day(settings, days)
    md.to_parquet(STUDY_DIR / "iron_fly_multiday.parquet", index=False)
    path = build_report(iv, trades, multi_day=md)
    print(f"{len(days)} days studied. Report written: {path.relative_to(PROJECT_ROOT)}")
    return 0


def cmd_opra_widen(settings, start: str, end: str, band: float, run_budget: float) -> int:
    from datetime import date

    from core.config import PROJECT_ROOT
    from data.collectors.historical import open_history
    from data.collectors.opra_history import OpraHistory, trading_days

    g = _databento(settings)
    history = open_history(PROJECT_ROOT / settings.storage.history_path)
    days = trading_days(history, date.fromisoformat(start), date.fromisoformat(end))
    s0 = g.summary()
    print(f"Widening {len(days)} days to ±{band:.1%}; this run may spend at most ${run_budget:.2f} "
          f"(spent so far ${s0.spent_usd:.2f} of ${s0.budget_usd:.2f}).", flush=True)
    rep = OpraHistory(settings.databento, g, history).widen(days, band, run_budget)
    s1 = g.summary()
    print(f"Widened {rep.days_done} days. This run spent ${s1.spent_usd - s0.spent_usd:.2f}; "
          f"total ${s1.spent_usd:.2f} of ${s1.budget_usd:.2f}.")
    if rep.stopped_reason:
        print(f"STOPPED: {rep.stopped_reason}")
    return 0


def cmd_killswitch(settings, action: str, authorization: str | None) -> int:
    from datetime import datetime

    from core.config import PROJECT_ROOT
    from data.storage.db import MarketDataStore
    from research.pricing_report import EASTERN
    from risk.state import RESET_PHRASE, Level, RiskStateStore

    store = MarketDataStore(PROJECT_ROOT / settings.storage.sqlite_path)
    try:
        state = RiskStateStore(store)
        today = datetime.now(EASTERN).date()
        if action == "stop":
            state.escalate(Level.HALTED, "manual_stop", today)
        elif action == "ack-warning":
            state.acknowledge_warning(today)
        elif action == "reset":
            if not authorization:
                print(f'Reset requires --authorization "{RESET_PHRASE}"')
                return 2
            try:
                state.manual_reset(authorization, today)
            except PermissionError as exc:
                print(f"REFUSED: {exc}")
                return 2
        level = state.level(today)
        print(f"Kill switch: {level.name} (last trigger: {state.last_trigger() or 'none'}); "
              f"high-water mark: {state.high_water_mark() or 'not set'}")
        for ts, lvl, trig in store.conn.execute(
                "SELECT ts, level, trigger FROM kill_switch_events ORDER BY id DESC LIMIT 5").fetchall():
            print(f"  {ts}  {lvl:<15} {trig}")
    finally:
        store.close()
    return 0


def cmd_risk_replay(settings, log) -> int:
    from core.config import PROJECT_ROOT
    from data.collectors.historical import open_history
    from research.risk_replay import build_risk_report
    from risk.stress import calibrate, save

    conn = open_history(PROJECT_ROOT / settings.storage.history_path)
    try:
        cal = calibrate(conn)
        save(cal, PROJECT_ROOT / "config" / "stress_calibration.json")
        print(f"Stress calibration: {cal.days:,} days; 2-hour worst drop {cal.worst_down:.1%} ({cal.worst_down_date}), "
              f"worst rally {cal.worst_up:+.1%} ({cal.worst_up_date})")
        path = build_risk_report(settings, conn, cal)
    finally:
        conn.close()
    print(f"Report written: {path.relative_to(PROJECT_ROOT)}")
    return 0


def cmd_history(settings, log) -> int:
    from core.config import PROJECT_ROOT
    from data.collectors.historical import download_minute_bars, download_raw_factors, open_history

    conn = open_history(PROJECT_ROOT / settings.storage.history_path)
    try:
        creds = load_alpaca_credentials(settings)
        n = download_minute_bars(creds, conn, settings.market_data.primary_underlying)
        download_raw_factors(creds, conn, settings.market_data.primary_underlying)
        total, first, last = conn.execute("SELECT COUNT(*), MIN(ts), MAX(ts) FROM minute_bars").fetchone()
    finally:
        conn.close()
    print(f"Downloaded {n:,} bars; history now {total:,} bars from {first} to {last}")
    return 0


def cmd_price(settings, log, as_of_text: str | None) -> int:
    from datetime import datetime, timezone

    from core.config import PROJECT_ROOT
    from data.storage.db import MarketDataStore
    from pricing.engine import price_snapshot
    from pricing.persistence import save_pricing
    from pricing.rates import risk_free_rate, update_rates
    from pricing.snapshot import latest_receipt_time, load_snapshot

    store = MarketDataStore(PROJECT_ROOT / settings.storage.sqlite_path)
    try:
        try:
            update_rates(settings, store)
        except Exception as exc:  # offline is fine: stored or fallback rates are used
            log.warning("could not refresh rates from FRED: %s", exc)
        underlying = settings.market_data.primary_underlying
        if as_of_text:
            as_of = datetime.fromisoformat(as_of_text)
            if as_of.tzinfo is None:
                raise ConfigError("--as-of needs a timezone, e.g. 2026-10-19T15:00:00-04:00")
        else:
            as_of = latest_receipt_time(store, underlying)
            if as_of is None:
                print("No collected data yet — run `main.py collect` first.")
                return 1
        snap = load_snapshot(store, underlying, as_of, settings.pricing.snapshot_max_quote_age_seconds)

        def rate_for(years):
            info = risk_free_rate(settings, store, as_of, years)
            return info.rate, info.source

        result = price_snapshot(snap, settings.pricing, rate_for)
        run_id = save_pricing(store, result)
    finally:
        store.close()

    contracts = result.contracts
    reliable = [c for c in contracts if c.reliable]
    print(f"Pricing run {run_id}: {underlying} {snap.spot:.2f} as of {as_of.astimezone(timezone.utc).isoformat()}  (rates: {result.rate_source})")
    print(f"  {len(contracts)} contracts, {sum(c.fair_price is not None for c in contracts)} priced, {len(reliable)} with no warning flags")
    flag_counts = {}
    for c in contracts:
        for f in c.flags:
            flag_counts[f] = flag_counts.get(f, 0) + 1
    print(f"  Flags: {dict(sorted(flag_counts.items(), key=lambda kv: -kv[1]))}")
    print("\n  Expiry      days  forward   impl.div  ATM IV  fit error  points (dropped)")
    for e in result.expiries:
        f = e.fit
        if f and f.status == "ok":
            print(f"  {e.expiration}  {e.years*365:5.1f}  {e.forward.forward:8.2f}  {e.forward.implied_dividend_yield*100:6.2f}%  "
                  f"{f.atm_iv*100:5.2f}%  {f.rmse_iv*100:5.2f} pts  {f.points_used} ({f.points_excluded})")
        else:
            print(f"  {e.expiration}  {e.years*365:5.1f}  no fit ({f.status if f else 'no forward'})")
    top = sorted(reliable, key=lambda c: -max(c.buy_z, c.sell_z))[:8]
    print("\n  Largest apparent edges (research only — NOT trade signals):")
    for c in top:
        side, edge, z = ("buy", c.buy_edge, c.buy_z) if c.buy_z >= c.sell_z else ("sell", c.sell_edge, c.sell_z)
        print(f"    {c.option.symbol}  {side:4}  bid {c.option.bid:6.2f} ask {c.option.ask:6.2f}  fair {c.fair_price:6.2f}  "
              f"edge {edge:+.3f} (${edge*100:+.0f}/contract)  z={z:+.1f}")
    return 0


def cmd_report(settings, log, day_text: str | None, every: int) -> int:
    from datetime import date

    from core.config import PROJECT_ROOT
    from data.storage.db import MarketDataStore
    from pricing.rates import update_rates
    from pricing.snapshot import latest_receipt_time
    from research.pricing_report import EASTERN, build_report

    store = MarketDataStore(PROJECT_ROOT / settings.storage.sqlite_path)
    try:
        try:
            update_rates(settings, store)
        except Exception as exc:
            log.warning("could not refresh rates from FRED: %s", exc)
        if day_text:
            day = date.fromisoformat(day_text)
        else:
            latest = latest_receipt_time(store, settings.market_data.primary_underlying)
            if latest is None:
                print("No collected data yet.")
                return 1
            day = latest.astimezone(EASTERN).date()
        path = build_report(settings, store, day, every)
    finally:
        store.close()
    print(f"Report written: {path.relative_to(PROJECT_ROOT)}")
    return 0


def cmd_notify(settings, title: str | None, message: str | None, priority: str) -> int:
    from monitoring import alerts

    if not alerts.configured():
        print("No ntfy topic configured (OPTIONS_QUANT_NTFY_TOPIC in .env).")
        return 1
    ok = alerts.send(title or "Options bot", message or "(no message)", priority)
    print("Alert sent." if ok else "Alert could not be sent (saved to logs/alerts_unsent.jsonl).")
    return 0 if ok else 1


def cmd_after_close(settings) -> int:
    """Daily summary to the phone, then storage cleanup. Read-only towards the broker."""
    from datetime import datetime, timedelta

    from core.config import PROJECT_ROOT
    from data.storage.db import MarketDataStore
    from data.storage.retention import run_retention
    from execution.broker import ReadOnlyBroker
    from monitoring import alerts
    from monitoring.daily import build_summary
    from research.pricing_report import EASTERN

    today = datetime.now(EASTERN).date()
    equity, days = None, []
    try:
        broker = ReadOnlyBroker(settings, load_alpaca_credentials(settings))
        equity = broker.account_summary().equity
        days = broker.trading_days(today - timedelta(days=7), today)
    except Exception as exc:                       # still report what we know from the local records
        print(f"Broker not reachable ({type(exc).__name__}); summary uses local records only.")
    # A run that fires before today's close (the Mac was asleep at 15:20 and woke later) reports the last full day.
    if datetime.now(EASTERN).hour < 16 and days:
        earlier = [d for d in days if d < today]
        today = earlier[-1] if earlier else today
        equity = None                               # today's equity would not belong to that day
    is_trading_day = today in days if days else today.weekday() < 5
    store = MarketDataStore(PROJECT_ROOT / settings.storage.sqlite_path)
    try:
        s = build_summary(store.conn, today, equity, [d for d in days if d <= today], is_trading_day)
        s.lines += _basket_lines()
        s.lines += _momentum_lines()
    finally:
        store.close()
    print(s.title)
    print(s.text())
    if settings.alerts.daily_summary and (is_trading_day or s.problems):
        alerts.send(s.title, s.text(), "high" if s.problems else "default", ["warning"] if s.problems else ["chart_with_upwards_trend"])
    rep = run_retention(settings)
    print(f"Storage: {rep.summary()}")
    return 0


def cmd_midday(settings) -> int:
    """Midday check-in to the phone: jobs alive, data fresh, screener so far, trades so far. Read-only."""
    from datetime import datetime

    from core.config import PROJECT_ROOT
    from data.storage.db import MarketDataStore
    from execution.broker import ReadOnlyBroker
    from monitoring import alerts
    from monitoring.daily import build_midday
    from research.pricing_report import EASTERN

    today = datetime.now(EASTERN).date()
    try:
        if today not in ReadOnlyBroker(settings, load_alpaca_credentials(settings)).trading_days(today, today):
            print("Not a trading day — no check-in.")
            return 0
    except Exception as exc:                       # can't tell; send anyway, an unreachable broker is worth knowing
        print(f"Broker not reachable ({type(exc).__name__}).")
    # A job started before the lock existed holds none, so also accept recent signs of life.
    trader_log = PROJECT_ROOT / "logs" / f"trader_{datetime.now():%Y-%m-%d}.log"
    trader_alive = trader_log.exists() and datetime.now().timestamp() - trader_log.stat().st_mtime < 300
    store = MarketDataStore(PROJECT_ROOT / settings.storage.sqlite_path)
    try:
        s = build_midday(store.conn, today, is_running("collector"), is_running("trader") or trader_alive)
    finally:
        store.close()
    print(s.title)
    print(s.text())
    if settings.alerts.daily_summary:
        alerts.send(s.title, s.text(), "high" if s.problems else "low", ["warning"] if s.problems else ["mag"])
    return 0


def cmd_storage(settings, action: str, day: str | None) -> int:
    import sqlite3
    import shutil

    from core.config import PROJECT_ROOT
    from data.storage.retention import data_days, restore_day, run_retention

    path = PROJECT_ROOT / settings.storage.sqlite_path
    archive = PROJECT_ROOT / settings.storage.archive_path
    if action == "cleanup":
        print(run_retention(settings).summary())
        return 0
    if action == "restore":
        if not day:
            print("restore needs --date YYYY-MM-DD")
            return 2
        conn = sqlite3.connect(path)
        try:
            print(f"Restored {restore_day(conn, day, archive):,} rows for {day}.")
        finally:
            conn.close()
        return 0
    conn = sqlite3.connect(path)
    try:
        days = data_days(conn)
    finally:
        conn.close()
    archived = sorted(p.name for p in archive.glob("*")) if archive.is_dir() else []
    arch_mb = sum(f.stat().st_size for f in archive.rglob("*.parquet")) / 1e6 if archive.is_dir() else 0
    print(f"Live database: {path.stat().st_size / 1e6:,.0f} MB, {len(days)} trading days "
          f"(newest {settings.storage.keep_full_days} at full detail, then one quote per minute)")
    print(f"Archive: {len(archived)} days, {arch_mb:,.0f} MB in {settings.storage.archive_path}")
    print(f"Free disk: {shutil.disk_usage(path.parent).free / 1e9:,.0f} GB")
    return 0


def cmd_backtest(settings, start: str | None, end: str | None, walk: str | None, grid: list[str], equity0: float) -> int:
    from datetime import date

    from backtesting.data import available_days
    from backtesting.engine import Backtest
    from backtesting.metrics import from_result, table
    from backtesting.walk_forward import report_lines, walk_forward
    from core.config import PROJECT_ROOT
    from risk.stress import load as load_stress

    days = [d for d in available_days() if (not start or d >= date.fromisoformat(start)) and (not end or d <= date.fromisoformat(end))]
    if not days:
        print("No downloaded quote days in that range.")
        return 1
    candidates = [{}]
    for spec in grid:                              # strategy.KEY=V1,V2  -> one candidate per combination
        key, _, values = spec.partition("=")
        section, _, field = key.partition(".")
        if section != "strategy" or not field or not values:
            print(f"--grid must look like strategy.min_edge_z=1.5,2,2.5 (got {spec!r})")
            return 2
        candidates = [{**c, field: float(v)} for c in candidates for v in values.split(",")]
    stress = load_stress(PROJECT_ROOT / "config" / "stress_calibration.json")

    def run(params, ds):
        s = settings.model_copy(update={"strategy": settings.strategy.model_copy(update=params)}) if params else settings
        return Backtest(s, stress).run(ds, equity0)

    L = [f"# Backtest — {days[0]} to {days[-1]}", "",
         f"Strategy: relative-value spreads (current rules). Real SPY option bid/ask (Databento), fills at the next "
         f"minute's executable prices plus fees, no look-ahead. Starting equity ${equity0:,.0f}.", ""]
    if walk:
        train, test = (int(x) for x in walk.split(","))
        wf = walk_forward(days, candidates, run, train, test, equity0=equity0)
        L += report_lines(wf)
    else:
        res = run(candidates[0], days)
        L += ["## Standard report", ""] + table(from_result(res, equity0))
        if res.refusals:
            L += ["", "Risk-engine refusals: " + ", ".join(f"{k} {v}" for k, v in sorted(res.refusals.items(), key=lambda x: -x[1]))]
    path = PROJECT_ROOT / "docs" / "research" / f"backtest_{days[0]}_{days[-1]}.md"
    path.write_text("\n".join(L) + "\n")
    print("\n".join(L))
    print(f"\nReport written: {path.relative_to(PROJECT_ROOT)}")
    return 0


def cmd_vol_data(settings, action: str, folder: str | None) -> int:
    """Volatility strategy, Phase 1: free data (CBOE indices, FOMC/CPI dates, OptionsDX chains) and its report."""
    from datetime import date

    from core.config import PROJECT_ROOT
    from data.collectors.historical import open_history
    from data.collectors.vol_data import fetch_all, load_optionsdx_folder, open_vol_db
    from research.vol_data_report import write_report

    vol = open_vol_db(PROJECT_ROOT / "data" / "storage" / "vol_history.db")
    try:
        if action == "fetch":
            for name, n in fetch_all(vol).items():
                print(f"{name:<6} {n:,} rows")
        elif action == "load":
            if not folder:
                print("vol-data load needs --path <folder with the OptionsDX files>")
                return 2
            loaded = load_optionsdx_folder(vol, folder)
            for name, read, kept in loaded:
                print(f"{name}: kept {kept:,} of {read:,} rows")
            print(f"{len(loaded)} new file(s) loaded")
        elif action == "forecast":
            from research.vol_forecast import run as run_forecast
            history = open_history(PROJECT_ROOT / settings.storage.history_path)
            try:
                path, passed = run_forecast(history, vol, PROJECT_ROOT / "docs" / "research", date.today())
            finally:
                history.close()
            print(f"Model bar {'PASSED' if passed else 'FAILED'}. Report written: {path.relative_to(PROJECT_ROOT)}")
        elif action == "backtest":
            from research.condor_phase3 import run as run_phase3
            history = open_history(PROJECT_ROOT / settings.storage.history_path)
            try:
                path, passed = run_phase3(vol, history, PROJECT_ROOT / "docs" / "research", date.today())
            finally:
                history.close()
            print(f"{'GO' if passed else 'NO-GO'}. Report written: {path.relative_to(PROJECT_ROOT)}")
        elif action == "trend":
            from research.trend_backtest import run as run_trend
            history = open_history(PROJECT_ROOT / settings.storage.history_path)
            try:
                path, passed = run_trend(vol, history, PROJECT_ROOT / "docs" / "research", date.today())
            finally:
                history.close()
            print(f"{'GO' if passed else 'NO-GO'}. Report written: {path.relative_to(PROJECT_ROOT)}")
        elif action == "monthly-trend":
            from research.monthly_trend import run as run_monthly
            history = open_history(PROJECT_ROOT / settings.storage.history_path)
            try:
                path, passed = run_monthly(history, PROJECT_ROOT / "docs" / "research", date.today(),
                                           PROJECT_ROOT / "data" / "storage" / "studies")
            finally:
                history.close()
            print(f"{'GO' if passed else 'NO-GO'}. Report written: {path.relative_to(PROJECT_ROOT)}")
        elif action == "multi-trend":
            from research.multi_asset_trend import run as run_multi
            history = open_history(PROJECT_ROOT / settings.storage.history_path)
            try:
                path, passed = run_multi(history, PROJECT_ROOT / "docs" / "research", date.today(),
                                         PROJECT_ROOT / "data" / "storage" / "studies")
            finally:
                history.close()
            print(f"{'GO' if passed else 'NO-GO'}. Report written: {path.relative_to(PROJECT_ROOT)}")
        elif action == "rel-momentum":
            from research.relative_momentum import run as run_relmom
            history = open_history(PROJECT_ROOT / settings.storage.history_path)
            try:
                path, passed = run_relmom(history, PROJECT_ROOT / "docs" / "research", date.today(),
                                          PROJECT_ROOT / "data" / "storage" / "studies")
            finally:
                history.close()
            print(f"{'GO' if passed else 'NO-GO'}. Report written: {path.relative_to(PROJECT_ROOT)}")
        elif action == "dual-momentum":
            from research.dual_momentum import run as run_dual
            history = open_history(PROJECT_ROOT / settings.storage.history_path)
            try:
                path, passed = run_dual(history, PROJECT_ROOT / "docs" / "research", date.today(),
                                        PROJECT_ROOT / "data" / "storage" / "studies")
            finally:
                history.close()
            print(f"{'GO' if passed else 'NO-GO'}. Report written: {path.relative_to(PROJECT_ROOT)}")
        elif action == "condors":
            from research.condor_build_report import write_report as write_condor_report
            history = open_history(PROJECT_ROOT / settings.storage.history_path)
            try:
                path = write_condor_report(vol, history, PROJECT_ROOT / "docs" / "research", date.today())
            finally:
                history.close()
            print(f"Report written: {path.relative_to(PROJECT_ROOT)}")
        elif action == "report":
            history = open_history(PROJECT_ROOT / settings.storage.history_path)
            try:
                path = write_report(vol, history, PROJECT_ROOT / "docs" / "research", date.today())
            finally:
                history.close()
            print(f"Report written: {path.relative_to(PROJECT_ROOT)}")
        else:
            for sym, first, last, n in vol.execute(
                    "SELECT symbol, MIN(day), MAX(day), COUNT(*) FROM index_daily GROUP BY symbol"):
                print(f"{sym:<6} {first} to {last} ({n:,} days)")
            for ev, n in vol.execute("SELECT event, COUNT(*) FROM events GROUP BY event"):
                print(f"{ev:<6} {n} dates")
            days, first, last = vol.execute(
                "SELECT COUNT(DISTINCT quote_date), MIN(quote_date), MAX(quote_date) FROM eod_chain").fetchone()
            print(f"Chains {days:,} quote days" + (f" ({first} to {last})" if days else " (not loaded yet)"))
    finally:
        vol.close()
    return 0


def cmd_overnight(settings, action: str) -> int:
    """Overnight drift screener: 'fetch' downloads daily bars (free), anything else (re)builds the report."""
    from datetime import date

    from core.config import PROJECT_ROOT
    from core.secrets import load_alpaca_credentials
    from research.overnight_screener import download, last_completed_day, open_db, run

    conn = open_db(PROJECT_ROOT / "data" / "storage" / "studies" / "overnight_bars.db")
    try:
        have = conn.execute("SELECT value FROM meta WHERE key='end'").fetchone()
        if action == "fetch" or have is None or (action == "refresh" and have[0] < last_completed_day().isoformat()):
            download(load_alpaca_credentials(settings), conn, last_completed_day())
        path, go = run(conn, PROJECT_ROOT / "docs" / "research", date.today(), liquid=action == "liquid")
    finally:
        conn.close()
    print(f"{'GO' if go else 'NO-GO'}. Report written: {path.relative_to(PROJECT_ROOT)}")
    return 0


def _basket_lines() -> list[str]:
    """One line for the daily summary about the overnight basket (it shares the account, so it moves the P&L)."""
    from execution import overnight_basket as ob
    if not ob.DB_PATH.exists():
        return []
    b = ob.OvernightBasket(ob.open_db())
    try:
        t, held = b.totals(), b.holdings()
        last = b.conn.execute("SELECT pnl, ret FROM nights ORDER BY sold DESC LIMIT 1").fetchone()
        out = f"Overnight basket: holding {len(held)} names tonight" if held else "Overnight basket: nothing held"
        if last:
            out += f"; last night ${last['pnl']:+,.0f} ({last['ret']:+.2%}); {t['nights']} nights total ${t['pnl']:+,.0f}"
        return [out]
    finally:
        b.conn.close()


def _momentum_lines() -> list[str]:
    """One line for the daily summary about dual momentum (it shares the account, so it moves the P&L)."""
    from execution import dual_momentum_paper as dmp
    if not dmp.DB_PATH.exists():
        return []
    b = dmp.DualMomentumBook(dmp.open_db())
    try:
        held = b.holdings()
        last = b.conn.execute("SELECT * FROM months ORDER BY signal_month_end DESC LIMIT 1").fetchone()
        out = ("Dual momentum: holding " + ", ".join(f"{q} {s}" for s, q in sorted(held.items()))) if held \
            else "Dual momentum: all cash"
        if last:
            out += f" (from the {last['signal_month_end']} signal" + ("" if last["done"] else ", rebalance not finished") + ")"
        return [out]
    finally:
        b.conn.close()


def cmd_momentum(settings, action: str) -> int:
    """Dual momentum on the PAPER account: plan | rebalance | resize | status (execution/dual_momentum_paper.py).
    resize = re-plan this month's holdings at today's size and prices and trade to them now."""
    import json
    import time as _t
    from datetime import datetime, time, timedelta

    from core.config import PROJECT_ROOT
    from core.secrets import load_alpaca_credentials
    from execution import dual_momentum_paper as dmp
    from monitoring import alerts

    book = dmp.DualMomentumBook(dmp.open_db())
    if action == "status":
        basis = book.cost_basis()
        print("Held now: " + (", ".join(f"{q} {s} @ ${c:,.2f}" for s, (q, c) in sorted(basis.items())) or "nothing (all cash)"))
        print(f"Realized P&L so far: ${book.realized():+,.2f}")
        for r in book.conn.execute("SELECT * FROM months ORDER BY signal_month_end DESC LIMIT 12"):
            print(f"  {r['signal_month_end']}: hold {', '.join(json.loads(r['held'])) or 'cash'}; "
                  f"targets {r['targets']}; {'done ' + r['done'] if r['done'] else 'NOT DONE'}")
        return 0

    from data.collectors.alpaca_source import AlpacaMarketSource
    from data.storage.db import MarketDataStore
    from execution.alpaca_paper import AlpacaPaperBroker
    from execution.broker import ReadOnlyBroker
    from risk.state import RiskStateStore

    creds = load_alpaca_credentials(settings)
    clock = ReadOnlyBroker(settings, creds)
    now = dmp.now_utc()
    today = now.astimezone(dmp.ET).date()
    first = today.replace(day=1)
    last_month = _retrying(lambda: clock.trading_days((first - timedelta(days=1)).replace(day=1), first - timedelta(days=1)))
    month_end = max(last_month)
    resize = action == "resize"
    broker = AlpacaPaperBroker(settings, creds)

    if action == "rebalance":
        row = book.month(month_end)
        if row is not None and row["done"]:
            book.sync(broker)
            due = [s for s in json.loads(row["targets"]) if dmp.is_option(s)
                   and (dmp.expiry_of(s) - today).days <= dmp.FORCE_DTE]
            if not due:
                msg = book.loss_alert(month_end, book.value(_retrying(broker.positions)))
                if msg:
                    print(msg)
                    alerts.urgent("Dual momentum loss alert", msg)
                print(f"{month_end:%B} signal already traded on {row['done']}; nothing to do")
                return 0
            print(f"calls near expiry, rolling: {', '.join(due)}")
    if action in ("rebalance", "resize") and today not in _retrying(lambda: clock.trading_days(today, today)):
        print("market closed today; will try at the next run")
        return 0
    try:
        decision = dmp.decide(dmp.load_live(), month_end)
    except Exception as exc:
        print(f"no decision: {exc}")
        if action != "plan":
            alerts.urgent("Dual momentum: no decision", f"Couldn't compute the {month_end} signal: {exc}. "
                          "The next scheduled run retries.")
        return 0                                  # already alerted; a non-zero exit would send a second alert
    prices = {s: _retrying(lambda s=s: clock.latest_underlying_trade(s))["price"] for s in decision.held}
    source = AlpacaMarketSource(settings, creds)

    if action == "plan":
        equity = broker.equity()
        print(f"Signal from {month_end}:")
        for s in dmp.ASSETS:
            print(f"  {s}: 12-month return {decision.scores[s]:+.1%}, trend {'on' if decision.trend_on[s] else 'off'}")
        for fund in decision.held:
            budget = dmp.fund_budget(decision, fund, equity)
            pick = None if fund in dmp.SHARES_ONLY else dmp.choose_call(source, fund, today, budget)
            if isinstance(pick, dmp.CallPick):
                print(f"  hold {fund}: {pick.qty} x {pick.symbol} (delta {pick.delta or 0:.2f}, {pick.bid:.2f} x "
                      f"{pick.ask:.2f}, ~${pick.qty * pick.ask * 100:,.0f} of a ${budget:,.0f} budget)")
            else:
                why = f" ({pick})" if pick else ""
                print(f"  hold {fund}: {round(budget / prices[fund])} shares (~${budget:,.0f}){why}")
        if not decision.held:
            print("  hold: all cash")
        row = book.month(month_end)
        print(f"This month's rebalance: {'done ' + row['done'] if row and row['done'] else 'not done yet'}")
        return 0

    if action not in ("rebalance", "resize"):
        print("momentum needs: plan | rebalance | resize | status")
        return 2

    store = MarketDataStore(PROJECT_ROOT / settings.storage.sqlite_path)
    try:
        level = int(RiskStateStore(store).level(today))
    finally:
        store.conn.close()
    stop = dmp.STOP_FILE.exists()

    def wait_until(t: time) -> None:
        target = datetime.combine(today, t, dmp.ET)
        while dmp.now_utc() < target:
            _t.sleep(min(60.0, max(1.0, (target - dmp.now_utc()).total_seconds())))

    def settle_shares() -> list[str]:
        """Wait for the 9:30 ET open (auction orders), then check fills until no share order works (max 5 min)."""
        wait_until(time(9, 33))
        notes, give_up = [], dmp.now_utc() + timedelta(minutes=5)
        while True:
            notes += book.sync(broker)
            if not any(not dmp.is_option(r["symbol"]) for r in book.working()) or dmp.now_utc() > give_up:
                return notes
            _t.sleep(10)

    def quote(sym: str):
        q = next(iter(_retrying(lambda: source.fetch_option_quotes([sym]))), None)
        return (q.bid, q.ask) if q and q.bid and q.ask else None

    book.sync(broker)
    book.plan(broker, today, decision, prices, resize=resize)
    book.roll_due(today, decision)
    notes: list[str] = []
    c = _retrying(clock.market_clock)
    shares = book.trade_shares(broker, now, decision, bool(c["is_open"]), level, stop)
    print(shares.summary(), flush=True)
    if shares.submitted:
        notes += settle_shares()
        if any(r["status"] in ("rejected", "canceled") for r in book.conn.execute(
                "SELECT status, symbol FROM orders WHERE day=?", (today.isoformat(),)) if not dmp.is_option(r["symbol"])):
            retry = book.trade_shares(broker, dmp.now_utc(), decision, True, level, stop)
            print(retry.summary(), flush=True)
            notes += retry.notes + settle_shares()
    notes += shares.notes
    wait_until(dmp.OPTIONS_FROM)
    opts = None
    if _retrying(clock.market_clock)["is_open"]:
        opts = book.trade_options(broker, dmp.now_utc(), decision,
                                  lambda fund, budget: dmp.choose_call(source, fund, today, budget), quote, level, stop,
                                  prices)
        print(opts.summary(), flush=True)
        notes += opts.notes + opts.skipped
    else:
        notes.append("market not open for options; the next run retries")
    book.sync(broker)
    value = book.value(_retrying(broker.positions))
    done = book.finish(month_end, today, value)
    held = book.holdings()
    msg = (f"{month_end:%B} signal: hold {', '.join(decision.held) or 'cash'}. Now holding "
           + (", ".join(f"{q} {s}" for s, q in sorted(held.items())) or "nothing") + f" (~${value:,.0f})."
           + (" " + "; ".join(notes) if notes else ""))
    print(msg)
    if done:
        alerts.send("Dual momentum rebalanced", msg, "low")
    elif shares.submitted or (opts and (opts.submitted or opts.skipped)) or book.working() or level != 0 or stop:
        alerts.urgent("Dual momentum needs a look", msg + " Not finished; the next scheduled run retries.")
    return 0


def cmd_gap_puts(settings, action: str) -> int:
    """Gap-puts backtest: 'fetch' downloads daily bars, 'sample' writes the news-classifier check, else the report."""
    from datetime import date

    from core.config import PROJECT_ROOT
    from core.secrets import load_alpaca_credentials
    from research import gap_puts as gpm

    creds = load_alpaca_credentials(settings)
    out = PROJECT_ROOT / "docs" / "research"
    conn = gpm.open_db(PROJECT_ROOT / "data" / "storage" / "studies" / "gap_puts.db")
    try:
        if action == "fetch" or conn.execute("SELECT value FROM meta WHERE key='end'").fetchone() is None:
            gpm.download_bars(creds, conn, gpm.last_completed_day())
            if action == "fetch":
                return 0
        if action == "sample":
            df = gpm.add_features(gpm.load_bars(conn))
            cands = gpm.collect(creds, conn, df, gpm.gaps(df), with_options=False)
            path = out / "gap_puts_classifier_sample.md"
            path.write_text("# Gap puts: news classifier check (40 random +30% gaps, seed 1)\n\n"
                            + gpm.classifier_sample(cands, conn))
            n_un = sum(not c.explained for c in cands)
            print(f"{len(cands):,} gaps, {n_un:,} unexplained. Sample written: {path.relative_to(PROJECT_ROOT)}")
            return 0
        path, go = gpm.run(creds, conn, out, date.today())
    finally:
        conn.close()
    print(f"{'GO' if go else 'NO-GO'}. Report written: {path.relative_to(PROJECT_ROOT)}")
    return 0


def cmd_spike_short_v4(settings) -> int:
    """Checklist short, enhanced (squeeze cap, risk-pool sizing, IBKR costs); uses the v2 caches."""
    from datetime import date

    from core.config import PROJECT_ROOT
    from core.secrets import load_alpaca_credentials
    from research import spike_short, spike_short_v2, spike_short_v4

    studies = PROJECT_ROOT / "data" / "storage" / "studies"
    bars = spike_short.open_db(studies / "spike_bars.db")
    conn = spike_short_v2.open_db(studies / "spike_v2.db")
    try:
        path, go = spike_short_v4.run(load_alpaca_credentials(settings), bars, conn, PROJECT_ROOT / "docs" / "research",
                                      date.today(), studies / f"ibkr_shortable_{date.today()}.txt")
    finally:
        bars.close()
        conn.close()
    print(f"{'GO' if go else 'NO-GO'}. Report written: {path.relative_to(PROJECT_ROOT)}")
    return 0


def _ibkr_paper(conn, today, what: str, rows=None) -> str:
    """IBKR PAPER orders for the shadow log (config/ibkr_paper.yaml). Never raises: returns a one-line outcome."""
    from core.config import PROJECT_ROOT
    from execution import ibkr_paper as ip

    cfg = ip.load_config(PROJECT_ROOT / "config" / "ibkr_paper.yaml")
    if not cfg.enabled:
        return "IBKR paper: off (config/ibkr_paper.yaml)"
    ib = None
    try:
        for port in dict.fromkeys([cfg.port, 4002, 7497]):           # configured port, then the other paper port
            try:
                ib = ip.connect(ip.Config(True, cfg.host, port, cfg.client_id))
                break
            except ip.PaperGuardError:
                raise
            except Exception as exc:                                 # nothing listening on this port
                last = exc
        if ib is None:
            return f"IBKR paper: could not connect - is IB Gateway (or TWS) open and logged in to PAPER? ({last})"
        ip.open_db(conn)
        if what == "check":
            acct = ib.managedAccounts()[0]
            nl = next((v.value for v in ib.accountSummary(acct) if v.tag == "NetLiquidation"), "?")
            return f"IBKR paper: connected to paper account {acct[:4]}... on port {port}, net liquidation {nl}"
        if what == "place":
            return ip.message(ip.place(ib, conn, today, rows or []))
        done = ip.cover(ib, conn, today)
        return "IBKR paper: " + ("; ".join(f"{d['symbol']} out at ${d['exit_fill']:.4g} ({d['exit_how']})"
                                           for d in done) if done else "nothing to cover")
    except ip.PaperGuardError as exc:
        return f"IBKR paper: REFUSED - {exc}"
    except Exception as exc:
        return f"IBKR paper: error - {exc}"
    finally:
        if ib is not None:
            ib.disconnect()


def cmd_spike_short_shadow(settings, action: str, dry_run: bool) -> int:
    """Forward shadow log (v4 rules, day-trade only). Read-only data; places no orders. --dry-run: nothing saved."""
    from datetime import datetime

    from core.config import PROJECT_ROOT
    from core.secrets import load_alpaca_credentials
    from monitoring import alerts
    from research import spike_short_shadow as sh
    from research.spike_short import ET

    path = PROJECT_ROOT / "data" / "storage" / "studies" / "spike_shadow.db"
    conn = sh.open_db(":memory:" if dry_run else path)
    now = datetime.now(ET)
    today = now.date()
    try:
        if action == "status":
            t = sh.totals(conn)
            print(sh.summary_line(t))
            print(f"{t['wins']} winners, {t['stops']} stopped; worst trade in the 40% account {t['worst_40']:+.1%} "
                  f"of it; placeable at 2.7% only: {t['settled_placeable']} trades, ${t['pnl_placeable']:+,.0f}.")
            print("   day, symbol, status, gap, IBKR shares, needed 2.7% / 40%, fee, net, P&L 2.7% / 40%, "
                  "first-15-min $, fillable 2.7% / 40%")
            for r in conn.execute("SELECT day, symbol, status, round(gap, 2), ibkr_available, shares_needed, "
                                  "shares_needed_40, ibkr_fee, round(net, 3), round(pnl), round(pnl_40), "
                                  "round(dv_first15), fillable_27, fillable_40 "
                                  "FROM shadow ORDER BY day DESC, rank LIMIT 20"):
                print("  ", r)
            return 0
        if action == "ibkr-check":
            print(_ibkr_paper(conn, today, "check"))
            return 0
        recent, history, news, ibkr, bar, minutes = sh.live_fns(load_alpaca_credentials(settings), conn, today)
        if action == "morning":
            if today.weekday() >= 5 or now.time() < sh.MORNING_FROM:
                print(f"Not a trading morning yet ({now:%a %H:%M} ET); the screen runs from 9:31 ET on weekdays.")
                return 0
            universe, bars = recent()
            rows, note = sh.morning(conn, today, universe, bars, history, news, ibkr)
            print(f"{today} morning: {note}." + (" (dry run, nothing saved)" if dry_run else ""))
            if rows:
                msg = sh.morning_message(rows)
                if not dry_run:
                    msg += "\n" + _ibkr_paper(conn, today, "place", rows)
                print(msg)
                if not dry_run:
                    alerts.send(f"Spike-short shadow {today}", msg, "default")
            return 0
        if action == "cover":
            msg = _ibkr_paper(conn, today, "cover")
            print(msg)
            if "nothing" not in msg and not dry_run:
                alerts.send(f"Spike-short IBKR paper {today}: covered", msg, "low")
            return 0
        if action == "close":
            if dry_run:                                   # nothing saved this morning: settle today's screen now
                universe, bars = recent()
                rows, note = sh.morning(conn, today, universe, bars, history, news, ibkr)
                print(f"{today} morning (dry run): {note}.")
                if now.time() < sh.CLOSE_FROM:            # settle on the day's bar so far
                    print("Before the close: settling on today's bar so far.")
                    now = now.replace(hour=sh.CLOSE_FROM.hour, minute=sh.CLOSE_FROM.minute)
            done = sh.close(conn, today, now, bar, minutes)
            t = sh.totals(conn)
            if not done:
                print(f"{today} close: nothing to settle.")
                return 0
            msg = sh.close_message(done, t)
            print(msg + (" (dry run, nothing saved)" if dry_run else ""))
            if not dry_run:
                alerts.send(f"Spike-short shadow {today}: closed", msg, "low")
            return 0
    finally:
        conn.close()
    print("spike-short-shadow needs: morning | cover | close | status | ibkr-check")
    return 2


def cmd_spike_short_v3(settings) -> int:
    """Checklist stocks, short open / cover close; uses the v2 caches."""
    from datetime import date

    from core.config import PROJECT_ROOT
    from core.secrets import load_alpaca_credentials
    from research import spike_short, spike_short_v2, spike_short_v3

    studies = PROJECT_ROOT / "data" / "storage" / "studies"
    bars = spike_short.open_db(studies / "spike_bars.db")
    conn = spike_short_v2.open_db(studies / "spike_v2.db")
    try:
        path, go = spike_short_v3.run(load_alpaca_credentials(settings), bars, conn, PROJECT_ROOT / "docs" / "research",
                                      date.today())
    finally:
        bars.close()
        conn.close()
    print(f"{'GO' if go else 'NO-GO'}. Report written: {path.relative_to(PROJECT_ROOT)}")
    return 0


def cmd_spike_short_v2(settings) -> int:
    """Webull-checklist short test; needs 'python main.py spike-short fetch' data first."""
    from datetime import date

    from core.config import PROJECT_ROOT
    from core.secrets import load_alpaca_credentials
    from research import spike_short, spike_short_v2

    studies = PROJECT_ROOT / "data" / "storage" / "studies"
    bars = spike_short.open_db(studies / "spike_bars.db")
    conn = spike_short_v2.open_db(studies / "spike_v2.db")
    try:
        path, go = spike_short_v2.run(load_alpaca_credentials(settings), bars, conn, PROJECT_ROOT / "docs" / "research",
                                      date.today())
    finally:
        bars.close()
        conn.close()
    print(f"{'GO' if go else 'NO-GO'}. Report written: {path.relative_to(PROJECT_ROOT)}")
    return 0


def cmd_spike_short(settings, action: str) -> int:
    """Short-the-spike backtest: 'fetch' downloads daily bars (free), anything else (re)builds the report."""
    from datetime import date

    from core.config import PROJECT_ROOT
    from core.secrets import load_alpaca_credentials
    from research.spike_short import download, last_completed_day, open_db, run

    conn = open_db(PROJECT_ROOT / "data" / "storage" / "studies" / "spike_bars.db")
    try:
        have = conn.execute("SELECT value FROM meta WHERE key='end'").fetchone()
        if action == "fetch" or have is None:
            download(load_alpaca_credentials(settings), conn, last_completed_day())
            if action == "fetch":
                return 0
        path, go = run(conn, PROJECT_ROOT / "docs" / "research", date.today())
    finally:
        conn.close()
    print(f"{'GO' if go else 'NO-GO'}. Report written: {path.relative_to(PROJECT_ROOT)}")
    return 0


def cmd_overnight_basket(settings, action: str) -> int:
    """Overnight share basket on the PAPER account: pick | buy | sell | status (execution/overnight_basket.py)."""
    import time as _t
    from datetime import date, datetime, time, timedelta

    from core.config import PROJECT_ROOT
    from core.secrets import load_alpaca_credentials
    from execution import overnight_basket as ob
    from monitoring import alerts

    if action == "pick":
        from research.overnight_screener import LIQUID_DV, load_panel, open_db as open_bars, walk_forward
        year = date.today().year
        conn = open_bars(PROJECT_ROOT / "data" / "storage" / "studies" / "overnight_bars.db")
        try:
            end = date.fromisoformat(conn.execute("SELECT value FROM meta WHERE key='end'").fetchone()[0])
            if end < date(year, 1, 1):
                print(f"price data ends {end}; run 'python main.py overnight fetch' first so {year - 1} is complete")
                return 2
            wf = walk_forward(load_panel(conn, end), end, years=(year,), ns=(20,), min_dv=LIQUID_DV, shortfall_is_problem=False)
        finally:
            conn.close()
        picks = [p for p in wf.picks[20][year] if p not in ob.EXCLUDED]
        ob.save_picks(year, picks)
        print(f"{year} picks ({len(picks)}): {', '.join(picks)}")
        return 0

    basket = ob.OvernightBasket(ob.open_db())
    if action == "status":
        t = basket.totals()
        print(f"Held now: {basket.holdings() or 'nothing'}")
        print(f"Nights traded: {t['nights']} ({t['up']} up); total P&L ${t['pnl']:,.2f}; compounded {t['compounded']:+.2%}")
        for r in basket.conn.execute("SELECT * FROM nights ORDER BY sold DESC LIMIT 10"):
            print(f"  {r['bought']} -> {r['sold']}: {r['names']} names, ${r['invested']:,.0f} in, "
                  f"P&L ${r['pnl']:+,.2f} ({r['ret']:+.2%})")
        return 0

    from data.storage.db import MarketDataStore
    from execution.alpaca_paper import AlpacaPaperBroker
    from execution.broker import ReadOnlyBroker
    from risk.state import RiskStateStore

    creds = load_alpaca_credentials(settings)
    broker = AlpacaPaperBroker(settings, creds)
    clock = ReadOnlyBroker(settings, creds)
    now = ob.now_utc()
    today = now.astimezone(ob.ET).date()

    def wait_until(t: time) -> None:
        target = datetime.combine(today, t, ob.ET)
        while ob.now_utc() < target:
            _t.sleep(min(60.0, max(1.0, (target - ob.now_utc()).total_seconds())))

    if action == "buy":
        c = _retrying(clock.market_clock)
        full = bool(c["is_open"]) and ob.full_trading_day(c["next_close"], now)
        store = MarketDataStore(PROJECT_ROOT / settings.storage.sqlite_path)
        try:
            level = int(RiskStateStore(store).level(today))
        finally:
            store.conn.close()
        picks = ob.load_picks(today.year)
        prices = {}
        for sym in picks:
            try:
                prices[sym] = clock.latest_underlying_trade(sym)["price"]
            except Exception as exc:
                print(f"no price for {sym}: {type(exc).__name__}")
        res = basket.buy(broker, now, prices, picks, full, level, ob.STOP_FILE.exists())
        print(res.summary(), flush=True)
        if not res.submitted:
            if full and "already bought today" not in res.summary():     # incl. "no picks" each January
                alerts.send("Overnight basket: not buying tonight", res.summary())
            return 0
        wait_until(time(16, 3))
        notes = basket.sync(broker)
        held = basket.holdings()
        cost = sum(r["qty"] * r["cost"] for r in basket.conn.execute("SELECT qty, cost FROM holdings"))
        msg = f"Bought {len(held)} names at the close, ${cost:,.0f} in total." + (" " + "; ".join(notes) if notes else "")
        print(msg)
        alerts.send("Overnight basket bought", msg, "low")
        return 0

    if action == "sell":
        c = _retrying(clock.market_clock)
        trading_day = today in _retrying(lambda: clock.trading_days(today, today))
        res = basket.sell(broker, now, bool(c["is_open"]), trading_day)
        print(res.summary(), flush=True)
        if not trading_day or not (res.submitted or basket.working()):
            return 0
        wait_until(time(9, 33))
        notes = basket.sync(broker)
        if basket.holdings():                     # e.g. an opening-auction order was rejected: sell now instead
            retry = basket.sell(broker, ob.now_utc(), True, True)
            print(retry.summary(), flush=True)
            _t.sleep(20)
            notes += basket.sync(broker)
        night = basket.settle_night(today)
        left = basket.holdings()
        if night:
            t = basket.totals()
            msg = (f"{night['names']} names: P&L ${night['pnl']:+,.2f} ({night['ret']:+.2%}) on ${night['invested']:,.0f}. "
                   f"Running total {t['nights']} nights, ${t['pnl']:+,.2f} ({t['compounded']:+.2%}).")
            print(msg)
            alerts.send("Overnight basket sold", msg, "low")
        if left or notes:
            msg = (f"Still holding: {left}. " if left else "") + "; ".join(notes)
            print(msg)
            alerts.urgent("Overnight basket needs a look", msg)
        return 0

    print("overnight-basket needs: pick | buy | sell | status")
    return 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="options-quant")
    parser.add_argument("command", choices=["config", "check", "collect", "price", "report", "scan", "scan-report", "history", "risk-replay", "killswitch", "trade", "paper-demo", "opra-history", "databento-spend", "vol-study", "opra-widen", "backtest", "after-close", "storage", "notify", "running", "midday", "vol-data", "overnight", "overnight-basket", "momentum", "spike-short", "gap-puts", "spike-short-v2", "spike-short-v3", "spike-short-v4", "spike-short-shadow"], nargs="?", default="config")
    parser.add_argument("--env", help="development | backtest | observation | paper | live")
    parser.add_argument("--once", action="store_true", help="collect: run a single cycle")
    parser.add_argument("--cycles", type=int, help="collect: stop after N cycles")
    parser.add_argument("action", nargs="?", default="status",
                        help="killswitch: status | stop | ack-warning | reset; storage: status | cleanup | restore; "
                             "running: collector | trader (exit 0 if that job is running); "
                             "vol-data: status | fetch | load | report | forecast | condors | backtest | trend | monthly-trend | multi-trend | rel-momentum | dual-momentum; "
                             "overnight: report | refresh | fetch | liquid; overnight-basket: pick | buy | sell | status")
    parser.add_argument("--path", help="vol-data load: folder of OptionsDX files (.txt/.csv/.zip)")
    parser.add_argument("--authorization", help="killswitch reset: the exact authorization phrase")
    parser.add_argument("--from", dest="date_from", help="opra-widen: first day YYYY-MM-DD")
    parser.add_argument("--to", dest="date_to", help="opra-widen: last day YYYY-MM-DD")
    parser.add_argument("--band", type=float, default=0.06, help="opra-widen: strike band, e.g. 0.06 = ±6%%")
    parser.add_argument("--run-budget", type=float, default=20.0, help="opra-widen: max $ this run may spend")
    parser.add_argument("--weeks", type=int, default=1, help="opra-history: how many recent weeks")
    parser.add_argument("--any-time", action="store_true", help="scan: ignore the entry-time window")
    parser.add_argument("--date", help="report: trading day YYYY-MM-DD")
    parser.add_argument("--every", type=int, help="report / scan-report: minutes between snapshots (default 5 / 1)")
    parser.add_argument("--as-of", help="price: snapshot time, ISO with timezone")
    parser.add_argument("--wait-for-open", action="store_true", help="collect: wait for the market to open")
    parser.add_argument("--walk-forward", help="backtest: TRAIN,TEST window sizes in trading days")
    parser.add_argument("--grid", action="append", default=[], help="backtest: strategy.KEY=V1,V2 candidate settings")
    parser.add_argument("--equity", type=float, default=100_000.0, help="backtest: starting equity")
    parser.add_argument("--dry-run", action="store_true", help="spike-short-shadow: run against live data, save nothing")
    parser.add_argument("--title", help="notify: alert title")
    parser.add_argument("--message", help="notify: alert text")
    parser.add_argument("--priority", default="default", help="notify: min | low | default | high | urgent")
    args = parser.parse_args(argv)

    try:
        settings = load_settings(args.env)
    except ConfigError as exc:
        print(f"CONFIG ERROR: {exc}", file=sys.stderr)
        return 2

    log = setup_logging(settings)
    log.info("starting command=%s env=%s", args.command, settings.environment.value, extra={"event": "startup"})
    try:
        if args.command == "config":
            return cmd_config(settings)
        if args.command == "notify":
            return cmd_notify(settings, args.title, args.message, args.priority)
        if args.command == "after-close":
            return cmd_after_close(settings)
        if args.command == "storage":
            return cmd_storage(settings, args.action, args.date)
        if args.command == "backtest":
            return cmd_backtest(settings, args.date_from, args.date_to, args.walk_forward, args.grid, args.equity)
        if args.command == "opra-widen":
            return cmd_opra_widen(settings, args.date_from, args.date_to, args.band, args.run_budget)
        if args.command == "vol-study":
            return cmd_vol_study(settings)
        if args.command == "databento-spend":
            return cmd_databento_spend(settings)
        if args.command == "opra-history":
            return cmd_opra_history(settings, args.weeks)
        if args.command == "midday":
            return cmd_midday(settings)
        if args.command == "vol-data":
            return cmd_vol_data(settings, args.action, args.path)
        if args.command == "overnight":
            return cmd_overnight(settings, args.action)
        if args.command == "spike-short-shadow":
            return cmd_spike_short_shadow(settings, args.action, args.dry_run)
        if args.command == "spike-short-v4":
            return cmd_spike_short_v4(settings)
        if args.command == "spike-short-v3":
            return cmd_spike_short_v3(settings)
        if args.command == "spike-short-v2":
            return cmd_spike_short_v2(settings)
        if args.command == "gap-puts":
            return cmd_gap_puts(settings, args.action)
        if args.command == "spike-short":
            return cmd_spike_short(settings, args.action)
        if args.command == "overnight-basket":
            return cmd_overnight_basket(settings, args.action)
        if args.command == "momentum":
            return cmd_momentum(settings, args.action)
        if args.command == "running":
            return 0 if is_running(args.action) else 1
        if args.command == "trade":
            with single_instance("trader"):
                return cmd_trade(settings, get_logger("main"))
        if args.command == "paper-demo":
            with single_instance("trader"):
                return cmd_paper_demo(settings, get_logger("main"))
        if args.command == "killswitch":
            return cmd_killswitch(settings, args.action, args.authorization)
        if args.command == "risk-replay":
            return cmd_risk_replay(settings, get_logger("main"))
        if args.command == "history":
            return cmd_history(settings, get_logger("main"))
        if args.command == "scan-report":
            return cmd_scan_report(settings, get_logger("main"), args.date, args.every or 1)
        if args.command == "scan":
            return cmd_scan(settings, get_logger("main"), args.as_of, args.any_time)
        if args.command == "report":
            return cmd_report(settings, get_logger("main"), args.date, args.every or 5)
        if args.command == "price":
            return cmd_price(settings, get_logger("main"), args.as_of)
        if args.command == "collect":
            with single_instance("collector"):
                return cmd_collect(settings, get_logger("main"), args.once, args.cycles, args.wait_for_open)
        return cmd_check(settings, get_logger("main"))
    except AlreadyRunning as exc:                 # another copy owns the job: not an error, nothing to alert
        log.warning("%s; not starting another", exc)
        print(f"{exc}; not starting another")
        return 0
    except ConfigError as exc:
        log.error("configuration error: %s", exc)
        print(f"CONFIG ERROR: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:  # report, never dump credentials
        log.exception("startup check failed: %s", type(exc).__name__)
        print(f"ERROR: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
