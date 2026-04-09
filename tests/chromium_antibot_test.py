"""Chromium (Patchright) anti-bot stress test — 10 sites.

Tests real interactions (signup/login) against sites using Cloudflare,
PerimeterX, DataDome, Akamai, and custom bot detection.

Usage: python tests/chromium_antibot_test.py
"""

import json
import logging
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from fantoma.agent import Agent

logging.basicConfig(level=logging.WARNING, format="%(asctime)s %(name)s: %(message)s")

EMAIL = "m5aibot@proton.me"
IMAP = {"host": "127.0.0.1", "port": 1143, "user": EMAIL, "password": os.environ.get("IMAP_PASS", "Mandruloc154"), "security": "none"}
HERCULES = "http://localhost:8080/v1"
MAX_TIME = 150

try:
    CAPSOLVER_KEY = json.load(open(Path.home() / ".config/capsolver/config.json"))["api_key"]
except (FileNotFoundError, KeyError):
    CAPSOLVER_KEY = os.environ.get("CAPSOLVER_KEY", "")

TS = str(int(time.time()) % 100000)


def make_agent(timeout=120):
    return Agent(
        llm_url=HERCULES,
        headless=True,
        timeout=timeout,
        captcha_api="capsolver",
        captcha_key=CAPSOLVER_KEY,
        email_imap=IMAP,
        browser="chromium",
    )


TESTS = [
    {
        "name": "1. bot.sannysoft.com",
        "anti_bot": "explicit detection test",
        "url": "https://bot.sannysoft.com",
        "method": "run",
        "task": (
            "Navigate to https://bot.sannysoft.com. "
            "Wait for the page to fully load and all tests to complete. "
            "Read every table row on the page and report whether each test passed or failed. "
            "A passed test means the browser looks human. A failed test means it was detected as a bot."
        ),
    },
    {
        "name": "2. nowsecure.nl",
        "anti_bot": "headless detection",
        "url": "https://nowsecure.nl",
        "method": "run",
        "task": (
            "Navigate to https://nowsecure.nl. "
            "Wait for the page to fully load. "
            "Read the detection results shown on the page and report whether the browser passed as human or was flagged."
        ),
    },
    {
        "name": "3. HuggingFace signup",
        "anti_bot": "Cloudflare",
        "url": "https://huggingface.co/join",
        "method": "login",
        "params": {"email": EMAIL, "password": "Fantoma2026Test!"},
    },
    {
        "name": "4. GitLab signup",
        "anti_bot": "Cloudflare + reCAPTCHA",
        "url": "https://gitlab.com/users/sign_up",
        "method": "login",
        "params": {
            "first_name": "Fantoma",
            "last_name": "Agent",
            "username": f"fantoma_agent_{TS}",
            "email": EMAIL,
            "password": "Fantoma2026Test!",
        },
    },
    {
        "name": "5. Indeed job search",
        "anti_bot": "custom bot detection",
        "url": "https://www.indeed.com/jobs?q=python+developer&l=London",
        "method": "run",
        "task": (
            "Navigate to https://www.indeed.com/jobs?q=python+developer&l=London. "
            "Wait for job listings to load. "
            "Click on the first job listing. "
            "Read the job title, company name, and salary if shown. "
            "Then click the Apply button if present. "
            "Report what happened at each step — specifically note if any block page, CAPTCHA, or access denied message appeared."
        ),
    },
    {
        "name": "6. Glassdoor job search",
        "anti_bot": "DataDome",
        "url": "https://www.glassdoor.com/Job/index.htm",
        "method": "run",
        "task": (
            "Navigate to https://www.glassdoor.com/Job/index.htm. "
            "Search for 'python developer' jobs in 'London'. "
            "Click on the first result. "
            "Read the job title and salary information. "
            "Report what happened — specifically note any block page, CAPTCHA, or access denied."
        ),
    },
    {
        "name": "7. G2 product browse",
        "anti_bot": "DataDome",
        "url": "https://www.g2.com/categories/crm",
        "method": "run",
        "task": (
            "Navigate to https://www.g2.com/categories/crm. "
            "Wait for the product listings to load. "
            "Click on the first product in the list. "
            "Read the product name, star rating, and number of reviews. "
            "Then click on one review to expand it. "
            "Report what happened — specifically note any block page, CAPTCHA, or access denied."
        ),
    },
    {
        "name": "8. Zillow property search",
        "anti_bot": "Akamai Bot Manager",
        "url": "https://www.zillow.com",
        "method": "run",
        "task": (
            "Navigate to https://www.zillow.com. "
            "Search for properties in 'Austin, TX'. "
            "Click on the first property listing. "
            "Read the price, number of bedrooms, and address. "
            "Report what happened — specifically note any block page, CAPTCHA, or access denied."
        ),
    },
    {
        "name": "9. Ticketmaster event search",
        "anti_bot": "Imperva + custom",
        "url": "https://www.ticketmaster.co.uk",
        "method": "run",
        "task": (
            "Navigate to https://www.ticketmaster.co.uk. "
            "Search for 'concert' events. "
            "Click on the first event listed. "
            "Read the event name, date, and venue. "
            "Then click to view tickets and read the available ticket options. "
            "Report what happened — specifically note any block page, CAPTCHA, or access denied."
        ),
    },
    {
        "name": "10. Amazon product search",
        "anti_bot": "PerimeterX",
        "url": "https://www.amazon.co.uk",
        "method": "run",
        "task": (
            "Navigate to https://www.amazon.co.uk. "
            "Search for 'wireless headphones'. "
            "Click on the first product in the results. "
            "Read the product title, price, and star rating. "
            "Click 'Add to Basket'. "
            "Report what happened at each step — specifically note any block page, CAPTCHA, robot check, or access denied."
        ),
    },
]


