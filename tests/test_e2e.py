"""End-to-end integration test with mock LLM.

Real SQLite DB, real file I/O, real shell execution.
Only the LLM calls are mocked. Proves the full pipeline works:
spec → plan → scaffold → implement → validate → test gen → stop conditions.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from adam.config import LLMSettings, Settings
from adam.db.session import get_engine, get_session, init_db
from adam.llm.client import LLMClient, LLMResponse
from adam.orchestrator.engine import Orchestrator
from adam.orchestrator.file_loop import FileLoopResult
from adam.orchestrator.planner import PlanningOrchestrator
from adam.orchestrator.policies import ImplementationPolicy
from adam.project import ProjectState, detect_project, save_project
from adam.types import ModelTier


def _make_mock_llm() -> LLMClient:
    """Create a mock LLM that returns appropriate responses per agent."""
    mock = MagicMock(spec=LLMClient)
    mock.settings = LLMSettings()
    call_count = 0

    async def mock_complete(tier, messages, **kwargs):
        nonlocal call_count
        call_count += 1

        system = kwargs.get("system", "")

        # Architect response (Opus, first call)
        if tier == ModelTier.OPUS and "architect" in system.lower():
            return LLMResponse(
                text=json.dumps({
                    "tech_stack": {"language": "python", "test_runner": "pytest"},
                    "architecture_decisions": [
                        {"decision": "Single module", "rationale": "Simple project"}
                    ],
                    "modules": [
                        {"name": "calculator", "purpose": "Math operations", "dependencies": []}
                    ],
                    "conventions": {"naming": "snake_case"},
                    "build_system": {
                        "test_runner": "python -m pytest tests/",
                        "linter": "echo lint_ok",
                    },
                    "critical_path": ["calculator"],
                    "notes": "",
                }),
                input_tokens=100,
                output_tokens=200,
                model="mock-opus",
                stop_reason="end_turn",
            )

        # Scaffolder response
        if "scaffolder" in system.lower():
            return LLMResponse(
                text=json.dumps({
                    "directories": ["calculator", "tests"],
                    "files": [
                        {
                            "path": "tests/__init__.py",
                            "content": "",
                            "description": "Test package init",
                        },
                    ],
                    "notes": "",
                }),
                input_tokens=50,
                output_tokens=100,
                model="mock-sonnet",
                stop_reason="end_turn",
            )

        # Module planner response (tool_use)
        if kwargs.get("tools"):
            tool_name = kwargs["tools"][0]["name"]
            return LLMResponse(
                text="",
                tool_use=[{
                    "id": "tool_1",
                    "name": tool_name,
                    "input": {
                        "files": [{
                            "path": "calculator/ops.py",
                            "purpose": "Basic arithmetic operations",
                            "language": "python",
                            "interface_spec": {
                                "functions": ["add(a, b)", "multiply(a, b)"]
                            },
                            "dependencies": [],
                            "test_needed": True,
                            "notes": "",
                        }],
                        "implementation_order": ["calculator/ops.py"],
                        "test_strategy": "pytest",
                        "notes": "",
                    },
                }],
                input_tokens=50,
                output_tokens=100,
                model="mock-sonnet",
                stop_reason="end_turn",
            )

        # File implementer response (returns code)
        if "implement" in system.lower() or "engineer" in system.lower():
            return LLMResponse(
                text=(
                    "def add(a: int, b: int) -> int:\n"
                    "    return a + b\n"
                    "\n\n"
                    "def multiply(a: int, b: int) -> int:\n"
                    "    return a * b\n"
                ),
                input_tokens=50,
                output_tokens=80,
                model="mock-sonnet",
                stop_reason="end_turn",
            )

        # Test writer response
        if "test" in system.lower():
            return LLMResponse(
                text=(
                    "from calculator.ops import add, multiply\n\n"
                    "def test_add():\n"
                    "    assert add(2, 3) == 5\n\n"
                    "def test_multiply():\n"
                    "    assert multiply(3, 4) == 12\n"
                ),
                input_tokens=50,
                output_tokens=80,
                model="mock-sonnet",
                stop_reason="end_turn",
            )

        # Repair agent / diagnostician
        if "repair" in system.lower() or "debug" in system.lower():
            return LLMResponse(
                text=(
                    "def add(a: int, b: int) -> int:\n"
                    "    return a + b\n\n\n"
                    "def multiply(a: int, b: int) -> int:\n"
                    "    return a * b\n"
                ),
                input_tokens=50,
                output_tokens=50,
                model="mock-sonnet",
                stop_reason="end_turn",
            )

        # Integration auditor (Opus)
        if tier == ModelTier.OPUS:
            return LLMResponse(
                text=json.dumps({
                    "issues": [],
                    "integration_tests_needed": [],
                    "overall_assessment": "Clean integration",
                    "confidence": 0.9,
                }),
                input_tokens=50,
                output_tokens=50,
                model="mock-opus",
                stop_reason="end_turn",
            )

        # Default
        return LLMResponse(
            text="ok",
            input_tokens=10,
            output_tokens=10,
            model="mock",
            stop_reason="end_turn",
        )

    mock.complete = AsyncMock(side_effect=mock_complete)
    return mock


@pytest.fixture
def e2e_dir(tmp_path: Path) -> Path:
    """Create a project directory with a spec file."""
    ctx_dir = tmp_path / "context"
    ctx_dir.mkdir()
    (ctx_dir / "spec.md").write_text(
        "Build a simple calculator module with add and multiply functions."
    )
    return tmp_path


class TestEndToEnd:
    @pytest.mark.asyncio
    async def test_full_pipeline(self, e2e_dir: Path):
        """Run the full pipeline: plan → scaffold → implement → validate."""
        mock_llm = _make_mock_llm()

        # Configure SQLite in the test dir
        settings = Settings()
        engine = get_engine(settings, project_dir=str(e2e_dir))
        await init_db(engine)

        async with get_session(engine=engine) as session:
            # Phase 1: Planning
            planner = PlanningOrchestrator(
                mock_llm, session, project_root=str(e2e_dir),
            )
            project_id = await planner.run({
                "title": "Test Calculator",
                "description": "A simple calculator with add and multiply.",
                "features": ["addition", "multiplication"],
                "tech_stack": {"language": "python"},
            })

            # Verify project was created
            from adam.store.store import ProjectStore
            store = ProjectStore(session)
            project = await store.get_project_full(project_id)
            assert project is not None
            assert project.title == "Test Calculator"
            assert project.status == "implementing"
            assert len(project.modules) == 1
            assert project.modules[0].name == "calculator"

            # Verify files were planned
            files = await store.get_files(project.modules[0].id)
            assert len(files) >= 1
            assert files[0].path == "calculator/ops.py"

            # Verify scaffolding created directories
            assert (e2e_dir / "tests").is_dir()

            # Verify obligations were seeded
            obligations = await store.get_obligations(project_id)
            assert len(obligations) == 2  # addition, multiplication

            # Phase 2: Implementation
            file_results: list[FileLoopResult] = []

            def on_file(result: FileLoopResult, current: int, total: int) -> None:
                file_results.append(result)

            policy = ImplementationPolicy(
                max_repair_rounds=1,
                run_soft_critics=False,
                auto_commit=False,
            )
            orchestrator = Orchestrator(
                llm=mock_llm,
                session=session,
                project_root=str(e2e_dir),
                policy=policy,
                on_file_complete=on_file,
            )
            result = await orchestrator.run(project_id)

            # Verify files were written to disk
            ops_file = e2e_dir / "calculator" / "ops.py"
            assert ops_file.exists()
            content = ops_file.read_text()
            assert "def add" in content
            assert "def multiply" in content

            # Verify callback was called
            assert len(file_results) >= 1

            # Verify orchestrator result
            assert result.files_processed >= 1
            assert result.files_accepted >= 1

            # Verify stop conditions were evaluated
            assert len(result.stop_conditions) >= 4

        await engine.dispose()

    @pytest.mark.asyncio
    async def test_project_state_persists(self, e2e_dir: Path):
        """Project state file is created and can be read back."""
        state = ProjectState(
            project_id="test-123",
            phase="implementing",
            title="Test Project",
        )
        save_project(e2e_dir, state)

        loaded = detect_project(e2e_dir)
        assert loaded is not None
        assert loaded.project_id == "test-123"
        assert loaded.title == "Test Project"

        # .adam file should exist
        assert (e2e_dir / ".adam").exists()

    @pytest.mark.asyncio
    async def test_sqlite_db_created(self, e2e_dir: Path):
        """SQLite database is created in .adam/ directory."""
        settings = Settings()
        engine = get_engine(settings, project_dir=str(e2e_dir))
        await init_db(engine)

        db_path = e2e_dir / ".adam" / "adam.db"
        assert db_path.exists()

        # Can create and query data
        async with get_session(engine=engine) as session:
            from adam.store.store import ProjectStore
            store = ProjectStore(session)
            project = await store.create_project(
                title="DB Test",
                description="Testing SQLite",
            )
            await store.commit()
            assert project.id is not None

            loaded = await store.get_project(project.id)
            assert loaded is not None
            assert loaded.title == "DB Test"

        await engine.dispose()

    @pytest.mark.asyncio
    async def test_multipass_revision(self, e2e_dir: Path):
        """Integration audit can trigger file revision on a second pass."""
        audit_call_count = 0

        async def mock_complete(tier, messages, **kwargs):
            nonlocal audit_call_count
            system = kwargs.get("system", "")

            # Architect
            if tier == ModelTier.OPUS and "architect" in system.lower():
                return LLMResponse(
                    text=json.dumps({
                        "tech_stack": {"language": "python"},
                        "architecture_decisions": [],
                        "modules": [
                            {"name": "app", "purpose": "App", "dependencies": []}
                        ],
                        "conventions": {},
                        "build_system": {},
                        "critical_path": ["app"],
                        "notes": "",
                    }),
                    input_tokens=50, output_tokens=100,
                    model="mock", stop_reason="end_turn",
                )

            # Scaffolder
            if "scaffolder" in system.lower():
                return LLMResponse(
                    text=json.dumps({
                        "directories": ["app"],
                        "files": [],
                        "notes": "",
                    }),
                    input_tokens=50, output_tokens=50,
                    model="mock", stop_reason="end_turn",
                )

            # Module planner (tool_use)
            if kwargs.get("tools"):
                return LLMResponse(
                    text="",
                    tool_use=[{
                        "id": "t1",
                        "name": kwargs["tools"][0]["name"],
                        "input": {
                            "files": [
                                {
                                    "path": "app/main.py",
                                    "purpose": "Entry point",
                                    "language": "python",
                                    "interface_spec": {},
                                    "dependencies": [],
                                    "test_needed": False,
                                },
                                {
                                    "path": "app/helper.py",
                                    "purpose": "Helper functions",
                                    "language": "python",
                                    "interface_spec": {},
                                    "dependencies": [],
                                    "test_needed": False,
                                },
                            ],
                            "implementation_order": [
                                "app/main.py", "app/helper.py"
                            ],
                            "test_strategy": "pytest",
                            "notes": "",
                        },
                    }],
                    input_tokens=50, output_tokens=100,
                    model="mock", stop_reason="end_turn",
                )

            # Integration auditor (Opus) — first call flags an issue
            if tier == ModelTier.OPUS:
                audit_call_count += 1
                if audit_call_count == 1:
                    return LLMResponse(
                        text=json.dumps({
                            "issues": [{
                                "severity": "major",
                                "description": "main.py needs to import from helper.py",
                                "affected_modules": ["app"],
                                "affected_files": ["app/main.py"],
                                "fix_suggestion": "Add import",
                            }],
                            "integration_tests_needed": [],
                            "overall_assessment": "Needs revision",
                            "confidence": 0.8,
                        }),
                        input_tokens=50, output_tokens=100,
                        model="mock", stop_reason="end_turn",
                    )
                # Second call: all clear
                return LLMResponse(
                    text=json.dumps({
                        "issues": [],
                        "integration_tests_needed": [],
                        "overall_assessment": "Clean",
                        "confidence": 0.9,
                    }),
                    input_tokens=50, output_tokens=50,
                    model="mock", stop_reason="end_turn",
                )

            # File implementer / repair / test writer
            return LLMResponse(
                text="print('hello')\n",
                input_tokens=30, output_tokens=30,
                model="mock", stop_reason="end_turn",
            )

        mock_llm = MagicMock(spec=LLMClient)
        mock_llm.settings = LLMSettings()
        mock_llm.complete = AsyncMock(side_effect=mock_complete)

        settings = Settings()
        engine = get_engine(settings, project_dir=str(e2e_dir))
        await init_db(engine)

        async with get_session(engine=engine) as session:
            planner = PlanningOrchestrator(
                mock_llm, session, project_root=str(e2e_dir),
            )
            project_id = await planner.run({
                "title": "Revision Test",
                "description": "Test multi-pass revision.",
            })

            policy = ImplementationPolicy(
                max_repair_rounds=1,
                max_passes=3,
                run_soft_critics=False,
                auto_commit=False,
            )
            orchestrator = Orchestrator(
                llm=mock_llm,
                session=session,
                project_root=str(e2e_dir),
                policy=policy,
            )
            result = await orchestrator.run(project_id)

            # Should have done at least 2 passes
            assert result.total_passes >= 2
            # Integration audit was called at least twice
            assert audit_call_count >= 2
            # All files should be accepted
            assert result.files_accepted >= 2

        await engine.dispose()

        await engine.dispose()
