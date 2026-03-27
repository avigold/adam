"""Adam exception hierarchy."""

from __future__ import annotations


class AdamError(Exception):
    """Base exception for all Adam errors."""


class BudgetExhaustedError(AdamError):
    """Token budget exceeded for a model tier."""


class ShellExecutionError(AdamError):
    """A shell command failed or timed out."""


class RepairLimitExceededError(AdamError):
    """Maximum repair rounds exhausted without resolution."""


class ProjectNotFoundError(AdamError):
    """No .adam project file found."""


class ValidationError(AdamError):
    """A hard validation check failed."""


class AgentError(AdamError):
    """An agent invocation failed."""
