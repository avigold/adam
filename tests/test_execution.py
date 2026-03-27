"""Tests for shell execution runner."""

import pytest

from adam.execution.runner import ExecutionResult, ShellRunner


class TestExecutionResult:
    def test_success(self):
        r = ExecutionResult(command="echo hi", return_code=0, stdout="hi\n", stderr="")
        assert r.success is True
        assert r.output == "hi"

    def test_failure(self):
        r = ExecutionResult(command="false", return_code=1, stdout="", stderr="error msg")
        assert r.success is False
        assert "error msg" in r.output

    def test_timeout(self):
        r = ExecutionResult(
            command="sleep 100", return_code=-1,
            stdout="", stderr="", timed_out=True,
        )
        assert r.success is False
        assert r.timed_out is True


class TestShellRunner:
    @pytest.mark.asyncio
    async def test_echo(self):
        runner = ShellRunner()
        result = await runner.run("echo hello")
        assert result.success
        assert "hello" in result.stdout

    @pytest.mark.asyncio
    async def test_failure(self):
        runner = ShellRunner()
        result = await runner.run("exit 1")
        assert not result.success
        assert result.return_code == 1

    @pytest.mark.asyncio
    async def test_timeout(self):
        runner = ShellRunner()
        result = await runner.run("sleep 10", timeout=1)
        assert result.timed_out
        assert not result.success

    @pytest.mark.asyncio
    async def test_cwd(self, tmp_path):
        runner = ShellRunner()
        result = await runner.run("pwd", cwd=str(tmp_path))
        assert result.success
        assert str(tmp_path) in result.stdout

    @pytest.mark.asyncio
    async def test_stderr_captured(self):
        runner = ShellRunner()
        result = await runner.run("echo error >&2")
        assert "error" in result.stderr

    @pytest.mark.asyncio
    async def test_run_test_convenience(self):
        runner = ShellRunner()
        result = await runner.run_test("echo 'tests pass'")
        assert result.success

    @pytest.mark.asyncio
    async def test_run_lint_convenience(self):
        runner = ShellRunner()
        result = await runner.run_lint("echo 'lint ok'")
        assert result.success

    @pytest.mark.asyncio
    async def test_duration_tracked(self):
        runner = ShellRunner()
        result = await runner.run("echo fast")
        assert result.duration_seconds >= 0
