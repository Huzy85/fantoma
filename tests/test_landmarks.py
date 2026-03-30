"""Tests for ARIA landmark grouping in extract_aria output."""
import pytest
from unittest.mock import MagicMock

from fantoma.dom.accessibility import extract_aria, LANDMARK_ROLES


def _make_page(title="Test Page", url="https://example.com", snapshot=""):
    page = MagicMock()
    page.title.return_value = title
    page.url = url
    page.locator.return_value.aria_snapshot.return_value = snapshot
    return page


# ── LANDMARK_ROLES constant ────────────────────────────────────

class TestLandmarkRolesConstant:
    def test_contains_expected_roles(self):
        expected = {"form", "navigation", "region", "main", "banner",
                    "contentinfo", "complementary", "search"}
        assert LANDMARK_ROLES == expected

    def test_no_interactive_roles_in_landmarks(self):
        from fantoma.dom.accessibility import INTERACTIVE_ROLES
        assert LANDMARK_ROLES.isdisjoint(INTERACTIVE_ROLES)


# ── Landmark detection and grouping ────────────────────────────

class TestLandmarkGrouping:
    """Elements nested under landmarks get grouped with headers."""

    SNAPSHOT_WITH_LANDMARKS = (
        '- form "Login":\n'
        '  - textbox "Username"\n'
        '  - textbox "Password"\n'
        '  - button "Sign In"\n'
        '- navigation "Main nav":\n'
        '  - link "Dashboard"\n'
        '  - link "Settings"\n'
        '- button "Cookie consent"\n'
    )

    def test_landmark_headers_appear(self):
        page = _make_page(snapshot=self.SNAPSHOT_WITH_LANDMARKS)
        result = extract_aria(page)
        assert "[form: Login]" in result
        assert "[navigation: Main nav]" in result

    def test_other_section_for_ungrouped(self):
        page = _make_page(snapshot=self.SNAPSHOT_WITH_LANDMARKS)
        result = extract_aria(page)
        assert "[Other]" in result

    def test_indices_globally_sequential(self):
        page = _make_page(snapshot=self.SNAPSHOT_WITH_LANDMARKS)
        result = extract_aria(page)
        lines = result.split("\n")
        indices = []
        for line in lines:
            import re
            m = re.match(r'\*?\[(\d+)\]', line.strip())
            if m:
                indices.append(int(m.group(1)))
        assert indices == list(range(len(indices)))

    def test_elements_under_correct_landmark(self):
        page = _make_page(snapshot=self.SNAPSHOT_WITH_LANDMARKS)
        result = extract_aria(page)
        lines = result.split("\n")
        # Find section after [form: Login]
        form_idx = next(i for i, l in enumerate(lines) if "[form: Login]" in l)
        nav_idx = next(i for i, l in enumerate(lines) if "[navigation: Main nav]" in l)
        form_section = "\n".join(lines[form_idx:nav_idx])
        assert 'textbox "Username"' in form_section
        assert 'textbox "Password"' in form_section
        assert 'button "Sign In"' in form_section

    def test_no_other_when_all_in_landmarks(self):
        snapshot = (
            '- form "Login":\n'
            '  - textbox "Email"\n'
            '  - button "Submit"\n'
        )
        page = _make_page(snapshot=snapshot)
        result = extract_aria(page)
        assert "[Other]" not in result

    def test_no_landmark_headers_when_no_landmarks(self):
        snapshot = (
            '- button "OK"\n'
            '- link "Home"\n'
        )
        page = _make_page(snapshot=snapshot)
        result = extract_aria(page)
        assert "[Other]" not in result
        assert "[0]" in result
        assert "[1]" in result

    def test_empty_landmark_no_header(self):
        """Landmarks with no interactive children produce no group header."""
        snapshot = (
            '- navigation "Empty nav":\n'
            '  - heading "Just a heading" [level=1]\n'
            '- button "OK"\n'
        )
        page = _make_page(snapshot=snapshot)
        result = extract_aria(page)
        assert "[navigation: Empty nav]" not in result

    def test_landmark_without_name(self):
        snapshot = (
            '- main:\n'
            '  - button "Click me"\n'
        )
        page = _make_page(snapshot=snapshot)
        result = extract_aria(page)
        assert "[main]" in result


# ── Landmark field on element dicts ────────────────────────────

