"""Chat message normalization shared by recorders and the highlight detector.

Pure functions only: no network, no GUI. Two on-disk formats are read:

* yt-dlp's YouTube ``*.live_chat.json`` (JSON lines of ``replayChatItemAction``
  wrappers), produced for both live chat and chat replay. The file stays in the
  upstream format so other tools can read it too.
* LiveCatch's normalized ``*.chat.jsonl`` (one :class:`ChatEvent` per line),
  written by the Twitch IRC recorder.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Iterable, Iterator

KINDS = ("text", "paid", "sticker", "member", "gift", "bits", "sub", "raid")
PAID_KINDS = frozenset({"paid", "sticker", "gift", "bits", "sub"})

_YT_RENDERERS = {
    "liveChatTextMessageRenderer": "text",
    "liveChatPaidMessageRenderer": "paid",
    "liveChatPaidStickerRenderer": "sticker",
    "liveChatMembershipItemRenderer": "member",
    "liveChatSponsorshipsGiftPurchaseAnnouncementRenderer": "gift",
}


@dataclass(frozen=True)
class ChatEvent:
    """One chat item.

    ``t_ms`` is the absolute (epoch) send time when known. ``offset_ms`` is an
    exact position in the source video, only available for chat replay.
    """
    t_ms: int | None
    offset_ms: int | None
    author: str
    text: str
    kind: str = "text"
    amount: str = ""

    def to_json(self) -> str:
        return json.dumps({k: v for k, v in asdict(self).items() if v not in (None, "")},
                          ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: dict) -> ChatEvent | None:
        if not isinstance(data, dict):
            return None
        kind = data.get("kind", "text")
        t_ms, offset = data.get("t_ms"), data.get("offset_ms")
        if kind not in KINDS:
            kind = "text"
        if t_ms is not None and type(t_ms) is not int:
            t_ms = None
        if offset is not None and type(offset) is not int:
            offset = None
        if t_ms is None and offset is None:
            return None
        return cls(t_ms, offset, str(data.get("author", ""))[:100],
                   str(data.get("text", ""))[:2000], kind, str(data.get("amount", ""))[:50])


def _int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _simple_text(node) -> str:
    if not isinstance(node, dict):
        return ""
    if isinstance(node.get("simpleText"), str):
        return node["simpleText"]
    return runs_text(node.get("runs"))


def runs_text(runs) -> str:
    """Flatten YouTube message runs. Custom emoji become their ``:shortcut:``."""
    if not isinstance(runs, list):
        return ""
    parts = []
    for run in runs:
        if not isinstance(run, dict):
            continue
        if isinstance(run.get("text"), str):
            parts.append(run["text"])
            continue
        emoji = run.get("emoji")
        if isinstance(emoji, dict):
            shortcuts = emoji.get("shortcuts")
            if emoji.get("isCustomEmoji") and isinstance(shortcuts, list) and shortcuts:
                parts.append(str(shortcuts[0]))
            elif isinstance(emoji.get("emojiId"), str):
                parts.append(emoji["emojiId"])
    return "".join(parts)


def _youtube_item(item: dict, offset_ms: int | None) -> ChatEvent | None:
    for key, kind in _YT_RENDERERS.items():
        renderer = item.get(key)
        if not isinstance(renderer, dict):
            continue
        t_us = _int(renderer.get("timestampUsec"))
        t_ms = t_us // 1000 if t_us is not None else None
        if t_ms is None and offset_ms is None:
            return None
        text = runs_text((renderer.get("message") or {}).get("runs"))
        if kind == "member" and not text:
            text = runs_text((renderer.get("headerSubtext") or {}).get("runs"))
        if kind == "gift":
            header = ((renderer.get("header") or {}).get("liveChatSponsorshipsHeaderRenderer") or {})
            text = runs_text((header.get("primaryText") or {}).get("runs"))
            author = _simple_text(header.get("authorName"))
        else:
            author = _simple_text(renderer.get("authorName"))
        amount = _simple_text(renderer.get("purchaseAmountText"))
        return ChatEvent(t_ms, offset_ms, author[:100], text[:2000], kind, amount[:50])
    return None


def parse_youtube_line(line: str | bytes) -> list[ChatEvent]:
    """Parse one line of yt-dlp's ``live_chat.json``. Invalid lines yield nothing."""
    try:
        data = json.loads(line)
    except ValueError:
        return []
    if not isinstance(data, dict):
        return []
    wrapper = data.get("replayChatItemAction")
    if not isinstance(wrapper, dict):
        return []
    # Live recordings use a pseudo offset relative to when the chat downloader
    # started, which is NOT a video position. Only replay offsets are exact.
    offset = None if data.get("isLive") else _int(
        data.get("videoOffsetTimeMsec", wrapper.get("videoOffsetTimeMsec")))
    events = []
    for action in wrapper.get("actions") or ():
        if not isinstance(action, dict):
            continue
        add = action.get("addChatItemAction")
        if not isinstance(add, dict) or not isinstance(add.get("item"), dict):
            continue
        event = _youtube_item(add["item"], offset)
        if event is not None:
            events.append(event)
    return events


