"""Progress monitor — cheap mechanical detection of thrashing.

Tracks error counts, repair rounds, and trajectory patterns.
Detects stagnation, oscillation, regression, and diminishing returns
without any LLM calls. When a trouble signal fires, the caller
should escalate to the Opus supervisor for strategic reflection.
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class TroubleSignal(str, Enum):
    """Signals that the monitor can raise."""
    NONE = "none"  # Everything looks fine
    STAGNATION = "stagnation"  # Error count unchanged for N rounds
    OSCILLATION = "oscillation"  # Error count ping-ponging
    REGRESSION = "regression"  # Error count increasing
    DIMINISHING_RETURNS = "diminishing_returns"  # Each round fixing less
    REPEATED_FAILURE = "repeated_failure"  # Same file/error failing repeatedly
    BUDGET_WARNING = "budget_warning"  # Too many rounds spent on one thing


@dataclass
class RoundOutcome:
    """Record of what happened in one repair round."""
    round_number: int
    error_count: int
    files_affected: list[str] = field(default_factory=list)
    errors_fixed: int = 0
    errors_introduced: int = 0
    action_taken: str = ""  # What was attempted
    result: str = ""  # What happened


@dataclass
class MonitorAssessment:
    """The monitor's assessment of the current trajectory."""
    signal: TroubleSignal
    confidence: float  # 0-1, how sure the monitor is
    evidence: str  # Human-readable explanation
    rounds_in_trouble: int = 0  # How many rounds this signal has persisted
    trajectory: list[int] = field(default_factory=list)  # Recent error counts

    @property
    def needs_supervisor(self) -> bool:
        """Whether this assessment warrants escalating to Opus."""
        return self.signal != TroubleSignal.NONE and self.confidence >= 0.6


