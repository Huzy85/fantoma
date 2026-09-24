"""Steps done in code before any model is asked (fantoma.fast_path).

Each case is run in a real Chromium against a local page shaped like the
public practice pages the live agent check uses, and graded on the page
itself. The cases that matter as much as the successes are the refusals:
an ambiguous page or an unfamiliar sentence must be handed to the model,
and a failed login must never be reported as done.
"""

import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from fantoma.fast_path import run_fast_path, split_clauses
from fantoma.task_spec import parse_task, verify_outcome

PAGES = {
    "/inputs": """<html><body><a href="https://github.com/x">Fork me on GitHub</a>
        <div><h3>Inputs</h3><p>Number</p><input type="number"></div>
        <div>Powered by <a href="http://e.com/">Elemental Selenium</a></div></body></html>""",
    "/dropdown": """<html><body><h3>Dropdown List</h3><select id="dropdown">
        <option value="" disabled selected>Please select an option</option>
        <option value="1">Option 1</option><option value="2">Option 2</option></select></body></html>""",
    "/two-dropdowns": """<html><body><select aria-label="From"><option>Option 1</option><option>Option 2</option></select>
        <select aria-label="To"><option>Option 1</option><option>Option 2</option></select></body></html>""",
    "/checkboxes": """<html><body><h3>Checkboxes</h3><form>
        <input type="checkbox"> checkbox 1<br><input type="checkbox" checked> checkbox 2</form></body></html>""",
    "/two-fields": """<html><body><label>First <input type="text"></label>
        <label>Second <input type="text"></label></body></html>""",
    "/login": """<html><body><form id="f"><input placeholder="Username" type="text" id="u">
        <input placeholder="Password" type="password" id="p"><div id="err"></div>
        <input type="submit" value="Login"></form>
        <script>f.addEventListener('submit', e => { e.preventDefault();
          if (u.value === 'standard_user' && p.value === 'secret_sauce') location.href = '/inventory';
          else err.innerHTML = '<h3>Epic sadface: Username and password do not match</h3>'; });
        </script></body></html>""",
    "/inventory": """<html><body><h1>Products</h1><div id="list"></div><script>
        const items = ["Sauce Labs Backpack", "Sauce Labs Fleece Jacket", "Sauce Labs Onesie"];
        list.innerHTML = items.map(t => `<div><div>${t}</div><div>$29.99</div>
          <button data-id="${t}">Add to cart</button></div>`).join('');
        list.addEventListener('click', e => { const b = e.target.closest('button');
          if (b) b.textContent = b.textContent === 'Add to cart' ? 'Remove' : 'Add to cart'; });
        </script></body></html>""",
    "/search": """<html><body><form action="/results"><input type="search" name="q"
        aria-label="Search the site"><button>Go</button></form>
        <input type="text" aria-label="Newsletter email"></body></html>""",
    "/results": "<html><body><h1>Results</h1></body></html>",
    "/links": """<html><body><a href="/travel">Travel</a> <a href="/poetry">Poetry</a>
        <a href="/travel-2">Travel guides</a></body></html>""",
    "/travel": "<html><body><h1>Travel</h1></body></html>",
}


@pytest.fixture(scope="module")
def site():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            body = PAGES.get(self.path.split("?")[0], "<html><body>other</body></html>").encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


@pytest.fixture(scope="module")
def chromium():
    sync_api = pytest.importorskip("playwright.sync_api")
    exe = os.environ.get("FANTOMA_TEST_CHROMIUM") or "/opt/pw-browsers/chromium-1194/chrome-linux/chrome"
    try:
        pw = sync_api.sync_playwright().start()
    except Exception as e:
        pytest.skip(f"playwright unavailable: {e}")
    try:
        browser = pw.chromium.launch(**({"executable_path": exe} if os.path.exists(exe) else {}))
    except Exception as e:
        pw.stop()
        pytest.skip(f"no Chromium available: {e}")
    yield browser
    browser.close()
    pw.stop()


class _Engine:
    """The slice of BrowserEngine the browser tool's actions use."""

    humanizer = None

    def __init__(self, page):
        self._page = page

    def get_page(self):
        return self._page

    def navigate(self, url):
        self._page.goto(url)

    def screenshot(self, *a, **k):
        return b""

    def get_storage_state(self):
        return {}

    def load_storage_state(self, state):
        pass


