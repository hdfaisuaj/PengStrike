"""
工具注册表 (tools/registry.py)

单例模式实现，提供:
- 工具注册 (register): 将工具类实例化后加入注册表
- 工具注销 (unregister): 从注册表中移除工具
- 工具查询 (get / list / search): 按名称/分类/tag 查询
- 批量执行 (run / run_batch): 执行单个或多个工具
- 自动扫描 (discover): 遍历 tools/modules 目录，动态发现并注册工具类

自动扫描实现步骤:
1. importlib 动态导入 tools/modules/*.py 中的模块
2. 检查模块中的类是否是 BaseTool 的子类
3. 实例化并注册
4. 跳过 _ 开头的私有模块和 __init__.py
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Type

from tools.base_tool import BaseTool, ToolCategory, ToolMetadata, ToolResult
from tools.executor import get_executor


# ========================================================================
# 工具注册表
# ========================================================================
class ToolRegistry:
    """工具注册表单例。"""

    _instance: Optional["ToolRegistry"] = None

    def __new__(cls, *args: Any, **kwargs: Any) -> "ToolRegistry":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(
        self,
        executor_config: Optional[Any] = None,
        auto_discover: bool = True,
    ) -> None:
        if self._initialized:
            return

        self._tools: Dict[str, BaseTool] = {}
        self._categories: Dict[ToolCategory, List[str]] = {cat: [] for cat in ToolCategory}
        self._tags: Dict[str, Set[str]] = {}
        self._executor = get_executor()
        self._initialized = True

        if auto_discover:
            self.discover()

    # ------------------------------------------------------------------
    # 注册 / 注销
    # ------------------------------------------------------------------
    def register(self, tool: BaseTool, overwrite: bool = False) -> bool:
        name = tool.get_name()
        if name in self._tools and not overwrite:
            return False

        self._tools[name] = tool

        cat = tool.metadata.category
        if name not in self._categories[cat]:
            self._categories[cat].append(name)

        for tag in tool.metadata.tags:
            if tag not in self._tags:
                self._tags[tag] = set()
            self._tags[tag].add(name)

        return True

    def unregister(self, name: str) -> Optional[BaseTool]:
        tool = self._tools.pop(name, None)
        if tool is None:
            return None

        cat = tool.metadata.category
        if name in self._categories[cat]:
            self._categories[cat].remove(name)

        for tag in tool.metadata.tags:
            self._tags.get(tag, set()).discard(name)

        return tool

    def register_class(
        self, tool_cls: Type[BaseTool], overwrite: bool = False
    ) -> bool:
        if not issubclass(tool_cls, BaseTool):
            raise TypeError(f"{tool_cls} 不是 BaseTool 的子类")
        return self.register(tool_cls(), overwrite=overwrite)

    # ------------------------------------------------------------------
    # 查询
    # ------------------------------------------------------------------
    def get(self, name: str) -> Optional[BaseTool]:
        return self._tools.get(name)

    def get_tool(self, name: str) -> Optional[BaseTool]:
        """按名称获取工具实例。"""
        return self._tools.get(name)

    def has(self, name: str) -> bool:
        return name in self._tools

    def list_all(self) -> List[str]:
        return sorted(self._tools.keys())

    def list_tools(self) -> List[str]:
        """列出所有已注册的工具名称。

        与 list_all() 语义相同，为外部调用提供更直观的方法名。
        """
        return self.list_all()

    def list_by_category(self, category: ToolCategory) -> List[str]:
        return list(self._categories.get(category, []))

    def list_by_tag(self, tag: str) -> List[str]:
        return sorted(self._tags.get(tag, set()))

    def search(self, query: str) -> List[str]:
        query_lower = query.lower()
        results: Dict[str, int] = {}

        for name, tool in self._tools.items():
            score = 0
            if query_lower in name.lower():
                score += 10
                if name.lower() == query_lower:
                    score += 20
            if query_lower in tool.metadata.description.lower():
                score += 5
            if query_lower in tool.metadata.name.lower():
                score += 5
            for tag in tool.metadata.tags:
                if query_lower in tag.lower():
                    score += 3
            if score > 0:
                results[name] = score

        return [name for name, _ in sorted(results.items(), key=lambda x: -x[1])]

    def get_metadata(self, name: str) -> Optional[ToolMetadata]:
        tool = self._tools.get(name)
        return tool.metadata if tool else None

    def get_all_metadata(self) -> List[ToolMetadata]:
        return [t.metadata for t in self._tools.values()]

    # ------------------------------------------------------------------
    # 执行
    # ------------------------------------------------------------------
    async def run(
        self,
        name: str,
        timeout: Optional[float] = None,
        **params,
    ) -> ToolResult:
        """执行单个工具。所有安全拦截/参数校验/结构化解析/error 填充均在 BaseTool.execute 内完成。"""
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(
                success=False,
                output="",
                error=f"工具不存在: {name}",
                tool_name=name,
            )
        # 支持覆盖超时（子类 run() 读取 _timeout）
        if timeout is not None:
            params["_timeout"] = timeout
        try:
            return await tool.execute(**params)
        except Exception as exc:
            return ToolResult.from_exception(exc, tool_name=name)

    async def run_batch(
        self,
        requests: List[Dict[str, Any]],
        *,
        concurrency: int = 5,
    ) -> List[ToolResult]:
        """批量执行多个工具（顺序执行，便于审计；并发由内部 _run_cmd 决定）。"""
        results: List[ToolResult] = []
        for req in requests:
            name = req.get("name") or req.get("tool") or ""
            results.append(await self.run(
                name,
                timeout=req.get("timeout"),
                **req.get("params", {}),
            ))
        return results

    # ------------------------------------------------------------------
    # 自动扫描
    # ------------------------------------------------------------------
    def discover(
        self,
        modules_path: Optional[str] = None,
        exclude_modules: Optional[List[str]] = None,
    ) -> List[str]:
        if modules_path is None:
            modules_path = str(Path(__file__).parent / "modules")

        exclude_modules = set(exclude_modules or [])
        exclude_modules.update({"__init__", "__main__"})
        registered: List[str] = []

        module_dir = Path(modules_path)
        if str(module_dir.parent) not in sys.path:
            sys.path.insert(0, str(module_dir.parent))

        for item in module_dir.rglob("*.py"):
            rel = item.relative_to(module_dir.parent)
            parts = list(rel.parts)
            parts = [p for p in parts if p != "__init__.py"]
            module_name = ".".join(parts).replace(".py", "")
            if any(p.startswith("_") or p in exclude_modules for p in parts):
                continue

            try:
                module = importlib.import_module(module_name)
                for cls_name, cls in inspect.getmembers(module, inspect.isclass):
                    if (
                        issubclass(cls, BaseTool)
                        and cls is not BaseTool
                        and cls.__module__ == module_name
                    ):
                        try:
                            instance = cls()
                            self.register(instance)
                            registered.append(instance.get_name())
                        except Exception as exc:
                            print(f"[ToolRegistry] 注册工具 {cls_name} 失败: {exc}")
            except Exception as exc:
                print(f"[ToolRegistry] 加载模块 {module_name} 失败: {exc}")

        return registered

    # ------------------------------------------------------------------
    # 统计
    # ------------------------------------------------------------------
    def stats(self) -> Dict[str, Any]:
        return {
            "total_tools": len(self._tools),
            "by_category": {cat.value: len(names) for cat, names in self._categories.items()},
            "all_tags": sorted(self._tags.keys()),
            "tools": sorted(self._tools.keys()),
        }

    def __len__(self) -> int:
        return len(self._tools)

    def __repr__(self) -> str:
        return f"<ToolRegistry: {len(self._tools)} tools>"


# ========================================================================
# 全局访问器
# ========================================================================
def get_registry(auto_discover: bool = True) -> ToolRegistry:
    registry = ToolRegistry(auto_discover=False)
    if len(registry) == 0 and auto_discover:
        registry.discover()
    return registry
