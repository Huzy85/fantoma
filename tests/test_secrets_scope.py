"""Secrets tied to their site (fantoma.secrets_scope)."""

import logging
from unittest.mock import MagicMock, PropertyMock, patch

import pytest

from fantoma.agent import Agent
from fantoma.fast_path import run_fast_path
from fantoma.navigator import Navigator
from fantoma.planner import Subtask
from fantoma.secrets_scope import (
    SecretStore, match_url_with_domain_pattern as match, refusal_message,
)
from fantoma.state_tracker import StateTracker

VALUE = "Zq9-hunter2-Xv"


class TestMatcher:
    @pytest.mark.parametrize("pattern,url,expected", [
        ("example.com", "https://example.com/login", True),
        ("example.com", "https://EXAMPLE.com/", True),
        ("example.com", "https://www.example.com/", False),
        ("*.example.com", "https://example.com/", True),
        ("*.example.com", "https://a.b.example.com/x", True),
        ("https://*.example.com", "https://app.example.com", True),
        ("example.com", "https://other.com/", False),
        ("example.com", "https://notexample.com/", False),
        ("*.example.com", "https://notexample.com/", False),
        ("github.com", "https://github.com.evil.test/", False),
        ("github.com", "https://evil.test/github.com", False),
        ("github.com", "https://github.com@evil.test/", False),
        ("*.github.com", "https://github.com.evil.test/", False),
        ("example.com", "http://example.com/", False),
        ("http://example.com", "http://example.com/", True),
        ("http://example.com", "https://example.com/", False),
        ("http*://example.com", "http://example.com/", True),
        ("http*://example.com", "https://example.com/", True),
        ("http*://example.com", "ftp://example.com/", False),
        ("example.com", "https://example.com:8443/", False),
        ("example.com:8443", "https://example.com:8443/", True),
        ("example.com", "https://example.com:443/", True),
        ("http://127.0.0.1:8000", "http://127.0.0.1:8000/a", True),
        ("http://127.0.0.1:8000", "http://localhost:8000/a", False),
        ("http://127.0.0.1:8000", "http://127.0.0.1:8001/a", False),
        ("bücher.example", "https://xn--bcher-kva.example/", True),
        ("xn--bcher-kva.example", "https://bücher.example/", True),
        ("bücher.example", "https://bucher.example/", False),
        ("example.com", "about:blank", False),
        ("example.com", "", False),
        ("example.com", "https://example.com./", True),
    ])
    def test_table(self, pattern, url, expected):
        assert match(url, pattern) is expected

    @pytest.mark.parametrize("pattern", [
        "*.*.example.com",
        "example.*",
        "*.com",
        "*google.com",
        "goo*gle.com",
        "*",
        "",
        "ftp://example.com",
        "https://user:pw@example.com",
        "*.127.0.0.1",
    ])
    def test_invalid_patterns(self, pattern):
        with pytest.raises(ValueError):
            match("https://example.com", pattern)

    def test_agent_rejects_bad_pattern_at_construction(self):
        with pytest.raises(ValueError):
            Agent(sensitive_data={"*google.com": {"pw": "x"}})


