"""Read a page as clean Markdown. No LLM needed.

    python examples/read_page.py https://news.ycombinator.com
"""
import sys

from fantoma import Fantoma

url = sys.argv[1] if len(sys.argv) > 1 else "https://example.com"

browser = Fantoma()          # Camoufox by default; Fantoma(browser="chromium") for Chromium
browser.start()
try:
    page = browser.read(url)
finally:
    browser.stop()

if page["blocked"]:
    print(f"Page looks like '{page['blocked']}', not real content.", file=sys.stderr)
print(f"# {page['title']}\n")
print(page["markdown"])
print(f"\n{len(page['links'])} links, {page['hidden_removed']} hidden elements removed")
