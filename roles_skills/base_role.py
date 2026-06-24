"""
角色抽象基类 (base_role.py)

所有角色必须继承 BaseRole 并实现:
- name:                 角色唯一标识
- description:          角色描述
- system_prompt_template: Jinja2 系统提示词模板
- allowed_tools:        允许调用的工具列表（"*" 表示全部允许）
- allowed_skills:       允许使用的技能列表（"*" 表示全部允许）
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class BaseRole(ABC, BaseModel):
    """角色抽象基类 — 所有角色必须继承此类。"""

    model_config = {"arbitrary_types_allowed": True}

    name: str = Field(..., description="角色唯一标识")
    description: str = Field(..., description="角色描述")
    system_prompt_template: str = Field(..., description="Jinja2 系统提示词模板")
    allowed_tools: List[str] = Field(default_factory=list, description="允许调用的工具列表（'*' 表示全部允许）")
    allowed_skills: List[str] = Field(default_factory=list, description="允许使用的技能列表（'*' 表示全部允许）")

    def can_use_tool(self, tool_name: str) -> bool:
        """检查是否有权限调用指定工具。"""
        if "*" in self.allowed_tools:
            return True
        return tool_name in self.allowed_tools

    def can_use_skill(self, skill_name: str) -> bool:
        """检查是否有权限使用指定技能。"""
        if "*" in self.allowed_skills:
            return True
        return skill_name in self.allowed_skills

    def get_prompt_variables(
        self,
        session_target: str = "",
        current_phase: str = "",
        session_history: str = "",
    ) -> Dict[str, Any]:
        """返回用于渲染 Jinja2 模板的变量字典。"""
        return {
            "role": self,
            "session": {"target": session_target},
            "state": {"current_phase": current_phase},
            "session": {"history": session_history},
        }

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}(name='{self.name}', tools={len(self.allowed_tools)}, skills={len(self.allowed_skills)})>"