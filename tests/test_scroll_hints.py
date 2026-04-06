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
