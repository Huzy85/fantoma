"""
Fantoma Benchmark Failure Analysis
Usage: python3 analyze_failures.py benchmark/results/RUN_ID
"""

import json
import sys
import re
from collections import Counter, defaultdict
from pathlib import Path


# ── helpers ──────────────────────────────────────────────────────────────────

def load_tasks(results_dir: Path):
    tasks_dir = results_dir / "tasks"
    tasks = []
    for task_dir in sorted(tasks_dir.iterdir()):
        if not task_dir.is_dir():
            continue
        rf = task_dir / "result.json"
        ef = task_dir / "result.eval.json"
        if not rf.exists():
            continue
        result = json.loads(rf.read_text())
        evaluation = json.loads(ef.read_text()) if ef.exists() else {}
        tasks.append({**result, **evaluation})
    return tasks


def domain(url: str) -> str:
    if not url:
        return ""
    m = re.search(r"https?://([^/]+)", url)
    return m.group(1).lower() if m else url.lower()


def action_type(action_str: str) -> str:
    if not action_str:
        return "unknown"
    m = re.match(r"(\w+)", str(action_str))
    return m.group(1) if m else str(action_str)[:20]


def categorise_failure(task: dict) -> str:
    steps = task.get("steps_taken") or 0
    status = task.get("status", "")
    steps_detail = task.get("steps_detail") or []
    start = task.get("start_url", "")
    final = task.get("final_url", "")
    answer = task.get("answer") or ""

    if status == "timeout":
        return "TIMEOUT"
    if status == "error" or not answer:
        return "ERROR"
    if steps >= 24:
        return "HIT_MAX_STEPS"

    # Loop detection: same action type 3+ times in last 10 steps
    last_actions = [action_type(s.get("action", "")) for s in steps_detail[-10:]]
    if last_actions:
        most_common, count = Counter(last_actions).most_common(1)[0]
        if count >= 3 and most_common in ("scroll", "click"):
            return "LOOP_DETECTED"

    # Navigation stuck: last 5 all scroll
    last5 = [action_type(s.get("action", "")) for s in steps_detail[-5:]]
    if last5 and all(a == "scroll" for a in last5):
        return "NAVIGATION_STUCK"

    # Wrong domain
    if domain(start) and domain(final):
        start_base = ".".join(domain(start).split(".")[-2:])
        final_base = ".".join(domain(final).split(".")[-2:])
        if start_base not in final_base and final_base not in start_base:
            return "WRONG_URL"

    return "WRONG_ANSWER"


def step_histogram(values: list[int], buckets=[(1,5),(6,10),(11,15),(16,20),(21,25),(25,999)]) -> str:
    labels = ["1-5", "6-10", "11-15", "16-20", "21-25", "25+"]
    counts = [sum(1 for v in values if lo <= v <= hi) for (lo, hi), _ in zip(buckets, labels)]
    total = len(values) or 1
    lines = []
    for label, count in zip(labels, counts):
        bar = "█" * int(count / total * 30)
        lines.append(f"  {label:>6}  {bar} {count}")
    return "\n".join(lines)


# ── main ─────────────────────────────────────────────────────────────────────

