# Render Studio KH Changelog

## 0.7.4 - 2026-07-23

- Changed the service watchdog from restarting after one missed health probe to
  requiring three consecutive failures. A successful probe immediately clears
  the repo-local failure counter, so isolated network or event-loop delays do
  not interrupt a healthy render worker.
- Added validated environment overrides for test and advanced deployments; a
  missing, non-numeric, or unsafe one-failure threshold falls back to three.
  The counter remains under the already git-ignored `service/` runtime folder.
- Added subprocess regression coverage for the three-failure threshold,
  immediate success reset, invalid override handling, and ignored state path.
  Render execution, queueing, updater behavior, and live services were
  deliberately unchanged, and no running render was restarted for this patch.

## 0.7.3 - 2026-07-23

- Fixed automatic-update availability so only a semantically newer published
  release is offered. An installed version newer than or equal to the remote
  release can no longer be presented as a downgrade through Update now.
- Added bounded FFmpeg supervision with a 12-hour default process ceiling,
  persisted 15-second job heartbeats, and terminate-then-kill cleanup. Timeout
  and cancellation now wait until the child is reaped before the partial output
  is removed or the cancellation API returns, preventing orphan writes.
- Aligned the Pinokio sidebar with sibling Studios: Updating and Resetting have
  visible running states, Open UI and Terminal use common names, and What's New
  remains available in install, update, reset, service, running, and idle menus.
- Raised dashboard labels, status text, badges, and controls to the shared
  readable minimum of 12 px text, 15 px controls, and 40 px control height.
- Added regression coverage for downgrade refusal, live heartbeat delivery,
  forced timeout cleanup, cancellation cleanup, readable UI sizing, sidebar
  state feedback, and release-note integrity. Rendering recipes, storage policy,
  Studio Hub scheduling, and encoder selection were deliberately unchanged.

## 0.7.2 - 2026-07-20

- Render failures now include a bounded tail of the actual FFmpeg or FFprobe
  diagnostic instead of only reporting an opaque step number. Worker-local
  data paths are scrubbed before the error travels through Studio Hub.
- Added regression coverage for diagnostic bounds, path scrubbing, and the
  error propagated by a failed render step.
- Added the 0.7.2 dashboard What's New details and a release-integrity test that
  requires the current version in both the changelog and in-product notes.

## 0.7.1 - 2026-07-19

- Changed the default verified-copy retention from seven days to three days and
  added an enabled-by-default 80 GB hard cap across Render Studio outputs,
  verified input objects, and job work data.
- Cleanup now runs hourly without the dashboard being open. It first expires
  old verified copies, then evicts the oldest acknowledged unpinned renders if
  the hard cap is reached. Active, pinned, and not-yet-returned renders remain
  protected even when the worker is over its limit.
- Added modern worker controls for enabling cleanup, retention, storage cap,
  free-disk reserve, Save policy & connection, and Clean now. Added the shared
  authenticated `/api/storage-policy` contract for Studio Hub fleet control.
- Added regression tests for oldest-first cap enforcement, active-render
  protection, and fleet policy persistence. All tests and Python compilation
  pass; FFmpeg execution, update logic, and launcher scripts are unchanged.

## 0.7.0 - 2026-07-19

- Added the same optional Off, Notify only, and Automatic update controller
  used by the sibling Studios, including daily/weekly maintenance schedules,
  installed/latest versions, last/next checks, live state, retry, defer reasons,
  release notes, and verified LaunchAgent scheduling.
- Render updates now wait for every running and queued render to finish. **Update
  after current work** persists independently of the browser and automatically
  retries when the worker becomes idle.
- Added the shared safe updater contract: fixed GitHub origin and `main`, clean
  fast-forward, disk/dependency/import checks, service-aware restart, exact-
  version health verification, one bounded rollback, locking, redacted logs,
  and removable Off-by-default scheduling.
- Render Studio now exposes authenticated automatic-update APIs so Studio Hub
  can include it in Check all, per-app mode changes, individual updates, and
  staggered **Update idle apps** operations.

## 0.6.2 - 2026-07-18

- Kept **Install as Startup Service** available while the manual worker is
  running, matching the sibling Studio launchers.
- The existing service installer already stops the manual listener and takes
  over port 47874 before starting launchd, so conversion no longer requires a
  separate manual stop step. Verified with the launcher flow and the full test
  suite; service scripts and backend behavior were checked and left unchanged.

## 0.6.1 - 2026-07-18

- Retried transient Studio Hub asset-download failures up to four times before
  failing a render. This covers brief Tailnet disconnects and temporary 404s
  while preserving checksum and byte-size verification on every attempt.
- Kept partial downloads isolated and removed them before each retry, so an
  interrupted transfer cannot be mistaken for a verified render input.

## 0.5.0 - 2026-07-16

- Added verified worker-side support for Story Studio title images, logo overlays,
  color grading, vignette, film grain, cinematic letterbox bars, and presentation
  frame backgrounds.
- Render recipes now transfer those supporting assets through Studio Hub with the
  same immutable checksum contract as scene media before FFmpeg runs locally.
- Expanded the published worker capability inventory so routing diagnostics can
  explain why a Video Assembly job is eligible for a Render Studio machine.

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
