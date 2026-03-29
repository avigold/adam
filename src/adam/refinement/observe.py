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
    suggested_fix: str = ""  # from Opus analysis — precise repair instruction
    related_file_paths: list[str] = field(default_factory=list)  # files the fix needs to see


@dataclass
class Observation:
    """Complete snapshot of project state at a point in time."""
    health: HealthLevel
    issues: list[Issue] = field(default_factory=list)
    build_output: str = ""
    runtime_output: str = ""
    test_output: str = ""
    screenshot_path: str = ""
    setup_commands_ran: bool = False  # Whether env setup commands were executed

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
        """Is this observation strictly better than the other?

        Health level is the primary signal. A higher health level is
        always better, regardless of issue count — going from
        "can't find the compiler" (1 issue) to "compiler found 26
        type errors" (26 issues) is progress.

        When health levels are equal, fewer issues is better.
        Setup commands running is always considered progress.
        """
        if self.health > other.health:
            return True
        if self.health == other.health:
            if self.issue_count < other.issue_count:
                return True
            # Setup commands running is progress even if issue count rose
            if self.setup_commands_ran and not other.setup_commands_ran:
                return True
        return False

    def is_worse_than(self, other: Observation) -> bool:
        """Is this observation strictly worse?

        Worse ONLY if health level dropped. Issue count changes at
        the same health level are NOT considered worse — they often
        mean we fixed a blocker and uncovered real errors underneath.

        Examples:
        - TESTS_FAILING(1) → TESTS_FAILING(5): NOT worse. We fixed
          a setup crash and now see individual test failures.
        - TESTS_FAILING(18) → TESTS_FAILING(55): NOT worse. The
          analyser may just be counting differently.
        - TESTS_FAILING → DOES_NOT_BUILD: WORSE. Health dropped.
        """
        if self.health < other.health:
            return True
        return False


