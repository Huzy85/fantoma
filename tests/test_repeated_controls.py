"""Repeated interactive controls must all survive extraction.

Fantoma used to deduplicate interactive elements by (role, name, state),
keeping only the first. On a six-product grid that deleted five of the six
"Add to cart" buttons before the model ever saw the page, so "add the third
item to the cart" could not be expressed at all. Measured live on saucedemo:
one button was visible, and a second only appeared after the first was used
and stopped being a duplicate.

No major framework dedupes here. Playwright MCP assigns a ref per element by
DOM position; Vercel's agent-browser documents the same case as two separate
entries. Repetition noise is handled by relevance scoring and by capping the
list, not by deleting distinct targets.
"""

from unittest.mock import MagicMock

from fantoma.dom.accessibility import extract_aria


def _page(snapshot):
    page = MagicMock()
    page.title.return_value = "Shop"
    page.url = "https://shop.test/inventory"
    page.locator.return_value.aria_snapshot.return_value = snapshot
    page.evaluate.return_value = None   # no scroll hints in tests
    return page


PRODUCT_GRID = (
    '- link "Sauce Labs Backpack":\n'
    '  - button "Add to cart"\n'
    '- link "Sauce Labs Bike Light":\n'
    '  - button "Add to cart"\n'
    '- link "Sauce Labs Bolt T-Shirt":\n'
    '  - button "Add to cart"\n'
    '- link "Sauce Labs Fleece Jacket":\n'
    '  - button "Add to cart"\n'
)


class TestRepeatedControlsSurvive:
    def test_every_add_to_cart_button_is_listed(self):
        """One per product. Anything fewer makes a specific item unreachable."""
        result = extract_aria(_page(PRODUCT_GRID), max_elements=50)
        assert result.count('button "Add to cart"') == 4

    def test_each_repeated_button_gets_its_own_index(self):
        """Distinct indices are how the model says *which* one."""
        result = extract_aria(_page(PRODUCT_GRID), max_elements=50)
        import re
        indices = re.findall(r'\[(\d+)\]\s+button "Add to cart"', result)
        assert len(indices) == 4
        assert len(set(indices)) == 4, "indices must be unique per control"

    def test_repeated_links_also_survive(self):
        """Search results repeat identical link text pointing at different pages."""
        snapshot = (
            '- link "Read more"\n'
            '- link "Read more"\n'
            '- link "Read more"\n'
        )
        result = extract_aria(_page(snapshot), max_elements=50)
        assert result.count('link "Read more"') == 3

    def test_identical_checkboxes_survive(self):
        """A settings page with several unlabelled toggles is still addressable."""
        snapshot = (
            '- checkbox "Enable"\n'
            '- checkbox "Enable"\n'
        )
        result = extract_aria(_page(snapshot), max_elements=50)
        assert result.count('checkbox "Enable"') == 2


class TestNoiseStillControlled:
    def test_the_element_cap_still_applies(self):
        """Dropping dedup must not let a page flood the prompt — pruning caps it."""
        snapshot = "".join(f'- link "Item {i}"\n' for i in range(60))
        result = extract_aria(_page(snapshot), max_elements=10)
        import re
        assert len(re.findall(r'^\[\d+\] link', result, re.M)) <= 10


class TestAmbiguousControlsGetContext:
    """A bare repeated "Add to cart" gives the model no way to say which one.

    The page should disambiguate with aria-label; a bare repeated control is
    a WCAG 2.4.4 failure. Real pages often do not, so the label is inferred
    from page order — and marked as inferred, because per W3C AccName a
    neighbour does not contribute to an accessible name.
    """

    def test_repeated_buttons_are_labelled_with_their_product(self):
        result = extract_aria(_page(PRODUCT_GRID), max_elements=50)
        assert '(in: Sauce Labs Fleece Jacket)' in result
        assert '(in: Sauce Labs Backpack)' in result

    def test_each_button_gets_its_own_product(self):
        result = extract_aria(_page(PRODUCT_GRID), max_elements=50)
        import re
        pairs = re.findall(r'button "Add to cart" \(in: ([^)]+)\)', result)
        assert len(pairs) == 4
        assert len(set(pairs)) == 4, "each button must name a different product"

    def test_unique_controls_are_left_alone(self):
        """Labelling something already unique is pure noise."""
        snapshot = '- button "Checkout"\n- button "Continue Shopping"\n'
        result = extract_aria(_page(snapshot), max_elements=50)
        assert "(in:" not in result

    def test_context_is_marked_as_inferred(self):
        """The model must be able to tell a guess from what the page states."""
        result = extract_aria(_page(PRODUCT_GRID), max_elements=50)
        assert "(in: " in result, "inferred context uses the 'in:' marker"

    def test_no_context_when_nothing_distinct_precedes(self):
        """Two identical buttons alone: no label to borrow, so claim nothing."""
        snapshot = '- button "Edit"\n- button "Edit"\n'
        result = extract_aria(_page(snapshot), max_elements=50)
        assert result.count('button "Edit"') == 2
        assert "(in:" not in result

    def test_context_is_never_borrowed_from_a_control(self):
        """Measured live: product links picked up "(in: Price (high to low))"
        from a sort dropdown and "(in: Remove)" from the button above them.
        Only a link names a destination worth borrowing."""
        snapshot = (
            '- option "Price (high to low)"\n'
            '- link "Sauce Labs Backpack"\n'
            '- link "Sauce Labs Backpack"\n'
            '- button "Add to cart"\n'
            '- link "Sauce Labs Bike Light"\n'
            '- link "Sauce Labs Bike Light"\n'
            '- button "Add to cart"\n'
        )
        result = extract_aria(_page(snapshot), max_elements=50)
        assert "(in: Price (high to low))" not in result
        assert "(in: Remove)" not in result
        # The useful case still works.
        assert 'button "Add to cart" (in: Sauce Labs Backpack)' in result
        assert 'button "Add to cart" (in: Sauce Labs Bike Light)' in result


class TestTaskRelevanceUsesContext:
    def test_the_button_for_the_named_product_outranks_the_others(self):
        """A button is called "Add to cart" and matches no task keyword, so
        scoring its name alone buries the one control the task is about."""
        from fantoma.dom.accessibility import annotate_ambiguous, prune_elements
        els = [
            {"role": "link", "name": "Sauce Labs Backpack", "state": ""},
            {"role": "button", "name": "Add to cart", "state": ""},
            {"role": "link", "name": "Sauce Labs Fleece Jacket", "state": ""},
            {"role": "button", "name": "Add to cart", "state": ""},
        ]
        annotate_ambiguous(els)
        top = prune_elements(els, task="add the Fleece Jacket to the cart",
                             max_elements=2)
        buttons = [e for e in top if e["role"] == "button"]
        assert buttons, "the relevant button must survive pruning"
        assert buttons[0].get("_context") == "Sauce Labs Fleece Jacket"
