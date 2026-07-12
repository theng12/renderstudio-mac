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

## Safety

- Only FFmpeg and FFprobe steps are accepted; shell commands are never run.
- Inputs must use HTTP(S), include SHA-256 checksums, and download completely
  before rendering begins.
- Output must contain a video stream and pass a complete decode validation.
- VideoToolbox is preferred. A failed hardware encode is retried with libx264.
- Retention cleanup starts only after the main machine acknowledges receipt.

## API

`GET /api/health` reports availability, hardware score, encoder support, and
queue depth. `GET /api/catalog` advertises `episode-assembly-v1`.

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
