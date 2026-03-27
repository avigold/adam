"""Integration tests — verify the full pipeline works end-to-end.

These tests mock the LLM client but exercise everything else:
context loading, planning, file loop, validation, repair planning,
git integration, and project state management.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from adam.config import LLMSettings
from adam.context.loader import ContextLoader
from adam.execution.runner import ShellRunner
from adam.git.manager import GitManager
from adam.llm.client import LLMClient, LLMResponse
from adam.orchestrator.file_loop import FileLoop
from adam.orchestrator.policies import ImplementationPolicy
from adam.profiles import apply_profile
from adam.project import ProjectState, detect_project, save_project
from adam.repair.planner import RepairPlanner
from adam.types import AgentContext, ValidationResult
from adam.validation.base import ValidationSuite
from adam.validation.hard.test_runner import TestRunnerValidator

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    """Create a project directory with context files."""
    ctx_dir = tmp_path / "context"
    ctx_dir.mkdir()
    (ctx_dir / "spec.md").write_text(
        "Build a simple CLI calculator that adds and multiplies numbers."
    )
    (ctx_dir / "tech-stack.md").write_text(
        "Language: Python 3.12\n"
        "Test runner: pytest\n"
        "Linter: ruff\n"
    )
    return tmp_path


@pytest.fixture
def mock_llm() -> LLMClient:
    """Create a mock LLM client that returns canned responses."""
    client = MagicMock(spec=LLMClient)
    client.settings = LLMSettings()
    client.complete = AsyncMock()
    return client


# ---------------------------------------------------------------------------
# Context loading integration
# ---------------------------------------------------------------------------


class TestContextLoadingIntegration:
    def test_full_context_pipeline(self, project_dir: Path):
        """Context loader finds and classifies all files."""
        loader = ContextLoader(project_dir / "context")
        files = loader.load()
        assert len(files) == 2

        manifest = loader.manifest
        types = {f.context_type.value for f in files}
        assert "spec" in types
        assert "tech_stack" in types

        spec_files = manifest.files_of_type(
            next(f.context_type for f in files if f.context_type.value == "spec")
        )
        assert len(spec_files) == 1
        assert "calculator" in spec_files[0].content


# ---------------------------------------------------------------------------
# File loop integration (with mock LLM, real shell runner)
# ---------------------------------------------------------------------------


class TestFileLoopIntegration:
    @pytest.mark.asyncio
    async def test_implement_and_validate_simple_file(self, tmp_path: Path):
        """File loop writes a file and validates it passes."""
        # Set up a mock LLM that returns valid Python
        mock_llm = MagicMock(spec=LLMClient)
        mock_llm.settings = LLMSettings()
        mock_llm.complete = AsyncMock(return_value=LLMResponse(
            text='def add(a: int, b: int) -> int:\n    return a + b\n',
            input_tokens=100,
            output_tokens=50,
            model="mock",
            stop_reason="end_turn",
        ))

        runner = ShellRunner()

        # Only use test runner (with a simple echo command)
        suite = ValidationSuite(
            hard_validators=[TestRunnerValidator(runner)],
        )

        policy = ImplementationPolicy(
            max_repair_rounds=1,
            run_soft_critics=False,
        )

        loop = FileLoop(
            llm=mock_llm,
            runner=runner,
            validation_suite=suite,
            policy=policy,
            project_root=str(tmp_path),
        )

        ctx = AgentContext(
            project_id="test",
            file_spec={
                "path": "calc.py",
                "purpose": "Basic calculator functions",
                "language": "python",
            },
            module_spec={"name": "calculator", "purpose": "Math operations"},
            tech_stack={"language": "python"},
        )

        result = await loop.process_file(
            ctx,
            test_command="echo 'tests pass'",  # Always passes
        )

        assert result.accepted
        assert (tmp_path / "calc.py").exists()
        content = (tmp_path / "calc.py").read_text()
        assert "def add" in content

    @pytest.mark.asyncio
    async def test_file_loop_with_failing_test_triggers_repair(
        self, tmp_path: Path,
    ):
        """When tests fail, the loop should attempt repair."""
        call_count = 0

        async def mock_complete(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # Initial implementation (has a bug)
                return LLMResponse(
                    text="def add(a, b):\n    return a - b  # bug!\n",
                    input_tokens=100,
                    output_tokens=50,
                    model="mock",
                    stop_reason="end_turn",
                )
            # Repair attempt (fixed)
            return LLMResponse(
                text="def add(a, b):\n    return a + b\n",
                input_tokens=100,
                output_tokens=50,
                model="mock",
                stop_reason="end_turn",
            )

        mock_llm = MagicMock(spec=LLMClient)
        mock_llm.settings = LLMSettings()
        mock_llm.complete = AsyncMock(side_effect=mock_complete)

        runner = ShellRunner()

        # Test command that always fails (simulating test failure)
        suite = ValidationSuite(
            hard_validators=[TestRunnerValidator(runner)],
        )

        policy = ImplementationPolicy(
            max_repair_rounds=2,
            run_soft_critics=False,
        )

        loop = FileLoop(
            llm=mock_llm,
            runner=runner,
            validation_suite=suite,
            policy=policy,
            project_root=str(tmp_path),
        )

        ctx = AgentContext(
            project_id="test",
            file_spec={
                "path": "calc.py",
                "purpose": "Calculator",
                "language": "python",
            },
            module_spec={"name": "calc"},
            tech_stack={},
        )

        await loop.process_file(
            ctx,
            test_command="exit 1",  # Always fails
        )

        # Should have tried repair
        assert call_count >= 2
        assert (tmp_path / "calc.py").exists()


# ---------------------------------------------------------------------------
# Repair planner integration
# ---------------------------------------------------------------------------


class TestRepairPlannerIntegration:
    def test_full_validation_to_repair_pipeline(self):
        """Validation results feed correctly into repair planning."""
        results = [
            ValidationResult(
                validator_name="test_runner",
                is_hard=True,
                passed=False,
                diagnosis="AssertionError: expected 5, got 3",
            ),
            ValidationResult(
                validator_name="lint_runner",
                is_hard=True,
                passed=True,
            ),
            ValidationResult(
                validator_name="code_quality",
                is_hard=False,
                score=0.3,
                diagnosis="Poor naming conventions",
                repair_suggestions=["Use descriptive variable names"],
            ),
        ]

        planner = RepairPlanner()
        actions = planner.plan(results)

        # Should have 2 actions: test failure + low code quality
        assert len(actions) == 2
        # Test failure first (priority 1)
        assert actions[0].target_dimension == "test_runner"
        assert "AssertionError" in actions[0].instruction
        # Code quality second (priority 7)
        assert actions[1].target_dimension == "code_quality"


# ---------------------------------------------------------------------------
# Git integration
# ---------------------------------------------------------------------------


class TestGitIntegration:
    @pytest.mark.asyncio
    async def test_full_git_workflow(self, tmp_path: Path):
        """Init, write, commit, verify, rollback."""
        gm = GitManager(tmp_path)

        # Init
        await gm.init()
        assert await gm.has_repo()

        # Write a file and commit
        (tmp_path / "main.py").write_text("print('hello')")
        result = await gm.commit_file("main.py", "Add main.py")
        assert result.success

        # Should be clean
        assert await gm.is_clean()

        # Modify and rollback
        (tmp_path / "main.py").write_text("print('modified')")
        assert not await gm.is_clean()

        await gm.rollback_file("main.py")
        assert await gm.is_clean()
        assert (tmp_path / "main.py").read_text() == "print('hello')"


# ---------------------------------------------------------------------------
# Project state integration
# ---------------------------------------------------------------------------


class TestProjectStateIntegration:
    def test_full_lifecycle(self, tmp_path: Path):
        """Project state persists through phases."""
        # No project initially
        assert detect_project(tmp_path) is None

        # Create
        state = ProjectState(
            project_id="abc-123",
            phase="planning",
            title="Test Calculator",
            tech_stack={"language": "python"},
        )
        save_project(tmp_path, state)

        # Detect
        loaded = detect_project(tmp_path)
        assert loaded is not None
        assert loaded.project_id == "abc-123"
        assert loaded.phase == "planning"

        # Update through phases
        from adam.project import update_phase
        for phase in ["implementing", "testing", "auditing", "complete"]:
            update_phase(tmp_path, phase)
            loaded = detect_project(tmp_path)
            assert loaded is not None
            assert loaded.phase == phase


# ---------------------------------------------------------------------------
# Profile integration
# ---------------------------------------------------------------------------


class TestProfileIntegration:
    def test_profile_affects_full_pipeline(self):
        """Applying a profile changes all relevant settings."""
        from adam.config import LLMSettings, OrchestratorSettings

        orch = OrchestratorSettings()
        llm = LLMSettings()

        # Default values
        assert orch.max_repair_rounds == 5
        assert llm.sonnet_token_budget == 0

        # Apply budget_conscious
        apply_profile("budget_conscious", orch, llm)
        assert orch.max_repair_rounds == 1
        assert orch.run_soft_critics is False
        assert llm.sonnet_token_budget == 500_000

        # Re-apply high_quality (overrides budget_conscious)
        apply_profile("high_quality", orch, llm)
        assert orch.max_repair_rounds == 5
        assert orch.run_soft_critics is True
