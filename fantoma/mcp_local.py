"""In-process browser for the MCP server: no Docker, no HTTP backend.

`fantoma-mcp` uses this when FANTOMA_MCP_BACKENDS is not set, so the whole
setup for an MCP client is one command. Setting FANTOMA_MCP_BACKENDS to one
or more URLs switches to the Docker/HTTP backends instead (for concurrency,
or when the browser must run on another machine).

Playwright's sync API is bound to the thread that created it and refuses to
run inside a running asyncio loop, and the MCP server is an asyncio app. So
one dedicated worker thread owns every browser, and tool calls are handed to
it through a queue. That also serialises calls, which is what a single
browser needs.

Environment:
    FANTOMA_LLM_URL      OpenAI-compatible endpoint (needed by run/extract only)
    FANTOMA_LLM_API_KEY  key for that endpoint, if any
    FANTOMA_LLM_MODEL    model name (default "auto")
    FANTOMA_BROWSER      camoufox (default) or chromium
    FANTOMA_HEADLESS     true (default), false, or virtual
    FANTOMA_PROXY        proxy URL for all traffic
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import os
import queue
import threading

log = logging.getLogger("fantoma.mcp_local")

NO_LLM = ("No LLM configured. Set FANTOMA_LLM_URL (and FANTOMA_LLM_API_KEY / "
          "FANTOMA_LLM_MODEL if needed) in the MCP server's environment. "
          "fantoma_read and fantoma_login work without one.")


def _headless():
    value = os.environ.get("FANTOMA_HEADLESS", "true").strip().lower()
    if value == "virtual":
        return "virtual"
    return value not in ("0", "false", "no")


def llm_settings() -> dict:
    return {
        "llm_url": os.environ.get("FANTOMA_LLM_URL", "").strip() or None,
        "api_key": os.environ.get("FANTOMA_LLM_API_KEY", ""),
        "model": os.environ.get("FANTOMA_LLM_MODEL", "auto"),
    }


def browser_settings() -> dict:
    return {
        "headless": _headless(),
        "browser": os.environ.get("FANTOMA_BROWSER", "camoufox").strip().lower() or "camoufox",
        "proxy": os.environ.get("FANTOMA_PROXY") or None,
    }


class LocalBrowser:
    """One worker thread owning one reusable Fantoma session.

    read, extract and login share the session, so a login is still in place
    for the next read. run uses its own browser (the Agent owns one), and the
    shared session is closed first because Playwright cannot hold two
    browsers on one thread.
    """

    def __init__(self):
        self._jobs: queue.Queue = queue.Queue()
        self._fantoma = None
        self._thread = threading.Thread(target=self._loop, name="fantoma-browser",
                                        daemon=True)
        self._thread.start()

    # ── worker thread ────────────────────────────────────────

    def _loop(self):
        asyncio.set_event_loop(asyncio.new_event_loop())
        while True:
            fn, future = self._jobs.get()
            if fn is None:
                break
            if not future.set_running_or_notify_cancel():
                continue
            try:
                future.set_result(fn())
            except BaseException as e:  # noqa: BLE001 — hand every failure back
                future.set_exception(e)

    def call(self, fn, timeout: float):
        """Run fn() on the browser thread and wait for its result."""
        future: concurrent.futures.Future = concurrent.futures.Future()
        self._jobs.put((fn, future))
        try:
            return future.result(timeout=timeout)
        except concurrent.futures.TimeoutError:
            # If it never started, make sure it never does: the caller has
            # already been told it failed.
            future.cancel()
            raise

    def shutdown(self):
        try:
            self.call(self._close_session, timeout=30)
        except Exception:
            pass
        self._jobs.put((None, None))

    # ── helpers that run ON the worker thread ────────────────

    def session(self):
        """The shared Fantoma session, started on first use."""
        if self._fantoma is None:
            from fantoma.browser_tool import Fantoma
            f = Fantoma(**llm_settings(), **browser_settings())
            f.start()
            self._fantoma = f
        return self._fantoma

    def _close_session(self):
        if self._fantoma is not None:
            try:
                self._fantoma.stop()
            except Exception:
                pass
            self._fantoma = None
            # A stopped sync Playwright can leave its loop registered on the
            # thread, and the next start then fails with "Sync API inside the
            # asyncio loop". A fresh loop keeps the next start clean.
            asyncio.set_event_loop(asyncio.new_event_loop())

    def reset_after_error(self):
        """Drop a session that raised, so the next call starts clean."""
        self._close_session()

    @property
    def session_active(self) -> bool:
        return self._fantoma is not None


_local: LocalBrowser | None = None
_local_lock = threading.Lock()


def local_browser() -> LocalBrowser:
    global _local
    with _local_lock:
        if _local is None or not _local._thread.is_alive():
            _local = LocalBrowser()
        return _local


def replace_after_hang() -> None:
    """Abandon a worker stuck inside the browser and start a fresh one.

    The stuck thread cannot be stopped from outside (Playwright objects are
    thread-bound), so it is left to die with the process; new calls get a new
    worker and a new browser rather than queueing behind the hang forever.
    """
    global _local
    with _local_lock:
        _local = None


# ── Tool implementations (called from mcp_server) ───────────

def run_task(task: str, url: str, max_steps: int, timeout: int) -> dict:
    llm = llm_settings()
    if not llm["llm_url"]:
        return {"success": False, "error": NO_LLM}
    lb = local_browser()

    def job():
        lb._close_session()
        from fantoma.agent import Agent
        agent = Agent(**llm, max_steps=max_steps, timeout=timeout, **browser_settings())
        try:
            result = agent.run(task, start_url=url or None, deadline_s=timeout)
        finally:
            asyncio.set_event_loop(asyncio.new_event_loop())
        return {
            "success": result.success, "data": result.data if isinstance(result.data, str)
            else ("" if result.data is None else str(result.data)),
            "steps_taken": result.steps_taken, "error": result.error or "",
            "escalations": result.escalations, "final_url": result.final_url or "",
            "final_title": result.final_title or "",
        }

    return _guarded(lb, job, timeout + 60)


def login(url: str, **fields) -> dict:
    lb = local_browser()

    def job():
        return lb.session().login(url=url, **fields)

    return _guarded(lb, job, 240)


def extract(url: str, query: str, schema: dict | None) -> dict:
    if not llm_settings()["llm_url"]:
        return {"success": False, "error": NO_LLM}
    lb = local_browser()

    def job():
        f = lb.session()
        nav = f.navigate(url)
        if not nav.get("success"):
            return {"success": False, "error": nav.get("error") or f"Could not open {url}"}
        return {"success": True, "data": f.extract(query, schema=schema or None)}

    return _guarded(lb, job, 240)


def read(url: str, main_only: bool, include_links: bool, selector: str,
         max_chars: int) -> dict:
    lb = local_browser()

    def job():
        f = lb.session()
        return {"success": True, **f.read(url, main_only=main_only,
                                          include_links=include_links,
                                          selector=selector, max_chars=max_chars)}

    return _guarded(lb, job, 180)


def health() -> dict:
    lb = local_browser()
    llm = llm_settings()
    return {
        "mode": "local",
        "browser": browser_settings()["browser"],
        "session_active": lb.session_active,
        "llm_configured": bool(llm["llm_url"]),
        "healthy": 1,
        "max_concurrent_tasks": 1,
    }


def _guarded(lb: LocalBrowser, job, timeout: float) -> dict:
    def wrapped():
        try:
            return job()
        except Exception:
            lb.reset_after_error()
            raise

    try:
        return lb.call(wrapped, timeout=timeout)
    except concurrent.futures.TimeoutError:
        replace_after_hang()
        return {"success": False, "error": f"Browser did not finish within {int(timeout)}s; "
                                           "it has been replaced, retry the call."}
    except Exception as e:
        return {"success": False, "error": str(e)}
