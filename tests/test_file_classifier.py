"""Tests for file type classifier."""

from adam.validation.file_classifier import classify_file


class TestClassifyFile:
    # Path-based classification
    def test_test_file_prefix(self):
        assert classify_file("tests/test_models.py") == "test"

    def test_test_file_suffix(self):
        assert classify_file("src/models_test.py") == "test"

    def test_spec_file(self):
        assert classify_file("src/App.spec.ts") == "test"

    def test_dot_test(self):
        assert classify_file("src/utils.test.js") == "test"

    def test_conftest(self):
        assert classify_file("tests/conftest.py") == "test"

    def test_migration(self):
        assert classify_file("db/migrations/001_init.py") == "migration"

    def test_alembic(self):
        assert classify_file("alembic/versions/abc123.py") == "migration"

    def test_config_by_name(self):
        assert classify_file("src/config.py") == "config"

    def test_settings(self):
        assert classify_file("app/settings.py") == "config"

    def test_toml_config(self):
        assert classify_file("pyproject.toml") == "config"

    def test_yaml_config(self):
        assert classify_file("docker-compose.yml") == "config"

    def test_entry_point_main(self):
        assert classify_file("src/main.py") == "entry_point"

    def test_entry_point_app(self):
        assert classify_file("app.py") == "entry_point"

    def test_handler_by_path(self):
        assert classify_file("src/routes/users.py") == "handler"

    def test_controller_by_path(self):
        assert classify_file("app/controllers/auth.ts") == "handler"

    def test_views_by_path(self):
        assert classify_file("myapp/views.py") == "handler"

    def test_model_by_path(self):
        assert classify_file("src/models/user.py") == "model"

    def test_schema_by_path(self):
        assert classify_file("app/schemas/task.py") == "model"

    def test_utility_by_name(self):
        assert classify_file("src/utils.py") == "utility"

    def test_helpers_by_name(self):
        assert classify_file("lib/helpers.ts") == "utility"

    def test_general_fallback(self):
        assert classify_file("src/processor.py") == "general"

    # Content-based classification
    def test_handler_by_content(self):
        content = """
from flask import request, jsonify

@app.route("/users")
def get_users():
    return jsonify(users)
"""
        assert classify_file("src/users.py", content) == "handler"

    def test_model_by_content(self):
        content = """
from sqlalchemy.orm import mapped_column
from sqlalchemy import Column, String

class User(Base):
    class Meta:
        table_name = "users"
"""
        assert classify_file("src/user.py", content) == "model"

    def test_general_with_no_signals(self):
        content = "def process(data): return sorted(data)"
        assert classify_file("src/processor.py", content) == "general"
