"""Accessibility-based DOM extraction — presents Fantoma as assistive technology.

Uses Playwright's ARIA snapshot instead of raw DOM traversal.
This gives cleaner, more structured data AND sites are legally
required to support it (WCAG 2.1, ADA, Equality Act 2010).

Screen readers interact with pages this way — Fantoma does the same.
"""

import logging
import re
from typing import Optional, Any

log = logging.getLogger("fantoma.accessibility")

# ARIA roles that represent interactive elements
INTERACTIVE_ROLES = {
    "button", "link", "textbox", "combobox", "searchbox",
    "checkbox", "radio", "slider", "switch", "tab",
    "menuitem", "option", "spinbutton",
}

# Form controls worth listing even with no accessible name.
#
# A bare `- checkbox` in the snapshot is a real, clickable control, and an
# unlabelled one is common: it is a WCAG 4.1.2 failure on the site's part,
# not a reason for us to pretend it is not there. Requiring a name dropped
# BOTH checkboxes on the-internet.herokuapp.com/checkboxes, so the only
# elements offered for "tick the first checkbox" were the GitHub ribbon and
# a footer link — the task was unwinnable no matter which model was driving.
#
# Deliberately excludes link/button/menuitem/option/tab: an unnamed one of
# those is usually an icon or decoration, and admitting them floods the list
# on real sites without adding anything actionable.
UNNAMED_OK_ROLES = {
    "checkbox", "radio", "textbox", "combobox", "searchbox",
    "switch", "spinbutton", "slider",
}

# Roles to skip (structural, not interactive)
SKIP_ROLES = {
    "separator", "presentation", "none", "generic",
    "paragraph", "group", "list", "listitem",
    "navigation", "main", "banner", "contentinfo",
    "complementary", "region", "article", "section",
    "img",  # Images without interaction
}

# ARIA landmark roles — tracked for parent-context grouping
LANDMARK_ROLES = {
    "form", "navigation", "region", "main", "banner",
    "contentinfo", "complementary", "search",
}

# Defaults — overridden by ExtractionConfig when available
MAX_ELEMENTS = 20
MAX_HEADINGS = 25
MAX_CONTENT_ELEMENTS = 60

# Navigation/UI noise — names that indicate chrome, not content
NAV_NOISE = {
    "close", "dismiss", "menu", "toggle", "collapse", "expand",
    "show", "hide", "previous", "next", "back", "forward",
    "notifications", "settings", "preferences", "manage",
    "create a new", "add folder", "add label",
}


def _is_nav_noise(name: str) -> bool:
    """Check if an element name is navigation/UI noise rather than content."""
    name_lower = name.lower()
    return any(noise in name_lower for noise in NAV_NOISE)


# Submit/action button patterns (boosted in pruning)
SUBMIT_PATTERNS = {
    "next", "continue", "sign in", "submit", "login",
    "search", "sign up", "register", "create", "confirm",
    "log in", "proceed", "send", "verify", "done",
}

# Stop words removed from task for keyword extraction
_STOP_WORDS = {
    "the", "a", "an", "to", "in", "on", "at", "for", "of", "and",
    "or", "is", "are", "was", "were", "be", "been", "being", "have",
    "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "this", "that", "these",
    "those", "it", "its", "i", "my", "me", "we", "our", "you", "your",
    "go", "get", "use", "find", "with", "from", "into", "then",
}


def get_scroll_info(page) -> dict | None:
    """Get viewport scroll position metrics via JavaScript.

    Returns dict with pixels_above, pixels_below, pages_above, pages_below.
    Returns None on any error (JS eval failure, headless quirks, etc.).
    """
    try:
        return page.evaluate("""() => {
            const vh = window.innerHeight;
            const ph = Math.max(document.documentElement.scrollHeight, document.body.scrollHeight || 0);
            const sy = window.scrollY || window.pageYOffset || 0;
            const below = Math.max(0, ph - (vh + sy));
            return {
                pixels_above: Math.round(sy),
                pixels_below: Math.round(below),
                pages_above: vh > 0 ? +(sy / vh).toFixed(1) : 0,
                pages_below: vh > 0 ? +(below / vh).toFixed(1) : 0,
            }
        }""")
    except Exception:
        return None


def format_scroll_hints(info: dict | None) -> tuple[str, str]:
    """Format scroll info into header/footer hint lines.

    Returns (above_hint, below_hint). Both empty strings if info is None.
    """
    if info is None:
        return "", ""

    THRESHOLD = 4

    if info["pixels_above"] <= THRESHOLD:
        above = "[Top of page]"
    else:
        above = f"... {info['pixels_above']} pixels above ({info['pages_above']} pages) - scroll up for more ..."

    if info["pixels_below"] <= THRESHOLD:
        below = "[End of page]"
    else:
        below = f"... {info['pixels_below']} pixels below ({info['pages_below']} pages) - scroll down for more ..."

    return above, below


