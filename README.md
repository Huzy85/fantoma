# Fantoma

Browser automation for AI agents that reads pages the way a screen reader does — through the accessibility tree, not screenshots.

No vision model, no pixel coordinates, no mouse telemetry. Pages arrive as structured text, which costs roughly 500 tokens a step instead of the 1,000-5,000 a screenshot costs. That is what makes it practical to drive a browser with a small local model instead of a frontier API.

Two classes. Use whichever fits:

```python
from fantoma import Fantoma, Agent

# Tool API — drive the browser step by step
browser = Fantoma()
state = browser.start("https://news.ycombinator.com")
# state["aria_tree"] → feed to your LLM, get back an action
result = browser.click(3)
# result["state"]["aria_tree"] → updated page
browser.stop()

# Convenience API — describe a task, the agent does it
agent = Agent(llm_url="http://localhost:8080/v1")
result = agent.run("Go to github.com/trending and tell me the top repo")

# Login — no LLM needed
browser = Fantoma()
browser.start()
result = browser.login("https://github.com/login", email="me@example.com", password="...")
browser.stop()
```

![Fantoma Demo](fantoma_demo.gif)

## Getting Started

```bash
pip install fantoma
fantoma setup        # Guided wizard: pick your LLM, done
fantoma test         # Verify it works
```

