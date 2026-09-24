"""Ranking, dropdown and role fixes, driven from real ARIA snapshot text.

The snapshots below are what Playwright reports for simple public test pages
(checkboxes, a native dropdown, a pricing page). Each test pins one thing the
model used to be shown wrongly, or not shown at all.
"""

from unittest.mock import MagicMock

from fantoma.dom.accessibility import (
    extract_aria, extract_aria_content, prune_elements, enrich_field_state,
    AccessibilityExtractor, INTERACTIVE_ROLES,
)


def _page(snapshot, url="https://the-internet.example/page", title="Test"):
    page = MagicMock()
    page.title.return_value = title
    page.url = url
    page.locator.return_value.aria_snapshot.return_value = snapshot
    page.evaluate.return_value = None
    return page


CHECKBOXES = """- link "Fork me on GitHub":
  - /url: https://github.com/tourdedave/the-internet
  - img "Fork me on GitHub"
- heading "Checkboxes" [level=3]
- checkbox
- text: checkbox 1
- checkbox [checked]
- text: checkbox 2
- separator
- text: Powered by
- link "Elemental Selenium":
  - /url: http://elementalselenium.com/"""

DROPDOWN = """- link "Fork me on GitHub":
  - /url: https://github.com/x
  - img "Fork me on GitHub"
- heading "Dropdown List" [level=3]
- combobox:
  - option "Please select an option" [disabled] [selected]
  - option "Option 1"
  - option "Option 2"
- text: Powered by
- link "Elemental Selenium":
  - /url: http://e.com/"""


class TestRanking:
    def test_offsite_ribbon_does_not_take_the_top_slot(self):
        out = extract_aria(_page(CHECKBOXES), task="make sure both boxes are ticked")
        first = [l for l in out.splitlines() if l.startswith("[0]")][0]
        assert "Fork me" not in first

    def test_offsite_link_still_wins_when_the_task_names_it(self):
        els = [
            {"role": "checkbox", "name": "", "_landmark": None},
            {"role": "link", "name": "Fork me on GitHub", "_landmark": None,
             "_url": "https://github.com/x"},
        ]
        top = prune_elements(els, task="open the github fork link", max_elements=1,
                             page_url="https://site.example/")
        assert top[0]["name"] == "Fork me on GitHub"

    def test_same_site_links_are_not_penalised(self):
        els = [
            {"role": "link", "name": "Pricing", "_landmark": None, "_url": "/pricing"},
            {"role": "link", "name": "Elsewhere", "_landmark": None,
             "_url": "https://other.example/"},
        ]
        top = prune_elements(els, task="", max_elements=1, page_url="https://site.example/")
        assert top[0]["name"] == "Pricing"

    def test_footer_links_rank_below_content_controls(self):
        els = [
            {"role": "link", "name": "About", "_landmark": "contentinfo"},
            {"role": "button", "name": "Buy", "_landmark": "main"},
        ]
        top = prune_elements(els, task="", max_elements=1)
        assert top[0]["name"] == "Buy"

    def test_ranks_even_without_a_task_when_the_list_is_capped(self):
        snap = "\n".join(f'- link "Nav {i}":\n  - /url: https://cdn.example/{i}'
                         for i in range(25)) + '\n- textbox "Search"'
        out = extract_aria(_page(snap), task="", max_elements=5)
        assert 'textbox "Search"' in out


class TestDropdowns:
    def test_selected_state_survives_alongside_disabled(self):
        el = {"raw": {"disabled": True, "selected": True}}
        assert enrich_field_state(el) == " [disabled, selected]"

    def test_dropdown_reports_its_current_choice(self):
        out = extract_aria(_page(DROPDOWN), task="select Option 2")
        assert 'combobox "" (selected: "Please select an option")' in out

    def test_options_follow_their_dropdown(self):
        out = extract_aria(_page(DROPDOWN), task="select Option 2")
        lines = [l for l in out.splitlines() if l.startswith("[")]
        roles = [l.split("]")[1].split()[0] for l in lines]
        combo = roles.index("combobox")
        assert roles[combo + 1:combo + 4] == ["option", "option", "option"]

    def test_shown_elements_keep_select_membership(self):
        shown = []
        extract_aria(_page(DROPDOWN), task="select Option 2", _shown_out=shown)
        combo = next(el for el in shown if el["role"] == "combobox")
        opts = [el for el in shown if el["role"] == "option"]
        assert all(o["_in_select"] == combo["_select_id"] for o in opts)


class TestRoles:
    def test_tree_and_checkable_menu_items_are_actionable(self):
        assert {"treeitem", "menuitemcheckbox", "menuitemradio"} <= INTERACTIVE_ROLES
        snap = '- treeitem "src"\n- menuitemcheckbox "Word wrap" [checked]'
        out = extract_aria(_page(snap), task="")
        assert 'treeitem "src"' in out
        assert 'menuitemcheckbox "Word wrap" [checked]' in out


class TestResolution:
    def test_exact_name_is_tried_before_substring(self):
        ext = AccessibilityExtractor()
        ext._last_interactive = [{"role": "button", "name": "Add", "_ordinal": 0}]
        page = MagicMock()
        exact = MagicMock()
        exact.count.return_value = 1
        page.get_by_role.return_value = exact
        ext.get_element_by_index(page, 0)
        page.get_by_role.assert_called_with("button", name="Add", exact=True)


PRICING = """- navigation:
  - link "Home":
    - /url: /
- main:
  - heading "Pricing" [level=1]
  - paragraph: Our basic plan costs $10 per month and includes 5 users.
  - paragraph:
    - text: The pro plan costs
    - strong: $25
    - text: per month.
  - list:
    - listitem: Email support
    - listitem: Phone support on pro
  - table:
    - rowgroup:
      - row "Plan Price":
        - columnheader "Plan"
        - columnheader "Price"
      - row "Basic $10":
        - cell "Basic"
        - cell "$10"
  - link "Start your free trial today":
    - /url: /signup
- contentinfo: "Contact: sales@widget.example\""""


class TestContentMode:
    def test_paragraphs_lists_and_tables_are_kept(self):
        out = extract_aria_content(_page(PRICING))
        assert "Our basic plan costs $10 per month" in out
        assert "$25" in out
        assert "Email support" in out
        assert "Basic $10" in out
        assert "sales@widget.example" in out
