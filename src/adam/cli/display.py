"""Rich-based display utilities for the CLI.

Animated thinking spinners, progress dashboards, and result rendering.
Ported from Postwriter's display system with engineering-themed verbs
and the same concurrency-safe spinner pattern.
"""

from __future__ import annotations

import asyncio
import random
import time as _time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.progress import BarColumn, Progress, SpinnerColumn, TextColumn
from rich.table import Table
from rich.text import Text

from adam.orchestrator.engine import OrchestratorResult
from adam.orchestrator.file_loop import FileLoopResult

console = Console()

# Engineering thinking messages, rotated during long operations
_THINKING_VERBS = [
    "Arguing both sides of the interface",
    "Untangling the sugya",
    "Building a fence around the types",
    "Schlepping through the stack trace",
    "Studying the tractate on modules",
    "Kibbitzing with the linter",
    "Noshing while refactoring",
    "Asking the four questions",
    "Performing tikkun on the codebase",
    "Engaging in pilpul over the contract",
    "Davening for convergence",
    "Checking the mezuzah on every doorpost",
    "Kvetching at the compiler",
    "Adding another layer of commentary",
    "Consulting the Gemara of the docs",
    "Finding the pshat in the error message",
    "Kvelling over clean tests",
    "Making a siyum on this module",
    "Resolving a machloket between dependencies",
    "Learning the daf of the codebase",
]

_active_spinner_lock = asyncio.Lock()
_active_spinner_count = 0


@asynccontextmanager
async def thinking(label: str | None = None) -> AsyncGenerator[None, None]:
    """Show an animated thinking spinner with engineering status messages.

    Uses Rich's Status context manager for proper in-place animation.
    Only the first concurrent caller gets the spinner; others run silently
    to avoid Rich's one-live-display limitation.

    Usage:
        async with thinking("Designing architecture"):
            result = await slow_operation()
    """
    global _active_spinner_count

    async with _active_spinner_lock:
        _active_spinner_count += 1
        owns_spinner = _active_spinner_count == 1

    if not owns_spinner:
        try:
            yield
        finally:
            async with _active_spinner_lock:
                _active_spinner_count -= 1
        return

    verbs = list(_THINKING_VERBS)
    random.shuffle(verbs)
    initial = label or verbs[0]
    t0 = _time.monotonic()

    status = console.status(
        f"  {initial}...", spinner="dots", spinner_style="green"
    )
    status.start()

    stop_event = asyncio.Event()

    async def _rotate() -> None:
        idx = 0
        while not stop_event.is_set():
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=4.0)
                break
            except TimeoutError:
                if not label:
                    idx = (idx + 1) % len(verbs)
                elapsed = int(_time.monotonic() - t0)
                verb = label or verbs[idx]
                if elapsed >= 10:
                    status.update(f"  {verb}... ({elapsed}s)")
                else:
                    status.update(f"  {verb}...")

    rotate_task = asyncio.create_task(_rotate())

    try:
        yield
    finally:
        stop_event.set()
        await rotate_task
        status.stop()
        async with _active_spinner_lock:
            _active_spinner_count -= 1


# ---------------------------------------------------------------------------
# Static display helpers
# ---------------------------------------------------------------------------

def banner() -> None:
    console.print(
        Panel(
            Text("ADAM", style="bold green", justify="center"),
            subtitle="Orchestrated Software Engineering",
            border_style="green",
        )
    )


def show_phase(phase: str, message: str = "") -> None:
    """Display a phase transition."""
    console.print(Panel(
        f"[bold]{phase}[/bold]" + (f"\n{message}" if message else ""),
        style="green",
    ))


def section(title: str) -> None:
    console.print(f"\n[bold green]{title}[/bold green]")
    console.print("[dim]" + "\u2500" * 60 + "[/dim]")


def show_info(message: str) -> None:
    console.print(f"[dim]  {message}[/dim]")


def show_success(message: str) -> None:
    console.print(f"[green]  {message}[/green]")


def show_warning(message: str) -> None:
    console.print(f"[yellow]  {message}[/yellow]")


def show_error(message: str) -> None:
    console.print(f"[bold red]Error:[/bold red] {message}")


# ---------------------------------------------------------------------------
# File result display
# ---------------------------------------------------------------------------

def show_file_result(result: FileLoopResult) -> None:
    """Display the result of processing a single file."""
    status = "[green]\u2713[/green]" if result.accepted else "[red]\u2717[/red]"

    score = ""
    if result.scores:
        score = f" [dim]({result.scores.composite:.2f})[/dim]"

    repairs = ""
    if result.repair_rounds:
        repairs = f" [dim]\u2192 {result.repair_rounds} repair(s)[/dim]"

    test_info = ""
    if result.test_path:
        test_info = f" [dim]+ {result.test_path}[/dim]"

    console.print(
        f"  {status} {result.file_path}{score}{repairs}{test_info}"
    )

    for w in result.warnings:
        console.print(f"    [yellow]\u26a0 {w}[/yellow]")

    if result.error:
        console.print(f"    [red]{result.error}[/red]")


# ---------------------------------------------------------------------------
# Orchestrator result dashboard
# ---------------------------------------------------------------------------

