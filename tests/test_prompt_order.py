"""Step prompts keep a byte-identical fixed prefix and put the page last."""
import datetime
from unittest.mock import MagicMock, patch

from fantoma.navigator import Navigator, NAVIGATOR_SYSTEM
from fantoma.planner import Subtask, Planner
from fantoma.prompt_parts import today_line
from fantoma.state_tracker import StateTracker


def _run_three_steps(webmcp=False):
    nav = Navigator()
    subtask = Subtask("Open the basket", "interact", "Basket is open")
    state = {"n": 0}
    page = MagicMock()
    page.url = "https://shop.example/a"
    fantoma = MagicMock()
    fantoma._engine.get_page.return_value = page
    fantoma._webmcp = webmcp
    fantoma.webmcp_prompt.return_value = "\n\nWebMCP tools:\n- search {}" if webmcp else ""
    pages = ["PAGE-ONE [0] link 'a'", "PAGE-TWO [0] button 'b'", "PAGE-THREE [0] link 'c'"]

    def extract(*a, **k):
        state["n"] += 1
        return pages[min(state["n"] - 1, 2)]
    fantoma._dom.extract.side_effect = extract
    fantoma._dom.extract_content.return_value = "content"
    fantoma.click.return_value = {"success": True, "state": {"url": "https://shop.example/a"}}
    llm = MagicMock()
    llm.chat.side_effect = ["CLICK [0]", "CLICK [0]", "DONE", "answer"]
    with patch("fantoma.navigator.aria_snapshot", return_value={}), \
         patch("fantoma.navigator.aria_diff", return_value="changed"), \
         patch("fantoma.navigator.classify_blocker", return_value=None):
        nav.execute(subtask, fantoma, llm, StateTracker(), max_steps=5, sensitive_data={})
    return [c[0][0] for c in llm.chat.call_args_list[:3]]


def test_system_prefix_identical_across_steps():
    calls = _run_three_steps()
    systems = [c[0]["content"] for c in calls]
    assert systems[0] == systems[1] == systems[2] == NAVIGATOR_SYSTEM
    # Nothing per-step or per-task in the fixed part.
    assert "Basket" not in NAVIGATOR_SYSTEM
    assert "http" not in NAVIGATOR_SYSTEM.split("NAVIGATE https://...")[0]
    assert "Today is" not in NAVIGATOR_SYSTEM


def test_task_block_stable_date_present_page_last():
    calls = _run_three_steps()
    users = [c[1]["content"] for c in calls]
    head = users[0].split("\n\n")[0]
    assert "Subtask: Open the basket" in head and "Done when: Basket is open" in head
    assert today_line() in head
    for u, marker in zip(users, ["PAGE-ONE", "PAGE-TWO", "PAGE-THREE"]):
        assert u.startswith(head)
        assert marker in u
        # page content is the last thing in the message
        assert u.rindex("Page (") > u.index("Change:")
        assert u.endswith("</untrusted_web_content id=\"%s\">" % u.rsplit('id="', 1)[1].split('"')[0])
    assert users[0] != users[1]


def test_webmcp_block_before_page():
    calls = _run_three_steps(webmcp=True)
    u = calls[0][1]["content"]
    assert u.index("WebMCP tools") < u.index("Page (")


def test_today_line_format():
    assert today_line(datetime.date(2026, 11, 6)) == "Today is Friday 2026-11-06."


def test_planner_gets_date_and_page_last():
    llm = MagicMock()
    llm.chat.return_value = "1. instruction: go | mode: find | done_when: ok"
    p = Planner(llm)
    p.decompose("book next Friday", "PAGE-X")
    sysm, user = llm.chat.call_args[0][0]
    assert today_line() in user["content"] and today_line() not in sysm["content"]
    p.replan("book next Friday", [], Subtask("go", "find", "ok"), "PAGE-Y", failure_reason="dom_stagnant")
    s2, u2 = llm.chat.call_args[0][0]
    assert s2["content"] == sysm["content"]
    assert u2["content"].rindex("PAGE-Y") > u2["content"].index("dom_stagnant")
