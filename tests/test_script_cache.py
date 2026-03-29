"""Tests for script caching — save and replay action sequences."""
import pytest


def _make_elements(names):
    """Create element list with (role, name) tuples."""
    return [{"role": "button", "name": n} for n in names]


@pytest.fixture
def cache(tmp_path):
    from fantoma.resilience.script_cache import ScriptCache
    db_path = str(tmp_path / "test_cache.db")
    return ScriptCache(db_path=db_path)


class TestScriptCacheSaveAndLookup:
    def test_save_and_lookup_exact_match(self, cache):
        elements = _make_elements(["Login", "Email", "Password"])
        actions = [
            {"action": 'TYPE [1] "user@test.com"', "expected_url_pattern": None, "expected_elements": ["textbox:Email"]},
            {"action": "CLICK [0]", "expected_url_pattern": "/dashboard", "expected_elements": []},
        ]
        cache.save("example.com", elements, actions)
        result = cache.lookup("example.com", elements)
        assert result is not None
        assert len(result) == 2
        assert result[0]["action"] == 'TYPE [1] "user@test.com"'

    def test_lookup_no_match(self, cache):
        result = cache.lookup("notfound.com", _make_elements(["A", "B"]))
        assert result is None

    def test_fuzzy_match_above_threshold(self, cache):
        elements_v1 = _make_elements(["Login", "Email", "Password", "Remember me"])
        actions = [{"action": "CLICK [0]", "expected_url_pattern": None, "expected_elements": []}]
        cache.save("example.com", elements_v1, actions)
        elements_v2 = _make_elements(["Login", "Email", "Password", "Remember me", "Ad Banner"])
        result = cache.lookup("example.com", elements_v2)
        assert result is not None

    def test_fuzzy_match_below_threshold(self, cache):
        elements_v1 = _make_elements(["Login", "Email", "Password"])
        actions = [{"action": "CLICK [0]", "expected_url_pattern": None, "expected_elements": []}]
        cache.save("example.com", elements_v1, actions)
        elements_v2 = _make_elements(["Register", "Phone", "Country", "Submit", "Terms"])
        result = cache.lookup("example.com", elements_v2)
        assert result is None

    def test_overwrites_same_domain_and_structure(self, cache):
        elements = _make_elements(["Login", "Email"])
        cache.save("example.com", elements, [{"action": "CLICK [0]", "expected_url_pattern": None, "expected_elements": []}])
        cache.save("example.com", elements, [{"action": 'TYPE [1] "new"', "expected_url_pattern": None, "expected_elements": []}])
        result = cache.lookup("example.com", elements)
        assert result[0]["action"] == 'TYPE [1] "new"'

    def test_credentials_stored_as_placeholders(self, cache):
        elements = _make_elements(["Login", "Email"])
        secrets = {"email": "user@test.com", "password": "secret123"}
        actions = [
            {"action": 'TYPE [1] "user@test.com"', "expected_url_pattern": None, "expected_elements": []},
            {"action": 'TYPE [2] "secret123"', "expected_url_pattern": None, "expected_elements": []},
        ]
        cache.save("example.com", elements, actions, sensitive_data=secrets)
        result = cache.lookup("example.com", elements)
        assert "<secret:email>" in result[0]["action"]
        assert "<secret:password>" in result[1]["action"]
        assert "user@test.com" not in result[0]["action"]
        assert "secret123" not in result[1]["action"]


class TestScriptCacheValidation:
    def test_rejects_long_sequences(self, cache):
        elements = _make_elements(["A"])
        actions = [{"action": f"CLICK [{i}]", "expected_url_pattern": None, "expected_elements": []} for i in range(25)]
        cache.save("example.com", elements, actions)
        result = cache.lookup("example.com", elements)
        assert result is None
