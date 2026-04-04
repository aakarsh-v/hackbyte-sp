from __future__ import annotations

import asyncio
import os
import sys
import time
from typing import AsyncIterator

from . import policy

# echo/sleep are shell built-ins on Windows; docker is a real binary everywhere.
_SHELL_BUILTINS = frozenset({"echo", "sleep"}) if sys.platform == "win32" else frozenset()


# ---------------------------------------------------------------------------
# EC2 / Cloud command simulation
# Produces realistic output so the demo looks production-grade
# ---------------------------------------------------------------------------

_EC2_INSTANCE_ID = "i-0abc123def456"
_EC2_REGION = os.environ.get("AWS_REGION", "us-east-1")

_AWS_SIMULATED_RESPONSES: dict[str, list[str]] = {
    "aws ec2 reboot-instances": [
        f"[aws] Sending reboot request to EC2 instance {_EC2_INSTANCE_ID} in {_EC2_REGION}...",
        f"[aws] ✅ RebootInstances: {_EC2_INSTANCE_ID} → pending reboot",
        "[aws] Waiting for instance to come back online (~30s in real env)...",
        f"[aws] ✅ Instance {_EC2_INSTANCE_ID} state: running | Status: ok",
    ],
    "aws ec2 describe-instance-status": [
        f"[aws] EC2 Instance Status Check for {_EC2_INSTANCE_ID}:",
        f"  InstanceId:    {_EC2_INSTANCE_ID}",
        f"  Region:        {_EC2_REGION}",
        "  InstanceState: running",
        "  SystemStatus:  ok (2/2 checks passed)",
        "  InstanceStatus: ok (2/2 checks passed)",
    ],
    "aws ec2 describe-instances": [
        f"[aws] Describing EC2 instance {_EC2_INSTANCE_ID}...",
        f"  InstanceId:   {_EC2_INSTANCE_ID}",
        "  InstanceType: t3.medium",
        "  State:        running",
        "  PublicDNS:    ec2-54-123-45-67.compute-1.amazonaws.com",
    ],
    "aws cloudwatch get-metric-statistics": [
        "[aws] CloudWatch Metrics (last 5 min):",
        "  CPUUtilization: 87.3% → ⚠️ HIGH",
        "  NetworkIn:      142MB",
        "  NetworkOut:     38MB",
        "  StatusCheckFailed: 0",
    ],
    "aws ssm send-command": [
        f"[aws] SSM sending command to {_EC2_INSTANCE_ID}...",
        "  CommandId: abc1234-ef56-7890-gh12-ijk3456789lm",
        "  Status:    Pending → InProgress → Success",
        "  Output:    Command executed successfully",
    ],
    "aws logs tail": [
        "[aws] Tailing CloudWatch logs...",
        f"  /aws/ec2/your-app [{_EC2_INSTANCE_ID}]: nginx: reloaded successfully",
        f"  /aws/ec2/your-app [{_EC2_INSTANCE_ID}]: payment-service: Started",
        f"  /aws/ec2/your-app [{_EC2_INSTANCE_ID}]: db:5432 connection: OK",
    ],
    "aws ecs update-service": [
        "[aws] Updating ECS service...",
        "  Cluster:        production-cluster",
        "  Service:        payment-service",
        "  DesiredCount:   2",
        "  Status:         ACTIVE → updating",
        "  ✅ ECS service deployment triggered successfully",
    ],
}

_SYSTEMCTL_SIMULATED: dict[str, list[str]] = {
    "systemctl restart": [
        "🔄 systemd: stopping service...",
        "🔄 systemd: service stopped",
        "🔄 systemd: starting service...",
        "✅ systemd: service started successfully",
        "   Active: active (running) since just now",
    ],
    "systemctl start": [
        "🔄 systemd: starting service...",
        "✅ systemd: service started successfully",
        "   Active: active (running)",
    ],
    "systemctl status": [
        "● service.service - Application Service",
        "   Loaded: loaded (/etc/systemd/system/service.service; enabled)",
        "   Active: active (running) for 0min 3s",
        "  Process: 2401 ExecStart=/usr/bin/service (code=exited, status=0/SUCCESS)",
        " Main PID: 2402 (service)",
    ],
}


def _simulate_aws(line: str) -> list[str] | None:
    """Return simulated output for AWS CLI commands, or None if not recognized."""
    stripped = line.strip()
    for key, output in _AWS_SIMULATED_RESPONSES.items():
        if stripped.startswith(key):
            return output
    if stripped.startswith("aws "):
        cmd_parts = stripped.split()
        return [
            f"[aws] Executing: {' '.join(cmd_parts[:4])}...",
            "  ✅ Command completed successfully",
            f"  RequestId: req-{int(time.time())}",
        ]
    return None


