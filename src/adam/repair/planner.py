"""Repair planner — converts validation failures into ordered repair actions.

Equivalent to Postwriter's RepairPlanner. Maps validator names to priorities,
converts hard failures and low soft scores into RepairActionSpecs.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from adam.types import RepairPriority, ValidationResult


@dataclass
class RepairActionSpec:
    """Specification for a single repair action."""
    priority: RepairPriority
    target_dimension: str
    instruction: str
    preserve_constraints: list[str] = field(default_factory=list)
    allowed_interventions: list[str] = field(default_factory=list)
    banned_interventions: list[str] = field(default_factory=list)
    issue_diagnosis: str = ""
    issue_evidence: list[dict[str, str]] = field(default_factory=list)
    file_path: str = ""


# Map validator names to repair priorities
PRIORITY_MAP: dict[str, RepairPriority] = {
    "test_runner": RepairPriority.TEST_FAILURE,
    "build_checker": RepairPriority.BUILD_FAILURE,
    "type_checker": RepairPriority.TYPE_ERROR,
    "lint_runner": RepairPriority.LINT_ERROR,
    "security": RepairPriority.SECURITY_ISSUE,
    "performance": RepairPriority.PERFORMANCE_ISSUE,
    "code_quality": RepairPriority.CODE_QUALITY,
    "accessibility": RepairPriority.ACCESSIBILITY,
    "visual_fidelity": RepairPriority.VISUAL_FIDELITY,
    "test_coverage": RepairPriority.TEST_COVERAGE,
}

# Soft score threshold below which we generate a repair action
SOFT_REPAIR_THRESHOLD = 0.4


class RepairPlanner:
    """Converts validation results into ordered repair actions."""

    def plan(self, results: list[ValidationResult]) -> list[RepairActionSpec]:
        """Generate repair actions from validation failures and low scores."""
        actions: list[RepairActionSpec] = []

        for r in results:
            if r.is_hard and not r.passed:
                # Hard failure → always repair
                priority = PRIORITY_MAP.get(
                    r.validator_name, RepairPriority.CODE_QUALITY
                )
                actions.append(RepairActionSpec(
                    priority=priority,
                    target_dimension=r.validator_name,
                    instruction=self._instruction_from_hard(r),
                    issue_diagnosis=r.diagnosis,
                    issue_evidence=[{"detail": str(e)} for e in r.evidence],
                ))
            elif not r.is_hard and r.score is not None and r.score < SOFT_REPAIR_THRESHOLD:
                # Low soft score → repair
                priority = PRIORITY_MAP.get(
                    r.validator_name, RepairPriority.CODE_QUALITY
                )
                actions.append(RepairActionSpec(
                    priority=priority,
                    target_dimension=r.validator_name,
                    instruction=self._instruction_from_soft(r),
                    issue_diagnosis=r.diagnosis,
                ))

        # Sort by priority (lower number = higher priority)
        actions.sort(key=lambda a: a.priority.value)
        return actions

    def _instruction_from_hard(self, result: ValidationResult) -> str:
        """Generate repair instruction from a hard failure."""
        if result.validator_name == "test_runner":
            return f"Fix failing tests. Error output:\n{result.diagnosis}"
        elif result.validator_name == "build_checker":
            return f"Fix build errors:\n{result.diagnosis}"
        elif result.validator_name == "type_checker":
            return f"Fix type errors:\n{result.diagnosis}"
        elif result.validator_name == "lint_runner":
            return f"Fix lint errors:\n{result.diagnosis}"
        return f"Fix {result.validator_name} failure:\n{result.diagnosis}"

    def _instruction_from_soft(self, result: ValidationResult) -> str:
        """Generate repair instruction from a low soft score."""
        suggestions = result.repair_suggestions
        if suggestions:
            return (
                f"Improve {result.validator_name} (score: {result.score:.2f}). "
                f"Suggestions:\n" + "\n".join(f"- {s}" for s in suggestions)
            )
        return (
            f"Improve {result.validator_name} (score: {result.score:.2f}). "
            f"Diagnosis: {result.diagnosis}"
        )
