# Changelog

## 0.11.0 — 2026-09-24

### Added

- **Common steps are done in code, before any model is asked.** Log in, search, pick a dropdown option, type into a field, tick a checkbox, add a named item to the cart and click a named link run without a model when the page leaves no doubt, and the page is checked afterwards. The model gets only what is left and is told what is already done. Measured on GitHub's machines with qwen2.5 7B and 14B: every task in the live check that fits these steps now passes with zero model steps, including searches on DuckDuckGo and Etsy. `fast_path=False` or `FANTOMA_FAST_PATH=0` turns it off.
- **Anti-Captcha and CapMonster**, alongside CapSolver and 2Captcha. `FANTOMA_CAPTCHA_API` / `FANTOMA_CAPTCHA_KEY` configure a service once for the library, the MCP server and the HTTP server.

### Fixed

- A model saying it is done is checked against the page. Before, 7B and 14B models reported success on an untouched dropdown, an empty box and a rejected login. When the page disagrees the model is told why.
- A visible password box after a login attempt now means the login failed (a failed saucedemo login stays on `/`, which was read as success). A select task is judged by what the dropdown holds, not by the option being listed. A click task is no longer failed because the clicked button went away.
- Labelled fields show what was typed; password fields show dots, never their contents.
- 2Captcha was sent CapSolver's task-type names; each service now gets its own. A solver error stops polling instead of waiting out the timeout.

## 0.10.0 — 2026-09-24

### Added

