import asyncio
import hashlib
import importlib
import json
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def worker(tmp_path, monkeypatch):
    monkeypatch.setenv("RENDERSTUDIO_DATA_DIR", str(tmp_path))
    from backend import main
    return importlib.reload(main)


def recipe(url="http://hub.test/input", checksum=None):
    checksum = checksum or hashlib.sha256(b"input").hexdigest()
    return {
        "version": 1,
        "assets": [{"id": "input", "url": url, "sha256": checksum}],
        "steps": [{"tool": "ffmpeg", "args": [
            "-y", "-i", "{{asset:input}}", "-c:v", "{{video_encoder}}",
            "{{output}}",
        ]}],
    }


def test_recipe_accepts_confined_ffmpeg_job(worker):
    worker._validate_recipe(recipe())


@pytest.mark.parametrize("bad", [
    {"version": 2, "assets": [], "steps": []},
    {"version": 1, "assets": [], "steps": [{"tool": "sh", "args": ["x"]}]},
])
def test_recipe_rejects_unsupported_shapes(worker, bad):
    with pytest.raises(ValueError):
        worker._validate_recipe(bad)


def test_recipe_rejects_local_and_direct_network_arguments(worker):
    for value in ("/etc/passwd", "../outside", "http://unverified/input"):
        value_recipe = recipe()
        value_recipe["steps"][0]["args"].insert(0, value)
        with pytest.raises(ValueError):
            worker._validate_recipe(value_recipe)


def test_placeholder_resolution(worker, tmp_path):
    asset = tmp_path / "asset"
    output = tmp_path / "out.mp4"
    value = worker._resolve_arg(
        "subtitles={{asset:captions}}:fontsdir={{work:fonts}}",
        {"captions": asset}, tmp_path, output, "h264_videotoolbox")
    assert str(asset) in value
    assert str(tmp_path / "fonts") in value


def test_cleanup_waits_for_ack_and_honors_pin(worker, tmp_path, monkeypatch):
    monkeypatch.setattr(worker.time, "time", lambda: 1_000_000)
    worker.SETTINGS_FILE.write_text(json.dumps({"retention_days": 1, "minimum_free_gb": 1}))
    output = worker.OUTPUTS / "job.mp4"
    output.write_bytes(b"video")
    job = {"id": "job", "state": "done", "acked_at": 1,
           "output_path": str(output), "recipe": {"assets": []}, "pinned": True}
    worker.jobs["job"] = job
    assert worker._cleanup_expired()["purged_jobs"] == 0
    job["pinned"] = False
    assert worker._cleanup_expired()["purged_jobs"] == 1
    assert not output.exists()


def test_health_score_prefers_newer_chip_and_memory(worker, monkeypatch):
    monkeypatch.setattr(worker.platform, "system", lambda: "Other")
    monkeypatch.setattr(worker.platform, "processor", lambda: "Apple M4")
    facts = worker._hardware()
    assert facts["render_score"] >= 400


def test_version_contract_uses_release_file(worker):
    client = TestClient(worker.app)
    expected = (Path(worker.__file__).parents[2] / "VERSION").read_text().strip()
    assert worker.VERSION == expected
    assert client.get("/api/health").json()["app_version"] == expected
    payload = client.get("/api/version").json()
    assert payload["app_version"] == expected
    assert payload["version"] == expected
    assert payload["title"] == "Render Studio KH"


def test_update_status_compares_semantic_versions(worker, monkeypatch):
    monkeypatch.setattr(worker, "_schedule_update_check", lambda: None)
    worker.update_state.update(latest="99.0.0", checking=False)
    payload = worker.update_status()
    assert payload["app_version"] == worker.VERSION
    assert payload["latest_version"] == "99.0.0"
    assert payload["update_available"] is True
    assert worker._parse_version("v1.12.3") > worker._parse_version("1.9.9")


def test_saved_fleet_token_takes_effect_without_restart(worker, monkeypatch):
    from backend import fleet_auth

    monkeypatch.setattr(fleet_auth, "load_token", lambda: "rotated-token")
    accepted = TestClient(worker.app, headers={"X-Studio-Token": "rotated-token"})
    stale = TestClient(worker.app, headers={"X-Studio-Token": worker.FLEET_TOKEN})
    assert accepted.get("/api/dashboard").status_code == 200
    assert stale.get("/api/dashboard").status_code == 401


