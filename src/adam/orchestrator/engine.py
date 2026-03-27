"""Main orchestration engine — coordinates the full implementation cycle.

Multi-pass: implement pending files → integration audit → if issues found,
mark affected files pending → re-sweep. Hard limit on passes.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession

from adam.agents.integration_auditor import IntegrationAuditor
from adam.agents.route_discoverer import RouteDiscoverer, find_routing_files
from adam.execution.dev_server import DevServer, detect_dev_server
from adam.execution.runner import ShellRunner
from adam.git.manager import GitManager
from adam.inspection.api_smoke import (
    DEFAULT_ENDPOINTS,
    APISmoker,
    discover_endpoints_from_code,
)
from adam.inspection.cli_verify import CLIVerifier, detect_cli_entry_point
from adam.inspection.evaluator import VisualEvaluator
from adam.inspection.screenshotter import PageSpec, Screenshotter
from adam.llm.client import LLMClient
from adam.orchestrator.file_loop import FileLoop, FileLoopResult
from adam.orchestrator.obligations import ObligationTracker
from adam.orchestrator.policies import ImplementationPolicy
from adam.orchestrator.stop_conditions import evaluate_stop_conditions
from adam.store.slicer import ContextSlicer
from adam.store.store import ProjectStore
from adam.types import AgentContext
from adam.validation.base import ValidationSuite
from adam.validation.hard.build_checker import BuildCheckerValidator
from adam.validation.hard.lint_runner import LintRunnerValidator
from adam.validation.hard.test_runner import TestRunnerValidator
from adam.validation.hard.type_checker import TypeCheckerValidator
from adam.validation.soft.code_quality import CodeQualityCritic
from adam.validation.soft.performance import PerformanceCritic
from adam.validation.soft.security import SecurityCritic

logger = logging.getLogger(__name__)

# Callback: (result, current_index, total_files) -> None
type OnFileComplete = Callable[[FileLoopResult, int, int], None]


class Orchestrator:
    """Top-level orchestrator for the implementation phase."""

    def __init__(
        self,
        llm: LLMClient,
        session: AsyncSession,
        project_root: str = ".",
        policy: ImplementationPolicy | None = None,
        on_file_complete: OnFileComplete | None = None,
    ) -> None:
        self._llm = llm
        self._session = session
        self._store = ProjectStore(session)
        self._slicer = ContextSlicer(session, project_root=project_root)
        self._runner = ShellRunner()
        self._policy = policy or ImplementationPolicy()
        self._project_root = project_root
        self._git = GitManager(project_root, self._runner)
        self._obligations = ObligationTracker(session)
        self._on_file_complete = on_file_complete
        self._suite = self._build_validation_suite()

    def _build_validation_suite(self) -> ValidationSuite:
        hard = [
            TestRunnerValidator(self._runner),
            LintRunnerValidator(self._runner),
            TypeCheckerValidator(self._runner),
            BuildCheckerValidator(self._runner),
        ]
        soft = []
        if self._policy.run_soft_critics:
            soft = [
                CodeQualityCritic(self._llm),
                SecurityCritic(self._llm),
                PerformanceCritic(self._llm),
            ]
        return ValidationSuite(hard_validators=hard, soft_critics=soft)

    async def run(self, project_id: uuid.UUID) -> OrchestratorResult:
        """Run the full implementation phase with multi-pass revision."""
        project = await self._store.get_project_full(project_id)
        if project is None:
            return OrchestratorResult(success=False, error="Project not found")

        logger.info("Starting implementation for: %s", project.title)

        build_sys = project.architecture.get("build_system", {})
        # Handle both flat and nested command structures
        commands = build_sys.get("commands", build_sys)
        test_cmd = (
            commands.get("test", "")
            or build_sys.get("test_runner", "")
        )
        lint_cmd = (
            commands.get("lint", "")
            or commands.get("type_check", "")
            or build_sys.get("linter", "")
        )
        type_cmd = (
            commands.get("type_check", "")
            or build_sys.get("type_checker", "")
        )
        build_cmd = (
            commands.get("build", "")
            or build_sys.get("build", "")
        )

        file_loop = FileLoop(
            llm=self._llm,
            runner=self._runner,
            validation_suite=self._suite,
            policy=self._policy,
            project_root=self._project_root,
        )

        all_results: list[FileLoopResult] = []
        total_files = sum(len(m.files) for m in project.modules)
        integration_issues: list[dict] = []
        total_passes = 0

        # ==============================================================
        # Multi-pass loop
        # ==============================================================
        for pass_num in range(self._policy.max_passes):
            total_passes = pass_num + 1
            is_revision = pass_num > 0

            if is_revision:
                logger.info(
                    "Revision pass %d: re-implementing affected files",
                    pass_num,
                )
                # Reload project to get updated statuses
                project = await self._store.get_project_full(project_id)
                if project is None:
                    break

            # ----------------------------------------------------------
            # Build repair (fix compiler errors FIRST, before anything else)
            # ----------------------------------------------------------
            # Run build repair if there are already written files
            # (resume or revision — either way, try building first)
            has_written_files = any(
                f.status == "written"
                for m in project.modules
                for f in m.files
            )
            if build_cmd and has_written_files:
                logger.info("Running build repair before revision sweep...")
                build_ok = await self._run_build_repair_loop(
                    project_id, build_cmd, file_loop,
                )
                if build_ok:
                    # Build passes — pending files were fixed by repair,
                    # not by re-implementation. Mark them written.
                    project = await self._store.get_project_full(project_id)
                    if project:
                        for module in project.modules:
                            for f in module.files:
                                if f.status == "pending":
                                    fp = Path(self._project_root) / f.path
                                    if fp.exists():
                                        await self._store.update_file(
                                            project_id, f.id,
                                            status="written",
                                        )
                        logger.info(
                            "Build passes — skipping re-implementation"
                        )

            # ----------------------------------------------------------
            # Sweep: implement all pending files
            # ----------------------------------------------------------
            pass_results: list[FileLoopResult] = []
            files_revised_this_pass = 0
            processed = 0

            for module in project.modules:
                pending_files = [
                    f for f in module.files if f.status == "pending"
                ]

                if not pending_files and not is_revision:
                    # First pass: mark module as implementing
                    await self._store.update_module(
                        project_id, module.id, status="implementing"
                    )

                if not pending_files:
                    # All files done in this module for this pass
                    processed += len(module.files)
                    continue

                if is_revision:
                    logger.info(
                        "Revising module %s: %d file(s) pending",
                        module.name, len(pending_files),
                    )
                else:
                    logger.info(
                        "Implementing module: %s (%d files)",
                        module.name, len(module.files),
                    )
                    await self._store.update_module(
                        project_id, module.id, status="implementing"
                    )

                for file_rec in module.files:
                    processed += 1

                    # Skip completed files
                    if file_rec.status in ("written", "tested", "reviewed"):
                        pass_results.append(FileLoopResult(
                            file_path=file_rec.path,
                            accepted=True,
                            code="",
                        ))
                        if self._on_file_complete:
                            self._on_file_complete(
                                pass_results[-1], processed, total_files,
                            )
                        continue

                    action = "Revising" if is_revision else "Implementing"
                    logger.info(
                        "[%d/%d] %s: %s",
                        processed, total_files, action, file_rec.path,
                    )

                    ctx = await self._slicer.build_file_context(
                        project_id, module.id, file_rec.id
                    )

                    result = await file_loop.process_file(
                        ctx,
                        test_command=test_cmd,
                        lint_command=lint_cmd,
                        type_check_command=type_cmd,
                        build_command=build_cmd,
                    )
                    pass_results.append(result)
                    files_revised_this_pass += 1

                    new_status = "written" if result.accepted else "pending"
                    await self._store.update_file(
                        project_id,
                        file_rec.id,
                        status=new_status,
                        content_hash=result.content_hash,
                        quality_scores={
                            "composite": (
                                result.scores.composite
                                if result.scores else 0.0
                            ),
                            "hard_pass": (
                                result.scores.hard_pass
                                if result.scores else False
                            ),
                        },
                    )

                    if result.accepted:
                        await self._obligations.link_file_to_obligations(
                            project_id, file_rec.path, file_rec.purpose,
                        )

                    if self._policy.auto_commit and result.accepted:
                        msg = (
                            f"Revise {file_rec.path}"
                            if is_revision
                            else f"Implement {file_rec.path}"
                        )
                        await self._git.commit_file(file_rec.path, msg)

                    if result.error:
                        logger.error(
                            "File failed: %s — %s",
                            file_rec.path, result.error,
                        )

                    # Cross-file issues flagged by diagnostician
                    if result.also_affected:
                        await self._mark_files_pending(
                            project_id, result.also_affected,
                        )
                        logger.info(
                            "Diagnostician flagged %d other file(s) for revision",
                            len(result.also_affected),
                        )

                    if self._on_file_complete:
                        self._on_file_complete(
                            result, processed, total_files,
                        )

                # Update module status
                module_results = [
                    r for r in pass_results
                    if any(f.path == r.file_path for f in module.files)
                ]
                all_module_accepted = (
                    module_results
                    and all(r.accepted for r in module_results)
                )

                # Module-level validation (for greenfield projects)
                if (
                    all_module_accepted
                    and not self._policy.test_per_file
                    and test_cmd
                ):
                        logger.info(
                            "Running tests for module: %s", module.name
                        )
                        test_result = await self._runner.run_test(
                            test_cmd, cwd=self._project_root,
                        )
                        if not test_result.success:
                            logger.warning(
                                "Module %s tests failed: %s",
                                module.name,
                                test_result.output[:300],
                            )

                if all_module_accepted:
                    await self._store.update_module(
                        project_id, module.id, status="tested"
                    )

            # Replace results with this pass's results
            all_results = pass_results

            # ----------------------------------------------------------
            # Build repair (also after first pass sweep)
            # ----------------------------------------------------------
            all_accepted = all(r.accepted for r in all_results)

            if all_accepted and build_cmd:
                await self._run_build_repair_loop(
                    project_id, build_cmd, file_loop,
                )

            # ----------------------------------------------------------
            # Integration audit
            # ----------------------------------------------------------
            files_to_revise: list[str] = []

            if all_accepted and total_files > 1:
                logger.info(
                    "Running integration audit (pass %d)...",
                    pass_num + 1,
                )
                integration_issues = await self._run_integration_audit(
                    project_id, test_cmd,
                )
                files_to_revise.extend(self._extract_affected_files(
                    integration_issues, project,
                ))

            # ----------------------------------------------------------
            # Visual audit (if UI project)
            # ----------------------------------------------------------
            if (
                all_accepted
                and self._policy.visual_inspection
            ):
                logger.info(
                    "Running visual audit (pass %d)...",
                    pass_num + 1,
                )
                visual_issues = await self._run_visual_pipeline(
                    project_id,
                )
                visual_revisions = self._extract_visual_revisions(
                    visual_issues,
                )
                files_to_revise.extend(visual_revisions)

            # ----------------------------------------------------------
            # API smoke test (if API project, not UI)
            # ----------------------------------------------------------
            if all_accepted and not self._policy.visual_inspection:
                api_results = await self._run_api_smoke(project_id)
                if api_results:
                    failed = [r for r in api_results if not r.success]
                    if failed:
                        logger.warning(
                            "API smoke: %d/%d endpoints failed",
                            len(failed), len(api_results),
                        )

            # ----------------------------------------------------------
            # CLI verification (if CLI project)
            # ----------------------------------------------------------
            if all_accepted and not self._policy.visual_inspection:
                cli_results = await self._run_cli_verify(project_id)
                if cli_results:
                    failed = [r for r in cli_results if not r.passed]
                    if failed:
                        logger.warning(
                            "CLI verify: %d/%d tests failed",
                            len(failed), len(cli_results),
                        )

            # ----------------------------------------------------------
            # Mark affected files for revision if needed
            # ----------------------------------------------------------
            if files_to_revise:
                # Deduplicate
                unique_revisions = list(dict.fromkeys(files_to_revise))
                revised = await self._mark_files_pending(
                    project_id, unique_revisions,
                )
                if revised > 0:
                    logger.info(
                        "Marked %d file(s) for revision: %s",
                        revised,
                        ", ".join(unique_revisions[:5]),
                    )
                    continue  # Next pass will re-implement them

            # ----------------------------------------------------------
            # No more revisions needed (or max passes reached)
            # ----------------------------------------------------------
            if files_revised_this_pass == 0 and is_revision:
                logger.info("No files needed revision; stopping.")
            break

        # ==============================================================
        # Stop condition evaluation
        # ==============================================================
        # Visual passes if we ran visual inspection and found nothing
        # to revise on the final pass
        visual_passes: bool | None = None
        if self._policy.visual_inspection:
            # If we got here without continuing, no visual revisions needed
            visual_passes = True

        ob_status = await self._obligations.get_completion_status(project_id)

        composites = [
            r.scores.composite for r in all_results
            if r.scores is not None
        ]
        avg_composite = (
            sum(composites) / len(composites) if composites else 0.5
        )
        hard_pass = all(
            r.scores.hard_pass for r in all_results
            if r.scores is not None
        )

        stop = evaluate_stop_conditions(
            obligation_status=ob_status,
            all_tests_pass=hard_pass,
            hard_validators_pass=hard_pass,
            soft_composite=avg_composite,
            acceptance_threshold=self._policy.acceptance_threshold,
            visual_passes=visual_passes,
            files_accepted=sum(1 for r in all_results if r.accepted),
            files_total=total_files,
        )

        logger.info("Stop conditions: %s", stop.summary)

        final_status = "complete" if stop.ready else "testing"
        await self._store.update_project(project_id, status=final_status)
        await self._store.commit()

        logger.info(
            "Implementation complete: %d/%d files accepted, "
            "%d pass(es), %d/%d conditions met",
            sum(1 for r in all_results if r.accepted), len(all_results),
            total_passes, stop.met_count, len(stop.conditions),
        )

        return OrchestratorResult(
            success=stop.ready,
            files_processed=len(all_results),
            files_accepted=sum(1 for r in all_results if r.accepted),
            total_repair_rounds=sum(r.repair_rounds for r in all_results),
            total_passes=total_passes,
            warnings=[w for r in all_results for w in r.warnings],
            file_results=all_results,
            obligation_status={
                "total": ob_status.total,
                "open": ob_status.open,
                "complete": ob_status.complete,
                "ratio": ob_status.completion_ratio,
            },
            stop_conditions=[
                {"name": c.name, "met": c.met, "detail": c.detail}
                for c in stop.conditions
            ],
            integration_issues=integration_issues,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _extract_affected_files(
        self,
        issues: list[dict],
        project: object,
    ) -> list[str]:
        """Extract file paths that need revision from integration issues."""
        affected: set[str] = set()
        for issue in issues:
            severity = issue.get("severity", "minor")
            if severity not in ("critical", "major"):
                continue
            for path in issue.get("affected_files", []):
                affected.add(path)
        return list(affected)

    async def _mark_files_pending(
        self,
        project_id: uuid.UUID,
        file_paths: list[str],
    ) -> int:
        """Mark specific files as pending for re-implementation."""
        project = await self._store.get_project_full(project_id)
        if project is None:
            return 0

        marked = 0
        for module in project.modules:
            for file_rec in module.files:
                if file_rec.path in file_paths and file_rec.status != "pending":
                    await self._store.update_file(
                        project_id, file_rec.id, status="pending",
                    )
                    marked += 1
                    logger.debug("Marked for revision: %s", file_rec.path)
        return marked

    async def _run_build_repair_loop(
        self,
        project_id: uuid.UUID,
        build_cmd: str,
        file_loop: FileLoop,
        max_rounds: int = 5,
    ) -> bool:
        """Run build, parse errors, repair affected files, repeat.

        Phase 1 (rounds 1-2): Sonnet fixes files individually (fast,
        handles simple/isolated errors like missing imports).
        Phase 2 (rounds 3+): Opus sees ALL errors and ALL affected
        source files at once, produces coordinated fixes.

        Returns True if the build passes, False if max rounds exhausted.
        """

        prev_error_count = None

        for round_num in range(max_rounds):
            logger.info(
                "Build check (round %d/%d)...",
                round_num + 1, max_rounds,
            )
            build_result = await self._runner.run_build(
                build_cmd, cwd=self._project_root,
            )

            if build_result.success:
                logger.info("Build passes!")
                return True

            errors = build_result.output
            error_files = self._parse_build_errors(errors)

            if not error_files:
                logger.warning(
                    "Could not parse file paths from build errors"
                )
                break

            error_count = len(error_files)
            logger.info(
                "Build errors in %d file(s)", error_count,
            )

            # Detect oscillation — if error count isn't decreasing,
            # switch to Opus immediately
            use_opus = (
                round_num >= 2
                or (
                    prev_error_count is not None
                    and error_count >= prev_error_count
                )
            )
            prev_error_count = error_count

            if use_opus:
                logger.info(
                    "Using Opus for holistic build repair "
                    "(%d files)...", error_count,
                )
                fixed = await self._holistic_build_repair(
                    errors, error_files,
                )
            else:
                logger.info(
                    "Using Sonnet for per-file build repair "
                    "(%d files)...", error_count,
                )
                fixed = await self._per_file_build_repair(
                    project_id, errors, error_files,
                )

            if fixed == 0:
                logger.warning("No files could be repaired.")
                break

        # Final check
        final = await self._runner.run_build(
            build_cmd, cwd=self._project_root,
        )
        if final.success:
            logger.info("Build passes after repair!")
            return True

        logger.warning(
            "Build still failing after %d rounds", max_rounds,
        )
        return False

    def _parse_build_errors(
        self, errors: str,
    ) -> dict[str, list[str]]:
        """Parse compiler output to find affected files and their errors."""
        import re
        error_files: dict[str, list[str]] = {}
        for line in errors.split("\n"):
            match = re.match(r"(src/[^\s:(]+\.ts)[:(]", line)
            if match:
                fpath = match.group(1)
                error_files.setdefault(fpath, []).append(line.strip())
        return error_files

    async def _per_file_build_repair(
        self,
        project_id: uuid.UUID,
        full_errors: str,
        error_files: dict[str, list[str]],
    ) -> int:
        """Sonnet fixes each file individually. Fast but may oscillate."""
        from adam.agents.repair_agent import RepairAgent, RepairSpec

        files_fixed = 0
        for fpath, file_errors in error_files.items():
            full_path = Path(self._project_root) / fpath
            if not full_path.exists():
                continue

            source = full_path.read_text(encoding="utf-8")
            error_text = "\n".join(file_errors[:20])

            repair_spec = RepairSpec(
                instruction=(
                    "Fix the build/compilation errors in "
                    f"this file:\n{error_text}"
                ),
                diagnosis=(
                    "TypeScript build failed. "
                    f"Errors:\n{error_text}"
                ),
            )

            repair_ctx = AgentContext(
                project_id=str(project_id),
                file_spec={"path": fpath},
                error_output=error_text,
            )

            repairer = RepairAgent(
                self._llm,
                source_code=source,
                repair_spec=repair_spec,
            )
            result = await repairer.execute(repair_ctx)

            if result.success and result.raw_response.strip():
                full_path.write_text(
                    result.raw_response, encoding="utf-8"
                )
                files_fixed += 1
                logger.info("Build-repaired: %s", fpath)
            else:
                logger.warning(
                    "Build repair failed for %s: %s",
                    fpath, result.error,
                )

        return files_fixed

    async def _holistic_build_repair(
        self,
        full_errors: str,
        error_files: dict[str, list[str]],
    ) -> int:
        """Opus sees all errors and all affected files at once.

        Produces a coordinated fix plan that doesn't oscillate.
        Returns the file contents for each file as a JSON response.
        """
        # Read all affected source files
        file_contents: dict[str, str] = {}
        for fpath in error_files:
            full_path = Path(self._project_root) / fpath
            if full_path.exists():
                content = full_path.read_text(encoding="utf-8")
                if len(content) > 5000:
                    content = content[:5000] + "\n[truncated]"
                file_contents[fpath] = content

        if not file_contents:
            return 0

        # Build prompt with ALL errors and ALL affected files
        # Use a delimiter format instead of JSON to avoid escaping nightmares
        delimiter = "===FILE==="

        parts = [
            "The TypeScript build is failing with these errors:\n",
            f"```\n{full_errors[:8000]}\n```\n",
            f"\nThere are {len(file_contents)} affected files.\n",
            "Fix ALL the errors in a coordinated way. If a type "
            "needs to change, update it AND all files that use it.\n\n",
            "For each file that needs changes, output:\n",
            f"{delimiter} filepath\n",
            "followed by the COMPLETE corrected file contents.\n",
            "Only include files that actually need changes.\n",
        ]
        for fpath, content in file_contents.items():
            parts.append(f"\n### {fpath}\n```\n{content}\n```\n")

        prompt = "\n".join(parts)

        from adam.types import ModelTier
        resp = await self._llm.complete(
            tier=ModelTier.OPUS,
            messages=[{"role": "user", "content": prompt}],
            system=(
                "You are a senior TypeScript engineer. Fix all "
                "build errors across these files in a coordinated "
                f"way. Output each fixed file preceded by "
                f"'{delimiter} filepath'. Only include files that "
                "need changes. Output ONLY the delimiter lines "
                "and file contents — no explanations."
            ),
            max_tokens=self._llm.settings.max_response_tokens,
            temperature=0.3,
        )

        # Parse delimiter-separated response
        files_fixed = 0
        sections = resp.text.split(delimiter)
        for section in sections[1:]:  # Skip everything before first delimiter
            lines = section.strip().split("\n", 1)
            if len(lines) < 2:
                continue
            fpath = lines[0].strip()
            content = lines[1].strip()
            # Strip markdown fences if present
            if content.startswith("```"):
                content_lines = content.split("\n")
                content = "\n".join(content_lines[1:])
            if content.endswith("```"):
                content = content[:-3].rstrip()

            full_path = Path(self._project_root) / fpath
            if full_path.exists() and content:
                full_path.write_text(content, encoding="utf-8")
                files_fixed += 1
                logger.info("Holistic-repaired: %s", fpath)

        logger.info(
            "Opus holistic repair fixed %d/%d files",
            files_fixed, len(file_contents),
        )
        return files_fixed

    async def _run_integration_audit(
        self,
        project_id: uuid.UUID,
        test_command: str,
    ) -> list[dict]:
        """Run integration audit after all modules complete."""
        project = await self._store.get_project_full(project_id)
        if project is None:
            return []

        test_output = ""
        if test_command:
            result = await self._runner.run_test(
                test_command, cwd=self._project_root,
            )
            test_output = result.output

        obs = await self._store.get_obligations(project_id)
        ob_dicts = [
            {"status": o.status, "description": o.description}
            for o in obs
        ]

        modules_info = [
            {
                "name": m.name,
                "purpose": m.purpose,
                "status": m.status,
                "files": [f.path for f in m.files],
            }
            for m in project.modules
        ]

        ctx = AgentContext(
            project_id=str(project_id),
            project_description=project.description,
            architecture=project.architecture,
            extra={
                "modules": modules_info,
                "test_output": test_output,
                "obligations": ob_dicts,
            },
        )

        auditor = IntegrationAuditor(self._llm)
        result = await auditor.execute(ctx)

        if result.success and result.parsed:
            issues = [
                {
                    "severity": i.severity,
                    "description": i.description,
                    "affected_modules": i.affected_modules,
                    "affected_files": i.affected_files,
                    "fix_suggestion": i.fix_suggestion,
                }
                for i in result.parsed.issues
            ]
            if issues:
                logger.warning(
                    "Integration audit found %d issue(s)", len(issues)
                )
            return issues

        return []

    async def _run_visual_pipeline(
        self,
        project_id: uuid.UUID,
    ) -> list[dict]:
        """Full visual inspection pipeline.

        1. Detect dev server config
        2. Discover routes
        3. Start dev server
        4. Screenshot all routes
        5. Evaluate with Opus vision
        6. Stop dev server
        7. Return evaluation results
        """
        project = await self._store.get_project(project_id)
        if project is None:
            return []

        # Step 1: Detect dev server
        server_config = detect_dev_server(
            self._project_root,
            tech_stack=project.tech_stack,
            build_system=project.architecture.get("build_system"),
        )
        if server_config is None:
            logger.warning("No dev server detected; skipping visual audit")
            return []

        # Step 2: Discover routes
        routes = await self._discover_routes(project_id)
        if not routes:
            # Fallback: just screenshot the root
            routes = [{"path": "/", "name": "index", "description": "Home page"}]

        # Step 3-6: Start server, screenshot, evaluate, stop
        server = DevServer.from_config(server_config, cwd=self._project_root)

        try:
            started = await server.start()
            if not started:
                logger.warning(
                    "Dev server failed to start; skipping visual audit. "
                    "Output: %s", server.recent_output[:500],
                )
                return []

            # Step 4: Screenshot
            pages = [
                PageSpec(
                    url=f"{server.url}{r.get('path', '/')}",
                    name=r.get("name", "page"),
                    actions=r.get("actions", []),
                )
                for r in routes
            ]

            screenshotter = Screenshotter(
                output_dir=Path(self._project_root) / ".adam-screenshots"
            )
            screenshots = await screenshotter.capture(pages)

            # Step 5: Evaluate
            evaluator = VisualEvaluator(self._llm)
            spec_desc = project.description if project else ""

            evaluations = await evaluator.evaluate(
                screenshots, spec_description=spec_desc,
            )

            results = []
            for ev in evaluations:
                results.append({
                    "page": ev.page_name,
                    "score": ev.score,
                    "passes": ev.passes,
                    "summary": ev.summary,
                    "issue_count": len(ev.issues),
                    "issues": [
                        {
                            "severity": i.severity,
                            "category": i.category,
                            "description": i.description,
                            "suggestion": i.suggestion,
                        }
                        for i in ev.issues
                    ],
                })
                level = logger.warning if not ev.passes else logger.info
                level(
                    "Visual: %s — score=%.2f %s",
                    ev.page_name, ev.score,
                    ev.summary[:100] if ev.summary else "",
                )

            return results

        finally:
            await server.stop()

    async def _discover_routes(
        self,
        project_id: uuid.UUID,
    ) -> list[dict]:
        """Discover routes/pages in the project for screenshotting."""
        project = await self._store.get_project(project_id)
        if project is None:
            return []

        # Find routing files on disk
        routing_files = find_routing_files(
            self._project_root,
            tech_stack=project.tech_stack,
        )

        if not routing_files:
            return []

        # Ask Sonnet to extract routes
        ctx = AgentContext(
            project_id=str(project_id),
            project_description=project.description,
            tech_stack=project.tech_stack,
            extra={"routing_files": routing_files},
        )

        discoverer = RouteDiscoverer(self._llm)
        result = await discoverer.execute(ctx)

        if result.success and result.parsed:
            return [
                {
                    "path": r.path,
                    "name": r.name,
                    "description": r.description,
                    "actions": [a.model_dump() for a in r.actions]
                    if hasattr(r.actions[0], "model_dump") and r.actions
                    else r.actions,
                }
                for r in result.parsed.routes
            ]

        return []

    @staticmethod
    def _extract_visual_revisions(visual_results: list[dict]) -> list[str]:
        """Extract file paths needing revision from visual audit results.

        Visual issues don't directly map to files — we flag component/page
        files based on the page name and issue descriptions.
        For now, return empty: visual issues are logged and tracked but
        file-level revision mapping requires the route→file mapping that
        the route discoverer doesn't yet provide.
        """
        # Future: map page names back to component files
        # For now, visual issues inform the developer but don't
        # auto-revise files (the mapping is too ambiguous)
        revisions: list[str] = []
        for result in visual_results:
            if not result.get("passes", True):
                for issue in result.get("issues", []):
                    if issue.get("severity") == "critical":
                        # Log for visibility but don't auto-revise yet
                        logger.warning(
                            "Critical visual issue on %s: %s",
                            result.get("page", "?"),
                            issue.get("description", ""),
                        )
        return revisions

    async def _run_api_smoke(
        self,
        project_id: uuid.UUID,
    ) -> list:
        """Run API smoke tests if this looks like an API project."""
        project = await self._store.get_project(project_id)
        if project is None:
            return []

        # Detect if there's a dev server (API projects have one)
        server_config = detect_dev_server(
            self._project_root,
            tech_stack=project.tech_stack,
            build_system=project.architecture.get("build_system"),
        )
        if server_config is None:
            return []

        # Discover endpoints from code
        endpoints = discover_endpoints_from_code(
            self._project_root,
            tech_stack=project.tech_stack,
        )
        if len(endpoints) <= len(DEFAULT_ENDPOINTS):
            # Only default endpoints — probably not an API project
            return []

        logger.info("Running API smoke tests (%d endpoints)", len(endpoints))

        server = DevServer.from_config(server_config, cwd=self._project_root)
        try:
            started = await server.start()
            if not started:
                logger.warning("Dev server failed for API smoke; skipping")
                return []

            smoker = APISmoker(self._runner)
            results = await smoker.smoke_test(server.url, endpoints)

            for r in results:
                level = logger.info if r.success else logger.warning
                level("  %s", r.summary)

            return results

        finally:
            await server.stop()

    async def _run_cli_verify(
        self,
        project_id: uuid.UUID,
    ) -> list:
        """Run CLI verification if this looks like a CLI project."""
        project = await self._store.get_project(project_id)
        if project is None:
            return []

        entry_point = detect_cli_entry_point(
            self._project_root,
            tech_stack=project.tech_stack,
            build_system=project.architecture.get("build_system"),
        )
        if entry_point is None:
            return []

        logger.info("Running CLI verification: %s", entry_point)

        verifier = CLIVerifier(self._runner, self._llm)
        test_cases = await verifier.generate_test_cases(
            project.description,
            entry_point,
            tech_stack=project.tech_stack,
        )

        results = await verifier.run_tests(
            test_cases, cwd=self._project_root,
        )

        for r in results:
            level = logger.info if r.passed else logger.warning
            level("  %s", r.summary)

        return results


class OrchestratorResult:
    """Result of the full orchestration run."""

    def __init__(
        self,
        success: bool,
        files_processed: int = 0,
        files_accepted: int = 0,
        total_repair_rounds: int = 0,
        total_passes: int = 1,
        warnings: list[str] | None = None,
        file_results: list[FileLoopResult] | None = None,
        error: str = "",
        obligation_status: dict | None = None,
        stop_conditions: list[dict] | None = None,
        integration_issues: list[dict] | None = None,
    ) -> None:
        self.success = success
        self.files_processed = files_processed
        self.files_accepted = files_accepted
        self.total_repair_rounds = total_repair_rounds
        self.total_passes = total_passes
        self.warnings = warnings or []
        self.file_results = file_results or []
        self.error = error
        self.obligation_status = obligation_status or {}
        self.stop_conditions = stop_conditions or []
        self.integration_issues = integration_issues or []
