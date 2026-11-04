"""Stable element numbers.

The element list shows each control as [N]. Numbers used to be positions in
the list, handed out afresh on every read, so [12] could point at a different
element once the page changed between the model's read and its action.

Here every listed element gets a Ref: role, accessible name, its index among
elements with the same role and name, how many look-alikes there were, and
the link target and item label when the page provides them. The number stays
with that identity across reads within a task:

* a control that is still present keeps its number, wherever it now sits;
* a new control gets a new number;
* a number is never reused for a different control.

Acting on [N] resolves the identity against the live page (see
`resolve_ref`): the stored position among look-alikes if their number is
unchanged, else role and name if exactly one control matches, else a refusal.
It never guesses.

Design credit: agent-browser (Apache-2.0), cli/src/native/element.rs, which
tries the stored id first, falls back to role and name, and refuses when the
fallback is ambiguous. This is an independent implementation in Python.

On Chromium (Patchright 1.64+) `page.aria_snapshot(mode="ai")` offers its own
[ref=eN] handles, but they restart on every read, so they cannot carry a
number from one read to the next. They are not used here.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

log = logging.getLogger("fantoma.refs")

# A long task could list thousands of controls. Old entries are only needed
# while the model may still quote their number, so cap the table.
MAX_REFS = 4000


@dataclass
class Ref:
    number: int
    role: str
    name: str
    frame: str = ""
    ordinal: int = 0          # index among elements with the same role+name
    count: int | None = None  # how many such elements the page had
    href: str = ""            # link target, when the snapshot gave one
    context: str = ""         # item label for repeated controls
    page: str = ""            # origin+path of the page it was read on


def page_key(url: str) -> str:
    """Origin and path: query strings and fragments change without the page changing."""
    try:
        p = urlsplit(url or "")
        return f"{p.scheme}://{p.netloc}{p.path}"
    except Exception:
        return url or ""


def _sig(el: dict) -> tuple:
    return (el.get("role", ""), el.get("name", ""), el.get("_frame") or "")


def _extras(el: dict) -> tuple:
    return (el.get("_url") or "", el.get("_context") or "")


class RefRegistry:
    """Number -> identity table for one task."""

    def __init__(self):
        self.entries: dict[int, Ref] = {}
        self._next = 0
        self._claimed: set[int] = set()

    def reset(self) -> None:
        """Forget every number. Called when a task starts."""
        self.entries.clear()
        self._next = 0
        self._claimed.clear()

    def begin_read(self) -> None:
        self._claimed.clear()

    def get(self, number) -> Ref | None:
        return self.entries.get(number)

    # -- assignment ---------------------------------------------------------

    def assign(self, elements: list[dict], url: str = "") -> None:
        """Stamp el["index"] on each element, reusing numbers for known ones."""
        if not elements:
            return
        self._fill_ordinals(elements)
        pk = page_key(url)

        by_sig: dict[tuple, list[Ref]] = {}
        for ref in self.entries.values():
            by_sig.setdefault((ref.role, ref.name, ref.frame), []).append(ref)

        groups: dict[tuple, list[dict]] = {}
        for el in elements:
            groups.setdefault(_sig(el), []).append(el)

        matched: dict[int, Ref] = {}   # id(el) -> Ref
        for sig, group in groups.items():
            cands = [r for r in by_sig.get(sig, []) if r.number not in self._claimed]
            for el in group:
                ref = self._match(el, group, cands)
                if ref is not None:
                    matched[id(el)] = ref
                    self._claimed.add(ref.number)
                    cands = [c for c in cands if c.number != ref.number]

        for el in elements:
            ref = matched.get(id(el))
            if ref is None:
                ref = Ref(self._next, el.get("role", ""), el.get("name", ""))
                self._next += 1
                self._claimed.add(ref.number)
                self.entries[ref.number] = ref
            ref.frame = el.get("_frame") or ""
            ref.ordinal = el.get("_ordinal", 0)
            ref.count = el.get("_same")
            ref.href, ref.context = _extras(el)
            ref.page = pk
            el["index"] = ref.number
        self._trim()

    @staticmethod
    def _match(el: dict, group: list[dict], cands: list[Ref]) -> Ref | None:
        if not cands:
            return None
        same = el.get("_same")
        ordinal = el.get("_ordinal", 0)
        ex = _extras(el)
        if same == 1 or (same is None and len(group) == 1):
            # The only control of its kind. Any earlier entry for it is the
            # same control, unless several earlier entries compete.
            exact = [c for c in cands if (c.href, c.context) == ex]
            pool = exact or cands
            return pool[0] if len(pool) == 1 else None
        # Look-alikes. The same position among an unchanged number of
        # look-alikes is the same control.
        hit = [c for c in cands if c.ordinal == ordinal and c.count == same
               and (c.href, c.context) == ex]
        if hit:
            return hit[0]
        # The number of look-alikes changed. Only a label that is unique among
        # them (the link target, or the item a button belongs to) can say which
        # is which; position alone cannot.
        if any(ex) and sum(1 for g in group if _extras(g) == ex) == 1:
            hit = [c for c in cands if (c.href, c.context) == ex]
            if len(hit) == 1:
                return hit[0]
        return None

    @staticmethod
    def _fill_ordinals(elements: list[dict]) -> None:
        """Ordinal and look-alike count for elements that arrive without them
        (iframe elements). Main-frame elements are filled in by extract_aria."""
        seen: dict[tuple, int] = {}
        todo = [el for el in elements if "_same" not in el]
        for el in todo:
            s = _sig(el)
            el["_ordinal"] = seen.get(s, 0)
            seen[s] = el["_ordinal"] + 1
        for el in todo:
            el["_same"] = seen[_sig(el)]

    def _trim(self) -> None:
        extra = len(self.entries) - MAX_REFS
        if extra > 0:
            for n in sorted(self.entries)[:extra]:
                del self.entries[n]


# -- resolution against the live page -----------------------------------------

GONE = "gone"
AMBIGUOUS = "ambiguous"


def gone_message(number) -> str:
    return f"element [{number}] is gone, re-read the page"


def ambiguous_message(number) -> str:
    return (f"element [{number}] now matches several identical elements, "
            "re-read the page")


def _fold(text: str) -> str:
    return " ".join((text or "").split()).casefold()


def _folded_name(name: str):
    """Regex for an accessible name equal to `name` after case and whitespace
    folding. A substring match is not enough: "Undo delete" is not "Delete"."""
    words = [re.escape(w) for w in (name or "").split()]
    return re.compile(r"^\s*" + r"\s+".join(words) + r"\s*$", re.IGNORECASE)


# Runs in the page on one candidate. True when the item the control sits in
# carries `ctx`: the nearest preceding link (what the context label is taken
# from) or the control's own row (the ancestor just below the first one that
# also holds a look-alike).
_CONTEXT_JS = """(el, ctx) => {
  const fold = s => (s || '').replace(/\\s+/g, ' ').trim().toLowerCase();
  const want = fold(ctx);
  if (!want) return false;
  const label = n => fold(n.getAttribute('aria-label') || n.innerText || n.textContent || '');
  const links = Array.from(document.querySelectorAll('a,[role=link]'))
    .filter(l => !l.contains(el) && (l.compareDocumentPosition(el) & Node.DOCUMENT_POSITION_FOLLOWING));
  if (links.length && label(links[links.length - 1]) === want) return true;
  const twin = n => n !== el && n.tagName === el.tagName
    && fold(n.getAttribute('aria-label') || n.innerText) === fold(el.getAttribute('aria-label') || el.innerText);
  let row = el.parentElement;
  for (let i = 0; row && i < 8; i++) {
    const up = row.parentElement;
    if (!up || Array.from(up.querySelectorAll(el.tagName)).some(twin)) break;
    row = up;
  }
  return !!row && fold(row.innerText || row.textContent).includes(want);
}"""


def _candidate_matches(cand, ref: Ref) -> bool:
    """Does this live candidate still carry the stored link target and item
    label? Only the ones the ref has are checked; an error counts as no."""
    try:
        if ref.href and cand.get_attribute("href") != ref.href:
            return False
        if ref.context and not cand.evaluate(_CONTEXT_JS, ref.context):
            return False
    except Exception:
        return False
    return True


def resolve_ref(scope, ref: Ref, empty_name):
    """Find the live locator for ref. Returns (locator, status).

    status is None on success, else GONE or AMBIGUOUS. `scope` is a Page or
    Frame; `empty_name` is the regex that matches an unnamed control.

    Residual risk: a ref with neither link target nor item label (a lone
    "Remove" button with nothing to tell the rows apart) can only be found by
    position, so a same-length reorder of such controls still lands on
    whatever now sits at the stored position.
    """
    role, name = ref.role, ref.name
    saw_many = False
    extras = bool(ref.href or ref.context)
    for exact in ((True, False) if name else (False,)):
        try:
            if not name:
                loc = scope.get_by_role(role, name=empty_name)
            elif exact:
                loc = scope.get_by_role(role, name=name, exact=True)
            else:
                loc = scope.get_by_role(role, name=_folded_name(name))
            n = loc.count()
        except Exception:
            continue
        if n == 0:
            continue
        if ref.count is None:
            # No stored count (an element list set by hand): stored position.
            return (loc.nth(ref.ordinal) if 0 < ref.ordinal < n else loc.first), None
        if n == ref.count and ref.ordinal < n:
            cand = loc.nth(ref.ordinal)
            if not extras or _candidate_matches(cand, ref):
                return cand, None                  # the original, by position
        elif n == 1 and not (extras and ref.count > 1):
            return loc.first, None                 # role+name, one match
        if extras:
            same = [i for i in range(n) if _candidate_matches(loc.nth(i), ref)]
            if len(same) == 1:
                return loc.nth(same[0]), None
            return None, (AMBIGUOUS if same else GONE)
        saw_many = True
        break
    if saw_many:
        return None, AMBIGUOUS

    # Not reachable by role. Last resorts, each only when exactly one element
    # answers; more than one is a guess.
    if name:
        try:
            found = scope.query_selector_all('[aria-label="%s"]' % name.replace('"', '\\"'))
            if len(found) == 1:
                return found[0], None
        except Exception:
            pass
        try:
            loc = scope.get_by_text(name, exact=True)
            if loc.count() == 1:
                return loc.first, None
        except Exception:
            pass
    return None, GONE
