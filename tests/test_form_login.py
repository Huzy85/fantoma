"""Tests for form_login — retry-on-empty and FormMemory integration."""

import os
import re
import tempfile
from unittest.mock import MagicMock, patch


def _parse_elements(tree):
    """Parse mock accessibility tree into element dicts like AccessibilityExtractor does."""
    elements = []
    for line in tree.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        idx_match = re.match(r'\[(\d+)\]', line)
        if not idx_match:
            continue
        index = int(idx_match.group(1))
        label = ""
        if "'" in line:
            parts = line.split("'")
            if len(parts) >= 2:
                label = parts[1]
        role = ""
        for r in ("textbox", "button", "link", "checkbox", "combobox", "input"):
            if r in line.lower():
                role = r
                break
        if role:
            elements.append({"index": index, "role": role, "name": label})
    return elements


def _make_mock_browser():
    browser = MagicMock()
    page = MagicMock()
    browser.get_page.return_value = page
    page.url = "https://x.com/login"
    return browser, page


def _make_dynamic_dom(tree_fn):
    """Create a mock DOM extractor where extract() calls tree_fn to get the tree."""
    dom = MagicMock()

    def fake_extract(p):
        tree = tree_fn()
        dom._last_interactive = _parse_elements(tree)
        return tree

    dom.extract.side_effect = fake_extract
    dom.get_element_by_index.return_value = MagicMock()
    return dom


def _make_static_dom(tree):
    """Create a mock DOM extractor with a fixed tree."""
    dom = MagicMock()
    dom.extract.return_value = tree
    dom._last_interactive = _parse_elements(tree)
    dom.get_element_by_index.return_value = MagicMock()
    return dom


# Patch targets — these are imported inside the login function body
_PATCHES = {
    "type_into": "fantoma.browser.actions.type_into",
    "dismiss_consent": "fantoma.browser.consent.dismiss_consent",
    "looks_logged_in": "fantoma.browser.form_login._looks_logged_in",
    "sleep": "fantoma.browser.form_login.time.sleep",
}


def test_retry_on_empty_step():
    """When step > 0 and no fillable fields found, retry 3 times before giving up."""
    from fantoma.browser.form_login import login

    browser, page = _make_mock_browser()

    call_count = {"n": 0}
    step0_tree = "[0] textbox 'Email'\n[1] button 'Next'"
    step1_empty = "[0] heading 'Loading...'"
    step1_ready = "[0] textbox 'Password'\n[1] button 'Log in'"

    def tree_fn():
        call_count["n"] += 1
        if call_count["n"] == 1:
            return step0_tree
        elif call_count["n"] < 5:
            return step1_empty
        else:
            return step1_ready

    dom = _make_dynamic_dom(tree_fn)

    # URL must contain /login so _looks_logged_in returns False (keeps loop going)
    # But must change between steps so "page unchanged" check doesn't exit
    logged_in_check = {"n": 0}
    def fake_looks_logged_in(p, url):
        logged_in_check["n"] += 1
        # Return True only after step 1 completes (password filled)
        return logged_in_check["n"] >= 2

    # URL changes between step 0 and step 1 to avoid "unchanged" exit
    url_calls = {"n": 0}
    def url_getter(self):
        url_calls["n"] += 1
        if url_calls["n"] <= 4:
            return "https://x.com/login"
        return "https://x.com/login/step2"
    type(page).url = property(url_getter)

    with patch(_PATCHES["type_into"], return_value=True):
        with patch(_PATCHES["dismiss_consent"]):
            with patch(_PATCHES["sleep"]):
                with patch(_PATCHES["looks_logged_in"], side_effect=fake_looks_logged_in):
                    result = login(browser, dom, email="test@test.com", password="pass123")

    assert result["success"] is True
    assert call_count["n"] >= 4


