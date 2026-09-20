from threading import Event, Thread
from livecatch_core.background import TrayController


def test_async_setup_failure_is_reported_and_stops_tray():
    stopped = Event()
    class Icon:
        @property
        def visible(self): return False
        @visible.setter
        def visible(self, value): raise RuntimeError("Shell notification area unavailable")
        def run(self, setup):
            worker = Thread(target=setup, args=(self,))
            worker.start()
            assert stopped.wait(2)
            worker.join(1)
        def stop(self): stopped.set()
    tray = TrayController(lambda *_: Icon())
    tray.start()
    kind, detail = tray.commands.get(timeout=2)
    assert kind == "failed" and "notification area" in detail
    tray.thread.join(2)
    assert not tray.ready.is_set() and not tray.thread.is_alive()
    assert tray.commands.empty()
    tray.stop()
