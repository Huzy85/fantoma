# tests/test_action_cache.py
"""Unit tests for the action-trace cache (Phase 3)."""

import pytest

from fantoma.action_cache import ActionCache, normalize_task


@pytest.fixture
def cache(tmp_path):
    c = ActionCache(db_path=str(tmp_path / "ac.db"))
    yield c
    c.close()


def test_normalize_task():
    assert normalize_task("  Find  the  TOP story ") == "find the top story"
    assert normalize_task(None) == ""


def test_record_and_lookup(cache):
    steps = [
        {"action": "click", "role": "link", "name": "Login", "value": None},
        {"action": "type_text", "role": "textbox", "name": "Email", "value": "x@y.z"},
    ]
    assert cache.record("example.com", "log in", steps)
    got = cache.lookup("example.com", "log in")
    assert got is not None and len(got) == 2
    assert got[0]["action"] == "click" and got[0]["name"] == "Login"
    assert got[1]["value"] == "x@y.z"


def test_lookup_normalizes_task(cache):
    cache.record("example.com", "Find  the Top Story", [{"action": "click", "role": "link", "name": "x"}])
    assert cache.lookup("example.com", "find the top story") is not None


def test_lookup_miss_returns_none(cache):
    assert cache.lookup("nope.com", "whatever") is None


def test_record_drops_non_replayable_steps(cache):
    steps = [{"action": "done"}, {"action": "click", "role": "button", "name": "Go"}]
    cache.record("d.com", "t", steps)
    got = cache.lookup("d.com", "t")
    assert len(got) == 1 and got[0]["action"] == "click"


def test_record_all_non_replayable_is_noop(cache):
    assert cache.record("d.com", "t", [{"action": "done"}]) is False
    assert cache.lookup("d.com", "t") is None


def test_record_empty_is_noop(cache):
    assert cache.record("d.com", "t", []) is False


def test_record_overwrites_existing(cache):
    cache.record("d.com", "t", [{"action": "click", "role": "button", "name": "A"}])
    cache.record("d.com", "t", [{"action": "click", "role": "button", "name": "B"}])
    got = cache.lookup("d.com", "t")
    assert len(got) == 1 and got[0]["name"] == "B"


def test_invalidate_removes_plan(cache):
    cache.record("d.com", "t", [{"action": "click", "role": "button", "name": "A"}])
    cache.invalidate("d.com", "t")
    assert cache.lookup("d.com", "t") is None


def test_mark_used_does_not_error(cache):
    cache.record("d.com", "t", [{"action": "click", "role": "button", "name": "A"}])
    cache.mark_used("d.com", "t")
    assert cache.lookup("d.com", "t") is not None


def test_empty_domain_returns_none(cache):
    cache.record("d.com", "t", [{"action": "click", "role": "button", "name": "A"}])
    assert cache.lookup("", "t") is None


def test_disabled_cache_is_noop(tmp_path):
    c = ActionCache(db_path=str(tmp_path / "x.db"), enabled=False)
    assert c.record("d.com", "t", [{"action": "click", "role": "b", "name": "n"}]) is False
    assert c.lookup("d.com", "t") is None
    c.close()


def test_persists_across_instances(tmp_path):
    p = str(tmp_path / "persist.db")
    c1 = ActionCache(db_path=p)
    c1.record("d.com", "t", [{"action": "click", "role": "b", "name": "n"}])
    c1.close()
    c2 = ActionCache(db_path=p)
    assert c2.lookup("d.com", "t") is not None
    c2.close()
