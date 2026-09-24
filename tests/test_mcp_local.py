"""The MCP server's built-in browser mode (no Docker backend)."""

from unittest.mock import MagicMock, patch

import pytest

from fantoma import mcp_local, mcp_server


@pytest.fixture(autouse=True)
def _clean(monkeypatch):
    monkeypatch.delenv("FANTOMA_MCP_BACKENDS", raising=False)
    monkeypatch.delenv("FANTOMA_LLM_URL", raising=False)
    mcp_server._pool = None
    mcp_server._mode = None
    mcp_local._local = None
    monkeypatch.setattr(mcp_server, "_backend_answers", lambda url: False)
    yield
    if mcp_local._local is not None:
        mcp_local._local._jobs.put((None, None))
    mcp_local._local = None
    mcp_server._pool = None


class TestModeSelection:
    def test_local_when_no_backends_are_set(self):
        assert mcp_server._use_local()

    def test_local_when_explicitly_asked(self, monkeypatch):
        monkeypatch.setenv("FANTOMA_MCP_BACKENDS", "local")
        assert mcp_server._use_local()

    def test_remote_when_backends_are_set(self, monkeypatch):
        monkeypatch.setenv("FANTOMA_MCP_BACKENDS", "http://127.0.0.1:7860")
        assert not mcp_server._use_local()

    def test_an_existing_backend_on_the_default_port_is_kept(self, monkeypatch):
        monkeypatch.setattr(mcp_server, "_backend_answers", lambda url: True)
        assert not mcp_server._use_local()

    def test_an_installed_pool_wins(self):
        mcp_server._pool = mcp_server.BackendPool(["http://a"])
        assert not mcp_server._use_local()


def _fake_session(**methods):
    f = MagicMock()
    for name, value in methods.items():
        getattr(f, name).return_value = value
    return f


class TestLocalTools:
    def test_read_runs_on_the_browser_thread_and_maps_fields(self):
        import threading
        seen = {}

        def fake_read(url, **kw):
            seen["thread"] = threading.current_thread().name
            return {"title": "T", "url": url, "markdown": "# T", "links": [],
                    "blocked": "", "injection_warnings": [], "truncated": False,
                    "hidden_removed": 0, "description": ""}

        lb = mcp_local.local_browser()
        lb._fantoma = MagicMock(read=fake_read)
        out = mcp_server.fantoma_read("https://a.example/")
        assert out.success and out.markdown == "# T" and out.url == "https://a.example/"
        assert seen["thread"] == "fantoma-browser"

    def test_session_is_shared_between_login_and_read(self):
        lb = mcp_local.local_browser()
        session = _fake_session(login={"success": True, "url": "u", "fields_filled": ["Email"],
                                       "steps": 1},
                                read={"title": "", "url": "u", "markdown": "logged in",
                                      "links": []})
        lb._fantoma = session
        mcp_server.fantoma_login("https://a.example/login", email="e", password="p")
        out = mcp_server.fantoma_read("https://a.example/account")
        assert out.markdown == "logged in"
        session.login.assert_called_once()
        session.read.assert_called_once()

    def test_extract_without_llm_explains_what_to_set(self):
        out = mcp_server.fantoma_extract("https://a.example/", "price")
        assert not out.success and "FANTOMA_LLM_URL" in out.error

    def test_run_without_llm_explains_what_to_set(self):
        out = mcp_server.fantoma_run("do a thing")
        assert not out.success and "FANTOMA_LLM_URL" in out.error

    def test_extract_with_llm(self, monkeypatch):
        monkeypatch.setenv("FANTOMA_LLM_URL", "http://127.0.0.1:1/v1")
        lb = mcp_local.local_browser()
        lb._fantoma = _fake_session(navigate={"success": True},
                                    extract={"price": "$10"})
        out = mcp_server.fantoma_extract("https://a.example/", "price",
                                         schema={"type": "object", "properties": {}})
        assert out.success and out.data == '{"price": "$10"}'

    def test_a_failing_call_drops_the_session(self):
        lb = mcp_local.local_browser()
        broken = MagicMock()
        broken.read.side_effect = RuntimeError("driver died")
        lb._fantoma = broken
        out = mcp_server.fantoma_read("https://a.example/")
        assert not out.success and "driver died" in out.error
        assert lb._fantoma is None

    def test_a_hung_call_gets_a_fresh_worker(self):
        lb = mcp_local.local_browser()
        with patch.object(mcp_local.LocalBrowser, "call",
                          side_effect=mcp_local.concurrent.futures.TimeoutError):
            out = mcp_local.read("https://a.example/", True, True, "", 0)
        assert not out["success"] and "retry" in out["error"]
        assert mcp_local.local_browser() is not lb

    def test_health_reports_local_mode(self):
        h = mcp_server.fantoma_health()
        assert h["mode"] == "local" and h["llm_configured"] is False
