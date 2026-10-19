#!/bin/zsh
# Daily PAPER trader, started by launchd on weekdays 10 minutes after the open.
# The first time, it runs the paper demo (rest/cancel/fill/close + reconciliation) and
# only trades if that passes. Exits when the market closes. Paper account only.

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

if /usr/bin/pgrep -f "main.py (trade|paper-demo)" >/dev/null; then
  echo "=== $(date) trader already running — not starting another ===" >> "$LOG"
  exit 0
fi
if ! /usr/bin/pgrep -f "main.py collect" >/dev/null; then
  echo "=== $(date) collector not running — no market data, not trading ===" >> "$LOG"
  exit 0
fi

echo "=== $(date) trader launch ===" >> "$LOG"
if [[ ! -f "$DEMO_OK" ]]; then
  if /usr/bin/caffeinate -i "$PROJECT/.venv/bin/python" -W ignore main.py paper-demo >> "$LOG" 2>&1; then
    touch "$DEMO_OK"
  else
    echo "=== paper demo did not pass — not trading today (see docs/research/paper_demo_*.md) ===" >> "$LOG"
    exit 1
  fi
fi
exec /usr/bin/caffeinate -i "$PROJECT/.venv/bin/python" -W ignore main.py trade >> "$LOG" 2>&1
