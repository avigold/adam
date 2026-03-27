"""File type classifier — infers the role of a source file for targeted evaluation.

Uses filename patterns and content heuristics to classify files as:
handler, model, utility, config, test, migration, or general.
This allows critics to apply role-specific evaluation criteria.
"""

from __future__ import annotations

# File type categories
FILE_TYPES = {
    "handler",      # HTTP handlers, API routes, controllers
    "model",        # Database models, schemas, data classes
    "utility",      # Helper functions, pure logic, libraries
    "config",       # Configuration, settings, environment
    "test",         # Test files
    "migration",    # Database migrations
    "entry_point",  # Main entry points, CLI commands
    "general",      # Default when no specific type detected
}


def classify_file(
    path: str,
    content: str = "",
) -> str:
    """Classify a source file by its role in the project.

    Uses path patterns first, then content heuristics.
    """
    path_lower = path.lower()
    name_part = path_lower.rsplit("/", 1)[-1] if "/" in path_lower else path_lower
    stem = name_part.rsplit(".", 1)[0]

    # Tests — check first, most distinctive
    if (
        stem.startswith("test_")
        or stem.endswith("_test")
        or ".test." in path_lower
        or ".spec." in path_lower
        or "/tests/" in path_lower
        or "/test/" in path_lower
        or stem == "conftest"
    ):
        return "test"

    # Migrations
    if (
        "migrations/" in path_lower
        or "migrate/" in path_lower
        or "alembic/" in path_lower
        or stem.startswith("migration")
    ):
        return "migration"

    # Config
    if stem in (
        "config", "settings", "conf", "env", "environment",
        "setup", "pyproject", "package", "tsconfig", "webpack",
        "vite.config", "next.config", "tailwind.config",
    ) or path_lower.endswith((".ini", ".toml", ".yaml", ".yml", ".env")):
        return "config"

    # Entry points
    if stem in ("main", "app", "index", "server", "cli", "__main__"):
        return "entry_point"

    # Path-based handler detection
    handler_path_patterns = (
        "routes/", "handlers/", "controllers/", "views/",
        "endpoints/", "api/", "pages/",
    )
    handler_names = ("routes", "handlers", "controllers", "views", "endpoints")
    if any(p in path_lower for p in handler_path_patterns) or stem in handler_names:
        return "handler"

    # Path-based model detection
    model_path_patterns = (
        "models/", "schemas/", "entities/",
    )
    model_names = ("models", "schemas", "entities")
    if any(p in path_lower for p in model_path_patterns) or stem in model_names:
        return "model"

    # Content-based classification (if path isn't conclusive)
    if content:
        content_lower = content.lower()

        # Handler heuristics
        handler_signals = [
            "def get(", "def post(", "def put(", "def delete(",
            "@app.route", "@router.", "request.", "response.",
            "req, res", "async def handler",
            "httpresponse", "jsonresponse", "jsonify",
            "export default function page",
            "from flask", "from fastapi", "from django",
            "from express", "app.get(", "app.post(",
        ]
        if sum(1 for s in handler_signals if s in content_lower) >= 2:
            return "handler"

        # Model heuristics
        model_signals = [
            "class meta:", "db.model", "base.metadata",
            "mapped_column", "column(", "field(",
            "schema", "serializer",
            "interface ", "type ",  # TypeScript type definitions
        ]
        if sum(1 for s in model_signals if s in content_lower) >= 2:
            return "model"

    # Filename-based utility detection
    utility_names = (
        "util", "utils", "helper", "helpers", "lib",
        "common", "shared", "tools", "support",
    )
    if any(stem == n or stem.startswith(n + "_") for n in utility_names):
        return "utility"

    return "general"
