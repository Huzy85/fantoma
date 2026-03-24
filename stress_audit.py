#!/usr/bin/env python3
"""Stress test audit — analyses results from stress_test.py and produces a detailed report."""
import json
import sys
from collections import defaultdict
from pathlib import Path


def load_results(tag: str) -> list:
    f = Path(__file__).parent / f"stress_test_results_{tag}.json"
    if not f.exists():
        print(f"No results file: {f}")
        return []
    return json.loads(f.read_text())


def audit(tag: str):
    results = load_results(tag)
    if not results:
        return

    print(f"\n{'=' * 80}")
    print(f"FANTOMA STRESS TEST AUDIT — {tag.upper()}")
    print(f"{'=' * 80}")
    print(f"Total tests: {len(results)}")
    print(f"Time range: {results[0].get('timestamp', '?')} → {results[-1].get('timestamp', '?')}")
    rounds = set(r.get("round", 0) for r in results)
    print(f"Rounds completed: {len(rounds)}")

    # --- Overall stats ---
    total = len(results)
    passed = sum(1 for r in results if r["success"] and not r["blocked"] and not r["captcha"])
    blocked = sum(1 for r in results if r["blocked"])
    captcha = sum(1 for r in results if r["captcha"])
    crashed = sum(1 for r in results if r.get("error"))
    failed = total - passed - blocked - captcha - crashed

    print(f"\nOverall: {passed} pass, {blocked} blocked, {captcha} captcha, {failed} fail, {crashed} crash")
    if total > 0:
        print(f"Detection rate: {(blocked + captcha) / total * 100:.1f}%")
        print(f"Success rate: {passed / total * 100:.1f}%")

    # --- Per-site breakdown ---
    print(f"\n{'Site':<20} {'Prot.':<20} {'Tests':>5} {'Pass':>5} {'Block':>5} {'CAPTCHA':>7} {'Fail':>5} {'Crash':>5} {'Avg(s)':>7}")
    print("-" * 80)

    by_site = defaultdict(lambda: {"total": 0, "pass": 0, "blocked": 0, "captcha": 0, "fail": 0, "crash": 0, "times": [], "protection": ""})
    for r in results:
        s = by_site[r["site"]]
        s["total"] += 1
        s["protection"] = r.get("protection", "")
        s["times"].append(r.get("time_seconds", 0))
        if r.get("error"):
            s["crash"] += 1
        elif r.get("blocked"):
            s["blocked"] += 1
        elif r.get("captcha"):
            s["captcha"] += 1
        elif r.get("success"):
            s["pass"] += 1
        else:
            s["fail"] += 1

    for site in sorted(by_site.keys()):
        s = by_site[site]
        avg = sum(s["times"]) / len(s["times"]) if s["times"] else 0
        print(f"{site:<20} {s['protection']:<20} {s['total']:>5} {s['pass']:>5} {s['blocked']:>5} {s['captcha']:>7} {s['fail']:>5} {s['crash']:>5} {avg:>6.1f}s")

    # --- Detection timeline (did blocking increase over rounds?) ---
    print(f"\n{'=' * 80}")
    print("DETECTION TIMELINE — does blocking increase over time?")
    print(f"{'=' * 80}")
    by_round = defaultdict(lambda: {"total": 0, "blocked": 0, "captcha": 0})
    for r in results:
        rd = by_round[r.get("round", 0)]
        rd["total"] += 1
        if r.get("blocked"):
            rd["blocked"] += 1
        if r.get("captcha"):
            rd["captcha"] += 1

    print(f"{'Round':>6} {'Tests':>6} {'Blocked':>8} {'CAPTCHA':>8} {'Detection %':>12}")
    for rnd in sorted(by_round.keys()):
        rd = by_round[rnd]
        det_pct = (rd["blocked"] + rd["captcha"]) / rd["total"] * 100 if rd["total"] > 0 else 0
        marker = " ← WARNING" if det_pct > 20 else ""
        print(f"{rnd:>6} {rd['total']:>6} {rd['blocked']:>8} {rd['captcha']:>8} {det_pct:>11.1f}%{marker}")

    # --- Sites that got BLOCKED or CAPTCHA'd ---
    print(f"\n{'=' * 80}")
    print("BLOCKING INCIDENTS (details)")
    print(f"{'=' * 80}")
    incidents = [r for r in results if r.get("blocked") or r.get("captcha")]
    if not incidents:
        print("None — zero detections across all tests!")
    else:
        for r in incidents:
            print(f"  Round {r.get('round', '?'):>3} | {r['site']:<18} | {r.get('detection_reason', '?')}")
            print(f"         URL: {r.get('final_url', '?')[:60]}")
            print(f"         Data: {r.get('data_preview', '')[:80]}")
            print()

    # --- Crashes ---
    crashes = [r for r in results if r.get("error")]
    if crashes:
        print(f"\n{'=' * 80}")
        print(f"CRASHES ({len(crashes)} total)")
        print(f"{'=' * 80}")
        by_error = defaultdict(list)
        for r in crashes:
            by_error[r["error"][:80]].append(r["site"])
        for err, sites in sorted(by_error.items(), key=lambda x: -len(x[1])):
            print(f"  [{len(sites)}x] {err}")
            print(f"       Sites: {', '.join(set(sites))}")

    # --- Protection system results ---
    print(f"\n{'=' * 80}")
    print("BY PROTECTION SYSTEM")
    print(f"{'=' * 80}")
    by_prot = defaultdict(lambda: {"total": 0, "pass": 0, "blocked": 0, "captcha": 0})
    for r in results:
        p = by_prot[r.get("protection", "unknown")]
        p["total"] += 1
        if r.get("blocked"):
            p["blocked"] += 1
        elif r.get("captcha"):
            p["captcha"] += 1
        elif r.get("success"):
            p["pass"] += 1

    print(f"{'Protection':<25} {'Tests':>6} {'Pass':>6} {'Blocked':>8} {'CAPTCHA':>8} {'Bypass %':>9}")
    for prot in sorted(by_prot.keys()):
        p = by_prot[prot]
        bypass = p["pass"] / p["total"] * 100 if p["total"] > 0 else 0
        print(f"{prot:<25} {p['total']:>6} {p['pass']:>6} {p['blocked']:>8} {p['captcha']:>8} {bypass:>8.1f}%")

    # --- Summary verdict ---
    print(f"\n{'=' * 80}")
    print("VERDICT")
    print(f"{'=' * 80}")
    if blocked + captcha == 0:
        print("ZERO DETECTIONS — Camoufox anti-detection held across all sites and rounds.")
    elif (blocked + captcha) / total < 0.05:
        print(f"MINIMAL DETECTION — {blocked + captcha} incidents out of {total} tests ({(blocked + captcha) / total * 100:.1f}%). Investigate specific sites.")
    elif (blocked + captcha) / total < 0.20:
        print(f"SOME DETECTION — {blocked + captcha} incidents ({(blocked + captcha) / total * 100:.1f}%). Some sites are detecting patterns.")
    else:
        print(f"SIGNIFICANT DETECTION — {blocked + captcha} incidents ({(blocked + captcha) / total * 100:.1f}%). Anti-detection needs improvement.")


