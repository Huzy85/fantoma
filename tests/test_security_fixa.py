"""Security audit fixes: URL schemes, private hosts, server hardening."""
import importlib.util
import os
from unittest.mock import MagicMock, patch

import pytest

from fantoma.browser.domains import DomainPolicy, _is_private_host

ROOT = os.path.dirname(os.path.dirname(__file__))


@pytest.mark.parametrize("url", [
    "file:///etc/passwd", "chrome://settings", "view-source:file:///etc/passwd",
    "view-source:https://example.com", "resource://gre/x", "about:config",
    "javascript:alert(1)", "ftp2://x",
])
def test_non_network_schemes_refused_without_policy(url):
    assert DomainPolicy().permits(url) is False


@pytest.mark.parametrize("url", ["about:blank", "data:text/html,hi", "https://example.com/", "http://127.0.0.1:8080/"])
def test_plain_policy_still_permits_inert_and_local_dev(url):
    assert DomainPolicy().permits(url) is True


@pytest.mark.parametrize("host", [
    "127.1", "0x7f000001", "0177.0.0.1", "2130706433", "127.0.0.1", "169.254.169.254",
    "metadata.google.internal", "foo.internal", "localhost", "[::1]", "::ffff:127.0.0.1",
    "10.0.0.5", "0.0.0.0",
])
def test_private_host_forms(host):
    assert _is_private_host(host) is True


@pytest.mark.parametrize("host", ["example.com", "8.8.8.8", "bad.cafe", "1.2.3.4.example.org"])
def test_public_hosts_not_private(host):
    assert _is_private_host(host) is False


def test_block_private_policy():
    p = DomainPolicy(block_private=True)
    assert p.active
    assert not p.permits("http://169.254.169.254/latest/meta-data")
    assert not p.permits("http://127.1/")
    assert p.permits("https://example.com/")


def test_block_private_from_env(monkeypatch):
    monkeypatch.setenv("FANTOMA_BLOCK_PRIVATE", "1")
    assert DomainPolicy.from_env().block_private
    monkeypatch.delenv("FANTOMA_BLOCK_PRIVATE")
    assert not DomainPolicy.from_env().block_private


# ── server ──────────────────────────────────────────────────

