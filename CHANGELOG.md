# Changelog

## v3.5.1

### Fixed

- Metadata embedding is now opt-in instead of the default, avoiding a quality-neutral full-file FFmpeg rewrite after recording.
- Existing v3 settings migrate metadata embedding to off once; users can explicitly turn it back on from Settings.
- The metadata option now warns that enabling it rewrites the complete media file after download.

## v3.5.0

### Fixed

- Unify network and writer stream IDs to prevent stale duplicate fragment rows.
- Report downloaded and ordered-appended fragments separately, with cumulative byte counts. First reception and append notifications are immediate.
- Coalesce telemetry rather than queue it behind logs; flush final pending samples before phase changes and completion. Repeated LIVE samples no longer bypass throttling.
- Fix exclusive YouTube live-edge counting and use the latest observed head at commit; do not declare a stale head or incomplete audio track caught up.
- Do not reuse download percentages/speed for muxing or revive a completed progress state with late telemetry.
- Enforce quality height caps instead of silently falling back to an uncapped format.
- Save common recording settings explicitly without starting a recording; watch settings save also preserves common values.

- Resize the notebook to the selected tab so short manual forms do not reserve the monitor/settings tab height and hide the progress meter.

### Refactored

- Share the progress reducer and renderer between manual and automatic recording. Isolate thread-safe fragment accounting from the yt-dlp adapter.
- Preserve bounded prefetch, ordered incremental writes, upstream resume/crypto and explicit cancellation. No additional forced disk flushes or re-encoding.
- Rewrite README around installation, recording, monitoring, quality choices and troubleshooting. Move internals to docs/DEVELOPMENT.md.
- Add slow-first-fragment real-HTTP regression and telemetry queue-latency benchmark.

## v3.4.1

### Added

- The automatic-recording tab now has its own `Open folder` action so the save directory remains accessible while manual recording controls are hidden.

## v3.4.0

### Added

- Manual recording now shows progress inside each processing phase: completed phases are 100%, measurable download phases show their current percentage, and intrinsically indeterminate phases remain explicit instead of using fake percentages.
- Live-from-start recording emits a dedicated live-edge catch-up percentage. YouTube DVR progress is normalized from the first actually available sequence to the current live-edge sequence, so truncated DVR windows still converge to 100%.
- Automatic recordings expose a distinct `Catching up to live` state with catch-up percent / remaining fragments and switch to `LIVE` after reaching the edge.

### Improved

- Catch-up telemetry is throttled to protect the GUI under high fragment concurrency, while the final caught-up event is always delivered.

## v3.3.1

### Changed

- Experimental fragment concurrency now supports 192 and 256 workers in the desktop GUI.
- Monitoring adds `ultra` (192) and `experimental_256` (256) presets while preserving the existing 128-worker `max` preset.
- Existing defaults remain unchanged; 192/256 remain explicit high-load choices.
## v3.3.0

### Added

- Automatic-recording rows now show per-channel progress inside the current state, including percentage, fragment position and transfer speed when available.
- Automatic-recording status now distinguishes preparation, stream extraction, downloading and mux/post-processing.

### Changed

- The manual recording action/progress panel is hidden while the YouTube / Twitch auto-record tab is selected; the shared log remains visible.
- Desktop and automatic recordings are stream-copy focused: GPU/CPU post-record export controls were removed from the GUI and saved legacy GPU-export settings migrate to off.
- Monitoring performance presets now control only fragment concurrency and prefetch. The optional standalone `livecatch_core export` CLI remains available for explicit transcoding.

## v3.2.1

### Changed

- The default manual recording layout now groups files by service and channel before the per-broadcast folder.
- Legacy default output templates migrate automatically; custom output templates are preserved.
- README clarifies that GPU export is optional and intended for transcoding/compatibility rather than recording speed.

## v3.2.0

### Added

- Monitoring can accept YouTube/Twitch channel registrations while already running and checks newly added channels immediately.
- `from_start` monitoring catch-up can recover the available YouTube DVR range and the associated growing Twitch VOD when supported, with `live_edge` available for detection-point recording.
- Separate monitoring performance presets: manual, balanced, fast, extreme and max.
- Manual and automatic recordings now show live elapsed time and final processing duration.

### Changed

- Recording, advanced, GPU and background controls are consolidated into a smaller Recording / Settings / Auto-record layout.
- New monitoring configurations default to the `fast` preset while existing saved configurations without a preset retain legacy manual values.