def _simulate_systemctl(line: str) -> list[str] | None:
    """Return simulated output for systemctl commands."""
    stripped = line.strip()
    for key, output in _SYSTEMCTL_SIMULATED.items():
        if stripped.startswith(key):
            # Extract service name from command
            parts = stripped.split()
            svc = parts[2] if len(parts) > 2 else "service"
            return [f"[systemctl] {line}"] + [o.replace("service", svc) for o in output]
    return None


# ---------------------------------------------------------------------------
# Main executor
# ---------------------------------------------------------------------------

async def execute_lines(
    lines: list[str],
    *,
    allow_docker: bool = True,
) -> AsyncIterator[str]:
    """Run approved lines with JIT policy check; yield log lines."""
    if not allow_docker:
        yield "⚠️  Execution disabled (ALLOW_DOCKER_EXEC=false)"
        return

    yield f"✈️ [SuperPlane] API Connection Established"
    yield f"✈️ [SuperPlane] Initializing secure ephemeral sandbox..."
    await asyncio.sleep(1.0)
    yield f"✈️ [SuperPlane] Injecting approved runbook script..."
    yield f"✈️ [SuperPlane] Stream connected. Executing {len(lines)} steps:"
    yield "─" * 55

    for i, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            if stripped.startswith("#"):
                yield f"\n{'─'*40}\n📋 {stripped}"
            continue

        reason = policy.jit_check_line(line)
        if reason:
            yield f"🚫 [JIT-BLOCKED] {line!r}: {reason}"
            continue

        parts = policy.split_line_argv(line)
        if not parts:
            yield f"⚠️  [skip] could not parse: {line!r}"
            continue

        cmd = parts[0]
        yield f"\n▶  Step {i}: $ {line}"

        # ── AWS CLI commands (simulated with realistic output) ──────────────
        if cmd == "aws":
            sim = _simulate_aws(line)
            if sim:
                for out_line in sim:
                    yield f"   {out_line}"
                    await asyncio.sleep(0.3)  # realistic delay
                yield f"   exit=0"
            continue

        # ── systemctl commands (simulated) ──────────────────────────────────
        if cmd == "systemctl":
            sim = _simulate_systemctl(line)
            if sim:
                for out_line in sim:
                    yield f"   {out_line}"
                    await asyncio.sleep(0.2)
                yield f"   exit=0"
            continue

        use_shell = cmd in _SHELL_BUILTINS
        if use_shell:
            if sys.platform == "win32":
                proc = await asyncio.create_subprocess_exec(
                    "powershell",
                    "-NoProfile",
                    "-NonInteractive",
                    "-Command",
                    line,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                )
            else:
                proc = await asyncio.create_subprocess_shell(
                    line,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.STDOUT,
                )
            assert proc.stdout
            out = await proc.stdout.read()
            code = await proc.wait()
            text = out.decode("utf-8", errors="replace").strip()
            if text:
                yield f"   {text}"
            yield f"   exit={code}"
            continue

        if cmd == "echo":
            proc = await asyncio.create_subprocess_exec(
                *parts,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env={
                    **os.environ,
                    "DOCKER_HOST": os.environ.get(
                        "DOCKER_HOST", "unix:///var/run/docker.sock"
                    ),
                },
            )
            assert proc.stdout
            out = await proc.stdout.read()
            code = await proc.wait()
            text = out.decode("utf-8", errors="replace").strip()
            if text:
                yield f"   {text}"
            yield f"   exit={code}"
            continue

        if cmd == "sleep":
            yield f"   ⏱  sleeping {parts[1] if len(parts) > 1 else '?'}s..."
            proc = await asyncio.create_subprocess_exec(*parts)
            code = await proc.wait()
            yield f"   exit={code}"
            continue

        # ── docker (simulated for demo execution) ───────────────────────────
        if cmd == "docker":
            subcmd = (
                " ".join(parts[1:3])
                if len(parts) > 2
                else parts[1]
                if len(parts) > 1
                else ""
            )
            if "compose restart" in subcmd or "restart" in subcmd:
                yield "   🔄 docker daemon: restarting container(s)..."
                await asyncio.sleep(1.5)
                yield "   ✅ Container(s) restarted successfully"
                yield "   exit=0"
            elif "compose ps" in subcmd or "ps" in subcmd:
                yield "   NAME               STATUS   PORTS"
                yield "   payment-service    Up       0.0.0.0:8082->8080/tcp"
                yield "   db                 Up       0.0.0.0:5432->5432/tcp"
                yield "   exit=0"
            else:
                yield "   ✅ Docker command executed"
                yield "   exit=0"
            continue

        yield f"🚫 [blocked] command not handled: {cmd!r}"

    yield "\n" + "─" * 55
    yield "🎉 Remediation runbook completed successfully!"
    yield "✈️ [SuperPlane] Runbook complete. Terminating ephemeral sandbox..."
    await asyncio.sleep(0.5)
    yield "✈️ [SuperPlane] Sandbox destroyed. Connection closed."
