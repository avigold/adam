"""Context fingerprinting — detect changes to context files between runs.

Stores a hash map in .adam/context_state.json after each run.
On the next run, compares fresh hashes to stored ones to detect
new, modified, and removed context files.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from adam.context.loader import ContextFile, ContextLoader
from adam.project import ADAM_DIR
from adam.types import ContextType

logger = logging.getLogger(__name__)

CONTEXT_STATE_FILE = "context_state.json"


@dataclass
class FileFingerprint:
    """Stored state of a single context file."""
    relative_path: str
    content_hash: str
    context_type: str
    last_seen: str  # ISO timestamp
    content_preview: str = ""  # First 200 chars, for human readability

    def to_dict(self) -> dict[str, Any]:
        return {
            "content_hash": self.content_hash,
            "context_type": self.context_type,
            "last_seen": self.last_seen,
            "content_preview": self.content_preview,
        }


@dataclass
class ContextChange:
    """A detected change to a context file."""
    relative_path: str
    context_type: ContextType
    change_type: str  # "added", "modified", "removed"
    old_hash: str = ""
    new_hash: str = ""
    content: str = ""  # Current content (for added/modified)
    old_content_preview: str = ""  # Preview of what it was before


@dataclass
class ContextDiff:
    """Summary of all changes to context files since last run."""
    added: list[ContextChange] = field(default_factory=list)
    modified: list[ContextChange] = field(default_factory=list)
    removed: list[ContextChange] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        return bool(self.added or self.modified or self.removed)

    @property
    def has_spec_changes(self) -> bool:
        """Whether any spec-type file was added or modified."""
        return any(
            c.context_type == ContextType.SPEC
            for c in self.added + self.modified
        )

    @property
    def change_count(self) -> int:
        return len(self.added) + len(self.modified) + len(self.removed)

    def summary(self) -> str:
        """Human-readable summary of changes."""
        parts: list[str] = []
        if self.added:
            names = [c.relative_path for c in self.added]
            parts.append(f"New: {', '.join(names)}")
        if self.modified:
            names = [c.relative_path for c in self.modified]
            parts.append(f"Modified: {', '.join(names)}")
        if self.removed:
            names = [c.relative_path for c in self.removed]
            parts.append(f"Removed: {', '.join(names)}")
        return "; ".join(parts) if parts else "No changes"


class ContextFingerprinter:
    """Detects changes to context files between Adam runs."""

    def __init__(self, project_dir: Path) -> None:
        self._project_dir = project_dir
        self._state_path = project_dir / ADAM_DIR / CONTEXT_STATE_FILE

    def diff(self, current_files: list[ContextFile]) -> ContextDiff:
        """Compare current context files against stored fingerprints.

        Returns a ContextDiff describing what changed.
        """
        stored = self._load_state()
        result = ContextDiff()
        now = datetime.now(timezone.utc).isoformat()

        # Build lookup of current files by relative path
        current_by_path: dict[str, ContextFile] = {}
        for cf in current_files:
            try:
                rel = str(cf.path.relative_to(
                    self._project_dir / "context"
                ))
            except ValueError:
                rel = cf.path.name
            current_by_path[rel] = cf

        # Check for added and modified
        for rel_path, cf in current_by_path.items():
            current_hash = cf.content_hash or self._hash_content(cf.content)

            if rel_path not in stored:
                # New file
                result.added.append(ContextChange(
                    relative_path=rel_path,
                    context_type=cf.context_type,
                    change_type="added",
                    new_hash=current_hash,
                    content=cf.content,
                ))
            elif stored[rel_path]["content_hash"] != current_hash:
                # Modified
                result.modified.append(ContextChange(
                    relative_path=rel_path,
                    context_type=cf.context_type,
                    change_type="modified",
                    old_hash=stored[rel_path]["content_hash"],
                    new_hash=current_hash,
                    content=cf.content,
                    old_content_preview=stored[rel_path].get(
                        "content_preview", ""
                    ),
                ))

        # Check for removed
        for rel_path, fp_data in stored.items():
            if rel_path not in current_by_path:
                try:
                    ct = ContextType(fp_data.get("context_type", "unknown"))
                except ValueError:
                    ct = ContextType.UNKNOWN
                result.removed.append(ContextChange(
                    relative_path=rel_path,
                    context_type=ct,
                    change_type="removed",
                    old_hash=fp_data["content_hash"],
                    old_content_preview=fp_data.get("content_preview", ""),
                ))

        if result.has_changes:
            logger.info(
                "Context changes detected: %d added, %d modified, %d removed",
                len(result.added), len(result.modified), len(result.removed),
            )

        return result

    def save(self, current_files: list[ContextFile]) -> None:
        """Save current fingerprints after a successful run."""
        now = datetime.now(timezone.utc).isoformat()
        state: dict[str, Any] = {}

        for cf in current_files:
            try:
                rel = str(cf.path.relative_to(
                    self._project_dir / "context"
                ))
            except ValueError:
                rel = cf.path.name

            state[rel] = {
                "content_hash": cf.content_hash or self._hash_content(
                    cf.content
                ),
                "context_type": cf.context_type.value,
                "last_seen": now,
                "content_preview": cf.content[:200] if cf.content else "",
            }

        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        self._state_path.write_text(
            json.dumps(state, indent=2) + "\n",
            encoding="utf-8",
        )
        logger.info(
            "Saved fingerprints for %d context file(s)", len(state),
        )

    def save_content_snapshot(self, current_files: list[ContextFile]) -> None:
        """Save full content of context files for future diffing.

        Stored in .adam/context_snapshot/ so the spec differ agent
        can read old versions of files.
        """
        snapshot_dir = self._project_dir / ADAM_DIR / "context_snapshot"
        snapshot_dir.mkdir(parents=True, exist_ok=True)

        for cf in current_files:
            if not cf.content:
                continue
            try:
                rel = str(cf.path.relative_to(
                    self._project_dir / "context"
                ))
            except ValueError:
                rel = cf.path.name

            dest = snapshot_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(cf.content, encoding="utf-8")

    def load_old_content(self, relative_path: str) -> str:
        """Load the previously-saved content of a context file."""
        snapshot = (
            self._project_dir / ADAM_DIR / "context_snapshot" / relative_path
        )
        if snapshot.is_file():
            return snapshot.read_text(encoding="utf-8")
        return ""

    def has_stored_state(self) -> bool:
        """Whether we have any previously stored fingerprints."""
        return self._state_path.is_file()

    def _load_state(self) -> dict[str, dict[str, Any]]:
        """Load stored fingerprints from disk."""
        if not self._state_path.is_file():
            return {}
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
        except (json.JSONDecodeError, OSError):
            return {}

    @staticmethod
    def _hash_content(content: str) -> str:
        return hashlib.sha256(content.encode("utf-8")).hexdigest()[:16]
