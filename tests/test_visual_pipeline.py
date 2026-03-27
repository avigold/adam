"""Tests for the visual inspection pipeline: dev server detection, route discovery."""

from pathlib import Path

from adam.agents.route_discoverer import find_routing_files
from adam.execution.dev_server import (
    KNOWN_SERVERS,
    DevServer,
    DevServerConfig,
    detect_dev_server,
)

# ---------------------------------------------------------------------------
# Dev server detection
# ---------------------------------------------------------------------------

class TestDetectDevServer:
    def test_explicit_config(self, tmp_path: Path):
        """Explicit build_system.dev_server takes priority."""
        result = detect_dev_server(
            tmp_path,
            build_system={
                "dev_server": "python -m http.server 8080",
                "dev_port": "8080",
                "dev_ready_pattern": "serving",
            },
        )
        assert result is not None
        assert result.name == "configured"
        assert result.command == "python -m http.server 8080"
        assert result.port == 8080
        assert result.ready_pattern == "serving"

    def test_detect_nextjs(self, tmp_path: Path):
        (tmp_path / "next.config.js").write_text("module.exports = {}")
        result = detect_dev_server(tmp_path)
        assert result is not None
        assert result.name == "next.js"
        assert "next" in result.command

    def test_detect_vite(self, tmp_path: Path):
        (tmp_path / "vite.config.ts").write_text("export default {}")
        result = detect_dev_server(tmp_path)
        assert result is not None
        assert result.name == "vite"

    def test_detect_django(self, tmp_path: Path):
        (tmp_path / "manage.py").write_text(
            "#!/usr/bin/env python\n"
            "import django\n"
            "django.setup()\n"
        )
        result = detect_dev_server(tmp_path)
        assert result is not None
        assert result.name == "django"

    def test_detect_npm_fallback(self, tmp_path: Path):
        (tmp_path / "package.json").write_text('{"name": "myapp"}')
        result = detect_dev_server(tmp_path)
        assert result is not None
        assert result.name == "npm-dev"

    def test_detect_from_tech_stack(self, tmp_path: Path):
        result = detect_dev_server(
            tmp_path,
            tech_stack={"framework": "Next.js"},
        )
        assert result is not None
        assert result.name == "next.js"

    def test_no_detection(self, tmp_path: Path):
        result = detect_dev_server(tmp_path)
        assert result is None

    def test_from_config_factory(self):
        cfg = DevServerConfig(
            name="test",
            command="echo ready",
            port=9999,
            ready_pattern="ready",
            detect_files=[],
        )
        server = DevServer.from_config(cfg, cwd="/tmp")
        assert server.url == "http://localhost:9999"


class TestKnownServers:
    def test_all_have_required_fields(self):
        for cfg in KNOWN_SERVERS:
            assert cfg.name
            assert cfg.command
            assert cfg.port > 0
            assert cfg.ready_pattern
            assert isinstance(cfg.detect_files, list)

    def test_common_frameworks_covered(self):
        names = {cfg.name for cfg in KNOWN_SERVERS}
        assert "next.js" in names
        assert "vite" in names
        assert "django" in names
        assert "flask" in names
        assert "rails" in names


# ---------------------------------------------------------------------------
# Route discovery file scanning
# ---------------------------------------------------------------------------

class TestFindRoutingFiles:
    def test_finds_nextjs_pages(self, tmp_path: Path):
        pages = tmp_path / "pages"
        pages.mkdir()
        (pages / "index.tsx").write_text("export default function Home() {}")
        (pages / "about.tsx").write_text("export default function About() {}")
        files = find_routing_files(tmp_path)
        assert len(files) == 2
        paths = {f["path"] for f in files}
        assert "pages/index.tsx" in paths
        assert "pages/about.tsx" in paths

    def test_finds_nextjs_app_router(self, tmp_path: Path):
        app = tmp_path / "app"
        app.mkdir()
        (app / "page.tsx").write_text("export default function Home() {}")
        about = app / "about"
        about.mkdir()
        (about / "page.tsx").write_text("export default function About() {}")
        files = find_routing_files(tmp_path)
        assert len(files) >= 2

    def test_finds_django_urls(self, tmp_path: Path):
        app = tmp_path / "myapp"
        app.mkdir()
        (app / "urls.py").write_text(
            "from django.urls import path\n"
            "urlpatterns = [path('/', views.home)]"
        )
        files = find_routing_files(tmp_path)
        assert len(files) >= 1
        assert any("urls.py" in f["path"] for f in files)

    def test_finds_react_router(self, tmp_path: Path):
        src = tmp_path / "src"
        src.mkdir()
        (src / "App.tsx").write_text(
            "import { BrowserRouter, Route } from 'react-router-dom';\n"
            "export default function App() { return <BrowserRouter /> }"
        )
        files = find_routing_files(tmp_path)
        assert len(files) >= 1

    def test_skips_node_modules(self, tmp_path: Path):
        nm = tmp_path / "node_modules" / "react" / "pages"
        nm.mkdir(parents=True)
        (nm / "index.js").write_text("module.exports = {}")
        files = find_routing_files(tmp_path)
        assert len(files) == 0

    def test_empty_project(self, tmp_path: Path):
        files = find_routing_files(tmp_path)
        assert files == []

    def test_limits_file_count(self, tmp_path: Path):
        """Should not return more than 15 files."""
        pages = tmp_path / "pages"
        pages.mkdir()
        for i in range(20):
            (pages / f"page{i}.tsx").write_text(f"export default function P{i}() {{}}")
        files = find_routing_files(tmp_path)
        assert len(files) <= 15

    def test_reads_file_content(self, tmp_path: Path):
        src = tmp_path / "src"
        src.mkdir()
        (src / "App.tsx").write_text("const routes = ['/home', '/about'];")
        files = find_routing_files(tmp_path)
        assert len(files) >= 1
        assert "routes" in files[0]["content"]
