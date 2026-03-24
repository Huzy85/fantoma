"""Tests for DOM extraction, element lookup, page diff, and selectors.

All tests run without a real browser by mocking the Playwright Page object.
"""

from unittest.mock import MagicMock, patch
import pytest

from fantoma.dom.extractor import DOMExtractor, _EXTRACT_JS, _GET_ELEMENT_JS
from fantoma.dom.diff import PageDiff
from fantoma.dom.selectors import build_selector, find_by_text, find_by_role


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_page(
    title="Test Page",
    url="https://example.com",
    elements=None,
    headings=None,
    iframe_count=0,
    iframes=None,
):
    """Create a mock Playwright Page with evaluate() returning DOM data."""
    page = MagicMock()
    page.title.return_value = title
    page.url = url

    extract_data = {
        "elements": elements or [],
        "headings": headings or [],
        "iframeCount": iframe_count,
        "iframes": iframes or [],
    }

    def evaluate_side_effect(js, *args):
        if js is _EXTRACT_JS or (isinstance(js, str) and "INTERACTIVE" in js and "results" in js):
            return extract_data
        if js is _GET_ELEMENT_JS:
            # Return a CSS selector for the target index
            idx = args[0] if args else 1
            if elements and 1 <= idx <= len(elements):
                el = elements[idx - 1]
                if el.get("id"):
                    return f"#{el['id']}"
                return f"{el.get('tag', 'div')}:nth-of-type({idx})"
            return None
        return None

    page.evaluate.side_effect = evaluate_side_effect
    return page


def _make_element(
    tag="button", text="Click me", el_type="", role="", aria_label="",
    placeholder="", name="", el_id="", href="", value="", data_test_id="",
):
    return {
        "tag": tag, "text": text, "type": el_type, "role": role,
        "ariaLabel": aria_label, "placeholder": placeholder, "name": name,
        "id": el_id, "href": href, "value": value, "dataTestId": data_test_id,
        "rect": {"top": 0, "left": 0, "width": 100, "height": 30},
    }


# ---------------------------------------------------------------------------
# DOMExtractor tests
# ---------------------------------------------------------------------------

class TestDOMExtractor:

    def test_extract_returns_numbered_elements(self):
        """Output contains [0], [1], [2] for three interactive elements (0-based indexing)."""
        elements = [
            _make_element(tag="input", text="", placeholder="Email", value=""),
            _make_element(tag="input", text="", placeholder="Password", el_type="password"),
            _make_element(tag="button", text="Sign In"),
        ]
        page = _make_page(title="Login", elements=elements)
        extractor = DOMExtractor()
        result = extractor.extract(page)

        assert "[0]" in result
        assert "[1]" in result
        assert "[2]" in result
        assert "Email" in result
        assert "Password" in result
        assert "Sign In" in result

    def test_extract_includes_page_info(self):
        """Output starts with page title and URL."""
        page = _make_page(title="My Page", url="https://example.com/test")
        extractor = DOMExtractor()
        result = extractor.extract(page)

        assert "Page: My Page" in result
        assert "URL: https://example.com/test" in result

    def test_extract_no_elements(self):
        """Pages with no interactive elements still produce valid output."""
        page = _make_page(title="Empty", elements=[])
        extractor = DOMExtractor()
        result = extractor.extract(page)

        assert "none found" in result.lower() or "Interactive elements:" in result

    def test_extract_includes_headings(self):
        """Headings appear in the key text section."""
        headings = [
            {"level": "h1", "text": "Welcome"},
            {"level": "h2", "text": "Getting Started"},
        ]
        page = _make_page(headings=headings)
        extractor = DOMExtractor()
        result = extractor.extract(page)

        assert "Key text:" in result
        assert "Welcome" in result
        assert "Getting Started" in result

    def test_extract_notes_iframes(self):
        """Iframes are noted but not extracted inside."""
        page = _make_page(
            iframe_count=2,
            iframes=[{"src": "https://ads.example.com", "title": "Ad"}],
        )
        extractor = DOMExtractor()
        result = extractor.extract(page)

        assert "iframe" in result.lower()
        assert "2" in result

    def test_skip_hidden_elements(self):
        """Hidden elements are excluded by the JS extraction (tested via mock).

        The actual filtering happens in browser JS. Here we verify that
        the extractor faithfully renders only what JS returns — if JS
        returns 2 elements (having already filtered hidden ones), the
        output should have exactly [0] and [1] (0-based indexing).
        """
        visible = [
            _make_element(tag="button", text="Visible 1"),
            _make_element(tag="button", text="Visible 2"),
        ]
        page = _make_page(elements=visible)
        extractor = DOMExtractor()
        result = extractor.extract(page)

        assert "[0]" in result
        assert "[1]" in result
        assert "[2]" not in result
        assert "Visible 1" in result
        assert "Visible 2" in result

    def test_get_element_by_index(self):
        """Correct element is returned for a given index (0-based)."""
        elements = [
            _make_element(tag="input", el_id="email-field", placeholder="Email"),
            _make_element(tag="button", text="Submit"),
        ]
        page = _make_page(elements=elements)
        mock_handle = MagicMock()
        page.query_selector.return_value = mock_handle

        extractor = DOMExtractor()
        # Must call extract() first to cache elements
        extractor.extract(page)
        result = extractor.get_element_by_index(page, 0)

        assert result is mock_handle
        page.query_selector.assert_called_with("input#email-field")

    def test_get_element_by_index_out_of_range(self):
        """Out-of-range index returns None."""
        page = _make_page(elements=[])
        extractor = DOMExtractor()
        result = extractor.get_element_by_index(page, 99)
        assert result is None

    def test_element_description_input(self):
        """Input elements show type and value."""
        el = _make_element(tag="input", placeholder="Search", el_type="text", value="hello")
        desc = DOMExtractor._describe_element(el)
        assert "input" in desc
        assert "Search" in desc
        assert "hello" in desc

    def test_element_description_link(self):
        """Links show their text."""
        el = _make_element(tag="a", text="Click here", href="https://example.com")
        desc = DOMExtractor._describe_element(el)
        assert "link" in desc
        assert "Click here" in desc

    def test_element_description_dropdown(self):
        """Select elements are described as dropdowns."""
        el = _make_element(tag="select", aria_label="Country", value="UK")
        desc = DOMExtractor._describe_element(el)
        assert "dropdown" in desc
        assert "Country" in desc


