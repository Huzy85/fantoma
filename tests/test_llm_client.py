# tests/test_llm_client.py
"""Unit tests for the LLM client — retry, 400 handling, and the
'return empty string, never raise' contract that the navigator relies on."""

from unittest.mock import MagicMock, patch

from fantoma.llm.client import LLMClient


def _resp(status=200, body=None, text=""):
    """Build a fake httpx response. body=None makes .json() raise (malformed)."""
    r = MagicMock()
    r.status_code = status
    r.text = text
    if body is None:
        r.json.side_effect = ValueError("No JSON could be decoded")
    else:
        r.json.return_value = body
    return r


def _ok_body(content="hello"):
    return {"choices": [{"message": {"content": content}}]}


MSGS = [{"role": "user", "content": "hi"}]


class TestChatContract:
    def test_normal_response(self):
        c = LLMClient("http://x/v1", model="m")
        with patch("fantoma.llm.client.httpx.post", return_value=_resp(200, body=_ok_body("answer"))):
            assert c.chat(MSGS) == "answer"

    def test_malformed_json_returns_empty(self):
        """A 200 with a non-JSON body must return '' (feeds navigator
        recovery), not raise and kill the whole run."""
        c = LLMClient("http://x/v1", model="m")
        with patch("fantoma.llm.client.httpx.post", return_value=_resp(200, body=None)):
            assert c.chat(MSGS) == ""

    def test_missing_choices_returns_empty(self):
        c = LLMClient("http://x/v1", model="m")
        with patch("fantoma.llm.client.httpx.post", return_value=_resp(200, body={"foo": 1})):
            assert c.chat(MSGS) == ""

    def test_reasoning_content_fallback(self):
        body = {"choices": [{"message": {
            "content": "",
            "reasoning_content": "1. Click the search box\n2. Type the query",
        }}]}
        c = LLMClient("http://x/v1", model="m")
        with patch("fantoma.llm.client.httpx.post", return_value=_resp(200, body=body)):
            out = c.chat(MSGS)
        assert "Click the search box" in out


class TestStatus400:
    def test_temperature_400_retries_and_locks(self):
        """A 400 whose body mentions temperature retries at temp=1 and pins
        the override for later calls."""
        c = LLMClient("http://x/v1", model="m")
        bad = _resp(400, body={}, text="temperature must be 1 for this model")
        good = _resp(200, body=_ok_body("ok"))
        with patch("fantoma.llm.client.httpx.post", side_effect=[bad, good]) as p:
            assert c.chat(MSGS, temperature=0.3) == "ok"
            assert p.call_count == 2
        assert c._temperature_override == 1

    def test_non_temperature_400_does_not_lock_temperature(self):
        """A coincidental 400 (e.g. stale model) must NOT permanently pin
        temperature=1 and degrade deterministic navigation."""
        c = LLMClient("http://x/v1", model="m")
        bad = _resp(400, body={}, text="model not found")
        good = _resp(200, body=_ok_body("ok"))
        with patch("fantoma.llm.client.httpx.post", side_effect=[bad, good]):
            assert c.chat(MSGS, temperature=0.3) == "ok"
        assert c._temperature_override is None

    def test_400_resets_model_cache_before_retry(self):
        c = LLMClient("http://x/v1", model="auto")
        c._resolved_model = "stale-model"
        bad = _resp(400, body={}, text="model not found")
        good = _resp(200, body=_ok_body("ok"))
        # _resolve_model re-fetches /models on the retry
        with patch("fantoma.llm.client.httpx.get",
                   return_value=_resp(200, body={"data": [{"id": "fresh-model"}]})):
            with patch("fantoma.llm.client.httpx.post", side_effect=[bad, good]):
                assert c.chat(MSGS) == "ok"
        assert c._resolved_model == "fresh-model"

    def test_double_400_returns_empty(self):
        c = LLMClient("http://x/v1", model="m")
        bad1 = _resp(400, body={}, text="temperature")
        bad2 = _resp(400, body={}, text="temperature")
        with patch("fantoma.llm.client.httpx.post", side_effect=[bad1, bad2]):
            assert c.chat(MSGS) == ""
        assert c._temperature_override is None


class TestStripFences:
    def test_strips_json_fence(self):
        assert LLMClient._strip_code_fences('```json\n{"a":1}\n```') == '{"a":1}'

    def test_no_fence_passthrough(self):
        assert LLMClient._strip_code_fences("plain text") == "plain text"