# --- Twitch IRC -----------------------------------------------------------

_TAG_ESCAPES = {"s": " ", ":": ";", "\\": "\\", "r": "\r", "n": "\n"}


def _unescape_tag(value: str) -> str:
    out, chars = [], iter(value)
    for char in chars:
        if char == "\\":
            nxt = next(chars, "")
            out.append(_TAG_ESCAPES.get(nxt, nxt))
        else:
            out.append(char)
    return "".join(out)


def parse_irc(line: str) -> tuple[dict, str, str, list[str]]:
    """Split an IRCv3 line into (tags, prefix, command, params)."""
    tags: dict[str, str] = {}
    prefix = ""
    line = line.rstrip("\r\n")
    if line.startswith("@"):
        raw, _, line = line[1:].partition(" ")
        for item in raw.split(";"):
            key, _, value = item.partition("=")
            tags[key] = _unescape_tag(value)
    if line.startswith(":"):
        prefix, _, line = line[1:].partition(" ")
    trailing = None
    if " :" in line:
        line, trailing = line.split(" :", 1)
    params = line.split()
    command = params.pop(0).upper() if params else ""
    if trailing is not None:
        params.append(trailing)
    return tags, prefix, command, params


def twitch_event(line: str, *, now_ms: int | None = None) -> ChatEvent | None:
    """Convert a Twitch PRIVMSG/USERNOTICE line to a :class:`ChatEvent`."""
    tags, prefix, command, params = parse_irc(line)
    if command not in ("PRIVMSG", "USERNOTICE"):
        return None
    t_ms = _int(tags.get("tmi-sent-ts")) or now_ms
    if t_ms is None:
        return None
    author = tags.get("display-name") or prefix.split("!", 1)[0]
    text = params[1] if len(params) > 1 else ""
    if text.startswith("\x01ACTION ") and text.endswith("\x01"):
        text = text[8:-1]
    kind, amount = "text", ""
    if command == "PRIVMSG":
        bits = _int(tags.get("bits"))
        if bits:
            kind, amount = "bits", f"{bits} bits"
    else:
        msg_id = tags.get("msg-id", "")
        if msg_id in ("sub", "resub"):
            kind = "sub"
        elif msg_id in ("subgift", "submysterygift", "giftpaidupgrade", "anongiftpaidupgrade"):
            kind = "gift"
        elif msg_id == "raid":
            kind, amount = "raid", tags.get("msg-param-viewerCount", "")
        else:
            return None
        text = text or tags.get("system-msg", "")
    return ChatEvent(t_ms, None, author[:100], text[:2000], kind, amount[:50])


# --- Files ----------------------------------------------------------------

def iter_chat_file(path: Path) -> Iterator[ChatEvent]:
    """Read either a yt-dlp ``live_chat.json`` or a LiveCatch ``chat.jsonl``.

    A trailing partial line (e.g. a recording that was force-stopped) is ignored.
    """
    with Path(path).open("rb") as handle:
        for raw in handle:
            raw = raw.strip()
            if not raw:
                continue
            if b'"replayChatItemAction"' in raw:
                yield from parse_youtube_line(raw)
                continue
            try:
                event = ChatEvent.from_dict(json.loads(raw))
            except ValueError:
                continue
            if event is not None:
                yield event


def write_events(handle, events: Iterable[ChatEvent]) -> int:
    count = 0
    for event in events:
        handle.write(event.to_json() + "\n")
        count += 1
    return count
