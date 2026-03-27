"""Tests for validation framework."""

import pytest

from adam.types import ValidationResult
from adam.validation.base import BaseValidator, ValidationContext, ValidationSuite
from adam.validation.hard.lint_runner import LintRunnerValidator
from adam.validation.hard.test_runner import TestRunnerValidator


class TestValidationSuite:
    @pytest.mark.asyncio
    async def test_empty_suite(self):
        suite = ValidationSuite()
        results = await suite.run_all(ValidationContext())
        assert results == []

    @pytest.mark.asyncio
    async def test_hard_validator_runs(self):
        class AlwaysPass(BaseValidator):
            name = "always_pass"
            is_hard = True

            async def validate(self, ctx: ValidationContext) -> ValidationResult:
                return ValidationResult(
                    validator_name=self.name,
                    is_hard=True,
                    passed=True,
                )

        suite = ValidationSuite(hard_validators=[AlwaysPass()])
        results = await suite.run_hard(ValidationContext())
        assert len(results) == 1
        assert results[0].passed is True

    @pytest.mark.asyncio
    async def test_crashed_validator_returns_failure(self):
        class Crasher(BaseValidator):
            name = "crasher"
            is_hard = True

            async def validate(self, ctx: ValidationContext) -> ValidationResult:
                raise RuntimeError("boom")

        suite = ValidationSuite(hard_validators=[Crasher()])
        results = await suite.run_hard(ValidationContext())
        assert len(results) == 1
        assert results[0].passed is False
        assert "boom" in results[0].diagnosis


class TestTestRunnerValidator:
    @pytest.mark.asyncio
    async def test_no_command_skips(self):
        v = TestRunnerValidator()
        result = await v.validate(ValidationContext(test_command=""))
        assert result.passed is True

    @pytest.mark.asyncio
    async def test_passing_command(self):
        v = TestRunnerValidator()
        result = await v.validate(ValidationContext(
            test_command="echo 'all tests pass'",
            project_root=".",
        ))
        assert result.passed is True

    @pytest.mark.asyncio
    async def test_failing_command(self):
        v = TestRunnerValidator()
        result = await v.validate(ValidationContext(
            test_command="exit 1",
            project_root=".",
        ))
        assert result.passed is False


class TestLintRunnerValidator:
    @pytest.mark.asyncio
    async def test_no_command_skips(self):
        v = LintRunnerValidator()
        result = await v.validate(ValidationContext(lint_command=""))
        assert result.passed is True

    @pytest.mark.asyncio
    async def test_passing_lint(self):
        v = LintRunnerValidator()
        result = await v.validate(ValidationContext(
            lint_command="echo 'lint ok'",
            project_root=".",
        ))
        assert result.passed is True
