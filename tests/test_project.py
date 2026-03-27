"""Tests for project state detection and persistence."""

from pathlib import Path

from adam.project import ProjectState, detect_project, save_project, update_phase


class TestProjectState:
    def test_detect_no_project(self, tmp_path: Path):
        assert detect_project(tmp_path) is None

    def test_save_and_detect(self, tmp_path: Path):
        state = ProjectState(
            project_id="abc-123",
            phase="implementing",
            title="Test Project",
        )
        save_project(tmp_path, state)
        loaded = detect_project(tmp_path)
        assert loaded is not None
        assert loaded.project_id == "abc-123"
        assert loaded.phase == "implementing"
        assert loaded.title == "Test Project"

    def test_update_phase(self, tmp_path: Path):
        state = ProjectState(project_id="abc-123", phase="planning", title="Test")
        save_project(tmp_path, state)

        update_phase(tmp_path, "complete")

        loaded = detect_project(tmp_path)
        assert loaded is not None
        assert loaded.phase == "complete"
        assert loaded.project_id == "abc-123"

    def test_corrupt_file(self, tmp_path: Path):
        (tmp_path / ".adam").write_text("not json")
        assert detect_project(tmp_path) is None
