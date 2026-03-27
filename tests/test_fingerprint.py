# tests/test_fingerprint.py

from unittest.mock import MagicMock, patch


def test_fingerprint_checks_return_dict():
    from fantoma.browser.fingerprint import FingerprintTest
    ft = FingerprintTest()
    mock_page = MagicMock()
    mock_page.evaluate.side_effect = [
        {"ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "platform": "Win32"},
        {"vendor": "Google Inc.", "renderer": "ANGLE (Intel HD Graphics)"},
        {"tz": "Europe/London", "locale": "en-GB"},
        {"width": 1920, "height": 1080, "availWidth": 1920, "availHeight": 1040},
        {"hasWebGL": True, "vendor": "Intel Inc.", "renderer": "Intel Iris"},
        {"main_ua": "Mozilla/5.0 ...", "worker_ua": "Mozilla/5.0 ...",
         "main_hw": 8, "worker_hw": 8},
        {"first": {"ua": "Mozilla/5.0 ...", "platform": "Win32"},
         "second": {"ua": "Mozilla/5.0 ...", "platform": "Win32"}},
    ]
    results = ft.run_all(mock_page)
    assert isinstance(results, dict)
    assert "overall" in results
    assert "checks" in results
    assert len(results["checks"]) == 7


def test_fingerprint_detects_ua_mismatch():
    from fantoma.browser.fingerprint import FingerprintTest
    ft = FingerprintTest()
    mock_page = MagicMock()
    mock_page.evaluate.side_effect = [
        {"ua": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)", "platform": "Linux x86_64"},
        {"vendor": "", "renderer": ""},
        {"tz": "UTC", "locale": "en-US"},
        {"width": 1920, "height": 1080, "availWidth": 1920, "availHeight": 1040},
        {"hasWebGL": True, "vendor": "X", "renderer": "Y"},
        {"main_ua": "a", "worker_ua": "a", "main_hw": 4, "worker_hw": 4},
        {"first": {"ua": "a", "platform": "b"}, "second": {"ua": "a", "platform": "b"}},
    ]
    results = ft.run_all(mock_page)
    ua_check = results["checks"]["ua_vs_platform"]
    assert ua_check["passed"] is False


def test_fingerprint_detects_missing_screen():
    from fantoma.browser.fingerprint import FingerprintTest
    ft = FingerprintTest()
    mock_page = MagicMock()
    mock_page.evaluate.side_effect = [
        {"ua": "Mozilla/5.0 (Windows NT 10.0)", "platform": "Win32"},
        {"vendor": "", "renderer": ""},
        {"tz": "UTC", "locale": "en-US"},
        {"width": 0, "height": 0, "availWidth": 0, "availHeight": 0},
        {"hasWebGL": True, "vendor": "X", "renderer": "Y"},
        {"main_ua": "a", "worker_ua": "a", "main_hw": 4, "worker_hw": 4},
        {"first": {"ua": "a", "platform": "b"}, "second": {"ua": "a", "platform": "b"}},
    ]
    results = ft.run_all(mock_page)
    screen_check = results["checks"]["screen_dimensions"]
    assert screen_check["passed"] is False


def test_fingerprint_detects_instance_instability():
    from fantoma.browser.fingerprint import FingerprintTest
    ft = FingerprintTest()
    mock_page = MagicMock()
    mock_page.evaluate.side_effect = [
        {"ua": "Mozilla/5.0 (Windows NT 10.0)", "platform": "Win32"},
        {"vendor": "", "renderer": ""},
        {"tz": "UTC", "locale": "en-US"},
        {"width": 1920, "height": 1080, "availWidth": 1920, "availHeight": 1040},
        {"hasWebGL": True, "vendor": "X", "renderer": "Y"},
        {"main_ua": "a", "worker_ua": "a", "main_hw": 4, "worker_hw": 4},
        {"first": {"ua": "ua1", "platform": "Win32"},
         "second": {"ua": "ua2", "platform": "Win32"}},
    ]
    results = ft.run_all(mock_page)
    stability_check = results["checks"]["instance_stability"]
    assert stability_check["passed"] is False
