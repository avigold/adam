"""Tests for spec-aware bootstrap — prefilling logic."""

from pathlib import Path

from adam.cli.bootstrap import _extract_prefilled
from adam.context.loader import ContextFile
from adam.types import ContextType


class TestExtractPrefilled:
    def test_empty_context(self):
        result = _extract_prefilled([])
        assert result == {}

    def test_spec_provides_description(self):
        files = [
            ContextFile(
                path=Path("spec.md"),
                context_type=ContextType.SPEC,
                content="Build a REST API for managing todos.",
            ),
        ]
        result = _extract_prefilled(files)
        assert "description" in result
        assert "REST API" in result["description"]

    def test_tech_stack_from_file(self):
        files = [
            ContextFile(
                path=Path("tech-stack.md"),
                context_type=ContextType.TECH_STACK,
                content="Python 3.12, FastAPI, PostgreSQL",
            ),
        ]
        result = _extract_prefilled(files)
        assert "tech_stack" in result

    def test_mockup_implies_ui(self):
        files = [
            ContextFile(
                path=Path("mockup.png"),
                context_type=ContextType.MOCKUP,
                is_image=True,
            ),
        ]
        result = _extract_prefilled(files)
        assert result.get("has_ui") is True

    def test_description_ui_heuristic(self):
        files = [
            ContextFile(
                path=Path("spec.md"),
                context_type=ContextType.SPEC,
                content="Build a React dashboard for analytics.",
            ),
        ]
        result = _extract_prefilled(files)
        assert result.get("has_ui") is True

    def test_no_ui_heuristic(self):
        files = [
            ContextFile(
                path=Path("spec.md"),
                context_type=ContextType.SPEC,
                content="Build a CLI tool for data processing.",
            ),
        ]
        result = _extract_prefilled(files)
        assert result.get("has_ui") is None

    def test_frontmatter_title(self):
        files = [
            ContextFile(
                path=Path("spec.md"),
                context_type=ContextType.SPEC,
                content="Some spec",
                frontmatter={"title": "My Project"},
            ),
        ]
        result = _extract_prefilled(files)
        assert result.get("title") == "My Project"

    def test_frontmatter_features(self):
        files = [
            ContextFile(
                path=Path("spec.md"),
                context_type=ContextType.SPEC,
                content="Some spec",
                frontmatter={"features": "auth, search, notifications"},
            ),
        ]
        result = _extract_prefilled(files)
        assert result["features"] == ["auth", "search", "notifications"]

    def test_style_provides_conventions(self):
        files = [
            ContextFile(
                path=Path("style.md"),
                context_type=ContextType.STYLE,
                content="Use snake_case. Max 100 chars per line.",
            ),
        ]
        result = _extract_prefilled(files)
        assert "conventions" in result

    def test_multiple_files_combine(self):
        files = [
            ContextFile(
                path=Path("spec.md"),
                context_type=ContextType.SPEC,
                content="Build a web app.",
            ),
            ContextFile(
                path=Path("tech.md"),
                context_type=ContextType.TECH_STACK,
                content="TypeScript, Next.js",
            ),
            ContextFile(
                path=Path("mockup.png"),
                context_type=ContextType.MOCKUP,
                is_image=True,
            ),
        ]
        result = _extract_prefilled(files)
        assert "description" in result
        assert "tech_stack" in result
        assert result["has_ui"] is True