def prune_elements(elements: list[dict], task: str = "", max_elements: int = 20) -> list[dict]:
    """Score and rank elements by relevance to the task. Returns top N.

    Scoring:
      +3  element name contains a task keyword (substring match, cumulative)
      +2  element landmark contains a task keyword
      +2  textbox/combobox/searchbox (form inputs)
      +2  name matches a submit pattern
      +1  checkbox or radio
      -2  name matches navigation noise
       0  baseline
    """
    task_lower = task.lower()
    words = task_lower.split()
    keywords = [w for w in words if w not in _STOP_WORDS and len(w) > 1]

    scored = []
    for el in elements:
        score = 0
        name_lower = el.get("name", "").lower()
        role = el.get("role", "")

        # Substring keyword matching, cumulative across all keywords.
        # The inferred context counts too: the button for a named product is
        # called "Add to cart" and matches no keyword at all, so scoring the
        # name alone leaves the one control the task is about ranked bottom.
        context_lower = (el.get("_context") or "").lower()
        for kw in keywords:
            if kw in name_lower:
                score += 3
            if context_lower and kw in context_lower:
                score += 3

        # Landmark keyword matching
        landmark = (el.get("_landmark") or "").lower()
        if landmark and keywords:
            for kw in keywords:
                if kw in landmark:
                    score += 2
                    break

        if role in ("textbox", "combobox", "searchbox"):
            score += 2

        if any(p in name_lower for p in SUBMIT_PATTERNS):
            score += 2

        if role in ("checkbox", "radio"):
            score += 1

        if _is_nav_noise(name_lower):
            score -= 2

        scored.append((score, el))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [el for _, el in scored[:max_elements]]


# How far back to look for a label when a control's name is ambiguous. Short
# on purpose: a product's own title sits immediately before its button, and a
# wider window starts pulling in unrelated text.
_CONTEXT_LOOKBACK = 4


def annotate_ambiguous(elements: list[dict]) -> list[dict]:
    """Tag repeated controls with the nearest preceding distinct label.

    A product grid gives every button the bare name "Add to cart", so the
    model has no way to say which one it means. The page ought to solve this
    with aria-label or aria-labelledby, and a bare repeated control is a WCAG
    2.4.4 failure, but real pages frequently do not — so the label has to be
    inferred here.

    The inference is deliberately marked as such when rendered. Per W3C
    AccName, an ancestor or neighbour does NOT contribute to an accessible
    name by proximity, so this is a hint and not a name. It can be wrong, and
    the model has to be able to tell the difference between what the page
    states and what we guessed.

    Sets `_context` on ambiguous elements only. Mutates and returns the list.
    """
    counts = {}
    for el in elements:
        key = (el.get("role", ""), el.get("name", ""))
        counts[key] = counts.get(key, 0) + 1

    for i, el in enumerate(elements):
        # An unnamed control already took its label from the text node beside
        # it, which is a far better hint than anything the look-back finds.
        # Without this guard the two unlabelled checkboxes both got relabelled
        # "(in: Fork me on GitHub)" from the ribbon above them.
        if el.get("_context"):
            continue
        key = (el.get("role", ""), el.get("name", ""))
        if counts.get(key, 0) < 2:
            continue  # unique already; naming it again only adds noise
        # A link's name IS its destination, so two links sharing a name
        # usually share a target and need no help. Annotating them borrowed
        # the PREVIOUS product's title, which is worse than saying nothing.
        if el.get("role") == "link":
            continue
        name = el.get("name", "")
        for j in range(i - 1, max(-1, i - 1 - _CONTEXT_LOOKBACK), -1):
            source = elements[j]
            candidate = source.get("name", "")
            # Only borrow from a link. Links name a destination, so a product
            # title is a real label for the button beneath it. Borrowing from
            # a control instead produces confident nonsense — measured live,
            # product links picked up "(in: Price (high to low))" from a sort
            # dropdown and "(in: Remove)" from the button above them.
            if source.get("role") != "link":
                continue
            if candidate and candidate != name:
                el["_context"] = candidate
                break
    return elements


def mark_new_elements(previous: list[dict], current: list[dict]) -> list[bool]:
    """Compare current elements with previous by (role, name) tuple.

    Returns a list of booleans — True if element is new (not in previous).
    On first page (empty previous), all elements are marked False.
    """
    if not previous:
        return [False] * len(current)

    prev_set = {(el.get("role", ""), el.get("name", "")) for el in previous}
    return [(el.get("role", ""), el.get("name", "")) not in prev_set for el in current]


def enrich_field_state(el: dict) -> str:
    """Build a state string from element attributes.

    Shows validation state (invalid, required) and error descriptions
    inline with the element, so the LLM sees why a field is failing.

    Returns a state string like ' [invalid: "Please enter a valid email"]'
    or empty string if no relevant state.
    """
    parts = []
    raw = el.get("raw", {})

    if raw.get("invalid"):
        error_text = el.get("_error", "")
        if error_text:
            parts.append(f'invalid: "{error_text}"')
        else:
            parts.append("invalid")

    if raw.get("required"):
        parts.append("required")

    if raw.get("checked"):
        parts.append("checked")

    if raw.get("disabled"):
        parts.append("disabled")

    if raw.get("value"):
        val = raw["value"]
        if len(val) > 30:
            val = val[:27] + "..."
        parts.append(f'value="{val}"')

    if not parts:
        return ""
    return " [" + ", ".join(parts) + "]"


