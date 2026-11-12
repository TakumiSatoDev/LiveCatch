"""v4 stream data: chat parsing, Twitch IRC, sidecar orchestration, highlights."""
from __future__ import annotations

import json
import random
import sys
from pathlib import Path
from threading import Event, Thread

import pytest

from livecatch_core import highlights as hl
from livecatch_core import sidecar as sc
from livecatch_core.chat import ChatEvent, iter_chat_file, parse_irc, parse_youtube_line, twitch_event
from livecatch_core.config import Settings
from livecatch_core.options import ydl_options
from livecatch_core.twitch_chat import TwitchChatRecorder

T0 = 1_760_000_000_000


def yt_text(t_ms, author, runs, *, live=True, offset=None, paid=None):
    renderer = {"timestampUsec": str(t_ms * 1000), "authorName": {"simpleText": author},
                "message": {"runs": runs}}
    key = "liveChatTextMessageRenderer"
    if paid:
        key = "liveChatPaidMessageRenderer"
        renderer["purchaseAmountText"] = {"simpleText": paid}
    line = {"replayChatItemAction": {"actions": [{"addChatItemAction": {"item": {key: renderer}}}]},
            "videoOffsetTimeMsec": str(offset if offset is not None else 1234)}
    if live:
        line["isLive"] = True
    return json.dumps(line, ensure_ascii=False)


# --- chat parsing ---------------------------------------------------------

def test_youtube_live_line_uses_wallclock_not_pseudo_offset():
    line = yt_text(T0, "Alice", [{"text": "やば "}, {"emoji": {"emojiId": "😂", "shortcuts": [":joy:"]}},
                                 {"emoji": {"emojiId": "UCx/abc", "isCustomEmoji": True,
                                            "shortcuts": [":_kusa:"]}}])
    [event] = parse_youtube_line(line)
    assert event == ChatEvent(T0, None, "Alice", "やば 😂:_kusa:", "text", "")


def test_youtube_replay_line_keeps_exact_offset_and_paid_amount():
    line = yt_text(T0, "Bob", [{"text": "ナイス"}], live=False, offset=65_000, paid="¥1,000")
    [event] = parse_youtube_line(line)
    assert (event.offset_ms, event.kind, event.amount) == (65_000, "paid", "¥1,000")


def test_youtube_parser_ignores_garbage_and_non_chat_actions():
    assert parse_youtube_line("{not json") == []
    assert parse_youtube_line(json.dumps({"replayChatItemAction": {"actions": [
        {"removeChatItemAction": {}}]}})) == []


def test_irc_tags_are_unescaped():
    tags, prefix, command, params = parse_irc(
        r"@display-name=Foo;system-msg=hello\sworld\:x :foo!foo@foo PRIVMSG #chan :hi there")
    assert tags["system-msg"] == "hello world;x"
    assert (prefix, command, params) == ("foo!foo@foo", "PRIVMSG", ["#chan", "hi there"])


def test_twitch_events():
    msg = twitch_event("@display-name=Foo;tmi-sent-ts=1700000000123;bits=100 "
                       ":foo!foo@foo.tmi.twitch.tv PRIVMSG #c :Cheer100 LUL")
    assert (msg.t_ms, msg.author, msg.kind, msg.amount) == (1700000000123, "Foo", "bits", "100 bits")
    sub = twitch_event(r"@msg-id=resub;display-name=Bar;tmi-sent-ts=5;system-msg=Bar\ssubscribed "
                       ":tmi.twitch.tv USERNOTICE #c")
    assert (sub.kind, sub.text) == ("sub", "Bar subscribed")
    assert twitch_event("@msg-id=announcement;tmi-sent-ts=5 :tmi.twitch.tv USERNOTICE #c") is None
    assert twitch_event("PING :tmi.twitch.tv") is None


def test_iter_chat_file_reads_both_formats_and_skips_torn_line(tmp_path):
    path = tmp_path / "x.live_chat.json"
    path.write_text(yt_text(T0, "A", [{"text": "a"}]) + "\n"
                    + ChatEvent(T0 + 1, None, "B", "b").to_json() + "\n" + '{"replayChat', encoding="utf-8")
    assert [e.author for e in iter_chat_file(path)] == ["A", "B"]


# --- Twitch IRC recorder --------------------------------------------------

class FakeSocket:
    def __init__(self, chunks, stop):
        self.chunks, self.stop, self.sent = list(chunks), stop, b""

    def settimeout(self, _):
        pass

    def sendall(self, data):
        self.sent += data

    def recv(self, _n):
        if self.chunks:
            return self.chunks.pop(0)
        self.stop.set()
        raise TimeoutError()

    def close(self):
        pass


