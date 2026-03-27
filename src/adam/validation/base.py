"""Validation base classes and suite orchestrator.

Equivalent to Postwriter's validation/base.py. Defines the contract
for hard validators and soft critics, plus the ValidationSuite that
runs them all.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from adam.types import ValidationResult

logger = logging.getLogger(__name__)


@dataclass
class ValidationContext:
    """Context passed to validators."""
    project_id: str = ""
    file_path: str = ""
    file_content: str = ""
    file_language: str = ""
    file_type: str = ""  # handler, model, utility, config, test, general
    module_name: str = ""
    project_root: str = "."
    tech_stack: dict[str, Any] = field(default_factory=dict)
    conventions: dict[str, Any] = field(default_factory=dict)
    test_command: str = ""
    lint_command: str = ""
    type_check_command: str = ""
    build_command: str = ""


# ---------------------------------------------------------------------------
# Validator registry
# ---------------------------------------------------------------------------

_HARD_VALIDATORS: dict[str, type[BaseValidator]] = {}
_SOFT_CRITICS: dict[str, type[BaseValidator]] = {}


def register_hard_validator(name: str):
    def decorator(cls: type[BaseValidator]):
        cls.name = name
        cls.is_hard = True
        _HARD_VALIDATORS[name] = cls
        return cls
    return decorator


def register_soft_critic(name: str):
    def decorator(cls: type[BaseValidator]):
        cls.name = name
        cls.is_hard = False
        _SOFT_CRITICS[name] = cls
        return cls
    return decorator


# ---------------------------------------------------------------------------
# Base validator
# ---------------------------------------------------------------------------

class BaseValidator:
    name: str = "base"
    is_hard: bool = True

    async def validate(self, ctx: ValidationContext) -> ValidationResult:
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Validation suite
# ---------------------------------------------------------------------------

class ValidationSuite:
    """Orchestrates running hard validators and soft critics."""

    def __init__(
        self,
        hard_validators: list[BaseValidator] | None = None,
        soft_critics: list[BaseValidator] | None = None,
    ) -> None:
        self._hard = hard_validators or []
        self._soft = soft_critics or []

    def get_hard_validators(self) -> list[BaseValidator]:
        return self._hard

    def get_soft_critics(self) -> list[BaseValidator]:
        return self._soft

    async def run_hard(self, ctx: ValidationContext) -> list[ValidationResult]:
        results = []
        for v in self._hard:
            try:
                result = await v.validate(ctx)
                results.append(result)
                logger.info(
                    "Hard validator %s: %s",
                    v.name, "PASS" if result.passed else "FAIL",
                )
            except Exception as e:
                logger.error("Hard validator %s crashed: %s", v.name, e)
                results.append(ValidationResult(
                    validator_name=v.name,
                    is_hard=True,
                    passed=False,
                    diagnosis=f"Validator crashed: {e}",
                ))
        return results

    async def run_soft(self, ctx: ValidationContext) -> list[ValidationResult]:
        results = []
        for c in self._soft:
            try:
                result = await c.validate(ctx)
                results.append(result)
                logger.info(
                    "Soft critic %s: score=%.2f",
                    c.name, result.score or 0.0,
                )
            except Exception as e:
                logger.error("Soft critic %s crashed: %s", c.name, e)
                results.append(ValidationResult(
                    validator_name=c.name,
                    is_hard=False,
                    score=0.0,
                    diagnosis=f"Critic crashed: {e}",
                ))
        return results

    async def run_all(self, ctx: ValidationContext) -> list[ValidationResult]:
        hard = await self.run_hard(ctx)
        soft = await self.run_soft(ctx)
        return hard + soft
