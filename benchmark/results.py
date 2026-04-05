"""Results aggregation and Markdown generation for benchmark runs."""

import json
import logging
from pathlib import Path

log = logging.getLogger("benchmark.results")

# Steel.dev leaderboard competitor scores (as of 2025-Q1)
COMPETITORS = [
    {"name": "Surfer 2", "llm": "—", "score_pct": 97.1, "avg_steps": None, "avg_duration_s": None},
    {"name": "Magnitude", "llm": "Claude Sonnet", "score_pct": 93.9, "avg_steps": None, "avg_duration_s": None},
    {"name": "browser-use", "llm": "GPT-4o", "score_pct": 89.1, "avg_steps": None, "avg_duration_s": None},
    {"name": "Skyvern 2.0", "llm": "—", "score_pct": 85.9, "avg_steps": None, "avg_duration_s": None},
]


def aggregate_results(results_dir: str) -> dict:
    """Iterate task directories, read result.json + result.eval.json, compute stats.

    Saves summary.json to results_dir and returns the summary dict.

    Args:
        results_dir: Root directory. Task dirs live in <results_dir>/tasks/.

    Returns:
        Summary dict with keys: evaluated, success, score_pct, avg_steps,
        avg_duration_s, per_site, agent, llm.
    """
    root = Path(results_dir)
    tasks_dir = root / "tasks"

    evaluated = 0
    success_count = 0
    total_steps = 0
    total_duration = 0.0
    per_site: dict[str, dict] = {}

    if tasks_dir.exists():
        for task_dir in sorted(tasks_dir.iterdir()):
            if not task_dir.is_dir():
                continue
            result_file = task_dir / "result.json"
            eval_file = task_dir / "result.eval.json"
            if not result_file.exists() or not eval_file.exists():
                continue

            try:
                result = json.loads(result_file.read_text())
                evaluation = json.loads(eval_file.read_text())
            except (json.JSONDecodeError, OSError) as exc:
                log.warning("Skipping %s: %s", task_dir.name, exc)
                continue

            verdict = evaluation.get("verdict")
            if verdict not in ("SUCCESS", "NOT SUCCESS"):
                continue

            evaluated += 1
            is_success = verdict == "SUCCESS"
            if is_success:
                success_count += 1

            steps = result.get("steps_taken", 0) or 0
            duration = result.get("duration_s", 0.0) or 0.0
            total_steps += steps
            total_duration += duration

            web_name = result.get("web_name", "unknown")
            if web_name not in per_site:
                per_site[web_name] = {"evaluated": 0, "success": 0, "total_steps": 0, "total_duration": 0.0}
            per_site[web_name]["evaluated"] += 1
            per_site[web_name]["success"] += (1 if is_success else 0)
            per_site[web_name]["total_steps"] += steps
            per_site[web_name]["total_duration"] += duration

    score_pct = round(success_count / evaluated * 100, 1) if evaluated > 0 else 0.0
    avg_steps = round(total_steps / evaluated, 1) if evaluated > 0 else 0.0
    avg_duration_s = round(total_duration / evaluated, 1) if evaluated > 0 else 0.0

    # Compute per-site score_pct
    for site in per_site.values():
        n = site["evaluated"]
        site["score_pct"] = round(site["success"] / n * 100, 1) if n > 0 else 0.0
        site["avg_steps"] = round(site["total_steps"] / n, 1) if n > 0 else 0.0
        site["avg_duration_s"] = round(site["total_duration"] / n, 1) if n > 0 else 0.0

    summary = {
        "agent": "fantoma",
        "llm": "auto",
        "evaluated": evaluated,
        "success": success_count,
        "score_pct": score_pct,
        "avg_steps": avg_steps,
        "avg_duration_s": avg_duration_s,
        "per_site": per_site,
    }

    summary_path = root / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2))
    log.info("Summary saved to %s", summary_path)

    return summary


