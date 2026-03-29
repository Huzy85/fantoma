"""MutationObserver injection for precise DOM change tracking.

Injects a MutationObserver before each action, collects mutations after.
Reports what changed: added nodes, removed nodes, changed attributes, new text.

Limitations:
- Only fires in the same document (full navigations lose mutations — OK, we detect those via URL).
- Shadow DOM mutations skipped for v0.6.
- Observer disconnected after collection to prevent memory leaks.
"""
import logging

log = logging.getLogger("fantoma.observer")

_INJECT_JS = """() => {
    if (window.__fantoma_observer) {
        window.__fantoma_observer.disconnect();
    }
    window.__fantoma_mutations = [];

    const observer = new MutationObserver((mutations) => {
        for (const m of mutations) {
            if (m.type === 'childList') {
                for (const node of m.addedNodes) {
                    if (node.nodeType === 1) {
                        const tag = node.tagName.toLowerCase();
                        const cls = node.className && typeof node.className === 'string'
                            ? '.' + node.className.split(/\\s+/)[0] : '';
                        window.__fantoma_mutations.push({type: 'added', value: tag + cls});
                    } else if (node.nodeType === 3 && node.textContent.trim()) {
                        window.__fantoma_mutations.push({type: 'text', value: node.textContent.trim().slice(0, 100)});
                    }
                }
                for (const node of m.removedNodes) {
                    if (node.nodeType === 1) {
                        const tag = node.tagName.toLowerCase();
                        const cls = node.className && typeof node.className === 'string'
                            ? '.' + node.className.split(/\\s+/)[0] : '';
                        window.__fantoma_mutations.push({type: 'removed', value: tag + cls});
                    }
                }
            } else if (m.type === 'attributes') {
                const el = m.target;
                const tag = el.tagName.toLowerCase();
                const id = el.id ? '#' + el.id : '';
                window.__fantoma_mutations.push({
                    type: 'attr',
                    element: tag + id,
                    attr: m.attributeName,
                    value: el.getAttribute(m.attributeName) || ''
                });
            }
        }
    });

    observer.observe(document.body, {
        childList: true,
        attributes: true,
        subtree: true,
        attributeFilter: ['aria-invalid', 'aria-hidden', 'class', 'disabled', 'hidden', 'style']
    });

    window.__fantoma_observer = observer;
}"""

_COLLECT_JS = """() => {
    if (window.__fantoma_observer) {
        window.__fantoma_observer.disconnect();
        window.__fantoma_observer = null;
    }

    const raw = window.__fantoma_mutations || [];
    window.__fantoma_mutations = [];

    const added = [];
    const removed = [];
    const changed_attrs = [];
    const text_changes = [];

    for (const m of raw) {
        if (m.type === 'added') added.push(m.value);
        else if (m.type === 'removed') removed.push(m.value);
        else if (m.type === 'attr') changed_attrs.push({element: m.element, attr: m.attr, value: m.value});
        else if (m.type === 'text') text_changes.push(m.value);
    }

    return {added, removed, changed_attrs, text_changes};
}"""

_MAX_ADDED = 10
_MAX_REMOVED = 10
_MAX_ATTRS = 5
_MAX_TEXT = 5


def inject_observer(page) -> None:
    """Inject a MutationObserver that records changes. Call BEFORE the action."""
    try:
        page.evaluate(_INJECT_JS)
    except Exception as e:
        log.debug("Failed to inject observer: %s", e)


def collect_mutations(page) -> dict:
    """Collect recorded mutations. Call AFTER the action.

    Returns:
        dict with added, removed, changed_attrs, text_changes lists
    """
    try:
        result = page.evaluate(_COLLECT_JS)
        if not isinstance(result, dict):
            return _empty_result()
        result["added"] = result.get("added", [])[:_MAX_ADDED]
        result["removed"] = result.get("removed", [])[:_MAX_REMOVED]
        result["changed_attrs"] = result.get("changed_attrs", [])[:_MAX_ATTRS]
        result["text_changes"] = result.get("text_changes", [])[:_MAX_TEXT]
        return result
    except Exception as e:
        log.debug("Failed to collect mutations: %s", e)
        return _empty_result()


def format_mutations(mutations: dict) -> str:
    """Format mutations into a compact string for the LLM prompt.
    Returns empty string if nothing interesting happened.
    """
    parts = []
    if mutations["added"]:
        parts.append(f"Added: {', '.join(mutations['added'][:5])}")
    if mutations["removed"]:
        parts.append(f"Removed: {', '.join(mutations['removed'][:5])}")
    if mutations["text_changes"]:
        parts.append(f"New text: {'; '.join(mutations['text_changes'][:3])}")
    if mutations["changed_attrs"]:
        attr_strs = [f"{a['element']}.{a['attr']}={a['value']}" for a in mutations["changed_attrs"][:3]]
        parts.append(f"Changed: {', '.join(attr_strs)}")
    return " | ".join(parts)


def _empty_result() -> dict:
    return {"added": [], "removed": [], "changed_attrs": [], "text_changes": []}
