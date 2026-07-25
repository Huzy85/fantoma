# Changelog

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
