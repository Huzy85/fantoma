"""Stable element numbers: identity survives reorders, refuses when unsure.

A small fake page holds the live elements. The ARIA text the extractor reads
and the role locators the resolver uses are both built from the same list, so
changing the list is changing the page.
"""

import re
from types import SimpleNamespace

import pytest

from fantoma.dom.accessibility import AccessibilityExtractor
from fantoma.dom.refs import RefRegistry
from fantoma.navigator import Navigator


class El:
    def __init__(self, role, name, href=None, context=None):
        self.role, self.name, self.href, self.context = role, name, href, context

    def get_attribute(self, attr):
        return self.href if attr == "href" else None

    def evaluate(self, js, arg=None):
        """Stands in for the in-page item-label check."""
        return self.context == arg

    def element_handle(self):
        return self

    def __repr__(self):
        return f"{self.role}:{self.name}"


class Loc:
    def __init__(self, els):
        self.els = els
        self.count = lambda: len(els)
        self.first = els[0] if els else None

    def nth(self, i):
        return self.els[i]


class Page:
    def __init__(self, els, url="https://shop.test/list"):
        self.els, self.url = list(els), url
        self.frames = []
        self.main_frame = object()
        self.locator_calls = 0

    # what the extractor reads
    def locator(self, sel):
        page = self

        class Body:
            def aria_snapshot(self, **kw):
                lines = ["- main:"]
                for e in page.els:
                    lines.append(f'  - {e.role} "{e.name}"' + (":" if e.href else ""))
                    if e.href:
                        lines.append(f"    - /url: {e.href}")
                return "\n".join(lines)
        return Body()

    def title(self):
        return "Test"

    def evaluate(self, js, arg=None):
        return [True] * len(arg) if isinstance(arg, list) else None

    # what the resolver uses
    def get_by_role(self, role, name=None, exact=False):
        def ok(e):
            if e.role != role:
                return False
            if isinstance(name, re.Pattern):
                return bool(name.match(e.name))
            if name is None:
                return True
            return e.name == name if exact else name.lower() in e.name.lower()
        return Loc([e for e in self.els if ok(e)])

    def query_selector_all(self, sel):
        return []

    def get_by_text(self, text, exact=False):
        return Loc([])


def read(ex, page):
    ex.extract(page, task="")
    return {(e["role"], e["name"], e.get("_ordinal", 0)): e["index"]
            for e in ex._last_interactive}


def btn(name):
    return El("button", name)


# (a) the page reorders between steps, [N] still hits the same element

def test_reorder_between_reads_keeps_number_on_same_element():
    a, b, c = btn("Alpha"), btn("Beta"), btn("Gamma")
    page, ex = Page([a, b, c]), AccessibilityExtractor()
    nums = read(ex, page)
    beta = nums[("button", "Beta", 0)]
    page.els = [c, a, b]
    assert ex.get_element_by_index(page, beta) is b          # no re-read yet
    nums2 = read(ex, page)
    assert nums2[("button", "Beta", 0)] == beta
    assert ex.get_element_by_index(page, beta) is b


def test_reorder_of_look_alikes_follows_their_labels():
    # Three "Add" buttons, told apart by the product each belongs to.
    items = [El("link", "Red hat", "/red"), El("link", "Blue hat", "/blue")]
    a1 = El("button", "Add to cart", context="Red hat")
    a2 = El("button", "Add to cart", context="Blue hat")
    ex, page = AccessibilityExtractor(), Page([items[0], a1, items[1], a2])
    read(ex, page)
    blue = next(e["index"] for e in ex._last_interactive
                if e["name"] == "Add to cart" and e.get("_context") == "Blue hat")
    page.els = [items[1], a2, items[0], a1]
    read(ex, page)
    again = next(e["index"] for e in ex._last_interactive
                 if e["name"] == "Add to cart" and e.get("_context") == "Blue hat")
    assert again == blue
    assert ex.get_element_by_index(page, blue) is a2


def test_inserted_item_does_not_steal_a_number():
    a, b = btn("Alpha"), btn("Beta")
    page, ex = Page([a, b]), AccessibilityExtractor()
    nums = read(ex, page)
    new = btn("Newcomer")
    page.els = [new, a, b]
    nums2 = read(ex, page)
    assert nums2[("button", "Alpha", 0)] == nums[("button", "Alpha", 0)]
    assert nums2[("button", "Beta", 0)] == nums[("button", "Beta", 0)]
    assert nums2[("button", "Newcomer", 0)] not in nums.values()


# (b) element removed -> clear refusal

def test_removed_element_is_refused_with_clear_message():
    a, b = btn("Alpha"), btn("Beta")
    page, ex = Page([a, b]), AccessibilityExtractor()
    beta = read(ex, page)[("button", "Beta", 0)]
    page.els = [a]
    assert ex.get_element_by_index(page, beta) is None
    assert ex.miss_message(beta) == f"element [{beta}] is gone, re-read the page"


