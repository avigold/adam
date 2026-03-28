"""CLI entry point — detect/resume/new project flow.

Equivalent to Postwriter's cli/app.py. The user runs `adam` in any
directory and it either resumes existing work or starts a new project.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from pathlib import Path

import click
from rich.console import Console
from rich.prompt import Confirm, Prompt

from adam.cli.bootstrap import collect_project_brief
from adam.cli.display import (
    banner,
    show_file_result,
    show_info,
    show_orchestrator_result,
    show_phase,
    show_refinement_result,
    show_token_usage,
)
from adam.config import Settings
from adam.context.loader import ContextLoader
from adam.db.session import get_engine, get_session, init_db
from adam.execution.dependencies import DependencyManager
from adam.git.manager import GitManager
from adam.llm.client import LLMClient
from adam.logging_config import setup_logging
from adam.orchestrator.engine import Orchestrator
from adam.orchestrator.planner import PlanningOrchestrator
from adam.orchestrator.policies import ImplementationPolicy
from adam.profiles import PROFILES, apply_profile
from adam.project import ProjectState, detect_project, save_project, update_phase

console = Console()
logger = logging.getLogger(__name__)


def _on_file_start(
    file_path: str, current: int, total: int, is_revision: bool,
) -> None:
    """Callback: display a brief status when a file starts processing."""
    action = "Revising" if is_revision else "Implementing"
    console.print(
        f"  [dim][{current}/{total}] {action}: {file_path}[/dim]"
    )


def _on_file(result: object, current: int, total: int) -> None:
    """Callback: display each file result as the orchestrator completes it."""
    from adam.orchestrator.file_loop import FileLoopResult

    if isinstance(result, FileLoopResult):
        console.print(f"  [dim][{current}/{total}][/dim]", end=" ")
        show_file_result(result)


async def _check_scaffold_build(
    session: object,
    project_id: object,
    project_dir: Path,
) -> None:
    """Verify the scaffolded project builds before implementation."""
    from adam.execution.runner import ShellRunner
    from adam.store.store import ProjectStore

    store = ProjectStore(session)  # type: ignore[arg-type]
    project = await store.get_project(project_id)  # type: ignore[arg-type]
    if project is None:
        return

    build_sys = project.architecture.get("build_system", {})
    build_cmd = build_sys.get("build", "")
    if not build_cmd:
        return

    show_info(f"Verifying scaffold builds: {build_cmd}")
    runner = ShellRunner()
    result = await runner.run_build(build_cmd, cwd=str(project_dir))

    if result.success:
        show_info("Scaffold builds successfully")
    else:
        show_info(
            "[yellow]Scaffold build check failed — "
            "config issues will be addressed during implementation[/yellow]"
        )


@click.group(invoke_without_command=True)
@click.option("--project-dir", type=click.Path(exists=True), default=".")
@click.option("--context-dir", type=click.Path(exists=False), default=None)
@click.option(
    "--profile",
    type=click.Choice(list(PROFILES.keys())),
    default=None,
    help="Generation profile (fast_draft, standard, high_quality, budget_conscious)",
)
@click.option("--debug", is_flag=True, default=False)
@click.option(
    "--no-checkpoints", is_flag=True, default=False,
    help="Skip human approval checkpoints (architecture review etc.)",
)
@click.pass_context
def cli(
    ctx: click.Context,
    project_dir: str = ".",
    context_dir: str | None = None,
    profile: str | None = None,
    debug: bool = False,
    no_checkpoints: bool = False,
) -> None:
    """Adam: Orchestrated long-form software engineering."""
    # Always log to file for diagnosis
    log_dir = Path(project_dir) / ".adam"
    log_dir.mkdir(parents=True, exist_ok=True)
    setup_logging(debug=debug, log_file=log_dir / "adam.log")

    # Store shared options for subcommands
    ctx.ensure_object(dict)
    ctx.obj["project_dir"] = Path(project_dir)
    ctx.obj["context_dir"] = context_dir
    ctx.obj["profile"] = profile
    ctx.obj["debug"] = debug
    ctx.obj["no_checkpoints"] = no_checkpoints

    # If no subcommand, run the default flow
    if ctx.invoked_subcommand is None:
        asyncio.run(_run(
            Path(project_dir), context_dir, profile, debug, no_checkpoints,
        ))


@cli.command()
@click.option("--build-cmd", default="", help="Build command (auto-detected if not set)")
@click.option("--test-cmd", default="", help="Test command (auto-detected if not set)")
@click.option("--run-cmd", default="", help="Run/start command (auto-detected if not set)")
@click.option("--max-rounds", default=15, help="Maximum fix attempts")
@click.pass_context
def fix(
    ctx: click.Context,
    build_cmd: str,
    test_cmd: str,
    run_cmd: str,
    max_rounds: int,
) -> None:
    """Fix a project that doesn't build, crashes, or has failing tests.

    Runs the observe-fix-verify loop: check what's broken, fix the top
    issue, verify the fix didn't make things worse, repeat.

    No spec analysis, no planning — just targeted repair.
    """
    project_dir: Path = ctx.obj["project_dir"]
    profile: str | None = ctx.obj["profile"]

    asyncio.run(_run_fix(
        project_dir, profile, build_cmd, test_cmd, run_cmd, max_rounds,
    ))


@cli.command()
@click.argument("instructions", nargs=-1)
@click.pass_context
def iterate(ctx: click.Context, instructions: tuple[str, ...]) -> None:
    """Iterate on an existing project — update spec, add features, refine.

    If context files have changed, Adam analyses the delta and plans
    incremental work. If no changes are detected, asks what you'd like
    to change.

    You can pass instructions directly:
        adam iterate add user authentication with JWT
    """
    project_dir: Path = ctx.obj["project_dir"]
    profile: str | None = ctx.obj["profile"]
    user_text = " ".join(instructions) if instructions else ""

    asyncio.run(_run_iterate(
        project_dir, ctx.obj["context_dir"], profile, user_text,
    ))


def main() -> None:
    """Entry point for the CLI."""
    cli()


async def _run(
    project_dir: Path,
    context_dir: str | None,
    profile_name: str | None,
    debug: bool,
    no_checkpoints: bool = False,
) -> None:
    """Main async entry point."""
    banner()

    state = detect_project(project_dir)

    if state is not None:
        await _handle_existing(project_dir, state, profile_name)
    else:
        await _handle_new(project_dir, context_dir, profile_name, no_checkpoints)


async def _handle_existing(
    project_dir: Path,
    state: ProjectState,
    profile_name: str | None,
) -> None:
    """Handle an existing project."""
    console.print(f"Found project: [bold]{state.title}[/bold] ({state.phase})")

    if state.phase == "complete":
        # Check for context file changes
        from adam.context.fingerprint import ContextFingerprinter
        fingerprinter = ContextFingerprinter(project_dir)
        if fingerprinter.has_stored_state():
            loader = ContextLoader(project_dir / "context")
            current_files = loader.load()
            diff = fingerprinter.diff(current_files)
            if diff.has_changes:
                console.print(
                    f"\n[bold]Context changes detected:[/bold] "
                    f"{diff.summary()}"
                )
                choice = Prompt.ask(
                    "Would you like to iterate on this project?",
                    choices=["iterate", "new", "quit"],
                    default="iterate",
                )
                if choice == "iterate":
                    await _run_iterate(
                        project_dir, None, profile_name, "",
                    )
                    return
                elif choice == "new":
                    if Confirm.ask("Start a new project?"):
                        await _handle_new(project_dir, None, profile_name)
                    return
                else:
                    return

        choice = Prompt.ask(
            "Project is complete. What would you like to do?",
            choices=["iterate", "new", "quit"],
            default="quit",
        )
        if choice == "iterate":
            await _run_iterate(project_dir, None, profile_name, "")
            return
    else:
        choice = Prompt.ask(
            "What would you like to do?",
            choices=["resume", "new", "quit"],
            default="resume",
        )

    if choice == "quit":
        return
    elif choice == "new":
        if Confirm.ask("Start a new project? This won't delete the existing one."):
            await _handle_new(project_dir, None, profile_name)
        return

    # Resume or revise
    show_phase("Resuming", f"Phase: {state.phase}")
    settings = Settings()

    if profile_name:
        apply_profile(profile_name, settings.orchestrator, settings.llm)

    engine = get_engine(settings, project_dir=str(project_dir))
    await init_db(engine)

    async with get_session(engine=engine) as session:
        llm = LLMClient(settings.llm)

        if state.phase in ("planning", "bootstrapping"):
            show_info("Advancing to implementation...")
            update_phase(project_dir, "implementing")
            state.phase = "implementing"

        # Re-scaffold if it failed on the previous run
        if not state.scaffold_complete:
            show_info("Scaffold incomplete — re-running scaffolder...")
            from adam.orchestrator.planner import PlanningOrchestrator
            planner = PlanningOrchestrator(
                llm, session, project_root=str(project_dir),
            )
            from adam.store.store import ProjectStore  # noqa: E402
            project = await ProjectStore(session).get_project(
                uuid.UUID(state.project_id)
            )
            if project:
                from adam.agents.architect import ArchitectureResponse
                arch_data = ArchitectureResponse(
                    tech_stack=project.tech_stack,
                    modules=[],
                    conventions=project.conventions,
                    build_system=project.architecture.get("build_system", {}),
                    architecture_decisions=project.architecture.get("decisions", []),
                )
                scaffold_ok = await planner._scaffold(
                    uuid.UUID(state.project_id), arch_data,
                )
                if scaffold_ok:
                    # Copy assets if context has them
                    ctx_dir = project_dir / "context"
                    if ctx_dir.is_dir():
                        from adam.context.loader import ContextLoader
                        loader = ContextLoader(ctx_dir)
                        loader.load()
                        if loader.assets.assets:
                            planner._copy_assets(loader.assets)

                    # Install dependencies
                    dep_mgr = DependencyManager(project_dir)
                    pm = dep_mgr.detect_package_manager()
                    if pm and not await dep_mgr.check_installed():
                        show_info(f"Installing dependencies with {pm.name}...")
                        await dep_mgr.install()

                    state.scaffold_complete = True
                    save_project(project_dir, state)
                    show_info("Scaffold complete")
                else:
                    show_info("[yellow]Scaffold still failed[/yellow]")

        if state.phase in ("implementing", "testing", "revise"):
            show_phase("Implementation")
            policy = ImplementationPolicy(
                max_repair_rounds=settings.orchestrator.max_repair_rounds,
                acceptance_threshold=settings.orchestrator.acceptance_threshold,
                run_soft_critics=settings.orchestrator.run_soft_critics,
                visual_inspection=settings.orchestrator.visual_inspection,
            )
            orchestrator = Orchestrator(
                llm=llm,
                session=session,
                project_root=str(project_dir),
                policy=policy,
                on_file_complete=_on_file,
                on_file_start=_on_file_start,
            )
            result = await orchestrator.run(uuid.UUID(state.project_id))
            show_orchestrator_result(result)

            # Refinement — observe, fix, verify
            refine_result = await _run_refine(
                llm, project_dir, settings,
            )
            if refine_result:
                show_refinement_result(refine_result)

            if result.success:
                update_phase(project_dir, "complete")
                _save_context_fingerprints(project_dir)
            else:
                update_phase(project_dir, "testing")

    await engine.dispose()


async def _handle_new(
    project_dir: Path,
    context_dir: str | None,
    profile_name: str | None,
    no_checkpoints: bool = False,
) -> None:
    """Handle a new project."""
    show_phase("New Project")

    # Step 1: Load context files
    ctx_dir = Path(context_dir) if context_dir else project_dir / "context"
    loader = ContextLoader(ctx_dir)
    context_files = loader.load()

    if context_files:
        show_info(f"Loaded {len(context_files)} context file(s) from {ctx_dir}")
        for cf in context_files:
            show_info(f"  - {cf.name} ({cf.context_type.value})")
    else:
        show_info("No context files found. You can add them to context/ later.")

    # Step 2: Collect project brief (spec-aware — skips what's answered)
    brief = collect_project_brief(context_files)

    # Step 3: Apply profile
    settings = Settings()
    if profile_name:
        apply_profile(profile_name, settings.orchestrator, settings.llm)
        show_info(f"Using profile: {profile_name}")

    # Step 4: Initialize git
    git = GitManager(project_dir)
    if not await git.has_repo():
        show_info("Initializing git repository...")
        await git.init()

    # Step 5: Initialize database and run planning
    engine = get_engine(settings, project_dir=str(project_dir))
    await init_db(engine)

    show_phase(
        "Architecture Design",
        "Using Claude Opus for architectural reasoning...",
    )

    async with get_session(engine=engine) as session:
        llm = LLMClient(settings.llm)

        # Planning phase (with optional human checkpoint)
        arch_callback = None
        if not no_checkpoints:
            from adam.cli.checkpoints import review_architecture
            arch_callback = review_architecture

        planner = PlanningOrchestrator(
            llm, session,
            project_root=str(project_dir),
            on_architecture_checkpoint=arch_callback,
        )
        project_id = await planner.run(
            brief, context_files,
            asset_manifest=loader.assets,
        )

        # Check if scaffold produced config files
        scaffold_ok = any(
            (project_dir / f).exists()
            for f in ("package.json", "pyproject.toml", "Cargo.toml", "go.mod")
        )

        # Save project state
        state = ProjectState(
            project_id=str(project_id),
            phase="implementing",
            title=brief.get("title", "Untitled"),
            tech_stack=brief.get("tech_stack", {}),
            root_path=str(project_dir),
            scaffold_complete=scaffold_ok,
        )
        save_project(project_dir, state)

        # Step 6: Install dependencies
        dep_mgr = DependencyManager(project_dir)
        pm = dep_mgr.detect_package_manager(brief.get("tech_stack"))
        if pm and not await dep_mgr.check_installed():
            show_info(f"Installing dependencies with {pm.name}...")
            install_result = await dep_mgr.install()
            if not install_result.success:
                show_info(
                    f"[yellow]Dependency install had issues: "
                    f"{install_result.stderr[:200]}[/yellow]"
                )

        # Step 7: Verify scaffold builds
        await _check_scaffold_build(session, project_id, project_dir)

        # Step 8: Implementation phase
        show_phase(
            "Implementation",
            "Writing code, running tests, repairing failures...",
        )

        policy = ImplementationPolicy(
            max_repair_rounds=settings.orchestrator.max_repair_rounds,
            acceptance_threshold=settings.orchestrator.acceptance_threshold,
            run_soft_critics=settings.orchestrator.run_soft_critics,
            visual_inspection=(
                brief.get("has_ui", False)
                and settings.orchestrator.visual_inspection
            ),
        )
        orchestrator = Orchestrator(
            llm=llm,
            session=session,
            project_root=str(project_dir),
            policy=policy,
            on_file_complete=_on_file,
        )
        result = await orchestrator.run(project_id)
        show_orchestrator_result(result)

        # Step 9: Refinement — observe, fix, verify
        refine_result = await _run_refine(
            llm, project_dir, settings,
        )
        if refine_result:
            show_refinement_result(refine_result)

        if result.success:
            update_phase(project_dir, "complete")
            console.print("\n[bold green]Project complete![/bold green]")
        else:
            update_phase(project_dir, "testing")
            console.print(
                "\n[bold yellow]Some files need attention.[/bold yellow]"
            )

        # Save context fingerprints so future runs detect changes
        _save_context_fingerprints(project_dir, context_dir=None)

        # Token usage summary
        show_token_usage(llm.budget.summary())
        show_info(f"Full log: {project_dir / '.adam' / 'adam.log'}")

    await engine.dispose()


async def _run_fix(
    project_dir: Path,
    profile_name: str | None,
    build_cmd: str,
    test_cmd: str,
    run_cmd: str,
    max_rounds: int,
) -> None:
    """Run the fix loop — observe, fix, verify, repeat."""
    from adam.refinement.refiner import Refiner, RefinementConfig

    banner()

    # Auto-detect commands if not provided
    if not build_cmd and not test_cmd:
        detected_build, detected_test, detected_run = _detect_project_commands(
            project_dir,
        )
        build_cmd = build_cmd or detected_build
        test_cmd = test_cmd or detected_test
        run_cmd = run_cmd or detected_run

    if not build_cmd and not test_cmd:
        console.print(
            "[red]No build or test commands detected. "
            "Use --build-cmd or --test-cmd.[/red]"
        )
        return

    show_phase("Fix", "Observing → fixing → verifying...")
    if build_cmd:
        show_info(f"Build: {build_cmd}")
    if test_cmd:
        show_info(f"Test: {test_cmd}")
    if run_cmd:
        show_info(f"Run: {run_cmd}")

    settings = Settings()
    if profile_name:
        apply_profile(profile_name, settings.orchestrator, settings.llm)

    llm = LLMClient(settings.llm)

    config = RefinementConfig(
        max_rounds=max_rounds,
        build_cmd=build_cmd,
        run_cmd=run_cmd,
        test_cmd=test_cmd,
    )

    def on_round_start(
        round_num: int, observation: object, issue: object,
    ) -> None:
        from adam.refinement.observe import Issue, Observation
        if isinstance(observation, Observation) and isinstance(issue, Issue):
            console.print(
                f"\n  [bold]Round {round_num}[/bold] "
                f"[dim]({observation.health.name}, "
                f"{observation.issue_count} issues)[/dim]"
            )
            console.print(f"  Fixing: {issue.summary[:80]}")
            if issue.file_path:
                console.print(f"  File: [cyan]{issue.file_path}[/cyan]")

    def on_round_end(
        round_num: int, improved: bool, reverted: bool,
    ) -> None:
        if reverted:
            console.print(
                f"  [yellow]Reverted[/yellow] — fix made things worse"
            )
        elif improved:
            console.print(f"  [green]Committed[/green]")

    refiner = Refiner(
        llm=llm,
        project_root=project_dir,
        config=config,
        on_round_start=on_round_start,
        on_round_end=on_round_end,
    )

    result = await refiner.refine()
    show_refinement_result(result)

    if result.final_health.name == "FULLY_HEALTHY":
        console.print("\n[bold green]Project is healthy![/bold green]")
    else:
        console.print(
            f"\n[bold yellow]Health: {result.final_health.name} "
            f"({result.final_issue_count} issues remaining)[/bold yellow]"
        )

    show_token_usage(llm.budget.summary())


async def _run_refine(
    llm: LLMClient,
    project_dir: Path,
    settings: Settings,
) -> object | None:
    """Run the refinement loop after construction.

    Detects build/test commands from the project, then runs the
    observe → fix → verify cycle until healthy or budget exhausted.
    """
    from adam.refinement.refiner import Refiner, RefinementConfig

    show_phase(
        "Refinement",
        "Observing output, fixing issues, verifying improvements...",
    )

    # Detect build/test commands
    build_cmd, test_cmd, run_cmd = _detect_project_commands(project_dir)

    if not build_cmd and not test_cmd:
        show_info(
            "[dim]No build or test commands detected — "
            "skipping refinement[/dim]"
        )
        return None

    if build_cmd:
        show_info(f"Build: {build_cmd}")
    if test_cmd:
        show_info(f"Test: {test_cmd}")

    config = RefinementConfig(
        max_rounds=settings.orchestrator.max_repair_rounds * 3,
        build_cmd=build_cmd,
        run_cmd=run_cmd,
        test_cmd=test_cmd,
    )

    def on_round_start(
        round_num: int, observation: object, issue: object,
    ) -> None:
        from adam.refinement.observe import Issue, Observation
        if isinstance(observation, Observation) and isinstance(issue, Issue):
            console.print(
                f"  [dim]Round {round_num}[/dim] "
                f"[bold]{observation.health.name}[/bold] "
                f"({observation.issue_count} issues) — "
                f"fixing: {issue.summary[:60]}"
            )

    def on_round_end(
        round_num: int, improved: bool, reverted: bool,
    ) -> None:
        if reverted:
            console.print(
                f"  [dim]Round {round_num}[/dim] "
                f"[yellow]reverted[/yellow] (made things worse)"
            )
        elif improved:
            console.print(
                f"  [dim]Round {round_num}[/dim] "
                f"[green]committed[/green]"
            )

    refiner = Refiner(
        llm=llm,
        project_root=project_dir,
        config=config,
        on_round_start=on_round_start,
        on_round_end=on_round_end,
    )

    result = await refiner.refine()
    return result


def _detect_project_commands(project_dir: Path) -> tuple[str, str, str]:
    """Detect build, test, and run commands from project files."""
    import json

    build_cmd = ""
    test_cmd = ""
    run_cmd = ""

    # package.json
    pkg_json = project_dir / "package.json"
    if pkg_json.is_file():
        try:
            pkg = json.loads(pkg_json.read_text(encoding="utf-8"))
            scripts = pkg.get("scripts", {})
            if scripts.get("build"):
                build_cmd = "npm run build"
            if scripts.get("test"):
                test_cmd = "npm test"
            if scripts.get("dev"):
                run_cmd = "npm run dev"
        except (json.JSONDecodeError, KeyError):
            pass

    # pyproject.toml
    pyproject = project_dir / "pyproject.toml"
    if pyproject.is_file() and not test_cmd:
        test_cmd = "pytest"

    # Cargo.toml
    cargo = project_dir / "Cargo.toml"
    if cargo.is_file() and not build_cmd:
        build_cmd = "cargo build"
        test_cmd = "cargo test"

    # go.mod
    gomod = project_dir / "go.mod"
    if gomod.is_file() and not build_cmd:
        build_cmd = "go build ./..."
        test_cmd = "go test ./..."

    return build_cmd, test_cmd, run_cmd


def _save_context_fingerprints(
    project_dir: Path,
    context_dir: Path | None = None,
) -> None:
    """Save context file fingerprints after a successful run."""
    from adam.context.fingerprint import ContextFingerprinter

    ctx_dir = context_dir or project_dir / "context"
    if not ctx_dir.is_dir():
        return

    loader = ContextLoader(ctx_dir)
    files = loader.load()
    if files:
        fp = ContextFingerprinter(project_dir)
        fp.save(files)
        fp.save_content_snapshot(files)


async def _run_iterate(
    project_dir: Path,
    context_dir: str | None,
    profile_name: str | None,
    user_instructions: str,
) -> None:
    """Run the iterate flow — incremental development on existing projects."""
    from adam.context.fingerprint import ContextFingerprinter
    from adam.pipeline.iterate import IterateStage

    banner()

    state = detect_project(project_dir)
    if state is None:
        console.print(
            "[red]No project found. Run `adam` first to create one.[/red]"
        )
        return

    console.print(
        f"Project: [bold]{state.title}[/bold] ({state.phase})"
    )

    # Load context and detect changes
    ctx_dir = Path(context_dir) if context_dir else project_dir / "context"
    loader = ContextLoader(ctx_dir)
    current_files = loader.load()

    fingerprinter = ContextFingerprinter(project_dir)
    context_diff = fingerprinter.diff(current_files)

    if context_diff.has_changes:
        show_phase("Context Changes Detected")
        show_info(context_diff.summary())
    elif not user_instructions:
        # No context changes and no CLI instructions — ask interactively
        show_phase("Iterate")
        show_info(
            "No context file changes detected. "
            "What would you like to change?"
        )
        user_instructions = Prompt.ask(
            "\n[bold]Describe what you'd like to add, change, or fix[/bold]"
        )
        if not user_instructions.strip():
            show_info("Nothing to do.")
            return

    # Run the iterate stage
    settings = Settings()
    if profile_name:
        from adam.profiles import apply_profile
        apply_profile(profile_name, settings.orchestrator, settings.llm)

    engine = get_engine(settings, project_dir=str(project_dir))
    await init_db(engine)

    async with get_session(engine=engine) as session:
        llm = LLMClient(settings.llm)

        iterate_stage = IterateStage(llm, project_dir)
        result = await iterate_stage.run(
            session=session,
            project_id=uuid.UUID(state.project_id),
            context_diff=context_diff,
            current_files=current_files,
            user_instructions=user_instructions,
        )

        if not result.success:
            console.print(f"[red]Iterate failed: {result.error}[/red]")
            await engine.dispose()
            return

        if not result.has_work:
            show_info("Analysis complete — no file changes needed.")
            await engine.dispose()
            return

        # Show the plan
        show_phase("Change Plan")
        if result.spec_diff:
            show_info(f"Scope: {result.spec_diff.estimated_scope}")
            show_info(f"Feature changes: {len(result.spec_diff.feature_changes)}")
            if result.spec_diff.migration_notes:
                show_info(f"Notes: {result.spec_diff.migration_notes}")

        if result.change_plan:
            if result.change_plan.files_to_create:
                show_info(
                    f"Files to create: "
                    f"{len(result.change_plan.files_to_create)}"
                )
                for pf in result.change_plan.files_to_create:
                    show_info(f"  + {pf.path}")
            if result.change_plan.files_to_modify:
                show_info(
                    f"Files to modify: "
                    f"{len(result.change_plan.files_to_modify)}"
                )
                for pf in result.change_plan.files_to_modify:
                    show_info(f"  ~ {pf.path}: {pf.purpose}")
            if result.change_plan.files_to_delete:
                show_info(
                    f"Files to delete: "
                    f"{len(result.change_plan.files_to_delete)}"
                )

        show_info(
            f"Obligations: {result.new_obligations} new, "
            f"{result.closed_obligations} closed"
        )
        show_info(f"Files marked pending: {len(result.files_marked_pending)}")

        # Now run construction on the pending files
        show_phase(
            "Implementation",
            "Writing code, running tests, repairing failures...",
        )

        policy = ImplementationPolicy(
            max_repair_rounds=settings.orchestrator.max_repair_rounds,
            acceptance_threshold=settings.orchestrator.acceptance_threshold,
            run_soft_critics=settings.orchestrator.run_soft_critics,
            visual_inspection=settings.orchestrator.visual_inspection,
        )
        orchestrator = Orchestrator(
            llm=llm,
            session=session,
            project_root=str(project_dir),
            policy=policy,
            on_file_complete=_on_file,
        )
        orch_result = await orchestrator.run(uuid.UUID(state.project_id))
        show_orchestrator_result(orch_result)

        # Refinement
        refine_result = await _run_refine(llm, project_dir, settings)
        if refine_result:
            show_refinement_result(refine_result)

        if orch_result.success:
            update_phase(project_dir, "complete")
            console.print("\n[bold green]Iteration complete![/bold green]")
        else:
            update_phase(project_dir, "testing")
            console.print(
                "\n[bold yellow]Some files need attention.[/bold yellow]"
            )

        # Save updated fingerprints
        _save_context_fingerprints(project_dir, ctx_dir)

        show_token_usage(llm.budget.summary())
        show_info(f"Full log: {project_dir / '.adam' / 'adam.log'}")

    await engine.dispose()
