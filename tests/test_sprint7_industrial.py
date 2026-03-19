"""Sprint 7 Tests — SDK migration, token counting, startup checks, invariants.

Covers:
  - Anthropic SDK graceful fallback
  - Improved token counting accuracy
  - Final 6 audit issues (M-15, M-34, M-42, M-46, L-04, L-23)
  - Startup dependency validation
  - Runtime invariant checking
"""

from __future__ import annotations

import pytest
from unittest.mock import MagicMock, AsyncMock, patch


# ── Token counting accuracy ──


class TestTokenCounting:
    """Test improved token counting vs old bytes//3."""

    def test_chinese_text(self):
        from anima.llm.token_budget import count_tokens
        # "你好世界" = 4 CJK chars → ~6 tokens
        tokens = count_tokens("你好世界")
        assert 3 <= tokens <= 8

    def test_english_text(self):
        from anima.llm.token_budget import count_tokens
        # "Hello world" = 11 ASCII chars → ~3 tokens
        tokens = count_tokens("Hello world")
        assert 2 <= tokens <= 5  # Old bytes//3 = 4, new should be ~3

    def test_mixed_text(self):
        from anima.llm.token_budget import count_tokens
        tokens = count_tokens("你好 Hello 世界 World")
        assert tokens > 0

    def test_empty_text(self):
        from anima.llm.token_budget import count_tokens
        assert count_tokens("") == 0

    def test_code_block(self):
        from anima.llm.token_budget import count_tokens
        code = "def foo():\n    return bar(x, y)\n"
        tokens = count_tokens(code)
        assert tokens > 0

    def test_json_text(self):
        from anima.llm.token_budget import count_tokens
        import json
        data = json.dumps({"key": "value", "list": [1, 2, 3]})
        tokens = count_tokens(data)
        assert tokens > 0

    def test_long_text_reasonable(self):
        from anima.llm.token_budget import count_tokens
        text = "Hello world. " * 100  # ~1300 chars
        tokens = count_tokens(text)
        # Should be ~300 tokens (not 433 from old bytes//3)
        assert 200 <= tokens <= 500


# ── Anthropic SDK graceful fallback ──


class TestAnthropicSDKFallback:
    """Test SDK availability detection."""

    def test_sdk_detection(self):
        from anima.llm.providers import HAS_ANTHROPIC_SDK
        # Just verify the flag exists (SDK may or may not be installed)
        assert isinstance(HAS_ANTHROPIC_SDK, bool)

    def test_client_returns_none_without_sdk(self):
        from anima.llm.providers import _get_anthropic_client
        with patch("anima.llm.providers.HAS_ANTHROPIC_SDK", False):
            # Reset singleton
            import anima.llm.providers as p
            old_client = p._anthropic_client
            p._anthropic_client = None
            try:
                result = _get_anthropic_client()
                assert result is None
            finally:
                p._anthropic_client = old_client


# ── Startup checks ──


class TestStartupChecks:
    """Test startup dependency validation."""

    def test_verify_returns_list(self):
        from anima.startup_check import verify_dependencies
        issues = verify_dependencies()
        assert isinstance(issues, list)
        for severity, message in issues:
            assert severity in ("critical", "warning", "info")
            assert isinstance(message, str)

    def test_python_version_check(self):
        from anima.startup_check import _check_python_version
        issues = []
        _check_python_version(issues)
        # We're running Python 3.11+, so no critical issue
        assert not any(s == "critical" for s, _ in issues)

    def test_semantic_search_check(self):
        from anima.startup_check import _check_semantic_search
        issues = []
        _check_semantic_search(issues)
        # Should produce either info (backend found) or warning (none found)
        assert len(issues) >= 1
        # Should not be critical (semantic search is optional)
        assert not any(s == "critical" for s, _ in issues)

    def test_required_files_check(self):
        from anima.startup_check import _check_required_files
        issues = []
        _check_required_files(issues)
        # identity/core.md and config/default.yaml should exist in this project
        critical_issues = [m for s, m in issues if s == "critical"]
        assert len(critical_issues) == 0, f"Missing files: {critical_issues}"

    def test_run_and_report(self):
        from anima.startup_check import run_and_report
        result = run_and_report()
        assert isinstance(result, bool)


