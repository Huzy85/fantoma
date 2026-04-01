"""Example: drive the browser step by step with the Tool API.

The Tool API gives you full control. Feed the ARIA tree to your own
LLM, parse the response, call browser.click() / browser.type_text().
No built-in LLM needed.
"""
from fantoma import Fantoma

browser = Fantoma()
state = browser.start("https://news.ycombinator.com")

print("Page:", state["title"])
print("ARIA tree (first 500 chars):")
print(state["aria_tree"][:500])
print(f"\nElements on page: feed this to your LLM and ask what to click.")

# Click the first link (element 0)
result = browser.click(0)
print(f"\nClicked: success={result['success']}")
print(f"New page: {result['state']['title']}")

browser.stop()
