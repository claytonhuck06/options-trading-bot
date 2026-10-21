#!/bin/zsh
# Spike-short forward shadow log (research only, NO orders). launchd runs this at 8:35 CT (9:35 ET: screen the
# morning's checklist candidates and record IBKR's shortable list) and 15:30 CT (16:30 ET: record the simulated
# exit at the close) on weekdays. See research/spike_short_shadow.py.

set -u
PROJECT="${0:A:h:h}"
cd "$PROJECT" || exit 1
mkdir -p logs
LOG="logs/spike_shadow_$(date +%Y-%m).log"
if [[ -z "${ALPACA_API_KEY_ID:-}" && -f "$HOME/.zshrc" ]]; then
  eval "$(grep -E '^export (ALPACA|APCA)_[A-Z_]+=' "$HOME/.zshrc")"
fi
export OPTIONS_QUANT_ENV=paper
PY="$PROJECT/.venv/bin/python"
ACTION="${1:-}"
if [[ -z "$ACTION" ]]; then
  if (( $(date +%H) < 12 )); then ACTION=morning; else ACTION=close; fi
fi
echo "=== $(date) spike-short shadow $ACTION ===" >> "$LOG"
/usr/bin/caffeinate -i "$PY" -W ignore main.py spike-short-shadow "$ACTION" >> "$LOG" 2>&1
code=$?
echo "=== $(date) exit $code ===" >> "$LOG"
if (( code != 0 )); then
  "$PY" -W ignore main.py notify --priority high --title "Spike-short shadow $ACTION failed" \
    --message "Exit code $code. See logs/spike_shadow_$(date +%Y-%m).log" >> "$LOG" 2>&1
fi
exit $code
