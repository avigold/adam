"""Supervisor — Opus meta-cognitive agent for strategic course correction.

When the progress monitor detects trouble (stagnation, oscillation,
regression), this agent reads the trajectory and decides what to do
differently. It doesn't fix code — it decides what the system should
do next.

Uses Opus because this requires project-level awareness and strategic
reasoning about the development process itself.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from adam.agents.base import BaseAgent
from adam.types import AgentContext, ModelTier


class Directive(BaseModel):
    """A strategic decision from the supervisor."""
    action: str = "continue"
    # Possible actions:
    #   "continue"            — keep going, the trouble signal is a false alarm
    #   "skip_and_return"     — skip this file/issue, come back to it later
    #   "change_approach"     — try a different repair strategy
    #   "reframe"             — the real problem is somewhere else
    #   "accept_imperfection" — this isn't worth more rounds, move on
    #   "freeze"              — we're oscillating, pick current state and stop
    #   "question_test"       — the test/validator may be wrong, not the code
    #   "restructure"         — the module/file decomposition is the problem
    #   "abort"               — stop this loop entirely, escalate to human

    reasoning: str = ""  # Why this decision was made
    target_files: list[str] = Field(default_factory=list)  # Files affected by this decision
    new_instruction: str = ""  # If change_approach: what to try instead
    confidence: float = 0.7


class SupervisorResponse(BaseModel):
    """Full response from the supervisor agent."""
    assessment: str = ""  # One-paragraph assessment of the situation
    directive: Directive
    observations: list[str] = Field(default_factory=list)  # Things the supervisor noticed


class Supervisor(BaseAgent):
    """Opus agent that makes strategic decisions about the development process.

    Called when the progress monitor detects trouble. Reads the trajectory
    of what's been tried and what's failed, and decides what to do next.
    This is meta-cognition — reasoning about the process, not the code.
    """

    role = "supervisor"
    model_tier = ModelTier.OPUS
    template_name = "supervisor.j2"
    response_model = SupervisorResponse
    use_tool_call = False  # Opus, JSON in text

    def build_system_prompt(self, context: AgentContext) -> str:
        return (
            "You are a senior engineering lead supervising an automated "
            "software development system. The system has been trying to "
            "fix issues in a project but the progress monitor has detected "
            "that it's not making effective progress.\n\n"
            "Your job is NOT to fix code. Your job is to decide what the "
            "system should do differently. You can see the trajectory of "
            "what's been tried and what happened. Think about:\n\n"
            "- Is the system attacking the right problem?\n"
            "- Is the repair approach appropriate for this kind of issue?\n"
            "- Would a different strategy be more effective?\n"
            "- Is this a situation where the right move is to stop trying?\n"
            "- Could the test or validator itself be wrong?\n"
            "- Is the problem actually in a different file than the one "
            "being repaired?\n\n"
            "Be decisive. The system is stuck — a non-answer wastes more "
            "budget. If you're not sure, 'skip_and_return' is always safe.\n\n"
            "Available actions:\n"
            "- continue: the signal is a false alarm, keep going\n"
            "- skip_and_return: skip this problem, try other work first\n"
            "- change_approach: try a fundamentally different repair strategy\n"
            "- reframe: the real problem is in a different file/module\n"
            "- accept_imperfection: not worth more rounds, move on\n"
            "- freeze: we're oscillating between states, pick current and stop\n"
            "- question_test: investigate whether the test/validator is wrong\n"
            "- restructure: the module or file decomposition is the root cause\n"
            "- abort: stop entirely, this needs human attention"
        )

    def build_template_context(self, context: AgentContext) -> dict[str, Any]:
        return {
            "trouble_signal": context.extra.get("trouble_signal", ""),
            "signal_evidence": context.extra.get("signal_evidence", ""),
            "monitor_summary": context.extra.get("monitor_summary", {}),
            "current_file": context.extra.get("current_file", ""),
            "current_error": context.extra.get("current_error", ""),
            "project_description": context.project_description,
            "tech_stack": context.tech_stack,
            "phase": context.extra.get("phase", ""),
        }

    def _temperature(self) -> float:
        return 0.4  # Strategic but grounded
