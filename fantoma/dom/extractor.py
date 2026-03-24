"""DOM extractor — converts a live page into a numbered text map for LLM consumption."""

import re
from typing import Any, Optional


# JavaScript executed inside the browser to collect all interactive elements
# in a single pass. Much faster than individual query_selector calls.
_EXTRACT_JS = """
() => {
    const SKIP = [
        '[aria-hidden="true"]',
        '[style*="display: none"]', '[style*="display:none"]',
        '[data-testid="placementTracking"]',
        'nav:not([aria-label*="main"])',
        'footer',
    ];

    const INTERACTIVE = [
        'input:not([type="hidden"])',
        'textarea',
        'select',
        'button',
        'a[href]',
        '[role="button"]',
        '[role="link"]',
        '[role="tab"]',
        '[role="menuitem"]',
        '[role="option"]',
        '[contenteditable="true"]',
        'li[class*="suggest"]',
        'li[class*="search"]',
        'li[class*="result"]',
        'li[class*="option"]',
        'li[class*="lookup"]',
        'li[class*="autocomplete"]',
        'div[class*="suggestion"]',
    ];

    // Build a set of elements to skip
    const skipSet = new Set();
    for (const sel of SKIP) {
        try {
            document.querySelectorAll(sel).forEach(el => skipSet.add(el));
        } catch (_) {}
    }

    // Also skip anything nested inside a skip element
    function isInsideSkipped(el) {
        let node = el.parentElement;
        while (node) {
            if (skipSet.has(node)) return true;
            node = node.parentElement;
        }
        return false;
    }

    const results = [];
    const seen = new Set();

    for (const sel of INTERACTIVE) {
        try {
            const elems = document.querySelectorAll(sel);
            for (const el of elems) {
                if (seen.has(el)) continue;
                seen.add(el);
                if (skipSet.has(el) || isInsideSkipped(el)) continue;

                // Visibility check — must have a bounding rect with area > 0
                const rect = el.getBoundingClientRect();
                if (!rect || rect.width === 0 || rect.height === 0) continue;

                const tag = el.tagName.toLowerCase();
                const type = el.getAttribute('type') || '';
                const role = el.getAttribute('role') || '';
                const ariaLabel = el.getAttribute('aria-label') || '';
                const placeholder = el.getAttribute('placeholder') || '';
                const name = el.getAttribute('name') || '';
                const id = el.getAttribute('id') || '';
                const href = el.getAttribute('href') || '';
                const value = (el.value !== undefined ? el.value : '') || '';
                const dataTestId = el.getAttribute('data-testid') || '';

                // Get visible text — trim and truncate
                let text = (el.innerText || el.textContent || '').trim();
                if (text.length > 100) text = text.substring(0, 100) + '...';

                results.push({
                    tag, type, role, ariaLabel, placeholder, name,
                    id, href, value, text, dataTestId,
                    rect: {top: rect.top, left: rect.left, width: rect.width, height: rect.height},
                });
            }
        } catch (_) {}
    }

    // Extract headings for key text section
    const headings = [];
    for (const level of ['h1', 'h2', 'h3']) {
        const hElems = document.querySelectorAll(level);
        for (const h of hElems) {
            let t = (h.innerText || h.textContent || '').trim();
            if (t && t.length > 0) {
                if (t.length > 100) t = t.substring(0, 100) + '...';
                headings.push({level, text: t});
            }
        }
    }

    // Detect iframes
    const iframes = document.querySelectorAll('iframe');
    const iframeInfo = [];
    for (const f of iframes) {
        const src = f.getAttribute('src') || '';
        const title = f.getAttribute('title') || '';
        if (src || title) {
            iframeInfo.push({src: src.substring(0, 200), title});
        }
    }

    return {elements: results, headings, iframeCount: iframes.length, iframes: iframeInfo};
}
"""

