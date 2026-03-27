"""Tests for architecture checkpoint logic."""

from adam.cli.checkpoints import _APPROVE_RESPONSES


class TestApproveResponses:
    def test_empty_string_approves(self):
        assert "" in _APPROVE_RESPONSES

    def test_y_approves(self):
        assert "y" in _APPROVE_RESPONSES

    def test_yes_approves(self):
        assert "yes" in _APPROVE_RESPONSES

    def test_go_for_it_approves(self):
        assert "go for it" in _APPROVE_RESPONSES

    def test_lgtm_approves(self):
        assert "lgtm" in _APPROVE_RESPONSES

    def test_random_text_does_not_approve(self):
        assert "use fastapi instead" not in _APPROVE_RESPONSES
        assert "change the database" not in _APPROVE_RESPONSES
