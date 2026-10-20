# options-quant

Local-first quantitative options trading research platform (SPY first), built
phase by phase per the master plan. See [docs/MILESTONES.md](docs/MILESTONES.md)
for status.

**Current state: Phase 6 — automatic PAPER trading (Alpaca paper account only; no live-money code).
Overnight holding is allowed for multi-day strategies (paper only); see docs/MILESTONES.md.**

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

Alpaca keys are read from your shell (`ALPACA_API_KEY_ID`, `ALPACA_SECRET_KEY`,
`ALPACA_BASE_URL`, or Alpaca's `APCA_*` names), or from a git-ignored `.env`
file (see `.env.example`). Keys are never printed or logged.

## Run

```bash
.venv/bin/python main.py config            # validated config + dollar limits
.venv/bin/python main.py check --env paper # + read-only Alpaca connection check
.venv/bin/python main.py collect --env paper       # collect SPY options all session
.venv/bin/python main.py collect --env paper --once # one cycle, for a quick test
.venv/bin/python main.py price --env paper          # price the latest snapshot
.venv/bin/python main.py report --env paper         # residual research report for the latest day
.venv/bin/python main.py scan --env paper           # candidate spreads right now, with reasons (no orders)
.venv/bin/python main.py scan-report --env paper    # replay the scanner over the latest day
.venv/bin/python main.py history --env paper        # download SPY minute bars since 2016 (free)
.venv/bin/python main.py risk-replay --env paper    # stress calibration + 10-year risk-control replay
.venv/bin/python main.py killswitch --env paper     # status | stop | ack-warning | reset --authorization "..."
.venv/bin/python main.py paper-demo --env paper     # end-to-end order test on the paper account (market hours)
.venv/bin/python main.py trade --env paper          # paper-trade until the close (collector must be running)
.venv/bin/python main.py backtest --env paper --from 2026-09-21 --to 2026-10-16 --walk-forward 10,5
.venv/bin/python main.py after-close --env paper    # daily summary to the phone + storage cleanup
.venv/bin/python main.py storage status --env paper # live-data storage: status | cleanup | restore --date D
.venv/bin/python main.py notify --env paper --title "Hi" --message "test"   # phone alert
.venv/bin/python -m pytest                 # test suite
```

## Daily automatic collection

A macOS launchd job (`com.optionsquant.collector`) runs `scripts/run_collector.sh`
at 8:15 a.m. local (Central) time Monday–Friday. It waits for the 9:30 ET open,
collects until the close, and exits on holidays. Logs: `logs/collector_<date>.log`.

- The Mac must be on and awake. The job keeps it from idle-sleeping while it
  collects, but closing the lid still sleeps it. A run missed during sleep starts
  when the Mac wakes.
- Keys are read from the `export ALPACA_...` lines in `~/.zshrc`.

```bash
launchctl bootout gui/$(id -u)/com.optionsquant.collector   # turn off
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.optionsquant.collector.plist  # turn on
```

## Daily automatic paper trading

A second launchd job (`com.optionsquant.trader`) runs `scripts/run_trader.sh` at 8:20 a.m. Central on weekdays,
before the open. It runs the morning check (unknown orders, overnight assignments, broker vs our records, missed days),
waits for the open, then trades until the close. It needs the collector running. The first time it runs the paper
demo, and it only trades if that passes. Log: `logs/trader_<date>.log`.

## Phone alerts and the daily summary

Alerts go through ntfy (free). Install the **ntfy** app and subscribe to the topic in `.env`
(`OPTIONS_QUANT_NTFY_TOPIC`; keep it private — anyone with the name can read the alerts). Urgent events are pushed at
once: kill switch, early assignment, a position closed after an overnight gap, a halt at startup, a failed paper
demo, the collector or trader stopping, missed trading days. Trades arrive in one summary after the close: a third
launchd job (`com.optionsquant.afterclose`, 3:20 p.m. Central) runs `main.py after-close`, which also cleans up old
live data (newest 5 days full detail, then one quote per minute, archived to compressed files after 30 days).

**To stop trading immediately**, any of these work:
- create a file named `STOP` in the project folder (the kill switch halts and flattens on the next cycle)
- `.venv/bin/python main.py killswitch stop --env paper`
- set `trading.enabled: false` in `config/environments/paper.yaml`
- `launchctl bootout gui/$(id -u)/com.optionsquant.trader` (turns off the daily start)

## Architecture

| Folder | Role | Phase |
|---|---|---|
| `config/` | `settings.yaml`, `risk_limits.yaml`, one file per environment | 1 |
| `core/` | config loading/validation, credentials, logging | 1 |
| `execution/broker.py` | read-only Alpaca access (no order methods) | 1 |
| `risk/limits.py` | percentage limits → today's dollar limits | 1 |
| `data/` | collector, quote validators, SQLite storage (`data/storage/options_quant.db`) | 2 |
| `pricing/` | Black-Scholes, Greeks, implied vol, American tree, rates, smile fits, pricing engine | 3 |
| `research/` | pricing-residual and scanner-replay reports | 3, 4 |
| `strategies/` | vertical spread builder, scoring and rejection rules | 4 |
| `risk/` | sizing, positions, risk engine, exits, hedging, kill switch, history-calibrated stress | 5 |
| `execution/` | paper broker, order manager, reconciliation, crash recovery, trading session | 6 |
| `backtesting/`, `research/` | event-driven backtests, pattern discovery | 7, 10 |
| `agents/` | Qwen analyst (read-only, advisory) | 9 |
| `monitoring/` | dashboard, alerts | 12 |

### Safety rules enforced in code
- One authoritative config. Unknown keys, out-of-range values and inconsistent
  limits stop the program at startup.
- Risk limits live only in `risk_limits.yaml`; environment files cannot override them.
- Uncovered shorts, automatic risk increases and autonomous strategy changes are
  rejected if switched on. Overnight positions are allowed only for multi-day
  strategies, in paper trading, from their own risk pool (owner decision 2026-10-19).
- Trading defaults to off and cannot be turned on in development, backtest or
  observation environments.
- A paper environment refuses to start if pointed at a live URL or live key.
- `live` requires `OPTIONS_QUANT_LIVE_AUTHORIZATION` set to an exact phrase,
  and is refused outright in this build.
- Logs are scrubbed of anything that looks like a key or secret.
- A test fails if any order-writing broker call appears in project code.

### Environments
`development` (default) · `backtest` · `observation` · `paper` · `live` —
select with `--env` or `OPTIONS_QUANT_ENV`.
