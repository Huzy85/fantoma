"""Recognise pages that are not the content that was asked for.

A bot-check interstitial, an error page or an empty app shell reads like a
page, so without a check it gets returned as if it were the answer: a
"Just a moment..." screen summarised as the site's content. This names what
went wrong instead, so a caller can retry, slow down or change approach.

Text-only and conservative. Every rule except the explicit vendor wording
applies to short pages only, because a long article can mention "403",
"captcha" or "sign in" without being any of those things.
"""

from __future__ import annotations

import re

# Wording shown by common bot-protection interstitials. Matching the page
# text rather than markup keeps this engine-independent.
_CHALLENGE = re.compile(
    r"just a moment\.\.\.|checking your browser|checking if the site connection is secure"
    r"|verify(ing)? you are (a )?human|press (&|and) hold|pardon our interruption"
    r"|attention required!?\s*\|\s*cloudflare|enable javascript and cookies to continue"
    r"|please complete the security check|request unsuccessful\. incapsula"
    r"|access to this page has been denied|why have i been blocked"
    r"|performance (&|and) security by cloudflare"
    r"|you('ve| have) been blocked|blocked by network security|request (was )?blocked"
    r"|click the button below to continue shopping|to discuss automated access"
    r"|sorry, we just need to make sure you'?re not a robot|unusual traffic from your",
    re.IGNORECASE,
)
_CAPTCHA = re.compile(r"captcha|i'?m not a robot|are you a robot", re.IGNORECASE)
# The standard notice on any form that merely *uses* reCAPTCHA or hCaptcha.
_CAPTCHA_NOTICE = re.compile(
    r"(this (site|page|form) is )?protected by (re)?captcha|(re)?captcha and the google"
    r"|hcaptcha privacy policy", re.IGNORECASE,
)
# Status codes count only beside the words that make them errors: a price of
# "$1,429", an order "Reference #48213" or "500 Startups" is not a block page.
_RATE = re.compile(
    r"too many requests|rate[- ]limit(ed|ing)?\b|\b(error|http|status)\s*429\b|\b429\s+too many",
    re.IGNORECASE,
)
_DENIED = re.compile(
    r"access denied|access to this (page|resource) (is|has been) (denied|blocked)"
    r"|\b403\s*[-:]?\s*forbidden\b|\b(error|http|status)\s*403\b"
    r"|you don'?t have permission to access",
    re.IGNORECASE,
)
_HTTP_ERROR_TITLE = re.compile(
    r"^\s*(40\d|50\d)\b\s*[-:|]?\s*(error|not found|forbidden|unauthori[sz]ed|bad request"
    r"|bad gateway|service unavailable|gateway time-?out|internal server error|gone)"
    r"|^\s*(error|http)\s*(40\d|50\d)\b|^\s*(page )?not found\s*$|\bpage not found\b"
    r"|^\s*(bad gateway|service unavailable|internal server error|gateway time-?out)\b",
    re.IGNORECASE,
)
# Titles that name a block or an error outright: "Blocked", "Blocked - Site",
# "Error Page | Site". Seen live on sites that answer a data-centre address
# with a plain page rather than a challenge.
_BLOCK_TITLE = re.compile(
    r"^\s*(access )?blocked\b|^\s*(request|access) (blocked|denied)\b|\berror page\b"
    r"|^\s*(robot|bot) check\b|^\s*security check\b",
    re.IGNORECASE,
)
_LOGIN = re.compile(
    r"(sign|log)\s*in\s+(to continue|to view|is required)|you (must|need to) (sign|log)\s*in"
    r"|(login|log\s*in) required", re.IGNORECASE,
)

SHORT_PAGE = 1500
EMPTY_PAGE = 40


def detect_block_page(title: str, text: str) -> str:
    """Return why this page is not real content, or "" if it looks fine.

    Reasons: "bot_challenge", "captcha", "rate_limited", "access_denied",
    "http_error", "login_wall", "empty".
    """
    title = title or ""
    text = text or ""
    both = f"{title}\n{text}"
    short = len(text) < SHORT_PAGE

    if _CHALLENGE.search(title) or (short and _CHALLENGE.search(text)):
        return "bot_challenge"
    if short and _BLOCK_TITLE.search(title):
        return "access_denied"
    if short and _CAPTCHA.search(_CAPTCHA_NOTICE.sub("", both)):
        return "captcha"
    if short and _RATE.search(both):
        return "rate_limited"
    if short and _DENIED.search(both):
        return "access_denied"
    if short and _HTTP_ERROR_TITLE.search(title):
        return "http_error"
    if short and _LOGIN.search(text):
        return "login_wall"
    if len(text.strip()) < EMPTY_PAGE:
        return "empty"
    return ""
