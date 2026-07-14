# Render Studio KH Changelog

## 0.4.1 - 2026-07-14

- Replaced the retired timestamp-assembly capability with the unified Story
  Studio `video_assembly` workflow and Scene Plan timing capability.
- Render jobs now retain their workflow identity in durable job history and
  reject unrelated workflow payloads instead of accepting an ambiguous recipe.
- Updated the catalog, API documentation, and regression coverage for unified
  local/Studio Hub episode assembly.

## 0.4.0 - 2026-07-14

- Added a visible installed-version badge to the Render Studio dashboard and
  made the root `VERSION` file the single release source for the backend,
  health response, Studio Hub inventory, dashboard, and release acknowledgement.
- Reworked What's New so its unread indicator follows the actual installed
  release instead of a hardcoded version, and added a clear current-version
  line inside the release view.
- Added a non-blocking, cache-safe published-version check and an in-app update
  notice that directs updates through the existing Pinokio Update action.
  GitHub outages do not delay or block the local dashboard.
- Added regression coverage for version reporting, update comparison, public
  update discovery, and the dynamic release UI.

## 0.3.3 - 2026-07-14

- Fixed a dashboard outage that began the first time a render was purged by
  retention or a manual clean: purging clears a job's `media`, and the video-
  duration lookup then dereferenced `None`, so `/api/dashboard` returned HTTP
  500 on every poll and the page reported the worker as unresponsive. The
  lookup now guards the cleared metadata and reads a durable `video_seconds`
  captured at completion, so lifetime video totals also survive purges.
- Cancelled or crashed renders no longer leave an orphaned `.partial.mp4` in
  the output store counting against disk usage; cleanup now runs for the
  cancellation path too.
- Added regression coverage for dashboards containing purged jobs.

## 0.3.2 - 2026-07-14

- Fixed the reported application version: the worker now reports 0.3.2 from
  `/api/version` and `/api/health` instead of a stale hardcoded 0.3.0, so
  Studio Hub's fleet version scanner and the dashboard show the real version.
  No launcher, encoder, or dependency changes.

## 0.3.1 - 2026-07-13

- Fleet credential saves and rotations now take effect immediately for both
  protected Render Studio requests and authenticated callbacks/downloads to
  Studio Hub; a running worker no longer keeps a stale startup credential.
- Added regression coverage for accepting a rotated credential without a
  process restart. No launcher, encoder, or dependency changes.

## 0.3.0 - 2026-07-12

- Added separate worker-online and authenticated Studio Hub connection status,
  including latency, Hub version, last check time, and a manual connection test.
- Added live current-episode progress and a durable recent render history.
- Added lifetime completed episodes, worker time, average render time, finished
  video duration and bytes, success rate, failures, acknowledgements, retained
  copies, and encoder-use totals.
- Added hardware, service uptime, queue, disk, cache, and output-storage details.
- Added editable Hub address, retention, and free-disk reserve settings.
- Added an in-app What's New view and regression coverage for reporting and
  authenticated Hub testing.

## 0.2.0 - 2026-07-12

- Added a dedicated Render Studio application icon.
- Added an optional macOS startup service with launchd crash recovery and a
  60-second health watchdog.
- Added service status, restart, repair, log, and uninstall actions to Pinokio.
- Updates now refresh the active startup service without starting a competing
  manual worker on port 47874.

## 0.1.0 - 2026-07-12

- Initial episode-level render worker with local verified assets, VideoToolbox
  encoding, CPU fallback, output validation, retention, and Studio Hub support.
