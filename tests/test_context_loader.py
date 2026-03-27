"""Tests for context loader."""

from pathlib import Path

import pytest

from adam.context.loader import ContextFile, ContextLoader, ContextManifest
from adam.types import ContextType


@pytest.fixture
def tmp_context_dir(tmp_path: Path) -> Path:
    ctx = tmp_path / "context"
    ctx.mkdir()
    return ctx


class TestContextLoader:
    def test_load_empty_dir(self, tmp_context_dir: Path):
        loader = ContextLoader(tmp_context_dir)
        files = loader.load()
        assert files == []

    def test_load_nonexistent_dir(self, tmp_path: Path):
        loader = ContextLoader(tmp_path / "nonexistent")
        files = loader.load()
        assert files == []

    def test_load_spec_file(self, tmp_context_dir: Path):
        spec = tmp_context_dir / "spec.md"
        spec.write_text("# My Project\n\nBuild a todo app.")
        loader = ContextLoader(tmp_context_dir)
        files = loader.load()
        assert len(files) == 1
        assert files[0].context_type == ContextType.SPEC
        assert "todo app" in files[0].content

    def test_load_multiple_files(self, tmp_context_dir: Path):
        (tmp_context_dir / "spec.md").write_text("spec content")
        (tmp_context_dir / "architecture.md").write_text("arch content")
        (tmp_context_dir / "style.md").write_text("style content")
        loader = ContextLoader(tmp_context_dir)
        files = loader.load()
        assert len(files) == 3
        types = {f.context_type for f in files}
        assert ContextType.SPEC in types
        assert ContextType.ARCHITECTURE in types
        assert ContextType.STYLE in types

    def test_frontmatter_type_override(self, tmp_context_dir: Path):
        f = tmp_context_dir / "notes.md"
        f.write_text("---\ntype: spec\n---\nThis is actually a spec.")
        loader = ContextLoader(tmp_context_dir)
        files = loader.load()
        assert len(files) == 1
        assert files[0].context_type == ContextType.SPEC
        assert "This is actually a spec." in files[0].content

    def test_image_detection(self, tmp_context_dir: Path):
        img = tmp_context_dir / "mockup.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n")
        loader = ContextLoader(tmp_context_dir)
        files = loader.load()
        assert len(files) == 1
        assert files[0].is_image is True
        assert files[0].context_type == ContextType.MOCKUP

    def test_refresh_detects_new(self, tmp_context_dir: Path):
        (tmp_context_dir / "spec.md").write_text("spec")
        loader = ContextLoader(tmp_context_dir)
        loader.load()
        assert len(loader.files) == 1

        (tmp_context_dir / "style.md").write_text("style")
        new = loader.refresh()
        assert len(new) == 1
        assert new[0].context_type == ContextType.STYLE
        assert len(loader.files) == 2

    def test_hidden_files_ignored(self, tmp_context_dir: Path):
        (tmp_context_dir / ".hidden.md").write_text("secret")
        (tmp_context_dir / "spec.md").write_text("public")
        loader = ContextLoader(tmp_context_dir)
        files = loader.load()
        assert len(files) == 1
        assert files[0].name == "spec.md"

    def test_reference_subdir(self, tmp_context_dir: Path):
        ref_dir = tmp_context_dir / "reference"
        ref_dir.mkdir()
        (ref_dir / "api-docs.md").write_text("API documentation")
        loader = ContextLoader(tmp_context_dir)
        files = loader.load()
        assert len(files) == 1
        assert files[0].context_type == ContextType.REFERENCE


class TestContextManifest:
    def test_files_of_type(self):
        files = [
            ContextFile(path=Path("a.md"), context_type=ContextType.SPEC, content="a"),
            ContextFile(path=Path("b.md"), context_type=ContextType.STYLE, content="b"),
            ContextFile(path=Path("c.md"), context_type=ContextType.SPEC, content="c"),
        ]
        manifest = ContextManifest(
            files=files,
            types_present={ContextType.SPEC, ContextType.STYLE},
        )
        assert len(manifest.files_of_type(ContextType.SPEC)) == 2
        assert manifest.has_type(ContextType.SPEC)
        assert not manifest.has_type(ContextType.MOCKUP)
