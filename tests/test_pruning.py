"""Tests for smart element pruning — relevance-based scoring."""
import pytest


def _el(role, name):
    return {"role": role, "name": name, "state": "", "raw": {}}


class TestPruneElements:
    def test_keyword_match_scores_higher(self):
        from fantoma.dom.accessibility import prune_elements
        elements = [
            _el("link", "About Us"),
            _el("textbox", "Email address"),
            _el("button", "Subscribe"),
        ]
        result = prune_elements(elements, task="enter email address and subscribe", max_elements=2)
        names = [e["name"] for e in result]
        assert "Email address" in names
        assert "Subscribe" in names
        assert "About Us" not in names

    def test_form_inputs_score_higher(self):
        from fantoma.dom.accessibility import prune_elements
        elements = [
            _el("link", "Home"),
            _el("textbox", "Search"),
            _el("link", "Contact"),
        ]
        result = prune_elements(elements, task="find information", max_elements=2)
        names = [e["name"] for e in result]
        assert "Search" in names

    def test_nav_noise_penalized(self):
        from fantoma.dom.accessibility import prune_elements
        elements = [
            _el("button", "Notifications"),
            _el("button", "Settings"),
            _el("button", "Submit Order"),
        ]
        result = prune_elements(elements, task="submit the order", max_elements=1)
        assert result[0]["name"] == "Submit Order"

    def test_submit_patterns_boosted(self):
        from fantoma.dom.accessibility import prune_elements
        elements = [
            _el("link", "Privacy Policy"),
            _el("button", "Sign In"),
            _el("link", "Terms"),
        ]
        result = prune_elements(elements, task="log into the website", max_elements=1)
        assert result[0]["name"] == "Sign In"

    def test_respects_max_elements(self):
        from fantoma.dom.accessibility import prune_elements
        elements = [_el("link", f"Link {i}") for i in range(20)]
        result = prune_elements(elements, task="anything", max_elements=5)
        assert len(result) == 5

    def test_empty_task_returns_all_up_to_max(self):
        from fantoma.dom.accessibility import prune_elements
        elements = [_el("textbox", "Name"), _el("button", "Submit")]
        result = prune_elements(elements, task="", max_elements=15)
        assert len(result) == 2

    def test_reindexes_from_zero(self):
        from fantoma.dom.accessibility import prune_elements
        elements = [
            _el("link", "Irrelevant"),
            _el("textbox", "Email"),
            _el("button", "Login"),
        ]
        result = prune_elements(elements, task="login with email", max_elements=2)
        assert len(result) == 2
