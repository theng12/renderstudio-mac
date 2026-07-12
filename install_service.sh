#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
UID_NUM="$(id -u)"
LA="$HOME/Library/LaunchAgents"
SRV="com.kh.renderstudio.server"
WD="com.kh.renderstudio.watchdog"
PORT=47874

if [ ! -x "$ROOT/conda_env/bin/python" ]; then
  echo "Render Studio is not installed yet. Run Install before enabling startup service."
  exit 1
fi

mkdir -p "$LA" "$ROOT/logs/service" "$ROOT/service"
chmod +x "$ROOT/renderstudio-serve.sh" "$ROOT/renderstudio-watchdog.sh"

cat > "$LA/$SRV.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$SRV</string>
  <key>ProgramArguments</key><array><string>$ROOT/renderstudio-serve.sh</string></array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ProcessType</key><string>Interactive</string>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>StandardOutPath</key><string>$ROOT/logs/service/server.log</string>
  <key>StandardErrorPath</key><string>$ROOT/logs/service/server.err.log</string>
</dict>
</plist>
PLIST

cat > "$LA/$WD.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$WD</string>
  <key>ProgramArguments</key><array><string>$ROOT/renderstudio-watchdog.sh</string></array>
  <key>RunAtLoad</key><true/>
  <key>StartInterval</key><integer>60</integer>
  <key>StandardOutPath</key><string>$ROOT/logs/service/watchdog.log</string>
  <key>StandardErrorPath</key><string>$ROOT/logs/service/watchdog.err.log</string>
</dict>
</plist>
PLIST

launchctl bootout "gui/$UID_NUM/$SRV" 2>/dev/null || true
launchctl bootout "gui/$UID_NUM/$WD" 2>/dev/null || true
wait_gone() {
  for _ in $(seq 1 25); do
    launchctl print "gui/$UID_NUM/$1" >/dev/null 2>&1 || return 0
    sleep 0.2
  done
}
wait_gone "$SRV"
wait_gone "$WD"

PORT_PIDS="$(lsof -ti tcp:$PORT -sTCP:LISTEN 2>/dev/null || true)"
if [ -n "$PORT_PIDS" ]; then
  echo "Taking over port $PORT from the manual Pinokio instance."
  for pid in $PORT_PIDS; do kill "$pid" 2>/dev/null || true; done
  sleep 2
  STRAGGLERS="$(lsof -ti tcp:$PORT -sTCP:LISTEN 2>/dev/null || true)"
  for pid in $STRAGGLERS; do kill -9 "$pid" 2>/dev/null || true; done
fi

bootstrap() {
  launchctl bootstrap "gui/$UID_NUM" "$1" 2>/dev/null || {
    sleep 1
    launchctl bootstrap "gui/$UID_NUM" "$1"
  }
}
bootstrap "$LA/$SRV.plist"
bootstrap "$LA/$WD.plist"
launchctl kickstart "gui/$UID_NUM/$SRV" 2>/dev/null || true
touch "$ROOT/service/.installed"

echo "Render Studio KH is now an automatic startup service on port $PORT."
echo "It starts at login, restarts after crashes, and is checked every minute."
echo "Logs: $ROOT/logs/service/"
