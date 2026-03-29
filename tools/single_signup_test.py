#!/usr/bin/env python3
"""Single site signup test — full end-to-end with detailed logging."""

import json
import logging
import os
import random
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
)
# Keep HTTP noise down
logging.getLogger("httpcore").setLevel(logging.WARNING)
logging.getLogger("httpx").setLevel(logging.WARNING)

log = logging.getLogger("signup_test")

# Config — use environment variables for secrets
HERCULES = os.environ.get("FANTOMA_LLM_URL", "http://localhost:8080/v1")
DEEPSEEK = os.environ.get("DEEPSEEK_URL", "https://api.deepseek.com/v1")
DEEPSEEK_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
try:
    CAPSOLVER_KEY = json.load(open(Path.home() / ".config/capsolver/config.json"))["api_key"]
except Exception:
    CAPSOLVER_KEY = os.environ.get("CAPSOLVER_KEY", "")

EMAIL_TAG = f"test{random.randint(1000, 9999)}"
BASE_EMAIL = os.environ.get("TEST_EMAIL", "m5aibot@proton.me")
EMAIL = f"{BASE_EMAIL.split('@')[0]}+{EMAIL_TAG}@{BASE_EMAIL.split('@')[1]}"
IMAP_HOST = os.environ.get("IMAP_HOST", "127.0.0.1")
IMAP_PORT = int(os.environ.get("IMAP_PORT", "1143"))
IMAP_PASS = os.environ.get("IMAP_PASS", "")
USERNAME = f"fantoma_test_{random.randint(10000, 99999)}"
PASSWORD = os.environ.get("TEST_PASSWORD", "F4nt0ma_Test_2026!")

log.info("Email: %s", EMAIL)
log.info("Username: %s", USERNAME)

from fantoma import Agent

agent = Agent(
    llm_url=HERCULES,
    escalation=[HERCULES, DEEPSEEK],
    escalation_keys=["", DEEPSEEK_KEY],
    headless=True,
    timeout=120,
    max_steps=30,
    trace=True,
    captcha_api="capsolver",
    captcha_key=CAPSOLVER_KEY,
    email_imap={
        "host": IMAP_HOST,
        "port": IMAP_PORT,
        "user": "m5aibot@proton.me",
        "password": IMAP_PASS,
        "security": "starttls",
    },
)

log.info("=" * 70)
log.info("SITE: Render (render.com)")
log.info("=" * 70)

result = agent.login(
    "https://dashboard.render.com/register",
    email=EMAIL,
    password=PASSWORD,
)

log.info("=" * 70)
log.info("RESULT: success=%s", result.success)
log.info("DATA: %s", result.data)
log.info("ERROR: %s", result.error)
log.info("STEPS: %s", result.steps_taken)
log.info("ESCALATIONS: %s", result.escalations)
log.info("=" * 70)
