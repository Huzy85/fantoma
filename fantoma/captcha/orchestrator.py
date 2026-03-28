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
        from fantoma.captcha.sitekey import extract_sitekey, detect_invisible, detect_version

        solver = APICaptchaSolver(self.config.captcha.api, self.config.captcha.key)
        site_key = extract_sitekey(page, captcha_type)

        if not site_key:
            log.warning("No sitekey found for %s on %s — cannot call API solver", captcha_type, page.url)
            return False

        # Validate sitekey length — reCAPTCHA keys are 40 chars, hCaptcha 36 (UUID)
        expected_len = {"recaptcha": 40, "hcaptcha": 36}
        if captcha_type in expected_len and len(site_key) != expected_len[captcha_type]:
            log.warning("Sitekey length %d doesn't match expected %d for %s — skipping API call",
                        len(site_key), expected_len[captcha_type], captcha_type)
            return False

        url = page.url
        token = None

        if captcha_type == "recaptcha":
            version = detect_version(page)
            if version == "v3":
                token = solver.solve_recaptcha_v3(site_key, url)
            else:
                is_invisible = detect_invisible(page, captcha_type)
                token = solver.solve_recaptcha_v2(site_key, url, is_invisible=is_invisible)
        elif captcha_type == "hcaptcha":
            token = solver.solve_hcaptcha(site_key, url)
        elif captcha_type == "turnstile":
            token = solver.solve_turnstile(site_key, url)

        if token:
            log.info("CAPTCHA solved via %s", self.config.captcha.api)
            return inject_token(page, token, captcha_type)
        return False

    def _solve_with_human(self, screenshot_bytes: bytes, captcha_type: str) -> bool:
        """Send to webhook for human solving."""
        from fantoma.captcha.human_solver import HumanCaptchaSolver
        solver = HumanCaptchaSolver(self.config.captcha.webhook, timeout=self.config.captcha.human_timeout)
        solution = solver.solve(screenshot_bytes, captcha_type)
        if solution:
            log.info("CAPTCHA solved by human")
            return True
        return False


def inject_token(page, token: str, captcha_type: str) -> bool:
    """Inject a solved CAPTCHA token into the page.

    Tries textarea/hidden input first (inline CAPTCHAs), then
    falls back to callback invocation (iframe CAPTCHAs like Reddit).

    Uses Playwright's page.evaluate(script, arg) to pass the token safely
    rather than f-string interpolation, preventing JS injection.
    """
    try:
        if captcha_type in ("recaptcha", "hcaptcha"):
            field = "g-recaptcha-response" if captcha_type == "recaptcha" else "h-captcha-response"

            # Try 1: Set textarea/hidden input value (inline CAPTCHAs)
            found = page.evaluate("""(token) => {
                const field = '%s';
                const el = document.querySelector(
                    '[name="' + field + '"], #' + field + ', textarea#' + field
                );
                if (el) {
                    el.value = token;
                    el.innerHTML = token;
                    return true;
                }
                for (const a of document.querySelectorAll('textarea')) {
                    if (a.name.includes('captcha') || a.id.includes('captcha')) {
                        a.value = token;
                        return true;
                    }
                }
                return false;
            }""" % field, token)

            if found:
                log.info("Token injected via form field")
                _try_callback(page, token, captcha_type)
                return True

            # Try 2: Invoke callback + create textarea (iframe CAPTCHAs)
            if captcha_type == "recaptcha":
                invoked = page.evaluate("""(token) => {
                    if (typeof ___grecaptcha_cfg !== 'undefined' && ___grecaptcha_cfg.clients) {
                        for (const [, client] of Object.entries(___grecaptcha_cfg.clients)) {
                            const json = JSON.stringify(client);
                            const match = json.match(/"callback"\\s*:\\s*"([^"]+)"/);
                            if (match && typeof window[match[1]] === 'function') {
                                window[match[1]](token);
                                return true;
                            }
                        }
                    }
                    const form = document.querySelector('form');
                    if (form) {
                        let resp = document.querySelector('textarea[name="g-recaptcha-response"]');
                        if (!resp) {
                            resp = document.createElement('textarea');
                            resp.name = 'g-recaptcha-response';
                            resp.id = 'g-recaptcha-response';
                            resp.style.display = 'none';
                            form.appendChild(resp);
                        }
                        resp.value = token;
                        resp.innerHTML = token;
                        return true;
                    }
                    return false;
                }""", token)
                if invoked:
                    log.info("Token injected via reCAPTCHA callback/form creation")
                    return True
            else:
                invoked = page.evaluate("""(token) => {
                    if (typeof hcaptcha !== 'undefined') {
                        try { hcaptcha.setResponse(token); return true; } catch(e) {}
                    }
                    return false;
                }""", token)
                if invoked:
                    log.info("Token injected via hCaptcha API")
                    return True

        elif captcha_type == "turnstile":
            invoked = page.evaluate("""(token) => {
                const input = document.querySelector(
                    '[name="cf-turnstile-response"], input[name*="turnstile"]'
                );
                if (input) { input.value = token; return true; }
                const widget = document.querySelector('.cf-turnstile');
                if (widget) {
                    const cb = widget.getAttribute('data-callback');
                    if (cb && typeof window[cb] === 'function') {
                        window[cb](token);
                        return true;
                    }
                }
                const form = document.querySelector('form');
                if (form) {
                    const inp = document.createElement('input');
                    inp.type = 'hidden';
                    inp.name = 'cf-turnstile-response';
                    inp.value = token;
                    form.appendChild(inp);
                    return true;
                }
                return false;
            }""", token)
            if invoked:
                log.info("Token injected for Turnstile")
                return True

        log.warning("Could not inject %s token into page", captcha_type)
        return False
    except Exception as e:
        log.error("Token injection failed: %s", e)
        return False


def _try_callback(page, token: str, captcha_type: str) -> None:
    """Best-effort callback invocation after setting form field."""
    try:
        if captcha_type == "recaptcha":
            page.evaluate("""(token) => {
                if (typeof ___grecaptcha_cfg !== 'undefined' && ___grecaptcha_cfg.clients) {
                    for (const [, client] of Object.entries(___grecaptcha_cfg.clients)) {
                        const json = JSON.stringify(client);
                        const match = json.match(/"callback"\\s*:\\s*"([^"]+)"/);
                        if (match && typeof window[match[1]] === 'function') {
                            window[match[1]](token);
                        }
                    }
                }
            }""", token)
    except Exception:
        pass
