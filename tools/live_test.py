#!/usr/bin/env python3
"""Fantoma Live Test — full integration: signup + email verify + escalation + CAPTCHA.

Validates every integration at startup before running.
Each test is isolated — one browser crash doesn't kill the suite.
Logs to ~/logs/fantoma-live-YYYYMMDD-HHMM.log + JSON results.

Usage:
    python3 tools/live_test.py
    python3 tools/live_test.py --dry-run
"""

import imaplib
import json
import logging
import os
import random
import signal
import subprocess
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# ── Config (copied from working test files) ─────────────────────────

HERCULES = os.environ.get("FANTOMA_LLM_URL", "http://localhost:8080/v1")
DEEPSEEK = os.environ.get("DEEPSEEK_URL", "https://api.deepseek.com/v1")
DEEPSEEK_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
try:
    CAPSOLVER_KEY = json.load(open(Path.home() / ".config/capsolver/config.json"))["api_key"]
except Exception:
    CAPSOLVER_KEY = os.environ.get("CAPSOLVER_KEY", "")

EMAIL = os.environ.get("TEST_EMAIL", "m5aibot@proton.me")
IMAP_HOST = os.environ.get("IMAP_HOST", "127.0.0.1")
IMAP_PORT = int(os.environ.get("IMAP_PORT", "1143"))
IMAP_PASS = os.environ.get("IMAP_PASS", "")
IMAP_SECURITY = os.environ.get("IMAP_SECURITY", "starttls")

PASSWORD = os.environ.get("TEST_PASSWORD", "F4nt0ma_Test_2026!")
LOG_DIR = os.path.expanduser("~/logs")
TIMEOUT_PER_TEST = 120  # seconds hard limit per test

# ── Logging ──────────────────────────────────────────────────────────

os.makedirs(LOG_DIR, exist_ok=True)
ts = datetime.now().strftime("%Y%m%d-%H%M")
log_file = os.path.join(LOG_DIR, f"fantoma-live-{ts}.log")
results_file = os.path.join(LOG_DIR, f"fantoma-live-{ts}.json")

# File gets everything (DEBUG) — console gets summary (INFO)
file_handler = logging.FileHandler(log_file)
file_handler.setLevel(logging.DEBUG)
file_handler.setFormatter(logging.Formatter("%(asctime)s | %(name)s | %(levelname)s | %(message)s"))

console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))

logging.basicConfig(level=logging.DEBUG, handlers=[file_handler, console_handler])

# Silence noisy HTTP debug logs in console but keep them in file
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)

log = logging.getLogger("live_test")


# ── Startup Validation ──────────────────────────────────────────────

def validate_integrations():
    """Check every integration is reachable. Abort if any required one fails."""
    errors = []

    # 1. LLM reachable
    log.info("Checking LLM at %s ...", HERCULES)
    try:
        import httpx
        r = httpx.get(f"{HERCULES}/models", timeout=10)
        r.raise_for_status()
        model = r.json()["data"][0]["id"]
        log.info("  LLM OK: %s", model)
    except Exception as e:
        errors.append(f"LLM unreachable at {HERCULES}: {e}")

    # 2. DeepSeek escalation reachable
    log.info("Checking DeepSeek escalation at %s ...", DEEPSEEK)
    try:
        r = httpx.get(f"{DEEPSEEK}/models", headers={"Authorization": f"Bearer {DEEPSEEK_KEY}"}, timeout=10)
        r.raise_for_status()
        log.info("  DeepSeek OK")
    except Exception as e:
        errors.append(f"DeepSeek unreachable: {e}")

    # 3. IMAP login
    log.info("Checking IMAP at %s:%d ...", IMAP_HOST, IMAP_PORT)
    try:
        m = imaplib.IMAP4(IMAP_HOST, IMAP_PORT)
        m.login(EMAIL, IMAP_PASS)
        m.select("INBOX")
        typ, data = m.search(None, "ALL")
        count = len(data[0].split()) if data[0] else 0
        m.logout()
        log.info("  IMAP OK: %d emails in inbox", count)
    except Exception as e:
        errors.append(f"IMAP login failed: {e}")

    # 4. CapSolver balance
    log.info("Checking CapSolver balance ...")
    try:
        r = httpx.post("https://api.capsolver.com/getBalance",
                       json={"clientKey": CAPSOLVER_KEY}, timeout=10)
        bal = r.json().get("balance", 0)
        log.info("  CapSolver OK: $%.2f balance", bal)
        if bal < 0.10:
            log.warning("  CapSolver balance LOW — CAPTCHA tests may fail")
    except Exception as e:
        errors.append(f"CapSolver unreachable: {e}")

    if errors:
        for e in errors:
            log.error("STARTUP FAIL: %s", e)
        log.error("Aborting — fix integrations before running tests")
        sys.exit(1)

    log.info("All integrations OK")
    log.info("Config: LLM=%s, Escalation=%s, Email=%s, CapSolver=YES", HERCULES, DEEPSEEK, EMAIL)


