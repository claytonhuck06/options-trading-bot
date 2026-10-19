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

Phase 1 cannot submit orders: there is no order-submission code in the project.
"""

from __future__ import annotations

import argparse
import sys

from core.config import ConfigError, load_settings
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="options-quant")
    parser.add_argument("command", choices=["config", "check", "collect", "price", "report"], nargs="?", default="config")
    parser.add_argument("--env", help="development | backtest | observation | paper | live")
    parser.add_argument("--once", action="store_true", help="collect: run a single cycle")
    parser.add_argument("--cycles", type=int, help="collect: stop after N cycles")
    parser.add_argument("--date", help="report: trading day YYYY-MM-DD")
    parser.add_argument("--every", type=int, default=5, help="report: minutes between snapshots")
    parser.add_argument("--as-of", help="price: snapshot time, ISO with timezone")
    parser.add_argument("--wait-for-open", action="store_true", help="collect: wait for the market to open")
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
        if args.command == "report":
            return cmd_report(settings, get_logger("main"), args.date, args.every)
        if args.command == "price":
            return cmd_price(settings, get_logger("main"), args.as_of)
        if args.command == "collect":
            return cmd_collect(settings, get_logger("main"), args.once, args.cycles, args.wait_for_open)
        return cmd_check(settings, get_logger("main"))
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
