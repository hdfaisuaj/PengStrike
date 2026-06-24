from __future__ import annotations

import pytest

from tools.base_tool import BaseTool, ToolCategory, ToolMetadata, ToolResult
from tools.registry import ToolRegistry, get_registry


class _DummyTool(BaseTool):
    metadata = ToolMetadata(
        name="dummy_test_tool",
        description="A dummy tool for testing",
        category=ToolCategory.UTILITY,
        tags=["test"],
    )

    async def run(self, **kwargs) -> ToolResult:
        return ToolResult(
            success=True,
            output="dummy output",
            tool_name=self.get_name(),
        )

    def parse_output(self, raw_output: str):
        return {"parsed": raw_output}


@pytest.fixture
def registry():
    ToolRegistry._instance = None
    reg = ToolRegistry(auto_discover=False)
    yield reg


@pytest.mark.unit
def test_tool_registry_list(registry: ToolRegistry):
    tool = _DummyTool()
    registry.register(tool)
    tools = registry.list_all()
    assert "dummy_test_tool" in tools
    assert len(tools) == 1


@pytest.mark.unit
def test_tool_registry_get(registry: ToolRegistry):
    tool = _DummyTool()
    registry.register(tool)
    fetched = registry.get("dummy_test_tool")
    assert fetched is not None
    assert fetched.get_name() == "dummy_test_tool"
    assert fetched.metadata.category == ToolCategory.UTILITY
    assert registry.get("non_existent") is None


@pytest.mark.unit
async def test_tool_execute(registry: ToolRegistry):
    tool = _DummyTool()
    registry.register(tool)

    result = await registry.run("dummy_test_tool")
    assert result.success is True
    assert result.output == "dummy output"
    assert result.tool_name == "dummy_test_tool"

    missing_result = await registry.run("non_existent_tool")
    assert missing_result.success is False
    assert "不存在" in (missing_result.error or "")


@pytest.mark.unit
def test_tool_categories(registry: ToolRegistry):
    tool = _DummyTool()
    registry.register(tool)

    utility_tools = registry.list_by_category(ToolCategory.UTILITY)
    assert "dummy_test_tool" in utility_tools

    recon_tools = registry.list_by_category(ToolCategory.RECONNAISSANCE)
    assert "dummy_test_tool" not in recon_tools