### Fixed

- Windows UI startup now hardens UTF-8 handling and repairs common UTF-8-as-CP932 mojibake in Japanese labels.
- Windows release builds execute the packaged LiveCatch.exe Unicode smoke test before publishing.

## v3.1.2

### Documentation

- Added Windows UTF-8 / mojibake troubleshooting for source and packaged builds.
- Added aggressive PC-spec-based fragment, prefetch, GPU export, and concurrent auto-recording recommendations.

## v3.1.1

### Fixed

- The in-app updater now retries Windows sharing violations while the old GUI, recording worker, or bundled tools finish releasing file handles.
- Updates still fail safely after a bounded timeout instead of overwriting files while a process is using them.

## v3.1.0

### Added

- YouTube / Twitch channel monitoring with automatic recording and optional Windows background/tray mode.
- Self-update integration for the automatic-recording desktop app, including safeguards against updating during monitoring, recording, or finalization.
- Windows release packages now include the in-app updater alongside the installer.

## v3.0.3

### Added

- In-app update button with periodic update checks.
- Self-update package and helper that replace the installed executables and restart LiveCatch without opening a browser or installer wizard.

## v3.0.2

### Fixed

- Runtime tool downloads now use curl retries and atomic temporary files so transient FFmpeg/GitHub download failures do not abort release builds.

## v3.0.1

### Fixed

- Clicking the update status now retries the update check when the app is up to date or the network check failed.
- The update notification now reports the release version and provides a clear action for opening the release page.

### Improved

- Recording progress now shows a visual phase pipeline, percentage, stream details, byte/fragment progress, and export counts.

## v3.0.0

### Added

- Windows installer build using PyInstaller and Inno Setup.
- GitHub Release workflow triggered by matching `vX.Y.Z` tags.
- GUI progress panel for recording phases, fragment/byte progress, saved outputs, and exports.
- Background update notification linked to the latest GitHub Release.
- Bundled FFmpeg, ffprobe, and Deno runtime tools in the installer.

### Changed

- The v3 recording worker is now distributed as a separate executable and managed by the GUI.

## v2.1.3

### Added

- YouTube / Twitch URL auto-detection.
- Japanese / English language switching menu.
- Catch-up priority quality presets.
- Catch-up progress logging with fragment rate and estimated remaining time.
- Output format selection: mp4 / mkv / webm.
- Fast temporary folder support using `yt-dlp -P home` / `-P temp`.
- Lightweight post-processing option for download-up-to-now mode.

### Changed

- Default output template now separates YouTube and Twitch by `%(extractor_key)s`.
- Quality presets are stored internally by stable keys so language switching does not break saved settings.

## v1.1.1

### Fixed / Optimized

- Reduced GUI slowdown during long downloads by throttling repeated fragment progress lines.
- Added automatic log trimming to prevent the Tkinter log area from growing indefinitely.
- Prevented repeated stop requests from creating multiple force-terminate watcher threads.
- Updated `install_tools.ps1` to skip re-downloading existing `yt-dlp.exe` / `ffmpeg.exe`.
- Kept `dist/tools` copy behavior intact when `dist` already exists.
- Added `--clean` to PyInstaller build command to reduce stale build artifacts.

## v1.1.0

### Added

- Speed preset dropdown.
  - Internally maps to `yt-dlp -N / --concurrent-fragments`.
  - Presets: 1, 2, 4, 8, 16, 32, 64, 128.
- Quality preset dropdown.
  - Recommended 1080p or lower.
  - Best quality.
  - 1440p / 1080p / 720p / 480p / 360p or lower.
- Recommended settings by PC/network environment in README.
- Warning dialog for very high speed settings.

### Changed

- Default speed is now 8 concurrent fragments.
- Default quality is now recommended 1080p or lower.

## v1.0.0

Initial release.

### Added

- Three recording modes:
  - Reservation recording
  - Record active livestream from start to end
  - Download active livestream from start to current point and stop
- GUI built with Tkinter
- Local `tools` folder support for `yt-dlp` and `ffmpeg`
- `install_tools.ps1` without `winget`
- Auto tool setup from `build_exe.bat`
- EXE build support with PyInstaller
- Config persistence in `.livecatch_config.json`
- Browser cookie support
- Metadata and info JSON options
