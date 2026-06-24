"""
MCP 服务器 (mcp_server/server.py)
基于 FastMCP 实现，功能:
- 自动注册 tools/registry 中的所有工具为 MCP 工具
- 处理工具参数转换和结果返回
- 支持工具分类 tags 用于分组
- 错误信息清晰返回
技术实现:
- 使用 FastMCP 创建 MCP 服务器实例
- 遍历 ToolRegistry 中所有已注册工具
- 将每个 BaseTool 的 metadata 转换为 MCP 工具定义
- 工具执行结果统一格式化为 MCP 响应
"""
from __future__ import annotations
import asyncio
import json
import logging
import sys
import types
from pathlib import Path
from typing import Any, Dict, List, Optional
logger = logging.getLogger(__name__)
FILE_DIR = Path(__file__).resolve().parent.parent
if str(FILE_DIR) not in sys.path:
    sys.path.insert(0, str(FILE_DIR))
_MCP_SERVER_INSTANCE: Optional[Any] = None
_MCP_HOST: str = "127.0.0.1"
_MCP_PORT: int = 8911

def _get_registry():
    from tools.registry import get_registry
    return get_registry()

def _convert_tool_to_mcp(tool) -> Dict[str, Any]:
    """将 BaseTool 实例转换为 MCP 工具定义字典。"""
    meta = tool.metadata
    schema = meta.get_param_schema()
    return {
        "name": meta.name,
        "description": meta.description,
        "inputSchema": schema,
        "tags": [meta.category.value] + meta.tags,
    }

async def _execute_tool(tool_name: str, arguments: Dict[str, Any]) -> str:
    """执行工具并返回格式化的结果字符串。"""
    registry = _get_registry()
    tool = registry.get_tool(tool_name)
    if tool is None:
        return json.dumps({
            "success": False,
            "error": f"工具 '{tool_name}' 不存在",
        }, ensure_ascii=False)
    try:
        result = await tool.execute(**arguments)
        response = {
            "success": result.success,
            "output": result.output[:10000] if result.output else "",
            "structured_data": result.structured_data,
            "error": result.error,
            "duration": result.duration,
            "return_code": result.return_code,
        }
        return json.dumps(response, ensure_ascii=False, default=str)
    except Exception as exc:
        return json.dumps({
            "success": False,
            "error": f"执行异常: {type(exc).__name__}: {exc}",
        }, ensure_ascii=False)

def _create_tool_handler(tool_name: str, tool_schema: Dict[str, Any], tool_description: str):
    """根据工具参数 schema 动态创建带有明确参数的处理函数。
    
    新版 fastmcp 不支持 **kwargs，所以需要动态生成明确参数的函数。
    """
    # 提取参数名
    properties = tool_schema.get("properties", {})
    param_names = list(properties.keys())
    
    # 构建参数字符串
    params_str = ", ".join([f"{name}=None" for name in param_names])
    
    # 构建函数体
    body_lines = []
    body_lines.append(f'    """{tool_description}"""')
    body_lines.append("    args = {}")
    for name in param_names:
        body_lines.append(f"    if {name} is not None:")
        body_lines.append(f"        args['{name}'] = {name}")
    body_lines.append(f'    return await _execute_tool("{tool_name}", args)')
    
    func_body = "\n".join(body_lines)
    
    # 完整函数代码
    func_code = f"async def tool_handler({params_str}):\n{func_body}\n"
    
    # 执行代码创建函数
    local_vars = {"_execute_tool": _execute_tool}
    exec(func_code, globals(), local_vars)
    
    handler = local_vars["tool_handler"]
    handler.__name__ = tool_name
    handler.__doc__ = tool_description
    
    return handler

