"""Pipeline stages — explicit lifecycle for Adam projects.

Stages:
    plan      → architecture + module planning + scaffolding
    construct → file implementation + validation + repair (the existing orchestrator)
    refine    → observe → fix one thing → verify → repeat (new refinement mode)
    done      → all stop conditions met

Each stage is a self-contained unit that reads project state, does its
work, and writes updated state. The pipeline runner coordinates transitions.
"""

from __future__ import annotations

import enum
import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from adam.config import Settings
from adam.project import ProjectState, detect_project, save_project, update_phase

logger = logging.getLogger(__name__)


class Stage(str, enum.Enum):
    """Explicit pipeline stages, in order."""
    PLAN = "plan"
    CONSTRUCT = "construct"
    REFINE = "refine"
    DONE = "done"
    # Iterate is not in the linear sequence — it's a re-entry point
    # that feeds back into CONSTRUCT → REFINE → DONE.
    ITERATE = "iterate"

    @property
    def next(self) -> Stage | None:
        """The stage that follows this one, or None if terminal."""
        linear = [Stage.PLAN, Stage.CONSTRUCT, Stage.REFINE, Stage.DONE]
        # ITERATE feeds back into CONSTRUCT
        if self == Stage.ITERATE:
            return Stage.CONSTRUCT
        try:
            idx = linear.index(self)
        except ValueError:
            return None
        if idx + 1 < len(linear):
            return linear[idx + 1]
        return None


# Map old phase names to pipeline stages for backward compatibility
_PHASE_TO_STAGE: dict[str, Stage] = {
    "bootstrapping": Stage.PLAN,
    "planning": Stage.PLAN,
    "implementing": Stage.CONSTRUCT,
    "testing": Stage.CONSTRUCT,
    "auditing": Stage.CONSTRUCT,
    "refining": Stage.REFINE,
    "iterating": Stage.ITERATE,
    "complete": Stage.DONE,
}


@dataclass
class StageResult:
    """Outcome of running a pipeline stage."""
    stage: Stage
    success: bool
    advance: bool = True  # Should the pipeline advance to the next stage?
    error: str = ""
    details: dict[str, Any] = field(default_factory=dict)


