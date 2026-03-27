"""Shared types, enums, and data classes for Adam."""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Model tiering
# ---------------------------------------------------------------------------

class ModelTier(str, enum.Enum):
    OPUS = "opus"
    SONNET = "sonnet"
    HAIKU = "haiku"


# ---------------------------------------------------------------------------
# Project lifecycle
# ---------------------------------------------------------------------------

class ProjectStatus(str, enum.Enum):
    BOOTSTRAPPING = "bootstrapping"
    PLANNING = "planning"
    IMPLEMENTING = "implementing"
    TESTING = "testing"
    AUDITING = "auditing"
    COMPLETE = "complete"


class ModuleStatus(str, enum.Enum):
    PENDING = "pending"
    IMPLEMENTING = "implementing"
    TESTED = "tested"
    COMPLETE = "complete"


class FileStatus(str, enum.Enum):
    PENDING = "pending"
    WRITTEN = "written"
    TESTED = "tested"
    REVIEWED = "reviewed"


class TestStatus(str, enum.Enum):
    PENDING = "pending"
    PASSING = "passing"
    FAILING = "failing"


class TestType(str, enum.Enum):
    UNIT = "unit"
    INTEGRATION = "integration"
    E2E = "e2e"
    VISUAL = "visual"


class ObligationStatus(str, enum.Enum):
    OPEN = "open"
    IMPLEMENTED = "implemented"
    TESTED = "tested"
    VERIFIED = "verified"
    BLOCKED = "blocked"


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

class RepairPriority(int, enum.Enum):
    """Lower number = higher priority."""
    TEST_FAILURE = 1
    BUILD_FAILURE = 2
    TYPE_ERROR = 3
    LINT_ERROR = 4
    SECURITY_ISSUE = 5
    PERFORMANCE_ISSUE = 6
    CODE_QUALITY = 7
    ACCESSIBILITY = 8
    VISUAL_FIDELITY = 9
    TEST_COVERAGE = 10


# ---------------------------------------------------------------------------
# Dependencies
# ---------------------------------------------------------------------------

class DependencyType(str, enum.Enum):
    IMPORTS = "imports"
    CALLS = "calls"
    EXTENDS = "extends"
    IMPLEMENTS = "implements"
    CONFIGURES = "configures"


# ---------------------------------------------------------------------------
# Context types (for context/ directory files)
# ---------------------------------------------------------------------------

class ContextType(str, enum.Enum):
    SPEC = "spec"
    ARCHITECTURE = "architecture"
    STYLE = "style"
    TECH_STACK = "tech_stack"
    REFERENCE = "reference"
    MOCKUP = "mockup"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# Agent context and result (passed to/from agents)
# ---------------------------------------------------------------------------

@dataclass
class AgentContext:
    """Context window for an agent invocation."""
    project_id: str = ""
    project_description: str = ""
    tech_stack: dict[str, Any] = field(default_factory=dict)
    architecture: dict[str, Any] = field(default_factory=dict)
    conventions: dict[str, Any] = field(default_factory=dict)
    module_spec: dict[str, Any] = field(default_factory=dict)
    file_spec: dict[str, Any] = field(default_factory=dict)
    dependency_interfaces: list[dict[str, Any]] = field(default_factory=list)
    related_files: list[dict[str, Any]] = field(default_factory=list)
    test_results: list[dict[str, Any]] = field(default_factory=list)
    error_output: str = ""
    available_assets: str = ""  # Asset manifest summary for agents
    user_context: list[dict[str, Any]] = field(default_factory=list)
    user_context_images: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentResult:
    """Result from an agent invocation."""
    success: bool
    agent_role: str
    model_tier: ModelTier
    input_tokens: int = 0
    output_tokens: int = 0
    raw_response: str = ""
    parsed: Any = None
    error: str | None = None


# ---------------------------------------------------------------------------
# Validation result
# ---------------------------------------------------------------------------

@dataclass
class ValidationResult:
    """Result from a hard validator or soft critic."""
    validator_name: str
    is_hard: bool
    passed: bool | None = None  # None for soft critics
    score: float | None = None  # None for hard validators
    diagnosis: str = ""
    evidence: list[dict[str, Any]] = field(default_factory=list)
    file_references: list[str] = field(default_factory=list)
    repair_suggestions: list[str] = field(default_factory=list)
    confidence: float = 1.0


# ---------------------------------------------------------------------------
# Score vector (quality dimensions)
# ---------------------------------------------------------------------------

@dataclass
class ScoreVectorData:
    """Quality scores across dimensions."""
    hard_pass: bool = True

    # Hard dimensions (pass/fail reflected here)
    tests_pass: bool = True
    types_pass: bool = True
    lint_pass: bool = True
    build_pass: bool = True

    # Soft dimensions (0.0-1.0)
    code_readability: float = 0.5
    maintainability: float = 0.5
    idiomaticity: float = 0.5
    security: float = 0.5
    performance: float = 0.5
    accessibility: float = 0.5
    visual_fidelity: float = 0.5
    test_coverage: float = 0.5
    error_handling: float = 0.5

    composite: float = 0.5

    def compute_composite(self, weights: dict[str, float] | None = None) -> float:
        w = weights or DEFAULT_WEIGHTS
        total = 0.0
        weight_sum = 0.0
        for dim, weight in w.items():
            val = getattr(self, dim, None)
            if val is not None and isinstance(val, float):
                total += val * weight
                weight_sum += weight
        self.composite = total / weight_sum if weight_sum > 0 else 0.0
        return self.composite


DEFAULT_WEIGHTS: dict[str, float] = {
    "code_readability": 0.15,
    "maintainability": 0.15,
    "idiomaticity": 0.10,
    "security": 0.15,
    "performance": 0.10,
    "accessibility": 0.05,
    "visual_fidelity": 0.05,
    "test_coverage": 0.15,
    "error_handling": 0.10,
}


def scores_from_validation(results: list[ValidationResult]) -> ScoreVectorData:
    """Build a ScoreVectorData from a list of validation results."""
    sv = ScoreVectorData()
    for r in results:
        if r.is_hard:
            if not r.passed:
                sv.hard_pass = False
                if r.validator_name == "test_runner":
                    sv.tests_pass = False
                elif r.validator_name == "type_checker":
                    sv.types_pass = False
                elif r.validator_name == "lint_runner":
                    sv.lint_pass = False
                elif r.validator_name == "build_checker":
                    sv.build_pass = False
        else:
            dim = _CRITIC_DIMENSION_MAP.get(r.validator_name)
            if dim and r.score is not None:
                setattr(sv, dim, r.score)
    sv.compute_composite()
    return sv


_CRITIC_DIMENSION_MAP: dict[str, str] = {
    "code_quality": "code_readability",
    "maintainability": "maintainability",
    "idiomaticity": "idiomaticity",
    "security": "security",
    "performance": "performance",
    "accessibility": "accessibility",
    "visual_fidelity": "visual_fidelity",
    "test_coverage": "test_coverage",
    "error_handling": "error_handling",
}
