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

        def fake_post(backend, path, payload, timeout, retry_transport=False, wait_for_restart=True):
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

        def fake_post(backend, path, payload, timeout, retry_transport=False, wait_for_restart=True):
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

        def fake_post(backend, path, payload, timeout, retry_transport=False, wait_for_restart=True):
            paths.append(path)
            if path == "/start":
                return {"url": "http://x", "title": "X"}
            return {"data": "extracted"}

        monkeypatch.setattr(mcp_server, "_post", fake_post)
        result = mcp_server.fantoma_extract("http://x", "get the title")
        assert paths == ["/stop", "/start", "/extract", "/stop"]
        assert result.success and result.data == "extracted"

    def test_extract_stops_session_even_when_extract_fails(self, monkeypatch):
        """A leaked session poisons the backend for the next caller."""
        _install_pool(["http://a"])
        paths = []

        def fake_post(backend, path, payload, timeout, retry_transport=False, wait_for_restart=True):
            paths.append(path)
            if path == "/extract":
                raise RuntimeError("extraction exploded")
            if path == "/start":
                return {"url": "http://x"}
            return {}

        monkeypatch.setattr(mcp_server, "_post", fake_post)
        with pytest.raises(RuntimeError):
            mcp_server.fantoma_extract("http://x", "q")
        assert "/stop" in paths

    def test_extract_releases_backend_after_failure(self, monkeypatch):
        pool = _install_pool(["http://a"])

        def fake_post(backend, path, payload, timeout, retry_transport=False, wait_for_restart=True):
            if path == "/extract":
                raise RuntimeError("boom")
            if path == "/start":
                return {"url": "http://x"}
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

        def fake_post(backend, path, payload, timeout, retry_transport=False, wait_for_restart=True):
            paths.append(path)
            if path == "/start":
                return {"url": "http://x"}
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

        def fake_post(backend, path, payload, timeout, retry_transport=False, wait_for_restart=True):
            if path == "/start":
                starts.append(1)
                return {"url": "http://x"}
            if path == "/extract":
                return {"error": "No active session. POST /start first."}
            return {}

        monkeypatch.setattr(mcp_server, "_post", fake_post)
        result = mcp_server.fantoma_extract("http://x", "q")
        assert len(starts) == 2, "one retry, not an infinite loop"
        assert not result.success

    def test_extract_clears_a_stale_session_before_starting(self, monkeypatch):
        """/start answers 409 when a session exists; extracting then reads the
        OLD page and reports success with the wrong content."""
        _install_pool(["http://a"])
        paths = []

        def fake_post(backend, path, payload, timeout, retry_transport=False, wait_for_restart=True):
            paths.append(path)
            if path == "/start":
                return {"url": "http://x", "title": "X"}
            if path == "/extract":
                return {"data": "correct content"}
            return {}

        monkeypatch.setattr(mcp_server, "_post", fake_post)
        mcp_server.fantoma_extract("http://x", "q")
        assert paths[0] == "/stop", "must clear any stale session first"
        assert paths[:3] == ["/stop", "/start", "/extract"]

    def test_extract_fails_loudly_when_start_returns_no_page_state(
        self, monkeypatch
    ):
        """The bug this guards: a failed /start followed by a successful
        /extract returns the previous page's content as if it were correct."""
        _install_pool(["http://a"])
        monkeypatch.setattr(mcp_server.time, "sleep", lambda s: None)

        def fake_post(backend, path, payload, timeout, retry_transport=False, wait_for_restart=True):
            if path == "/start":
                return {"error": "session active"}   # 409 body, no url
            if path == "/extract":
                return {"data": "STALE content from a previous page"}
            return {}

        monkeypatch.setattr(mcp_server, "_post", fake_post)
        result = mcp_server.fantoma_extract("http://wanted", "q")
        assert not result.success
        assert "STALE" not in (result.data or "")
        assert "wanted" in result.error

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


