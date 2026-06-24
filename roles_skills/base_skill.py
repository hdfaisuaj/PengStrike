"""
技能抽象基类 (base_skill.py)

所有技能必须继承 BaseSkill 并实现:
- name:           技能唯一标识
- description:    技能描述
- parameters:     技能参数定义（JSON Schema 格式）
- async run():    技能执行逻辑（内部可串行/并行调用多个工具）
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, Optional, TYPE_CHECKING

from pydantic import BaseModel, Field

from tools.base_tool import ToolResult

if TYPE_CHECKING:
    from core.orchestrator import Orchestrator


class BaseSkill(ABC, BaseModel):
    """技能抽象基类 — 所有技能必须继承此类。"""

    model_config = {"arbitrary_types_allowed": True}

    name: str = Field(..., description="技能唯一标识")
    description: str = Field(..., description="技能描述")
    parameters: Dict[str, Any] = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {},
            "required": [],
        },
        description="技能参数定义（JSON Schema 格式）",
    )

    @abstractmethod
    async def run(self, orchestrator: Orchestrator, **kwargs) -> ToolResult:
        """
        执行技能逻辑。

        参数:
            orchestrator: 编排引擎实例，通过它调用工具和其他技能
            kwargs:       技能执行参数（由 parameters 定义）

        返回:
            ToolResult — 统一的工具执行结果
        """
        ...

    def get_param_schema(self) -> Dict[str, Any]:
        """获取参数 schema（用于 LLM 工具定义）。"""
        return dict(self.parameters)

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}(name='{self.name}')>"