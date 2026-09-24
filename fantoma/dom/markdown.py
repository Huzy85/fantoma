"""Page → clean Markdown, the format LLMs read best.

Runs one script in the page that walks the rendered DOM (open shadow roots
included) and writes Markdown: headings, paragraphs, lists, tables, code,
quotes and links. No LLM is involved, so it costs nothing and works with no
model configured at all.

Two things it deliberately leaves out:

* Text a person cannot see. display:none, visibility:hidden, opacity:0,
  aria-hidden, the `hidden` attribute, zero-size or clipped boxes and text
  pushed far off-screen. This is where injected instructions aimed at AI
  agents usually hide, and none of it is content a reader of the page gets.
* Page furniture, when `main_only` is on: nav, header, footer, aside and
  their ARIA equivalents, plus cookie/consent banners.

The same walk also returns every link on the page, absolute and
de-duplicated, so a caller can crawl without asking a model to find them.
"""

from __future__ import annotations

import logging
import re

log = logging.getLogger("fantoma.markdown")

# Characters that render as nothing but still reach the model: zero-width
# spaces and joiners, the BOM, and bidi overrides that can reorder text so a
# human reviewer and the model read different things.
_INVISIBLE = re.compile(
    "[​‌‍⁠⁡⁢⁣⁤﻿"
    "‪‫‬‭‮⁦⁧⁨⁩­]"
)

