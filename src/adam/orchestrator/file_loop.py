"""File implementation loop — the core inner cycle.

Equivalent to Postwriter's SceneLoop. For each file:
1. Implement from spec
2. Write to disk
3. Run tests
4. If tests fail → diagnose → repair → re-test (up to N rounds)
5. Run soft critics
6. If critics flag issues → repair → re-validate
7. Mark file complete

This is the cycle described in CLAUDE.md Section 9.
"""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path

from adam.agents.file_implementer import FileImplementer
from adam.agents.repair_agent import RepairAgent, RepairSpec
from adam.agents.test_writer import TestWriter
from adam.execution.runner import ShellRunner
from adam.llm.client import LLMClient
from adam.orchestrator.monitor import ProgressMonitor, RoundOutcome
from adam.orchestrator.policies import ImplementationPolicy
from adam.repair.planner import RepairPlanner
from adam.types import AgentContext, ScoreVectorData, ValidationResult, scores_from_validation
from adam.validation.base import ValidationContext, ValidationSuite
from adam.validation.file_classifier import classify_file

logger = logging.getLogger(__name__)


class FileLoop:
    """Implements a single file through the implement→test→repair cycle."""

    def __init__(
        self,
        llm: LLMClient,
        runner: ShellRunner,
        validation_suite: ValidationSuite,
        policy: ImplementationPolicy | None = None,
        project_root: str = ".",
    ) -> None:
        self._llm = llm
        self._runner = runner
        self._suite = validation_suite
        self._policy = policy or ImplementationPolicy()
        self._project_root = project_root
        self._repair_planner = RepairPlanner()

    async def process_file(
        self,
        context: AgentContext,
        test_command: str = "",
        lint_command: str = "",
        type_check_command: str = "",
        build_command: str = "",
        generate_tests: bool = True,
    ) -> FileLoopResult:
        """Run the full implementation loop for a single file."""
        file_path = context.file_spec.get("path", "")
        logger.info("Processing file: %s", file_path)

        # Step 1: Implement
        implementer = FileImplementer(self._llm)
        impl_result = await implementer.execute(context)

        if not impl_result.success:
            logger.error("Implementation failed for %s: %s", file_path, impl_result.error)
            return FileLoopResult(
                file_path=file_path,
                accepted=False,
                error=impl_result.error or "Implementation failed",
            )

        code = impl_result.raw_response
        self._write_file(file_path, code)
        logger.info("File written: %s (%d chars)", file_path, len(code))

        # Step 2: Repair loop
        # If test_per_file is False (greenfield), skip hard validators
        # during per-file implementation. They'll run at module level.
        run_hard = self._policy.test_per_file
        val_ctx = ValidationContext(
            project_id=context.project_id,
            file_path=file_path,
            file_content=code,
            file_language=context.file_spec.get("language", ""),
            file_type=classify_file(file_path, code),
            module_name=context.module_spec.get("name", ""),
            project_root=self._project_root,
            tech_stack=context.tech_stack,
            conventions=context.conventions,
            test_command=test_command if run_hard else "",
            lint_command=lint_command if run_hard else "",
            type_check_command=type_check_command if run_hard else "",
            build_command=build_command if run_hard else "",
        )

        best_scores: ScoreVectorData | None = None
        all_results: list[ValidationResult] = []
        all_cross_file_affected: list[str] = []
        monitor = ProgressMonitor(
            stagnation_threshold=3,
            max_rounds_per_file=self._policy.max_repair_rounds,
        )

        for round_num in range(self._policy.max_repair_rounds):
            logger.info("Validation round %d for %s", round_num + 1, file_path)

            # Run hard validators
            hard_results = await self._suite.run_hard(val_ctx)
            all_pass = all(r.passed for r in hard_results if r.passed is not None)

            if all_pass and self._policy.run_soft_critics:
                soft_results = await self._suite.run_soft(val_ctx)
                all_results = hard_results + soft_results
            else:
                all_results = hard_results

            scores = scores_from_validation(all_results)

            critics_ok = (
                not self._policy.run_soft_critics
                or scores.composite >= self._policy.acceptance_threshold
            )
            if scores.hard_pass and critics_ok:
                logger.info(
                    "File accepted: %s (composite=%.2f)",
                    file_path, scores.composite,
                )
                return await self._accept_file(
                    context, code, scores, all_results,
                    round_num, generate_tests,
                    also_affected=all_cross_file_affected,
                )

            # Check if we're improving
            if best_scores is not None:
                delta = scores.composite - best_scores.composite
                if delta < self._policy.min_improvement_delta and scores.hard_pass:
                    logger.info(
                        "Improvement stalled (delta=%.3f) for %s; accepting.",
                        delta, file_path,
                    )
                    return await self._accept_file(
                        context, code, scores, all_results,
                        round_num, generate_tests,
                        warnings=["Improvement stalled; accepted at current quality."],
                        also_affected=all_cross_file_affected,
                    )
            best_scores = scores

            # Diagnose and repair
            repair_actions = self._repair_planner.plan(all_results)
            if not repair_actions:
                break

            top_action = repair_actions[0]
            logger.info(
                "Repairing %s: %s (priority=%d)",
                file_path, top_action.target_dimension, top_action.priority.value,
            )

            # Record for monitor — count hard failures as "errors"
            hard_failures = sum(
                1 for r in all_results
                if r.is_hard and r.passed is not None and not r.passed
            )
            monitor.record(RoundOutcome(
                round_number=round_num + 1,
                error_count=hard_failures,
                files_affected=[file_path],
                action_taken=f"repair:{top_action.target_dimension}",
                result=(
                    f"{hard_failures} hard failures, "
                    f"composite={scores.composite:.2f}"
                ),
            ))

            # Check for trouble — but only after round 2
            # (give it a chance to make initial progress)
            if round_num >= 2:
                assessment = monitor.assess()
                if assessment.needs_supervisor:
                    logger.info(
                        "File loop monitor: %s for %s — %s",
                        assessment.signal.value,
                        file_path,
                        assessment.evidence,
                    )
                    # For file-level issues, don't call Opus supervisor
                    # (too expensive per file). Instead, apply heuristic:
                    if assessment.signal in (
                        assessment.signal.STAGNATION,
                        assessment.signal.OSCILLATION,
                    ):
                        logger.info(
                            "Accepting %s at current quality "
                            "(monitor detected %s)",
                            file_path, assessment.signal.value,
                        )
                        return await self._accept_file(
                            context, code, scores, all_results,
                            round_num, generate_tests,
                            warnings=[
                                f"Accepted early: {assessment.signal.value} "
                                f"detected — {assessment.evidence}"
                            ],
                            also_affected=all_cross_file_affected,
                        )

            # Get error output for context
            error_output = "\n".join(
                r.diagnosis for r in all_results
                if (r.is_hard and not r.passed)
                or (not r.is_hard and r.score is not None and r.score < 0.4)
            )

            # Run diagnostician for deeper analysis on test/build failures
            diagnosis_text = top_action.issue_diagnosis
            cross_file_affected: list[str] = []

            if top_action.target_dimension in ("test_runner", "build_checker"):
                from adam.agents.diagnostician import ErrorDiagnostician

                diag_ctx = AgentContext(
                    project_id=context.project_id,
                    file_spec=context.file_spec,
                    error_output=error_output,
                    related_files=self._read_related_files(context),
                )
                diagnostician = ErrorDiagnostician(self._llm)
                diag_result = await diagnostician.execute(diag_ctx)

                if diag_result.success and diag_result.parsed:
                    diagnosis_text = diag_result.parsed.root_cause
                    cross_file_affected = [
                        f for f in diag_result.parsed.affected_files
                        if f != file_path
                    ]
                    if cross_file_affected:
                        logger.info(
                            "Diagnostician flagged cross-file issue: %s",
                            ", ".join(cross_file_affected),
                        )
                        all_cross_file_affected.extend(cross_file_affected)

            repair_spec = RepairSpec(
                instruction=top_action.instruction,
                diagnosis=diagnosis_text,
            )

            related = self._read_related_files(context)

            repair_ctx = AgentContext(
                project_id=context.project_id,
                file_spec=context.file_spec,
                module_spec=context.module_spec,
                tech_stack=context.tech_stack,
                conventions=context.conventions,
                error_output=error_output,
                related_files=related,
            )

            repairer = RepairAgent(self._llm, source_code=code, repair_spec=repair_spec)
            repair_result = await repairer.execute(repair_ctx)

            if repair_result.success and repair_result.raw_response.strip():
                code = repair_result.raw_response
                self._write_file(file_path, code)
                val_ctx.file_content = code
                logger.info("Repair applied to %s", file_path)
            else:
                logger.warning("Repair failed for %s: %s", file_path, repair_result.error)
                break

        # Max rounds exhausted
        logger.warning("Max repair rounds reached for %s", file_path)
        return await self._accept_file(
            context, code, best_scores, all_results,
            self._policy.max_repair_rounds, generate_tests,
            warnings=["Max repair rounds reached; accepted at best-effort quality."],
            also_affected=all_cross_file_affected,
        )

    async def _accept_file(
        self,
        context: AgentContext,
        code: str,
        scores: ScoreVectorData | None,
        validation_results: list[ValidationResult],
        repair_rounds: int,
        generate_tests: bool,
        warnings: list[str] | None = None,
        also_affected: list[str] | None = None,
    ) -> FileLoopResult:
        """Finalize an accepted file: generate tests, build result."""
        file_path = context.file_spec.get("path", "")
        test_path = ""

        if generate_tests and code:
            tp = await self._generate_and_write_tests(context, code)
            if tp:
                test_path = tp

        return FileLoopResult(
            file_path=file_path,
            accepted=True,
            code=code,
            test_path=test_path,
            also_affected=also_affected or [],
            scores=scores,
            validation_results=validation_results,
            repair_rounds=repair_rounds,
            warnings=warnings,
        )

    async def _generate_and_write_tests(
        self,
        context: AgentContext,
        source_code: str,
    ) -> str | None:
        """Generate tests for an accepted file, write them to disk.

        Returns the test file path if successful, None otherwise.
        """
        file_path = context.file_spec.get("path", "")
        test_path = _infer_test_path(file_path)
        if not test_path:
            return None

        logger.info("Generating tests: %s -> %s", file_path, test_path)

        writer = TestWriter(self._llm, source_code=source_code)
        result = await writer.execute(context)

        if result.success and result.raw_response.strip():
            self._write_file(test_path, result.raw_response)
            logger.info("Tests written: %s", test_path)
            return test_path

        logger.warning("Test generation failed for %s: %s", file_path, result.error)
        return None

    def _read_related_files(self, context: AgentContext) -> list[dict]:
        """Read dependency and related file contents from disk for repair context."""
        related: list[dict] = []
        root = Path(self._project_root)

        # Read dependency files
        for dep in context.dependency_interfaces:
            path = dep.get("path", "")
            if not path:
                continue
            full = root / path
            try:
                content = full.read_text(encoding="utf-8")
                if len(content) > 8000:
                    content = content[:8000] + "\n[truncated]"
                related.append({"path": path, "content": content})
            except (OSError, UnicodeDecodeError):
                continue

        # Read related files from same module
        for rf in context.related_files[:3]:
            path = rf.get("path", "")
            if not path or any(r["path"] == path for r in related):
                continue
            full = root / path
            try:
                content = full.read_text(encoding="utf-8")
                if len(content) > 8000:
                    content = content[:8000] + "\n[truncated]"
                related.append({"path": path, "content": content})
            except (OSError, UnicodeDecodeError):
                continue

        return related[:5]  # Cap at 5 files to manage context window

    def _write_file(self, file_path: str, content: str) -> None:
        """Write content to disk, creating directories as needed."""
        full_path = Path(self._project_root) / file_path
        full_path.parent.mkdir(parents=True, exist_ok=True)
        full_path.write_text(content, encoding="utf-8")