def show_orchestrator_result(result: OrchestratorResult) -> None:
    """Display the final orchestration summary dashboard."""
    section("Results")

    # Main metrics table
    table = Table(show_header=False, box=None, padding=(0, 2))
    table.add_column("Metric", style="bold")
    table.add_column("Value")

    table.add_row("Files processed", str(result.files_processed))
    table.add_row("Files accepted", str(result.files_accepted))
    if result.files_processed > 0:
        rate = result.files_accepted / result.files_processed * 100
        table.add_row("Acceptance rate", f"{rate:.0f}%")
    table.add_row("Repair rounds", str(result.total_repair_rounds))
    if result.total_passes > 1:
        table.add_row("Passes", str(result.total_passes))

    if result.success:
        table.add_row("Status", Text("COMPLETE", style="bold green"))
    else:
        table.add_row("Status", Text("INCOMPLETE", style="bold yellow"))

    console.print(table)

    # Obligation status
    ob = result.obligation_status
    if ob and ob.get("total", 0) > 0:
        console.print(
            f"\n  Obligations: {ob['total']} total, "
            f"{ob.get('open', 0)} open, "
            f"completion: {ob.get('ratio', 0):.0%}"
        )

    # Stop conditions
    if result.stop_conditions:
        console.print()
        for sc in result.stop_conditions:
            icon = "[green]\u2713[/green]" if sc["met"] else "[red]\u2717[/red]"
            console.print(f"  {icon} {sc['name']}: {sc['detail']}")

    # Integration issues
    if result.integration_issues:
        console.print(
            f"\n  [yellow]{len(result.integration_issues)} "
            f"integration issue(s):[/yellow]"
        )
        for issue in result.integration_issues[:5]:
            console.print(
                f"    [{issue.get('severity', '?')}] "
                f"{issue.get('description', '')}"
            )

    # Warnings
    if result.warnings:
        console.print(
            f"\n  [yellow]{len(result.warnings)} warning(s)[/yellow]"
        )
        for w in result.warnings[:5]:
            console.print(f"    - {w}")
        if len(result.warnings) > 5:
            remaining = len(result.warnings) - 5
            console.print(f"    ... and {remaining} more")


# ---------------------------------------------------------------------------
# Token usage display
# ---------------------------------------------------------------------------

def show_token_usage(usage: dict[str, Any]) -> None:
    """Display token usage by tier."""
    table = Table(title="Token Usage", show_lines=False)
    table.add_column("Tier", style="bold")
    table.add_column("Calls", justify="right")
    table.add_column("Tokens", justify="right")
    table.add_column("Remaining", justify="right")

    for tier, data in usage.items():
        remaining = (
            f"{data['remaining']:,}"
            if data.get("remaining") is not None
            else "unlimited"
        )
        total = data.get("input_tokens", 0) + data.get("output_tokens", 0)
        table.add_row(
            tier,
            str(data.get("calls", 0)),
            f"{total:,}",
            remaining,
        )
    console.print(table)


def show_refinement_result(result: Any) -> None:
    """Display the outcome of the refinement loop."""
    from adam.refinement.refiner import RefinementResult

    if not isinstance(result, RefinementResult):
        return

    console.print()

    health_colors = {
        "DOES_NOT_BUILD": "red",
        "BUILDS_BUT_CRASHES": "red",
        "RUNS_BUT_BROKEN": "yellow",
        "RUNS_WITH_ISSUES": "yellow",
        "RUNS_CLEAN": "green",
        "TESTS_FAILING": "yellow",
        "FULLY_HEALTHY": "bold green",
    }

    initial_color = health_colors.get(result.initial_health.name, "white")
    final_color = health_colors.get(result.final_health.name, "white")

    table = Table(title="Refinement", show_lines=False)
    table.add_column("Metric", style="bold")
    table.add_column("Value", justify="right")

    table.add_row("Rounds", str(result.rounds_completed))
    table.add_row("Fixes committed", str(result.fixes_committed))
    table.add_row("Fixes reverted", str(result.fixes_reverted))
    table.add_row(
        "Health",
        f"[{initial_color}]{result.initial_health.name}[/] → "
        f"[{final_color}]{result.final_health.name}[/]",
    )
    table.add_row(
        "Issues",
        f"{result.initial_issue_count} → {result.final_issue_count}",
    )
    table.add_row("Stopped", result.stopped_reason)

    console.print(table)

    if result.issues_fixed:
        console.print("\n[dim]Fixed:[/dim]")
        for fix in result.issues_fixed[:10]:
            console.print(f"  [green]✓[/green] {fix}")
        if len(result.issues_fixed) > 10:
            console.print(
                f"  [dim]... and {len(result.issues_fixed) - 10} more[/dim]"
            )


# ---------------------------------------------------------------------------
# Progress bar factory
# ---------------------------------------------------------------------------

def create_progress() -> Progress:
    """Create a Rich progress bar for file implementation."""
    return Progress(
        SpinnerColumn(spinner_name="dots", style="green"),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(bar_width=30),
        TextColumn("[progress.percentage]{task.percentage:>3.0f}%"),
        TextColumn("[dim]{task.completed}/{task.total}[/dim]"),
        console=console,
    )
