"""
工具管理 API 路由 (api/tool_routes.py)

端点:
- GET    /api/tools                列出所有工具
- GET    /api/tools/search         搜索工具
- GET    /api/tools/{name}         获取工具详情
- POST   /api/tools/{name}/execute 执行工具
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, status

from tools.registry import get_registry
from pydantic import BaseModel

router = APIRouter(prefix="/api/tools", tags=["工具"])


@router.get("")
async def list_tools(
    category: Optional[str] = Query(None, description="按分类筛选"),
):
    registry = get_registry()
    all_metadata = registry.get_all_metadata()
    tool_list = []
    for meta in sorted(all_metadata, key=lambda m: m.name):
        if category and meta.category.value != category:
            continue
        tool_list.append({
            "name": meta.name,
            "description": meta.description,
            "category": meta.category.value,
            "tags": meta.tags,
            "timeout": meta.timeout_default,
            "available": True  # 前端需要available字段
        })
    # 前端期望直接返回数组
    return tool_list


@router.get("/search")
async def search_tools(
    q: str = Query("", description="搜索关键词"),
):
    if not q.strip():
        return {"total": 0, "items": []}
    registry = get_registry()
    names = registry.search(q)
    items = []
    for name in names:
        meta = registry.get_metadata(name)
        if meta:
            items.append({
                "name": meta.name,
                "description": meta.description,
                "category": meta.category.value,
            })
    return {"total": len(items), "query": q, "items": items}


@router.get("/{tool_name}")
async def get_tool_info(
    tool_name: str,
):
    registry = get_registry()
    meta = registry.get_metadata(tool_name)
    if meta is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="工具不存在")
    schema = meta.get_param_schema()
    return {
        "name": meta.name,
        "description": meta.description,
        "category": meta.category.value,
        "tags": meta.tags,
        "timeout_default": meta.timeout_default,
        "param_schema": schema,
    }


@router.post("/{tool_name}/execute", status_code=status.HTTP_202_ACCEPTED)
async def execute_tool(
    tool_name: str,
    body: Dict[str, Any],
):
    registry = get_registry()
    meta = registry.get_metadata(tool_name)
    if meta is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="工具不存在")

    # 真正调用工具执行
    tool = registry.get_tool(tool_name)
    if tool is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="工具实例不可用")

    try:
        result = await tool.execute(**body)
        return {
            "success": result.success,
            "tool": tool_name,
            "output": result.output,
            "structured_data": result.structured_data,
            "error": result.error,
            "duration": result.duration,
            "return_code": result.return_code,
        }
    except Exception as e:
        return {
            "success": False,
            "tool": tool_name,
            "error": f"执行异常: {type(e).__name__}: {e}",
        }
