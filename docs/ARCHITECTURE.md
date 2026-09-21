# Architecture / handoff

> 進捗系の現行構成・テストは [DEVELOPMENT.md](DEVELOPMENT.md) を参照してください。

`livecatch.py` → `gui.LiveCatchApp` → `Supervisor` → isolated `livecatch_worker.py` → `worker.record`.

GUI/Tk stays on the main thread. Settings are frozen dataclasses, validated before sending one JSON document through worker stdin.
Subsequent stdin lines carry stop commands. Worker stdout is JSON-lines; diagnostics and fragment telemetry are bounded/throttled.
The supervisor owns each child identity. There is no delayed stop callback referring to a mutable future job.
A process completion event cannot be dropped even when telemetry floods the bounded GUI buffer.

Within the worker, `ydl_options` constructs yt-dlp options, `fragment_patch` temporarily replaces private scheduling methods,
`SnapshotBarrier` negotiates a finite YouTube cutoff, and upstream download/postprocessing produces the original file.
The optional `media.export_batch` follows after the original has been moved. It uses a separate bounded pool,
a runtime-tested CUDA path (or CPU fallback), private temp files and no-clobber publication.

Modules have no GUI construction or background thread startup at import time.
Tests are separated into pure/contract, real local subprocess/FFmpeg/Tk, and pinned-yt-dlp HTTP integration.

## Extension boundaries

- Do not add CUDA to HTTP copying/muxing just to show GPU activity.
- Do not increase HTTP fan-out without measuring 429/retry/disk effects; global UI cap remains32.
- Do not silently accept a newer yt-dlp private API. Review upstream diff, update version guard, run real integration then live fixtures.
- Do not remove source recordings after export, overwrite existing targets, or force-stop finalization on an arbitrary timer.
- Do not call Tk widgets or Tk variables from worker threads.
- Adding HDR requires explicit pixel format, colour metadata and tone-mapping policy tests.
- Supporting Twitch snapshot requires real manifest/timestamp semantics, not re-enabling the old log regex heuristic.
