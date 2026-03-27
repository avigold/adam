"""Route discoverer — extracts pages/routes from a codebase for visual inspection.

Uses Sonnet. Reads routing configuration files and returns a list of
routes with descriptions, so the screenshotter knows what URLs to visit.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from adam.agents.base import BaseAgent
from adam.types import AgentContext, ModelTier

logger = logging.getLogger(__name__)


class DiscoveredRoute(BaseModel):
    """A discovered route/page to screenshot."""
    path: str  # URL path, e.g. "/", "/about", "/users/:id"
    name: str  # Human-readable name for the screenshot file
    description: str = ""
    needs_auth: bool = False
    needs_data: bool = False  # Needs seed data to look meaningful
    actions: list[dict[str, str]] = []  # Pre-screenshot actions


class RouteDiscoveryResponse(BaseModel):
    """Routes discovered in the codebase."""
    routes: list[DiscoveredRoute]
    framework: str = ""  # Detected framework
    notes: Any = ""


class RouteDiscoverer(BaseAgent):
    """Discovers routes/pages in a web project for visual inspection."""

    role = "route_discoverer"
    model_tier = ModelTier.SONNET
    response_model = RouteDiscoveryResponse
    use_tool_call = False

    def build_system_prompt(self, context: AgentContext) -> str:
        return (
            "You are a route discovery agent. Given source files from a "
            "web application, you identify all user-facing pages/routes "
            "that should be visually inspected. Respond with JSON. "
            "Do not wrap in markdown code fences."
        )

    def build_user_message(self, context: AgentContext) -> str:
        parts = ["## Project\n", context.project_description, ""]

        if context.tech_stack:
            parts.append("## Tech Stack")
            for k, v in context.tech_stack.items():
                parts.append(f"- {k}: {v}")
            parts.append("")

        # Include routing-relevant source files
        routing_files = context.extra.get("routing_files", [])
        if routing_files:
            parts.append("## Routing Files\n")
            for rf in routing_files:
                parts.append(f"### {rf['path']}")
                parts.append(f"```\n{rf['content']}\n```\n")

        parts.append(
            "## Task\n"
            "Identify all user-facing routes/pages in this application. "
            "For each route, provide:\n"
            "- **path**: The URL path (e.g., '/', '/about', '/users')\n"
            "- **name**: A short name for the screenshot file\n"
            "- **description**: What this page shows\n"
            "- **needs_auth**: Whether the page requires login\n"
            "- **needs_data**: Whether seed data is needed\n"
            "- **actions**: Any actions to perform before screenshotting "
            '(e.g., [{"type": "click", "selector": "#tab-2"}])\n\n'
            "Focus on distinct visual states. Include the home page, "
            "key feature pages, error states if they have custom UI. "
            "Skip API-only routes.\n\n"
            "Also report the detected framework and any notes.\n\n"
            "Respond with JSON: "
            '{"routes": [...], "framework": "...", "notes": "..."}'
        )
        return "\n".join(parts)


    def _temperature(self) -> float:
        return 0.3


# ---------------------------------------------------------------------------
# File scanning for routing files
# ---------------------------------------------------------------------------

# Patterns that indicate routing configuration
_ROUTING_PATTERNS: dict[str, list[str]] = {
    # Next.js (app router)
    "next.js-app": ["app/**/page.tsx", "app/**/page.jsx", "app/**/page.js"],
    # Next.js (pages router)
    "next.js-pages": ["pages/**/*.tsx", "pages/**/*.jsx", "pages/**/*.js"],
    # React Router
    "react-router": [
        "src/App.tsx", "src/App.jsx", "src/App.js",
        "src/router.tsx", "src/router.ts", "src/routes.tsx", "src/routes.ts",
    ],
    # Vue Router
    "vue-router": ["src/router/index.ts", "src/router/index.js", "src/router.ts"],
    # Svelte Kit
    "svelte-kit": ["src/routes/**/+page.svelte"],
    # Django
    "django": ["**/urls.py"],
    # Flask
    "flask": ["app.py", "views.py", "**/views.py"],
    # Rails
    "rails": ["config/routes.rb"],
    # Static
    "static": ["*.html", "public/*.html", "dist/*.html"],
}


def find_routing_files(
    project_root: str | Path,
    tech_stack: dict | None = None,
) -> list[dict[str, str]]:
    """Find files that define routes in the project.

    Returns list of {"path": ..., "content": ...} dicts.
    """
    root = Path(project_root)
    results: list[dict[str, str]] = []
    seen: set[str] = set()

    # Check all patterns
    for _fw_name, patterns in _ROUTING_PATTERNS.items():
        for pattern in patterns:
            for p in root.glob(pattern):
                if not p.is_file():
                    continue
                rel = str(p.relative_to(root))
                if rel in seen:
                    continue
                # Skip node_modules, .venv, etc
                if any(
                    part.startswith(".")
                    or part in ("node_modules", ".venv", "__pycache__", "dist", "build")
                    for part in p.parts
                ):
                    continue
                try:
                    content = p.read_text(encoding="utf-8")
                    # Only include files that look like routing config
                    if len(content) > 50_000:
                        content = content[:50_000] + "\n[truncated]"
                    results.append({"path": rel, "content": content})
                    seen.add(rel)
                except (OSError, UnicodeDecodeError):
                    continue

    # Limit total files to avoid blowing context
    return results[:15]
