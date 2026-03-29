"""Cookie consent auto-dismisser — handles GDPR/cookie banners across sites."""
import json
import logging
import time

log = logging.getLogger("fantoma.consent")

# Selectors for common consent frameworks, ordered by specificity
ACCEPT_SELECTORS = [
    # OneTrust (Amazon, Indeed, Booking.com, many others)
    '#onetrust-accept-btn-handler',
    # Amazon-specific
    '#sp-cc-accept',
    'input[name="accept"][type="submit"]',
    # CookieBot
    '#CybotCookiebotDialogBodyLevelButtonLevelOptinAllowAll',
    '#CybotCookiebotDialogBodyButtonDecline',
    # GDPR generic
    '[data-testid="cookie-accept"]',
    '[data-action="accept-cookies"]',
    '[data-gdpr="accept"]',
    'button[id*="accept"][id*="cookie"]',
    # Common class patterns
    '.cookie-accept',
    '.accept-cookies',
    '.consent-accept',
    '.js-accept-cookies',
    '.cc-accept',
    '.cc-dismiss',
]

# Text-based fallback — click any button containing these words
ACCEPT_TEXTS = [
    "Accept all",
    "Accept cookies",
    "Accept All Cookies",
    "I accept",
    "I agree",
    "Allow all",
    "Allow all cookies",
    "Allow cookies",
    "Decline optional cookies",
    "Got it",
    "OK",
    "Agree",
    "Dismiss",
]

# Overlay selectors to check if a consent modal is blocking
OVERLAY_SELECTORS = [
    '#onetrust-consent-sdk',
    '#sp-cc',
    '#CybotCookiebotDialog',
    '[id*="cookie-consent"]',
    '[id*="gdpr"]',
    '.cookie-banner',
    '.consent-banner',
    '.cookie-notice',
    '[class*="consent-overlay"]',
    '[class*="cookie-wall"]',
]


def dismiss_consent(page, timeout: float = 3.0) -> bool:
    """Try to dismiss any cookie consent banner on the page.

    Returns True if a consent banner was found and dismissed.
    """
    # Check if there's a consent overlay — use JS to check display/visibility
    # (Playwright's is_visible() can miss some overlay implementations)
    selectors_json = json.dumps(OVERLAY_SELECTORS)
    has_overlay = page.evaluate(f'''() => {{
        // Check known overlay selectors
        const selectors = {selectors_json};
        for (const sel of selectors) {{
            try {{
                const el = document.querySelector(sel);
                if (el) {{
                    const style = window.getComputedStyle(el);
                    if (style.display !== 'none' && style.visibility !== 'hidden') {{
                        return true;
                    }}
                }}
            }} catch(e) {{}}
        }}
        // Fallback: check if there are visible buttons with cookie-related text
        const buttons = document.querySelectorAll('button');
        let cookieButtons = 0;
        for (const btn of buttons) {{
            const text = (btn.textContent || '').toLowerCase();
            if (text.includes('cookie') || text.includes('consent') || text.includes('allow all')) {{
                if (btn.getBoundingClientRect().height > 0) cookieButtons++;
            }}
        }}
        return cookieButtons >= 2;
    }}''')

    # Skip if we already dismissed consent on this page (prevent infinite loops)
    try:
        already = page.evaluate('() => window.__fantoma_consent_dismissed === true')
        if already:
            return False
    except Exception:
        pass

    if not has_overlay:
        return False

    log.info("Cookie consent overlay detected — dismissing")

    # Try specific selectors first — use JavaScript click to bypass overlay issues
    for sel in ACCEPT_SELECTORS:
        try:
            el = page.query_selector(sel)
            if el:
                # Use JS click — bypasses Playwright's overlay interception check
                page.evaluate("el => el.click()", el)
                time.sleep(1.0)
                _force_remove_overlays(page)
                log.info("Dismissed consent via selector: %s", sel)
                return True
        except Exception:
            continue

    # Try text-based button search
    for text in ACCEPT_TEXTS:
        try:
            el = page.query_selector(f'button:has-text("{text}")')
            if el:
                page.evaluate("el => el.click()", el)
                time.sleep(0.5)
                _force_remove_overlays(page)
                log.info("Dismissed consent via text: %s", text)
                return True
        except Exception:
            continue

    # Last resort: try to click away the overlay by pressing Escape
    try:
        page.keyboard.press("Escape")
        time.sleep(0.5)
        _force_remove_overlays(page)
        log.info("Dismissed consent via Escape key")
        return True
    except Exception:
        pass

    # Nuclear option: force-remove all overlays even without clicking accept
    _force_remove_overlays(page)
    log.warning("Force-removed consent overlay without clicking accept")
    return True


def _force_remove_overlays(page):
    """Remove all consent overlays from DOM and mark page to prevent re-detection.

    Sets common consent cookies so the banner doesn't reappear on navigation.
    """
    selectors_json = json.dumps(OVERLAY_SELECTORS)
    try:
        page.evaluate(f'''() => {{
            // Remove all known overlay elements from the DOM entirely
            const selectors = {selectors_json};
            for (const sel of selectors) {{
                try {{
                    document.querySelectorAll(sel).forEach(el => el.remove());
                }} catch(e) {{}}
            }}

            // Remove dark filter overlays (OneTrust, etc.)
            document.querySelectorAll(
                '.onetrust-pc-dark-filter, [class*="consent-overlay"], '
                + '[class*="cookie-wall"], [class*="modal-backdrop"]'
            ).forEach(el => el.remove());

            // Restore body scroll (consent banners often set overflow:hidden)
            document.body.style.overflow = '';
            document.documentElement.style.overflow = '';

            // Set common consent cookies so banner doesn't reappear
            const domain = location.hostname;
            const expires = new Date(Date.now() + 365*24*60*60*1000).toUTCString();
            const cookies = [
                'OptanonAlertBoxClosed=' + new Date().toISOString(),
                'OptanonConsent=isGpcEnabled=0&groups=C0001%3A1%2CC0002%3A1%2CC0003%3A1%2CC0004%3A1',
                'CookieConsent=true',
                'cookie-consent=accepted',
                'gdpr-consent=1',
                'cc_cookie=accepted',
            ];
            for (const c of cookies) {{
                document.cookie = c + '; path=/; expires=' + expires + '; SameSite=Lax';
            }}

            // Mark page so we don't re-detect
            window.__fantoma_consent_dismissed = true;
        }}''')
    except Exception as e:
        log.debug("Force-remove overlays failed: %s", e)