def _parse_aria_line(line: str) -> dict | None:
    """Parse one line of ARIA snapshot into a structured dict.

    Examples:
        '- button "Search"' → {"role": "button", "name": "Search"}
        '- combobox "Search with DuckDuckGo"' → {"role": "combobox", "name": "Search with DuckDuckGo"}
        '- heading "Title" [level=1]' → {"role": "heading", "name": "Title", "level": "1"}
        '- checkbox "Agree" [checked]' → {"role": "checkbox", "name": "Agree", "checked": True}
    """
    line = line.strip().lstrip("- ")
    if not line:
        return None

    # Match: role "name" [attributes]...
    match = re.match(r'(\w+)\s*"([^"]*)"(.*)$', line)
    if match:
        result = {"role": match.group(1), "name": match.group(2)}
        _apply_aria_attrs(result, _attr_groups(match.group(3)))
        return result

    # Match: role: "value"  — an unnamed control that HAS a value.
    #
    # This is what an input looks like the moment somebody types into it:
    # `- spinbutton` becomes `- spinbutton: "42"`. Matching neither pattern,
    # it was dropped, so a field DISAPPEARED from the element list as soon as
    # it was filled. The model types a value and the box it just used is gone,
    # leaving it no way to confirm the value landed or to correct a typo.
    match = re.match(r'(\w+):\s*"(.*)"\s*((?:\[[^\]]+\]\s*)*)$', line)
    if match:
        result = {"role": match.group(1), "name": "", "value": match.group(2)}
        _apply_aria_attrs(result, _attr_groups(match.group(3)))
        return result

    # Match: role [attributes] (no name), with an optional trailing colon.
    #
    # The snapshot appends ":" to any element that has children, so an
    # unlabelled <select> arrives as "- combobox:". Anchoring to $ without
    # allowing that colon meant EVERY unnamed element with children was
    # dropped — the combobox on the dropdown page never reached the model,
    # only its options did, so there was nothing to select ON.
    #
    # The attribute groups are matched explicitly rather than with a loose
    # `.*` so that a bare text node (`- text: some words`) still fails here,
    # which keeps page-text handling exactly as it was.
    match = re.match(r'(\w+)((?:\s*\[[^\]]+\])*)\s*:?$', line)
    if match:
        # Attributes used to come back as a raw "attrs" string, so
        # `parsed.get("checked")` was always falsey here and an unnamed
        # control rendered without its [checked] state. That matters most for
        # exactly the task these elements exist for: "tick it if it is not
        # already ticked" is unanswerable without the state.
        result = {"role": match.group(1), "name": ""}
        _apply_aria_attrs(result, _attr_groups(match.group(2)))
        return result

    return None


def _attr_groups(rest: str) -> str:
    """Collect EVERY "[...]" group in the remainder into one attr string.

    The old pattern captured a single non-greedy group, so a line carrying two
    of them — `option "Please select an option" [disabled] [selected]` — kept
    "disabled" and silently discarded "selected". Selection state is the whole
    answer to "did my choice land", so losing it left the model unable to tell
    a successful select from a no-op.
    """
    return ", ".join(re.findall(r'\[([^\]]+)\]', rest or ""))


def _apply_aria_attrs(result: dict, attrs_str: str) -> None:
    """Parse attributes: [checked], [disabled], [level=1], [value="text"]."""
    if not attrs_str:
        return
    for attr in attrs_str.split(", "):
        attr = attr.strip()
        if "=" in attr:
            k, v = attr.split("=", 1)
            result[k] = v.strip('"')
        else:
            result[attr] = True


