"""Tests for viewport scroll hints in ARIA extraction."""

from unittest.mock import MagicMock
import pytest

from fantoma.dom.accessibility import get_scroll_info


def _make_page(scroll_y=0, inner_height=800, scroll_height=2400):
    """Mock page with evaluate() returning scroll metrics."""
    page = MagicMock()
    def eval_fn(js):
        return {
            "pixels_above": scroll_y,
            "pixels_below": max(0, scroll_height - (inner_height + scroll_y)),
            "pages_above": round(scroll_y / inner_height, 1) if inner_height else 0,
            "pages_below": round(max(0, scroll_height - (inner_height + scroll_y)) / inner_height, 1) if inner_height else 0,
        }
    page.evaluate.side_effect = eval_fn
    return page


class TestGetScrollInfo:

    def test_at_top_of_page(self):
        page = _make_page(scroll_y=0, inner_height=800, scroll_height=2400)
        info = get_scroll_info(page)
        assert info["pixels_above"] == 0
        assert info["pixels_below"] == 1600
        assert info["pages_above"] == 0
        assert info["pages_below"] == 2.0

    def test_scrolled_midway(self):
        page = _make_page(scroll_y=800, inner_height=800, scroll_height=2400)
        info = get_scroll_info(page)
        assert info["pixels_above"] == 800
        assert info["pixels_below"] == 800
        assert info["pages_above"] == 1.0
        assert info["pages_below"] == 1.0

    def test_at_bottom_of_page(self):
        page = _make_page(scroll_y=1600, inner_height=800, scroll_height=2400)
        info = get_scroll_info(page)
        assert info["pixels_above"] == 1600
        assert info["pixels_below"] == 0
        assert info["pages_below"] == 0

    def test_short_page_no_scroll(self):
        page = _make_page(scroll_y=0, inner_height=800, scroll_height=600)
        info = get_scroll_info(page)
        assert info["pixels_above"] == 0
        assert info["pixels_below"] == 0

    def test_evaluate_failure_returns_none(self):
        page = MagicMock()
        page.evaluate.side_effect = Exception("JS error")
        assert get_scroll_info(page) is None


from fantoma.dom.accessibility import extract_aria, format_scroll_hints


class TestFormatScrollHints:

    def test_at_top_with_content_below(self):
        above, below = format_scroll_hints({"pixels_above": 0, "pixels_below": 1600, "pages_above": 0, "pages_below": 2.0})
        assert above == "[Top of page]"
        assert "1600 pixels below" in below
        assert "2.0 pages" in below

    def test_at_bottom_with_content_above(self):
        above, below = format_scroll_hints({"pixels_above": 1600, "pixels_below": 0, "pages_above": 2.0, "pages_below": 0})
        assert "1600 pixels above" in above
        assert below == "[End of page]"

    def test_midway(self):
        above, below = format_scroll_hints({"pixels_above": 800, "pixels_below": 800, "pages_above": 1.0, "pages_below": 1.0})
        assert "800 pixels above" in above
        assert "800 pixels below" in below

    def test_short_page(self):
        above, below = format_scroll_hints({"pixels_above": 0, "pixels_below": 3, "pages_above": 0, "pages_below": 0})
        assert above == "[Top of page]"
        assert below == "[End of page]"

    def test_none_returns_empty(self):
        above, below = format_scroll_hints(None)
        assert above == ""
        assert below == ""


class TestExtractAriaScrollHints:

    def test_scroll_hints_appear_in_output(self):
        """extract_aria output includes scroll context when page has content below."""
        page = MagicMock()
        page.title.return_value = "Test"
        page.url = "https://example.com"
        page.locator.return_value.aria_snapshot.return_value = '- button "Submit"'
        # Mock evaluate for scroll info
        page.evaluate.return_value = {
            "pixels_above": 0,
            "pixels_below": 1600,
            "pages_above": 0,
            "pages_below": 2.0,
        }
        result = extract_aria(page)
        assert "[Top of page]" in result
        assert "1600 pixels below" in result

    def test_no_scroll_hints_on_error(self):
        """extract_aria works without scroll hints when JS fails."""
        page = MagicMock()
        page.title.return_value = "Test"
        page.url = "https://example.com"
        page.locator.return_value.aria_snapshot.return_value = '- button "Submit"'
        page.evaluate.side_effect = Exception("JS error")
        result = extract_aria(page)
        assert "Submit" in result
        assert "[Top of page]" not in result
