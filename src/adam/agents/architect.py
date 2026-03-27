"""Architect agent — designs project architecture and module structure.

Uses Opus for high-level architectural reasoning.
Equivalent to Postwriter's PremiseArchitect + SpineArchitect.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from adam.agents.base import BaseAgent
from adam.types import AgentContext, ModelTier


class ArchitectureResponse(BaseModel):
    """Structured architecture design.

    All fields accept Any because LLMs return creative variations —
    lists of strings, lists of dicts, nested objects — and we'd rather
    accept the data than reject a valid architecture over schema pedantry.
    """
    tech_stack: dict[str, Any] = {}
    architecture_decisions: list[Any] = []
    modules: list[dict[str, Any]] = []
    conventions: dict[str, Any] = {}
    build_system: dict[str, Any] = {}
    critical_path: list[Any] = []
    notes: Any = ""


class Architect(BaseAgent):
    """Designs the overall architecture for a software project."""

    role = "architect"
    model_tier = ModelTier.OPUS
    template_name = "architect.j2"
    response_model = ArchitectureResponse
    use_tool_call = False

    def build_system_prompt(self, context: AgentContext) -> str:
        return (
            "You are a senior software architect. Respond with a JSON "
            "object. Do not wrap it in markdown code fences."
        )


    def _temperature(self) -> float:
        return 1.0
