import email.mime.text
from unittest.mock import MagicMock, patch


def test_email_config_defaults():
    from fantoma.config import EmailConfig
    cfg = EmailConfig()
    assert cfg.host == ""
    assert cfg.port == 993
    assert cfg.security == "ssl"


def test_email_config_starttls():
    from fantoma.config import EmailConfig
    cfg = EmailConfig(host="127.0.0.1", port=1143, security="starttls")
    assert cfg.security == "starttls"


def test_extract_code_digits():
    from fantoma.browser.email_verify import extract_code_from_body
    assert extract_code_from_body("Your verification code is 847291.") == "847291"


def test_extract_code_prefixed():
    from fantoma.browser.email_verify import extract_code_from_body
    assert extract_code_from_body("Your code is G-284951") == "G-284951"


def test_extract_code_labelled():
    from fantoma.browser.email_verify import extract_code_from_body
    assert extract_code_from_body("OTP: 938471") == "938471"


def test_extract_code_ignores_years():
    from fantoma.browser.email_verify import extract_code_from_body
    assert extract_code_from_body("Welcome in 2026! Code: 938471") == "938471"


def test_extract_code_none():
    from fantoma.browser.email_verify import extract_code_from_body
    assert extract_code_from_body("No codes here.") is None


def test_extract_link_html():
    from fantoma.browser.email_verify import extract_link_from_body
    body = '<a href="https://example.com/verify?token=abc">Verify your email</a>'
    assert extract_link_from_body(body, "example.com") == "https://example.com/verify?token=abc"


def test_extract_link_anchor_text():
    from fantoma.browser.email_verify import extract_link_from_body
    body = '<a href="https://example.com/a/b/c123">Click here to verify your account</a>'
    link = extract_link_from_body(body, "example.com")
    assert link == "https://example.com/a/b/c123"


def test_extract_link_plain_text():
    from fantoma.browser.email_verify import extract_link_from_body
    body = "Click here: https://example.com/confirm/abc123 Thanks!"
    assert "confirm" in extract_link_from_body(body, "example.com")


def test_extract_link_none():
    from fantoma.browser.email_verify import extract_link_from_body
    assert extract_link_from_body("No links.", "example.com") is None


def test_check_inbox_no_config():
    from fantoma.browser.email_verify import check_inbox
    assert check_inbox(None, "example.com") is None


def test_check_inbox_mocked():
    from fantoma.browser.email_verify import check_inbox
    from fantoma.config import EmailConfig

    mock_conn = MagicMock()
    mock_conn.search.return_value = ("OK", [b"1"])

    msg = email.mime.text.MIMEText("Your code is 123456")
    msg["From"] = "noreply@example.com"
    msg["Subject"] = "Verify your account"

    mock_conn.fetch.return_value = ("OK", [(b"1", msg.as_bytes())])

    config = EmailConfig(host="localhost", port=1143, user="test", password="test", security="none")

    with patch("fantoma.browser.email_verify._connect") as mock_connect:
        mock_connect.return_value = mock_conn
        mock_conn.select.return_value = ("OK", [b"1"])
        result = check_inbox(config, "example.com", timeout=1, poll_interval=0.1)

    assert result is not None
    assert result["type"] == "code"
    assert result["value"] == "123456"


def test_detect_verification_page_code():
    from fantoma.browser.form_login import _detect_verification_page
    assert _detect_verification_page("[0] textbox 'Verification code'", "Enter the code we sent.") == "code"


def test_detect_verification_page_link():
    from fantoma.browser.form_login import _detect_verification_page
    assert _detect_verification_page("", "Check your email for a verification link.") == "link"


def test_detect_verification_page_none():
    from fantoma.browser.form_login import _detect_verification_page
    assert _detect_verification_page("[0] textbox 'Email'", "Welcome back!") is None


# --- Fantoma wiring tests (verification moved from Agent to Fantoma) ---

def _make_fantoma(**kwargs):
    """Create a Fantoma without starting a browser or LLM."""
    from fantoma.browser_tool import Fantoma
    return Fantoma(**kwargs)


def test_fantoma_accepts_email_imap():
    f = _make_fantoma(email_imap={
        "host": "127.0.0.1", "port": 1143,
        "user": "me@test.com", "password": "pass", "security": "starttls",
    })
    assert f.config.email.host == "127.0.0.1"
    assert f.config.email.port == 1143
    assert f.config.email.security == "starttls"


def test_fantoma_accepts_verification_callback():
    cb = lambda domain, msg: "123456"
    f = _make_fantoma(verification_callback=cb)
    assert f._verification_callback is cb


def test_fantoma_default_no_email():
    f = _make_fantoma()
    assert f.config.email.host == ""
    assert f._verification_callback is None


def test_get_verification_imap_first():
    """IMAP tier runs before callback."""
    f = _make_fantoma(
        email_imap={"host": "localhost", "port": 993, "user": "u", "password": "p"},
        verification_callback=lambda d, m: "from_callback",
    )
    with patch("fantoma.browser.email_verify.check_inbox") as mock_inbox:
        mock_inbox.return_value = {"type": "code", "value": "from_imap", "subject": "test"}
        result = f._get_verification("code", "example.com")
    assert result == "from_imap"


def test_get_verification_callback_when_no_imap():
    """Callback tier used when no IMAP configured."""
    f = _make_fantoma(verification_callback=lambda d, m: "  callback_code  ")
    result = f._get_verification("code", "example.com")
    assert result == "callback_code"


def test_get_verification_callback_when_imap_empty():
    """Callback tier used when IMAP returns nothing."""
    f = _make_fantoma(
        email_imap={"host": "localhost", "port": 993, "user": "u", "password": "p"},
        verification_callback=lambda d, m: "fallback",
    )
    with patch("fantoma.browser.email_verify.check_inbox") as mock_inbox:
        mock_inbox.return_value = None
        result = f._get_verification("code", "example.com")
    assert result == "fallback"


def test_get_verification_terminal_fallback():
    """Terminal prompt used when no IMAP or callback."""
    f = _make_fantoma()
    with patch("builtins.input", return_value="manual_code"):
        result = f._get_verification("code", "example.com")
    assert result == "manual_code"


def test_get_verification_no_terminal():
    """Returns None when nothing available and no terminal."""
    f = _make_fantoma()
    with patch("builtins.input", side_effect=EOFError):
        result = f._get_verification("code", "example.com")
    assert result is None


def test_get_verification_callback_exception():
    """Bad callback doesn't crash — falls through to terminal."""
    def bad_callback(d, m):
        raise RuntimeError("boom")
    f = _make_fantoma(verification_callback=bad_callback)
    with patch("builtins.input", return_value="recovered"):
        result = f._get_verification("code", "example.com")
    assert result == "recovered"
