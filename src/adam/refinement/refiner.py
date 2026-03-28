"""Refinement loop — the core observe-fix-verify cycle.

Observes the project, picks the single highest-priority issue,
attempts a minimum fix, verifies the fix didn't make things worse,
and either commits or reverts. Repeats until healthy or budget exhausted.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from adam.agents.diagnostician import ErrorDiagnostician
from adam.agents.repair_agent import RepairAgent, RepairSpec
from adam.execution.runner import ShellRunner
from adam.git.manager import GitManager
from adam.llm.client import LLMClient
from adam.orchestrator.monitor import ProgressMonitor, RoundOutcome
from adam.refinement.observe import HealthLevel, Issue, Observation, Observer
from adam.refinement.snapshot import Snapshot, SnapshotManager
from adam.types import AgentContext, ModelTier

logger = logging.getLogger(__name__)


# Callback: (round_number, observation, issue_being_fixed) -> None
type OnRoundStart = Callable[[int, Observation, Issue | None], None]
# Callback: (round_number, improved, reverted) -> None
type OnRoundEnd = Callable[[int, bool, bool], None]


@dataclass
class RefinementResult:
    """Outcome of a refinement session."""
    rounds_completed: int = 0
    fixes_committed: int = 0
    fixes_reverted: int = 0
    initial_health: HealthLevel = HealthLevel.DOES_NOT_BUILD
    final_health: HealthLevel = HealthLevel.DOES_NOT_BUILD
    initial_issue_count: int = 0
    final_issue_count: int = 0
    issues_fixed: list[str] = field(default_factory=list)
    stopped_reason: str = ""

    @property
    def improved(self) -> bool:
        return (
            self.final_health > self.initial_health
            or self.final_issue_count < self.initial_issue_count
        )


@dataclass
class RefinementConfig:
    """Configuration for the refinement loop."""
    max_rounds: int = 20
    max_consecutive_reverts: int = 3
    build_cmd: str = ""
    run_cmd: str = ""
    test_cmd: str = ""
    # Use Opus for architectural issues, Sonnet for local fixes
    escalate_to_opus: bool = True
    # How many rounds at same health before giving up
    stagnation_limit: int = 5


class Refiner:
    """Runs the refinement loop on an existing codebase.

    The loop:
        1. Observe the project state
        2. If healthy, stop
        3. Pick the top issue
        4. Snapshot current state
        5. Diagnose and attempt a fix
        6. Re-observe
        7. If worse, revert to snapshot
        8. If better or neutral, commit the fix
        9. Repeat
    """

    def __init__(
        self,
        llm: LLMClient,
        project_root: str | Path,
        config: RefinementConfig | None = None,
        on_round_start: OnRoundStart | None = None,
        on_round_end: OnRoundEnd | None = None,
    ) -> None:
        self._llm = llm
        self._root = str(project_root)
        self._config = config or RefinementConfig()
        self._runner = ShellRunner()
        self._git = GitManager(project_root, self._runner)
        self._observer = Observer(project_root, self._runner)
        self._snapshots = SnapshotManager(self._git)
        self._on_round_start = on_round_start
        self._on_round_end = on_round_end

    async def refine(self) -> RefinementResult:
        """Run the refinement loop until healthy or budget exhausted."""
        result = RefinementResult()

        # Initial observation
        observation = await self._observe()
        result.initial_health = observation.health
        result.initial_issue_count = observation.issue_count

        if observation.health == HealthLevel.FULLY_HEALTHY:
            result.final_health = observation.health
            result.final_issue_count = 0
            result.stopped_reason = "already healthy"
            return result

        consecutive_reverts = 0
        stagnation_count = 0
        last_health = observation.health
        monitor = ProgressMonitor(stagnation_threshold=4)

        for round_num in range(1, self._config.max_rounds + 1):
            issue = observation.top_issue
            if issue is None:
                result.stopped_reason = "no issues found"
                break

            logger.info(
                "Refinement round %d: health=%s, issues=%d, fixing: %s",
                round_num, observation.health.name,
                observation.issue_count, issue.summary,
            )

            if self._on_round_start:
                self._on_round_start(round_num, observation, issue)

            # Snapshot before attempting fix
            snapshot = await self._snapshots.take(
                f"round {round_num}: {issue.summary[:60]}"
            )

            # Attempt the fix
            fixed_files = await self._attempt_fix(issue, observation)

            if not fixed_files:
                logger.warning("Round %d: fix produced no changes", round_num)
                consecutive_reverts += 1
                result.rounds_completed = round_num
                if self._on_round_end:
                    self._on_round_end(round_num, False, False)
                if consecutive_reverts >= self._config.max_consecutive_reverts:
                    result.stopped_reason = (
                        f"no progress after {consecutive_reverts} rounds"
                    )
                    break
                continue

            # Re-observe after fix
            new_observation = await self._observe()

            # Decide: keep or revert
            if new_observation.is_worse_than(observation):
                logger.info(
                    "Round %d: fix made things worse "
                    "(health %s→%s, issues %d→%d), reverting",
                    round_num,
                    observation.health.name, new_observation.health.name,
                    observation.issue_count, new_observation.issue_count,
                )
                await self._snapshots.revert(snapshot)
                consecutive_reverts += 1
                result.fixes_reverted += 1

                if self._on_round_end:
                    self._on_round_end(round_num, False, True)

                if consecutive_reverts >= self._config.max_consecutive_reverts:
                    result.stopped_reason = (
                        f"reverted {consecutive_reverts} consecutive fixes"
                    )
                    break
            else:
                # Fix helped or was neutral — commit it
                commit_msg = (
                    f"fix: {issue.summary[:80]}"
                )
                await self._snapshots.commit_fix(commit_msg, fixed_files)
                consecutive_reverts = 0
                result.fixes_committed += 1
                result.issues_fixed.append(issue.summary)
                observation = new_observation

                if self._on_round_end:
                    self._on_round_end(round_num, True, False)

                logger.info(
                    "Round %d: fix committed (health %s→%s, issues %d→%d)",
                    round_num,
                    last_health.name, observation.health.name,
                    result.initial_issue_count, observation.issue_count,
                )

            result.rounds_completed = round_num

            # Record for monitor
            monitor.record(RoundOutcome(
                round_number=round_num,
                error_count=observation.issue_count,
                files_affected=(
                    [issue.file_path] if issue.file_path else []
                ),
                action_taken=f"fix:{issue.summary[:50]}",
                result=(
                    "committed" if consecutive_reverts == 0
                    else "reverted"
                ),
            ))

            # Check for full health
            if observation.health == HealthLevel.FULLY_HEALTHY:
                result.stopped_reason = "fully healthy"
                break

            # Check monitor for trouble — escalate to supervisor
            assessment = monitor.assess()
            if assessment.needs_supervisor:
                directive = await self._consult_supervisor(
                    assessment, observation, issue,
                )
                if directive and directive.action in (
                    "accept_imperfection", "freeze", "abort",
                ):
                    result.stopped_reason = (
                        f"supervisor: {directive.action} — "
                        f"{directive.reasoning[:80]}"
                    )
                    break
                elif directive and directive.action == "skip_and_return":
                    # Skip this issue, try the next one
                    logger.info(
                        "Supervisor: skipping current issue, "
                        "will try next"
                    )
                    # Re-observe to get fresh issues
                    observation = await self._observe()
                    continue

            # Check for stagnation (fallback if supervisor not available)
            if observation.health == last_health:
                stagnation_count += 1
                if stagnation_count >= self._config.stagnation_limit:
                    result.stopped_reason = (
                        f"health stuck at {observation.health.name} "
                        f"for {stagnation_count} rounds"
                    )
                    break
            else:
                stagnation_count = 0
                last_health = observation.health

        else:
            result.stopped_reason = f"reached max rounds ({self._config.max_rounds})"

        result.final_health = observation.health
        result.final_issue_count = observation.issue_count
        return result

    async def _consult_supervisor(
        self,
        assessment: Any,
        observation: Observation,
        issue: Issue | None,
    ) -> Any:
        """Escalate to the Opus supervisor for strategic guidance."""
        from adam.agents.supervisor import Supervisor, SupervisorResponse

        supervisor = Supervisor(self._llm)
        monitor_summary = {}

        # Try to get monitor summary if we have one
        # (the monitor is a local var in refine(), pass via assessment)
        error_text = ""
        if issue:
            error_text = issue.error_output or issue.summary
        elif observation.build_output:
            error_text = observation.build_output[:2000]

        from adam.cli.display import thinking
        async with thinking("Reflecting on approach"):
            result = await supervisor.execute(AgentContext(
                error_output=error_text,
                extra={
                    "trouble_signal": assessment.signal.value,
                    "signal_evidence": assessment.evidence,
                    "monitor_summary": {
                        "total_rounds": assessment.rounds_in_trouble,
                        "error_trajectory": assessment.trajectory,
                        "current_error_count": observation.issue_count,
                    },
                    "current_file": issue.file_path if issue else "",
                    "current_error": error_text[:2000],
                    "phase": "refinement",
                },
            ))

        if result.success and isinstance(result.parsed, SupervisorResponse):
            logger.info(
                "Supervisor: %s — %s",
                result.parsed.directive.action,
                result.parsed.directive.reasoning[:100],
            )
            return result.parsed.directive

        return None

    async def _observe(self) -> Observation:
        """Take a full observation of the project."""
        return await self._observer.observe(
            build_cmd=self._config.build_cmd,
            run_cmd=self._config.run_cmd,
            test_cmd=self._config.test_cmd,
        )

    async def _attempt_fix(
        self,
        issue: Issue,
        observation: Observation,
    ) -> list[str]:
        """Attempt to fix a single issue. Returns list of modified file paths.

        For build errors with a known file, reads the file, diagnoses,
        and applies a targeted repair. For broader issues, diagnoses first
        to identify affected files.
        """
        if issue.file_path:
            return await self._fix_known_file(issue, observation)
        return await self._fix_unknown_location(issue, observation)

    async def _fix_known_file(
        self,
        issue: Issue,
        observation: Observation,
    ) -> list[str]:
        """Fix an issue in a known file."""
        file_path = Path(self._root) / issue.file_path
        if not file_path.is_file():
            logger.warning("Issue file not found: %s", issue.file_path)
            return []

        source_code = file_path.read_text(encoding="utf-8")

        # Read imports to give repair agent context
        related_files = self._read_imports(source_code, issue.file_path)

        # Determine tier — escalate to Opus for architectural issues
        use_opus = (
            self._config.escalate_to_opus
            and issue.level <= HealthLevel.BUILDS_BUT_CRASHES
            and len(related_files) > 2
        )

        # Diagnose
        diagnosis = await self._diagnose(issue, related_files)

        # Repair
        repair_spec = RepairSpec(
            instruction=diagnosis.get("proposed_fix", issue.summary),
            diagnosis=diagnosis.get("root_cause", issue.summary),
            preserve_constraints=[
                "Do not change the public API or exports",
                "Do not add new dependencies",
            ],
        )

        agent = RepairAgent(
            llm=self._llm,
            source_code=source_code,
            repair_spec=repair_spec,
        )
        if use_opus:
            agent.model_tier = ModelTier.OPUS

        context = AgentContext(
            error_output=issue.error_output or observation.build_output,
            related_files=related_files,
            file_spec={"path": issue.file_path},
        )

        result = await agent.execute(context)
        if not result.success or not result.raw_response:
            logger.warning("Repair agent failed: %s", result.error)
            return []

        # Extract the fixed code from the response
        fixed_code = self._extract_code(result.raw_response)
        if not fixed_code or fixed_code.strip() == source_code.strip():
            logger.info("Repair produced no changes")
            return []

        # Write the fix
        file_path.write_text(fixed_code, encoding="utf-8")
        logger.info("Wrote fix to %s", issue.file_path)

        # If the diagnosis identified other affected files, try those too
        affected = diagnosis.get("affected_files", [])
        modified = [issue.file_path]

        for other_path in affected:
            if other_path == issue.file_path:
                continue
            other_file = Path(self._root) / other_path
            if not other_file.is_file():
                continue

            other_source = other_file.read_text(encoding="utf-8")
            other_related = self._read_imports(other_source, other_path)

            other_agent = RepairAgent(
                llm=self._llm,
                source_code=other_source,
                repair_spec=RepairSpec(
                    instruction=(
                        f"This file is affected by a fix in {issue.file_path}. "
                        f"Root cause: {diagnosis.get('root_cause', '')}. "
                        f"Ensure this file is consistent with the fix."
                    ),
                    diagnosis=diagnosis.get("root_cause", ""),
                ),
            )

            other_ctx = AgentContext(
                error_output=issue.error_output,
                related_files=other_related,
                file_spec={"path": other_path},
            )

            other_result = await other_agent.execute(other_ctx)
            if other_result.success and other_result.raw_response:
                other_fixed = self._extract_code(other_result.raw_response)
                if other_fixed and other_fixed.strip() != other_source.strip():
                    other_file.write_text(other_fixed, encoding="utf-8")
                    modified.append(other_path)
                    logger.info("Also fixed: %s", other_path)

        return modified

    async def _fix_unknown_location(
        self,
        issue: Issue,
        observation: Observation,
    ) -> list[str]:
        """Fix an issue where we don't know which file is responsible."""
        # Diagnose first to find affected files
        diagnosis = await self._diagnose(issue, [])

        affected = diagnosis.get("affected_files", [])
        if not affected:
            logger.warning(
                "Diagnosis found no affected files for: %s", issue.summary,
            )
            return []

        # Fix the first identified file
        file_issue = Issue(
            level=issue.level,
            summary=issue.summary,
            file_path=affected[0],
            error_output=issue.error_output,
        )
        return await self._fix_known_file(file_issue, observation)

    async def _diagnose(
        self,
        issue: Issue,
        related_files: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Run the diagnostician on an issue."""
        diagnostician = ErrorDiagnostician(self._llm)

        context = AgentContext(
            error_output=issue.error_output or issue.summary,
            file_spec={"path": issue.file_path},
            related_files=related_files,
        )

        result = await diagnostician.execute(context)
        if result.success and result.parsed:
            return result.parsed.model_dump()

        # Fallback: return what we know from the issue itself
        return {
            "root_cause": issue.summary,
            "affected_files": [issue.file_path] if issue.file_path else [],
            "proposed_fix": f"Fix: {issue.summary}",
            "confidence": 0.3,
        }

    def _read_imports(
        self, source: str, file_path: str,
    ) -> list[dict[str, Any]]:
        """Read imported files from disk to give repair agent context."""
        import re

        related: list[dict[str, Any]] = []
        root = Path(self._root)

        # TypeScript/JavaScript imports
        for match in re.finditer(
            r"""(?:import|from)\s+['"](\.[^'"]+)['"]""", source,
        ):
            import_path = match.group(1)
            # Resolve relative to the file's directory
            file_dir = (root / file_path).parent
            for ext in ["", ".ts", ".tsx", ".js", ".jsx", "/index.ts", "/index.js"]:
                candidate = file_dir / (import_path + ext)
                if candidate.is_file():
                    try:
                        content = candidate.read_text(encoding="utf-8")
                        rel = str(candidate.relative_to(root))
                        related.append({"path": rel, "content": content})
                    except Exception:
                        pass
                    break

        # Python imports — from adam.foo.bar import X
        for match in re.finditer(
            r"from\s+(adam\.[^\s]+)\s+import", source,
        ):
            module = match.group(1)
            module_path = root / "src" / module.replace(".", "/") / "__init__.py"
            if not module_path.is_file():
                module_path = root / "src" / (module.replace(".", "/") + ".py")
            if module_path.is_file():
                try:
                    content = module_path.read_text(encoding="utf-8")
                    rel = str(module_path.relative_to(root))
                    related.append({"path": rel, "content": content})
                except Exception:
                    pass

        # Deduplicate and limit
        seen: set[str] = set()
        unique: list[dict[str, Any]] = []
        for rf in related:
            if rf["path"] not in seen:
                seen.add(rf["path"])
                unique.append(rf)
            if len(unique) >= 5:
                break

        return unique

    def _extract_code(self, response: str) -> str:
        """Extract source code from an agent response.

        The repair agent should return just code, but sometimes wraps
        it in markdown fences.
        """
        text = response.strip()

        # Strip markdown fences
        if text.startswith("```"):
            lines = text.split("\n")
            # Remove first line (```lang) and last line (```)
            if lines[-1].strip() == "```":
                lines = lines[1:-1]
            else:
                lines = lines[1:]
            text = "\n".join(lines)

        return text
