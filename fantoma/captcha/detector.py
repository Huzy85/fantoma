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
        # Check for type-specific elements first (class, id, src, iframe) — NOT
        # data-sitekey, which is shared across reCAPTCHA/hCaptcha/Turnstile and
        # would match whichever type we check first.
        for captcha_type, signatures in CAPTCHA_SIGNATURES.items():
            for sig in signatures:
                try:
                    element = page.query_selector(
                        f'[class*="{sig}"], [id*="{sig}"], [src*="{sig}"], '
                        f'iframe[src*="{sig}"]'
                    )
                    if element:
                        log.info("Detected %s CAPTCHA (element found)", captcha_type)
                        return {"type": captcha_type, "signature": sig}
                except Exception:
                    pass

        # Fallback: data-sitekey present but no type-specific match above.
        # Disambiguate by checking which provider's scripts/iframes are on the page.
        try:
            has_sitekey = page.query_selector('[data-sitekey]')
            if has_sitekey:
                resolved = self._resolve_sitekey_type(page)
                log.info("Detected %s CAPTCHA (via data-sitekey fallback)", resolved)
                return {"type": resolved, "signature": "data-sitekey"}
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

    def _resolve_sitekey_type(self, page) -> str:
        """Determine CAPTCHA type when only a data-sitekey element is present.

        Checks for provider-specific scripts and iframes to disambiguate.
        Falls back to 'recaptcha' if nothing else matches.
        """
        try:
            provider = page.evaluate("""() => {
                if (document.querySelector('script[src*="hcaptcha.com"]')
                    || document.querySelector('iframe[src*="hcaptcha.com"]'))
                    return 'hcaptcha';
                if (document.querySelector('script[src*="challenges.cloudflare.com"]')
                    || document.querySelector('iframe[src*="challenges.cloudflare.com"]'))
                    return 'turnstile';
                if (document.querySelector('script[src*="recaptcha"]')
                    || document.querySelector('script[src*="google.com/recaptcha"]')
                    || document.querySelector('iframe[src*="recaptcha"]'))
                    return 'recaptcha';
                return null;
            }""")
            if provider:
                return provider
        except Exception:
            pass
        return "recaptcha"

    def is_blocked(self, page) -> bool:
        """Check if the page shows a block/challenge page (Cloudflare, DataDome, etc.)."""
        indicators = [
            "access denied", "please verify", "checking your browser",
            "ray id", "attention required", "please complete the security check",
            "just a moment", "enable javascript and cookies",
        ]
        text = page.inner_text("body").lower()[:2000]
        return any(ind in text for ind in indicators)
