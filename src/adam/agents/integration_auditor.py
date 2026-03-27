"""Integration auditor — runs after all modules complete.

Uses Opus. Tests cross-module interactions, identifies integration issues.
Section 8.12 of the spec.
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel

from adam.agents.base import BaseAgent
from adam.types import AgentContext, ModelTier

logger = logging.getLogger(__name__)


class IntegrationIssue(BaseModel):
    severity: str  # critical, major, minor
    description: str
    affected_modules: list[str]
    affected_files: list[str] = []  # specific file paths needing revision
    fix_suggestion: str


class IntegrationAuditResponse(BaseModel):
    issues: list[IntegrationIssue]
    integration_tests_needed: list[str]
    overall_assessment: str
    confidence: float


class IntegrationAuditor(BaseAgent):
    """Audits cross-module interactions after individual modules are complete."""

    role = "integration_auditor"
    model_tier = ModelTier.OPUS
    template_name = "integration_auditor.j2"
    response_model = IntegrationAuditResponse
    use_tool_call = False

    def build_system_prompt(self, context: AgentContext) -> str:
        return (
            "You are a senior integration engineer. Review the project "
            "for cross-module issues. Respond with JSON."
        )

    def build_template_context(self, context: AgentContext) -> dict[str, Any]:
        return {
            "project_description": context.project_description,
            "architecture": context.architecture,
            "modules": context.extra.get("modules", []),
            "test_output": context.extra.get("test_output", ""),
            "obligations": context.extra.get("obligations", []),
        }


    def _temperature(self) -> float:
        return 0.5
