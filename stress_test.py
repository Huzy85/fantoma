#!/usr/bin/env python3
"""Fantoma Stress Test — continuous site testing over hours.

Tests bot detection by repeatedly hitting protected sites at realistic intervals.
Logs every attempt with timing, success/failure, blocking, and CAPTCHA status.

Usage:
    python3 stress_test.py                          # Default: local LLM, 8 hours
    python3 stress_test.py --hours 4                # Run for 4 hours
    python3 stress_test.py --interval 20            # 20 minutes between rounds
    python3 stress_test.py --llm-url https://api.anthropic.com/v1 --api-key sk-...
"""

import argparse
import json
import logging
import random
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from fantoma import Agent

log = logging.getLogger("stress_test")

# Sites to test — the hardest anti-bot protections on the internet
SITES = [
    # --- CLOUDFLARE PROTECTED ---
    {
        "name": "X.com",
        "protection": "Cloudflare",
        "tasks": [
            "Go to https://x.com/explore and tell me what trending topics are shown",
            "Go to https://x.com and tell me what the page says",
        ],
    },
    {
        "name": "Indeed",
        "protection": "Cloudflare",
        "tasks": [
            "Go to https://uk.indeed.com and tell me what job categories are shown",
            "Go to https://uk.indeed.com, type 'data analyst' in the job title box, press Enter, tell me the first job",
        ],
    },
    {
        "name": "Etsy",
        "protection": "Cloudflare",
        "tasks": [
            "Go to https://www.etsy.com and tell me what categories are featured",
            "Go to https://www.etsy.com, type 'handmade candles' in the search box, press Enter, tell me the first product",
        ],
    },
    {
        "name": "Reddit",
        "protection": "Cloudflare + custom",
        "tasks": [
            "Go to https://www.reddit.com and tell me what the top post on the front page is",
            "Go to https://old.reddit.com and tell me the top post title",
        ],
    },
    # --- DATADOME PROTECTED ---
    {
        "name": "Amazon UK",
        "protection": "DataDome",
        "tasks": [
            "Go to https://www.amazon.co.uk and tell me what the top deal is",
            "Go to https://www.amazon.co.uk, type 'headphones' in the search box, click Go, tell me the first product",
        ],
    },
    # --- PERIMETERX PROTECTED ---
    {
        "name": "Zillow",
        "protection": "PerimeterX",
        "tasks": [
            "Go to https://www.zillow.com and tell me what the page says",
        ],
    },
    {
        "name": "Walmart",
        "protection": "PerimeterX",
        "tasks": [
            "Go to https://www.walmart.com and tell me what deals are featured",
        ],
    },
    # --- AKAMAI PROTECTED ---
    {
        "name": "Nike",
        "protection": "Akamai",
        "tasks": [
            "Go to https://www.nike.com/gb/ and tell me what products are featured",
            "Go to https://www.nike.com/gb/w/mens-shoes-nik1zy7ok and tell me the first shoe name",
        ],
    },
    # --- META ANTI-BOT ---
    {
        "name": "Instagram",
        "protection": "Meta anti-bot",
        "tasks": [
            "Go to https://www.instagram.com and tell me what the page says",
        ],
    },
    {
        "name": "Facebook",
        "protection": "Meta anti-bot",
        "tasks": [
            "Go to https://www.facebook.com and tell me what the page says",
        ],
    },
    # --- CUSTOM ANTI-BOT ---
    {
        "name": "LinkedIn",
        "protection": "Custom anti-bot",
        "tasks": [
            "Go to https://www.linkedin.com and tell me what the page says",
        ],
    },
    {
        "name": "Booking.com",
        "protection": "Custom anti-scrape",
        "tasks": [
            "Go to https://www.booking.com and tell me what destinations are shown",
        ],
    },
    {
        "name": "Ticketmaster",
        "protection": "Queue/bot gate",
        "tasks": [
            "Go to https://www.ticketmaster.co.uk and tell me what events are featured",
        ],
    },
    {
        "name": "TikTok",
        "protection": "Custom anti-bot",
        "tasks": [
            "Go to https://www.tiktok.com and tell me what the page says",
        ],
    },
    {
        "name": "Craigslist",
        "protection": "Very aggressive anti-bot",
        "tasks": [
            "Go to https://london.craigslist.org and tell me what categories are shown",
        ],
    },
    {
        "name": "Rightmove",
        "protection": "Anti-bot + OneTrust",
        "tasks": [
            "Go to https://www.rightmove.co.uk and tell me what search options are available",
        ],
    },
    {
        "name": "GitHub",
        "protection": "Rate limiting",
        "tasks": [
            "Go to https://github.com/trending and tell me the top trending repository",
        ],
    },
    {
        "name": "StubHub",
        "protection": "Custom anti-bot",
        "tasks": [
            "Go to https://www.stubhub.co.uk and tell me what events are featured",
        ],
    },
    # --- FINGERPRINT TEST ---
    {
        "name": "nowsecure.nl",
        "protection": "Cloudflare fingerprint test",
        "tasks": [
            "Go to https://nowsecure.nl and tell me what the page says",
        ],
    },
    {
        "name": "bot.sannysoft",
        "protection": "Browser fingerprint test",
        "tasks": [
            "Go to https://bot.sannysoft.com and tell me if any tests show failed",
        ],
    },
]

