"""Episode-level, non-preemptive FFmpeg render worker."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import inspect
import json
import os
import platform
import re
import shutil
import subprocess
import threading
import time
import urllib.request
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
from .auto_update import UpdateError
from .auto_update_config import create_updater


ROOT = Path(__file__).resolve().parents[2]


def _read_app_version() -> str:
    try:
        return (ROOT / "VERSION").read_text().strip() or "0.0.0"
    except OSError:
        return "0.0.0"


VERSION = _read_app_version()
APP_VERSION = VERSION
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
    cleanup_task = asyncio.create_task(_storage_cleanup_loop())
    try:
        yield
    finally:
        cleanup_task.cancel()


FLEET_TOKEN = load_token()
app = FastAPI(title="Render Studio KH", version=VERSION, lifespan=lifespan)
app.middleware("http")(make_middleware(FLEET_TOKEN))

jobs: dict[str, dict] = {}
tasks: dict[str, asyncio.Task] = {}
worker_lock = asyncio.Lock()
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
RETENTION_CHOICES = {1, 3, 7, 15, 30, 90}
DEFAULT_HUB_URL = "http://127.0.0.1:47873"
connection_cache: dict = {"checked_at": 0.0, "ok": False, "status": "not_tested"}
storage_cache: dict = {"checked_at": 0.0}
PROCESS_STARTED_AT = time.time()
UPDATE_REPO = "theng12/renderstudio-mac"
UPDATE_VERSION_URL = f"https://api.github.com/repos/{UPDATE_REPO}/contents/VERSION?ref=main"
UPDATE_CHECK_SECONDS = 6 * 3600
update_state: dict = {"checked_at": 0.0, "latest": None, "checking": False}
update_lock = threading.Lock()


def _bounded_env_seconds(name: str, default: float, minimum: float,
                         maximum: float) -> float:
    try:
        value = float(os.environ.get(name, default))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(maximum, value))


PROCESS_TIMEOUT_SECONDS = _bounded_env_seconds(
    "RENDERSTUDIO_PROCESS_TIMEOUT_SECONDS", 12 * 60 * 60, 60, 24 * 60 * 60)
PROCESS_HEARTBEAT_SECONDS = _bounded_env_seconds(
    "RENDERSTUDIO_PROCESS_HEARTBEAT_SECONDS", 15, 1, 300)
PROCESS_TERMINATE_GRACE_SECONDS = _bounded_env_seconds(
    "RENDERSTUDIO_PROCESS_TERMINATE_GRACE_SECONDS", 10, 1, 60)
STORAGE_POLICY_VERSION = 2


class RenderProcessTimeout(RuntimeError):
    """A supervised FFmpeg process exceeded its configured runtime ceiling."""


class RenderRequest(BaseModel):
    repo: str = "episode-assembly-v1"
    label: str = "episode"
    workflow: str = "video_assembly"
    recipe: dict


class SettingsRequest(BaseModel):
    retention_days: int | None = Field(default=30)
    storage_enabled: bool = True
    max_storage_gb: float = Field(default=80, ge=1, le=1000)
    minimum_free_gb: int = Field(default=20, ge=1, le=1000)
    hub_url: str = DEFAULT_HUB_URL


class StoragePolicyRequest(BaseModel):
    enabled: bool = True
    retention_days: int = Field(default=30)
    max_gb: float = Field(default=80, ge=1, le=1000)


class AutoUpdateSettingsBody(BaseModel):
    mode: str
    frequency: str
    maintenance_hour: int
    idle_only: bool = True


class AutoUpdateRequestBody(BaseModel):
    after_current: bool = False


def _automatic_update_blockers() -> list[str]:
    active = [job for job in jobs.values() if job.get("state") in {"queued", "running"}]
    if not active:
        return []
    running = sum(1 for job in active if job.get("state") == "running")
    queued = len(active) - running
    parts = []
    if running:
        parts.append(f"{running} render job{' is' if running == 1 else 's are'} running")
    if queued:
        parts.append(f"{queued} render job{' is' if queued == 1 else 's are'} queued")
    return [" and ".join(parts)]


auto_updater = create_updater(readiness=_automatic_update_blockers)


def _normalise_hub_url(value: str) -> str:
    parsed = urlparse((value or "").strip())
    if (parsed.scheme not in {"http", "https"} or not parsed.hostname
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise ValueError("Studio Hub URL must be a plain http(s) address")
    return (value or "").strip().rstrip("/")


def _parse_version(value: str | None) -> tuple[int, ...]:
    try:
        return tuple(int(part) for part in str(value).strip().lstrip("v").split(".")[:3])
    except (TypeError, ValueError):
        return (0,)


def _refresh_latest_version() -> None:
    try:
        request = urllib.request.Request(
            UPDATE_VERSION_URL,
            headers={"Accept": "application/vnd.github.raw+json",
                     "User-Agent": "renderstudio-mac-update-check"},
        )
        with urllib.request.urlopen(request, timeout=5) as response:
            latest = response.read().decode("utf-8").strip()
            if _parse_version(latest) != (0,):
                update_state["latest"] = latest
    except (OSError, UnicodeError):
        pass
    finally:
        with update_lock:
            update_state["checked_at"] = time.time()
            update_state["checking"] = False


def _schedule_update_check() -> None:
    with update_lock:
        stale = time.time() - float(update_state["checked_at"]) > UPDATE_CHECK_SECONDS
        if not stale or update_state["checking"]:
            return
        update_state["checking"] = True
    threading.Thread(target=_refresh_latest_version, daemon=True).start()


def _load_settings() -> dict:
    defaults = {"retention_days": 30, "storage_enabled": True,
                "max_storage_gb": 80.0, "minimum_free_gb": 20,
                "hub_url": DEFAULT_HUB_URL,
                "storage_policy_version": STORAGE_POLICY_VERSION}
    value = {}
    try:
        value = json.loads(SETTINGS_FILE.read_text())
        if isinstance(value, dict):
            defaults.update(value)
        else:
            value = {}
    except (OSError, json.JSONDecodeError):
        pass
    if defaults["retention_days"] not in RETENTION_CHOICES | {None}:
        defaults["retention_days"] = 30
    if not isinstance(defaults.get("storage_enabled"), bool):
        defaults["storage_enabled"] = True
    try:
        defaults["max_storage_gb"] = float(defaults["max_storage_gb"])
    except (TypeError, ValueError):
        defaults["max_storage_gb"] = 80.0
    if not 1 <= defaults["max_storage_gb"] <= 1000:
        defaults["max_storage_gb"] = 80.0
    try:
        defaults["hub_url"] = _normalise_hub_url(defaults["hub_url"])
    except (TypeError, ValueError):
        defaults["hub_url"] = DEFAULT_HUB_URL
    try:
        policy_version = int(value.get("storage_policy_version", 1))
    except (TypeError, ValueError):
        policy_version = 1
    if (
        value
        and policy_version < STORAGE_POLICY_VERSION
        and defaults["retention_days"] == 3
    ):
        defaults["retention_days"] = 30
        SETTINGS_FILE.write_text(json.dumps(defaults, indent=2) + "\n")
    return defaults


def _folder_bytes(path: Path) -> int:
    total = 0
    try:
        for item in path.rglob("*"):
            if item.is_file():
                total += item.stat().st_size
    except OSError:
        pass
    return total


def _storage_snapshot(now: float) -> dict:
    if now - float(storage_cache.get("checked_at", 0)) < 30:
        return {key: value for key, value in storage_cache.items() if key != "checked_at"}
    disk = psutil.disk_usage(DATA)
    value = {
        "data_bytes": _folder_bytes(DATA), "cache_bytes": _folder_bytes(OBJECTS),
        "outputs_bytes": _folder_bytes(OUTPUTS), "free_bytes": disk.free,
        "total_bytes": disk.total,
    }
    storage_cache.clear()
    storage_cache.update(checked_at=now, **value)
    return value


def _elapsed(job: dict, now: float) -> float:
    if job.get("duration_seconds") is not None:
        return max(0.0, float(job["duration_seconds"]))
    started = job.get("started_at")
    if not started:
        return 0.0
    return max(0.0, float(job.get("finished_at") or now) - float(started))


def _video_duration(job: dict) -> float:
    # Prefer the durable scalar captured at completion so lifetime totals
    # survive retention purges, which clear the full `media` metadata.
    stored = job.get("video_seconds")
    if stored is not None:
        try:
            return max(0.0, float(stored))
        except (TypeError, ValueError):
            return 0.0
    try:
        media = job.get("media") or {}
        return max(0.0, float(media.get("format", {}).get("duration", 0)))
    except (TypeError, ValueError, AttributeError):
        return 0.0


def _job_summary(job: dict, now: float) -> dict:
    output_path = job.get("output_path")
    return {
        "id": job.get("id"), "label": job.get("label") or "Untitled episode",
        "state": job.get("state", "unknown"),
        "progress": round(float(job.get("progress") or 0), 4),
        "created_at": job.get("created_at"), "started_at": job.get("started_at"),
        "finished_at": job.get("finished_at"), "duration_seconds": _elapsed(job, now),
        "video_seconds": _video_duration(job), "bytes": int(job.get("bytes") or 0),
        "encoder": job.get("encoder"), "sha256": job.get("sha256"),
        "acknowledged": bool(job.get("acked_at")), "pinned": bool(job.get("pinned")),
        "retained": bool(output_path and Path(output_path).is_file()),
        "error": job.get("error"),
    }


def _dashboard_snapshot() -> dict:
    now = time.time()
    ordered = sorted(jobs.values(), key=lambda item: item.get("created_at", 0), reverse=True)
    summaries = [_job_summary(job, now) for job in ordered]
    completed = [item for item in summaries if item["state"] in {"done", "purged"}]
    failed = [item for item in summaries if item["state"] == "error"]
    cancelled = [item for item in summaries if item["state"] == "cancelled"]
    active = next((item for item in summaries if item["state"] == "running"), None)
    queued = [item for item in summaries if item["state"] == "queued"]
    attempts = len(completed) + len(failed)
    completed_seconds = sum(item["duration_seconds"] for item in completed)
    encoders: dict[str, int] = {}
    for item in completed:
        name = item["encoder"] or "unknown"
        encoders[name] = encoders.get(name, 0) + 1
    return {
        "version": VERSION,
        "now": now,
        "current": active or (queued[0] if queued else None),
        "recent": summaries[:25],
        "totals": {
            "jobs": len(summaries), "completed": len(completed),
            "failed": len(failed), "cancelled": len(cancelled),
            "queued": len(queued), "running": 1 if active else 0,
            "acknowledged": sum(1 for item in completed if item["acknowledged"]),
            "retained": sum(1 for item in completed if item["retained"]),
            "render_seconds": sum(item["duration_seconds"] for item in summaries),
            "completed_render_seconds": completed_seconds,
            "average_render_seconds": completed_seconds / len(completed) if completed else 0,
            "video_seconds": sum(item["video_seconds"] for item in completed),
            "output_bytes": sum(item["bytes"] for item in completed),
            "success_rate": (len(completed) / attempts * 100) if attempts else None,
            "first_job_at": min((item["created_at"] for item in summaries if item["created_at"]), default=None),
            "last_job_at": max((item["created_at"] for item in summaries if item["created_at"]), default=None),
            "encoders": encoders,
        },
        "storage": _storage_snapshot(now),
        "settings": _load_settings(),
    }


async def _test_hub_connection(force: bool = False) -> dict:
    now = time.time()
    if not force and now - float(connection_cache.get("checked_at", 0)) < 15:
        return dict(connection_cache)
    hub_url = _load_settings()["hub_url"]
    result = {"checked_at": now, "ok": False, "status": "offline",
              "hub_url": hub_url, "latency_ms": None, "version": None,
              "detail": "Studio Hub did not respond"}
    started = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            response = await client.get(
                f"{hub_url}/api/hub/health", headers={"X-Hub-Token": load_token()})
            version_response = await client.get(f"{hub_url}/api/version")
        result["latency_ms"] = round((time.monotonic() - started) * 1000)
        if response.status_code == 200:
            payload = response.json()
            version_payload = version_response.json() if version_response.status_code == 200 else {}
            result.update(ok=bool(payload.get("ok", True)), status="connected",
                          version=version_payload.get("app_version") or version_payload.get("version"),
                          detail="Authenticated connection is ready")
        elif response.status_code in {401, 403}:
            result.update(status="auth_error", detail="Hub responded but rejected the fleet token")
        else:
            result["detail"] = f"Hub responded with HTTP {response.status_code}"
    except (httpx.HTTPError, ValueError) as exc:
        result["detail"] = str(exc)[:180]
    connection_cache.clear()
    connection_cache.update(result)
    return dict(connection_cache)


def _save_job(job: dict) -> None:
    folder = JOBS / job["id"]
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "job.json").write_text(json.dumps(job, indent=2) + "\n")


def _purge_job(job: dict) -> None:
    output_path = job.get("output_path")
    if output_path:
        Path(output_path).unlink(missing_ok=True)
    shutil.rmtree(JOBS / job["id"] / "work", ignore_errors=True)
    job.update(state="purged", output_path=None, output_url=None,
               media=None, purged_at=time.time())
    _save_job(job)


def _remove_unreferenced_objects() -> int:
    referenced = {
        asset["sha256"].lower()
        for job in jobs.values() if job.get("state") != "purged"
        for asset in job.get("recipe", {}).get("assets", [])
        if asset.get("sha256")
    }
    removed = 0
    for obj in OBJECTS.iterdir():
        if obj.is_file() and obj.name not in referenced and not obj.name.endswith(".partial"):
            obj.unlink(missing_ok=True)
            removed += 1
    return removed


def _cleanup_expired(target_bytes: int | None = None) -> dict:
    """Expire verified copies, then enforce the hard cap oldest-first.

    Only acknowledged, completed, unpinned renders are eligible. Active and
    not-yet-returned renders remain protected even when the cap is exceeded.
    """
    settings = _load_settings()
    before = _folder_bytes(DATA)
    if not settings["storage_enabled"] and target_bytes is None:
        return {"enabled": False, "purged_jobs": 0, "purged_objects": 0,
                "deleted": 0, "freed_bytes": 0, "used_before_bytes": before,
                "used_bytes": before}
    eligible = sorted(
        (job for job in jobs.values()
         if job.get("state") == "done" and not job.get("pinned") and job.get("acked_at")),
        key=lambda job: float(job.get("acked_at") or 0),
    )
    purged = 0
    days = settings["retention_days"]
    cutoff = time.time() - days * 86400 if days is not None else None
    for job in eligible:
        if cutoff is None or float(job.get("acked_at") or 0) > cutoff:
            continue
        _purge_job(job)
        purged += 1
    removed_objects = _remove_unreferenced_objects()
    maximum = (max(0, int(target_bytes)) if target_bytes is not None
               else round(settings["max_storage_gb"] * 1024 ** 3))
    used = _folder_bytes(DATA)
    for job in eligible:
        if used <= maximum:
            break
        if job.get("state") != "done":
            continue
        _purge_job(job)
        purged += 1
        removed_objects += _remove_unreferenced_objects()
        used = _folder_bytes(DATA)
    storage_cache["checked_at"] = 0.0
    used = _folder_bytes(DATA)
    return {
        "enabled": settings["storage_enabled"],
        "retention_days": settings["retention_days"],
        "max_gb": settings["max_storage_gb"],
        "max_bytes": maximum,
        "purged_jobs": purged,
        "purged_objects": removed_objects,
        "deleted": purged + removed_objects,
        "freed_bytes": max(0, before - used),
        "used_before_bytes": before,
        "used_bytes": used,
        "over_limit": used > maximum,
    }


async def _storage_cleanup_loop() -> None:
    await asyncio.sleep(60)
    while True:
        try:
            _cleanup_expired()
        except Exception as exc:
            print(f"[storage] automatic cleanup failed: {exc}", flush=True)
        await asyncio.sleep(3600)


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
    if recipe.get("output_extension", "mp4") not in {"mp4", "mov", "webm"}:
        raise ValueError("output_extension must be mp4, mov, or webm")
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
        extension = asset.get("extension", "")
        if extension and not re.fullmatch(r"\.[A-Za-z0-9]{1,10}", extension):
            raise ValueError(f"asset {name} has an invalid filename extension")
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
    extension = asset.get("extension", "")
    dest = OBJECTS / f"{expected}{extension}"
    if dest.exists() and _sha256(dest) == expected:
        return dest
    partial = dest.with_name(f"{dest.name}.partial")
    last_error: Exception | None = None
    # The Hub owns a seven-day immutable lease for these assets. A worker can
    # therefore retry a transient Tailnet/HTTP stream failure without asking
    # Story Studio to upload the entire episode a second time.
    for attempt in range(1, 5):
        digest = hashlib.sha256()
        total = 0
        try:
            partial.unlink(missing_ok=True)
            async with client.stream(
                "GET", asset["url"], timeout=None,
                headers={"X-Hub-Token": load_token()},
            ) as response:
                response.raise_for_status()
                with partial.open("wb") as handle:
                    async for chunk in response.aiter_bytes(1024 * 1024):
                        total += len(chunk)
                        digest.update(chunk)
                        handle.write(chunk)
            if digest.hexdigest() != expected:
                raise ValueError(f"checksum mismatch for {asset['id']}")
            if asset.get("bytes") is not None and total != int(asset["bytes"]):
                raise ValueError(f"size mismatch for {asset['id']}")
            partial.replace(dest)
            return dest
        except (httpx.HTTPError, OSError, ValueError) as error:
            last_error = error
            partial.unlink(missing_ok=True)
            if attempt < 4:
                await asyncio.sleep(attempt * 2)
    raise ValueError(
        f"could not download render asset {asset['id']} after 4 attempts: {last_error}"
    )


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


async def _stop_process(process: asyncio.subprocess.Process,
                        waiter: asyncio.Task, grace_seconds: float) -> None:
    """Stop and reap a child process before returning to the render task."""
    if process.returncode is not None:
        await waiter
        return
    with contextlib.suppress(ProcessLookupError):
        process.terminate()
    try:
        await asyncio.wait_for(asyncio.shield(waiter), timeout=grace_seconds)
        return
    except asyncio.TimeoutError:
        pass
    if process.returncode is None:
        with contextlib.suppress(ProcessLookupError):
            process.kill()
    await waiter


async def _run_process(
    argv: list[str],
    log: Path,
    *,
    timeout_seconds: float = PROCESS_TIMEOUT_SECONDS,
    heartbeat_seconds: float = PROCESS_HEARTBEAT_SECONDS,
    termination_grace_seconds: float = PROCESS_TERMINATE_GRACE_SECONDS,
    on_heartbeat=None,
) -> int:
    """Run one FFmpeg command with heartbeats and guaranteed child cleanup."""
    timeout_seconds = max(0.01, float(timeout_seconds))
    heartbeat_seconds = max(0.01, min(float(heartbeat_seconds), timeout_seconds))
    termination_grace_seconds = max(0.01, float(termination_grace_seconds))
    with log.open("ab") as handle:
        process = await asyncio.create_subprocess_exec(
            *argv, stdout=handle, stderr=asyncio.subprocess.STDOUT)
        waiter = asyncio.create_task(process.wait())
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout_seconds
        try:
            while True:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise asyncio.TimeoutError
                try:
                    return await asyncio.wait_for(
                        asyncio.shield(waiter),
                        timeout=min(heartbeat_seconds, remaining),
                    )
                except asyncio.TimeoutError:
                    if loop.time() >= deadline:
                        raise
                    if on_heartbeat is not None:
                        result = on_heartbeat()
                        if inspect.isawaitable(result):
                            await result
        except asyncio.TimeoutError as exc:
            await _stop_process(process, waiter, termination_grace_seconds)
            handle.write(
                f"\n[renderstudio] process timed out after {timeout_seconds:g} seconds\n".encode()
            )
            handle.flush()
            raise RenderProcessTimeout(
                f"{Path(argv[0]).name} exceeded the {timeout_seconds:g}-second runtime limit"
            ) from exc
        except asyncio.CancelledError:
            await _stop_process(process, waiter, termination_grace_seconds)
            handle.write(b"\n[renderstudio] process cancelled and stopped\n")
            handle.flush()
            raise
        except BaseException:
            await _stop_process(process, waiter, termination_grace_seconds)
            raise


def _process_failure_detail(log: Path, start_offset: int, max_chars: int = 1600) -> str:
    """Return a bounded, path-scrubbed tail for the command that just failed."""
    try:
        size = log.stat().st_size
        with log.open("rb") as handle:
            handle.seek(max(start_offset, size - 8192))
            raw = handle.read()
    except OSError:
        return ""
    text = raw.decode(errors="replace").replace(str(DATA), "<render-data>")
    lines = [re.sub(r"\s+", " ", line).strip()
             for line in text.splitlines() if line.strip()]
    return " | ".join(lines[-12:])[-max_chars:]


async def _validate_output(output: Path, log: Path, on_heartbeat=None) -> dict:
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
        "-f", "null", "-"], log, on_heartbeat=on_heartbeat)
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
        extension = job["recipe"].get("output_extension", "mp4")
        output = OUTPUTS / f"{job['id']}.partial.{extension}"
        final = OUTPUTS / f"{job['id']}.{extension}"
        try:
            def heartbeat() -> None:
                job["last_heartbeat_at"] = time.time()
                job["duration_seconds"] = round(
                    job["last_heartbeat_at"] - job["started_at"], 2)
                _save_job(job)

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
                log_start = log.stat().st_size if log.exists() else 0
                code = await _run_process([tool, *args], log, on_heartbeat=heartbeat)
                if code and encoder == "h264_videotoolbox" and step["tool"] == "ffmpeg":
                    fallback = [_resolve_arg(arg, assets, work, output, "libx264")
                                for arg in step["args"]]
                    code = await _run_process(
                        [tool, *fallback], log, on_heartbeat=heartbeat)
                    if code == 0:
                        encoder = "libx264"
                if code:
                    message = f"{step['tool']} step {index + 1} failed"
                    detail = _process_failure_detail(log, log_start)
                    if detail:
                        message = f"{message}: {detail}"
                    raise ValueError(message)
                job["progress"] = 0.3 + 0.6 * ((index + 1) / len(recipe["steps"]))
                _save_job(job)
            metadata = await _validate_output(output, log, on_heartbeat=heartbeat)
            output.replace(final)
            job.update(state="done", progress=1.0, output_path=str(final),
                       output_url=f"/api/outputs/{job['id']}",
                       sha256=_sha256(final), bytes=final.stat().st_size,
                       encoder=encoder, media=metadata, finished_at=time.time(),
                       duration_seconds=round(time.time() - job["started_at"], 2))
            try:
                job["video_seconds"] = max(0.0, float(metadata.get("format", {}).get("duration", 0)))
            except (TypeError, ValueError):
                job["video_seconds"] = 0.0
        except asyncio.CancelledError:
            job.update(state="cancelled", finished_at=time.time())
            raise
        except Exception as exc:
            job.update(state="error", error=str(exc), finished_at=time.time())
        finally:
            # On success the partial was renamed to `final`; on error or
            # cancellation (CancelledError bypasses `except Exception`) this
            # removes the orphaned partial so it stops counting against storage.
            output.unlink(missing_ok=True)
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
            "videotoolbox": _has_videotoolbox(),
            "started_at": PROCESS_STARTED_AT,
            "uptime_seconds": round(time.time() - PROCESS_STARTED_AT), **hardware}


@app.get("/api/version")
def version():
    return {"app_version": VERSION, "version": VERSION,
            "app": "renderstudio-mac", "title": app.title}


@app.get("/api/update-status")
def update_status():
    _schedule_update_check()
    latest = update_state["latest"]
    return {"app_version": VERSION, "latest_version": latest,
            "update_available": bool(latest and _parse_version(latest) > _parse_version(VERSION)),
            "checking": bool(update_state["checking"])}


@app.get("/api/auto-update/status")
def automatic_update_status() -> dict:
    return auto_updater.public_status()


@app.get("/api/auto-update/readiness")
def automatic_update_readiness() -> dict:
    return auto_updater.readiness_status()


@app.post("/api/auto-update/settings")
def automatic_update_settings(body: AutoUpdateSettingsBody) -> dict:
    try:
        return auto_updater.save_settings(body.model_dump())
    except UpdateError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/auto-update/check")
def automatic_update_check() -> dict:
    try:
        return auto_updater.trigger_check()
    except UpdateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/auto-update/update")
def automatic_update_run(body: AutoUpdateRequestBody) -> dict:
    try:
        return auto_updater.trigger_update(after_current=body.after_current)
    except UpdateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.post("/api/auto-update/retry")
def automatic_update_retry() -> dict:
    try:
        return auto_updater.retry()
    except UpdateError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@app.get("/api/capabilities")
@app.get("/api/catalog")
def capabilities():
    return {"models": [{"repo": "episode-assembly-v1", "label": "Video Assembly",
                         "cache": {"state": "cached"}, "is_cloud": True,
                         "capabilities": [
                             "video-assembly", "scene-plan-timing", "title-image",
                             "logo-overlay", "color-grading", "vignette", "film-grain",
                             "letterbox", "presentation-frame-media", "title-text-card",
                             "outro-media", "timeline-clips", "music-mix",
                             "background-noise", "overlay-layers", "chroma-key",
                             "animated-subtitles", "compilation-layout", "mov-export",
                             "webm-export",
                         ]}],
            "retention": [1, 3, 7, 15, 30, 90, "forever"]}


@app.get("/api/dashboard")
async def dashboard():
    snapshot = _dashboard_snapshot()
    snapshot["health"] = health()
    snapshot["hub_connection"] = await _test_hub_connection()
    return snapshot


@app.post("/api/connection/test")
async def test_connection():
    return await _test_hub_connection(force=True)


@app.post("/api/generate/render")
async def submit(request: RenderRequest):
    if request.repo != "episode-assembly-v1":
        raise HTTPException(400, "unsupported render recipe")
    if request.workflow != "video_assembly":
        raise HTTPException(400, "unsupported render workflow")
    try:
        _validate_recipe(request.recipe)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    free_gb = psutil.disk_usage(DATA).free / (1024 ** 3)
    if free_gb < _load_settings()["minimum_free_gb"]:
        raise HTTPException(507, "worker is below its minimum free-disk reserve")
    job_id = uuid.uuid4().hex[:12]
    job = {"id": job_id, "state": "queued", "progress": 0.0,
           "label": request.label, "workflow": request.workflow,
           "recipe": request.recipe,
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
async def cancel(job_id: str):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "unknown job")
    task = tasks.get(job_id)
    if task and not task.done():
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    job.update(state="cancelled", finished_at=time.time())
    _save_job(job)
    return {"ok": True}


@app.get("/api/outputs/{job_id}")
def output(job_id: str):
    job = jobs.get(job_id)
    path = Path(job.get("output_path", "")) if job else None
    if not path or not path.is_file() or path.parent != OUTPUTS:
        raise HTTPException(404, "output not found")
    media_types = {".mp4": "video/mp4", ".mov": "video/quicktime", ".webm": "video/webm"}
    return FileResponse(path, media_type=media_types.get(path.suffix.lower(), "application/octet-stream"), filename=path.name)


@app.get("/api/settings")
def get_settings():
    return _load_settings()


@app.put("/api/settings")
def put_settings(request: SettingsRequest):
    if request.retention_days not in RETENTION_CHOICES | {None}:
        raise HTTPException(400, "retention_days must be 1, 3, 7, 15, 30, 90, or null")
    value = request.model_dump()
    value["storage_policy_version"] = STORAGE_POLICY_VERSION
    try:
        value["hub_url"] = _normalise_hub_url(value["hub_url"])
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    SETTINGS_FILE.write_text(json.dumps(value, indent=2) + "\n")
    connection_cache["checked_at"] = 0.0
    return value


@app.post("/api/storage/cleanup")
def cleanup(body: dict | None = None):
    body = body or {}
    target = body.get("target_bytes")
    if target is not None and (not isinstance(target, int) or target < 0):
        raise HTTPException(400, "target_bytes must be a non-negative integer")
    return _cleanup_expired(target)


@app.get("/api/storage-policy")
def get_storage_policy():
    settings = _load_settings()
    used = _folder_bytes(DATA)
    maximum = round(settings["max_storage_gb"] * 1024 ** 3)
    return {
        "enabled": settings["storage_enabled"],
        "retention_days": settings["retention_days"],
        "max_gb": settings["max_storage_gb"],
        "used_bytes": used,
        "max_bytes": maximum,
        "over_limit": settings["storage_enabled"] and used > maximum,
        "scope": "render outputs and verified input cache",
    }


@app.put("/api/storage-policy")
def put_storage_policy(request: StoragePolicyRequest):
    if request.retention_days not in RETENTION_CHOICES:
        raise HTTPException(400, "retention_days must be 1, 3, 7, 15, 30, or 90")
    value = _load_settings()
    value.update(storage_enabled=request.enabled,
                 retention_days=request.retention_days,
                 max_storage_gb=request.max_gb,
                 storage_policy_version=STORAGE_POLICY_VERSION)
    SETTINGS_FILE.write_text(json.dumps(value, indent=2) + "\n")
    return get_storage_policy()


@app.post("/api/storage-policy/cleanup")
def cleanup_storage_policy(body: dict | None = None):
    return cleanup(body)


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