**Need an LLM?** Install [Ollama](https://ollama.com), run `ollama pull phi3.5`, done. Works on CPU or GPU (8GB+ GPU recommended for speed). Or use a cloud API (OpenAI, Anthropic, DeepSeek) — the wizard handles it.

**Requirements:** Python 3.10+, Linux or macOS (Windows via WSL). No other dependencies — everything installs automatically.

## Use It From Any LLM (MCP)

Fantoma ships an MCP server, so any MCP client — Claude Code, Claude Desktop, Cursor, or your own agent — can drive a browser directly.

```bash
pip install "fantoma[mcp]"
python -m fantoma.mcp_server          # stdio
```

Register it with Claude Code:

```bash
claude mcp add fantoma -- python -m fantoma.mcp_server
```

Four tools:

| Tool | What it does |
|---|---|
| `fantoma_run` | Give it a task in plain English; it drives the browser to completion |
| `fantoma_login` | Fill and submit a login form using code alone — no LLM call, usually one step |
| `fantoma_extract` | Open a page and pull out data, optionally against a JSON Schema |
| `fantoma_health` | Which backends are reachable and how many tasks can run at once |

**Concurrency and failover.** Each browser backend serves one task at a time, so the MCP server hands out one backend per call and blocks when they are all busy. Run more containers to raise the ceiling:

```bash
export FANTOMA_MCP_BACKENDS=http://127.0.0.1:7860,http://127.0.0.1:7861
```

Backends restart themselves when their browser driver dies (see [Reliability](#reliability)). With more than one configured, a call that meets a restarting backend moves to another one immediately rather than waiting for it to come back. With a single backend it waits instead, since there is nowhere else to go.

Set `FANTOMA_API_KEY` if your backends are gated, and `FANTOMA_MCP_TRANSPORT=http` to serve over streamable HTTP instead of stdio.

## Plugging Things In

Fantoma defines the extension points and stays out of the way. It does not bundle every provider — a bundled integration is a dependency that rots, and you almost certainly want your own solver and your own proxies.

**Proxies.** Anything with a `.next()` returning a Playwright proxy dict works, so a custom pool needs no plumbing. A single proxy, a list, and a rotator are built in; for residential pools there is `ProxyPool`, which holds one exit IP and rotates only when it is actually blocked:

```python
from fantoma.browser.residential import ProxyPool

# One port per exit IP (the common residential idiom)
pool = ProxyPool.port_based(
    host="gw.your-provider.com", port_base=10000, port_span=100,
    username="user", password="pass", pin_gateway_ip=True,
)

# Or a session token inside the username
pool = ProxyPool.session_based(
    host="gw.your-provider.com", port=7000,
    username="customer", password="pass", sessions=50,
)

agent = Agent(proxy=pool)
pool.report_failure("429")   # too many strikes and it moves to a fresh IP
```

Rotating on every request is the wrong default for residential proxies: you throw away cookies, logins, and any anti-bot clearance tied to that IP. `ProxyPool` is sticky and rotates on failure.

**CAPTCHA solvers.** `APICaptchaSolver` reads from a `PROVIDERS` table, so adding one is a dict entry with its two endpoints. CapSolver and 2Captcha ship configured. There are also proof-of-work solvers (ALTCHA, Friendly Captcha — free, no service needed), a human-in-the-loop solver, and a Telegram solver.

**The coupling worth knowing about.** A CAPTCHA solution is bound to the exit IP it was solved through — clearance obtained on one IP is worthless from another. So a solver and a proxy pool are not independent plugins. Solve through the same proxy you then browse through, and treat "rotate the IP" and "discard the clearance" as a single action. `ProxyPool(on_rotate=...)` exists for that: register a callback that clears whatever was bound to the retired IP.

**Browser engines.** The accessibility layer reads whatever engine is underneath, so the stealth backend is swappable without touching the agent, planner or navigator. Camoufox is the default; Chromium via Patchright is `Agent(browser="chromium")`.

## Knowing It Worked

An agent describing its own work is not evidence. A run that added the wrong
item to a cart will report success just as confidently as one that got it
right, and nothing downstream can tell the difference.

Fantoma parses a task once into fields, then checks the finished page against
them:

```python
result = agent.run("Add the 'Sauce Labs Fleece Jacket' to the cart")
result.success        # False if the end state does not match the task
result.verified       # whether the check ran and what it concluded
result.verify_reason  # "Sauce Labs Fleece Jacket still shows an add-to-cart control"
result.final_url      # where the browser actually finished
```

Target extraction is deterministic — quoted text is the target in nearly every
task a person writes, and code finds it more reliably than a small model. An
LLM is consulted only when a task carries no quotes, and its answer is
discarded unless it appears verbatim in the task, so a model cannot substitute
something nobody asked for.

Verification is fail-open. A task it cannot parse, or a browser that has
already closed, reports ok rather than inventing a failure, so it can never
fail a run that actually worked.

Over HTTP, pass `keep_session: true` to `/run` to leave the browser on the
final page and inspect the end state yourself. `steps_detail` returns what the
agent actually did, step by step.

## Reliability

The Playwright Firefox driver Camoufox rides on can die outright: a page whose JavaScript throws an error with no location takes down the Node driver process. When that happens the sync API in that worker is bound to a dead transport, and every later request fails — killing the driver and installing a fresh event loop does not bring it back.

Fantoma treats this as crash-only rather than trying to repair it in place:

- A request that hits an unrecoverable browser state returns **503 with `retryable: true`** and the worker exits. The supervisor replaces it in about two seconds.
- Playwright can also **hang** instead of raising, which is worse: a process supervisor sees a healthy RUNNING process and leaves it alone, so the container serves timeouts indefinitely. Every request therefore runs under a watchdog (75s for `/start`, longer for task endpoints, and always longer than a client-supplied `timeout`). A request that outlives its budget takes the worker down.
- The MCP layer absorbs both, so callers see a slower call rather than an error. `/run` and `/login` are never retried at the transport level, because repeating them could submit a form twice.

A consequence worth knowing: `/health` deliberately does not start a browser, so a green health check means the HTTP server is up, not that the browser works. Use `fantoma_health` plus a real task if you need end-to-end assurance.

**In practice.** Driver crashes are intermittent rather than tied to particular sites. Pages that repeatedly killed a single backend load fine once there is somewhere to fail over to — a three-backend pool reads BBC News, example.com and iana.org in 46s total with every backend healthy afterwards. Running at least two backends is the difference between a crash being a retry and a crash being a failure. The underlying driver bug is upstream; the maintained [cloverlabs-camoufox](https://pypi.org/project/cloverlabs-camoufox/) fork is where a real fix would come from.

## What It Does

- **Gets through the gate** — login, signup, CAPTCHA solving. Code handles the forms, LLM handles the unexpected.
- **LLM as brain, code as hands** — Code matches form fields by label (fast, zero tokens). When it can't match, one LLM call labels all fields at once. Code fills based on the LLM's answer. Results cached in SQLite — LLM never called twice for the same site.
- **Signup forms** — fills first name, last name, email, username, password, confirm password. Clicks terms checkboxes. Tracks what's been filled to avoid double-submission.
- **25 real sites tested** (March 2026) — GitHub, HN, Etsy, eBay, Reddit, Discord, Spotify, and 18 more, with no detection events recorded in that run.
- [Camoufox](https://github.com/daijro/camoufox) anti-detection — passed bot.sannysoft.com and nowsecure.nl across 2,241 stress tests (March 2026). Detection moves; treat any such figure as a snapshot, not a guarantee.
- **ARIA + raw DOM** — always reads both. No form is invisible, even old-school HTML without ARIA labels.
- **Form Memory** — SQLite database records every login page. Gets smarter with every visit.
- **Universal form filling** — one approach for React, Vue, Angular, vanilla HTML. No framework detection.
- **Action caching (zero-LLM replay)** — a successful task plan is recorded as ARIA-node signatures `(role, name, value)` keyed by `(domain, task)`. Repeat the same task and Fantoma replays the actions with no navigation LLM calls, then re-extracts the answer (so dynamic results stay current). If the page changed and a step can't be resolved, replay falls back to a normal run and re-records. The biggest win for weak local models: a repeated task on a stable site costs ~zero tokens. On by default; disable with `Agent(action_cache=False)` or `FANTOMA_ACTION_CACHE=0`. Cached `type_text` values keep the `<secret:name>` placeholder, never the real credential.
- **ARIA snapshot diffing** — after each action the navigator snapshots the ARIA tree before and after, producing a compact `+ [button] "Submit"` / `- [alert] "Error"` / `~ [textbox] "Email": "" → "x"` diff. The LLM gets a semantic view of what changed rather than raw DOM mutation events. Capped at 8 lines per step.
- **Answer validator (opt-in)** — `Agent(validate=True)` or `FANTOMA_VALIDATE=1` runs a single LLM call at the end of a successful task to verify "does this answer satisfy the original question?" Returns YES/NO and a one-sentence reason. Result stored in `AgentResult.validated`. Fails open — any LLM error counts as passed so the task result is never discarded.
- **Wall-clock deadline** — `agent.run(deadline_s=N)` (default 300) fires at step boundaries so a slow or stalled LLM cannot run indefinitely. Wired correctly through the HTTP `/run` API endpoint via the `timeout` field.
- **Flat-first agent (v0.9)** — Two-phase execution. Phase 1 runs a flat reactive loop (20 steps by default) that handles 70-80% of tasks without any planning overhead. Phase 2 activates the hierarchical planner only if Phase 1 stalls, receiving full context (visited URLs, partial data, failure reason) so it doesn't repeat what was already tried. Simple tasks complete in Phase 1 alone. Complex tasks get the full planner treatment with the remaining step budget.
- **Hierarchical fallback (v0.8 core)** — Planner decomposes the task into typed subtasks, Navigator executes each one, StateTracker watches for stagnation and loops. Failure-typed replan guidance: each navigator failure carries a reason (`stagnation`, `loop`, `domain_drift`, `rate_limit`, `login_wall`, `captcha`, `llm_empty`) and the planner gets targeted recovery instructions.
- **Search-first navigation policy (v0.8)** — Planner step 1 must either NAVIGATE to a known URL or NAVIGATE to a Google search. Scrolling a landing page is banned. Navigator must press Enter or click submit on any typed field before saying DONE, and may not say DONE on a search results page when the task targets a specific resource.
- **Subtask-cycle escape hatch (v0.8)** — Agent loop tracks the last 4 failed subtask instructions. If two share more than 60% token overlap (planner stuck repeating the same broken approach), the loop force-injects a hard-coded "Google the task, click the first organic result, read the page" plan. Same fallback fires if the replanner returns a near-duplicate of what just failed. One-shot per task.
- **Wired escalation chain** — Three-tier LLM fallback (local → backup → cloud) with per-tier model names. After 3 failed replans on the current model, the Agent automatically swaps to the next tier and re-decomposes the task. Use `escalation`, `escalation_keys`, and `escalation_models` to wire it up.
- **Empty-response bail-out** — If the LLM returns no parseable actions for 2 consecutive steps, the Navigator bails with `failure_reason="llm_empty"` instead of silently burning the step budget. Triggers escalation through the normal replan path.
- **Multi-API compatible** — JSON mode (`response_format`) only sent to local endpoints. Cloud APIs (OpenRouter, OpenAI, Anthropic) work without 400 errors.
- **Sequential session safety** — after each browser session closes, the asyncio "running loop" pointer is cleared so the next session starts clean. Prevents "Event loop is closed" errors when running many tests back-to-back. The Docker server resets the event loop before each `/start` to handle stale greenlet residue.
- **TLS validation** — certificate validation is on by default. Set `FANTOMA_IGNORE_HTTPS_ERRORS=1` to tolerate self-signed or expired certs on target sites.
- **Playwright traces** — `Agent(trace=True)` records full debug sessions
- **Fingerprint self-test** — `fantoma test fingerprint` runs 7 in-browser checks
- **Chromium fallback** — `Agent(browser="chromium")` via [Patchright](https://github.com/Kaliiiiiiiiii-Vinyzu/patchright-python) for sites that block Firefox
- Multi-tab sessions, proxy rotation, CAPTCHA solving, verification code extraction
- **Session persistence** — cookies + localStorage saved to encrypted files per domain + account. Login once, skip forms forever. `pip install fantoma[sessions]` for encryption.
- **Unified login pipeline** — signup → CAPTCHA → email verification → login-back, all in one `login()` call. Tries saved session first.
- **Sensitive data** — pass credentials as `sensitive_data={"email": "...", "password": "..."}`. They appear as `<secret:email>` in LLM prompts and logs. Real values injected only at execution time.
- **Inline error detection** — JS scans for `role="alert"`, `aria-invalid`, error CSS classes, and common error text patterns. No LLM needed.
- **Smart element pruning** — relevance-based scoring replaces the hard cap. The LLM sees the most relevant elements for the current task, not the first N on the page.
- **Tree diffing** — new elements (from dropdowns, modals, next form steps) marked with `*` prefix so the LLM sees what just appeared.
- **Iframe ARIA extraction** — payment forms, embedded logins, and consent dialogs inside iframes are visible. Up to 5 iframes scanned per page.
- **Inline field state** — `aria-invalid`, `required`, current value, and error text shown directly in the element list. LLM sees `[3] textbox "Email" [invalid: "Please enter a valid email"]` instead of guessing why a submit failed.
- **Adaptive DOM modes** — three extraction modes (form/content/navigate) inferred per step from task keywords and page state. Form mode boosts inputs to top with tighter caps. Content mode strips UI for scraping.
- **ARIA landmark grouping** — interactive elements grouped under their nearest ARIA landmark (`[form: Login]`, `[navigation: Main nav]`). LLM sees structural context, not a flat list.
- **Cookie consent auto-dismiss** — detects and closes consent banners without LLM involvement.

## What It's For

- **Agentic workflows on your own systems.** Give an LLM a browser it can actually drive, including past a login, without handing credentials to a third-party cloud.
- **QA and regression testing of your own web apps**, including the signup and login flows that are painful to script by hand and break every redesign.
- **Accessibility auditing.** Fantoma navigates through the same tree a screen reader uses. If it cannot complete a task on your site, that is direct evidence a screen-reader user cannot either. Most testing tools check for static WCAG violations; this checks whether the journey actually works.
- **Personal automation** against accounts you own, running locally, with credentials that never leave your machine.
- **Research and data collection** where you have the right to the data and want it done without a cloud dependency.

The local-model support is the point of the architecture, not a footnote. If a task can run on a 7B model on your own hardware, it costs nothing per run and no third party sees the page or the credentials.

## Responsible Use

Fantoma is general-purpose automation, in the same category as Playwright or Selenium. It can be pointed anywhere, and what you point it at is your responsibility.

What the project will not do: ship recipes targeting specific third-party platforms, or optimise for bulk account creation. Those are the uses that get automation projects taken down, and they are not what this is built for.

Before pointing it at a site you do not own, check that site's terms and applicable law. Automating your own systems, or systems you are authorised to test, is unambiguous. Beyond that, the judgement is yours to make.

## How It Reads Pages

Fantoma interacts via the browser's accessibility API (ARIA tree) — the same channel used by screen readers like JAWS, NVDA, and VoiceOver.

**Zero mouse telemetry.** No mouse movements, no click coordinates, no scroll velocity. Anti-bot systems that fingerprint pointer behaviour see nothing because there is no pointer.

**Zero visual layer interaction.** No screenshots, no pixel coordinates. The browser processes accessibility API calls — identical to what it sees from a screen reader user.

**A channel sites cannot cheaply degrade.** WCAG, the ADA and the EU Accessibility Act push sites to expose a working accessibility tree, and real assistive-technology users depend on it. That does not grant automated access any legal right — it means the channel Fantoma reads is one sites have strong reasons to keep working, unlike a scraping-specific surface they can break freely.

**Different signal profile to screenshot-driven agents.** browser-use runs a hybrid of accessibility tree and vision, Stagehand drives CDP, Skyvern is vision-first. Approaches that synthesise pointer input or drive over CDP emit signals those channels expose. Fantoma emits none of that, because it never generates pointer input at all.

Worth stating plainly: no automation is undetectable. Modern bot management (Cloudflare's Precursor, behavioural Turnstile) scores behaviour across a whole session, not just fingerprints, and any tool claiming otherwise is selling something. Fantoma's design goal is a small, honest signal profile, not invisibility.

## Login & Signup (No LLM)

`login()` handles the full flow: saved session check → form fill → CAPTCHA → email verification → login-back. No LLM needed for known forms. Sessions saved to encrypted files — login once, instant access next time. Available on both `Fantoma` and `Agent`.

```python
# Tool API — no LLM needed
browser = Fantoma()
browser.start()
result = browser.login("https://github.com/login", email="me@example.com", password="...")
browser.stop()

# Convenience API
agent = Agent(llm_url="http://localhost:8080/v1")
result = agent.login("https://github.com/login", email="me@example.com", password="...")

# Login with username instead of email
result = browser.login("https://news.ycombinator.com/login", username="myuser", password="pass")

# Signup with name fields
result = browser.login(
    "https://demo.nopcommerce.com/register",
    first_name="Fantoma", last_name="Agent",
    email="me@example.com", password="SecurePass123!"
)
# Fills: FirstName, LastName, Email, Password, ConfirmPassword — all by code

# Result
print(result.success)       # True if login detected
print(result.data)          # {"fields_filled": [...], "url": "...", "steps": 1}
```

**Tested on:** the-internet.herokuapp.com (logged in), GitHub (React), HN (vanilla HTML), OrangeHRM (logged in), SauceDemo, DemoQA (4-field signup), nopCommerce (5-field signup), Parabank (logged in), Automationexercise (multi-step).

## Limitations

- **CAPTCHAs:** Proof-of-work types (ALTCHA) are solved automatically for free. reCAPTCHA and hCaptcha need a paid solver like CapSolver. How often you see one varies by site and by IP reputation.
- **Context window:** Local LLMs need at least 8K tokens. Set `--ctx-size 8192` in llama.cpp or `num_ctx: 8192` in Ollama.
- **Small models:** A 3.8B model handles browsing, extraction, and simple forms. Complex multi-step signups work better with a larger model. The escalation chain handles this — your local model tries first, and after 3 failed planner replans on the current tier, Fantoma automatically switches to the next model in the chain and re-decomposes the task fresh.
- **IP rate limiting:** Reddit detects repeated visits from the same IP after 2+ hours. Use proxy rotation for heavy scraping.

## Examples

```bash
# Run a task from the command line
fantoma run "Go to amazon.co.uk and tell me the top deal"

# Interactive mode
fantoma
fantoma> /session https://booking.com
session> /act Search for hotels in London
session> /read What is the cheapest hotel?
session> /done

# Extract structured data
fantoma> /extract https://books.toscrape.com First 3 books with title and price
```

```python
# Python: structured extraction with schema validation
agent = Agent(llm_url="http://localhost:8080/v1")
books = agent.extract(
    "https://books.toscrape.com",
    "First 3 books",
    schema={"title": str, "price": str}
)

# Python: automatic email verification (IMAP polling)
agent = Agent(
    llm_url="http://localhost:8080/v1",
    email_imap={
        "host": "127.0.0.1", "port": 1143,
        "user": "me@example.com", "password": "bridge-pass",
        "security": "starttls",
    },
)
result = agent.login("https://example.com/register",
                     email="me@example.com", password="SecurePass123!")
# If the site sends a verification email, Fantoma polls IMAP,
# extracts the code/link, and completes verification automatically.
```

```python
# Python: session persistence — login once, saved for next time
browser = Fantoma()
browser.start()
result = browser.login("https://github.com/login", email="me@example.com", password="...")
browser.stop()
# First call: fills form, logs in, saves session to ~/.local/share/fantoma/sessions/
# Next call: loads saved cookies, skips the form entirely

# Python: sensitive data — credentials never in logs or LLM history
agent = Agent(
    llm_url="http://localhost:8080/v1",
    sensitive_data={"email": "me@example.com", "password": "SecurePass123!"},
)
result = agent.run("Sign up at https://example.com/register")
# LLM sees: TYPE [3] "<secret:email>" — real value injected at execution time

# Python: local model with cloud fallback
agent = Agent(
    llm_url="http://localhost:8080/v1",
    escalation=["http://localhost:8080/v1", "https://api.openai.com/v1"],
    escalation_keys=["", "sk-..."],
    escalation_models=["auto", "gpt-4o"],
)

# Python: three-tier chain (local → backup → OpenRouter Qwen 3.6 Plus)
agent = Agent(
    llm_url="http://localhost:8081/v1",
    escalation=[
        "http://localhost:8081/v1",          # local-coder (local coder)
        "http://localhost:8082/v1",          # local-llm (local backup)
        "https://openrouter.ai/api/v1",      # Cloud escalation
    ],
    escalation_keys=["", "", "sk-or-..."],
    escalation_models=["auto", "auto", "qwen/qwen3.6-plus"],
)
# Auto-escalates after 3 failed replans on the current tier.

# Python: with proxy
agent = Agent(
    llm_url="http://localhost:8080/v1",
    proxy="socks5://user:pass@proxy:1080",
)

# Python: debug with traces
agent = Agent(llm_url="http://localhost:8080/v1", trace=True)
# Trace saved to ~/.local/share/fantoma/traces/<domain>-<timestamp>.zip
# View: playwright show-trace <file>.zip

# Python: Chromium instead of Firefox
agent = Agent(llm_url="http://localhost:8080/v1", browser="chromium")
# Requires: pip install fantoma[chromium]
```

## Troubleshooting

| Problem | Fix |
|---------|-----|
| LLM connection fails | Check it's running: `curl http://localhost:8080/v1/models` |
| Browser won't start | Run `fantoma test` again — Camoufox downloads on first run |
| Task times out | `Agent(timeout=120)` or use a faster model |
| Empty LLM responses | Navigator now bails out after 2 consecutive empty responses (`failure_reason="llm_empty"`) and triggers escalation. Context window should be at least 8192 tokens. |
| CAPTCHA blocks you | `Agent(captcha_api="capsolver", captcha_key="...")` |
| Site detects the bot | `Agent(proxy="socks5://user:pass@host:port")` |
| Small model misses buttons | Add escalation to a cloud API for hard steps |
| Form not filled | Check `fantoma logs --trace` for debug data |
| Login fields invisible | Fantoma falls back to raw DOM — check trace for details |
| LLM says DONE without acting | Fixed in v0.5.0 — prompt fix included |
| Same action repeating | Agent has built-in loop detection and escalation |
| "Event loop is closed" on second run | Fixed — `stop()` cleans up the asyncio event loop |
| SSL certificate error blocks navigation | Fixed — contexts use `ignore_https_errors=True` by default |
| Camoufox SIGSEGV / "Page crashed" on Fedora 43 | Use Docker (recommended) or LD_PRELOAD shim. See [Fedora 43 / glibc 2.42](#fedora-43--glibc-242-camoufox-crash) below. |

## Fedora 43 / glibc 2.42 — Camoufox Crash

If Camoufox crashes immediately with `TargetClosedError: Page crashed` or SIGSEGV on Fedora 43 (or any distro with glibc 2.42+), this is a known compatibility issue.

**Root cause:** glibc 2.42 calls `madvise(MADV_GUARD_INSTALL)` during `pthread_create` for thread stack guard pages. Camoufox's seccomp BPF filter was built before this `madvise` argument existed — child browser processes (content, RDD, utility) receive SIGSYS and die.

**Fix — LD_PRELOAD shim:**

```c
// madvise_shim.c
#define _GNU_SOURCE
#include <sys/mman.h>
#include <sys/prctl.h>
#include <linux/seccomp.h>
#include <linux/filter.h>
#include <stdarg.h>
#include <syscall.h>

// Intercept madvise — pass through everything except MADV_GUARD_INSTALL (102) and MADV_GUARD_REMOVE (103)
int madvise(void *addr, size_t length, int advice) {
    if (advice == 102 || advice == 103) return 0;
    return (int)syscall(SYS_madvise, addr, length, advice);
}

// Intercept prctl to block seccomp installation
int prctl(int option, ...) {
    va_list args;
    va_start(args, option);
    unsigned long a2 = va_arg(args, unsigned long);
    unsigned long a3 = va_arg(args, unsigned long);
    unsigned long a4 = va_arg(args, unsigned long);
    unsigned long a5 = va_arg(args, unsigned long);
    va_end(args);
    if (option == PR_SET_SECCOMP) return 0;
    return (int)syscall(SYS_prctl, option, a2, a3, a4, a5);
}

// Intercept syscall() for the SYS_seccomp path (inline assembly to avoid va_arg issues)
long syscall(long number, ...) __attribute__((weak));
```

```bash
# Build
gcc -shared -fPIC -O2 -o madvise_shim.so madvise_shim.c -ldl

# Test
LD_PRELOAD=/path/to/madvise_shim.so python3 -c "from fantoma import Agent; a = Agent(); print('OK')"
```

Fantoma sets `LD_PRELOAD` automatically when it detects the shim at `~/.local/share/fantoma/madvise_shim.so`. Copy your compiled shim there and Fantoma will use it without any other config changes.

You also need Xvfb running and `glxtest` available:

```bash
sudo dnf install xorg-x11-server-Xvfb mesa-libGL
Xvfb :99 -screen 0 1920x1080x24 &
# Copy glxtest from your Firefox install
cp /usr/lib64/firefox/glxtest ~/.cache/camoufox/
```

**After a Camoufox upgrade:** upgrades wipe `~/.cache/camoufox/`, so re-copy `glxtest` and run one test to confirm the shim still works.

**What does NOT work:** binary-patching `camoufox-bin` or `libxul.so`, or intercepting `madvise` at the glibc wrapper level (glibc uses inline syscalls internally, so the wrapper is never called).

## Docker API

Fantoma runs in a Docker container (Ubuntu 22.04 + Camoufox + Xvfb). Single session at a time. This is the recommended approach on Fedora 43+ to avoid the glibc/seccomp issue.

```bash
docker compose -f docker-compose.fantoma.yml up -d
```

| Endpoint | Method | Purpose |
|----------|--------|---------|
| /health | GET | Status check |
| /start | POST | Start session: `{"url": "..."}` |
| /stop | POST | End session |
| /state | GET | Current ARIA tree + page info. Optional `?mode=form\|content\|navigate` |
| /screenshot | GET | PNG screenshot |
| /click | POST | `{"element_id": 0}` |
| /type | POST | `{"element_id": 0, "text": "..."}` |
| /fill | POST | Fill input by CSS selector: `{"selector": "...", "value": "..."}` |
| /select | POST | Select dropdown option: `{"element_id": 0, "value": "..."}` |
| /evaluate | POST | Run JS in page: `{"script": "..."}` |
| /navigate | POST | `{"url": "..."}` |
| /scroll | POST | `{"direction": "down"}` |
| /press_key | POST | `{"key": "Enter"}` |
| /login | POST | LLM-free login (manages own session) |
| /extract | POST | Structured extraction (requires session) |
| /run | POST | Full agent task (manages own lifecycle) |
| /manual/open | POST | Open a visible browser on noVNC: `{"url": "...", "profile": "..."}` |
| /manual/screenshot | GET | Screenshot of the manual session |
| /manual/close | POST | Close manual session (cookies saved to profile) |
| /manual/status | GET | Check if a manual session is active |

### Security

The HTTP API and noVNC ship without authentication by default, so secure them before exposing the container beyond localhost. The browser keeps logged-in sessions in a persistent profile, which means a reachable, unauthenticated API can read cookies and act as the logged-in user. All controls are environment variables (wired in `docker-compose.fantoma.yml`):

| Variable | Default | Effect |
|----------|---------|--------|
| `FANTOMA_API_KEY` | unset (open) | When set, every endpoint except `/health` requires it via `X-API-Key: <key>` or `Authorization: Bearer <key>`. Unset logs a warning at startup. |
| `FANTOMA_VNC_PASSWORD` | unset (open) | Password-protects the noVNC display on port 6080. |
| `FANTOMA_ALLOW_EVAL` | `0` (off) | `/evaluate` runs arbitrary JS in the page (a cookie and token theft surface). Returns 403 unless set to `1`. |
| `FANTOMA_IGNORE_HTTPS_ERRORS` | `0` (off) | TLS certificate validation stays on by default. Set to `1` only for sites with known bad certificates. |

On any shared network, set `FANTOMA_API_KEY` and `FANTOMA_VNC_PASSWORD`. Credentials passed via `sensitive_data` are masked in the ARIA tree sent to the LLM and in step logs; the real value is substituted only at execution time.

<!-- BENCHMARK:START -->
## WebVoyager Benchmark (v0.8, GPT-4o)

5-task pilot subset, GPT-4o agent + GPT-4o judge, Camoufox + Capsolver, Docker.

| Site | Result | Steps | Duration |
|------|--------|-------|----------|
| Apple — buy a MacBook Air with specific options | PASS | 15 | 106s |
| Coursera — find a beginner 3D-printing course | PASS | 15 | 100s |
| ESPN — current NBA standings | PASS | 15 | 108s |
| Booking — Mexico hotel for given dates | FAIL | 13 | 131s |
| Google Flights — round trip with flexible dates | FAIL | 15 | 93s |

**Score: 3/5 (60%).** Average 14.6 steps, 107.5s per task. Booking and Google Flights still need work — Booking rejects "Mexico" as a search string (needs destination translation), Google Flights stalls on a non-submitting form repeat. Run dir: `benchmark/results/2026-04-09_191104/`.

| Agent | LLM | Score on this subset |
|-------|-----|----------------------|
| **Fantoma v0.8** | **GPT-4o** | **60.0%** |
| Surfer 2 (full benchmark) | — | 97.1% |
| Magnitude (full benchmark) | Claude Sonnet | 93.9% |
| browser-use (full benchmark) | GPT-4o | 89.1% |
| Skyvern 2.0 (full benchmark) | — | 85.9% |

Leaderboard scores are over the full WebVoyager suite, not the same 5-task pilot. See `docs/benchmark.md` for the full progression and per-run analysis.
<!-- BENCHMARK:END -->

## Test Results

Tested across 25 real sites with 6 different LLMs. 519 unit tests. Passed fingerprint checks on bot.sannysoft.com and nowsecure.nl. Zero bot detections across 2,241 stress tests. Full results below.

**v0.7.0 live test — 25 sites, local-llm 9B local model (2026-03-31):**

| # | Site | Result | Time |
|---|------|--------|------|
| 1 | The Guardian | PASS | 44s |
| 2 | Reuters | FAIL | 2s (stale context) |
| 3 | TechCrunch | PASS | 181s |
| 4 | PyPI | PASS | 44s |
| 5 | npm / npmcharts | PASS | 119s |
| 6 | Regex101 | FAIL | 457s (custom code editor) |
| 7 | Python docs | PASS | 249s |
| 8 | Wayback Machine | PASS | 150s |
| 9 | CodePen | PASS | 25s |
| 10 | Reddit | PASS | 63s |
| 11 | GitLab | PASS | 34s |
| 12 | WordPress.com | PASS | 75s |
| 13 | Twitch | PASS | 52s |
| 14 | Discord | PASS | 55s |
| 15 | Spotify | PASS | 27s |
| 16 | Dev.to | PASS | 99s |
| 17 | Disqus | PASS | 78s |
| 18 | Etsy | PASS | 151s |
| 19 | eBay UK | PASS | 16s |
| 20 | Argos | PASS | 56s |
| 21 | Reed.co.uk | PASS | 43s |
| 22 | Glassdoor UK | PASS | 34s |
| 23 | Rightmove | PASS | 19s |
| 24 | Ticketmaster UK | PASS | 38s |
| 25 | TotalJobs | PASS | 144s |

**23/25 (92%). Zero browser crashes. Both failures are agent logic, not browser stability.**

<details>
<summary>Detailed test breakdown</summary>

**Login/signup tests (v0.4.0, code path + LLM brain):**

| Site | Type | Fields Filled | Result |
|------|------|---------------|--------|
| the-internet.herokuapp.com | Login | Username, Password | Logged in |
| GitHub | Login (React) | Email, Password | Form filled |
| OrangeHRM | Login (SPA) | Username, Password | Logged in |
| Parabank | Signup | FirstName, LastName, Username, Password | Account created |
| MongoDB Atlas | Signup (5 fields) | FirstName, LastName, Email, Password | All filled |
| Stripe | Signup | Full name, Email, Password | All filled |
| Twilio | Signup (4 fields) | FirstName, LastName, Email, Password | All filled |
| Ghost | Signup | Name, Email, Password | All filled |
| Zapier | Signup (4 fields) | FirstName, LastName, Email, Password | All filled |
| Postman | Signup (3 fields) | Email, Username, Password | All filled |
| nopCommerce | Signup (5 fields) | FirstName, LastName, Email, Password, ConfirmPassword | All filled |
| Supabase | Signup | Email, Password | All filled |
| PlanetScale | Signup | Email, Password, Confirm | All filled |
| Clerk | Signup | Email, Password | All filled |
| Wandb | Signup | Email, Password | All filled |

**15 login/signup sites tested on v0.4, zero bot detections, zero form failures.**

**Overnight stress test (7 hours, 3 cloud APIs):**

| Provider | Tests | Pass Rate |
|----------|-------|-----------|
| OpenAI GPT-4o-mini | 180 | 100% |
| Claude Sonnet | 1,159 | 99.9% |
| Kimi Moonshot | 902 | 96.7% |

**Anti-bot systems bypassed:** Cloudflare (X.com, Reddit, Indeed), DataDome (Amazon), PerimeterX (Walmart, Zillow), Akamai (Nike), Meta (Instagram, Facebook), custom (LinkedIn, Booking.com, TikTok, Craigslist, GitHub).

**Small model (Phi-3.5-mini 3.8B):** 15/15 bot-protected sites passed. Logged into ProtonMail. Created Reddit account with email verification.

**6 LLMs tested:**

| Model | Size | Pass Rate |
|-------|------|-----------|
| Qwen3.5-122B | 122B | 100% |
| Qwen3-Coder | 45B | 100% |
| Phi-3.5-mini | 3.8B | 100% |
| Claude Sonnet | Cloud | 99.9% |
| Kimi Moonshot | Cloud | 96.7% |
| GPT-4o-mini | Cloud | 100% |

</details>

## Configuration

```python
# Tool API — drive the browser step by step
Fantoma(
    llm_url=None,           # Optional — only needed for extract() and field labelling
    headless=True,
    proxy=None,
    browser="camoufox",
    captcha_api=None,
    captcha_key=None,
    email_imap=None,
    verification_callback=None,
    timeout=300,
)

# Convenience API — describe a task, the agent does it
Agent(
    llm_url="http://localhost:8080/v1",  # Required for Agent
    api_key="",
    model="auto",            # "auto" resolves via /v1/models, or pass exact model id
    escalation=None,         # list[str] of OpenAI-compatible base URLs (cheapest first)
    escalation_keys=None,    # list[str] of API keys, "" for local endpoints
    escalation_models=None,  # list[str] of model ids, "auto" for local endpoints
    max_steps=50,
    flat_budget=20,          # Steps for Phase 1 flat loop before hierarchical fallback
    timeout=300,
    validate=False,          # Post-run answer validation (opt-in, fails open)
    sensitive_data=None,
    **fantoma_kwargs,        # All Fantoma params passed through
)

# agent.run() wall-clock guard
result = agent.run("task", deadline_s=120)   # hard 120s limit, default 300
print(result.validated)                      # True/False/None (None = not validated)
```

## CLI Commands

```
fantoma setup              # Guided setup wizard
fantoma test               # Quick check
fantoma test full           # Test against 10 real sites
fantoma test fingerprint    # Validate anti-detection (7 checks)
fantoma run "task"          # Run a task
fantoma logs               # View recent activity and errors
fantoma logs --trace        # List saved Playwright traces
fantoma                    # Interactive mode
```

Interactive mode: `/help`, `/run`, `/session`, `/act`, `/read`, `/observe`, `/tab`, `/switch`, `/status`, `/history`, `/logs`, `/quit`

All activity is logged to `~/.fantoma/fantoma.log` — check it with `fantoma logs` or `/logs` in interactive mode.

## Architecture

```
fantoma/
├── browser_tool.py      # Fantoma class — the browser tool (start, stop, click, type, login, extract)
├── agent.py             # Agent class — flat-first with hierarchical fallback, deadline guard, validator
├── validator.py         # Post-run answer validation (opt-in LLM YES/NO check)
├── session.py           # Encrypted session persistence
├── cli.py               # CLI + interactive mode (uses Agent)
├── config.py            # Settings
├── dom/                 # Page reading (ARIA tree + raw DOM fallback), ARIA snapshot diffing
│   └── aria_diff.py     # Semantic before/after diff for step change lines
├── browser/             # Browser engine, anti-detection, forms, CAPTCHA, consent
├── captcha/             # Detection + solving (PoW, API, human fallback)
├── llm/                 # Thin OpenAI-compatible client (for field labelling + extract)
└── resilience/          # Escalation chain (used by Agent only)
```

## Example Scripts

| File | What it does |
|------|-------------|
| `examples/simple_search.py` | Search Hacker News |
| `examples/local_llm.py` | Ollama / llama.cpp / vLLM |
| `examples/data_extraction.py` | Structured data extraction |
| `examples/form_filling.py` | Fill and submit forms |
| `examples/multi_tab.py` | Signup with email verification |
| `examples/with_proxy.py` | Browse through a proxy |
| `examples/escalation.py` | Local model + cloud fallback |

## Contributing

Contributions welcome. Fork, branch, test, PR.

## Acknowledgments

Built on top of these projects:

- [Camoufox](https://github.com/daijro/camoufox) — anti-detect browser (hardened Firefox with fingerprint rotation)
- [Patchright](https://github.com/Kaliiiiiiiiii-Vinyzu/patchright-python) — patched Chromium (optional)
- [Playwright](https://github.com/microsoft/playwright) — browser automation framework
- [httpx](https://github.com/encode/httpx) — HTTP client for LLM API calls

Inspired by these projects and research:

- [browser-use](https://github.com/browser-use/browser-use) — the leading open-source browser agent. Fantoma's credential placeholder injection pattern was informed by their approach. Reimplemented from scratch to fit Fantoma's code-first architecture.
- [WebVoyager](https://arxiv.org/abs/2401.13919) — web agent benchmark. Tree diffing (marking new elements with `*` prefix) was inspired by their set-of-marks approach, adapted for DOM-only operation without screenshots.
- [Playwright](https://playwright.dev/docs/frames) — iframe frame traversal and ARIA snapshot APIs used for iframe element extraction.

## License

MIT — Steam Vibe Ltd
