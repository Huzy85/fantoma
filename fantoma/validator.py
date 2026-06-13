# fantoma/validator.py
"""Post-run answer validator.

Makes one small LLM call after agent.run() succeeds to verify the extracted
answer actually satisfies the task. Opt-in — off by default.

Fails open: LLM error or empty response → (True, "validator unavailable").
Does not retry. Callers use the AgentResult.validated field to decide.
"""

import logging

log = logging.getLogger("fantoma.validator")

_SYSTEM = """\
You are checking whether a browser-task answer satisfies the original task.

Reply on exactly two lines:
Line 1: YES or NO
Line 2: One sentence — what the answer provides and why it does or does not satisfy the task.\
"""


def validate_answer(task: str, answer: str, llm) -> tuple[bool, str]:
    """Return (passed, reason). Fails open on any LLM error."""
    if not (answer and answer.strip()):
        return False, "Answer is empty"

    messages = [
        {"role": "system", "content": _SYSTEM},
        {"role": "user", "content": f"Task: {task}\n\nAnswer: {answer}"},
    ]
    try:
        raw = llm.chat(messages, max_tokens=80)
        if not raw:
            return True, "validator unavailable"
        lines = [l.strip() for l in raw.strip().split("\n") if l.strip()]
        verdict = lines[0].upper() if lines else ""
        reason = lines[1] if len(lines) > 1 else ""
        passed = verdict.startswith("YES")
        log.info("Validator: %s — %s", "PASS" if passed else "FAIL", reason)
        return passed, reason
    except Exception as e:
        log.warning("Validator LLM call failed (%s) — failing open", e)
        return True, "validator unavailable"