class Pipeline:
    """Runs Adam's lifecycle as an explicit sequence of stages.

    The pipeline is additive — it wraps the existing PlanningOrchestrator
    and Orchestrator without changing them, and adds the refinement stage
    after construction completes.

    Usage:
        pipeline = Pipeline(settings, project_dir)
        await pipeline.run()
    """

    def __init__(
        self,
        settings: Settings,
        project_dir: Path,
        context_dir: Path | None = None,
        no_checkpoints: bool = False,
        on_stage_start: Any = None,
        on_stage_end: Any = None,
        user_instructions: str = "",
    ) -> None:
        self._settings = settings
        self._project_dir = project_dir
        self._context_dir = context_dir or project_dir / "context"
        self._no_checkpoints = no_checkpoints
        self._on_stage_start = on_stage_start
        self._on_stage_end = on_stage_end
        self._user_instructions = user_instructions

    def detect_stage(self) -> Stage:
        """Determine current stage from project state.

        If the project is complete and context files have changed,
        automatically enters ITERATE instead of DONE.
        """
        state = detect_project(self._project_dir)
        if state is None:
            return Stage.PLAN

        stage = _PHASE_TO_STAGE.get(state.phase, Stage.PLAN)

        # Auto-detect iteration: project is done but context changed
        if stage == Stage.DONE:
            from adam.context.fingerprint import ContextFingerprinter
            from adam.context.loader import ContextLoader

            fingerprinter = ContextFingerprinter(self._project_dir)
            if fingerprinter.has_stored_state():
                loader = ContextLoader(self._context_dir)
                current_files = loader.load()
                diff = fingerprinter.diff(current_files)
                if diff.has_changes:
                    return Stage.ITERATE

        return stage

    async def run(
        self,
        start_stage: Stage | None = None,
        stop_after: Stage | None = None,
    ) -> list[StageResult]:
        """Run the pipeline from start_stage through stop_after.

        If start_stage is None, auto-detects from project state.
        If stop_after is None, runs to completion.
        """
        current = start_stage or self.detect_stage()
        results: list[StageResult] = []

        while current != Stage.DONE:
            logger.info("Pipeline: entering stage %s", current.value)

            if self._on_stage_start:
                self._on_stage_start(current)

            result = await self._run_stage(current)
            results.append(result)

            if self._on_stage_end:
                self._on_stage_end(current, result)

            if not result.success:
                logger.warning(
                    "Stage %s failed: %s", current.value, result.error,
                )
                break

            if not result.advance:
                logger.info("Stage %s completed but not advancing", current.value)
                break

            if stop_after and current == stop_after:
                logger.info("Stopping after %s (as requested)", current.value)
                break

            next_stage = current.next
            if next_stage is None:
                break
            current = next_stage

        return results

    async def _run_stage(self, stage: Stage) -> StageResult:
        """Dispatch to the appropriate stage handler."""
        handlers = {
            Stage.PLAN: self._run_plan,
            Stage.CONSTRUCT: self._run_construct,
            Stage.REFINE: self._run_refine,
            Stage.ITERATE: self._run_iterate,
        }

        handler = handlers.get(stage)
        if handler is None:
            return StageResult(
                stage=stage, success=False,
                error=f"No handler for stage {stage.value}",
            )

        try:
            return await handler()
        except Exception as e:
            logger.exception("Stage %s raised an exception", stage.value)
            return StageResult(
                stage=stage, success=False,
                error=str(e),
            )

    async def _run_plan(self) -> StageResult:
        """Run the planning stage: architecture + module planning + scaffold."""
        from adam.cli.bootstrap import collect_project_brief
        from adam.context.loader import ContextLoader
        from adam.db.session import get_engine, get_session, init_db
        from adam.execution.dependencies import DependencyManager
        from adam.git.manager import GitManager
        from adam.llm.client import LLMClient
        from adam.orchestrator.planner import PlanningOrchestrator
        from adam.profiles import apply_profile

        # Load context
        loader = ContextLoader(self._context_dir)
        context_files = loader.load()
        brief = collect_project_brief(context_files)

        # Git init
        git = GitManager(self._project_dir)
        if not await git.has_repo():
            await git.init()

        # Database
        engine = get_engine(self._settings, project_dir=str(self._project_dir))
        await init_db(engine)

        try:
            async with get_session(engine=engine) as session:
                llm = LLMClient(self._settings.llm)

                # Architecture checkpoint
                arch_callback = None
                if not self._no_checkpoints:
                    from adam.cli.checkpoints import review_architecture
                    arch_callback = review_architecture

                planner = PlanningOrchestrator(
                    llm, session,
                    project_root=str(self._project_dir),
                    on_architecture_checkpoint=arch_callback,
                )
                project_id = await planner.run(
                    brief, context_files,
                    asset_manifest=loader.assets,
                )

                # Scaffold check
                scaffold_ok = any(
                    (self._project_dir / f).exists()
                    for f in (
                        "package.json", "pyproject.toml",
                        "Cargo.toml", "go.mod",
                    )
                )

                # Save state
                state = ProjectState(
                    project_id=str(project_id),
                    phase="implementing",
                    title=brief.get("title", "Untitled"),
                    tech_stack=brief.get("tech_stack", {}),
                    root_path=str(self._project_dir),
                    scaffold_complete=scaffold_ok,
                )
                save_project(self._project_dir, state)

                # Dependencies
                dep_mgr = DependencyManager(self._project_dir)
                pm = dep_mgr.detect_package_manager(brief.get("tech_stack"))
                if pm and not await dep_mgr.check_installed():
                    await dep_mgr.install()

            return StageResult(
                stage=Stage.PLAN,
                success=True,
                details={
                    "project_id": str(project_id),
                    "title": brief.get("title", ""),
                },
            )
        finally:
            await engine.dispose()

    async def _run_construct(self) -> StageResult:
        """Run the construction stage: implementation + validation + repair."""
        from adam.db.session import get_engine, get_session, init_db
        from adam.llm.client import LLMClient
        from adam.orchestrator.engine import Orchestrator
        from adam.orchestrator.policies import ImplementationPolicy

        state = detect_project(self._project_dir)
        if state is None:
            return StageResult(
                stage=Stage.CONSTRUCT, success=False,
                error="No project state found — run plan stage first",
            )

        engine = get_engine(self._settings, project_dir=str(self._project_dir))
        await init_db(engine)

        try:
            async with get_session(engine=engine) as session:
                llm = LLMClient(self._settings.llm)

                policy = ImplementationPolicy(
                    max_repair_rounds=self._settings.orchestrator.max_repair_rounds,
                    acceptance_threshold=self._settings.orchestrator.acceptance_threshold,
                    run_soft_critics=self._settings.orchestrator.run_soft_critics,
                    visual_inspection=self._settings.orchestrator.visual_inspection,
                )
                orchestrator = Orchestrator(
                    llm=llm,
                    session=session,
                    project_root=str(self._project_dir),
                    policy=policy,
                )
                result = await orchestrator.run(uuid.UUID(state.project_id))

                if result.success:
                    update_phase(self._project_dir, "refining")
                else:
                    # Still advance to refine — it may be able to fix remaining issues
                    update_phase(self._project_dir, "refining")

                return StageResult(
                    stage=Stage.CONSTRUCT,
                    success=True,
                    details={
                        "files_processed": result.files_processed,
                        "files_accepted": result.files_accepted,
                        "tests_generated": result.tests_generated,
                        "construction_success": result.success,
                    },
                )
        finally:
            await engine.dispose()

    async def _run_refine(self) -> StageResult:
        """Run the refinement stage: observe → fix → verify → repeat."""
        from adam.llm.client import LLMClient
        from adam.refinement.refiner import Refiner, RefinementConfig
        from adam.store.store import ProjectStore

        state = detect_project(self._project_dir)
        if state is None:
            return StageResult(
                stage=Stage.REFINE, success=False,
                error="No project state found",
            )

        # Determine build/test commands from project state
        build_cmd, test_cmd, run_cmd = await self._detect_commands()

        llm = LLMClient(self._settings.llm)
        config = RefinementConfig(
            max_rounds=20,
            build_cmd=build_cmd,
            run_cmd=run_cmd,
            test_cmd=test_cmd,
        )

        refiner = Refiner(
            llm=llm,
            project_root=self._project_dir,
            config=config,
        )

        result = await refiner.refine()

        if result.final_health.value >= 6:  # FULLY_HEALTHY
            update_phase(self._project_dir, "complete")
        else:
            # Record where we are but don't block done
            update_phase(self._project_dir, "complete")

        return StageResult(
            stage=Stage.REFINE,
            success=True,
            details={
                "rounds": result.rounds_completed,
                "fixes_committed": result.fixes_committed,
                "fixes_reverted": result.fixes_reverted,
                "initial_health": result.initial_health.name,
                "final_health": result.final_health.name,
                "stopped_reason": result.stopped_reason,
                "improved": result.improved,
            },
        )

    async def _run_iterate(self) -> StageResult:
        """Run the iterate stage: detect changes, plan incremental work."""
        from adam.context.fingerprint import ContextFingerprinter
        from adam.context.loader import ContextLoader
        from adam.db.session import get_engine, get_session, init_db
        from adam.llm.client import LLMClient
        from adam.pipeline.iterate import IterateStage

        state = detect_project(self._project_dir)
        if state is None:
            return StageResult(
                stage=Stage.ITERATE, success=False,
                error="No project state found",
            )

        # Load current context files
        loader = ContextLoader(self._context_dir)
        current_files = loader.load()

        # Detect changes
        fingerprinter = ContextFingerprinter(self._project_dir)
        context_diff = fingerprinter.diff(current_files)

        # Get user instructions if provided (stored by CLI before calling)
        user_instructions = self._user_instructions or ""

        if not context_diff.has_changes and not user_instructions:
            return StageResult(
                stage=Stage.ITERATE, success=True,
                advance=False,
                details={"reason": "no changes detected"},
            )

        engine = get_engine(self._settings, project_dir=str(self._project_dir))
        await init_db(engine)

        try:
            async with get_session(engine=engine) as session:
                llm = LLMClient(self._settings.llm)
                iterate_stage = IterateStage(llm, self._project_dir)

                result = await iterate_stage.run(
                    session=session,
                    project_id=uuid.UUID(state.project_id),
                    context_diff=context_diff,
                    current_files=current_files,
                    user_instructions=user_instructions,
                )

                if not result.success:
                    return StageResult(
                        stage=Stage.ITERATE, success=False,
                        error=result.error,
                    )

                if not result.has_work:
                    return StageResult(
                        stage=Stage.ITERATE, success=True,
                        advance=False,
                        details={"reason": "no file changes needed"},
                    )

                return StageResult(
                    stage=Stage.ITERATE,
                    success=True,
                    details={
                        "files_pending": result.files_marked_pending,
                        "new_obligations": result.new_obligations,
                        "closed_obligations": result.closed_obligations,
                        "scope": (
                            result.spec_diff.estimated_scope
                            if result.spec_diff else "unknown"
                        ),
                    },
                )
        finally:
            await engine.dispose()

    async def _detect_commands(self) -> tuple[str, str, str]:
        """Detect build, test, and run commands from the project."""
        build_cmd = ""
        test_cmd = ""
        run_cmd = ""

        # Check package.json
        pkg_json = self._project_dir / "package.json"
        if pkg_json.is_file():
            import json
            try:
                pkg = json.loads(pkg_json.read_text(encoding="utf-8"))
                scripts = pkg.get("scripts", {})
                build_cmd = f"npm run {scripts and 'build' in scripts and 'build' or ''}" if scripts.get("build") else ""
                test_cmd = f"npm run {scripts and 'test' in scripts and 'test' or ''}" if scripts.get("test") else ""
                run_cmd = f"npm run {scripts and 'dev' in scripts and 'dev' or ''}" if scripts.get("dev") else ""
                # Simplify
                if scripts.get("build"):
                    build_cmd = "npm run build"
                if scripts.get("test"):
                    test_cmd = "npm test"
                if scripts.get("dev"):
                    run_cmd = "npm run dev"
            except (json.JSONDecodeError, KeyError):
                pass

        # Check pyproject.toml
        pyproject = self._project_dir / "pyproject.toml"
        if pyproject.is_file() and not build_cmd:
            # Python projects: pytest for tests
            test_cmd = test_cmd or "pytest"

        # Check Cargo.toml
        cargo = self._project_dir / "Cargo.toml"
        if cargo.is_file() and not build_cmd:
            build_cmd = "cargo build"
            test_cmd = "cargo test"

        # Check go.mod
        gomod = self._project_dir / "go.mod"
        if gomod.is_file() and not build_cmd:
            build_cmd = "go build ./..."
            test_cmd = "go test ./..."

        # Also try reading from project architecture (stored in DB)
        state = detect_project(self._project_dir)
        if state and not build_cmd:
            # Fall back to project.json tech_stack hints
            ts = state.tech_stack
            if isinstance(ts, dict):
                lang = str(ts.get("language", "")).lower()
                if "typescript" in lang or "javascript" in lang:
                    build_cmd = build_cmd or "npm run build"
                    test_cmd = test_cmd or "npm test"

        return build_cmd, test_cmd, run_cmd