class TestStore:
    def test_flat_format_works_everywhere(self):
        s = SecretStore.from_any({"pw": VALUE})
        assert s.reveal("<secret:pw>", "https://anything.test/")[0] == VALUE
        assert s.names_for("https://anything.test/") == ["pw"]
        assert s.mask(f"x {VALUE} y") == "x <secret:pw> y"

    def test_scoped_format_only_on_its_site(self):
        s = SecretStore.from_any({"https://*.example.com": {"pw": VALUE}})
        assert s.reveal("<secret:pw>", "https://app.example.com/")[0] == VALUE
        text, refused = s.reveal("<secret:pw>", "https://evil.test/")
        assert text == "<secret:pw>" and refused == ["pw"]
        assert "pw" in refusal_message(refused) and "not allowed" in refusal_message(refused)

    def test_other_site_secrets_are_not_listed(self):
        s = SecretStore.from_any({"a.test": {"pa": "1111"}, "b.test": {"pb": "2222"},
                                  "flat": "3333"})
        assert s.names_for("https://a.test/") == ["pa", "flat"]
        assert s.names_for("https://c.test/") == ["flat"]

    def test_mask_covers_scoped_values_even_on_other_sites(self):
        s = SecretStore.from_any({"a.test": {"pa": VALUE}})
        assert VALUE not in s.mask(f"echo {VALUE}")

    def test_same_name_different_sites(self):
        s = SecretStore.from_any({"a.test": {"pw": "AAAA"}, "b.test": {"pw": "BBBB"}})
        assert s.reveal("<secret:pw>", "https://b.test/")[0] == "BBBB"

    def test_unknown_placeholder_untouched(self):
        s = SecretStore.from_any({"a.test": {"pw": "AAAA"}})
        assert s.reveal("<secret:nope>", "https://a.test/") == ("<secret:nope>", [])


def _run_nav(secrets, url_for_call, llm_lines):
    """Run the navigator with a fake page whose URL changes between calls.

    url_for_call(n): URL returned for the n-th read of page.url.
    """
    calls = {"n": 0}

    def url():
        calls["n"] += 1
        return url_for_call(calls["n"])

    page = MagicMock()
    type(page).url = PropertyMock(side_effect=url)
    page.title.return_value = "T"
    page.inner_text.return_value = "body"
    fantoma = MagicMock()
    fantoma._engine.get_page.return_value = page
    fantoma._dom.extract.return_value = f"[0] textbox 'Password' echo {VALUE}"
    fantoma._dom.extract_content.return_value = "content"
    fantoma._dom.signature.return_value = ("textbox", "Password")
    fantoma.type_text.return_value = {"success": True}
    llm = MagicMock()
    llm.chat.side_effect = llm_lines
    with patch("fantoma.navigator.aria_snapshot", return_value={}), \
         patch("fantoma.navigator.aria_diff", return_value=""), \
         patch("fantoma.navigator.classify_blocker", return_value=None):
        result = Navigator().execute(Subtask("log in", "find", "done"), fantoma, llm,
                                     StateTracker(), max_steps=3, sensitive_data=secrets)
    return result, fantoma, llm


def _typed(fantoma):
    return [c.args[1] if len(c.args) > 1 else c.kwargs.get("text")
            for c in fantoma.type_text.call_args_list]


class TestNavigator:
    TYPE = 'TYPE [0] "<secret:pw>"'

    def test_flat_secret_typed(self):
        _, f, _ = _run_nav({"pw": VALUE}, lambda n: "https://any.test/",
                           [self.TYPE, "DONE", "answer"])
        assert _typed(f) == [VALUE]

    def test_scoped_secret_typed_on_right_site(self):
        _, f, _ = _run_nav({"https://site.test": {"pw": VALUE}},
                           lambda n: "https://site.test/login", [self.TYPE, "DONE", "ok"])
        assert _typed(f) == [VALUE]

    def test_scoped_secret_refused_on_wrong_site(self):
        res, f, llm = _run_nav({"https://site.test": {"pw": VALUE}},
                               lambda n: "https://evil.test/login",
                               [self.TYPE, "NAVIGATE https://evil.test/x", "ok", "ok", "ok"])
        assert _typed(f) == []
        assert "not allowed on this site" in repr(llm.chat.call_args_list)
        assert any("REFUSED" in repr(s) or not s["success"] for s in res.steps_detail)

    def test_redirect_between_step_start_and_fill_is_refused(self):
        # The page is on the right site when the step starts and while the
        # model is choosing; it redirects before the fill happens.
        state = {"site": "https://site.test/"}
        replies = iter([self.TYPE, "NAVIGATE https://site.test/x", "ok", "ok", "ok"])

        def llm_reply(*a, **k):
            line = next(replies)
            if line == self.TYPE:
                state["site"] = "https://evil.test/"  # redirect lands now
            return line

        res, f, llm = _run_nav({"https://site.test": {"pw": VALUE}},
                               lambda n: state["site"], llm_reply)
        assert _typed(f) == []
        assert "not allowed on this site" in repr(llm.chat.call_args_list)

    def test_value_never_in_prompts_or_logs(self, caplog):
        caplog.set_level(logging.DEBUG)
        res, f, llm = _run_nav({"https://site.test": {"pw": VALUE}},
                               lambda n: "https://site.test/login",
                               [self.TYPE, "DONE", "answer"])
        assert _typed(f) == [VALUE]
        assert VALUE not in repr(llm.chat.call_args_list)
        assert VALUE not in caplog.text
        assert VALUE not in repr(res)
        assert "<secret:pw>" in repr(llm.chat.call_args_list)

    def test_other_site_placeholders_not_shown_to_model(self):
        _, _, llm = _run_nav({"https://other.test": {"hidden_name": VALUE}},
                             lambda n: "https://site.test/", ["NAVIGATE https://site.test/x", "a", "a", "a"])
        assert "hidden_name" not in repr(llm.chat.call_args_list)
        assert VALUE not in repr(llm.chat.call_args_list)