def generate_comparison_table(summary: dict) -> str:
    """Return a Markdown table with Fantoma (bold) plus competitor rows.

    Args:
        summary: Dict produced by aggregate_results (or equivalent).

    Returns:
        Markdown string with a comparison table.
    """
    agent_label = summary.get("agent", "fantoma")
    llm_label = summary.get("llm", "—")
    score_pct = summary.get("score_pct", 0.0)
    avg_steps = summary.get("avg_steps", "—")
    avg_duration_s = summary.get("avg_duration_s", "—")

    display_name = "Fantoma" if "fantoma" in str(agent_label).lower() else agent_label

    lines = [
        "| Agent | LLM | Score | Avg Steps | Avg Duration |",
        "|-------|-----|-------|-----------|--------------|",
        f"| **{display_name}** | **{llm_label}** | **{score_pct}%** | **{avg_steps}** | **{avg_duration_s}s** |",
    ]

    for comp in COMPETITORS:
        comp_steps = comp["avg_steps"] if comp["avg_steps"] is not None else "—"
        comp_dur = f"{comp['avg_duration_s']}s" if comp["avg_duration_s"] is not None else "—"
        lines.append(
            f"| {comp['name']} | {comp['llm']} | {comp['score_pct']}% | {comp_steps} | {comp_dur} |"
        )

    return "\n".join(lines)


def generate_site_breakdown(summary: dict) -> str:
    """Return a per-site Markdown table sorted by score descending.

    Args:
        summary: Dict produced by aggregate_results.

    Returns:
        Markdown string with site breakdown table.
    """
    per_site = summary.get("per_site", {})
    if not per_site:
        return "_No site data available._"

    lines = [
        "| Site | Evaluated | Success | Score | Avg Steps | Avg Duration |",
        "|------|-----------|---------|-------|-----------|--------------|",
    ]

    for site_name, data in sorted(per_site.items(), key=lambda x: x[1].get("score_pct", 0), reverse=True):
        lines.append(
            f"| {site_name} | {data['evaluated']} | {data['success']} | {data.get('score_pct', 0)}% "
            f"| {data.get('avg_steps', '—')} | {data.get('avg_duration_s', '—')}s |"
        )

    return "\n".join(lines)


def generate_summary_md(summary: dict) -> str:
    """Generate full Markdown content for docs/benchmark.md.

    Args:
        summary: Dict produced by aggregate_results.

    Returns:
        Markdown string ready to write to a file.
    """
    agent = summary.get("agent", "fantoma")
    llm = summary.get("llm", "—")
    evaluated = summary.get("evaluated", 0)
    success = summary.get("success", 0)
    score_pct = summary.get("score_pct", 0.0)

    comparison = generate_comparison_table(summary)
    site_breakdown = generate_site_breakdown(summary)

    return f"""# Fantoma Benchmark Results

**Agent:** {agent}
**LLM:** {llm}
**Tasks evaluated:** {evaluated}
**Successful:** {success}
**Score:** {score_pct}%

## Comparison vs. Leaderboard

{comparison}

## Per-Site Breakdown

{site_breakdown}

_Results generated from WebVoyager task suite. Evaluation by GPT-4o judge._
"""


def update_readme(results_dir: str, readme_path: str = "README.md") -> None:
    """Replace benchmark section in README.md between marker comments.

    Markers: <!-- BENCHMARK:START --> and <!-- BENCHMARK:END -->
    If markers are absent, inserts before ## Test Results.

    Args:
        results_dir: Directory containing summary.json.
        readme_path: Path to README.md (default: repo root README.md).
    """
    root = Path(results_dir)
    summary_file = root / "summary.json"
    if not summary_file.exists():
        log.error("summary.json not found in %s", results_dir)
        return

    summary = json.loads(summary_file.read_text())
    comparison = generate_comparison_table(summary)

    readme = Path(readme_path)
    if not readme.exists():
        log.error("README.md not found at %s", readme_path)
        return

    content = readme.read_text()

    start_marker = "<!-- BENCHMARK:START -->"
    end_marker = "<!-- BENCHMARK:END -->"
    block = f"{start_marker}\n{comparison}\n{end_marker}"

    if start_marker in content and end_marker in content:
        before = content[: content.index(start_marker)]
        after = content[content.index(end_marker) + len(end_marker):]
        new_content = before + block + after
    elif "## Test Results" in content:
        new_content = content.replace("## Test Results", block + "\n\n## Test Results", 1)
    else:
        new_content = content + "\n\n" + block + "\n"

    readme.write_text(new_content)
    log.info("README.md updated with benchmark results")
