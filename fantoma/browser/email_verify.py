"""Email verification — polls IMAP for verification codes and links."""

import email
import imaplib
import logging
import re
import time
from typing import Optional

log = logging.getLogger("fantoma.email_verify")

VERIFY_URL_KEYWORDS = [
    "verify", "confirm", "activate", "validate",
    "email-verification", "account/verify", "auth/confirm",
    "registration/confirm", "signup/verify",
]


def check_inbox(email_config, site_domain, timeout=120, poll_interval=10):
    """Poll IMAP for a verification email from site_domain.

    Returns:
        {"type": "code"|"link", "value": str, "subject": str} or None
    """
    if not email_config or not email_config.host:
        return None

    log.info("Checking IMAP for verification from %s (max %ds)", site_domain, timeout)
    start = time.time()
    site_lower = site_domain.lower().replace("www.", "")

    while time.time() - start < timeout:
        try:
            conn = _connect(email_config)
            conn.select("INBOX")

            for search_criteria in ["UNSEEN", "ALL"]:
                typ, data = conn.search(None, search_criteria)
                if not data[0]:
                    continue

                ids = data[0].split()
                for eid in reversed(ids[-10:]):
                    typ, msg_data = conn.fetch(eid, "(RFC822)")
                    raw = msg_data[0][1]
                    msg = email.message_from_bytes(raw)

                    sender = str(msg.get("From", "")).lower()
                    subject = str(msg.get("Subject", ""))

                    if site_lower not in sender and site_lower not in subject.lower():
                        continue

                    log.info("Found email from %s: %s", sender[:40], subject[:60])
                    body = _get_body(msg)

                    code = extract_code_from_body(body)
                    if code:
                        conn.logout()
                        return {"type": "code", "value": code, "subject": subject}

                    link = extract_link_from_body(body, site_lower)
                    if link:
                        conn.logout()
                        return {"type": "link", "value": link, "subject": subject}

                    log.info("Email matched but no code/link found")

            conn.logout()
        except Exception as e:
            log.warning("IMAP error: %s", e)

        if time.time() - start + poll_interval < timeout:
            time.sleep(poll_interval)
        else:
            break

    return None


def _connect(config):
    """Connect to IMAP server based on security setting."""
    if config.security == "ssl":
        conn = imaplib.IMAP4_SSL(config.host, config.port)
    else:
        conn = imaplib.IMAP4(config.host, config.port)
        if config.security == "starttls":
            conn.starttls()
    conn.login(config.user, config.password)
    return conn


def extract_code_from_body(body):
    """Extract a verification code from email body. Handles multiple formats."""
    if not body:
        return None

    patterns = [
        # Labelled: "code is: 123456" or "code: G-123456"
        r'(?:code|OTP|pin|token)\s*(?:is|:)\s*([A-Z0-9][-A-Z0-9]{2,9})',
        # Prefixed: G-284951
        r'\b([A-Z]{1,3}-\d{4,8})\b',
        # Pure 4-8 digits (most common)
        r'\b(\d{4,8})\b',
    ]

    for pattern in patterns:
        matches = re.findall(pattern, body, re.IGNORECASE)
        for match in matches:
            # Filter out years and small numbers for digit-only matches
            if match.isdigit():
                num = int(match)
                if 1900 <= num <= 2099 or num < 1000:
                    continue
            return match

    return None


def extract_link_from_body(body, site_domain=""):
    """Extract a verification link from email body (checks URLs and anchor text)."""
    if not body:
        return None

    # Check HTML anchor tags — both href and text
    anchor_pattern = re.compile(r'<a\s+[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>', re.IGNORECASE | re.DOTALL)
    for href, text in anchor_pattern.findall(body):
        combined = (href + " " + text).lower()
        if any(kw in combined for kw in VERIFY_URL_KEYWORDS):
            return href.rstrip(".,;)>]\"'")

    # Check raw URLs in text
    urls = re.findall(r'https?://[^\s<>"\']+', body)
    for url in urls:
        if any(kw in url.lower() for kw in VERIFY_URL_KEYWORDS):
            return url.rstrip(".,;)>]\"'")

    # Fallback: any URL matching the site domain
    if site_domain:
        for url in urls:
            if site_domain in url.lower():
                return url.rstrip(".,;)>]\"'")

    return None


def _get_body(msg):
    """Extract text body from email message."""
    if msg.is_multipart():
        html_body = ""
        text_body = ""
        for part in msg.walk():
            ct = part.get_content_type()
            payload = part.get_payload(decode=True)
            if not payload:
                continue
            decoded = payload.decode(errors="ignore")
            if ct == "text/html":
                html_body = decoded
            elif ct == "text/plain" and not text_body:
                text_body = decoded
        return html_body or text_body
    payload = msg.get_payload(decode=True)
    return payload.decode(errors="ignore") if payload else ""
