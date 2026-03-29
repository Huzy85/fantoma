"""Tests for SEARCH_PAGE and FIND actions — free JS-based search tools."""
from fantoma.action_parser import PATTERNS
from fantoma.browser.actions import search_page, find_elements


def test_search_page_pattern_exists():
    assert "search_page" in PATTERNS


def test_find_pattern_exists():
    assert "find" in PATTERNS


def test_search_page_pattern_matches():
    m = PATTERNS["search_page"].match('SEARCH_PAGE "login button"')
    assert m is not None
    assert m.group(1) == "login button"


def test_find_pattern_matches():
    m = PATTERNS["find"].match('FIND "input[type=email]"')
    assert m is not None
    assert m.group(1) == "input[type=email]"


class TestSearchPage:
    def test_returns_results(self):
        class MockPage:
            def evaluate(self, js, *args):
                return [{"text": "hello world", "index": 0}, {"text": "say hello", "index": 1}]
        assert len(search_page(MockPage(), "hello")) == 2

    def test_no_results(self):
        class MockPage:
            def evaluate(self, js, *args):
                return []
        assert search_page(MockPage(), "x") == []

    def test_handles_error(self):
        class MockPage:
            def evaluate(self, js, *args):
                raise Exception("JS error")
        assert search_page(MockPage(), "x") == []


class TestFindElements:
    def test_returns_matches(self):
        class MockPage:
            def evaluate(self, js, *args):
                return [{"tag": "input", "type": "email", "name": "user_email", "id": "", "text": "", "href": ""}]
        results = find_elements(MockPage(), "input[type=email]")
        assert len(results) == 1
        assert results[0]["type"] == "email"

    def test_handles_error(self):
        class MockPage:
            def evaluate(self, js, *args):
                raise Exception("JS error")
        assert find_elements(MockPage(), "div") == []
