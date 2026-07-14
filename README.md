# Render Studio KH

Render Studio KH turns a Mac into an episode-level final-video worker for
Story Studio KH. Studio Hub schedules work; the worker downloads immutable
inputs into its local cache, verifies every checksum, renders locally, validates
the completed video, and exposes the result for return to Story Studio.

## Use

1. Select **Install** once.
2. Select **Start** and leave the worker running.
3. In Studio Hub, discover or register the Mac's `render` studio on port 47874.

For dedicated workers, select **Install as Startup Service** after the normal
Install step. Render Studio will start automatically when that Mac logs in,
restart after crashes, and run a health watchdog every minute. Service mode and
Pinokio's manual Start are mutually exclusive because both use port 47874.

Jobs never preempt one another. Studio Hub grants one heavy-work lease per Mac,
so Image Studio and Render Studio take turns without being stopped mid-job.

## Dashboard

The Render Studio page separates worker health from Studio Hub connectivity and
includes a manual authenticated connection test. It shows the active episode,
queue, hardware, encoder, uptime, disk use, recent render history, and durable
lifetime totals. Historical totals remain available after acknowledged local
copies reach their retention date and are removed.

The Studio Hub address defaults to `http://127.0.0.1:47873`. Set it to the LAN
or Tailscale address of the scheduling Hub when the worker uses a remote Hub.
The dashboard also controls the verified-copy retention period and minimum free
disk reserve.

The header shows the exact installed release and opens **What's New**. Render
Studio checks the published `VERSION` in the background and displays an update
notice when a newer release is available; installation still happens safely
through the **Update** action in Pinokio.

## Safety

- Only FFmpeg and FFprobe steps are accepted; shell commands are never run.
- Inputs must use HTTP(S), include SHA-256 checksums, and download completely
  before rendering begins.
- Output must contain a video stream and pass a complete decode validation.
- VideoToolbox is preferred. A failed hardware encode is retried with libx264.
- Retention cleanup starts only after the main machine acknowledges receipt.

## API

`GET /api/health` reports availability, application version, hardware score,
encoder support, queue depth, and service uptime. `GET /api/version` exposes the
same root release version to Studio Hub. `GET /api/update-status` performs a
non-blocking published-version check. `GET /api/dashboard` returns sanitized
work history, lifetime totals, storage, connection state, and settings.
`GET /api/catalog` advertises `episode-assembly-v1`.

Submit a render with `POST /api/generate/render`:

```json
{
  "repo": "episode-assembly-v1",
  "label": "EP001",
  "recipe": {
    "version": 1,
    "assets": [
      {"id": "audio", "url": "http://hub/assets/audio", "sha256": "..."}
    ],
    "steps": [
      {"tool": "ffmpeg", "args": ["-y", "-i", "{{asset:audio}}", "{{output}}"]}
    ]
  }
}
```

Poll `GET /api/generate/jobs/{id}`, download `output_url`, verify the returned
`sha256`, then call `POST /api/generate/jobs/{id}/ack`.

Python, JavaScript, and curl clients can use these ordinary JSON endpoints.
Remote calls send the shared fleet token in `X-Studio-Token`; loopback calls are
token-exempt.
