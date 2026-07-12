import hashlib
import importlib
import json

import pytest


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
