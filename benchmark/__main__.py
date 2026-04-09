"""CLI entry point: python -m benchmark"""

import argparse
import sys


def main():
    parser = argparse.ArgumentParser(
        prog="benchmark",
        description="Run WebVoyager benchmark against Fantoma",
    )
    parser.add_argument("--llm", default=None, help="LLM endpoint URL")
    parser.add_argument("--llm-api-key", default=None, help="LLM API key")
    parser.add_argument("--llm-model", default=None, help="LLM model name")
    parser.add_argument("--eval-model", default=None, help="Evaluator model (default: gpt-4o)")
    parser.add_argument("--workers", type=int, default=None, help="Parallel workers (default: 4)")
    parser.add_argument("--max-steps", type=int, default=None, help="Max agent steps per task")
    parser.add_argument("--timeout", type=int, default=None, help="Seconds per task")
    parser.add_argument("--browser", default=None, help="Browser engine")
    parser.add_argument("--task", default=None, help="Run single task by ID")
    parser.add_argument("--site", default=None, help="Run tasks for one website only")
    parser.add_argument("--limit", type=int, default=None, help="Max number of tasks to run")
    parser.add_argument("--eval-only", default=None, metavar="DIR", help="Re-evaluate existing results")
    parser.add_argument("--update-readme", default=None, metavar="DIR", help="Update README from results")
    parser.add_argument("--captcha-api", default=None, help="CAPTCHA solver API (e.g. capsolver)")
    parser.add_argument("--captcha-key", default=None, help="CAPTCHA solver API key")
    parser.add_argument("--step-screenshots", action="store_true", help="Capture per-step screenshots")
    args = parser.parse_args()

    from benchmark.config import BenchmarkConfig

    overrides = {}
    if args.llm:
        overrides["llm_url"] = args.llm
    if args.llm_api_key:
        overrides["llm_api_key"] = args.llm_api_key
    if args.llm_model:
        overrides["llm_model"] = args.llm_model
    if args.eval_model:
        overrides["eval_model"] = args.eval_model
    if args.workers is not None:
        overrides["workers"] = args.workers
    if args.max_steps is not None:
        overrides["max_steps"] = args.max_steps
    if args.timeout is not None:
        overrides["timeout"] = args.timeout
    if args.browser:
        overrides["browser"] = args.browser
    if args.captcha_api:
        overrides["captcha_api"] = args.captcha_api
    if args.captcha_key:
        overrides["captcha_key"] = args.captcha_key
    if args.step_screenshots:
        overrides["capture_step_screenshots"] = True

    config = BenchmarkConfig.from_env(**overrides)

    if args.eval_only:
        from benchmark.evaluator import evaluate_results
        evaluate_results(args.eval_only, config)
        sys.exit(0)

    if args.update_readme:
        from benchmark.results import update_readme
        update_readme(args.update_readme)
        sys.exit(0)

    from benchmark.runner import run_benchmark
    run_benchmark(config, task_filter=args.task, site_filter=args.site, limit=args.limit)


if __name__ == "__main__":
    main()
