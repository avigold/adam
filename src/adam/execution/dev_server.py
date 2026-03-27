"""Dev server lifecycle management.

Start, monitor, and stop development servers for UI projects.
Used by the visual inspection system — screenshots require a running server.

Includes auto-detection of dev server commands from tech stack and project files.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Known dev server configurations per framework
# ---------------------------------------------------------------------------

@dataclass
class DevServerConfig:
    """Known dev server configuration for a framework."""
    name: str
    command: str
    port: int
    ready_pattern: str
    detect_files: list[str]  # files whose presence indicates this framework


KNOWN_SERVERS: list[DevServerConfig] = [
    # JavaScript/TypeScript
    DevServerConfig(
        name="next.js",
        command="npx next dev",
        port=3000,
        ready_pattern="ready",
        detect_files=["next.config.js", "next.config.ts", "next.config.mjs"],
    ),
    DevServerConfig(
        name="vite",
        command="npx vite",
        port=5173,
        ready_pattern="ready",
        detect_files=["vite.config.js", "vite.config.ts", "vite.config.mjs"],
    ),
    DevServerConfig(
        name="create-react-app",
        command="npx react-scripts start",
        port=3000,
        ready_pattern="compiled",
        detect_files=["src/reportWebVitals.js", "src/reportWebVitals.ts"],
    ),
    DevServerConfig(
        name="nuxt",
        command="npx nuxi dev",
        port=3000,
        ready_pattern="ready",
        detect_files=["nuxt.config.js", "nuxt.config.ts"],
    ),
    DevServerConfig(
        name="svelte-kit",
        command="npx vite dev",
        port=5173,
        ready_pattern="ready",
        detect_files=["svelte.config.js"],
    ),
    DevServerConfig(
        name="astro",
        command="npx astro dev",
        port=4321,
        ready_pattern="ready",
        detect_files=["astro.config.mjs", "astro.config.ts"],
    ),
    # Python
    DevServerConfig(
        name="django",
        command="python manage.py runserver",
        port=8000,
        ready_pattern="starting development server",
        detect_files=["manage.py"],
    ),
    DevServerConfig(
        name="flask",
        command="flask run",
        port=5000,
        ready_pattern="running on",
        detect_files=["app.py", "wsgi.py"],
    ),
    DevServerConfig(
        name="fastapi",
        command="uvicorn main:app --reload",
        port=8000,
        ready_pattern="started server",
        detect_files=["main.py"],  # Checked after Django
    ),
    # Ruby
    DevServerConfig(
        name="rails",
        command="rails server",
        port=3000,
        ready_pattern="listening",
        detect_files=["Gemfile", "config/routes.rb"],
    ),
    # Go
    DevServerConfig(
        name="go-http",
        command="go run .",
        port=8080,
        ready_pattern="listening",
        detect_files=["main.go"],
    ),
    # Rust
    DevServerConfig(
        name="actix/axum",
        command="cargo run",
        port=8080,
        ready_pattern="listening",
        detect_files=["Cargo.toml"],
    ),
    # Generic npm
    DevServerConfig(
        name="npm-dev",
        command="npm run dev",
        port=3000,
        ready_pattern="ready",
        detect_files=["package.json"],
    ),
]


def detect_dev_server(
    project_root: str | Path,
    tech_stack: dict | None = None,
    build_system: dict | None = None,
) -> DevServerConfig | None:
    """Auto-detect the dev server configuration for a project.

    Checks in order:
    1. Explicit build_system.dev_server from architecture
    2. Tech stack hints (framework name)
    3. File-based detection (most specific first)
    """
    root = Path(project_root)

    # 1. Explicit config from architecture
    if build_system and build_system.get("dev_server"):
        cmd = build_system["dev_server"]
        port = int(build_system.get("dev_port", 3000))
        return DevServerConfig(
            name="configured",
            command=cmd,
            port=port,
            ready_pattern=build_system.get("dev_ready_pattern", "ready"),
            detect_files=[],
        )

    # 2. Tech stack framework hint
    if tech_stack:
        framework = str(tech_stack.get("framework", "")).lower()
        for cfg in KNOWN_SERVERS:
            if cfg.name in framework:
                return cfg

    # 3. File-based detection (order matters — specific before generic)
    # detect_files = any of these files present → match
    for cfg in KNOWN_SERVERS:
        if any((root / f).exists() for f in cfg.detect_files):
            # Django-specific: needs manage.py AND it should be a Django project
            if cfg.name == "django":
                manage_py = root / "manage.py"
                if manage_py.exists():
                    content = manage_py.read_text(encoding="utf-8", errors="ignore")
                    if "django" in content.lower():
                        return cfg
                continue
            # Rails-specific: needs both Gemfile and routes.rb
            if cfg.name == "rails" and not all(
                (root / f).exists() for f in cfg.detect_files
            ):
                continue
            return cfg

    return None


# ---------------------------------------------------------------------------
# Dev server process manager
# ---------------------------------------------------------------------------

class DevServer:
    """Manages a development server process."""

    def __init__(
        self,
        command: str,
        cwd: str | Path = ".",
        port: int = 3000,
        ready_pattern: str = "ready",
        startup_timeout: int = 30,
    ) -> None:
        self._command = command
        self._cwd = str(cwd)
        self._port = port
        self._ready_pattern = ready_pattern.lower()
        self._startup_timeout = startup_timeout
        self._process: asyncio.subprocess.Process | None = None
        self._output_lines: list[str] = []

    @classmethod
    def from_config(
        cls,
        config: DevServerConfig,
        cwd: str | Path = ".",
        startup_timeout: int = 30,
    ) -> DevServer:
        """Create a DevServer from a detected config."""
        return cls(
            command=config.command,
            cwd=cwd,
            port=config.port,
            ready_pattern=config.ready_pattern,
            startup_timeout=startup_timeout,
        )

    @property
    def url(self) -> str:
        return f"http://localhost:{self._port}"

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.returncode is None

    async def start(self) -> bool:
        """Start the dev server and wait for it to be ready."""
        if self.running:
            logger.info("Dev server already running at %s", self.url)
            return True

        logger.info(
            "Starting dev server: %s (port %d)", self._command, self._port
        )

        self._process = await asyncio.create_subprocess_shell(
            self._command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            cwd=self._cwd,
        )

        try:
            ready = await asyncio.wait_for(
                self._wait_for_ready(),
                timeout=self._startup_timeout,
            )
            if ready:
                logger.info("Dev server ready at %s", self.url)
                return True
            logger.warning("Dev server started but ready pattern not found")
            return self.running
        except TimeoutError:
            logger.warning(
                "Dev server startup timed out after %ds",
                self._startup_timeout,
            )
            return self.running

    async def _wait_for_ready(self) -> bool:
        """Read output until the ready pattern appears."""
        if self._process is None or self._process.stdout is None:
            return False

        while True:
            line_bytes = await self._process.stdout.readline()
            if not line_bytes:
                return False

            line = line_bytes.decode("utf-8", errors="replace").strip()
            self._output_lines.append(line)

            if self._ready_pattern in line.lower():
                asyncio.create_task(self._drain_output())
                return True

    async def _drain_output(self) -> None:
        """Keep reading output so the pipe doesn't fill up."""
        if self._process is None or self._process.stdout is None:
            return
        try:
            while True:
                line_bytes = await self._process.stdout.readline()
                if not line_bytes:
                    break
                line = line_bytes.decode("utf-8", errors="replace").strip()
                self._output_lines.append(line)
                if len(self._output_lines) > 100:
                    self._output_lines = self._output_lines[-100:]
        except Exception:
            pass

    async def stop(self) -> None:
        """Stop the dev server."""
        if self._process is None:
            return

        logger.info("Stopping dev server (pid=%s)", self._process.pid)
        try:
            self._process.terminate()
            try:
                await asyncio.wait_for(self._process.wait(), timeout=5)
            except TimeoutError:
                logger.warning("Dev server didn't terminate, killing")
                self._process.kill()
                await self._process.wait()
        except ProcessLookupError:
            pass

        self._process = None
        logger.info("Dev server stopped")

    @property
    def recent_output(self) -> str:
        """Get recent server output for debugging."""
        return "\n".join(self._output_lines[-20:])

    async def __aenter__(self) -> DevServer:
        await self.start()
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.stop()
