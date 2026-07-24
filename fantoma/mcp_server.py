"""MCP server exposing Fantoma to any MCP-speaking LLM client.

This is a translator, not a second brain. Every tool maps onto an endpoint
the HTTP server already implements; no session state or navigation logic
lives here. If you find yourself adding a state machine, it belongs in
server.py instead.

Backends are single-session and single-threaded (server.py runs Flask with
threaded=False), so a backend can serve exactly one task at a time. The pool
below hands out one backend per call and blocks when all are busy, which is
why running several containers is how you get concurrency:

    FANTOMA_MCP_BACKENDS=http://127.0.0.1:7860,http://127.0.0.1:7861

Run it:

    python -m fantoma.mcp_server                 # stdio, for local clients
    FANTOMA_MCP_TRANSPORT=http python -m fantoma.mcp_server
"""

from __future__ import annotations

import contextlib
import os
import queue
import time
import threading

import httpx
from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

DEFAULT_BACKENDS = "http://127.0.0.1:7860"

# Browser work is slow: a cold Camoufox start alone can take ~15 s, and a
# multi-step task runs to minutes. These are deliberately generous — a
# timeout here aborts the HTTP read but leaves the backend still working,
# which is the one failure mode that desynchronises the pool.
CONNECT_TIMEOUT = 10.0
TASK_TIMEOUT = 600.0

mcp = FastMCP("fantoma")


# ── Backend pool ──────────────────────────────────────────────────────────

class BackendPool:
    """Hands out one exclusive backend per call.

    A plain Queue is the whole implementation: taking a backend removes it,
    so a second caller blocks until the first returns it. That is exactly the
    serialisation the single-session backends need.
    """

    def __init__(self, urls: list[str]):
        if not urls:
            raise ValueError("FANTOMA_MCP_BACKENDS resolved to no backends")
        self._urls = urls
        self._free: queue.Queue[str] = queue.Queue()
        for url in urls:
            self._free.put(url)

    @property
    def size(self) -> int:
        return len(self._urls)

    @contextlib.contextmanager
    def acquire(self, timeout: float | None = None):
        try:
            url = self._free.get(timeout=timeout)
        except queue.Empty:
            raise RuntimeError(
                f"All {self.size} Fantoma backend(s) are busy. Run more "
                f"containers and list them in FANTOMA_MCP_BACKENDS to raise "
                f"concurrency."
            ) from None
        try:
            yield url
        finally:
            self._free.put(url)


def _load_pool() -> BackendPool:
    raw = os.environ.get("FANTOMA_MCP_BACKENDS", DEFAULT_BACKENDS)
    return BackendPool([u.strip().rstrip("/") for u in raw.split(",") if u.strip()])


_pool: BackendPool | None = None
_pool_lock = threading.Lock()


def _pool_instance() -> BackendPool:
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = _load_pool()
    return _pool


def _api_key_headers() -> dict[str, str]:
    key = os.environ.get("FANTOMA_API_KEY", "")
    return {"X-API-Key": key} if key else {}


def _post_once(backend: str, path: str, payload: dict, timeout: float) -> tuple[int, dict]:
    with httpx.Client(timeout=httpx.Timeout(timeout, connect=CONNECT_TIMEOUT)) as client:
        resp = client.post(
            f"{backend}{path}", json=payload, headers=_api_key_headers()
        )
        # The backend reports task failure in the JSON body with a 4xx/5xx
        # status, so parse before raising to keep the real error message.
        try:
            body = resp.json()
        except Exception:
            resp.raise_for_status()
            raise
        return resp.status_code, body


def _post(
    backend: str,
    path: str,
    payload: dict,
    timeout: float,
    retry_transport: bool = False,
) -> dict:
    """POST, absorbing one worker restart.

    A backend answers 503 with retryable=true when its browser driver has
    died and supervisord is replacing the process (startsecs=2). That is a
    routine, self-healing event, so waiting it out is better than handing
    the model an error it cannot act on.

    retry_transport also retries connection-level failures, which happen
    when the worker exits mid-request. Only pass it for calls that are safe
    to repeat: a retried /run or /login could submit a form twice.
    """
    def attempt():
        try:
            return _post_once(backend, path, payload, timeout)
        except httpx.HTTPError:
            if not retry_transport:
                raise
            return None, None

    status, body = attempt()
    if body is not None and not (status == 503 and body.get("retryable")):
        return body

    # A hung worker is only reclaimed when its request watchdog fires (up to
    # ~75 s for /start), so the ladder has to outlast that or the client gives
    # up on a backend that was about to come back.
    for delay in (5.0, 15.0, 30.0, 45.0):
        time.sleep(delay)
        try:
            status, body = attempt()
        except httpx.HTTPError:
            continue  # worker still coming back up
        if body is not None and not (status == 503 and body.get("retryable")):
            return body
    if body is None:
        raise RuntimeError(
            f"Fantoma backend {backend} did not come back after a restart"
        )
    return body


def _get(backend: str, path: str, timeout: float) -> dict:
    with httpx.Client(timeout=httpx.Timeout(timeout, connect=CONNECT_TIMEOUT)) as client:
        resp = client.get(f"{backend}{path}", headers=_api_key_headers())
        return resp.json()


# ── Result models ─────────────────────────────────────────────────────────

class TaskResult(BaseModel):
    success: bool
    data: str = Field("", description="The agent's answer, or extracted content")
    steps_taken: int = 0
    error: str = ""
    escalations: int = Field(
        0, description="How many times the agent fell back to a stronger model"
    )


class LoginResult(BaseModel):
    success: bool
    url: str = Field("", description="URL after the login attempt")
    fields_filled: list[str] = Field(default_factory=list)
    steps: int = 0
    error: str = ""
    verification_needed: str = Field(
        "", description="Set when the site demanded an email code or link"
    )


