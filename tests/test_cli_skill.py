"""`fantoma skill` prints or installs the bundled SKILL.md."""

import re
import sys
from pathlib import Path

import pytest

from fantoma import cli

ROOT = Path(__file__).resolve().parent.parent
BUNDLED = ROOT / "fantoma" / "skills" / "fantoma" / "SKILL.md"
TOP = ROOT / "skills" / "fantoma" / "SKILL.md"


def _frontmatter(text):
    m = re.match(r"---\n(.*?)\n---\n", text, re.S)
    assert m, "missing frontmatter"
    out = {}
    for line in m.group(1).splitlines():
        k, _, v = line.partition(":")
        out[k.strip()] = v.strip()
    return out


def test_root_copy_matches_bundled_copy():
    assert BUNDLED.read_text() == TOP.read_text()


def test_frontmatter_follows_spec():
    text = BUNDLED.read_text()
    fm = _frontmatter(text)
    name = fm["name"]
    assert name == "fantoma" == BUNDLED.parent.name == TOP.parent.name
    assert re.fullmatch(r"[a-z0-9]+(-[a-z0-9]+)*", name) and len(name) <= 64
    assert 0 < len(fm["description"]) <= 1024
    assert len(fm.get("compatibility", "x")) <= 500
    assert set(fm) <= {"name", "description", "license", "compatibility", "metadata", "allowed-tools"}
    assert len(text.splitlines()) < 500


def test_no_private_details_or_em_dash():
    text = BUNDLED.read_text()
    assert "—" not in text
    assert not re.search(r"192\.168\.|\bM5\b|\bTC[12]\b|Hermes|Hercules|Apollo", text)


def test_every_cli_flag_in_skill_exists():
    text = BUNDLED.read_text()
    parser_src = (ROOT / "fantoma" / "cli.py").read_text()
    for flag in set(re.findall(r"(?<![\w-])--[a-z][a-z-]+", text)):
        if flag in ("--env", "--trace"):
            continue
        assert f'"{flag}"' in parser_src, flag


def test_prints_skill(monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["fantoma", "skill"])
    cli.main()
    assert capsys.readouterr().out == BUNDLED.read_text()


def test_install_creates_dirs(tmp_path, capsys):
    cli.cmd_skill(str(tmp_path / "a" / "b"))
    dest = tmp_path / "a" / "b" / "fantoma" / "SKILL.md"
    assert dest.read_text() == BUNDLED.read_text()


def test_install_refuses_overwrite_then_force(tmp_path):
    dest = tmp_path / "fantoma" / "SKILL.md"
    dest.parent.mkdir()
    dest.write_text("mine")
    with pytest.raises(SystemExit):
        cli.cmd_skill(str(tmp_path))
    assert dest.read_text() == "mine"
    cli.cmd_skill(str(tmp_path), force=True)
    assert dest.read_text() == BUNDLED.read_text()


def test_install_flag_via_main(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "argv", ["fantoma", "skill", "--install", str(tmp_path)])
    cli.main()
    assert (tmp_path / "fantoma" / "SKILL.md").is_file()
