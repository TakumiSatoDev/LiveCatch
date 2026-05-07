# LiveCatch Codex Handoff

## Project

**LiveCatch**

A Windows GUI app for recording YouTube livestreams using `yt-dlp` and `ffmpeg`.

Primary target users:

- Official clip editors
- Stream archive managers
- YouTube livestream clipping workflows

The project is currently at:

```text
LiveCatch v2.1.3
```

## Current Goal

Please review and continue development **without breaking existing behavior**.

The current app already works as a Windows-oriented GUI wrapper around `yt-dlp`.

The next developer/Codex task is to:

1. Review the codebase for duplicated processing, redundant file generation, excessive calls, or long-running UI performance issues.
2. Preserve all existing features.
3. Apply safe optimizations only.
4. Avoid changing UX/behavior unless clearly beneficial.
5. Keep the app simple and understandable.

## Repository Structure

Expected current structure:

```text
LiveCatch-v2.1.3/
├─ livecatch.py
├─ install_tools.ps1
├─ build_exe.bat
├─ run_app.bat
├─ README.md
├─ CHANGELOG.md
├─ LICENSE
├─ .gitignore
└─ requirements.txt
```

After building:

```text
dist/
├─ LiveCatch.exe
└─ tools/
   ├─ yt-dlp.exe
   ├─ ffmpeg.exe
   └─ ffprobe.exe
```

Local tools before building:

```text
tools/
├─ yt-dlp.exe
├─ ffmpeg.exe
└─ ffprobe.exe
```

## Main File

```text
livecatch.py
```

The GUI is built with Python `tkinter`.

No external Python GUI framework is currently used.

## External Tools

LiveCatch depends on:

```text
yt-dlp
ffmpeg
ffprobe
```

These are not bundled in source by default.

They are installed locally by:

```text
install_tools.ps1
```

The app searches for tools in this order:

1. `tools/` folder next to the app or exe
2. App folder
3. System `PATH`

This is handled by:

```python
find_executable(name)
```

## Build Flow

The build script is:

```text
build_exe.bat
```

Current intended behavior:

1. Check whether `tools/yt-dlp.exe` exists.
2. If missing, run `install_tools.ps1`.
3. Check whether `tools/ffmpeg.exe` exists.
4. If missing, run `install_tools.ps1`.
5. Install or update PyInstaller.
6. Build with PyInstaller.
7. Copy tools into `dist/tools`.

Current PyInstaller command:

```bat
pyinstaller --clean --onefile --windowed --name LiveCatch livecatch.py
```

## Tool Installer Flow

The installer is:

```text
install_tools.ps1
```

Expected behavior:

1. Create `tools/`.
2. Download `yt-dlp.exe` if it does not already exist.
3. Download and extract ffmpeg if `ffmpeg.exe` does not already exist.
4. Copy `ffmpeg.exe` and `ffprobe.exe` into `tools/`.
5. Clean `_tmp_install/`.
6. If `dist/` exists, copy tools into `dist/tools`.

Important behavior to preserve:

- Do not require `winget`.
- Do not re-download tools unnecessarily.
- If `dist` already exists, ensure `dist/tools` is updated.

## App Modes

LiveCatch has three recording modes.

### 1. Reservation Recording

Label:

```text
予約録画
```

Behavior:

```text
Waiting room URL → wait until livestream starts → record → stop when stream ends
```

Uses:

```text
--wait-for-video <seconds>
--live-from-start optional
```

### 2. Record Active Livestream From Start To End

Label:

```text
配信中ライブを最後まで録画
```

Behavior:

```text
Active livestream URL → fetch from beginning if DVR is enabled → catch up to live point → continue recording until livestream ends
```

Uses:

```text
--live-from-start
```

This mode is important and must be preserved.

It is useful when the user joins a stream late but wants the full archive from start to end.

### 3. Download Active Livestream From Start To Current Point And Stop

Label:

```text
配信中ライブを現在まで取得
```

Behavior:

```text
Active livestream URL → fetch from beginning if DVR is enabled → stop once it catches up to the current live point
```

Uses:

```text
--live-from-start
```

The catch-up detection reads `yt-dlp` fragment logs like:

```text
frag 8999/9001
```

The app currently watches progress internally and sends a stop request once the current fragment approaches the latest known fragment.

Important behavior to preserve:

