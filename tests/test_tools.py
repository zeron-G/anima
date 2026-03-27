"""Tests for tool registry and executor."""

import pytest

from anima.tools.registry import ToolRegistry
from anima.tools.executor import ToolExecutor
from anima.models.tool_spec import ToolSpec, RiskLevel


@pytest.fixture
def registry():
    r = ToolRegistry()
    r.register_builtins()
    return r


def test_registry_has_builtins(registry):
    tools = registry.list_tools()
    names = {t.name for t in tools}
    assert "shell" in names
    assert "read_file" in names
    assert "write_file" in names
    assert "save_note" in names
    assert "get_datetime" in names
    assert "system_info" in names
    assert "robot_dog_status" in names
    assert "robot_dog_command" in names
    assert "robot_dog_exploration" in names
    assert "spawn_remote_node" in names


def test_registry_get(registry):
    tool = registry.get("shell")
    assert tool is not None
    assert tool.risk_level == RiskLevel.HIGH


def test_registry_to_llm_schemas(registry):
    schemas = registry.to_llm_schemas()
    assert len(schemas) > 0
    assert all("function" in s for s in schemas)


@pytest.mark.asyncio
async def test_executor_datetime():
    r = ToolRegistry()
    r.register_builtins()
    executor = ToolExecutor(r, max_risk=2)
    result = await executor.execute("get_datetime", {})
    assert result["success"]
    assert "local" in result["result"]


@pytest.mark.asyncio
async def test_executor_blocks_over_max_risk():
    r = ToolRegistry()
    r.register_builtins()
    executor = ToolExecutor(r, max_risk=0)  # Only allow SAFE
    # 'mv a b' is MEDIUM risk, should be blocked at max_risk=0
    result = await executor.execute("shell", {"command": "mv a.txt b.txt"})
    assert not result["success"]
    assert "risk" in result["error"].lower()


@pytest.mark.asyncio
async def test_executor_unknown_tool():
    r = ToolRegistry()
    executor = ToolExecutor(r, max_risk=3)
    result = await executor.execute("nonexistent", {})
    assert not result["success"]
    assert "Unknown" in result["error"]


@pytest.mark.asyncio
async def test_executor_blocked_command():
    r = ToolRegistry()
    r.register_builtins()
    executor = ToolExecutor(r, max_risk=3)  # Allow HIGH
    result = await executor.execute("shell", {"command": "rm -rf /"})
    assert not result["success"]
    assert "blocked" in result["error"].lower()


@pytest.mark.asyncio
async def test_list_directory(tmp_path):
    (tmp_path / "a.txt").write_text("hi")
    (tmp_path / "b.txt").write_text("hello")
    r = ToolRegistry()
    r.register_builtins()
    executor = ToolExecutor(r, max_risk=2)
    result = await executor.execute("list_directory", {"path": str(tmp_path)})
    assert result["success"]
    assert "a.txt" in result["result"]
