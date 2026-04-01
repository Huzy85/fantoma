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
