# tests/test_agent_cache.py
"""Agent-level action-cache integration (Phase 3): record, replay, self-heal."""

from unittest.mock import MagicMock

from fantoma.agent import Agent
from fantoma.action_cache import ActionCache
from fantoma.navigator import NavigatorResult


def _agent(tmp_path, enabled=True):
    a = Agent.__new__(Agent)
    a._max_steps = 25
    a._flat_budget = 20
    a._sensitive_data = {}
    a._planner = MagicMock()
    a._planner.summarise.return_value = "Answer"
    a._navigator = MagicMock()
    a.fantoma = MagicMock()
    a.fantoma.start.return_value = {"url": "https://example.com"}
    a._llm = MagicMock()
    a.escalation = MagicMock()
    a.escalation.total_escalations = 0
    a._action_cache = ActionCache(db_path=str(tmp_path / "ac.db"), enabled=enabled)
    return a


def test_phase1_success_records_plan(tmp_path):
    a = _agent(tmp_path)
    steps = [{"action": "click", "role": "link", "name": "Top", "value": None}]
    a._navigator.execute.return_value = NavigatorResult(
        "done", "Found", 3, [{"url": "https://example.com"}], "https://example.com",
        replay_steps=steps,
    )
    res = a.run("find top", start_url="https://example.com")
    assert res.success
    assert a._action_cache.lookup("example.com", "find top") == steps
    a._action_cache.close()


def test_cache_hit_replays_without_navigation_llm(tmp_path):
    a = _agent(tmp_path)
    a._action_cache.record("example.com", "find top",
                           [{"action": "click", "role": "link", "name": "Top", "value": None}])
    a.fantoma.navigate.return_value = {"success": True}
    a.fantoma.get_state.return_value = {}
    a.fantoma._dom.find_by_signature.return_value = 0
    a.fantoma.click.return_value = {"success": True}
    a._navigator._extract_answer.return_value = "Replayed answer"

    res = a.run("find top", start_url="https://example.com")
    assert res.success and res.data == "Replayed answer"
    a._navigator.execute.assert_not_called()  # the LLM navigation loop was skipped
    a.fantoma.click.assert_called_once_with(0)
    a._action_cache.close()


def test_stale_cache_falls_back_and_reheals(tmp_path):
    a = _agent(tmp_path)
    a._action_cache.record("example.com", "find top",
                           [{"action": "click", "role": "link", "name": "Gone", "value": None}])
    a.fantoma.navigate.return_value = {"success": True}
    a.fantoma.get_state.return_value = {}
    a.fantoma._dom.find_by_signature.return_value = None  # element no longer on the page
    # Fallback full run succeeds with a fresh plan
    a._navigator.execute.return_value = NavigatorResult(
        "done", "Fresh", 2, [{"url": "https://example.com"}], "https://example.com",
        replay_steps=[{"action": "click", "role": "link", "name": "New", "value": None}],
    )
    res = a.run("find top", start_url="https://example.com")
    assert res.success
    a._navigator.execute.assert_called()  # fell back to the full run
    # cache re-healed with the fresh plan
    assert a._action_cache.lookup("example.com", "find top")[0]["name"] == "New"
    a._action_cache.close()


def test_secret_placeholder_not_resolved_until_replay(tmp_path):
    """Cached type_text value keeps the <secret:> placeholder; the real value
    is substituted only at replay time from live sensitive_data."""
    a = _agent(tmp_path)
    a._sensitive_data = {"pw": "hunter2"}
    a._action_cache.record("example.com", "log in",
                           [{"action": "type_text", "role": "textbox", "name": "Password", "value": "<secret:pw>"}])
    # the stored value must be the placeholder, never the real secret
    stored = a._action_cache.lookup("example.com", "log in")[0]["value"]
    assert stored == "<secret:pw>" and "hunter2" not in stored
    # on replay it resolves
    a.fantoma.navigate.return_value = {"success": True}
    a.fantoma.get_state.return_value = {}
    a.fantoma._dom.find_by_signature.return_value = 1
    a.fantoma.type_text.return_value = {"success": True}
    a._navigator._extract_answer.return_value = "logged in"
    a.run("log in", start_url="https://example.com")
    a.fantoma.type_text.assert_called_once_with(1, "hunter2")
    a._action_cache.close()
