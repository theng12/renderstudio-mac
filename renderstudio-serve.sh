#!/bin/bash
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
export PATH="$HERE/conda_env/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin"
export PYTHONUNBUFFERED=1

cd "$HERE/app"
exec "$HERE/conda_env/bin/python" -m uvicorn backend.main:app \
  --host 0.0.0.0 --port 47874
