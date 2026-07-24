"""Tests for the MCP server translation layer.

The MCP server owns no browser logic, so these tests only assert the two
things it IS responsible for: mapping tool calls onto the right HTTP calls,
and never letting two callers share a single-session backend.
"""

import threading
import time

import pytest

from fantoma import mcp_server
from fantoma.mcp_server import BackendPool


@pytest.fixture(autouse=True)
def _reset_pool():
    """Each test installs its own pool."""
    mcp_server._pool = None
    yield
    mcp_server._pool = None


def _install_pool(urls):
    mcp_server._pool = BackendPool(urls)
    return mcp_server._pool


class TestBackendPool:
    def test_rejects_empty_backend_list(self):
        with pytest.raises(ValueError):
            BackendPool([])

    def test_acquire_returns_a_backend_and_gives_it_back(self):
        pool = BackendPool(["http://a"])
        with pool.acquire() as url:
            assert url == "http://a"
        # Returned to the pool, so a second acquire succeeds immediately.
        with pool.acquire(timeout=0.1) as url:
            assert url == "http://a"

    def test_second_caller_blocks_while_backend_is_busy(self):
        """The core guarantee: single-session backends are never shared."""
        pool = BackendPool(["http://a"])
        with pool.acquire():
            with pytest.raises(RuntimeError, match="busy"):
                with pool.acquire(timeout=0.1):
                    pass

    def test_backend_released_even_when_caller_raises(self):
        pool = BackendPool(["http://a"])
        with pytest.raises(ValueError):
            with pool.acquire():
                raise ValueError("task blew up")
        # Must not leak the backend.
        with pool.acquire(timeout=0.1) as url:
            assert url == "http://a"

    def test_two_backends_serve_two_callers_concurrently(self):
        pool = BackendPool(["http://a", "http://b"])
        with pool.acquire() as first:
            with pool.acquire(timeout=0.1) as second:
                assert first != second

    def test_concurrent_callers_never_get_the_same_backend(self):
        pool = BackendPool(["http://a", "http://b"])
        seen = []
        lock = threading.Lock()

        def worker():
            with pool.acquire(timeout=5) as url:
                with lock:
                    seen.append(url)
                time.sleep(0.05)
                with lock:
                    seen.remove(url)
                    # No duplicate may be held at the same moment.
                    assert url not in seen

        threads = [threading.Thread(target=worker) for _ in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()


class TestToolTranslation:
    def test_run_posts_task_and_maps_result(self, monkeypatch):
        _install_pool(["http://a"])
        calls = []

        def fake_post(backend, path, payload, timeout, retry_transport=False):
            calls.append((backend, path, payload))
            return {"success": True, "data": "42", "steps_taken": 3,
                    "error": None, "escalations": 1}

        monkeypatch.setattr(mcp_server, "_post", fake_post)
        result = mcp_server.fantoma_run("what is the answer", url="http://x")

        assert (calls[0][0], calls[0][1]) == ("http://a", "/run")
        assert calls[0][2]["task"] == "what is the answer"
        assert calls[0][2]["url"] == "http://x"
        assert result.success and result.data == "42"
        assert result.steps_taken == 3 and result.escalations == 1

    def test_run_omits_url_when_not_given(self, monkeypatch):
        _install_pool(["http://a"])
        captured = {}

        def fake_post(backend, path, payload, timeout, retry_transport=False):
            captured.update(payload)
            return {"success": True, "data": ""}

        monkeypatch.setattr(mcp_server, "_post", fake_post)
        mcp_server.fantoma_run("do a thing")
        assert "url" not in captured

    def test_null_error_becomes_empty_string(self, monkeypatch):
        """The backend sends error: null on success; the model shouldn't see 'None'."""
        _install_pool(["http://a"])
        monkeypatch.setattr(
            mcp_server, "_post",
            lambda *a, **k: {"success": True, "data": "ok", "error": None},
        )
        assert mcp_server.fantoma_run("t").error == ""

    def test_login_maps_fields(self, monkeypatch):
        _install_pool(["http://a"])
        monkeypatch.setattr(
            mcp_server, "_post",
            lambda *a, **k: {"success": True, "url": "http://x/secure",
                             "fields_filled": ["Username", "Password"], "steps": 1},
        )
        result = mcp_server.fantoma_login("http://x/login", username="u", password="p")
        assert result.success and result.steps == 1
        assert result.fields_filled == ["Username", "Password"]

    def test_extract_starts_then_stops_the_session(self, monkeypatch):
        _install_pool(["http://a"])
        paths = []

        def fake_post(backend, path, payload, timeout, retry_transport=False):
            paths.append(path)
            return {"data": "extracted"}

        monkeypatch.setattr(mcp_server, "_post", fake_post)
        result = mcp_server.fantoma_extract("http://x", "get the title")
        assert paths == ["/start", "/extract", "/stop"]
        assert result.success and result.data == "extracted"

    def test_extract_stops_session_even_when_extract_fails(self, monkeypatch):
        """A leaked session poisons the backend for the next caller."""
        _install_pool(["http://a"])
        paths = []

        def fake_post(backend, path, payload, timeout, retry_transport=False):
            paths.append(path)
            if path == "/extract":
                raise RuntimeError("extraction exploded")
            return {}

        monkeypatch.setattr(mcp_server, "_post", fake_post)
        with pytest.raises(RuntimeError):
            mcp_server.fantoma_extract("http://x", "q")
        assert "/stop" in paths

    def test_extract_releases_backend_after_failure(self, monkeypatch):
        pool = _install_pool(["http://a"])

        def fake_post(backend, path, payload, timeout, retry_transport=False):
            if path == "/extract":
                raise RuntimeError("boom")
            return {}

        monkeypatch.setattr(mcp_server, "_post", fake_post)
        with pytest.raises(RuntimeError):
            mcp_server.fantoma_extract("http://x", "q")
        with pool.acquire(timeout=0.1) as url:
            assert url == "http://a"

    def test_extract_restarts_the_session_if_the_worker_died_mid_sequence(
        self, monkeypatch
    ):
        """/start and /extract are one session; a restart between them loses it."""
        _install_pool(["http://a"])
        monkeypatch.setattr(mcp_server.time, "sleep", lambda s: None)
        paths = []

        def fake_post(backend, path, payload, timeout, retry_transport=False):
            paths.append(path)
            if path == "/extract":
                # First attempt: the worker restarted, session gone.
                if paths.count("/extract") == 1:
                    return {"error": "No active session. POST /start first."}
                return {"data": "recovered"}
            return {}

        monkeypatch.setattr(mcp_server, "_post", fake_post)
        result = mcp_server.fantoma_extract("http://x", "q")
        assert paths.count("/start") == 2, "must re-establish the session"
        assert result.success and result.data == "recovered"

    def test_extract_gives_up_after_one_session_retry(self, monkeypatch):
        _install_pool(["http://a"])
        monkeypatch.setattr(mcp_server.time, "sleep", lambda s: None)
        starts = []

        def fake_post(backend, path, payload, timeout, retry_transport=False):
            if path == "/start":
                starts.append(1)
            if path == "/extract":
                return {"error": "No active session. POST /start first."}
            return {}

        monkeypatch.setattr(mcp_server, "_post", fake_post)
        result = mcp_server.fantoma_extract("http://x", "q")
        assert len(starts) == 2, "one retry, not an infinite loop"
        assert not result.success

    def test_health_counts_only_reachable_backends(self, monkeypatch):
        _install_pool(["http://a", "http://b"])

        def fake_get(backend, path, timeout):
            if backend == "http://b":
                raise RuntimeError("connection refused")
            return {"status": "ok", "engine": "camoufox"}

        monkeypatch.setattr(mcp_server, "_get", fake_get)
        health = mcp_server.fantoma_health()
        assert health["healthy"] == 1
        assert health["max_concurrent_tasks"] == 1
        assert any(b["status"] == "unreachable" for b in health["backends"])


class TestWorkerRestartRetry:
    """A backend whose driver died returns 503 + retryable and restarts.

    That is routine self-healing, so the MCP layer should absorb it rather
    than hand the model an error it cannot act on.
    """

    def test_retries_through_a_restart_and_succeeds(self, monkeypatch):
        monkeypatch.setattr(mcp_server.time, "sleep", lambda s: None)
        calls = []

        def fake_post_once(backend, path, payload, timeout):
            calls.append(path)
            if len(calls) == 1:
                return 503, {"retryable": True, "error": "driver died"}
            return 200, {"success": True, "data": "recovered"}

        monkeypatch.setattr(mcp_server, "_post_once", fake_post_once)
        body = mcp_server._post("http://a", "/run", {}, timeout=10)
        assert body == {"success": True, "data": "recovered"}
        assert len(calls) == 2

    def test_tolerates_connection_refused_while_worker_is_down(self, monkeypatch):
        """Between the crash and supervisord's restart the port is closed."""
        monkeypatch.setattr(mcp_server.time, "sleep", lambda s: None)
        state = {"n": 0}

        def fake_post_once(backend, path, payload, timeout):
            state["n"] += 1
            if state["n"] == 1:
                return 503, {"retryable": True}
            if state["n"] == 2:
                raise mcp_server.httpx.ConnectError("connection refused")
            return 200, {"success": True, "data": "back"}

        monkeypatch.setattr(mcp_server, "_post_once", fake_post_once)
        body = mcp_server._post("http://a", "/run", {}, timeout=10)
        assert body["data"] == "back"

    def test_gives_up_and_returns_last_body_if_never_recovers(self, monkeypatch):
        monkeypatch.setattr(mcp_server.time, "sleep", lambda s: None)
        monkeypatch.setattr(
            mcp_server, "_post_once",
            lambda *a, **k: (503, {"retryable": True, "error": "still dead"}),
        )
        body = mcp_server._post("http://a", "/run", {}, timeout=10)
        assert body["error"] == "still dead"

    def test_does_not_retry_a_plain_failure(self, monkeypatch):
        """A normal task failure is not a restart — retrying wastes a minute."""
        calls = []

        def fake_post_once(backend, path, payload, timeout):
            calls.append(1)
            return 200, {"success": False, "error": "no such element"}

        monkeypatch.setattr(mcp_server, "_post_once", fake_post_once)
        body = mcp_server._post("http://a", "/run", {}, timeout=10)
        assert len(calls) == 1
        assert body["error"] == "no such element"


class TestConfig:
    def test_backends_parsed_from_env(self, monkeypatch):
        monkeypatch.setenv(
            "FANTOMA_MCP_BACKENDS", "http://a:7860, http://b:7861/ ,"
        )
        pool = mcp_server._load_pool()
        assert pool._urls == ["http://a:7860", "http://b:7861"]
        assert pool.size == 2

    def test_api_key_header_omitted_when_unset(self, monkeypatch):
        monkeypatch.delenv("FANTOMA_API_KEY", raising=False)
        assert mcp_server._api_key_headers() == {}

    def test_api_key_header_sent_when_set(self, monkeypatch):
        monkeypatch.setenv("FANTOMA_API_KEY", "secret")
        assert mcp_server._api_key_headers() == {"X-API-Key": "secret"}
