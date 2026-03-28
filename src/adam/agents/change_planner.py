"""Change planner — Sonnet agent that maps spec deltas to file-level work.

Given a SpecDiffResponse and the current project structure, produces a
concrete ChangePlan: which files to create, modify, or remove, and what
new obligations to seed. Well-scoped workhorse task for Sonnet — the
Opus spec differ has already done the hard reasoning about scope and
implications.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from adam.agents.base import BaseAgent
from adam.types import AgentContext, ModelTier


class PlannedFile(BaseModel):
    """A file that needs to be created or modified."""
    path: str
    action: str  # "create", "modify", "delete"
    module: str = ""  # Which module this belongs to
    purpose: str = ""  # What this file does / what changes
    dependencies: list[str] = Field(default_factory=list)
    implements: list[str] = Field(default_factory=list)  # Which feature changes this addresses
    priority: int = 5  # 1 = highest, 10 = lowest


class PlannedObligation(BaseModel):
    """A new or updated obligation to track."""
    description: str
    action: str  # "create", "update", "close"
    priority: str = "normal"
    source: str = "spec_change"


class ChangePlanResponse(BaseModel):
    """Concrete file-level plan for implementing spec changes."""
    summary: str
    files_to_create: list[PlannedFile] = Field(default_factory=list)
    files_to_modify: list[PlannedFile] = Field(default_factory=list)
    files_to_delete: list[str] = Field(default_factory=list)
    obligations: list[PlannedObligation] = Field(default_factory=list)
    implementation_order: list[str] = Field(default_factory=list)  # File paths in order
    new_dependencies: list[str] = Field(default_factory=list)  # npm/pip packages to add
    notes: Any = ""


class ChangePlanner(BaseAgent):
    """Maps spec-level changes to concrete file-level work.

    Sonnet tier — the scope and implications have already been worked out
    by the Opus spec differ. This agent's job is the mechanical decomposition
    into files, dependencies, and ordering.
    """

    role = "change_planner"
    model_tier = ModelTier.SONNET
    template_name = "change_planner.j2"
    response_model = ChangePlanResponse

    def build_system_prompt(self, context: AgentContext) -> str:
        return (
            "You are a module planner for an existing software project. "
            "You have been given a structured analysis of what changed in "
            "the project specification, and you need to produce a concrete "
            "plan: which files to create, modify, or delete, in what order, "
            "and what new obligations to track.\n\n"
            "Important principles:\n"
            "- Modify existing files rather than creating new ones where possible\n"
            "- Respect the existing project structure and conventions\n"
            "- Order files by dependency (implement dependencies first)\n"
            "- For modifications, describe what needs to change, not just 'update this file'\n"
            "- Don't plan changes to files that aren't actually affected\n"
            "- Mark deleted features' implementing files for removal only if "
            "they serve no other purpose"
        )

    def build_template_context(self, context: AgentContext) -> dict[str, Any]:
        return {
            "spec_diff": context.extra.get("spec_diff", {}),
            "project_description": context.project_description,
            "tech_stack": context.tech_stack,
            "conventions": context.conventions,
            "existing_modules": context.extra.get("existing_modules", []),
            "existing_files": context.extra.get("existing_files", []),
            "existing_obligations": context.extra.get(
                "existing_obligations", []
            ),
        }

    def _temperature(self) -> float:
        return 0.3  # Deterministic planning
