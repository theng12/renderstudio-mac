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
        "assets": [{"id": "input", "url": url, "sha256": checksum, "extension": ".mp4"}],
        "steps": [{"tool": "ffmpeg", "args": [
            "-y", "-i", "{{asset:input}}", "-c:v", "{{video_encoder}}",
            "{{output}}",
        ]}],
    }


def test_recipe_accepts_confined_ffmpeg_job(worker):
    worker._validate_recipe(recipe())


def test_catalog_advertises_unified_video_assembly(worker):
    model = worker.capabilities()["models"][0]
    assert model["label"] == "Video Assembly"
    assert "video-assembly" in model["capabilities"]
    assert "scene-plan-timing" in model["capabilities"]
    assert "title-image" in model["capabilities"]
    assert "logo-overlay" in model["capabilities"]
    assert "presentation-frame-media" in model["capabilities"]
    assert "overlay-layers" in model["capabilities"]
    assert "animated-subtitles" in model["capabilities"]
    assert "compilation-layout" in model["capabilities"]
    assert "webm-export" in model["capabilities"]
    assert worker.RenderRequest(recipe=recipe()).workflow == "video_assembly"


def test_submit_rejects_retired_render_workflows(worker):
    request = worker.RenderRequest(
        workflow="timestamp_assembly", recipe=recipe())
    with pytest.raises(worker.HTTPException, match="unsupported render workflow"):
        asyncio.run(worker.submit(request))


@pytest.mark.parametrize("bad", [
    {"version": 2, "assets": [], "steps": []},
    {"version": 1, "assets": [], "steps": [{"tool": "sh", "args": ["x"]}]},
])
def test_recipe_rejects_unsupported_shapes(worker, bad):
    with pytest.raises(ValueError):
        worker._validate_recipe(bad)


def test_recipe_accepts_supported_output_containers(worker):
    for extension in ("mp4", "mov", "webm"):
        value = recipe()
        value["output_extension"] = extension
        worker._validate_recipe(value)
    value = recipe()
    value["output_extension"] = "avi"
    with pytest.raises(ValueError, match="output_extension"):
        worker._validate_recipe(value)


def test_recipe_rejects_local_and_direct_network_arguments(worker):
    for value in ("/etc/passwd", "../outside", "http://unverified/input"):
        value_recipe = recipe()
        value_recipe["steps"][0]["args"].insert(0, value)
        with pytest.raises(ValueError):
            worker._validate_recipe(value_recipe)


def test_recipe_rejects_unsafe_asset_extension(worker):
    value = recipe()
    value["assets"][0]["extension"] = "../mp4"
    with pytest.raises(ValueError, match="filename extension"):
        worker._validate_recipe(value)


def test_placeholder_resolution(worker, tmp_path):
    asset = tmp_path / "asset"
    output = tmp_path / "out.mp4"
    value = worker._resolve_arg(
        "subtitles={{asset:captions}}:fontsdir={{work:fonts}}",
        {"captions": asset}, tmp_path, output, "h264_videotoolbox")
    assert str(asset) in value
    assert str(tmp_path / "fonts") in value


def test_process_failure_detail_is_bounded_and_scrubs_worker_paths(worker):
    log = worker.JOBS / "failure.log"
    old = "old command output\n"
    log.write_text(old)
    start = len(old.encode())
    private_path = worker.DATA / "jobs" / "secret" / "work" / "scene.mp4"
    log.write_text(old + (f"failed to open {private_path}\n" * 200))

    detail = worker._process_failure_detail(log, start)

    assert len(detail) <= 1600
    assert "failed to open" in detail
    assert str(worker.DATA) not in detail
    assert "<render-data>" in detail
    assert "old command output" not in detail


def test_render_job_surfaces_bounded_ffmpeg_failure_detail(worker, monkeypatch):
    async def fail_process(_argv, log):
        with log.open("a") as handle:
            handle.write(f"Invalid data found in {worker.DATA / 'jobs' / 'job' / 'work'}\n")
        return 1

    monkeypatch.setattr(worker, "_run_process", fail_process)
    monkeypatch.setattr(worker, "_tool_path", lambda name: name)
    monkeypatch.setattr(worker, "_has_videotoolbox", lambda: True)
    job = {
        "id": "failed-render", "state": "queued", "progress": 0,
        "recipe": {
            "version": 1, "assets": [], "steps": [{
                "tool": "ffmpeg", "args": ["-y", "{{output}}"],
            }],
        },
    }

    asyncio.run(worker._render(job))

    assert job["state"] == "error"
    assert job["error"].startswith("ffmpeg step 1 failed:")
    assert "Invalid data found" in job["error"]
    assert str(worker.DATA) not in job["error"]


