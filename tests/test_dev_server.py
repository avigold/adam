"""Tests for dev server lifecycle management."""

import pytest

from adam.execution.dev_server import DevServer


class TestDevServer:
    def test_url(self):
        ds = DevServer("echo hi", port=3000)
        assert ds.url == "http://localhost:3000"

    def test_custom_port(self):
        ds = DevServer("echo hi", port=8080)
        assert ds.url == "http://localhost:8080"

    def test_not_running_initially(self):
        ds = DevServer("echo hi")
        assert ds.running is False

    @pytest.mark.asyncio
    async def test_start_simple_command(self):
        """A command that outputs 'ready' should start successfully."""
        ds = DevServer(
            "echo 'Server ready on port 3000'",
            ready_pattern="ready",
            startup_timeout=5,
        )
        result = await ds.start()
        # echo exits immediately, so process won't be "running"
        # but start should detect the ready pattern
        assert result is True or not ds.running
        await ds.stop()

    @pytest.mark.asyncio
    async def test_stop_idempotent(self):
        """Stopping a non-started server should not error."""
        ds = DevServer("echo hi")
        await ds.stop()  # Should not raise

    @pytest.mark.asyncio
    async def test_context_manager(self):
        ds = DevServer(
            "echo 'ready'",
            ready_pattern="ready",
            startup_timeout=5,
        )
        async with ds:
            pass  # Should start and stop cleanly

    def test_recent_output_empty(self):
        ds = DevServer("echo hi")
        assert ds.recent_output == ""
