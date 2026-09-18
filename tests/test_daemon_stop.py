"""Stopping the daemon twice returns instead of waiting on a thread that is gone."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap

import pytest

pytest.importorskip("PySide6")

SCRIPT = textwrap.dedent(
    """
    import sys
    from PySide6.QtCore import QCoreApplication
    from refrain.config import Config
    from refrain.daemon import Daemon, DaemonWorker

    DaemonWorker.start_polling = lambda self: None
    app = QCoreApplication(sys.argv)
    daemon = Daemon(Config())
    daemon.start()
    while not daemon.thread.isRunning():
        app.processEvents()
    daemon.stop()
    daemon.stop()
    print("stopped")
    """
)


def test_a_second_stop_returns(tmp_path):
    env = dict(os.environ, QT_QPA_PLATFORM="offscreen")
    env["PYTHONPATH"] = os.pathsep.join(p for p in sys.path if p)
    result = subprocess.run(
        [sys.executable, "-c", SCRIPT],
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert "stopped" in result.stdout
