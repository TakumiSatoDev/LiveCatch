"""Highlight ("見所") detection from recorded chat and stream statistics.

Pure analysis: no file I/O, network, GUI or yt-dlp. Reading sidecar files and
writing reports lives in :mod:`highlight_report`.

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
import math
import re
import statistics
import time
from typing import Iterable, Sequence

from .chat import PAID_KINDS, ChatEvent

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


GENERIC_LABELS = frozenset({"chat", "paid", "viewers"})


def _merged_label(primary: Highlight, other: Highlight) -> str:
    """Prefer a specific reaction label (爆笑, 拍手…) over a generic detector name."""
    if primary.label in GENERIC_LABELS and other.label not in GENERIC_LABELS:
        return other.label
    return primary.label


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
                label=_merged_label(primary, other),
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
