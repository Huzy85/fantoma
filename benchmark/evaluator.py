"""GPT-4V evaluator for benchmark tasks using the standard WebVoyager prompt."""

import base64
import json
import logging
import time
from pathlib import Path

import httpx

log = logging.getLogger("benchmark.evaluator")

EVAL_SYSTEM_PROMPT = """As an evaluator, you will be presented with three primary components to assist you in your role:

1. Web Task Instruction: This is a clear and specific directive provided in natural language, detailing the online activity to be carried out. These requirements may include conducting searches, verifying information, comparing prices, checking availability, or any other action relevant to the specified web service (such as Amazon, Apple, ArXiv, BBC News, Booking etc).

2. Result Screenshots: This is a visual representation of the screen showing the result or intermediate state of performing a web task. It serves as visual proof of the actions taken in response to the instruction.

3. Result Response: This is a textual response obtained after the execution of the web task. It serves as textual result in response to the instruction.

-- You DO NOT NEED to interact with web pages or perform actions such as booking flights or conducting searches on websites.
-- You SHOULD NOT make assumptions based on information not presented in the screenshot when comparing it to the instructions.
-- Your primary responsibility is to conduct a thorough assessment of the web task instruction against the outcome depicted in the screenshot and in the response, evaluating whether the actions taken align with the given instructions.
-- NOTE that the instruction may involve more than one task, for example, locating the garage and summarizing the review. Failing to complete either task, such as not providing a summary, should be considered unsuccessful.
-- NOTE that the screenshot is authentic, but the response provided by LLM is generated at the end of web browsing, and there may be discrepancies between the text and the screenshots.
-- Note the difference: 1) Result response may contradict the screenshot, then the content of the screenshot prevails, 2) The content in the Result response is not mentioned on the screenshot, choose to believe the content.

You should elaborate on how you arrived at your final evaluation and then provide a definitive verdict on whether the task has been successfully accomplished, either as 'SUCCESS' or 'NOT SUCCESS'."""


def build_eval_messages(
    instruction: str,
    answer: str,
    screenshot_b64: str,
) -> list[dict]:
    """Build OpenAI multimodal messages for GPT-4V evaluation.

    Args:
        instruction: The web task instruction.
        answer: The agent's textual response.
        screenshot_b64: Base64-encoded screenshot PNG.

    Returns:
        List of OpenAI chat messages (system + user).
    """
    system_msg = {
        "role": "system",
        "content": EVAL_SYSTEM_PROMPT,
    }
    user_msg = {
        "role": "user",
        "content": [
            {
                "type": "text",
                "text": f"Web Task Instruction: {instruction}",
            },
            {
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/png;base64,{screenshot_b64}",
                    "detail": "high",
                },
            },
            {
                "type": "text",
                "text": f"Result Response: {answer}",
            },
        ],
    }
    return [system_msg, user_msg]


def parse_verdict(response_text: str) -> str | None:
    """Extract SUCCESS / NOT SUCCESS verdict from evaluator response.

    NOT SUCCESS takes priority over SUCCESS (which appears as a substring).

    Args:
        response_text: Raw text from the GPT-4V evaluator.

    Returns:
        "SUCCESS", "NOT SUCCESS", or None if indeterminate.
    """
    if "NOT SUCCESS" in response_text:
        return "NOT SUCCESS"
    if "SUCCESS" in response_text:
        return "SUCCESS"
    return None


def evaluate_single(
    instruction: str,
    answer: str,
    screenshot_path: str | Path,
    openai_api_key: str,
    model: str = "gpt-4o",
) -> dict:
    """Call GPT-4V to evaluate a single task result.

    Args:
        instruction: The web task instruction.
        answer: The agent's textual response.
        screenshot_path: Path to the final screenshot PNG.
        openai_api_key: OpenAI API key.
        model: GPT-4V model identifier.

    Returns:
        Dict with keys: verdict, eval_model, eval_response.
    """
    screenshot_path = Path(screenshot_path)
    screenshot_b64 = base64.b64encode(screenshot_path.read_bytes()).decode()

    # Truncate long answers so GPT-4o has room to output its full verdict
    if len(answer) > 3000:
        answer = answer[:3000] + "\n[... truncated ...]"

    messages = build_eval_messages(instruction, answer, screenshot_b64)

    response = httpx.post(
        "https://api.openai.com/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {openai_api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "messages": messages,
            "max_tokens": 1000,
            "temperature": 0,
            "seed": 42,
        },
        timeout=60.0,
    )
    response.raise_for_status()

    data = response.json()
    eval_response = data["choices"][0]["message"]["content"]
    verdict = parse_verdict(eval_response)

    return {
        "verdict": verdict,
        "eval_model": model,
        "eval_response": eval_response,
    }


