"""Tests for prompt template loading."""

from pathlib import Path

from adam.prompts.loader import PromptLoader


class TestPromptLoader:
    def test_list_templates(self):
        loader = PromptLoader()
        templates = loader.list_templates()
        assert len(templates) >= 8
        assert "architect.j2" in templates
        assert "file_implementer.j2" in templates
        assert "test_writer.j2" in templates

    def test_has_template(self):
        loader = PromptLoader()
        assert loader.has_template("architect.j2")
        assert not loader.has_template("nonexistent.j2")

    def test_render_architect(self):
        loader = PromptLoader()
        result = loader.render(
            "architect.j2",
            project_description="Build a todo API",
            tech_stack={"language": "Python", "framework": "FastAPI"},
            user_context=[],
            user_context_images=[],
        )
        assert "todo API" in result
        assert "Python" in result
        assert "FastAPI" in result

    def test_render_file_implementer(self):
        loader = PromptLoader()
        result = loader.render(
            "file_implementer.j2",
            file_path="src/models.py",
            file_purpose="Database models",
            file_language="python",
            interface_spec={"classes": ["User", "Task"]},
            module_name="core",
            module_purpose="Core business logic",
            dependency_interfaces=[],
            related_files=[],
            tech_stack={"language": "python"},
            conventions={},
            available_assets="",
        )
        assert "src/models.py" in result
        assert "Database models" in result

    def test_render_diagnostician(self):
        loader = PromptLoader()
        result = loader.render(
            "diagnostician.j2",
            error_output="ImportError: No module named 'foo'",
            file_path="main.py",
            test_results=[],
            related_files=[],
        )
        assert "ImportError" in result

    def test_render_with_missing_template_dir(self, tmp_path: Path):
        loader = PromptLoader(tmp_path / "nonexistent")
        assert not loader.has_template("anything.j2")
