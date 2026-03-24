# Fantoma

AI browser agent that works with any LLM. Anti-detection built in. Runs locally or with cloud APIs.

![Fantoma Demo](fantoma_demo.gif)

```python
from fantoma import Agent

agent = Agent(llm_url="http://localhost:8080/v1")
result = agent.run("Go to github.com/trending and tell me the top repo")
if result.success:
    print(result.data)  # "TauricResearch / TradingAgents"
```

## Getting Started

```bash
pip install fantoma
fantoma setup        # Guided wizard: pick your LLM, done
fantoma test         # Verify it works
```

**Need an LLM?** Install [Ollama](https://ollama.com), run `ollama pull phi3.5`, done. Works on CPU or GPU (8GB+ GPU recommended for speed). Or use a cloud API (OpenAI, Anthropic, Kimi) — the wizard handles it.

**Requirements:** Python 3.10+, Linux or macOS (Windows via WSL). No other dependencies — everything installs automatically.

## What It Does

- Browses the web, fills forms, clicks buttons, extracts data
- [Camoufox](https://github.com/daijro/camoufox) anti-detection — passes bot.sannysoft.com and nowsecure.nl
- Reads pages as text (~200 tokens) instead of screenshots (~1000 tokens) — cheap, fast, works with small models
- Handles cookie popups, autocomplete, SPA navigation automatically
- Multi-tab sessions with shared cookies (signup in one tab, check email in another)
- Proxy rotation, model escalation, verification code extraction

## Limitations

- **CAPTCHAs:** Proof-of-work types (ALTCHA) are solved automatically for free. reCAPTCHA and hCaptcha need a paid solver like CapSolver. Most sites never show CAPTCHAs because Camoufox prevents detection.
- **Context window:** Local LLMs need at least 8K tokens. Set `--ctx-size 8192` in llama.cpp or `num_ctx: 8192` in Ollama.
- **Small models:** A 3.8B model handles browsing, extraction, and simple forms. Complex multi-step signups (Reddit, GitHub) work better with a larger model. The escalation chain handles this — your local model tries first, and if it gets stuck after 3 retries on the same step, Fantoma automatically switches to your cloud API for just that step, then goes back to local.
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
from fantoma.browser.verification import extract_verification_code

with agent.session("https://example.com/register") as s:
    s.act("Type 'user@email.com' in the email field")
    s.act("Click Sign Up")

    s.new_tab("https://mail.example.com", name="email")
    s.act("Open the verification email")
    code = extract_verification_code(s._browser.get_page())  # Regex, no LLM

    s.switch_tab("main")
    s.act(f"Type '{code}' in the verification field")
    s.close_tab("email")
```

```python
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

## Test Results

Tested across 20+ bot-protected sites with 6 different LLMs. Passed fingerprint checks on bot.sannysoft.com and nowsecure.nl. Full results below.

<details>
<summary>Detailed test breakdown</summary>

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
    captcha_api=None,                    # "capsolver", "2captcha"
    captcha_key=None,                    # API key for CAPTCHA solver
    timeout=300,                         # Total timeout in seconds
    max_steps=50,                        # Max actions before giving up
)
```

## CLI Commands

```
fantoma setup         # Guided setup wizard
fantoma test          # Quick check
fantoma test full     # Test against 10 real sites
fantoma run "task"    # Run a task
fantoma logs          # View recent activity and errors
fantoma               # Interactive mode
```

Interactive mode: `/help`, `/run`, `/session`, `/act`, `/read`, `/observe`, `/tab`, `/switch`, `/status`, `/history`, `/logs`, `/quit`

All activity is logged to `~/.fantoma/fantoma.log` — check it with `fantoma logs` or `/logs` in interactive mode.

## Architecture

```
fantoma/
├── agent.py             # Public API
├── cli.py               # CLI + interactive mode
├── executor.py          # Reactive loop
├── action_parser.py     # LLM response → browser action
├── config.py            # All settings
├── dom/                 # Page reading (ARIA tree + DOM fallback)
├── browser/             # Camoufox, anti-detection, proxy, consent, tabs
├── captcha/             # Detection + solving (PoW, API, human fallback)
├── resilience/          # Action memory, checkpoints, model escalation
└── llm/                 # OpenAI-compatible client, prompts
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

Fantoma is built on top of these projects:

- [Camoufox](https://github.com/daijro/camoufox) — anti-detect browser (hardened Firefox with fingerprint rotation)
- [Playwright](https://github.com/microsoft/playwright) — browser automation framework
- [httpx](https://github.com/encode/httpx) — HTTP client for LLM API calls

## License

MIT — Steam Vibe Ltd