# JavaScript to count interactive elements (same logic, for get_element_by_index)
_GET_ELEMENT_JS = """
(targetIndex) => {
    const SKIP = [
        '[aria-hidden="true"]',
        '[style*="display: none"]', '[style*="display:none"]',
        '[data-testid="placementTracking"]',
        'nav:not([aria-label*="main"])',
        'footer',
    ];

    const INTERACTIVE = [
        'input:not([type="hidden"])',
        'textarea',
        'select',
        'button',
        'a[href]',
        '[role="button"]',
        '[role="link"]',
        '[role="tab"]',
        '[role="menuitem"]',
        '[role="option"]',
        '[contenteditable="true"]',
        'li[class*="suggest"]',
        'li[class*="search"]',
        'li[class*="result"]',
        'li[class*="option"]',
        'li[class*="lookup"]',
        'li[class*="autocomplete"]',
        'div[class*="suggestion"]',
    ];

    const skipSet = new Set();
    for (const sel of SKIP) {
        try {
            document.querySelectorAll(sel).forEach(el => skipSet.add(el));
        } catch (_) {}
    }

    function isInsideSkipped(el) {
        let node = el.parentElement;
        while (node) {
            if (skipSet.has(node)) return true;
            node = node.parentElement;
        }
        return false;
    }

    const seen = new Set();
    let index = 0;

    for (const sel of INTERACTIVE) {
        try {
            const elems = document.querySelectorAll(sel);
            for (const el of elems) {
                if (seen.has(el)) continue;
                seen.add(el);
                if (skipSet.has(el) || isInsideSkipped(el)) continue;

                const rect = el.getBoundingClientRect();
                if (!rect || rect.width === 0 || rect.height === 0) continue;

                index++;
                if (index === targetIndex) {
                    // Return a unique selector path so Playwright can find this element
                    // Build a selector: prefer id, then data-testid, then nth-of-type path
                    if (el.id) return '#' + CSS.escape(el.id);
                    if (el.getAttribute('data-testid'))
                        return '[data-testid="' + el.getAttribute('data-testid') + '"]';

                    // Build a path from the element to a parent with an id
                    const path = [];
                    let cur = el;
                    while (cur && cur !== document.body) {
                        let seg = cur.tagName.toLowerCase();
                        if (cur.id) {
                            path.unshift('#' + CSS.escape(cur.id) + ' > ' + seg);
                            return path.join(' > ');
                        }
                        const parent = cur.parentElement;
                        if (parent) {
                            const siblings = Array.from(parent.children).filter(
                                c => c.tagName === cur.tagName
                            );
                            if (siblings.length > 1) {
                                const idx = siblings.indexOf(cur) + 1;
                                seg += ':nth-of-type(' + idx + ')';
                            }
                        }
                        path.unshift(seg);
                        cur = parent;
                    }
                    path.unshift('body');
                    return path.join(' > ');
                }
            }
        } catch (_) {}
    }

    return null;  // Index out of range
}
"""



