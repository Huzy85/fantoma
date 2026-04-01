from unittest.mock import MagicMock


def test_field_labeller_prompt_exists():
    """FIELD_LABELLER_SYSTEM is now a local variable inside form_login._ask_llm_to_label."""
    import inspect
    from fantoma.browser.form_login import _ask_llm_to_label
    source = inspect.getsource(_ask_llm_to_label)
    assert "FIELD_LABELLER_SYSTEM" in source
    assert "email" in source
    assert "skip" in source


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


def test_classify_fields_uses_llm_when_unmatched():
    from fantoma.browser.form_login import _classify_fields
    from unittest.mock import MagicMock, patch

    mock_llm = MagicMock()
    mock_llm.chat.return_value = "[0]=email, [1]=password, [2]=submit"

    # Use type="text" for the password field so heuristic matching won't catch it
    elements = [
        {"index": 0, "name": "Identifier", "role": "textbox", "type": "text"},
        {"index": 1, "name": "Secret", "role": "input", "type": "text"},
        {"index": 2, "name": "Go", "role": "button", "type": ""},
    ]

    with patch("fantoma.browser.form_login._find_raw_inputs", return_value=[]):
        with patch("fantoma.browser.form_login._find_raw_buttons", return_value=[]):
            result = _classify_fields(
                page=MagicMock(), elements=elements, step=0,
                first_name="", last_name="", llm=mock_llm
            )

    elems, email_f, user_f, pass_f, fn_f, ln_f, submit_f = result
    assert email_f is not None and email_f["name"] == "Identifier"
    assert pass_f is not None and pass_f["name"] == "Secret"
    assert submit_f is not None and submit_f["name"] == "Go"


def test_classify_fields_no_llm_when_all_matched():
    from fantoma.browser.form_login import _classify_fields
    from unittest.mock import MagicMock, patch

    mock_llm = MagicMock()

    elements = [
        {"index": 0, "name": "Email", "role": "textbox", "type": "email"},
        {"index": 1, "name": "Password", "role": "input", "type": "password"},
        {"index": 2, "name": "Log in", "role": "button", "type": ""},
    ]

    with patch("fantoma.browser.form_login._find_raw_inputs", return_value=[]):
        with patch("fantoma.browser.form_login._find_raw_buttons", return_value=[]):
            _classify_fields(
                page=MagicMock(), elements=elements, step=0,
                first_name="", last_name="", llm=mock_llm
            )

    mock_llm.chat.assert_not_called()


def test_classify_fields_without_llm_unchanged():
    from fantoma.browser.form_login import _classify_fields
    from unittest.mock import MagicMock, patch

    elements = [
        {"index": 0, "name": "Weird Label", "role": "textbox", "type": "text"},
        {"index": 1, "name": "Go", "role": "button", "type": ""},
    ]

    with patch("fantoma.browser.form_login._find_raw_inputs", return_value=[]):
        with patch("fantoma.browser.form_login._find_raw_buttons", return_value=[]):
            result = _classify_fields(
                page=MagicMock(), elements=elements, step=0,
                first_name="", last_name="", llm=None
            )

    elems, email_f, user_f, pass_f, fn_f, ln_f, submit_f = result
    assert email_f is None
    assert user_f is None
    assert pass_f is None


def test_apply_llm_labels_checkbox():
    from fantoma.browser.form_login import _apply_llm_labels
    from unittest.mock import MagicMock

    mock_page = MagicMock()
    mock_dom = MagicMock()
    mock_element = MagicMock()
    mock_dom.get_element_by_index.return_value = mock_element

    elements = [
        {"index": 0, "name": "Email", "role": "textbox", "type": "email"},
        {"index": 9, "name": "I agree to Terms", "role": "checkbox", "type": "checkbox"},
    ]

    labels = {0: "email", 9: "checkbox_terms"}
    result = _apply_llm_labels(labels, elements, mock_page, mock_dom)
    assert result["email"]["name"] == "Email"
    assert result["checkboxes_clicked"] >= 1