async def create_mcp_server(host: str = "127.0.0.1", port: int = 8911) -> Any:
    """创建并启动 MCP 服务器。
    服务器通过 HTTP SSE 协议暴露所有已注册的渗透测试工具。
    客户端可通过 SSE 端点发现工具列表并调用工具。
    返回 MCP 服务器实例。
    """
    global _MCP_SERVER_INSTANCE, _MCP_HOST, _MCP_PORT
    try:
        from fastmcp import FastMCP
    except ImportError:
        logger.error("[MCP] fastmcp 未安装，请执行: pip install fastmcp")
        return None

    _MCP_HOST = host
    _MCP_PORT = port

    registry = _get_registry()
    all_tools = registry.list_all()
    tool_count = len(all_tools)
    logger.info("[MCP] 正在创建 MCP 服务器，注册 %d 个工具...", tool_count)

    # 新版 FastMCP 构造函数不接受 host/port
    mcp = FastMCP(
        name="PengStrike",
        instructions="PengStrike 渗透测试工具 MCP 服务器 — 提供 50+ 渗透测试工具",
    )

    registered_count = 0
    for tool_name in all_tools:
        tool = registry.get_tool(tool_name)
        if tool is None:
            continue
        tool_meta = tool.metadata
        tool_schema = tool_meta.get_param_schema()

        # 动态创建带有明确参数的处理函数
        handler_fn = _create_tool_handler(
            tool_name=tool_name,
            tool_schema=tool_schema,
            tool_description=tool_meta.description,
        )

        # 新版 add_tool 只接受一个 callable
        mcp.add_tool(handler_fn)
        registered_count += 1

    logger.info("[MCP] 注册完成: %d/%d 工具已注册", registered_count, tool_count)

    @mcp.resource("pentest://tools/list")
    def list_tools_resource() -> str:
        """列出所有可用的渗透测试工具。"""
        tools_list = registry.get_all_metadata()
        lines = [f"PengStrike MCP 工具列表 ({len(tools_list)} 个):"]
        for tm in sorted(tools_list, key=lambda x: x.name):
            lines.append(f"  - {tm.name}: {tm.description} [{tm.category.value}]")
        return "\n".join(lines)

    @mcp.resource("pentest://tools/categories")
    def list_categories_resource() -> str:
        """按分类列出工具。"""
        by_cat = {}
        for tm in registry.get_all_metadata():
            cat = tm.category.value
            if cat not in by_cat:
                by_cat[cat] = []
            by_cat[cat].append(tm.name)
        lines = ["工具分类:"]
        for cat, tools in sorted(by_cat.items()):
            lines.append(f"\n[{cat}]")
            for t in tools:
                lines.append(f"  - {t}")
        return "\n".join(lines)

    @mcp.tool()
    async def pentest_search_tools(query: str) -> str:
        """搜索渗透测试工具。"""
        results = registry.search(query)
        if not results:
            return f"未找到与 '{query}' 相关的工具"
        lines = [f"找到 {len(results)} 个与 '{query}' 相关的工具:"]
        for name in results:
            meta = registry.get_metadata(name)
            if meta:
                lines.append(f"  - {name}: {meta.description}")
        return "\n".join(lines)

    @mcp.tool()
    async def pentest_get_tool_info(tool_name: str) -> str:
        """获取指定工具的详细信息。"""
        meta = registry.get_metadata(tool_name)
        if meta is None:
            return f"工具 '{tool_name}' 不存在"
        schema = meta.get_param_schema()
        info = [
            f"工具名称: {meta.name}",
            f"描述: {meta.description}",
            f"分类: {meta.category.value}",
            f"标签: {', '.join(meta.tags) if meta.tags else '无'}",
            f"超时: {meta.timeout_default}秒",
            f"参数: {json.dumps(schema, ensure_ascii=False, indent=2)}",
        ]
        return "\n".join(info)

    _MCP_SERVER_INSTANCE = mcp
    logger.info("[MCP] 服务器已创建 (host=%s, port=%d, tools=%d)", host, port, registered_count)
    return mcp

def get_mcp_server() -> Optional[Any]:
    """获取已创建的 MCP 服务器实例。"""
    return _MCP_SERVER_INSTANCE

async def start_mcp_server(host: str = "127.0.0.1", port: int = 8911) -> Optional[Any]:
    """创建并启动 MCP 服务器（阻塞运行）。"""
    mcp = await create_mcp_server(host=host, port=port)
    if mcp is None:
        return None
    logger.info("[MCP] 服务器启动于 http://%s:%d/sse", host, port)
    print(f"[MCP] PengStrike MCP 服务器运行于 http://{host}:{port}/sse")
    try:
        # 新版 fastmcp 使用 run_http_async，transport='sse' 指定 SSE 模式
        await mcp.run_http_async(
            transport='sse',
            host=host,
            port=port,
            show_banner=False,
        )
    except KeyboardInterrupt:
        logger.info("[MCP] 服务器已停止 (Ctrl+C)")
    except Exception as exc:
        logger.error("[MCP] 服务器异常: %s", exc)
    return mcp
