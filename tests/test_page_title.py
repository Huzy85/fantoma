"""page_title() survives a navigation that lands mid-read."""
from fantoma.dom.accessibility import page_title


class _Page:
    url = "https://example.test/results"

    def __init__(self, failures):
        self._failures = failures
        self.waited = 0

    def title(self):
        if self._failures:
            self._failures -= 1
            raise RuntimeError("Execution context was destroyed, most likely because of a navigation")
        return "Results"

    def wait_for_load_state(self, state, timeout=None):
        assert state == "domcontentloaded"
        self.waited += 1


def test_title_is_read_after_the_new_document_lands():
    page = _Page(failures=1)
    assert page_title(page) == "Results"
    assert page.waited == 1


def test_falls_back_to_the_url_when_the_title_never_comes():
    page = _Page(failures=5)
    assert page_title(page) == page.url


def test_a_quiet_page_is_read_at_once():
    page = _Page(failures=0)
    assert page_title(page) == "Results"
    assert page.waited == 0