def test_dashboard_reports_durable_lifetime_work(worker, monkeypatch):
    monkeypatch.setattr(worker.time, "time", lambda: 1_000)
    output = worker.OUTPUTS / "finished.mp4"
    output.write_bytes(b"video")
    worker.jobs.update({
        "finished": {
            "id": "finished", "label": "storystudio:EP001", "state": "done",
            "created_at": 700, "started_at": 750, "finished_at": 870,
            "duration_seconds": 120, "bytes": 500, "encoder": "h264_videotoolbox",
            "acked_at": 900, "output_path": str(output),
            "media": {"format": {"duration": "300.5"}},
        },
        "failed": {
            "id": "failed", "label": "storystudio:EP002", "state": "error",
            "created_at": 880, "started_at": 900, "finished_at": 930,
            "error": "ffmpeg failed",
        },
    })
    result = worker._dashboard_snapshot()
    assert result["totals"]["completed"] == 1
    assert result["totals"]["failed"] == 1
    assert result["totals"]["render_seconds"] == 150
    assert result["totals"]["video_seconds"] == 300.5
    assert result["totals"]["success_rate"] == 50
    assert result["totals"]["retained"] == 1
    assert result["totals"]["encoders"] == {"h264_videotoolbox": 1}


def test_dashboard_survives_purged_job_and_keeps_video_total(worker, monkeypatch):
    monkeypatch.setattr(worker.time, "time", lambda: 1_000)
    worker.jobs["finished"] = {
        "id": "finished", "label": "storystudio:EP001", "state": "done",
        "created_at": 700, "started_at": 750, "finished_at": 870,
        "duration_seconds": 120, "bytes": 500, "encoder": "libx264",
        "acked_at": 900, "media": {"format": {"duration": "300.5"}},
        "video_seconds": 300.5,
    }
    # Retention/clean purge clears media to None; the dashboard must not crash
    # and the durable video total must survive.
    worker.jobs["finished"].update(state="purged", output_path=None,
                                   output_url=None, media=None, purged_at=950)
    result = worker._dashboard_snapshot()
    assert result["totals"]["completed"] == 1
    assert result["totals"]["video_seconds"] == 300.5
    # A legacy purged job saved before the durable scalar existed must not raise.
    worker.jobs["legacy"] = {"id": "legacy", "state": "purged", "media": None,
                             "created_at": 600}
    assert worker._dashboard_snapshot()["totals"]["video_seconds"] == 300.5


def test_hub_connection_test_is_authenticated(worker, monkeypatch):
    calls = []

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url, headers=None):
            calls.append((url, headers))
            if url.endswith("/api/version"):
                return httpx.Response(200, json={"app_version": "1.22.1"})
            return httpx.Response(200, json={"ok": True})

    monkeypatch.setattr(worker.httpx, "AsyncClient", lambda **_kwargs: Client())
    result = asyncio.run(worker._test_hub_connection(force=True))
    assert result["ok"] is True and result["version"] == "1.22.1"
    assert calls[0][1] == {"X-Hub-Token": worker.FLEET_TOKEN}


def test_hub_url_validation(worker):
    assert worker._normalise_hub_url("http://127.0.0.1:47873/") == "http://127.0.0.1:47873"
    for value in ("file:///tmp/hub", "http://user:pass@hub.local", "not-a-url"):
        with pytest.raises(ValueError):
            worker._normalise_hub_url(value)


def test_dashboard_ui_exposes_status_history_and_whats_new():
    html = (Path(__file__).parents[1] / "frontend" / "index.html").read_text()
    assert "Test connection" in html
    assert "What's New" in html
    assert 'id="version-badge"' in html
    assert 'id="update-banner"' in html
    assert "renderstudio_seen\",APP_VERSION" in html
    assert "0.4.0 / Releases you can trust" in html
    assert "Lifetime episodes" in html
    assert "Recent render work" in html