class Observer:
    """Observes the current state of a project."""

    def __init__(
        self,
        project_root: str | Path,
        runner: ShellRunner | None = None,
        llm: object | None = None,
    ) -> None:
        self._root = str(project_root)
        self._runner = runner or ShellRunner()
        self._llm = llm  # LLMClient, if available — enables Opus build analysis
        self._setup_commands_ran = False
        self._any_setup_succeeded = False

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
                build_issues = await self._analyse_errors(
                    build_result.output, build_cmd,
                    HealthLevel.DOES_NOT_BUILD,
                )
                issues.extend(build_issues)

                return Observation(
                    health=HealthLevel.DOES_NOT_BUILD,
                    issues=issues,
                    build_output=build_result.output,
                    setup_commands_ran=self._setup_commands_ran,
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
                test_issues = await self._analyse_errors(
                    test_result.output, test_cmd,
                    HealthLevel.TESTS_FAILING,
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

    async def _analyse_errors(
        self, output: str, command: str, level: HealthLevel,
    ) -> list[Issue]:
        """Analyse build/test output using Opus if available, regex fallback."""
        if self._llm is not None:
            issues = await self._llm_analyse(output, command, level)
            if issues:
                return issues

        # Fallback: regex-based parsing
        return self._regex_parse_errors(output, level)

    async def _llm_analyse(
        self, output: str, command: str, level: HealthLevel,
    ) -> list[Issue]:
        """Use the Opus build analyser to parse errors from any language."""
        try:
            from adam.agents.build_analyser import BuildAnalyser, BuildAnalysis
            from adam.types import AgentContext

            analyser = BuildAnalyser(self._llm)

            # Pass the project file listing so Opus can see where files
            # actually live — critical for resolving import paths
            file_listing = self._get_file_listing()
            env_info = self._get_environment_info()

            from adam.cli.display import thinking
            async with thinking("Analysing build output"):
                result = await analyser.execute(AgentContext(
                    error_output=output[:8000],
                    extra={
                        "build_command": command,
                        "file_listing": file_listing,
                        "environment_info": env_info,
                    },
                ))

            if not result.success or not isinstance(result.parsed, BuildAnalysis):
                logger.warning("Build analyser failed: %s", result.error)
                return []

            analysis = result.parsed
            issues: list[Issue] = []
            seen: set[str] = set()

            # Execute any setup commands first (npm install, etc.)
            if analysis.commands_to_run:
                self._any_setup_succeeded = False
                for cmd in analysis.commands_to_run:
                    if not cmd.command:
                        continue

                    # Safety: block destructive commands
                    cmd_lower = cmd.command.lower().strip()
                    if any(
                        dangerous in cmd_lower
                        for dangerous in (
                            "rm -rf", "rm -r", "rmdir",
                            "del /", "format", "mkfs",
                            "drop database", "drop table",
                        )
                    ):
                        logger.warning(
                            "Blocked destructive command: %s",
                            cmd.command,
                        )
                        continue
                    cwd = self._root
                    if cmd.working_directory:
                        cwd = str(Path(self._root) / cmd.working_directory)
                    logger.info(
                        "Running setup command: %s (in %s) — %s",
                        cmd.command, cwd, cmd.reason,
                    )
                    cmd_result = await self._runner.run(
                        cmd.command, cwd=cwd, timeout=120,
                    )
                    if cmd_result.success:
                        logger.info("Setup command succeeded: %s", cmd.command)
                        self._any_setup_succeeded = True
                    elif (
                        "npm install" in cmd.command
                        and "ERESOLVE" in cmd_result.output
                    ):
                        # Retry with --legacy-peer-deps
                        logger.info(
                            "npm peer conflict — retrying with "
                            "--legacy-peer-deps"
                        )
                        fallback = cmd.command.replace(
                            "npm install", "npm install --legacy-peer-deps",
                        )
                        cmd_result = await self._runner.run(
                            fallback, cwd=cwd, timeout=120,
                        )
                        if cmd_result.success:
                            logger.info("Setup command succeeded with --legacy-peer-deps")
                            self._any_setup_succeeded = True
                        else:
                            logger.warning(
                                "Setup command still failed: %s",
                                cmd_result.output[:200],
                            )
                    else:
                        logger.warning(
                            "Setup command failed: %s — %s",
                            cmd.command, cmd_result.output[:200],
                        )

                # Only mark as ran if at least one command succeeded
                if not self._any_setup_succeeded:
                    # No commands succeeded — don't mark as ran
                    logger.info(
                        "All setup commands failed — not marking as progress"
                    )
                else:
                    # Re-run the build to see if commands fixed it
                    recheck = await self._runner.run_build(
                        command, cwd=self._root,
                    )
                    if recheck.success:
                        logger.info(
                            "Build passes after running setup commands"
                        )
                        return []  # No issues — commands fixed it
                    # Update output and re-analyse the NEW errors
                    output = recheck.output
                    logger.info(
                        "Setup commands ran but build still fails — "
                        "re-analysing new errors"
                    )
                    # Mark that we ran commands (affects is_worse_than logic)
                    self._setup_commands_ran = True

            # Build a map of which files reference which other files
            # so the repair agent gets the full picture
            all_error_files = {e.file_path for e in analysis.errors if e.file_path}

            for error in analysis.errors:
                key = f"{error.file_path}:{error.line_number}:{error.summary}"
                if key in seen:
                    continue
                seen.add(key)

                # Identify related files from the analysis — other files
                # mentioned in the same error cluster
                related = [
                    e.file_path for e in analysis.errors
                    if e.file_path and e.file_path != error.file_path
                ]
                # Deduplicate while preserving order
                seen_related: set[str] = set()
                unique_related: list[str] = []
                for r in related:
                    if r not in seen_related:
                        seen_related.add(r)
                        unique_related.append(r)

                issues.append(Issue(
                    level=level,
                    summary=error.suggested_fix or error.summary,
                    file_path=error.file_path,
                    error_output=error.root_cause or error.summary,
                    line_number=error.line_number,
                    suggested_fix=error.suggested_fix,
                    related_file_paths=unique_related[:5],
                ))

            if not issues and output.strip():
                issues.append(Issue(
                    level=level,
                    summary=analysis.root_cause_summary or "Build/test failed",
                    error_output=output[:2000],
                ))

            return issues

        except Exception as e:
            logger.warning("LLM analysis failed, falling back to regex: %s", e)
            return []

    def _regex_parse_errors(
        self, output: str, level: HealthLevel,
    ) -> list[Issue]:
        """Regex fallback for common error patterns across languages."""
        import re
        issues: list[Issue] = []
        seen: set[str] = set()

        # TypeScript: src/foo.ts(12,5): error TS2345: ...
        # TypeScript: src/foo.ts:12:5 - error TS2345: ...
        # Python: File "app/main.py", line 12, in <module>
        # Rust: error[E0308]: mismatched types --> src/main.rs:12:5
        # Go: ./main.go:12:5: undefined: foo
        # General: file.ext:line:col: error message

        patterns = [
            # TypeScript style 1
            (r"(src/[^\s:(]+)\((\d+),\d+\):\s*error\s+(.*)", None),
            # TypeScript style 2 / general
            (r"([^\s:(]+\.(?:ts|tsx|js|jsx|py|rs|go)):(\d+):\d+\s*[-:]?\s*(?:error\s*)?(.*)", None),
            # Python traceback
            (r'File "([^"]+)", line (\d+)', "Python error"),
            # Rust
            (r"--> ([^\s:]+):(\d+):\d+", None),
            # Python ModuleNotFoundError / ImportError
            (r"(ModuleNotFoundError|ImportError):\s*(.*)", None),
        ]

        for line in output.split("\n"):
            for pattern, default_msg in patterns:
                match = re.match(pattern, line.strip())
                if not match:
                    match = re.search(pattern, line.strip())
                if match:
                    groups = match.groups()
                    if len(groups) >= 2:
                        fpath = groups[0]
                        try:
                            line_num = int(groups[1])
                        except (ValueError, IndexError):
                            line_num = 0
                        msg = groups[2].strip() if len(groups) > 2 else (
                            default_msg or line.strip()
                        )
                    else:
                        fpath = ""
                        line_num = 0
                        msg = " ".join(groups)

                    key = f"{fpath}:{line_num}:{msg[:80]}"
                    if key not in seen:
                        seen.add(key)
                        issues.append(Issue(
                            level=level,
                            summary=msg[:200],
                            file_path=fpath,
                            error_output=line.strip(),
                            line_number=line_num,
                        ))
                    break

        if not issues and output.strip():
            issues.append(Issue(
                level=level,
                summary="Build/test failed (could not parse individual errors)",
                error_output=output[:2000],
            ))

        return issues

    def _get_environment_info(self) -> str:
        """Gather environment info for the build analyser.

        Includes: Python version, Node version, dependency file contents,
        installed packages. This lets Opus make informed decisions about
        dependency management instead of guessing.
        """
        import subprocess

        lines: list[str] = []
        root = Path(self._root)

        # Python version
        try:
            result = subprocess.run(
                ["python3", "--version"],
                capture_output=True, text=True, timeout=5,
                cwd=self._root,
            )
            lines.append(f"Python: {result.stdout.strip()}")
        except Exception:
            lines.append("Python: not detected")

        # Node version
        try:
            result = subprocess.run(
                ["node", "--version"],
                capture_output=True, text=True, timeout=5,
            )
            lines.append(f"Node: {result.stdout.strip()}")
        except Exception:
            pass

        # Dependency file contents
        for dep_file in (
            "pyproject.toml", "requirements.txt", "setup.py",
            "package.json", "Cargo.toml", "go.mod",
        ):
            # Check root and common subdirs
            for candidate_dir in [root] + [
                root / sub for sub in ("site", "frontend", "client", "web")
                if (root / sub).is_dir()
            ]:
                path = candidate_dir / dep_file
                if path.is_file():
                    try:
                        content = path.read_text(encoding="utf-8")
                        if len(content) > 3000:
                            content = content[:3000] + "\n[truncated]"
                        rel = str(path.relative_to(root))
                        lines.append(f"\n--- {rel} ---\n{content}")
                    except (OSError, UnicodeDecodeError):
                        pass

        return "\n".join(lines) if lines else ""

    def _get_file_listing(self) -> str:
        """Get a tree-style listing of source files in the project.

        Excludes node_modules, .git, dist, build, __pycache__, .adam.
        Gives the build analyser the context to resolve import paths.
        """
        root = Path(self._root)
        skip_dirs = {
            "node_modules", ".git", "dist", "build", "__pycache__",
            ".adam", ".adam-screenshots", ".venv", "venv", ".next",
            "coverage", ".nyc_output",
        }
        lines: list[str] = []
        for p in sorted(root.rglob("*")):
            if not p.is_file():
                continue
            # Skip excluded directories
            parts = p.relative_to(root).parts
            if any(part in skip_dirs for part in parts):
                continue
            # Skip non-source files
            if p.suffix not in (
                ".ts", ".tsx", ".js", ".jsx", ".py", ".rs", ".go",
                ".json", ".toml", ".yaml", ".yml", ".md", ".css",
                ".html", ".svg",
            ):
                continue
            lines.append(str(p.relative_to(root)))
            if len(lines) >= 200:
                lines.append("... (truncated)")
                break
        return "\n".join(lines)

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
