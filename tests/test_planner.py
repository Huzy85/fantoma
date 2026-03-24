"""Tests for Planner step parsing."""


def test_parse_numbered_steps():
    from fantoma.planner import Planner
    steps = Planner._parse_steps("1. Go to google.com\n2. Type 'hello'\n3. Click search")
    assert len(steps) == 3
    assert steps[0] == "Go to google.com"


def test_parse_step_prefix():
    from fantoma.planner import Planner
    steps = Planner._parse_steps("Step 1: Navigate\nStep 2: Click button")
    assert len(steps) == 2


def test_parse_bullet_list():
    from fantoma.planner import Planner
    steps = Planner._parse_steps("- Go to site\n- Fill form\n- Submit")
    assert len(steps) == 3


def test_parse_parenthesis_numbered():
    from fantoma.planner import Planner
    steps = Planner._parse_steps("1) Open browser\n2) Navigate to URL\n3) Click login")
    assert len(steps) == 3
    assert steps[2] == "Click login"


def test_parse_empty_string():
    from fantoma.planner import Planner
    steps = Planner._parse_steps("")
    assert steps == []


def test_parse_mixed_blank_lines():
    from fantoma.planner import Planner
    steps = Planner._parse_steps("1. First step\n\n2. Second step\n\n3. Third step")
    assert len(steps) == 3
