"""Tests for file loop extras: test path inference, acceptance with test gen."""

from adam.orchestrator.file_loop import _infer_test_path


class TestInferTestPath:
    def test_python_file(self):
        assert _infer_test_path("src/models.py") == "tests/test_models.py"

    def test_python_nested(self):
        assert _infer_test_path("src/api/routes.py") == "tests/test_routes.py"

    def test_typescript(self):
        assert _infer_test_path("src/utils.ts") == "tests/utils.test.ts"

    def test_javascript(self):
        assert _infer_test_path("lib/helpers.js") == "tests/helpers.test.js"

    def test_tsx(self):
        assert _infer_test_path("src/App.tsx") == "tests/App.test.tsx"

    def test_rust(self):
        assert _infer_test_path("src/parser.rs") == "tests/test_parser.rs"

    def test_go(self):
        assert _infer_test_path("pkg/handler.go") == "pkg/handler_test.go"

    def test_skip_init(self):
        assert _infer_test_path("src/__init__.py") is None

    def test_skip_main(self):
        assert _infer_test_path("src/__main__.py") is None

    def test_skip_setup(self):
        assert _infer_test_path("setup.py") is None

    def test_skip_conftest(self):
        assert _infer_test_path("tests/conftest.py") is None

    def test_skip_test_file(self):
        assert _infer_test_path("tests/test_models.py") is None

    def test_skip_spec_file(self):
        assert _infer_test_path("src/app.spec.ts") is None

    def test_skip_dottest_file(self):
        assert _infer_test_path("src/app.test.js") is None

    def test_unknown_extension(self):
        result = _infer_test_path("src/schema.graphql")
        assert result == "tests/test_schema.graphql"
