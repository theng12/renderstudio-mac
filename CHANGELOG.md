# Render Studio KH Changelog

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
