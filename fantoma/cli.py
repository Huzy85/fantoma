#!/usr/bin/env python3
"""Fantoma CLI — guided setup, quick test, and run commands.

Usage:
    fantoma setup          # Guided first-time setup
    fantoma test           # Quick test to verify everything works
    fantoma run "task"     # Run a browser task
    fantoma monitor        # Run the weekly monitor suite
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

CONFIG_DIR = Path.home() / ".fantoma"
CONFIG_FILE = CONFIG_DIR / "config.json"
LOG_FILE = CONFIG_DIR / "fantoma.log"

# ── Terminal helpers ─────────────────────────────────────────────

def _bold(text): return f"\033[1m{text}\033[0m"
def _green(text): return f"\033[92m{text}\033[0m"
def _yellow(text): return f"\033[93m{text}\033[0m"
def _red(text): return f"\033[91m{text}\033[0m"
def _dim(text): return f"\033[2m{text}\033[0m"

def _ask(prompt, default=None, secret=False):
    """Ask user for input with optional default."""
    if default:
        display = f"{prompt} [{default}]: "
    else:
        display = f"{prompt}: "

    if secret:
        import getpass
        value = getpass.getpass(display)
    else:
        value = input(display)

    return value.strip() or default or ""

def _choose(prompt, options, default=1):
    """Present numbered options, return the chosen value."""
    print(f"\n{_bold(prompt)}")
    for i, (label, _) in enumerate(options, 1):
        marker = " *" if i == default else ""
        print(f"  {i}. {label}{_dim(marker)}")

    while True:
        choice = input(f"\nChoice [{default}]: ").strip()
        if not choice:
            return options[default - 1][1]
        try:
            idx = int(choice)
            if 1 <= idx <= len(options):
                return options[idx - 1][1]
        except ValueError:
            pass
        print(_red(f"  Please enter 1-{len(options)}"))

def _confirm(prompt, default=True):
    """Yes/no question."""
    suffix = "[Y/n]" if default else "[y/N]"
    answer = input(f"{prompt} {suffix}: ").strip().lower()
    if not answer:
        return default
    return answer in ("y", "yes")


# ── Setup wizard ─────────────────────────────────────────────────

BANNER = f"""
\033[1m  +----------------------------------------+
  |         F A N T O M A                  |
  |  The AI browser agent for any LLM      |
  +----------------------------------------+\033[0m"""

def cmd_setup():
    """Guided first-time setup."""
    print(BANNER)
    print(f"\nThis will walk you through setting up Fantoma step by step.")
    print(f"Your settings will be saved to {_dim(str(CONFIG_FILE))}")
    print()

    config = {}

    # ── Step 1: LLM ──────────────────────────────────────────────
    print(f"\n{_bold('Step 1: LLM (Language Model)')}")
    print("Fantoma needs an LLM to understand web pages and decide what to do.")
    print("It works with any model — from a 3.8B local model to cloud APIs.\n")

    llm_type = _choose("Where is your LLM running?", [
        ("Local — Ollama, llama.cpp, or vLLM on my machine (free, private)", "local"),
        ("Cloud API — OpenAI, Anthropic, Kimi, or similar (needs API key)", "cloud"),
        ("I don't have one yet — help me choose", "help"),
    ])

    if llm_type == "local":
        print(f"\n  Where is your LLM running? Common URLs:")
        print(f"    Ollama:     http://localhost:11434/v1")
        print(f"    llama.cpp:  http://localhost:8080/v1")
        print(f"    vLLM:       http://localhost:8000/v1")
        print(f"\n  {_dim('Just press Enter to use the default URL.')}")
        while True:
            url = _ask("  Paste your LLM URL here", "http://localhost:8080/v1")
            if url.startswith("http://") or url.startswith("https://"):
                config["llm_url"] = url
                break
            print(_red("  That doesn't look like a URL. It should start with http://"))
        config["model"] = _ask("Model name (or 'auto' to detect)", "auto")
        config["api_key"] = ""

        print(f"\n{_yellow('Important:')} Set --ctx-size to at least 8192 in your LLM server.")
        print(f"Smaller context windows will fail on complex pages.")

    elif llm_type == "cloud":
        provider = _choose("Which provider?", [
            ("OpenAI (GPT-4o-mini — fast, reliable)", "openai"),
            ("Kimi Moonshot (very affordable)", "kimi"),
            ("Anthropic Claude (most capable)", "anthropic"),
            ("Other OpenAI-compatible API", "other"),
        ])

        endpoints = {
            "openai": ("https://api.openai.com/v1", "gpt-4o-mini"),
            "kimi": ("https://api.moonshot.ai/v1", "moonshot-v1-8k"),
            "anthropic": ("https://api.anthropic.com/v1", "claude-sonnet-4-20250514"),
        }

        if provider in endpoints:
            url, model = endpoints[provider]
            config["llm_url"] = url
            config["model"] = model
        else:
            while True:
                url = _ask("API endpoint URL")
                if url.startswith("http://") or url.startswith("https://"):
                    config["llm_url"] = url
                    break
                print(_red("  URL must start with http:// or https://"))
            config["model"] = _ask("Model name")

        config["api_key"] = _ask("API key", secret=True)
        if not config["api_key"]:
            print(_red("  Warning: No API key provided. Most cloud APIs require one."))

    else:  # help
        print(f"""
{_bold('Easiest option: Ollama (free, runs on your machine)')}

1. Install Ollama:  curl -fsSL https://ollama.com/install.sh | sh
2. Pull a model:    ollama pull phi3.5    {_dim('(3.8B — works on 8GB GPU)')}
                    ollama pull qwen2.5   {_dim('(7B — better quality)')}
3. It runs at:      http://localhost:11434/v1

{_bold('Easiest cloud option: Kimi Moonshot')}

1. Sign up at:      https://platform.moonshot.ai
2. Get API key from dashboard
3. Endpoint:        https://api.moonshot.ai/v1
""")
        while True:
            url = _ask("LLM endpoint URL", "http://localhost:11434/v1")
            if url.startswith("http://") or url.startswith("https://"):
                config["llm_url"] = url
                break
            print(_red("  URL must start with http:// or https://"))
        config["model"] = _ask("Model name", "auto")
        config["api_key"] = _ask("API key (leave blank for local)", secret=True) or ""

    # ── Step 2: Proxy / VPN ──────────────────────────────────────
    print(f"\n{_bold('Step 2: Proxy / VPN (optional)')}")
    print("A proxy hides your real IP address when browsing.")
    print("Most people don't need this. It's useful for heavy scraping")
    print("or accessing content restricted to certain countries.\n")

    use_proxy = _confirm("Do you want to set up a proxy?", default=False)
    if use_proxy:
        proxy_type = _choose("What kind of proxy do you have?", [
            ("One proxy (e.g. from ProtonVPN, NordVPN, or a provider)", "single"),
            ("Multiple proxies (rotate between them automatically)", "rotation"),
        ])

        if proxy_type == "single":
            print(f"\n{_dim('Your VPN or proxy provider gives you credentials in this format:')}")
            print(f"{_dim('  SOCKS5: socks5://username:password@server:port')}")
            print(f"{_dim('  HTTP:   http://username:password@server:port')}")
            print(f"\n{_dim('Check your provider dashboard for these details.')}")
            config["proxy"] = _ask("Proxy URL")
        else:
            proxies = []
            print(f"\n{_dim('Enter each proxy URL on its own line.')}")
            print(f"{_dim('Press Enter on an empty line when done.')}")
            while True:
                p = input("  Proxy URL: ").strip()
                if not p:
                    break
                proxies.append(p)
            if proxies:
                config["proxy"] = proxies
                print(f"\n  {_green(str(len(proxies)))} proxies added. Fantoma will rotate between them.")
            else:
                config["proxy"] = None
                print(f"  No proxies added — skipping.")
    else:
        config["proxy"] = None

    # ── Step 3: CAPTCHA ──────────────────────────────────────────
    print(f"\n{_bold('Step 3: CAPTCHA Solving (optional)')}")
    print("Fantoma handles simple CAPTCHAs automatically:")
    print(f"  {_green('Free')}  — Proof-of-work CAPTCHAs (ALTCHA, Friendly Captcha)")
    print(f"          Fantoma clicks the checkbox and the browser solves it.")
    print(f"  {_green('Free')}  — Most sites never show CAPTCHAs at all because")
    print(f"          Fantoma's anti-detection prevents them from triggering.")
    print(f"\nThe only CAPTCHAs that need a paid solver are:")
    print(f"  {_yellow('Paid')}  — reCAPTCHA v2/v3, hCaptcha, Cloudflare Turnstile")
    print(f"          These need a third-party API like CapSolver or 2Captcha.\n")

    use_captcha = _confirm("Do you want to set up a paid CAPTCHA solver?", default=False)
    if use_captcha:
        captcha_provider = _choose("Which CAPTCHA solver?", [
            ("CapSolver — supports reCAPTCHA, hCaptcha, Turnstile", "capsolver"),
            ("2Captcha", "2captcha"),
            ("Anti-Captcha", "anticaptcha"),
        ])
        config["captcha_api"] = captcha_provider
        print(f"\n{_dim('Get your API key from the provider dashboard.')}")
        config["captcha_key"] = _ask(f"{captcha_provider} API key", secret=True)

        print(f"\n  If even the API solver fails, Fantoma can send you a screenshot")
        print(f"  of the CAPTCHA via a webhook (Slack, Discord, or any URL).")
        use_webhook = _confirm("  Set up human fallback?", default=False)
        if use_webhook:
            print(f"\n{_dim('  Paste your Slack/Discord webhook URL:')}")
            config["captcha_webhook"] = _ask("  Webhook URL")
        else:
            config["captcha_webhook"] = None
    else:
        config["captcha_api"] = None
        config["captcha_key"] = None
        config["captcha_webhook"] = None
        print(f"\n  {_dim('No problem. Proof-of-work CAPTCHAs are still handled for free.')}")

    # ── Step 4: Model Escalation ─────────────────────────────────
    if llm_type == "local":
        print(f"\n{_bold('Step 4: Model Escalation (optional)')}")
        print("Sometimes a small local model gets stuck on a complex page.")
        print("Fantoma can automatically switch to a cloud API just for that")
        print("one step, then go back to your local model for the rest.\n")
        print(f"{_dim('You only pay for the steps that need it — most stay local and free.')}\n")

        use_escalation = _confirm("Set up a cloud API fallback?", default=False)
        if use_escalation:
            esc_provider = _choose("Which cloud API to fall back to?", [
                ("Kimi Moonshot (most affordable)", "kimi"),
                ("OpenAI GPT-4o-mini (fast, reliable)", "openai"),
                ("Anthropic Claude (most capable)", "anthropic"),
                ("Other OpenAI-compatible API", "other"),
            ])

            esc_endpoints = {
                "kimi": "https://api.moonshot.ai/v1",
                "openai": "https://api.openai.com/v1",
                "anthropic": "https://api.anthropic.com/v1",
            }

            if esc_provider == "other":
                esc_url = _ask("API endpoint URL")
            else:
                esc_url = esc_endpoints[esc_provider]

            esc_key = _ask("API key for the fallback", secret=True)
            config["escalation"] = [config["llm_url"], esc_url]
            config["escalation_key"] = esc_key
            print(f"\n  {_green('Escalation set up.')} Local model first, cloud fallback if stuck.")
        else:
            config["escalation"] = None
    else:
        config["escalation"] = None

    # ── Save config ──────────────────────────────────────────────
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(json.dumps(config, indent=2))
    os.chmod(CONFIG_FILE, 0o600)  # Restrict permissions (contains API keys)

    print(f"""
{_green('Setup complete!')}

Config saved to: {CONFIG_FILE}
{_dim('(File permissions set to owner-only for security)')}

{_bold('Next steps:')}
  fantoma test     — verify your setup works
  fantoma run "Go to github.com/trending and tell me the top repo"

{_dim('Run fantoma setup again any time to change your settings.')}
""")


# ── Test command ─────────────────────────────────────────────────

def cmd_test():
    """Quick test to verify the setup works."""
    config = _load_config()
    if not config:
        print(_red("No config found. Run 'fantoma setup' first."))
        return

    print(BANNER)
    print(f"{_bold('Fantoma Quick Test')}\n")

    # Test 1: LLM connection
    print(f"  Testing LLM connection... ", end="", flush=True)
    try:
        from fantoma.llm.client import LLMClient
        client = LLMClient(
            base_url=config["llm_url"],
            api_key=config.get("api_key", ""),
            model=config.get("model", "auto"),
        )
        response = client.chat(
            [{"role": "user", "content": "Reply with only the word 'hello'"}],
            max_tokens=10,
        )
        if response and "hello" in response.lower():
            print(_green("OK") + f" — {client._resolve_model()}")
        elif response:
            print(_green("OK") + f" — got: {response[:30]}")
        else:
            print(_red("FAIL") + " — no response")
            return
    except Exception as e:
        print(_red("FAIL") + f" — {e}")
        return

    # Test 2: Browser launch
    print(f"  Testing browser launch... ", end="", flush=True)
    try:
        from fantoma import Fantoma
        browser = Fantoma(headless=True)
        state = browser.start("https://example.com")
        title = state.get("title", "")
        browser.stop()
        if "example" in title.lower():
            print(_green("OK") + f" — Camoufox working")
        else:
            print(_yellow("WARN") + f" — got title: {title}")
    except Exception as e:
        print(_red("FAIL") + f" — {e}")
        print(f"\n  {_dim('Camoufox may need to download on first run. Try again.')}")
        return

    # Test 3: Full agent task
    print(f"  Testing full agent task... ", end="", flush=True)
    try:
        from fantoma import Agent
        agent = Agent(
            llm_url=config["llm_url"],
            api_key=config.get("api_key", ""),
            model=config.get("model", "auto"),
            headless=True,
            timeout=60,
            max_steps=10,
        )
        result = agent.run("Go to https://example.com and tell me what the heading says")
        if result.success and result.data:
            print(_green("OK") + f" — {result.data[:50]}")
        else:
            print(_yellow("PARTIAL") + f" — task ran but: {result.error[:50]}")
    except Exception as e:
        print(_red("FAIL") + f" — {e}")
        return

    # Test 4: Proxy (if configured)
    if config.get("proxy"):
        print(f"  Testing proxy... ", end="", flush=True)
        try:
            from fantoma import Fantoma as _Fantoma
            proxy_browser = _Fantoma(headless=True, proxy=config["proxy"])
            state = proxy_browser.start("https://httpbin.org/ip")
            ip_text = state.get("text", "")[:50]
            proxy_browser.stop()
            print(_green("OK") + f" — {ip_text}")
        except Exception as e:
            print(_red("FAIL") + f" — {e}")

    print(f"\n{_green('All tests passed!')} Fantoma is ready to use.")
    print(f"\n{_dim('Run')} fantoma test full {_dim('to test against 10 real bot-protected sites.')}\n")


# ── Fingerprint test ─────────────────────────────────────────────

def cmd_test_fingerprint():
    """Test Camoufox fingerprint anti-detection consistency."""
    config = _load_config()
    if not config:
        print(_red("No config found. Run 'fantoma setup' first."))
        return

    print(BANNER)
    print(f"{_bold('Fantoma Fingerprint Test')}\n")
    print(f"  Launching browser... ", end="", flush=True)

    try:
        from fantoma import Fantoma as _Fantoma
        from fantoma.browser.fingerprint import FingerprintTest

        browser = _Fantoma(headless=True)
        browser.start("about:blank")
        page = browser._engine.get_page()
        print(_green("OK"))

        print(f"  Running fingerprint checks...\n")
        ft = FingerprintTest()
        results = ft.run_all(page)
        browser.stop()

        for name, check in results["checks"].items():
            status = _green("PASS") if check["passed"] else _red("FAIL")
            print(f"    {status}  {name}: {check['reason']}")

        passed = sum(1 for c in results["checks"].values() if c["passed"])
        total = len(results["checks"])
        print()

        if results["overall"]:
            print(f"  {_green(f'All {total} checks passed!')} Fingerprint looks clean.\n")
        else:
            print(f"  {_yellow(f'{passed}/{total} checks passed.')} Some fingerprint leaks detected.\n")

    except Exception as e:
        print(_red("FAIL") + f" — {e}")
        return


# ── Full test (real sites) ───────────────────────────────────────

FULL_TEST_SITES = [
    ("GitHub", "Go to https://github.com/trending and tell me the top trending repository"),
    ("Amazon UK", "Go to https://www.amazon.co.uk and tell me what the top deal is"),
    ("Reddit", "Go to https://old.reddit.com and tell me the top post title"),
    ("Instagram", "Go to https://www.instagram.com and tell me what the page says"),
    ("LinkedIn", "Go to https://www.linkedin.com and tell me what the page says"),
    ("Booking.com", "Go to https://www.booking.com and tell me what destinations are shown"),
    ("Craigslist", "Go to https://london.craigslist.org and tell me what categories are shown"),
    ("bot.sannysoft", "Go to https://bot.sannysoft.com and tell me if any tests show failed"),
    ("DuckDuckGo", "Go to https://duckduckgo.com, type 'best restaurants', press Enter, tell me the first result"),
    ("Books to Scrape", "Go to https://books.toscrape.com and tell me the first book title and price"),
]


def cmd_test_full():
    """Test against 10 real bot-protected sites."""
    config = _load_config()
    if not config:
        print(_red("No config found. Run 'fantoma setup' first."))
        return

    print(BANNER)
    print(f"{_bold('Fantoma Full Test')} — 10 real sites\n")

    from fantoma import Agent

    agent = Agent(
        llm_url=config["llm_url"],
        api_key=config.get("api_key", ""),
        model=config.get("model", "auto"),
        proxy=config.get("proxy"),
        headless=True,
        timeout=90,
        max_steps=15,
    )

    passed = 0
    failed = 0
    results = []

    for name, task in FULL_TEST_SITES:
        print(f"  {name:15s} ", end="", flush=True)
        start = time.time()
        try:
            result = agent.run(task)
            elapsed = time.time() - start
            if result.success:
                passed += 1
                data = str(result.data)[:40] if result.data else ""
                print(f"{_green('PASS')} {elapsed:5.1f}s  {data}")
            else:
                failed += 1
                print(f"{_red('FAIL')} {elapsed:5.1f}s  {result.error[:40]}")
        except Exception as e:
            elapsed = time.time() - start
            failed += 1
            print(f"{_red('ERR')}  {elapsed:5.1f}s  {str(e)[:40]}")

        results.append({"site": name, "time": round(time.time() - start, 1)})
        time.sleep(1)

    print(f"\n  {_bold('Results:')} {_green(str(passed))}/{len(FULL_TEST_SITES)} passed", end="")
    if failed:
        print(f", {_red(str(failed))} failed")
    else:
        print(f" — {_green('all sites passed!')}")

    avg = sum(r["time"] for r in results) / len(results)
    print(f"  {_dim(f'Average time per site: {avg:.1f}s')}\n")


# ── Run command ──────────────────────────────────────────────────

def cmd_run(task, start_url=None):
    """Run a browser task."""
    config = _load_config()
    if not config:
        print(_red("No config found. Run 'fantoma setup' first."))
        return

    from fantoma import Agent

    escalation_endpoints = config.get("escalation")
    escalation_keys = None
    if escalation_endpoints:
        escalation_keys = [config.get("api_key", ""), config.get("escalation_key", "")]

    agent = Agent(
        llm_url=config["llm_url"],
        api_key=config.get("api_key", ""),
        model=config.get("model", "auto"),
        proxy=config.get("proxy"),
        captcha_api=config.get("captcha_api"),
        captcha_key=config.get("captcha_key"),
        escalation=escalation_endpoints,
        escalation_keys=escalation_keys,
        headless=True,
    )

    result = agent.run(task, start_url=start_url)

    if result.success:
        print(f"\n{_green('Success')} ({result.steps_taken} steps)")
        if result.data:
            print(f"\n{result.data}")
        _add_history(task, True, data=str(result.data)[:100])
        _add_log(f"OK: {task[:60]} — {result.steps_taken} steps")
    else:
        print(f"\n{_red('Failed')}: {result.error}")
        _add_history(task, False, error=result.error[:100])
        _add_log(f"FAIL: {task[:60]} — {result.error[:60]}")


# ── Config helpers ───────────────────────────────────────────────

def _load_config() -> dict:
    """Load saved config."""
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text())
        except Exception:
            return {}
    return {}


# ── Interactive mode ─────────────────────────────────────────────

HELP_TEXT = f"""
{_bold('General:')}

  {_green('/help')}              Show this help
  {_green('/setup')}             Guided first-time setup
  {_green('/config')}             Show current configuration
  {_green('/status')}             Check LLM connection, browser, and config
  {_green('/version')}            Show Fantoma version
  {_green('/update')}             Check for updates
  {_green('/clear')}              Clear the screen
  {_green('/quit')}               Exit Fantoma

{_bold('Tasks:')}

  {_green('/run')} task           Run a browser task
  {_green('/extract')} url query  Extract data from a page
  {_green('/history')}            Show recent tasks and results

{_bold('Testing:')}

  {_green('/test')}              Quick check (LLM + browser + one task)
  {_green('/test full')}         Test against 10 real bot-protected sites
  {_green('/monitor')}            Run the weekly test suite

{_bold('Sessions:')}

  {_green('/session')} url        Start an interactive browser session
  {_green('/act')} instruction    Do something on the page
  {_green('/read')} query         Extract data from current page
  {_green('/observe')}            Show what's on the page (elements + text)
  {_green('/url')}                Show current page URL
  {_green('/tab')} url [name]     Open a new tab
  {_green('/switch')} name        Switch to a tab by name
  {_green('/close')}              Close current tab
  {_green('/tabs')}               List open tabs
  {_green('/done')}               End the session

{_bold('Logs:')}

  {_green('/logs')}              Show recent errors and warnings
  {_green('/logs --trace')}      List saved Playwright trace recordings
  {_green('/logs clear')}        Clear the log

{_dim('Or just type a task directly — Fantoma will run it.')}
"""

# ── History tracking ─────────────────────────────────────────────

HISTORY_FILE = CONFIG_DIR / "history.json"
LOG_BUFFER: list[str] = []  # In-memory log of recent errors/warnings


def _add_history(task: str, success: bool, data: str = "", error: str = ""):
    """Add a task to the history file."""
    from datetime import datetime
    history = []
    if HISTORY_FILE.exists():
        try:
            history = json.loads(HISTORY_FILE.read_text())
        except Exception:
            pass
    history.append({
        "task": task[:200],
        "success": success,
        "data": data[:100],
        "error": error[:100],
        "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
    })
    # Keep last 50 tasks
    history = history[-50:]
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    HISTORY_FILE.write_text(json.dumps(history, indent=2))


def _add_log(message: str):
    """Add to in-memory log buffer and persistent log file."""
    from datetime import datetime
    ts = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    LOG_BUFFER.append(f"{ts} {message}")
    if len(LOG_BUFFER) > 100:
        LOG_BUFFER.pop(0)
    # Persist to file
    try:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a") as f:
            f.write(f"{ts} {message}\n")
        # Keep file under 1000 lines
        if LOG_FILE.stat().st_size > 100_000:
            lines = LOG_FILE.read_text().splitlines()
            LOG_FILE.write_text("\n".join(lines[-500:]) + "\n")
    except Exception:
        pass


def cmd_interactive():
    """Interactive shell mode."""
    print(BANNER)
    print(f"{_bold('Fantoma Interactive Mode')}")
    print(f"{_dim('Type /help for commands, or just type a task to run it.')}\n")

    config = _load_config()
    if not config:
        print(f"{_yellow('No config found.')} Run {_green('/setup')} first.\n")

    session = None
    session_agent = None

    while True:
        try:
            prompt = f"{_green('fantoma')}> " if not session else f"{_green('session')}> "
            user_input = input(prompt).strip()
        except (EOFError, KeyboardInterrupt):
            print("\n")
            break

        if not user_input:
            continue

        # ── Global commands ──────────────────────────────────────
        if user_input in ("/quit", "/exit", "/q"):
            if session:
                session.__exit__(None, None, None)
            break

        elif user_input == "/help":
            print(HELP_TEXT)

        elif user_input == "/setup":
            cmd_setup()
            config = _load_config()

        elif user_input == "/test":
            cmd_test()

        elif user_input == "/test full":
            cmd_test_full()

        elif user_input == "/config":
            config = _load_config()
            if config:
                safe = {k: ("***" if "key" in k.lower() else v) for k, v in config.items()}
                print(f"\n{_bold('Current config:')} {CONFIG_FILE}")
                for k, v in safe.items():
                    if v is not None and v != "":
                        print(f"  {k}: {v}")
                print()
            else:
                print(f"{_yellow('No config.')} Run {_green('/setup')}\n")

        elif user_input == "/version":
            from fantoma import __version__
            print(f"  Fantoma v{__version__}")

        elif user_input == "/status":
            config = _load_config()
            print(f"\n  {_bold('Status:')}")
            # Config
            if config:
                print(f"  Config:  {_green('loaded')} ({CONFIG_FILE})")
                print(f"  LLM:     {config.get('llm_url', 'not set')}")
                print(f"  Model:   {config.get('model', 'auto')}")
            else:
                print(f"  Config:  {_red('not found')} — run /setup")
            # LLM connection
            if config and config.get("llm_url"):
                try:
                    from fantoma.llm.client import LLMClient
                    client = LLMClient(base_url=config["llm_url"], api_key=config.get("api_key", ""), model=config.get("model", "auto"))
                    model = client._resolve_model()
                    print(f"  LLM:     {_green('connected')} — {model}")
                except Exception as e:
                    print(f"  LLM:     {_red('error')} — {e}")
            # Proxy
            if config and config.get("proxy"):
                print(f"  Proxy:   {_green('configured')}")
            else:
                print(f"  Proxy:   {_dim('none')}")
            # CAPTCHA
            if config and config.get("captcha_api"):
                print(f"  CAPTCHA: {_green(config['captcha_api'])}")
            else:
                print(f"  CAPTCHA: {_dim('free only (proof-of-work)')}")
            # Session
            if session:
                print(f"  Session: {_green('active')} ({len(session.tabs)} tabs)")
            else:
                print(f"  Session: {_dim('none')}")
            print()

        elif user_input == "/update":
            print(f"  Checking for updates... ", end="", flush=True)
            try:
                import subprocess
                result = subprocess.run(
                    [sys.executable, "-m", "pip", "index", "versions", "fantoma"],
                    capture_output=True, text=True, timeout=10
                )
                if result.returncode == 0 and result.stdout:
                    print(f"\n  {result.stdout.strip()}")
                else:
                    from fantoma import __version__
                    print(f"\n  Current version: {__version__}")
                    print(f"  {_dim('Update with: pip install --upgrade fantoma')}")
            except Exception:
                from fantoma import __version__
                print(f"\n  Current version: {__version__}")
                print(f"  {_dim('Update with: pip install --upgrade fantoma')}")

        elif user_input == "/clear":
            os.system("clear" if os.name != "nt" else "cls")

        elif user_input == "/history":
            if HISTORY_FILE.exists():
                try:
                    history = json.loads(HISTORY_FILE.read_text())
                    if history:
                        print(f"\n  {_bold('Recent tasks:')}")
                        for h in history[-10:]:
                            icon = _green("OK") if h["success"] else _red("XX")
                            print(f"  {icon} {h['time']}  {h['task'][:60]}")
                        print()
                    else:
                        print(_dim("  No history yet."))
                except Exception:
                    print(_dim("  No history yet."))
            else:
                print(_dim("  No history yet. Run a task first."))

        elif user_input == "/logs":
            # Show from persistent file if in-memory is empty
            lines = LOG_BUFFER[-20:] if LOG_BUFFER else []
            if not lines and LOG_FILE.exists():
                lines = LOG_FILE.read_text().splitlines()[-20:]
            if lines:
                print(f"\n  {_bold('Recent logs:')}")
                for line in lines:
                    print(f"  {line}")
                print()
            else:
                print(_dim("  No logs yet."))

        elif user_input in ("/logs --trace", "/logs trace"):
            cmd_logs_trace()

        elif user_input == "/logs clear":
            LOG_BUFFER.clear()
            if LOG_FILE.exists():
                LOG_FILE.unlink()
            print("  Logs cleared.")

        elif user_input == "/monitor":
            monitor_path = Path(__file__).parent.parent / "weekly_monitor.py"
            if monitor_path.exists():
                os.system(f"{sys.executable} {monitor_path} --quick")
            else:
                print(_red("weekly_monitor.py not found"))

        # ── Session commands ─────────────────────────────────────
        elif user_input.startswith("/session"):
            if not config:
                print(_red("Run /setup first."))
                continue
            url = user_input[8:].strip()
            if not url:
                print(f"  Usage: {_green('/session')} https://example.com")
                continue
            if not url.startswith("http"):
                url = f"https://{url}"

            from fantoma import Agent
            session_agent = Agent(
                llm_url=config["llm_url"],
                api_key=config.get("api_key", ""),
                model=config.get("model", "auto"),
                proxy=config.get("proxy"),
                headless=True,
                timeout=120,
                max_steps=20,
            )
            session = session_agent.session(url)
            session.__enter__()
            print(f"  Session started: {url}")
            print(f"  {_dim('Use /act, /read, /tab, /done. Type /help for all commands.')}\n")

        elif user_input == "/done":
            if session:
                session.__exit__(None, None, None)
                session = None
                session_agent = None
                print("  Session ended.\n")
            else:
                print(_dim("  No active session."))

        elif user_input.startswith("/act"):
            if not session:
                print(_dim("  No active session. Use /session url first."))
                continue
            instruction = user_input[4:].strip()
            if not instruction:
                print(f"  Usage: {_green('/act')} Click the login button")
                continue
            print(f"  Acting: {instruction}... ", end="", flush=True)
            success = session.act(instruction)
            print(_green("done") if success else _red("failed"))

        elif user_input.startswith("/read"):
            if not session:
                print(_dim("  No active session. Use /session url first."))
                continue
            query = user_input[5:].strip()
            if not query:
                print(f"  Usage: {_green('/read')} Get the page title")
                continue
            data = session.extract(query)
            print(f"\n  {data}\n")

        elif user_input.startswith("/tab"):
            if not session:
                print(_dim("  No active session."))
                continue
            parts = user_input[4:].strip().split(None, 1)
            url = parts[0] if parts else ""
            name = parts[1] if len(parts) > 1 else None
            if not url:
                print(f"  Usage: {_green('/tab')} https://example.com [name]")
                continue
            if not url.startswith("http"):
                url = f"https://{url}"
            idx = session.new_tab(url, name=name)
            print(f"  Opened tab {idx}: {url}")

        elif user_input.startswith("/switch"):
            if not session:
                print(_dim("  No active session."))
                continue
            target = user_input[7:].strip()
            if not target:
                print(f"  Usage: {_green('/switch')} main  or  {_green('/switch')} 0")
                continue
            try:
                session.switch_tab(int(target))
            except ValueError:
                session.switch_tab(target)
            print(f"  Switched to: {target}")

        elif user_input == "/close":
            if session:
                session.close_tab()
                print("  Tab closed.")

        elif user_input == "/tabs":
            if session:
                for t in session.tabs:
                    print(f"  [{t['index']}] {t['name']:10s} {t['url'][:60]}")
            else:
                print(_dim("  No active session."))

        elif user_input == "/observe":
            if not session:
                print(_dim("  No active session."))
                continue
            state = session.agent.fantoma.get_state()
            print(f"\n{state['aria_tree']}\n")

        elif user_input == "/url":
            if not session:
                print(_dim("  No active session."))
                continue
            print(f"  {session.agent.fantoma.get_state()['url']}")

        # ── /run or /extract ─────────────────────────────────────
        elif user_input.startswith("/run"):
            task = user_input[4:].strip()
            if not task:
                print(f"  Usage: {_green('/run')} Go to github.com and find the top repo")
                continue
            cmd_run(task)

        elif user_input.startswith("/extract"):
            parts = user_input[8:].strip().split(None, 1)
            if len(parts) < 2:
                print(f"  Usage: {_green('/extract')} https://example.com What is the heading?")
                continue
            url, query = parts[0], parts[1]
            if not url.startswith("http"):
                url = f"https://{url}"
            config = _load_config()
            if not config:
                print(_red("Run /setup first."))
                continue
            from fantoma import Agent
            agent = Agent(
                llm_url=config["llm_url"],
                api_key=config.get("api_key", ""),
                model=config.get("model", "auto"),
                headless=True, timeout=60,
            )
            data = agent.extract(url, query)
            print(f"\n  {data}\n")

        # ── Bare text = run as task ──────────────────────────────
        elif not user_input.startswith("/"):
            if session:
                # In a session, bare text = act
                print(f"  Acting: {user_input}... ", end="", flush=True)
                success = session.act(user_input)
                print(_green("done") if success else _red("failed"))
            else:
                # Outside session, bare text = run as task
                cmd_run(user_input)

        else:
            print(f"  Unknown command: {user_input}. Type {_green('/help')} for options.")


# ── Trace viewer ─────────────────────────────────────────────────

TRACE_DIR = Path.home() / ".local" / "share" / "fantoma" / "traces"


def cmd_logs_trace():
    """List saved Playwright traces."""
    if not TRACE_DIR.exists():
        print(_dim("  No traces found. Run a task with trace=True to record one."))
        print(f"  Trace directory: {TRACE_DIR}")
        return

    traces = sorted(TRACE_DIR.glob("*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not traces:
        print(_dim("  No traces found. Run a task with trace=True to record one."))
        return

    print(f"\n  {_bold('Saved traces:')} ({TRACE_DIR})\n")
    for t in traces[:20]:
        size_kb = t.stat().st_size / 1024
        mtime = time.strftime("%Y-%m-%d %H:%M", time.localtime(t.stat().st_mtime))
        print(f"  {mtime}  {size_kb:6.0f}KB  {t.name}")

    print(f"\n  {_dim('View a trace in your browser:')}")
    print(f"  npx playwright show-trace {TRACE_DIR}/<filename>.zip\n")


# ── Main ─────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Fantoma — the AI browser agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Commands:
  setup           Guided first-time setup
  test            Quick check (LLM + browser + one task)
  test full       Test against 10 real bot-protected sites
  test fingerprint  Validate Camoufox anti-detection fingerprint
  run "task"      Run a browser task
  (no args)       Interactive mode with /commands

Examples:
  fantoma setup
  fantoma test
  fantoma test full
  fantoma test fingerprint
  fantoma run "Go to github.com/trending and tell me the top repo"
  fantoma             (starts interactive mode)
""",
    )
    parser.add_argument("command", nargs="?", default=None,
                       choices=["setup", "test", "run", "monitor", "logs"],
                       help="Command to run (or omit for interactive mode)")
    parser.add_argument("task", nargs="?", default=None,
                       help="Task description (for 'run'), or 'full' (for 'test')")
    parser.add_argument("--start-url", default=None,
                       help="Starting URL (for 'run' command)")

    args = parser.parse_args()

    if args.command is None:
        # No command = interactive mode
        cmd_interactive()
    elif args.command == "setup":
        cmd_setup()
    elif args.command == "test":
        if args.task == "full":
            cmd_test_full()
        elif args.task == "fingerprint":
            cmd_test_fingerprint()
        else:
            cmd_test()
    elif args.command == "run":
        if not args.task:
            print(_red("Usage: fantoma run \"task description\""))
            sys.exit(1)
        cmd_run(args.task, args.start_url)
    elif args.command == "logs":
        if args.task == "trace" or args.task == "--trace":
            cmd_logs_trace()
        elif LOG_FILE.exists():
            lines = LOG_FILE.read_text().splitlines()[-20:]
            for line in lines:
                print(f"  {line}")
        else:
            print("No logs yet.")
    elif args.command == "monitor":
        monitor_path = Path(__file__).parent.parent / "weekly_monitor.py"
        if monitor_path.exists():
            os.system(f"{sys.executable} {monitor_path} --quick")
        else:
            print(_red("weekly_monitor.py not found"))


if __name__ == "__main__":
    main()
