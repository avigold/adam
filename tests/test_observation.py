"""Tests for observation layer: API smoke testing and CLI verification."""

from pathlib import Path

import pytest

from adam.inspection.api_smoke import (
    EndpointSpec,
    SmokeTestResult,
    discover_endpoints_from_code,
)
from adam.inspection.cli_verify import (
    CLITestCase,
    CLITestResult,
    CLIVerifier,
    detect_cli_entry_point,
)

# ---------------------------------------------------------------------------
# API endpoint discovery
# ---------------------------------------------------------------------------


class TestDiscoverEndpoints:
    def test_discovers_flask_routes(self, tmp_path: Path):
        app_py = tmp_path / "app.py"
        app_py.write_text(
            "from flask import Flask\n"
            "app = Flask(__name__)\n\n"
            '@app.get("/users")\n'
            "def get_users(): pass\n\n"
            '@app.post("/users")\n'
            "def create_user(): pass\n"
        )
        endpoints = discover_endpoints_from_code(str(tmp_path))
        paths = {(ep.method, ep.path) for ep in endpoints}
        assert ("GET", "/users") in paths
        assert ("POST", "/users") in paths

    def test_discovers_fastapi_routes(self, tmp_path: Path):
        main_py = tmp_path / "main.py"
        main_py.write_text(
            "from fastapi import FastAPI\n"
            "app = FastAPI()\n\n"
            '@app.get("/items/{item_id}")\n'
            "def get_item(item_id: int): pass\n\n"
            '@app.delete("/items/{item_id}")\n'
            "def delete_item(item_id: int): pass\n"
        )
        endpoints = discover_endpoints_from_code(str(tmp_path))
        methods = {(ep.method, ep.path) for ep in endpoints}
        assert ("GET", "/items/{item_id}") in methods
        assert ("DELETE", "/items/{item_id}") in methods

    def test_discovers_django_paths(self, tmp_path: Path):
        urls = tmp_path / "urls.py"
        urls.write_text(
            "from django.urls import path\n"
            "urlpatterns = [\n"
            "    path('api/users/', views.user_list),\n"
            "    path('api/tasks/', views.task_list),\n"
            "]\n"
        )
        endpoints = discover_endpoints_from_code(str(tmp_path))
        paths = {ep.path for ep in endpoints}
        assert "/api/users" in paths
        assert "/api/tasks" in paths

    def test_includes_default_endpoints(self, tmp_path: Path):
        endpoints = discover_endpoints_from_code(str(tmp_path))
        paths = {ep.path for ep in endpoints}
        assert "/" in paths
        assert "/health" in paths

    def test_skips_node_modules(self, tmp_path: Path):
        nm = tmp_path / "node_modules" / "express" / "lib"
        nm.mkdir(parents=True)
        (nm / "routes.py").write_text('@app.get("/internal")\ndef x(): pass')
        endpoints = discover_endpoints_from_code(str(tmp_path))
        paths = {ep.path for ep in endpoints}
        assert "/internal" not in paths

    def test_no_duplicates(self, tmp_path: Path):
        app_py = tmp_path / "app.py"
        app_py.write_text(
            '@app.get("/users")\ndef a(): pass\n'
            '@app.get("/users")\ndef b(): pass\n'
        )
        endpoints = discover_endpoints_from_code(str(tmp_path))
        user_endpoints = [ep for ep in endpoints if ep.path == "/users"]
        assert len(user_endpoints) == 1


class TestEndpointSpec:
    def test_defaults(self):
        ep = EndpointSpec(method="GET", path="/api")
        assert ep.expected_status == 200
        assert ep.needs_auth is False
        assert ep.sample_body is None


class TestSmokeTestResult:
    def test_success_summary(self):
        ep = EndpointSpec(method="GET", path="/users")
        r = SmokeTestResult(
            endpoint=ep, status_code=200,
            response_time_ms=45, success=True,
        )
        assert "200" in r.summary
        assert "45ms" in r.summary

    def test_failure_summary(self):
        ep = EndpointSpec(method="POST", path="/users")
        r = SmokeTestResult(endpoint=ep, error="Connection refused")
        assert "FAILED" in r.summary
        assert "Connection refused" in r.summary


# ---------------------------------------------------------------------------
# CLI entry point detection
# ---------------------------------------------------------------------------


