"""Email verification helper — extracts codes and links from verification emails.

Handles three verification types:
1. Code-based: Extract 4-8 digit code, paste into signup form
2. Link-based: Find verify/confirm URL, click it
3. Magic link: Find login URL, navigate to it

All extraction is code-based (regex) — no LLM calls needed.
"""

import logging
import re
from typing import Optional

log = logging.getLogger("fantoma.verification")

# Patterns for verification codes (4-8 digits, standalone)
CODE_PATTERN = re.compile(r'\b(\d{4,8})\b')

# URL patterns that indicate verification links
VERIFY_URL_KEYWORDS = [
    "verify", "confirm", "activate", "validate",
    "email-verification", "account/verify", "auth/confirm",
    "registration/confirm", "signup/verify", "magic",
]

# Sender name patterns to match against site names
SENDER_ALIASES = {
    "reddit": ["reddit", "noreply@reddit"],
    "github": ["github", "noreply@github"],
    "booking": ["booking", "noreply@booking"],
    "stackoverflow": ["stack overflow", "stackoverflow", "noreply@stackoverflow"],
    "medium": ["medium", "noreply@medium"],
    "pinterest": ["pinterest", "noreply@pinterest"],
    "linkedin": ["linkedin", "noreply@linkedin"],
    "indeed": ["indeed", "noreply@indeed"],
    "etsy": ["etsy", "noreply@etsy"],
}


def find_verification_email(page, site_name: str) -> Optional[dict]:
    """Find the most recent verification email from a specific site.

    Scans the email inbox (ARIA tree or inner text) for emails matching
    the site name. Returns the newest match.

    Args:
        page: Playwright page object (should be on an email inbox)
        site_name: Name of the site to find email from (e.g. "reddit")

    Returns:
        {"index": int, "subject": str, "sender": str} or None
    """
    site_lower = site_name.lower()

    # Get all text from the main content area
    try:
        main = page.locator("main, [role=main]")
        if main.count() > 0:
            text = main.first.inner_text()
        else:
            text = page.inner_text("body")[:5000]
    except Exception:
        text = ""

    if not text:
        return None

    # Look for the site name in the email list
    lines = text.split("\n")
    for i, line in enumerate(lines):
        line_lower = line.lower().strip()
        # Check if this line mentions the site
        aliases = SENDER_ALIASES.get(site_lower, [site_lower])
        if any(alias in line_lower for alias in aliases):
            # Found a match — return context around it
            subject = ""
            for j in range(max(0, i-2), min(len(lines), i+3)):
                if lines[j].strip() and len(lines[j].strip()) > 10:
                    subject = lines[j].strip()
                    break
            return {"line_index": i, "subject": subject, "sender": line.strip()}

    return None


def extract_verification_code(page, site_name: str = None) -> Optional[str]:
    """Extract a verification code from the currently open email.

    Looks for 4-8 digit standalone numbers in the email body.
    If multiple codes found, returns the most prominent one
    (longest, or nearest to "code"/"verification" text).

    Args:
        page: Playwright page object (should be showing an email)
        site_name: Optional site name for context

    Returns:
        Code string (e.g. "321013") or None
    """
    try:
        main = page.locator("main, [role=main]")
        if main.count() > 0:
            text = main.first.inner_text()
        else:
            text = page.inner_text("body")[:5000]
    except Exception:
        return None

    if not text:
        return None

    # Find all potential codes
    codes = CODE_PATTERN.findall(text)
    if not codes:
        return None

    # Filter out unlikely codes (years, common numbers)
    filtered = []
    for code in codes:
        num = int(code)
        # Skip years (1900-2099), very small numbers, common patterns
        if 1900 <= num <= 2099:
            continue
        if num < 1000:
            continue
        filtered.append(code)

    if not filtered:
        # Fall back to unfiltered if everything was filtered out
        filtered = codes

    # Prefer 6-digit codes (most common verification code length)
    six_digit = [c for c in filtered if len(c) == 6]
    if six_digit:
        return six_digit[0]

    # Otherwise return the first valid code
    return filtered[0]


def extract_verification_link(page, site_name: str = None) -> Optional[str]:
    """Extract a verification/confirmation link from the currently open email.

    Looks for URLs containing verify/confirm/activate keywords.

    Args:
        page: Playwright page object (should be showing an email)
        site_name: Optional site name to prefer matching links

    Returns:
        URL string or None
    """
    try:
        # Get all links on the page
        links = page.locator("a[href]").all()
        candidates = []

        for link in links:
            try:
                href = link.get_attribute("href") or ""
                text = link.inner_text().strip().lower()
            except Exception:
                continue

            if not href or href.startswith("mailto:") or href.startswith("#"):
                continue

            href_lower = href.lower()

            # Check if URL contains verification keywords
            is_verify = any(kw in href_lower for kw in VERIFY_URL_KEYWORDS)
            # Check if link text suggests verification
            is_verify_text = any(kw in text for kw in
                                ["verify", "confirm", "activate", "click here",
                                 "complete registration", "validate"])

            if is_verify or is_verify_text:
                # Score: prefer links matching the site name
                score = 1
                if site_name and site_name.lower() in href_lower:
                    score = 3
                if is_verify and is_verify_text:
                    score += 1
                candidates.append((score, href))

        if candidates:
            # Return highest-scored link
            candidates.sort(key=lambda x: x[0], reverse=True)
            return candidates[0][1]

    except Exception as e:
        log.warning("Error extracting verification link: %s", e)

    # Fallback: regex search in page text for URLs
    try:
        text = page.inner_text("body")[:8000]
        url_pattern = re.compile(r'https?://\S+(?:verify|confirm|activate|validate)\S*', re.IGNORECASE)
        match = url_pattern.search(text)
        if match:
            return match.group(0).rstrip('.,;)')
    except Exception:
        pass

    return None


def detect_verification_type(page) -> str:
    """Detect what type of verification the current email contains.

    Returns:
        "code" — contains a verification code (digits)
        "link" — contains a verification link
        "unknown" — can't determine
    """
    code = extract_verification_code(page)
    if code:
        return "code"

    link = extract_verification_link(page)
    if link:
        return "link"

    return "unknown"
