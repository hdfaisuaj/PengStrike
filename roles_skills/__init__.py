"""
roles_skills — 角色与技能系统 (Phase 4)

分层架构:
- base_role.py      BaseRole 抽象基类 (Pydantic + abc)
- base_skill.py     BaseSkill 抽象基类 (Pydantic + abc)
- registry.py       自动扫描注册表 (RoleRegistry / SkillRegistry)
- predefined/
  ├── roles/        预定义角色（Web渗透/内网渗透/漏洞扫描）
  └── skills/       预定义技能（端口扫描/目录爆破/SQL注入/提权/信息收集）
"""

from .base_role import BaseRole
from .base_skill import BaseSkill
from .registry import RoleRegistry, SkillRegistry

__all__ = [
    "BaseRole",
    "BaseSkill",
    "RoleRegistry",
    "SkillRegistry",
]