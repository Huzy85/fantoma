import os
import tempfile


def test_form_memory_creates_db():
    from fantoma.browser.form_memory import FormMemory
    with tempfile.TemporaryDirectory() as d:
        db_path = os.path.join(d, "test.db")
        fm = FormMemory(db_path=db_path)
        assert os.path.exists(db_path)
        fm.close()


def test_record_visit_creates_site():
    from fantoma.browser.form_memory import FormMemory
    with tempfile.TemporaryDirectory() as d:
        fm = FormMemory(db_path=os.path.join(d, "test.db"))
        fm.record_visit("x.com", success=True)
        fm.record_visit("x.com", success=False)
        history = fm.get_history("x.com")
        assert history["total_attempts"] == 2
        assert history["total_successes"] == 1
        fm.close()


def test_record_step_and_lookup():
    from fantoma.browser.form_memory import FormMemory
    with tempfile.TemporaryDirectory() as d:
        fm = FormMemory(db_path=os.path.join(d, "test.db"))
        fm.record_step(
            domain="x.com", visit_id="abc123", step_number=0,
            field_label="Phone, email, or username", field_role="textbox",
            field_purpose="email", submit_label="Next", success=True,
            tree_text="[0] textbox 'Phone, email, or username'",
            elements_json='[{"label": "Phone, email, or username"}]',
            url="https://x.com/login", action="filled email", result="page changed"
        )
        live_elements = [{"label": "Phone, email, or username", "role": "textbox"}]
        match = fm.lookup("x.com", 0, live_elements)
        assert match["Phone, email, or username"] == "email"
        fm.close()


def test_lookup_returns_empty_for_unknown_domain():
    from fantoma.browser.form_memory import FormMemory
    with tempfile.TemporaryDirectory() as d:
        fm = FormMemory(db_path=os.path.join(d, "test.db"))
        match = fm.lookup("unknown.com", 0, [{"label": "Email", "role": "textbox"}])
        assert match == {}
        fm.close()


def test_lookup_ignores_stale_labels():
    from fantoma.browser.form_memory import FormMemory
    with tempfile.TemporaryDirectory() as d:
        fm = FormMemory(db_path=os.path.join(d, "test.db"))
        fm.record_step(
            domain="x.com", visit_id="v1", step_number=0,
            field_label="Old label", field_role="textbox",
            field_purpose="email", submit_label="Next", success=True,
            tree_text="", elements_json="[]",
            url="https://x.com/login", action="filled", result="ok"
        )
        live_elements = [{"label": "New label", "role": "textbox"}]
        match = fm.lookup("x.com", 0, live_elements)
        assert match == {}
        fm.close()


def test_upsert_increments_seen_count():
    from fantoma.browser.form_memory import FormMemory
    with tempfile.TemporaryDirectory() as d:
        fm = FormMemory(db_path=os.path.join(d, "test.db"))
        for i in range(3):
            fm.record_step(
                domain="x.com", visit_id=f"v{i}", step_number=0,
                field_label="Email", field_role="textbox",
                field_purpose="email", submit_label="Next", success=True,
                tree_text="", elements_json="[]",
                url="https://x.com/login", action="filled", result="ok"
            )
        history = fm.get_history("x.com")
        steps = history["steps"]
        email_steps = [s for s in steps if s["field_purpose"] == "email"]
        assert len(email_steps) == 1
        assert email_steps[0]["seen_count"] == 3
        fm.close()


def test_get_snapshot():
    from fantoma.browser.form_memory import FormMemory
    with tempfile.TemporaryDirectory() as d:
        fm = FormMemory(db_path=os.path.join(d, "test.db"))
        fm.record_step(
            domain="x.com", visit_id="snap1", step_number=0,
            field_label="Email", field_role="textbox",
            field_purpose="email", submit_label="Next", success=True,
            tree_text="[0] textbox 'Email'", elements_json='[{"label":"Email"}]',
            url="https://x.com/login", action="filled email", result="page changed"
        )
        snaps = fm.get_snapshot("x.com", visit_id="snap1")
        assert len(snaps) == 1
        assert snaps[0]["tree_text"] == "[0] textbox 'Email'"
        fm.close()