- The detector should keep reading every line from `yt-dlp`.
- The GUI does not need to display every progress line.
- Stopping should still allow ffmpeg/yt-dlp cleanup if possible.

## Settings

Settings are persisted to:

```text
C:\Users\<username>\.livecatch_config.json
```

Important saved settings:

```json
{
  "version": "2.1.3",
  "mode": "reservation",
  "url": "",
  "save_dir": "...",
  "wait_seconds": 30,
  "cookies_from_browser": false,
  "browser": "chrome",
  "live_from_start": true,
  "write_info_json": true,
  "embed_metadata": true,
  "quality_preset": "おすすめ：1080p以下",
  "concurrent_fragments": 8,
  "output_template": "%(upload_date)s_%(channel)s_%(title)s/%(upload_date)s_%(title)s.%(ext)s"
}
```

Important behavior:

- The last used mode must be restored on next launch.
- Speed and quality settings should also persist.
- Existing user config should not be broken when adding new keys.

## Speed Setting

The speed setting is **not disk write speed**.

It maps to `yt-dlp`:

```text
-N / --concurrent-fragments
```

Current presets:

```text
1
2
4
8
16
32
64
128
```

Default:

```text
8
```

Important:

- This only speeds up already-buffered DVR content.
- It cannot download future livestream content faster than real time.
- Very high values like `64` or `128` should warn the user.

## Quality Setting

Quality is selected via a dropdown.

Current presets:

```python
QUALITY_PRESETS = {
    "おすすめ：1080p以下": "bv*[height<=1080]+ba/b[height<=1080]/b",
    "最高画質": "bv*+ba/b",
    "1440p以下": "bv*[height<=1440]+ba/b[height<=1440]/b",
    "1080p以下": "bv*[height<=1080]+ba/b[height<=1080]/b",
    "720p以下": "bv*[height<=720]+ba/b[height<=720]/b",
    "480p以下": "bv*[height<=480]+ba/b[height<=480]/b",
    "360p以下": "bv*[height<=360]+ba/b[height<=360]/b",
}
```

Default:

```text
おすすめ：1080p以下
```

## Command Construction

The main command builder is:

```python
build_command()
```

Current command pattern:

```text
yt-dlp
-N <concurrent_fragments>
-f <quality_selector>
--merge-output-format mp4
--newline
-o <output_path>
--ffmpeg-location <ffmpeg_dir>   optional
--wait-for-video <seconds>       reservation mode only
--live-from-start                depending on mode
--write-info-json                optional
--embed-metadata                 optional
--cookies-from-browser <browser> optional
<url>
```

Important:

- Do not remove `--newline` unless catch-up detection is redesigned.
- Do not remove `--live-from-start` from modes 2 or 3.
- Do not remove `--ffmpeg-location` because bundled/local ffmpeg support depends on it.

## Known Implementation Details

### Catch-up detection

The regex is:

```python
FRAG_RE = re.compile(
    r"^(?:(?P<stream>\d+):\s*)?.*?\(frag\s+(?P<current>\d+)\s*/\s*(?P<total>\d+)\)"
)
```

It tracks per-stream fragment state.

Important behavior:

- Wait at least 15 seconds before allowing auto-stop.
- Ignore very small totals under 20.
- Consider active streams updated within the last 10 seconds.
- Stop if all active streams are within 1 fragment of the total for at least 2 seconds.
- Send graceful interrupt first.
- Force terminate after 45 seconds if still alive.

Potential future improvement:

- Make catch-up detection more robust if `yt-dlp` changes log format.
- Consider a user-visible "catch-up sensitivity" advanced setting later, but not necessary now.

### Log performance

v1.1.1 introduced log throttling/trimming:

- The catch-up detector still reads every line.
- GUI display throttles repeated fragment progress lines.
- GUI log trims old lines to prevent unlimited growth.

Relevant constants:

```python
MAX_LOG_LINES = 2500
LOG_TRIM_LINES = 500
DOWNLOAD_LOG_INTERVAL_SEC = 0.25
```

Please preserve the distinction between:

```text
internal line processing
```

and

```text
GUI log display
```

The app must not miss catch-up detection events just because GUI display is throttled.

## Review Checklist

Please review the code for:

### File generation

Check that the app does not accidentally generate duplicate output files.

Expected output behavior:

