# fantoma/navigator.py
"""Single-subtask execution loop for the hierarchical agent."""

import logging
import re
import time
from dataclasses import dataclass
from urllib.parse import urlparse

from fantoma.dom.markdown import page_to_markdown, aria_text_fallback
from fantoma.safety import UNTRUSTED_NOTE, wrap_untrusted
from fantoma.secrets_scope import SecretStore, refusal_message
from fantoma.dom.aria_diff import aria_diff, aria_snapshot
from fantoma.dom.page_reads import StepMeter
from fantoma.dom.refs import page_key
from fantoma.browser.page_state import classify_blocker
from fantoma.planner import Planner, Subtask
from fantoma.state_tracker import StateTracker

log = logging.getLogger("fantoma.navigator")

# Actions that can actually change the page. navigate/scroll/back move you
# around but leave the site's state untouched, so they are not evidence that
# a "do something" task was carried out.
_STATE_CHANGING = frozenset({"click", "type_text", "select", "press_key"})

# Imperatives that mean the task is not complete until the page changes.
# Kept deliberately narrow: a false positive here blocks DONE on a read-only
# task and burns the whole step budget, which is worse than the bug being fixed.
_ACTION_VERBS = frozenset({
    "add", "click", "select", "choose", "tick", "check", "uncheck", "submit",
    "send", "log in", "login", "sign in", "signin", "sign up", "signup",
    "register", "buy", "order", "purchase", "checkout", "check out",
    "upload", "download", "fill", "enter", "type", "search for", "set",
    "remove", "delete", "clear", "apply", "post", "reply", "subscribe",
    "accept", "decline", "confirm", "toggle", "drag", "sort", "filter",
})

_MAX_DONE_REJECTIONS = 2


def _requires_state_change(instruction: str) -> bool:
    """True when the instruction asks the agent to act, not merely to read.

    Word-boundary matched so "search for" does not fire on "research", and
    "post" does not fire on "postcode".
    """
    text = (instruction or "").lower()
    for verb in _ACTION_VERBS:
        if re.search(rf"\b{re.escape(verb)}\b", text):
            return True
    return False


MODE_MAP = Planner.MODE_MAP


@dataclass
class NavigatorResult:
    status: str         # "done" | "stagnant" | "failed" | "max_steps" | "blocked"
    data: str
    steps_taken: int
    steps_detail: list
    final_url: str
    failure_reason: str = ""    # "scroll_limit" | "action_cycle" | "dom_stagnant" | "rate_limit" | "login_wall" | "captcha" | "domain_drift" | "llm_empty"
    last_actions: list = None   # last 5 actions before stop
    is_placeholder: bool = False  # True when `data` is a synthetic status line, not real extracted content
    replay_steps: list = None   # successful actions as {action, role, name, value} for the action cache (None = not cacheable)


NAVIGATOR_SYSTEM = """\
You control a browser to complete one specific task.

Subtask: {instruction}
Done when: {done_when}

Pick 1-5 actions (one per line):
CLICK [number]
TYPE [number] "text"
SELECT [number] "option"
SCROLL down|up
NAVIGATE https://...
BACK
PRESS Enter
DONE

Rules:
- Match [number] to the element list shown below.
- When several controls share a name, each shows the item it belongs to as
  (in: ...). To act on a specific item, pick the [number] whose (in: ...)
  names that item. Do NOT pick the first matching control -- that is a
  different item. This hint is inferred from page order and can be wrong,
  so prefer a control whose own name already identifies the item.
- To fill a form: TYPE each field, then CLICK submit, all in one response.
- After typing in a search field, add PRESS Enter.
- BACK returns to the previous page (use when current page is a dead end).
- NAVIGATE, BACK, and DONE end the sequence.
- Read the Content section first -- if it contains the answer, say DONE immediately.
- Reply with ONLY action lines, nothing else.
- {untrusted_note}

Before saying DONE, verify:
- The user's asked-for values have evidence in the current page or in prior data.
- If you typed text into any field during this subtask, you MUST also press
  Enter on it OR click a submit/search button before saying DONE. Typed text
  that is never submitted is incomplete -- the page state has not changed.
- If you are on a Google or Bing search results page and the user wants a
  specific resource (a product page, a course page, an article, a hotel),
  CLICK the first organic result whose title contains your task keywords.
  Never say DONE on a search results page unless the task is to LIST the
  search results themselves.
- If no evidence supports the goal, do NOT say DONE. Take more actions instead.\
"""

