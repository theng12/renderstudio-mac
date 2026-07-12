"""Episode-level, non-preemptive FFmpeg render worker."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

import httpx
import psutil
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

from .fleet_auth import load_token, make_middleware

VERSION = "0.2.0"
ROOT = Path(__file__).resolve().parents[2]
DATA = Path(os.environ.get("RENDERSTUDIO_DATA_DIR", ROOT / "data")).resolve()
OBJECTS = DATA / "cache" / "objects"
JOBS = DATA / "jobs"
OUTPUTS = DATA / "outputs"
SETTINGS_FILE = DATA / "settings.json"
for directory in (OBJECTS, JOBS, OUTPUTS):
    directory.mkdir(parents=True, exist_ok=True)

@asynccontextmanager
async def lifespan(_app: FastAPI):
    restore_jobs()
    yield


FLEET_TOKEN = load_token()
app = FastAPI(title="Render Studio KH", version=VERSION, lifespan=lifespan)
app.middleware("http")(make_middleware(FLEET_TOKEN))

jobs: dict[str, dict] = {}
tasks: dict[str, asyncio.Task] = {}
worker_lock = asyncio.Lock()
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
RETENTION_CHOICES = {1, 3, 7, 15, 30}


class RenderRequest(BaseModel):
    repo: str = "episode-assembly-v1"
    label: str = "episode"
    recipe: dict


class SettingsRequest(BaseModel):
    retention_days: int | None = Field(default=7)
    minimum_free_gb: int = Field(default=20, ge=1, le=1000)


def _load_settings() -> dict:
    defaults = {"retention_days": 7, "minimum_free_gb": 20}
    try:
        value = json.loads(SETTINGS_FILE.read_text())
        defaults.update(value)
    except (OSError, json.JSONDecodeError):
        pass
    if defaults["retention_days"] not in RETENTION_CHOICES | {None}:
        defaults["retention_days"] = 7
    return defaults


def _save_job(job: dict) -> None:
    folder = JOBS / job["id"]
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "job.json").write_text(json.dumps(job, indent=2) + "\n")


def _cleanup_expired() -> dict:
    """Remove only acknowledged, unpinned work after its retention window."""
    days = _load_settings()["retention_days"]
    if days is None:
        return {"purged_jobs": 0, "purged_objects": 0}
    cutoff = time.time() - days * 86400
    purged = 0
    for job in jobs.values():
        if (job.get("state") != "done" or job.get("pinned")
                or not job.get("acked_at") or job["acked_at"] > cutoff):
            continue
        output_path = job.get("output_path")
        if output_path:
            Path(output_path).unlink(missing_ok=True)
        work = JOBS / job["id"] / "work"
        shutil.rmtree(work, ignore_errors=True)
        job.update(state="purged", output_path=None, output_url=None,
                   media=None, purged_at=time.time())
        _save_job(job)
        purged += 1
    referenced = {
        asset["sha256"].lower()
        for job in jobs.values() if job.get("state") != "purged"
        for asset in job.get("recipe", {}).get("assets", [])
        if asset.get("sha256")
    }
    removed_objects = 0
    for obj in OBJECTS.iterdir():
        if obj.is_file() and obj.name not in referenced and not obj.name.endswith(".partial"):
            obj.unlink(missing_ok=True)
            removed_objects += 1
    return {"purged_jobs": purged, "purged_objects": removed_objects}


def _hardware() -> dict:
    chip = platform.processor() or platform.machine()
    if platform.system() == "Darwin":
        try:
            chip = subprocess.check_output(
                ["sysctl", "-n", "machdep.cpu.brand_string"], text=True,
                timeout=2).strip() or chip
        except (OSError, subprocess.SubprocessError):
            pass
    memory_gb = round(psutil.virtual_memory().total / (1024 ** 3))
    chip_upper = chip.upper()
    generation = 4 if "M4" in chip_upper else 3 if "M3" in chip_upper else 2 if "M2" in chip_upper else 1
    return {"chip": chip, "memory_gb": memory_gb,
            "render_score": generation * 100 + memory_gb}


def _tool_path(name: str) -> str | None:
    """Prefer Homebrew's macOS-native build for VideoToolbox support."""
    homebrew = Path("/opt/homebrew/bin") / name
    return str(homebrew) if homebrew.is_file() else shutil.which(name)


