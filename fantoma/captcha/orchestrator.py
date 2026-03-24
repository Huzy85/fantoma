"""CAPTCHA orchestrator — detects and solves CAPTCHAs using the right strategy.

Tries in order: local proof-of-work → API solver → human fallback.
"""
import logging
import time

from fantoma.captcha.detector import CaptchaDetector
from fantoma.captcha.pow_solver import solve_altcha
from fantoma.config import FantomaConfig

log = logging.getLogger("fantoma.captcha")


class CaptchaOrchestrator:
    """Detect and solve CAPTCHAs on the current page."""

    def __init__(self, config: FantomaConfig):
        self.config = config
        self.detector = CaptchaDetector()

    def handle(self, page, screenshot_fn=None):
        """Check for and handle CAPTCHAs. Returns True if solved, False if not."""
        captcha = self.detector.detect(page)
        if not captcha:
            return False

        captcha_type = captcha["type"]
        log.info("CAPTCHA detected: %s", captcha_type)

        # Tier 1: Local proof-of-work (free)
        if captcha_type == "altcha":
            if self._solve_altcha(page):
                return True

        # Tier 2: API solver (paid)
        if self.config.captcha.api and self.config.captcha.key:
            if self._solve_with_api(page, captcha_type):
                return True

        # Tier 3: Human fallback (webhook)
        if self.config.captcha.webhook and screenshot_fn:
            if self._solve_with_human(screenshot_fn(), captcha_type):
                return True

        log.warning("Could not solve %s CAPTCHA", captcha_type)
        return False

    def _solve_altcha(self, page) -> bool:
        """ALTCHA: click checkbox → browser runs proof-of-work → submit."""
        try:
            cb = page.query_selector('altcha-widget input[type="checkbox"]')
            if not cb:
                time.sleep(3)
                cb = page.query_selector('altcha-widget input[type="checkbox"]')
            if not cb:
                return False

            cb.click()
            page.wait_for_function(
                '() => { const w = document.querySelector("altcha-widget .altcha"); '
                'return w && w.getAttribute("data-state") === "verified"; }',
                timeout=self.config.timeouts.captcha_pow,
            )
            log.info("ALTCHA solved (proof-of-work completed in browser)")

            # Click submit if present
            submit = page.query_selector('button[type="submit"], input[type="submit"]')
            if submit:
                submit.click()
                page.wait_for_load_state("networkidle", timeout=self.config.timeouts.network_idle)
            return True
        except Exception as e:
            log.warning("ALTCHA solve failed: %s", e)
            return False

    def _solve_with_api(self, page, captcha_type: str) -> bool:
        """Solve via CapSolver/2Captcha/Anti-Captcha API."""
        from fantoma.captcha.api_solver import APICaptchaSolver
        solver = APICaptchaSolver(self.config.captcha.api, self.config.captcha.key)

        site_key = page.evaluate("""() => {
            const el = document.querySelector('[data-sitekey]');
            return el ? el.getAttribute('data-sitekey') : null;
        }""")

        if not site_key:
            return False

        url = page.url
        token = None
        if captcha_type == "recaptcha":
            token = solver.solve_recaptcha_v2(site_key, url)
        elif captcha_type == "hcaptcha":
            token = solver.solve_hcaptcha(site_key, url)
        elif captcha_type == "turnstile":
            token = solver.solve_turnstile(site_key, url)

        if token:
            log.info("CAPTCHA solved via %s", self.config.captcha.api)
            page.evaluate(
                f'document.querySelector(\'[name="g-recaptcha-response"], '
                f'[name="h-captcha-response"]\').value = \'{token}\''
            )
            return True
        return False

    def _solve_with_human(self, screenshot_bytes: bytes, captcha_type: str) -> bool:
        """Send to webhook for human solving."""
        from fantoma.captcha.human_solver import HumanCaptchaSolver
        solver = HumanCaptchaSolver(self.config.captcha.webhook)
        solution = solver.solve(screenshot_bytes, captcha_type)
        if solution:
            log.info("CAPTCHA solved by human")
            return True
        return False
