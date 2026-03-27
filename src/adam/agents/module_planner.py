"""Module planner — breaks modules into files with specs and ordering.

Uses Sonnet. Equivalent to Postwriter's ChapterPlanner + ScenePlanner.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from adam.agents.base import BaseAgent
from adam.types import AgentContext, ModelTier


class FilePlan(BaseModel):
    """Plan for a single file."""
    path: str
    purpose: str
    language: str
    interface_spec: dict[str, Any]
    dependencies: list[str]
    test_needed: bool
    implements: list[str] = []  # which features/obligations this file fulfills
    notes: Any = ""


class ModulePlanResponse(BaseModel):
    """Structured module decomposition."""
    files: list[FilePlan]
    implementation_order: list[str] = []
    test_strategy: str = ""
    notes: Any = ""


class ModulePlanner(BaseAgent):
    """Breaks a module into files with specs, interfaces, and ordering."""

    role = "module_planner"
    model_tier = ModelTier.SONNET
    template_name = "module_planner.j2"
    response_model = ModulePlanResponse

    def build_system_prompt(self, context: AgentContext) -> str:
        return (
            "You are a software module planner. Decompose the module into "
            "individual files with interfaces, dependencies, and ordering."
        )

    def build_template_context(self, context: AgentContext) -> dict[str, Any]:
        return {
            "project_description": context.project_description,
            "tech_stack": context.tech_stack,
            "architecture": context.architecture,
            "conventions": context.conventions,
            "module_name": context.module_spec.get("name", ""),
            "module_purpose": context.module_spec.get("purpose", ""),
            "module_dependencies": context.module_spec.get("dependencies", []),
            "all_modules": context.extra.get("all_modules", []),
            "obligations": context.extra.get("obligations", []),
        }

