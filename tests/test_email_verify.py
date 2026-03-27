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
