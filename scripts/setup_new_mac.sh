#!/bin/zsh
# One-step setup after copying this whole project folder to a new Mac. Run it in Terminal:
#   zsh ~/"Options Trading bot"/scripts/setup_new_mac.sh
# It rebuilds the Python environment, asks for any missing keys (typed in hidden, saved to ~/.zshrc like
# set_databento_key.sh), checks the Alpaca paper connection and the tests, then turns on the weekday jobs in
# JOBS (collector 8:15, trader 8:20, midday 12:00, after-close 3:20, overnight basket, momentum, spike-short
# shadow log 8:35 and 3:30, Central time). Safe to run again.
# Nothing here places orders.

set -u
PROJECT="${0:A:h:h}"
cd "$PROJECT" || exit 1
OLD_PROJECT="/Users/claytonhuck/Options Trading bot"   # path baked into the .plist files in scripts/
PAPER_URL="https://paper-api.alpaca.markets"
JOBS=(collector trader midday afterclose overnight-buy overnight-sell momentum spike-shadow)

step() { print "\n== $1"; }
fail() { print "\nSTOPPED: $1"; print "Nothing was turned on. Fix the above (or ask Claude), then run this script again."; exit 1; }
ask_yes() { local a; read -r "a?$1 (yes/no): "; [[ "${a:l}" == y || "${a:l}" == yes ]]; }

print "Options bot — new Mac setup"
print "Project folder: $PROJECT"
print "\nIMPORTANT: the bot must be turned OFF on the old laptop, or both will trade the same paper account."
ask_yes "Is the bot turned off on the old laptop?" || { print "Turn it off there first, then run this again."; exit 1; }

step "1/7  Python 3.13"
PY313=""
for p in /Library/Frameworks/Python.framework/Versions/3.13/bin/python3.13 "$(command -v python3.13 2>/dev/null)"; do
  [[ -n "$p" && -x "$p" ]] && { PY313="$p"; break; }
done
if [[ -z "$PY313" ]]; then
  print "Python 3.13 is not installed. Opening the free download page — install the macOS 3.13 version, then run this again."
  open "https://www.python.org/downloads/macos/"
  exit 1
fi
print "Found $("$PY313" --version)"

step "2/7  Rebuilding the bot's Python environment (a few minutes)"
# Files arriving by AirDrop are tagged as downloaded; clear that so the scheduled scripts run without prompts.
xattr -dr com.apple.quarantine "$PROJECT" 2>/dev/null
chmod +x scripts/*.sh
mkdir -p logs
rm -rf .venv
"$PY313" -m venv .venv || fail "could not create the Python environment."
.venv/bin/python -m pip install --quiet --upgrade pip >/dev/null
.venv/bin/python -m pip install --quiet -r requirements.txt || fail "installing the bot's packages failed (check the internet connection)."
PY="$PROJECT/.venv/bin/python"
print "Done."

step "3/7  Keys (Alpaca paper account, Databento)"
touch ~/.zshrc
have_key() { grep -qE "^export $1=.+" ~/.zshrc; }
NEW_KEYS=()
for name in ALPACA_API_KEY_ID ALPACA_SECRET_KEY DATABENTO_API_KEY; do
  if have_key "$name"; then
    print "$name: already saved"
  else
    read -rs "val?Paste $name, then press Return (nothing will appear): "
    print ""
    val="${val//[[:space:]]/}"
    [[ -z "$val" ]] && fail "no value entered for $name."
    NEW_KEYS+=("$name=$val")
  fi
done
have_key ALPACA_BASE_URL || NEW_KEYS+=("ALPACA_BASE_URL=$PAPER_URL")
if (( ${#NEW_KEYS} )); then
  cp ~/.zshrc ~/.zshrc.bak-before-bot-setup
  # Values go through the environment, never the command line, so they don't show up in process listings.
  BOT_NEW_KEYS="${(F)NEW_KEYS}" "$PY313" - <<'PY'
import os, pathlib, re
p = pathlib.Path.home() / ".zshrc"
text = p.read_text()
for item in os.environ["BOT_NEW_KEYS"].splitlines():
    name, _, value = item.partition("=")
    line = f'export {name}="{value}"'
    if re.search(rf"^export {name}=.*$", text, flags=re.M):
        text = re.sub(rf"^export {name}=.*$", lambda m: line, text, flags=re.M)
    else:
        text = text.rstrip("\n") + "\n" + line + "\n"
p.write_text(text)
PY
  unset NEW_KEYS val
  print "Saved to ~/.zshrc."
fi
eval "$(grep -E '^export (ALPACA|APCA|DATABENTO)_[A-Z_]+=' ~/.zshrc)"
[[ "${ALPACA_BASE_URL%/}" == "$PAPER_URL" ]] || fail "ALPACA_BASE_URL in ~/.zshrc is not the paper account ($PAPER_URL)."

step "4/7  Time zone"
TZ_NOW="$(readlink /etc/localtime)"
if [[ "$TZ_NOW" != *America/Chicago ]]; then
  print "This Mac is set to ${TZ_NOW##*/zoneinfo/}, but the daily schedule is in Central time."
  print "Set System Settings > General > Date & Time > Time zone to Chicago (Central), then run this again."
  exit 1