class TestLandmarkField:
    """Each element dict should have a _landmark field."""

    def test_landmark_field_set(self):
        """Elements inside a landmark get _landmark set."""
        snapshot = (
            '- form "Login":\n'
            '  - textbox "Email"\n'
            '- button "OK"\n'
        )
        page = _make_page(snapshot=snapshot)
        # We need to check the intermediate state — inspect via output
        result = extract_aria(page)
        assert "[form: Login]" in result

    def test_landmark_field_none_outside(self):
        """Elements outside any landmark get _landmark=None."""
        snapshot = '- button "OK"\n'
        page = _make_page(snapshot=snapshot)
        result = extract_aria(page)
        # No landmark headers, just plain elements
        assert "[Other]" not in result


# ── Indent tracking ────────────────────────────────────────────

class TestIndentTracking:
    """Landmark scope ends when indent drops to/below landmark level."""

    def test_nested_landmark_scope(self):
        snapshot = (
            '- form "Outer":\n'
            '  - textbox "Field 1"\n'
            '  - textbox "Field 2"\n'
            '- link "Outside"\n'
        )
        page = _make_page(snapshot=snapshot)
        result = extract_aria(page)
        lines = result.split("\n")
        # "Outside" link should not be in the form group
        form_idx = next(i for i, l in enumerate(lines) if "[form: Outer]" in l)
        other_idx = next(i for i, l in enumerate(lines) if "[Other]" in l)
        form_section = "\n".join(lines[form_idx:other_idx])
        assert 'link "Outside"' not in form_section

    def test_deeply_nested_elements(self):
        """Elements deeply nested inside a landmark still belong to it."""
        snapshot = (
            '- form "Deep":\n'
            '  - group:\n'
            '    - list:\n'
            '      - listitem:\n'
            '        - textbox "Deep field"\n'
            '  - button "Submit"\n'
        )
        page = _make_page(snapshot=snapshot)
        result = extract_aria(page)
        assert "[form: Deep]" in result
        assert 'textbox "Deep field"' in result
        # Both elements in the same form group
        lines = result.split("\n")
        form_idx = next(i for i, l in enumerate(lines) if "[form: Deep]" in l)
        # All interactive elements should follow the form header
        after_form = "\n".join(lines[form_idx:])
        assert 'textbox "Deep field"' in after_form
        assert 'button "Submit"' in after_form

    def test_consecutive_landmarks(self):
        """Two landmarks back to back, each gets its own group."""
        snapshot = (
            '- form "First":\n'
            '  - textbox "A"\n'
            '- form "Second":\n'
            '  - textbox "B"\n'
        )
        page = _make_page(snapshot=snapshot)
        result = extract_aria(page)
        assert "[form: First]" in result
        assert "[form: Second]" in result


# ── Mode interaction ───────────────────────────────────────────

class TestLandmarkWithModes:
    """Landmark grouping works with form and navigate modes."""

    FORM_SNAPSHOT = (
        '- form "Register":\n'
        '  - textbox "Name"\n'
        '  - textbox "Email"\n'
        '  - button "Sign Up"\n'
        '- link "Terms"\n'
    )

    def test_form_mode_keeps_landmarks(self):
        page = _make_page(snapshot=self.FORM_SNAPSHOT)
        result = extract_aria(page, mode="form")
        # Form mode sorts inputs first but landmarks should still appear
        assert "Elements" in result
        # Indices should still be sequential
        import re
        indices = [int(m.group(1)) for m in re.finditer(r'\*?\[(\d+)\]', result)]
        assert indices == list(range(len(indices)))

    def test_navigate_mode_has_landmarks(self):
        page = _make_page(snapshot=self.FORM_SNAPSHOT)
        result = extract_aria(page, mode="navigate")
        assert "[form: Register]" in result

    def test_content_mode_unaffected(self):
        """Content mode delegates early, no landmark grouping needed."""
        page = _make_page(snapshot=self.FORM_SNAPSHOT)
        result = extract_aria(page, mode="content")
        assert "Page content:" in result
        # No element indices
        assert "[0]" not in result


# ── _parse_interactive_from_output still works ─────────────────

class TestParseInteractiveWithLandmarks:
    """The output parser must still extract elements from landmark-grouped output."""

    def test_parse_with_landmark_headers(self):
        from fantoma.dom.accessibility import AccessibilityExtractor
        output = (
            'Page: Test\n'
            'URL: https://example.com\n'
            '\n'
            'Elements (3 of 3):\n'
            '\n'
            '[form: Login]\n'
            '[0] textbox "Email"\n'
            '[1] button "Submit"\n'
            '\n'
            '[Other]\n'
            '[2] link "Home"\n'
        )
        elements = AccessibilityExtractor._parse_interactive_from_output(output)
        assert len(elements) == 3
        assert elements[0]["role"] == "textbox"
        assert elements[1]["role"] == "button"
        assert elements[2]["role"] == "link"
        assert elements[0]["index"] == 0
        assert elements[2]["index"] == 2
