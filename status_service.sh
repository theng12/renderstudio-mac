#!/bin/bash

ROOT="$(cd "$(dirname "$0")" && pwd)"
UID_NUM="$(id -u)"
SRV="com.kh.renderstudio.server"
WD="com.kh.renderstudio.watchdog"
PORT=47874

echo "Render Studio KH - startup service status"
echo ""
if [ ! -f "$ROOT/service/.installed" ]; then
  echo "Startup service is not installed."
  exit 0
fi

if launchctl print "gui/$UID_NUM/$SRV" >/dev/null 2>&1; then
  echo "Server agent: loaded"
else
  echo "Server agent: not loaded"
fi
if launchctl print "gui/$UID_NUM/$WD" >/dev/null 2>&1; then
  echo "Watchdog: scheduled every 60 seconds"
else
  echo "Watchdog: not loaded"
fi
if curl -fsS --max-time 5 "http://127.0.0.1:$PORT/api/health"; then
  echo ""
  echo "Health: ready on port $PORT"
else
  echo "Health: not responding on port $PORT"
fi
echo ""
echo "Recent service errors:"
tail -n 15 "$ROOT/logs/service/server.err.log" 2>/dev/null || echo "(none)"
