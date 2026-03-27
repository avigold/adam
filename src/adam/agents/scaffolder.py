"""Project scaffolder — creates initial directory structure and config files.

Uses Sonnet. Runs after architecture design, before file implementation.
Creates the skeleton the file implementer will fill in: directories,
package config, entry points, gitignore, etc.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from adam.agents.base import BaseAgent
from adam.types import AgentContext, ModelTier


class ScaffoldFile(BaseModel):
    """A file to create during scaffolding."""
    path: str
    content: str
    description: str = ""


class ScaffoldResponse(BaseModel):
    """Structured scaffolding output."""
    directories: list[str] = []
    files: list[ScaffoldFile] = []
    notes: Any = ""


class Scaffolder(BaseAgent):
    """Creates the initial project skeleton on disk."""

    role = "scaffolder"
    model_tier = ModelTier.SONNET
    response_model = ScaffoldResponse
    use_tool_call = False

    def build_system_prompt(self, context: AgentContext) -> str:
        return (
            "You are a project scaffolder. Given an architecture design, "
            "you produce the initial directory structure and configuration "
            "files for the project. You create only what's needed to start "
            "implementation: package config, directory structure, entry "
            "points, gitignore, linter config.\n\n"
            "Do NOT create source files that will be written by the file "
            "implementer. Create only infrastructure: config files, "
            "empty __init__.py files, and boilerplate that every project "
            "of this type needs.\n\n"
            "Respond with a JSON object. Do not wrap in markdown code fences."
        )

    def build_user_message(self, context: AgentContext) -> str:
        parts = [f"## Project\n\n{context.project_description}"]

        if context.tech_stack:
            parts.append("\n## Tech Stack")
            for k, v in context.tech_stack.items():
                parts.append(f"- **{k}**: {v}")

        if context.architecture:
            parts.append("\n## Architecture")
            for k, v in context.architecture.items():
                if isinstance(v, list):
                    parts.append(f"\n### {k}")
                    for item in v:
                        parts.append(f"- {item}")
                else:
                    parts.append(f"- **{k}**: {v}")

        if context.conventions:
            parts.append("\n## Conventions")
            for k, v in context.conventions.items():
                parts.append(f"- **{k}**: {v}")

        # Show the modules so scaffolder knows what dirs to create
        modules = context.extra.get("modules", [])
        if modules:
            parts.append("\n## Modules")
            for m in modules:
                parts.append(
                    f"- **{m.get('name', '?')}**: {m.get('purpose', '')}"
                )

        build_sys = context.architecture.get("build_system", {})
        if build_sys:
            parts.append("\n## Build System")
            for k, v in build_sys.items():
                parts.append(f"- **{k}**: {v}")

        parts.append(
            "\n\nGenerate the scaffold. Return JSON with:"
            "\n- directories: list of directory paths to create"
            "\n- files: list of {path, content, description} for each "
            "config/boilerplate file"
            "\n- notes: anything worth flagging"
            "\n\nDo NOT generate source files — only infrastructure."
        )
        return "\n".join(parts)


    def _temperature(self) -> float:
        return 0.5
