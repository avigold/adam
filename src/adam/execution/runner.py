"""Shell execution — async subprocess runner for tests, builds, linters.

This is a NEW capability that Postwriter doesn't have. Sandboxed,
timeout-aware, with structured output capture.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from pathlib import Path

from adam.config import ExecutionSettings
from adam.errors import ShellExecutionError

logger = logging.getLogger(__name__)


@dataclass
class ExecutionResult:
    """Result of a shell command execution."""
    command: str
    return_code: int
    stdout: str
    stderr: str
    timed_out: bool = False
    duration_seconds: float = 0.0

    @property
    def success(self) -> bool:
        return self.return_code == 0 and not self.timed_out

    @property
    def output(self) -> str:
        """Combined stdout + stderr for diagnosis."""
        parts = []
        if self.stdout.strip():
            parts.append(self.stdout.strip())
        if self.stderr.strip():
            parts.append(self.stderr.strip())
        return "\n".join(parts)


class ShellRunner:
    """Executes shell commands asynchronously with timeout and output capture."""

    def __init__(self, settings: ExecutionSettings | None = None) -> None:
        self._settings = settings or ExecutionSettings()

    async def run(
        self,
        command: str,
        cwd: str | Path | None = None,
        timeout: int | None = None,
        env: dict[str, str] | None = None,
    ) -> ExecutionResult:
        """Run a shell command and capture output."""
        working_dir = str(cwd or self._settings.working_dir)
        timeout_sec = min(
            timeout or self._settings.default_timeout,
            self._settings.max_timeout,
        )

        # Merge environment
        run_env = os.environ.copy()
        if env:
            run_env.update(env)

        logger.info("Running: %s (cwd=%s, timeout=%ds)", command, working_dir, timeout_sec)

        import time
        start = time.monotonic()

        try:
            import signal

            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=working_dir,
                env=run_env,
                preexec_fn=os.setsid,  # New process group so we can kill the whole tree
            )

            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout_sec
                )
            except TimeoutError:
                # Kill the entire process group (npm + child vite/node)
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                except (ProcessLookupError, OSError):
                    proc.kill()
                await proc.wait()
                duration = time.monotonic() - start
                logger.warning("Command timed out after %.1fs: %s", duration, command)
                return ExecutionResult(
                    command=command,
                    return_code=-1,
                    stdout="",
                    stderr=f"Command timed out after {timeout_sec}s",
                    timed_out=True,
                    duration_seconds=duration,
                )

            duration = time.monotonic() - start
            stdout = stdout_bytes.decode("utf-8", errors="replace")
            stderr = stderr_bytes.decode("utf-8", errors="replace")

            # Truncate very long output
            max_output = 50_000
            if len(stdout) > max_output:
                stdout = stdout[:max_output] + f"\n... (truncated, {len(stdout)} total chars)"
            if len(stderr) > max_output:
                stderr = stderr[:max_output] + f"\n... (truncated, {len(stderr)} total chars)"

            result = ExecutionResult(
                command=command,
                return_code=proc.returncode or 0,
                stdout=stdout,
                stderr=stderr,
                duration_seconds=duration,
            )

            logger.info(
                "Command finished: rc=%d duration=%.1fs: %s",
                result.return_code, duration, command,
            )
            return result

        except OSError as e:
            raise ShellExecutionError(f"Failed to execute: {command}: {e}") from e

    async def run_test(
        self,
        command: str,
        cwd: str | Path | None = None,
        timeout: int | None = None,
    ) -> ExecutionResult:
        """Run a test command. Convenience wrapper with test-specific defaults."""
        return await self.run(
            command,
            cwd=cwd,
            timeout=timeout or 120,
        )

    async def run_lint(
        self,
        command: str,
        cwd: str | Path | None = None,
    ) -> ExecutionResult:
        """Run a linter. Short timeout."""
        return await self.run(command, cwd=cwd, timeout=60)

    async def run_build(
        self,
        command: str,
        cwd: str | Path | None = None,
    ) -> ExecutionResult:
        """Run a build command. Longer timeout."""
        return await self.run(command, cwd=cwd, timeout=300)

    async def run_type_check(
        self,
        command: str,
        cwd: str | Path | None = None,
    ) -> ExecutionResult:
        """Run a type checker."""
        return await self.run(command, cwd=cwd, timeout=120)
