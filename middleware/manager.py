"""
中间件管理器
支持优先级排序、动态加载/卸载、热插拔能力
"""

import asyncio
import importlib
import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Type
import yaml

from .base import IMiddleware, MiddlewareContext

logger = logging.getLogger(__name__)


class MiddlewareManager:
    """中间件管理器 - 核心组件"""

    def __init__(self, config_path: Optional[str] = None):
        self._middlewares: List[IMiddleware] = []
        self._middleware_map: Dict[str, IMiddleware] = {}
        self._config_path = config_path

        if config_path:
            self.load_config(config_path)

    def register(self, middleware: IMiddleware) -> None:
        """注册中间件"""
        if middleware.name in self._middleware_map:
            logger.warning(f"Middleware {middleware.name} already registered, replacing")
            self._middlewares = [m for m in self._middlewares if m.name != middleware.name]

        self._middlewares.append(middleware)
        self._middleware_map[middleware.name] = middleware
        self._sort_middlewares()
        logger.info(f"Registered middleware: {middleware.name} (priority: {middleware.priority})")

    def unregister(self, name: str) -> bool:
        """卸载中间件（热插拔）"""
        if name not in self._middleware_map:
            logger.warning(f"Middleware {name} not found")
            return False

        middleware = self._middleware_map.pop(name)
        self._middlewares.remove(middleware)
        logger.info(f"Unregistered middleware: {name}")
        return True

    def get(self, name: str) -> Optional[IMiddleware]:
        """获取中间件实例"""
        return self._middleware_map.get(name)

    def list(self) -> List[Dict[str, Any]]:
        """列出所有已注册中间件"""
        return [
            {
                'name': m.name,
                'priority': m.priority,
                'enabled': m.enabled,
                'class': m.__class__.__name__
            }
            for m in self._middlewares
        ]

    def enable(self, name: str) -> bool:
        """启用中间件"""
        middleware = self.get(name)
        if middleware:
            middleware.enable()
            logger.info(f"Enabled middleware: {name}")
            return True
        return False

    def disable(self, name: str) -> bool:
        """禁用中间件"""
        middleware = self.get(name)
        if middleware:
            middleware.disable()
            logger.info(f"Disabled middleware: {name}")
            return True
        return False

    def _sort_middlewares(self) -> None:
        """按优先级排序中间件"""
        self._middlewares.sort()

    async def process_request(self, context: MiddlewareContext) -> MiddlewareContext:
        """执行请求处理链"""
        for middleware in self._middlewares:
            if not middleware.enabled:
                continue

            try:
                await middleware.process_request(context)
                if context.is_interrupted():
                    logger.info(f"Request interrupted by {middleware.name}: {context.get_interrupt_reason()}")
                    break
            except Exception as e:
                logger.error(f"Middleware {middleware.name} process_request error: {e}")
                context.set_error(e)
                break

        return context

    async def process_response(self, context: MiddlewareContext) -> MiddlewareContext:
        """执行响应处理链（逆序执行）"""
        for middleware in reversed(self._middlewares):
            if not middleware.enabled:
                continue

            try:
                await middleware.process_response(context)
            except Exception as e:
                logger.error(f"Middleware {middleware.name} process_response error: {e}")

        return context

    async def execute(self, context: MiddlewareContext, handler) -> Any:
        """完整执行中间件链 + 业务处理"""
        # 请求处理链
        context = await self.process_request(context)
        if context.is_interrupted():
            return {'error': context.get_interrupt_reason(), 'interrupted': True}

        if context.get_error():
            return {'error': str(context.get_error())}

        # 执行业务处理
        try:
            result = await handler()
            context.set_response(result)
        except Exception as e:
            context.set_error(e)
            result = {'error': str(e)}

        # 响应处理链
        await self.process_response(context)

        return result

    def load_config(self, config_path: str) -> None:
        """从配置文件加载中间件配置（支持YAML/JSON）"""
        path = Path(config_path)
        if not path.exists():
            logger.warning(f"Config file not found: {config_path}")
            return

        try:
            if path.suffix in ('.yaml', '.yml'):
                with open(path, 'r', encoding='utf-8') as f:
                    config = yaml.safe_load(f)
            elif path.suffix == '.json':
                with open(path, 'r', encoding='utf-8') as f:
                    config = json.load(f)
            else:
                logger.error(f"Unsupported config format: {path.suffix}")
                return

            self._load_from_config(config)
            logger.info(f"Loaded middleware config from {config_path}")
        except Exception as e:
            logger.error(f"Failed to load middleware config: {e}")

    def _load_from_config(self, config: Dict[str, Any]) -> None:
        """从配置字典加载中间件"""
        middlewares_config = config.get('middlewares', [])

        for mw_config in middlewares_config:
            class_path = mw_config.get('class')
            if not class_path:
                continue

            try:
                middleware = self._instantiate_middleware(class_path, mw_config)
                if middleware:
                    middleware.enabled = mw_config.get('enabled', True)
                    middleware.priority = mw_config.get('priority', middleware.priority)
                    middleware.configure(mw_config.get('config', {}))
                    self.register(middleware)
            except Exception as e:
                logger.error(f"Failed to instantiate middleware {class_path}: {e}")

    def _instantiate_middleware(self, class_path: str, config: Dict[str, Any]) -> Optional[IMiddleware]:
        """动态实例化中间件类"""
        try:
            module_path, class_name = class_path.rsplit('.', 1)
            module = importlib.import_module(module_path)
            cls = getattr(module, class_name)
            return cls()
        except (ImportError, AttributeError, ValueError) as e:
            logger.error(f"Failed to load middleware class {class_path}: {e}")
            return None

    def save_config(self, config_path: str) -> None:
        """保存当前中间件配置到文件"""
        config = {
            'middlewares': [
                {
                    'class': f"{m.__class__.__module__}.{m.__class__.__name__}",
                    'name': m.name,
                    'enabled': m.enabled,
                    'priority': m.priority,
                    'config': m.config
                }
                for m in self._middlewares
            ]
        }

        path = Path(config_path)
        try:
            if path.suffix in ('.yaml', '.yml'):
                with open(path, 'w', encoding='utf-8') as f:
                    yaml.dump(config, f, default_flow_style=False, allow_unicode=True)
            elif path.suffix == '.json':
                with open(path, 'w', encoding='utf-8') as f:
                    json.dump(config, f, indent=2, ensure_ascii=False)
            logger.info(f"Saved middleware config to {config_path}")
        except Exception as e:
            logger.error(f"Failed to save middleware config: {e}")
