"""Unified project observation — answers 'what state is the project in right now?'

Single function that checks build, runtime, visual output, and tests.
Returns a scored snapshot that can be compared to previous snapshots
to detect improvement or regression.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path

from adam.execution.runner import ShellRunner

logger = logging.getLogger(__name__)


class HealthLevel(IntEnum):
    """Project health, ordered by priority. Lower = more broken."""
    DOES_NOT_BUILD = 0
    BUILDS_BUT_CRASHES = 1
    RUNS_BUT_BROKEN = 2
    RUNS_WITH_ISSUES = 3
    RUNS_CLEAN = 4
    TESTS_FAILING = 5
    FULLY_HEALTHY = 6


@dataclass
class Issue:
    """A single observed problem."""
    level: HealthLevel
    summary: str
    file_path: str = ""  # specific file, if known
    error_output: str = ""  # raw error text
    line_number: int = 0


@dataclass
class Observation:
    """Complete snapshot of project state at a point in time."""
    health: HealthLevel
    issues: list[Issue] = field(default_factory=list)
    build_output: str = ""
    runtime_output: str = ""
    test_output: str = ""
    screenshot_path: str = ""

    @property
    def top_issue(self) -> Issue | None:
        """The single most important issue to fix next."""
        if not self.issues:
            return None
        # Lowest health level = highest priority
        return min(self.issues, key=lambda i: (i.level, i.file_path))

    @property
    def issue_count(self) -> int:
        return len(self.issues)

    @property
    def build_error_count(self) -> int:
        return sum(
            1 for i in self.issues
            if i.level == HealthLevel.DOES_NOT_BUILD
        )

    def is_better_than(self, other: Observation) -> bool:
        """Is this observation strictly better than the other?"""
        if self.health > other.health:
            return True
        if self.health == other.health:
            return self.issue_count < other.issue_count
        return False

    def is_worse_than(self, other: Observation) -> bool:
        """Is this observation strictly worse?"""
        if self.health < other.health:
            return True
        if self.health == other.health:
            return self.issue_count > other.issue_count
        return False


class Observer:
    """Observes the current state of a project."""

    def __init__(
        self,
        project_root: str | Path,
        runner: ShellRunner | None = None,
    ) -> None:
        self._root = str(project_root)
        self._runner = runner or ShellRunner()

    async def observe(
        self,
        build_cmd: str = "",
        run_cmd: str = "",
        test_cmd: str = "",
    ) -> Observation:
        """Take a complete observation of the project state.

        Checks in priority order, stops escalating once a level passes:
        1. Does it build?
        2. Does it run? (start and immediately check for crash)
        3. Do tests pass?
        """
        issues: list[Issue] = []

        # Level 0: Does it build?
        if build_cmd:
            build_result = await self._runner.run_build(
                build_cmd, cwd=self._root,
            )
            if not build_result.success:
                build_issues = self._parse_build_errors(
                    build_result.output
                )
                issues.extend(build_issues)

                return Observation(
                    health=HealthLevel.DOES_NOT_BUILD,
                    issues=issues,
                    build_output=build_result.output,
                )

        # Level 1-2: Does it run?
        if run_cmd:
            run_observation = await self._check_runtime(run_cmd)
            if run_observation:
                issues.extend(run_observation)
                if any(
                    i.level == HealthLevel.BUILDS_BUT_CRASHES
                    for i in run_observation
                ):
                    return Observation(
                        health=HealthLevel.BUILDS_BUT_CRASHES,
                        issues=issues,
                    )

        # Level 5: Do tests pass?
        if test_cmd:
            test_result = await self._runner.run_test(
                test_cmd, cwd=self._root,
            )
            if not test_result.success:
                test_issues = self._parse_test_errors(
                    test_result.output
                )
                issues.extend(test_issues)

                return Observation(
                    health=HealthLevel.TESTS_FAILING,
                    issues=issues,
                    test_output=test_result.output,
                )

        # If we got here with no issues, project is healthy
        if not issues:
            return Observation(health=HealthLevel.FULLY_HEALTHY)

        # We have some issues but it builds and runs
        return Observation(
            health=HealthLevel.RUNS_WITH_ISSUES,
            issues=issues,
        )

    def _parse_build_errors(self, output: str) -> list[Issue]:
        """Extract individual build errors with file paths."""
        import re
        issues: list[Issue] = []
        seen: set[str] = set()

        for line in output.split("\n"):
            # TypeScript: src/foo.ts(12,5): error TS2345: ...
            match = re.match(
                r"(src/[^\s:(]+)\((\d+),\d+\):\s*error\s+(.*)", line
            )
            if not match:
                # Alternative: src/foo.ts:12:5 - error TS2345: ...
                match = re.match(
                    r"(src/[^\s:(]+):(\d+):\d+\s*-?\s*error\s+(.*)", line
                )
            if match:
                fpath = match.group(1)
                line_num = int(match.group(2))
                msg = match.group(3).strip()
                key = f"{fpath}:{line_num}:{msg}"
                if key not in seen:
                    seen.add(key)
                    issues.append(Issue(
                        level=HealthLevel.DOES_NOT_BUILD,
                        summary=msg,
                        file_path=fpath,
                        error_output=line.strip(),
                        line_number=line_num,
                    ))

        # If we couldn't parse individual errors, create one generic issue
        if not issues and output.strip():
            issues.append(Issue(
                level=HealthLevel.DOES_NOT_BUILD,
                summary="Build failed (could not parse individual errors)",
                error_output=output[:2000],
            ))

        return issues

    def _parse_test_errors(self, output: str) -> list[Issue]:
        """Extract individual test failures."""
        import re
        issues: list[Issue] = []

        # Look for common test failure patterns
        # Vitest: FAIL src/foo.test.ts > test name
        for match in re.finditer(
            r"FAIL\s+(src/[^\s>]+)", output
        ):
            fpath = match.group(1).strip()
            issues.append(Issue(
                level=HealthLevel.TESTS_FAILING,
                summary=f"Test failed in {fpath}",
                file_path=fpath,
            ))

        if not issues and "FAIL" in output:
            issues.append(Issue(
                level=HealthLevel.TESTS_FAILING,
                summary="Tests failing",
                error_output=output[:2000],
            ))

        return issues

    async def _check_runtime(
        self, run_cmd: str,
    ) -> list[Issue] | None:
        """Start the app briefly and check for immediate crashes."""
        # Start the process
        result = await self._runner.run(
            run_cmd, cwd=self._root, timeout=10,
        )

        if result.return_code != 0 and not result.timed_out:
            # Crashed immediately
            return [Issue(
                level=HealthLevel.BUILDS_BUT_CRASHES,
                summary="Application crashed on startup",
                error_output=result.output[:2000],
            )]

        # Timed out means it's still running (good — it didn't crash)
        return None
