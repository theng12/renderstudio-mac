#!/bin/bash

PORT=47874
LABEL="com.kh.renderstudio.server"

if ! curl -fsS --max-time 10 "http://127.0.0.1:${PORT}/api/health" >/dev/null 2>&1; then
  echo "[watchdog] $(date '+%Y-%m-%d %H:%M:%S') no /api/health on :${PORT} - restarting ${LABEL}"
  launchctl kickstart -k "gui/$(id -u)/${LABEL}" 2>/dev/null || true
fi
