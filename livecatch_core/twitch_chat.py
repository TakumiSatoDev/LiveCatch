"""Anonymous, read-only Twitch chat recorder (IRC over TLS, stdlib only).

Twitch allows anonymous read access with a ``justinfan<digits>`` nickname, so no
account or token is required. Every message is written immediately as one
normalized JSON line, so a force-stopped recording keeps everything received.
"""
from __future__ import annotations

import random
import socket
import ssl
import time
from pathlib import Path
from threading import Event
from typing import Callable

from .chat import parse_irc, twitch_event

HOST, PORT = "irc.chat.twitch.tv", 6697


def default_connect(timeout: float = 10.0):
    raw = socket.create_connection((HOST, PORT), timeout=timeout)
    return ssl.create_default_context().wrap_socket(raw, server_hostname=HOST)


class TwitchChatRecorder:
    def __init__(self, login: str, path: Path, stop: Event, *,
                 connect: Callable = default_connect, log: Callable[[str], None] = lambda _m: None,
                 clock: Callable[[], float] = time.time, max_backoff: float = 60.0):
        if not login or not login.replace("_", "").isalnum():
            raise ValueError("Invalid Twitch login for chat")
        self.login = login.lower()
        self.path = Path(path)
        self.stop = stop
        self.connect = connect
        self.log = log
        self.clock = clock
        self.max_backoff = max_backoff
        self.count = 0

    def run(self) -> int:
        backoff = 1.0
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8", newline="\n") as out:
            while not self.stop.is_set():
                try:
                    if self._session(out):
                        backoff = 1.0
                except (OSError, ssl.SSLError) as exc:
                    self.log(f"Twitch chat disconnected: {type(exc).__name__}: {exc}")
                if self.stop.wait(backoff):
                    break
                backoff = min(self.max_backoff, backoff * 2)
        return self.count

    def _session(self, out) -> bool:
        """Run one connection. Returns True once the channel was joined."""
        sock = self.connect()
        joined = False
        try:
            sock.settimeout(1.0)
            nick = f"justinfan{random.randint(10000, 99999)}"
            sock.sendall(("CAP REQ :twitch.tv/tags twitch.tv/commands\r\n"
                          f"NICK {nick}\r\nJOIN #{self.login}\r\n").encode())
            buffer = b""
            last_rx = self.clock()
            while not self.stop.is_set():
                try:
                    data = sock.recv(65536)
                except (socket.timeout, TimeoutError):
                    if self.clock() - last_rx > 360:  # server PINGs every ~5 minutes
                        raise TimeoutError("no data from Twitch chat for 6 minutes")
                    continue
                if not data:
                    return joined
                last_rx = self.clock()
                buffer += data
                *lines, buffer = buffer.split(b"\r\n")
                for raw in lines:
                    line = raw.decode("utf-8", "replace")
                    tags, _prefix, command, params = parse_irc(line)
                    if command == "PING":
                        sock.sendall(f"PONG :{params[-1] if params else 'tmi.twitch.tv'}\r\n".encode())
                    elif command == "RECONNECT":
                        return joined
                    elif command == "JOIN" or (command == "ROOMSTATE"):
                        if not joined:
                            self.log(f"Twitch chat joined #{self.login}")
                        joined = True
                    else:
                        event = twitch_event(line, now_ms=int(self.clock() * 1000))
                        if event is not None:
                            out.write(event.to_json() + "\n")
                            out.flush()
                            self.count += 1
            return joined
        finally:
            try:
                sock.close()
            except OSError:
                pass