@pytest.fixture
def srv():
    path = os.path.join(ROOT, "server.py")
    spec = importlib.util.spec_from_file_location("fantoma_server_sec", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.API_KEY = ""
    mod.ALLOW_PRIVATE = False
    mod.app.config["TESTING"] = True
    mod._fantoma = MagicMock()
    return mod


@pytest.mark.parametrize("route,body", [
    ("/navigate", {"url": "file:///etc/passwd"}),
    ("/navigate", {"url": "http://169.254.169.254/"}),
    ("/start", {"url": "http://127.0.0.1:8080/"}),
    ("/read", {"url": "http://127.1/"}),
    ("/run", {"task": "x", "url": "chrome://settings"}),
    ("/login", {"url": "http://localhost/login", "password": "pw"}),
])
def test_server_refuses_private_and_local_urls(srv, route, body):
    srv._fantoma = None if route == "/start" else srv._fantoma
    r = srv.app.test_client().post(route, json=body)
    assert r.status_code == 400
    assert "not allowed" in r.json["error"]


def test_server_allow_private_flag(srv):
    srv.ALLOW_PRIVATE = True
    srv._fantoma.navigate.return_value = {"ok": 1}
    r = srv.app.test_client().post("/navigate", json={"url": "http://127.0.0.1:8080/"})
    assert r.status_code == 200
    # files are never allowed
    assert srv.app.test_client().post("/navigate", json={"url": "file:///etc/passwd"}).status_code == 400


def test_allowed_domains_validation(srv):
    ok, err = srv._clean_allowed_domains(["Example.com", "*.foo.org"])
    assert ok == ["example.com", "*.foo.org"] and err is None
    for bad in ("example.com", [1], ["a/b"], ["127.0.0.1"], ["x"] * 101):
        assert srv._clean_allowed_domains(bad)[1]


def test_run_passes_allowed_domains(srv):
    with patch.object(srv, "Agent") as A, patch.object(srv, "_release_session"):
        A.return_value.run.return_value = MagicMock(
            success=True, data="d", steps_taken=1, error=None, escalations=0, final_url="",
            final_title="", verified=None, verify_reason="", steps_detail=[])
        r = srv.app.test_client().post("/run", json={"task": "t", "allowed_domains": ["example.com"]})
    assert r.status_code == 200
    assert A.call_args.kwargs["allowed_domains"] == ["example.com"]


@pytest.mark.parametrize("field,val", [
    ("max_steps", 0), ("max_steps", 501), ("max_steps", "5"), ("max_steps", True), ("max_steps", 1.5),
    ("timeout", 0), ("timeout", 3601), ("timeout", "x"), ("timeout", None),
])
def test_run_validates_limits(srv, field, val):
    r = srv.app.test_client().post("/run", json={"task": "t", field: val})
    assert r.status_code == 400
    assert field in r.json["error"]


def test_run_500_is_generic_and_masked(srv, caplog):
    with patch.object(srv, "Agent", side_effect=RuntimeError("boom /srv/x hunter2secret")), \
            patch.object(srv, "_release_session"):
        r = srv.app.test_client().post(
            "/run", json={"task": "t", "sensitive_data": {"password": "hunter2secret"}})
    assert r.status_code == 500
    assert "boom" not in r.json["error"] and "hunter2secret" not in r.get_data(as_text=True)
    assert r.json["log_id"] in r.json["error"]
    assert "hunter2secret" not in caplog.text and r.json["log_id"] in caplog.text


def test_startup_refusal(srv):
    assert srv._startup_problem("0.0.0.0", "", False)
    assert srv._startup_problem("192.0.2.5", "", False)
    assert srv._startup_problem("0.0.0.0", "k", False) is None
    assert srv._startup_problem("127.0.0.1", "", False) is None
    assert srv._startup_problem("localhost", "", False) is None
    assert srv._startup_problem("::1", "", False) is None
    assert srv._startup_problem("0.0.0.0", "", True) is None


def _login_client(srv, live_url):
    f = MagicMock()
    f._engine.get_page.return_value.url = live_url
    srv._fantoma = f
    return f


def test_login_guard_blocks_password_on_other_host(srv):
    guard = srv._login_guard("https://shop.example.com/login", {"password": "s3cret!", "email": "a@b.co"})
    _login_client(srv, "https://shop.example.com/auth")
    assert guard("s3cret!") == "s3cret!"
    assert guard("Alex") == "Alex"
    _login_client(srv, "https://evil.example.net/")
    with pytest.raises(srv._SecretOffSite):
        guard("s3cret!")
    assert guard("Alex") == "Alex"
    _login_client(srv, "")
    with pytest.raises(srv._SecretOffSite):
        guard("a@b.co")


def test_login_route_passes_guard_and_masks_error(srv):
    f = _login_client(srv, "https://shop.example.com/")
    f.login.side_effect = RuntimeError("failed typing s3cret!")
    r = srv.app.test_client().post("/login", json={"url": "https://shop.example.com/login", "password": "s3cret!"})
    assert r.status_code == 500 and "s3cret!" not in r.get_data(as_text=True)
    assert callable(f.login.call_args.kwargs["value_guard"])


def test_supervisord_and_compose_defaults():
    conf = open(os.path.join(ROOT, "supervisord.conf")).read()
    assert "FANTOMA_VNC_ALLOW_NO_PASSWORD" in conf and "exit 0" in conf
    comp = open(os.path.join(ROOT, "docker-compose.fantoma.yml")).read()
    assert '"127.0.0.1:7860:7860"' in comp and '"127.0.0.1:6080:6080"' in comp
    assert "2>/dev/null || true" not in open(os.path.join(ROOT, "Dockerfile")).read()
