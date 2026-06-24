"""
插件管理器 (plugins/manager.py)

核心职责:
- 自动扫描 plugins/ 目录下的所有插件
- 热加载 / 热卸载插件
- 将插件注册到对应的注册表 (ToolRegistry / SkillRegistry / RoleRegistry)
- 依赖解析与按序加载
- 加载失败的插件记录明确错误日志

技术实现:
- 使用 importlib 实现动态导入和热加载
- 使用 importlib.reload() 清除模块缓存
- 使用 inspect 检测插件类继承关系
"""

from __future__ import annotations

import importlib
import inspect
import logging
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Type

from plugins.base_plugin import (
    BasePlugin,
    PluginMetadata,
    ReportPlugin,
    RolePlugin,
    SkillPlugin,
    ToolPlugin,
)

logger = logging.getLogger(__name__)

_PLUGINS_DIR = Path(__file__).parent


class PluginManager:
    """插件管理器（单例）— 负责插件的全生命周期管理。

    功能:
    - scan(): 扫描目录发现插件类
    - load(): 加载单个插件（含依赖解析）
    - load_all(): 批量加载所有发现的插件
    - unload(): 卸载单个插件
    - reload(): 热重载（清除缓存 → 重新导入 → 加载）
    - get_plugin(): 按名称获取插件实例
    - list_plugins(): 列出所有已加载插件
    """

    _instance: Optional["PluginManager"] = None

    def __new__(cls, *args: Any, **kwargs: Any) -> "PluginManager":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self, plugins_dir: Optional[str] = None) -> None:
        if self._initialized:
            return
        self._plugins_dir = Path(plugins_dir) if plugins_dir else _PLUGINS_DIR
        self._loaded_plugins: Dict[str, BasePlugin] = {}
        self._plugin_classes: Dict[str, Type[BasePlugin]] = {}
        self._scan_results: List[str] = []
        self._failed_plugins: Dict[str, str] = {}
        self._initialized = True

    # ------------------------------------------------------------------
    # 自动扫描
    # ------------------------------------------------------------------
    def scan(self, scan_dir: Optional[str] = None) -> List[str]:
        """扫描插件目录，发现所有 BasePlugin 子类。

        返回发现的插件类名称列表。
        """
        target_dir = Path(scan_dir) if scan_dir else self._plugins_dir
        self._plugin_classes.clear()
        self._scan_results = []
        self._failed_plugins.clear()

        if str(target_dir.parent) not in sys.path:
            sys.path.insert(0, str(target_dir.parent))

        package_name = target_dir.name

        for py_file in sorted(target_dir.glob("*.py")):
            if py_file.name.startswith("_") or py_file.name == "manager.py":
                continue

            module_name = f"{package_name}.{py_file.stem}"
            try:
                module = importlib.import_module(module_name)
                for cls_name, cls_obj in inspect.getmembers(module, inspect.isclass):
                    if (
                        issubclass(cls_obj, BasePlugin)
                        and cls_obj is not BasePlugin
                        and cls_obj is not ToolPlugin
                        and cls_obj is not SkillPlugin
                        and cls_obj is not RolePlugin
                        and cls_obj is not ReportPlugin
                    ):
                        try:
                            meta = cls_obj.metadata
                            self._plugin_classes[meta.name] = cls_obj
                            self._scan_results.append(meta.name)
                            logger.info(
                                "[PluginManager] 发现插件: %s v%s",
                                meta.name, meta.version,
                            )
                        except Exception as exc:
                            logger.warning(
                                "[PluginManager] 插件 %s 元信息读取失败: %s",
                                cls_name, exc,
                            )
                            self._failed_plugins[cls_name] = str(exc)
            except Exception as exc:
                logger.error("[PluginManager] 扫描模块 %s 失败: %s", module_name, exc)
                self._failed_plugins[module_name] = str(exc)

        logger.info(
            "[PluginManager] 扫描完成: 发现 %d 个插件, %d 个失败",
            len(self._scan_results), len(self._failed_plugins),
        )
        return list(self._scan_results)

    # ------------------------------------------------------------------
    # 依赖解析（拓扑排序）
    # ------------------------------------------------------------------
    def _resolve_dependencies(self, plugin_name: str) -> List[str]:
        """解析插件的依赖关系，返回按拓扑序排列的加载顺序（含自身）。"""
        order: List[str] = []
        visited: Set[str] = set()

        def _visit(name: str, path: Set[str]) -> None:
            if name in visited:
                return
            if name not in self._plugin_classes:
                raise ValueError(f"依赖插件 '{name}' 未找到")
            if name in path:
                raise ValueError(f"循环依赖: {' -> '.join(path | {name})}")

            cls_obj = self._plugin_classes[name]
            deps = getattr(cls_obj.metadata, "dependencies", [])
            path.add(name)
            for dep in deps:
                if dep not in visited:
                    _visit(dep, path)
            path.remove(name)
            visited.add(name)
            order.append(name)

        _visit(plugin_name, set())
        return order

    # ------------------------------------------------------------------
    # 加载 / 卸载
    # ------------------------------------------------------------------
    async def load(self, plugin_name: str) -> bool:
        """加载指定插件（自动处理依赖）。"""
        if plugin_name in self._loaded_plugins:
            logger.info("[PluginManager] 插件 %s 已加载，跳过", plugin_name)
            return True

        if plugin_name not in self._plugin_classes:
            logger.error("[PluginManager] 插件 %s 未找到", plugin_name)
            self._failed_plugins[plugin_name] = "插件类未注册"
            return False

        try:
            load_order = self._resolve_dependencies(plugin_name)
        except ValueError as exc:
            logger.error("[PluginManager] 依赖解析失败: %s", exc)
            self._failed_plugins[plugin_name] = str(exc)
            return False

        for name in load_order:
            if name in self._loaded_plugins:
                continue
            cls_obj = self._plugin_classes[name]
            try:
                instance = cls_obj()
                success = await instance.on_load()
                if success:
                    instance.set_loaded(True)
                    self._loaded_plugins[name] = instance
                    self._register_to_registries(instance)
                    logger.info("[PluginManager] 插件已加载: %s v%s", name, instance.metadata.version)
                else:
                    logger.error("[PluginManager] 插件 %s 加载失败（on_load 返回 False）", name)
                    self._failed_plugins[name] = "on_load 返回 False"
                    return False
            except Exception as exc:
                logger.error("[PluginManager] 插件 %s 加载异常: %s", name, exc)
                self._failed_plugins[name] = str(exc)
                return False

        return True

    async def load_all(self) -> int:
        """扫描并加载所有发现的插件。返回成功加载数。"""
        if not self._plugin_classes:
            self.scan()

        success_count = 0
        for name in list(self._plugin_classes.keys()):
            if await self.load(name):
                success_count += 1

        logger.info(
            "[PluginManager] 批量加载完成: %d/%d 成功",
            success_count, len(self._plugin_classes),
        )
        return success_count

    async def unload(self, plugin_name: str) -> bool:
        """卸载指定插件。"""
        instance = self._loaded_plugins.pop(plugin_name, None)
        if instance is None:
            logger.warning("[PluginManager] 插件 %s 未加载，无法卸载", plugin_name)
            return False

        try:
            self._unregister_from_registries(instance)
            success = await instance.on_unload()
            instance.set_loaded(False)
            if success:
                logger.info("[PluginManager] 插件已卸载: %s", plugin_name)
            else:
                logger.warning("[PluginManager] 插件 %s on_unload 返回 False", plugin_name)
            return success
        except Exception as exc:
            logger.error("[PluginManager] 插件 %s 卸载异常: %s", plugin_name, exc)
            return False

    async def reload(self, plugin_name: str) -> bool:
        """热重载插件：卸载 → 清除模块缓存 → 重新扫描 → 加载。"""
        await self.unload(plugin_name)

        cls_obj = self._plugin_classes.get(plugin_name)
        if cls_obj is not None:
            module_name = cls_obj.__module__
            if module_name in sys.modules:
                importlib.reload(sys.modules[module_name])

        self.scan()
        return await self.load(plugin_name)

    # ------------------------------------------------------------------
    # 注册表集成
    # ------------------------------------------------------------------
    def _register_to_registries(self, plugin: BasePlugin) -> None:
        """将插件提供的工具/技能/角色注册到对应注册表。"""
        from tools.registry import ToolRegistry
        from roles_skills.registry import RoleRegistry, SkillRegistry

        tool_registry = ToolRegistry()
        role_registry = RoleRegistry()
        skill_registry = SkillRegistry()

        if isinstance(plugin, ToolPlugin):
            for tool_cls in plugin.get_tools():
                try:
                    tool_registry.register_class(tool_cls)
                    logger.info("  ↳ 注册工具: %s", tool_cls.metadata.name)
                except Exception as exc:
                    logger.error("  ↳ 工具注册失败 %s: %s", tool_cls, exc)

        if isinstance(plugin, SkillPlugin):
            for skill_cls in plugin.get_skills():
                try:
                    skill_registry.register_skill(skill_cls)
                    logger.info("  ↳ 注册技能: %s", skill_cls().name)
                except Exception as exc:
                    logger.error("  ↳ 技能注册失败 %s: %s", skill_cls, exc)

        if isinstance(plugin, RolePlugin):
            for role_cls in plugin.get_roles():
                try:
                    role_registry.register_role(role_cls)
                    logger.info("  ↳ 注册角色: %s", role_cls().name)
                except Exception as exc:
                    logger.error("  ↳ 角色注册失败 %s: %s", role_cls, exc)

    def _unregister_from_registries(self, plugin: BasePlugin) -> None:
        """从注册表中注销插件提供的工具/技能/角色。"""
        from tools.registry import ToolRegistry
        from roles_skills.registry import RoleRegistry, SkillRegistry

        tool_registry = ToolRegistry()
        role_registry = RoleRegistry()
        skill_registry = SkillRegistry()

        if isinstance(plugin, ToolPlugin):
            for tool_cls in plugin.get_tools():
                tool_registry.unregister(tool_cls.metadata.name)
                logger.info("  ↳ 注销工具: %s", tool_cls.metadata.name)

        if isinstance(plugin, SkillPlugin):
            for skill_cls in plugin.get_skills():
                skill_registry.unregister_skill(skill_cls().name)
                logger.info("  ↳ 注销技能: %s", skill_cls().name)

        if isinstance(plugin, RolePlugin):
            for role_cls in plugin.get_roles():
                role_registry.unregister_role(role_cls().name)
                logger.info("  ↳ 注销角色: %s", role_cls().name)

    # ------------------------------------------------------------------
    # 查询接口
    # ------------------------------------------------------------------
    def get_plugin(self, name: str) -> Optional[BasePlugin]:
        return self._loaded_plugins.get(name)

    def list_plugins(self) -> List[str]:
        return sorted(self._loaded_plugins.keys())

    def list_failed(self) -> Dict[str, str]:
        return dict(self._failed_plugins)

    def get_plugin_count(self) -> int:
        return len(self._loaded_plugins)

    def has_plugin(self, name: str) -> bool:
        return name in self._loaded_plugins

    def get_stats(self) -> Dict[str, Any]:
        return {
            "scanned": len(self._scan_results),
            "loaded": len(self._loaded_plugins),
            "failed": len(self._failed_plugins),
            "plugins": self.list_plugins(),
            "failed_details": dict(self._failed_plugins),
        }


def get_plugin_manager(plugins_dir: Optional[str] = None) -> PluginManager:
    return PluginManager(plugins_dir)