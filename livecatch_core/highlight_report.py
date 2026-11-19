"""Highlight reports: read a recording's sidecar files, analyze, write reports.

Also the ``python -m livecatch_core highlights`` command.
"""
from __future__ import annotations

import json
from pathlib import Path

from .chat import iter_chat_file
from .highlights import SENSITIVITY, Options, analyze
from .stream_files import StreamFiles, atomic_write_text, base_of, write_json

__all__ = ["analyze_recording", "base_of", "main", "read_chat", "read_stats", "render_text"]


def read_chat(path: Path) -> list:
    """Read chat events, dropping exact duplicates.

    A retried recording of the same broadcast appends its chat to the existing
    file, and YouTube re-sends recent backlog on reconnect. Without this the
    overlap would look like a comment surge at the retry point.
    """
    seen, events = set(), []
    for event in iter_chat_file(path):
        if event not in seen:
            seen.add(event)
            events.append(event)
    return events


def read_stats(path: Path) -> list[tuple[int, int]]:
    """Return ``(epoch_ms, viewers)`` samples; unreadable lines are skipped."""
    samples = []
    try:
        handle = Path(path).open("rb")
    except FileNotFoundError:
        return samples
    with handle:
        for raw in handle:
            try:
                data = json.loads(raw)
            except ValueError:
                continue
            if isinstance(data, dict) and type(data.get("t")) is int and type(data.get("viewers")) is int:
                samples.append((data["t"], data["viewers"]))
    return samples


def render_text(report: dict, title: str = "", language: str = "ja") -> str:
    ja = language != "en"
    chat = report["chat"]
    lines = [f"LiveCatch {'見所' if ja else 'highlights'}: {title}".rstrip(": "), ""]
    if ja:
        lines.append(f"コメント {chat['messages']}件 / 参加 {chat['unique_authors']}人 / "
                     f"平均 {chat['avg_per_minute']}件/分 / 最大 {chat['peak_per_minute']}件/分")
    else:
        lines.append(f"{chat['messages']} messages / {chat['unique_authors']} chatters / "
                     f"avg {chat['avg_per_minute']}/min / peak {chat['peak_per_minute']}/min")
    if report["viewers"]["max"] is not None:
        lines.append(("最大同接 " if ja else "Peak viewers ") + str(report["viewers"]["max"]))
    lines.append("")
    if not report["highlights"]:
        lines.append("見所は検出されませんでした。" if ja else "No highlights detected.")
    for h in report["highlights"]:
        phrase = " ".join(f"「{p}」" for p in h["phrases"]) if ja else " ".join(f'"{p}"' for p in h["phrases"])
        lines.append(f"{h['start_hms']}  #{h['rank']:<2} [{h['label_text']}] "
                     f"{' / '.join(h['reasons'])} {phrase}".rstrip())
    lines += ["", ("時刻は動画の先頭からの位置です（クリップ開始の目安として少し前に置いています）。"
                   if ja else "Times are positions in the saved video, placed slightly before each surge.")]
    if report.get("timing") == "wallclock":
        lines.append("ライブ録画のため時刻は推定です。ずれる場合は `highlights --shift 秒` で補正できます。"
                     if ja else "Live recording: times are estimated. Use `highlights --shift SECONDS` to correct.")
    return "\n".join(lines) + "\n"


def analyze_recording(path: Path, *, sensitivity: str = "normal", shift: float = 0.0,
                      duration: float | None = None, language: str = "ja",
                      max_highlights: int | None = None) -> tuple[dict, Path, Path] | None:
    """Analyze the sidecar files next to a recording and write the reports.

    Returns ``(report, json_path, txt_path)`` or ``None`` if there is nothing to analyze.
    """
    files = StreamFiles.for_path(path)
    manifest = files.read_manifest()
    chat_path = files.chat()
    stats = read_stats(files.stats)
    if chat_path is None and not stats:
        return None
    events = read_chat(chat_path) if chat_path else []
    opts = Options.for_sensitivity(sensitivity)
    if max_highlights is not None:
        opts.max_highlights = max_highlights
    start = manifest.get("video_start_epoch_ms")
    if duration is None:
        duration = manifest.get("duration") if isinstance(manifest.get("duration"), (int, float)) else 0.0
    report = analyze(events, video_start_ms=start if type(start) is int else None,
                     duration=float(duration or 0.0), viewer_samples=stats, opts=opts,
                     shift=shift, language=language)
    replay = bool(events) and all(e.offset_ms is not None for e in events)
    report["timing"] = "replay" if replay else "wallclock"
    report["source"] = {k: manifest.get(k) for k in ("url", "video_id", "service", "title")
                        if manifest.get(k)}
    report["files"] = {"chat": chat_path.name if chat_path else None,
                       "stats": files.stats.name if stats else None}
    write_json(files.highlights_json, report)
    atomic_write_text(files.highlights_txt,
                      render_text(report, str(manifest.get("title") or files.base.name), language))
    return report, files.highlights_json, files.highlights_txt


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m livecatch_core highlights",
        description="Detect highlights from a recording's chat/stats sidecar files.")
    parser.add_argument("files", nargs="+", type=Path,
                        help="recording, *.live_chat.json, *.chat.jsonl or base path")
    parser.add_argument("--sensitivity", choices=tuple(SENSITIVITY), default="normal")
    parser.add_argument("--shift", type=float, default=0.0,
                        help="add SECONDS to every chat time (fix live timing drift)")
    parser.add_argument("--top", type=int, default=None, help="maximum highlights")
    parser.add_argument("--language", choices=("ja", "en"), default="ja")
    args = parser.parse_args(argv)
    status = 0
    for item in args.files:
        result = analyze_recording(item, sensitivity=args.sensitivity, shift=args.shift,
                                   language=args.language, max_highlights=args.top)
        if result is None:
            print(f"{item}: no chat/stats sidecar found")
            status = 1
            continue
        report, _json, txt = result
        print(txt.read_text(encoding="utf-8"))
    return status