def evaluate_results(results_dir: str | Path, config) -> list[dict]:
    """Batch evaluate all tasks in a benchmark run directory.

    Reads each result.json, calls GPT-4V, and writes result.eval.json.
    Tasks that errored, timed out, or have no screenshot are auto-failed.

    Args:
        results_dir: Directory containing per-task subdirectories.
        config: BenchmarkConfig dataclass or dict with openai_api_key, eval_model.

    Returns:
        List of evaluation result dicts (one per task).
    """
    results_dir = Path(results_dir)
    openai_api_key = getattr(config, "openai_api_key", None) or config.get("openai_api_key", "") if isinstance(config, dict) else config.openai_api_key
    eval_model = getattr(config, "eval_model", "gpt-4o") if not isinstance(config, dict) else config.get("eval_model", "gpt-4o")

    eval_results = []

    # Results live under results_dir/tasks/<task-id>/
    tasks_dir = results_dir / "tasks"
    if not tasks_dir.exists():
        tasks_dir = results_dir  # Fallback: flat structure
    task_dirs = sorted(d for d in tasks_dir.iterdir() if d.is_dir())
    log.info("Evaluating %d task directories in %s", len(task_dirs), results_dir)

    for task_dir in task_dirs:
        result_file = task_dir / "result.json"
        eval_file = task_dir / "result.eval.json"

        if not result_file.exists():
            log.warning("No result.json in %s — skipping", task_dir)
            continue

        # Skip tasks that already have a valid verdict
        if eval_file.exists():
            try:
                existing = json.loads(eval_file.read_text())
                if existing.get("verdict") in ("SUCCESS", "NOT SUCCESS"):
                    log.debug("Skipping already-evaluated task %s", task_dir.name)
                    eval_results.append(existing)
                    continue
            except (json.JSONDecodeError, OSError):
                pass  # Re-evaluate if file is corrupt

        with result_file.open() as f:
            task_result = json.load(f)

        task_id = task_result.get("task_id", task_dir.name)
        status = task_result.get("status", "error")
        answer = task_result.get("answer") or ""
        instruction = task_result.get("instruction", "")

        # Auto-fail errored / timed-out tasks or those with no answer
        if status in ("error", "timeout") or not answer:
            eval_entry = {
                "task_id": task_id,
                "verdict": "NOT SUCCESS",
                "eval_model": eval_model,
                "eval_response": f"Auto-failed: task status={status}, answer present={bool(answer)}",
                "auto_failed": True,
            }
            eval_file.write_text(json.dumps(eval_entry, indent=2))
            eval_results.append(eval_entry)
            log.info("Auto-failed task %s (status=%s)", task_id, status)
            continue

        # Find screenshot
        screenshots = sorted(task_dir.glob("screenshot_*.png"))
        if not screenshots:
            eval_entry = {
                "task_id": task_id,
                "verdict": "NOT SUCCESS",
                "eval_model": eval_model,
                "eval_response": "Auto-failed: no screenshot found",
                "auto_failed": True,
            }
            eval_file.write_text(json.dumps(eval_entry, indent=2))
            eval_results.append(eval_entry)
            log.info("Auto-failed task %s (no screenshot)", task_id)
            continue

        # Use the last screenshot
        screenshot_path = screenshots[-1]

        # Retry with exponential backoff on 429 rate limit errors
        eval_entry = None
        for attempt in range(5):
            try:
                result = evaluate_single(
                    instruction=instruction,
                    answer=answer,
                    screenshot_path=screenshot_path,
                    openai_api_key=openai_api_key,
                    model=eval_model,
                )
                eval_entry = {"task_id": task_id, "auto_failed": False, **result}
                time.sleep(6)  # ~10 req/min to stay under TPM limits
                break
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code == 429:
                    wait = 30 * (2 ** attempt)  # 30s, 60s, 120s, 240s, 480s
                    log.warning("429 on %s (attempt %d) — waiting %ds", task_id, attempt + 1, wait)
                    time.sleep(wait)
                else:
                    log.error("Evaluation failed for task %s: %s", task_id, exc)
                    eval_entry = {
                        "task_id": task_id,
                        "verdict": None,
                        "eval_model": eval_model,
                        "eval_response": f"Evaluation error: {exc}",
                        "auto_failed": False,
                    }
                    break
            except Exception as exc:
                log.error("Evaluation failed for task %s: %s", task_id, exc)
                eval_entry = {
                    "task_id": task_id,
                    "verdict": None,
                    "eval_model": eval_model,
                    "eval_response": f"Evaluation error: {exc}",
                    "auto_failed": False,
                }
                break
        if eval_entry is None:
            eval_entry = {
                "task_id": task_id,
                "verdict": None,
                "eval_model": eval_model,
                "eval_response": "Evaluation error: max retries exceeded",
                "auto_failed": False,
            }

        eval_file.write_text(json.dumps(eval_entry, indent=2))
        eval_results.append(eval_entry)
        log.info(
            "Evaluated task %s: verdict=%s", task_id, eval_entry.get("verdict")
        )

    return eval_results
