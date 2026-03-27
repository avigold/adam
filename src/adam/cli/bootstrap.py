"""Spec-aware interactive bootstrap — collects only what's missing.

Reads context files first, determines what's already answered, and
only asks the user questions the specs don't answer. If spec.md fully
describes the project, the user may not need to answer anything beyond
confirmation.
"""

from __future__ import annotations

from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Confirm, Prompt

from adam.context.loader import ContextFile
from adam.types import ContextType

console = Console()


def collect_project_brief(
    context_files: list[ContextFile] | None = None,
) -> dict[str, Any]:
    """Interactively collect the project brief, skipping what specs answer."""
    console.print(Panel("New Project Setup", style="bold blue"))

    context_files = context_files or []
    prefilled = _extract_prefilled(context_files)
    brief: dict[str, Any] = {}

    # ---------------------------------------------------------------
    # Title — always ask (short, user wants to name their project)
    # ---------------------------------------------------------------
    brief["title"] = Prompt.ask(
        "[bold]Project name[/bold]",
        default=prefilled.get("title", ""),
    )

    # ---------------------------------------------------------------
    # Description — skip if spec file provides it
    # ---------------------------------------------------------------
    if prefilled.get("description"):
        brief["description"] = prefilled["description"]
        console.print(
            f"[dim]Description loaded from spec "
            f"({len(brief['description'])} chars)[/dim]"
        )
    else:
        console.print(
            "\n[dim]Describe your project. What does it do? "
            "Who is it for?[/dim]"
        )
        brief["description"] = Prompt.ask("[bold]Project description[/bold]")

    # ---------------------------------------------------------------
    # Tech stack — skip if tech-stack file provides it
    # ---------------------------------------------------------------
    if prefilled.get("tech_stack"):
        brief["tech_stack"] = prefilled["tech_stack"]
        console.print("[dim]Tech stack loaded from context files[/dim]")
    else:
        console.print(
            "\n[dim]What technologies should this use? "
            "(language, framework, database, etc.)[/dim]"
        )
        tech_input = Prompt.ask(
            "[bold]Tech stack[/bold]",
            default="",
        )
        if tech_input:
            brief["tech_stack"] = {"description": tech_input}
        else:
            brief["tech_stack"] = {}

    # ---------------------------------------------------------------
    # Architecture preferences — skip if architecture file provides it
    # ---------------------------------------------------------------
    if prefilled.get("architecture"):
        brief["architecture"] = prefilled["architecture"]
        console.print("[dim]Architecture preferences loaded from context[/dim]")
    else:
        brief["architecture"] = {}

    # ---------------------------------------------------------------
    # Style / conventions — skip if style file provides it
    # ---------------------------------------------------------------
    if prefilled.get("conventions"):
        brief["conventions"] = prefilled["conventions"]
        console.print("[dim]Coding conventions loaded from context[/dim]")
    else:
        brief["conventions"] = {}

    # ---------------------------------------------------------------
    # Features — ask if spec doesn't provide them
    # ---------------------------------------------------------------
    if prefilled.get("features"):
        brief["features"] = prefilled["features"]
        console.print(
            f"[dim]{len(brief['features'])} feature(s) extracted from spec[/dim]"
        )
    else:
        console.print(
            "\n[dim]List the key features "
            "(one per line, empty line to finish):[/dim]"
        )
        features: list[str] = []
        while True:
            feat = Prompt.ask("[bold]Feature[/bold]", default="")
            if not feat:
                break
            features.append(feat)
        brief["features"] = features

    # ---------------------------------------------------------------
    # UI project — ask unless spec makes it obvious
    # ---------------------------------------------------------------
    if prefilled.get("has_ui") is not None:
        brief["has_ui"] = prefilled["has_ui"]
        ui_label = "Yes" if brief["has_ui"] else "No"
        console.print(f"[dim]UI project: {ui_label} (from context)[/dim]")
    else:
        brief["has_ui"] = Confirm.ask(
            "\n[bold]Does this project have a UI?[/bold]",
            default=False,
        )

    # ---------------------------------------------------------------
    # Summary and confirm
    # ---------------------------------------------------------------
    feature_count = len(brief.get("features", []))
    desc_preview = brief.get("description", "")[:120]
    if len(brief.get("description", "")) > 120:
        desc_preview += "..."

    console.print()
    console.print(Panel.fit(
        f"[bold]{brief['title']}[/bold]\n"
        f"{desc_preview}\n"
        f"Features: {feature_count}\n"
        f"UI: {'Yes' if brief['has_ui'] else 'No'}",
        title="Project Summary",
    ))

    if not Confirm.ask("Proceed with this configuration?", default=True):
        return collect_project_brief(context_files)

    return brief


def _extract_prefilled(
    context_files: list[ContextFile],
) -> dict[str, Any]:
    """Extract answers from context files to skip redundant questions.

    Checks frontmatter for explicit fields, falls back to content analysis.
    """
    prefilled: dict[str, Any] = {}

    for cf in context_files:
        if cf.is_image:
            # Mockup images imply UI project
            prefilled["has_ui"] = True
            continue

        fm = cf.frontmatter

        # Spec files → description and features
        if cf.context_type == ContextType.SPEC:
            if cf.content and not prefilled.get("description"):
                prefilled["description"] = cf.content
            if "title" in fm:
                prefilled["title"] = fm["title"]
            if "features" in fm:
                raw = fm["features"]
                if isinstance(raw, str):
                    prefilled["features"] = [
                        f.strip() for f in raw.split(",") if f.strip()
                    ]

        # Tech stack files → tech_stack dict
        elif cf.context_type == ContextType.TECH_STACK:
            prefilled["tech_stack"] = {"from_file": cf.content}
            # Check frontmatter for structured fields
            for key in ("language", "framework", "database", "test_runner"):
                if key in fm:
                    prefilled.setdefault("tech_stack", {})[key] = fm[key]

        # Architecture files
        elif cf.context_type == ContextType.ARCHITECTURE:
            prefilled["architecture"] = {"from_file": cf.content}

        # Style files → conventions
        elif cf.context_type == ContextType.STYLE:
            prefilled["conventions"] = {"from_file": cf.content}

        # Check frontmatter for explicit has_ui
        if "has_ui" in fm:
            val = fm["has_ui"]
            prefilled["has_ui"] = val in ("true", "yes", "1", True)

    # Heuristic: if description mentions UI/frontend/web app, likely has UI
    desc = prefilled.get("description", "").lower()
    if prefilled.get("has_ui") is None:
        ui_keywords = [
            "web app", "frontend", "user interface", "dashboard",
            "landing page", " react ", " vue ", " angular ",
        ]
        # Use word-boundary-aware matching to avoid "build" matching "ui"
        desc_words = set(desc.split())
        ui_word_matches = {"react", "vue", "angular", "frontend", "dashboard"}
        if any(kw in desc for kw in ui_keywords) or (desc_words & ui_word_matches):
            prefilled["has_ui"] = True

    return prefilled
