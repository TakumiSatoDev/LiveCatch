"""Highlight ("見所") detection from recorded chat and stream statistics.

Pure analysis plus small file writers; no network, no GUI, no yt-dlp.

Detectors
---------
* **Chat burst** – message rate in a short sliding window compared with a robust
  trailing baseline (median + MAD). Catches sudden comment surges.
* **Reaction burst** – the same, per reaction category (laugh, applause, surprise,
  cute, hype, clip request…). Gives each highlight a human-readable label.
* **Paid burst** – Super Chats / stickers / gifts / bits / subs clustering.
* **Viewer surge** – concurrent viewer count rising quickly (stats log).

All times in the output are seconds from the start of the saved video. Chat
reacts *after* the moment it is reacting to, so each highlight's suggested clip
start is placed ``lead`` seconds before the surge begins.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
import json
import math
import os
from pathlib import Path
import re
import statistics
import tempfile
import time
from typing import Iterable, Sequence

from .chat import PAID_KINDS, ChatEvent, iter_chat_file

VERSION = 1
SENSITIVITY = {"low": 4.0, "normal": 3.0, "high": 2.2}

# Reaction lexicon (lower-cased match). Kept deliberately small and explainable.
REACTIONS: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "laugh": ("爆笑", "Laugh", (
        "草", "笑", "ｗｗ", "ww", "lol", "lmao", "kekw", "lul", "omegalul", "😂", "🤣",
        "haha", "xd", "icant", ":_kusa:")),
    "clap": ("拍手", "Applause", ("888", "８８８", "👏", "パチパチ", "clap", "gg", "ggs", "nice", "ナイス", "うまい", "上手")),
    "surprise": ("驚き", "Surprise", (
        "!?", "！？", "?!", "えっ", "え？", "は？", "まじ", "マジ", "wtf", "omg", "pog", "poggers",
        "pogchamp", "😱", "😮", "ファッ", "うおお", "うわ")),
    "cute": ("かわいい", "Cute", ("かわいい", "可愛い", "かわ", "kawaii", "cute", "てぇてぇ", "尊い", "💕", "❤", "♡")),
    "hype": ("盛り上がり", "Hype", (
        "きた", "キタ", "来た", "うおおお", "神", "やば", "ヤバ", "すげ", "すご", "hype", "letsgo",
        "let's go", "lets go", "🔥", "おめでとう", "congrats", "おめ")),
    "clip": ("切り抜き", "Clip", ("切り抜き", "切り抜", "clip", "クリップ", "保存", "神回")),
    "sad": ("しんみり", "Emotional", ("泣", "涙", "😭", "sadge", "かなしい", "悲しい", "えぐ")),
}
_REPEAT = re.compile(r"(.)\1{2,}")


@dataclass
class Options:
    bin_seconds: float = 5.0
    window_bins: int = 3          # sliding window = 15 s
    baseline_seconds: float = 300.0
    threshold: float = 3.0        # robust z-score
    min_ratio: float = 1.8        # window rate vs baseline
    min_messages: int = 8         # per sliding window
    merge_gap: float = 30.0
    lead: float = 20.0            # clip starts this long before the surge
    tail: float = 10.0
    max_highlights: int = 20
    paid_window: float = 60.0
    paid_min: int = 3
    viewer_min_pct: float = 0.15
    viewer_min_abs: int = 30

    @classmethod
    def for_sensitivity(cls, name: str) -> Options:
        if name not in SENSITIVITY:
            raise ValueError(f"Invalid highlight sensitivity: {name!r}")
        return cls(threshold=SENSITIVITY[name])


@dataclass
class Highlight:
    start: float
    peak: float
    end: float
    score: float
    kind: str                       # chat | paid | viewers
    label: str = "chat"             # reaction key or kind
    messages: int = 0
    ratio: float = 0.0
    paid: int = 0
    viewers_delta: int = 0
    phrases: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)


def classify(text: str) -> str | None:
    lower = text.lower()
    best, hits = None, 0
    for key, (_ja, _en, words) in REACTIONS.items():
        count = sum(1 for word in words if word in lower)
        if count > hits:
            best, hits = key, count
    return best


def normalize_phrase(text: str) -> str:
    text = " ".join(text.split()).lower()
    text = _REPEAT.sub(lambda m: m[1] * 3, text)
    return text[:24]


def hms(seconds: float) -> str:
    seconds = max(0, int(seconds))
    return f"{seconds // 3600}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"


# --- Timing ---------------------------------------------------------------

def resolve_offsets(events: Iterable[ChatEvent], video_start_ms: int | None,
                    *, shift: float = 0.0) -> list[tuple[float, ChatEvent]]:
    """Map chat events to seconds in the video.

    Replay offsets are exact. Live chat uses wall-clock time minus the estimated
    video start; without an estimate the first message is treated as 0.
    """
    events = list(events)
    if video_start_ms is None:
        absolute = [e.t_ms for e in events if e.offset_ms is None and e.t_ms is not None]
        video_start_ms = min(absolute) if absolute else 0
    out = []
    for event in events:
        if event.offset_ms is not None:
            pos = event.offset_ms / 1000
        elif event.t_ms is not None:
            pos = (event.t_ms - video_start_ms) / 1000
        else:
            continue
        pos += shift
        if pos >= 0:
            out.append((pos, event))
    out.sort(key=lambda item: item[0])
    return out


# --- Detection ------------------------------------------------------------

def _robust_scores(series: Sequence[float], opts: Options) -> list[tuple[float, float, float]]:
    """Return (score, ratio, baseline) per bin using a trailing robust baseline."""
    hist = max(6, int(opts.baseline_seconds / opts.bin_seconds))
    gap = opts.window_bins  # exclude the window itself from its own baseline
    global_med = statistics.median(series) if series else 0.0
    global_mad = statistics.median([abs(v - global_med) for v in series]) if series else 0.0
    out = []
    for i, value in enumerate(series):
        past = series[max(0, i - gap - hist):max(0, i - gap)]
        if len(past) >= 6:
            med = statistics.median(past)
            mad = statistics.median([abs(v - med) for v in past])
        else:
            med, mad = global_med, global_mad
        # Poisson noise alone has sd = sqrt(mean); the +1 keeps hundreds of
        # overlapping windows from producing chance z≈3 "surges" in quiet chat.
        spread = max(mad * 1.4826, math.sqrt(med) + 1.0, 1.0)
        out.append(((value - med) / spread, value / max(med, 0.5), med))
    return out


def _windowed(counts: Sequence[int], width: int) -> list[int]:
    out, running = [], 0
    for i, value in enumerate(counts):
        running += value
        if i >= width:
            running -= counts[i - width]
        out.append(running)
    return out


def _groups(flags: Sequence[bool], gap_bins: int) -> list[tuple[int, int]]:
    groups, start, last = [], None, None
    for i, flag in enumerate(flags):
        if not flag:
            continue
        if start is None:
            start = last = i
        elif i - last <= gap_bins:
            last = i
        else:
            groups.append((start, last))
            start = last = i
    if start is not None:
        groups.append((start, last))
    return groups


def detect_chat(timeline: list[tuple[float, ChatEvent]], duration: float,
                opts: Options) -> list[Highlight]:
    if not timeline:
        return []
    size = opts.bin_seconds
    bins = int(max(duration, timeline[-1][0]) // size) + 1
    counts = [0] * bins
    by_bin: list[list[ChatEvent]] = [[] for _ in range(bins)]
    for pos, event in timeline:
        index = min(bins - 1, int(pos // size))
        counts[index] += 1
        by_bin[index].append(event)
    window = _windowed(counts, opts.window_bins)
    scored = _robust_scores(window, opts)
    flags = [score >= opts.threshold and ratio >= opts.min_ratio and window[i] >= opts.min_messages
             for i, (score, ratio, _base) in enumerate(scored)]
    found = []
    for first, last in _groups(flags, max(1, int(opts.merge_gap // size))):
        peak = max(range(first, last + 1), key=lambda i: scored[i][0])
        # A window ending at bin i covers bins i-width+1 .. i.
        lo = max(0, first - opts.window_bins + 1)
        items = [e for b in by_bin[lo:last + 1] for e in b]
        labels = Counter(filter(None, (classify(e.text) for e in items)))
        label = "chat"
        if labels:
            key, hits = labels.most_common(1)[0]
            if hits >= max(3, 0.25 * len(items)):
                label = key
        phrases = [p for p, n in Counter(normalize_phrase(e.text) for e in items if e.text.strip())
                   .most_common(3) if n >= 2]
        score, ratio, _base = scored[peak]
        found.append(Highlight(
            start=max(0.0, lo * size - opts.lead),
            peak=max(0.0, (peak - opts.window_bins + 1) * size),
            end=(last + 1) * size + opts.tail,
            score=round(score, 2), kind="chat", label=label,
            messages=len(items), ratio=round(ratio, 2),
            paid=sum(1 for e in items if e.kind in PAID_KINDS), phrases=phrases))
    return found


def detect_paid(timeline: list[tuple[float, ChatEvent]], opts: Options) -> list[Highlight]:
    paid = [(pos, e) for pos, e in timeline if e.kind in PAID_KINDS]
    found, i = [], 0
    while i < len(paid):
        j = i
        while j + 1 < len(paid) and paid[j + 1][0] - paid[i][0] <= opts.paid_window:
            j += 1
        count = j - i + 1
        if count >= opts.paid_min:
            # Extend while the cluster keeps going.
            while j + 1 < len(paid) and paid[j + 1][0] - paid[j][0] <= opts.paid_window / 2:
                j += 1
            count = j - i + 1
            start, end = paid[i][0], paid[j][0]
            found.append(Highlight(
                start=max(0.0, start - opts.lead), peak=start, end=end + opts.tail,
                score=round(opts.threshold + math.log2(count), 2), kind="paid", label="paid",
                messages=count, paid=count))
            i = j + 1
        else:
            i += 1
    return found


def detect_viewers(samples: list[tuple[float, int]], opts: Options) -> list[Highlight]:
    found = []
    for i in range(1, len(samples)):
        pos, value = samples[i]
        lookback = [v for p, v in samples[max(0, i - 3):i] if pos - p <= 600]
        if not lookback:
            continue
        base = min(lookback)
        delta = value - base
        if base > 0 and delta >= opts.viewer_min_abs and delta / base >= opts.viewer_min_pct:
            if found and pos - found[-1].peak <= 600:
                if delta > found[-1].viewers_delta:
                    found[-1].viewers_delta = delta
                    found[-1].score = round(opts.threshold + 10 * delta / base, 2)
                found[-1].end = pos + opts.tail
                continue
            found.append(Highlight(
                start=max(0.0, pos - 90.0), peak=pos, end=pos + opts.tail,
                score=round(opts.threshold + 10 * delta / base, 2), kind="viewers",
                label="viewers", viewers_delta=delta))
    return found


def merge(highlights: list[Highlight]) -> list[Highlight]:
    """Merge overlapping highlights from different detectors into one entry."""
    merged: list[Highlight] = []
    for item in sorted(highlights, key=lambda h: h.start):
        if merged and item.start <= merged[-1].end:
            top = merged[-1]
            primary, other = (top, item) if top.score >= item.score else (item, top)
            combined = Highlight(
                start=min(top.start, item.start), peak=primary.peak, end=max(top.end, item.end),
                score=round(primary.score + 0.25 * other.score, 2), kind=primary.kind,
                label=primary.label if primary.label not in ("chat", "paid", "viewers") or
                other.label in ("chat", "paid", "viewers") else other.label,
                messages=max(top.messages, item.messages), ratio=max(top.ratio, item.ratio),
                paid=max(top.paid, item.paid),
                viewers_delta=max(top.viewers_delta, item.viewers_delta),
                phrases=primary.phrases or other.phrases)
            merged[-1] = combined
        else:
            merged.append(item)
    return merged


def describe(h: Highlight, language: str = "ja") -> list[str]:
    ja = language != "en"
    reasons = []
    if h.ratio:
        reasons.append(f"チャット ×{h.ratio:.1f}（{h.messages}件）" if ja
                       else f"chat ×{h.ratio:.1f} ({h.messages} msgs)")
    if h.paid:
        reasons.append(f"スパチャ/ギフト {h.paid}件" if ja else f"{h.paid} paid/gift events")
    if h.viewers_delta:
        reasons.append(f"同接 +{h.viewers_delta}" if ja else f"viewers +{h.viewers_delta}")
    return reasons


def label_text(label: str, language: str = "ja") -> str:
    if label in REACTIONS:
        ja, en, _ = REACTIONS[label]
        return en if language == "en" else ja
    names = {"chat": ("チャット急増", "Chat surge"), "paid": ("スパチャ集中", "Paid burst"),
             "viewers": ("同接急増", "Viewer surge")}
    ja, en = names.get(label, (label, label))
    return en if language == "en" else ja


def summarize_timeline(timeline: list[tuple[float, ChatEvent]], duration: float) -> dict:
    minutes = int(max(duration, timeline[-1][0] if timeline else 0) // 60) + 1
    per_minute = [0] * minutes
    for pos, _event in timeline:
        per_minute[min(minutes - 1, int(pos // 60))] += 1
    authors = {e.author for _p, e in timeline if e.author}
    return {
        "messages": len(timeline),
        "unique_authors": len(authors),
        "paid_events": sum(1 for _p, e in timeline if e.kind in PAID_KINDS),
        "avg_per_minute": round(len(timeline) / max(1, minutes), 2),
        "peak_per_minute": max(per_minute) if per_minute else 0,
        "per_minute": per_minute,
    }


def analyze(events: Iterable[ChatEvent], *, video_start_ms: int | None = None,
            duration: float = 0.0, viewer_samples: Sequence[tuple[int, int]] = (),
            opts: Options | None = None, shift: float = 0.0, language: str = "ja") -> dict:
    """Run all detectors. ``viewer_samples`` are (epoch_ms, viewers)."""
    opts = opts or Options()
    timeline = resolve_offsets(events, video_start_ms, shift=shift)
    if video_start_ms is None and timeline:
        absolute = [e.t_ms for _p, e in timeline if e.offset_ms is None and e.t_ms is not None]
        video_start_ms = min(absolute) - int(shift * 1000) if absolute else None
    viewers = []
    if video_start_ms is not None:
        viewers = sorted(((t - video_start_ms) / 1000 + shift, v) for t, v in viewer_samples
                         if type(v) is int and (t - video_start_ms) / 1000 + shift >= 0)
    if not duration:
        duration = max([p for p, _e in timeline] + [p for p, _v in viewers] + [0.0])
    found = merge(detect_chat(timeline, duration, opts) + detect_paid(timeline, opts)
                  + detect_viewers(viewers, opts))
    found = [h for h in found if h.start < duration + opts.tail or not duration]
    ranked = sorted(found, key=lambda h: -h.score)[:opts.max_highlights]
    rank = {id(h): i + 1 for i, h in enumerate(ranked)}
    result = []
    for h in sorted(ranked, key=lambda h: h.start):
        h.end = min(h.end, duration) if duration else h.end
        h.reasons = describe(h, language)
        result.append({
            "rank": rank[id(h)], "start": round(h.start, 1), "peak": round(h.peak, 1),
            "end": round(h.end, 1), "start_hms": hms(h.start), "peak_hms": hms(h.peak),
            "score": h.score, "kind": h.kind, "label": h.label,
            "label_text": label_text(h.label, language), "messages": h.messages,
            "ratio": h.ratio, "paid": h.paid, "viewers_delta": h.viewers_delta,
            "phrases": h.phrases, "reasons": h.reasons,
        })
    return {
        "version": VERSION,
        "generated_at": int(time.time()),
        "duration": round(duration, 1),
        "video_start_epoch_ms": video_start_ms,
        "shift": shift,
        "sensitivity_threshold": opts.threshold,
        "chat": summarize_timeline(timeline, duration),
        "viewers": {"samples": len(viewers),
                    "max": max((v for _p, v in viewers), default=None)},
        "highlights": result,
    }


# --- Files ----------------------------------------------------------------

SIDE_SUFFIXES = (".live_chat.json", ".chat.jsonl", ".stats.jsonl", ".livecatch.json",
                 ".highlights.json", ".highlights.txt")
VIDEO_SUFFIXES = (".mp4", ".mkv", ".webm", ".m4a", ".ts")


def base_of(path: Path) -> Path:
    """``dir/name.mp4`` / ``dir/name.live_chat.json`` → ``dir/name``."""
    path = Path(path)
    for suffix in SIDE_SUFFIXES + VIDEO_SUFFIXES + (".info.json",):
        if path.name.endswith(suffix):
            return path.with_name(path.name[:-len(suffix)])
    return path


def read_stats(path: Path) -> list[tuple[int, int]]:
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


def read_manifest(base: Path) -> dict:
    path = Path(str(base) + ".livecatch.json")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as out:
            out.write(text)
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


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
    base = base_of(Path(path))
    manifest = read_manifest(base)
    chat_path = next((Path(str(base) + s) for s in (".live_chat.json", ".chat.jsonl")
                      if Path(str(base) + s).is_file()), None)
    stats = read_stats(Path(str(base) + ".stats.jsonl"))
    if chat_path is None and not stats:
        return None
    events = list(iter_chat_file(chat_path)) if chat_path else []
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
                       "stats": Path(str(base) + ".stats.jsonl").name if stats else None}
    json_path, txt_path = Path(str(base) + ".highlights.json"), Path(str(base) + ".highlights.txt")
    _atomic_write(json_path, json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    _atomic_write(txt_path, render_text(report, str(manifest.get("title") or base.name), language))
    return report, json_path, txt_path


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