def test_twitch_recorder_answers_ping_and_writes_jsonl(tmp_path):
    stop = Event()
    sock = FakeSocket([
        b":tmi.twitch.tv 001 justinfan1 :Welcome\r\n:justinfan1!j@j JOIN #chan\r\nPING :tmi.twitch.tv\r\n",
        b"@display-name=A;tmi-sent-ts=1000 :a!a@a PRIVMSG #chan :hel",
        b"lo\r\n@display-name=B;tmi-sent-ts=2000 :b!b@b PRIVMSG #chan :KEKW\r\n",
    ], stop)
    out = tmp_path / "rec.chat.jsonl"
    recorder = TwitchChatRecorder("Chan", out, stop, connect=lambda: sock)
    assert recorder.run() == 2
    assert b"JOIN #chan\r\n" in sock.sent and b"PONG :tmi.twitch.tv\r\n" in sock.sent
    assert sock.sent.startswith(b"CAP REQ :twitch.tv/tags twitch.tv/commands\r\n")
    assert [(e.t_ms, e.text) for e in iter_chat_file(out)] == [(1000, "hello"), (2000, "KEKW")]


def test_twitch_recorder_rejects_injection():
    with pytest.raises(ValueError):
        TwitchChatRecorder("chan\r\nPRIVMSG", Path("x"), Event())


# --- highlights -----------------------------------------------------------

def synthetic_chat(seed=7, minutes=40, burst_at=900, burst_text="草", burst_n=70):
    rng = random.Random(seed)
    events = []
    for second in range(minutes * 60):
        # ~0.3 msg/s background with natural noise
        for _ in range(sum(rng.random() < 0.1 for _ in range(3))):
            events.append(ChatEvent(T0 + second * 1000 + rng.randrange(1000), None,
                                    f"u{rng.randrange(200)}", rng.choice(["hello", "こんにちは", "それな"])))
    for i in range(burst_n):
        events.append(ChatEvent(T0 + burst_at * 1000 + i * 200, None, f"b{i}", burst_text * 3))
    return events


def test_chat_burst_detected_with_label_and_lead():
    report = hl.analyze(synthetic_chat(), video_start_ms=T0, duration=2400)
    found = report["highlights"]
    assert len(found) == 1, found
    h = found[0]
    assert h["label"] == "laugh" and h["label_text"] == "爆笑"
    assert 900 - 40 <= h["start"] <= 900 - 15
    assert 895 <= h["peak"] <= 915
    assert h["phrases"][0] == "草草草"
    assert report["chat"]["messages"] > 70


def test_steady_noise_has_no_highlights():
    report = hl.analyze(synthetic_chat(burst_n=0), video_start_ms=T0, duration=2400)
    assert report["highlights"] == []


def test_sensitivity_and_shift():
    events = synthetic_chat(burst_n=18)
    low = hl.analyze(events, video_start_ms=T0, duration=2400, opts=hl.Options.for_sensitivity("low"))
    high = hl.analyze(events, video_start_ms=T0, duration=2400, opts=hl.Options.for_sensitivity("high"))
    assert len(high["highlights"]) >= len(low["highlights"])
    shifted = hl.analyze(synthetic_chat(), video_start_ms=T0, duration=2400, shift=-30)
    assert 895 - 30 <= shifted["highlights"][0]["peak"] <= 915 - 30


def test_paid_and_viewer_surges():
    events = synthetic_chat(burst_n=0)
    events += [ChatEvent(T0 + (1500 + i * 5) * 1000, None, f"p{i}", "おめでとう", "paid", "¥500")
               for i in range(5)]
    viewers = [(T0 + m * 60_000, 1000 if m < 30 else 1600) for m in range(40)]
    report = hl.analyze(events, video_start_ms=T0, duration=2400, viewer_samples=viewers)
    kinds = {h["kind"] for h in report["highlights"]}
    assert {"paid", "viewers"} <= kinds
    surge = next(h for h in report["highlights"] if h["kind"] == "viewers")
    assert surge["viewers_delta"] == 600 and "同接 +600" in surge["reasons"]
    assert report["viewers"]["max"] == 1600


def test_replay_offsets_do_not_need_video_start():
    events = [ChatEvent(None, i * 1000, "a", "x") for i in range(10)]
    timeline = hl.resolve_offsets(events, None)
    assert [p for p, _ in timeline] == [float(i) for i in range(10)]


