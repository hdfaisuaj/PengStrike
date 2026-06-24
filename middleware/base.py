"""
中间件抽象基类定义
定义中间件通用接口和上下文对象
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, List
from datetime import datetime
import uuid


@dataclass
class MiddlewareContext:
    """中间件执行上下文"""
    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: datetime = field(default_factory=datetime.now)
    user_id: Optional[str] = None
    session_id: Optional[str] = None
    ip_address: Optional[str] = None
    command: Optional[str] = None
    tool_name: Optional[str] = None
    extra: Dict[str, Any] = field(default_factory=dict)
    _response_data: Optional[Any] = None
    _error: Optional[Exception] = None
    _interrupted: bool = False
    _interrupt_reason: Optional[str] = None

    def set_response(self, data: Any) -> None:
        """设置响应数据"""
        self._response_data = data

    def get_response(self) -> Any:
        """获取响应数据"""
        return self._response_data

    def set_error(self, error: Exception) -> None:
        """设置错误"""
        self._error = error

    def get_error(self) -> Optional[Exception]:
        """获取错误"""
        return self._error

    def interrupt(self, reason: str) -> None:
        """中断执行链"""
        self._interrupted = True
        self._interrupt_reason = reason

    def is_interrupted(self) -> bool:
        """检查是否被中断"""
        return self._interrupted

    def get_interrupt_reason(self) -> Optional[str]:
        """获取中断原因"""
        return self._interrupt_reason


class IMiddleware(ABC):
    """中间件抽象基类"""

    def __init__(self, name: str, priority: int = 100, enabled: bool = True):
        """
        初始化中间件
        :param name: 中间件名称
        :param priority: 优先级，数字越小优先级越高
        :param enabled: 是否启用
        """
        self.name = name
        self.priority = priority
        self.enabled = enabled
        self.config: Dict[str, Any] = {}

    @abstractmethod
    async def process_request(self, context: MiddlewareContext) -> None:
        """
        请求处理前执行
        :param context: 中间件上下文
        """
        pass

    @abstractmethod
    async def process_response(self, context: MiddlewareContext) -> None:
        """
        请求处理后执行
        :param context: 中间件上下文
        """
        pass

    def configure(self, config: Dict[str, Any]) -> None:
        """配置中间件"""
        self.config.update(config)

    def enable(self) -> None:
        """启用中间件"""
        self.enabled = True

    def disable(self) -> None:
        """禁用中间件"""
        self.enabled = False

    def __lt__(self, other: 'IMiddleware') -> bool:
        """用于优先级排序"""
        return self.priority < other.priority

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name='{self.name}', priority={self.priority}, enabled={self.enabled})"
