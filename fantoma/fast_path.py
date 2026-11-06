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

from fantoma.secrets_scope import SecretStore, refusal_message
from fantoma.task_spec import _quoted_spans, _value_key

log = logging.getLogger("fantoma.fast_path")

_QUOTE = r"['\"‘“](.+?)['\"’”]"
# Clause boundaries: "..., then ...", "... and then ...", "... after that ...".
# "and" alone splits only before a question, so "open 'Travel' and tell me
# the first title" becomes a click plus a question for the model, while
# "type 'x' and press Enter" stays one clause.
# "..., add ..." and "... and click ..." also start a new step when what
# follows is an imperative: the 2026-11-05 benchmark tasks are written that
# way ("log in ..., add the 'X' to the cart, open the cart, and click
# Checkout") and none of them reached the fast path. "press Enter" is kept
# inside its clause. A full stop ends a clause too.
_VERB = (r"add|open|click|tap|go to|navigate|visit|select|choose|pick|type|enter|fill (?:in|out)|write|input"
         r"|tick|check|uncheck|untick|clear|search|log ?in|sign ?in|remove|delete|sort|register|sign up"
         r"|accept|dismiss|confirm|wait|scroll|submit"
         r"|press(?!\s+(?:the\s+)?(?:enter|return)\b)|hit(?!\s+(?:the\s+)?(?:enter|return)\b)")