class ProgressMonitor:
    """Tracks progress and detects when things aren't working.

    Maintains a rolling window of outcomes and applies heuristics
    to detect trouble patterns. No LLM calls — pure arithmetic.

    Usage:
        monitor = ProgressMonitor()
        monitor.record(RoundOutcome(...))
        assessment = monitor.assess()
        if assessment.needs_supervisor:
            # Escalate to Opus
    """

    def __init__(
        self,
        stagnation_threshold: int = 3,
        oscillation_window: int = 6,
        max_rounds_per_file: int = 5,
    ) -> None:
        self._history: deque[RoundOutcome] = deque(maxlen=50)
        self._stagnation_threshold = stagnation_threshold
        self._oscillation_window = oscillation_window
        self._max_rounds_per_file = max_rounds_per_file
        self._file_attempt_counts: dict[str, int] = {}
        self._last_signal = TroubleSignal.NONE
        self._signal_persistence = 0

    def record(self, outcome: RoundOutcome) -> None:
        """Record the outcome of a round."""
        self._history.append(outcome)

        # Track per-file attempt counts
        for fpath in outcome.files_affected:
            self._file_attempt_counts[fpath] = (
                self._file_attempt_counts.get(fpath, 0) + 1
            )

    def assess(self) -> MonitorAssessment:
        """Assess the current trajectory. Call after each round."""
        if len(self._history) < 2:
            return MonitorAssessment(
                signal=TroubleSignal.NONE,
                confidence=0.0,
                evidence="Too few rounds for assessment",
            )

        trajectory = [r.error_count for r in self._history]

        # Check in priority order (most serious first)
        checks = [
            self._check_regression,
            self._check_oscillation,
            self._check_stagnation,
            self._check_diminishing_returns,
            self._check_repeated_failure,
            self._check_budget,
        ]

        for check in checks:
            assessment = check(trajectory)
            if assessment.signal != TroubleSignal.NONE:
                # Track signal persistence
                if assessment.signal == self._last_signal:
                    self._signal_persistence += 1
                else:
                    self._signal_persistence = 1
                self._last_signal = assessment.signal
                assessment.rounds_in_trouble = self._signal_persistence
                assessment.trajectory = trajectory[-10:]

                logger.info(
                    "Monitor: %s (confidence=%.2f, persistence=%d) — %s",
                    assessment.signal.value,
                    assessment.confidence,
                    assessment.rounds_in_trouble,
                    assessment.evidence,
                )
                return assessment

        self._last_signal = TroubleSignal.NONE
        self._signal_persistence = 0
        return MonitorAssessment(
            signal=TroubleSignal.NONE,
            confidence=1.0,
            evidence="Progress is steady",
            trajectory=trajectory[-10:],
        )

    def reset(self) -> None:
        """Clear all history. Call when starting a new phase."""
        self._history.clear()
        self._file_attempt_counts.clear()
        self._last_signal = TroubleSignal.NONE
        self._signal_persistence = 0

    def summary(self) -> dict:
        """Current state summary for the supervisor agent."""
        trajectory = [r.error_count for r in self._history]
        return {
            "total_rounds": len(self._history),
            "error_trajectory": trajectory[-20:],
            "current_error_count": trajectory[-1] if trajectory else 0,
            "files_with_most_attempts": sorted(
                self._file_attempt_counts.items(),
                key=lambda x: x[1], reverse=True,
            )[:10],
            "recent_actions": [
                {
                    "round": r.round_number,
                    "action": r.action_taken,
                    "result": r.result,
                    "errors": r.error_count,
                }
                for r in list(self._history)[-10:]
            ],
        }

    # ------------------------------------------------------------------
    # Detection heuristics
    # ------------------------------------------------------------------

    def _check_regression(
        self, trajectory: list[int],
    ) -> MonitorAssessment:
        """Error count is increasing."""
        if len(trajectory) < 3:
            return self._no_signal()

        recent = trajectory[-3:]
        if recent[-1] > recent[-2] > recent[-3]:
            return MonitorAssessment(
                signal=TroubleSignal.REGRESSION,
                confidence=0.8,
                evidence=(
                    f"Error count increasing: "
                    f"{recent[-3]} → {recent[-2]} → {recent[-1]}"
                ),
            )

        # Check if latest is significantly worse than best
        if len(trajectory) >= 4:
            best = min(trajectory[-6:])
            latest = trajectory[-1]
            if latest > best * 2 and latest > best + 3:
                return MonitorAssessment(
                    signal=TroubleSignal.REGRESSION,
                    confidence=0.7,
                    evidence=(
                        f"Error count regressed: was {best}, now {latest}"
                    ),
                )

        return self._no_signal()

    def _check_oscillation(
        self, trajectory: list[int],
    ) -> MonitorAssessment:
        """Error count is ping-ponging (up-down-up-down)."""
        window = min(self._oscillation_window, len(trajectory))
        if window < 4:
            return self._no_signal()

        recent = trajectory[-window:]
        direction_changes = 0
        for i in range(2, len(recent)):
            prev_delta = recent[i - 1] - recent[i - 2]
            curr_delta = recent[i] - recent[i - 1]
            if prev_delta * curr_delta < 0:  # Sign change
                direction_changes += 1

        # Oscillation: direction changes at least 60% of the time
        oscillation_ratio = direction_changes / (len(recent) - 2)
        if oscillation_ratio >= 0.6 and direction_changes >= 3:
            return MonitorAssessment(
                signal=TroubleSignal.OSCILLATION,
                confidence=min(0.9, oscillation_ratio),
                evidence=(
                    f"Error count oscillating: {recent} "
                    f"({direction_changes} direction changes)"
                ),
            )

        return self._no_signal()

    def _check_stagnation(
        self, trajectory: list[int],
    ) -> MonitorAssessment:
        """Error count unchanged for N rounds."""
        if len(trajectory) < self._stagnation_threshold:
            return self._no_signal()

        recent = trajectory[-self._stagnation_threshold:]
        if len(set(recent)) == 1 and recent[0] > 0:
            return MonitorAssessment(
                signal=TroubleSignal.STAGNATION,
                confidence=0.85,
                evidence=(
                    f"Error count stuck at {recent[0]} "
                    f"for {self._stagnation_threshold} rounds"
                ),
            )

        return self._no_signal()

    def _check_diminishing_returns(
        self, trajectory: list[int],
    ) -> MonitorAssessment:
        """Each round is fixing fewer errors than the last."""
        if len(trajectory) < 4:
            return self._no_signal()

        improvements = [
            trajectory[i - 1] - trajectory[i]
            for i in range(1, len(trajectory))
        ]

        # Look at recent improvements
        recent_imp = improvements[-4:]
        if all(imp >= 0 for imp in recent_imp):
            # All non-negative (improving or stable)
            if len(recent_imp) >= 3 and all(
                recent_imp[i] <= recent_imp[i - 1]
                for i in range(1, len(recent_imp))
            ):
                # Monotonically decreasing improvement
                if recent_imp[-1] <= 1 and trajectory[-1] > 0:
                    return MonitorAssessment(
                        signal=TroubleSignal.DIMINISHING_RETURNS,
                        confidence=0.7,
                        evidence=(
                            f"Improvements shrinking: "
                            f"{recent_imp}, {trajectory[-1]} errors remain"
                        ),
                    )

        return self._no_signal()

    def _check_repeated_failure(
        self, trajectory: list[int],
    ) -> MonitorAssessment:
        """Same file is being repaired too many times."""
        chronic = [
            (fpath, count)
            for fpath, count in self._file_attempt_counts.items()
            if count >= self._max_rounds_per_file
        ]

        if chronic:
            worst = max(chronic, key=lambda x: x[1])
            return MonitorAssessment(
                signal=TroubleSignal.REPEATED_FAILURE,
                confidence=0.8,
                evidence=(
                    f"{worst[0]} has been repaired {worst[1]} times. "
                    f"{len(chronic)} file(s) exceed the threshold."
                ),
            )

        return self._no_signal()

    def _check_budget(
        self, trajectory: list[int],
    ) -> MonitorAssessment:
        """Too many total rounds spent."""
        total = len(self._history)
        if total > 15 and trajectory[-1] > 0:
            return MonitorAssessment(
                signal=TroubleSignal.BUDGET_WARNING,
                confidence=0.6,
                evidence=(
                    f"{total} rounds completed, "
                    f"still {trajectory[-1]} error(s) remaining"
                ),
            )

        return self._no_signal()

    @staticmethod
    def _no_signal() -> MonitorAssessment:
        return MonitorAssessment(
            signal=TroubleSignal.NONE,
            confidence=0.0,
            evidence="",
        )
