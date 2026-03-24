"""Cookie consent auto-dismisser — handles GDPR/cookie banners across sites."""
import logging
import time

log = logging.getLogger("fantoma.consent")

# Selectors for common consent frameworks, ordered by specificity
ACCEPT_SELECTORS = [
    # OneTrust (Amazon, Indeed, many others)
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
    # Booking.com
    '#onetrust-accept-btn-handler',
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
    import json
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
                # Verify overlay is actually gone — remove it via JS if still present
                still_visible = page.evaluate(f'''() => {{
                    const el = document.querySelector('#onetrust-consent-sdk');
                    if (el && window.getComputedStyle(el).display !== 'none') {{
                        el.style.display = 'none';
                        // Also remove the dark filter overlay
                        document.querySelectorAll('.onetrust-pc-dark-filter').forEach(f => f.style.display = 'none');
                        return 'force-hidden';
                    }}
                    return 'gone';
                }}''')
                log.info("Dismissed consent via selector: %s (%s)", sel, still_visible)
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
                log.info("Dismissed consent via text: %s", text)
                return True
        except Exception:
            continue

    # Last resort: try to click away the overlay by pressing Escape
    try:
        page.keyboard.press("Escape")
        time.sleep(0.5)
        # Check if overlay is gone
        for sel in OVERLAY_SELECTORS:
            el = page.query_selector(sel)
            if el and el.is_visible():
                log.warning("Consent overlay still visible after Escape")
                return False
        log.info("Dismissed consent via Escape key")
        return True
    except Exception:
        pass

    log.warning("Could not dismiss consent overlay")
    return False
