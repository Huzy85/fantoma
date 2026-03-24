"""CAPTCHA solving via paid APIs (CapSolver, 2Captcha, Anti-Captcha). User provides their own key."""
import json
import logging
import time
import httpx

log = logging.getLogger("fantoma.captcha.api")

PROVIDERS = {
    "capsolver": {
        "create_url": "https://api.capsolver.com/createTask",
        "result_url": "https://api.capsolver.com/getTaskResult",
    },
    "2captcha": {
        "create_url": "https://api.2captcha.com/createTask",
        "result_url": "https://api.2captcha.com/getTaskResult",
    },
}


class APICaptchaSolver:
    """Solve CAPTCHAs using external API services."""

    def __init__(self, provider: str, api_key: str):
        if provider not in PROVIDERS:
            raise ValueError(f"Unknown provider '{provider}'. Supported: {list(PROVIDERS.keys())}")
        self.provider = provider
        self.api_key = api_key
        self.config = PROVIDERS[provider]

    def solve_recaptcha_v2(self, site_key: str, page_url: str, timeout: int = 120) -> str | None:
        """Solve reCAPTCHA v2. Returns token or None."""
        return self._solve({
            "type": "ReCaptchaV2TaskProxyLess",
            "websiteURL": page_url,
            "websiteKey": site_key,
        }, timeout)

    def solve_hcaptcha(self, site_key: str, page_url: str, timeout: int = 120) -> str | None:
        """Solve hCaptcha. Returns token or None."""
        return self._solve({
            "type": "HCaptchaTaskProxyLess",
            "websiteURL": page_url,
            "websiteKey": site_key,
        }, timeout)

    def solve_turnstile(self, site_key: str, page_url: str, timeout: int = 120) -> str | None:
        """Solve Cloudflare Turnstile. Returns token or None."""
        return self._solve({
            "type": "AntiTurnstileTaskProxyLess",
            "websiteURL": page_url,
            "websiteKey": site_key,
        }, timeout)

    def _solve(self, task: dict, timeout: int) -> str | None:
        """Submit task and poll for result."""
        try:
            # Create task
            resp = httpx.post(self.config["create_url"], json={
                "clientKey": self.api_key,
                "task": task,
            }, timeout=30)
            data = resp.json()
            task_id = data.get("taskId")
            if not task_id:
                log.error("Failed to create CAPTCHA task: %s", data)
                return None

            # Poll for result
            deadline = time.time() + timeout
            while time.time() < deadline:
                time.sleep(5)
                resp = httpx.post(self.config["result_url"], json={
                    "clientKey": self.api_key,
                    "taskId": task_id,
                }, timeout=30)
                result = resp.json()
                status = result.get("status", "")
                if status == "ready":
                    solution = result.get("solution", {})
                    token = solution.get("gRecaptchaResponse") or solution.get("token") or solution.get("text")
                    log.info("CAPTCHA solved via %s", self.provider)
                    return token
                elif status == "failed":
                    log.error("CAPTCHA solving failed: %s", result)
                    return None

            log.warning("CAPTCHA solving timed out after %ds", timeout)
            return None
        except Exception as e:
            log.error("CAPTCHA API error: %s", e)
            return None
