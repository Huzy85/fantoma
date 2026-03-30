"""Tests for fantoma/browser/form_assist.py"""

from unittest.mock import MagicMock, call, patch

import fantoma.browser.form_assist as fa


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_page():
    page = MagicMock()
    page.keyboard = MagicMock()
    return page


# ---------------------------------------------------------------------------
# after_type()
# ---------------------------------------------------------------------------

class TestAfterType:

    def test_autocomplete_suggestion_clicked(self):
        """When autocomplete succeeds, returns 'autocomplete' and skips form submit."""
        page = _make_page()
        with patch("fantoma.browser.form_assist.time.sleep"):
            with patch("fantoma.browser.form_assist._try_autocomplete", return_value=True) as mock_ac:
                with patch("fantoma.browser.form_assist._try_form_submit") as mock_fs:
                    result = fa.after_type(page, "London")

        assert result == "autocomplete"
        mock_ac.assert_called_once_with(page, "London")
        mock_fs.assert_not_called()

    def test_no_autocomplete_tries_form_submit(self):
        """When autocomplete finds nothing, falls through to form submit."""
        page = _make_page()
        with patch("fantoma.browser.form_assist.time.sleep"):
            with patch("fantoma.browser.form_assist._try_autocomplete", return_value=False):
                with patch("fantoma.browser.form_assist._try_form_submit", return_value=True) as mock_fs:
                    result = fa.after_type(page, "test@example.com")

        assert result == "submit"
        mock_fs.assert_called_once_with(page)

    def test_single_input_form_with_submit_button_auto_submits(self):
        """Single visible input + submit button → returns 'submit'."""
        page = _make_page()
        # First evaluate call: autocomplete check — empty list (no suggestions)
        # Second evaluate call: form state — single input with a submit button
        page.evaluate.side_effect = [
            [],  # autocomplete: no suggestions
            {"inputs": 1, "buttons": [{"text": "next", "ariaLabel": ""}]},
        ]
        with patch("fantoma.browser.form_assist.time.sleep"):
            result = fa.after_type(page, "hello")

        assert result == "submit"

    def test_multi_input_form_does_not_auto_submit(self):
        """Multiple visible inputs → _try_form_submit returns False → after_type returns ''."""
        page = _make_page()
        # First evaluate call (autocomplete check) returns []
        # Second evaluate call (form state) returns multiple inputs
        page.evaluate.side_effect = [
            [],  # autocomplete: no suggestions
            {"inputs": 2, "buttons": [{"text": "log in", "ariaLabel": ""}]},
        ]
        with patch("fantoma.browser.form_assist.time.sleep"):
            result = fa.after_type(page, "user@example.com")

        assert result == ""

    def test_nothing_done_returns_empty_string(self):
        """Both autocomplete and form submit do nothing → returns ''."""
        page = _make_page()
        with patch("fantoma.browser.form_assist.time.sleep"):
            with patch("fantoma.browser.form_assist._try_autocomplete", return_value=False):
                with patch("fantoma.browser.form_assist._try_form_submit", return_value=False):
                    result = fa.after_type(page, "anything")

        assert result == ""

    def test_exception_during_evaluate_handled_gracefully(self):
        """If page.evaluate raises inside after_type's path, it still returns cleanly."""
        page = _make_page()
        page.evaluate.side_effect = Exception("evaluate exploded")
        with patch("fantoma.browser.form_assist.time.sleep"):
            # Should not raise
            result = fa.after_type(page, "typed text")

        assert result in ("", "autocomplete", "submit")


# ---------------------------------------------------------------------------
# _try_autocomplete()
# ---------------------------------------------------------------------------

class TestTryAutocomplete:

    def test_suggestions_found_clicks_best_match(self):
        """When evaluate returns matching suggestion texts, returns True."""
        page = _make_page()
        page.evaluate.return_value = ["london bridge", "london eye"]
        with patch("fantoma.browser.form_assist.time.sleep"):
            result = fa._try_autocomplete(page, "london")

        assert result is True

    def test_no_suggestions_returns_false(self):
        """Empty list from evaluate → returns False."""
        page = _make_page()
        page.evaluate.return_value = []
        result = fa._try_autocomplete(page, "xyz")
        assert result is False

    def test_evaluate_throws_returns_false(self):
        """Exception in evaluate → caught, returns False."""
        page = _make_page()
        page.evaluate.side_effect = RuntimeError("js crash")
        result = fa._try_autocomplete(page, "test")
        assert result is False

    def test_multiple_suggestions_all_returned(self):
        """Multiple matching suggestions → still returns True (all clicks happen in JS)."""
        page = _make_page()
        page.evaluate.return_value = ["john doe", "john smith", "johnny cash"]
        with patch("fantoma.browser.form_assist.time.sleep"):
            result = fa._try_autocomplete(page, "john")

        assert result is True

    def test_typed_text_passed_to_evaluate(self):
        """The typed text is forwarded to page.evaluate."""
        page = _make_page()
        page.evaluate.return_value = []
        fa._try_autocomplete(page, "my search term")

        # evaluate should have been called once with the JS string + the typed text arg
        assert page.evaluate.call_count == 1
        _, args, _ = page.evaluate.mock_calls[0]
        # Second positional arg is the typed_text passed to the JS function
        assert "my search term" in args


