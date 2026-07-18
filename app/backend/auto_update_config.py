"""Render Studio's fixed, non-user-editable updater identity."""
from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from .auto_update import AutoUpdater


ROOT = Path(__file__).resolve().parents[2]
SPEC = {
    "root": str(ROOT),
    "title": "Render Studio KH",
    "slug": "renderstudio",
    "expected_remote": "https://github.com/theng12/renderstudio-mac.git",
    "branch": "main",
    "port": 47874,
    "server_label": "com.kh.renderstudio.server",
    "watchdog_label": "com.kh.renderstudio.watchdog",
    "default_hour": 6,
    "default_weekday": 6,
    "verify_module": "backend.main",
}


def create_updater(readiness: Optional[Callable[[], list[str]]] = None, **kwargs) -> AutoUpdater:
    return AutoUpdater(SPEC, readiness=readiness, **kwargs)
