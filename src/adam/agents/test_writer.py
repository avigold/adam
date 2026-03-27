"""Test writer — writes tests for implemented files.

Uses Sonnet. Has access to the implementation, the spec, and module interfaces.
"""

from __future__ import annotations

from typing import Any

from adam.agents.base import BaseAgent
from adam.types import AgentContext, ModelTier


class TestWriter(BaseAgent):
    """Writes tests for an implemented source file."""

    role = "test_writer"
    model_tier = ModelTier.SONNET
    template_name = "test_writer.j2"
    response_model = None

    def __init__(self, llm: object, source_code: str = "") -> None:
        super().__init__(llm)  # type: ignore[arg-type]
        self._source_code = source_code

    def build_system_prompt(self, context: AgentContext) -> str:
        parts = [
            "You are an expert test engineer. Write thorough, clear tests "
            "that verify correctness without being brittle.",
            "",
            "Rules:",
            "- Test behaviour, not implementation details.",
            "- Cover happy path, edge cases, and error cases.",
            "- Use descriptive test names.",
            "- Keep tests independent.",
            "- Mock external dependencies, not code under test.",
        ]
        parts.append(
            "\nReturn ONLY the test file contents. No markdown fences, "
            "no explanation."
        )
        return "\n".join(parts)

    def build_template_context(self, context: AgentContext) -> dict[str, Any]:
        fs = context.file_spec
        build = context.tech_stack.get("build_system", {})
        test_runner = ""
        if isinstance(build, dict):
            test_runner = build.get("test_runner", "")

        return {
            "file_path": fs.get("path", ""),
            "file_purpose": fs.get("purpose", ""),
            "source_code": self._source_code,
            "interface_spec": fs.get("interface_spec", {}),
            "module_name": context.module_spec.get("name", ""),
            "tech_stack": context.tech_stack,
            "test_runner": test_runner,
        }


    def _temperature(self) -> float:
        return 0.5
