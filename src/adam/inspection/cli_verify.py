"""CLI output verification — run built CLI tools and verify they work.

For projects that produce command-line tools, this is the observation
method: run the tool with sample inputs, capture output, evaluate whether
it does what the spec says.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from adam.execution.runner import ShellRunner
from adam.llm.client import LLMClient
from adam.llm.json_extract import extract_json
from adam.types import ModelTier

logger = logging.getLogger(__name__)


@dataclass
class CLITestCase:
    """A test case for a CLI tool."""
    command: str  # The command to run
    name: str = ""  # Human-readable test name
    expected_exit_code: int = 0
    expected_output_contains: list[str] = field(default_factory=list)
    expected_output_not_contains: list[str] = field(default_factory=list)
    stdin: str = ""  # Input to pipe via stdin


@dataclass
class CLITestResult:
    """Result of running a CLI test case."""
    test_case: CLITestCase
    exit_code: int = -1
    stdout: str = ""
    stderr: str = ""
    passed: bool = False
    failure_reason: str = ""
    duration_seconds: float = 0

    @property
    def summary(self) -> str:
        status = "PASS" if self.passed else "FAIL"
        name = self.test_case.name or self.test_case.command[:40]
        return f"[{status}] {name} (exit={self.exit_code}, {self.duration_seconds:.1f}s)"


class CLIVerifier:
    """Verifies CLI tool output."""

    def __init__(
        self,
        runner: ShellRunner | None = None,
        llm: LLMClient | None = None,
    ) -> None:
        self._runner = runner or ShellRunner()
        self._llm = llm

    async def run_tests(
        self,
        test_cases: list[CLITestCase],
        cwd: str = ".",
        timeout: int = 30,
    ) -> list[CLITestResult]:
        """Run all CLI test cases and collect results."""
        results: list[CLITestResult] = []
        for tc in test_cases:
            result = await self._run_test(tc, cwd, timeout)
            results.append(result)
        return results

    async def _run_test(
        self,
        tc: CLITestCase,
        cwd: str,
        timeout: int,
    ) -> CLITestResult:
        """Run a single CLI test case."""
        command = tc.command
        if tc.stdin:
            command = f"echo '{tc.stdin}' | {command}"

        exec_result = await self._runner.run(command, cwd=cwd, timeout=timeout)

        # Check exit code
        passed = exec_result.return_code == tc.expected_exit_code

        failure_reason = ""
        if not passed:
            failure_reason = (
                f"Expected exit code {tc.expected_exit_code}, "
                f"got {exec_result.return_code}"
            )

        # Check expected output
        output = exec_result.stdout + exec_result.stderr
        if passed and tc.expected_output_contains:
            for expected in tc.expected_output_contains:
                if expected not in output:
                    passed = False
                    failure_reason = f"Expected output to contain: {expected!r}"
                    break

        if passed and tc.expected_output_not_contains:
            for forbidden in tc.expected_output_not_contains:
                if forbidden in output:
                    passed = False
                    failure_reason = f"Output should not contain: {forbidden!r}"
                    break

        return CLITestResult(
            test_case=tc,
            exit_code=exec_result.return_code,
            stdout=exec_result.stdout[:5000],
            stderr=exec_result.stderr[:5000],
            passed=passed,
            failure_reason=failure_reason,
            duration_seconds=exec_result.duration_seconds,
        )

    async def generate_test_cases(
        self,
        project_description: str,
        entry_point: str,
        tech_stack: dict | None = None,
    ) -> list[CLITestCase]:
        """Use Sonnet to generate CLI test cases from the spec.

        Given the project description and the CLI entry point command,
        generates a set of smoke test cases.
        """
        if not self._llm:
            return self._default_test_cases(entry_point)

        prompt = (
            f"## Project\n{project_description}\n\n"
            f"## CLI Entry Point\n`{entry_point}`\n\n"
        )
        if tech_stack:
            prompt += "## Tech Stack\n"
            for k, v in tech_stack.items():
                prompt += f"- {k}: {v}\n"
            prompt += "\n"

        prompt += (
            "Generate 3-6 smoke test cases for this CLI tool. "
            "Include:\n"
            "- A basic help/version check\n"
            "- The primary use case with sample input\n"
            "- An error case (invalid input)\n\n"
            "For each, provide:\n"
            '- command: the full shell command to run\n'
            '- name: short description\n'
            '- expected_exit_code: 0 for success, non-zero for errors\n'
            '- expected_output_contains: list of strings that should '
            'appear in output\n\n'
            "Respond with JSON: "
            '{"test_cases": [{"command": "...", "name": "...", '
            '"expected_exit_code": 0, '
            '"expected_output_contains": ["..."]}]}'
        )

        resp = await self._llm.complete(
            tier=ModelTier.SONNET,
            messages=[{"role": "user", "content": prompt}],
            system="Generate CLI test cases. Return only JSON.",
            max_tokens=1500,
            temperature=0.3,
        )

        data = extract_json(resp.text)
        if data and "test_cases" in data:
            return [
                CLITestCase(
                    command=tc.get("command", ""),
                    name=tc.get("name", ""),
                    expected_exit_code=tc.get("expected_exit_code", 0),
                    expected_output_contains=tc.get(
                        "expected_output_contains", []
                    ),
                )
                for tc in data["test_cases"]
                if tc.get("command")
            ]

        return self._default_test_cases(entry_point)

    @staticmethod
    def _default_test_cases(entry_point: str) -> list[CLITestCase]:
        """Fallback test cases when LLM generation fails."""
        return [
            CLITestCase(
                command=f"{entry_point} --help",
                name="help flag",
                expected_exit_code=0,
            ),
            CLITestCase(
                command=f"{entry_point} --version",
                name="version flag",
                # --version might not exist; exit code 0 or 2 both ok
            ),
        ]


# ---------------------------------------------------------------------------
# Entry point detection
# ---------------------------------------------------------------------------

def detect_cli_entry_point(
    project_root: str,
    tech_stack: dict | None = None,
    build_system: dict | None = None,
) -> str | None:
    """Detect the CLI entry point command for a project.

    Checks:
    1. Explicit build_system.entry_point
    2. package.json bin/scripts
    3. pyproject.toml scripts
    4. Cargo.toml binary name
    5. main.go
    """
    from pathlib import Path

    root = Path(project_root)

    # 1. Explicit config
    if build_system and build_system.get("entry_point"):
        return build_system["entry_point"]

    # 2. package.json
    pkg_json = root / "package.json"
    if pkg_json.exists():
        try:
            import json
            data = json.loads(pkg_json.read_text())
            # Check bin field
            if "bin" in data:
                bins = data["bin"]
                if isinstance(bins, str):
                    return f"node {bins}"
                if isinstance(bins, dict):
                    name = next(iter(bins))
                    return f"npx {name}"
            # Check scripts.start
            scripts = data.get("scripts", {})
            if "start" in scripts:
                return "npm start"
        except (json.JSONDecodeError, OSError):
            pass

    # 3. pyproject.toml
    pyproject = root / "pyproject.toml"
    if pyproject.exists():
        try:
            content = pyproject.read_text()
            lines = content.split("\n")

            # Look for [project.scripts] section entries
            in_scripts = False
            for line in lines:
                stripped = line.strip()
                if stripped == "[project.scripts]":
                    in_scripts = True
                    continue
                if stripped.startswith("["):
                    in_scripts = False
                    continue
                if in_scripts and "=" in stripped and not stripped.startswith("#"):
                    script_name = stripped.split("=")[0].strip().strip('"')
                    if script_name:
                        return script_name

            # Fallback: python -m package_name
            for line in lines:
                stripped = line.strip()
                if stripped.startswith("name") and "=" in stripped:
                    pkg_name = stripped.split("=")[1].strip().strip('"')
                    if pkg_name:
                        return f"python -m {pkg_name}"
        except OSError:
            pass

    # 4. Cargo.toml
    cargo = root / "Cargo.toml"
    if cargo.exists():
        try:
            content = cargo.read_text()
            for line in content.split("\n"):
                if line.strip().startswith("name"):
                    name = line.split("=")[1].strip().strip('"')
                    return "cargo run"
        except OSError:
            pass

    # 5. main.go
    if (root / "main.go").exists():
        return "go run ."

    # 6. Python main.py
    if (root / "main.py").exists():
        return "python main.py"

    return None
