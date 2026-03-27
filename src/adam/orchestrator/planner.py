"""Planning orchestrator — coordinates architecture, module planning, and scaffolding.

Runs: Architect → ModulePlanner for each module → Scaffolder → store results.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from adam.agents.architect import Architect, ArchitectureResponse
from adam.agents.module_planner import ModulePlanner, ModulePlanResponse
from adam.agents.scaffolder import Scaffolder, ScaffoldResponse
from adam.context.loader import AssetManifest, ContextFile
from adam.llm.client import LLMClient
from adam.store.store import ProjectStore
from adam.types import AgentContext

logger = logging.getLogger(__name__)

# Checkpoint callback: receives arch dict, project title.
# Returns None to approve, or a string with feedback to revise.
type ArchCheckpoint = Callable[[dict[str, Any], str], str | None]

MAX_ARCHITECTURE_REVISIONS = 5


class PlanningOrchestrator:
    """Coordinates the full planning phase."""

    def __init__(
        self,
        llm: LLMClient,
        session: AsyncSession,
        project_root: str = ".",
        on_architecture_checkpoint: ArchCheckpoint | None = None,
    ) -> None:
        self._llm = llm
        self._session = session
        self._store = ProjectStore(session)
        self._project_root = project_root
        self._arch_checkpoint = on_architecture_checkpoint

    async def run(
        self,
        project_brief: dict[str, Any],
        context_files: list[ContextFile] | None = None,
        asset_manifest: AssetManifest | None = None,
    ) -> uuid.UUID:
        """Run the full planning pipeline. Returns the project ID."""

        # Step 1: Create project
        project = await self._store.create_project(
            title=project_brief.get("title", "Untitled Project"),
            description=project_brief.get("description", ""),
            specification=project_brief,
            status="planning",
        )
        project_id = project.id
        logger.info("Created project: %s (%s)", project.title, project_id)

        # Step 2: Architecture design (Opus) with optional human checkpoint
        # Include asset manifest in the brief so the architect knows
        # what assets are available
        if asset_manifest and asset_manifest.assets:
            project_brief = {
                **project_brief,
                "available_assets": asset_manifest.summary(),
            }

        arch_data = await self._design_architecture(
            project_id, project_brief, context_files,
        )

        # Update project with architecture
        await self._store.update_project(
            project_id,
            tech_stack=arch_data.tech_stack,
            architecture={
                "decisions": list(arch_data.architecture_decisions),
                "build_system": arch_data.build_system,
                "notes": arch_data.notes,
            },
            conventions=arch_data.conventions,
        )

        logger.info(
            "Architecture designed: %d modules, %d decisions",
            len(arch_data.modules), len(arch_data.architecture_decisions),
        )

        # Step 3: Create modules
        for i, mod_spec in enumerate(arch_data.modules):
            module = await self._store.create_module(
                project_id,
                ordinal=i,
                name=mod_spec.get("name", f"module_{i}"),
                purpose=mod_spec.get("purpose", ""),
                dependencies=mod_spec.get("dependencies", []),
            )
            logger.info("Created module: %s", module.name)

        # Step 4: Seed obligations from spec (before module planning
        # so the planner can see which features each file should fulfill)
        features = project_brief.get("features", [])
        feature_strings: list[str] = []
        if isinstance(features, list):
            for feat in features:
                desc = (
                    feat
                    if isinstance(feat, str)
                    else feat.get("description", str(feat))
                )
                await self._store.create_obligation(
                    project_id,
                    description=desc,
                    source="spec",
                )
                feature_strings.append(desc)

        # Step 5: Module planning (Sonnet) — with obligation awareness
        modules = await self._store.get_modules(project_id)
        project = await self._store.get_project(project_id)

        for module in modules:
            logger.info("Planning module: %s", module.name)
            mod_ctx = AgentContext(
                project_id=str(project_id),
                project_description=project.description if project else "",
                tech_stack=arch_data.tech_stack,
                architecture={
                    "decisions": list(arch_data.architecture_decisions),
                },
                conventions=arch_data.conventions,
                module_spec={
                    "name": module.name,
                    "purpose": module.purpose,
                    "dependencies": module.dependencies,
                },
                extra={
                    "all_modules": [
                        {
                            "name": m.name,
                            "purpose": m.purpose,
                            "status": m.status,
                        }
                        for m in modules
                    ],
                    "obligations": feature_strings,
                },
            )

            planner = ModulePlanner(self._llm)
            from adam.cli.display import thinking as _thinking  # noqa: E402
            async with _thinking(f"Planning module: {module.name}"):
                plan_result = await planner.execute(mod_ctx)

            if plan_result.success and isinstance(
                plan_result.parsed, ModulePlanResponse
            ):
                plan = plan_result.parsed
                for j, fp in enumerate(plan.files):
                    file_rec = await self._store.create_file(
                        project_id,
                        module_id=module.id,
                        ordinal=j,
                        path=fp.path,
                        purpose=fp.purpose,
                        language=fp.language,
                        interface_spec=fp.interface_spec,
                    )

                    # Link file to obligations it explicitly implements
                    if fp.implements:
                        await self._link_obligations(
                            project_id, file_rec.path, fp.implements,
                        )

                logger.info(
                    "Module %s planned: %d files",
                    module.name, len(plan.files),
                )
            else:
                logger.warning(
                    "Module planning failed for %s: %s",
                    module.name, plan_result.error,
                )

        # Step 6: Scaffold project on disk
        logger.info("Scaffolding project...")
        await self._scaffold(project_id, arch_data)

        # Step 7: Copy assets to project directory
        if asset_manifest and asset_manifest.assets:
            self._copy_assets(asset_manifest)

        await self._store.update_project(project_id, status="implementing")
        await self._store.commit()

        logger.info("Planning complete for project %s", project_id)
        return project_id

    async def _scaffold(
        self,
        project_id: uuid.UUID,
        arch_data: ArchitectureResponse,
    ) -> None:
        """Run the scaffolder agent and write output to disk."""
        project = await self._store.get_project(project_id)
        modules = await self._store.get_modules(project_id)

        ctx = AgentContext(
            project_id=str(project_id),
            project_description=project.description if project else "",
            tech_stack=arch_data.tech_stack,
            architecture={
                "decisions": list(arch_data.architecture_decisions),
                "build_system": arch_data.build_system,
            },
            conventions=arch_data.conventions,
            extra={
                "modules": [
                    {"name": m.name, "purpose": m.purpose}
                    for m in modules
                ],
            },
        )

        scaffolder = Scaffolder(self._llm)
        from adam.cli.display import thinking as _thinking  # noqa: E402
        async with _thinking("Scaffolding project"):
            result = await scaffolder.execute(ctx)

        if not result.success or not isinstance(
            result.parsed, ScaffoldResponse
        ):
            logger.warning(
                "Scaffolding failed: %s. Creating minimal structure.",
                result.error,
            )
            # Fallback: just create the directory structure from modules
            for m in modules:
                (Path(self._project_root) / m.name).mkdir(
                    parents=True, exist_ok=True,
                )
            return

        scaffold = result.parsed
        root = Path(self._project_root)

        # Create directories
        for dir_path in scaffold.directories:
            (root / dir_path).mkdir(parents=True, exist_ok=True)
            logger.debug("Created directory: %s", dir_path)

        # Create files
        for sf in scaffold.files:
            file_path = root / sf.path
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(sf.content, encoding="utf-8")
            logger.info("Scaffolded: %s", sf.path)

    def _copy_assets(self, manifest: AssetManifest) -> None:
        """Copy binary assets from context/assets/ to the project directory."""
        import shutil

        root = Path(self._project_root)
        dest_dir = root / "public" / "assets"
        dest_dir.mkdir(parents=True, exist_ok=True)

        copied = 0
        for asset in manifest.assets:
            dest = dest_dir / asset.relative_path
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(asset.source_path, dest)
            copied += 1

        logger.info(
            "Copied %d asset(s) to %s", copied, dest_dir,
        )

    async def _link_obligations(
        self,
        project_id: uuid.UUID,
        file_path: str,
        implements: list[str],
    ) -> None:
        """Link a planned file to the obligations it implements."""
        obligations = await self._store.get_obligations(project_id)
        for ob in obligations:
            for feature_desc in implements:
                # Match by exact text or substring
                if (
                    feature_desc.lower() == ob.description.lower()
                    or feature_desc.lower() in ob.description.lower()
                    or ob.description.lower() in feature_desc.lower()
                ):
                    impl_files = list(ob.implementing_files or [])
                    if file_path not in impl_files:
                        impl_files.append(file_path)
                        await self._store.update_obligation(
                            project_id,
                            ob.id,
                            implementing_files=impl_files,
                        )
                    break

    async def _design_architecture(
        self,
        project_id: uuid.UUID,
        project_brief: dict[str, Any],
        context_files: list[ContextFile] | None,
    ) -> ArchitectureResponse:
        """Run the architect with optional human refinement loop."""
        user_feedback: str | None = None

        for attempt in range(MAX_ARCHITECTURE_REVISIONS):
            logger.info("Running architect (attempt %d)...", attempt + 1)

            description = project_brief.get("description", "")
            if user_feedback:
                description = (
                    f"{description}\n\n"
                    f"## Human Feedback on Previous Design\n\n"
                    f"{user_feedback}\n\n"
                    f"Please revise the architecture based on this feedback."
                )

            arch_ctx = AgentContext(
                project_id=str(project_id),
                project_description=description,
                tech_stack=project_brief.get("tech_stack", {}),
                user_context=self._context_to_dicts(context_files),
            )

            architect = Architect(self._llm)
            # Spinner wraps only the LLM call, not the checkpoint prompt
            from adam.cli.display import thinking as _thinking  # noqa: E402
            async with _thinking("Designing architecture"):
                arch_result = await architect.execute(arch_ctx)

            if not arch_result.success or not isinstance(
                arch_result.parsed, ArchitectureResponse
            ):
                logger.error(
                    "Architecture design failed: %s", arch_result.error
                )
                return ArchitectureResponse(
                    tech_stack=project_brief.get("tech_stack", {}),
                    architecture_decisions=[],
                    modules=[{
                        "name": "main",
                        "purpose": "Main application module",
                        "dependencies": [],
                    }],
                    conventions={},
                    build_system={},
                    critical_path=["main"],
                    notes="Architecture design failed; using defaults.",
                )

            arch_data = arch_result.parsed

            logger.info(
                "Architecture designed: %d modules, %d decisions",
                len(arch_data.modules),
                len(arch_data.architecture_decisions),
            )

            # Human checkpoint (if callback provided)
            if self._arch_checkpoint is not None:
                arch_dict = {
                    "tech_stack": arch_data.tech_stack,
                    "modules": arch_data.modules,
                    "architecture_decisions": arch_data.architecture_decisions,
                    "conventions": arch_data.conventions,
                    "build_system": arch_data.build_system,
                    "notes": arch_data.notes,
                }
                title = project_brief.get("title", "")
                user_feedback = self._arch_checkpoint(arch_dict, title)

                if user_feedback is None:
                    # Approved
                    logger.info("Architecture approved by user")
                    return arch_data
                logger.info(
                    "User requested changes: %s", user_feedback[:100]
                )
                continue  # Re-run architect with feedback

            # No checkpoint — auto-approve
            return arch_data

        # Max revisions exhausted
        logger.warning("Max architecture revisions reached; proceeding")
        return arch_data  # type: ignore[possibly-undefined]

    def _context_to_dicts(
        self, context_files: list[ContextFile] | None
    ) -> list[dict[str, Any]]:
        if not context_files:
            return []
        return [
            {
                "name": cf.name,
                "type": cf.context_type.value,
                "content": cf.content,
            }
            for cf in context_files
            if not cf.is_image
        ]
