"""Extract CAPTCHA sitekeys from pages using multiple strategies.

Sites load CAPTCHAs in different ways:
  1. Inline: <div data-sitekey="..."> (simple, rare on modern sites)
  2. Iframe: <iframe src="...recaptcha...?k=SITEKEY"> (Reddit, most sites)
  3. JS globals: window.___grecaptcha_cfg or similar config objects

CAPTCHAs are third-party iframes that load after the page itself, so
extraction retries with a backoff until the widget appears.
"""
import logging
import time

log = logging.getLogger("fantoma.captcha")

# Iframe CSS selectors per CAPTCHA type
_IFRAME_SELECTORS = {
    "recaptcha": 'iframe[src*="recaptcha"], iframe[src*="google.com/recaptcha"]',
    "hcaptcha": 'iframe[src*="hcaptcha.com"]',
    "turnstile": 'iframe[src*="challenges.cloudflare.com"]',
}

# JS global extraction scripts per CAPTCHA type
_JS_GLOBALS = {
    "recaptcha": """() => {
        if (typeof ___grecaptcha_cfg !== 'undefined' && ___grecaptcha_cfg.clients) {
            for (const [, client] of Object.entries(___grecaptcha_cfg.clients)) {
                const json = JSON.stringify(client);
                const match = json.match(/"sitekey"\\s*:\\s*"([^"]+)"/);
                if (match) return match[1];
            }
        }
        const scripts = document.querySelectorAll('script[src*="recaptcha"]');
        for (const s of scripts) {
            const m = s.src.match(/[?&]render=([^&]+)/);
            if (m && m[1] !== 'explicit') return m[1];
        }
        return null;
    }""",
    "hcaptcha": """() => {
        if (typeof hcaptcha !== 'undefined' && hcaptcha._configs) {
            const keys = Object.keys(hcaptcha._configs);
            if (keys.length > 0) return keys[0];
        }
        return null;
    }""",
    "turnstile": """() => {
        if (typeof turnstile !== 'undefined' && turnstile._configs) {
            const keys = Object.keys(turnstile._configs);
            if (keys.length > 0) return keys[0];
        }
        const divs = document.querySelectorAll('.cf-turnstile[data-sitekey]');
        for (const d of divs) {
            const key = d.getAttribute('data-sitekey');
            if (key) return key;
        }
        return null;
    }""",
}


def _try_extract_sitekey(page, captcha_type: str) -> str | None:
    """Single-pass extraction across all three strategies."""
    # Strategy 1: data-sitekey attribute (inline)
    try:
        key = page.evaluate("""() => {
            const el = document.querySelector('[data-sitekey]');
            return el ? el.getAttribute('data-sitekey') : null;
        }""")
        if key:
            log.info("Sitekey found via data-sitekey attribute")
            return key
    except Exception as e:
        log.debug("data-sitekey extraction failed: %s", e)

    # Strategy 2: iframe src URL parameter (k= or sitekey=)
    iframe_sel = _IFRAME_SELECTORS.get(captcha_type)
    if iframe_sel:
        try:
            key = page.evaluate(f"""() => {{
                const iframe = document.querySelector('{iframe_sel}');
                if (!iframe || !iframe.src) return null;
                try {{
                    const url = new URL(iframe.src);
                    return url.searchParams.get('k')
                        || url.searchParams.get('sitekey')
                        || null;
                }} catch(e) {{ return null; }}
            }}""")
            if key:
                log.info("Sitekey found via iframe src URL")
                return key
        except Exception as e:
            log.debug("iframe sitekey extraction failed: %s", e)

    # Strategy 3: JS globals
    js_script = _JS_GLOBALS.get(captcha_type)
    if js_script:
        try:
            key = page.evaluate(js_script)
            if key:
                log.info("Sitekey found via JS globals")
                return key
        except Exception as e:
            log.debug("JS global sitekey extraction failed: %s", e)

    return None


def extract_sitekey(page, captcha_type: str, max_wait: float = 10.0) -> str | None:
    """Try multiple strategies to find the CAPTCHA sitekey, retrying as it loads.

    CAPTCHAs are third-party widgets that load after the main page. This
    retries extraction with increasing delays (0.5s → 1s → 2s → 2s → ...)
    up to max_wait seconds total.

    Args:
        page: Playwright page object.
        captcha_type: One of 'recaptcha', 'hcaptcha', 'turnstile'.
        max_wait: Maximum seconds to wait for the CAPTCHA to appear.

    Returns:
        Sitekey string or None if not found.
    """
    # First try — immediate, no delay
    key = _try_extract_sitekey(page, captcha_type)
    if key:
        return key

    # Retry with backoff: 0.5, 1.0, 2.0, 2.0, 2.0 ...
    delays = [0.5, 1.0, 2.0]
    elapsed = 0.0
    attempt = 0

    while elapsed < max_wait:
        delay = delays[attempt] if attempt < len(delays) else 2.0
        if elapsed + delay > max_wait:
            delay = max_wait - elapsed
            if delay < 0.1:
                break
        log.debug("Waiting %.1fs for %s widget to load (%.1fs elapsed)", delay, captcha_type, elapsed)
        time.sleep(delay)
        elapsed += delay
        attempt += 1

        key = _try_extract_sitekey(page, captcha_type)
        if key:
            log.info("Sitekey found after %.1fs wait", elapsed)
            return key

    log.warning("Could not extract %s sitekey after %.1fs", captcha_type, elapsed)
    return None


def detect_invisible(page, captcha_type: str) -> bool:
    """Detect if the CAPTCHA is invisible (no visible checkbox/widget).

    Args:
        page: Playwright page object.
        captcha_type: CAPTCHA type string.

    Returns:
        True if invisible, False if visible widget present.
    """
    if captcha_type != "recaptcha":
        return False

    try:
        return bool(page.evaluate("""() => {
            const el = document.querySelector('.g-recaptcha[data-size="invisible"]');
            if (el) return true;
            const badge = document.querySelector('.grecaptcha-badge');
            if (badge) return true;
            return false;
        }"""))
    except Exception:
        return False


def detect_version(page) -> str:
    """Detect reCAPTCHA version (v2 or v3).

    v3 signals: script with render= param, client ID >= 10000 in ___grecaptcha_cfg.
    Everything else is assumed v2.

    Returns:
        'v2' or 'v3'.
    """
    try:
        result = page.evaluate("""() => {
            // Check for v3 script render= parameter
            const scripts = document.querySelectorAll('script[src*="recaptcha"]');
            for (const s of scripts) {
                if (s.src && /[?&]render=(?!explicit)/.test(s.src)) return 'v3';
            }
            // Check for v3 client IDs (>= 10000) in grecaptcha config
            if (typeof ___grecaptcha_cfg !== 'undefined' && ___grecaptcha_cfg.clients) {
                for (const cid of Object.keys(___grecaptcha_cfg.clients)) {
                    if (parseInt(cid, 10) >= 10000) return 'v3';
                }
            }
            return null;
        }""")
        return result if result else "v2"
    except Exception:
        return "v2"