EXTRACT_ON_DONE = """\
You are extracting the answer from a web page.

Rules:
- Give the ACTUAL answer with specific data (names, numbers, dates, URLs).
- NEVER give instructions like "to find X, do Y". The answer must contain the data itself.
- If the page contains partial information, extract what is available.
- Address every criterion in the task explicitly.
- If information is not on the page, say exactly what is missing.
- Report every relevant value that appears in the page content below. Titles, prices, dates, specs, pronunciations, descriptions — extract and state them, paraphrasing naturally where that reads better than a raw quote.
- Do not invent values from general knowledge that are not in the page content. If a specific asked-for value is genuinely absent from the page, note which value is missing — never default to "not on page" when the page actually contains the answer.
- """ + UNTRUSTED_NOTE


def _norm_url(url: str) -> str:
    """Strip fragment, query, and trailing slash for revisit comparison."""
    u = (url or "").split("#")[0].split("?")[0].rstrip("/")
    return u.lower()


def _parse_actions(raw: str) -> list[tuple[str, dict]]:
    """Parse LLM response into (action_type, params) tuples."""
    results = []
    for line in (raw or "").strip().split("\n"):
        line = line.strip()
        if not line:
            continue

        if line[:6].upper() == "WEBMCP":
            from fantoma.webmcp import parse_action_line
            parsed = parse_action_line(line)
            if parsed:
                results.append(parsed)
                break  # the page may change; look again
            continue

        m = re.match(r'CLICK\s*\[?(\d+)\]?', line, re.IGNORECASE)
        if m:
            results.append(("click", {"element_id": int(m.group(1))}))
            if len(results) >= 5:
                break
            continue

        # Quoted TYPE — match the closing quote to the opener so apostrophes
        # inside the text (it's, O'Brien) are not truncated at the first quote.
        m = re.match(r'TYPE\s*\[?(\d+)\]?\s*(["\'])(.+?)\2\s*$', line, re.IGNORECASE)
        if m:
            results.append(("type_text", {"element_id": int(m.group(1)), "text": m.group(3)}))
            if len(results) >= 5:
                break
            continue

        # Unquoted TYPE — weak models routinely omit the quotes. Without this
        # the line falls through to the [N] catch-all and becomes a wrong CLICK
        # on the input field, corrupting the trajectory while looking successful.
        m = re.match(r'TYPE\s*\[?(\d+)\]?\s+(.+?)\s*$', line, re.IGNORECASE)
        if m:
            results.append(("type_text", {"element_id": int(m.group(1)), "text": m.group(2)}))
            if len(results) >= 5:
                break
            continue

        m = re.match(r'SELECT\s*\[?(\d+)\]?\s*["\'](.+?)["\']', line, re.IGNORECASE)
        if m:
            results.append(("select", {"element_id": int(m.group(1)), "value": m.group(2)}))
            if len(results) >= 5:
                break
            continue

        m = re.match(r'SCROLL\s*(UP|DOWN)', line, re.IGNORECASE)
        if m:
            results.append(("scroll", {"direction": m.group(1).lower()}))
            if len(results) >= 5:
                break
            continue

        m = re.match(r'NAVIGATE\s+["\']?(https?://\S+?)["\']?\s*$', line, re.IGNORECASE)
        if m:
            # Strip trailing sentence punctuation the model may append.
            url = m.group(1).rstrip('.,;)]')
            results.append(("navigate", {"url": url}))
            break

        if re.match(r'BACK\b', line, re.IGNORECASE):
            results.append(("go_back", {}))
            break

        m = re.match(r'PRESS\s+(\w+)', line, re.IGNORECASE)
        if m:
            results.append(("press_key", {"key": m.group(1)}))
            if len(results) >= 5:
                break
            continue

        # Full-match only (optional trailing . or !). A prefix match fired on
        # any line starting with "done" — including the prompt's own
        # "done_when:" field echoed back by a weak model — ending the subtask
        # prematurely and triggering a wrong answer extraction.
        if re.fullmatch(r'DONE[.!]?', line, re.IGNORECASE):
            results.append(("done", {}))
            break

        # A malformed TYPE/SELECT line must not degrade into a CLICK on the
        # field via the catch-all below.
        if re.match(r'(TYPE|SELECT)\b', line, re.IGNORECASE):
            continue

        m = re.search(r'\[(\d+)\]', line)
        if m:
            results.append(("click", {"element_id": int(m.group(1))}))
            if len(results) >= 5:
                break

    return results


