#!/bin/bash
set -euo pipefail

LABEL="com.kh.renderstudio.server"
if launchctl kickstart -k "gui/$(id -u)/$LABEL" 2>/dev/null; then
  echo "Restart signal sent to $LABEL."
  echo "Wait a few seconds, then use Check Service Status."
else
  echo "The startup service is not installed."
  exit 1
fi
