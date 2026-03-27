from unittest.mock import MagicMock


def test_field_labeller_prompt_exists():
    from fantoma.llm.prompts import FIELD_LABELLER_SYSTEM
    assert "label" in FIELD_LABELLER_SYSTEM.lower()
    assert "email" in FIELD_LABELLER_SYSTEM
    assert "skip" in FIELD_LABELLER_SYSTEM


def test_parse_llm_labels():
    from fantoma.browser.form_login import _parse_llm_labels
    raw = "[3]=email, [5]=password, [7]=submit, [9]=checkbox_terms"
    result = _parse_llm_labels(raw)
    assert result == {3: "email", 5: "password", 7: "submit", 9: "checkbox_terms"}


def test_parse_llm_labels_messy_output():
    from fantoma.browser.form_login import _parse_llm_labels
    raw = "Here are the labels:\n[3]=email, [5]=password\n[7]=submit"
    result = _parse_llm_labels(raw)
    assert result == {3: "email", 5: "password", 7: "submit"}


def test_parse_llm_labels_empty():
    from fantoma.browser.form_login import _parse_llm_labels
    assert _parse_llm_labels("") == {}
    assert _parse_llm_labels("no labels found") == {}


def test_ask_llm_to_label():
    from fantoma.browser.form_login import _ask_llm_to_label

    mock_llm = MagicMock()
    mock_llm.chat.return_value = "[0]=email, [1]=password, [2]=submit"

    elements = [
        {"index": 0, "name": "Identifier", "role": "textbox", "type": "text", "_selector": "input[name='id']"},
        {"index": 1, "name": "Secret", "role": "input", "type": "password", "_selector": "input[name='pw']"},
        {"index": 2, "name": "Go", "role": "button", "type": "", "_selector": "button"},
    ]

    result = _ask_llm_to_label(mock_llm, elements, "https://example.com/login")
    assert result == {0: "email", 1: "password", 2: "submit"}
    mock_llm.chat.assert_called_once()


def test_ask_llm_to_label_no_llm():
    from fantoma.browser.form_login import _ask_llm_to_label
    result = _ask_llm_to_label(None, [], "https://example.com")
    assert result == {}
