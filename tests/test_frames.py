"""Tests for iframe ARIA extraction and merge."""
import pytest
from unittest.mock import MagicMock, PropertyMock


def _make_frame(url="https://example.com/iframe", name="login-frame",
                snapshot="- textbox \"Email\"\n- button \"Submit\"",
                is_main=False):
    """Create a mock Playwright Frame."""
    frame = MagicMock()
    type(frame).url = PropertyMock(return_value=url)
    type(frame).name = PropertyMock(return_value=name)
    frame.locator.return_value.aria_snapshot.return_value = snapshot
    frame.parent_frame = None if is_main else MagicMock()
    return frame


def _make_page_with_frames(frames):
    """Create a mock page with frame list."""
    page = MagicMock()
    main_frame = _make_frame(url="https://example.com", name="", is_main=True,
                             snapshot="- button \"Login\"\n- link \"Home\"")
    page.main_frame = main_frame
    page.frames = [main_frame] + frames
    return page


class TestExtractFrameElements:
    def test_extracts_from_single_iframe(self):
        from fantoma.dom.frames import extract_frame_elements
        frame = _make_frame(snapshot="- textbox \"Card Number\"\n- button \"Pay\"")
        result = extract_frame_elements(frame)
        assert len(result) == 2
        assert result[0]["role"] == "textbox"
        assert result[0]["name"] == "Card Number"
        assert result[0]["_frame"] == "login-frame"

    def test_empty_snapshot_returns_empty(self):
        from fantoma.dom.frames import extract_frame_elements
        frame = _make_frame(snapshot="")
        result = extract_frame_elements(frame)
        assert result == []

    def test_snapshot_failure_returns_empty(self):
        from fantoma.dom.frames import extract_frame_elements
        frame = MagicMock()
        type(frame).name = PropertyMock(return_value="broken")
        frame.locator.return_value.aria_snapshot.side_effect = Exception("Detached")
        result = extract_frame_elements(frame)
        assert result == []


class TestCollectAllFrameElements:
    def test_collects_from_all_child_frames(self):
        from fantoma.dom.frames import collect_all_frame_elements
        f1 = _make_frame(name="frame1", snapshot="- textbox \"Email\"")
        f2 = _make_frame(name="frame2", snapshot="- button \"Pay\"")
        page = _make_page_with_frames([f1, f2])
        result = collect_all_frame_elements(page)
        assert len(result) == 2
        names = [e["name"] for e in result]
        assert "Email" in names
        assert "Pay" in names

    def test_skips_main_frame(self):
        from fantoma.dom.frames import collect_all_frame_elements
        page = _make_page_with_frames([])
        result = collect_all_frame_elements(page)
        assert result == []

    def test_caps_at_max_frames(self):
        from fantoma.dom.frames import collect_all_frame_elements
        frames = [_make_frame(name=f"f{i}", snapshot=f'- button "Btn{i}"') for i in range(20)]
        page = _make_page_with_frames(frames)
        result = collect_all_frame_elements(page, max_frames=5)
        assert len(result) <= 5

    def test_skips_about_blank_frames(self):
        from fantoma.dom.frames import collect_all_frame_elements
        blank = _make_frame(url="about:blank", name="blank")
        real = _make_frame(name="real", snapshot="- button \"OK\"")
        page = _make_page_with_frames([blank, real])
        result = collect_all_frame_elements(page)
        assert len(result) == 1
        assert result[0]["name"] == "OK"
