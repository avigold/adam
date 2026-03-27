"""Hard validator: test runner — executes tests and checks for pass/fail."""

from __future__ import annotations

from adam.execution.runner import ShellRunner
from adam.types import ValidationResult
from adam.validation.base import BaseValidator, ValidationContext, register_hard_validator


@register_hard_validator("test_runner")
class TestRunnerValidator(BaseValidator):
    """Runs the project's test suite and reports pass/fail."""

    def __init__(self, runner: ShellRunner | None = None) -> None:
        self._runner = runner or ShellRunner()

    async def validate(self, ctx: ValidationContext) -> ValidationResult:
        if not ctx.test_command:
            return ValidationResult(
                validator_name=self.name,
                is_hard=True,
                passed=True,
                diagnosis="No test command configured; skipping.",
            )

        result = await self._runner.run_test(
            ctx.test_command,
            cwd=ctx.project_root,
        )

        return ValidationResult(
            validator_name=self.name,
            is_hard=True,
            passed=result.success,
            diagnosis=result.output if not result.success else "All tests passed.",
            evidence=[{
                "command": result.command,
                "return_code": result.return_code,
                "duration": result.duration_seconds,
            }],
        )
