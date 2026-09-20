"""Real Tk cases run in isolated processes; native tray is replaced by a fake."""
from pathlib import Path
import os
import shutil
import subprocess
import sys

import pytest

@pytest.mark.parametrize('case',['mixed','liveadd','tray','failure','commands','startup','updater'])
def test_desktop(case):
    root=Path(__file__).resolve().parents[1]
    command=[sys.executable,str(root/'tests'/'desktop_cases.py'),case]
    if sys.platform.startswith('linux') and not os.environ.get('DISPLAY'):
        xvfb=shutil.which('xvfb-run')
        if not xvfb:pytest.skip('No X display or Xvfb available')
        command=[xvfb,'-a',*command]
    result=subprocess.run(command,text=True,capture_output=True,timeout=30,cwd=root)
    assert result.returncode==0, result.stdout+'\n'+result.stderr
