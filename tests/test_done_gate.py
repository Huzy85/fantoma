"""Tests for the premature-DONE gate.

Measured live before this existed: "add the Sauce Labs Backpack to the cart"
reached the product listing and declared DONE after two steps, cart untouched.
The system prompt already told the model not to do that; prompts do not bind
weak models, so the check has to be in code.

The risk in the other direction is worse than the bug: if the gate fires on a
read-only task it blocks DONE and burns the whole step budget. Half of these
tests guard that side.
"""

import pytest

from fantoma.navigator import _requires_state_change, _STATE_CHANGING


class TestActionDetection:
    @pytest.mark.parametrize("instruction", [
        "Add the Sauce Labs Backpack to the cart",
        "Log in with username 'tomsmith' and password 'secret'",
        "Select 'Option 2' from the dropdown",
        "Tick the first checkbox on this page",
        "Register a new account with username 'probe'",
        "Search for 'flask' and report the version",
        "Fill in the checkout form",
        "Click the Sign In button",
        "Subscribe to the newsletter",
        "Submit the contact form",
    ])
    def test_action_tasks_require_a_state_change(self, instruction):
        assert _requires_state_change(instruction) is True

    @pytest.mark.parametrize("instruction", [
        "What is the main heading on this page?",
        "Tell me the title of the top story",
        "How many stars does this repository have?",
        "Who created this programming language?",
        "What kind of website is this?",
        "Report the latest version of the requests package",
        "Summarise what this page is about",
    ])
    def test_read_only_tasks_do_not(self, instruction):
        """A false positive here burns the entire step budget."""
        assert _requires_state_change(instruction) is False

    def test_word_boundaries_prevent_false_positives(self):
        """Substring matching would fire on innocent words."""
        # "research" contains "search", "postcode" contains "post",
        # "checkered" contains "check", "ordering" is fine but "order" is in
        # "border" — all must stay read-only.
        for text in ("Summarise the research on this page",
                     "What is the postcode shown?",
                     "Describe the border style",
                     "What does the checkered flag mean?"):
            assert _requires_state_change(text) is False, text

    def test_empty_instruction_is_safe(self):
        assert _requires_state_change("") is False
        assert _requires_state_change(None) is False


class TestStateChangingActions:
    def test_interaction_counts_as_a_state_change(self):
        for action in ("click", "type_text", "select", "press_key"):
            assert action in _STATE_CHANGING

    def test_movement_does_not_count(self):
        """Navigating or scrolling moves you; it does not do the task.

        This is the whole point: the failing run had navigated to the right
        page and called that done.
        """
        for action in ("navigate", "scroll", "go_back"):
            assert action not in _STATE_CHANGING


def test_already_done_note_does_not_count_as_pending_action():
    """The fast path hands over "Tell me what it says\\n(Already done, do not
    repeat: clicked 'Remove'; waited 3.0s...)". The verbs in that note were
    already carried out; only the first line decides whether DONE needs an
    action first (2026-11-05, dynamic-controls refused a correct DONE)."""
    head = "Tell me what it says."
    note = "\n(Already done, do not repeat: clicked 'Remove'; waited 3.0s for the page to settle.)"
    assert _requires_state_change((head + note).split("\n(Already done", 1)[0]) is False
    assert _requires_state_change("click Checkout" + note) is True


def test_a_question_naming_a_control_is_not_an_action():
    assert _requires_state_change("Tell me how many Delete buttons there are") is False
    assert _requires_state_change("What does the Remove button say now?") is False
    assert _requires_state_change("Click Remove and wait. Tell me what it says.") is True
    assert _requires_state_change("Add two items then tell me the total") is True