class TestPoolFailover:
    """A dead worker takes ~75 s to be reclaimed by its watchdog. With other
    backends free, moving the work is far better than waiting."""

    def test_moves_to_another_backend_when_one_is_restarting(self, monkeypatch):
        _install_pool(["http://a", "http://b"])
        tried = []

        def fake_post(backend, path, payload, timeout, retry_transport=False,
                      wait_for_restart=True):
            tried.append(backend)
            if backend == "http://a":
                raise mcp_server.BackendRestarting("a is restarting")
            return {"success": True, "data": "served by b"}

        monkeypatch.setattr(mcp_server, "_post", fake_post)
        result = mcp_server.fantoma_run("do a thing")
        assert tried == ["http://a", "http://b"]
        assert result.data == "served by b"

    def test_single_backend_waits_instead_of_failing_over(self, monkeypatch):
        """With nowhere to go, the call must wait the restart out."""
        _install_pool(["http://a"])
        seen_flags = []

        def fake_post(backend, path, payload, timeout, retry_transport=False,
                      wait_for_restart=True):
            seen_flags.append(wait_for_restart)
            return {"success": True, "data": "ok"}

        monkeypatch.setattr(mcp_server, "_post", fake_post)
        mcp_server.fantoma_run("t")
        assert seen_flags == [True], "one backend means wait, never fail fast"

    def test_last_attempt_waits_rather_than_giving_up(self, monkeypatch):
        _install_pool(["http://a", "http://b"])
        flags = []

        def fake_post(backend, path, payload, timeout, retry_transport=False,
                      wait_for_restart=True):
            flags.append(wait_for_restart)
            raise mcp_server.BackendRestarting("still down")

        monkeypatch.setattr(mcp_server, "_post", fake_post)
        with pytest.raises(mcp_server.BackendRestarting):
            mcp_server.fantoma_run("t")
        # First tries fail fast to move on; the final one waits it out.
        assert flags == [False, True]

    def test_failed_backend_is_returned_to_the_pool(self, monkeypatch):
        """It must not be leaked — it will have recovered by its next turn."""
        pool = _install_pool(["http://a", "http://b"])

        def fake_post(backend, path, payload, timeout, retry_transport=False,
                      wait_for_restart=True):
            if backend == "http://a":
                raise mcp_server.BackendRestarting("down")
            return {"success": True, "data": "ok"}

        monkeypatch.setattr(mcp_server, "_post", fake_post)
        mcp_server.fantoma_run("t")
        assert pool._free.qsize() == 2, "both backends back in the pool"

    def test_failover_caps_attempts_on_a_large_pool(self, monkeypatch):
        """Trying every backend in a big pool would take longer than waiting."""
        _install_pool([f"http://{n}" for n in "abcdef"])
        tried = []

        def fake_post(backend, path, payload, timeout, retry_transport=False,
                      wait_for_restart=True):
            tried.append(backend)
            raise mcp_server.BackendRestarting("down")

        monkeypatch.setattr(mcp_server, "_post", fake_post)
        with pytest.raises(mcp_server.BackendRestarting):
            mcp_server.fantoma_run("t")
        assert len(tried) == 3

    def test_login_also_fails_over(self, monkeypatch):
        _install_pool(["http://a", "http://b"])

        def fake_post(backend, path, payload, timeout, retry_transport=False,
                      wait_for_restart=True):
            if backend == "http://a":
                raise mcp_server.BackendRestarting("down")
            return {"success": True, "url": "http://x/secure", "steps": 1}

        monkeypatch.setattr(mcp_server, "_post", fake_post)
        assert mcp_server.fantoma_login("http://x/login", username="u").success

    def test_extract_also_fails_over(self, monkeypatch):
        _install_pool(["http://a", "http://b"])

        def fake_post(backend, path, payload, timeout, retry_transport=False,
                      wait_for_restart=True):
            if backend == "http://a":
                raise mcp_server.BackendRestarting("down")
            if path == "/start":
                return {"url": "http://x"}
            return {"data": "from b"}

        monkeypatch.setattr(mcp_server, "_post", fake_post)
        result = mcp_server.fantoma_extract("http://x", "q")
        assert result.success and result.data == "from b"


class TestPostRestartSignal:
    def test_raises_instead_of_waiting_when_told_not_to_wait(self, monkeypatch):
        monkeypatch.setattr(
            mcp_server, "_post_once",
            lambda *a, **k: (503, {"retryable": True}),
        )
        with pytest.raises(mcp_server.BackendRestarting):
            mcp_server._post("http://a", "/run", {}, timeout=5,
                             wait_for_restart=False)

    def test_still_waits_when_asked_to(self, monkeypatch):
        monkeypatch.setattr(mcp_server.time, "sleep", lambda s: None)
        calls = []

        def fake_post_once(backend, path, payload, timeout):
            calls.append(1)
            if len(calls) == 1:
                return 503, {"retryable": True}
            return 200, {"data": "recovered"}

        monkeypatch.setattr(mcp_server, "_post_once", fake_post_once)
        body = mcp_server._post("http://a", "/run", {}, timeout=5,
                                wait_for_restart=True)
        assert body["data"] == "recovered"


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
