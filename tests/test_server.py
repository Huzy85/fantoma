# tests/test_server.py
import pytest
import json
from unittest.mock import patch, MagicMock


@pytest.fixture
def client():
    """Create a test client with mocked Fantoma."""
    with patch("server.Fantoma") as MockFantoma:
        mock_f = MagicMock()
        MockFantoma.return_value = mock_f
        mock_f.start.return_value = {"url": "https://example.com", "title": "Example",
                                     "aria_tree": "[1] link 'Home'", "errors": [], "tab_count": 1}
        mock_f.get_state.return_value = {"url": "https://example.com", "title": "Example",
                                          "aria_tree": "[1] link 'Home'", "errors": [], "tab_count": 1}
        mock_f.click.return_value = {"success": True, "changed": True, "url_changed": False,
                                      "errors": [], "state": {"url": "https://example.com",
                                      "title": "Example", "aria_tree": "[1] button 'Submit'",
                                      "errors": [], "tab_count": 1}}
        mock_f.stop.return_value = None

        import server
        server._fantoma = None  # Reset state
        app = server.app
        app.config["TESTING"] = True
        with app.test_client() as c:
            yield c, mock_f


def test_health(client):
    c, _ = client
    r = c.get("/health")
    assert r.status_code == 200
    assert r.json["status"] == "ok"


def test_start_creates_session(client):
    c, mock_f = client
    r = c.post("/start", json={"url": "https://example.com"})
    assert r.status_code == 200
    assert "url" in r.json
    mock_f.start.assert_called_once_with("https://example.com")


def test_start_while_active_returns_error(client):
    c, mock_f = client
    c.post("/start", json={"url": "https://example.com"})
    r = c.post("/start", json={"url": "https://other.com"})
    assert r.status_code == 409
    assert "error" in r.json


def test_stop_clears_session(client):
    c, mock_f = client
    c.post("/start", json={"url": "https://example.com"})
    r = c.post("/stop")
    assert r.status_code == 200
    mock_f.stop.assert_called_once()


def test_state_returns_current(client):
    c, mock_f = client
    c.post("/start", json={"url": "https://example.com"})
    r = c.get("/state")
    assert r.status_code == 200
    assert "aria_tree" in r.json


def test_click_returns_result(client):
    c, mock_f = client
    c.post("/start", json={"url": "https://example.com"})
    r = c.post("/click", json={"element_id": 1})
    assert r.status_code == 200
    assert r.json["success"] is True
    mock_f.click.assert_called_once_with(1)


def test_action_without_session_returns_error(client):
    c, _ = client
    r = c.post("/click", json={"element_id": 1})
    assert r.status_code == 400
    assert "error" in r.json


class TestApiKeyGate:
    """Phase 1 — shared-secret auth gate."""

    def test_open_when_no_key(self, monkeypatch):
        import server
        monkeypatch.setattr(server, "API_KEY", "")
        server._fantoma = None
        with server.app.test_client() as c:
            # No key configured → /state reachable (returns 400 no-session, not 401)
            assert c.get("/state").status_code == 400

    def test_blocks_without_header_when_key_set(self, monkeypatch):
        import server
        monkeypatch.setattr(server, "API_KEY", "s3cret")
        server._fantoma = None
        with server.app.test_client() as c:
            assert c.get("/state").status_code == 401

    def test_allows_with_x_api_key(self, monkeypatch):
        import server
        monkeypatch.setattr(server, "API_KEY", "s3cret")
        server._fantoma = None
        with server.app.test_client() as c:
            r = c.get("/state", headers={"X-API-Key": "s3cret"})
            assert r.status_code != 401  # auth passed (400 no-session)

    def test_allows_with_bearer(self, monkeypatch):
        import server
        monkeypatch.setattr(server, "API_KEY", "s3cret")
        server._fantoma = None
        with server.app.test_client() as c:
            r = c.get("/state", headers={"Authorization": "Bearer s3cret"})
            assert r.status_code != 401

    def test_wrong_key_blocked(self, monkeypatch):
        import server
        monkeypatch.setattr(server, "API_KEY", "s3cret")
        server._fantoma = None
        with server.app.test_client() as c:
            assert c.get("/state", headers={"X-API-Key": "nope"}).status_code == 401

    def test_health_open_even_with_key(self, monkeypatch):
        import server
        monkeypatch.setattr(server, "API_KEY", "s3cret")
        with server.app.test_client() as c:
            assert c.get("/health").status_code == 200


class TestEvaluateGate:
    """Phase 1 — /evaluate locked behind FANTOMA_ALLOW_EVAL."""

    def test_evaluate_403_by_default(self, monkeypatch):
        import server
        monkeypatch.setattr(server, "API_KEY", "")
        monkeypatch.setattr(server, "ALLOW_EVAL", False)
        server._fantoma = MagicMock()
        with server.app.test_client() as c:
            r = c.post("/evaluate", json={"script": "1+1"})
            assert r.status_code == 403
        server._fantoma = None

    def test_evaluate_runs_when_enabled(self, monkeypatch):
        import server
        monkeypatch.setattr(server, "API_KEY", "")
        monkeypatch.setattr(server, "ALLOW_EVAL", True)
        mf = MagicMock()
        mf.evaluate.return_value = 2
        server._fantoma = mf
        with server.app.test_client() as c:
            r = c.post("/evaluate", json={"script": "1+1"})
            assert r.status_code == 200
            assert r.json["result"] == 2
        server._fantoma = None