@pytest.fixture
def browser(chromium, tmp_path, monkeypatch):
    from fantoma.browser_tool import Fantoma
    monkeypatch.setenv("HOME", str(tmp_path))    # login saves a session file
    monkeypatch.setattr("time.sleep", lambda s: None)  # login waits for slow sites
    ctx = chromium.new_context()
    f = Fantoma()
    f._engine = _Engine(ctx.new_page())
    yield f
    ctx.close()


def _open(browser, url):
    browser._engine.get_page().goto(url)
    return browser._engine.get_page()


class TestSplit:
    def test_then_separates_steps(self):
        assert split_clauses("Log in with username 'a' and password 'b', then add the 'X' "
                             "to the cart.") == ["Log in with username 'a' and password 'b'",
                                                 "add the 'X' to the cart"]

    def test_and_before_a_question_separates(self):
        assert split_clauses("Open the 'Travel' category and tell me the first title.") == [
            "Open the 'Travel' category", "tell me the first title"]

    def test_and_press_enter_stays_one_step(self):
        assert split_clauses("Type 'shoes' into the search box and press Enter") == [
            "Type 'shoes' into the search box and press Enter"]


class TestDoneInCode:
    def test_types_into_the_only_field(self, browser, site):
        page = _open(browser, site + "/inputs")
        r = run_fast_path("Type the number 42 into the input box on this page.", browser)
        assert r.complete, r
        assert page.evaluate("document.querySelector('input').value") == "42"

    def test_selects_an_option(self, browser, site):
        page = _open(browser, site + "/dropdown")
        r = run_fast_path("Select 'Option 2' from the dropdown on this page.", browser)
        assert r.complete, r
        assert page.evaluate("document.querySelector('#dropdown').value") == "2"

    def test_ticks_the_first_checkbox(self, browser, site):
        page = _open(browser, site + "/checkboxes")
        r = run_fast_path("Tick the first checkbox on this page if it is not already ticked.", browser)
        assert r.complete, r
        assert page.evaluate("[...document.querySelectorAll('input')].map(c => c.checked)") == [True, True]

    def test_logs_in_then_adds_the_named_item(self, browser, site):
        page = _open(browser, site + "/login")
        r = run_fast_path("Log in with username 'standard_user' and password 'secret_sauce', "
                          "then add the 'Sauce Labs Fleece Jacket' to the cart.", browser)
        assert r.complete, r
        assert page.url.endswith("/inventory")
        states = page.evaluate("[...document.querySelectorAll('button')].map(b => b.textContent)")
        assert states == ["Add to cart", "Remove", "Add to cart"]

    def test_searches(self, browser, site):
        page = _open(browser, site + "/search")
        r = run_fast_path("Search for 'wooden spoon'", browser)
        assert r.complete, r
        assert "q=wooden+spoon" in page.url

    def test_clicks_a_named_link_and_leaves_the_question(self, browser, site):
        page = _open(browser, site + "/links")
        r = run_fast_path("Open the 'Travel' category and tell me the title of the first book.", browser)
        assert r.done and not r.complete
        assert r.remainder == "tell me the title of the first book"
        assert page.url.endswith("/travel")


class TestLeftToTheModel:
    def test_a_wrong_password_is_never_done(self, browser, site):
        page = _open(browser, site + "/login")
        r = run_fast_path("Log in with username 'standard_user' and password 'nope'.", browser)
        assert not r.complete
        assert "still showing" in r.failed
        assert page.url.endswith("/login")

    def test_two_dropdowns_offering_the_option(self, browser, site):
        _open(browser, site + "/two-dropdowns")
        r = run_fast_path("Select 'Option 2'", browser)
        assert not r.done and r.remainder == "Select 'Option 2'"

    def test_two_fields_and_no_hint(self, browser, site):
        _open(browser, site + "/two-fields")
        r = run_fast_path("Type 'hello' into the box", browser)
        assert not r.done

    def test_field_named_in_the_task(self, browser, site):
        page = _open(browser, site + "/two-fields")
        r = run_fast_path("Type 'hello' into the Second field", browser)
        assert r.complete, r
        assert page.evaluate("[...document.querySelectorAll('input')].map(i => i.value)") == ["", "hello"]

    def test_unfamiliar_sentence(self, browser, site):
        _open(browser, site + "/inputs")
        r = run_fast_path("Find the cheapest flight to Lisbon next Tuesday", browser)
        assert not r.done and not r.failed
        assert r.remainder == "Find the cheapest flight to Lisbon next Tuesday"

    def test_link_name_must_match_exactly(self, browser, site):
        _open(browser, site + "/links")
        r = run_fast_path("Open the 'Trav' category", browser)
        assert not r.done


