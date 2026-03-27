"""Error diagnostician — diagnoses test failures and build errors.

Uses Sonnet. Receives error output + source code, produces diagnosis.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from adam.agents.base import BaseAgent
from adam.types import AgentContext, ModelTier


class DiagnosisResponse(BaseModel):
    root_cause: str
    category: str
    affected_files: list[str]
    proposed_fix: str
    confidence: float


class ErrorDiagnostician(BaseAgent):
    """Diagnoses test failures, build errors, and runtime crashes."""

    role = "error_diagnostician"
    model_tier = ModelTier.SONNET
    template_name = "diagnostician.j2"
    response_model = DiagnosisResponse

    def build_system_prompt(self, context: AgentContext) -> str:
        return (
            "You are an expert debugger. Diagnose root causes of "
            "failures from error output and source code."
        )

    def build_template_context(self, context: AgentContext) -> dict[str, Any]:
        return {
            "error_output": context.error_output,
            "file_path": context.file_spec.get("path", ""),
            "test_results": context.test_results,
            "related_files": context.related_files,
        }


    def _temperature(self) -> float:
        return 0.3