def test_download_retries_transient_hub_asset_error(worker):
    payload = b"durable render input"
    checksum = hashlib.sha256(payload).hexdigest()
    attempts = []

    def handler(request):
        attempts.append(request.url.path)
        if len(attempts) == 1:
            return httpx.Response(404, request=request)
        return httpx.Response(200, content=payload, request=request)

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await worker._download(client, {
                "id": "narration", "url": "http://hub.test/api/hub/render-assets/asset",
                "sha256": checksum, "extension": ".mp3", "bytes": len(payload),
            })

    destination = asyncio.run(run())
    assert destination.read_bytes() == payload
    assert len(attempts) == 2


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


def test_hard_cap_evicts_verified_oldest_and_protects_active(worker, monkeypatch):
    monkeypatch.setattr(worker.time, "time", lambda: 1_000)
    worker.SETTINGS_FILE.write_text(json.dumps({
        "retention_days": 90, "storage_enabled": True,
        "max_storage_gb": 80, "minimum_free_gb": 1,
    }))
    old = worker.OUTPUTS / "old.mp4"
    active = worker.OUTPUTS / "active.mp4"
    old.write_bytes(b"old-video")
    active.write_bytes(b"active-video")
    worker.jobs.update({
        "old": {"id": "old", "state": "done", "acked_at": 900,
                "output_path": str(old), "recipe": {"assets": []}},
        "active": {"id": "active", "state": "running",
                   "output_path": str(active), "recipe": {"assets": []}},
    })

    result = worker._cleanup_expired(target_bytes=0)

    assert result["purged_jobs"] == 1
    assert not old.exists()
    assert active.exists()
    assert worker.jobs["active"]["state"] == "running"


def test_standard_storage_policy_api(worker):
    client = TestClient(worker.app, headers={"X-Studio-Token": worker.FLEET_TOKEN})
    response = client.put("/api/storage-policy", json={
        "enabled": True, "retention_days": 3, "max_gb": 80,
    })
    assert response.status_code == 200
    assert response.json()["retention_days"] == 3
    assert response.json()["max_gb"] == 80


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


def test_current_release_is_in_changelog_and_whats_new(worker):
    root = Path(worker.__file__).parents[2]
    expected = (root / "VERSION").read_text().strip()
    changelog = (root / "CHANGELOG.md").read_text()
    html = (root / "app" / "frontend" / "index.html").read_text()

    assert f"## {expected} " in changelog
    assert f">{expected} /" in html


def test_update_status_compares_semantic_versions(worker, monkeypatch):
    monkeypatch.setattr(worker, "_schedule_update_check", lambda: None)
    worker.update_state.update(latest="99.0.0", checking=False)
    payload = worker.update_status()
    assert payload["app_version"] == worker.VERSION
    assert payload["latest_version"] == "99.0.0"
    assert payload["update_available"] is True
    assert worker._parse_version("v1.12.3") > worker._parse_version("1.9.9")


def test_update_check_reads_uncached_github_release(worker, monkeypatch):
    captured = {}

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return b"9.8.7\n"

    def open_request(request, timeout):
        captured.update(url=request.full_url,
                        accept=request.headers.get("Accept"), timeout=timeout)
        return Response()

    monkeypatch.setattr(worker.urllib.request, "urlopen", open_request)
    worker._refresh_latest_version()
    assert captured == {"url": worker.UPDATE_VERSION_URL,
                        "accept": "application/vnd.github.raw+json", "timeout": 5}
    assert worker.update_state["latest"] == "9.8.7"


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
    assert "0.6.0 / Complete Video Assembly on workers" in html
    assert "Lifetime episodes" in html
    assert "Recent render work" in html
    assert 'id="automatic-updates"' in html
    assert "Update after current work" in html
    assert "0.7.0 / Safe automatic updates" in html


def test_automatic_update_readiness_tracks_running_and_queued_renders(worker):
    assert worker.automatic_update_readiness() == {"idle": True, "reasons": []}
    worker.jobs.update({
        "running": {"id": "running", "state": "running"},
        "queued": {"id": "queued", "state": "queued"},
    })
    readiness = worker.automatic_update_readiness()
    assert readiness["idle"] is False
    assert "1 render job is running" in readiness["reasons"][0]
    assert "1 render job is queued" in readiness["reasons"][0]


def test_automatic_update_api_exposes_controller(worker, monkeypatch):
    status = {"state": "idle", "installed_version": worker.VERSION,
              "settings": {"mode": "off"}}
    monkeypatch.setattr(worker.auto_updater, "public_status", lambda: status)
    client = TestClient(worker.app, headers={"X-Studio-Token": worker.FLEET_TOKEN})
    assert client.get("/api/auto-update/status").json() == status

    saved = {}
    monkeypatch.setattr(
        worker.auto_updater, "save_settings",
        lambda payload: saved.update(payload) or {**status, "settings": payload},
    )
    response = client.post("/api/auto-update/settings", json={
        "mode": "notify", "frequency": "weekly",
        "maintenance_hour": 6, "idle_only": True,
    })
    assert response.status_code == 200
    assert saved == {"mode": "notify", "frequency": "weekly",
                     "maintenance_hour": 6, "idle_only": True}
