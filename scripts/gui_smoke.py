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
        app.vars['language'].set('en');app._rebuild();app.update()
        app.notebook.select(app.settings_tab);app.update()
        assert app.settings().language=='en'
        assert app.winfo_width()>=840 and app.start_button.winfo_ismapped()
        print('Tk GUI smoke passed')
    finally:app.destroy()

# Run the new watch UI tests under the same real Xvfb display.
import subprocess
subprocess.run([sys.executable, "-m", "pytest", "-q", "tests/test_twitch_watch_gui.py"],
               cwd=Path(__file__).resolve().parents[1], check=True, timeout=60)