def extract_aria(page, max_elements: int = None, max_headings: int = None, task: str = "", previous_elements: list = None, mode: str = "navigate", _shown_out: list = None) -> str:
    """Extract page content via ARIA accessibility tree.

    Returns a numbered element map similar to DOMExtractor but using
    ARIA roles and names instead of HTML tags and selectors.

    Modes:
      "navigate" — default, current behaviour unchanged.
      "form" — inputs sorted first, max_elements=20, max_headings=5.
      "content" — delegates to extract_aria_content() (text only, no numbered elements).

    This is what a screen reader sees — clean, structured, legally protected.
    """
    if mode not in ("navigate", "form", "content"):
        raise ValueError(f"Invalid mode: {mode!r} — expected 'navigate', 'form', or 'content'")

    # Content mode: delegate entirely to the content extractor
    if mode == "content":
        return extract_aria_content(page)

    title = page.title()
    url = page.url

    try:
        snapshot = page.locator("body").aria_snapshot()
    except Exception as e:
        log.warning("ARIA snapshot failed: %s — falling back to DOM", e)
        return ""

    # Parse snapshot into elements
    lines = snapshot.split("\n")
    interactive = []
    headings = []

    # Landmark tracking: detect ARIA landmarks and tag child elements
    current_landmark = None       # e.g. "form: Login"
    landmark_indent = -1          # indent level of current landmark line

    for idx, line in enumerate(lines):
        # Measure indent before parsing — needed for landmark scope tracking
        stripped = line.lstrip()
        indent = len(line) - len(stripped)

        # Check if indent has dropped out of the current landmark scope
        if current_landmark is not None and indent <= landmark_indent:
            current_landmark = None
            landmark_indent = -1

        # Detect landmark roles from the raw line BEFORE _parse_aria_line
        # Format: "- navigation "Main nav":" or "- form "Login":" (colon = has children)
        landmark_match = re.match(r'^-\s+(\w+)(?:\s+"([^"]*)")?', stripped)
        if landmark_match:
            lm_role = landmark_match.group(1)
            if lm_role in LANDMARK_ROLES:
                lm_name = landmark_match.group(2) or ""
                current_landmark = f"{lm_role}: {lm_name}" if lm_name else lm_role
                landmark_indent = indent
                continue  # Don't parse this line as an interactive element

        parsed = _parse_aria_line(line)
        if not parsed:
            continue

        role = parsed["role"]
        name = parsed.get("name", "")

        if role in SKIP_ROLES:
            continue

        if role == "heading" and name:
            level = parsed.get("level", "")
            headings.append(f"  (h{level}) {name}" if level else f"  {name}")
            continue

        if role == "text" and name:
            # Key page text — include if short
            if len(name) < 100:
                headings.append(f"  {name}")
            continue

        if role in INTERACTIVE_ROLES and (name or role in UNNAMED_OK_ROLES):
            state = ""
            if parsed.get("checked"):
                state = " [checked]"
            elif parsed.get("disabled"):
                state = " [disabled]"
            elif parsed.get("value"):
                state = f' (value: "{parsed["value"]}")'
            # Appended rather than folded into the chain above: an option can
            # be disabled AND selected, and "which one is selected" is the only
            # feedback the model gets that a select actually took effect. With
            # it missing, a successful selection looked identical to a failed
            # one, so the agent kept re-trying a choice it had already made.
            if parsed.get("selected"):
                state += " [selected]"

            # An unnamed control carries no clue about which one it is, and
            # these pages put the label in the text node immediately after
            # it ("- checkbox" then "- text: checkbox 1"). Borrow that as a
            # hint only: per W3C AccName a neighbour does not contribute to
            # the accessible name, so it renders as "(in: ...)" and never as
            # the name itself.
            # Matched here rather than via _parse_aria_line because the
            # snapshot writes bare text as `- text: checkbox 1`, which fits
            # neither of that parser's shapes. Widening it globally would
            # start feeding every stray text node into page text and change
            # read output on every site, which is not a trade worth making
            # for a label.
            inferred = ""
            if not name:
                for look in lines[idx + 1:idx + 3]:
                    m = re.match(r'^\s*-\s+text:\s*(.+?)\s*$', look)
                    if m:
                        inferred = m.group(1)[:60]
                        break
                    if look.strip().startswith("- "):
                        break

            interactive.append({
                "role": role,
                "name": name,
                "state": state,
                "raw": parsed,
                "_landmark": current_landmark,
                **({"_context": inferred} if inferred else {}),
            })

    # Form mode: override caps and sort inputs to the top
    if mode == "form":
        _max_el = max_elements or 20
        _max_hd = max_headings or 5
        # Sort: textbox/combobox/searchbox first, then others
        input_roles = {"textbox", "combobox", "searchbox"}
        inputs = [el for el in interactive if el["role"] in input_roles]
        others = [el for el in interactive if el["role"] not in input_roles]
        interactive = inputs + others
    else:
        _max_el = max_elements or MAX_ELEMENTS
        _max_hd = max_headings or MAX_HEADINGS

    # Build output
    output = []
    output.append(f"Page: {title}")
    output.append(f"URL: {url}")
    output.append("")

    # Scroll context hints
    scroll_info = get_scroll_info(page)
    above_hint, below_hint = format_scroll_hints(scroll_info)
    if above_hint:
        output.append(above_hint)
        output.append("")

    if interactive:
        # Deliberately NOT deduplicated. Every element here is an interactive
        # control, and repeated controls are almost always distinct targets:
        # a product grid has one "Add to cart" per product, a results list one
        # "Download" per row. Collapsing them by (role, name) kept only the
        # first, which made "add the third item to the cart" impossible to
        # express — measured live on a six-product page, where five of the six
        # buttons were deleted before the model ever saw them.
        #
        # No major framework dedupes here: Playwright MCP assigns a ref per
        # element by DOM position, and Vercel's agent-browser documents the
        # same case as two entries, @e2 and @e3. Repetition noise is already
        # handled by _is_nav_noise() scoring and by prune_elements() capping
        # the list, so dedup was redundant as well as destructive.

        # Form mode: sort to keep inputs on top
        if mode == "form":
            input_roles = {"textbox", "combobox", "searchbox"}
            inputs = [el for el in interactive if el["role"] in input_roles]
            others = [el for el in interactive if el["role"] not in input_roles]
            interactive = inputs + others

        # Before pruning, so a product's title is still present to label its
        # button even if the title itself is pruned away.
        annotate_ambiguous(interactive)

        # Which of the identically-named controls this is, in DOM order.
        # Resolution used get_by_role(...).first, so every one of six "Add to
        # cart" buttons resolved to the first product no matter which index
        # the model picked. Recorded here, while the list is still in DOM
        # order — pruning reorders it moments later.
        seen_sig: dict = {}
        for el in interactive:
            sig = (el.get("role", ""), el.get("name", ""))
            el["_ordinal"] = seen_sig.get(sig, 0)
            seen_sig[sig] = el["_ordinal"] + 1

        if task and mode != "form":
            shown = prune_elements(interactive, task, _max_el)
        else:
            shown = interactive[:_max_el]

        new_flags = mark_new_elements(previous_elements or [], shown)

        # Hand back the very elements that were numbered. Re-parsing them out
        # of the rendered text loses _ordinal and _context, and any mismatch
        # between what was numbered and what is resolved is a wrong click.
        if _shown_out is not None:
            # Stamp the number each element is rendered with. Callers resolve
            # by it (form_login, browser_tool), and the re-parsed dicts this
            # replaced carried it — without it, field lookup silently returns
            # nothing and a login fills no fields at all.
            for i, el in enumerate(shown):
                el["index"] = i
            _shown_out.extend(shown)

        output.append(f"Elements ({len(shown)} of {len(interactive)}):")

        # Group elements by landmark for output
        groups = []  # list of (landmark_label, [(global_idx, el, new_flag)])
        current_group_label = None
        current_group_items = []

        for i, el in enumerate(shown):
            landmark = el.get("_landmark")
            label = landmark if landmark else None
            if label != current_group_label:
                if current_group_items:
                    groups.append((current_group_label, current_group_items))
                current_group_label = label
                current_group_items = []
            current_group_items.append((i, el, new_flags[i]))

        if current_group_items:
            groups.append((current_group_label, current_group_items))

        for label, items in groups:
            if label:
                output.append(f"\n[{label}]")
            elif any(lbl is not None for lbl, _ in groups):
                # Only show [Other] if there are landmark groups too
                output.append("\n[Other]")
            for idx, el, is_new in items:
                prefix = "*" if is_new else ""
                state = enrich_field_state(el) or el["state"]
                ctx = el.get("_context")
                # "in:" marks this as inferred from page order, not an
                # accessible name the page actually provides.
                hint = f' (in: {ctx})' if ctx else ""
                output.append(
                    f'{prefix}[{idx}] {el["role"]} "{el["name"]}"{state}{hint}'
                )
    else:
        output.append("Elements: none found")

    if headings:
        output.append("")
        output.append("Page text:")
        for h in headings[:_max_hd]:
            output.append(h)

    if below_hint:
        output.append("")
        output.append(below_hint)

    return "\n".join(output)


