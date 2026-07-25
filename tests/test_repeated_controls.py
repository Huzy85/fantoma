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