_JS = r"""
(opts) => {
  // Compared against tagOf(), which upper-cases: SVG elements report a
  // lower-case tagName, so an 'SVG' entry never matched and icon <title>
  // and <style> text leaked into the output.
  const SKIP_TAGS = new Set(['SCRIPT','STYLE','NOSCRIPT','TEMPLATE','SVG','MATH','CANVAS',
    'HEAD','META','LINK','OBJECT','EMBED','IFRAME','FRAME','AUDIO','VIDEO','MAP',
    'OPTION','BUTTON','INPUT','TEXTAREA']);
  const tagOf = (el) => (el.tagName || '').toUpperCase();
  // Button text is furniture ("Accept", "Menu") except where it IS the
  // heading, as in the standard accordion markup <h3><button>Question</button></h3>.
  const LABEL_PARENTS = 'h1,h2,h3,h4,h5,h6,summary,dt,th,caption';
  const CHROME_TAGS = new Set(['NAV','HEADER','FOOTER','ASIDE']);
  const CHROME_ROLES = new Set(['navigation','banner','contentinfo','complementary',
    'search','menubar','menu','toolbar','dialog','alertdialog']);
  const CONSENT = /(cookie|consent|gdpr|onetrust|cmp-|didomi|usercentrics|truste)/i;
  const BLOCK = new Set(['P','DIV','SECTION','ARTICLE','MAIN','FORM','FIGURE',
    'FIGCAPTION','ADDRESS','DETAILS','SUMMARY','DL','DT','DD','CENTER','BODY']);
  const links = [];
  const seenLinks = new Set();
  let hidden = 0;

  const abs = (href) => { try { return new URL(href, document.baseURI).href; } catch (e) { return ''; } };
  const esc = (t) => t.replace(/\s+/g, ' ');

  function isHidden(el) {
    if (el.hidden || el.getAttribute('aria-hidden') === 'true') return true;
    const cs = getComputedStyle(el);
    if (cs.display === 'none' || cs.visibility === 'hidden' || cs.visibility === 'collapse') return true;
    if (parseFloat(cs.opacity) === 0) return true;
    if (parseFloat(cs.fontSize) === 0) return true;
    const clip = cs.clip || '';
    if (/rect\(\s*0(px)?[\s,]+0(px)?[\s,]+0(px)?[\s,]+0(px)?\s*\)/.test(clip)) return true;
    if (cs.clipPath && /inset\(\s*(50%|100%)/.test(cs.clipPath)) return true;
    const r = el.getBoundingClientRect();
    // Zero-size only hides what is clipped to it. A zero-size absolutely
    // positioned wrapper often holds visible absolutely positioned children.
    if ((r.width === 0 || r.height === 0) && cs.overflow === 'hidden')
      return true;
    if (cs.position === 'absolute' || cs.position === 'fixed') {
      if (r.right < -500 || r.bottom < -500 || r.left > 20000) return true;
    }
    if (parseFloat(cs.textIndent) < -500) return true;
    return false;
  }

  function isChrome(el) {
    // An explicit selector is the caller saying which part they want, even
    // if that part is a nav or a footer.
    if (!opts.mainOnly || opts.selector) return false;
    const t = tagOf(el);
    if (CHROME_TAGS.has(t)) {
      // A <header> inside an article is the article's own header, not site chrome.
      if ((t === 'HEADER' || t === 'FOOTER') && el.closest('article, main, [role=main]'))
        return false;
      return true;
    }
    const role = (el.getAttribute('role') || '').toLowerCase();
    if (CHROME_ROLES.has(role)) return true;
    const idc = (el.id || '') + ' ' + (typeof el.className === 'string' ? el.className : '');
    if (CONSENT.test(idc) && (getComputedStyle(el).position === 'fixed' || /banner|modal|popup|notice/i.test(idc)))
      return true;
    return false;
  }

  function inline(node) {
    let out = '';
    for (const child of childrenOf(node)) out += inlineNode(child);
    return out;
  }

  // What is rendered, in order. A shadow host renders its shadow tree only
  // (its light children appear where slots place them); a slot renders what
  // is assigned to it, or its own fallback content when nothing is. Walking
  // both the shadow tree and the light children duplicated slotted text at
  // every nesting level (2^depth copies) and leaked unslotted, unrendered text.
  function childrenOf(node) {
    if (node.shadowRoot) return Array.from(node.shadowRoot.childNodes);
    if (tagOf(node) === 'SLOT' && node.assignedNodes) {
      const assigned = node.assignedNodes({flatten: true});
      if (assigned.length) return assigned;
    }
    return Array.from(node.childNodes || []);
  }

  function inlineNode(n) {
    if (n.nodeType === 3) return esc(n.textContent);
    if (n.nodeType !== 1) return '';
    const el = n;
    const t = tagOf(el);
    if (t === 'BUTTON' && el.closest(LABEL_PARENTS)) {
      if (isHidden(el)) { hidden++; return ''; }
      return inline(el);
    }
    if (SKIP_TAGS.has(t)) return '';
    if (isHidden(el)) { hidden++; return ''; }
    if (t === 'BR') return '\n';
    if (t === 'SELECT') {
      // A dropdown's current choice is page state a reader needs ("which
      // plan is selected"); its unchosen options are not.
      const o = el.selectedOptions && el.selectedOptions[0];
      const label = o ? (o.label || o.textContent || '').trim() : '';
      return label ? ` [${label}] ` : '';
    }
    if (t === 'IMG') {
      const alt = (el.getAttribute('alt') || '').trim();
      return (opts.images && alt) ? `![${alt}](${abs(el.getAttribute('src') || '')})` : (alt ? alt : '');
    }
    const inner = inline(el);
    const text = inner.trim();
    if (!text) return inner;
    if (t === 'A') {
      const href = el.getAttribute('href') || '';
      const url = href && !href.startsWith('javascript:') ? abs(href) : '';
      if (url && !seenLinks.has(url)) { seenLinks.add(url); links.push({text: text.replace(/\s+/g, ' ').slice(0, 200), url}); }
      return (opts.links && url && !url.startsWith('mailto:')) ? `[${text}](${url})` : inner;
    }
    if (t === 'STRONG' || t === 'B') return `**${text}**`;
    if (t === 'EM' || t === 'I') return `*${text}*`;
    if (t === 'CODE' || t === 'KBD' || t === 'SAMP') return '`' + text + '`';
    if (t === 'DEL' || t === 'S') return `~~${text}~~`;
    return inner;
  }

  function table(el) {
    const rows = [];
    for (const tr of el.querySelectorAll('tr')) {
      if (tr.closest('table') !== el || isHidden(tr)) continue;
      const cells = [];
      for (const c of tr.children) {
        if ((tagOf(c) === 'TD' || tagOf(c) === 'TH') && !isHidden(c))
          cells.push(inline(c).replace(/\s+/g, ' ').replace(/\|/g, '\\|').trim());
      }
      if (cells.length) rows.push(cells);
    }
    if (!rows.length) return '';
    const width = Math.max(...rows.map(r => r.length));
    const pad = (r) => r.concat(Array(width - r.length).fill(''));
    const lines = ['| ' + pad(rows[0]).join(' | ') + ' |', '|' + ' --- |'.repeat(width)];
    for (const r of rows.slice(1)) lines.push('| ' + pad(r).join(' | ') + ' |');
    return lines.join('\n');
  }

  function block(node, out, depth) {
    for (const n of childrenOf(node)) {
      if (n.nodeType === 3) {
        const t = esc(n.textContent);
        if (t.trim()) out.push({inline: t});
        continue;
      }
      if (n.nodeType !== 1) continue;
      const el = n;
      const t = tagOf(el);
      if (SKIP_TAGS.has(t)) continue;
      if (isHidden(el)) { hidden++; continue; }
      if (isChrome(el)) continue;
      if (/^H[1-6]$/.test(t)) {
        const text = inline(el).replace(/\s+/g, ' ').trim();
        if (text) out.push({block: '#'.repeat(+t[1]) + ' ' + text});
      } else if (t === 'UL' || t === 'OL') {
        let i = 1;
        const items = [];
        for (const li of el.children) {
          if (tagOf(li) !== 'LI' || isHidden(li)) continue;
          const sub = [];
          const own = [];
          for (const c of childrenOf(li)) {
            if (c.nodeType === 1 && (tagOf(c) === 'UL' || tagOf(c) === 'OL')) {
              const nested = []; block({childNodes: [c], tagName: 'DIV'}, nested, depth + 1);
              sub.push(...nested.map(x => x.block || x.inline));
            } else own.push(inlineNode(c));
          }
          const text = own.join('').replace(/\s+/g, ' ').trim();
          const indent = '  '.repeat(depth);
          if (text) items.push(indent + (t === 'OL' ? `${i++}. ` : '- ') + text);
          items.push(...sub);
        }
        if (items.length) out.push({block: items.join('\n')});
      } else if (t === 'TABLE') {
        const md = table(el);
        if (md) out.push({block: md});
      } else if (t === 'PRE') {
        const code = el.innerText.replace(/\n+$/, '');
        if (code.trim()) out.push({block: '```\n' + code + '\n```'});
      } else if (t === 'BLOCKQUOTE') {
        const inner = []; block(el, inner, depth);
        const text = render(inner);
        if (text) out.push({block: text.split('\n').map(l => '> ' + l).join('\n')});
      } else if (t === 'HR') {
        out.push({block: '---'});
      } else if (BLOCK.has(t) || t === 'LI' || getComputedStyle(el).display.startsWith('block')
                 || getComputedStyle(el).display === 'flex' || getComputedStyle(el).display === 'grid') {
        const inner = []; block(el, inner, depth);
        out.push({block: render(inner)});
      } else {
        const text = inlineNode(el);
        if (text.trim()) out.push({inline: text});
      }
    }
  }

  // Runs of inline text only; blocks such as code keep their spacing.
  const tidy = (t) => t.replace(/[ \t]{2,}/g, ' ').replace(/ *\n */g, '\n').trim();

  function render(items) {
    const parts = [];
    let buf = '';
    for (const it of items) {
      if (it.inline !== undefined) { buf += it.inline; continue; }
      if (buf.trim()) parts.push(tidy(buf));
      buf = '';
      if (it.block && it.block.trim()) parts.push(it.block.trim());
    }
    if (buf.trim()) parts.push(tidy(buf));
    return parts.join('\n\n');
  }

  let root = document.body;
  if (opts.selector) {
    // A bad or unmatched selector is the caller's error to see. Falling back
    // to the whole page returned content they did not ask for.
    try { root = document.querySelector(opts.selector); }
    catch (e) { return {error: `invalid selector: ${opts.selector}`}; }
    if (!root) return {error: `no element matches selector: ${opts.selector}`};
  } else if (opts.mainOnly) {
    const mains = document.querySelectorAll('main, [role=main]');
    const visibleMains = Array.from(mains).filter(m => !isHidden(m) && m.innerText.trim().length > 50);
    if (visibleMains.length === 1) root = visibleMains[0];
    else {
      const arts = Array.from(document.querySelectorAll('article')).filter(a => !isHidden(a));
      if (arts.length === 1 && arts[0].innerText.trim().length > 200) root = arts[0];
    }
  }
  // Walk the root itself, not only its children, so a root that is a table,
  // list or <pre> (a selector pointing straight at one) keeps its formatting.
  const items = [];
  if (root) block({childNodes: [root], tagName: 'DIV'}, items, 0);
  let md = render(items);
  // A main landmark that yields almost nothing (an app shell, a login wall)
  // is worse than the whole page. Fall back rather than return a stub.
  if (opts.mainOnly && !opts.selector && root !== document.body && md.length < 200) {
    const all = []; opts.mainOnly = false; block(document.body, all, 0); opts.mainOnly = true;
    const whole = render(all);
    if (whole.length > md.length) md = whole;
  }
  const desc = document.querySelector('meta[name=description], meta[property="og:description"]');
  return {
    error: '',
    title: document.title || '',
    url: location.href,
    description: desc ? (desc.getAttribute('content') || '') : '',
    markdown: md,
    links: links,
    hidden_removed: hidden,
  };
}
"""