fi
print "Central time — OK."

step "5/7  Checking the connection to your Alpaca paper account"
export OPTIONS_QUANT_ENV=paper
"$PY" -W ignore main.py check || fail "could not connect to Alpaca with these keys. Re-copy them from the Alpaca paper dashboard; to replace a saved key, delete its line in ~/.zshrc and run this again."

step "6/7  Running the bot's tests"
"$PY" -W ignore -m pytest -q -p no:warnings >> logs/setup_tests.log 2>&1
(( $? == 0 )) && print "All tests passed." || fail "some tests failed (details in logs/setup_tests.log)."

step "7/7  Turning on the weekday schedule"
mkdir -p ~/Library/LaunchAgents
for job in $JOBS; do
  src="scripts/com.optionsquant.$job.plist"
  dst="$HOME/Library/LaunchAgents/com.optionsquant.$job.plist"
  BOT_OLD="$OLD_PROJECT" BOT_NEW="$PROJECT" "$PY313" -c '
import os, sys
src, dst = sys.argv[1:]
open(dst, "w").write(open(src).read().replace(os.environ["BOT_OLD"], os.environ["BOT_NEW"]))' "$src" "$dst"
  plutil -lint -s "$dst" || fail "the schedule file for $job is damaged."
  launchctl bootout "gui/$(id -u)/com.optionsquant.$job" 2>/dev/null
  launchctl bootstrap "gui/$(id -u)" "$dst" || fail "could not turn on the $job job."
  print "on: $job"
done

"$PY" -W ignore main.py notify --title "Bot set up on new Mac" --message "Setup finished. Daily jobs start at 8:15 CT on the next weekday." >/dev/null 2>&1 \
  && print "\nSent a test alert to your phone (ntfy). If it didn't arrive, check the ntfy app is subscribed." \
  || print "\nCould not send the test phone alert — check OPTIONS_QUANT_NTFY_TOPIC in .env."

print "\nKeep this Mac awake during market hours:"
print "  - leave it plugged in with the lid OPEN (closing the lid puts a MacBook Air to sleep)"
print "  - stay logged in; after a restart, log back in or nothing runs"
if ask_yes "Stop this Mac from sleeping while plugged in? (asks for your Mac password)"; then
  sudo pmset -c sleep 0 disksleep 0 && print "Done — it won't sleep on its own while plugged in (the screen can still turn off)."
else
  print "OK. You can do it later in System Settings > Battery > Options > \"Prevent automatic sleeping on power adapter when the display is off\"."
fi

print "\nAll set. The bot starts at 8:15 a.m. Central on the next weekday."