# ── Agent Factory ────────────────────────────────────────────────────

def make_agent(timeout=90):
    from fantoma import Agent
    return Agent(
        llm_url=HERCULES,
        escalation=[HERCULES, DEEPSEEK],
        escalation_keys=["", DEEPSEEK_KEY],
        headless=True,
        timeout=timeout,
        max_steps=30,
        trace=True,
        captcha_api="capsolver",
        captcha_key=CAPSOLVER_KEY,
        email_imap={
            "host": IMAP_HOST,
            "port": IMAP_PORT,
            "user": EMAIL,
            "password": IMAP_PASS,
            "security": IMAP_SECURITY,
        },
    )


# ── Helpers ──────────────────────────────────────────────────────────

def random_username():
    return f"fantoma_test_{random.randint(10000, 99999)}"

def random_email():
    tag = f"test{random.randint(1000, 9999)}"
    return f"m5aibot+{tag}@proton.me"

def kill_stale_browsers():
    """Kill any leftover browser processes between tests."""
    try:
        subprocess.run(
            ["pkill", "-f", "chrome-headless-shell.*fantoma"],
            capture_output=True, timeout=5
        )
        subprocess.run(
            ["pkill", "-f", "camoufox.*fantoma"],
            capture_output=True, timeout=5
        )
    except Exception:
        pass
    time.sleep(1)


# ── Test Definitions ─────────────────────────────────────────────────

TESTS = [
    {
        "name": "1. Dev.to signup",
        "category": "signup",
        "url": "https://dev.to/enter",
        "run": lambda a: a.login(
            "https://dev.to/enter",
            email=random_email(), password=PASSWORD,
        ),
    },
    {
        "name": "2. Hashnode signup",
        "category": "signup",
        "url": "https://hashnode.com/onboard",
        "run": lambda a: a.login(
            "https://hashnode.com/onboard",
            email=random_email(), password=PASSWORD,
        ),
    },
    {
        "name": "3. TestPyPI signup",
        "category": "signup",
        "url": "https://test.pypi.org/account/register/",
        "run": lambda a: a.login(
            "https://test.pypi.org/account/register/",
            first_name="Fantoma", last_name="Tester",
            email=random_email(), username=random_username(),
            password=PASSWORD + "!Aa1",
        ),
    },
    {
        "name": "4. Discourse signup",
        "category": "signup",
        "url": "https://try.discourse.org/signup",
        "run": lambda a: a.login(
            "https://try.discourse.org/signup",
            first_name="Fantoma",
            email=random_email(), username=random_username(),
            password=PASSWORD,
        ),
    },
    {
        "name": "5. nopCommerce signup",
        "category": "signup",
        "url": "https://demo.nopcommerce.com/register",
        "run": lambda a: a.login(
            "https://demo.nopcommerce.com/register",
            first_name="Fantoma", last_name="Tester",
            email=random_email(), password=PASSWORD,
        ),
    },
    {
        "name": "6. Parabank signup",
        "category": "signup",
        "url": "https://parabank.parasoft.com/parabank/register.htm",
        "run": lambda a: a.login(
            "https://parabank.parasoft.com/parabank/register.htm",
            first_name="Fantoma", last_name="Tester",
            username=random_username(), password=PASSWORD,
        ),
    },
    {
        "name": "7. Booking.com browse",
        "category": "antidetect",
        "url": "https://www.booking.com/",
        "run": lambda a: a.run(
            "What destination is being promoted?",
            start_url="https://www.booking.com/",
        ),
    },
    {
        "name": "8. Amazon UK browse",
        "category": "antidetect",
        "url": "https://www.amazon.co.uk/",
        "run": lambda a: a.run(
            "What is the top deal on the homepage?",
            start_url="https://www.amazon.co.uk/",
        ),
    },
    {
        "name": "9. GitHub trending extract",
        "category": "extract",
        "url": "https://github.com/trending",
        "run": lambda a: a.extract(
            "https://github.com/trending",
            "Top 3 trending repos with name and description",
            schema={"name": str, "description": str},
        ),
    },
    {
        "name": "10. Wikipedia extract",
        "category": "extract",
        "url": "https://en.wikipedia.org/wiki/United_Kingdom",
        "run": lambda a: a.extract(
            "https://en.wikipedia.org/wiki/United_Kingdom",
            "Population and capital city",
        ),
    },
]


