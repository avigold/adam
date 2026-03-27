"""Tests for repair planner."""

from adam.repair.planner import RepairPlanner
from adam.types import RepairPriority, ValidationResult


class TestRepairPlanner:
    def setup_method(self):
        self.planner = RepairPlanner()

    def test_no_failures(self):
        results = [
            ValidationResult(validator_name="test_runner", is_hard=True, passed=True),
            ValidationResult(validator_name="code_quality", is_hard=False, score=0.8),
        ]
        actions = self.planner.plan(results)
        assert actions == []

    def test_hard_failure_generates_action(self):
        results = [
            ValidationResult(
                validator_name="test_runner",
                is_hard=True,
                passed=False,
                diagnosis="AssertionError in test_foo",
            ),
        ]
        actions = self.planner.plan(results)
        assert len(actions) == 1
        assert actions[0].priority == RepairPriority.TEST_FAILURE
        assert "AssertionError" in actions[0].instruction

    def test_low_soft_score_generates_action(self):
        results = [
            ValidationResult(
                validator_name="security",
                is_hard=False,
                score=0.2,
                diagnosis="SQL injection vulnerability",
                repair_suggestions=["Use parameterised queries"],
            ),
        ]
        actions = self.planner.plan(results)
        assert len(actions) == 1
        assert actions[0].priority == RepairPriority.SECURITY_ISSUE
        assert "parameterised" in actions[0].instruction

    def test_adequate_soft_score_no_action(self):
        results = [
            ValidationResult(
                validator_name="code_quality",
                is_hard=False,
                score=0.6,
            ),
        ]
        actions = self.planner.plan(results)
        assert actions == []

    def test_priority_ordering(self):
        results = [
            ValidationResult(
                validator_name="security", is_hard=False, score=0.1, diagnosis="vuln"
            ),
            ValidationResult(
                validator_name="test_runner", is_hard=True, passed=False, diagnosis="fail"
            ),
            ValidationResult(
                validator_name="lint_runner", is_hard=True, passed=False, diagnosis="lint"
            ),
        ]
        actions = self.planner.plan(results)
        assert len(actions) == 3
        # Test failure (1) < Lint error (4) < Security issue (5)
        assert actions[0].priority == RepairPriority.TEST_FAILURE
        assert actions[1].priority == RepairPriority.LINT_ERROR
        assert actions[2].priority == RepairPriority.SECURITY_ISSUE

    def test_multiple_hard_failures(self):
        results = [
            ValidationResult(
                validator_name="build_checker", is_hard=True, passed=False, diagnosis="build failed"
            ),
            ValidationResult(
                validator_name="type_checker", is_hard=True, passed=False, diagnosis="type error"
            ),
        ]
        actions = self.planner.plan(results)
        assert len(actions) == 2
        # Build failure (2) < Type error (3)
        assert actions[0].priority == RepairPriority.BUILD_FAILURE
        assert actions[1].priority == RepairPriority.TYPE_ERROR