def extract_aria_content(page) -> str:
    """Extract page content for data extraction — strips navigation UI, keeps content.

    Used when the goal is to READ data from the page (extract emails, products, etc.)
    rather than NAVIGATE it (click buttons, fill forms).

    Differences from extract_aria:
    - Filters out navigation buttons, menus, toolbars
    - Higher caps on headings and text (30 vs 10)
    - Includes all text nodes, not just short ones
    - Groups content by ARIA regions/landmarks when available
    """
    title = page.title()
    url = page.url

    try:
        snapshot = page.locator("body").aria_snapshot()
    except Exception as e:
        log.warning("ARIA content snapshot failed: %s", e)
        return ""

    if not snapshot or len(snapshot.strip()) < 10:
        return ""

    lines = snapshot.split("\n")
    content_items = []
    current_region = None

    for line in lines:
        parsed = _parse_aria_line(line)
        if not parsed:
            # Check for raw region markers in the ARIA snapshot
            stripped = line.strip().lstrip("- ")
            if stripped.startswith("region ") and '"' in stripped:
                region_name = stripped.split('"')[1]
                if not _is_nav_noise(region_name):
                    current_region = region_name
            continue

        role = parsed["role"]
        name = parsed.get("name", "")

        if not name:
            continue

        # Skip structural roles
        if role in SKIP_ROLES:
            continue

        # Skip navigation noise
        if role in INTERACTIVE_ROLES and _is_nav_noise(name):
            continue

        # Include headings (primary content signals)
        if role == "heading":
            level = parsed.get("level", "")
            prefix = f"(h{level}) " if level else ""
            if current_region:
                content_items.append(f"  [{current_region}] {prefix}{name}")
            else:
                content_items.append(f"  {prefix}{name}")
            continue

        # Include text nodes (the actual content)
        if role == "text":
            if current_region:
                content_items.append(f"  [{current_region}] {name}")
            else:
                content_items.append(f"  {name}")
            continue

        # Include links and buttons only if they look like content (not nav)
        if role in ("link", "button") and not _is_nav_noise(name):
            # Content links are typically longer or descriptive
            if len(name) > 15 or role == "link":
                content_items.append(f"  {role}: {name}")

    # Build output — content only, no element numbers (not for clicking)
    output = [f"Page: {title}", f"URL: {url}", "", "Page content:"]

    for item in content_items[:MAX_CONTENT_ELEMENTS]:
        output.append(item)

    if not content_items:
        output.append("  (no content found)")

    return "\n".join(output)


