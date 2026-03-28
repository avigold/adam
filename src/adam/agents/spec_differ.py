"""Spec differ — Opus agent that analyses changes between spec versions.

Project-level awareness: reads old spec + new spec (and any other changed
context files) and produces a structured change analysis. This requires
Opus because it must reason about the full scope of the project to
understand what a spec change implies.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from adam.agents.base import BaseAgent
from adam.types import AgentContext, ModelTier


class FeatureChange(BaseModel):
    """A single feature-level change detected in the spec."""
    description: str = ""
    change_type: str = "modified"  # "added", "modified", "removed", "clarified"
    scope: str = "local"  # "local" (single file), "module" (multiple files), "architectural" (structural)
    priority: str = "normal"  # "critical", "high", "normal", "low"
    rationale: str = ""  # Why the agent thinks this change was made
    affected_areas: list[str] = Field(default_factory=list)  # Module/area names likely affected
    details: str = ""  # Additional context from the LLM


class ConstraintChange(BaseModel):
    """A change to project constraints (style, tech, architecture)."""
    description: str = ""
    change_type: str = "modified"  # "added", "modified", "removed", "relaxed", "tightened"
    category: str = "architecture"  # "style", "tech_stack", "architecture", "performance", "security"
    sweep_required: bool = False  # Whether existing files need checking


class SpecDiffResponse(BaseModel):
    """Structured analysis of spec changes."""
    summary: str = ""  # One-paragraph overview of what changed
    feature_changes: list[FeatureChange] = Field(default_factory=list)
    constraint_changes: list[ConstraintChange] = Field(default_factory=list)
    removed_features: list[str] = Field(default_factory=list)
    migration_notes: str = ""  # Any special considerations for the transition
    estimated_scope: str = "minor"  # "minor", "moderate", "major", "rewrite"
    confidence: float = 0.8


class SpecDiffer(BaseAgent):
    """Analyses changes between old and new specs to produce a structured delta.

    Uses Opus because this requires project-level reasoning — understanding
    what a textual change to the spec actually means for the codebase.
    """

    role = "spec_differ"
    model_tier = ModelTier.OPUS
    template_name = "spec_differ.j2"
    response_model = SpecDiffResponse
    use_tool_call = False  # Opus, JSON in text

    def build_system_prompt(self, context: AgentContext) -> str:
        return (
            "You are a senior technical analyst. You are given the previous "
            "version of a project specification alongside the current version, "
            "plus any other context files that have changed. Your job is to "
            "produce a precise, structured analysis of what changed and what "
            "it means for the codebase.\n\n"
            "Think about implications: a one-line addition to a spec may "
            "require new modules, new database tables, new API endpoints. "
            "A removal may leave dead code. A constraint change may require "
            "sweeping existing files.\n\n"
            "Be specific about scope. 'Added user authentication' is major. "
            "'Changed button colour' is minor. Distinguish between them."
        )

    def build_template_context(self, context: AgentContext) -> dict[str, Any]:
        return {
            "old_spec": context.extra.get("old_spec", ""),
            "new_spec": context.extra.get("new_spec", ""),
            "other_changes": context.extra.get("other_changes", []),
            "project_description": context.project_description,
            "tech_stack": context.tech_stack,
            "existing_modules": context.extra.get("existing_modules", []),
            "existing_obligations": context.extra.get(
                "existing_obligations", []
            ),
        }

    def _temperature(self) -> float:
        return 0.4  # Analytical, not creative