# ---------------------------------------------------------------------------
# _try_form_submit()
# ---------------------------------------------------------------------------

class TestTryFormSubmit:

    def _state(self, inputs=1, buttons=None):
        return {"inputs": inputs, "buttons": buttons or []}

    def test_submit_button_found_presses_enter(self):
        """Single input + 'submit' button → presses Enter, returns True."""
        page = _make_page()
        page.evaluate.return_value = self._state(
            buttons=[{"text": "submit", "ariaLabel": ""}]
        )
        with patch("fantoma.browser.form_assist.time.sleep"):
            result = fa._try_form_submit(page)

        assert result is True
        page.keyboard.press.assert_called_once_with("Enter")

    def test_no_submit_button_returns_false(self):
        """Single input, no matching button → returns False."""
        page = _make_page()
        page.evaluate.return_value = self._state(
            buttons=[{"text": "cancel", "ariaLabel": ""}]
        )
        result = fa._try_form_submit(page)
        assert result is False

    def test_multiple_inputs_returns_false(self):
        """Two visible inputs → returns False without pressing Enter."""
        page = _make_page()
        page.evaluate.return_value = self._state(
            inputs=2,
            buttons=[{"text": "log in", "ariaLabel": ""}]
        )
        result = fa._try_form_submit(page)
        assert result is False
        page.keyboard.press.assert_not_called()

    def test_button_inside_form_clicked(self):
        """Button text 'log in' matches SUBMIT_PATTERNS → presses Enter."""
        page = _make_page()
        page.evaluate.return_value = self._state(
            buttons=[{"text": "log in", "ariaLabel": ""}]
        )
        with patch("fantoma.browser.form_assist.time.sleep"):
            result = fa._try_form_submit(page)

        assert result is True

    def test_next_button_pattern(self):
        """'next' matches SUBMIT_PATTERNS."""
        page = _make_page()
        page.evaluate.return_value = self._state(
            buttons=[{"text": "next", "ariaLabel": ""}]
        )
        with patch("fantoma.browser.form_assist.time.sleep"):
            result = fa._try_form_submit(page)
        assert result is True

    def test_continue_button_pattern(self):
        """'continue' matches SUBMIT_PATTERNS."""
        page = _make_page()
        page.evaluate.return_value = self._state(
            buttons=[{"text": "continue", "ariaLabel": ""}]
        )
        with patch("fantoma.browser.form_assist.time.sleep"):
            result = fa._try_form_submit(page)
        assert result is True

    def test_sign_in_button_pattern(self):
        """'sign in' matches SUBMIT_PATTERNS."""
        page = _make_page()
        page.evaluate.return_value = self._state(
            buttons=[{"text": "sign in", "ariaLabel": ""}]
        )
        with patch("fantoma.browser.form_assist.time.sleep"):
            result = fa._try_form_submit(page)
        assert result is True

    def test_aria_label_match(self):
        """Button with matching aria-label also triggers Enter."""
        page = _make_page()
        page.evaluate.return_value = self._state(
            buttons=[{"text": "", "ariaLabel": "next"}]
        )
        with patch("fantoma.browser.form_assist.time.sleep"):
            result = fa._try_form_submit(page)
        assert result is True

    def test_evaluate_throws_returns_false(self):
        """Exception in evaluate → returns False without raising."""
        page = _make_page()
        page.evaluate.side_effect = Exception("dom gone")
        result = fa._try_form_submit(page)
        assert result is False
        page.keyboard.press.assert_not_called()

    def test_no_buttons_at_all_returns_false(self):
        """Single input but zero buttons → returns False."""
        page = _make_page()
        page.evaluate.return_value = self._state(buttons=[])
        result = fa._try_form_submit(page)
        assert result is False

    def test_first_matching_pattern_wins(self):
        """When multiple buttons match, only one Enter press happens."""
        page = _make_page()
        page.evaluate.return_value = self._state(
            buttons=[
                {"text": "next", "ariaLabel": ""},
                {"text": "submit", "ariaLabel": ""},
            ]
        )
        with patch("fantoma.browser.form_assist.time.sleep"):
            result = fa._try_form_submit(page)

        assert result is True
        # Only one Enter press total
        page.keyboard.press.assert_called_once_with("Enter")

    def test_evaluate_returns_none_handled(self):
        """If evaluate returns None, returns False cleanly."""
        page = _make_page()
        page.evaluate.return_value = None
        result = fa._try_form_submit(page)
        assert result is False
