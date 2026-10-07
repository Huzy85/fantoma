"""The step-by-step session's named tabs.

The Agent rewrite dropped `_Session.tabs` and name lookups, so the CLI's
/tabs command and the weekly monitor's Multi-Tab check both raised
"'_Session' object has no attribute 'tabs'". These pin the contract
without a browser: a fake Fantoma that keeps a list of page URLs.
"""

from unittest.mock import MagicMock

from fantoma.agent import _Session


class _FakeFantoma:
    def __init__(self):
        self.pages = []
        self.current = 0

    def start(self, url):
        self.pages = [url]

    def stop(self):
        self.pages = []

    def list_tabs(self):
        return [{"index": i, "url": u} for i, u in enumerate(self.pages)]

    def get_state(self):
        return {"url": self.pages[self.current]}

    @property
    def _engine(self):
        fake = self

        class _Engine:
            def get_url(self):
                return fake.pages[fake.current]
        return _Engine()

    def new_tab(self, url):
        self.pages.append(url)
        self.current = len(self.pages) - 1
        return {"state": self.get_state()}

    def switch_tab(self, idx):
        assert isinstance(idx, int), "engine only takes indexes"
        self.current = idx
        return {"state": self.get_state()}

    def close_tab(self, idx=None):
        if idx is None:
            idx = self.current
        self.pages.pop(idx)
        self.current = len(self.pages) - 1
        return {"state": self.get_state()}


def _session():
    agent = MagicMock()
    agent.fantoma = _FakeFantoma()
    return _Session(agent, "https://example.com")


def test_main_tab_is_tracked_on_enter():
    with _session() as s:
        assert s.tabs == [{"index": 0, "name": "main", "url": "https://example.com"}]


def test_named_tab_can_be_switched_to_and_closed_by_name():
    with _session() as s:
        idx = s.new_tab("https://github.com/trending", name="github")
        assert idx == 1
        assert [t["name"] for t in s.tabs] == ["main", "github"]

        s.switch_tab("main")
        assert s.agent.fantoma.current == 0
        assert s.url == "https://example.com"

        s.close_tab("github")
        assert s.tabs == [{"index": 0, "name": "main", "url": "https://example.com"}]


def test_closing_a_middle_tab_renumbers_the_rest():
    with _session() as s:
        s.new_tab("https://a.test", name="a")
        s.new_tab("https://b.test", name="b")
        s.close_tab("a")
        assert s.tabs == [
            {"index": 0, "name": "main", "url": "https://example.com"},
            {"index": 1, "name": "b", "url": "https://b.test"},
        ]


def test_unknown_name_is_a_no_op():
    with _session() as s:
        s.new_tab("https://a.test", name="a")
        s.switch_tab("nope")
        assert s.agent.fantoma.current == 1
        s.close_tab("nope")
        assert len(s.tabs) == 2


def test_unnamed_tab_gets_a_default_name():
    with _session() as s:
        s.new_tab("https://a.test")
        assert s.tabs[1]["name"] == "tab-1"
