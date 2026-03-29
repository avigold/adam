"""Build analyser — Opus agent that reads any build/test output and
identifies what's broken, where, and what to do about it.

Replaces language-specific regex parsing. Reads raw compiler, linter,
test runner, or runtime output from any language and produces structured
file-level error analysis with suggested fixes.

Uses Opus because this requires project-level reasoning — understanding
what an error in file A means for file B, distinguishing root causes
from symptoms, and prioritising fixes.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from adam.agents.base import BaseAgent
from adam.types import AgentContext, ModelTier


class BuildError(BaseModel):
    """A single error identified in build/test output."""
    file_path: str = ""  # Which file has the error (empty if unknown)
    line_number: int = 0  # Line number if identifiable
    error_type: str = ""  # "import", "type", "syntax", "runtime", "test", "config", "dependency"
    summary: str = ""  # One-line description of the error
    root_cause: str = ""  # What's actually wrong (may differ from the error message)
    suggested_fix: str = ""  # What should be changed to fix this
    is_root_cause: bool = True  # False if this is a symptom of another error


class SetupCommand(BaseModel):
    """A shell command that must be run to fix the environment."""
    command: str = ""  # e.g., "npm install", "pip install -r requirements.txt"
    working_directory: str = ""  # Relative to project root, empty = root
    reason: str = ""  # Why this command needs to run


class BuildAnalysis(BaseModel):
    """Complete analysis of build/test output."""
    language: str = ""  # Detected language/framework
    total_errors: int = 0
    errors: list[BuildError] = Field(default_factory=list)
    commands_to_run: list[SetupCommand] = Field(default_factory=list)  # Shell commands needed before/instead of file edits
    root_cause_summary: str = ""  # High-level: what's fundamentally wrong
    fix_order: list[str] = Field(default_factory=list)  # File paths in recommended fix order
    batch_fix_confidence: float = 0.0  # 0-1: confidence that ALL errors can be fixed in one pass
    notes: Any = ""


class BuildAnalyser(BaseAgent):
    """Opus agent that analyses build/test output from any language.

    Given raw output from a build command, test runner, linter, or
    runtime crash, identifies the individual errors, maps them to
    files, distinguishes root causes from symptoms, and suggests
    the order in which to fix things.
    """

    role = "build_analyser"
    model_tier = ModelTier.OPUS
    template_name = "build_analyser.j2"
    response_model = BuildAnalysis
    use_tool_call = False  # Opus, JSON in text

    def build_system_prompt(self, context: AgentContext) -> str:
        return (
            "You are a senior engineer debugging a build failure. You are "
            "given raw output from a build command, test runner, or runtime "
            "check. Your job is to:\n\n"
            "1. Identify every distinct error in the output\n"
            "2. Map each error to the file that needs to be fixed\n"
            "3. Distinguish root causes from symptoms — if fixing file A "
            "would also fix the errors in files B and C, say so\n"
            "4. Suggest the specific fix for each error\n"
            "5. Recommend the order in which to fix files\n\n"
            "Be precise about file paths. If the output shows "
            "'src/foo/bar.ts(12,5): error', the file_path is 'src/foo/bar.ts' "
            "and the line_number is 12.\n\n"
            "If you can't determine the file path from the output, set "
            "file_path to empty string and describe what you can in the summary.\n\n"
            "For test failures, the file_path should be the SOURCE file that "
            "needs fixing, not the test file — unless the test itself is wrong."
        )

    def build_template_context(self, context: AgentContext) -> dict[str, Any]:
        return {
            "build_output": context.error_output,
            "build_command": context.extra.get("build_command", ""),
            "project_description": context.project_description,
            "tech_stack": context.tech_stack,
            "file_listing": context.extra.get("file_listing", ""),
            "environment_info": context.extra.get("environment_info", ""),
        }

    def _temperature(self) -> float:
        return 0.2  # Analytical precision
