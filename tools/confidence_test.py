#!/usr/bin/env python3
"""Fantoma Confidence Test — 10 brand new sites, full pipeline.

Tests signup, browsing, extraction, and login on sites never tested before.
Each test runs one browser at a time, kills it after, pauses between tests.
Startup validation aborts if any integration is broken.

Usage:
    python3 tools/confidence_test.py
    python3 tools/confidence_test.py --dry-run
"""

import imaplib
import json
import logging
import os
import random
import subprocess
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

# ── Config ───────────────────────────────────────────────────────────

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
TIMEOUT_PER_TEST = 150  # seconds — generous for email polling

# ── Logging ──────────────────────────────────────────────────────────

os.makedirs(LOG_DIR, exist_ok=True)
ts = datetime.now().strftime("%Y%m%d-%H%M")
log_file = os.path.join(LOG_DIR, f"fantoma-confidence-{ts}.log")
results_file = os.path.join(LOG_DIR, f"fantoma-confidence-{ts}.json")

file_handler = logging.FileHandler(log_file)
file_handler.setLevel(logging.DEBUG)
file_handler.setFormatter(logging.Formatter("%(asctime)s | %(name)s | %(levelname)s | %(message)s"))

console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s"))

logging.basicConfig(level=logging.DEBUG, handlers=[file_handler, console_handler])
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)

log = logging.getLogger("confidence")


# ── Startup Validation ──────────────────────────────────────────────

def validate():
    errors = []

    log.info("Checking LLM ...")
    try:
        import httpx
        r = httpx.get(f"{HERCULES}/models", timeout=10)
        r.raise_for_status()
        log.info("  LLM OK: %s", r.json()["data"][0]["id"])
    except Exception as e:
        errors.append(f"LLM: {e}")

    log.info("Checking DeepSeek ...")
    try:
        import httpx
        r = httpx.get(f"{DEEPSEEK}/models", headers={"Authorization": f"Bearer {DEEPSEEK_KEY}"}, timeout=10)
        r.raise_for_status()
        log.info("  DeepSeek OK")
    except Exception as e:
        errors.append(f"DeepSeek: {e}")

    log.info("Checking IMAP ...")
    try:
        m = imaplib.IMAP4(IMAP_HOST, IMAP_PORT)
        m.login(EMAIL, IMAP_PASS)
        m.select("INBOX")
        m.logout()
        log.info("  IMAP OK")
    except Exception as e:
        errors.append(f"IMAP: {e}")

    log.info("Checking CapSolver ...")
    try:
        import httpx
        r = httpx.post("https://api.capsolver.com/getBalance",
                       json={"clientKey": CAPSOLVER_KEY}, timeout=10)
        bal = r.json().get("balance", 0)
        log.info("  CapSolver OK: $%.2f", bal)
    except Exception as e:
        errors.append(f"CapSolver: {e}")

    if errors:
        for e in errors:
            log.error("FAIL: %s", e)
        sys.exit(1)
    log.info("All integrations OK")


# ── Agent Factory ────────────────────────────────────────────────────

def make_agent(timeout=120):
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


def random_email():
    return f"m5aibot+conf{random.randint(1000, 9999)}@proton.me"

def random_username():
    return f"fantoma_conf_{random.randint(10000, 99999)}"

def kill_browsers():
    try:
        subprocess.run(["pkill", "-f", "chrome-headless-shell"], capture_output=True, timeout=5)
        subprocess.run(["pkill", "-f", "camoufox"], capture_output=True, timeout=5)
    except Exception:
        pass
    time.sleep(1)


# ── Test Definitions ─────────────────────────────────────────────────

