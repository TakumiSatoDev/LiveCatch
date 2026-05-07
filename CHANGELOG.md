# Changelog

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
