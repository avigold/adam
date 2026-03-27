"""Hard validator: type checker — runs static type checking."""

from __future__ import annotations

from adam.execution.runner import ShellRunner
from adam.types import ValidationResult
from adam.validation.base import BaseValidator, ValidationContext, register_hard_validator


@register_hard_validator("type_checker")
class TypeCheckerValidator(BaseValidator):
    """Runs the project's type checker (mypy, pyright, tsc, etc.)."""

    def __init__(self, runner: ShellRunner | None = None) -> None:
        self._runner = runner or ShellRunner()

    async def validate(self, ctx: ValidationContext) -> ValidationResult:
        if not ctx.type_check_command:
            return ValidationResult(
                validator_name=self.name,
                is_hard=True,
                passed=True,
                diagnosis="No type check command configured; skipping.",
            )

        result = await self._runner.run_type_check(
            ctx.type_check_command,
            cwd=ctx.project_root,
        )

        return ValidationResult(
            validator_name=self.name,
            is_hard=True,
            passed=result.success,
            diagnosis=result.output if not result.success else "Type check passed.",
            evidence=[{
                "command": result.command,
                "return_code": result.return_code,
            }],
        )
