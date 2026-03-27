"""ContextSlicer — builds agent-specific context windows.

Equivalent to Postwriter's CanonSlicer. Each agent type gets only
the context it needs, trimmed to fit token budgets.

Key difference from the initial version: reads actual file contents
from disk for dependency files so the implementer can see real code,
not just interface specs.
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from adam.store.store import ProjectStore
from adam.types import AgentContext

logger = logging.getLogger(__name__)

MAX_FILE_CONTENT_CHARS = 8000  # Truncate large files in context


class ContextSlicer:
    def __init__(
        self,
        session: AsyncSession,
        project_root: str = ".",
    ) -> None:
        self._store = ProjectStore(session)
        self._root = Path(project_root)

    async def build_file_context(
        self,
        project_id: uuid.UUID,
        module_id: uuid.UUID,
        file_id: uuid.UUID,
    ) -> AgentContext:
        """Build context for a file implementer or test writer.

        Reads actual source code from disk for dependency files and
        related files that have already been implemented.
        """
        project = await self._store.get_project(project_id)
        if project is None:
            return AgentContext(project_id=str(project_id))

        module = await self._store.get_module_with_files(module_id)
        file_rec = await self._store.get_file(file_id)

        # Related files: same module, already written, with disk content
        related = []
        if module:
            for f in module.files:
                if f.id != file_id and f.status != "pending":
                    entry: dict = {
                        "path": f.path,
                        "purpose": f.purpose,
                        "interface_spec": f.interface_spec,
                    }
                    # Read actual content from disk
                    content = self._read_file(f.path)
                    if content:
                        entry["content"] = content
                    related.append(entry)

        # Dependency interfaces — with actual source code
        # Note: outgoing_deps may not be loaded (lazy); safely skip if not
        dep_interfaces = []
        try:
            deps = file_rec.outgoing_deps if file_rec else []
        except Exception:
            deps = []
        if deps:
            for dep in deps:
                target = await self._store.get_file(dep.target_file_id)
                if target:
                    entry = {
                        "path": target.path,
                        "interface_spec": target.interface_spec,
                        "dependency_type": dep.dependency_type,
                    }
                    content = self._read_file(target.path)
                    if content:
                        entry["content"] = content
                    dep_interfaces.append(entry)

        # Asset manifest from project specification
        spec = project.specification or {}
        available_assets = spec.get("available_assets", "")

        return AgentContext(
            project_id=str(project_id),
            project_description=project.description,
            tech_stack=project.tech_stack,
            architecture=project.architecture,
            conventions=project.conventions,
            available_assets=available_assets,
            module_spec={
                "name": module.name if module else "",
                "purpose": module.purpose if module else "",
            },
            file_spec={
                "path": file_rec.path if file_rec else "",
                "purpose": file_rec.purpose if file_rec else "",
                "language": file_rec.language if file_rec else "",
                "interface_spec": (
                    file_rec.interface_spec if file_rec else {}
                ),
            },
            dependency_interfaces=dep_interfaces,
            related_files=related[:3],
        )

    async def build_module_context(
        self,
        project_id: uuid.UUID,
        module_id: uuid.UUID,
    ) -> AgentContext:
        """Build context for a module planner."""
        project = await self._store.get_project_full(project_id)
        if project is None:
            return AgentContext(project_id=str(project_id))

        module = await self._store.get_module(module_id)
        modules_summary = [
            {"name": m.name, "purpose": m.purpose, "status": m.status}
            for m in project.modules
        ]

        return AgentContext(
            project_id=str(project_id),
            project_description=project.description,
            tech_stack=project.tech_stack,
            architecture=project.architecture,
            conventions=project.conventions,
            module_spec={
                "name": module.name if module else "",
                "purpose": module.purpose if module else "",
                "dependencies": module.dependencies if module else [],
            },
            extra={"all_modules": modules_summary},
        )

    async def build_architecture_context(
        self,
        project_id: uuid.UUID,
    ) -> AgentContext:
        """Build context for the architect agent."""
        project = await self._store.get_project(project_id)
        if project is None:
            return AgentContext(project_id=str(project_id))

        return AgentContext(
            project_id=str(project_id),
            project_description=project.description,
            tech_stack=project.tech_stack,
            architecture=project.architecture,
        )

    async def build_repair_context(
        self,
        project_id: uuid.UUID,
        file_id: uuid.UUID,
        error_output: str,
        test_results: list[dict] | None = None,
    ) -> AgentContext:
        """Build context for error diagnosis and repair."""
        file_rec = await self._store.get_file(file_id)
        module_id = file_rec.module_id if file_rec else uuid.uuid4()
        ctx = await self.build_file_context(
            project_id, module_id, file_id,
        )
        ctx.error_output = error_output
        ctx.test_results = test_results or []
        return ctx

    def _read_file(self, relative_path: str) -> str | None:
        """Read a file from the project root. Returns None if not found."""
        full_path = self._root / relative_path
        try:
            content = full_path.read_text(encoding="utf-8")
            if len(content) > MAX_FILE_CONTENT_CHARS:
                content = (
                    content[:MAX_FILE_CONTENT_CHARS]
                    + f"\n... (truncated, {len(content)} total chars)"
                )
            return content
        except (OSError, UnicodeDecodeError):
            return None
