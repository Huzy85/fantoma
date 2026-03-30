"""Tests for adaptive DOM modes — form, content, navigate."""
import pytest
from unittest.mock import MagicMock, patch


def _el(role, name, state="", raw=None):
    return {"role": role, "name": name, "state": state, "raw": raw or {}}


def _make_page(title="Test Page", url="https://example.com", snapshot=""):
    """Create a mock Playwright page with ARIA snapshot."""
    page = MagicMock()
    page.title.return_value = title
    page.url = url
    page.locator.return_value.aria_snapshot.return_value = snapshot
    return page


# ── extract_aria mode parameter ──────────────────────────────

class TestExtractAriaMode:
    """Test the mode parameter on extract_aria."""

    SNAPSHOT = (
        '- heading "Welcome" [level=1]\n'
        '- textbox "Email"\n'
        '- textbox "Password"\n'
        '- button "Sign In"\n'
        '- link "Forgot Password"\n'
        '- link "About"\n'
        '- link "Contact"\n'
    )

    def test_navigate_mode_is_default(self):
        """Navigate mode should produce the same output as before (no mode param)."""
        from fantoma.dom.accessibility import extract_aria
        page = _make_page(snapshot=self.SNAPSHOT)
        result_default = extract_aria(page, task="sign in")
        result_navigate = extract_aria(page, task="sign in", mode="navigate")
        assert result_default == result_navigate

    def test_navigate_mode_includes_numbered_elements(self):
        from fantoma.dom.accessibility import extract_aria
        page = _make_page(snapshot=self.SNAPSHOT)
        result = extract_aria(page, task="sign in", mode="navigate")
        assert "[0]" in result
        assert "Elements" in result

    def test_content_mode_delegates_to_extract_aria_content(self):
        """Content mode should call extract_aria_content, not number elements."""
        from fantoma.dom.accessibility import extract_aria
        page = _make_page(snapshot=self.SNAPSHOT)
        result = extract_aria(page, mode="content")
        # Content mode output has "Page content:" not "Elements"
        assert "Page content:" in result
        # No numbered elements
        assert "[0]" not in result

    def test_form_mode_sorts_inputs_first(self):
        """Form mode should put textbox/combobox/searchbox before other elements."""
        from fantoma.dom.accessibility import extract_aria
        snapshot = (
            '- link "Home"\n'
            '- link "About"\n'
            '- textbox "Username"\n'
            '- button "Submit"\n'
            '- textbox "Password"\n'
            '- link "Terms"\n'
            '- combobox "Country"\n'
            '- searchbox "Search"\n'
        )
        page = _make_page(snapshot=snapshot)
        result = extract_aria(page, mode="form")
        lines = [l for l in result.split("\n") if l.startswith("[") or l.startswith("*[")]
        # First elements should be inputs
        input_roles = {"textbox", "combobox", "searchbox"}
        # Check that the first 4 elements (the inputs) are input types
        first_four_roles = []
        for line in lines[:4]:
            for role in input_roles:
                if role in line:
                    first_four_roles.append(role)
                    break
        assert len(first_four_roles) == 4, f"Expected 4 input roles first, got {first_four_roles} in {lines[:4]}"

    def test_form_mode_caps_elements_at_20(self):
        """Form mode should limit to 20 elements."""
        from fantoma.dom.accessibility import extract_aria
        elements = "\n".join(f'- button "Button {i}"' for i in range(30))
        page = _make_page(snapshot=elements)
        result = extract_aria(page, mode="form")
        # Count numbered elements
        numbered = [l for l in result.split("\n") if l.startswith("[") or l.startswith("*[")]
        assert len(numbered) <= 20

    def test_form_mode_caps_headings_at_5(self):
        """Form mode should limit to 5 headings."""
        from fantoma.dom.accessibility import extract_aria
        headings = "\n".join(f'- heading "Heading {i}" [level=1]' for i in range(10))
        elements = '- textbox "Email"\n' + headings
        page = _make_page(snapshot=elements)
        result = extract_aria(page, mode="form")
        # Count heading lines in "Page text:" section
        heading_lines = [l for l in result.split("\n") if "(h1)" in l]
        assert len(heading_lines) <= 5

    def test_invalid_mode_raises(self):
        """Invalid mode should raise ValueError."""
        from fantoma.dom.accessibility import extract_aria
        page = _make_page(snapshot='- button "OK"')
        with pytest.raises(ValueError, match="mode"):
            extract_aria(page, mode="invalid")