LOG_FILE = Path(__file__).parent / "stress_test_results.json"


def load_results() -> list:
    if LOG_FILE.exists():
        try:
            return json.loads(LOG_FILE.read_text())
        except Exception:
            return []
    return []


def save_results(results: list):
    LOG_FILE.write_text(json.dumps(results, indent=2, ensure_ascii=False))


def detect_blocking(data: str) -> dict:
    """Check if the response indicates blocking/detection."""
    data_lower = (data or "").lower()
    blocked = False
    captcha = False
    reason = ""

    blocking_indicators = [
        "access denied", "blocked", "forbidden", "please verify",
        "security check", "unusual activity", "too many requests",
        "rate limit", "bot detected", "automated", "suspicious",
    ]
    captcha_indicators = [
        "captcha", "recaptcha", "hcaptcha", "verify you are human",
        "prove you are not a robot", "challenge",
    ]

    for indicator in blocking_indicators:
        if indicator in data_lower:
            blocked = True
            reason = indicator
            break

    for indicator in captcha_indicators:
        if indicator in data_lower:
            captcha = True
            reason = indicator
            break

    return {"blocked": blocked, "captcha": captcha, "reason": reason}


def run_round(agent: Agent, round_num: int, results: list) -> dict:
    """Run one round of testing across all sites."""
    round_results = {
        "round": round_num,
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "sites": [],
    }

    # Randomise site order each round (more realistic)
    sites = list(SITES)
    random.shuffle(sites)

    for site in sites:
        # Pick a random task for this site
        task = random.choice(site["tasks"])

        log.info("Round %d | %s | %s", round_num, site["name"], task[:60])

        start = time.time()
        try:
            result = agent.run(task)
            elapsed = time.time() - start
            data = (result.data or "")[:200]
            detection = detect_blocking(data)

            # Also check step details for blocking signals
            steps_log = result.steps_detail or []
            final_url = steps_log[-1].get("url", "") if steps_log else ""

            entry = {
                "round": round_num,
                "site": site["name"],
                "protection": site["protection"],
                "task": task[:100],
                "success": result.success,
                "steps": result.steps_taken,
                "time_seconds": round(elapsed, 1),
                "data_preview": data[:200],
                "final_url": final_url,
                "blocked": detection["blocked"],
                "captcha": detection["captcha"],
                "detection_reason": detection["reason"],
                "escalations": result.escalations,
                "timestamp": datetime.now().isoformat(timespec="seconds"),
            }

            status = "BLOCKED" if detection["blocked"] else "CAPTCHA" if detection["captcha"] else "PASS" if result.success else "FAIL"
            log.info("  %s in %.0fs | url=%s | %s", status, elapsed, final_url[:50], data[:50])

        except Exception as e:
            elapsed = time.time() - start
            entry = {
                "round": round_num,
                "site": site["name"],
                "protection": site["protection"],
                "task": task[:100],
                "success": False,
                "steps": 0,
                "time_seconds": round(elapsed, 1),
                "data_preview": "",
                "final_url": "",
                "blocked": False,
                "captcha": False,
                "detection_reason": "",
                "escalations": 0,
                "error": str(e)[:200],
                "timestamp": datetime.now().isoformat(timespec="seconds"),
            }
            log.error("  CRASH in %.0fs | %s", elapsed, str(e)[:60])

        round_results["sites"].append(entry)
        results.append(entry)
        save_results(results)

        # Random pause between sites (15-45 seconds — realistic browsing)
        pause = random.uniform(15, 45)
        log.info("  Waiting %.0fs before next site...", pause)
        time.sleep(pause)

    round_results["finished_at"] = datetime.now().isoformat(timespec="seconds")

    # Round summary
    passed = sum(1 for s in round_results["sites"] if s["success"] and not s["blocked"])
    blocked = sum(1 for s in round_results["sites"] if s["blocked"])
    captchas = sum(1 for s in round_results["sites"] if s["captcha"])
    total = len(round_results["sites"])
    log.info("Round %d complete: %d/%d pass, %d blocked, %d captcha", round_num, passed, total, blocked, captchas)

    return round_results