class TestDetectCLIEntryPoint:
    def test_explicit_config(self, tmp_path: Path):
        result = detect_cli_entry_point(
            str(tmp_path),
            build_system={"entry_point": "python -m myapp"},
        )
        assert result == "python -m myapp"

    def test_pyproject_scripts(self, tmp_path: Path):
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "myapp"\n\n'
            '[project.scripts]\n'
            'myapp = "myapp.cli:main"\n'
        )
        result = detect_cli_entry_point(str(tmp_path))
        assert result == "myapp"

    def test_package_json_bin(self, tmp_path: Path):
        import json
        (tmp_path / "package.json").write_text(json.dumps({
            "name": "mytool",
            "bin": {"mytool": "./bin/cli.js"},
        }))
        result = detect_cli_entry_point(str(tmp_path))
        assert result is not None
        assert "mytool" in result

    def test_cargo_toml(self, tmp_path: Path):
        (tmp_path / "Cargo.toml").write_text(
            '[package]\nname = "myrust"\nversion = "0.1.0"\n'
        )
        result = detect_cli_entry_point(str(tmp_path))
        assert result == "cargo run"

    def test_main_go(self, tmp_path: Path):
        (tmp_path / "main.go").write_text("package main\nfunc main() {}")
        result = detect_cli_entry_point(str(tmp_path))
        assert result == "go run ."

    def test_main_py(self, tmp_path: Path):
        (tmp_path / "main.py").write_text("print('hello')")
        result = detect_cli_entry_point(str(tmp_path))
        assert result == "python main.py"

    def test_no_entry_point(self, tmp_path: Path):
        result = detect_cli_entry_point(str(tmp_path))
        assert result is None


# ---------------------------------------------------------------------------
# CLI verification execution
# ---------------------------------------------------------------------------


class TestCLIVerifier:
    @pytest.mark.asyncio
    async def test_passing_command(self):
        verifier = CLIVerifier()
        results = await verifier.run_tests([
            CLITestCase(
                command="echo hello world",
                name="echo test",
                expected_exit_code=0,
                expected_output_contains=["hello"],
            ),
        ])
        assert len(results) == 1
        assert results[0].passed

    @pytest.mark.asyncio
    async def test_failing_exit_code(self):
        verifier = CLIVerifier()
        results = await verifier.run_tests([
            CLITestCase(
                command="exit 1",
                name="fail test",
                expected_exit_code=0,
            ),
        ])
        assert len(results) == 1
        assert not results[0].passed
        assert "exit code" in results[0].failure_reason.lower()

    @pytest.mark.asyncio
    async def test_expected_nonzero_exit(self):
        verifier = CLIVerifier()
        results = await verifier.run_tests([
            CLITestCase(
                command="exit 2",
                name="expected error",
                expected_exit_code=2,
            ),
        ])
        assert len(results) == 1
        assert results[0].passed

    @pytest.mark.asyncio
    async def test_output_contains_check(self):
        verifier = CLIVerifier()
        results = await verifier.run_tests([
            CLITestCase(
                command="echo 'version 1.2.3'",
                name="version check",
                expected_output_contains=["1.2.3"],
            ),
        ])
        assert results[0].passed

    @pytest.mark.asyncio
    async def test_output_not_contains_check(self):
        verifier = CLIVerifier()
        results = await verifier.run_tests([
            CLITestCase(
                command="echo 'all good'",
                name="no error",
                expected_output_not_contains=["ERROR", "FATAL"],
            ),
        ])
        assert results[0].passed

    @pytest.mark.asyncio
    async def test_default_test_cases(self):
        verifier = CLIVerifier()
        cases = verifier._default_test_cases("python myapp.py")
        assert len(cases) >= 2
        assert any("--help" in tc.command for tc in cases)


class TestCLITestResult:
    def test_summary_pass(self):
        r = CLITestResult(
            test_case=CLITestCase(command="echo hi", name="echo"),
            exit_code=0, passed=True, duration_seconds=0.1,
        )
        assert "PASS" in r.summary

    def test_summary_fail(self):
        r = CLITestResult(
            test_case=CLITestCase(command="bad", name="bad cmd"),
            exit_code=1, passed=False, duration_seconds=0.5,
        )
        assert "FAIL" in r.summary