_QWORDS = r"tell|report|give|return|list|show|say|find out|what|which|who|how"
_SPLIT = re.compile(
    r"(?:\s*(?:,\s*)?\b(?:and then|then|after that|afterwards"
    r"|and(?=\s+(?:" + _QWORDS + r")\b)|and(?=\s+(?:" + _VERB + r")\b))\b[,:]?\s*)"
    r"|(?:,\s*(?=(?:" + _VERB + r")\b))"
    r"|(?:(?<=[.!?])\s+(?=\S))",
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
    # Quoted text is masked first so a comma, a verb or a full stop inside
    # a product name or a password never splits a clause.
    spans: list[str] = []

    def mask(m):
        spans.append(m.group(0))
        return f"\x00{len(spans) - 1}\x00"

    masked = re.sub(_QUOTE, mask, task or "")
    out = []
    for part in _SPLIT.split(masked):
        part = re.sub(r"\x00(\d+)\x00", lambda m: spans[int(m.group(1))], part or "")
        part = part.strip(" ,.;")
        if part:
            out.append(part)
    return out


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
            if not out.failed:
                try:
                    _open_for_question(clause, fantoma, out)
                except Exception as e:
                    log.info("Could not open the item for %r: %s", clause, e)
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
        log.info("Fast path leaves %r to the model: %s", clause,
                 "no handler for this sentence" if result is None else "the page does not settle which control")
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
    for handler in (_login, _register, _search, _select, _sort, _fill_fields, _type, _checkbox,
                    _add_to_cart, _remove, _wait, _dialog, _press_key, _click):
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


_FLASH_JS = """() => {
  const vis = e => e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden';
  const sel = '[role=alert],#flash,.flash,.alert,.error,.invalid-feedback,.help-block,.message';
  const el = [...document.querySelectorAll(sel)].find(e => vis(e) && e.innerText.trim());
  return el ? el.innerText.trim().slice(0, 200) : '';
}"""


def _register(clause, fantoma):
    """"Register a new account with username 'x' and password 'y', confirming
    the password if asked": the sign-up form filled and sent in code.

    The site's own verdict comes back when it refuses, so a model given what
    is left can report it instead of retyping into the same boxes.
    """
    low = clause.lower()
    if not re.match(r"^(?:register|sign ?up|create)\b", low):
        return None
    creds = {}
    for quote, lead in _quoted_spans(clause):
        key = _value_key(lead)
        if key:
            creds.setdefault(key, quote)
    rest = re.sub(_QUOTE, "", low)
    rest = re.sub(r"\b(register|sign ?up|create|for|a|an|new|account|with|and|the|username|"
                  r"user name|user|email|e-mail|password|as|using|on|this|site|page|confirming|"
                  r"confirm|repeating|repeat|it|if|asked|needed|required|then|my|your)\b|[,.:]",
                  " ", rest)
    if rest.strip() or "password" not in creds or not (creds.get("username") or creds.get("email")):
        return None
    els = _elements(fantoma)
    fields = [i for i, e in enumerate(els) if e.get("role") in _EDITABLE]

    def label(i):
        return f"{els[i].get('name', '')} {els[i].get('_context', '')}".lower()

    pw = [i for i in fields if "pass" in label(i)]
    confirm = [i for i in pw if re.search(r"confirm|again|repeat|re-?enter|retype|verify", label(i))]
    first = [i for i in pw if i not in confirm]
    if len(first) != 1 or len(confirm) > 1:
        return _UNSURE
    plan = []
    for key, want in (("username", "user"), ("email", "mail")):
        if not creds.get(key):
            continue
        hits = [i for i in fields if i not in pw and want in label(i)]
        if len(hits) != 1:
            hits = [i for i in fields if i not in pw and re.search(r"user|mail|login", label(i))]
        if len(hits) != 1:
            return _UNSURE
        plan.append((hits[0], creds[key]))
    plan.append((first[0], creds["password"]))
    if confirm:
        plan.append((confirm[0], creds["password"]))
    before = _url(fantoma)
    for idx, value in plan:
        fantoma.type_text(_num(els, idx), value)
    buttons = [i for i, e in enumerate(els) if e.get("role") == "button"
               and re.search(r"regist|sign ?up|create|submit|continue|join", (e.get("name") or "").lower())]
    if len(buttons) == 1:
        fantoma.click(_num(els, buttons[0]))
    else:
        fantoma.press_key("Enter")
    for _ in range(16):
        if _url(fantoma) != before:
            break
        time.sleep(0.5)
    page = fantoma._engine.get_page()
    after = _url(fantoma)
    who = creds.get("username") or creds.get("email")
    if after != before or not _password_box_visible(page):
        return True, f"registered {who!r} (now at {after})", "register"
    try:
        msg = page.evaluate(_FLASH_JS)
    except Exception:
        msg = ""
    return False, f"registration refused: {msg or 'the form is still showing'}", "register"


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
    fantoma.type_text(_num(els, fields[0]), query)
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
    fantoma.select(_num(els, boxes[0]), option)
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
    fantoma.type_text(_num(els, idx), text)
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
        fantoma.click(_num(els, pick))
    now = next((e for e in _elements(fantoma)
                if (e.get("role"), e.get("name", ""), e.get("_ordinal", 0)) == key), None)
    if now is not None and _is_checked(now) == want_on:
        return True, f"{what} the checkbox", f"checkbox({what})"
    return False, f"the checkbox was not {what}", f"checkbox({what})"


def _add_to_cart(clause, fantoma):
    m = re.match(r"^add\s+(?:the\s+|a\s+|one\s+)?" + _QUOTE +
                 r"(?:\s*(?:,|and)\s*(?:the\s+|a\s+)?" + _QUOTE + r")*"
                 r"\s+to\s+(?:the\s+|my\s+|your\s+)?(cart|basket|bag|trolley)" + _TAIL, clause, re.I)
    if not m:
        return None
    items = [q for q, _ in _quoted_spans(clause)]
    where = m.group(m.lastindex).lower()
    done = []
    for item in items:
        result = _add_one(item, fantoma)
        if result is not True:
            return result
        done.append(item)
        fantoma._fast_last_item = item
    names = ", ".join(repr(i) for i in done)
    return True, f"added {names} to the {where}", f"add_to_cart({names})"


def _add_one(item, fantoma):
    """Add one named product; True when done, else a failed-step tuple or _UNSURE."""
    want = item.strip().lower()
    add = re.compile(r"add\s+to\s+(cart|basket|bag|trolley)", re.I)

    def buttons():
        els = _elements(fantoma)
        return els, [i for i, e in enumerate(els) if e.get("role") == "button"
                     and add.search(e.get("name", ""))
                     and want in f"{e.get('name', '')} {e.get('_context', '')}".lower()]

    els, found = buttons()
    if len(found) != 1:
        found = _button_in_card(fantoma, els, want, _ADD_PATTERN)
    if len(found) != 1:
        return _UNSURE
    fantoma.click(_num(els, found[0]))
    time.sleep(0.5)
    els, still = buttons()
    still = still or _button_in_card(fantoma, els, want, _ADD_PATTERN)
    if not still:
        return True
    return False, f"{item!r} still shows an add button", f"add_to_cart({item!r})"


def _remove(clause, fantoma):
    m = re.match(r"^(?:remove|delete|take out)\s+(?:the\s+)?(?:" + _QUOTE + r"|it|that|them|the item)?"
                 r"(?:\s+again)?(?:\s+from\s+(?:the\s+|my\s+)?"
                 r"(?:cart|basket|bag|trolley|products?(?:\s+page)?|list|page))?" + _TAIL, clause, re.I)
    if not m:
        return None
    # "remove it again": the item the fast path just added.
    item = m.group(1) or getattr(fantoma, "_fast_last_item", "") or ""
    want = item.strip().lower()
    if not want:
        return None
    rm = re.compile(r"\bremove\b", re.I)

    def buttons():
        els = _elements(fantoma)
        return els, [i for i, e in enumerate(els) if e.get("role") == "button"
                     and rm.search(e.get("name", ""))
                     and want in f"{e.get('name', '')} {e.get('_context', '')}".lower()]

    els, found = buttons()
    if len(found) != 1:
        found = _button_in_card(fantoma, els, want, _REMOVE_PATTERN)
    if len(found) != 1:
        return _UNSURE
    fantoma.click(_num(els, found[0]))
    time.sleep(0.5)
    els, still = buttons()
    still = still or _button_in_card(fantoma, els, want, _REMOVE_PATTERN)
    if not still:
        return True, f"removed {item!r}", f"remove({item!r})"
    return False, f"{item!r} still shows a remove button", f"remove({item!r})"


# From each element whose own text is the product name, walk up to the
# smallest ancestor holding exactly one add button: that is the product's
# card. Returns the button's name and its position among same-named buttons,
# which is how the element list tells identical buttons apart.
_ADD_PATTERN = r"add\s+to\s+(cart|basket|bag|trolley)"
_REMOVE_PATTERN = r"\bremove\b"
_CARD_JS = """([want, pattern]) => {
  const add = new RegExp(pattern, 'i');
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


def _button_in_card(fantoma, els, want, pattern):
    """The matching button inside the named product's card, as an element index.

    Used when the button's inferred label is not the product (a price or a
    rating sits between them, as on many shops).
    """
    try:
        hit = fantoma._engine.get_page().evaluate(_CARD_JS, [want, pattern])
    except Exception:
        return []
    if not hit:
        return []
    return [i for i, e in enumerate(els) if e.get("role") == "button"
            and e.get("name", "") == hit["name"] and e.get("_ordinal", 0) == hit["ordinal"]]


_TIMES = {"once": 1, "twice": 2, "two times": 2, "three times": 3, "four times": 4, "five times": 5}
_KEYS = {"enter": "Enter", "return": "Enter", "escape": "Escape", "esc": "Escape", "tab": "Tab",
         "space": "Space", "backspace": "Backspace", "delete": "Delete", "up": "ArrowUp",
         "down": "ArrowDown", "left": "ArrowLeft", "right": "ArrowRight"}


def _click(clause, fantoma):
    m = re.match(
        r"^(?:click|press|tap|open|go to)\s+(?:on\s+|into\s+|in\s+)?(?:the\s+)?"
        r"(?:" + _QUOTE + r"|(?P<plain>[A-Za-z][A-Za-z0-9&'/ -]{0,40}?))"
        r"(?:\s+(?P<kind>button|link|tab|category|page|section|menu|item|box|field|icon))?"
        r"(?:\s+(?P<times>once|twice|two times|three times|four times|five times|\d+\s+times))?"
        r"(?P<enter>\s+and\s+(?:press|hit)\s+(?:the\s+)?enter(?:\s+key)?)?" + _TAIL,
        clause, re.I)
    if not m:
        return None
    quoted = m.group(1) is not None
    name = (m.group(1) if quoted else m.group("plain")).strip()
    # An unquoted name is trusted only when short: "click Checkout", "open
    # the cart". Longer phrases describe a thing rather than name it.
    if not quoted and len(name.split()) > 3:
        return None
    want = name.lower()
    kind = (m.group("kind") or "").lower()
    editable = kind in ("box", "field") or bool(m.group("enter"))
    roles = _EDITABLE if editable else _CLICKABLE

    def find():
        els = _elements(fantoma)
        cands = [i for i, e in enumerate(els) if e.get("role") in roles]
        hits = _by_name(els, cands, want, exact_only=quoted)
        if editable and not hits and len(cands) == 1 and want in ("input", "text", "search", "the"):
            hits = cands
        return els, hits

    times = (m.group("times") or "").lower()
    n = _TIMES.get(times) or (int(times.split()[0]) if times else 1)
    before = _url(fantoma)
    els, hits = find()
    if len(hits) != 1:
        return _UNSURE
    for k in range(n):
        if k:
            time.sleep(0.3)
            els, hits = find()
            if len(hits) != 1:
                return False, f"clicked {name!r} {k} time(s) but then could not find it again", f"click({name!r})"
        r = fantoma.click(_num(els, hits[0]))
        if not r.get("success"):
            return False, f"could not click {name!r}", f"click({name!r})"
    desc = f"clicked {name!r}" + (f" {n} times" if n > 1 else "")
    if m.group("enter"):
        fantoma.press_key("Enter")
        desc += " and pressed Enter"
    where = _url(fantoma)
    if where != before:
        desc += f" (now at {where})"
    return True, desc, f"click({name!r})" + (f"x{n}" if n > 1 else "")


def _by_name(els, cands, want, exact_only=False):
    """Candidates whose name is `want`; failing that, for an unquoted name,
    the one candidate whose name starts with it or contains it as a word."""
    def nm(i):
        return (els[i].get("name") or "").strip().lower()
    hits = [i for i in cands if nm(i) == want]
    if hits or exact_only:
        return hits
    hits = [i for i in cands if nm(i).startswith(want)]
    if len(hits) == 1:
        return hits
    hits = [i for i in cands if re.search(r"\b" + re.escape(want) + r"\b", nm(i))]
    return hits if len(hits) == 1 else []


def _press_key(clause, fantoma):
    m = re.match(r"^(?:press|hit)\s+(?:the\s+)?(enter|return|escape|esc|tab|space|backspace|delete"
                 r"|(?:arrow\s*)?(?:up|down|left|right))(?:\s+(?:key|arrow))?" + _TAIL, clause, re.I)
    if not m:
        return None
    word = re.sub(r"^arrow\s*", "", m.group(1).lower())
    key = _KEYS[word]
    r = fantoma.press_key(key)
    if r.get("success", True):
        return True, f"pressed {key}", f"press({key})"
    return False, f"could not press {key}", f"press({key})"


_FILL_LEAD = re.compile(r"^(?:enter|type|fill (?:in|out)|fill|input|write|put|set)\b", re.I)
_HINT_NOISE = re.compile(
    r"\b(?:enter|type|fill (?:in|out)|fill|input|write|put|set|the|a|an|and|with|as|in|into|to"
    r"|field|box|value|of|your|my|then)\b|[,:]", re.I)


def _fill_fields(clause, fantoma):
    """\"enter first name 'Fan', last name 'Toma', postal code '12345'\".

    Each quoted value is introduced by the words that name its field. A
    value with no such words is _type's business ("type 'x' into the box").
    """
    if not _FILL_LEAD.match(clause):
        return None
    spans = _quoted_spans(clause)
    if not spans:
        return None
    pairs = []
    for quote, lead in spans:
        hint = re.sub(r"\s+", " ", _HINT_NOISE.sub(" ", lead)).strip().lower()
        if not hint:
            return None
        pairs.append((hint, quote))
    # Text after the last value ("into the box", "and press Enter") means
    # a different sentence shape.
    last = re.search(_QUOTE.replace("(.+?)", re.escape(pairs[-1][1])) + r"(?!.*" + _QUOTE + r")", clause, re.S)
    tail = clause[last.end():] if last else ""
    if not re.match(_TAIL, tail, re.I):
        return None
    els = _elements(fantoma)
    fields = [i for i, e in enumerate(els) if e.get("role") in _EDITABLE]
    picks = []
    for hint, value in pairs:
        words = hint.split()
        named = [i for i in fields
                 if all(w in f"{els[i].get('name', '')} {els[i].get('_context', '')}".lower() for w in words)]
        if len(named) != 1:
            return _UNSURE
        picks.append((named[0], value))
    done = []
    for idx, value in picks:
        label = els[idx].get("name") or els[idx].get("_context") or els[idx].get("role")
        fantoma.type_text(_num(els, idx), value)
        now = _field_value(fantoma, els[idx])
        if not (now == fantoma.reveal(value) or (now and set(now) == {"•"})):
            return False, f"typed {value!r} into {label!r} but the field shows {now!r}", "fill"
        done.append(f"{value!r} into {label!r}")
    return True, "entered " + ", ".join(done), f"fill({len(done)})"


def _sort(clause, fantoma):
    m = re.match(r"^sort\s+(?:the\s+)?(?:products|items|list|results|page|them)?\s*(?:by\s+)?(.+?)" + _TAIL,
                 clause, re.I)
    if not m:
        return None
    # "by price from low to high using the sort dropdown": the control is
    # named after "using"/"via"/"with the", and those words are not in any
    # option (sort-price went to the model on every run, 2026-11-05).
    wanted = re.split(r"\s+(?:using|via|with\s+the)\s+", m.group(1), 1, flags=re.I)[0]
    words = [w for w in re.findall(r"[a-z]+", wanted.lower())
             if w not in ("from", "to", "the", "by", "order", "in", "ascending", "descending", "and")]
    if not words:
        return None
    # The option must carry the words in the task's order: "low to high"
    # must not pick "high to low".
    pat = re.compile(r".*?".join(re.escape(w[:4]) for w in words))
    els = _elements(fantoma)
    hits = [(i, o) for i, e in enumerate(els) if e.get("role") == "combobox"
            for o in (e.get("_options") or []) if pat.search(o.lower())]
    if len(hits) != 1:
        return _UNSURE
    i, option = hits[0]
    fantoma.select(_num(els, i), option)
    after = [e for e in _elements(fantoma) if e.get("role") == "combobox"
             and (e.get("_selected") or "") == option]
    if after:
        return True, f"sorted by {option!r}", f"sort({option!r})"
    return False, f"could not select {option!r}", f"sort({option!r})"


_BUSY_JS = """() => {
  const vis = e => e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden'
                   && getComputedStyle(e).display !== 'none';
  const busy = [...document.querySelectorAll(
      '[id*=loading i],[class*=loading i],[class*=spinner i],[aria-busy=true],progress')].some(vis);
  const text = document.body ? document.body.innerText : '';
  return [busy, text.length + '|' + document.querySelectorAll('*').length];
}"""


def _wait(clause, fantoma):
    """\"wait for the loading to finish\": until nothing says loading and the
    page has stopped changing, at most 20 seconds."""
    if not re.match(r"^wait\b", clause, re.I):
        return None
    page = fantoma._engine.get_page()
    t0 = time.time()
    last, stable = None, 0
    while time.time() - t0 < 20:
        try:
            busy, sig = page.evaluate(_BUSY_JS)
        except Exception:
            busy, sig = False, None
        stable = stable + 1 if (sig == last and not busy) else 0
        last = sig
        if stable >= 3:
            break
        time.sleep(0.5)
    return True, f"waited {time.time() - t0:.1f}s for the page to settle", "wait"


def _dialog(clause, fantoma):
    """\"accept the dialog that pops up\": the engine answered it as the task
    said (the agent set the policy from the task); confirm it happened."""
    m = re.match(r"^(?:accept|confirm|dismiss|cancel|close|press ok on|click ok on)\s+(?:the\s+|any\s+)?"
                 r"(?:(?:confirm(?:ation)?|js|javascript|browser)\s+)?"
                 r"(?:dialog|dialogue|alert|pop-?up|prompt|box|confirm)\b", clause, re.I)
    if not m:
        return None
    want = "dismissed" if re.match(r"^(?:dismiss|cancel|close)", clause, re.I) else "accepted"
    engine = fantoma._engine
    hist = []
    for _ in range(6):
        hist = list(getattr(engine, "dialog_history", []) or [])
        if hist:
            break
        time.sleep(0.5)
    if not hist:
        return None             # nothing popped up; the model takes it from here
    last = hist[-1]
    kind = last.get("type", "dialog")
    if last.get("answer") == want:
        return True, f"{want} the {kind} dialog", f"dialog({want})"
    return False, f"the {kind} dialog was {last.get('answer')}, not {want}", f"dialog({want})"


# ── page helpers ────────────────────────────────────────────────


class _Revealing:
    """The browser, with <secret:name> placeholders filled in as it types.

    The page URL is read at the moment of filling. A scoped secret on the
    wrong site raises SecretRefused, which the clause loop reports as a
    failed step; the value is never typed.
    """

    def __init__(self, fantoma, secrets):
        self._f = fantoma
        self._store = SecretStore.from_any(secrets)

    def __getattr__(self, name):
        return getattr(self._f, name)

    def reveal(self, text: str) -> str:
        text, refused = self._store.reveal(text, _url(self._f))
        if refused:
            raise SecretRefused(refusal_message(refused))
        return text

    def type_text(self, element_id, text):
        return self._f.type_text(element_id, self.reveal(text))

    def login(self, url, **creds):
        # Placeholders go down as they are; each is filled in at the moment of
        # typing, after the page URL is read again, so a redirect between the
        # username and password steps is caught.
        return self._f.login(url, value_guard=self.reveal, **creds)


class SecretRefused(Exception):
    """A secret was asked for on a site it is not scoped to."""


_QUESTION = re.compile(r"^(?:what|what's|how|when|who|which|where|tell|give|show|list|find out|report)\b", re.I)


def _open_for_question(clause, fantoma, out: FastResult) -> None:
    """Open the item a question names, then leave the question to the model.

    Measured live: asked for the price of 'A Light in the Attic' on a shop
    whose home page links to it, a 7B model spent fifteen actions searching
    a site with no search box before reaching the book's page. When the
    question quotes a name and exactly one destination on the page carries
    that name, going there is not a judgement call.
    """
    if not _QUESTION.match(clause):
        return
    names = [q.strip().lower() for q, _ in _quoted_spans(clause)]
    if len(names) != 1:
        return
    els = _elements(fantoma)
    hits = [i for i, e in enumerate(els) if e.get("role") == "link"
            and (e.get("name") or "").strip().lower() == names[0]]
    # The same item is often linked twice (its picture and its title).
    if not hits or len({els[i].get("_url") or i for i in hits}) != 1:
        return
    before = _url(fantoma)
    r = fantoma.click(_num(els, hits[0]))
    if r.get("success") and _url(fantoma) != before:
        out.steps.append({"step": len(out.steps) + 1, "action": f"fast:open({names[0]!r})",
                          "success": True, "url": _url(fantoma)})
        out.done.append(f"opened the page for {els[hits[0]]['name']!r}")


def _num(els: list, i: int):
    """The element number to act on for position i in els.

    Numbers are stable across reads and no longer equal list positions.
    """
    return els[i].get("index", i)


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
