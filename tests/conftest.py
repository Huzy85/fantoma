"""Pytest configuration for Fantoma test suite.

Excludes live/integration test files from automatic collection.
These files hit real websites, require network access, API keys,
and running services. Run them manually:
    python tests/live_reddit_test.py
    python tests/real_site_test.py
    python tests/real_signup_test.py
    python tests/full_signup_test.py
    python tests/scenario_test_deepseek.py
"""

collect_ignore = [
    "live_reddit_test.py",
    "real_site_test.py",
    "real_signup_test.py",
    "full_signup_test.py",
    "scenario_test_deepseek.py",
]
