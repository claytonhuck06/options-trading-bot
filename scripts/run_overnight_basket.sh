#!/bin/zsh
# Overnight share basket, PAPER account only. launchd runs "buy" at 14:35 CT (market-on-close orders, then
# waits for the 16:00 ET fills) and "sell" at 8:05 CT (market-on-open orders, then waits for the fills and
# pushes the night's result to the phone). See execution/overnight_basket.py.

set -u
PROJECT="${0:A:h:h}"
cd "$PROJECT" || exit 1
mkdir -p logs
ACTION="${1:?buy or sell}"
LOG="logs/overnight_basket_$(date +%Y-%m-%d).log"
if [[ -z "${ALPACA_API_KEY_ID:-}" && -f "$HOME/.zshrc" ]]; then
  eval "$(grep -E '^export (ALPACA|APCA)_[A-Z_]+=' "$HOME/.zshrc")"
fi
export OPTIONS_QUANT_ENV=paper
PY="$PROJECT/.venv/bin/python"
echo "=== $(date) overnight basket $ACTION ===" >> "$LOG"
/usr/bin/caffeinate -i "$PY" -W ignore main.py overnight-basket "$ACTION" >> "$LOG" 2>&1
code=$?
if (( code != 0 )); then
  "$PY" -W ignore main.py notify --priority urgent --title "Overnight basket $ACTION failed" \
    --message "Exit code $code. See logs/overnight_basket_$(date +%Y-%m-%d).log" >> "$LOG" 2>&1
fi
exit $code
