"""Iterate stage — incremental development on existing codebases.

Detects context file changes, analyses the delta with Opus,
produces a file-level change plan with Sonnet, seeds new obligations,
and marks affected files for re-implementation.

Entry points:
    1. Automatic: context file changes detected on `adam` run
    2. Explicit: user runs `adam iterate`
    3. Interactive: user enters iterate mode manually, answers questions
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from adam.agents.change_planner import ChangePlanner, ChangePlanResponse
from adam.agents.spec_differ import SpecDiffer, SpecDiffResponse
from adam.context.fingerprint import ContextDiff, ContextFingerprinter
from adam.context.loader import ContextFile, ContextLoader
from adam.llm.client import LLMClient
from adam.project import ProjectState, detect_project, save_project
from adam.types import AgentContext, ContextType

logger = logging.getLogger(__name__)


@dataclass
class IterateResult:
    """Outcome of the iterate stage."""
    spec_diff: SpecDiffResponse | None = None
    change_plan: ChangePlanResponse | None = None
    context_diff: ContextDiff | None = None
    files_marked_pending: list[str] = field(default_factory=list)
    new_obligations: int = 0
    updated_obligations: int = 0
    closed_obligations: int = 0
    error: str = ""

    @property
    def success(self) -> bool:
        return not self.error and self.change_plan is not None

    @property
    def has_work(self) -> bool:
        """Whether the change plan actually contains work to do."""
        if not self.change_plan:
            return False
        return bool(
            self.change_plan.files_to_create
            or self.change_plan.files_to_modify
            or self.change_plan.files_to_delete
        )


class IterateStage:
    """Analyses spec changes and produces a plan for incremental work.

    This stage replaces `plan` for iteration cycles. It reads
    changed context files, diffs them against stored snapshots,
    and uses Opus to reason about what the changes mean for the
    codebase, then Sonnet to decompose into file-level work.
    """

    def __init__(
        self,
        llm: LLMClient,
        project_dir: Path,
    ) -> None:
        self._llm = llm
        self._project_dir = project_dir
        self._fingerprinter = ContextFingerprinter(project_dir)

    async def run(
        self,
        session: Any,  # AsyncSession — lazy import to avoid circular deps
        project_id: uuid.UUID,
        context_diff: ContextDiff | None = None,
        current_files: list[ContextFile] | None = None,
        user_instructions: str = "",
    ) -> IterateResult:
        """Run the iterate stage.

        Args:
            session: Database session for reading/writing obligations.
            project_id: The existing project ID.
            context_diff: Pre-computed diff (if None, computed from fingerprints).
            current_files: Current context files (if None, loaded from disk).
            user_instructions: Free-text instructions from the user (interactive mode).
        """
        from adam.store.store import ProjectStore

        store = ProjectStore(session)
        result = IterateResult()

        # Load context if not provided
        if current_files is None:
            loader = ContextLoader(self._project_dir / "context")
            current_files = loader.load()

        # Compute diff if not provided
        if context_diff is None:
            context_diff = self._fingerprinter.diff(current_files)
        result.context_diff = context_diff

        # If no context changes and no user instructions, nothing to do
        if not context_diff.has_changes and not user_instructions:
            logger.info("No context changes and no user instructions")
            result.error = "No changes detected"
            return result

        # Get project state for context
        project = await store.get_project_full(project_id)
        if project is None:
            result.error = f"Project {project_id} not found"
            return result

        # Build module/obligation summaries for the agents
        existing_modules = self._summarise_modules(project)
        existing_obligations = self._summarise_obligations(project)
        existing_files = self._summarise_files(project)

        # ── Step 1: Spec diff (Opus) ──
        # Only run if there are spec-affecting changes or user instructions
        if context_diff.has_changes or user_instructions:
            spec_diff_result = await self._run_spec_differ(
                context_diff,
                current_files,
                project,
                existing_modules,
                existing_obligations,
                user_instructions,
            )
            if spec_diff_result is None:
                result.error = "Spec diff agent failed"
                return result
            result.spec_diff = spec_diff_result

        # ── Step 2: Change plan (Sonnet) ──
        if result.spec_diff is not None:
            change_plan = await self._run_change_planner(
                result.spec_diff,
                project,
                existing_modules,
                existing_files,
                existing_obligations,
            )
            if change_plan is None:
                result.error = "Change planner agent failed"
                return result
            result.change_plan = change_plan

        # ── Step 3: Apply the plan to the database ──
        if result.change_plan is not None:
            await self._apply_plan(
                store, project_id, result.change_plan, result,
            )

        # ── Step 4: Save fingerprints so next run sees a clean baseline ──
        self._fingerprinter.save(current_files)
        self._fingerprinter.save_content_snapshot(current_files)

        # Update project phase
        state = detect_project(self._project_dir)
        if state:
            state.phase = "implementing"
            save_project(self._project_dir, state)

        return result

    async def _run_spec_differ(
        self,
        context_diff: ContextDiff,
        current_files: list[ContextFile],
        project: Any,
        existing_modules: list[dict[str, Any]],
        existing_obligations: list[dict[str, Any]],
        user_instructions: str,
    ) -> SpecDiffResponse | None:
        """Run the Opus spec differ agent."""
        # Find the current spec content
        new_spec = ""
        for cf in current_files:
            if cf.context_type == ContextType.SPEC:
                new_spec += cf.content + "\n\n"

        # If user gave instructions, append them as spec additions
        if user_instructions:
            new_spec += (
                f"\n\n## Additional Instructions (from user)\n\n"
                f"{user_instructions}\n"
            )

        # Load old spec from snapshot
        old_spec = ""
        for change in context_diff.modified + context_diff.removed:
            if change.context_type == ContextType.SPEC:
                old_spec += self._fingerprinter.load_old_content(
                    change.relative_path,
                ) + "\n\n"

        # If we have no old spec but have stored state, the first run
        # didn't save a snapshot. Treat everything as new.
        if not old_spec and self._fingerprinter.has_stored_state():
            # We had fingerprints but no content snapshot — first iteration
            old_spec = "(No previous specification stored)"

        # Collect other (non-spec) changes for context
        other_changes = []
        for change in context_diff.added + context_diff.modified:
            if change.context_type != ContextType.SPEC:
                other_changes.append({
                    "path": change.relative_path,
                    "change_type": change.change_type,
                    "context_type": change.context_type.value,
                    "content": change.content,
                })

        differ = SpecDiffer(self._llm)

        from adam.cli.display import thinking
        async with thinking("Analysing spec changes"):
            result = await differ.execute(AgentContext(
                project_description=project.description or "",
                tech_stack=project.tech_stack or {},
                extra={
                    "old_spec": old_spec.strip(),
                    "new_spec": new_spec.strip(),
                    "other_changes": other_changes,
                    "existing_modules": existing_modules,
                    "existing_obligations": existing_obligations,
                },
            ))

        if result.success and isinstance(result.parsed, SpecDiffResponse):
            logger.info(
                "Spec diff: %d feature changes, %d constraint changes, scope=%s",
                len(result.parsed.feature_changes),
                len(result.parsed.constraint_changes),
                result.parsed.estimated_scope,
            )
            return result.parsed

        logger.warning("Spec differ failed: %s", result.error)
        return None

    async def _run_change_planner(
        self,
        spec_diff: SpecDiffResponse,
        project: Any,
        existing_modules: list[dict[str, Any]],
        existing_files: list[dict[str, Any]],
        existing_obligations: list[dict[str, Any]],
    ) -> ChangePlanResponse | None:
        """Run the Sonnet change planner agent."""
        planner = ChangePlanner(self._llm)

        from adam.cli.display import thinking
        async with thinking("Planning changes"):
            result = await planner.execute(AgentContext(
                project_description=project.description or "",
                tech_stack=project.tech_stack or {},
                conventions=project.conventions or {},
                extra={
                    "spec_diff": spec_diff.model_dump(),
                    "existing_modules": existing_modules,
                    "existing_files": existing_files,
                    "existing_obligations": existing_obligations,
                },
            ))

        if result.success and isinstance(result.parsed, ChangePlanResponse):
            logger.info(
                "Change plan: %d create, %d modify, %d delete",
                len(result.parsed.files_to_create),
                len(result.parsed.files_to_modify),
                len(result.parsed.files_to_delete),
            )
            return result.parsed

        logger.warning("Change planner failed: %s", result.error)
        return None

    async def _apply_plan(
        self,
        store: Any,
        project_id: uuid.UUID,
        plan: ChangePlanResponse,
        result: IterateResult,
    ) -> None:
        """Apply a change plan: seed obligations, mark files pending."""
        # Seed new obligations
        for ob in plan.obligations:
            if ob.action == "create":
                await store.create_obligation(
                    project_id,
                    description=ob.description,
                    source="spec_change",
                    priority=ob.priority,
                    status="open",
                )
                result.new_obligations += 1
            elif ob.action == "close":
                # Find and close matching obligations
                existing = await store.get_obligations(project_id)
                for ex_ob in existing:
                    if (
                        ob.description.lower() in ex_ob.description.lower()
                        or ex_ob.description.lower() in ob.description.lower()
                    ):
                        await store.update_obligation(
                            project_id, ex_ob.id, status="verified",
                            notes="Closed by spec change",
                        )
                        result.closed_obligations += 1
                        break

        # Mark files for creation — add as new file specs to existing modules
        for pf in plan.files_to_create:
            # Find or default the module
            module = await self._find_or_create_module(
                store, project_id, pf.module, pf.purpose,
            )
            if module:
                await store.create_file(
                    project_id,
                    module_id=module.id,
                    path=pf.path,
                    purpose=pf.purpose,
                    status="pending",
                )
                result.files_marked_pending.append(pf.path)

        # Mark files for modification — reset their status to pending
        for pf in plan.files_to_modify:
            existing_file = await store.find_file_by_path(
                project_id, pf.path,
            )
            if existing_file:
                await store.update_file(
                    project_id,
                    existing_file.id,
                    status="pending",
                    purpose=f"{existing_file.purpose}; UPDATE: {pf.purpose}",
                )
                result.files_marked_pending.append(pf.path)
            else:
                logger.warning(
                    "File to modify not found in store: %s", pf.path,
                )

        # Handle deletions — mark as "deleted" rather than actually removing
        for path in plan.files_to_delete:
            existing_file = await store.find_file_by_path(
                project_id, path,
            )
            if existing_file:
                await store.update_file(
                    project_id, existing_file.id, status="deleted",
                )
                # Also delete the actual file from disk
                disk_path = self._project_dir / path
                if disk_path.is_file():
                    disk_path.unlink()
                    logger.info("Deleted file: %s", path)

        logger.info(
            "Applied plan: %d files pending, %d new obligations, "
            "%d closed obligations",
            len(result.files_marked_pending),
            result.new_obligations,
            result.closed_obligations,
        )

    async def _find_or_create_module(
        self,
        store: Any,
        project_id: uuid.UUID,
        module_name: str,
        purpose: str,
    ) -> Any:
        """Find an existing module by name, or create a new one."""
        if not module_name:
            module_name = "main"

        modules = await store.get_modules(project_id)
        for m in modules:
            if m.name == module_name:
                return m

        # Create new module
        return await store.create_module(
            project_id=project_id,
            name=module_name,
            purpose=purpose,
            dependencies=[],
        )

    def _summarise_modules(self, project: Any) -> list[dict[str, Any]]:
        """Build module summaries for agent context."""
        return [
            {
                "name": m.name,
                "purpose": m.purpose or "",
                "file_count": len(m.files) if hasattr(m, "files") else 0,
                "files": [
                    {
                        "path": f.path,
                        "purpose": f.purpose or "",
                        "status": f.status or "unknown",
                    }
                    for f in (m.files if hasattr(m, "files") else [])
                ],
            }
            for m in (project.modules if hasattr(project, "modules") else [])
        ]

    def _summarise_obligations(self, project: Any) -> list[dict[str, Any]]:
        """Build obligation summaries for agent context."""
        return [
            {
                "description": ob.description,
                "status": ob.status,
                "implementing_files": ob.implementing_files or [],
            }
            for ob in (
                project.obligations
                if hasattr(project, "obligations") else []
            )
        ]

    def _summarise_files(self, project: Any) -> list[dict[str, Any]]:
        """Flat list of all files across modules."""
        files: list[dict[str, Any]] = []
        for m in (project.modules if hasattr(project, "modules") else []):
            for f in (m.files if hasattr(m, "files") else []):
                files.append({
                    "path": f.path,
                    "purpose": f.purpose or "",
                    "status": f.status or "unknown",
                    "module": m.name,
                })
        return files