class TestVerification:
    """What the finished page must show before a run may report success."""

    def test_login_with_the_form_still_showing_fails(self):
        spec = parse_task("Log in with username 'standard_user' and password 'secret_sauce'.")
        ok, why = verify_outcome(spec, "https://www.saucedemo.com/",
                                 '[0] textbox "Username"\n[1] textbox "Password"\n[2] button "Login"')
        assert not ok and "still showing" in why

    def test_select_is_judged_by_what_the_dropdown_holds(self):
        spec = parse_task("Select 'Option 2' from the dropdown.")
        listed = '[0] combobox "" (selected: "Please select an option")\n[3] option "Option 2"'
        assert not verify_outcome(spec, "u", listed)[0]
        assert verify_outcome(spec, "u", '[0] combobox "" (selected: "Option 2")')[0]

    def test_typed_text_must_be_in_a_field(self):
        spec = parse_task("Type the number 42 into the input box.")
        assert spec.action == "type" and spec.values["text"] == "42"
        assert verify_outcome(spec, "u", '[0] spinbutton "" [value="42"] (in: Number)')[0]
        assert not verify_outcome(spec, "u", '[0] spinbutton "" (in: Number)')[0]

    def test_type_as_a_noun_is_not_a_typing_task(self):
        assert parse_task("What type of licence does Python use?").action != "type"


class TestFieldValues:
    def test_labelled_field_shows_its_text_and_passwords_are_masked(self, browser, site):
        _open(browser, site + "/login")
        page = browser._engine.get_page()
        page.fill("#u", "standard_user")
        page.fill("#p", "secret_sauce")
        tree = browser.get_state()["aria_tree"]
        assert 'textbox "Username" [value="standard_user"]' in tree
        assert "secret_sauce" not in tree


class TestAddToCartIsChecked:
    def test_a_click_that_changes_nothing_is_not_done(self, browser, site):
        PAGES["/dead-cart"] = PAGES["/inventory"].replace("b.textContent = b.textContent", "void 0; ({}).x")
        _open(browser, site + "/dead-cart")
        r = run_fast_path("Add the 'Sauce Labs Fleece Jacket' to the cart", browser)
        assert not r.complete
        assert "still shows an add button" in r.failed


class TestAgentUsesTheFastPath:
    def _agent(self, tmp_path, fast_result):
        from unittest.mock import MagicMock
        from fantoma.agent import Agent
        from fantoma.action_cache import ActionCache
        from fantoma.navigator import NavigatorResult
        a = Agent.__new__(Agent)
        a._max_steps, a._flat_budget, a._sensitive_data = 25, 20, {}
        a._planner = MagicMock()
        a._planner.summarise.return_value = "Answer"
        a._navigator = MagicMock()
        a._navigator.execute.return_value = NavigatorResult(
            "done", "Model answer", 2, [{"url": "https://example.com"}], "https://example.com")
        a.fantoma = MagicMock()
        a.fantoma.get_state.return_value = {"aria_tree": '[0] combobox "" (selected: "Option 2")'}
        a._llm = MagicMock()
        a.escalation = MagicMock()
        a.escalation.total_escalations = 0
        a._action_cache = ActionCache(db_path=str(tmp_path / "ac.db"), enabled=False)
        a._fast_path = True
        return a

    def test_a_task_done_in_code_never_asks_the_model(self, tmp_path, monkeypatch):
        from fantoma.fast_path import FastResult
        done = FastResult(done=["selected 'Option 2'"])
        monkeypatch.setattr("fantoma.agent.run_fast_path", lambda task, f, s=None: done)
        a = self._agent(tmp_path, done)
        res = a.run("Select 'Option 2' from the dropdown", start_url="https://example.com")
        assert res.success and "selected 'Option 2'" in res.data
        a._navigator.execute.assert_not_called()

    def test_the_model_gets_only_what_is_left(self, tmp_path, monkeypatch):
        from fantoma.fast_path import FastResult
        part = FastResult(done=["clicked 'Travel'"], remainder="tell me the first title")
        monkeypatch.setattr("fantoma.agent.run_fast_path", lambda task, f, s=None: part)
        a = self._agent(tmp_path, part)
        a.run("Open the 'Travel' category and tell me the first title", start_url="https://example.com")
        sub = a._navigator.execute.call_args.kwargs["subtask"]
        assert sub.instruction.startswith("tell me the first title")
        assert "clicked 'Travel'" in sub.instruction


