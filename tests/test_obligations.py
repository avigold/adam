"""Tests for obligation purpose matching."""

from adam.orchestrator.obligations import _purposes_match


class TestPurposesMatch:
    def test_exact_overlap(self):
        assert _purposes_match(
            "User authentication module",
            "Implement user authentication",
        )

    def test_no_overlap(self):
        assert not _purposes_match(
            "Database connection pool",
            "Render homepage template",
        )

    def test_partial_overlap(self):
        assert _purposes_match(
            "REST API for user management",
            "Build user management endpoints",
        )

    def test_empty_strings(self):
        assert not _purposes_match("", "")
        assert not _purposes_match("something", "")
        assert not _purposes_match("", "something")

    def test_stop_words_ignored(self):
        # "the", "a", "is" etc should not count as matches
        assert not _purposes_match(
            "the a is are and",
            "the a is are and",
        )

    def test_case_insensitive(self):
        assert _purposes_match(
            "User Authentication Module",
            "user authentication module",
        )