# ── Tools ─────────────────────────────────────────────────────────────────

@mcp.tool(
    title="Run a browser task",
    description=(
        "Give Fantoma a task in plain English and let it drive the browser to "
        "completion. Use for multi-step work: find something, compare pages, "
        "fill a form, read a value behind a few clicks. Returns the answer. "
        "Blocks until the task finishes, which is typically 20-120 seconds."
    ),
)
def fantoma_run(
    task: str,
    url: str = "",
    max_steps: int = 50,
    timeout: int = 300,
) -> TaskResult:
    payload: dict = {"task": task, "max_steps": max_steps, "timeout": timeout}
    if url:
        payload["url"] = url
    with _pool_instance().acquire() as backend:
        body = _post(backend, "/run", payload, timeout=timeout + 30)
    return TaskResult(
        success=bool(body.get("success")),
        data=body.get("data") or "",
        steps_taken=body.get("steps_taken") or 0,
        error=body.get("error") or "",
        escalations=body.get("escalations") or 0,
    )


@mcp.tool(
    title="Log into a site",
    description=(
        "Fill and submit a login form using code alone — no LLM call, no "
        "tokens, typically one step. Reads the accessibility tree, matches "
        "fields by label and submits. Handles multi-step flows where email "
        "and password are on separate pages. Prefer this over fantoma_run "
        "for logging in: it is faster and far more reliable. The session "
        "stays open afterwards so a following fantoma_extract sees the "
        "logged-in page."
    ),
)
def fantoma_login(
    url: str,
    email: str = "",
    username: str = "",
    password: str = "",
    first_name: str = "",
    last_name: str = "",
) -> LoginResult:
    payload = {
        "url": url, "email": email, "username": username, "password": password,
        "first_name": first_name, "last_name": last_name,
    }
    with _pool_instance().acquire() as backend:
        body = _post(backend, "/login", payload, timeout=TASK_TIMEOUT)
    return LoginResult(
        success=bool(body.get("success")),
        url=body.get("url") or "",
        fields_filled=body.get("fields_filled") or [],
        steps=body.get("steps") or 0,
        error=body.get("error") or "",
        verification_needed=str(body.get("verification_needed") or ""),
    )


@mcp.tool(
    title="Extract data from a page",
    description=(
        "Open a page and pull out specific information. Pass a JSON Schema as "
        "'schema' to get structured fields back, or leave it empty for prose. "
        "Use this instead of fantoma_run when the data is on one known page "
        "and no navigation is needed — it is cheaper and more predictable."
    ),
)
def fantoma_extract(
    url: str,
    query: str,
    schema: dict | None = None,
) -> TaskResult:
    payload: dict = {"query": query}
    if schema:
        payload["schema"] = schema

    with _pool_instance().acquire() as backend:
        # /start and /extract are two calls against one session. If the worker
        # restarts between them the session is gone and /extract answers "No
        # active session", so the pair has to be retried as a unit rather than
        # each call individually.
        body: dict = {}
        for attempt in range(2):
            # Clear any session left behind by an earlier call. Without this,
            # /start answers 409 "session active" and /extract silently reads
            # whatever page the browser was already on — returning confident,
            # wrong content for the URL that was asked for.
            try:
                _post(backend, "/stop", {}, timeout=60.0, retry_transport=True)
            except Exception:
                pass

            started = _post(backend, "/start", {"url": url},
                            timeout=TASK_TIMEOUT, retry_transport=True)
            # A successful /start returns page state. Anything without a url
            # means we do not know what is on screen, so extracting would be
            # guesswork — fail loudly instead.
            if not started.get("url"):
                if attempt == 0:
                    time.sleep(2.0)
                    continue
                return TaskResult(
                    success=False,
                    error=f"Could not open {url}: "
                          f"{started.get('error') or 'no page state returned'}",
                )
            try:
                body = _post(backend, "/extract", payload, timeout=TASK_TIMEOUT)
            finally:
                # Always release the session, or this backend is poisoned for
                # the next caller even though the pool believes it is free.
                try:
                    _post(backend, "/stop", {}, timeout=60.0, retry_transport=True)
                except Exception:
                    pass
            if "no active session" not in str(body.get("error", "")).lower():
                break
            if attempt == 0:
                time.sleep(2.0)

    if body.get("error"):
        return TaskResult(success=False, error=str(body["error"]))
    data = body.get("data", body)
    return TaskResult(
        success=True, data=data if isinstance(data, str) else str(data)
    )


@mcp.tool(
    title="Check Fantoma backend health",
    description=(
        "Report which browser backends are reachable and how many concurrent "
        "tasks can run. Call this first if other tools are failing."
    ),
)
def fantoma_health() -> dict:
    pool = _pool_instance()
    backends = []
    for url in pool._urls:
        try:
            body = _get(url, "/health", timeout=10.0)
            backends.append({"url": url, "status": body.get("status", "unknown"),
                             "engine": body.get("engine", "")})
        except Exception as e:
            backends.append({"url": url, "status": "unreachable", "error": str(e)})
    healthy = sum(1 for b in backends if b["status"] == "ok")
    return {
        "backends": backends,
        "healthy": healthy,
        "max_concurrent_tasks": healthy,
    }


def main() -> None:
    transport = os.environ.get("FANTOMA_MCP_TRANSPORT", "stdio").lower()
    if transport in ("http", "streamable-http"):
        mcp.run(
            transport="streamable-http",
            host=os.environ.get("FANTOMA_MCP_HOST", "127.0.0.1"),
            port=int(os.environ.get("FANTOMA_MCP_PORT", "8767")),
        )
    else:
        mcp.run()


if __name__ == "__main__":
    main()
