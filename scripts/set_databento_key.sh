#!/bin/zsh
# Store the Databento API key in ~/.zshrc without showing it on screen or saving it in shell history.
set -u
print -n "Paste your Databento API key, then press Return (nothing will appear as you paste): "
read -rs KEY
print ""
KEY="${KEY//[[:space:]]/}"
if [[ -z "$KEY" ]]; then
  print "No key entered — nothing changed."
  exit 1
fi
cp ~/.zshrc ~/.zshrc.bak-before-databento-key
DATABENTO_NEW_KEY="$KEY" /usr/bin/python3 - <<'PY'
import os, re, pathlib
p = pathlib.Path.home() / ".zshrc"
key = os.environ["DATABENTO_NEW_KEY"]
text = p.read_text()
line = f'export DATABENTO_API_KEY="{key}"'
if re.search(r'^export DATABENTO_API_KEY=.*$', text, flags=re.M):
    text = re.sub(r'^export DATABENTO_API_KEY=.*$', lambda m: line, text, flags=re.M)
else:
    text = text.rstrip("\n") + "\n# Databento (historical options data)\n" + line + "\n"
p.write_text(text)
PY
unset KEY
print "Saved. Done — you can close this tab and tell Claude 'saved'."
