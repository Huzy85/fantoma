"""Tests for session persistence — save/load/delete/list encrypted browser state."""
import json
import os
import pytest
from fantoma.session import SessionManager


@pytest.fixture
def session_dir(tmp_path):
    return str(tmp_path / "sessions")


@pytest.fixture
def mgr(session_dir):
    return SessionManager(base_dir=session_dir)


def _fake_state():
    return {
        "cookies": [{"name": "sid", "value": "abc123", "domain": ".github.com", "path": "/"}],
        "origins": [{"origin": "https://github.com", "localStorage": [{"name": "token", "value": "xyz"}]}],
    }


class TestSaveAndLoad:
    def test_save_then_load_returns_state(self, mgr):
        mgr.save("github.com", "user@test.com", _fake_state(), "https://github.com/login")
        loaded = mgr.load("github.com", "user@test.com")
        assert loaded is not None
        assert loaded["storage_state"]["cookies"][0]["value"] == "abc123"
        assert loaded["login_url"] == "https://github.com/login"

    def test_load_nonexistent_returns_none(self, mgr):
        assert mgr.load("github.com", "nobody@test.com") is None

    def test_save_overwrites_existing(self, mgr):
        mgr.save("github.com", "user@test.com", _fake_state(), "https://github.com/login")
        new_state = {"cookies": [{"name": "sid", "value": "NEW", "domain": ".github.com", "path": "/"}], "origins": []}
        mgr.save("github.com", "user@test.com", new_state, "https://github.com/login")
        loaded = mgr.load("github.com", "user@test.com")
        assert loaded["storage_state"]["cookies"][0]["value"] == "NEW"

    def test_separate_accounts_separate_sessions(self, mgr):
        mgr.save("github.com", "a@test.com", _fake_state(), "https://github.com/login")
        mgr.save("github.com", "b@test.com", {"cookies": [], "origins": []}, "https://github.com/login")
        a = mgr.load("github.com", "a@test.com")
        b = mgr.load("github.com", "b@test.com")
        assert len(a["storage_state"]["cookies"]) == 1
        assert len(b["storage_state"]["cookies"]) == 0


class TestDelete:
    def test_delete_removes_session(self, mgr):
        mgr.save("github.com", "user@test.com", _fake_state(), "https://github.com/login")
        mgr.delete("github.com", "user@test.com")
        assert mgr.load("github.com", "user@test.com") is None

    def test_delete_nonexistent_no_error(self, mgr):
        mgr.delete("github.com", "nobody@test.com")


class TestList:
    def test_list_all(self, mgr):
        mgr.save("github.com", "a@test.com", _fake_state(), "https://github.com/login")
        mgr.save("render.com", "a@test.com", _fake_state(), "https://render.com/login")
        sessions = mgr.list()
        assert len(sessions) == 2

    def test_list_filtered_by_domain(self, mgr):
        mgr.save("github.com", "a@test.com", _fake_state(), "https://github.com/login")
        mgr.save("render.com", "a@test.com", _fake_state(), "https://render.com/login")
        sessions = mgr.list(domain="github.com")
        assert len(sessions) == 1
        assert sessions[0]["domain"] == "github.com"


class TestEncryption:
    def test_file_is_not_plaintext_json(self, mgr, session_dir):
        mgr.save("github.com", "user@test.com", _fake_state(), "https://github.com/login")
        files = [f for f in os.listdir(session_dir) if f.endswith(".enc")]
        assert len(files) == 1
        raw = open(os.path.join(session_dir, files[0]), "rb").read()
        with pytest.raises(Exception):
            json.loads(raw)

    def test_corrupted_file_returns_none(self, mgr, session_dir):
        mgr.save("github.com", "user@test.com", _fake_state(), "https://github.com/login")
        files = [f for f in os.listdir(session_dir) if f.endswith(".enc")]
        with open(os.path.join(session_dir, files[0]), "wb") as f:
            f.write(b"corrupted data")
        assert mgr.load("github.com", "user@test.com") is None

    def test_key_file_created_with_restricted_perms(self, mgr, session_dir):
        mgr.save("github.com", "user@test.com", _fake_state(), "https://github.com/login")
        key_path = os.path.join(session_dir, ".key")
        assert os.path.exists(key_path)
        stat = os.stat(key_path)
        assert oct(stat.st_mode)[-3:] == "600"


class TestAtomicWrite:
    def test_no_tmp_files_left_after_save(self, mgr, session_dir):
        mgr.save("github.com", "user@test.com", _fake_state(), "https://github.com/login")
        files = os.listdir(session_dir)
        assert not any(f.endswith(".tmp") for f in files)


class TestPlaintextFallback:
    def test_plaintext_when_no_cryptography(self, session_dir, monkeypatch):
        mgr = SessionManager(base_dir=session_dir)
        monkeypatch.setattr("fantoma.session._has_cryptography", False)
        mgr.save("github.com", "user@test.com", _fake_state(), "https://github.com/login")
        loaded = mgr.load("github.com", "user@test.com")
        assert loaded is not None
        assert loaded["storage_state"]["cookies"][0]["value"] == "abc123"
        files = [f for f in os.listdir(session_dir) if not f.startswith(".")]
        raw = open(os.path.join(session_dir, files[0]), "r").read()
        data = json.loads(raw)
        assert "storage_state" in data
