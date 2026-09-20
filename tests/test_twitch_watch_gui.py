"""Run every real-Tk case in its own process, matching one app per process.

Repeated destruction/reinitialization of Tcl interpreters in one Windows test
process can fail in tcl_findLibrary. Isolation also prevents pending Tk callbacks
from one case leaking into the next. These are not mocked or skipped on Windows.
"""
import os
from pathlib import Path
import subprocess
import sys

import pytest

pytestmark = pytest.mark.skipif(
    os.name != 'nt' and not os.environ.get('DISPLAY'),
    reason='Tk display unavailable; run under Xvfb',
)

CASES = (
    'test_watch_tab_registration_persistence_and_language',
    'test_gui_start_detection_stop_and_retry',
    'test_manual_start_reserves_channel_through_modal_dialog',
    'test_manual_done_does_not_close_while_auto_recording_finishes',
)


@pytest.mark.parametrize('case', CASES, ids=CASES)
def test_isolated_twitch_watch_gui(case):
    root = Path(__file__).resolve().parents[1]
    target = str(Path(__file__).with_name('twitch_watch_gui_cases.py')) + '::' + case
    result = subprocess.run(
        [sys.executable, '-m', 'pytest', '-q', target], cwd=root,
        capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
