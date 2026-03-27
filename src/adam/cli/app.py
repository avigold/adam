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


@click.command()
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
def cli(
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
    asyncio.run(_run(
        Path(project_dir), context_dir, profile, debug, no_checkpoints,
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
        choice = Prompt.ask(
            "Project is complete. What would you like to do?",
            choices=["revise", "new", "quit"],
            default="quit",
        )
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
            )
            result = await orchestrator.run(uuid.UUID(state.project_id))
            show_orchestrator_result(result)

            if result.success:
                update_phase(project_dir, "complete")
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

        if result.success:
            update_phase(project_dir, "complete")
            console.print("\n[bold green]Project complete![/bold green]")
        else:
            update_phase(project_dir, "testing")
            console.print(
                "\n[bold yellow]Some files need attention.[/bold yellow]"
            )

        # Token usage summary
        show_token_usage(llm.budget.summary())
        show_info(f"Full log: {project_dir / '.adam' / 'adam.log'}")

    await engine.dispose()