def combined_audit(tags: list):
    """Combine results from all test runs into one summary."""
    all_results = []
    for tag in tags:
        results = load_results(tag)
        for r in results:
            r["api"] = tag
        all_results.extend(results)

    if not all_results:
        return

    print(f"\n{'=' * 80}")
    print("COMBINED AUDIT — ALL APIS")
    print(f"{'=' * 80}")
    print(f"Total tests: {len(all_results)}")
    print(f"APIs: {', '.join(tags)}")

    total = len(all_results)
    passed = sum(1 for r in all_results if r["success"] and not r["blocked"] and not r["captcha"])
    blocked = sum(1 for r in all_results if r["blocked"])
    captcha = sum(1 for r in all_results if r["captcha"])

    print(f"Pass: {passed} ({passed/total*100:.1f}%)")
    print(f"Blocked: {blocked} ({blocked/total*100:.1f}%)")
    print(f"CAPTCHA: {captcha}")

    # By site across all APIs
    by_site = defaultdict(lambda: {"total": 0, "blocked": 0, "captcha": 0, "pass": 0})
    for r in all_results:
        s = by_site[r["site"]]
        s["total"] += 1
        if r.get("blocked"): s["blocked"] += 1
        elif r.get("captcha"): s["captcha"] += 1
        elif r.get("success"): s["pass"] += 1

    print(f"\n{'Site':<20} {'Total':>6} {'Pass':>6} {'Blocked':>8} {'CAPTCHA':>8} {'Block %':>8}")
    print("-" * 60)
    for site in sorted(by_site.keys(), key=lambda s: -by_site[s]["blocked"]):
        s = by_site[site]
        block_pct = s["blocked"] / s["total"] * 100 if s["total"] > 0 else 0
        marker = " ←" if s["blocked"] > 0 else ""
        print(f"{site:<20} {s['total']:>6} {s['pass']:>6} {s['blocked']:>8} {s['captcha']:>8} {block_pct:>7.1f}%{marker}")

    # By API
    print(f"\n{'API':<25} {'Tests':>6} {'Pass':>6} {'Blocked':>8}")
    print("-" * 50)
    for tag in tags:
        api_results = [r for r in all_results if r["api"] == tag]
        api_pass = sum(1 for r in api_results if r["success"] and not r["blocked"])
        api_blocked = sum(1 for r in api_results if r["blocked"])
        print(f"{tag:<25} {len(api_results):>6} {api_pass:>6} {api_blocked:>8}")

    print(f"\n{'=' * 80}")
    if blocked + captcha == 0:
        print("VERDICT: ZERO DETECTIONS across all APIs and all sites.")
    else:
        blocking_sites = set(r["site"] for r in all_results if r["blocked"] or r["captcha"])
        clean_sites = set(r["site"] for r in all_results) - blocking_sites
        print(f"VERDICT: {len(blocking_sites)} site(s) detected us: {', '.join(sorted(blocking_sites))}")
        print(f"         {len(clean_sites)} site(s) fully clean: {', '.join(sorted(clean_sites))}")
        print(f"         Detection rate: {(blocked+captcha)/total*100:.1f}%")
        print(f"         Detection method: {'IP rate limiting' if 'Reddit' in blocking_sites and len(blocking_sites) == 1 else 'mixed'}")


if __name__ == "__main__":
    tags = sys.argv[1:] or ["claude", "kimi", "openai", "claude_aggressive"]
    for tag in tags:
        f = Path(__file__).parent / f"stress_test_results_{tag}.json"
        if f.exists():
            audit(tag)
    # Combined
    valid_tags = [t for t in tags if (Path(__file__).parent / f"stress_test_results_{t}.json").exists()]
    if len(valid_tags) > 1:
        combined_audit(valid_tags)
