"""Bounded reads, the per-step snapshot, and read counting. No browser needed."""

import time
from unittest.mock import MagicMock

import pytest

from fantoma.browser.page_state import classify_blocker
from fantoma.dom import page_reads
from fantoma.dom.accessibility import (AccessibilityExtractor, extract_aria,
                                       get_scroll_info)
from fantoma.dom.aria_diff import aria_snapshot
from fantoma.dom.page_reads import PageSnapshot, ReadTimeout, counters, safe_evaluate

ARIA = """- banner:
  - link "Home"
  - navigation "Main":
    - link "Products"
    - link "Cart"
- main:
  - heading "Sign in" [level=1]
  - paragraph: Welcome back to the shop.
  - form "Login":
    - textbox "Username"
    - textbox "Password"
    - checkbox "Remember me"
    - button "Login"
  - button "Add to cart"
  - button "Add to cart"
- contentinfo:
  - link "Privacy"
"""


class FakePage:
    """Counts reads like Playwright would; answers evaluates harmlessly."""

    def __init__(self, aria=ARIA, url="https://shop.test/login"):
        self.url = url
        self.aria = aria
        self.frames = []
        self.main_frame = object()
        self.aria_calls = 0
        self.eval_calls = 0
        loc = MagicMock()
        loc.aria_snapshot.side_effect = self._aria_snapshot
        self.locator = MagicMock(return_value=loc)

    def _aria_snapshot(self, **kw):
        self.aria_calls += 1
        return self.aria

    def title(self):
        return "Shop"

    def evaluate(self, js, arg=None):
        self.eval_calls += 1
        if isinstance(arg, list):
            return [True] * len(arg)
        return None


class BlockingPage(FakePage):
    def evaluate(self, js, arg=None):
        time.sleep(5)


@pytest.fixture(autouse=True)
def short_timeout(monkeypatch):
    monkeypatch.setenv("FANTOMA_READ_TIMEOUT", "0.3")
    page_reads.reset_stuck()
    yield
    page_reads.reset_stuck()


# -- hang fix -------------------------------------------------------------

def test_blocking_evaluate_returns_degraded_result_and_logs(caplog):
    page = BlockingPage()
    t0 = time.monotonic()
    with caplog.at_level("WARNING", logger="fantoma.page_reads"):
        with pytest.raises(ReadTimeout):
            safe_evaluate(page, "() => 1", label="probe")
    assert time.monotonic() - t0 < 2
    assert "timed out" in caplog.text


def test_read_helpers_degrade_instead_of_blocking():
    page = BlockingPage()
    t0 = time.monotonic()
    assert get_scroll_info(page) is None
    page_reads.reset_stuck()
    assert classify_blocker(page) is None
    assert time.monotonic() - t0 < 2


def test_blocking_aria_snapshot_gives_empty_result():
    page = FakePage()

    def hang(**kw):
        time.sleep(5)

    page.locator.return_value.aria_snapshot.side_effect = hang
    t0 = time.monotonic()
    assert aria_snapshot(page) == {}
    page_reads.reset_stuck()
    assert extract_aria(page) == ""
    assert time.monotonic() - t0 < 2


def test_after_one_timeout_rest_of_step_fails_fast():
    page = BlockingPage()
    with pytest.raises(ReadTimeout):
        safe_evaluate(page, "() => 1")
    t0 = time.monotonic()
    with pytest.raises(ReadTimeout):
        safe_evaluate(page, "() => 2")
    assert time.monotonic() - t0 < 0.1
    page_reads.reset_stuck()  # a new step gets a fresh chance


def test_timer_and_handler_restored_after_read():
    import signal
    before = signal.getsignal(signal.SIGALRM)
    safe_evaluate(FakePage(), "() => 1")
    assert signal.getsignal(signal.SIGALRM) == before
    assert signal.getitimer(signal.ITIMER_REAL)[0] == 0


# -- cached path equals uncached path ---------------------------------------

def _run(use_cache):
    page = FakePage()
    ex = AccessibilityExtractor()
    if use_cache:
        ex.begin_step(page)
    before = page.aria_calls
    elements_text = ex.extract(page, task="log in")
    content = ex.extract_content(page)
    diff_map = aria_snapshot(page, ex._snap_for(page))
    return page.aria_calls - before, elements_text, content, diff_map, ex._last_interactive


def test_cached_path_matches_uncached_exactly():
    reads_u, text_u, content_u, map_u, els_u = _run(False)
    reads_c, text_c, content_c, map_c, els_c = _run(True)
    assert text_c == text_u
    assert content_c == content_u
    assert map_c == map_u
    assert els_c == els_u
    assert "Login" in text_c and "[0]" in text_c
    assert reads_u == 3 and reads_c == 1


def test_diff_and_stagnation_inputs_identical():
    page = FakePage()
    ex = AccessibilityExtractor()
    snap = ex.begin_step(page)
    cached = aria_snapshot(page, snap)
    assert cached == aria_snapshot(page)
    assert ex.extract_content(page)[:800] == AccessibilityExtractor().extract_content(page)[:800]


def test_invalidate_forces_a_new_read():
    page = FakePage()
    ex = AccessibilityExtractor()
    ex.begin_step(page)
    ex.extract(page)
    ex.extract_content(page)
    assert page.aria_calls == 1
    page.aria = ARIA.replace("Welcome back", "Goodbye")
    ex.extract_content(page)
    assert page.aria_calls == 1          # still the cached view
    ex.invalidate()
    assert "Goodbye" in ex.extract_content(page)
    assert page.aria_calls == 2


def test_url_change_invalidates_silently():
    page = FakePage()
    ex = AccessibilityExtractor()
    ex.begin_step(page)
    ex.extract_content(page)
    page.url = "https://shop.test/cart"
    assert ex._snap_for(page) is None    # falls back to a live read


def test_no_cache_outside_a_step():
    page = FakePage()
    ex = AccessibilityExtractor()
    ex.extract(page)
    ex.extract(page)
    assert page.aria_calls == 2
    ex.begin_step(page)
    ex.end_step()
    ex.extract(page)
    assert page.aria_calls == 3


def test_failed_read_is_not_retried_within_a_snapshot():
    page = FakePage()
    page.locator.return_value.aria_snapshot.side_effect = RuntimeError("boom")
    snap = PageSnapshot(page)
    for _ in range(3):
        with pytest.raises(RuntimeError):
            snap.aria()
    assert page.locator.return_value.aria_snapshot.call_count == 1


def test_counters_count_reads_and_evals():
    page = FakePage()
    c0 = counters()
    safe_evaluate(page, "() => 1")
    PageSnapshot(page).aria()
    c1 = counters()
    assert c1["evals"] - c0["evals"] == 1
    assert c1["reads"] - c0["reads"] == 1


def test_one_batched_evaluate_for_occlusion():
    page = FakePage()
    ex = AccessibilityExtractor()
    els = [{"role": "button", "name": f"B{i}"} for i in range(10)]
    ex._filter_occluded(page, els)
    assert page.eval_calls == 1