def print_summary(results: list):
    """Print overall summary of all results."""
    print("\n" + "=" * 70)
    print("STRESS TEST SUMMARY")
    print("=" * 70)

    # Group by site
    by_site = {}
    for r in results:
        site = r["site"]
        if site not in by_site:
            by_site[site] = {"total": 0, "pass": 0, "blocked": 0, "captcha": 0, "fail": 0, "times": []}
        by_site[site]["total"] += 1
        if r.get("blocked"):
            by_site[site]["blocked"] += 1
        elif r.get("captcha"):
            by_site[site]["captcha"] += 1
        elif r.get("success"):
            by_site[site]["pass"] += 1
        else:
            by_site[site]["fail"] += 1
        by_site[site]["times"].append(r.get("time_seconds", 0))

    print(f"\n{'Site':<20} {'Total':>6} {'Pass':>6} {'Blocked':>8} {'CAPTCHA':>8} {'Fail':>6} {'Avg Time':>9}")
    print("-" * 70)
    for site, stats in sorted(by_site.items()):
        avg_time = sum(stats["times"]) / len(stats["times"]) if stats["times"] else 0
        print(f"{site:<20} {stats['total']:>6} {stats['pass']:>6} {stats['blocked']:>8} {stats['captcha']:>8} {stats['fail']:>6} {avg_time:>8.1f}s")

    total = len(results)
    total_blocked = sum(1 for r in results if r.get("blocked"))
    total_captcha = sum(1 for r in results if r.get("captcha"))
    total_pass = sum(1 for r in results if r.get("success") and not r.get("blocked"))
    print("-" * 70)
    print(f"{'TOTAL':<20} {total:>6} {total_pass:>6} {total_blocked:>8} {total_captcha:>8} {total - total_pass - total_blocked - total_captcha:>6}")
    print(f"\nDetection rate: {(total_blocked + total_captcha) / total * 100:.1f}%" if total > 0 else "")


def main():
    parser = argparse.ArgumentParser(description="Fantoma stress test")
    parser.add_argument("--hours", type=float, default=8, help="Duration in hours")
    parser.add_argument("--interval", type=int, default=15, help="Minutes between rounds")
    parser.add_argument("--llm-url", default="http://localhost:8080/v1", help="LLM endpoint")
    parser.add_argument("--api-key", default="", help="API key for cloud LLMs")
    parser.add_argument("--model", default="auto", help="Model name")
    parser.add_argument("--resume", action="store_true", help="Resume from previous results")
    parser.add_argument("--tag", default="default", help="Tag for output files (e.g. claude, kimi)")
    args = parser.parse_args()

    # Use tag for separate output files per test instance
    global LOG_FILE
    LOG_FILE = Path(__file__).parent / f"stress_test_results_{args.tag}.json"

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(message)s",
        datefmt="%H:%M:%S",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(Path(__file__).parent / f"stress_test_{args.tag}.log"),
        ],
    )

    agent = Agent(
        llm_url=args.llm_url,
        api_key=args.api_key,
        model=args.model,
        verbose=False,
    )

    results = load_results() if args.resume else []
    end_time = datetime.now() + timedelta(hours=args.hours)
    round_num = len(set(r.get("round", 0) for r in results)) + 1 if results else 1

    log.info("Fantoma Stress Test starting")
    log.info("Duration: %.1f hours | Interval: %d min | LLM: %s", args.hours, args.interval, args.llm_url)
    log.info("End time: %s", end_time.strftime("%H:%M:%S"))
    log.info("Sites: %d | Tasks per round: %d", len(SITES), len(SITES))

    try:
        while datetime.now() < end_time:
            log.info("\n--- Round %d (%.1f hours remaining) ---", round_num, (end_time - datetime.now()).total_seconds() / 3600)
            run_round(agent, round_num, results)
            round_num += 1

            remaining = (end_time - datetime.now()).total_seconds()
            if remaining <= 0:
                break

            wait = min(args.interval * 60, remaining)
            log.info("Waiting %.0f minutes before next round...", wait / 60)
            time.sleep(wait)

    except KeyboardInterrupt:
        log.info("\nStopped by user")
    finally:
        save_results(results)
        print_summary(results)


if __name__ == "__main__":
    main()
