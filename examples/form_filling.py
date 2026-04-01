"""Example: fill out a login form — no LLM needed."""
from fantoma import Fantoma

browser = Fantoma()
browser.start()
result = browser.login(
    "https://the-internet.herokuapp.com/login",
    username="tomsmith",
    password="SuperSecretPassword!",
)
print(f"Success: {result['success']}")
print(f"URL: {result.get('url', 'unknown')}")
browser.stop()
