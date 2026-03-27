"""Human checkpoint interactions — pause for approval and refinement.

Shows the user what the system has decided and lets them approve,
tweak, or skip. The architecture checkpoint is the most important one:
a bad architecture cascades through every downstream agent.
"""

from __future__ import annotations

from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table

console = Console()

# Responses that mean "approved, proceed"
_APPROVE_RESPONSES = {
    "", "y", "yes", "ok", "go", "go for it", "looks good",
    "lgtm", "ship it", "proceed", "approve", "approved",
}


def review_architecture(
    arch_data: dict[str, Any],
    project_title: str = "",
) -> str | None:
    """Show the architecture to the user and collect feedback.

    Returns:
        None if approved (proceed as-is).
        A string with the user's tweak/feedback if they want changes.
    """
    console.print()
    console.print(Panel(
        f"[bold]{project_title}[/bold]" if project_title else "[bold]Architecture Review[/bold]",
        title="Architecture Checkpoint",
        border_style="green",
    ))

    # Tech stack
    tech = arch_data.get("tech_stack", {})
    if tech:
        console.print("\n[bold green]Tech Stack[/bold green]")
        for k, v in tech.items():
            console.print(f"  [bold]{k}:[/bold] {v}")

    # Modules
    modules = arch_data.get("modules", [])
    if modules:
        console.print("\n[bold green]Modules[/bold green]")
        mod_table = Table(show_header=True, box=None, padding=(0, 2))
        mod_table.add_column("#", style="dim", justify="right")
        mod_table.add_column("Module", style="bold")
        mod_table.add_column("Purpose")
        mod_table.add_column("Dependencies", style="dim")

        for i, m in enumerate(modules, 1):
            deps = ", ".join(m.get("dependencies", [])) or "—"
            mod_table.add_row(
                str(i),
                m.get("name", "?"),
                m.get("purpose", ""),
                deps,
            )
        console.print(mod_table)

    # Architecture decisions
    decisions = arch_data.get("architecture_decisions", [])
    if decisions:
        console.print("\n[bold green]Key Decisions[/bold green]")
        for d in decisions:
            console.print(
                f"  [bold]{d.get('decision', '')}[/bold]"
            )
            console.print(f"  [dim]{d.get('rationale', '')}[/dim]")

    # Build system
    build = arch_data.get("build_system", {})
    if build:
        console.print("\n[bold green]Build System[/bold green]")
        for k, v in build.items():
            console.print(f"  [bold]{k}:[/bold] {v}")

    # Conventions
    conventions = arch_data.get("conventions", {})
    if conventions:
        console.print("\n[bold green]Conventions[/bold green]")
        for k, v in conventions.items():
            console.print(f"  [bold]{k}:[/bold] {v}")

    # Notes
    notes = arch_data.get("notes", "")
    if notes:
        console.print(f"\n[dim]{notes}[/dim]")

    # Collect response
    console.print()
    response = Prompt.ask(
        "[bold]Approve this architecture?[/bold] "
        "[dim](Y to proceed, or describe changes)[/dim]",
        default="y",
    )

    if response.strip().lower() in _APPROVE_RESPONSES:
        return None

    return response.strip()
