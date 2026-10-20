#!/bin/zsh
# Dual momentum, PAPER account only. launchd runs this at 8:10 CT every weekday; it trades only on the first
# trading day of a month (or the next weekday, if that day was missed): opening-auction orders, then it waits
# for the 9:30 ET fills and pushes the result to the phone. See execution/dual_momentum_paper.py.

set -u
PROJECT="${0:A:h:h}"
cd "$PROJECT" || exit 1
mkdir -p logs
LOG="logs/momentum_$(date +%Y-%m).log"
if [[ -z "${ALPACA_API_KEY_ID:-}" && -f "$HOME/.zshrc" ]]; then
  eval "$(grep -E '^export (ALPACA|APCA)_[A-Z_]+=' "$HOME/.zshrc")"
fi
export OPTIONS_QUANT_ENV=paper
PY="$PROJECT/.venv/bin/python"
echo "=== $(date) dual momentum rebalance ===" >> "$LOG"
/usr/bin/caffeinate -i "$PY" -W ignore main.py momentum rebalance >> "$LOG" 2>&1
code=$?
if (( code != 0 )); then
  "$PY" -W ignore main.py notify --priority urgent --title "Dual momentum rebalance failed" \
    --message "Exit code $code. See logs/momentum_$(date +%Y-%m).log" >> "$LOG" 2>&1
fi
exit $code
