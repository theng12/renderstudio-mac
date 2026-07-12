# Render Studio KH Changelog

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
