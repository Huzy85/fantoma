"""Tests for structured task parsing and outcome verification.

The failure this exists to stop, measured live: asked to add the 'Sauce Labs
Fleece Jacket' to the cart, the agent added the first product on the page and
returned success=True. Nothing in the system could tell, because the only
evidence was the agent's own description of its work.
"""

import pytest

from fantoma.task_spec import TaskSpec, parse_task, verify_outcome


class TestActionParsing:
    @pytest.mark.parametrize("task,expected", [
        ("Add the 'Fleece Jacket' to the cart", "add_to_cart"),
        ("Log in with username 'bob'", "login"),
        ("Sign up for an account", "register"),
        ("Search for 'wireless headphones'", "search"),
        ("Click the 'Continue' button", "click"),
        ("What is the main heading?", "extract"),
        ("Tell me the top story", "extract"),
    ])
    def test_action_is_recognised(self, task, expected):
        assert parse_task(task).action == expected

    def test_specific_actions_beat_generic_ones(self):
        """"add to cart" must not be read as a bare "add"."""
        assert parse_task("Add 'X' to the cart").action == "add_to_cart"

    def test_unknown_task_parses_without_an_action(self):
        """An unparsed task must degrade, never raise."""
        spec = parse_task("mumble something incomprehensible")
        assert spec.action == ""
        assert spec.target == ""


class TestTargetExtraction:
    def test_quoted_item_becomes_the_target(self):
        spec = parse_task("Add the 'Sauce Labs Fleece Jacket' to the cart")
        assert spec.target == "Sauce Labs Fleece Jacket"
        assert spec.confident is True

    def test_credentials_are_values_not_targets(self):
        """A password is an input to type, never the thing being acted on."""
        spec = parse_task(
            "Log in with username 'standard_user' and password 'secret_sauce'")
        assert spec.values["username"] == "standard_user"
        assert spec.values["password"] == "secret_sauce"
        assert spec.target == "", "credentials must not be mistaken for a target"

    def test_target_survives_a_task_that_also_has_credentials(self):
        """The exact live failure: login and add in one sentence."""
        spec = parse_task(
            "Log in with username 'standard_user' and password 'secret_sauce', "
            "then add the 'Sauce Labs Fleece Jacket' to the cart")
        assert spec.target == "Sauce Labs Fleece Jacket"
        assert spec.values["username"] == "standard_user"

    def test_double_and_curly_quotes_work(self):
        assert parse_task('Add "Blue Shirt" to the cart').target == "Blue Shirt"
        assert parse_task("Add ‘Red Hat’ to the cart").target == "Red Hat"

    def test_empty_task_is_safe(self):
        spec = parse_task("")
        assert spec.target == "" and spec.action == ""


class TestLLMFallback:
    def test_llm_is_only_asked_when_code_finds_nothing(self):
        calls = []

        class LLM:
            def chat(self, *a, **k):
                calls.append(1)
                return "Fleece Jacket"

        parse_task("Add the 'Fleece Jacket' to the cart", llm=LLM())
        assert not calls, "quoted target needs no model call"

    def test_llm_target_is_used_when_no_quotes_exist(self):
        class LLM:
            def chat(self, *a, **k):
                return "Fleece Jacket"

        spec = parse_task("Add the Fleece Jacket to the cart", llm=LLM())
        assert spec.target == "Fleece Jacket"

    def test_invented_targets_are_rejected(self):
        """A model must not be able to substitute a product nobody asked for."""
        class LLM:
            def chat(self, *a, **k):
                return "Gold Plated Widget"

        spec = parse_task("Add the Fleece Jacket to the cart", llm=LLM())
        assert spec.target == "", "target absent from the task must be ignored"

    def test_llm_failure_does_not_break_parsing(self):
        class LLM:
            def chat(self, *a, **k):
                raise RuntimeError("model down")

        assert parse_task("Add the Fleece Jacket to the cart", llm=LLM()).target == ""


class TestOutcomeVerification:
    def test_wrong_item_in_the_cart_fails(self):
        """The live failure this whole module exists to catch."""
        spec = parse_task("Add the 'Sauce Labs Fleece Jacket' to the cart")
        page = ('[5] link "Sauce Labs Backpack"\n'
                '[7] button "Remove"\n'
                '[16] button "Add to cart" (in: Sauce Labs Fleece Jacket)')
        ok, reason = verify_outcome(spec, "https://shop/inventory", page)
        assert ok is False
        assert "Fleece Jacket" in reason

    def test_right_item_in_the_cart_passes(self):
        spec = parse_task("Add the 'Sauce Labs Fleece Jacket' to the cart")
        page = ('[14] link "Sauce Labs Fleece Jacket"\n'
                '[16] button "Remove"')
        ok, _ = verify_outcome(spec, "https://shop/inventory", page)
        assert ok is True

    def test_login_verified_by_where_it_landed(self):
        spec = parse_task("Log in with username 'bob' and password 'hunter2'")
        ok, _ = verify_outcome(spec, "https://site/secure", "Welcome")
        assert ok is True
        ok, _ = verify_outcome(spec, "https://site/login", "Wrong password")
        assert ok is False

    def test_unparsed_task_never_fails_a_good_run(self):
        """Verification must not invent failures it cannot judge."""
        spec = TaskSpec(raw="something odd")
        ok, reason = verify_outcome(spec, "https://x", "anything")
        assert ok is True and "no target" in reason
