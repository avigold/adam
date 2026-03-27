"""Orchestrator policies — configuration for the implementation loop."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ImplementationPolicy:
    """Controls the implementation loop behaviour."""
    max_repair_rounds: int = 5
    acceptance_threshold: float = 0.6  # Minimum composite score
    min_improvement_delta: float = 0.02
    hard_pass_required: bool = True
    run_soft_critics: bool = True
    visual_inspection: bool = False  # Enable for UI projects
    auto_commit: bool = True  # Git commit after each accepted file
    max_passes: int = 3  # Max full sweeps (initial + revision passes)
    human_checkpoints: bool = True  # Pause for approval after architecture
    test_per_file: bool = False  # If False, defer testing to end of module (greenfield)
