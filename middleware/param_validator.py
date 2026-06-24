"""
Pydantic参数验证中间件 - Parameter Validation Middleware
统一处理CLI/API参数校验与错误提示
基于Pydantic实现强类型参数验证
"""
from __future__ import annotations
from typing import Any, Dict, Optional, Type
from pydantic import BaseModel, ValidationError
from .base import IMiddleware, MiddlewareContext
class ParameterValidationMiddleware(IMiddleware):
    """
    参数验证中间件
    在请求阶段自动验证输入参数，统一处理错误提示
    """
    def __init__(self):
        super().__init__(name="param_validation", priority=20, enabled=True)
        self.schemas: Dict[str, Type[BaseModel]] = {}
    def register_schema(self, command: str, schema: Type[BaseModel]) -> None:
        """为命令注册参数验证Schema"""
        self.schemas[command] = schema
    def unregister_schema(self, command: str) -> None:
        """注销参数验证Schema"""
        self.schemas.pop(command, None)
    async def process_request(self, context: MiddlewareContext) -> None:
        """请求处理 - 验证参数"""
        command = context.command
        if not command or command not in self.schemas:
            return
        schema = self.schemas[command]
        params = context.extra.get('params', {})
        try:
            validated = schema(**params)
            context.extra['validated_params'] = validated.dict()
            context.extra['validation_passed'] = True
        except ValidationError as e:
            context.extra['validation_passed'] = False
            context.extra['validation_errors'] = self._format_errors(e)
            context.interrupt(f"参数验证失败: {self._get_first_error(e)}")
    async def process_response(self, context: MiddlewareContext) -> None:
        """响应处理"""
        pass
    def _format_errors(self, error: ValidationError) -> list:
        """格式化验证错误"""
        errors = []
        for err in error.errors():
            field = ".".join(str(loc) for loc in err['loc'])
            errors.append({
                'field': field,
                'message': err['msg'],
                'type': err['type']
            })
        return errors
    def _get_first_error(self, error: ValidationError) -> str:
        """获取第一个错误信息用于快速提示"""
        for err in error.errors():
            field = ".".join(str(loc) for loc in err['loc'])
            return f"{field}: {err['msg']}"
        return str(error)
    def format_errors_for_cli(self, errors: list) -> str:
        """格式化错误信息用于CLI输出"""
        lines = ["[bold red]❌ 参数验证失败[/bold red]\n"]
        for i, err in enumerate(errors, 1):
            lines.append(f"  {i}. [cyan]{err['field']}[/cyan]: {err['message']}")
        return "\n".join(lines)
# 常用参数Schema
class TargetParams(BaseModel):
    """目标参数"""
    target: str
    port: Optional[int] = None
    timeout: int = 30
class ScanParams(BaseModel):
    """扫描参数"""
    target: str
    ports: Optional[str] = None
    threads: int = 10
    rate: int = 1000
    timeout: int = 30
class SessionParams(BaseModel):
    """会话参数"""
    session_id: Optional[str] = None
    target: Optional[str] = None
# 单例实例
param_validator = ParameterValidationMiddleware()
__all__ = [
    'ParameterValidationMiddleware',
    'TargetParams',
    'ScanParams',
    'SessionParams',
    'param_validator'
]
