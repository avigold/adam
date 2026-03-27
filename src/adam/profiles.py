"""Generation profiles — presets for different quality/cost tradeoffs.

Same pattern as Postwriter's profiles module.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from adam.config import LLMSettings, OrchestratorSettings


@dataclass
class Profile:
    """A named configuration preset."""
    name: str
    description: str
    orchestrator_overrides: dict[str, Any]
    llm_overrides: dict[str, Any] | None = None


PROFILES: dict[str, Profile] = {
    "fast_draft": Profile(
        name="fast_draft",
        description="Quick generation with minimal validation. Good for prototyping.",
        orchestrator_overrides={
            "max_repair_rounds": 1,
            "acceptance_threshold": 0.3,
            "min_improvement_delta": 0.05,
            "run_soft_critics": False,
            "visual_inspection": False,
        },
    ),
    "standard": Profile(
        name="standard",
        description="Balanced quality and cost. Default for most projects.",
        orchestrator_overrides={
            "max_repair_rounds": 3,
            "acceptance_threshold": 0.5,
            "min_improvement_delta": 0.02,
            "run_soft_critics": True,
            "visual_inspection": False,
        },
    ),
    "high_quality": Profile(
        name="high_quality",
        description="Maximum quality with full validation. More expensive.",
        orchestrator_overrides={
            "max_repair_rounds": 5,
            "acceptance_threshold": 0.7,
            "min_improvement_delta": 0.01,
            "run_soft_critics": True,
            "visual_inspection": True,
        },
    ),
    "budget_conscious": Profile(
        name="budget_conscious",
        description="Minimal LLM usage. Tests only, no critics.",
        orchestrator_overrides={
            "max_repair_rounds": 1,
            "acceptance_threshold": 0.0,
            "run_soft_critics": False,
            "visual_inspection": False,
        },
        llm_overrides={
            "opus_token_budget": 50_000,
            "sonnet_token_budget": 500_000,
            "haiku_token_budget": 200_000,
        },
    ),
}


def apply_profile(
    profile_name: str,
    orchestrator_settings: OrchestratorSettings,
    llm_settings: LLMSettings | None = None,
) -> None:
    """Apply a profile's overrides to settings objects (mutates in place)."""
    profile = PROFILES.get(profile_name)
    if profile is None:
        return

    for key, value in profile.orchestrator_overrides.items():
        if hasattr(orchestrator_settings, key):
            setattr(orchestrator_settings, key, value)

    if llm_settings and profile.llm_overrides:
        for key, value in profile.llm_overrides.items():
            if hasattr(llm_settings, key):
                setattr(llm_settings, key, value)


def list_profiles() -> list[dict[str, str]]:
    """List available profiles with descriptions."""
    return [
        {"name": p.name, "description": p.description}
        for p in PROFILES.values()
    ]
