"""Real Tk initialization/language-switch/settings smoke test; no downloads."""
from pathlib import Path
import sys
import tempfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from livecatch_core.config import ConfigStore
from livecatch_core.gui import LiveCatchApp
with tempfile.TemporaryDirectory() as directory:
    app=LiveCatchApp(ConfigStore(Path(directory)/'config.json'), update_checker=lambda _version: None)
    try:
        app.update()
        assert app.settings().gpu_export=='off'
        assert app.notebook.tab(app.record_tab,'text')=='録画'
        assert app.notebook.tab(app.settings_tab,'text')=='設定'
        assert str(app.start_button.cget('text'))=='開始'
        app._reset_progress(); app._progress['active']=True
        app._handle_progress_event({'event':'phase','name':'extracting'})
        assert '100%' in str(app.phase_steps['starting'].cget('text'))
        assert '…' in str(app.phase_steps['extracting'].cget('text'))
        app._handle_progress_event({'event':'phase','name':'downloading'})
        app._handle_progress_event({'event':'progress','stream':'video','percent':42.5,
                                    'fragment_index':42,'fragment_count':100})
        assert '42%' in str(app.phase_steps['downloading'].cget('text'))
        app._handle_progress_event({'event':'streams','count':1})
        app._handle_progress_event({'event':'catchup','stream':'video','percent':60.0,
                                    'current':60,'total':100,'gap_fragments':40,'caught_up':False})
        assert '60%' in app.phase_var.get() and '追いつき' in app.phase_var.get()
        app._handle_progress_event({'event':'catchup','stream':'video','percent':100.0,
                                    'current':100,'total':100,'gap_fragments':0,'caught_up':True})
        assert 'LIVE' in app.phase_var.get()
        app.vars['language'].set('en');app._rebuild();app.update()
        app.notebook.select(app.settings_tab);app.update()
        assert app.settings().language=='en'
        assert app.winfo_width()>=840 and not app.start_button.winfo_ismapped()
        app.notebook.select(app.record_tab);app.update()
        assert app.start_button.winfo_ismapped()
        assert app.progressbar.winfo_ismapped()
        assert app.metrics_var.get()
        print('Tk GUI smoke passed')
    finally:app.destroy()

# Run the new watch UI tests under the same real Xvfb display.
import subprocess
subprocess.run([sys.executable, "-m", "pytest", "-q", "tests/test_twitch_watch_gui.py"],
               cwd=Path(__file__).resolve().parents[1], check=True, timeout=60)