- `yt-dlp` creates the final recording file.
- `--write-info-json` may create `.info.json`.
- Temporary files may exist while downloading.
- ffmpeg/yt-dlp may create intermediate files internally.

Watch for:

- Duplicate mp4 files.
- Duplicate JSON files.
- Files being created both in root and in selected save folder.
- Unexpected files in `dist/` during runtime.

### Tool duplication

Check:

- `install_tools.ps1` should not repeatedly download tools if they already exist.
- `build_exe.bat` should not run `install_tools.ps1` twice unnecessarily.
- `dist/tools` copy should be idempotent.

Potential improvement:

- In `build_exe.bat`, if `yt-dlp.exe` is missing and `ffmpeg.exe` is missing, it may call installer once, then skip the second call after tools exist.
- This is already mostly safe, but can be made cleaner by checking both first and calling installer once.

### Process handling

Check:

- Stop button should not spawn multiple watcher threads.
- Auto-stop in mode 3 should not repeatedly send interrupts.
- Manual stop should still work in all modes.
- Process should not be orphaned.
- `CTRL_BREAK_EVENT` should work when process is created with `CREATE_NEW_PROCESS_GROUP`.

### GUI performance

Check:

- Long-running downloads should not freeze because of too many log lines.
- Queue polling should not process infinite messages in a single UI tick.
- Text widget should not grow forever.

### Config compatibility

Check:

- Existing `.livecatch_config.json` from older versions should still load.
- Missing new keys should use defaults.
- Bad values should not crash the app.

### Command safety

Check:

- URL is passed as a subprocess argument list, not shell string.
- Output path is passed as argument list.
- Browser cookie option is only added when selected.
- Very high speed setting warns the user.

## Do Not Break

Do not remove these features:

- Three recording modes.
- Last-used mode restore.
- Local `tools` folder detection.
- `winget`-free setup.
- `dist/tools` copy behavior.
- Speed dropdown.
- Quality dropdown.
- Browser Cookie support.
- `info.json` option.
- Metadata option.
- Save folder selection.
- Command preview.
- Tool check button.

## Suggested Safe Improvements

These are acceptable if done carefully:

1. Cleaner installer/build flow:
   - In `build_exe.bat`, detect whether either tool is missing and run `install_tools.ps1` only once.

2. Better status messages:
   - Show selected mode, speed, quality, save path at start.

3. Safer log trimming:
   - Keep the latest logs only.
   - Avoid trimming every single line if expensive.

4. Graceful stop reliability:
   - Ensure manual stop and auto-stop share the same code path.

5. Code organization:
   - Split constants and preset definitions cleanly.
   - Avoid large behavioral refactors unless necessary.

6. Add version display:
   - Already included in title and header as `LiveCatch v2.1.3`.

## Suggested Future Features

Do not implement unless requested, but these are reasonable roadmap ideas:

- Multiple URL queue.
- Simultaneous recording sessions.
- Recording history list.
- Per-channel save folder presets.
- Automatic file name sanitization display.
- Tray notification on completion.
- Error summary after failed recording.
- Update checker for `yt-dlp`.
- Advanced catch-up sensitivity settings.
- Dark mode.
- Portable release zip containing `LiveCatch.exe` and `tools/`.

## Build / Test Commands

### Run from source

```powershell
.\run_app.bat
```

or:

```powershell
python livecatch.py
```

### Install tools

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
.\install_tools.ps1
```

### Build exe

```powershell
.\build_exe.bat
```

### Expected build output

```text
dist/
├─ LiveCatch.exe
└─ tools/
   ├─ yt-dlp.exe
   ├─ ffmpeg.exe
   └─ ffprobe.exe
```

### Python syntax check

```powershell
python -m py_compile livecatch.py
```

## Release Notes Template

For the next release, use:

```markdown
## LiveCatch vX.Y.Z

### Added

-

### Changed

-

### Fixed

-

### Notes

-
```

## Important Legal / Policy Note

LiveCatch should be used only where the user has permission to record and process the livestream.

Keep README wording focused on:

```text
official clip editors
authorized archive management
personal workflows with permission
```

Avoid positioning the tool as a way to bypass rights or restrictions.

## Current Status Summary

LiveCatch v2.1.3 is a working MVP/release candidate.

The most important next step is a careful code cleanup pass, not a major feature rewrite.

Prioritize:

```text
stability
no duplicate processing
safe build/install behavior
long-run GUI performance
config compatibility
```
