"""Stop condition evaluation — determines when a project is complete.

Section 12 of the spec. The system declares complete when ALL of:
1. All specified features have implementations
2. All tests pass
3. No hard validator failures (lint, types, build)
4. All soft critic scores above threshold
5. Visual inspection passes (if UI project)
6. The obligation ledger has no unresolved items
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from adam.orchestrator.obligations import ObligationStatus

logger = logging.getLogger(__name__)


@dataclass
class StopConditionResult:
    """Result of evaluating all stop conditions."""
    ready: bool  # True if all conditions met
    conditions: list[ConditionCheck] = field(default_factory=list)
    summary: str = ""

    @property
    def unmet_count(self) -> int:
        return sum(1 for c in self.conditions if not c.met)

    @property
    def met_count(self) -> int:
        return sum(1 for c in self.conditions if c.met)


@dataclass
class ConditionCheck:
    """Result of a single stop condition."""
    name: str
    met: bool
    detail: str = ""


def evaluate_stop_conditions(
    obligation_status: ObligationStatus,
    all_tests_pass: bool,
    hard_validators_pass: bool,
    soft_composite: float,
    acceptance_threshold: float,
    visual_passes: bool | None = None,  # None = no UI / not checked
    files_accepted: int = 0,
    files_total: int = 0,
) -> StopConditionResult:
    """Evaluate all stop conditions for project completion."""
    conditions: list[ConditionCheck] = []

    # 1. All features have implementations
    conditions.append(ConditionCheck(
        name="obligations_resolved",
        met=obligation_status.complete,
        detail=(
            f"{obligation_status.total - obligation_status.open} of "
            f"{obligation_status.total} obligations resolved"
            if obligation_status.total > 0
            else "No obligations defined"
        ),
    ))

    # 2. All tests pass
    conditions.append(ConditionCheck(
        name="tests_pass",
        met=all_tests_pass,
        detail="All tests passing" if all_tests_pass else "Some tests failing",
    ))

    # 3. No hard validator failures
    conditions.append(ConditionCheck(
        name="hard_validators_pass",
        met=hard_validators_pass,
        detail=(
            "All hard validators pass"
            if hard_validators_pass
            else "Hard validation failures remain"
        ),
    ))

    # 4. Soft critics above threshold
    critics_met = soft_composite >= acceptance_threshold
    conditions.append(ConditionCheck(
        name="soft_critics_above_threshold",
        met=critics_met,
        detail=f"Composite score: {soft_composite:.2f} (threshold: {acceptance_threshold:.2f})",
    ))

    # 5. Visual inspection (if applicable)
    if visual_passes is not None:
        conditions.append(ConditionCheck(
            name="visual_inspection_passes",
            met=visual_passes,
            detail=(
                "Visual inspection passed"
                if visual_passes
                else "Visual issues remain"
            ),
        ))

    # 6. All files accepted
    all_files_done = files_accepted >= files_total if files_total > 0 else True
    conditions.append(ConditionCheck(
        name="all_files_accepted",
        met=all_files_done,
        detail=f"{files_accepted}/{files_total} files accepted",
    ))

    all_met = all(c.met for c in conditions)

    # Build summary
    if all_met:
        summary = "All stop conditions met. Project is complete."
    else:
        unmet = [c for c in conditions if not c.met]
        summary = (
            f"{len(unmet)} condition(s) unmet: "
            + ", ".join(c.name for c in unmet)
        )

    return StopConditionResult(
        ready=all_met,
        conditions=conditions,
        summary=summary,
    )
