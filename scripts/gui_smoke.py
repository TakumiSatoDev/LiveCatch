"""Real Tk initialization/language-switch/settings smoke test; no downloads."""
from pathlib import Path
import sys
import tempfile
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from livecatch_core.config import ConfigStore
from livecatch_core.gui import LiveCatchApp
with tempfile.TemporaryDirectory() as directory:
    app=LiveCatchApp(ConfigStore(Path(directory)/'config.json'))
    try:
        app.update()
        assert app.settings().gpu_export=='off'
        app.vars['language'].set('en');app._rebuild();app.update()
        app.notebook.select(2);app.update()
        assert app.settings().language=='en'
        assert app.winfo_width()>=840 and app.start_button.winfo_ismapped()
        print('Tk GUI smoke passed')
    finally:app.destroy()
