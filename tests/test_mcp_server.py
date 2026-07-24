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

        def fake_post(backend, path, payload, timeout):
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

        def fake_post(backend, path, payload, timeout):
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

        def fake_post(backend, path, payload, timeout):
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

        def fake_post(backend, path, payload, timeout):
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

        def fake_post(backend, path, payload, timeout):
            if path == "/extract":
                raise RuntimeError("boom")
            return {}

        monkeypatch.setattr(mcp_server, "_post", fake_post)
        with pytest.raises(RuntimeError):
            mcp_server.fantoma_extract("http://x", "q")
        with pool.acquire(timeout=0.1) as url:
            assert url == "http://a"

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
