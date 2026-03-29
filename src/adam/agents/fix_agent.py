"""Fix agent — Opus works through the problem directly.

No intermediate analysis. No handoff to Sonnet. Opus reads the
error, reads the affected files, reasons about the fix, and
produces surgical search-and-replace edits in a single call.

This is the Claude Code model: read → think → edit. The same
reasoning chain that understands the error also produces the fix.
No serialization boundary, no context loss.

Used in fix mode only. Construction mode still uses the Sonnet
workhorse pipeline.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from adam.agents.base import BaseAgent
from adam.types import AgentContext, ModelTier


class FileEdit(BaseModel):
    """A single search-and-replace edit."""
    file: str = ""  # File path relative to project root
    find: str = ""  # Exact string to find in the file
    replace: str = ""  # Replacement string


class FileCreate(BaseModel):
    """A new file to create."""
    file: str = ""
    content: str = ""


class SetupCommand(BaseModel):
    """A shell command to run."""
    command: str = ""
    working_directory: str = ""
    reason: str = ""


class FixResponse(BaseModel):
    """The complete fix — edits, new files, and commands."""
    assessment: str = ""  # What's wrong, in one paragraph
    edits: list[FileEdit] = Field(default_factory=list)
    creates: list[FileCreate] = Field(default_factory=list)
    commands: list[SetupCommand] = Field(default_factory=list)
    confidence: float = 0.0  # 0-1: how confident in this fix


class FixAgent(BaseAgent):
    """Opus agent that reads errors, reads code, and produces fixes.

    One call. No handoff. The same reasoning chain that understands
    the problem also produces the solution.
    """

    role = "fix_agent"
    model_tier = ModelTier.OPUS
    template_name = "fix_agent.j2"
    response_model = FixResponse
    use_tool_call = False  # Opus, JSON in text

    def build_system_prompt(self, context: AgentContext) -> str:
        return (
            "You are a senior engineer fixing a broken project. You are "
            "given the build/test output, the project file listing, and "
            "the source code of affected files.\n\n"
            "Produce surgical fixes as search-and-replace edits. For each "
            "edit, provide the EXACT string to find in the file and the "
            "EXACT string to replace it with. The 'find' string must appear "
            "exactly once in the file — include enough surrounding context "
            "to make it unique, but no more than necessary.\n\n"
            "Principles:\n"
            "- Fix the root cause, not the symptom\n"
            "- Minimal changes — don't refactor, don't improve, just fix\n"
            "- If the fix is 'run npm install', that's a command, not an edit\n"
            "- If a module 'app.core' doesn't exist but 'app.config' does, "
            "that's a wrong import path — fix the import, don't create app/core/\n"
            "- For test files with wrong import paths, check the file listing "
            "to find where the source file actually lives\n"
            "- NEVER suggest rm -rf or other destructive commands\n"
            "- Be precise with the 'find' strings — copy them exactly from "
            "the file content shown, preserving whitespace and quotes"
        )

    def build_template_context(self, context: AgentContext) -> dict[str, Any]:
        return {
            "build_output": context.error_output,
            "build_command": context.extra.get("build_command", ""),
            "file_listing": context.extra.get("file_listing", ""),
            "environment_info": context.extra.get("environment_info", ""),
            "affected_files": context.extra.get("affected_files", []),
        }

    def _temperature(self) -> float:
        return 0.2
