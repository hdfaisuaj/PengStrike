"""
插件系统抽象基类 (plugins/base_plugin.py)

定义四类插件基类:
- BasePlugin:     所有插件的根抽象基类
- ToolPlugin:     工具插件（注册到 ToolRegistry）
- ReportPlugin:   报告插件（扩展报告生成功能）

设计原则:
- 每个插件有唯一的 name 和 version
- 通过 on_load() / on_unload() 生命周期管理
- 插件可声明依赖的其他插件
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any, ClassVar, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


class PluginMetadata:
    """插件元信息。"""

    def __init__(
        self,
        name: str,
        version: str = "1.0.0",
        description: str = "",
        author: str = "",
        dependencies: Optional[List[str]] = None,
        tags: Optional[List[str]] = None,
    ) -> None:
        self.name = name
        self.version = version
        self.description = description
        self.author = author
        self.dependencies = dependencies or []
        self.tags = tags or []

    def __repr__(self) -> str:
        return f"<PluginMetadata(name='{self.name}', v{self.version})>"


class BasePlugin(ABC):
    """所有插件的根抽象基类。"""

    metadata: ClassVar[PluginMetadata]

    def __init__(self) -> None:
        self._loaded: bool = False
        self._enabled: bool = True

    @abstractmethod
    async def on_load(self) -> bool:
        """插件加载时调用。返回 True 表示加载成功。"""

    @abstractmethod
    async def on_unload(self) -> bool:
        """插件卸载时调用。返回 True 表示卸载成功。"""

    def get_name(self) -> str:
        return self.metadata.name

    def get_version(self) -> str:
        return self.metadata.version

    def is_loaded(self) -> bool:
        return self._loaded

    def set_loaded(self, loaded: bool) -> None:
        self._loaded = loaded

    def is_enabled(self) -> bool:
        return self._enabled

    def set_enabled(self, enabled: bool) -> None:
        self._enabled = enabled

    def get_dependencies(self) -> List[str]:
        return list(self.metadata.dependencies)

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}({self.metadata.name})>"


class ToolPlugin(BasePlugin):
    """工具插件基类 — 插件可提供多个工具。

    子类需实现:
    - metadata: 插件元信息
    - on_load(): 加载时注册工具到 ToolRegistry
    - on_unload(): 卸载时从 ToolRegistry 注销工具
    - get_tools(): 返回插件提供的工具类列表
    """

    @abstractmethod
    def get_tools(self) -> List[Any]:
        """返回插件提供的 BaseTool 子类列表。"""


class SkillPlugin(BasePlugin):
    """技能插件基类 — 插件可提供多个技能。

    子类需实现:
    - metadata: 插件元信息
    - get_skills(): 返回插件提供的技能类列表
    """

    @abstractmethod
    def get_skills(self) -> List[Any]:
        """返回插件提供的 BaseSkill 子类列表。"""


class RolePlugin(BasePlugin):
    """角色插件基类 — 插件可提供多个角色。

    子类需实现:
    - metadata: 插件元信息
    - on_load(): 加载时注册角色到 RoleRegistry
    """

    @abstractmethod
    def get_roles(self) -> List[Any]:
        """返回插件提供的 BaseRole 子类列表。"""


class ReportPlugin(BasePlugin):
    """报告插件基类 — 插件可扩展报告生成功能。

    子类需实现:
    - metadata: 插件元信息
    - on_load(): 加载时注册自定义报告格式
    - on_unload(): 卸载时注销自定义报告格式
    - get_report_formats(): 返回支持的报告格式列表
    - generate(): 生成自定义格式报告
    """

    @abstractmethod
    def get_report_formats(self) -> List[str]:
        """返回插件支持的报告格式（如 'docx', 'csv'）。"""

    @abstractmethod
    async def generate(self, data: Dict[str, Any], output_path: str) -> bool:
        """生成自定义格式报告。"""