def main(results_dir: str):
    root = Path(results_dir)
    tasks = load_tasks(root)

    all_tasks = tasks
    scored = [t for t in tasks if t.get("verdict") in ("SUCCESS", "NOT SUCCESS")]
    unscored = [t for t in tasks if t.get("verdict") not in ("SUCCESS", "NOT SUCCESS")]
    successes = [t for t in scored if t.get("verdict") == "SUCCESS"]
    failures = [t for t in scored if t.get("verdict") == "NOT SUCCESS"]

    score_pct = round(len(successes) / len(scored) * 100, 1) if scored else 0

    out = []
    p = out.append

    # ── 1. OVERALL SUMMARY ───────────────────────────────────────────────────
    p("# Fantoma Benchmark — Failure Analysis\n")
    p(f"**Results dir:** `{root}`\n")
    p("## 1. Overall Summary\n")
    p(f"| Metric | Value |")
    p(f"|--------|-------|")
    p(f"| Total tasks | {len(all_tasks)} |")
    p(f"| Scored (verdict) | {len(scored)} |")
    p(f"| Unscored (null verdict) | {len(unscored)} |")
    p(f"| Successes | {len(successes)} |")
    p(f"| Failures | {len(failures)} |")
    p(f"| Score (scored only) | **{score_pct}%** |")
    p(f"| Avg steps (all) | {round(sum(t.get('steps_taken') or 0 for t in all_tasks) / len(all_tasks), 1) if all_tasks else 0} |")
    p("")

    # ── 2. PER-SITE BREAKDOWN ────────────────────────────────────────────────
    p("## 2. Per-Site Breakdown\n")
    sites: dict[str, list] = defaultdict(list)
    for t in all_tasks:
        sites[t.get("web_name", "unknown")].append(t)

    p("| Site | Total | Scored | Success | Score% | Avg Steps | Avg Dur(s) | Hit Max Steps |")
    p("|------|-------|--------|---------|--------|-----------|------------|---------------|")
    for site, site_tasks in sorted(sites.items(), key=lambda x: -(sum(1 for t in x[1] if t.get("verdict") == "SUCCESS") / max(1, sum(1 for t in x[1] if t.get("verdict") in ("SUCCESS","NOT SUCCESS"))))):
        sc = [t for t in site_tasks if t.get("verdict") in ("SUCCESS", "NOT SUCCESS")]
        su = [t for t in sc if t.get("verdict") == "SUCCESS"]
        sp = round(len(su) / len(sc) * 100, 1) if sc else 0
        avg_steps = round(sum(t.get("steps_taken") or 0 for t in site_tasks) / len(site_tasks), 1)
        avg_dur = round(sum(t.get("duration_s") or 0 for t in site_tasks) / len(site_tasks), 1)
        max_hits = sum(1 for t in site_tasks if (t.get("steps_taken") or 0) >= 24)
        p(f"| {site} | {len(site_tasks)} | {len(sc)} | {len(su)} | {sp}% | {avg_steps} | {avg_dur} | {max_hits} |")
    p("")

    # ── 3. FAILURE CATEGORIES ────────────────────────────────────────────────
    p("## 3. Failure Categories\n")
    cat_counts: Counter = Counter()
    for t in failures:
        cat_counts[categorise_failure(t)] += 1

    p("| Category | Count | % of Failures |")
    p("|----------|-------|---------------|")
    for cat, count in cat_counts.most_common():
        pct = round(count / len(failures) * 100, 1) if failures else 0
        p(f"| {cat} | {count} | {pct}% |")
    p("")

    cat_descriptions = {
        "HIT_MAX_STEPS": "Agent used all 25 steps without completing — either the task is too long or the agent is going in circles.",
        "LOOP_DETECTED": "Same action repeated 3+ times in the last 10 steps — agent is stuck.",
        "WRONG_ANSWER": "Agent reached the right place but produced an incorrect or incomplete answer — LLM reasoning failure.",
        "WRONG_URL": "Agent ended up on a different domain — navigation went off-track.",
        "NAVIGATION_STUCK": "Last 5 actions were all scrolls — agent couldn't find the next element to interact with.",
        "TIMEOUT": "Task exceeded time limit.",
        "ERROR": "Agent encountered a hard error or produced no answer.",
    }
    for cat, desc in cat_descriptions.items():
        if cat in cat_counts:
            p(f"**{cat}** ({cat_counts[cat]}): {desc}\n")

    # ── 4. SITE FAILURE PATTERNS ─────────────────────────────────────────────
    p("## 4. Site Failure Patterns\n")
    for site, site_tasks in sorted(sites.items()):
        sc = [t for t in site_tasks if t.get("verdict") in ("SUCCESS", "NOT SUCCESS")]
        su = [t for t in sc if t.get("verdict") == "SUCCESS"]
        sp = round(len(su) / len(sc) * 100, 1) if sc else 0
        site_failures = [t for t in sc if t.get("verdict") == "NOT SUCCESS"]
        if sp >= 50 or not site_failures:
            continue

        p(f"### {site} ({sp}% — {len(su)}/{len(sc)} scored)")
        site_cats = Counter(categorise_failure(t) for t in site_failures)
        p("**Failure breakdown:**")
        for cat, cnt in site_cats.most_common():
            p(f"- {cat}: {cnt}")
        p("")
        p("**Example failed instructions:**")
        for t in site_failures[:5]:
            cat = categorise_failure(t)
            steps = t.get("steps_taken") or 0
            p(f"- [{cat}, {steps} steps] {t.get('instruction','')[:120]}")
        p("")

    # ── 5. ARCHITECTURAL BARRIERS ────────────────────────────────────────────
    p("## 5. Architectural Barriers\n")
    p("Sites with 0% or near-zero scores where the failure pattern points to structural limitations:\n")

    barrier_sites = {s: ts for s, ts in sites.items() if True}
    for site, site_tasks in sorted(barrier_sites.items()):
        sc = [t for t in site_tasks if t.get("verdict") in ("SUCCESS", "NOT SUCCESS")]
        if not sc:
            continue
        su = [t for t in sc if t.get("verdict") == "SUCCESS"]
        sp = round(len(su) / len(sc) * 100, 1)
        if sp > 15:
            continue

        site_failures = [t for t in sc if t.get("verdict") == "NOT SUCCESS"]
        site_cats = Counter(categorise_failure(t) for t in site_failures)
        max_hits = sum(1 for t in site_tasks if (t.get("steps_taken") or 0) >= 24)
        max_pct = round(max_hits / len(site_tasks) * 100) if site_tasks else 0

        # Analyse final URLs for patterns
        final_urls = [t.get("final_url", "") for t in site_failures]
        url_domains = Counter(domain(u) for u in final_urls if u)

        p(f"### {site} — {sp}%")
        p(f"- Tasks hit max steps: {max_hits}/{len(site_tasks)} ({max_pct}%)")
        p(f"- Top failure categories: {', '.join(f'{c}={n}' for c,n in site_cats.most_common(3))}")
        p(f"- Final URL domains seen: {dict(url_domains.most_common(3))}")

        # Heuristic diagnosis
        if max_pct > 60:
            p("- **Diagnosis: Task complexity exceeds step budget.** Agent navigates but runs out of steps. Either max_steps needs raising or agent needs more efficient navigation.")
        if site_cats.get("LOOP_DETECTED", 0) > len(site_failures) * 0.3:
            p("- **Diagnosis: Agent loops on this site.** DOM representation likely too noisy or element IDs unstable — agent keeps re-clicking.")
        if site_cats.get("WRONG_ANSWER", 0) > len(site_failures) * 0.4:
            p("- **Diagnosis: Navigation is OK but answer extraction fails.** The LLM reaches the right page but can't extract structured data (prices, dates, availability) from the DOM text.")
        if site_cats.get("NAVIGATION_STUCK", 0) > len(site_failures) * 0.3:
            p("- **Diagnosis: Agent can't find interactive elements.** Likely dynamic content (JS-rendered dropdowns, modals, lazy-loaded sections) that aren't visible in the DOM snapshot.")
        p("")

    # ── 6. STEP EFFICIENCY ───────────────────────────────────────────────────
    p("## 6. Step Efficiency\n")
    success_steps = [t.get("steps_taken") or 0 for t in successes]
    failure_steps = [t.get("steps_taken") or 0 for t in failures]

    p(f"**Successful tasks** (n={len(successes)}, avg={round(sum(success_steps)/len(success_steps),1) if success_steps else 0} steps):")
    p("```")
    p(step_histogram(success_steps))
    p("```")
    p(f"**Failed tasks** (n={len(failures)}, avg={round(sum(failure_steps)/len(failure_steps),1) if failure_steps else 0} steps):")
    p("```")
    p(step_histogram(failure_steps))
    p("```\n")

    # ── 7. TOP WASTED ACTION SEQUENCES ───────────────────────────────────────
    p("## 7. Top Wasted Action Sequences (Last 5 Steps of Failed Tasks)\n")
    seq_counter: Counter = Counter()
    for t in failures:
        steps_detail = t.get("steps_detail") or []
        last5 = [action_type(s.get("action", "")) for s in steps_detail[-5:]]
        if last5:
            seq_counter[" → ".join(last5)] += 1

    p("| Sequence | Count |")
    p("|----------|-------|")
    for seq, count in seq_counter.most_common(15):
        p(f"| `{seq}` | {count} |")
    p("")

    # ── 8. IMPROVEMENT RECOMMENDATIONS ──────────────────────────────────────
    p("## 8. Improvement Recommendations\n")

    total_failures = len(failures)
    max_step_count = cat_counts.get("HIT_MAX_STEPS", 0)
    loop_count = cat_counts.get("LOOP_DETECTED", 0)
    stuck_count = cat_counts.get("NAVIGATION_STUCK", 0)
    wrong_ans_count = cat_counts.get("WRONG_ANSWER", 0)
    wrong_url_count = cat_counts.get("WRONG_URL", 0)

    recs = []

    if max_step_count > total_failures * 0.25:
        recs.append((
            "Raise max_steps or add adaptive step budget",
            f"{max_step_count} tasks ({round(max_step_count/total_failures*100)}% of failures) hit the 25-step cap. "
            "Consider raising to 35 or dynamically adjusting based on task complexity. "
            "Short-term: run the same tasks with max_steps=35 and compare."
        ))

    if loop_count > total_failures * 0.15:
        recs.append((
            "Improve loop detection and recovery",
            f"{loop_count} tasks got into action loops. Fantoma should detect repeated element IDs in recent history "
            "and force a different strategy (e.g. try a search instead of clicking nav)."
        ))

    if stuck_count > total_failures * 0.1:
        recs.append((
            "Handle dynamic/lazy-loaded content",
            f"{stuck_count} tasks ended in scroll loops. The DOM snapshot likely doesn't include "
            "JS-rendered elements (dropdowns, carousels, price widgets). "
            "Consider adding a wait-for-stable or explicit JS trigger before taking DOM snapshots."
        ))

    if wrong_ans_count > total_failures * 0.2:
        recs.append((
            "Improve answer extraction prompt",
            f"{wrong_ans_count} tasks reached the right page but gave wrong answers. "
            "The LLM is seeing the DOM but not extracting the correct structured data. "
            "A dedicated extraction step (separate from navigation) with a targeted prompt would help."
        ))

    if wrong_url_count > total_failures * 0.05:
        recs.append((
            "Add domain drift detection",
            f"{wrong_url_count} tasks ended on the wrong domain. "
            "Fantoma should check whether the current domain still matches the task's target site "
            "and recover (go back) when drift is detected."
        ))

    # Site-specific recs
    zero_sites = [s for s, ts in sites.items()
                  if sum(1 for t in ts if t.get("verdict") == "SUCCESS") == 0
                  and sum(1 for t in ts if t.get("verdict") in ("SUCCESS", "NOT SUCCESS")) >= 5]
    if zero_sites:
        recs.append((
            f"Investigate zero-score sites: {', '.join(zero_sites)}",
            "These sites scored 0% across all evaluated tasks. Manually review 2-3 trajectories per site "
            "to identify whether the block is: login wall, CAPTCHA, JS-only content, or DOM parsing failure. "
            "Each may need a targeted fix (cookie injection, wait strategy, site-specific selector)."
        ))

    for i, (title, detail) in enumerate(recs, 1):
        p(f"### {i}. {title}")
        p(f"{detail}\n")

    if not recs:
        p("_No strong patterns detected — review individual site breakdowns above._\n")

    print("\n".join(out))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 analyze_failures.py benchmark/results/RUN_ID")
        sys.exit(1)
    main(sys.argv[1])
