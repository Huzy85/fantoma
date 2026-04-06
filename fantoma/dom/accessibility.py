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
MAX_ELEMENTS = 15
MAX_HEADINGS = 25
MAX_CONTENT_ELEMENTS = 30

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


def prune_elements(elements: list[dict], task: str = "", max_elements: int = 15) -> list[dict]:
    """Score and rank elements by relevance to the task. Returns top N.

    Scoring:
      +3  element name contains a task keyword
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

        for kw in keywords:
            if kw in name_lower.split():
                score += 3
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


def mark_new_elements(previous: list[dict], current: list[dict]) -> list[bool]:
    """Compare current elements with previous by (role, name) tuple.

    Returns a list of booleans — True if element is new (not in previous).
    On first page (empty previous), all elements are marked False.
    """
    if not previous:
        return [False] * len(current)

    prev_set = {(el.get("role", ""), el.get("name", "")) for el in previous}
    return [(el.get("role", ""), el.get("name", "")) not in prev_set for el in current]


def dedup_elements(elements: list[dict]) -> list[dict]:
    """Remove duplicate interactive elements by (role, name, state) tuple.

    Keeps the first occurrence. Sites repeat the same link/button in nav,
    footer, and main content — this removes the noise.

    Textboxes with the same name but different state (value) are kept
    as separate fields (e.g., two "Email" fields on different forms).
    """
    seen = set()
    result = []
    for el in elements:
        key = (el.get("role", ""), el.get("name", ""), el.get("state", ""))
        if key not in seen:
            seen.add(key)
            result.append(el)
    return result


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

    # Match: role "name" [attributes]
    match = re.match(r'(\w+)\s*"([^"]*)"(?:\s*\[(.+?)\])?', line)
    if not match:
        # Match: role [attributes] (no name)
        match2 = re.match(r'(\w+)(?:\s*\[(.+?)\])?$', line)
        if match2:
            return {"role": match2.group(1), "name": "", "attrs": match2.group(2) or ""}
        return None

    role = match.group(1)
    name = match.group(2)
    attrs_str = match.group(3) or ""

    result = {"role": role, "name": name}

    # Parse attributes: [checked], [disabled], [level=1], [value="text"]
    if attrs_str:
        for attr in attrs_str.split(", "):
            attr = attr.strip()
            if "=" in attr:
                k, v = attr.split("=", 1)
                result[k] = v.strip('"')
            else:
                result[attr] = True

    return result


def extract_aria(page, max_elements: int = None, max_headings: int = None, task: str = "", previous_elements: list = None, mode: str = "navigate") -> str:
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

    for line in lines:
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

        if role in INTERACTIVE_ROLES and name:
            state = ""
            if parsed.get("checked"):
                state = " [checked]"
            elif parsed.get("disabled"):
                state = " [disabled]"
            elif parsed.get("value"):
                state = f' (value: "{parsed["value"]}")'

            interactive.append({
                "role": role,
                "name": name,
                "state": state,
                "raw": parsed,
                "_landmark": current_landmark,
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
        interactive = dedup_elements(interactive)

        # Form mode: sort again after dedup to keep inputs on top
        if mode == "form":
            input_roles = {"textbox", "combobox", "searchbox"}
            inputs = [el for el in interactive if el["role"] in input_roles]
            others = [el for el in interactive if el["role"] not in input_roles]
            interactive = inputs + others

        if task and mode != "form":
            shown = prune_elements(interactive, task, _max_el)
        else:
            shown = interactive[:_max_el]

        new_flags = mark_new_elements(previous_elements or [], shown)

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
                output.append(f'{prefix}[{idx}] {el["role"]} "{el["name"]}"{state}')
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
        result = extract_aria(page, self._max_elements, self._max_headings,
                              task=task, previous_elements=previous, mode=mode)
        if not result or "Elements: none found" in result:
            log.debug("ARIA tree empty — falling back to DOM extraction")
            self._last_interactive = []
            from fantoma.dom.extractor import DOMExtractor
            fallback = DOMExtractor()
            return fallback.extract(page)

        # Cache interactive elements for get_element_by_index
        self._last_interactive = self._parse_interactive_from_output(result)
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

        Strips navigation UI, buttons, menus. Keeps headings, text, content links.
        Higher caps (30 items vs 10). Groups by ARIA regions when available.
        Falls back to full page inner_text if ARIA content extraction is empty.
        """
        result = extract_aria_content(page)
        if not result or "(no content found)" in result:
            # Fallback: raw page text
            try:
                text = page.inner_text("body")[:4000]
                return f"Page: {page.title()}\nURL: {page.url}\n\nPage content:\n{text}"
            except Exception:
                return ""
        return result

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

        # Use Playwright's role-based locator (the modern, recommended way)
        try:
            locator = page.get_by_role(role, name=name)
            if locator.count() > 0:
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
        """Parse the numbered elements from the output string."""
        elements = []
        for line in output.split("\n"):
            match = re.match(r'\*?\[(\d+)\]\s+(\w+)\s+"([^"]*)"', line)
            if match:
                elements.append({
                    "index": int(match.group(1)),
                    "role": match.group(2),
                    "name": match.group(3),
                })
        return elements
