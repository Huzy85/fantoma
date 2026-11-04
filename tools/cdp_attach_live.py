"""Live check: attach Fantoma to a Chrome that is already running.

Starts a full Chrome as a subprocess with remote debugging and one tab, reads
a page through Fantoma attached to it, stops Fantoma, then checks that Chrome
and its original tab are still there. Always terminates its own Chrome.

    CHROME_BIN=/path/to/chrome python tools/cdp_attach_live.py
"""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from fantoma import Fantoma  # noqa: E402

CHROME = os.environ.get("CHROME_BIN") or os.path.expanduser(
    "~/.cache/ms-playwright/chromium-1248/chrome-linux64/chrome")
URL = "https://example.com"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def tabs(port):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=5) as r:
        return [t for t in json.load(r) if t.get("type") == "page"]


def main() -> int:
    port = free_port()
    tmp = tempfile.mkdtemp(prefix="fantoma-cdp-", dir=os.environ.get("CDP_LIVE_TMP"))
    proc = subprocess.Popen(
        [CHROME, "--headless=new", f"--remote-debugging-port={port}",
         f"--user-data-dir={tmp}", "--no-first-run",
         "--disable-gpu", "about:blank"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        for _ in range(50):
            try:
                before = tabs(port)
                break
            except Exception:
                time.sleep(0.2)
        else:
            print("FAIL: Chrome did not open its debugging port")
            return 1
        print(f"Chrome up on port {port}, tabs before: {[t['url'] for t in before]}")

        f = Fantoma(browser="chromium", cdp_url=f"http://127.0.0.1:{port}")
        try:
            f.start()
            during = len(tabs(port))
            result = f.read(URL)
        finally:
            f.stop()
        text = (result.get("title") or "") + "\n" + (result.get("markdown") or "")
        print("title:", result.get("title"), "| blocked:", result.get("blocked"), "| head:", text[:80].replace("\n", " "))
        print(f"read ok: {'Example Domain' in text}, tabs while attached: {during}")

        time.sleep(0.5)
        after = tabs(port)
        alive = proc.poll() is None
        print(f"chrome alive: {alive}, tabs after: {[t['url'] for t in after]}")
        ok = ("Example Domain" in text and alive and during == len(before) + 1
              and [t["id"] for t in after] == [t["id"] for t in before])
        print("PASS" if ok else "FAIL")
        return 0 if ok else 1
    finally:
        proc.terminate()
        try:
            proc.wait(10)
        except subprocess.TimeoutExpired:
            proc.kill()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
