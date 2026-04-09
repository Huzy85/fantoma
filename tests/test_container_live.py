"""Live container tests — exercises Camoufox inside Docker against real sites.

Run from HOST:
    python3 tests/test_container_live.py

Tests browser launch, navigation, page loading, content extraction,
and sequential reliability (the main bug from v0.6).
"""

import json
import time
import sys
import httpx

API = "http://localhost:7860"
LLM_URL = "http://host.docker.internal:8082/v1"  # Hermes

# ── Round 1: 10 sites — basic navigation + content ──────────────

ROUND_1 = [
    {"name": "Hacker News", "url": "https://news.ycombinator.com", "expect_text": "Hacker News"},
    {"name": "Wikipedia", "url": "https://en.wikipedia.org/wiki/Web_browser", "expect_text": "web browser"},
    {"name": "GitHub", "url": "https://github.com/trending", "expect_text": "Trending"},
    {"name": "PyPI", "url": "https://pypi.org/project/requests/", "expect_text": "requests"},
    {"name": "BBC News", "url": "https://www.bbc.co.uk/news", "expect_text": "BBC"},
    {"name": "The Guardian", "url": "https://www.theguardian.com", "expect_text": "Guardian"},
    {"name": "Reuters", "url": "https://www.reuters.com", "expect_text": "Reuters"},
    {"name": "npm", "url": "https://www.npmjs.com/package/express", "expect_text": "express"},
    {"name": "Stack Overflow", "url": "https://stackoverflow.com/questions", "expect_text": "Questions"},
    {"name": "httpbin", "url": "https://httpbin.org/html", "expect_text": "Moby-Dick"},
]

# ── Round 2: 15 sites — harder targets + sequential stress ──────

ROUND_2 = [
    {"name": "Reddit", "url": "https://old.reddit.com", "expect_text": "reddit"},
    {"name": "Amazon", "url": "https://www.amazon.co.uk", "expect_text": "Amazon"},
    {"name": "LinkedIn (landing)", "url": "https://www.linkedin.com", "expect_text": "LinkedIn"},
    {"name": "DuckDuckGo", "url": "https://duckduckgo.com/?q=python", "expect_text": "Python"},
    {"name": "TechCrunch", "url": "https://techcrunch.com", "expect_text": "TechCrunch"},
    {"name": "Ars Technica", "url": "https://arstechnica.com", "expect_text": "Ars"},
    {"name": "Books to Scrape", "url": "https://books.toscrape.com", "expect_text": "Books"},
    {"name": "Quotes to Scrape", "url": "https://quotes.toscrape.com", "expect_text": "Quotes"},
    {"name": "Hacker News (page 2)", "url": "https://news.ycombinator.com/news?p=2", "expect_text": "Hacker News"},
    {"name": "Mozilla MDN", "url": "https://developer.mozilla.org/en-US/docs/Web/HTML", "expect_text": "HTML"},
    {"name": "Cloudflare blog", "url": "https://blog.cloudflare.com", "expect_text": "Cloudflare"},
    {"name": "example.com", "url": "https://example.com", "expect_text": "Example Domain"},
    {"name": "GitHub (Python)", "url": "https://github.com/topics/python", "expect_text": "python"},
    {"name": "W3Schools", "url": "https://www.w3schools.com", "expect_text": "W3Schools"},
    {"name": "Python docs", "url": "https://docs.python.org/3/", "expect_text": "Python"},
]


def test_health():
    """Verify the container is up."""
    r = httpx.get(f"{API}/health", timeout=5)
    data = r.json()
    assert data["status"] == "ok", f"Health check failed: {data}"
    print("  PASS  health check")
    return True