def _has_videotoolbox() -> bool:
    ffmpeg = _tool_path("ffmpeg")
    if not ffmpeg:
        return False
    try:
        result = subprocess.run([ffmpeg, "-hide_banner", "-encoders"],
                                capture_output=True, text=True, timeout=10)
        return "h264_videotoolbox" in result.stdout
    except (OSError, subprocess.SubprocessError):
        return False


def _validate_recipe(recipe: dict) -> None:
    if recipe.get("version") != 1:
        raise ValueError("recipe.version must be 1")
    assets = recipe.get("assets")
    steps = recipe.get("steps")
    if not isinstance(assets, list) or not isinstance(steps, list) or not steps:
        raise ValueError("recipe needs assets[] and at least one step")
    seen = set()
    for asset in assets:
        name = asset.get("id", "")
        parsed = urlparse(asset.get("url", ""))
        if not SAFE_NAME.fullmatch(name) or name in seen:
            raise ValueError(f"invalid or duplicate asset id: {name!r}")
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError(f"asset {name} needs an http(s) URL")
        if not re.fullmatch(r"[0-9a-fA-F]{64}", asset.get("sha256", "")):
            raise ValueError(f"asset {name} needs a SHA-256 checksum")
        seen.add(name)
    for step in steps:
        if step.get("tool") not in {"ffmpeg", "ffprobe"}:
            raise ValueError("steps may only use ffmpeg or ffprobe")
        args = step.get("args")
        if not isinstance(args, list) or not all(isinstance(arg, str) for arg in args):
            raise ValueError("step.args must be a list of strings")
        for arg in args:
            if "../" in arg or arg.startswith(("/", "file:", "http:", "https:")):
                raise ValueError("step arguments may only reference worker-local placeholders")
    for generated in recipe.get("files", []):
        if not SAFE_NAME.fullmatch(generated.get("name", "")):
            raise ValueError("generated work files need a safe name")
        if not isinstance(generated.get("content"), str):
            raise ValueError("generated work-file content must be text")
    if not any("{{output}}" in arg for step in steps for arg in step["args"]):
        raise ValueError("one render step must write {{output}}")


async def _download(client: httpx.AsyncClient, asset: dict) -> Path:
    expected = asset["sha256"].lower()
    dest = OBJECTS / expected
    if dest.exists() and _sha256(dest) == expected:
        return dest
    partial = dest.with_suffix(".partial")
    digest = hashlib.sha256()
    total = 0
    async with client.stream(
        "GET", asset["url"], timeout=None,
        headers={"X-Hub-Token": FLEET_TOKEN},
    ) as response:
        response.raise_for_status()
        with partial.open("wb") as handle:
            async for chunk in response.aiter_bytes(1024 * 1024):
                total += len(chunk)
                digest.update(chunk)
                handle.write(chunk)
    if digest.hexdigest() != expected:
        partial.unlink(missing_ok=True)
        raise ValueError(f"checksum mismatch for {asset['id']}")
    if asset.get("bytes") is not None and total != int(asset["bytes"]):
        partial.unlink(missing_ok=True)
        raise ValueError(f"size mismatch for {asset['id']}")
    partial.replace(dest)
    return dest


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _resolve_arg(arg: str, assets: dict[str, Path], work: Path, output: Path,
                 encoder: str) -> str:
    value = arg.replace("{{output}}", str(output)).replace("{{video_encoder}}", encoder)
    for name, asset_path in assets.items():
        value = value.replace(f"{{{{asset:{name}}}}}", str(asset_path))
    for match in re.findall(r"\{\{work:([^}]+)\}\}", value):
        if not SAFE_NAME.fullmatch(match):
            raise ValueError(f"invalid work filename: {match!r}")
        value = value.replace(f"{{{{work:{match}}}}}", str(work / match))
    if "{{" in value or "}}" in value:
        raise ValueError(f"unknown recipe placeholder in {arg!r}")
    return value


