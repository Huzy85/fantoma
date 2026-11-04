"""Kill leftover browser processes, but only our own.

Fantoma used to run ``pkill -f playwright/driver/node`` (and the same for
``camoufox-bin`` and the glxtest Xvfb) to clear a wedged driver. pkill matches
every process on the machine, so one Fantoma instance would kill the browsers
of any other Playwright program running under the same user, including a
second Fantoma.

By default these helpers only touch processes descended from the current
process. Inside a container, where every browser belongs to Fantoma and
orphans of a crashed worker get re-parented to PID 1, set
``FANTOMA_REAP_ALL=1`` to get the old machine-wide behaviour back. The Docker
image sets it.
"""
from __future__ import annotations

import logging
import os
import re
import signal
import subprocess

_log = logging.getLogger("fantoma.browser")

DRIVER = r"playwright/driver/node"
CAMOUFOX = r"camoufox-bin"
GLXTEST_XVFB = r"Xvfb :[0-9]+ -screen 0 1x1x24"


def reap_all_mode() -> bool:
    return os.environ.get("FANTOMA_REAP_ALL", "").strip().lower() in ("1", "true", "yes", "on")


def _children(pid: int) -> list[int]:
    try:
        out = subprocess.run(["pgrep", "-P", str(pid)], capture_output=True,
                             text=True, timeout=3).stdout
    except Exception:
        return []
    return [int(x) for x in out.split() if x.isdigit()]


def descendants(pid: int | None = None) -> list[int]:
    """All descendants of ``pid`` (default: this process), breadth first."""
    root = os.getpid() if pid is None else pid
    seen: list[int] = []
    queue = [root]
    while queue and len(seen) < 2000:
        for child in _children(queue.pop(0)):
            if child not in seen:
                seen.append(child)
                queue.append(child)
    return seen


def _cmdline(pid: int) -> str:
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as fh:
            return fh.read().replace(b"\0", b" ").decode("utf-8", "replace")
    except OSError:
        pass
    try:
        return subprocess.run(["ps", "-o", "command=", "-p", str(pid)],
                              capture_output=True, text=True, timeout=3).stdout
    except Exception:
        return ""


def find_own(pattern: str) -> list[int]:
    rx = re.compile(pattern)
    return [p for p in descendants() if rx.search(_cmdline(p))]


def any_alive(pattern: str) -> bool:
    """Is a process matching ``pattern`` running that we are allowed to reap?"""
    if reap_all_mode():
        try:
            return subprocess.run(["pgrep", "-f", pattern], capture_output=True,
                                  timeout=5).returncode == 0
        except Exception:
            return False
    return bool(find_own(pattern))


def kill(pattern: str, sig: int = signal.SIGTERM) -> int:
    """Kill processes matching ``pattern``. Own descendants only, unless
    FANTOMA_REAP_ALL=1. Returns how many were signalled (-1 in reap-all mode,
    where pkill does not say)."""
    if reap_all_mode():
        flag = "-9" if sig == signal.SIGKILL else "-15"
        try:
            subprocess.run(["pkill", flag, "-f", pattern], capture_output=True, timeout=5)
        except Exception:
            pass
        return -1
    n = 0
    for pid in find_own(pattern):
        try:
            os.kill(pid, sig)
            n += 1
        except OSError:
            pass
    if n:
        _log.info("Reaped %d own process(es) matching %r", n, pattern)
    return n
