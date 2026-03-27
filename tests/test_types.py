"""Tests for types module."""

from adam.types import (
    AgentContext,
    ScoreVectorData,
    ValidationResult,
    scores_from_validation,
)


class TestScoreVectorData:
    def test_default_values(self):
        sv = ScoreVectorData()
        assert sv.hard_pass is True
        assert sv.composite == 0.5

    def test_compute_composite(self):
        sv = ScoreVectorData(
            code_readability=0.8,
            maintainability=0.7,
            security=0.9,
            performance=0.6,
        )
        result = sv.compute_composite()
        assert 0.0 <= result <= 1.0
        assert sv.composite == result

    def test_compute_composite_custom_weights(self):
        sv = ScoreVectorData(code_readability=1.0, security=0.0)
        result = sv.compute_composite({"code_readability": 1.0, "security": 1.0})
        assert result == 0.5


class TestScoresFromValidation:
    def test_hard_pass_all(self):
        results = [
            ValidationResult(validator_name="test_runner", is_hard=True, passed=True),
            ValidationResult(validator_name="lint_runner", is_hard=True, passed=True),
        ]
        sv = scores_from_validation(results)
        assert sv.hard_pass is True
        assert sv.tests_pass is True
        assert sv.lint_pass is True

    def test_hard_fail(self):
        results = [
            ValidationResult(
                validator_name="test_runner", is_hard=True,
                passed=False, diagnosis="1 test failed",
            ),
        ]
        sv = scores_from_validation(results)
        assert sv.hard_pass is False
        assert sv.tests_pass is False

    def test_soft_scores(self):
        results = [
            ValidationResult(validator_name="code_quality", is_hard=False, score=0.85),
            ValidationResult(validator_name="security", is_hard=False, score=0.92),
        ]
        sv = scores_from_validation(results)
        assert sv.code_readability == 0.85
        assert sv.security == 0.92

    def test_mixed(self):
        results = [
            ValidationResult(validator_name="test_runner", is_hard=True, passed=True),
            ValidationResult(validator_name="type_checker", is_hard=True, passed=False),
            ValidationResult(validator_name="code_quality", is_hard=False, score=0.7),
        ]
        sv = scores_from_validation(results)
        assert sv.hard_pass is False
        assert sv.tests_pass is True
        assert sv.types_pass is False
        assert sv.code_readability == 0.7


class TestAgentContext:
    def test_defaults(self):
        ctx = AgentContext()
        assert ctx.project_id == ""
        assert ctx.tech_stack == {}
        assert ctx.related_files == []

    def test_custom_values(self):
        ctx = AgentContext(
            project_id="abc",
            tech_stack={"language": "python"},
        )
        assert ctx.project_id == "abc"
        assert ctx.tech_stack["language"] == "python"