class FileLoopResult:
    """Result of processing a single file through the implementation loop."""

    def __init__(
        self,
        file_path: str,
        accepted: bool,
        code: str = "",
        test_path: str = "",
        also_affected: list[str] | None = None,
        scores: ScoreVectorData | None = None,
        validation_results: list[ValidationResult] | None = None,
        repair_rounds: int = 0,
        warnings: list[str] | None = None,
        error: str = "",
    ) -> None:
        self.file_path = file_path
        self.accepted = accepted
        self.test_path = test_path
        self.also_affected = also_affected or []
        self.code = code
        self.scores = scores
        self.validation_results = validation_results or []
        self.repair_rounds = repair_rounds
        self.warnings = warnings or []
        self.error = error

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.code.encode()).hexdigest()[:16]


def _infer_test_path(file_path: str) -> str | None:
    """Infer a test file path from a source file path.

    Examples:
        src/models.py -> tests/test_models.py
        lib/utils.js -> tests/utils.test.js
        app/main.rs -> tests/test_main.rs
        __init__.py -> None (skip)
        setup.py -> None (skip)
    """
    p = Path(file_path)

    # Skip files that don't need tests
    skip_names = {
        "__init__", "__main__", "setup", "conftest",
        "alembic", "migrations", "config",
    }
    if p.stem in skip_names:
        return None

    # Skip test files themselves
    if p.stem.startswith("test_") or p.stem.endswith("_test"):
        return None
    if ".test." in p.name or ".spec." in p.name:
        return None

    suffix = p.suffix

    # Python: src/foo/bar.py -> tests/test_bar.py
    if suffix == ".py":
        return f"tests/test_{p.stem}.py"

    # JavaScript/TypeScript: src/foo.ts -> tests/foo.test.ts
    if suffix in (".js", ".ts", ".jsx", ".tsx"):
        return f"tests/{p.stem}.test{suffix}"

    # Rust: src/foo.rs -> tests/test_foo.rs
    if suffix == ".rs":
        return f"tests/test_{p.stem}.rs"

    # Go: pkg/foo.go -> pkg/foo_test.go (same dir convention)
    if suffix == ".go":
        return str(p.parent / f"{p.stem}_test.go")

    # Default: tests/test_<stem><suffix>
    return f"tests/test_{p.stem}{suffix}"
