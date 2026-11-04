"""Reaping must never touch browser processes that are not ours."""
import signal
import subprocess
import sys
import time

import pytest

from fantoma.browser import reap

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="uses /proc and pgrep")

MARK = "fantoma-reap-test-marker"


def _sleeper():
    # A process whose command line contains the marker.
    return subprocess.Popen([sys.executable, "-c", f"import time; time.sleep(60)  # {MARK}"])


def test_kills_own_child(monkeypatch):
    monkeypatch.delenv("FANTOMA_REAP_ALL", raising=False)
    p = _sleeper()
    try:
        time.sleep(0.2)
        assert reap.any_alive(MARK)
        assert reap.kill(MARK, signal.SIGKILL) == 1
        assert p.wait(timeout=5) is not None
    finally:
        p.kill()


def test_leaves_foreign_process_alone(monkeypatch):
    monkeypatch.delenv("FANTOMA_REAP_ALL", raising=False)
    # setsid + double fork: the sleeper is re-parented away from us, like a
    # browser started by some other program on the same machine.
    out = subprocess.run(
        ["setsid", "sh", "-c",
         f"{sys.executable} -c 'import time; time.sleep(30)  # {MARK}' >/dev/null 2>&1 & echo $!"],
        capture_output=True, text=True, timeout=5).stdout.strip()
    pid = int(out)
    try:
        time.sleep(0.3)
        assert pid not in reap.descendants()
        assert not reap.any_alive(MARK)
        assert reap.kill(MARK, signal.SIGKILL) == 0
        assert subprocess.run(["kill", "-0", str(pid)]).returncode == 0
    finally:
        subprocess.run(["kill", "-9", str(pid)])


def test_reap_all_mode_env(monkeypatch):
    monkeypatch.setenv("FANTOMA_REAP_ALL", "1")
    assert reap.reap_all_mode()
    monkeypatch.setenv("FANTOMA_REAP_ALL", "0")
    assert not reap.reap_all_mode()


def test_no_machine_wide_pkill_left():
    import pathlib
    root = pathlib.Path(__file__).resolve().parent.parent
    for f in [root / "server.py", *(root / "fantoma").rglob("*.py")]:
        if f.name == "reap.py":
            continue
        assert '"pkill"' not in f.read_text(), f"machine-wide pkill in {f}"