def test_number_on_another_page_is_gone():
    a = btn("Alpha")
    page, ex = Page([a]), AccessibilityExtractor()
    n = read(ex, page)[("button", "Alpha", 0)]
    page.url = "https://shop.test/other"
    assert ex.get_element_by_index(page, n) is None


def test_query_string_change_is_not_a_new_page():
    a = btn("Alpha")
    page, ex = Page([a]), AccessibilityExtractor()
    n = read(ex, page)[("button", "Alpha", 0)]
    page.url = "https://shop.test/list?utm=1#top"
    assert ex.get_element_by_index(page, n) is a


# (c) two identical elements after the change -> refusal, no guess

@pytest.mark.parametrize("reread", [False, True])
def test_second_identical_element_means_refusal(reread):
    save = btn("Save")
    page, ex = Page([save]), AccessibilityExtractor()
    n = read(ex, page)[("button", "Save", 0)]
    page.els = [btn("Save"), save]
    if reread:
        read(ex, page)
    assert ex.get_element_by_index(page, n) is None
    assert "several identical" in ex.miss_message(n)
    assert "re-read the page" in ex.miss_message(n)


def test_look_alike_removed_leaves_one_match_which_is_used():
    s1, s2 = btn("Save"), btn("Save")
    page, ex = Page([s1, s2]), AccessibilityExtractor()
    nums = read(ex, page)
    page.els = [s2]
    assert ex.get_element_by_index(page, nums[("button", "Save", 1)]) is s2


def test_link_target_picks_among_changed_look_alikes():
    l1, l2 = El("link", "More", "/a"), El("link", "More", "/b")
    page, ex = Page([l1, l2]), AccessibilityExtractor()
    nums = read(ex, page)
    page.els = [El("link", "More", "/c"), l1, l2]
    for e in page.els:
        e.get_attribute = (lambda k, e=e: e.href)
    assert ex.get_element_by_index(page, nums[("link", "More", 1)]) is l2


# (d) queued actions: valid ones kept, stale ones dropped

def test_queue_keeps_resolvable_and_drops_stale():
    a, b, c = btn("Alpha"), btn("Beta"), btn("Gamma")
    page, ex = Page([a, b, c]), AccessibilityExtractor()
    nums = read(ex, page)
    fantoma = SimpleNamespace(_dom=ex, _engine=SimpleNamespace(get_page=lambda: page))
    page.els = [c, a]                       # Beta gone, rest reordered
    queued = [
        (1, ("click", {"element_id": nums[("button", "Alpha", 0)]})),
        (2, ("click", {"element_id": nums[("button", "Beta", 0)]})),
        (3, ("type_text", {"element_id": nums[("button", "Gamma", 0)], "text": "x"})),
        (4, ("scroll", {"direction": "down"})),
    ]
    stale = set()
    Navigator._screen_queue(fantoma, queued, stale, "click", page.url)
    assert stale == {2}


def test_queue_all_dropped_when_page_moved():
    a = btn("Alpha")
    page, ex = Page([a]), AccessibilityExtractor()
    n = read(ex, page)[("button", "Alpha", 0)]
    fantoma = SimpleNamespace(_dom=ex, _engine=SimpleNamespace(get_page=lambda: page))
    start = page.url
    page.url = "https://shop.test/next"
    stale = set()
    Navigator._screen_queue(fantoma, [(1, ("click", {"element_id": n})),
                                      (2, ("scroll", {"direction": "down"}))],
                            stale, "click", start)
    assert stale == {1, 2}


def test_queue_drop_is_logged(caplog):
    a = btn("Alpha")
    page, ex = Page([a]), AccessibilityExtractor()
    n = read(ex, page)[("button", "Alpha", 0)]
    fantoma = SimpleNamespace(_dom=ex, _engine=SimpleNamespace(get_page=lambda: page))
    page.els = []
    with caplog.at_level("INFO", logger="fantoma.navigator"):
        Navigator._screen_queue(fantoma, [(1, ("click", {"element_id": n}))],
                                set(), "click", page.url)
    assert f"click[{n}]" in caplog.text and "dropping 1" in caplog.text


# (e) numbers are stable across reads

def test_numbers_stable_new_get_new_gone_never_reused():
    a, b, c = btn("Alpha"), btn("Beta"), btn("Gamma")
    page, ex = Page([a, b, c]), AccessibilityExtractor()
    first = read(ex, page)
    assert sorted(first.values()) == [0, 1, 2]       # small, from zero
    assert read(ex, page) == first                   # same page, same numbers
    page.els = [a, c]
    assert read(ex, page)[("button", "Gamma", 0)] == first[("button", "Gamma", 0)]
    d = btn("Delta")
    page.els = [a, c, d]
    third = read(ex, page)
    assert third[("button", "Delta", 0)] == 3        # not Beta's old number
    assert first[("button", "Beta", 0)] not in third.values()


