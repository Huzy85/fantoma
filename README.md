# Fantoma

The undetectable browser automation library. Drives browsers via the accessibility API — the same channel used by screen readers. No mouse movements, no screenshots, no pixel coordinates.

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

## What It Does

- **Gets through the gate** — login, signup, CAPTCHA solving. Code handles the forms, LLM handles the unexpected.
- **LLM as brain, code as hands** — Code matches form fields by label (fast, zero tokens). When it can't match, one LLM call labels all fields at once. Code fills based on the LLM's answer. Results cached in SQLite — LLM never called twice for the same site.
- **Signup forms** — fills first name, last name, email, username, password, confirm password. Clicks terms checkboxes. Tracks what's been filled to avoid double-submission.
- **27 real sites tested** — MongoDB Atlas, Stripe, Twilio, Zapier, GitHub, HN, Notion, Supabase, and 19 more. Zero bot detections.
- [Camoufox](https://github.com/daijro/camoufox) anti-detection — passes bot.sannysoft.com and nowsecure.nl. 2,241 stress tests, zero fingerprint detections.
- **ARIA + raw DOM** — always reads both. No form is invisible, even old-school HTML without ARIA labels.
- **Form Memory** — SQLite database records every login page. Gets smarter with every visit.
- **Universal form filling** — one approach for React, Vue, Angular, vanilla HTML. No framework detection.
- **Resilience** — 3-level model escalation (local → cloud → back), 3-level environment escalation (cookies → proxy → fresh fingerprint), retry on slow SPAs. Page reference auto-refreshed after each browser restart so stale handles never cause crashes.
- **Multi-API compatible** — JSON mode (`response_format`) only sent to local endpoints. Cloud APIs (DeepSeek, OpenAI, Anthropic) work without 400 errors.
- **Sequential session safety** — after each browser session closes, the asyncio "running loop" pointer is cleared so the next session starts clean. Prevents "Event loop is closed" errors when running many tests back-to-back.
- **Playwright traces** — `Agent(trace=True)` records full debug sessions
- **Fingerprint self-test** — `fantoma test fingerprint` runs 7 in-browser checks
- **Chromium fallback** — `Agent(browser="chromium")` via [Patchright](https://github.com/Kaliiiiiiiiii-Vinyzu/patchright-python) for sites that block Firefox
- **Auto-follow popups** — OAuth flows, `target="_blank"` links, and payment pages in new tabs are automatically followed. When the popup closes, focus returns to the original tab. No LLM actions needed.
- Multi-tab sessions, proxy rotation, CAPTCHA solving, verification code extraction
- **Session persistence** — cookies + localStorage saved to encrypted files per domain + account. Login once, skip forms forever. `pip install fantoma[sessions]` for encryption.
- **Unified login pipeline** — signup → CAPTCHA → email verification → login-back, all in one `agent.login()` call. Tries saved session first.
- **Multi-action steps** — LLM returns up to 5 actions per call (3-5x fewer LLM calls). Page-change guards abort stale actions if the page navigates mid-sequence.
- **Paint-order filtering** — removes elements hidden behind modals and overlays before showing them to the LLM. Fewer confused clicks on invisible buttons.
- **Free search tools** — `SEARCH_PAGE "text"` and `FIND "css selector"` — the LLM can search page content without extra LLM calls.
- **Message compaction** — long tasks (50+ steps) don't blow the context window. Old history gets summarized automatically.
- **Sensitive data** — pass credentials as `sensitive_data={"email": "...", "password": "..."}`. They appear as `<secret:email>` in LLM prompts and logs. Real values injected only at execution time.
- **Action verification** — after every click or submit, code checks what happened: URL change, new elements, error messages. The LLM sees `"After CLICK [5]: error — Invalid email"` instead of just `"failed"`.
- **Inline error detection** — JS scans for `role="alert"`, `aria-invalid`, error CSS classes, and common error text patterns. No LLM needed.
- **Smart element pruning** — relevance-based scoring replaces the hard cap. The LLM sees the 15 most relevant elements for the current task, not the first 15 on the page.
- **MutationObserver tracking** — precise feedback on what each action changed in the DOM. Added nodes, removed nodes, changed attributes, new text — all reported to the LLM.
- **Tree diffing** — new elements (from dropdowns, modals, next form steps) marked with `*` prefix so the LLM sees what just appeared.
- **Observation masking** — action outcomes kept verbatim, old DOM snapshots dropped. LLM compaction only kicks in at 40% of context window. Most tasks use zero compaction calls.
- **Script caching** — after a successful task, saves the action sequence to SQLite. Next time, replays without any LLM calls. Falls back to LLM if the page changed.
- **Structured JSON output** — LLM returns `{"actions": [...]}` instead of free text. Schema-constrained via `response_format`. Falls back to text parsing if JSON fails (backward-compatible with all models).
- **DOM element deduplication** — removes repeated nav/footer/header elements before the LLM sees them. Sites repeat the same links in three places; Fantoma shows each once.
- **Iframe ARIA extraction** — payment forms, embedded logins, and consent dialogs inside iframes are now visible. Up to 5 iframes scanned per page.
- **Adaptive DOM wait** — replaces fixed `network_idle` with a debounced MutationObserver. Waits until the DOM stops changing for 300ms, not until the network quiets. Faster on SPAs, more reliable on slow CDNs.
- **Inline field state** — `aria-invalid`, `required`, current value, and error text shown directly in the element list. LLM sees `[3] textbox "Email" [invalid: "Please enter a valid email"]` instead of guessing why a submit failed.
- **Adaptive DOM modes** — three extraction modes (form/content/navigate) inferred per step from task keywords and page state. Form mode boosts inputs to top with tighter caps. Content mode strips UI for scraping. Inspired by Agent-E's DOM distillation.
- **ARIA landmark grouping** — interactive elements grouped under their nearest ARIA landmark (`[form: Login]`, `[navigation: Main nav]`). LLM sees structural context, not a flat list. Novel approach supported by LCoW (ICLR 2025) research.
- **Per-step success criteria** — after every action, code verifies it worked (TYPE checks field value, CLICK checks URL/form). Task-level progress tracking detects stalls. Inspired by Skyvern 2.0's validator pattern.
- **Self-healing selectors** — cached scripts survive page changes. When an element moves or gets renamed, fuzzy matching (difflib SequenceMatcher) finds it by role + name similarity. Inspired by Stagehand v3 and Healenium.

## Accessibility-First Stealth

Fantoma interacts via the browser's accessibility API (ARIA tree) — the same channel used by screen readers like JAWS, NVDA, and VoiceOver.

**Zero mouse telemetry.** No mouse movements, no click coordinates, no scroll velocity. Anti-bot systems that fingerprint pointer behaviour see nothing because there is no pointer.

**Zero visual layer interaction.** No screenshots, no pixel coordinates. The browser processes accessibility API calls — identical to what it sees from a screen reader user.

**Legally protected channel.** WCAG, ADA, and the EU Accessibility Act require websites to support accessibility APIs. Blocking accessibility API access means blocking disabled users — sites cannot do this without legal exposure.

**Competitors produce detectable signals.** browser-use takes screenshots. Stagehand uses CDP. Skyvern combines LLM with computer vision. All three produce signals that anti-bot systems can fingerprint. Fantoma produces none.

## Login & Signup (No LLM)

`agent.login()` handles the full flow: saved session check → form fill → CAPTCHA → email verification → login-back. No LLM needed for known forms. Sessions saved to encrypted files — login once, instant access next time.

```python
# Simple login
result = agent.login("https://example.com/login", email="me@example.com", password="pass")

# Login with username instead of email
result = agent.login("https://news.ycombinator.com/login", username="myuser", password="pass")

# Signup with name fields
result = agent.login(
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

- **CAPTCHAs:** Proof-of-work types (ALTCHA) are solved automatically for free. reCAPTCHA and hCaptcha need a paid solver like CapSolver. Most sites never show CAPTCHAs because Camoufox prevents detection.
- **Context window:** Local LLMs need at least 8K tokens. Set `--ctx-size 8192` in llama.cpp or `num_ctx: 8192` in Ollama.
- **Small models:** A 3.8B model handles browsing, extraction, and simple forms. Complex multi-step signups work better with a larger model. The escalation chain handles this — your local model tries first, and if it gets stuck, Fantoma automatically switches to your cloud API.
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

# Python: multi-tab session (signup + email verification)
# Automatic email verification (IMAP polling)
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

# Manual multi-tab verification (no IMAP needed)
with agent.session("https://example.com/register") as s:
    s.act("Type 'user@email.com' in the email field")
    s.act("Click Sign Up")

    s.new_tab("https://mail.example.com", name="email")
    s.act("Open the verification email")
    code = s.extract("Get the verification code")

    s.switch_tab("main")
    s.act(f"Type '{code}' in the verification field")
    s.close_tab("email")
```

```python
# Python: session persistence — login once, saved for next time
agent = Agent(llm_url="http://localhost:8080/v1")
result = agent.login("https://github.com/login", email="me@example.com", password="...")
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
)

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
| Empty LLM responses | Context window too small — need at least 8192 tokens |
| CAPTCHA blocks you | `Agent(captcha_api="capsolver", captcha_key="...")` |
| Site detects the bot | `Agent(proxy="socks5://user:pass@host:port")` |
| Small model misses buttons | Add escalation to a cloud API for hard steps |
| Form not filled | Check `fantoma logs --trace` for debug data |
| Login fields invisible | Fantoma falls back to raw DOM — check trace for details |
| LLM says DONE without acting | Fixed in v0.5.0 — prompt fix included |
| Same action repeating | Fixed in v0.6 — action verification tells LLM what happened after each step |
| "Event loop is closed" on second run | Fixed in v0.6 — `stop()` cleans up the asyncio event loop |
| Camoufox SIGSEGV / "Page crashed" on Fedora 43 | glibc 2.42 uses `madvise(MADV_GUARD_INSTALL)` for thread stacks — blocked by Camoufox's seccomp filter. Fix: LD_PRELOAD shim. See [Fedora 43 / glibc 2.42](#fedora-43--glibc-242-camoufox-crash) below. |

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

## Test Results

Tested across 27 real sites with 6 different LLMs. 508 unit tests. Passed fingerprint checks on bot.sannysoft.com and nowsecure.nl. Zero bot detections across 2,241 stress tests. Full results below.

**v0.7.0 live test — 25 sites, Hermes 9B local model (2026-03-31):**

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

**27 sites tested total, zero bot detections, zero form failures on v0.4.**

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
Agent(
    llm_url="http://localhost:8080/v1",  # Required
    model="auto",                        # Or specific model name
    api_key="",                          # For cloud APIs
    headless=True,                       # False to see the browser
    proxy=None,                          # "socks5://..." or ["proxy1", "proxy2"]
    escalation=None,                     # ["local_url", "cloud_url"]
    escalation_keys=None,                # ["", "sk-cloud-key"] per endpoint
    captcha_api=None,                    # "capsolver", "2captcha"
    captcha_key=None,                    # API key for CAPTCHA solver
    timeout=300,                         # Total timeout in seconds
    max_steps=50,                        # Max actions before giving up
    trace=False,                         # Save Playwright debug traces
    browser="camoufox",                  # Or "chromium" (pip install fantoma[chromium])
    email_imap=None,                     # {"host": ..., "port": 993, "user": ..., "password": ..., "security": "ssl"}
    verification_callback=None,          # callable(domain, message) → code/link string
    sensitive_data=None,                 # {"email": "...", "password": "..."} — never in logs
)
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
├── agent.py             # Agent class — convenience wrapper with run() for vibe coders
├── session.py           # Encrypted session persistence
├── cli.py               # CLI + interactive mode (uses Agent)
├── config.py            # Settings
├── dom/                 # Page reading (ARIA tree + raw DOM fallback)
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

- [browser-use](https://github.com/browser-use/browser-use) — the leading open-source browser agent. Fantoma adopted several of their patterns: multi-action batching per LLM call, paint-order DOM filtering via `elementFromPoint()`, free JS-based page search tools, history compaction for long tasks, credential placeholder injection, and DOM element deduplication. Their structured JSON output approach (schema-constrained responses) informed Fantoma's structured output design. All patterns were reimplemented from scratch to fit Fantoma's code-first architecture.
- [WebVoyager](https://arxiv.org/abs/2401.13919) — web agent benchmark. Tree diffing (marking new elements with `*` prefix) was inspired by their set-of-marks approach, adapted for DOM-only operation without screenshots.
- [AgentQ](https://arxiv.org/abs/2408.07199) — Monte Carlo Tree Search web agent. Their action verification and outcome reporting pattern (checking URL changes, error detection, DOM mutations after each action) influenced Fantoma's post-action verification pipeline. Fantoma implements this as pure code checks rather than AgentQ's LLM self-reflection.
- [SWE-bench](https://swe-bench.github.io/) / JetBrains research on observation masking — keeping action history verbatim while dropping old observations. Fantoma's observation masking (action outcomes kept, old DOM snapshots discarded) is based on this principle.
- [MutationObserver debounce pattern](https://developer.mozilla.org/en-US/docs/Web/API/MutationObserver) — DOM stability detection via debounced MutationObserver (wait until mutations stop for 300ms). Used for both change tracking and adaptive wait strategies.
- [Playwright](https://playwright.dev/docs/frames) — iframe frame traversal and ARIA snapshot APIs used for iframe element extraction.

## License

MIT — Steam Vibe Ltd
