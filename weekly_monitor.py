#!/usr/bin/env python3
"""Fantoma Weekly Monitor — comprehensive feature test suite.

Runs every Friday at 22:00. Tests all features against real sites.
Sends Telegram notification with pass/fail summary.

Usage:
    python3 weekly_monitor.py                    # Run all tests
    python3 weekly_monitor.py --quick            # Quick smoke test (5 sites)
    python3 weekly_monitor.py --notify-only      # Just send last results
"""

import argparse
import json
import logging
import os
import re
import sys
import time
import urllib.request
import urllib.error
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

LOG_DIR = Path(__file__).parent / "monitor_logs"
LOG_DIR.mkdir(exist_ok=True)

RESULTS_FILE = LOG_DIR / "latest_results.json"
HISTORY_FILE = LOG_DIR / "history.json"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(name)s | %(message)s",
    handlers=[
        logging.FileHandler(LOG_DIR / f"monitor_{datetime.now().strftime('%Y%m%d_%H%M')}.log"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("fantoma.monitor")


def detect_llm() -> tuple[str, str]:
    """Find which LLM is loaded. Checks ports 8081 and 8082."""
    for port in [8081, 8082]:
        try:
            req = urllib.request.Request(f"http://localhost:{port}/v1/models")
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read())
                model_id = data["data"][0]["id"]
                return f"http://localhost:{port}/v1", model_id
        except Exception:
            continue
    return "", ""


def send_telegram(message: str):
    """Send result to Telegram via Nero's bot."""
    bot_token = chat_id = None

    # Read from fantoma config or environment
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")

    if not bot_token or not chat_id:
        log.warning("No Telegram config found — skipping notification")
        return

    payload = json.dumps({
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "HTML",
    }).encode()
    req = urllib.request.Request(
        f"https://api.telegram.org/bot{bot_token}/sendMessage",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        urllib.request.urlopen(req, timeout=10)
        log.info("Telegram notification sent")
    except Exception as e:
        log.error("Telegram send failed: %s", e)


# ── Test definitions ─────────────────────────────────────────────

# ── Bot detection test sites (designed for this purpose) ──
FINGERPRINT_TESTS = [
    ("bot.sannysoft", "Go to https://bot.sannysoft.com and tell me if any tests show failed"),
    ("nowsecure.nl", "Go to https://nowsecure.nl and tell me what the page says"),
    ("browserleaks.com", "Go to https://browserleaks.com/javascript and tell me if the browser is detected as automated"),
]

# ── Practice scraping sites (safe, designed for testing) ──
SCRAPE_TESTS = [
    ("books.toscrape", "Go to https://books.toscrape.com and tell me the first book title and price"),
    ("quotes.toscrape", "Go to https://quotes.toscrape.com and tell me the first quote and author"),
    ("httpbin", "Go to https://httpbin.org/headers and tell me what the User-Agent header is"),
]

# ── Real bot-protected sites ──
PROTECTED_TESTS = [
    ("GitHub", "Go to https://github.com/trending and tell me the top trending repository"),
    ("Amazon UK", "Go to https://www.amazon.co.uk and tell me what the top deal is"),
    ("Reddit", "Go to https://old.reddit.com and tell me the top post title"),
    ("Nike", "Go to https://www.nike.com/gb/ and tell me what products are featured"),
    ("Instagram", "Go to https://www.instagram.com and tell me what the page says"),
    ("LinkedIn", "Go to https://www.linkedin.com and tell me what the page says"),
    ("Craigslist", "Go to https://london.craigslist.org and tell me what categories are shown"),
    ("Booking.com", "Go to https://www.booking.com and tell me what destinations are shown"),
]

# ── CAPTCHA test sites ──
CAPTCHA_TESTS = [
    ("reCAPTCHA demo", "Go to https://www.google.com/recaptcha/api2/demo and tell me what the page says"),
    ("hCaptcha demo", "Go to https://accounts.hcaptcha.com/demo and tell me what the page says"),
    ("CS Jobs (ALTCHA)", "Go to https://www.civilservicejobs.service.gov.uk and tell me what the page says"),
]

# Full suite: fingerprints → practice → CAPTCHA → real protected sites
BROWSE_TESTS = FINGERPRINT_TESTS + SCRAPE_TESTS + CAPTCHA_TESTS + PROTECTED_TESTS
QUICK_TESTS = FINGERPRINT_TESTS[:2] + PROTECTED_TESTS[:3]

EXTRACTION_TEST = {
    "url": "https://books.toscrape.com",
    "query": "First 3 books with title and price",
    "schema": {"title": str, "price": str},
}


def run_browse_test(agent_cls, llm_url: str, model: str, name: str, task: str) -> dict:
    """Run a single browse test."""
    start = time.time()
    try:
        agent = agent_cls(llm_url=llm_url, model=model, headless=True, timeout=90, max_steps=15)
        result = agent.run(task)
        elapsed = time.time() - start
        return {
            "test": name,
            "type": "browse",
            "status": "PASS" if result.success else "FAIL",
            "time_s": round(elapsed, 1),
            "data": str(result.data)[:100] if result.data else "",
            "error": result.error[:100] if result.error else "",
        }
    except Exception as e:
        return {
            "test": name,
            "type": "browse",
            "status": "ERROR",
            "time_s": round(time.time() - start, 1),
            "error": str(e)[:100],
        }


def run_extraction_test(agent_cls, llm_url: str, model: str) -> dict:
    """Run structured extraction test."""
    start = time.time()
    try:
        agent = agent_cls(llm_url=llm_url, model=model, headless=True, timeout=60)
        data = agent.extract(
            EXTRACTION_TEST["url"],
            EXTRACTION_TEST["query"],
            schema=EXTRACTION_TEST["schema"],
        )
        elapsed = time.time() - start
        success = isinstance(data, list) and len(data) > 0
        return {
            "test": "Structured Extraction",
            "type": "extraction",
            "status": "PASS" if success else "FAIL",
            "time_s": round(elapsed, 1),
            "data": str(data)[:200] if data else "",
        }
    except Exception as e:
        return {
            "test": "Structured Extraction",
            "type": "extraction",
            "status": "ERROR",
            "time_s": round(time.time() - start, 1),
            "error": str(e)[:100],
        }


def run_login_test(agent_cls, llm_url: str, model: str) -> dict:
    """Test ProtonMail login (if credentials available)."""
    email = os.environ.get("PROTON_EMAIL", "")
    password = os.environ.get("PROTON_PASS", "")
    if not password:
        return {"test": "ProtonMail Login", "type": "login", "status": "SKIP", "error": "No PROTON_PASS"}

    start = time.time()
    try:
        from fantoma.browser.engine import BrowserEngine
        browser = BrowserEngine(headless=True)
        browser.start()
        browser.navigate("https://mail.google.com")
        time.sleep(5)
        page = browser.get_page()
        page.get_by_label("Email or username").fill(email)
        page.get_by_label("Password").fill(password)
        page.get_by_role("button", name="Sign in").click()
        page.wait_for_load_state("networkidle", timeout=30000)
        time.sleep(10)
        text = page.inner_text("body")[:1000]
        browser.stop()
        elapsed = time.time() - start

        success = "inbox" in text.lower() or "mail" in text.lower() or "conversation" in text.lower()
        return {
            "test": "ProtonMail Login",
            "type": "login",
            "status": "PASS" if success else "FAIL",
            "time_s": round(elapsed, 1),
            "data": text[:100],
        }
    except Exception as e:
        return {
            "test": "ProtonMail Login",
            "type": "login",
            "status": "ERROR",
            "time_s": round(time.time() - start, 1),
            "error": str(e)[:100],
        }


def run_multitab_test(agent_cls, llm_url: str, model: str) -> dict:
    """Test multi-tab functionality."""
    start = time.time()
    try:
        agent = agent_cls(llm_url=llm_url, model=model, headless=True, timeout=60, max_steps=10)
        with agent.session("https://example.com") as s:
            time.sleep(2)
            # Open second tab
            s.new_tab("https://github.com/trending", name="github")
            time.sleep(3)
            tabs = s.tabs
            # Switch back
            s.switch_tab("main")
            time.sleep(1)
            main_url = s._browser.get_url()
            # Close second tab
            s.close_tab("github")
            remaining = s.tabs

            elapsed = time.time() - start
            success = (
                len(tabs) == 2
                and "example.com" in main_url
                and len(remaining) == 1
            )
            return {
                "test": "Multi-Tab",
                "type": "feature",
                "status": "PASS" if success else "FAIL",
                "time_s": round(elapsed, 1),
                "data": f"tabs={len(tabs)}, switched OK, closed OK" if success else f"tabs={tabs}",
            }
    except Exception as e:
        return {
            "test": "Multi-Tab",
            "type": "feature",
            "status": "ERROR",
            "time_s": round(time.time() - start, 1),
            "error": str(e)[:100],
        }


def run_verification_test() -> dict:
    """Test verification code/link extraction (no browser needed)."""
    from fantoma.browser.verification import extract_verification_code, extract_verification_link

    start = time.time()
    try:
        # Test with mock — create a minimal page-like object
        class MockPage:
            def inner_text(self, sel):
                return "Your verification code is 482917. Enter it to confirm your account."
            def locator(self, sel):
                class MockLocator:
                    def count(self): return 0
                    def all(self): return []
                return MockLocator()

        code = extract_verification_code(MockPage())
        success = code == "482917"
        return {
            "test": "Verification Extraction",
            "type": "feature",
            "status": "PASS" if success else "FAIL",
            "time_s": round(time.time() - start, 3),
            "data": f"code={code}" if code else "no code found",
        }
    except Exception as e:
        return {
            "test": "Verification Extraction",
            "type": "feature",
            "status": "ERROR",
            "time_s": round(time.time() - start, 3),
            "error": str(e)[:100],
        }


def run_all_tests(quick: bool = False) -> list[dict]:
    """Run the full test suite."""
    from fantoma import Agent

    llm_url, model_id = detect_llm()
    if not llm_url:
        log.error("No LLM found on ports 8081 or 8082")
        return [{"test": "LLM Detection", "status": "ERROR", "error": "No LLM available"}]

    log.info("Using LLM: %s (%s)", model_id, llm_url)
    results = []
    results.append({"test": "LLM Detection", "type": "setup", "status": "PASS", "data": f"{model_id} on {llm_url}"})

    # 1. Browse tests
    tests = QUICK_TESTS if quick else BROWSE_TESTS
    log.info("Running %d browse tests...", len(tests))
    for name, task in tests:
        log.info("  Testing %s...", name)
        r = run_browse_test(Agent, llm_url, model_id, name, task)
        results.append(r)
        log.info("    %s (%ss)", r["status"], r["time_s"])
        time.sleep(2)

    # 2. Structured extraction
    log.info("Running extraction test...")
    results.append(run_extraction_test(Agent, llm_url, model_id))

    # 4. Login test
    log.info("Running login test...")
    results.append(run_login_test(Agent, llm_url, model_id))

    # 5. Multi-tab test
    log.info("Running multi-tab test...")
    results.append(run_multitab_test(Agent, llm_url, model_id))

    # 6. Verification extraction test (no browser)
    log.info("Running verification extraction test...")
    results.append(run_verification_test())

    return results


def format_results(results: list[dict]) -> str:
    """Format results for Telegram message."""
    passed = sum(1 for r in results if r["status"] == "PASS")
    failed = sum(1 for r in results if r["status"] == "FAIL")
    errors = sum(1 for r in results if r["status"] == "ERROR")
    skipped = sum(1 for r in results if r["status"] == "SKIP")
    total = len(results)

    # Overall status
    if failed == 0 and errors == 0:
        header = "✅ <b>Fantoma Weekly Monitor — ALL PASS</b>"
    else:
        header = "❌ <b>Fantoma Weekly Monitor — ISSUES FOUND</b>"

    lines = [
        header,
        f"<code>{datetime.now().strftime('%Y-%m-%d %H:%M')}</code>",
        f"",
        f"<b>Summary:</b> {passed}/{total} pass, {failed} fail, {errors} error, {skipped} skip",
        "",
    ]

    # Group by type
    by_type = {}
    for r in results:
        t = r.get("type", "other")
        by_type.setdefault(t, []).append(r)

    type_names = {"setup": "Setup", "browse": "Browse Tests", "extraction": "Extraction",
                  "login": "Login", "feature": "Features", "other": "Other"}

    for t, items in by_type.items():
        lines.append(f"<b>{type_names.get(t, t)}:</b>")
        for r in items:
            icon = "✅" if r["status"] == "PASS" else "❌" if r["status"] in ("FAIL", "ERROR") else "⏭"
            time_str = f" ({r['time_s']}s)" if "time_s" in r else ""
            error_str = f" — {r.get('error', '')}" if r["status"] in ("FAIL", "ERROR") and r.get("error") else ""
            lines.append(f"  {icon} {r['test']}{time_str}{error_str}")
        lines.append("")

    # Model info
    model_info = next((r for r in results if r["test"] == "LLM Detection"), None)
    if model_info and model_info.get("data"):
        lines.append(f"<b>Model:</b> {model_info['data']}")

    return "\n".join(lines)


def save_results(results: list[dict]):
    """Save results to file and append to history."""
    timestamp = datetime.now().isoformat()
    entry = {"timestamp": timestamp, "results": results}

    # Save latest
    RESULTS_FILE.write_text(json.dumps(entry, indent=2))

    # Append to history (keep last 12 weeks)
    history = []
    if HISTORY_FILE.exists():
        try:
            history = json.loads(HISTORY_FILE.read_text())
        except Exception:
            pass
    history.append(entry)
    history = history[-12:]
    HISTORY_FILE.write_text(json.dumps(history, indent=2))


def main():
    parser = argparse.ArgumentParser(description="Fantoma Weekly Monitor")
    parser.add_argument("--quick", action="store_true", help="Quick smoke test (5 sites)")
    parser.add_argument("--notify-only", action="store_true", help="Just send last results")
    args = parser.parse_args()

    if args.notify_only:
        if RESULTS_FILE.exists():
            entry = json.loads(RESULTS_FILE.read_text())
            msg = format_results(entry["results"])
            send_telegram(msg)
            print(msg)
        else:
            print("No results file found")
        return

    log.info("Fantoma Weekly Monitor starting...")
    start = time.time()
    results = run_all_tests(quick=args.quick)
    elapsed = time.time() - start
    log.info("Monitor complete in %.0fs", elapsed)

    # Save
    save_results(results)

    # Format and notify
    msg = format_results(results)
    print("\n" + msg)
    send_telegram(msg)


if __name__ == "__main__":
    main()