class AccessibilityExtractor:
    """ARIA-based element extraction — screen reader mode.

    Uses Playwright's ARIA snapshot for cleaner, more structured data.
    Falls back to DOMExtractor when ARIA tree is empty.
    """

    def __init__(self, max_elements: int = None, max_headings: int = None):
        self._last_interactive: list[dict] = []
        self._max_elements = max_elements
        self._max_headings = max_headings

    def extract(self, page, task: str = "", mode: str = "navigate") -> str:
        """Extract page via ARIA tree. Falls back to DOM if empty.

        mode: "navigate" (default), "form", or "content".
        """
        # Content mode: delegate to extract_content, no interactive caching needed
        if mode == "content":
            return self.extract_content(page)

        previous = list(self._last_interactive)  # copy before overwriting
        shown: list = []
        result = extract_aria(page, self._max_elements, self._max_headings,
                              task=task, previous_elements=previous, mode=mode,
                              _shown_out=shown)
        if not result or "Elements: none found" in result:
            log.debug("ARIA tree empty — falling back to DOM extraction")
            self._last_interactive = []
            from fantoma.dom.extractor import DOMExtractor
            fallback = DOMExtractor()
            return fallback.extract(page)

        # Cache the elements exactly as numbered, so index N resolves to the
        # element the model saw as [N]. Falls back to re-parsing the text if
        # nothing came back, which keeps older callers working.
        self._last_interactive = shown or self._parse_interactive_from_output(result)
        if self._last_interactive:
            self._last_interactive = self._filter_occluded(page, self._last_interactive)

        # Merge iframe elements
        from fantoma.dom.frames import collect_all_frame_elements
        iframe_elements = collect_all_frame_elements(page)
        if iframe_elements:
            base_idx = len(self._last_interactive)
            self._last_interactive.extend(iframe_elements)
            iframe_section = [f"\nIframe elements ({len(iframe_elements)}):"]
            for i, el in enumerate(iframe_elements):
                frame_tag = f" [{el['_frame']}]" if el.get("_frame") else ""
                iframe_section.append(
                    f'[{base_idx + i}] {el["role"]} "{el["name"]}"{el["state"]}{frame_tag}'
                )
            result = result + "\n".join(iframe_section)

        return result

    def _filter_occluded(self, page, elements: list[dict]) -> list[dict]:
        """Remove elements that are visually hidden behind other elements (e.g. modals).

        Uses document.elementFromPoint() to check whether each element is actually
        on top at its centre coordinates. Elements outside the viewport or that
        cannot be located are assumed visible and kept. On any JS error the full
        list is returned unchanged.
        """
        _JS = """
        (function(role, name) {
            // Find the element by role + accessible name
            var candidates = [];
            var all = document.querySelectorAll('*');
            for (var i = 0; i < all.length; i++) {
                var el = all[i];
                var elRole = el.getAttribute('role') || el.tagName.toLowerCase();
                var elLabel = el.getAttribute('aria-label') || el.textContent.trim().slice(0, 80);
                if (elRole === role && elLabel === name) {
                    candidates.push(el);
                }
            }
            if (candidates.length === 0) return true;  // not found → assume visible
            var el = candidates[0];
            var rect = el.getBoundingClientRect();
            if (rect.width === 0 || rect.height === 0) return false;  // zero-size → hidden
            var vw = window.innerWidth || document.documentElement.clientWidth;
            var vh = window.innerHeight || document.documentElement.clientHeight;
            var cx = rect.left + rect.width / 2;
            var cy = rect.top + rect.height / 2;
            if (cx < 0 || cy < 0 || cx > vw || cy > vh) return true;  // off-screen → keep
            var top = document.elementFromPoint(cx, cy);
            if (!top) return true;  // can't determine → keep
            return el.contains(top) || top.contains(el) || el === top;
        })(arguments[0], arguments[1])
        """
        try:
            visible = []
            for el in elements:
                try:
                    is_on_top = page.evaluate(_JS, el["role"], el["name"])
                    if is_on_top:
                        visible.append(el)
                    else:
                        log.debug(
                            "paint-order: hiding occluded %s %r", el["role"], el["name"]
                        )
                except Exception as inner:
                    log.debug("paint-order check failed for %r: %s — keeping", el, inner)
                    visible.append(el)
            return visible
        except Exception as e:
            log.warning("paint-order filtering failed: %s — returning all elements", e)
            return elements

    def extract_content(self, page) -> str:
        """Extract page content only — for data extraction, not navigation.

        Priority:
        1. JSON-LD schema.org structured data (recipe pages, article pages)
        2. Recipe card list (search/listing pages)
        3. ARIA content extraction
        4. Raw page inner_text fallback
        """
        # 1. Try JSON-LD structured data
        structured = self._extract_jsonld(page)
        if structured:
            return structured

        # 2. Try recipe card list (search/listing pages)
        cards = self._extract_recipe_cards(page)
        if cards:
            return cards

        # 3. ARIA content extraction
        result = extract_aria_content(page)
        if result and "(no content found)" not in result:
            return result

        # 4. Fallback: raw page text
        try:
            text = page.inner_text("body")[:4000]
            return f"Page: {page.title()}\nURL: {page.url}\n\nPage content:\n{text}"
        except Exception:
            return ""

    def _extract_jsonld(self, page) -> str:
        """Extract and summarise schema.org JSON-LD from the page."""
        try:
            items = page.evaluate("""() => {
                const scripts = document.querySelectorAll('script[type="application/ld+json"]');
                return Array.from(scripts).map(s => {
                    try { return JSON.parse(s.textContent); } catch(e) { return null; }
                }).filter(Boolean);
            }""")
        except Exception:
            return ""

        if not items:
            return ""

        # Flatten — some sites wrap JSON-LD in an array at the top level
        flat = []
        for item in items:
            if isinstance(item, list):
                flat.extend(item)
            elif isinstance(item, dict):
                flat.append(item)
        items = flat

        parts = []
        for item in items:
            if not isinstance(item, dict):
                continue
            types = item.get("@type", [])
            if isinstance(types, str):
                types = [types]

            if "Recipe" in types:
                lines = [f"Recipe: {item.get('name', '')}"]
                if item.get("description"):
                    lines.append(f"Description: {item['description'][:200]}")
                agg = item.get("aggregateRating", {})
                if agg:
                    count = agg.get('reviewCount') or agg.get('ratingCount') or '?'
                    lines.append(f"Rating: {agg.get('ratingValue', '?')}/5 ({count} reviews)")
                if item.get("prepTime"):
                    lines.append(f"Prep time: {item['prepTime']}")
                if item.get("cookTime"):
                    lines.append(f"Cook time: {item['cookTime']}")
                if item.get("totalTime"):
                    lines.append(f"Total time: {item['totalTime']}")
                if item.get("recipeYield"):
                    lines.append(f"Servings: {item['recipeYield']}")
                nutrition = item.get("nutrition", {})
                if nutrition.get("calories"):
                    lines.append(f"Calories: {nutrition['calories']}")
                ingredients = item.get("recipeIngredient", [])
                if ingredients:
                    lines.append(f"Ingredients ({len(ingredients)}):")
                    for ing in ingredients[:20]:
                        lines.append(f"  - {ing}")
                instructions = item.get("recipeInstructions", [])
                if instructions:
                    lines.append(f"Instructions ({len(instructions)} steps):")
                    for i, step in enumerate(instructions[:10], 1):
                        text = step.get("text", step) if isinstance(step, dict) else step
                        lines.append(f"  {i}. {str(text)[:150]}")
                parts.append("\n".join(lines))

            elif any(t in types for t in ("Article", "NewsArticle", "BlogPosting", "ScholarlyArticle")):
                lines = [f"Article: {item.get('headline') or item.get('name', '')}"]
                if item.get("description"):
                    lines.append(f"Description: {item['description'][:300]}")
                if item.get("datePublished"):
                    lines.append(f"Published: {item['datePublished']}")
                if item.get("dateModified"):
                    lines.append(f"Modified: {item['dateModified']}")
                author = item.get("author", {})
                if isinstance(author, dict) and author.get("name"):
                    lines.append(f"Author: {author['name']}")
                elif isinstance(author, list):
                    names = [a.get("name", "") for a in author if isinstance(a, dict)]
                    if names:
                        lines.append(f"Authors: {', '.join(names)}")
                parts.append("\n".join(lines))

            elif "LocalBusiness" in types or "Restaurant" in types:
                lines = [f"Business: {item.get('name', '')}"]
                if item.get("address"):
                    addr = item["address"]
                    if isinstance(addr, dict):
                        lines.append(f"Address: {addr.get('streetAddress', '')} {addr.get('addressLocality', '')}")
                agg = item.get("aggregateRating", {})
                if agg:
                    count = agg.get('reviewCount') or agg.get('ratingCount') or '?'
                    lines.append(f"Rating: {agg.get('ratingValue', '?')}/5 ({count} reviews)")
                if item.get("telephone"):
                    lines.append(f"Phone: {item['telephone']}")
                parts.append("\n".join(lines))

            elif "Product" in types:
                lines = [f"Product: {item.get('name', '')}"]
                if item.get("description"):
                    lines.append(f"Description: {item['description'][:200]}")
                agg = item.get("aggregateRating", {})
                if agg:
                    count = agg.get('reviewCount') or agg.get('ratingCount') or '?'
                    lines.append(f"Rating: {agg.get('ratingValue', '?')}/5 ({count} reviews)")
                offers = item.get("offers", {})
                if isinstance(offers, dict) and offers.get("price"):
                    lines.append(f"Price: {offers.get('priceCurrency', '')} {offers['price']}")
                parts.append("\n".join(lines))

            elif "ItemList" in types or "BreadcrumbList" in types:
                pass  # Skip non-content lists

        return "\n\n".join(parts) if parts else ""

    def _extract_recipe_cards(self, page) -> str:
        """Extract recipe cards from search/listing pages."""
        try:
            url = page.url
            # Only run on pages that look like search/listing/collection pages
            if not any(x in url for x in ["/search", "/recipes/", "?q=", "category", "collection"]):
                return ""

            cards = page.evaluate("""() => {
                const results = [];
                const seen = new Set();
                document.querySelectorAll('a[href*="/recipe/"]').forEach(a => {
                    const href = a.href;
                    if (seen.has(href)) return;
                    seen.add(href);
                    const text = a.innerText?.trim().replace(/\\s+/g, ' ');
                    if (text && text.length > 5 && text.length < 250) {
                        results.push(href + ' | ' + text);
                    }
                });
                return results.slice(0, 20);
            }""")
        except Exception:
            return ""

        if not cards:
            return ""

        lines = [f"Search results on {page.url}:", ""]
        for card in cards:
            lines.append(f"  {card}")
        return "\n".join(lines)

    def signature(self, index: int):
        """Return (role, name) for an element index, or None. Used by the
        action cache to record a replayable, index-free target."""
        if 0 <= index < len(self._last_interactive):
            el = self._last_interactive[index]
            return el.get("role", ""), el.get("name", "")
        return None

    def find_index_for_target(self, index: int, target: str):
        """Return the index of the control belonging to `target`, if different.

        A repeated control ("Add to cart") tells a model nothing about which
        item it acts on, and weak models take the first match — measured live,
        asked for the Fleece Jacket the agent added the first product on the
        page. The item each control belongs to is already known here, so the
        right index can be resolved in code rather than hoped for in a prompt.

        Returns None when there is nothing to correct: no target, the choice
        already matches, or no single unambiguous alternative. Silence is the
        safe answer — a wrong correction is worse than no correction.
        """
        if not target or index is None:
            return None
        if not (0 <= index < len(self._last_interactive)):
            return None

        chosen = self._last_interactive[index]
        ctx = (chosen.get("_context") or "").lower()
        want = target.lower()
        if not ctx:
            return None      # not an ambiguous control; leave it alone
        if want in ctx or ctx in want:
            return None      # already the right one

        matches = [
            el for el in self._last_interactive
            if el.get("role") == chosen.get("role")
            and el.get("name") == chosen.get("name")
            and want in (el.get("_context") or "").lower()
        ]
        if len(matches) != 1:
            return None      # ambiguous or absent — do not guess
        return self._last_interactive.index(matches[0])

    def find_by_signature(self, role: str, name: str):
        """Return the current index whose (role, name) matches, or None. Used
        on cache replay to re-resolve a step against a possibly-changed page."""
        for i, el in enumerate(self._last_interactive):
            if el.get("role", "") == role and el.get("name", "") == name:
                return i
        return None

    def get_element_by_index(self, page, index: int) -> Optional[Any]:
        """Find element by ARIA role and name using Playwright locators.

        This is more stable than CSS selectors — ARIA attributes are
        semantically meaningful and sites are required to maintain them.
        """
        if index < 0 or index >= len(self._last_interactive):
            return None

        el = self._last_interactive[index]
        role = el["role"]
        name = el["name"]

        # If element is from an iframe, search in that frame
        if el.get("_frame"):
            return self._find_in_frame(page, el)

        # Use Playwright's role-based locator, at the right position among
        # identically-named controls. Taking .first here meant every one of
        # six "Add to cart" buttons resolved to the first product, so the
        # model's choice of index was discarded and the wrong item was
        # actioned no matter what it picked.
        ordinal = el.get("_ordinal", 0)
        try:
            locator = page.get_by_role(role, name=name)
            count = locator.count()
            if count > 0:
                if ordinal and ordinal < count:
                    return locator.nth(ordinal).element_handle()
                return locator.first.element_handle()
        except Exception:
            pass

        # Fallback: try aria-label
        try:
            element = page.query_selector(f'[aria-label="{name}"]')
            if element:
                return element
        except Exception:
            pass

        # Fallback: text-based
        try:
            locator = page.get_by_text(name, exact=True)
            if locator.count() > 0:
                return locator.first.element_handle()
        except Exception:
            pass

        return None

    def _find_in_frame(self, page, el: dict) -> Optional[Any]:
        """Find an element inside an iframe by frame name and role/name."""
        frame_name = el["_frame"]
        for frame in page.frames:
            if frame.name == frame_name or frame.url.split("/")[-1][:20] == frame_name:
                try:
                    locator = frame.get_by_role(el["role"], name=el["name"])
                    if locator.count() > 0:
                        return locator.first.element_handle()
                except Exception:
                    pass
        return None

    @staticmethod
    def _parse_interactive_from_output(output: str) -> list[dict]:
        """Parse the numbered elements back out of the rendered output.

        The inferred "(in: ...)" label is captured too, so a caller can tell
        which item a repeated control belongs to without re-extracting.
        """
        elements = []
        for line in output.split("\n"):
            match = re.match(r'\*?\[(\d+)\]\s+(\w+)\s+"([^"]*)"', line)
            if match:
                ctx = re.search(r'\(in: (.+?)\)\s*$', line)
                elements.append({
                    "index": int(match.group(1)),
                    "role": match.group(2),
                    "name": match.group(3),
                    "_context": ctx.group(1) if ctx else "",
                })
        return elements
