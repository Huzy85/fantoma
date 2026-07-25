"""Tests for the `changed` flag on action results.

`changed` was a copy of `url_changed`, so every in-page interaction claimed
nothing had happened. Measured live on a single-page shop: clicking "Add to
cart" genuinely swapped the button to "Remove", and the result still said
changed=False. Any caller using that flag to decide whether an action worked
would conclude the click failed and retry or give up.
"""

import pytest

from fantoma.browser_tool import Fantoma


@pytest.fixture
def tool():
    """A Fantoma instance with state extraction stubbed out."""
    f = Fantoma.__new__(Fantoma)   # skip __init__; no browser wanted here
    f._task = ""
    f._last_tree = None
    f._state = {"url": "https://shop.test/inventory", "title": "Shop",
                "aria_tree": "[0] button \"Add to cart\"", "errors": [],
                "tab_count": 1}
    f.get_state = lambda **kw: dict(f._state)
    return f


class TestChangedFlag:
    def test_dom_change_without_url_change_is_reported(self, tool):
        """The bug: an SPA click changes the page but never the URL."""
        tool._last_tree = '[0] button "Add to cart"'
        tool._state["aria_tree"] = '[0] button "Remove"'
        result = tool._action_result(True, pre_url="https://shop.test/inventory")
        assert result["changed"] is True
        assert result["url_changed"] is False, "URL genuinely did not change"

    def test_identical_page_reports_no_change(self, tool):
        tool._last_tree = '[0] button "Add to cart"'
        tool._state["aria_tree"] = '[0] button "Add to cart"'
        result = tool._action_result(True, pre_url="https://shop.test/inventory")
        assert result["changed"] is False

    def test_url_change_alone_still_counts(self, tool):
        """A plain navigation changes the URL; that is still a change."""
        tool._last_tree = '[0] button "Add to cart"'
        tool._state["aria_tree"] = '[0] button "Add to cart"'
        result = tool._action_result(True, pre_url="https://shop.test/other")
        assert result["changed"] is True
        assert result["url_changed"] is True

    def test_first_action_has_no_baseline_and_does_not_guess(self, tool):
        """Without a prior tree, only a URL change can be asserted."""
        tool._last_tree = None
        result = tool._action_result(True, pre_url="https://shop.test/inventory")
        assert result["changed"] is False

    def test_baseline_advances_after_each_action(self, tool):
        """The post-state becomes the next action's baseline, so comparing
        costs no extra extraction."""
        tool._last_tree = '[0] button "Add to cart"'
        tool._state["aria_tree"] = '[0] button "Remove"'
        tool._action_result(True, pre_url="https://shop.test/inventory")
        assert tool._last_tree == '[0] button "Remove"'
        # Same page again — now correctly reported as unchanged.
        assert tool._action_result(
            True, pre_url="https://shop.test/inventory")["changed"] is False

    def test_changed_is_reported_even_when_the_action_failed(self, tool):
        """success and changed answer different questions."""
        tool._last_tree = '[0] button "Add to cart"'
        tool._state["aria_tree"] = '[0] alert "Out of stock"'
        result = tool._action_result(False, pre_url="https://shop.test/inventory")
        assert result["success"] is False
        assert result["changed"] is True
