#!/usr/bin/env python3
"""Live API test suite — hits browser-host Fantoma HTTP endpoints with real sites.

Tests the deployed containers exactly as callers (openclaw, etc.) would use them.
No mocks. No local browser. Just POST /run and check the result.

Usage:
    python3 tools/live_api_test.py
    python3 tools/live_api_test.py --host 127.0.0.1
"""
import argparse
import json
import subprocess
import sys
import time
import urllib.request
import urllib.error

CONTAINER_FOR_PORT = {
    7860: "fantoma-browser",
    7861: "fantoma-browser-2",
    7862: "fantoma-browser-3",
}


def restart_container(host: str, port: int) -> None:
    """Restart the fantoma process inside the container for this port.

    Called after a timeout so Flask can accept new connections again.
    Uses ssh if the host is remote, direct docker exec if local.
    """
    container = CONTAINER_FOR_PORT.get(port)
    if not container:
        return
    try:
        if host in ("127.0.0.1", "localhost"):
            subprocess.run(
                ["docker", "exec", container, "supervisorctl", "restart", "fantoma"],
                capture_output=True, timeout=15,
            )
        else:
            subprocess.run(
                ["ssh", host, f"docker exec {container} supervisorctl restart fantoma"],
                capture_output=True, timeout=15,
            )
        time.sleep(2)
    except Exception as e:
        print(f"         (restart failed: {e})")


TESTS = [
    {
        "name": "example.com heading (minimal page)",
        "url": "https://example.com",
        "task": "What is the main heading on this page?",
        "expect": "Example Domain",
    },
    {
        "name": "PyPI version lookup (read/extract)",
        "url": "https://pypi.org/project/requests/",
        "task": "What is the latest version of the requests package on PyPI?",
        "expect": None,
    },
    {
        "name": "MDN button element (technical docs)",
        "url": "https://developer.mozilla.org/en-US/docs/Web/HTML/Element/button",
        "task": "What HTML element does this MDN page document?",
        "expect": "button",
    },
    {
        "name": "httpbin JSON (machine-readable page)",
        "url": "https://httpbin.org/json",
        "task": "What is the title field in the slideshow on this page?",
        "expect": None,
    },
    {
        "name": "IANA homepage (ultra-stable minimal page)",
        "url": "https://www.iana.org/",
        "task": "What is the main heading or title on this page?",
        "expect": "Internet Assigned Numbers Authority",
    },
    {
        "name": "ifconfig.me plain text (trivial extract)",
        "url": "https://ifconfig.me/ip",
        "task": "What IP address is shown on this page?",
        "expect": None,
    },
]


def run_test(host: str, port: int, test: dict, timeout: int = 90) -> dict:
    payload = json.dumps({
        "task": test["task"],
        "url": test["url"],
        "timeout": timeout,
    }).encode()

    req = urllib.request.Request(
        f"http://{host}:{port}/run",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout + 10) as resp:
            result = json.loads(resp.read())
    except Exception as e:
        return {"success": False, "error": str(e), "data": "", "steps_taken": 0, "secs": round(time.time() - t0, 1)}

    result["secs"] = round(time.time() - t0, 1)
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--ports", default="7860,7861,7862")
    parser.add_argument("--timeout", type=int, default=120)
    args = parser.parse_args()

    ports = [int(p) for p in args.ports.split(",")]
    print(f"Live API test — http://{args.host} ports {ports}\n")

    passed = 0
    failed = 0
    results = []

    for i, test in enumerate(TESTS, 1):
        port = ports[(i - 1) % len(ports)]  # round-robin across instances
        print(f"[{i}/{len(TESTS)}] {test['name']} (:{port}) ... ", end="", flush=True)
        r = run_test(args.host, port, test, args.timeout)

        ok = r.get("success", False)
        data = r.get("data", "") or ""

        # Check expected substring if specified
        expect_ok = True
        if ok and test.get("expect"):
            expect_ok = test["expect"].lower() in data.lower()
            if not expect_ok:
                ok = False

        status = "PASS" if ok else "FAIL"
        steps = r.get("steps_taken", "?")
        secs = r.get("secs", "?")
        print(f"{status} ({steps} steps, {secs}s)")

        if not ok:
            print(f"         error: {r.get('error', '')}")
            if data:
                print(f"         data:  {data[:120]}")
            if test.get("expect") and not expect_ok:
                print(f"         expected '{test['expect']}' in answer")
            # Restart the container if it timed out — Flask single-threaded,
            # slow local-llm call blocks the server even after client disconnect.
            if "timed out" in r.get("error", ""):
                print(f"         (restarting container on :{port} to unblock Flask)")
                restart_container(args.host, port)
            failed += 1
        else:
            print(f"         {data[:100]}")
            passed += 1

        results.append({"test": test["name"], "passed": ok, **r})

    print(f"\n{'='*50}")
    print(f"Results: {passed}/{len(TESTS)} passed, {failed} failed")

    # Write JSON log
    log_path = f"/tmp/fantoma-live-{int(time.time())}.json"
    with open(log_path, "w") as f:
        json.dump(results, f, indent=2)
    print(f"Log: {log_path}")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
