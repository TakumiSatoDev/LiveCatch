"""Process-local adaptation of yt-dlp's real FragmentFD internals.

We keep upstream HTTP requests, retry policy, decryption, resume checkpoints,
ordered appends and muxing. Only fragment scheduling and snapshot termination
are changed. This is NOT a downloader plugin (yt-dlp has no such plugin API).
"""
from __future__ import annotations

from contextlib import contextmanager
import inspect
import math
import re
from threading import Event

from .parallel import OrderedPrefetch
from .snapshot import SnapshotBarrier, snapshot_fragments

SUPPORTED_VERSION = (2026, 8, 19)


def require_supported_version(version: str) -> None:
    try:
        actual = tuple(map(int, version.split(".")))
    except ValueError:
        actual = ()
    if actual != SUPPORTED_VERSION:
        raise RuntimeError(
            f"Unreviewed yt-dlp version {version}. Install requirements.txt or use stock engine; "
            "the private fragment patch is NOT applied to unknown versions.")


@contextmanager
def fragment_patch(cancel: Event, *, prefetch: int = 2, snapshot: bool = False, emit=lambda *_a, **_k: None):
    from yt_dlp.downloader.fragment import FragmentFD
    from yt_dlp.version import __version__
    require_supported_version(__version__)
    with _patch_class(FragmentFD, cancel, prefetch=prefetch, snapshot=snapshot, emit=emit):
        yield


@contextmanager
def _patch_class(cls, cancel: Event, *, prefetch: int, snapshot: bool, emit):
    original = cls.download_and_append_fragments
    multiple = cls.download_and_append_fragments_multiple
    append = cls._append_fragment
    required = {"ctx", "fragments", "info_dict", "tpe", "interrupt_trigger"}
    if not required <= set(inspect.signature(original).parameters):
        raise RuntimeError("yt-dlp FragmentFD signature changed; patch refused")
    if getattr(original, "_livecatch_patch", False):
        raise RuntimeError("Fragment patch is already active; use one isolated worker per recording")

    def patched_multiple(fd, *args, **kwargs):
        group = SnapshotBarrier(len(args), cancel) if snapshot else None
        # Declare ALL streams before starting any; a slow audio stream is not dropped.
        emit("streams", count=len(args))
        for index, (ctx, _fragments, info) in enumerate(args):
            ctx["_lc_stream"] = f"{index}:{info.get('format_id', 'media')}"
            ctx["_lc_snapshot"] = group
        return multiple(fd, *args, **kwargs)

    def patched(fd, ctx, fragments, info_dict, **kwargs):
        try:
            stream = ctx.setdefault("_lc_stream", str(info_dict.get("format_id", "media")))
            if snapshot and info_dict.get("is_live"):
                if (info_dict.get("extractor_key", "").lower() != "youtube"
                        or "http_dash_segments_generator" not in info_dict.get("protocol", "")):
                    raise RuntimeError("Snapshot supports YouTube DVR only; use normal recording or a VOD URL")
                group = ctx.get("_lc_snapshot") or SnapshotBarrier(1, cancel)
                fragments = snapshot_fragments(fragments, group, stream, emit)

            def guarded():
                iterator = iter(fragments)
                while not cancel.is_set():
                    try:
                        item = next(iterator)
                    except StopIteration:
                        return
                    if cancel.is_set():
                        return
                    yield item

            count = max(1, ctx.get("max_progress", 1))
            workers = max(1, math.ceil(fd.params.get("concurrent_fragment_downloads", 1) / count))
            # Do not reuse yt-dlp's pool containing the stream coordinator itself.
            # A separate bounded pool avoids nested-pool starvation.
            kwargs.pop("tpe", None)
            with OrderedPrefetch(workers, workers * prefetch, cancel) as pool:
                return original(fd, ctx, guarded(), info_dict, tpe=pool, **kwargs)
        except BaseException:
            cancel.set()  # Unblock sibling stream/cutoff producers before propagating.
            raise

    def patched_append(fd, ctx, data):
        # Upstream HTTP progress updates ctx['fragment_index'] concurrently.
        # Commit/checkpoint a stable index from the ordered fragment filename,
        # not a completion count that another worker may change during fsync.
        committed = ctx.copy()
        match = re.search(r"-Frag(\d+)$", str(ctx.get("fragment_filename_sanitized", "")))
        if match:
            committed["fragment_index"] = int(match[1])
        try:
            result = append(fd, committed, data)
        finally:
            if "fragment_filename_sanitized" not in committed:
                ctx.pop("fragment_filename_sanitized", None)
        total = ctx.get("total_frags") or ctx.get("fragment_count")
        emit("fragment", stream=ctx.get("_lc_stream", "media"),
             current=committed.get("fragment_index", 0), total=total, bytes=len(data))
        return result

    patched._livecatch_patch = True
    cls.download_and_append_fragments = patched
    cls.download_and_append_fragments_multiple = patched_multiple
    cls._append_fragment = patched_append
    try:
        yield
    finally:
        cls.download_and_append_fragments = original
        cls.download_and_append_fragments_multiple = multiple
        cls._append_fragment = append


@contextmanager
def ffmpeg_stop_bridge(cancel: Event):
    """Give yt-dlp's external FFmpeg a real stdin 'q' on Windows as well.

    Scoped to downloader.external.Popen, not global subprocess/FFmpeg PP state.
    Unknown versions retain upstream behavior instead of receiving a private patch.
    """
    from pathlib import Path
    from threading import Thread
    import yt_dlp.downloader.external as external
    from yt_dlp.version import __version__
    try:
        require_supported_version(__version__)
    except RuntimeError:
        yield
        return
    original = external.Popen

    class ControlledPopen(original):
        def __init__(self, args, *a, **kw):
            super().__init__(args, *a, **kw)
            if isinstance(args, (list, tuple)) and Path(args[0]).stem.lower() == "ffmpeg" and self.stdin:
                def stop_on_request():
                    while self.poll() is None:
                        if cancel.wait(0.1):
                            try:
                                self.stdin.write("q\n" if self.text_mode else b"q\n")
                                self.stdin.flush()
                            except (OSError, ValueError):
                                pass  # Parent can explicitly force-stop a hung child tree.
                            return
                Thread(target=stop_on_request, daemon=True, name="lc-ffmpeg-stop").start()

    external.Popen = ControlledPopen
    try:
        yield
    finally:
        external.Popen = original
