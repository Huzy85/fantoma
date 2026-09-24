"""`fantoma read URL` prints Markdown and closes the browser."""

import json
from unittest.mock import MagicMock, patch

import pytest

from fantoma import cli


def _result(**over):
    r = {"title": "T", "url": "https://a.example/", "markdown": "# Body", "links": [],
         "blocked": "", "injection_warnings": [], "hidden_removed": 0,
         "truncated": False, "description": ""}
    r.update(over)
    return r


def test_prints_markdown_and_stops_the_browser(capsys):
    f = MagicMock()
    f.read.return_value = _result()
    with patch("fantoma.browser_tool.Fantoma", return_value=f):
        cli.cmd_read("a.example")
    out = capsys.readouterr().out
    assert "# T" in out and "# Body" in out
    f.read.assert_called_once_with("https://a.example", main_only=True, include_links=True,
                                   selector="", max_chars=0)
    f.stop.assert_called_once()


def test_json_output(capsys):
    f = MagicMock()
    f.read.return_value = _result(blocked="bot_challenge")
    with patch("fantoma.browser_tool.Fantoma", return_value=f):
        cli.cmd_read("https://a.example/", as_json=True, full=True)
    assert json.loads(capsys.readouterr().out)["blocked"] == "bot_challenge"
    assert f.read.call_args.kwargs["main_only"] is False


def test_failure_exits_nonzero_and_still_stops(capsys):
    f = MagicMock()
    f.read.side_effect = RuntimeError("not permitted")
    with patch("fantoma.browser_tool.Fantoma", return_value=f):
        with pytest.raises(SystemExit):
            cli.cmd_read("https://a.example/")
    f.stop.assert_called_once()