class DOMExtractor:
    """Extracts interactive elements from a page into a numbered text map for LLM consumption."""


    SKIP_SELECTORS = [
        '[aria-hidden="true"]',
        '[style*="display: none"]',
        '[style*="display:none"]',
        '[data-testid="placementTracking"]',
        'nav:not([aria-label*="main"])',
        'footer',
    ]

    INTERACTIVE_SELECTORS = [
        'input:not([type="hidden"])',
        'textarea',
        'select',
        'button',
        'a[href]',
        '[role="button"]',
        '[role="link"]',
        '[role="tab"]',
        '[role="menuitem"]',
        '[role="option"]',
        '[contenteditable="true"]',
        # Autocomplete/suggestion dropdowns
        'li[class*="suggest"]',
        'li[class*="search"]',
        'li[class*="result"]',
        'li[class*="option"]',
        'li[class*="item"][class*="dropdown"]',
        'li[class*="autocomplete"]',
        'li[class*="lookup"]',
        'div[class*="suggestion"]',
        'div[class*="autocomplete-item"]',
    ]

    def __init__(self, max_elements: int = 15):
        self._max_elements = max_elements
        self._last_elements: list[dict] = []  # Cached elements from last extraction

    def extract(self, page) -> str:
        """Extract page into numbered element map.

        Args:
            page: Playwright Page object.

        Returns:
            Formatted string for LLM consumption with numbered interactive
            elements and key page text.
        """
        title = page.title()
        url = page.url

        # Single bulk JS call for speed
        data = page.evaluate(_EXTRACT_JS)
        elements = data.get("elements", [])

        # Smart prioritisation: visible text inputs + suggestions first, skip hidden/footer inputs
        def _priority(e):
            tag = e.get("tag", "")
            role = e.get("role", "")
            etype = e.get("type", "")
            cls = (e.get("class", "") or "").lower()
            text = (e.get("text", "") or "").lower()
            name = (e.get("name", "") or "").lower()
            # Highest: text/search inputs (not checkboxes, not hidden)
            if tag in ("input", "textarea") and etype in ("text", "search", "email", "password", "tel", "url", ""):
                return 0
            # High: autocomplete suggestion items
            if tag == "li" and ("suggest" in cls or "search" in cls or "result" in cls or "option" in cls or "lookup" in cls):
                return 1
            if role in ("option", "button") and tag == "li":
                return 1
            # Medium: submit/search buttons
            if tag == "button":
                return 2
            # Low: other inputs (checkboxes, radio, hidden)
            if tag == "input":
                return 4
            # Lowest: links
            return 5
        elements.sort(key=_priority)
        headings = data.get("headings", [])

        # Cache elements for stable get_element_by_index lookups
        self._last_elements = elements
        iframe_count = data.get("iframeCount", 0)
        iframes = data.get("iframes", [])

        # Build output
        lines: list[str] = []
        lines.append(f"Page: {title}")
        lines.append(f"URL: {url}")
        lines.append("")

        # Interactive elements section
        # Keep it SHORT — the LLM only needs actionable elements, not the whole page
        if elements:
            shown = elements[:self._max_elements]
            lines.append(f"Interactive elements ({len(shown)} of {len(elements)}):")
            for i, el in enumerate(shown):
                desc = self._describe_element(el)
                lines.append(f"[{i}] {desc}")
        else:
            lines.append("Interactive elements: none found")

        # Key text section
        if headings:
            lines.append("")
            lines.append("Key text:")
            for h in headings:
                lines.append(f'  ({h["level"]}) {h["text"]}')

        # Iframe notice
        if iframe_count > 0:
            lines.append("")
            lines.append(f"Note: {iframe_count} iframe(s) detected (content not extracted)")
            for iframe in iframes[:3]:
                src_short = iframe.get("src", "")[:80]
                iframe_title = iframe.get("title", "")
                if iframe_title:
                    lines.append(f'  iframe: "{iframe_title}" src={src_short}')
                elif src_short:
                    lines.append(f"  iframe: src={src_short}")

        return "\n".join(lines)

    def get_element_by_index(self, page, index: int) -> Optional[Any]:
        """Return the Playwright ElementHandle for a numbered element.

        Uses cached element data from the last extract() call for stability.
        Falls back to multiple selector strategies if the primary one fails.

        Args:
            page: Playwright Page object.
            index: 1-based element index from the extraction output.

        Returns:
            Playwright ElementHandle or None if not found.
        """
        # 0-based indexing
        if index < 0 or index >= len(self._last_elements):
            return None

        el_data = self._last_elements[index]

        # Try multiple selector strategies in priority order
        strategies = []
        tag = el_data.get("tag", "")
        name = el_data.get("name", "")

        # 1. By tag + name (most reliable for form inputs)
        if name and tag:
            strategies.append(f'{tag}[name="{name}"]')

        # 2. By tag + ID (qualified with tag to avoid span/label collisions)
        el_id = el_data.get("id", "")
        if el_id and tag:
            strategies.append(f'{tag}#{el_id}')
        if el_id:
            strategies.append(f'#{el_id}')

        # 3. By data-testid
        testid = el_data.get("dataTestId", "")
        if testid:
            strategies.append(f'[data-testid="{testid}"]')

        # 4. By aria-label
        aria = el_data.get("ariaLabel", "")
        if aria and tag:
            strategies.append(f'{tag}[aria-label="{aria}"]')

        # 5. By placeholder
        placeholder = el_data.get("placeholder", "")
        if placeholder and tag:
            strategies.append(f'{tag}[placeholder="{placeholder}"]')

        # 6. By stored selector from JS extraction
        stored_selector = el_data.get("selector", "")
        if stored_selector:
            strategies.append(stored_selector)

        # Try each strategy — first pass requires visible element, second pass accepts any
        for require_visible in (True, False):
            for selector in strategies:
                try:
                    element = page.query_selector(selector)
                    if not element:
                        continue
                    if require_visible:
                        if element.bounding_box():
                            return element
                    else:
                        return element
                except Exception:
                    continue

        # Final fallback: re-query all elements and pick by index
        try:
            result = page.evaluate(_GET_ELEMENT_JS, index)
            if result:
                return page.query_selector(result)
        except Exception:
            pass

        return None

    @staticmethod
    def _describe_element(el: dict) -> str:
        """Build a human-readable description of an element."""
        tag = el.get("tag", "")
        el_type = el.get("type", "")
        role = el.get("role", "")
        text = el.get("text", "")
        aria = el.get("ariaLabel", "")
        placeholder = el.get("placeholder", "")
        value = el.get("value", "")
        href = el.get("href", "")

        # Determine the label (best available name for the element)
        label = aria or placeholder or text
        if not label and el.get("name"):
            label = el["name"]
        if not label and el.get("id"):
            label = el["id"]

        # Determine the element kind to display
        if tag == "input":
            kind = f"input"
            parts = []
            if label:
                parts.append(f'"{label}"')
            if el_type and el_type not in ("text", ""):
                parts.append(f"(type: {el_type})")
            if value:
                val_display = value if len(value) <= 50 else value[:50] + "..."
                parts.append(f'(value: "{val_display}")')
            elif el_type not in ("submit", "button", "checkbox", "radio"):
                parts.append('(value: "")')
            return f"{kind} {' '.join(parts)}".strip()

        elif tag == "textarea":
            parts = [f'"{label}"'] if label else []
            if value:
                val_display = value if len(value) <= 50 else value[:50] + "..."
                parts.append(f'(value: "{val_display}")')
            return f"textarea {' '.join(parts)}".strip()

        elif tag == "select":
            parts = [f'"{label}"'] if label else []
            if value:
                parts.append(f'(value: "{value}")')
            return f"dropdown {' '.join(parts)}".strip()

        elif tag == "button" or role == "button":
            btn_label = label or "unlabelled"
            return f'button "{btn_label}"'

        elif tag == "a" or role == "link":
            link_label = label or href[:60] if href else "unlabelled"
            return f'link "{link_label}"'

        elif role == "tab":
            tab_label = label or "unlabelled"
            return f'tab "{tab_label}"'

        elif role == "menuitem":
            item_label = label or "unlabelled"
            return f'menuitem "{item_label}"'

        elif el.get("contenteditable"):
            return f'editable "{label}"' if label else "editable region"

        else:
            return f'{tag} "{label}"' if label else tag
