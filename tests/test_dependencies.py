"""Tests for dependency manager."""

from pathlib import Path

from adam.execution.dependencies import PACKAGE_MANAGERS, DependencyManager


class TestPackageManagerDetection:
    def test_detect_npm(self, tmp_path: Path):
        (tmp_path / "package.json").write_text("{}")
        dm = DependencyManager(tmp_path)
        pm = dm.detect_package_manager()
        assert pm is not None
        assert pm.name == "npm"

    def test_detect_yarn(self, tmp_path: Path):
        (tmp_path / "package.json").write_text("{}")
        (tmp_path / "yarn.lock").write_text("")
        dm = DependencyManager(tmp_path)
        pm = dm.detect_package_manager()
        assert pm is not None
        assert pm.name == "yarn"

    def test_detect_pnpm(self, tmp_path: Path):
        (tmp_path / "package.json").write_text("{}")
        (tmp_path / "pnpm-lock.yaml").write_text("")
        dm = DependencyManager(tmp_path)
        pm = dm.detect_package_manager()
        assert pm is not None
        assert pm.name == "pnpm"

    def test_detect_uv(self, tmp_path: Path):
        (tmp_path / "pyproject.toml").write_text("[project]")
        (tmp_path / "uv.lock").write_text("")
        dm = DependencyManager(tmp_path)
        pm = dm.detect_package_manager()
        assert pm is not None
        assert pm.name == "uv"

    def test_detect_poetry(self, tmp_path: Path):
        (tmp_path / "pyproject.toml").write_text("[project]")
        (tmp_path / "poetry.lock").write_text("")
        dm = DependencyManager(tmp_path)
        pm = dm.detect_package_manager()
        assert pm is not None
        assert pm.name == "poetry"

    def test_detect_pyproject_default_uv(self, tmp_path: Path):
        (tmp_path / "pyproject.toml").write_text("[project]")
        dm = DependencyManager(tmp_path)
        pm = dm.detect_package_manager()
        assert pm is not None
        assert pm.name == "uv"

    def test_detect_cargo(self, tmp_path: Path):
        (tmp_path / "Cargo.toml").write_text("[package]")
        dm = DependencyManager(tmp_path)
        pm = dm.detect_package_manager()
        assert pm is not None
        assert pm.name == "cargo"

    def test_detect_go(self, tmp_path: Path):
        (tmp_path / "go.mod").write_text("module example.com/foo")
        dm = DependencyManager(tmp_path)
        pm = dm.detect_package_manager()
        assert pm is not None
        assert pm.name == "go"

    def test_detect_none(self, tmp_path: Path):
        dm = DependencyManager(tmp_path)
        pm = dm.detect_package_manager()
        assert pm is None

    def test_detect_from_tech_stack(self, tmp_path: Path):
        dm = DependencyManager(tmp_path)
        pm = dm.detect_package_manager({"package_manager": "cargo"})
        assert pm is not None
        assert pm.name == "cargo"

    def test_check_installed_no_pm(self, tmp_path: Path):
        dm = DependencyManager(tmp_path)
        # No package manager = nothing to install
        import asyncio
        assert asyncio.run(dm.check_installed()) is True


class TestPackageManagerConfig:
    def test_all_managers_have_required_fields(self):
        for name, pm in PACKAGE_MANAGERS.items():
            assert pm.name == name
            assert pm.install_command
            assert "{package}" in pm.add_command
            assert pm.config_file
