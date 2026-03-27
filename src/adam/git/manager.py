"""Git manager — wraps git operations for the orchestrator.

Provides init, commit, rollback, and status through ShellRunner.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from adam.execution.runner import ExecutionResult, ShellRunner

logger = logging.getLogger(__name__)


@dataclass
class CommitResult:
    """Result of a git commit operation."""
    success: bool
    commit_hash: str = ""
    message: str = ""
    error: str = ""


class GitManager:
    """Manages git operations for a project directory."""

    def __init__(
        self,
        project_root: str | Path,
        runner: ShellRunner | None = None,
    ) -> None:
        self._root = str(project_root)
        self._runner = runner or ShellRunner()

    async def init(self) -> ExecutionResult:
        """Initialize a git repository if one doesn't exist."""
        # Check if already a git repo
        check = await self._run("git rev-parse --is-inside-work-tree")
        if check.success:
            logger.info("Git repo already exists at %s", self._root)
            return check

        result = await self._run("git init")
        if result.success:
            logger.info("Initialized git repo at %s", self._root)
            # Create initial .gitignore
            gitignore = Path(self._root) / ".gitignore"
            if not gitignore.exists():
                gitignore.write_text(
                    "# Adam project\n"
                    ".adam-screenshots/\n"
                    "__pycache__/\n"
                    "*.pyc\n"
                    ".venv/\n"
                    "node_modules/\n"
                    ".env\n"
                    "*.egg-info/\n"
                    "dist/\n"
                    "build/\n",
                    encoding="utf-8",
                )
                await self._run("git add .gitignore")
                await self._run('git commit -m "Initial commit with .gitignore"')
        return result

    async def add(self, *paths: str) -> ExecutionResult:
        """Stage files for commit."""
        if not paths:
            return await self._run("git add -A")
        escaped = " ".join(f'"{p}"' for p in paths)
        return await self._run(f"git add {escaped}")

    async def commit(
        self,
        message: str,
        paths: list[str] | None = None,
    ) -> CommitResult:
        """Create a commit. Optionally stage specific paths first."""
        if paths:
            add_result = await self.add(*paths)
            if not add_result.success:
                return CommitResult(
                    success=False,
                    error=f"Failed to stage files: {add_result.output}",
                )

        # Check if there's anything to commit
        status = await self._run("git status --porcelain")
        if status.success and not status.stdout.strip():
            return CommitResult(
                success=True,
                message="Nothing to commit",
            )

        # Append co-author tag
        full_msg = (
            f"{message}\n\n"
            "Co-Authored-By: Adam <adam@meetadam.app>"
        )
        safe_msg = full_msg.replace("'", "'\\''")
        result = await self._run(f"git commit -m '{safe_msg}'")

        if not result.success:
            return CommitResult(
                success=False,
                error=result.output,
            )

        # Get the commit hash
        hash_result = await self._run("git rev-parse --short HEAD")
        commit_hash = hash_result.stdout.strip() if hash_result.success else ""

        logger.info("Committed: %s (%s)", commit_hash, message)
        return CommitResult(
            success=True,
            commit_hash=commit_hash,
            message=message,
        )

    async def commit_file(self, file_path: str, message: str = "") -> CommitResult:
        """Convenience: stage and commit a single file."""
        msg = message or f"Implement {file_path}"
        return await self.commit(msg, paths=[file_path])

    async def rollback_file(self, file_path: str) -> ExecutionResult:
        """Restore a file to its last committed state."""
        return await self._run(f'git checkout HEAD -- "{file_path}"')

    async def rollback_last_commit(self) -> ExecutionResult:
        """Undo the last commit, keeping changes staged."""
        return await self._run("git reset --soft HEAD~1")

    async def status(self) -> ExecutionResult:
        """Get git status."""
        return await self._run("git status --short")

    async def diff(self, staged: bool = False) -> ExecutionResult:
        """Get diff of changes."""
        cmd = "git diff --cached" if staged else "git diff"
        return await self._run(cmd)

    async def log(self, count: int = 10) -> ExecutionResult:
        """Get recent commit log."""
        return await self._run(
            f"git log --oneline --no-decorate -n {count}"
        )

    async def current_hash(self) -> str:
        """Get current commit hash."""
        result = await self._run("git rev-parse --short HEAD")
        return result.stdout.strip() if result.success else ""

    async def is_clean(self) -> bool:
        """Check if working tree is clean."""
        result = await self._run("git status --porcelain")
        return result.success and not result.stdout.strip()

    async def has_repo(self) -> bool:
        """Check if current directory is a git repo."""
        result = await self._run("git rev-parse --is-inside-work-tree")
        return result.success

    async def create_branch(self, name: str) -> ExecutionResult:
        """Create and switch to a new branch."""
        return await self._run(f"git checkout -b {name}")

    async def switch_branch(self, name: str) -> ExecutionResult:
        """Switch to an existing branch."""
        return await self._run(f"git checkout {name}")

    async def stash(self) -> ExecutionResult:
        """Stash current changes."""
        return await self._run("git stash")

    async def stash_pop(self) -> ExecutionResult:
        """Pop stashed changes."""
        return await self._run("git stash pop")

    async def _run(self, command: str) -> ExecutionResult:
        """Run a git command in the project root."""
        return await self._runner.run(command, cwd=self._root, timeout=30)