# ── Test Runner ──────────────────────────────────────────────────────

def run_one_test(test_def):
    """Run a single test with hard timeout and full isolation."""
    name = test_def["name"]
    category = test_def["category"]
    url = test_def["url"]

    log.info("=" * 70)
    log.info("TEST: %s [%s]", name, category)
    log.info("URL: %s", url)
    log.info("=" * 70)

    result = {
        "name": name,
        "category": category,
        "url": url,
        "success": False,
        "duration": 0,
        "error": "",
        "data": "",
        "steps": 0,
        "escalations": 0,
    }

    start = time.time()

    try:
        agent = make_agent(timeout=TIMEOUT_PER_TEST - 10)
        raw = test_def["run"](agent)
        duration = time.time() - start
        result["duration"] = round(duration, 1)

        if hasattr(raw, "success"):
            result["success"] = raw.success
            result["error"] = raw.error or ""
            result["data"] = str(raw.data)[:500] if raw.data else ""
            result["steps"] = raw.steps_taken
            result["escalations"] = raw.escalations
        elif isinstance(raw, dict):
            result["success"] = raw.get("success", False)
            result["steps"] = raw.get("steps", 0)
            result["data"] = str(raw.get("fields_filled", []))[:500]
            result["url"] = raw.get("url", url)
            result["error"] = raw.get("error", "")
            if raw.get("verification_needed"):
                result["data"] += f" | verification_needed={raw['verification_needed']}"
        elif isinstance(raw, list):
            result["success"] = len(raw) > 0
            result["data"] = str(raw)[:500]
            result["steps"] = len(raw)
        elif isinstance(raw, str):
            result["success"] = len(raw) > 10
            result["data"] = raw[:500]

        log.info("RESULT: success=%s, steps=%s, escalations=%s, duration=%.1fs",
                 result["success"], result["steps"], result["escalations"], duration)
        if result["data"]:
            log.info("DATA: %s", result["data"][:200])
        if result["error"]:
            log.warning("ERROR: %s", result["error"])

    except Exception as e:
        duration = time.time() - start
        result["duration"] = round(duration, 1)
        result["error"] = str(e)
        log.error("CRASHED: %s (%.1fs)", e, duration)
        log.error(traceback.format_exc())

    # Kill any leftover browser processes from this test
    kill_stale_browsers()

    return result


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.dry_run:
        print(f"\n{len(TESTS)} tests:")
        for t in TESTS:
            print(f"  [{t['category']:10s}] {t['name']} — {t['url']}")
        return

    validate_integrations()

    results = []
    for i, t in enumerate(TESTS, 1):
        log.info("[%d/%d] %s", i, len(TESTS), t["name"])
        result = run_one_test(t)
        results.append(result)

        # Save after each test (survive crashes)
        with open(results_file, "w") as f:
            json.dump(results, f, indent=2)

        if i < len(TESTS):
            pause = 10 + random.randint(0, 10)
            log.info("Pausing %ds ...", pause)
            time.sleep(pause)

    # Summary
    total = len(results)
    passed = sum(1 for r in results if r["success"])
    escalated = sum(r["escalations"] for r in results)
    total_time = sum(r["duration"] for r in results)

    log.info("=" * 70)
    log.info("DONE: %d/%d passed (%.0f%%), %d escalations, %.0fs total",
             passed, total, 100 * passed / total if total else 0, escalated, total_time)
    log.info("=" * 70)

    for r in results:
        status = "PASS" if r["success"] else "FAIL"
        esc = f" [esc={r['escalations']}]" if r["escalations"] else ""
        err = f" — {r['error'][:60]}" if r["error"] else ""
        log.info("  [%s] %s (%.1fs)%s%s", status, r["name"], r["duration"], esc, err)

    log.info("Results: %s", results_file)
    log.info("Log: %s", log_file)


if __name__ == "__main__":
    main()
