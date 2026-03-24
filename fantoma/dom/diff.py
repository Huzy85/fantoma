"""Page change detection — determines if an action had an effect."""

import hashlib


_DOM_HASH_JS = """
() => {
    // Hash the outer structure of the body (tag names + classes, not text)
    // Fast and catches structural changes without being noise-sensitive.
    const body = document.body;
    if (!body) return '';

    const walk = (el, depth) => {
        if (depth > 6) return '';
        const parts = [el.tagName];
        if (el.id) parts.push('#' + el.id);
        if (el.className && typeof el.className === 'string') {
            parts.push('.' + el.className.trim().split(/\\s+/).slice(0, 3).join('.'));
        }
        let result = parts.join('');
        const children = el.children;
        for (let i = 0; i < Math.min(children.length, 30); i++) {
            result += '>' + walk(children[i], depth + 1);
        }
        return result;
    };

    return walk(body, 0);
}
"""

_COUNT_INTERACTIVE_JS = """
() => {
    const selectors = [
        'input:not([type="hidden"])', 'textarea', 'select', 'button',
        'a[href]', '[role="button"]', '[role="link"]',
    ];
    let count = 0;
    for (const sel of selectors) {
        try { count += document.querySelectorAll(sel).length; } catch(_) {}
    }
    return count;
}
"""


class PageDiff:
    """Detect if a page changed after an action."""

    def snapshot(self, page) -> dict:
        """Capture current page state.

        Returns:
            Dict with url, title, dom_hash, and element_count.
        """
        url = page.url
        title = page.title()
        dom_structure = page.evaluate(_DOM_HASH_JS)
        dom_hash = hashlib.sha256(dom_structure.encode("utf-8", errors="replace")).hexdigest()[:16]
        element_count = page.evaluate(_COUNT_INTERACTIVE_JS)

        return {
            "url": url,
            "title": title,
            "dom_hash": dom_hash,
            "element_count": element_count,
        }

    def changed(self, before: dict, after: dict) -> bool:
        """Return True if the page meaningfully changed.

        Checks URL, title, DOM structure hash, and significant element count shift.
        """
        if before["url"] != after["url"]:
            return True
        if before["title"] != after["title"]:
            return True
        if before["dom_hash"] != after["dom_hash"]:
            return True
        # Element count shift of more than 20% or at least 3 elements
        diff = abs(before["element_count"] - after["element_count"])
        if diff >= 3:
            return True
        total = max(before["element_count"], 1)
        if diff / total > 0.2:
            return True
        return False

    def describe_change(self, before: dict, after: dict) -> str:
        """Human-readable description of what changed between two snapshots."""
        changes: list[str] = []

        if before["url"] != after["url"]:
            changes.append(f'URL changed: {before["url"]} -> {after["url"]}')
        if before["title"] != after["title"]:
            changes.append(f'Title changed: "{before["title"]}" -> "{after["title"]}"')
        if before["dom_hash"] != after["dom_hash"]:
            changes.append("Page structure changed")
        elem_diff = after["element_count"] - before["element_count"]
        if elem_diff != 0:
            direction = "more" if elem_diff > 0 else "fewer"
            changes.append(f"{abs(elem_diff)} {direction} interactive elements")

        if not changes:
            return "No meaningful changes detected"
        return "; ".join(changes)