def test_prompt_format_unchanged():
    page, ex = Page([btn("Alpha"), btn("Beta")]), AccessibilityExtractor()
    out = ex.extract(page, task="")
    assert re.search(r'^\[0\] button "Alpha"$', out, re.M)
    assert re.search(r'^\[1\] button "Beta"$', out, re.M)


def test_reset_starts_numbering_again():
    page, ex = Page([btn("Alpha")]), AccessibilityExtractor()
    read(ex, page)
    page.els = [btn("Beta")]
    assert read(ex, page)[("button", "Beta", 0)] == 1
    ex.reset_refs()
    assert read(ex, page)[("button", "Beta", 0)] == 0


def test_registry_ambiguous_group_gets_new_numbers():
    reg = RefRegistry()
    one = [{"role": "button", "name": "Save", "_ordinal": 0, "_same": 1}]
    reg.assign(one)
    two = [{"role": "button", "name": "Save", "_ordinal": 0, "_same": 2},
           {"role": "button", "name": "Save", "_ordinal": 1, "_same": 2}]
    reg.assign(two)
    assert sorted(e["index"] for e in two) == [1, 2]


# -- audit: look-alikes and same-length reorders --------------------------------

def _ref(role, name, **kw):
    from fantoma.dom.refs import Ref
    return Ref(0, role, name, **kw)


def test_lookalike_name_is_not_accepted_when_the_original_is_gone():
    from fantoma.dom.refs import GONE, resolve_ref
    page = Page([btn("Undo delete")])
    loc, status = resolve_ref(page, _ref("button", "Delete", ordinal=0, count=1), re.compile("^$"))
    assert loc is None and status == GONE


def test_case_and_whitespace_differences_still_resolve():
    from fantoma.dom.refs import resolve_ref
    e = btn("  save   Draft ")
    loc, status = resolve_ref(Page([e]), _ref("button", "Save draft", ordinal=0, count=1),
                              re.compile("^$"))
    assert status is None and loc is e


def test_same_length_reorder_of_rows_does_not_hit_the_wrong_row():
    from fantoma.dom.refs import resolve_ref
    rows = [El("button", "Remove", context=f"Item {i}") for i in range(5)]
    page = Page(rows)
    ref = _ref("button", "Remove", ordinal=1, count=5, context="Item 1")
    page.els = [rows[0], rows[2], rows[1], rows[3], rows[4]]   # rows 1 and 2 swapped
    loc, status = resolve_ref(page, ref, re.compile("^$"))
    assert status is None and loc is rows[1]


def test_reorder_with_context_that_vanished_is_refused():
    from fantoma.dom.refs import GONE, resolve_ref
    rows = [El("button", "Remove", context=f"Item {i}") for i in range(5)]
    ref = _ref("button", "Remove", ordinal=1, count=5, context="Item 1")
    loc, status = resolve_ref(Page([rows[0], rows[2], rows[3], rows[4], El("button", "Remove", context="X")]),
                              ref, re.compile("^$"))
    assert loc is None and status == GONE


def test_same_length_reorder_by_link_target():
    from fantoma.dom.refs import resolve_ref
    rows = [El("link", "Remove", href=f"/r/{i}") for i in range(3)]
    ref = _ref("link", "Remove", ordinal=0, count=3, href="/r/0")
    page = Page([rows[2], rows[1], rows[0]])
    loc, status = resolve_ref(page, ref, re.compile("^$"))
    assert status is None and loc is rows[0]


class TestContextBesideTheControl:
    """The snapshot ran "checkbox 2" into the footer's "Powered by", so the
    stored label never matched the live row and the box was "gone"
    (herokuapp checkboxes, 0/9 across three models, 2026-11-06)."""

    HTML = """<h3>Checkboxes</h3><form id="checkboxes">
    <input type="checkbox"> checkbox 1<br>
    <input type="checkbox" checked> checkbox 2
    </form><div>Powered by <a href="#">Elemental</a></div>"""

    def _page(self):
        import os
        sync_api = pytest.importorskip("playwright.sync_api")
        exe = os.environ.get("FANTOMA_TEST_CHROMIUM") or "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"
        try:
            pw = sync_api.sync_playwright().start()
            browser = pw.chromium.launch(**({"executable_path": exe} if os.path.exists(exe) else {}))
        except Exception as e:
            pytest.skip(f"no Chromium: {e}")
        page = browser.new_page()
        page.set_content(self.HTML)
        return pw, browser, page

    def test_label_run_into_the_next_block_still_resolves(self):
        from fantoma.dom.refs import _CONTEXT_JS
        pw, browser, page = self._page()
        try:
            boxes = page.locator("input[type=checkbox]")
            assert boxes.nth(1).evaluate(_CONTEXT_JS, "checkbox 2 Powered by") is True
            assert boxes.nth(0).evaluate(_CONTEXT_JS, "checkbox 2 Powered by") is False
            assert boxes.nth(0).evaluate(_CONTEXT_JS, "checkbox 1") is True
        finally:
            browser.close()
            pw.stop()
