"""Tests for stop condition evaluation."""

from adam.orchestrator.obligations import ObligationStatus
from adam.orchestrator.stop_conditions import evaluate_stop_conditions


def _make_ob_status(
    total: int = 5,
    open_count: int = 0,
    complete: bool = True,
) -> ObligationStatus:
    return ObligationStatus(
        total=total,
        open=open_count,
        implemented=total - open_count,
        tested=0,
        verified=0,
        blocked=0,
        complete=complete,
    )


class TestStopConditions:
    def test_all_conditions_met(self):
        result = evaluate_stop_conditions(
            obligation_status=_make_ob_status(),
            all_tests_pass=True,
            hard_validators_pass=True,
            soft_composite=0.8,
            acceptance_threshold=0.6,
            files_accepted=10,
            files_total=10,
        )
        assert result.ready is True
        assert result.unmet_count == 0

    def test_tests_failing(self):
        result = evaluate_stop_conditions(
            obligation_status=_make_ob_status(),
            all_tests_pass=False,
            hard_validators_pass=True,
            soft_composite=0.8,
            acceptance_threshold=0.6,
            files_accepted=10,
            files_total=10,
        )
        assert result.ready is False
        assert any(c.name == "tests_pass" and not c.met for c in result.conditions)

    def test_obligations_unresolved(self):
        result = evaluate_stop_conditions(
            obligation_status=_make_ob_status(total=5, open_count=2, complete=False),
            all_tests_pass=True,
            hard_validators_pass=True,
            soft_composite=0.8,
            acceptance_threshold=0.6,
            files_accepted=10,
            files_total=10,
        )
        assert result.ready is False
        assert any(
            c.name == "obligations_resolved" and not c.met
            for c in result.conditions
        )

    def test_soft_critics_below_threshold(self):
        result = evaluate_stop_conditions(
            obligation_status=_make_ob_status(),
            all_tests_pass=True,
            hard_validators_pass=True,
            soft_composite=0.3,
            acceptance_threshold=0.6,
            files_accepted=10,
            files_total=10,
        )
        assert result.ready is False
        assert any(
            c.name == "soft_critics_above_threshold" and not c.met
            for c in result.conditions
        )

    def test_files_not_all_accepted(self):
        result = evaluate_stop_conditions(
            obligation_status=_make_ob_status(),
            all_tests_pass=True,
            hard_validators_pass=True,
            soft_composite=0.8,
            acceptance_threshold=0.6,
            files_accepted=8,
            files_total=10,
        )
        assert result.ready is False

    def test_visual_inspection_fails(self):
        result = evaluate_stop_conditions(
            obligation_status=_make_ob_status(),
            all_tests_pass=True,
            hard_validators_pass=True,
            soft_composite=0.8,
            acceptance_threshold=0.6,
            visual_passes=False,
            files_accepted=10,
            files_total=10,
        )
        assert result.ready is False

    def test_visual_inspection_not_applicable(self):
        """When visual_passes is None, no visual condition is added."""
        result = evaluate_stop_conditions(
            obligation_status=_make_ob_status(),
            all_tests_pass=True,
            hard_validators_pass=True,
            soft_composite=0.8,
            acceptance_threshold=0.6,
            visual_passes=None,
            files_accepted=10,
            files_total=10,
        )
        assert result.ready is True
        assert not any(
            c.name == "visual_inspection_passes" for c in result.conditions
        )

    def test_no_obligations(self):
        result = evaluate_stop_conditions(
            obligation_status=_make_ob_status(total=0, complete=True),
            all_tests_pass=True,
            hard_validators_pass=True,
            soft_composite=0.8,
            acceptance_threshold=0.6,
            files_accepted=5,
            files_total=5,
        )
        assert result.ready is True

    def test_summary_message(self):
        result = evaluate_stop_conditions(
            obligation_status=_make_ob_status(),
            all_tests_pass=True,
            hard_validators_pass=True,
            soft_composite=0.8,
            acceptance_threshold=0.6,
            files_accepted=10,
            files_total=10,
        )
        assert "complete" in result.summary.lower()
