"""File implementer — writes a single source file from its specification.

Uses Sonnet. Equivalent to Postwriter's SceneWriter.
"""

from __future__ import annotations

from typing import Any

from adam.agents.base import BaseAgent
from adam.types import AgentContext, ModelTier


class FileImplementer(BaseAgent):
    """Writes a single source file from its specification and context."""

    role = "file_implementer"
    model_tier = ModelTier.SONNET
    template_name = "file_implementer.j2"
    response_model = None

    def build_system_prompt(self, context: AgentContext) -> str:
        lang = context.file_spec.get("language", "")
        parts = [
            "You are an expert software engineer. Write clean, correct, "
            "production-quality code. Implement exactly what is specified.",
            "",
            "Rules:",
            "- Write the complete file. No placeholders or TODOs.",
            "- Follow project conventions exactly.",
            "- Import only what you need.",
            "- Handle errors appropriately.",
            "- Do not add unspecified features.",
            "- Write idiomatic code for the language.",
        ]
        if lang:
            parts.append(f"\nLanguage: {lang}")
        parts.append(
            "\nReturn ONLY the file contents. No markdown fences, "
            "no explanation."
        )
        return "\n".join(parts)

    def build_template_context(self, context: AgentContext) -> dict[str, Any]:
        fs = context.file_spec
        return {
            "file_path": fs.get("path", ""),
            "file_purpose": fs.get("purpose", ""),
            "file_language": fs.get("language", ""),
            "interface_spec": fs.get("interface_spec", {}),
            "module_name": context.module_spec.get("name", ""),
            "module_purpose": context.module_spec.get("purpose", ""),
            "dependency_interfaces": context.dependency_interfaces,
            "related_files": context.related_files,
            "tech_stack": context.tech_stack,
            "conventions": context.conventions,
            "available_assets": context.available_assets,
        }


    def _temperature(self) -> float:
        return 0.7