class _Page:
    def __init__(self, urls):
        self._urls = list(urls)

    @property
    def url(self):
        return self._urls.pop(0) if len(self._urls) > 1 else self._urls[0]


class TestAgentAndFastPath:
    def _agent(self, secrets, urls):
        a = Agent.__new__(Agent)
        a._sensitive_data = secrets
        a.fantoma = MagicMock()
        a.fantoma._engine.get_page.return_value = _Page(urls)
        return a

    def test_replay_refuses_on_wrong_site(self):
        a = self._agent({"https://site.test": {"pw": VALUE}}, ["https://evil.test/"])
        a.fantoma._dom.find_by_signature.return_value = 0
        ok = a._replay_steps([{"action": "type_text", "role": "textbox",
                               "name": "Password", "value": "<secret:pw>"}], "")
        assert ok is False
        a.fantoma.type_text.assert_not_called()

    def test_replay_types_on_right_site(self):
        a = self._agent({"https://site.test": {"pw": VALUE}}, ["https://site.test/"])
        a.fantoma._dom.find_by_signature.return_value = 0
        a.fantoma.type_text.return_value = {"success": True}
        assert a._replay_steps([{"action": "type_text", "role": "textbox",
                                 "name": "Password", "value": "<secret:pw>"}], "")
        a.fantoma.type_text.assert_called_once_with(0, VALUE)

    def test_mask_result_hides_values_everywhere(self):
        from fantoma.agent import AgentResult
        a = self._agent({"site.test": {"pw": VALUE}}, ["https://other.test/"])
        r = a._mask_result(AgentResult(success=True, data=f"got {VALUE}",
                                       error=f"bad {VALUE}",
                                       steps_detail=[{"action": f"type({VALUE})"}]))
        assert VALUE not in repr(r)

    def test_fast_path_refuses_wrong_site_and_never_types(self):
        f = MagicMock()
        f._engine.get_page.return_value = _Page(["https://evil.test/login"])
        f.get_state.return_value = {"aria_tree": ""}
        r = run_fast_path("Log in with username 'bob' and password '<secret:pw>'.", f,
                          secrets={"https://site.test": {"pw": VALUE}})
        assert not r.complete
        f.login.assert_not_called()
        assert VALUE not in repr(r)

    def test_fast_path_flat_still_logs_in(self):
        f = MagicMock()
        f._engine.get_page.return_value = _Page(["https://any.test/login"])
        f.login.return_value = {"success": True}
        with patch("fantoma.fast_path._password_box_visible", return_value=False):
            r = run_fast_path("Log in with username 'bob' and password '<secret:pw>'.", f,
                              secrets={"pw": VALUE})
        assert f.login.call_args.kwargs["password"] == VALUE
        assert r.complete