# ── AccessibilityExtractor.extract mode pass-through ─────────

class TestAccessibilityExtractorMode:
    """Test that AccessibilityExtractor.extract passes mode correctly."""

    SNAPSHOT = (
        '- heading "Login" [level=1]\n'
        '- textbox "Email"\n'
        '- textbox "Password"\n'
        '- button "Sign In"\n'
    )

    def test_extract_passes_mode_to_extract_aria(self):
        from fantoma.dom.accessibility import AccessibilityExtractor
        ext = AccessibilityExtractor()
        page = _make_page(snapshot=self.SNAPSHOT)
        # Default mode (navigate) should include numbered elements
        result = ext.extract(page, task="sign in", mode="navigate")
        assert "[0]" in result

    def test_extract_content_mode(self):
        from fantoma.dom.accessibility import AccessibilityExtractor
        ext = AccessibilityExtractor()
        page = _make_page(snapshot=self.SNAPSHOT)
        result = ext.extract(page, task="read the page", mode="content")
        assert "Page content:" in result

    def test_extract_default_mode_is_navigate(self):
        from fantoma.dom.accessibility import AccessibilityExtractor
        ext = AccessibilityExtractor()
        page = _make_page(snapshot=self.SNAPSHOT)
        result_default = ext.extract(page, task="do something")
        result_navigate = ext.extract(page, task="do something", mode="navigate")
        assert result_default == result_navigate


# ── _infer_dom_mode ──────────────────────────────────────────

class TestInferDomMode:
    """Test the DOM mode inference function."""

    def test_form_keywords(self):
        from fantoma.executor import _infer_dom_mode
        form_tasks = [
            "login to the website",
            "sign in with my email",
            "register a new account",
            "checkout and pay",
            "search for products",
            "fill in the form",
            "enter your email address",
            "submit the application",
            "subscribe to newsletter",
            "signup for free trial",
            "sign up for the service",
        ]
        for task in form_tasks:
            mode = _infer_dom_mode(task, page=None, element_count=0)
            assert mode == "form", f"Expected 'form' for task '{task}', got '{mode}'"

    def test_content_keywords(self):
        from fantoma.executor import _infer_dom_mode
        content_tasks = [
            "extract the email addresses",
            "read the article",
            "scrape product prices",
            "copy the main text",
            "get text from the page",
            "find information about pricing",
            "summarize this page",
        ]
        for task in content_tasks:
            mode = _infer_dom_mode(task, page=None, element_count=0)
            assert mode == "content", f"Expected 'content' for task '{task}', got '{mode}'"

    def test_default_is_navigate(self):
        from fantoma.executor import _infer_dom_mode
        navigate_tasks = [
            "click the download button",
            "go to the settings page",
            "open the profile menu",
        ]
        for task in navigate_tasks:
            mode = _infer_dom_mode(task, page=None, element_count=0)
            assert mode == "navigate", f"Expected 'navigate' for task '{task}', got '{mode}'"

    def test_textbox_override_forces_form(self):
        """5+ textboxes should force form mode regardless of keywords."""
        from fantoma.executor import _infer_dom_mode
        # Task says "read" (content keyword) but page has 5+ textboxes
        elements = [_el("textbox", f"Field {i}") for i in range(6)]
        mode = _infer_dom_mode("read this page", page=None, element_count=6)
        assert mode == "form"

    def test_textbox_under_threshold_no_override(self):
        """< 5 textboxes should not force form mode."""
        from fantoma.executor import _infer_dom_mode
        mode = _infer_dom_mode("read this page", page=None, element_count=3)
        assert mode == "content"

    def test_empty_task_defaults_navigate(self):
        from fantoma.executor import _infer_dom_mode
        assert _infer_dom_mode("", page=None, element_count=0) == "navigate"

    def test_case_insensitive(self):
        from fantoma.executor import _infer_dom_mode
        assert _infer_dom_mode("LOGIN to site", page=None, element_count=0) == "form"
        assert _infer_dom_mode("EXTRACT data", page=None, element_count=0) == "content"
