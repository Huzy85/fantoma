# Changelog

## 0.9.0 — 2026-07-25

### Added

- **MCP server** (`fantoma/mcp_server.py`). Any MCP client — Claude Code, Claude Desktop, Cursor, or your own agent — can drive a browser directly. Four tools: `fantoma_run`, `fantoma_login`, `fantoma_extract`, `fantoma_health`. Runs over stdio or streamable HTTP. Install with `pip install "fantoma[mcp]"`, or use the `fantoma-mcp` console script.
- **Backend pool with failover.** Browser backends are single-session, so the MCP server hands out one per call and blocks when they are all busy. List several with `FANTOMA_MCP_BACKENDS` to raise concurrency. A call that meets a restarting backend moves to another one immediately instead of waiting; only the final attempt waits, so a single-backend setup still behaves correctly.
- **Live site matrix** (`tools/live_site_matrix.py`). 19 real sites across five difficulty tiers, run through the pool. Each case checks the answer contains something only the correct page would say, so a confident wrong answer counts as a failure. First run: 16/19 in 145s, with protected sites 5/5, JS-heavy 3/3 and docs 4/4.

### Fixed

- **Sessions could not be reused after `stop()`.** `stop()` ran Camoufox's `__exit__` on a worker thread, but Playwright's sync API is thread-bound, so the call failed silently: the driver was never shut down, one browser leaked per session, and the next `start()` raised "Sync API inside the asyncio loop". `__exit__` now runs on the calling thread under a watchdog that kills the driver if it blocks, and is skipped entirely when the driver process is already gone.
- **`/run` after `/login` broke the server.** `/login` and `/start` leave a session open by design, and `/run` then built a second browser on the same thread. Two live sync Playwright instances per thread is not possible. `/run` now releases any existing session first.
- **A crashed browser driver poisoned the worker permanently.** The Playwright Firefox driver dies on some pages (a page JS error with no location kills its bundle), leaving the sync API bound to a dead transport with nothing repairable in-process. Unrecoverable states now return 503 with `retryable: true` and exit, so the supervisor supplies a clean worker.
- **Hung requests are now bounded.** Playwright can hang rather than raise, and a hung worker is worse than a crashed one — a process supervisor sees RUNNING and leaves it alone while the container serves timeouts. Every request runs under a watchdog (75s for `/start`, longer for task endpoints, and always above a client-supplied `timeout`).
- **Extraction could return a previous page's content as a confident answer.** `/start` answers 409 when a session already exists; the MCP extract flow ignored the status and read whatever page was loaded, reporting `success: true`. Observed live: a request for bbc.co.uk/news returned "Example Domain". The session is now cleared before every start, and a start that returns no page state fails loudly instead of guessing.
- **22 long-standing test failures.** The landmark and DOM-mode suites had been red since June. The cause was a stale fixture, not a broken feature: the mock page never stubbed `page.evaluate`, so once `extract_aria` gained viewport scroll hints every test raised `TypeError` before reaching any landmark logic. Suite is now 575 passing, zero failing.

### Changed

- README rewritten to lead with the architecture — accessibility tree rather than screenshots, and the token economics that make small local models practical — instead of "undetectable". Two claims that would not survive scrutiny were corrected: accessibility law does not grant automated access a legal right, and no automation is undetectable against session-level behavioural scoring.
- Added `What It's For` and `Responsible Use` sections, and a `Reliability` section documenting crash-only recovery, the request watchdog and failover.
- `/health` deliberately does not start a browser. That is documented now, because a green health check means the HTTP server is up, not that the browser works.

### Notes

Driver crashes are intermittent rather than tied to particular sites. Pages that repeatedly killed a single backend load fine once there is somewhere to fail over to. Running at least two backends is the difference between a crash being a retry and a crash being a failure.

## 0.7.0 — 2026-04-01

Tool/agent separation: `Fantoma` (browser tool, no LLM required) and `Agent` (LLM loop). Docker image with HTTP API and a noVNC manual-intervention hatch.
