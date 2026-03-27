"""Jinja2 template loading and rendering for agent prompts."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, StrictUndefined

TEMPLATES_DIR = Path(__file__).parent / "templates"


class PromptLoader:
    """Loads and renders Jinja2 prompt templates."""

    def __init__(self, templates_dir: Path | None = None) -> None:
        self._dir = templates_dir or TEMPLATES_DIR
        self._env = Environment(
            loader=FileSystemLoader(str(self._dir)),
            undefined=StrictUndefined,
            trim_blocks=True,
            lstrip_blocks=True,
            keep_trailing_newline=False,
        )

    def render(self, template_name: str, **kwargs: Any) -> str:
        """Render a template with the given context variables."""
        template = self._env.get_template(template_name)
        return template.render(**kwargs)

    def has_template(self, template_name: str) -> bool:
        """Check if a template exists."""
        try:
            self._env.get_template(template_name)
            return True
        except Exception:
            return False

    def list_templates(self) -> list[str]:
        """List all available template names."""
        return sorted(self._env.list_templates())
