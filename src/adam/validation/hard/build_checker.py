"""Hard validator: build checker — verifies the project builds."""

from __future__ import annotations

from adam.execution.runner import ShellRunner
from adam.types import ValidationResult
from adam.validation.base import BaseValidator, ValidationContext, register_hard_validator


@register_hard_validator("build_checker")
class BuildCheckerValidator(BaseValidator):
    """Runs the project build command and checks for success."""

    def __init__(self, runner: ShellRunner | None = None) -> None:
        self._runner = runner or ShellRunner()

    async def validate(self, ctx: ValidationContext) -> ValidationResult:
        if not ctx.build_command:
            return ValidationResult(
                validator_name=self.name,
                is_hard=True,
                passed=True,
                diagnosis="No build command configured; skipping.",
            )

        result = await self._runner.run_build(
            ctx.build_command,
            cwd=ctx.project_root,
        )

        return ValidationResult(
            validator_name=self.name,
            is_hard=True,
            passed=result.success,
            diagnosis=result.output if not result.success else "Build succeeded.",
            evidence=[{
                "command": result.command,
                "return_code": result.return_code,
                "duration": result.duration_seconds,
            }],
        )
