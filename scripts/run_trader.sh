#!/bin/zsh
# Daily PAPER trader, started by launchd on weekdays at 8:20 CT, before the open. It runs the
# morning check (orders, assignments, broker vs our records, missed days), waits for the open,
# then trades until the close. The first time, it runs the paper demo (rest/cancel/fill/close +
# reconciliation) once the market is open and only trades if that passes. Paper account only.
# Problems are pushed to the phone (ntfy).

set -u
PROJECT="${0:A:h:h}"
cd "$PROJECT" || exit 1
mkdir -p logs
LOG="logs/trader_$(date +%Y-%m-%d).log"
DEMO_OK="data/storage/.paper_demo_passed"

if [[ -z "${ALPACA_API_KEY_ID:-}" && -f "$HOME/.zshrc" ]]; then
  eval "$(grep -E '^export (ALPACA|APCA)_[A-Z_]+=' "$HOME/.zshrc")"
fi
export OPTIONS_QUANT_ENV=paper
PY="$PROJECT/.venv/bin/python"
alert() { "$PY" -W ignore main.py notify --priority urgent --title "$1" --message "$2" >> "$LOG" 2>&1; }

# Running copies are detected by the lock each job holds (`main.py running`), not by searching process names.
running() { "$PY" -W ignore main.py running "$1" >/dev/null 2>&1; }
if running trader; then
  echo "=== $(date) trader already running — not starting another ===" >> "$LOG"
  exit 0
fi
# The collector starts at 8:15 and waits for the open; give it a few minutes if it is late.
for _ in 1 2 3 4 5 6; do
  running collector && break
  sleep 30
done
if ! running collector; then
  echo "=== $(date) collector not running — no market data, not trading ===" >> "$LOG"
  alert "Bot not trading today" "The market-data collector is not running, so the trader did not start."
  exit 0
fi

echo "=== $(date) trader launch ===" >> "$LOG"
if [[ ! -f "$DEMO_OK" ]]; then
  # The demo needs the market open and the 9:35 ET entry window; the schedule starts before the open, so wait
  # (times here are the Mac's local Central time; holidays: give up at 11:00).
  until (( 10#$(date +%H%M) >= 836 )) && "$PY" -W ignore -c "import sys; from execution.broker import ReadOnlyBroker; from core.config import load_settings; \
from core.secrets import load_alpaca_credentials; s=load_settings(); \
sys.exit(0 if ReadOnlyBroker(s, load_alpaca_credentials(s)).market_clock()['is_open'] else 1)" 2>/dev/null; do
    if (( $(date +%H) >= 11 )); then echo "=== market did not open today ===" >> "$LOG"; exit 0; fi
    sleep 60
  done
  if /usr/bin/caffeinate -i "$PY" -W ignore main.py paper-demo >> "$LOG" 2>&1; then
    touch "$DEMO_OK"
  else
    echo "=== paper demo did not pass — not trading today (see docs/research/paper_demo_*.md) ===" >> "$LOG"
    alert "Paper demo FAILED" "The one-time order test did not pass, so the bot is not trading. Report: docs/research/paper_demo_$(date +%Y-%m-%d).md"
    exit 1
  fi
fi
/usr/bin/caffeinate -i "$PY" -W ignore main.py trade >> "$LOG" 2>&1
code=$?
if (( code != 0 )); then
  alert "Trader stopped with an error" "The trading program exited with code $code. See logs/trader_$(date +%Y-%m-%d).log"
fi
exit $code