# ---------------------------------------------------------------------------
# PageDiff tests
# ---------------------------------------------------------------------------

class TestPageDiff:

    def test_detects_url_change(self):
        """URL change is detected as a meaningful change."""
        before = {"url": "https://a.com/page1", "title": "P1", "dom_hash": "abc", "element_count": 5}
        after = {"url": "https://a.com/page2", "title": "P1", "dom_hash": "abc", "element_count": 5}
        diff = PageDiff()
        assert diff.changed(before, after) is True

    def test_same_page_not_changed(self):
        """Identical snapshots are not flagged as changed."""
        snapshot = {"url": "https://a.com", "title": "T", "dom_hash": "xyz", "element_count": 10}
        diff = PageDiff()
        assert diff.changed(snapshot, snapshot) is False

    def test_detects_title_change(self):
        before = {"url": "https://a.com", "title": "Before", "dom_hash": "abc", "element_count": 5}
        after = {"url": "https://a.com", "title": "After", "dom_hash": "abc", "element_count": 5}
        diff = PageDiff()
        assert diff.changed(before, after) is True

    def test_detects_dom_hash_change(self):
        before = {"url": "https://a.com", "title": "T", "dom_hash": "aaa", "element_count": 5}
        after = {"url": "https://a.com", "title": "T", "dom_hash": "bbb", "element_count": 5}
        diff = PageDiff()
        assert diff.changed(before, after) is True

    def test_detects_large_element_count_change(self):
        before = {"url": "https://a.com", "title": "T", "dom_hash": "abc", "element_count": 5}
        after = {"url": "https://a.com", "title": "T", "dom_hash": "abc", "element_count": 15}
        diff = PageDiff()
        assert diff.changed(before, after) is True

    def test_small_element_count_not_changed(self):
        """A difference of 1-2 elements (with many total) is not meaningful."""
        before = {"url": "https://a.com", "title": "T", "dom_hash": "abc", "element_count": 50}
        after = {"url": "https://a.com", "title": "T", "dom_hash": "abc", "element_count": 51}
        diff = PageDiff()
        assert diff.changed(before, after) is False

    def test_describe_change_url(self):
        before = {"url": "https://a.com/1", "title": "T", "dom_hash": "abc", "element_count": 5}
        after = {"url": "https://a.com/2", "title": "T", "dom_hash": "abc", "element_count": 5}
        diff = PageDiff()
        desc = diff.describe_change(before, after)
        assert "URL changed" in desc

    def test_describe_no_change(self):
        snap = {"url": "https://a.com", "title": "T", "dom_hash": "abc", "element_count": 5}
        diff = PageDiff()
        desc = diff.describe_change(snap, snap)
        assert "No meaningful changes" in desc


# ---------------------------------------------------------------------------
# Selector builder tests
# ---------------------------------------------------------------------------

class TestSelectors:

    def test_build_selector_data_testid(self):
        info = {"tag": "button", "dataTestId": "submit-btn"}
        assert build_selector(info) == '[data-testid="submit-btn"]'

    def test_build_selector_id(self):
        info = {"tag": "input", "id": "email"}
        assert build_selector(info) == "#email"

    def test_build_selector_aria_label(self):
        info = {"tag": "button", "ariaLabel": "Close dialog"}
        assert build_selector(info) == 'button[aria-label="Close dialog"]'

    def test_build_selector_name(self):
        info = {"tag": "input", "name": "username"}
        assert build_selector(info) == 'input[name="username"]'

    def test_build_selector_fallback_tag(self):
        info = {"tag": "div"}
        assert build_selector(info) == "div"

    def test_build_selector_priority(self):
        """data-testid takes priority over id."""
        info = {"tag": "button", "dataTestId": "btn-1", "id": "my-btn"}
        assert build_selector(info) == '[data-testid="btn-1"]'

    def test_find_by_text_uses_locator(self):
        page = MagicMock()
        mock_handle = MagicMock()
        mock_locator = MagicMock()
        mock_locator.count.return_value = 1
        mock_locator.first.element_handle.return_value = mock_handle
        page.get_by_text.return_value = mock_locator

        result = find_by_text(page, "Submit")
        assert result is mock_handle

    def test_find_by_role_uses_locator(self):
        page = MagicMock()
        mock_handle = MagicMock()
        mock_locator = MagicMock()
        mock_locator.count.return_value = 1
        mock_locator.first.element_handle.return_value = mock_handle
        page.get_by_role.return_value = mock_locator

        result = find_by_role(page, "button", name="OK")
        assert result is mock_handle
