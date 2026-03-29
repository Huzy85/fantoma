"""Tests for paint-order DOM filtering in AccessibilityExtractor.

All tests run without a real browser by mocking the Playwright Page object.
"""

from unittest.mock import MagicMock, patch, call
import pytest

from fantoma.dom.accessibility import AccessibilityExtractor


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_elements(*names):
    """Build a list of element dicts with the given names (all buttons)."""
    return [{"index": i, "role": "button", "name": name} for i, name in enumerate(names)]


def _make_page_with_evaluate(evaluate_fn=None, side_effect=None):
    """Return a mock Page whose evaluate() uses the given side-effect function."""
    page = MagicMock()
    page.title.return_value = "Test Page"
    page.url = "https://example.com"
    if side_effect is not None:
        page.evaluate.side_effect = side_effect
    elif evaluate_fn is not None:
        page.evaluate.side_effect = evaluate_fn
    return page


# ---------------------------------------------------------------------------
# Method existence
# ---------------------------------------------------------------------------

class TestFilterOccludedExists:

    def test_method_exists_on_class(self):
        """AccessibilityExtractor must have a _filter_occluded method."""
        assert hasattr(AccessibilityExtractor, "_filter_occluded")
        assert callable(getattr(AccessibilityExtractor, "_filter_occluded"))

    def test_method_accepts_page_and_elements(self):
        """_filter_occluded(page, elements) must return a list."""
        extractor = AccessibilityExtractor()
        page = _make_page_with_evaluate(lambda js, *args: True)
        elements = _make_elements("Submit", "Cancel")
        result = extractor._filter_occluded(page, elements)
        assert isinstance(result, list)


# ---------------------------------------------------------------------------
# All elements visible
# ---------------------------------------------------------------------------

class TestFilterOccludedAllVisible:

    def test_keeps_all_when_all_on_top(self):
        """When every element is visually on top, none are removed."""
        extractor = AccessibilityExtractor()
        page = _make_page_with_evaluate(lambda js, *args: True)
        elements = _make_elements("Login", "Sign up", "Forgot password")
        result = extractor._filter_occluded(page, elements)
        assert len(result) == 3

    def test_returns_same_elements_when_visible(self):
        """Returned list must contain the same dicts, not copies."""
        extractor = AccessibilityExtractor()
        page = _make_page_with_evaluate(lambda js, *args: True)
        elements = _make_elements("OK")
        result = extractor._filter_occluded(page, elements)
        assert result[0] is elements[0]

    def test_empty_list_returns_empty(self):
        """Empty input returns empty output without touching evaluate."""
        extractor = AccessibilityExtractor()
        page = MagicMock()
        result = extractor._filter_occluded(page, [])
        assert result == []
        page.evaluate.assert_not_called()


# ---------------------------------------------------------------------------
# JS error handling
# ---------------------------------------------------------------------------

class TestFilterOccludedJsError:

    def test_outer_js_error_returns_all_unchanged(self):
        """If evaluate() raises on every call, all elements are returned unchanged."""
        extractor = AccessibilityExtractor()
        page = _make_page_with_evaluate(side_effect=Exception("JS engine crash"))
        elements = _make_elements("A", "B", "C")
        result = extractor._filter_occluded(page, elements)
        # All kept — graceful degradation
        assert len(result) == 3

    def test_per_element_js_error_keeps_that_element(self):
        """If a single element's check raises, that element is kept (assume visible)."""
        extractor = AccessibilityExtractor()
        call_count = [0]

        def evaluate_fn(js, *args):
            call_count[0] += 1
            if call_count[0] == 2:
                raise RuntimeError("bad element")
            return True

        page = _make_page_with_evaluate(evaluate_fn)
        elements = _make_elements("One", "Two", "Three")
        result = extractor._filter_occluded(page, elements)
        # All three kept — Two had error so it defaults to visible
        assert len(result) == 3

    def test_returns_original_list_on_catastrophic_error(self):
        """On a top-level exception, the original list object is returned."""
        extractor = AccessibilityExtractor()
        page = MagicMock()
        # Make page.evaluate itself raise when iterated
        page.evaluate.side_effect = Exception("catastrophic")
        elements = _make_elements("X", "Y")
        result = extractor._filter_occluded(page, elements)
        assert len(result) == len(elements)


# ---------------------------------------------------------------------------
# Occluded element removal
# ---------------------------------------------------------------------------

class TestFilterOccludedRemovesHidden:

    def test_removes_occluded_element(self):
        """Elements where evaluate returns False are filtered out."""
        extractor = AccessibilityExtractor()
        # "Submit" is on top; "Cancel" is behind the modal
        responses = {"Submit": True, "Cancel": False}

        def evaluate_fn(js, role, name):
            return responses.get(name, True)

        page = _make_page_with_evaluate(evaluate_fn)
        elements = _make_elements("Submit", "Cancel")
        result = extractor._filter_occluded(page, elements)
        assert len(result) == 1
        assert result[0]["name"] == "Submit"

    def test_keeps_only_visible_elements(self):
        """Only elements explicitly returning True pass through."""
        extractor = AccessibilityExtractor()
        visibility = {"Open": True, "Hidden1": False, "Close": True, "Hidden2": False}

        def evaluate_fn(js, role, name):
            return visibility.get(name, True)

        page = _make_page_with_evaluate(evaluate_fn)
        elements = _make_elements("Open", "Hidden1", "Close", "Hidden2")
        result = extractor._filter_occluded(page, elements)
        names = [el["name"] for el in result]
        assert names == ["Open", "Close"]

    def test_removes_all_when_all_occluded(self):
        """All elements can be filtered if all are behind a modal."""
        extractor = AccessibilityExtractor()
        page = _make_page_with_evaluate(lambda js, *args: False)
        elements = _make_elements("A", "B", "C")
        result = extractor._filter_occluded(page, elements)
        assert result == []

    def test_evaluate_called_once_per_element(self):
        """evaluate() is called exactly once for each element."""
        extractor = AccessibilityExtractor()
        page = _make_page_with_evaluate(lambda js, *args: True)
        elements = _make_elements("X", "Y", "Z")
        extractor._filter_occluded(page, elements)
        assert page.evaluate.call_count == 3


# ---------------------------------------------------------------------------
# Integration: extract() calls _filter_occluded
# ---------------------------------------------------------------------------

class TestExtractCallsFilter:

    def test_extract_applies_filter(self):
        """extract() must call _filter_occluded and use its result."""
        extractor = AccessibilityExtractor()

        aria_output = (
            'Page: Test\nURL: https://example.com\n\n'
            'Elements (2 of 2):\n'
            '[0] button "Visible"\n'
            '[1] button "Hidden"\n'
        )

        page = MagicMock()
        page.title.return_value = "Test"
        page.url = "https://example.com"
        page.locator.return_value.aria_snapshot.return_value = (
            '- button "Visible"\n- button "Hidden"'
        )

        filtered = [{"index": 0, "role": "button", "name": "Visible"}]

        with patch.object(extractor, "_filter_occluded", return_value=filtered) as mock_filter:
            with patch("fantoma.dom.accessibility.extract_aria", return_value=aria_output):
                extractor.extract(page)
            mock_filter.assert_called_once()
            assert extractor._last_interactive == filtered
