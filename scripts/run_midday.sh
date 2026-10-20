#!/bin/zsh
# Midday on weekdays (12:00 CT): push a short check-in to the phone (jobs alive, data fresh, screener
# so far, trades so far). Read-only; never places orders.

set -u
PROJECT="${0:A:h:h}"
cd "$PROJECT" || exit 1
mkdir -p logs
LOG="logs/midday_$(date +%Y-%m-%d).log"
if [[ -z "${ALPACA_API_KEY_ID:-}" && -f "$HOME/.zshrc" ]]; then
  eval "$(grep -E '^export (ALPACA|APCA)_[A-Z_]+=' "$HOME/.zshrc")"
fi
export OPTIONS_QUANT_ENV=paper
echo "=== $(date) midday ===" >> "$LOG"
exec "$PROJECT/.venv/bin/python" -W ignore main.py midday >> "$LOG" 2>&1
