"""Do the common steps of a task in code, before any model is asked.

A small model is the least reliable part of the agent. Measured on GitHub's
machines with qwen2.5 7B and 14B: both typed into the right box, picked the
right dropdown and logged in when Fantoma was driven directly, and both
failed the same three tasks when the model chose the steps, each time
reporting success. The tasks were not hard. They were phrased in a way code
can read exactly: "Select 'Option 2'", "Type the number 42 into the input
box", "Log in with username 'x' and password 'y'".

So the task is split into clauses and each clause is matched against a
short list of patterns. A clause is carried out here only when it is
unambiguous on the live page (one dropdown offers that option, one field
fits, one button belongs to that item) and the page confirms the change
afterwards. Anything else, and everything after it, is left to the model.
A clause this module does not understand is not an error; the agent simply
runs as it did before.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field

from fantoma.task_spec import _quoted_spans, _value_key

log = logging.getLogger("fantoma.fast_path")

_QUOTE = r"['\"‘“](.+?)['\"’”]"
# Clause boundaries: "..., then ...", "... and then ...", "... after that ...".
# "and" alone splits only before a question, so "open 'Travel' and tell me
# the first title" becomes a click plus a question for the model, while
# "type 'x' and press Enter" stays one clause.
_SPLIT = re.compile(
    r"\s*(?:,\s*)?\b(?:and then|then|after that|afterwards"
    r"|and(?=\s+(?:tell|report|give|return|list|show|say|find out|what|which|who|how)\b))\b[,:]?\s*",
    re.I)
# What may trail a clause without changing its meaning.
_TAIL = r"(?:\s+(?:on|in) (?:this|the) (?:page|form|site))?(?:\s*[.!])?\s*$"

_EDITABLE = ("textbox", "searchbox", "spinbutton")
_CLICKABLE = ("button", "link", "tab", "menuitem")
_ORDINALS = {"first": 0, "1st": 0, "second": 1, "2nd": 1, "third": 2, "3rd": 2,
             "fourth": 3, "4th": 3, "fifth": 4, "5th": 4, "last": -1}


@dataclass
class FastResult:
    """What was done in code, and what is left for the model."""

    steps: list = field(default_factory=list)   # steps_detail entries
    done: list = field(default_factory=list)    # plain descriptions
    remainder: str = ""                         # clauses not handled, in order
    failed: str = ""                            # why a clause stopped the run, if it did

    @property
    def complete(self) -> bool:
        return bool(self.done) and not self.remainder and not self.failed


def split_clauses(task: str) -> list[str]:
    parts = [p.strip(" ,.;") for p in _SPLIT.split(task or "")]
    return [p for p in parts if p]


def run_fast_path(task: str, fantoma, secrets: dict = None) -> FastResult:
    """Carry out the leading clauses of `task` that code can do reliably.

    Stops at the first clause it cannot do; that clause and the rest become
    `remainder`. Never raises.

    secrets: the agent's sensitive_data. A task may say password
    '<secret:pw>'; the real value is typed, and only the placeholder ever
    appears in the steps, the descriptions or anything shown to a model.
    """
    out = FastResult()
    fantoma = _Revealing(fantoma, secrets or {})
    clauses = split_clauses(task)
    for i, clause in enumerate(clauses):
        try:
            handled = _do_clause(clause, fantoma, out)
        except Exception as e:  # a shortcut must never break a run
            log.info("Fast path stopped on %r: %s", clause, e)
            handled = False
        if not handled:
            out.remainder = ", then ".join(clauses[i:])
            break
    if out.done:
        log.info("Fast path did %d step(s) without the model: %s",
                 len(out.done), "; ".join(out.done))
    return out


# ── clause handlers ─────────────────────────────────────────────


# A handler returns None when the clause is not its kind, and _UNSURE when
# it is but the page does not settle which control to use: nothing matched
# yet (a search box that renders late) or several did.
_UNSURE = "unsure"
_SETTLE_S = 2.0


def _do_clause(clause: str, fantoma, out: FastResult) -> bool:
    for attempt in range(2):
        result = _match(clause, fantoma)
        if result != _UNSURE:
            break
        if attempt == 0:
            # Measured live: Etsy's search box was not in the page yet when
            # the check ran straight after navigating. One short wait, then
            # the page gets one more look before the model is asked.
            time.sleep(_SETTLE_S)
    if result is None or result == _UNSURE:
        return False
    ok, desc, action = result
    out.steps.append({"step": len(out.steps) + 1, "action": f"fast:{action}",
                      "success": ok, "url": _url(fantoma)})
    if ok:
        out.done.append(desc)
        return True
    out.failed = desc
    return False


def _match(clause: str, fantoma):
    unsure = False
    for handler in (_login, _search, _select, _type, _checkbox, _add_to_cart, _click):
        result = handler(clause, fantoma)
        if result == _UNSURE:
            unsure = True
            continue
        if result is None:
            continue           # not this kind of clause
        return result
    return _UNSURE if unsure else None


def _login(clause, fantoma):
    low = clause.lower()
    if not re.search(r"\b(log ?in|sign ?in)\b", low):
        return None
    creds = {}
    for quote, lead in _quoted_spans(clause):
        key = _value_key(lead)
        if key:
            creds.setdefault(key, quote)
    # Only a clause that is nothing but the login and its credentials.
    rest = re.sub(_QUOTE, "", low)
    rest = re.sub(r"\b(log ?in|sign ?in|with|and|the|username|user name|user|email|"
                  r"e-mail|password|as|using|to|this|site|page|account|my|your|on)\b|[,.:]",
                  " ", rest)
    if rest.strip() or "password" not in creds or not (creds.get("username") or creds.get("email")):
        return None
    page = fantoma._engine.get_page()
    before = page.url
    result = fantoma.login(before, username=creds.get("username", ""),
                           email=creds.get("email", ""), password=creds["password"])
    page = fantoma._engine.get_page()
    still_form = _password_box_visible(page)
    ok = bool(result.get("success")) and not still_form
    if ok:
        return True, "logged in", "login"
    why = "the login form is still showing" if still_form else "login did not complete"
    return False, f"login failed: {why}", "login"


def _search(clause, fantoma):
    m = re.match(r"^search\s+(?:for\s+)?" + _QUOTE +
                 r"(?:\s+(?:on|in|using)\s+(?:this|the)\s+(?:site|page|search box))?" + _TAIL,
                 clause, re.I)
    if not m:
        return None
    query = m.group(1)
    els = _elements(fantoma)
    # A search field: a searchbox, or a text field / free-text combobox
    # (not a <select>) whose name says search.
    fields = [i for i, e in enumerate(els) if e.get("role") == "searchbox"]
    if len(fields) != 1:
        fields = [i for i, e in enumerate(els)
                  if e.get("role") in ("textbox", "combobox") and not e.get("_options")
                  and "search" in f"{e.get('name', '')} {e.get('_context', '')}".lower()]
    if len(fields) != 1:
        return _UNSURE
    before = _url(fantoma)
    fantoma.type_text(fields[0], query)
    fantoma.press_key("Enter")
    for _ in range(10):
        if _url(fantoma) != before:
            break
        time.sleep(0.5)
    after = _url(fantoma)
    if after != before:
        return True, f"searched for {query!r} (now at {after})", f"search({query!r})"
    return False, f"searched for {query!r} but the page did not change", f"search({query!r})"


def _select(clause, fantoma):
    m = re.match(r"^(?:select|choose|pick)\s+" + _QUOTE +
                 r"(?:\s+(?:from|in)\s+the\s+(?:dropdown|drop-down|select|list|menu)(?:\s+list)?)?" + _TAIL,
                 clause, re.I)
    if not m:
        return None
    want = m.group(1).strip().lower()
    els = _elements(fantoma)
    boxes = [i for i, e in enumerate(els) if e.get("role") == "combobox"
             and any((o or "").strip().lower() == want for o in e.get("_options") or [])]
    if len(boxes) != 1:
        return _UNSURE            # none or several dropdowns offer it: the model decides
    option = next(o for o in els[boxes[0]]["_options"] if o.strip().lower() == want)
    fantoma.select(boxes[0], option)
    after = [e for e in _elements(fantoma) if e.get("role") == "combobox"
             and (e.get("_selected") or "").strip().lower() == want]
    if after:
        return True, f"selected {option!r}", f"select({option!r})"
    return False, f"could not select {option!r}", f"select({option!r})"


def _type(clause, fantoma):
    m = re.match(
        r"^(?:type|enter|fill in|write|input)\s+(?:the\s+(?:number|text|value|word|name|code)\s+)?"
        r"(?:" + _QUOTE + r"|(\S+))"
        r"(?:\s+(?:in|into|in to)\s+(?:the\s+)?(.+?))?"
        r"(?P<enter>\s+and\s+(?:press|hit)\s+enter)?" + _TAIL,
        clause, re.I)
    if not m:
        return None
    text = m.group(1) if m.group(1) is not None else m.group(2)
    hint = (m.group(3) or "").lower()
    hint = re.sub(r"\b(the|a|an|input|box|field|text ?box|search ?box|area|bar)\b", " ", hint).strip()
    els = _elements(fantoma)
    fields = [i for i, e in enumerate(els) if e.get("role") in _EDITABLE]
    if hint:
        named = [i for i in fields
                 if hint in f"{els[i].get('name', '')} {els[i].get('_context', '')}".lower()]
        if len(named) == 1:
            fields = named
    if len(fields) != 1:
        return _UNSURE
    idx = fields[0]
    label = els[idx].get("name") or els[idx].get("_context") or els[idx].get("role")
    fantoma.type_text(idx, text)
    if m.group("enter"):
        fantoma.press_key("Enter")
        return True, f"typed {text!r} into {label!r} and pressed Enter", f"type({text!r})+Enter"
    now = _field_value(fantoma, els[idx])
    if now == fantoma.reveal(text) or (now and set(now) == {"•"}):
        return True, f"typed {text!r} into {label!r}", f"type({text!r})"
    return False, f"typed {text!r} but the field shows {now!r}", f"type({text!r})"


def _checkbox(clause, fantoma):
    m = re.match(
        r"^(tick|check|uncheck|untick|clear)\s+(?:the\s+)?"
        r"(?:(first|second|third|fourth|fifth|last|1st|2nd|3rd|4th|5th)\s+)?"
        r"(?:" + _QUOTE + r"\s+)?(?:check ?box|tick ?box|box|option)"
        r"(?:\s+(?:labelled|labeled|named|called)\s+" + _QUOTE + r")?"
        r"(?:\s+on (?:this|the) page)?"
        r"(?:,?\s*if it is(?:n't| not) already (?:ticked|checked|selected|unticked|unchecked|cleared))?"
        r"(?:\s*[.!])?\s*$", clause, re.I)
    if not m:
        return None
    want_on = m.group(1).lower() in ("tick", "check")
    ordinal = (m.group(2) or "").lower()
    label = (m.group(3) or m.group(4) or "").lower()
    els = _elements(fantoma)
    boxes = [i for i, e in enumerate(els) if e.get("role") in ("checkbox", "switch")]
    if label:
        boxes = [i for i in boxes
                 if label in f"{els[i].get('name', '')} {els[i].get('_context', '')}".lower()]
        if len(boxes) != 1:
            return _UNSURE
        pick = boxes[0]
    elif ordinal:
        # Positions only mean something among boxes that look alike; the
        # element list is ranked, so _ordinal (page order within one name)
        # is what "first" refers to.
        if len({els[i].get("name", "") for i in boxes}) != 1:
            return _UNSURE
        ordered = sorted(boxes, key=lambda i: els[i].get("_ordinal", 0))
        pos = _ORDINALS[ordinal]
        if pos >= len(ordered):
            return _UNSURE
        pick = ordered[pos]
    elif len(boxes) == 1:
        pick = boxes[0]
    else:
        return _UNSURE
    key = (els[pick].get("role"), els[pick].get("name", ""), els[pick].get("_ordinal", 0))
    what = "ticked" if want_on else "cleared"
    if _is_checked(els[pick]) != want_on:
        fantoma.click(pick)
    now = next((e for e in _elements(fantoma)
                if (e.get("role"), e.get("name", ""), e.get("_ordinal", 0)) == key), None)
    if now is not None and _is_checked(now) == want_on:
        return True, f"{what} the checkbox", f"checkbox({what})"
    return False, f"the checkbox was not {what}", f"checkbox({what})"


def _add_to_cart(clause, fantoma):
    m = re.match(r"^add\s+(?:the\s+|a\s+|one\s+)?" + _QUOTE +
                 r"\s+to\s+(?:the\s+|my\s+|your\s+)?(cart|basket|bag|trolley)" + _TAIL, clause, re.I)
    if not m:
        return None
    want = m.group(1).strip().lower()
    add = re.compile(r"add\s+to\s+(cart|basket|bag|trolley)", re.I)

    def buttons():
        els = _elements(fantoma)
        return els, [i for i, e in enumerate(els) if e.get("role") == "button"
                     and add.search(e.get("name", ""))
                     and want in f"{e.get('name', '')} {e.get('_context', '')}".lower()]

    els, found = buttons()
    if len(found) != 1:
        found = _add_button_in_card(fantoma, els, want)
    if len(found) != 1:
        return _UNSURE
    fantoma.click(found[0])
    time.sleep(0.5)
    els, still = buttons()
    still = still or _add_button_in_card(fantoma, els, want)
    if not still:
        return True, f"added {m.group(1)!r} to the {m.group(2).lower()}", f"add_to_cart({m.group(1)!r})"
    return False, f"{m.group(1)!r} still shows an add button", f"add_to_cart({m.group(1)!r})"


# From each element whose own text is the product name, walk up to the
# smallest ancestor holding exactly one add button: that is the product's
# card. Returns the button's name and its position among same-named buttons,
# which is how the element list tells identical buttons apart.
_CARD_JS = """(want) => {
  const add = /add\\s+to\\s+(cart|basket|bag|trolley)/i;
  const name = b => (b.getAttribute('aria-label') || b.innerText || b.value || '').trim();
  const buttons = [...document.querySelectorAll(
      'button, [role=button], input[type=submit], input[type=button], a')]
    .filter(b => add.test(name(b)) && b.getClientRects().length);
  const hits = new Set();
  for (const el of document.querySelectorAll('body *')) {
    if ((el.innerText || '').trim().toLowerCase() !== want) continue;
    for (let a = el.parentElement; a && a !== document.body; a = a.parentElement) {
      const inside = buttons.filter(b => a.contains(b));
      if (inside.length === 1) { hits.add(inside[0]); break; }
      if (inside.length > 1) break;
    }
  }
  if (hits.size !== 1) return null;
  const b = [...hits][0];
  return {name: name(b), ordinal: buttons.filter(x => name(x) === name(b)).indexOf(b)};
}"""


def _add_button_in_card(fantoma, els, want):
    """The add button inside the named product's card, as an element index.

    Used when the button's inferred label is not the product (a price or a
    rating sits between them, as on many shops).
    """
    try:
        hit = fantoma._engine.get_page().evaluate(_CARD_JS, want)
    except Exception:
        return []
    if not hit:
        return []
    return [i for i, e in enumerate(els) if e.get("role") == "button"
            and e.get("name", "") == hit["name"] and e.get("_ordinal", 0) == hit["ordinal"]]


def _click(clause, fantoma):
    m = re.match(r"^(?:click|press|tap|open|go to)\s+(?:on\s+)?(?:the\s+)?" + _QUOTE +
                 r"(?:\s+(?:button|link|tab|category|page|section|menu|item))?" + _TAIL,
                 clause, re.I)
    if not m:
        return None
    want = m.group(1).strip().lower()
    els = _elements(fantoma)
    hits = [i for i, e in enumerate(els) if e.get("role") in _CLICKABLE
            and (e.get("name") or "").strip().lower() == want]
    if len(hits) != 1:
        return _UNSURE
    before = _url(fantoma)
    r = fantoma.click(hits[0])
    if r.get("success"):
        where = _url(fantoma)
        moved = f" (now at {where})" if where != before else ""
        return True, f"clicked {m.group(1)!r}{moved}", f"click({m.group(1)!r})"
    return False, f"could not click {m.group(1)!r}", f"click({m.group(1)!r})"


# ── page helpers ────────────────────────────────────────────────


class _Revealing:
    """The browser, with <secret:name> placeholders filled in as it types."""

    def __init__(self, fantoma, secrets):
        self._f = fantoma
        self._secrets = secrets

    def __getattr__(self, name):
        return getattr(self._f, name)

    def reveal(self, text: str) -> str:
        for name, value in self._secrets.items():
            text = text.replace(f"<secret:{name}>", value)
        return text

    def type_text(self, element_id, text):
        return self._f.type_text(element_id, self.reveal(text))

    def login(self, url, **creds):
        return self._f.login(url, **{k: self.reveal(v) for k, v in creds.items()})


def _elements(fantoma) -> list:
    fantoma.get_state(task="")
    return list(fantoma._dom._last_interactive)


def _url(fantoma) -> str:
    try:
        return fantoma._engine.get_page().url
    except Exception:
        return ""


def _is_checked(el: dict) -> bool:
    return bool((el.get("raw") or {}).get("checked")) or "checked" in (el.get("state") or "")


def _field_value(fantoma, el: dict) -> str:
    for e in _elements(fantoma):
        if (e.get("role"), e.get("name", ""), e.get("_ordinal", 0)) == \
                (el.get("role"), el.get("name", ""), el.get("_ordinal", 0)):
            return (e.get("raw") or {}).get("value", "")
    return ""


def _password_box_visible(page) -> bool:
    try:
        return bool(page.evaluate(
            "() => [...document.querySelectorAll('input[type=password]')]"
            ".some(e => { const r = e.getBoundingClientRect();"
            " return r.width > 0 && r.height > 0 && getComputedStyle(e).visibility !== 'hidden'; })"))
    except Exception:
        return False