def run_test(test):
    name = test["name"]
    method = test["method"]
    anti_bot = test["anti_bot"]

    agent = make_agent()
    start = time.time()

    try:
        if method == "run":
            result = agent.run(test["task"], start_url=test["url"])
            elapsed = time.time() - start
            return {
                "name": name,
                "anti_bot": anti_bot,
                "elapsed": round(elapsed, 1),
                "success": result.success,
                "steps": result.steps_taken,
                "output": str(result.data or "")[:300],
                "error": result.error or "",
                "slow": elapsed > MAX_TIME,
            }
        else:
            result = agent.login(test["url"], **test["params"])
            elapsed = time.time() - start
            data = result.data or {}
            fields = data.get("fields_filled", []) if isinstance(data, dict) else []
            return {
                "name": name,
                "anti_bot": anti_bot,
                "elapsed": round(elapsed, 1),
                "success": result.success,
                "steps": result.steps_taken,
                "fields": fields,
                "output": "",
                "error": result.error or "",
                "slow": elapsed > MAX_TIME,
            }
    except Exception as e:
        elapsed = time.time() - start
        return {
            "name": name,
            "anti_bot": anti_bot,
            "elapsed": round(elapsed, 1),
            "success": False,
            "steps": 0,
            "fields": [],
            "output": "",
            "error": str(e)[:200],
            "slow": elapsed > MAX_TIME,
        }


def main():
    print(f"\nFantoma — Chromium (Patchright) Anti-Bot Stress Test")
    print(f"Browser: Patchright/Chromium  LLM: Hercules  CAPTCHA: CapSolver")
    print(f"Email: {EMAIL}  Threshold: >{MAX_TIME}s = slow")
    print(f"{'=' * 70}\n")

    results = []
    for test in TESTS:
        print(f"Running: {test['name']} [{test['anti_bot']}]...")
        r = run_test(test)
        results.append(r)

        icon = "PASS" if r["success"] else ("SLOW" if r["slow"] else "FAIL")
        fields_str = f"  fields: {r.get('fields')}" if r.get("fields") else ""
        print(f"  {icon}  {r['elapsed']}s  {r.get('output') or r.get('error') or ''[:150]}")
        if fields_str:
            print(fields_str)
        print()

    print(f"{'=' * 70}")
    passed = sum(1 for r in results if r["success"])
    failed = sum(1 for r in results if not r["success"])
    slow = sum(1 for r in results if r["slow"])
    total_time = sum(r["elapsed"] for r in results)
    print(f"PASS: {passed}  FAIL: {failed}  SLOW: {slow}  TOTAL: {len(results)}")
    print(f"Total time: {total_time:.0f}s ({total_time/60:.1f} min)\n")

    print("RESULTS:")
    for r in results:
        status = "PASS" if r["success"] else "FAIL"
        flags = []
        if r["slow"]:
            flags.append(f"slow ({r['elapsed']}s)")
        if r["error"]:
            flags.append(r["error"][:100])
        flag_str = "  — " + "; ".join(flags) if flags else ""
        print(f"  [{status}] {r['name']} ({r['anti_bot']}){flag_str}")

    ts = int(time.time())
    out_path = Path(__file__).parent / f"chromium_antibot_{ts}.json"
    with open(out_path, "w") as f:
        json.dump(results, f, indent=2, default=str)
    print(f"\nResults saved to {out_path}")


if __name__ == "__main__":
    main()
