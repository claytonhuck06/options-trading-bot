#!/bin/zsh
# Daily SPY options collector, started by launchd on weekdays before the open.
# Waits for the open, collects until the close, then exits. Skips holidays.
# Read-only: this never places orders.

set -u
PROJECT="${0:A:h:h}"
cd "$PROJECT" || exit 1
mkdir -p logs
LOG="logs/collector_$(date +%Y-%m-%d).log"

# launchd does not read ~/.zshrc, so pick up only the Alpaca key exports from it.
# (Keys stay in ~/.zshrc; nothing is copied or printed.)
if [[ -z "${ALPACA_API_KEY_ID:-}" && -f "$HOME/.zshrc" ]]; then
  eval "$(grep -E '^export (ALPACA|APCA)_[A-Z_]+=' "$HOME/.zshrc")"
fi

# Only one collector at a time. The collector holds a lock file while it runs (released automatically if it
# stops or crashes); `main.py running` checks that lock, so nothing else can be mistaken for a running copy.
if "$PROJECT/.venv/bin/python" -W ignore main.py running collector >/dev/null 2>&1; then
  echo "=== $(date) collector already running — not starting another ===" >> "$LOG"
  exit 0
fi

export OPTIONS_QUANT_ENV=paper
echo "=== $(date) collector launch ===" >> "$LOG"
# caffeinate -i keeps the Mac from idle-sleeping while collecting (closing the lid still sleeps it).
/usr/bin/caffeinate -i "$PROJECT/.venv/bin/python" -W ignore main.py collect --wait-for-open >> "$LOG" 2>&1
code=$?
if (( code != 0 )); then
  "$PROJECT/.venv/bin/python" -W ignore main.py notify --priority urgent --title "Market-data collector stopped" \
    --message "The collector exited with code $code (see logs/collector_$(date +%Y-%m-%d).log). Trading halts without fresh data." >> "$LOG" 2>&1
fi
exit $code