class Navigator:
    """Executes a single subtask against the browser."""

    def execute(self, subtask: Subtask, fantoma, llm, tracker: StateTracker,
                max_steps: int = 15, start_domain: str = "",
                sensitive_data: dict = None, deadline: float = None,
                target: str = "", spec=None) -> NavigatorResult:
        self._meter = StepMeter()
        try:
            return self._execute(subtask, fantoma, llm, tracker, max_steps,
                                 start_domain, sensitive_data, deadline,
                                 target, spec)
        finally:
            self._meter.flush()
            # Disarm the step cache so later callers read the live page.
            end = getattr(getattr(fantoma, "_dom", None), "end_step", None)
            if callable(end):
                end()

    def _execute(
        self,
        subtask: Subtask,
        fantoma,
        llm,
        tracker: StateTracker,
        max_steps: int = 15,
        start_domain: str = "",
        sensitive_data: dict = None,
        deadline: float = None,
        target: str = "",
        spec=None,
    ) -> NavigatorResult:
        steps_detail = []
        # Element numbers are stable within a task. A new task starts a new
        # numbering, so nothing from an earlier task can be quoted by mistake.
        reset_refs = getattr(fantoma._dom, "reset_refs", None)
        if callable(reset_refs):
            reset_refs()
        store = SecretStore.from_any(sensitive_data)
        self._secrets = store  # read by _extract_answer to mask page text
        dom_mode = MODE_MAP.get(subtask.mode, "navigate")
        # Orchestrator-injected fallback subtasks (the Google search escape
        # hatch) legitimately leave the start domain. Disable drift detection
        # for them — otherwise their first NAVIGATE to google.com trips the
        # guard and the fallback is consumed without ever running.
        if getattr(subtask, "allow_cross_domain", False):
            start_domain = ""
        change_line = "First step"
        last_content = ""
        replay_steps = []     # successful actions captured for the action cache
        replay_ok = True      # cleared if an element action has no resolvable signature
        empty_streak = 0  # consecutive empty/unparseable LLM responses
        visited_urls = set()       # pages seen this subtask
        nav_loop_targets = {}      # how often we've bounced back to an already-seen URL
        # Premature-DONE guard state. requires_action is decided once from the
        # instruction; state_changed flips when an action that can actually
        # alter the page succeeds.
        requires_action = _requires_state_change(subtask.instruction)
        state_changed = False
        done_rejections = 0
        nudge = ""

        for step_num in range(1, max_steps + 1):
            # Wall-clock guard — a slow LLM can blow the time budget long before
            # the step budget. Stop and extract whatever the page has.
            if deadline and time.monotonic() > deadline:
                log.info("Wall-clock deadline exceeded at step %d", step_num)
                data = self._extract_answer(subtask, fantoma, llm)
                return NavigatorResult(
                    status="timeout", data=data or "Deadline exceeded",
                    steps_taken=step_num, steps_detail=steps_detail,
                    final_url=fantoma._engine.get_page().url,
                    failure_reason="timeout",
                    last_actions=[s["action"] for s in steps_detail[-5:]],
                    is_placeholder=not bool(data),
                    replay_steps=replay_steps if replay_ok else None,
                )

            # Browser liveness check — if the Playwright driver crashed (e.g. a
            # Node.js TypeError in the Firefox backend), page.url raises. Catch
            # it here so the loop exits cleanly rather than spinning until
            # engine.stop() is called and hangs on the dead process.
            try:
                page = fantoma._engine.get_page()
                current_url = page.url
            except Exception as e:
                log.warning("Browser connection lost at step %d: %s", step_num, e)
                tail = [s["action"] for s in steps_detail[-5:]]
                return NavigatorResult(
                    status="failed",
                    data="Browser connection lost",
                    steps_taken=step_num,
                    steps_detail=steps_detail,
                    final_url="",
                    failure_reason="browser_crashed",
                    last_actions=tail,
                    is_placeholder=True,
                )
            visited_urls.add(_norm_url(current_url))

            self._meter.start(step_num)

            # One ARIA read per step, shared by the before-snapshot used for
            # the change_line diff, the element list and the content summary.
            # Invalidated after every action below.
            step_snap = fantoma._dom.begin_step(page)
            snap_before = aria_snapshot(page, step_snap)

            # Get filtered DOM
            aria = fantoma._dom.extract(page, task=subtask.instruction, mode=dom_mode)
            aria = store.mask(aria, current_url)
            usable = store.names_for(current_url)

            # Get page content for state tracking
            try:
                last_content = fantoma._dom.extract_content(page)[:800]
            except Exception:
                last_content = ""

            # Build prompt
            system = NAVIGATOR_SYSTEM.format(
                instruction=subtask.instruction,
                done_when=subtask.done_when,
                untrusted_note=UNTRUSTED_NOTE,
            )
            prefix = f"{nudge}\n\n" if nudge else ""
            nudge = ""  # shown once; the model gets a fresh look at the page
            # Only the placeholders usable on this site are named; secrets
            # scoped to other sites are not listed at all.
            secrets_line = ("Secrets you may type here: "
                            + ", ".join(f"<secret:{n}>" for n in usable) + "\n\n") if usable else ""
            user_msg = (f"{prefix}{secrets_line}Change: {change_line}\n\nPage ({current_url}):\n"
                        f"{wrap_untrusted(aria, current_url)}")
            if getattr(fantoma, "_webmcp", False) is True:
                block = fantoma.webmcp_prompt()
                for name, value in sensitive_data.items():
                    block = block.replace(value, f"<secret:{name}>")
                user_msg += block

            messages = [
                {"role": "system", "content": system},
                {"role": "user", "content": user_msg},
            ]
            self._meter.prompt_chars = len(system) + len(user_msg)

            raw = llm.chat(messages, max_tokens=300)
            actions = _parse_actions(raw) if raw else []

            # Track consecutive empty/unparseable responses. After 2 in a row,
            # bail out with failure_reason="llm_empty" so the orchestrator
            # can replan or escalate to a stronger model.
            if not actions:
                empty_streak += 1
                log.info("Empty/unparseable LLM response (streak=%d)", empty_streak)
                if empty_streak >= 2:
                    return NavigatorResult(
                        status="failed",
                        data="LLM produced no parseable actions",
                        steps_taken=step_num,
                        steps_detail=steps_detail,
                        final_url=current_url,
                        failure_reason="llm_empty",
                        last_actions=[s["action"] for s in steps_detail[-5:]],
                        is_placeholder=True,
                    )
                continue
            empty_streak = 0

            stale = set()   # positions in `actions` whose target is gone
            for pos, (action_type, params) in enumerate(actions):
                if pos in stale:
                    continue
                if action_type == "done":
                    # A task that asks the agent to *do* something is not
                    # finished until something was actually done. Weak models
                    # routinely announce DONE on arriving at the right page —
                    # measured live: "add the backpack to the cart" reached the
                    # product listing and stopped, cart untouched. The prompt
                    # already asks the model to check this; prompts do not bind
                    # weak models, so the check lives here instead.
                    if (requires_action and not state_changed
                            and done_rejections < _MAX_DONE_REJECTIONS):
                        done_rejections += 1
                        log.info(
                            "Rejected DONE: task needs an action but none has "
                            "succeeded yet (rejection %d/%d)",
                            done_rejections, _MAX_DONE_REJECTIONS,
                        )
                        nudge = (
                            "NOT DONE: this task requires you to act on the page, "
                            "and no click, typing or selection has succeeded yet. "
                            "Find the control that performs the task and use it. "
                            "Only say DONE once the page reflects the change."
                        )
                        break  # abandon the rest of this batch, re-prompt

                    # The model saying DONE is a claim; the page is the
                    # evidence. Both 7B and 14B models reported success on
                    # an untouched dropdown, an empty box and a rejected
                    # login. Tell the model what the page actually shows.
                    if (spec is not None and spec.is_state_changing
                            and done_rejections < _MAX_DONE_REJECTIONS):
                        verdict = self._check_done(spec, fantoma)
                        if verdict:
                            done_rejections += 1
                            log.info("Rejected DONE: %s", verdict)
                            nudge = (f"NOT DONE: {verdict}. Look at the page "
                                     "again and fix it before saying DONE.")
                            break

                    data = self._extract_answer(subtask, fantoma, llm)
                    return NavigatorResult(
                        status="done", data=data, steps_taken=step_num,
                        steps_detail=steps_detail, final_url=current_url,
                        replay_steps=replay_steps if replay_ok else None,
                    )

                # Navigation-loop guard: if the model keeps bouncing back to a
                # page it already left (e.g. re-doing a login it already
                # completed), stop here WITHOUT navigating away — the current
                # page is the likely goal — and extract from it.
                if action_type == "navigate":
                    target = _norm_url(params.get("url", ""))
                    live_url = _norm_url(fantoma._engine.get_page().url)
                    if target and target in visited_urls and target != live_url:
                        nav_loop_targets[target] = nav_loop_targets.get(target, 0) + 1
                        if nav_loop_targets[target] >= 2:
                            log.info("Navigation loop to %s — stopping on current page", target)
                            data = self._extract_answer(subtask, fantoma, llm)
                            tail = [s["action"] for s in steps_detail[-5:]]
                            return NavigatorResult(
                                status="stagnant", data=data or "Navigation loop",
                                steps_taken=step_num, steps_detail=steps_detail,
                                final_url=fantoma._engine.get_page().url,
                                failure_reason="navigation_loop", last_actions=tail,
                                is_placeholder=not bool(data),
                                replay_steps=replay_steps if replay_ok else None,
                            )

                # Build the trace/log description from the MASKED params, before
                # re-substituting real secrets — so passwords and 2FA codes never
                # reach steps_detail or the log file (params still holds the
                # <secret:name> placeholders at this point).
                action_desc = f"{action_type}({params})"

                # The task names the item to act on, and the element list
                # already records which item each repeated control belongs
                # to, so the right control can be resolved here instead of
                # relying on the model to read a hint. Weak models take the
                # first match: asked for the Fleece Jacket, the agent added
                # the first product on the page. Correcting in code makes
                # that choice impossible rather than merely discouraged.
                if target and "element_id" in params:
                    try:
                        better = fantoma._dom.find_index_for_target(
                            params["element_id"], target)
                    except Exception:
                        better = None
                    if better is not None:
                        log.info(
                            "Retargeted %s from [%s] to [%s] for %r",
                            action_type, params["element_id"], better, target,
                        )
                        params["element_id"] = better

                # Capture a replayable signature + masked value BEFORE secret
                # substitution, so cached plans store the <secret:name>
                # placeholder, never the real credential.
                replay_sig = None
                if "element_id" in params:
                    try:
                        replay_sig = fantoma._dom.signature(params["element_id"])
                    except Exception:
                        replay_sig = None
                replay_value = (
                    params.get("text") or params.get("value")
                    or params.get("direction") or params.get("key")
                    or params.get("url")
                )

                # Re-read the URL right now: a redirect since the step began
                # must not let a secret be typed on the wrong site.
                refused = []
                if "text" in params:
                    try:
                        fill_url = fantoma._engine.get_page().url
                    except Exception:
                        fill_url = ""
                    params["text"], refused = store.reveal(params["text"], fill_url)

                method = getattr(fantoma, action_type)
                try:
                    pre_action_url = fantoma._engine.get_page().url
                except Exception:
                    pre_action_url = ""
                if refused:
                    msg = refusal_message(refused)
                    log.warning("Action %s refused: %s", action_desc, msg)
                    result = {"success": False, "notes": [msg]}
                    outcome = "REFUSED"
                else:
                    fantoma._dom.invalidate()
                    try:
                        result = method(**params)
                        outcome = "OK" if result["success"] else "FAILED"
                    except Exception as e:
                        log.warning("Action %s failed: %s", action_desc, store.mask(str(e)))
                        result = {"success": False}
                        outcome = "ERROR"

                steps_detail.append({
                    "step": step_num, "action": action_desc,
                    "success": result.get("success", False),
                    "url": fantoma._engine.get_page().url,
                })

                # Capture the successful action for the cache. An element action
                # without a resolvable (role, name) can't be replayed safely, so
                # the whole plan is marked non-cacheable.
                if result.get("success", False):
                    if action_type in _STATE_CHANGING:
                        state_changed = True
                    needs_sig = "element_id" in params
                    if action_type == "webmcp_call":
                        replay_ok = False  # page tools are not replayable from a cache
                    valid_sig = isinstance(replay_sig, (tuple, list)) and len(replay_sig) == 2
                    if needs_sig and not valid_sig:
                        replay_ok = False
                    else:
                        rs = {"action": action_type, "role": "", "name": "", "value": replay_value}
                        if valid_sig:
                            rs["role"], rs["name"] = replay_sig
                        replay_steps.append(rs)

                # ARIA diff: snapshot after action and compare with step-start state.
                # Produces a semantic change summary the LLM already speaks
                # (role/name/value), replacing the raw MutationObserver output.
                structure_changed = False
                # The action may have changed the page: drop every cached read.
                fantoma._dom.invalidate()
                try:
                    snap_after = aria_snapshot(fantoma._engine.get_page(),
                                               fantoma._dom._snap_for(fantoma._engine.get_page()))
                    diff = aria_diff(snap_before, snap_after)
                    change_line = diff if diff else "No visible changes"
                    # Elements appearing or disappearing means the numbering the
                    # model was given no longer describes the page. A value
                    # changing does not — typing into a field leaves every
                    # element where it was, which is why the whole batch is not
                    # abandoned on any change at all.
                    structure_changed = set(snap_after) != set(snap_before)
                    snap_before = snap_after  # track across multi-action batches
                except Exception:
                    change_line = "No changes detected"
                notes = result.get("notes") if isinstance(result, dict) else None
                if notes:
                    change_line = f"{change_line} | " + " ".join(notes)

                # Element numbers used to be positions in a list that was
                # rebuilt on every read, so any action after a re-render acted
                # on whatever now sat at that number. Measured live: "add the
                # Fleece Jacket" fired two clicks on index 0; the first added
                # the jacket, the page re-rendered, and the second added a
                # different product. The whole rest of the batch was dropped.
                #
                # A number now follows the element it was given to (role, name
                # and position among look-alikes; see dom/refs.py), so each
                # queued action is checked on its own: it stays if its target
                # still resolves to exactly one element, and goes if the target
                # is gone or has become ambiguous. If the page itself changed
                # (new URL), nothing from the old page applies and all of the
                # rest go, as before.
                #
                # Playwright MCP and agent-browser both require a re-snapshot
                # before the next reference is used; Stagehand and Skyvern act
                # once per observation. Here a stale reference is re-checked
                # against the live page instead of being trusted or discarded.
                if structure_changed and action_type in _STATE_CHANGING:
                    queued = [(k, a) for k, a in enumerate(actions)
                              if k > pos and k not in stale]
                    if queued:
                        self._screen_queue(fantoma, queued, stale, action_type,
                                           pre_action_url)

                # Update state tracker with the POST-action DOM content, not
                # the stale once-per-round snapshot. Using last_content here
                # made a multi-action batch (TYPE, TYPE, TYPE on a form — the
                # exact pattern the prompt recommends) write three identical
                # fingerprints and trip a false "dom_stagnant" abort on the
                # subtask's very first round.
                post_action_url = fantoma._engine.get_page().url
                try:
                    post_content = fantoma._dom.extract_content(
                        fantoma._engine.get_page()
                    )[:800]
                except Exception:
                    post_content = last_content
                tracker.add(
                    post_action_url,
                    post_content,
                    f"{action_desc} -> {outcome}",
                )

                # Check domain drift immediately after every action, not just
                # at end-of-batch. A bait-click that redirects to a partner
                # site must break the remaining actions in the batch so we
                # don't keep interacting with the wrong domain.
                if self._is_domain_drift(post_action_url, start_domain):
                    log.info("Domain drift detected mid-batch: %s", post_action_url)
                    tail = [s["action"] for s in steps_detail[-5:]]
                    return NavigatorResult(
                        status="failed", data=f"Domain drift to {post_action_url}",
                        steps_taken=step_num, steps_detail=steps_detail,
                        final_url=post_action_url,
                        failure_reason="domain_drift", last_actions=tail,
                        is_placeholder=True,
                    )

                if not result.get("success", False):
                    break

            tail = [s["action"] for s in steps_detail[-5:]]

            # Check for blockers (rate limit, login wall, CAPTCHA)
            try:
                blocker = classify_blocker(fantoma._engine.get_page())
            except Exception:
                blocker = None
            if blocker:
                log.info("Blocker detected: %s (step %d)", blocker, step_num)
                data = self._extract_answer(subtask, fantoma, llm)
                return NavigatorResult(
                    status="blocked", data=data or f"Blocked: {blocker}",
                    steps_taken=step_num, steps_detail=steps_detail,
                    final_url=fantoma._engine.get_page().url,
                    failure_reason=blocker, last_actions=tail,
                    is_placeholder=not bool(data),
                )

            # Check stagnation
            should_stop, reason = tracker.should_stop()
            if should_stop:
                log.info("Navigator stopping: %s (step %d)", reason, step_num)
                data = self._extract_answer(subtask, fantoma, llm)
                return NavigatorResult(
                    status="stagnant", data=data or f"Stopped: {reason}",
                    steps_taken=step_num, steps_detail=steps_detail,
                    final_url=fantoma._engine.get_page().url,
                    failure_reason=reason, last_actions=tail,
                    is_placeholder=not bool(data),
                )

            # Check domain drift
            current_url = fantoma._engine.get_page().url
            if self._is_domain_drift(current_url, start_domain):
                log.info("Domain drift detected: %s", current_url)
                return NavigatorResult(
                    status="failed", data=f"Domain drift to {current_url}",
                    steps_taken=step_num, steps_detail=steps_detail,
                    final_url=current_url,
                    failure_reason="domain_drift", last_actions=tail,
                    is_placeholder=True,
                )

        # Extract whatever is on the page before giving up
        data = self._extract_answer(subtask, fantoma, llm)
        tail = [s["action"] for s in steps_detail[-5:]]
        return NavigatorResult(
            status="max_steps", data=data or "Step budget exhausted",
            steps_taken=max_steps, steps_detail=steps_detail,
            final_url=fantoma._engine.get_page().url,
            failure_reason="max_steps", last_actions=tail,
            is_placeholder=not bool(data),
        )

    @staticmethod
    def _screen_queue(fantoma, queued, stale: set, after: str, start_url: str) -> None:
        """After the page changed, mark queued actions whose target is gone.

        queued is a list of (position, (action_type, params)). Positions of
        actions to skip are added to `stale`. Actions with no element target
        (navigate, scroll, a bare key press) stay unless the page moved to a
        different URL, in which case everything queued for the old page goes.
        """
        page = fantoma._engine.get_page()
        try:
            moved = page_key(page.url) != page_key(start_url)
        except Exception:
            moved = True
        dropped = []
        for k, (kind, params) in queued:
            if moved:
                stale.add(k)
                dropped.append(f"{kind}[{params.get('element_id', '-')}] (new page)")
                continue
            if "element_id" not in params:
                continue
            n = params["element_id"]
            ok = False
            try:
                ok = fantoma._dom.can_resolve(page, n)
            except Exception:
                ok = False
            if not ok:
                stale.add(k)
                dropped.append(f"{kind}[{n}] (gone or ambiguous)")
        kept = len(queued) - len(dropped)
        log.info(
            "Page structure changed after %s: keeping %d queued action(s) whose "
            "target still resolves, dropping %d%s",
            after, kept, len(dropped),
            (": " + ", ".join(dropped)) if dropped else "")

    @staticmethod
    def _check_done(spec, fantoma) -> str:
        """Why the page says the task is not done yet, or "" if it looks done."""
        from fantoma.task_spec import verify_outcome
        try:
            page = fantoma._engine.get_page()
            ok, reason = verify_outcome(spec, page.url, fantoma.get_state().get("aria_tree", ""))
        except Exception:
            return ""
        return "" if ok else reason

    def _extract_answer(self, subtask: Subtask, fantoma, llm) -> str:
        """Extract answer from current page when a subtask reaches a stop state.

        Uses raw body inner_text rather than the ARIA accessibility tree
        because answer extraction needs human-readable page content, not a
        navigation-oriented element list. `_dom.extract_content` frequently
        returned <200 chars of nav links for content pages (Wikipedia,
        Cambridge Dictionary), leaving the LLM with nothing to report.
        """
        try:
            page = fantoma._engine.get_page()
            # The whole page as Markdown, without text a person cannot see
            # (where planted instructions hide). Whole page rather than main
            # content only: answers live in sidebars and infoboxes too. Capped
            # at ~12k chars to fit a small model's context.
            rendered = page_to_markdown(page, main_only=False, include_links=False,
                                        max_chars=12000)
            body = rendered["markdown"] or aria_text_fallback(page)[:12000]
            body = getattr(self, "_secrets", SecretStore()).mask(body, page.url)
            title = rendered["title"]
            content = wrap_untrusted(f"Page title: {title}\nURL: {page.url}\n\n{body}", page.url)
            log.info(
                "Extract: body=%d chars title=%r url=%s",
                len(body), title[:80], page.url,
            )
            messages = [
                {"role": "system", "content": EXTRACT_ON_DONE},
                {"role": "user", "content": f"Task: {subtask.instruction}\n\nPage content:\n{content}"},
            ]
            answer = llm.chat(messages, max_tokens=1000) or ""
            log.info("Extract: answer=%d chars, preview=%r", len(answer), answer[:150])
            return answer
        except Exception as e:
            log.warning("Extract answer failed: %s", e)
            return ""

    @staticmethod
    def _is_domain_drift(current_url: str, start_domain: str) -> bool:
        """Check if current URL has drifted from the expected domain."""
        if not start_domain:
            return False
        try:
            current = urlparse(current_url).netloc.lower()
            start = start_domain.lower()
            # Allow subdomain matching: www.amazon.com matches amazon.com
            return not (current == start or current.endswith("." + start) or start.endswith("." + current))
        except Exception:
            return False