def test_analyze_recording_writes_reports(tmp_path):
    base = tmp_path / "20260101_title"
    with open(str(base) + ".live_chat.json", "w", encoding="utf-8") as out:
        for e in synthetic_chat():
            renderer = {"timestampUsec": str(e.t_ms * 1000), "authorName": {"simpleText": e.author},
                        "message": {"runs": [{"text": e.text}]}}
            out.write(json.dumps({"replayChatItemAction": {"actions": [{"addChatItemAction": {
                "item": {"liveChatTextMessageRenderer": renderer}}}]}, "isLive": True,
                "videoOffsetTimeMsec": "0"}, ensure_ascii=False) + "\n")
    (tmp_path / "20260101_title.livecatch.json").write_text(json.dumps(
        {"video_start_epoch_ms": T0, "duration": 2400.0, "title": "テスト配信"}), encoding="utf-8")
    report, json_path, txt_path = hl.analyze_recording(tmp_path / "20260101_title.mp4")
    assert json_path.name == "20260101_title.highlights.json"
    assert report["timing"] == "wallclock" and len(report["highlights"]) == 1
    text = txt_path.read_text(encoding="utf-8")
    assert "テスト配信" in text and "[爆笑]" in text and "--shift" in text
    assert hl.main([str(tmp_path / "20260101_title.mp4")]) == 0
    assert hl.analyze_recording(tmp_path / "missing.mp4") is None


def test_base_of():
    assert hl.base_of(Path("/a/b.c.mp4")) == Path("/a/b.c")
    assert hl.base_of(Path("/a/b.live_chat.json")) == Path("/a/b")
    assert hl.base_of(Path("/a/b")) == Path("/a/b")


# --- sidecar --------------------------------------------------------------

def job(tmp_path, **kw):
    values = dict(service="youtube", url="https://www.youtube.com/watch?v=abcdefghijk",
                  base=str(tmp_path / "v"), live=True)
    values.update(kw)
    return sc.Job(**values)


def test_job_validation(tmp_path):
    with pytest.raises(ValueError):
        sc.Job.from_dict({"service": "x", "url": "u", "base": str(tmp_path), "live": True})
    with pytest.raises(ValueError):
        sc.Job.from_dict({"service": "youtube", "url": "u", "base": "relative", "live": True})
    assert sc.Job.from_dict({**job(tmp_path).__dict__, "unknown": 1}).live is True


def test_tasks_for(tmp_path):
    names = lambda j: [n for n, _ in sc.tasks_for(j)]
    assert names(job(tmp_path, stats=True)) == ["chat", "stats"]
    assert names(job(tmp_path, live=False, subtitles=True, stats=True)) == ["subtitles", "chat"]
    assert names(job(tmp_path, service="twitch", twitch_login="foo")) == ["chat"]
    assert names(job(tmp_path, service="twitch", twitch_login="foo", live=False)) == []


def test_chat_retry_appends_instead_of_overwriting(tmp_path):
    base = str(tmp_path / "v")
    final, dl = sc.chat_target(base)
    assert (final, dl) == (Path(base + ".live_chat.json"), base)
    final.write_bytes(b"first\n")
    final2, dl2 = sc.chat_target(base)
    assert final2 == final and dl2 == base + ".1"
    Path(dl2 + ".live_chat.json").write_bytes(b"second\ntorn")
    sc.append_and_remove(Path(dl2 + ".live_chat.json"), final)
    assert final.read_bytes() == b"first\nsecond\n"
    assert not Path(dl2 + ".live_chat.json").exists()


def test_youtube_chat_stops_through_progress_hook(tmp_path):
    stop = Event()
    seen = {}

    class FakeYDL:
        def __init__(self, options):
            seen.update(options)

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def download(self, urls):
            path = Path(seen["outtmpl"].replace("%(ext)s", "live_chat.json"))
            path.write_text("line\n", encoding="utf-8")
            stop.set()
            seen["progress_hooks"][0]({"status": "downloading"})
            raise AssertionError("hook should have interrupted")

    logs = []
    sc.run_youtube_chat(job(tmp_path), stop, logs.append, ydl_factory=FakeYDL)
    assert seen["subtitleslangs"] == ["live_chat"] and seen["skip_download"] and seen["nopart"]
    assert (tmp_path / "v.live_chat.json").read_text(encoding="utf-8") == "line\n"
    assert logs == ["Chat saved: v.live_chat.json"]


def test_stats_sampler_records_and_stops_when_offline(tmp_path):
    samples = iter([{"viewers": 10, "live": True, "title": "t"}, {"viewers": 12, "live": True},
                    {"live": False}, {}, {"live": False}])
    logs = []
    sc.run_stats(job(tmp_path, stats=True, stats_interval=15), Event(), logs.append,
                 probe=lambda *_: next(samples), clock=iter(range(0, 10_000, 1)).__next__,
                 wait=lambda _s: None)
    rows = [json.loads(l) for l in (tmp_path / "v.stats.jsonl").read_text().splitlines()]
    assert [r.get("viewers") for r in rows] == [10, 12, None, None]
    assert hl.read_stats(tmp_path / "v.stats.jsonl") == [(rows[0]["t"], 10), (rows[1]["t"], 12)]
    assert "no longer live" in logs[-1]


