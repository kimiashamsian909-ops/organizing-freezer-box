#!/bin/bash
# One-time setup on the always-on lab Mac:
#   - installs the app into ~/FreezerTracker
#   - writes config.json (asks for a lab passcode)
#   - keeps the server running (restarts after reboot) and backs up nightly at 2 am
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
APP="$HOME/FreezerTracker"
AGENTS="$HOME/Library/LaunchAgents"

mkdir -p "$APP" "$AGENTS"
rsync -a --exclude .git --exclude freezer.db --exclude config.json --exclude tests --exclude __pycache__ "$SRC/" "$APP/"

if [[ ! -f "$APP/config.json" ]]; then
  read -r -p "Choose a lab passcode (lab mates type it once; leave blank for none): " PASS
  /usr/bin/python3 - "$SRC/config.example.json" "$APP/config.json" "$PASS" <<'PY'
import json, sys
c = json.load(open(sys.argv[1])); c["passcode"] = sys.argv[3]
json.dump(c, open(sys.argv[2], "w"), indent=2)
PY
  echo "Wrote $APP/config.json"
fi

if [[ ! -f "$APP/freezer.db" ]]; then
  read -r -p "Start with example data? (y/N) " EX
  if [[ "$EX" =~ ^[Yy] ]]; then (cd "$APP" && /usr/bin/python3 seed.py); fi
fi

write_agent() {  # label, program args..., extra plist keys
  local label="$1" keys="$2"; shift 2
  local args=""
  for a in "$@"; do args="$args<string>$a</string>"; done
  cat > "$AGENTS/$label.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>$label</string>
  <key>ProgramArguments</key><array>$args</array>
  <key>WorkingDirectory</key><string>$APP</string>
  <key>StandardOutPath</key><string>$APP/$label.log</string>
  <key>StandardErrorPath</key><string>$APP/$label.log</string>
  $keys
</dict></plist>
PLIST
  launchctl unload "$AGENTS/$label.plist" 2>/dev/null || true
  launchctl load "$AGENTS/$label.plist"
}

write_agent org.lab.freezer-tracker "<key>RunAtLoad</key><true/><key>KeepAlive</key><true/>" \
  /usr/bin/python3 "$APP/server.py" --no-browser
write_agent org.lab.freezer-backup "<key>StartCalendarInterval</key><dict><key>Hour</key><integer>2</integer><key>Minute</key><integer>0</integer></dict>" \
  /bin/bash "$APP/backup.sh"

PORT="$(/usr/bin/python3 -c "import json; print(json.load(open('$APP/config.json'))['port'])")"
echo
echo "Freezer Tracker is running."
echo "  On this Mac:   http://localhost:$PORT/"
echo "  Lab mates:     http://$(scutil --get LocalHostName 2>/dev/null || hostname).local:$PORT/   or   http://$(ipconfig getifaddr en0 2>/dev/null || echo '<this-mac-ip>'):$PORT/"
echo "If macOS asks whether Python may accept incoming connections, click Allow."