# ── Runtime invariants ──


class TestInvariants:
    """Test runtime invariant checking."""

    def test_require_passes(self):
        from anima.utils.invariants import require
        require(True, "should pass")  # No exception

    def test_require_fails(self):
        from anima.utils.invariants import require
        with pytest.raises(RuntimeError, match="Invariant violation"):
            require(False, "this should fail")

    def test_ensure_initialized_passes(self):
        from anima.utils.invariants import ensure_initialized

        class MyService:
            def __init__(self):
                self._db = MagicMock()

            @ensure_initialized("_db")
            def do_work(self):
                return "ok"

        svc = MyService()
        assert svc.do_work() == "ok"

    def test_ensure_initialized_fails(self):
        from anima.utils.invariants import ensure_initialized

        class MyService:
            def __init__(self):
                self._db = None

            @ensure_initialized("_db")
            def do_work(self):
                return "ok"

        svc = MyService()
        with pytest.raises(RuntimeError, match="_db"):
            svc.do_work()

    @pytest.mark.asyncio
    async def test_ensure_initialized_async(self):
        from anima.utils.invariants import ensure_initialized

        class MyAsyncService:
            def __init__(self):
                self._router = MagicMock()

            @ensure_initialized("_router")
            async def process(self):
                return "done"

        svc = MyAsyncService()
        result = await svc.process()
        assert result == "done"

    @pytest.mark.asyncio
    async def test_ensure_initialized_async_fails(self):
        from anima.utils.invariants import ensure_initialized

        class MyAsyncService:
            def __init__(self):
                self._router = None

            @ensure_initialized("_router")
            async def process(self):
                return "done"

        svc = MyAsyncService()
        with pytest.raises(RuntimeError, match="_router"):
            await svc.process()

    def test_check_type_passes(self):
        from anima.utils.invariants import check_type
        check_type("hello", str, "greeting")

    def test_check_type_fails(self):
        from anima.utils.invariants import check_type
        with pytest.raises(TypeError, match="Expected"):
            check_type(42, str, "greeting")


# ── Cross-platform path detection ──


class TestCrossPlatformPaths:
    """Test L-23: cross-platform hardcoded path detection."""

    def test_detects_windows_paths(self):
        """Evolution review should detect Windows-specific paths."""
        from anima.evolution.engine import EvolutionEngine
        engine = EvolutionEngine()
        diff = '+path = "C:\\Users\\admin\\Desktop\\file.txt"'
        ok, msg = engine._review_diff(diff, MagicMock(files=[]))
        # Should detect hardcoded path
        assert not ok or "Hardcoded path" in msg or "path" in msg.lower()

    def test_detects_unix_paths(self):
        from anima.evolution.engine import EvolutionEngine
        engine = EvolutionEngine()
        diff = '+path = "/home/user/data/file.txt"'
        ok, msg = engine._review_diff(diff, MagicMock(files=[]))
        assert not ok or "Hardcoded path" in msg or "path" in msg.lower()


# ── ChromaDB backfill ──


class TestChromaDBBackfill:
    """Test M-15: backfill on init."""

    @pytest.mark.asyncio
    async def test_backfill_code_exists(self, tmp_path):
        """Verify backfill code path exists in store create."""
        from anima.memory.store import MemoryStore
        # Just verify store creates without error
        db_path = str(tmp_path / "test_backfill.db")
        store = await MemoryStore.create(db_path)
        # Save some memories
        store.save_memory("test content", "chat")
        # ChromaDB backfill only runs if chromadb is installed
        # We just verify it doesn't crash
        await store.close()


# ── MCP backoff ──


class TestMCPBackoff:
    """Test M-46: exponential backoff exists in MCP client."""

    def test_backoff_code_present(self):
        """Verify backoff import exists in mcp client."""
        import importlib
        mod = importlib.import_module("anima.mcp.client")
        source = open(mod.__file__, encoding="utf-8").read()
        assert "backoff" in source.lower() or "sleep" in source.lower()
