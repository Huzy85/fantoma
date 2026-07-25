"""Resolving which repeated control the task actually means.

Measured live: asked to add the 'Sauce Labs Fleece Jacket', the agent clicked
the first "Add to cart" on the page and added the Backpack. The element list
already records which item each control belongs to, so the right one can be
resolved in code rather than hoped for in a prompt.

A wrong correction is worse than none, so every ambiguous case returns None.
"""

import pytest

from fantoma.dom.accessibility import AccessibilityExtractor


def _extractor(elements):
    ex = AccessibilityExtractor()
    ex._last_interactive = elements
    return ex


GRID = [
    {"index": 0, "role": "button", "name": "Add to cart", "_context": "Sauce Labs Backpack"},
    {"index": 1, "role": "button", "name": "Add to cart", "_context": "Sauce Labs Bike Light"},
    {"index": 2, "role": "button", "name": "Add to cart", "_context": "Sauce Labs Fleece Jacket"},
]


class TestRetargeting:
    def test_wrong_choice_is_corrected_to_the_named_item(self):
        """The exact live failure: first button clicked, Fleece Jacket wanted."""
        assert _extractor(GRID).find_index_for_target(0, "Sauce Labs Fleece Jacket") == 2

    def test_correct_choice_is_left_alone(self):
        assert _extractor(GRID).find_index_for_target(2, "Sauce Labs Fleece Jacket") is None

    def test_partial_name_still_matches(self):
        """People say "the Fleece Jacket", not the full catalogue name."""
        assert _extractor(GRID).find_index_for_target(0, "Fleece Jacket") == 2


class TestRefusesToGuess:
    def test_no_target_means_no_correction(self):
        assert _extractor(GRID).find_index_for_target(0, "") is None

    def test_unambiguous_control_is_never_touched(self):
        """A control with its own distinct name needs no help."""
        els = [{"index": 0, "role": "button", "name": "Checkout", "_context": ""}]
        assert _extractor(els).find_index_for_target(0, "Fleece Jacket") is None

    def test_absent_target_is_not_forced_onto_something_else(self):
        assert _extractor(GRID).find_index_for_target(0, "Winter Boots") is None

    def test_two_matching_candidates_are_left_alone(self):
        """Ambiguity must not be resolved by picking one at random."""
        els = GRID + [
            {"index": 3, "role": "button", "name": "Add to cart",
             "_context": "Sauce Labs Fleece Jacket"},
        ]
        assert _extractor(els).find_index_for_target(0, "Fleece Jacket") is None

    def test_only_same_control_type_is_considered(self):
        """Retargeting a click must not jump to a different kind of control."""
        els = [
            {"index": 0, "role": "button", "name": "Add to cart", "_context": "Backpack"},
            {"index": 1, "role": "link", "name": "Add to cart", "_context": "Fleece Jacket"},
        ]
        assert _extractor(els).find_index_for_target(0, "Fleece Jacket") is None

    def test_out_of_range_index_is_safe(self):
        assert _extractor(GRID).find_index_for_target(99, "Fleece Jacket") is None
        assert _extractor([]).find_index_for_target(0, "Fleece Jacket") is None


class TestContextParsedBackFromOutput:
    def test_inferred_label_survives_the_round_trip(self):
        out = '[16] button "Add to cart" (in: Sauce Labs Fleece Jacket)'
        parsed = AccessibilityExtractor._parse_interactive_from_output(out)
        assert parsed[0]["_context"] == "Sauce Labs Fleece Jacket"

    def test_plain_element_has_empty_context(self):
        parsed = AccessibilityExtractor._parse_interactive_from_output('[3] button "Checkout"')
        assert parsed[0]["_context"] == ""
