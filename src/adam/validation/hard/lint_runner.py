"""Hard validator: lint runner — runs the project linter."""

from __future__ import annotations

from adam.execution.runner import ShellRunner
from adam.types import ValidationResult
from adam.validation.base import BaseValidator, ValidationContext, register_hard_validator


@register_hard_validator("lint_runner")
class LintRunnerValidator(BaseValidator):
    """Runs the project linter and checks for errors."""

    def __init__(self, runner: ShellRunner | None = None) -> None:
        self._runner = runner or ShellRunner()

    async def validate(self, ctx: ValidationContext) -> ValidationResult:
        if not ctx.lint_command:
            return ValidationResult(
                validator_name=self.name,
                is_hard=True,
                passed=True,
                diagnosis="No lint command configured; skipping.",
            )

        result = await self._runner.run_lint(
            ctx.lint_command,
            cwd=ctx.project_root,
        )

        return ValidationResult(
            validator_name=self.name,
            is_hard=True,
            passed=result.success,
            diagnosis=result.output if not result.success else "Lint passed.",
            evidence=[{
                "command": result.command,
                "return_code": result.return_code,
            }],
        )