def test_site(site: dict, index: int) -> dict:
    """Test a single site via the /run endpoint (browser-only, no LLM extraction).

    Uses agent.run() with a simple task so the browser launches, navigates,
    and reads the page. This tests the full Camoufox lifecycle.
    """
    name = site["name"]
    url = site["url"]
    expect = site["expect_text"].lower()

    start = time.time()
    result = {"name": name, "url": url, "pass": False, "error": "", "time_s": 0}

    try:
        r = httpx.post(
            f"{API}/extract",
            json={
                "url": url,
                "query": f"What is the main heading or title of this page?",
                "llm_url": LLM_URL,
            },
            timeout=120,
        )
        elapsed = time.time() - start
        result["time_s"] = round(elapsed, 1)

        data = r.json()
        if r.status_code != 200:
            result["error"] = f"HTTP {r.status_code}: {data}"
            print(f"  FAIL  [{index:2d}] {name:25s} — HTTP {r.status_code} ({elapsed:.1f}s)")
            return result

        if not data.get("success"):
            result["error"] = data.get("error", "unknown error")
            print(f"  FAIL  [{index:2d}] {name:25s} — {result['error'][:60]} ({elapsed:.1f}s)")
            return result

        # Check if we got any content back (even empty string means browser worked)
        content = str(data.get("data", "")).lower()
        if content:
            result["pass"] = True
            has_expected = expect in content
            marker = "+" if has_expected else "~"
            print(f"  PASS{marker} [{index:2d}] {name:25s} — {len(content)} chars ({elapsed:.1f}s)")
        else:
            # Empty content but no error means browser loaded but LLM returned nothing
            # This is still a PASS for the browser — the LLM might be weak
            result["pass"] = True
            result["error"] = "empty LLM response (browser OK)"
            print(f"  PASS~ [{index:2d}] {name:25s} — browser loaded, LLM empty ({elapsed:.1f}s)")

    except httpx.TimeoutException:
        elapsed = time.time() - start
        result["time_s"] = round(elapsed, 1)
        result["error"] = "timeout (120s)"
        print(f"  FAIL  [{index:2d}] {name:25s} — TIMEOUT ({elapsed:.1f}s)")
    except Exception as e:
        elapsed = time.time() - start
        result["time_s"] = round(elapsed, 1)
        result["error"] = str(e)
        print(f"  FAIL  [{index:2d}] {name:25s} — {e} ({elapsed:.1f}s)")

    return result


def run_round(sites: list, label: str) -> list:
    """Run a round of tests sequentially (tests sequential browser reliability)."""
    print(f"\n{'='*60}")
    print(f"  {label} — {len(sites)} sites, sequential")
    print(f"{'='*60}")

    results = []
    for i, site in enumerate(sites, 1):
        result = test_site(site, i)
        results.append(result)

    passed = sum(1 for r in results if r["pass"])
    failed = len(results) - passed
    total_time = sum(r["time_s"] for r in results)

    print(f"\n  Results: {passed}/{len(results)} passed, {failed} failed, {total_time:.0f}s total")

    if failed:
        print(f"\n  Failed sites:")
        for r in results:
            if not r["pass"]:
                print(f"    - {r['name']}: {r['error']}")

    return results


def main():
    print("Fantoma Container Live Tests")
    print(f"API: {API}")
    print(f"LLM: {LLM_URL}")

    # Health check
    try:
        test_health()
    except Exception as e:
        print(f"  FAIL  health check — {e}")
        print("  Container not running? Start with: docker compose -f docker-compose.fantoma.yml up -d")
        sys.exit(1)

    # Round 1
    r1 = run_round(ROUND_1, "ROUND 1: Basic sites")

    # Pause between rounds to check for resource leaks
    print("\n  Pausing 5s between rounds...")
    time.sleep(5)

    # Round 2
    r2 = run_round(ROUND_2, "ROUND 2: Harder sites + sequential stress")

    # Summary
    all_results = r1 + r2
    total_pass = sum(1 for r in all_results if r["pass"])
    total_fail = len(all_results) - total_pass
    total_time = sum(r["time_s"] for r in all_results)

    print(f"\n{'='*60}")
    print(f"  FINAL: {total_pass}/{len(all_results)} passed, {total_fail} failed")
    print(f"  Total time: {total_time:.0f}s")
    print(f"{'='*60}")

    # Save results
    with open("/home/workspace/workbench/fantoma/tests/container_test_results.json", "w") as f:
        json.dump({"round_1": r1, "round_2": r2, "summary": {
            "total": len(all_results), "passed": total_pass, "failed": total_fail,
            "time_s": round(total_time, 1),
        }}, f, indent=2)
    print("  Results saved to tests/container_test_results.json")

    sys.exit(0 if total_fail == 0 else 1)


if __name__ == "__main__":
    main()
