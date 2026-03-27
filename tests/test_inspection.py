"""Tests for visual inspection system."""

from pathlib import Path

from adam.inspection.evaluator import VisualEvaluation, VisualIssue
from adam.inspection.screenshotter import PageSpec, ScreenshotResult, _sanitize


class TestPageSpec:
    def test_defaults(self):
        ps = PageSpec(url="http://localhost:3000")
        assert ps.viewport_width == 1280
        assert ps.viewport_height == 720
        assert ps.wait_for == "networkidle"

    def test_custom(self):
        ps = PageSpec(
            url="http://localhost:3000/about",
            name="about",
            viewport_width=375,
            viewport_height=812,
        )
        assert ps.name == "about"
        assert ps.viewport_width == 375


class TestScreenshotResult:
    def test_success(self):
        sr = ScreenshotResult(
            page_name="index",
            url="http://localhost:3000",
            image_path=Path("/tmp/index.png"),
            success=True,
        )
        assert sr.success is True
        assert sr.error == ""

    def test_failure(self):
        sr = ScreenshotResult(
            page_name="index",
            url="http://localhost:3000",
            image_path=Path(""),
            success=False,
            error="Connection refused",
        )
        assert sr.success is False


class TestVisualEvaluation:
    def test_passing(self):
        ev = VisualEvaluation(page_name="index", score=0.8)
        assert ev.passes is True
        assert ev.issues == []

    def test_with_issues(self):
        ev = VisualEvaluation(
            page_name="index",
            score=0.4,
            passes=False,
            issues=[
                VisualIssue(
                    severity="major",
                    category="layout",
                    description="Header overlaps content",
                    suggestion="Add margin-top to main content",
                ),
            ],
        )
        assert ev.passes is False
        assert len(ev.issues) == 1
        assert ev.issues[0].severity == "major"


class TestSanitize:
    def test_simple(self):
        assert _sanitize("index") == "index"

    def test_special_chars(self):
        assert _sanitize("about/page") == "about_page"

    def test_spaces(self):
        assert _sanitize("my page") == "my_page"

    def test_already_clean(self):
        assert _sanitize("dashboard-v2") == "dashboard-v2"