async def _run_process(argv: list[str], log: Path) -> int:
    with log.open("ab") as handle:
        process = await asyncio.create_subprocess_exec(
            *argv, stdout=handle, stderr=asyncio.subprocess.STDOUT)
        return await process.wait()


async def _validate_output(output: Path, log: Path) -> dict:
    if not output.exists() or output.stat().st_size == 0:
        raise ValueError("render produced no output")
    probe = await asyncio.create_subprocess_exec(
        _tool_path("ffprobe") or "ffprobe", "-v", "error", "-show_streams",
        "-show_format", "-of", "json", str(output),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
    stdout, stderr = await probe.communicate()
    if probe.returncode:
        raise ValueError(f"ffprobe validation failed: {stderr.decode(errors='replace')[-500:]}")
    metadata = json.loads(stdout)
    streams = metadata.get("streams", [])
    if not any(s.get("codec_type") == "video" for s in streams):
        raise ValueError("output has no video stream")
    decode_code = await _run_process([
        _tool_path("ffmpeg") or "ffmpeg", "-v", "error", "-i", str(output),
        "-f", "null", "-"], log)
    if decode_code:
        raise ValueError("full output decode validation failed")
    return metadata


async def _render(job: dict) -> None:
    async with worker_lock:
        job.update(state="running", progress=0.01, started_at=time.time())
        _save_job(job)
        folder = JOBS / job["id"]
        work = folder / "work"
        work.mkdir(exist_ok=True)
        log = folder / "render.log"
        output = OUTPUTS / f"{job['id']}.partial.mp4"
        final = OUTPUTS / f"{job['id']}.mp4"
        try:
            recipe = job["recipe"]
            async with httpx.AsyncClient(follow_redirects=True) as client:
                assets = {}
                for index, asset in enumerate(recipe["assets"]):
                    assets[asset["id"]] = await _download(client, asset)
                    job["progress"] = 0.05 + 0.25 * ((index + 1) / max(1, len(recipe["assets"])))
                    _save_job(job)
            encoder = "h264_videotoolbox" if _has_videotoolbox() else "libx264"
            for generated in recipe.get("files", []):
                content = _resolve_arg(generated["content"], assets, work, output, encoder)
                (work / generated["name"]).write_text(content)
            for index, step in enumerate(recipe["steps"]):
                tool = _tool_path(step["tool"])
                if not tool:
                    raise ValueError(f"{step['tool']} is not installed")
                args = [_resolve_arg(arg, assets, work, output, encoder) for arg in step["args"]]
                code = await _run_process([tool, *args], log)
                if code and encoder == "h264_videotoolbox" and step["tool"] == "ffmpeg":
                    fallback = [_resolve_arg(arg, assets, work, output, "libx264")
                                for arg in step["args"]]
                    code = await _run_process([tool, *fallback], log)
                    if code == 0:
                        encoder = "libx264"
                if code:
                    raise ValueError(f"{step['tool']} step {index + 1} failed")
                job["progress"] = 0.3 + 0.6 * ((index + 1) / len(recipe["steps"]))
                _save_job(job)
            metadata = await _validate_output(output, log)
            output.replace(final)
            job.update(state="done", progress=1.0, output_path=str(final),
                       output_url=f"/api/outputs/{job['id']}",
                       sha256=_sha256(final), bytes=final.stat().st_size,
                       encoder=encoder, media=metadata, finished_at=time.time(),
                       duration_seconds=round(time.time() - job["started_at"], 2))
        except Exception as exc:
            output.unlink(missing_ok=True)
            job.update(state="error", error=str(exc), finished_at=time.time())
        finally:
            _save_job(job)


@app.get("/", response_class=HTMLResponse)
def index():
    return (ROOT / "app" / "frontend" / "index.html").read_text()


@app.get("/api/health")
def health():
    hardware = _hardware()
    return {"ok": bool(_tool_path("ffmpeg") and _tool_path("ffprobe")),
            "app_version": VERSION, "busy": worker_lock.locked(),
            "queue_depth": sum(1 for j in jobs.values() if j["state"] == "queued"),
            "videotoolbox": _has_videotoolbox(), **hardware}


@app.get("/api/version")
def version():
    return {"version": VERSION, "app": "renderstudio-mac"}


@app.get("/api/capabilities")
@app.get("/api/catalog")
def capabilities():
    return {"models": [{"repo": "episode-assembly-v1", "label": "Episode Assembly",
                         "cache": {"state": "cached"}, "is_cloud": True,
                         "capabilities": ["timestamp-assembly"]}],
            "retention": [1, 3, 7, 15, 30, "forever"]}


@app.post("/api/generate/render")
async def submit(request: RenderRequest):
    if request.repo != "episode-assembly-v1":
        raise HTTPException(400, "unsupported render recipe")
    try:
        _validate_recipe(request.recipe)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    free_gb = psutil.disk_usage(DATA).free / (1024 ** 3)
    if free_gb < _load_settings()["minimum_free_gb"]:
        raise HTTPException(507, "worker is below its minimum free-disk reserve")
    job_id = uuid.uuid4().hex[:12]
    job = {"id": job_id, "state": "queued", "progress": 0.0,
           "label": request.label, "recipe": request.recipe,
           "created_at": time.time(), "error": None}
    jobs[job_id] = job
    _save_job(job)
    tasks[job_id] = asyncio.create_task(_render(job))
    return {"job": job}


@app.get("/api/generate/jobs/{job_id}")
def get_job(job_id: str):
    if job_id not in jobs:
        raise HTTPException(404, "unknown job")
    return {"job": jobs[job_id]}


@app.post("/api/generate/jobs/{job_id}/ack")
def acknowledge(job_id: str):
    if job_id not in jobs or jobs[job_id]["state"] != "done":
        raise HTTPException(409, "only completed jobs can be acknowledged")
    jobs[job_id]["acked_at"] = time.time()
    _save_job(jobs[job_id])
    return {"ok": True}


@app.post("/api/generate/jobs/{job_id}/pin")
def pin(job_id: str, pinned: bool = True):
    if job_id not in jobs:
        raise HTTPException(404, "unknown job")
    jobs[job_id]["pinned"] = pinned
    _save_job(jobs[job_id])
    return {"ok": True, "pinned": pinned}


@app.delete("/api/generate/jobs/{job_id}")
def cancel(job_id: str):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "unknown job")
    task = tasks.get(job_id)
    if task and not task.done():
        task.cancel()
    job.update(state="cancelled", finished_at=time.time())
    _save_job(job)
    return {"ok": True}