def test_run_survives_task_failure_and_honours_stop(tmp_path):
    events = []
    stop = Event()

    def boom(*_):
        raise RuntimeError("nope")

    def forever(_job, stop_event, _log):
        stop_event.wait()

    Thread(target=lambda: (Event().wait(0.3), stop.set()), daemon=True).start()
    sc.run(job(tmp_path), stop, lambda kind, **d: events.append(d["message"]),
           tasks=[("bad", boom), ("live", forever)], grace=2)
    assert any("bad failed: RuntimeError: nope" in m for m in events)


def test_sidecar_process_roundtrip(tmp_path):
    logs = []
    proc = sc.SidecarProcess(job(tmp_path, chat=False), lambda kind, **d: logs.append(d))
    assert proc.wait(30)
    assert proc.proc.returncode == 0


# --- settings / worker ----------------------------------------------------

def test_settings_defaults_and_validation():
    s = Settings()
    assert s.save_live_chat and s.record_stats and s.detect_highlights and s.save_extras
    with pytest.raises(ValueError):
        Settings(stats_interval=5).validate(require_url=False)
    with pytest.raises(ValueError):
        Settings(highlight_sensitivity="max").validate(require_url=False)
    assert Settings.from_dict({"save_live_chat": False}).save_live_chat is False


def test_ydl_options_extras_never_request_subtitles():
    opts = ydl_options(Settings(url="https://www.youtube.com/watch?v=abcdefghijk"))
    assert opts["writethumbnail"] and opts["writedescription"]
    assert "writesubtitles" not in opts
    off = ydl_options(Settings(url="https://www.youtube.com/watch?v=abcdefghijk", save_extras=False))
    assert "writethumbnail" not in off


def test_stream_data_lifecycle(tmp_path):
    from livecatch_core.worker import StreamData

    created = []

    class FakeSidecar:
        def __init__(self, job, emit):
            self.job, self.closed = job, False
            created.append(self)

        def close(self):
            self.closed = True

    events = []
    settings = Settings(url="https://www.youtube.com/watch?v=abcdefghijk", save_dir=str(tmp_path))
    data = StreamData(settings, lambda kind, **d: events.append((kind, d)), None, factory=FakeSidecar)
    info = {"extractor_key": "Youtube", "is_live": True, "id": "abcdefghijk", "title": "配信",
            "release_timestamp": T0 // 1000}
    data.start(info, str(tmp_path / "ch" / "rec.mp4"), from_start=True)
    manifest = json.loads((tmp_path / "ch" / "rec.livecatch.json").read_text(encoding="utf-8"))
    assert manifest["video_start_epoch_ms"] == T0 and manifest["from_start"] is True
    assert created[0].job.url == "https://www.youtube.com/watch?v=abcdefghijk"
    assert created[0].job.chat and created[0].job.stats and created[0].job.live
    # Sidecar output appears while recording; finish stops it and runs detection.
    with open(tmp_path / "ch" / "rec.chat.jsonl", "w", encoding="utf-8") as out:
        for e in synthetic_chat():
            out.write(e.to_json() + "\n")
    data.finish(Event())
    assert created[0].closed
    kind, payload = events[-1]
    assert kind == "highlights" and payload["count"] == 1 and "爆笑" in payload["message"]
    # A retry of the same broadcast keeps the original video start.
    again = StreamData(settings, lambda *a, **k: None, None, factory=FakeSidecar)
    again.start(dict(info, release_timestamp=None), str(tmp_path / "ch" / "rec.mp4"), from_start=False)
    assert again.manifest["video_start_epoch_ms"] == T0


def test_stream_data_twitch_job(tmp_path):
    from livecatch_core.worker import StreamData

    jobs = []
    settings = Settings(url="https://www.twitch.tv/SomeOne", save_dir=str(tmp_path))
    data = StreamData(settings, lambda *a, **k: None, None,
                      factory=lambda job, emit: jobs.append(job) or object())
    data.start({"extractor_key": "TwitchStream", "is_live": True, "id": "123"},
               str(tmp_path / "x.mp4"), from_start=False)
    assert jobs[0].service == "twitch" and jobs[0].twitch_login == "someone"
    assert jobs[0].url == "https://www.twitch.tv/someone"
