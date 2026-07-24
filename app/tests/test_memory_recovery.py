from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import importlib
import json
from pathlib import Path

import pytest


@pytest.fixture
def worker(tmp_path, monkeypatch):
    monkeypatch.setenv("RENDERSTUDIO_DATA_DIR", str(tmp_path))
    from backend import main
    return importlib.reload(main)


@pytest.mark.parametrize(
    "detail",
    [
        "Cannot allocate memory",
        "FFmpeg: out of memory",
        "av_malloc failed",
        "terminate called after throwing std::bad_alloc",
    ],
)
def test_memory_classifier_requires_explicit_allocator_evidence(worker, detail):
    assert worker._is_process_memory_failure(detail) is True


@pytest.mark.parametrize(
    "detail",
    [
        "resource temporarily unavailable",
        "invalid data found when processing input",
        "network connection reset",
        "disk quota exceeded",
    ],
)
def test_memory_classifier_rejects_unrelated_failures(worker, detail):
    assert worker._is_process_memory_failure(detail) is False


def test_ffmpeg_memory_failure_retries_once_and_cleans_new_partial(
    worker, monkeypatch
):
    attempts = []
    retry_saw_partial = []
    log = worker.JOBS / "oom-retry.log"
    partial = worker.OUTPUTS / "render.partial.mp4"

    async def fake_run(_argv, target_log, **_kwargs):
        attempts.append(len(attempts) + 1)
        if len(attempts) == 1:
            partial.write_bytes(b"incomplete")
            with target_log.open("a") as handle:
                handle.write("Cannot allocate memory\n")
            return 1
        retry_saw_partial.append(partial.exists())
        return 0

    monkeypatch.setattr(worker, "_run_process", fake_run)
    code = asyncio.run(worker._run_ffmpeg_with_memory_retry(
        ["ffmpeg", "-i", "input", str(partial)],
        log,
        cleanup_path=partial,
    ))

    assert code == 0
    assert attempts == [1, 2]
    assert retry_saw_partial == [False]
    assert worker._memory_recovery_status()["consecutive_failures"] == 0
    assert "retrying once" in log.read_text()


def test_non_memory_ffmpeg_failure_is_not_retried(worker, monkeypatch):
    attempts = []
    log = worker.JOBS / "normal-failure.log"

    async def fake_run(_argv, target_log, **_kwargs):
        attempts.append(1)
        with target_log.open("a") as handle:
            handle.write("Invalid data found when processing input\n")
        return 1

    monkeypatch.setattr(worker, "_run_process", fake_run)
    code = asyncio.run(worker._run_ffmpeg_with_memory_retry(
        ["ffmpeg", "-i", "input", "output.mp4"], log
    ))

    assert code == 1
    assert attempts == [1]
    assert worker._memory_recovery_status()["consecutive_failures"] == 0


def test_repeated_ffmpeg_oom_is_reported_without_parent_restart(
    worker, monkeypatch
):
    attempts = []
    log = worker.JOBS / "repeated-oom.log"

    async def fake_run(_argv, target_log, **_kwargs):
        attempts.append(1)
        with target_log.open("a") as handle:
            handle.write("Error allocating memory\n")
        return 1

    monkeypatch.setattr(worker, "_run_process", fake_run)
    code = asyncio.run(worker._run_ffmpeg_with_memory_retry(
        ["ffmpeg", "-i", "input", "output.mp4"], log
    ))
    status = worker._memory_recovery_status()

    assert code == 1
    assert attempts == [1, 1]
    assert status["strategy"] == "retry_ffmpeg_once"
    assert status["parent_restart_on_ffmpeg_oom"] is False
    assert status["consecutive_failures"] == 2
    assert status["last_event"]["type"] == "ffmpeg_memory_allocation_failure"
    assert set(status["last_event"]["memory"]) == {
        "total_bytes", "available_bytes", "used_percent"
    }


def test_health_exposes_privacy_safe_memory_and_restart_telemetry(
    worker, monkeypatch
):
    monkeypatch.setattr(worker, "_tool_path", lambda _name: "/usr/bin/tool")
    monkeypatch.setattr(worker, "_has_videotoolbox", lambda: False)
    monkeypatch.setattr(
        worker,
        "restart_rate_snapshot",
        lambda: {
            "status": "healthy",
            "alert": False,
            "restarts_24h": 0,
            "restarts_7d": 0,
            "last_restart_at": None,
            "observed_at": "2026-07-24T08:00:00",
            "message": "No watchdog restarts observed in the last 7 days",
        },
    )

    payload = worker.health()

    assert set(payload["memory"]) == {
        "total_bytes", "available_bytes", "used_percent"
    }
    assert payload["memory_recovery"]["parent_restart_on_ffmpeg_oom"] is False
    assert payload["restart_health"]["status"] == "healthy"
    assert not any(
        key in json.dumps(payload).casefold()
        for key in ("prompt", "customer", "output_path", "job_id")
    )


def test_restart_rate_snapshot_alerts_after_repeated_watchdog_restarts(tmp_path):
    from backend.restart_health import restart_rate_snapshot

    now = datetime(2026, 7, 24, 8, 0, 0)
    log = tmp_path / "watchdog.log"
    first = now - timedelta(hours=2)
    second = now - timedelta(minutes=10)
    log.write_text(
        f"[watchdog] {first:%Y-%m-%d %H:%M:%S} health probe failed 3 consecutive times — restarting\n"
        f"[watchdog] {second:%Y-%m-%d %H:%M:%S} health probe failed 3 consecutive times — restarting\n"
    )

    snapshot = restart_rate_snapshot(log, now=now)

    assert snapshot["status"] == "warning"
    assert snapshot["alert"] is True
    assert snapshot["restarts_24h"] == 2
