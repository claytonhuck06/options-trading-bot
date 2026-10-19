# options-quant

Local-first quantitative options trading research platform (SPY first), built
phase by phase per the master plan. See [docs/MILESTONES.md](docs/MILESTONES.md)
for status.

**Current state: Phase 2 market-data collector (read-only). The system cannot place, change or cancel
orders — that code does not exist yet.**

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

## Architecture

| Folder | Role | Phase |
|---|---|---|
| `config/` | `settings.yaml`, `risk_limits.yaml`, one file per environment | 1 |
| `core/` | config loading/validation, credentials, logging | 1 |
| `execution/broker.py` | read-only Alpaca access (no order methods) | 1 |
| `risk/limits.py` | percentage limits → today's dollar limits | 1 |
| `data/` | collector, quote validators, SQLite storage (`data/storage/options_quant.db`) | 2 |
| `pricing/` | Black-Scholes, implied vol, surface, Greeks | 3 |
| `strategies/` | relative-value scanner, spread builder | 4 |
| `risk/` | sizing, portfolio risk, hedging, kill switch | 5 |
| `execution/` | orders, reconciliation | 6 |
| `backtesting/`, `research/` | event-driven backtests, pattern discovery | 7, 10 |
| `agents/` | Qwen analyst (read-only, advisory) | 9 |
| `monitoring/` | dashboard, alerts | 12 |

### Safety rules enforced in code
- One authoritative config. Unknown keys, out-of-range values and inconsistent
  limits stop the program at startup.
- Risk limits live only in `risk_limits.yaml`; environment files cannot override them.
- Uncovered shorts, overnight positions, automatic risk increases and autonomous
  strategy changes are rejected if switched on.
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
