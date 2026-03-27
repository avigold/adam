"""Project state file (.adam/project.json) — detect, save, resume.

Equivalent to Postwriter's .postwriter project file.
The .adam/ directory holds all Adam state: project.json, adam.db, adam.log.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

ADAM_DIR = ".adam"
PROJECT_FILE = "project.json"


@dataclass
class ProjectState:
    """Persisted project state."""
    project_id: str
    phase: str = "planning"
    title: str = ""
    tech_stack: dict = field(default_factory=dict)
    root_path: str = "."
    scaffold_complete: bool = False


def detect_project(project_dir: Path) -> ProjectState | None:
    """Check for an existing .adam/project.json file."""
    path = project_dir / ADAM_DIR / PROJECT_FILE
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return ProjectState(**{
            k: v for k, v in data.items()
            if k in ProjectState.__dataclass_fields__
        })
    except (json.JSONDecodeError, TypeError, KeyError):
        return None


def save_project(project_dir: Path, state: ProjectState) -> None:
    """Write project state to .adam/project.json."""
    adam_dir = project_dir / ADAM_DIR
    adam_dir.mkdir(parents=True, exist_ok=True)
    path = adam_dir / PROJECT_FILE
    path.write_text(
        json.dumps(asdict(state), indent=2) + "\n",
        encoding="utf-8",
    )


def update_phase(project_dir: Path, phase: str) -> None:
    """Update just the phase in the project file."""
    state = detect_project(project_dir)
    if state:
        state.phase = phase
        save_project(project_dir, state)


def update_scaffold_status(project_dir: Path, complete: bool) -> None:
    """Mark scaffolding as complete or incomplete."""
    state = detect_project(project_dir)
    if state:
        state.scaffold_complete = complete
        save_project(project_dir, state)
