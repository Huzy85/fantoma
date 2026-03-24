"""CAPTCHA fallback — send to a human via webhook (Telegram, Discord, Slack, custom URL)."""
import json
import logging
import time
import httpx

log = logging.getLogger("fantoma.captcha.human")


class HumanCaptchaSolver:
    """Send CAPTCHA screenshots to a human via webhook and wait for solution."""

    def __init__(self, webhook_url: str, timeout: int = 300):
        self.webhook_url = webhook_url
        self.timeout = timeout  # How long to wait for human response

    def solve(self, screenshot_bytes: bytes, captcha_type: str = "unknown") -> str | None:
        """Send CAPTCHA to human, wait for solution.

        The webhook receives a POST with:
        {"type": "captcha_request", "captcha_type": "recaptcha", "image": "base64..."}

        Expected response:
        {"solution": "the text or token"}
        """
        import base64
        image_b64 = base64.b64encode(screenshot_bytes).decode()

        try:
            resp = httpx.post(self.webhook_url, json={
                "type": "captcha_request",
                "captcha_type": captcha_type,
                "image": image_b64,
            }, timeout=30)

            if resp.status_code != 200:
                log.error("Webhook returned %d", resp.status_code)
                return None

            log.info("CAPTCHA sent to human, waiting up to %ds for solution...", self.timeout)

            # The webhook might return the solution immediately (synchronous)
            # or we might need to poll a callback URL
            data = resp.json()
            if "solution" in data:
                return data["solution"]

            # If async, poll the callback URL
            callback_url = data.get("callback_url") or data.get("poll_url")
            if callback_url:
                deadline = time.time() + self.timeout
                while time.time() < deadline:
                    time.sleep(10)
                    poll_resp = httpx.get(callback_url, timeout=10)
                    poll_data = poll_resp.json()
                    if poll_data.get("solution"):
                        log.info("Human solved CAPTCHA")
                        return poll_data["solution"]
                    if poll_data.get("status") == "expired":
                        break

            log.warning("Human CAPTCHA solving timed out")
            return None
        except Exception as e:
            log.error("Human solver error: %s", e)
            return None
