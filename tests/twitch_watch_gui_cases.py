"""Run with xvfb-run on Linux. No Twitch/network/GPU calls are made."""
import os
from pathlib import Path
import pytest

pytestmark = pytest.mark.skipif(os.name != 'nt' and not os.environ.get('DISPLAY'), reason='Tk display unavailable; run under Xvfb')

from livecatch_core.config import ConfigStore
from livecatch_core.events import EventBuffer
from livecatch_core.twitch_watch import WatchStore, WatchManager
from livecatch_core.twitch_watch_gui import TwitchWatchApp
from livecatch_core.gui import LiveCatchApp


class FakeWorker:
    def __init__(self, *args): self.active=False; self.events=EventBuffer(); self.stopped=False
    def start(self, settings): self.active=True; self.settings=settings
    def stop(self): self.stopped=True
    def force_stop(self): self.finish('forced')
    def finish(self, status='completed', result=None):
        if result: self.events.put({'event':'probe_result',**result})
        self.active=False; self.events.put({'event':'done','status':status,'code':0})


@pytest.fixture
def app(tmp_path, monkeypatch):
    monkeypatch.setattr(TwitchWatchApp, '_check_updates', lambda self: None)
    monkeypatch.setattr('livecatch_core.twitch_watch_gui.find_tool', lambda name:'/fake/'+name)
    def fail(title, message): raise AssertionError(message)
    monkeypatch.setattr('livecatch_core.twitch_watch_gui.messagebox.showerror', fail)
    monkeypatch.setattr('livecatch_core.twitch_watch_gui.messagebox.askyesno', lambda *a,**k:True)
    manager=WatchManager(factory=lambda *a: FakeWorker())
    instance=TwitchWatchApp(store=ConfigStore(tmp_path/'config.json'), watch_store=WatchStore(tmp_path/'watch.json'), manager=manager)
    manager.manual_channels=instance._manual_channels
    instance.update()
    try: yield instance
    finally:
        manager.shutdown(force=True)
        try: instance.destroy()
        except Exception: pass


def test_watch_tab_registration_persistence_and_language(app):
    assert len(app.notebook.tabs()) == 4
    assert not app.watch_manager.running
    app.watch_input.set('alice,https://twitch.tv/Bob alice')
    app._watch_add()
    assert app.watch_tree.get_children() == ('alice','bob')
    assert [c.login for c in app.watch_store.load().channels] == ['alice','bob']
    app.watch_tree.selection_set('bob'); app._watch_toggle()
    assert app.watch_config.channels[1].enabled is False
    app.vars['language'].set('en'); app._rebuild(); app.update()
    app.notebook.select(3); app.update()
    assert app.watch_tree.winfo_ismapped() and app.start_button.winfo_ismapped()
    assert app.watch_tree.get_children() == ('alice','bob')
    app.watch_tree.selection_set('bob'); app._watch_remove()
    assert app.watch_tree.get_children() == ('alice',)


def test_gui_start_detection_stop_and_retry(app):
    app.watch_input.set('alice'); app._watch_add(); app._watch_start()
    manager=app.watch_manager
    assert manager.running
    manager.tick(); s=manager.states['alice']
    s.probe.worker.finish(result={'status':'live','stream_id':'1234','title':'Synthetic'})
    manager.tick(); app._render_watch()
    assert s.recording is not None
    app.watch_tree.selection_set('alice'); app._watch_selected_stop()
    assert s.recording.worker.stopped
    s.recording.worker.finish('cancelled'); manager.tick()
    app._watch_retry(); assert s.suppressed_id == ''


def test_manual_start_reserves_channel_through_modal_dialog(app, monkeypatch):
    app.vars['url'].set('https://twitch.tv/alice')
    app.watch_input.set('alice'); app._watch_add(); app._watch_start(); app.watch_manager.tick()
    s=app.watch_manager.states['alice']
    def modal_start(self):
        # Simulate an async result arriving inside the base handler's dialog.
        s.probe.worker.finish(result={'status':'live','stream_id':'1234','title':'Synthetic'})
        app.watch_manager.tick()
        assert s.recording is None and s.status == 'manual'
    monkeypatch.setattr(LiveCatchApp, '_start', modal_start)
    app._start()
    assert app._manual_pending is None


def test_manual_done_does_not_close_while_auto_recording_finishes(app):
    app.watch_input.set('alice'); app._watch_add(); app._watch_start()
    manager=app.watch_manager; manager.tick(); s=manager.states['alice']
    s.probe.worker.finish(result={'status':'live','stream_id':'1234','title':'Synthetic'})
    manager.tick()
    app._close()
    assert app._watch_closing and s.recording.worker.stopped
    app.supervisor.events.put({'event':'done','status':'completed','code':0})
    app._poll()
    assert app.winfo_exists()
    s.recording.worker.finish('cancelled')
    app._poll()
    # The second _poll destroys the root only after auto-recording termination.