- **Read any page as clean Markdown, no LLM.** `fantoma read URL` on the command line, `Fantoma.read()`, `POST /read` and the `fantoma_read` MCP tool return the page as Markdown (headings, nested lists, tables, code blocks, quotes, links, open shadow DOM, a dropdown's current choice) plus every link on it, absolute and de-duplicated. `main_only` (default on) drops navigation, header, footer, aside and cookie banners; `selector` narrows to one element; `max_chars` cuts on a paragraph boundary.
- **The MCP server runs its own browser.** With `FANTOMA_MCP_BACKENDS` unset, `fantoma-mcp` drives a browser in-process, so an MCP client needs one command and no Docker. `FANTOMA_LLM_URL`, `FANTOMA_LLM_MODEL` and `FANTOMA_LLM_API_KEY` configure the model for `fantoma_run` and `fantoma_extract`; `fantoma_read` and `fantoma_login` need none. In this mode read, extract and login share one session, so a login carries over. Setting `FANTOMA_MCP_BACKENDS` keeps the Docker backends as before.
- **Prompt-injection defences** (`fantoma/safety.py`). Text a person cannot see never reaches a model (`display:none`, `visibility:hidden`, `opacity:0`, `aria-hidden`, `[hidden]`, zero font size, clipped or far off-screen boxes, zero-width and bidi characters). Page text sent to the navigator, planner, answer extraction and `extract()` is fenced in `<untrusted_web_content>` with a random id the page cannot forge, and each system prompt says fenced text is data. `read()` returns `injection_warnings`: excerpts that read like instructions aimed at an AI.
- **Allowed and blocked domains**, enforced by the browser on every request, not only navigation: `Fantoma(allowed_domains=[...], blocked_domains=[...])` or `FANTOMA_ALLOWED_DOMAINS` / `FANTOMA_BLOCKED_DOMAINS`. A page that talks the agent into loading a URL on another host sends nothing.
- **Block-page detection.** `read()` reports `blocked` as `bot_challenge`, `captcha`, `rate_limited`, `access_denied`, `http_error`, `login_wall` or `empty` instead of returning an interstitial as if it were the page.
- **JSON Schema extraction.** `extract()` and `fantoma_extract` accept a real JSON Schema; an object schema returns an object. The older flat `{field: type}` map still returns a list.

### Fixed

- **Found in review before release, and fixed:** the domain policy was bypassed by a redirect from an allowed host and by WebSockets, and ignored Unicode host names; nested web components repeated their text 2^depth times and leaked unslotted text; a failed page walk fell back to raw text that includes hidden text; `selector` pointing at a nav or footer returned nothing, and a bad selector silently returned the whole page; accordion headings built from buttons vanished; SVG icon titles and styles leaked into text; zero-size positioned wrappers hid their visible children; scroll-revealed content was missed (read() now scrolls first); normal short pages were flagged as blocked ("$1,429" read as a 429, "Reference #…" as access denied, the "protected by reCAPTCHA" notice as a captcha, "500 Startups" as an HTTP error); JSON replies with a list inside an object were parsed as the list; a custom dropdown could pick a same-named option from an unrelated native select; an MCP setup relying on the old default backend address silently switched to the built-in browser (it now uses a backend that answers there).
- **`pip install "fantoma[mcp]"` installed an MCP SDK the server could not import.** MCP 2.x renamed `FastMCP`; the extra is now pinned below 2.
- **Native dropdowns never worked for an agent (0/3 on the dropdown flow).** Small models click the `<option>` they want, which does nothing inside a closed native `<select>` in Firefox. Such a click now performs the select. `select()` matches label, then value, then case-insensitively, and opens scripted ARIA dropdowns. A dropdown now shows its current choice, its options are listed directly beneath it, and `[selected]` is no longer lost when an option is also `[disabled]`.
- **A successful select was reported to the model as "No visible changes".** The change line compared only the first state flag and never tracked `[selected]`, and repeated or unnamed controls shared one key, so a change to the second of two checkboxes was invisible too.
- **Controls behind a modal were offered to the model, and the filter meant to hide them never ran.** Its script was called with two arguments where Playwright passes one, so it raised on every element and kept everything. It had also been applied after the list was numbered, which would have shifted every later element one slot. It now runs before numbering, and hides a control only when an overlay (a fixed or sticky layer, or a modal dialog) sits on top of it, so a label drawn over a styled checkbox no longer counts as covering it.
- **Unnamed controls could resolve to a named one.** A bare checkbox was looked up among every checkbox on the page, so with "Agree to terms" and two unlabelled boxes, the second unlabelled box resolved to "Agree to terms". Unnamed controls are now counted among unnamed controls only.
- **Name lookup was a substring match.** `Add` also matched `Add to cart`, so the position among same-named controls was counted over the wrong set. Exact match is tried first.
- **Page furniture outranked the task's controls.** A "Fork me on GitHub" ribbon held slot `[0]` on a checkbox page; a cheap model clicked it and left. Links to other sites and controls in the header, footer or navigation now lose a point unless they match the task, and ranking applies whenever the list is capped, not only when a task is given.
- **Read mode dropped almost all page text.** Unquoted snapshot lines such as `- paragraph: words` matched no pattern, so paragraphs, list items, table rows and footers vanished and content mode returned headings and links only.
- **`treeitem`, `menuitemcheckbox` and `menuitemradio` could never be acted on.** They were in neither the interactive nor the skipped role set.
- **The server flattened JSON Schemas.** `/extract` turned `{"type": "object", "properties": ...}` into `{"type": str, "properties": str}`, so the schema form the MCP tool documented never worked.
- **The Docker image fetched a browser for a different Camoufox release** than the one the package pins, then downgraded the package underneath it. The Dockerfile now pins the same version.
- **A model served from another machine never had thinking turned off.** Only URLs containing `localhost` or `127.0.0.1` were treated as self-hosted, so a Qwen-style thinking model on a LAN address, a Docker service name or a Tailscale address reasoned through its whole token budget on every step. Private, loopback, link-local, CGNAT and `.local`/`.internal` hosts and bare hostnames now count as self-hosted; `FANTOMA_LLM_SELF_HOSTED=1/0` overrides.
- **Four CAPTCHA tests failed on Python below 3.13**, where logging calls `time.time()` and exhausted a three-item mock.

### Also in 0.10.0 (landed on main after 0.9.0)

#### Fixed

- **Unnamed form controls never reached the model.** The element list required a non-empty accessible name, so a bare `- checkbox` in the ARIA snapshot was discarded. On a page whose only task was "tick the first checkbox", the two elements offered were a GitHub ribbon and a footer link: the task was not hard, it was impossible, on every model. Form-control roles (checkbox, radio, textbox, combobox, searchbox, switch, spinbutton, slider) are now listed when unnamed and labelled from the adjacent text node, rendered as `(in: ...)` because per W3C AccName a neighbour does not contribute to an accessible name. Link, button, menuitem, option and tab are deliberately still excluded when unnamed — an unnamed one of those is usually an icon and admitting them floods the list.
- **Unnamed elements with children were dropped entirely.** The snapshot appends `:` to any element that has children, and the no-name pattern anchored to end-of-string, so `- combobox:` matched nothing. Every unlabelled `<select>` on the web was invisible; on the dropdown page the model was shown the three options but never the control to select on.
- **A field disappeared the moment it was filled.** Once a value is present the snapshot writes `- spinbutton: "42"`, which matched neither pattern. The model typed a value and the box it had just used vanished from the next observation, leaving it unable to confirm the value landed or to correct a mistake.
- **Only the first `[...]` group on a line was kept.** `option "Please select an option" [disabled] [selected]` retained `disabled` and silently discarded `selected`. Selection state is the entire feedback signal for "did my choice take effect", so a successful select was indistinguishable from a no-op and agents re-tried choices they had already made. All attribute groups are now collected, and `[selected]` is rendered.
- **Unnamed elements lost their state.** Attributes on a no-name line were returned as a raw string rather than parsed, so `parsed.get("checked")` was always falsey and an unnamed checkbox rendered without `[checked]` — which is the one fact "tick it if it is not already ticked" depends on.

#### Changed

- **`camoufox` is pinned to an exact version.** It was `>=0.4`, so a routine image rebuild moved 0.4.11 to 0.5.4 with nobody choosing it. The one dependency the anti-detection behaviour rests on should not change silently; bump it deliberately and re-run the protected-site checks when you do.
- **README claims now match measurement.** Reading pages works on a small local model — 30/31 real sites for a 35B local MoE, matching the best cheap API model and beating most. Multi-step interaction does not: the same model completed 1 of 6 login and form flows where a frontier model completed all 6. The distinction is now stated up front rather than implied away.
- **Flow test checkpoints verify the live page.** Three of them asserted that the URL still contained the path the flow *started* on, so they were true before the agent acted: a model that errored on every call and issued no actions at all scored 4 of 6. They now read `[checked]`, `[selected]` and the field value off the live ARIA tree via `keep_session`. A `herokuapp-inputs` flow was added.

## 0.9.0 — 2026-07-25

### Added

- **MCP server** (`fantoma/mcp_server.py`). Any MCP client — Claude Code, Claude Desktop, Cursor, or your own agent — can drive a browser directly. Four tools: `fantoma_run`, `fantoma_login`, `fantoma_extract`, `fantoma_health`. Runs over stdio or streamable HTTP. Install with `pip install "fantoma[mcp]"`, or use the `fantoma-mcp` console script.
- **Backend pool with failover.** Browser backends are single-session, so the MCP server hands out one per call and blocks when they are all busy. List several with `FANTOMA_MCP_BACKENDS` to raise concurrency. A call that meets a restarting backend moves to another one immediately instead of waiting; only the final attempt waits, so a single-backend setup still behaves correctly.
- **Structured task parsing** (`fantoma/task_spec.py`). A task sentence is parsed once into action, target and values, so the navigator matches a named target rather than re-interpreting prose every step. Extraction is deterministic — quoted text is the target in nearly every human-written task, and code finds it more reliably than a small model. An LLM is consulted only when there are no quotes, and its answer is rejected unless it appears verbatim in the task.
- **Outcome verification.** The finished page is checked against the parsed target and a failed check flips `success` to `False`, surfaced as `verified` and `verify_reason`. Verification is fail-open: an unparsed task, or a browser that has already closed, reports ok rather than inventing a failure.
- **`keep_session`** on `/run` (`Agent.run(keep_open=True)`) leaves the browser on the final page so callers can inspect what a run actually did, plus `final_url`, `final_title` and `steps_detail` on the result.
- **Inferred labels for repeated controls.** A product grid names every button "Add to cart", so each is rendered with the item it belongs to as `(in: ...)`, marked as inferred because per W3C AccName a neighbour does not contribute to an accessible name. Relevance scoring reads that context too, since a button called "Add to cart" matches none of the task's keywords.
- **Flow and agentic test harnesses** (`tools/live_flow_test.py`, `tools/live_agentic_test.py`) covering multi-step journeys graded on the browser's end state with partial credit per checkpoint.
- **Live site matrix** (`tools/live_site_matrix.py`). 19 real sites across five difficulty tiers, run through the pool. Each case checks the answer contains something only the correct page would say, so a confident wrong answer counts as a failure. First run: 16/19 in 145s, with protected sites 5/5, JS-heavy 3/3 and docs 4/4.

### Fixed

- **Sessions could not be reused after `stop()`.** `stop()` ran Camoufox's `__exit__` on a worker thread, but Playwright's sync API is thread-bound, so the call failed silently: the driver was never shut down, one browser leaked per session, and the next `start()` raised "Sync API inside the asyncio loop". `__exit__` now runs on the calling thread under a watchdog that kills the driver if it blocks, and is skipped entirely when the driver process is already gone.
- **`/run` after `/login` broke the server.** `/login` and `/start` leave a session open by design, and `/run` then built a second browser on the same thread. Two live sync Playwright instances per thread is not possible. `/run` now releases any existing session first.
- **A crashed browser driver poisoned the worker permanently.** The Playwright Firefox driver dies on some pages (a page JS error with no location kills its bundle), leaving the sync API bound to a dead transport with nothing repairable in-process. Unrecoverable states now return 503 with `retryable: true` and exit, so the supervisor supplies a clean worker.
- **Hung requests are now bounded.** Playwright can hang rather than raise, and a hung worker is worse than a crashed one — a process supervisor sees RUNNING and leaves it alone while the container serves timeouts. Every request runs under a watchdog (75s for `/start`, longer for task endpoints, and always above a client-supplied `timeout`).
- **Extraction could return a previous page's content as a confident answer.** `/start` answers 409 when a session already exists; the MCP extract flow ignored the status and read whatever page was loaded, reporting `success: true`. Observed live: a request for bbc.co.uk/news returned "Example Domain". The session is now cleared before every start, and a start that returns no page state fails loudly instead of guessing.
- **The model's choice of element was discarded.** Resolution looked an element up by role and name and then took `.first`, so all six "Add to cart" buttons on a product grid resolved to the first product no matter which index was chosen. Each control now records its position among identically-named siblings, in DOM order before pruning reorders anything, and resolves with `locator.nth()`.
- **Repeated controls were deleted before the model saw them.** Interactive elements were deduplicated by `(role, name)`, keeping only the first, so five of six "Add to cart" buttons vanished and a specific item could not be chosen at all. Repetition noise is handled by relevance scoring and by capping the list, so the deduplication was redundant as well as destructive.
- **Queued actions reused stale element numbers.** Numbers are captured once per step, so any action after a re-render acted on whatever then sat at that number — two clicks on one index added two different products. A batch now stops when the element set changes; value-only changes do not stop it, so filling a form still works.
- **`changed` reported that nothing had happened after any in-page action.** It was a copy of `url_changed`, so clicking "Add to cart" on a single-page shop reported no change while the button had plainly become "Remove". It now compares the accessibility tree against the previous action's.
- **DONE was accepted without evidence.** A task that asks the agent to act is not finished until something was acted on; the agent announced completion on merely reaching the right page. DONE is now refused until a click, keystroke or selection has succeeded, twice at most so a stuck run still ends.
- **Verification passed runs with unwanted side effects.** Checking only that the target was present passed a run that added the right item and a wrong one. It now fails when more changed than was asked for.
- **22 long-standing test failures.** The landmark and DOM-mode suites had been red since June. The cause was a stale fixture, not a broken feature: the mock page never stubbed `page.evaluate`, so once `extract_aria` gained viewport scroll hints every test raised `TypeError` before reaching any landmark logic. Suite is now 659 passing, zero failing.

### Changed

- README rewritten to lead with the architecture — accessibility tree rather than screenshots, and the token economics that make small local models practical — instead of "undetectable". Two claims that would not survive scrutiny were corrected: accessibility law does not grant automated access a legal right, and no automation is undetectable against session-level behavioural scoring.
- Added `What It's For` and `Responsible Use` sections, and a `Reliability` section documenting crash-only recovery, the request watchdog and failover.
- `/health` deliberately does not start a browser. That is documented now, because a green health check means the HTTP server is up, not that the browser works.

### Notes

The element-resolution defect above is worth calling out: it silently discarded several other correct fixes made alongside it, because every improvement to how the page was presented was thrown away one line before the click. When a fix appears to have no effect, check that the fixed code ran at all — an action cache replaying a plan recorded while a bug was live will happily hide it.

Driver crashes are intermittent rather than tied to particular sites. Pages that repeatedly killed a single backend load fine once there is somewhere to fail over to. Running at least two backends is the difference between a crash being a retry and a crash being a failure.

## 0.7.0 — 2026-04-01

Tool/agent separation: `Fantoma` (browser tool, no LLM required) and `Agent` (LLM loop). Docker image with HTTP API and a noVNC manual-intervention hatch.
