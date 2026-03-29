"""Tool-use fix agent — Opus with read/edit/run tools in a loop.

The same pattern as Claude Code: the model decides what to read,
what to edit, and what to run. It explores the project, reasons
about the errors, and fixes them autonomously.

No JSON parsing, no template rendering, no structured output
extraction. The model calls tools directly, we execute them,
and feed the results back. The conversation continues until the
model says it's done.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from adam.execution.runner import ShellRunner
from adam.llm.client import LLMClient
from adam.types import ModelTier

logger = logging.getLogger(__name__)

# Maximum turns to prevent runaway conversations
MAX_TURNS = 40
# Maximum total tokens before we stop
MAX_TOTAL_TOKENS = 500_000

# Tool definitions for the Anthropic API
TOOLS: list[dict[str, Any]] = [
    {
        "name": "read_file",
        "description": (
            "Read a file from the project. Returns the file content. "
            "Use this to examine source files, config files, etc."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path relative to project root",
                },
            },
            "required": ["path"],
        },
    },
    {
        "name": "edit_file",
        "description": (
            "Edit a file by replacing an exact string with a new string. "
            "The 'old' string must appear exactly in the file. Include "
            "enough context (surrounding lines) to make it unique."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path relative to project root",
                },
                "old": {
                    "type": "string",
                    "description": "Exact string to find in the file",
                },
                "new": {
                    "type": "string",
                    "description": "Replacement string",
                },
            },
            "required": ["path", "old", "new"],
        },
    },
    {
        "name": "create_file",
        "description": (
            "Create a new file with the given content. Parent directories "
            "are created automatically."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "File path relative to project root",
                },
                "content": {
                    "type": "string",
                    "description": "File content",
                },
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "run_command",
        "description": (
            "Run a shell command and return its output. Use for "
            "build checks, dependency installation, grep, etc. "
            "Do NOT use rm -rf or other destructive commands."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "Shell command to run",
                },
                "cwd": {
                    "type": "string",
                    "description": (
                        "Working directory relative to project root. "
                        "Empty string = project root."
                    ),
                },
            },
            "required": ["command"],
        },
    },
    {
        "name": "list_files",
        "description": (
            "List files in a directory. Returns file paths, one per line. "
            "Excludes node_modules, .git, __pycache__, dist, build."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "directory": {
                    "type": "string",
                    "description": (
                        "Directory path relative to project root. "
                        "Empty string = project root."
                    ),
                },
            },
            "required": [],
        },
    },
    {
        "name": "done",
        "description": (
            "Signal that you've finished fixing. Call this when "
            "the build passes or you've done everything you can."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "summary": {
                    "type": "string",
                    "description": "Brief summary of what was fixed",
                },
                "files_modified": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of files that were modified",
                },
            },
            "required": ["summary"],
        },
    },
]

# Blocked commands
_BLOCKED = ("rm -rf", "rm -r", "rmdir", "format", "mkfs", "drop database")


@dataclass
class ToolFixResult:
    """Result of the tool-use fix session."""
    success: bool = False
    summary: str = ""
    files_modified: list[str] = field(default_factory=list)
    turns: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    error: str = ""


class ToolFixAgent:
    """Opus with tools — reads, edits, runs commands autonomously.

    Usage:
        agent = ToolFixAgent(llm, project_root="/path/to/project")
        result = await agent.fix(
            build_cmd="npm run build",
            build_output="error TS2345: ...",
        )
    """

    def __init__(
        self,
        llm: LLMClient,
        project_root: str | Path,
        runner: ShellRunner | None = None,
    ) -> None:
        self._llm = llm
        self._root = str(project_root)
        self._runner = runner or ShellRunner()
        self._files_modified: list[str] = []

    async def fix(
        self,
        build_cmd: str,
        build_output: str,
        test_cmd: str = "",
        test_output: str = "",
    ) -> ToolFixResult:
        """Run the tool-use fix loop.

        Gives Opus the build error and lets it explore and fix
        the project using tools. Returns when the model calls
        'done' or we hit the turn/token limit.
        """
        result = ToolFixResult()

        system = (
            "You are a senior engineer fixing a broken project. "
            "You have tools to read files, edit files, run commands, "
            "and list the project structure.\n\n"
            "IMPORTANT: Be decisive. Fix quickly. You have a limited "
            "token budget — if you spend it all reading files without "
            "making edits, you fail. Prefer action over investigation.\n\n"
            "Your approach:\n"
            "1. Read the error — you often already know what to fix\n"
            "2. Read ONLY the file(s) that need changing\n"
            "3. Make the edit immediately\n"
            "4. Run the build to verify\n"
            "5. If new errors, fix those too\n"
            "6. Call 'done' when the build passes\n\n"
            "Anti-patterns to avoid:\n"
            "- Reading every file in the project before making any edit\n"
            "- Running grep to 'understand the full picture' before fixing\n"
            "- Reading test files when the error is in source files\n"
            "- Investigating the environment when the error is clearly "
            "a wrong import path\n\n"
            "For import errors like 'No module named app.core': just "
            "grep for the bad import, read the file, fix it. Don't "
            "explore the whole project first.\n\n"
            f"Project root: {self._root}\n"
            f"Build command: {build_cmd}"
        )

        # Initial message with the error
        user_content = f"The build is failing. Here's the output:\n\n```\n{build_output[:6000]}\n```"
        if test_output:
            user_content += f"\n\nTest output:\n\n```\n{test_output[:4000]}\n```"
        user_content += (
            f"\n\nBuild command: `{build_cmd}`"
        )
        if test_cmd:
            user_content += f"\nTest command: `{test_cmd}`"
        user_content += "\n\nPlease investigate and fix the errors."

        messages: list[dict[str, Any]] = [
            {"role": "user", "content": user_content},
        ]

        try:
            for turn in range(MAX_TURNS):
                result.turns = turn + 1

                # Call Opus
                response = await self._llm.complete(
                    tier=ModelTier.OPUS,
                    messages=messages,
                    system=system,
                    max_tokens=4096,
                    temperature=0.2,
                    tools=TOOLS,
                )

                result.total_input_tokens += response.input_tokens
                result.total_output_tokens += response.output_tokens

                # Log any text output
                if response.text:
                    logger.info("Fix agent: %s", response.text[:200])

                # Check token budget
                total = result.total_input_tokens + result.total_output_tokens
                if total > MAX_TOTAL_TOKENS:
                    logger.warning(
                        "Token limit reached (%d), stopping", total,
                    )
                    result.summary = "Token limit reached"
                    break

                # No tool calls = model is done talking
                if not response.tool_use:
                    result.success = True
                    result.summary = response.text[:200]
                    result.files_modified = self._files_modified
                    break

                # Build the assistant message with all content blocks
                assistant_content: list[dict[str, Any]] = []
                if response.text:
                    assistant_content.append({
                        "type": "text",
                        "text": response.text,
                    })
                for tool in response.tool_use:
                    assistant_content.append({
                        "type": "tool_use",
                        "id": tool["id"],
                        "name": tool["name"],
                        "input": tool["input"],
                    })

                messages.append({
                    "role": "assistant",
                    "content": assistant_content,
                })

                # Execute each tool call and collect results
                tool_results: list[dict[str, Any]] = []
                done_called = False

                for tool in response.tool_use:
                    tool_name = tool["name"]
                    tool_input = tool["input"]
                    tool_id = tool["id"]

                    logger.info(
                        "Tool call: %s(%s)",
                        tool_name,
                        str(tool_input)[:100],
                    )

                    if tool_name == "done":
                        result.success = True
                        result.summary = tool_input.get("summary", "")
                        result.files_modified = (
                            tool_input.get("files_modified", [])
                            or self._files_modified
                        )
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": tool_id,
                            "content": "Done acknowledged.",
                        })
                        done_called = True
                    else:
                        output = await self._execute_tool(
                            tool_name, tool_input,
                        )
                        tool_results.append({
                            "type": "tool_result",
                            "tool_use_id": tool_id,
                            "content": output,
                        })

                messages.append({
                    "role": "user",
                    "content": tool_results,
                })

                if done_called:
                    break

            else:
                result.summary = f"Reached max turns ({MAX_TURNS})"

        except Exception as e:
            logger.exception("Tool fix agent crashed: %s", e)
            result.error = str(e)

        result.files_modified = self._files_modified
        return result

    async def _execute_tool(
        self,
        name: str,
        inputs: dict[str, Any],
    ) -> str:
        """Execute a tool call and return the result as a string."""
        try:
            if name == "read_file":
                return self._tool_read_file(inputs["path"])
            elif name == "edit_file":
                return self._tool_edit_file(
                    inputs["path"], inputs["old"], inputs["new"],
                )
            elif name == "create_file":
                return self._tool_create_file(
                    inputs["path"], inputs["content"],
                )
            elif name == "run_command":
                return await self._tool_run_command(
                    inputs["command"], inputs.get("cwd", ""),
                )
            elif name == "list_files":
                return self._tool_list_files(
                    inputs.get("directory", ""),
                )
            else:
                return f"Unknown tool: {name}"
        except Exception as e:
            return f"Error: {e}"

    def _tool_read_file(self, path: str) -> str:
        """Read a file from the project."""
        full = Path(self._root) / path
        if not full.is_file():
            return f"File not found: {path}"
        try:
            content = full.read_text(encoding="utf-8")
            if len(content) > 10000:
                content = content[:10000] + "\n[truncated at 10000 chars]"
            return content
        except (OSError, UnicodeDecodeError) as e:
            return f"Error reading {path}: {e}"

    def _tool_edit_file(self, path: str, old: str, new: str) -> str:
        """Edit a file via search and replace."""
        full = Path(self._root) / path
        if not full.is_file():
            return f"File not found: {path}"

        content = full.read_text(encoding="utf-8")
        if old not in content:
            # Try stripping whitespace
            if old.strip() in content:
                old = old.strip()
            else:
                return (
                    f"String not found in {path}. "
                    f"Looking for: {old[:100]!r}"
                )

        count = content.count(old)
        new_content = content.replace(old, new, 1)
        full.write_text(new_content, encoding="utf-8")

        if path not in self._files_modified:
            self._files_modified.append(path)

        if count > 1:
            return (
                f"Edited {path} (replaced 1 of {count} occurrences). "
                f"Call edit_file again if you want to replace more."
            )
        return f"Edited {path}"

    def _tool_create_file(self, path: str, content: str) -> str:
        """Create a new file."""
        full = Path(self._root) / path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")

        if path not in self._files_modified:
            self._files_modified.append(path)

        return f"Created {path}"

    async def _tool_run_command(self, command: str, cwd: str = "") -> str:
        """Run a shell command."""
        # Block destructive commands
        cmd_lower = command.lower().strip()
        if any(d in cmd_lower for d in _BLOCKED):
            return f"Blocked: {command} (destructive command)"

        working_dir = self._root
        if cwd:
            working_dir = str(Path(self._root) / cwd)

        result = await self._runner.run(
            command, cwd=working_dir, timeout=120,
        )

        output = result.output
        if len(output) > 8000:
            output = output[:8000] + "\n[truncated]"

        if result.timed_out:
            return f"Command timed out after 120s (this may be expected for dev servers)\n{output}"
        if result.success:
            return output or "(no output)"
        return f"Exit code {result.return_code}\n{output}"

    def _tool_list_files(self, directory: str = "") -> str:
        """List files in a directory."""
        root = Path(self._root)
        target = root / directory if directory else root
        if not target.is_dir():
            return f"Directory not found: {directory}"

        skip = {
            "node_modules", ".git", "__pycache__", "dist", "build",
            ".adam", ".adam-screenshots", ".venv", "venv", ".next",
            "coverage",
        }

        lines: list[str] = []
        for p in sorted(target.rglob("*")):
            if not p.is_file():
                continue
            parts = p.relative_to(root).parts
            if any(part in skip for part in parts):
                continue
            lines.append(str(p.relative_to(root)))
            if len(lines) >= 200:
                lines.append("... (truncated)")
                break

        return "\n".join(lines) if lines else "(empty directory)"
