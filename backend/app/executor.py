from __future__ import annotations

import asyncio
import os
from typing import AsyncIterator

from . import policy


async def execute_lines(
    lines: list[str],
    *,
    allow_docker: bool = True,
) -> AsyncIterator[str]:
    """Run approved lines with JIT policy check; yield log lines. Never uses shell=True."""
    if not allow_docker:
        yield "execution disabled (ALLOW_DOCKER_EXEC=false)"
        return

    for line in lines:
        reason = policy.jit_check_line(line)
        if reason:
            yield f"[blocked JIT] {line!r}: {reason}"
            continue
        parts = policy.split_line_argv(line)
        if not parts:
            yield f"[skip] could not parse: {line!r}"
            continue

        cmd = parts[0]
        if cmd == "docker":
            pass
        elif cmd in ("echo", "sleep"):
            pass
        else:
            yield f"[blocked] only docker/echo/sleep allowed, got: {cmd!r}"
            continue

        proc = await asyncio.create_subprocess_exec(
            *parts,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env={
                **os.environ,
                "DOCKER_HOST": os.environ.get("DOCKER_HOST", "unix:///var/run/docker.sock"),
            },
        )
        assert proc.stdout
        out = await proc.stdout.read()
        code = await proc.wait()
        text = out.decode("utf-8", errors="replace").strip()
        yield f"$ {line}\nexit={code}" + (f"\n{text}" if text else "")
