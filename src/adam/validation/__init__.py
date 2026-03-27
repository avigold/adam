"""Validation framework — hard validators and soft critics."""

from adam.validation.base import (
    BaseValidator,
    ValidationContext,
    ValidationSuite,
)

# Import validators to trigger registration
from adam.validation.hard import build_checker, lint_runner, test_runner, type_checker  # noqa: F401
from adam.validation.soft import code_quality, performance, security  # noqa: F401

__all__ = ["BaseValidator", "ValidationContext", "ValidationSuite"]