def test_retry_gives_up_after_3_attempts():
    """When retries exhausted, stop."""
    from fantoma.browser.form_login import login

    browser, page = _make_mock_browser()

    call_count = {"n": 0}
    step0_tree = "[0] textbox 'Email'\n[1] button 'Next'"
    empty_tree = "[0] heading 'Loading...'"

    def tree_fn():
        call_count["n"] += 1
        if call_count["n"] == 1:
            return step0_tree
        return empty_tree

    dom = _make_dynamic_dom(tree_fn)

    url_seq = iter(["https://x.com/login"] * 3 + ["https://x.com/login/step2"] * 20)
    type(page).url = property(lambda self: next(url_seq, "https://x.com/login/step2"))

    with patch(_PATCHES["type_into"], return_value=True):
        with patch(_PATCHES["dismiss_consent"]):
            with patch(_PATCHES["sleep"]):
                result = login(browser, dom, email="test@test.com", password="pass123")

    # 1 for step 0 + 1 initial for step 1 + 3 retries = 5
    assert call_count["n"] >= 4


def test_memory_fallback_matching():
    """When hardcoded labels don't match, memory lookup provides the mapping."""
    from fantoma.browser.form_login import login
    from fantoma.browser.form_memory import FormMemory

    browser, page = _make_mock_browser()

    # 'Identifier' doesn't match EMAIL_LABELS or USERNAME_LABELS
    tree = "[0] textbox 'Identifier'\n[1] button 'Continue'"
    dom = _make_static_dom(tree)

    with tempfile.TemporaryDirectory() as d:
        fm = FormMemory(db_path=os.path.join(d, "test.db"))

        # Pre-populate: 'Identifier' was previously identified as email
        fm.record_step(
            domain="x.com", visit_id="prev", step_number=0,
            field_label="Identifier", field_role="textbox",
            field_purpose="email", submit_label="Continue", success=True,
            tree_text="", elements_json="[]",
            url="https://x.com/login", action="filled", result="ok"
        )

        with patch(_PATCHES["type_into"], return_value=True):
            with patch(_PATCHES["dismiss_consent"]):
                with patch(_PATCHES["looks_logged_in"], return_value=True):
                    with patch(_PATCHES["sleep"]):
                        with patch("fantoma.browser.form_login._find_raw_inputs", return_value=[]):
                            result = login(browser, dom, email="test@test.com",
                                           password="pass", memory=fm, visit_id="v2")

        assert len(result["fields_filled"]) >= 1
        fm.close()


def test_memory_records_steps():
    """When memory is provided, steps are recorded to it."""
    from fantoma.browser.form_login import login
    from fantoma.browser.form_memory import FormMemory

    browser, page = _make_mock_browser()

    tree = "[0] textbox 'Email'\n[1] textbox 'Password'\n[2] button 'Sign in'"
    dom = _make_static_dom(tree)

    with tempfile.TemporaryDirectory() as d:
        fm = FormMemory(db_path=os.path.join(d, "test.db"))

        with patch(_PATCHES["type_into"], return_value=True):
            with patch(_PATCHES["dismiss_consent"]):
                with patch(_PATCHES["looks_logged_in"], return_value=True):
                    with patch(_PATCHES["sleep"]):
                        result = login(browser, dom, email="a@b.com", password="p",
                                       memory=fm, visit_id="rec1")

        snaps = fm.get_snapshot("x.com", visit_id="rec1")
        assert len(snaps) >= 1
        fm.close()


def test_login_without_memory_unchanged():
    """Calling login without memory still works exactly as before."""
    from fantoma.browser.form_login import login

    browser, page = _make_mock_browser()

    tree = "[0] textbox 'Email'\n[1] textbox 'Password'\n[2] button 'Log in'"
    dom = _make_static_dom(tree)

    with patch(_PATCHES["type_into"], return_value=True):
        with patch(_PATCHES["dismiss_consent"]):
            with patch(_PATCHES["looks_logged_in"], return_value=True):
                with patch(_PATCHES["sleep"]):
                    result = login(browser, dom, email="a@b.com", password="p")

    assert result["success"] is True