def strip_invisible(text: str) -> str:
    """Remove zero-width and bidi-control characters."""
    return _INVISIBLE.sub("", text or "")


def _tidy(md: str) -> str:
    md = strip_invisible(md)
    md = re.sub(r"[ \t]+\n", "\n", md)
    md = re.sub(r"\n{3,}", "\n\n", md)
    return md.strip()


_REVEAL_JS = r"""
async (steps) => {
  // Walk the page down and back so scroll-triggered content (fade-in
  // sections, lazy lists) is rendered before it is read.
  const pause = (ms) => new Promise(r => setTimeout(r, ms));
  const start = window.scrollY;
  const h = () => Math.max(document.documentElement.scrollHeight, document.body ? document.body.scrollHeight : 0);
  for (let i = 1; i <= steps; i++) {
    window.scrollTo(0, h() * i / steps);
    await pause(120);
  }
  window.scrollTo(0, start);
  await pause(150);
}
"""


def reveal(page, steps: int = 8) -> None:
    """Scroll through the page so reveal-on-scroll content is rendered."""
    try:
        page.evaluate(_REVEAL_JS, steps)
    except Exception as e:
        log.debug("reveal scroll failed: %s", e)


def page_to_markdown(
    page,
    main_only: bool = True,
    include_links: bool = True,
    include_images: bool = False,
    selector: str = "",
    max_chars: int = 0,
    scroll: bool = False,
) -> dict:
    """Render the current page as Markdown.

    Returns a dict with: title, url, description, markdown, links (list of
    {text, url}), hidden_removed (how many invisible elements were dropped),
    truncated (bool) and error ("" on success). `max_chars` of 0 means no
    limit. `scroll` walks the page first so scroll-revealed content counts.

    Never raises. When the page cannot be walked (a bad selector, or a page
    that breaks the script) `markdown` is empty and `error` says why. There
    is deliberately no raw-text fallback: raw text includes the invisible
    text this function exists to remove, and a hostile page can force the
    fallback on purpose.
    """
    opts = {
        "mainOnly": bool(main_only),
        "links": bool(include_links),
        "images": bool(include_images),
        "selector": selector or "",
    }
    if scroll:
        reveal(page)
    error = ""
    try:
        result = page.evaluate(_JS, opts)
        if not isinstance(result, dict):
            raise ValueError(f"unexpected result type {type(result).__name__}")
        error = str(result.get("error") or "")
        if not error and not isinstance(result.get("markdown"), str):
            raise ValueError("no markdown in result")
    except Exception as e:
        log.warning("markdown walk failed: %s", e)
        error = f"could not read page: {e}"
        result = {}
    if error:
        try:
            title, url = page.title(), page.url
        except Exception:
            title, url = "", ""
        result = {"title": title if isinstance(title, str) else "",
                  "url": url if isinstance(url, str) else "", "markdown": ""}

    md = _tidy(result.get("markdown") or "")
    truncated = False
    if max_chars and len(md) > max_chars:
        cut = md.rfind("\n\n", 0, max_chars)
        md = md[: cut if cut > max_chars * 0.6 else max_chars].rstrip()
        truncated = True

    links = []
    for link in result.get("links") or []:
        url = (link.get("url") or "").strip()
        if url.startswith(("http://", "https://")):
            links.append({"text": strip_invisible(link.get("text", "")).strip(), "url": url})

    return {
        "title": strip_invisible(result.get("title") or "").strip(),
        "url": result.get("url") or "",
        "description": strip_invisible(result.get("description") or "").strip(),
        "markdown": md,
        "links": links,
        "hidden_removed": int(result.get("hidden_removed") or 0),
        "error": error,
        "truncated": truncated,
    }


def aria_text_fallback(page) -> str:
    """Page text from the accessibility tree, for when the Markdown walk fails.

    The accessibility tree already leaves out display:none and aria-hidden
    content, so this is a safer fallback than raw innerText, though it does
    not catch every trick the walk does (opacity:0, off-screen text).
    """
    try:
        from fantoma.dom.accessibility import extract_aria_content
        text = extract_aria_content(page)
    except Exception as e:
        log.debug("aria fallback failed: %s", e)
        return ""
    return strip_invisible(text if isinstance(text, str) else "")
