"""API smoke testing — hit endpoints and verify responses.

For projects with HTTP APIs (but no browser UI), this is the primary
observation method. Discovers endpoints from code, generates test
requests, verifies responses.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from adam.execution.runner import ShellRunner

logger = logging.getLogger(__name__)


@dataclass
class EndpointSpec:
    """A discovered API endpoint to smoke test."""
    method: str  # GET, POST, PUT, DELETE
    path: str  # /api/users, /health
    name: str = ""
    description: str = ""
    sample_body: dict[str, Any] | None = None
    expected_status: int = 200
    needs_auth: bool = False


@dataclass
class SmokeTestResult:
    """Result of hitting a single endpoint."""
    endpoint: EndpointSpec
    status_code: int = 0
    response_body: str = ""
    response_time_ms: float = 0
    success: bool = False
    error: str = ""

    @property
    def summary(self) -> str:
        if self.success:
            return (
                f"{self.endpoint.method} {self.endpoint.path} "
                f"-> {self.status_code} ({self.response_time_ms:.0f}ms)"
            )
        return (
            f"{self.endpoint.method} {self.endpoint.path} "
            f"-> FAILED: {self.error}"
        )


class APISmoker:
    """Runs smoke tests against a running API server."""

    def __init__(self, runner: ShellRunner | None = None) -> None:
        self._runner = runner or ShellRunner()

    async def smoke_test(
        self,
        base_url: str,
        endpoints: list[EndpointSpec],
        timeout: int = 10,
    ) -> list[SmokeTestResult]:
        """Hit each endpoint and collect results."""
        results: list[SmokeTestResult] = []

        for ep in endpoints:
            result = await self._test_endpoint(base_url, ep, timeout)
            results.append(result)

        return results

    async def _test_endpoint(
        self,
        base_url: str,
        ep: EndpointSpec,
        timeout: int,
    ) -> SmokeTestResult:
        """Test a single endpoint via curl."""
        url = f"{base_url.rstrip('/')}{ep.path}"

        # Build curl command
        parts = ["curl", "-s", "-o", "-", "-w", r"\n%{http_code}\n%{time_total}"]
        parts.extend(["-X", ep.method])

        if ep.sample_body is not None:
            parts.extend([
                "-H", "Content-Type: application/json",
                "-d", json.dumps(ep.sample_body),
            ])

        parts.append(url)
        cmd = " ".join(_shell_quote(p) for p in parts)

        exec_result = await self._runner.run(cmd, timeout=timeout)

        if exec_result.timed_out:
            return SmokeTestResult(
                endpoint=ep,
                error=f"Timed out after {timeout}s",
            )

        if not exec_result.success and not exec_result.stdout:
            return SmokeTestResult(
                endpoint=ep,
                error=exec_result.stderr or "Connection failed",
            )

        # Parse curl output: body\nstatus_code\ntime
        lines = exec_result.stdout.strip().split("\n")
        if len(lines) >= 2:
            try:
                status_code = int(lines[-2])
                time_total = float(lines[-1])
                body = "\n".join(lines[:-2])

                ok = (
                    status_code == ep.expected_status
                    or (ep.expected_status == 200 and 200 <= status_code < 300)
                )

                return SmokeTestResult(
                    endpoint=ep,
                    status_code=status_code,
                    response_body=body[:5000],
                    response_time_ms=time_total * 1000,
                    success=ok,
                    error="" if ok else f"Expected {ep.expected_status}, got {status_code}",
                )
            except (ValueError, IndexError):
                pass

        return SmokeTestResult(
            endpoint=ep,
            error=f"Unexpected curl output: {exec_result.stdout[:200]}",
        )

    async def quick_health_check(self, base_url: str) -> bool:
        """Quick check if the server is responding."""
        result = await self._test_endpoint(
            base_url,
            EndpointSpec(method="GET", path="/", name="root"),
            timeout=5,
        )
        # Any response (even 404) means the server is up
        return result.status_code > 0


# ---------------------------------------------------------------------------
# Endpoint discovery from code (heuristic, not LLM-based)
# ---------------------------------------------------------------------------

# Common health/meta endpoints to always test
DEFAULT_ENDPOINTS: list[EndpointSpec] = [
    EndpointSpec(method="GET", path="/", name="root"),
    EndpointSpec(method="GET", path="/health", name="health"),
    EndpointSpec(method="GET", path="/api", name="api_root"),
]


def discover_endpoints_from_code(
    project_root: str,
    tech_stack: dict | None = None,
) -> list[EndpointSpec]:
    """Discover API endpoints by scanning route definitions in code.

    This is a heuristic scan — the route discoverer agent does deeper
    analysis. This catches the obvious cases without an LLM call.
    """
    import re
    from pathlib import Path

    root = Path(project_root)
    endpoints: list[EndpointSpec] = list(DEFAULT_ENDPOINTS)
    seen: set[tuple[str, str]] = {(ep.method, ep.path) for ep in endpoints}

    # Scan Python files for route decorators
    for py_file in root.rglob("*.py"):
        if _should_skip(py_file):
            continue
        try:
            content = py_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        # Flask/FastAPI: @app.get("/path"), @router.post("/path")
        for match in re.finditer(
            r'@(?:app|router)\.(get|post|put|delete|patch)\s*\(\s*["\']([^"\']+)',
            content,
            re.IGNORECASE,
        ):
            method = match.group(1).upper()
            path = match.group(2)
            if (method, path) not in seen:
                endpoints.append(EndpointSpec(
                    method=method, path=path,
                    name=path.strip("/").replace("/", "_") or "root",
                ))
                seen.add((method, path))

        # Django: path("api/users/", views.user_list)
        for match in re.finditer(
            r'path\s*\(\s*["\']([^"\']*)["\']',
            content,
        ):
            path = "/" + match.group(1).strip("/")
            if ("GET", path) not in seen and path != "/":
                endpoints.append(EndpointSpec(
                    method="GET", path=path, name=path.strip("/").replace("/", "_"),
                ))
                seen.add(("GET", path))

    # Scan JS/TS files for express-style routes
    for js_file in root.rglob("*.{ts,js}"):
        if _should_skip(js_file):
            continue
        try:
            content = js_file.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue

        for match in re.finditer(
            r'(?:app|router)\.(get|post|put|delete|patch)\s*\(\s*["\']([^"\']+)',
            content,
            re.IGNORECASE,
        ):
            method = match.group(1).upper()
            path = match.group(2)
            if (method, path) not in seen:
                endpoints.append(EndpointSpec(
                    method=method, path=path,
                    name=path.strip("/").replace("/", "_") or "root",
                ))
                seen.add((method, path))

    return endpoints


def _should_skip(path: Any) -> bool:
    """Check if a file path should be skipped during scanning."""
    parts = str(path).split("/")
    skip_dirs = {
        "node_modules", ".venv", "__pycache__", "dist",
        "build", ".git", ".adam",
    }
    return any(p in skip_dirs for p in parts)


def _shell_quote(s: str) -> str:
    """Quote a string for shell usage."""
    if " " not in s and '"' not in s and "'" not in s:
        return s
    return "'" + s.replace("'", "'\\''") + "'"
