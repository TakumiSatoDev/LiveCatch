from pathlib import Path
from queue import Queue
import sys
import tempfile
from threading import Event
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))

from livecatch_core.background import BackgroundConfig, BackgroundStore
from livecatch_core.config import ConfigStore
from livecatch_core.desktop import DesktopApp
from livecatch_core.events import EventBuffer
from livecatch_core.twitch_watch import WatchManager, WatchStore
from livecatch_core import twitch_watch_gui, desktop

class Tray:
    def __init__(self):self.ready=Event();self.commands=Queue();self.started=False;self.stopped=False
    def start(self,*args):self.started=True
    def stop(self):self.stopped=True;self.ready.clear()
class Worker:
    def __init__(self):self.events=EventBuffer();self.active=False
    def start(self,settings):self.active=True
    def stop(self):self.active=False;self.events.put({'event':'done','status':'cancelled'})
    force_stop=stop
class Startup:
    def __init__(self):self.writes=[]
    def enabled(self):return False
    def set_enabled(self,enabled):self.writes.append(enabled)

with tempfile.TemporaryDirectory() as directory:
    root=Path(directory);tray=Tray();startup=Startup();now=[100.0]
    manager=WatchManager(lambda *args:Worker(),clock=lambda:now[0])
    DesktopApp._check_updates=lambda self:None
    twitch_watch_gui.find_tool=lambda name:'/fake/'+name
    warnings=[]
    desktop.messagebox.showwarning=lambda *args:warnings.append(args)
    desktop.messagebox.askyesno=lambda *args:True
    def fail(*args):raise AssertionError(args)
    desktop.messagebox.showerror=fail
    app=DesktopApp(store=ConfigStore(root/'config.json'),watch_store=WatchStore(root/'watch.json'),
                   manager=manager,background_store=BackgroundStore(root/'background.json'),tray=tray,startup=startup)
    try:
        app.update()
        assert len(app.notebook.tabs())==5 and startup.writes==[]
        case=sys.argv[1]
        if case=='mixed':
            app.watch_input.set('example @Example');app._watch_add()
            assert [c.login for c in app.watch_config.channels]==['example','youtube:@example']
            app.vars['language'].set('en');app._rebuild();app.update()
            assert 'YouTube' in app.notebook.tab(3,'text')
            assert len(app.watch_tree.get_children())==2
        elif case in {'tray','failure'}:
            app.background_config=BackgroundConfig(close_to_tray=True)
            app.watch_input.set('@Example');app._watch_add();app._watch_start()
            app._close();app.update()
            assert tray.started and app.state()!='withdrawn' and manager.running
            if case=='tray':
                tray.ready.set();tray.commands.put(('ready',''));app._drain_tray();app.update()
                assert app.state()=='withdrawn' and manager.running
                now[0]+=1;app._poll()
                assert manager.states['youtube:@example'].probe is not None
                tray.commands.put(('open',''));app._drain_tray();app.update()
                assert app.state()!='withdrawn'
            else:
                tray.commands.put(('failed','test failure'));app._drain_tray();app.update()
                assert app.state()!='withdrawn' and warnings
        elif case=='commands':
            app.watch_input.set('@Example');app._watch_add()
            tray.commands.put(('start',''));app._drain_tray();assert manager.running
            tray.commands.put(('pause',''));app._drain_tray();assert not manager.running
            tray.commands.put(('exit',''));app._drain_tray();assert app._destroyed and tray.stopped
        elif case=='updater':
            from livecatch_core.gui import LiveCatchApp
            launches=[];finished=[]
            LiveCatchApp._start_self_update=lambda self:launches.append(True)
            LiveCatchApp._finish_self_update=lambda self,payload,error:finished.append(error)
            app.watch_input.set('@Example');app._watch_add();app._watch_start()
            app._start_self_update()
            assert warnings and not launches
            manager.shutdown();manager.tick()
            app._start_self_update();assert launches==[True]
            app._update_installing=True
            app._watch_start();app._start()
            assert not manager.running and not app.supervisor.active
            manager.running=True
            app._finish_self_update(root,None)
            assert isinstance(finished[-1],RuntimeError)
            manager.running=False
        elif case=='startup':
            app.close_to_tray.set(True);app.start_hidden.set(True)
            original=desktop.sys.platform
            desktop.sys.platform='win32'
            try:
                app.login_startup.set(True);app._apply_background()
                assert startup.writes==[True]
                assert app.background_store.load()==BackgroundConfig(True,True)
                app.login_startup.set(False);app._apply_background();assert startup.writes==[True,False]
            finally:desktop.sys.platform=original
    finally:
        manager.shutdown(force=True)
        manager.tick()
        if not app._destroyed:app.destroy()
