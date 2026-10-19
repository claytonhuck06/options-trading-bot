"""options-quant entry point.

Commands:
    python main.py config   Show the validated configuration and today's dollar limits.
    python main.py check    Also connect to Alpaca (read-only) and report account status.

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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="options-quant")
    parser.add_argument("command", choices=["config", "check"], nargs="?", default="config")
    parser.add_argument("--env", help="development | backtest | observation | paper | live")
    args = parser.parse_args(argv)

    try:
        settings = load_settings(args.env)
    except ConfigError as exc:
        print(f"CONFIG ERROR: {exc}", file=sys.stderr)
        return 2

    log = setup_logging(settings)
    log.info("starting command=%s env=%s", args.command, settings.environment.value, extra={"event": "startup"})
    try:
        return cmd_config(settings) if args.command == "config" else cmd_check(settings, get_logger("main"))
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