TESTS = [
    # --- SIGNUP + EMAIL VERIFICATION (brand new sites) ---
    {
        "name": "1. Supabase signup",
        "category": "signup+verify",
        "url": "https://supabase.com/dashboard/sign-up",
        "run": lambda a: a.login(
            "https://supabase.com/dashboard/sign-up",
            email=random_email(),
            password=PASSWORD,
        ),
    },
    {
        "name": "2. Replinova signup",
        "category": "signup+verify",
        "url": "https://replinova.co.uk/signup",
        "run": lambda a: a.login(
            "https://replinova.co.uk/signup",
            email=random_email(),
        ),
    },
    {
        "name": "3. CouchSync signup",
        "category": "signup+verify",
        "url": "https://couchsync.com",
        "run": lambda a: a.login(
            "https://couchsync.com",
            email=random_email(),
        ),
    },
    {
        "name": "4. PlanetScale signup",
        "category": "signup+verify",
        "url": "https://auth.planetscale.com/sign-up",
        "run": lambda a: a.login(
            "https://auth.planetscale.com/sign-up",
            email=random_email(),
            password=PASSWORD,
        ),
    },
    {
        "name": "5. Linear signup",
        "category": "signup+verify",
        "url": "https://linear.app/join",
        "run": lambda a: a.login(
            "https://linear.app/join",
            email=random_email(),
        ),
    },

    # --- BROWSING (anti-detection on new sites) ---
    {
        "name": "6. Rightmove browse",
        "category": "antidetect",
        "url": "https://www.rightmove.co.uk/",
        "run": lambda a: a.run(
            "What properties are featured on the homepage?",
            start_url="https://www.rightmove.co.uk/",
        ),
    },
    {
        "name": "7. BBC News browse",
        "category": "antidetect",
        "url": "https://www.bbc.co.uk/news",
        "run": lambda a: a.run(
            "What is the top headline?",
            start_url="https://www.bbc.co.uk/news",
        ),
    },

    # --- EXTRACTION (structured data from new sites) ---
    {
        "name": "8. PyPI trending extract",
        "category": "extract",
        "url": "https://pypi.org/",
        "run": lambda a: a.extract(
            "https://pypi.org/",
            "Top 3 trending projects with name and description",
            schema={"name": str, "description": str},
        ),
    },
    {
        "name": "9. Hacker News extract",
        "category": "extract",
        "url": "https://news.ycombinator.com/",
        "run": lambda a: a.extract(
            "https://news.ycombinator.com/",
            "Top 3 stories with title and points",
            schema={"title": str, "points": str},
        ),
    },

    # --- LOGIN (verify Render account still works) ---
    {
        "name": "10. Render login (existing account)",
        "category": "login",
        "url": "https://dashboard.render.com/login",
        "run": lambda a: a.login(
            "https://dashboard.render.com/login",
            email="m5aibot+test5182@proton.me",
            password=PASSWORD,
        ),
    },
]


# ── Runner ───────────────────────────────────────────────────────────

def run_one(test_def):
    name = test_def["name"]
    category = test_def["category"]

    log.info("=" * 70)
    log.info("TEST: %s [%s]", name, category)
    log.info("URL: %s", test_def["url"])
    log.info("=" * 70)

    result = {
        "name": name, "category": category, "url": test_def["url"],
        "success": False, "duration": 0, "error": "", "data": "",
        "steps": 0, "escalations": 0, "verification": None,
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
            if isinstance(raw.data, dict):
                result["verification"] = raw.data.get("verification_needed")
                if raw.data.get("verification_completed"):
                    result["verification"] = f"{result['verification']}:completed"
        elif isinstance(raw, list):
            result["success"] = len(raw) > 0
            result["data"] = str(raw)[:500]
        elif isinstance(raw, str):
            result["success"] = len(raw) > 10
            result["data"] = raw[:500]
        elif isinstance(raw, dict):
            result["success"] = raw.get("success", False)
            result["data"] = str(raw)[:500]
            result["verification"] = raw.get("verification_needed")

        log.info("RESULT: success=%s, steps=%s, escalations=%s, verification=%s, duration=%.1fs",
                 result["success"], result["steps"], result["escalations"],
                 result["verification"], duration)
        if result["data"]:
            log.info("DATA: %s", str(result["data"])[:200])
        if result["error"]:
            log.warning("ERROR: %s", result["error"])

    except Exception as e:
        result["duration"] = round(time.time() - start, 1)
        result["error"] = str(e)
        log.error("CRASHED: %s (%.1fs)", e, result["duration"])
        log.error(traceback.format_exc())

    kill_browsers()
    return result


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.dry_run:
        print(f"\n{len(TESTS)} tests:")
        for t in TESTS:
            print(f"  [{t['category']:15s}] {t['name']} — {t['url']}")
        return

    validate()

    results = []
    for i, t in enumerate(TESTS, 1):
        log.info("[%d/%d] %s", i, len(TESTS), t["name"])
        result = run_one(t)
        results.append(result)

        with open(results_file, "w") as f:
            json.dump(results, f, indent=2)

        if i < len(TESTS):
            pause = 10 + random.randint(0, 10)
            log.info("Pausing %ds ...", pause)
            time.sleep(pause)

    # Summary
    total = len(results)
    passed = sum(1 for r in results if r["success"])
    verified = sum(1 for r in results if (r.get("verification") or "").endswith(":completed"))
    escalated = sum(r["escalations"] for r in results)
    total_time = sum(r["duration"] for r in results)

    log.info("=" * 70)
    log.info("DONE: %d/%d passed, %d verified, %d escalations, %.0fs",
             passed, total, verified, escalated, total_time)
    log.info("=" * 70)

    for r in results:
        status = "PASS" if r["success"] else "FAIL"
        v = f" [verified:{r['verification']}]" if r.get("verification") else ""
        esc = f" [esc={r['escalations']}]" if r["escalations"] else ""
        err = f" — {r['error'][:60]}" if r["error"] else ""
        log.info("  [%s] %s (%.1fs)%s%s%s", status, r["name"], r["duration"], v, esc, err)

    log.info("Results: %s", results_file)
    log.info("Log: %s", log_file)


if __name__ == "__main__":
    main()