class TestModelDoneIsChecked:
    def _fantoma(self, tree, url="https://www.saucedemo.com/"):
        from unittest.mock import MagicMock
        f = MagicMock()
        f._engine.get_page.return_value.url = url
        f.get_state.return_value = {"aria_tree": tree}
        return f

    def test_done_on_a_rejected_login_is_refused_with_the_reason(self):
        from fantoma.navigator import Navigator
        spec = parse_task("Log in with username 'standard_user' and password 'secret_sauce'.")
        why = Navigator._check_done(spec, self._fantoma('[0] textbox "Username"\n[1] textbox "Password"'))
        assert "still showing" in why

    def test_done_is_accepted_when_the_page_agrees(self):
        from fantoma.navigator import Navigator
        spec = parse_task("Select 'Option 2' from the dropdown.")
        assert Navigator._check_done(spec, self._fantoma('[0] combobox "" (selected: "Option 2")')) == ""


class TestSecrets:
    def test_placeholders_are_typed_as_the_real_value_and_never_shown(self, browser, site):
        page = _open(browser, site + "/login")
        r = run_fast_path("Log in with username 'standard_user' and password '<secret:pw>'.",
                          browser, secrets={"pw": "secret_sauce"})
        assert r.complete, r
        assert page.url.endswith("/inventory")
        assert "secret_sauce" not in repr(r)

    def test_a_click_task_is_not_failed_because_the_button_went_away(self):
        spec = parse_task("Click 'Sign up'")
        assert verify_outcome(spec, "https://x/welcome", "Welcome aboard")[0]


class TestLatePage:
    def test_a_search_box_that_renders_late_is_found(self, browser, site, monkeypatch):
        import types
        PAGES["/late-search"] = """<html><body><div id="app"></div><script>
            setTimeout(() => app.innerHTML = '<form action="/results"><input type="search" name="q" '
              + 'aria-label="Search for anything"></form>', 800);</script></body></html>"""
        page = _open(browser, site + "/late-search")
        # time.sleep is stubbed for the login tests; let the settle wait really wait.
        monkeypatch.setattr("fantoma.fast_path.time",
                            types.SimpleNamespace(sleep=lambda s: page.wait_for_timeout(s * 1000)))
        r = run_fast_path("Search for 'wooden spoon'", browser)
        assert r.complete, r
        assert "q=wooden+spoon" in page.url


class TestQuestionsOpenTheNamedItem:
    GRID = """<html><body><ol>
      <li><a href="/book/1"><img alt="A Light in the Attic" src="x.png"></a>
          <h3><a href="/book/1" title="A Light in the Attic">A Light in the ...</a></h3><p>£51.77</p></li>
      <li><a href="/book/2"><img alt="Tipping the Velvet" src="y.png"></a>
          <h3><a href="/book/2">Tipping the Velvet</a></h3><p>£53.74</p></li></ol></body></html>"""

    def test_the_named_book_is_opened_and_the_question_left(self, browser, site):
        PAGES["/grid"] = self.GRID
        page = _open(browser, site + "/grid")
        r = run_fast_path("What is the price of the book 'A Light in the Attic'?", browser)
        assert page.url.endswith("/book/1")
        assert r.done == ["opened the page for 'A Light in the Attic'"]
        assert r.remainder == "What is the price of the book 'A Light in the Attic'?"

    def test_a_question_naming_nothing_on_the_page_stays_put(self, browser, site):
        PAGES["/grid"] = self.GRID
        page = _open(browser, site + "/grid")
        r = run_fast_path("What is the price of 'Sapiens'?", browser)
        assert page.url.endswith("/grid") and not r.done
