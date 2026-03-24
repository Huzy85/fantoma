import logging

log = logging.getLogger("fantoma.captcha")

# Known CAPTCHA signatures in DOM
CAPTCHA_SIGNATURES = {
    "recaptcha": [
        'g-recaptcha', 'grecaptcha', 'recaptcha/api',
        'www.google.com/recaptcha',
    ],
    "hcaptcha": [
        'h-captcha', 'hcaptcha.com/1/api',
    ],
    "turnstile": [
        'cf-turnstile', 'challenges.cloudflare.com/turnstile',
    ],
    "altcha": [
        'altcha', 'altcha-widget',
    ],
    "friendly_captcha": [
        'frc-captcha', 'friendlycaptcha.com',
    ],
}


class CaptchaDetector:
    """Detect if a page contains a CAPTCHA and identify its type."""

    def detect(self, page) -> dict | None:
        """Check page for CAPTCHA. Returns {"type": "recaptcha", "element": ...} or None."""
        # Check for specific CAPTCHA elements (not raw HTML — avoids false positives
        # from comments, scripts, and API references like Wikipedia's MediaWiki)
        for captcha_type, signatures in CAPTCHA_SIGNATURES.items():
            for sig in signatures:
                # Look for actual elements with these signatures (class, id, src, data attributes)
                try:
                    element = page.query_selector(
                        f'[class*="{sig}"], [id*="{sig}"], [src*="{sig}"], '
                        f'[data-sitekey], iframe[src*="{sig}"]'
                    )
                    if element:
                        log.info("Detected %s CAPTCHA (element found)", captcha_type)
                        return {"type": captcha_type, "signature": sig}
                except Exception:
                    pass

        # Check for Cloudflare challenge page (visible text indicators)
        try:
            body_text = page.inner_text("body")[:1000].lower()
            if "checking your browser" in body_text or "please complete the security check" in body_text:
                log.info("Detected Cloudflare challenge page")
                return {"type": "cloudflare", "signature": "challenge_page"}
        except Exception:
            pass

        return None

    def is_blocked(self, page) -> bool:
        """Check if the page shows a block/challenge page (Cloudflare, DataDome, etc.)."""
        indicators = [
            "access denied", "please verify", "checking your browser",
            "ray id", "attention required", "please complete the security check",
            "just a moment", "enable javascript and cookies",
        ]
        text = page.inner_text("body").lower()[:2000]
        return any(ind in text for ind in indicators)
