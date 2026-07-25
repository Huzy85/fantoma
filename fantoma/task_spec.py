"""Turn a free-text task into a small structured record.

A sentence like "log in with username 'x' and password 'y', then add the
'Sauce Labs Fleece Jacket' to the cart" asks a model to do four things at
once: parse the intent, hold the target in mind, scan a list of controls,
and match a hint it was told about in a rules paragraph. Small models drop
the thread at every one of those, which is measurable: asked for the Fleece
Jacket, the agent added the first product on the page and reported success.

Parsing the sentence once, into fields, changes the per-step question from
"what am I trying to do" to "which control names Sauce Labs Fleece Jacket".
That is matching rather than reasoning, and small models are far better at
it.

The second use matters more. A parsed target gives something to check the
world against once the run ends, so "did it work" stops being a question
answered by the agent's own prose. Every failure found while building this
was invisible for exactly that reason: success=True with the wrong item in
the basket.

Extraction is deterministic. Quoted text is the target in nearly every task
a person writes, and code is more reliable than a weak model at finding it.
An LLM is only a fallback for sentences that carry no quotes, and even then
its answer is checked against the sentence before it is trusted.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

log = logging.getLogger("fantoma.task_spec")

# Action families, most specific first — "add to cart" must beat "add".
_ACTIONS: list[tuple[str, tuple[str, ...]]] = [
    ("add_to_cart", ("add to cart", "add to basket", "put in the cart",
                     "add it to the cart", "to the cart", "to the basket")),
    ("checkout", ("checkout", "check out", "place the order", "proceed to pay")),
    ("login", ("log in", "login", "sign in", "signin")),
    ("register", ("sign up", "signup", "register", "create an account")),
    ("subscribe", ("subscribe", "sign up to the newsletter")),
    ("search", ("search for", "search")),
    ("submit", ("submit", "send the form")),
    ("select", ("select", "choose", "pick")),
    ("click", ("click", "press", "tap")),
    ("extract", ("what is", "tell me", "how many", "find out", "report",
                 "summarise", "summarize", "who is", "when is")),
]

# Credential-ish labels whose quoted values are inputs, never targets.
_VALUE_LABELS = ("username", "user name", "password", "email", "e-mail",
                 "login", "user", "pass", "code", "otp")


@dataclass
class TaskSpec:
    """What the task is trying to do, in fields rather than prose."""

    raw: str
    action: str = ""
    target: str = ""
    values: dict = field(default_factory=dict)
    confident: bool = False

    @property
    def is_state_changing(self) -> bool:
        return self.action not in ("", "extract")

    def as_prompt_lines(self) -> str:
        """The fields as a short block for a navigator prompt.

        Deliberately terse. The point of parsing was to stop asking a small
        model to hold a sentence in mind.
        """
        lines = []
        if self.action:
            lines.append(f"ACTION: {self.action}")
        if self.target:
            lines.append(f"TARGET: {self.target}")
        for k, v in self.values.items():
            lines.append(f"{k.upper()}: {v}")
        return "\n".join(lines)


def _quoted_spans(text: str) -> list[tuple[str, str]]:
    """Quoted spans with the words that introduce each one.

    The introducing words are bounded by the end of the previous quote, not
    a fixed window. A fixed window reached back past the previous quote and
    stole its label, so "password 'x', then add the 'Widget'" read Widget as
    a password.
    """
    out = []
    prev_end = 0
    for m in re.finditer(r"(['\"‘“])(.+?)(['\"’”])", text):
        lead = text[prev_end:m.start()].lower()
        out.append((m.group(2), lead))
        prev_end = m.end()
    return out


def _value_key(lead: str) -> str | None:
    """Which credential field this quote belongs to, if any.

    Picks the label NEAREST the quote. Scanning in list order matched
    "username" from an earlier clause and dropped the password entirely.
    """
    best, best_pos = None, -1
    for label in _VALUE_LABELS:
        pos = lead.rfind(label)
        if pos > best_pos:
            best, best_pos = label, pos
    if best_pos < 0:
        return None
    return "password" if "pass" in best else (
        "email" if "mail" in best else "username")


def parse_task(task: str, llm=None) -> TaskSpec:
    """Parse a task into fields. Never raises; falls back to a bare spec.

    An unparsed task is not an error — it just means the agent behaves as it
    did before, so this can never make a working task worse.
    """
    spec = TaskSpec(raw=task or "")
    if not isinstance(task, str) or not task:
        return spec

    low = task.lower()

    for name, phrases in _ACTIONS:
        if any(p in low for p in phrases):
            spec.action = name
            break

    # Quoted spans: those introduced by a credential label are values, the
    # rest are candidate targets.
    for quote, lead in _quoted_spans(task):
        key = _value_key(lead)
        if key:
            spec.values.setdefault(key, quote)
        elif not spec.target:
            spec.target = quote

    if spec.target or spec.values:
        spec.confident = True

    # Only ask a model when code found nothing to go on, and only for the
    # target — actions are a closed set and matching them in code is exact.
    if not spec.target and llm is not None and spec.is_state_changing:
        guess = _llm_target(task, llm)
        if guess:
            spec.target = guess

    return spec


def _llm_target(task: str, llm) -> str:
    """One short call for the target when no quoted span exists.

    The answer is only accepted if it actually appears in the task, which
    stops a model inventing a plausible product that was never asked for.
    """
    try:
        reply = llm.chat(
            [
                {"role": "system", "content":
                 "Reply with ONLY the name of the specific item the task acts "
                 "on, copied exactly from the task. No quotes, no explanation. "
                 "If there is no specific named item, reply NONE."},
                {"role": "user", "content": task},
            ],
            max_tokens=40,
        )
    except Exception as e:
        log.debug("Target extraction failed: %s", e)
        return ""

    if not isinstance(reply, str):
        return ""   # a stubbed or misbehaving client must not break parsing
    guess = reply.strip().strip("'\"")
    if not guess or guess.upper() == "NONE":
        return ""
    if guess.lower() not in task.lower():
        log.info("Ignoring invented target %r — not present in the task", guess)
        return ""
    return guess


def verify_outcome(spec: TaskSpec, end_url: str, end_page: str) -> tuple[bool, str]:
    """Check the finished page against the spec.

    This is the half that matters. Without it, "did it work" is answered by
    the agent describing its own work, which is how a run that added the
    wrong item still reported success.

    Returns (ok, reason). What cannot be judged is reported as ok — a spec
    that could not be parsed must never fail a run that actually worked.
    """
    url = (end_url or "").lower()
    lines = (end_page or "").split("\n")
    page_low = (end_page or "").lower()
    target = (spec.target or "").lower()

    if spec.action in ("login", "register"):
        # Judged by where it landed, so this works with no target at all.
        if any(w in url for w in
               ("secure", "inventory", "account", "dashboard", "profile")):
            return True, "reached a post-login page"
        if "login" in url or "signin" in url or "register" in url:
            return False, "still on the sign-in page"
        return True, "left the sign-in page"

    if not spec.confident or not target:
        return True, "no target to verify"

    if spec.action == "add_to_cart":
        # A control still offering to ADD the target proves it is not in the
        # cart, whatever the agent says. Checked first because the target
        # name appears in that line too, and a bare substring search would
        # read it as evidence of success.
        for line in lines:
            low = line.lower()
            if target in low and "add to cart" in low:
                return False, f"{spec.target} still shows an add-to-cart control"
        # Otherwise the item's own line should sit beside a remove control.
        for i, line in enumerate(lines):
            if target in line.lower():
                near = " ".join(lines[i:i + 3]).lower()
                if "remove" in near:
                    return True, f"{spec.target} sits with a remove control"
        return False, f"no evidence {spec.target} was added"

    if target in page_low:
        return True, f"{spec.target} present on the final page"
    return False, f"{spec.target} not found on the final page"
