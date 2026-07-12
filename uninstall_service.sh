#!/bin/bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
UID_NUM="$(id -u)"
LA="$HOME/Library/LaunchAgents"
SRV="com.kh.renderstudio.server"
WD="com.kh.renderstudio.watchdog"

launchctl bootout "gui/$UID_NUM/$SRV" 2>/dev/null || true
launchctl bootout "gui/$UID_NUM/$WD" 2>/dev/null || true
rm -f "$LA/$SRV.plist" "$LA/$WD.plist" "$ROOT/service/.installed"

echo "Render Studio KH startup service removed."
echo "Use Pinokio's Start button when you want to run it manually."