@app.get("/api/outputs/{job_id}")
def output(job_id: str):
    job = jobs.get(job_id)
    path = Path(job.get("output_path", "")) if job else None
    if not path or not path.is_file() or path.parent != OUTPUTS:
        raise HTTPException(404, "output not found")
    return FileResponse(path, media_type="video/mp4", filename=path.name)


@app.get("/api/settings")
def get_settings():
    return _load_settings()


@app.put("/api/settings")
def put_settings(request: SettingsRequest):
    if request.retention_days not in RETENTION_CHOICES | {None}:
        raise HTTPException(400, "retention_days must be 1, 3, 7, 15, 30, or null")
    value = request.model_dump()
    SETTINGS_FILE.write_text(json.dumps(value, indent=2) + "\n")
    return value


@app.post("/api/storage/cleanup")
def cleanup():
    return _cleanup_expired()


@app.delete("/api/storage/jobs/{job_id}")
def clean_now(job_id: str):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "unknown job")
    if job.get("state") in {"queued", "running"}:
        raise HTTPException(409, "an active render cannot be cleaned")
    output_path = job.get("output_path")
    if output_path:
        Path(output_path).unlink(missing_ok=True)
    shutil.rmtree(JOBS / job_id / "work", ignore_errors=True)
    job.update(state="purged", output_path=None, output_url=None,
               media=None, purged_at=time.time())
    _save_job(job)
    return {"ok": True, **_cleanup_expired()}


def restore_jobs():
    for path in JOBS.glob("*/job.json"):
        try:
            job = json.loads(path.read_text())
            if job.get("state") in {"queued", "running"}:
                job.update(state="error", error="worker restarted before completion",
                           finished_at=time.time())
                _save_job(job)
            jobs[job["id"]] = job
        except (OSError, json.JSONDecodeError, KeyError):
            continue
    _cleanup_expired()
