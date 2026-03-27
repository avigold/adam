"""Tests for git manager."""

from pathlib import Path

import pytest

from adam.git.manager import GitManager


@pytest.fixture
def git_dir(tmp_path: Path) -> Path:
    """Create a temp directory and init git."""
    import subprocess

    subprocess.run(
        ["git", "init"], cwd=tmp_path,
        capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@test.com"],
        cwd=tmp_path, capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=tmp_path, capture_output=True, check=True,
    )
    # Create initial commit
    (tmp_path / "README.md").write_text("# test")
    subprocess.run(
        ["git", "add", "README.md"],
        cwd=tmp_path, capture_output=True, check=True,
    )
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=tmp_path, capture_output=True, check=True,
    )
    return tmp_path


class TestGitManager:
    @pytest.mark.asyncio
    async def test_has_repo(self, git_dir: Path):
        gm = GitManager(git_dir)
        assert await gm.has_repo() is True

    @pytest.mark.asyncio
    async def test_has_repo_no_git(self, tmp_path: Path):
        gm = GitManager(tmp_path)
        assert await gm.has_repo() is False

    @pytest.mark.asyncio
    async def test_init_new_repo(self, tmp_path: Path):
        gm = GitManager(tmp_path)
        result = await gm.init()
        assert result.success
        assert await gm.has_repo() is True
        # Should have created .gitignore
        assert (tmp_path / ".gitignore").exists()

    @pytest.mark.asyncio
    async def test_init_existing_repo(self, git_dir: Path):
        gm = GitManager(git_dir)
        result = await gm.init()
        assert result.success  # Should not fail on existing repo

    @pytest.mark.asyncio
    async def test_commit_file(self, git_dir: Path):
        (git_dir / "hello.py").write_text("print('hello')")
        gm = GitManager(git_dir)
        result = await gm.commit_file("hello.py")
        assert result.success
        assert result.commit_hash

    @pytest.mark.asyncio
    async def test_commit_nothing(self, git_dir: Path):
        gm = GitManager(git_dir)
        result = await gm.commit("nothing changed")
        assert result.success
        assert result.message == "Nothing to commit"

    @pytest.mark.asyncio
    async def test_is_clean(self, git_dir: Path):
        gm = GitManager(git_dir)
        assert await gm.is_clean() is True
        (git_dir / "dirty.txt").write_text("dirty")
        assert await gm.is_clean() is False

    @pytest.mark.asyncio
    async def test_status(self, git_dir: Path):
        gm = GitManager(git_dir)
        result = await gm.status()
        assert result.success

    @pytest.mark.asyncio
    async def test_log(self, git_dir: Path):
        gm = GitManager(git_dir)
        result = await gm.log()
        assert result.success
        assert "init" in result.stdout

    @pytest.mark.asyncio
    async def test_current_hash(self, git_dir: Path):
        gm = GitManager(git_dir)
        h = await gm.current_hash()
        assert len(h) > 0

    @pytest.mark.asyncio
    async def test_rollback_file(self, git_dir: Path):
        (git_dir / "README.md").write_text("modified")
        gm = GitManager(git_dir)
        result = await gm.rollback_file("README.md")
        assert result.success
        assert (git_dir / "README.md").read_text() == "# test"
