#!/bin/zsh
# After the close on weekdays (15:20 CT): push the daily summary to the phone, then clean up
# old live market data. Never places orders.

set -u
PROJECT="${0:A:h:h}"
cd "$PROJECT" || exit 1
mkdir -p logs
LOG="logs/after_close_$(date +%Y-%m-%d).log"
if [[ -z "${ALPACA_API_KEY_ID:-}" && -f "$HOME/.zshrc" ]]; then
  eval "$(grep -E '^export (ALPACA|APCA)_[A-Z_]+=' "$HOME/.zshrc")"
fi
export OPTIONS_QUANT_ENV=paper
echo "=== $(date) after-close ===" >> "$LOG"
exec "$PROJECT/.venv/bin/python" -W ignore main.py after-close >> "$LOG" 2>&1
