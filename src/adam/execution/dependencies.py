"""Dependency manager — installs packages and resolves conflicts.

Detects the project's package manager from tech stack and handles
installation, lock files, and dependency resolution.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

from adam.execution.runner import ExecutionResult, ShellRunner

logger = logging.getLogger(__name__)


@dataclass
class PackageManager:
    """Configuration for a detected package manager."""
    name: str  # npm, pip, uv, cargo, go, etc.
    install_command: str  # e.g. "npm install"
    add_command: str  # e.g. "npm install {package}"
    lock_file: str  # e.g. "package-lock.json"
    config_file: str  # e.g. "package.json"


# Known package managers and their commands
PACKAGE_MANAGERS: dict[str, PackageManager] = {
    "npm": PackageManager(
        name="npm",
        install_command="npm install",
        add_command="npm install {package}",
        lock_file="package-lock.json",
        config_file="package.json",
    ),
    "yarn": PackageManager(
        name="yarn",
        install_command="yarn install",
        add_command="yarn add {package}",
        lock_file="yarn.lock",
        config_file="package.json",
    ),
    "pnpm": PackageManager(
        name="pnpm",
        install_command="pnpm install",
        add_command="pnpm add {package}",
        lock_file="pnpm-lock.yaml",
        config_file="package.json",
    ),
    "pip": PackageManager(
        name="pip",
        install_command="pip install -r requirements.txt",
        add_command="pip install {package}",
        lock_file="requirements.txt",
        config_file="requirements.txt",
    ),
    "uv": PackageManager(
        name="uv",
        install_command="uv sync",
        add_command="uv add {package}",
        lock_file="uv.lock",
        config_file="pyproject.toml",
    ),
    "poetry": PackageManager(
        name="poetry",
        install_command="poetry install",
        add_command="poetry add {package}",
        lock_file="poetry.lock",
        config_file="pyproject.toml",
    ),
    "cargo": PackageManager(
        name="cargo",
        install_command="cargo build",
        add_command="cargo add {package}",
        lock_file="Cargo.lock",
        config_file="Cargo.toml",
    ),
    "go": PackageManager(
        name="go",
        install_command="go mod download",
        add_command="go get {package}",
        lock_file="go.sum",
        config_file="go.mod",
    ),
}


class DependencyManager:
    """Manages project dependencies — detection, installation, resolution."""

    def __init__(
        self,
        project_root: str | Path,
        runner: ShellRunner | None = None,
    ) -> None:
        self._root = Path(project_root)
        self._runner = runner or ShellRunner()
        self._pm: PackageManager | None = None

    def detect_package_manager(
        self, tech_stack: dict | None = None,
    ) -> PackageManager | None:
        """Detect the package manager from project files or tech stack."""
        # Check for explicit tech stack hint
        if tech_stack:
            pm_name = tech_stack.get("package_manager", "")
            if pm_name and pm_name in PACKAGE_MANAGERS:
                self._pm = PACKAGE_MANAGERS[pm_name]
                return self._pm

        # Detect from files in project root
        for _name, pm in PACKAGE_MANAGERS.items():
            if (self._root / pm.config_file).exists():
                # Disambiguate: pyproject.toml could be uv or poetry
                if pm.config_file == "pyproject.toml":
                    if (self._root / "uv.lock").exists():
                        self._pm = PACKAGE_MANAGERS["uv"]
                        return self._pm
                    if (self._root / "poetry.lock").exists():
                        self._pm = PACKAGE_MANAGERS["poetry"]
                        return self._pm
                    # Default to uv for pyproject.toml
                    self._pm = PACKAGE_MANAGERS["uv"]
                    return self._pm

                # Disambiguate: package.json could be npm, yarn, or pnpm
                if pm.config_file == "package.json":
                    if (self._root / "pnpm-lock.yaml").exists():
                        self._pm = PACKAGE_MANAGERS["pnpm"]
                        return self._pm
                    if (self._root / "yarn.lock").exists():
                        self._pm = PACKAGE_MANAGERS["yarn"]
                        return self._pm
                    self._pm = PACKAGE_MANAGERS["npm"]
                    return self._pm

                self._pm = pm
                return self._pm

        return None

    async def install(self) -> ExecutionResult:
        """Install all project dependencies."""
        pm = self._pm or self.detect_package_manager()
        if pm is None:
            logger.warning("No package manager detected")
            return ExecutionResult(
                command="(none)",
                return_code=1,
                stdout="",
                stderr="No package manager detected",
            )

        logger.info("Installing dependencies with %s", pm.name)
        return await self._runner.run(
            pm.install_command, cwd=self._root, timeout=300,
        )

    async def add_package(self, package: str) -> ExecutionResult:
        """Add a single package dependency."""
        pm = self._pm or self.detect_package_manager()
        if pm is None:
            return ExecutionResult(
                command="(none)",
                return_code=1,
                stdout="",
                stderr="No package manager detected",
            )

        cmd = pm.add_command.format(package=package)
        logger.info("Adding package: %s (via %s)", package, pm.name)
        return await self._runner.run(cmd, cwd=self._root, timeout=120)

    async def add_packages(self, packages: list[str]) -> list[ExecutionResult]:
        """Add multiple packages."""
        results = []
        for pkg in packages:
            result = await self.add_package(pkg)
            results.append(result)
            if not result.success:
                logger.warning("Failed to install %s: %s", pkg, result.output)
        return results

    async def check_installed(self) -> bool:
        """Check if dependencies appear to be installed."""
        pm = self._pm or self.detect_package_manager()
        if pm is None:
            return True  # No package manager = nothing to install

        # Check for lock file or node_modules / .venv
        if pm.name in ("npm", "yarn", "pnpm"):
            return (self._root / "node_modules").is_dir()
        if pm.name in ("pip", "uv", "poetry"):
            return (self._root / ".venv").is_dir()
        if pm.name == "cargo":
            return (self._root / "target").is_dir()
        return True

    @property
    def package_manager(self) -> PackageManager | None:
        return self._pm
