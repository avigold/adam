"""Repair agent — applies minimum targeted fixes to source code.

Uses Sonnet (temp=0.7). Equivalent to Postwriter's LocalRewriter.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from adam.agents.base import BaseAgent
from adam.types import AgentContext, ModelTier


@dataclass
class RepairSpec:
    """Specification for a repair action."""
    instruction: str
    diagnosis: str = ""
    preserve_constraints: list[str] = field(default_factory=list)
    banned_interventions: list[str] = field(default_factory=list)


class RepairAgent(BaseAgent):
    """Applies the minimum change needed to fix a diagnosed issue."""

    role = "repair_agent"
    model_tier = ModelTier.SONNET
    template_name = "repair_agent.j2"
    response_model = None

    def __init__(
        self,
        llm: object,
        source_code: str = "",
        repair_spec: RepairSpec | None = None,
    ) -> None:
        super().__init__(llm)  # type: ignore[arg-type]
        self._source_code = source_code
        self._repair = repair_spec or RepairSpec(instruction="Fix the issue.")

    def build_system_prompt(self, context: AgentContext) -> str:
        return (
            "You are a precise repair agent. Apply the minimum change "
            "needed to fix the diagnosed issue. Do not refactor. Do not "
            "add features. Return ONLY the complete fixed file."
        )

    def build_template_context(self, context: AgentContext) -> dict[str, Any]:
        return {
            "diagnosis": self._repair.diagnosis,
            "instruction": self._repair.instruction,
            "source_code": self._source_code,
            "error_output": context.error_output,
            "preserve_constraints": self._repair.preserve_constraints,
            "banned_interventions": self._repair.banned_interventions,
            "related_files": context.related_files,
        }


    def _temperature(self) -> float:
        return 0.7
