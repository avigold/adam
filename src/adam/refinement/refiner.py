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
        self._observer = Observer(project_root, self._runner, llm=llm)
        self._snapshots = SnapshotManager(self._git)
        self._on_round_start = on_round_start
        self._on_round_end = on_round_end

    async def refine(self) -> RefinementResult:
        """Run the refinement loop until healthy or budget exhausted.

        This method must never crash. All exceptions are caught, logged,
        and result in a graceful return with whatever progress was made.
        """
        result = RefinementResult()

        try:
            return await self._refine_inner(result)
        except Exception as e:
            logger.exception("Refinement loop crashed: %s", e)
            result.stopped_reason = f"crashed: {e}"
            return result

    async def _refine_inner(self, result: RefinementResult) -> RefinementResult:
        """Inner refinement loop — separated so refine() can catch crashes.

        Primary mechanism: the Opus FixAgent reads errors and code
        together and produces surgical edits in a single call. This
        is the Claude Code model: read → think → edit.

        Fallback: the old one-at-a-time loop with separate analysis
        and repair agents, used only when the FixAgent can't solve it.
        """
        # Initial observation
        observation = await self._observe()
        result.initial_health = observation.health
        result.initial_issue_count = observation.issue_count

        if observation.health == HealthLevel.FULLY_HEALTHY:
            result.final_health = observation.health
            result.final_issue_count = 0
            result.stopped_reason = "already healthy"
            return result

        # ── Primary: tool-use fix agent (Opus with read/edit/run tools) ──
        tool_fix_result = await self._tool_fix(observation, result)
        if tool_fix_result:
            observation = await self._observe()
            if observation.health == HealthLevel.FULLY_HEALTHY:
                result.final_health = observation.health
                result.final_issue_count = 0
                result.stopped_reason = "tool fix resolved all issues"
                return result

        # ── Fallback: one-at-a-time loop ──
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

            # If the observation itself ran setup commands (npm install etc.),
            # that IS the fix for this round — don't try to edit files too.
            # Just re-observe to see the new state.
            if observation.setup_commands_ran:
                logger.info(
                    "Round %d: setup commands ran during observation, "
                    "re-observing to see new state",
                    round_num,
                )
                new_observation = await self._observe()
                # Setup commands are always progress — don't revert
                consecutive_reverts = 0
                result.fixes_committed += 1
                result.issues_fixed.append(
                    "Environment setup (installed dependencies)"
                )
                observation = new_observation
                result.rounds_completed = round_num

                if self._on_round_end:
                    self._on_round_end(round_num, True, False)

                if observation.health == HealthLevel.FULLY_HEALTHY:
                    result.stopped_reason = "fully healthy"
                    break
                continue

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
                try:
                    directive = await self._consult_supervisor(
                        assessment, observation, issue,
                    )
                except Exception as e:
                    logger.warning("Supervisor call failed: %s", e)
                    directive = None

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

    async def _tool_fix(
        self,
        observation: Observation,
        result: RefinementResult,
    ) -> bool:
        """Run the tool-use fix agent — Opus with read/edit/run tools.

        This is the primary fix mechanism. The model explores the
        project and fixes errors autonomously, same as Claude Code.
        Returns True if any fixes were applied.
        """
        from adam.refinement.tool_fix import ToolFixAgent

        error_text = (
            observation.build_output
            or observation.test_output
            or "\n".join(i.error_output for i in observation.issues[:10])
        )

        if not error_text.strip():
            return False

        # Snapshot before the agent makes changes
        snapshot = await self._snapshots.take("tool fix")

        agent = ToolFixAgent(
            llm=self._llm,
            project_root=self._root,
            runner=self._runner,
        )

        from adam.cli.display import thinking
        async with thinking("Working through the problem"):
            fix_result = await agent.fix(
                build_cmd=self._config.build_cmd,
                build_output=error_text[:6000],
                test_cmd=self._config.test_cmd,
                test_output=observation.test_output[:4000] if observation.test_output else "",
            )

        logger.info(
            "Tool fix: %s (%d turns, %d files, %d+%d tokens)",
            fix_result.summary[:100],
            fix_result.turns,
            len(fix_result.files_modified),
            fix_result.total_input_tokens,
            fix_result.total_output_tokens,
        )

        if fix_result.error:
            logger.warning("Tool fix error: %s", fix_result.error)

        if not fix_result.files_modified:
            logger.info("Tool fix made no changes")
            return False

        # Re-observe to check if things improved
        new_observation = await self._observe()

        if new_observation.is_worse_than(observation):
            logger.info(
                "Tool fix made things worse (%s→%s, %d→%d) — reverting",
                observation.health.name, new_observation.health.name,
                observation.issue_count, new_observation.issue_count,
            )
            await self._snapshots.revert(snapshot)
            return False

        # Commit the changes
        await self._snapshots.commit_fix(
            f"tool fix: {fix_result.summary[:60]}",
            fix_result.files_modified,
        )
        result.fixes_committed += len(fix_result.files_modified)
        result.issues_fixed.append(fix_result.summary[:100])

        if self._on_round_end:
            self._on_round_end(0, True, False)

        logger.info(
            "Tool fix committed: %d files, health %s→%s, issues %d→%d",
            len(fix_result.files_modified),
            observation.health.name, new_observation.health.name,
            observation.issue_count, new_observation.issue_count,
        )
        return True

    async def _direct_fix(
        self,
        observation: Observation,
        result: RefinementResult,
    ) -> Observation:
        """Opus reads the errors and code, produces edits directly.

        One call. No handoff. The same reasoning chain that
        understands the error also produces the fix.
        """
        from adam.agents.fix_agent import FixAgent, FixResponse

        # Collect affected file contents from the error traceback
        affected_files = self._collect_affected_files(observation)
        if not affected_files:
            logger.info("No affected files identified — skipping direct fix")
            return observation

        file_listing = self._observer._get_file_listing()
        env_info = self._observer._get_environment_info()

        error_text = (
            observation.build_output
            or observation.test_output
            or "\n".join(i.error_output for i in observation.issues[:10])
        )

        agent = FixAgent(self._llm)

        from adam.cli.display import thinking
        async with thinking("Working through the problem"):
            agent_result = await agent.execute(AgentContext(
                error_output=error_text[:8000],
                extra={
                    "build_command": self._config.build_cmd,
                    "file_listing": file_listing,
                    "environment_info": env_info,
                    "affected_files": affected_files,
                },
            ))

        if not agent_result.success or not isinstance(
            agent_result.parsed, FixResponse
        ):
            logger.warning("FixAgent failed: %s", agent_result.error)
            return observation

        fix = agent_result.parsed
        logger.info(
            "FixAgent: %d edits, %d creates, %d commands "
            "(confidence=%.2f)",
            len(fix.edits), len(fix.creates), len(fix.commands),
            fix.confidence,
        )
        logger.info("Assessment: %s", fix.assessment[:200])

        if not fix.edits and not fix.creates and not fix.commands:
            logger.info("FixAgent produced no actions")
            return observation

        # Snapshot before applying
        snapshot = await self._snapshots.take("direct fix")

        # Apply commands first (npm install, etc.)
        for cmd in fix.commands:
            if not cmd.command:
                continue
            cmd_lower = cmd.command.lower().strip()
            if any(d in cmd_lower for d in ("rm -rf", "rm -r", "rmdir")):
                logger.warning("Blocked destructive command: %s", cmd.command)
                continue
            cwd = self._root
            if cmd.working_directory:
                cwd = str(Path(self._root) / cmd.working_directory)
            logger.info("Running: %s (in %s)", cmd.command, cwd)
            cmd_result = await self._runner.run(
                cmd.command, cwd=cwd, timeout=120,
            )
            if cmd_result.success:
                logger.info("Command succeeded: %s", cmd.command)
            else:
                logger.warning(
                    "Command failed: %s — %s",
                    cmd.command, cmd_result.output[:200],
                )

        # Apply edits
        applied_edits: list[tuple[str, str, str]] = []  # (file, find, replace)
        for edit in fix.edits:
            if not edit.file or not edit.find:
                continue
            file_path, resolved = self._resolve_file_path(edit.file)
            if not file_path.is_file():
                logger.warning("Edit target not found: %s", edit.file)
                continue

            content = file_path.read_text(encoding="utf-8")
            if edit.find not in content:
                # Try fuzzy match — strip leading/trailing whitespace
                # from each line and try again
                stripped_find = edit.find.strip()
                if stripped_find in content:
                    edit.find = stripped_find
                else:
                    logger.warning(
                        "Find string not found in %s: %s",
                        resolved, edit.find[:80],
                    )
                    continue

            # Verify uniqueness
            count = content.count(edit.find)
            if count > 1:
                logger.warning(
                    "Find string appears %d times in %s — applying all",
                    count, resolved,
                )

            new_content = content.replace(edit.find, edit.replace)
            file_path.write_text(new_content, encoding="utf-8")
            applied_edits.append((resolved, edit.find, edit.replace))
            logger.info("Applied edit to %s", resolved)

        # Apply creates
        for create in fix.creates:
            if not create.file or not create.content:
                continue
            file_path = Path(self._root) / create.file
            file_path.parent.mkdir(parents=True, exist_ok=True)
            file_path.write_text(create.content, encoding="utf-8")
            logger.info("Created file: %s", create.file)

        if not applied_edits and not fix.creates:
            logger.info("No edits applied")
            return observation

        # Re-observe
        new_observation = await self._observe()

        if new_observation.is_worse_than(observation):
            logger.info(
                "Direct fix made things worse (%s→%s, %d→%d) — reverting",
                observation.health.name, new_observation.health.name,
                observation.issue_count, new_observation.issue_count,
            )
            await self._snapshots.revert(snapshot)
            return observation

        # Commit
        modified = [e[0] for e in applied_edits] + [c.file for c in fix.creates]
        await self._snapshots.commit_fix(
            f"direct fix: {fix.assessment[:60]}",
            modified,
        )
        result.fixes_committed += len(applied_edits) + len(fix.creates)
        result.issues_fixed.append(fix.assessment[:100])

        if self._on_round_end:
            self._on_round_end(0, True, False)

        logger.info(
            "Direct fix committed: %d edits, health %s→%s, issues %d→%d",
            len(applied_edits),
            observation.health.name, new_observation.health.name,
            observation.issue_count, new_observation.issue_count,
        )
        return new_observation

    def _collect_affected_files(
        self, observation: Observation,
    ) -> list[dict[str, str]]:
        """Read the source files mentioned in errors.

        Follows the error traceback to find affected files, then
        reads their imports too. Returns file contents for the
        FixAgent to reason about.
        """
        # Collect file paths from issues
        paths: set[str] = set()
        for issue in observation.issues:
            if issue.file_path:
                paths.add(issue.file_path)
            for rp in issue.related_file_paths:
                paths.add(rp)

        # Also parse the raw build output for Python tracebacks
        import re
        raw = observation.build_output or observation.test_output or ""
        for match in re.finditer(r'File "([^"]+)"', raw):
            fpath = match.group(1)
            # Make relative to project root
            if fpath.startswith(self._root):
                fpath = fpath[len(self._root):].lstrip("/")
            if not fpath.startswith("/"):
                paths.add(fpath)

        # TypeScript errors
        for match in re.finditer(
            r"([^\s:(]+\.(?:ts|tsx|js|jsx))\(?\d", raw,
        ):
            paths.add(match.group(1))

        # Resolve and read files
        files: list[dict[str, str]] = []
        seen: set[str] = set()
        total_chars = 0
        max_total = 30000  # Cap total content

        for path in paths:
            file_path, resolved = self._resolve_file_path(path)
            if resolved in seen or not file_path.is_file():
                continue
            seen.add(resolved)
            try:
                content = file_path.read_text(encoding="utf-8")
                if total_chars + len(content) > max_total:
                    if len(content) > 3000:
                        content = content[:3000] + "\n[truncated]"
                total_chars += len(content)
                # Detect language from extension
                ext = file_path.suffix
                lang_map = {
                    ".py": "python", ".ts": "typescript", ".tsx": "tsx",
                    ".js": "javascript", ".jsx": "jsx", ".rs": "rust",
                    ".go": "go",
                }
                files.append({
                    "path": resolved,
                    "content": content,
                    "language": lang_map.get(ext, ""),
                })
            except (OSError, UnicodeDecodeError):
                pass

            # Also read this file's imports
            if total_chars < max_total:
                for imp in self._read_imports(
                    files[-1]["content"], resolved,
                ):
                    if imp["path"] not in seen:
                        seen.add(imp["path"])
                        total_chars += len(imp.get("content", ""))
                        ext = Path(imp["path"]).suffix
                        files.append({
                            "path": imp["path"],
                            "content": imp.get("content", ""),
                            "language": lang_map.get(ext, ""),
                        })
                        if total_chars > max_total:
                            break

        return files

    async def _try_batch_fix(
        self,
        observation: Observation,
        result: RefinementResult,
    ) -> Observation:
        """Attempt to fix all issues in one pass if confidence is high.

        The build analyser returns batch_fix_confidence. If >= 0.7,
        we fix every identified file in one go. If the build is worse
        after, revert and return the original observation — the caller
        falls through to the one-at-a-time loop.
        """
        if not observation.issues:
            return observation

        # Run the build analyser to get confidence
        analysis = await self._get_batch_analysis(observation)
        if analysis is None:
            return observation

        confidence = getattr(analysis, "batch_fix_confidence", 0.0)
        if confidence < 0.7:
            logger.info(
                "Batch fix confidence %.2f < 0.7 — using one-at-a-time",
                confidence,
            )
            return observation

        logger.info(
            "Batch fix confidence %.2f — attempting to fix %d errors "
            "in one pass",
            confidence, len(analysis.errors),
        )

        # Snapshot before batch
        snapshot = await self._snapshots.take("batch fix attempt")

        # Fix every file the analyser identified
        fixed_files: list[str] = []
        for error in analysis.errors:
            if not error.file_path or not error.suggested_fix:
                continue

            file_path, resolved = self._resolve_file_path(error.file_path)
            if not file_path.is_file():
                continue

            source = file_path.read_text(encoding="utf-8")

            # Only include files this specific file imports — not
            # every file from the analysis. Keeps context tight.
            related = self._read_imports(source, resolved)

            # Cap total related content to ~8k chars
            total_chars = 0
            capped: list[dict] = []
            for rf in related:
                content_len = len(rf.get("content", ""))
                if total_chars + content_len > 8000:
                    break
                capped.append(rf)
                total_chars += content_len
            related = capped

            repair_spec = RepairSpec(
                instruction=error.suggested_fix,
                diagnosis=error.root_cause or error.summary,
                preserve_constraints=[
                    "Match the interfaces of the imported/referenced files "
                    "shown in Related Files — those are the source of truth",
                ],
            )

            agent = RepairAgent(
                llm=self._llm,
                source_code=source,
                repair_spec=repair_spec,
            )
            ctx = AgentContext(
                error_output=error.summary,
                related_files=related,
                file_spec={"path": resolved},
            )

            from adam.cli.display import thinking
            async with thinking(f"Batch fixing {resolved}"):
                repair_result = await agent.execute(ctx)

            if repair_result.success and repair_result.raw_response:
                fixed_code = self._extract_code(repair_result.raw_response)
                if fixed_code and fixed_code.strip() != source.strip():
                    file_path.write_text(fixed_code, encoding="utf-8")
                    fixed_files.append(resolved)
                    logger.info("Batch fixed: %s", resolved)

        if not fixed_files:
            logger.info("Batch fix produced no changes")
            return observation

        # Re-observe
        new_observation = await self._observe()

        if new_observation.is_worse_than(observation):
            logger.info(
                "Batch fix made things worse (%s→%s, %d→%d issues) "
                "— reverting to one-at-a-time",
                observation.health.name, new_observation.health.name,
                observation.issue_count, new_observation.issue_count,
            )
            await self._snapshots.revert(snapshot)
            return observation

        # Batch fix helped — commit
        await self._snapshots.commit_fix(
            f"batch fix: {len(fixed_files)} files",
            fixed_files,
        )
        result.fixes_committed += len(fixed_files)
        result.issues_fixed.append(
            f"Batch fix: {', '.join(f[:40] for f in fixed_files[:5])}"
        )
        logger.info(
            "Batch fix committed: %d files, health %s→%s, issues %d→%d",
            len(fixed_files),
            observation.health.name, new_observation.health.name,
            observation.issue_count, new_observation.issue_count,
        )
        return new_observation

    async def _get_batch_analysis(
        self, observation: Observation,
    ) -> Any:
        """Get the build analysis with confidence score."""
        try:
            from adam.agents.build_analyser import BuildAnalyser, BuildAnalysis

            analyser = BuildAnalyser(self._llm)
            error_text = (
                observation.build_output
                or observation.test_output
                or "\n".join(i.error_output for i in observation.issues[:10])
            )

            from adam.cli.display import thinking
            # Pass file listing and env info so Opus has full context
            file_listing = self._observer._get_file_listing()
            env_info = self._observer._get_environment_info()

            async with thinking("Assessing batch fix feasibility"):
                result = await analyser.execute(AgentContext(
                    error_output=error_text[:8000],
                    extra={
                        "build_command": self._config.build_cmd,
                        "file_listing": file_listing,
                        "environment_info": env_info,
                    },
                ))

            if result.success and isinstance(result.parsed, BuildAnalysis):
                return result.parsed
        except Exception as e:
            logger.warning("Batch analysis failed: %s", e)
        return None

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
                        "files_with_most_attempts": [],
                        "recent_actions": [],
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

    def _resolve_file_path(self, reported_path: str) -> tuple[Path, str]:
        """Resolve a file path from build output to an actual file on disk.

        Build commands like 'cd site && npm run build' produce errors
        with paths relative to 'site/', not the project root. This
        tries the path as-is first, then prefixes with subdirectories
        extracted from the build command.
        """
        # Try as-is
        candidate = Path(self._root) / reported_path
        if candidate.is_file():
            return candidate, reported_path

        # Extract subdirectories from build command (cd site && ...)
        import re
        for cmd in (self._config.build_cmd, self._config.test_cmd):
            match = re.search(r"cd\s+(\S+)\s*&&", cmd)
            if match:
                subdir = match.group(1)
                candidate = Path(self._root) / subdir / reported_path
                if candidate.is_file():
                    resolved = f"{subdir}/{reported_path}"
                    logger.info(
                        "Resolved path: %s → %s", reported_path, resolved,
                    )
                    return candidate, resolved

        # Try common frontend subdirectories
        for subdir in ("site", "frontend", "client", "web", "ui", "app"):
            candidate = Path(self._root) / subdir / reported_path
            if candidate.is_file():
                resolved = f"{subdir}/{reported_path}"
                logger.info(
                    "Resolved path: %s → %s", reported_path, resolved,
                )
                return candidate, resolved

        return Path(self._root) / reported_path, reported_path

    async def _fix_known_file(
        self,
        issue: Issue,
        observation: Observation,
    ) -> list[str]:
        """Fix an issue in a known file.

        Uses the Opus analysis directly — the issue already carries
        the suggested fix and related file paths from the build
        analyser. No re-diagnosis needed. The repair agent gets:
        1. The source file to fix
        2. The precise instruction from Opus
        3. The actual source code of referenced files (imports, callees)
        """
        file_path, resolved_path = self._resolve_file_path(issue.file_path)
        if not file_path.is_file():
            logger.warning("Issue file not found: %s", issue.file_path)
            return []
        issue.file_path = resolved_path

        source_code = file_path.read_text(encoding="utf-8")

        # Build context: imports + files the Opus analysis said are related
        # Cap total related content to ~8k chars to avoid bloat
        related_files = self._read_imports(source_code, resolved_path)

        # Add files from the Opus analysis that aren't already in related
        seen_paths = {r["path"] for r in related_files}
        for rel_path in issue.related_file_paths:
            if rel_path in seen_paths:
                continue
            resolved_rel, resolved_rel_path = self._resolve_file_path(rel_path)
            if resolved_rel.is_file():
                try:
                    content = resolved_rel.read_text(encoding="utf-8")
                    if len(content) > 4000:
                        content = content[:4000] + "\n[truncated]"
                    related_files.append({
                        "path": resolved_rel_path,
                        "content": content,
                    })
                    seen_paths.add(resolved_rel_path)
                except (OSError, UnicodeDecodeError):
                    pass

        # Cap total related content
        total_chars = 0
        capped: list[dict] = []
        for rf in related_files:
            content_len = len(rf.get("content", ""))
            if total_chars + content_len > 8000:
                break
            capped.append(rf)
            total_chars += content_len
        related_files = capped

        # Use the Opus suggested fix directly — don't re-diagnose
        instruction = issue.suggested_fix or issue.summary
        diagnosis_text = issue.error_output or issue.summary

        repair_spec = RepairSpec(
            instruction=instruction,
            diagnosis=diagnosis_text,
            preserve_constraints=[
                "Do not change the public API or exports unless the "
                "fix specifically requires it",
                "Do not add new dependencies",
                "Match the interfaces of the imported/referenced files "
                "shown in Related Files — those are the source of truth",
            ],
        )

        agent = RepairAgent(
            llm=self._llm,
            source_code=source_code,
            repair_spec=repair_spec,
        )

        context = AgentContext(
            error_output=issue.error_output or observation.build_output,
            related_files=related_files,
            file_spec={"path": resolved_path},
        )

        from adam.cli.display import thinking
        async with thinking(f"Repairing {resolved_path}"):
            result = await agent.execute(context)

        if not result.success or not result.raw_response:
            logger.warning("Repair agent failed: %s", result.error)
            return []

        fixed_code = self._extract_code(result.raw_response)
        if not fixed_code or fixed_code.strip() == source_code.strip():
            logger.info("Repair produced no changes")
            return []

        file_path.write_text(fixed_code, encoding="utf-8")
        logger.info("Wrote fix to %s", resolved_path)
        return [resolved_path]

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

        The repair agent should return just code, but sometimes:
        - Wraps it in markdown fences
        - Prepends a prose explanation before the code
        - Returns commentary instead of code entirely

        We detect prose by checking if the response starts with
        natural language rather than code syntax.
        """
        text = response.strip()

        # If the response contains a fenced code block, extract it
        if "```" in text:
            import re
            # Find the largest fenced block
            blocks = re.findall(
                r"```(?:\w+)?\n(.*?)```", text, re.DOTALL,
            )
            if blocks:
                # Use the longest block (likely the full file)
                text = max(blocks, key=len).strip()
                return text

        # Detect prose preamble: if first line doesn't look like code,
        # find where the code starts
        lines = text.split("\n")
        if lines and self._looks_like_prose(lines[0]):
            # Find the first line that looks like code
            for i, line in enumerate(lines):
                if not self._looks_like_prose(line) and line.strip():
                    text = "\n".join(lines[i:])
                    break
            else:
                # Entire response is prose — no code found
                return ""

        return text

    @staticmethod
    def _looks_like_prose(line: str) -> bool:
        """Heuristic: does this line look like natural language, not code?"""
        stripped = line.strip()
        if not stripped:
            return False
        # Code typically starts with these
        code_starts = (
            "import ", "from ", "export ", "const ", "let ", "var ",
            "function ", "class ", "interface ", "type ", "enum ",
            "def ", "async ", "await ", "return ", "if ", "for ",
            "while ", "try ", "catch ", "switch ", "{", "}", "//",
            "/*", "#!", "#!/", "@", "<", "package ", "use ",
            "pub ", "fn ", "struct ", "impl ", "mod ", "crate ",
        )
        if stripped.startswith(code_starts):
            return False
        # Prose typically starts with capital letter + has spaces
        # and doesn't contain common code characters at the start
        if (
            stripped[0].isupper()
            and " " in stripped[:30]
            and not stripped.startswith(("I ", ))
            and len(stripped) > 40
        ):
            return True
        return False
