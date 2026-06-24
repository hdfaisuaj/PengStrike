"""
角色/技能注册表 (registry.py)

单例模式实现:
- RoleRegistry:   自动扫描 predefined/roles 目录，注册所有 BaseRole 子类
- SkillRegistry:  自动扫描 predefined/skills 目录，注册所有 BaseSkill 子类

功能:
- 自动扫描与注册
- 按名称查询 (get_role / get_skill)
- 角色注销 (unregister_role)
- 列出所有注册项 (list_roles / list_skills)
- 内置缓存（首次扫描后不会重复扫描）
"""

from __future__ import annotations

import importlib
import inspect
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Type

from .base_role import BaseRole
from .base_skill import BaseSkill


# ========================================================================
# 路径常量
# ========================================================================
_PACKAGE_DIR = Path(__file__).parent
_ROLES_DIR = _PACKAGE_DIR / "predefined" / "roles"
_SKILLS_DIR = _PACKAGE_DIR / "predefined" / "skills"


# ========================================================================
# 角色注册表
# ========================================================================
class RoleRegistry:
    """角色注册表（单例 + 懒扫描）。"""

    _instance: Optional["RoleRegistry"] = None
    _roles: Dict[str, Type[BaseRole]] = {}
    _scanned: bool = False

    def __new__(cls) -> "RoleRegistry":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def _ensure_scanned(self) -> None:
        if self._scanned:
            return
        self._roles.clear()
        self._scan_directory(self._roles, _ROLES_DIR, BaseRole, "roles_skills.predefined.roles")
        self._scanned = True

    @staticmethod
    def _scan_directory(
        registry: Dict[str, Type],
        scan_dir: Path,
        base_class: type,
        package_prefix: str,
    ) -> None:
        if not scan_dir.exists():
            return

        if str(scan_dir.parent.parent.parent) not in sys.path:
            sys.path.insert(0, str(scan_dir.parent.parent.parent))

        for file in sorted(os.listdir(str(scan_dir))):
            if not file.endswith(".py") or file.startswith("_"):
                continue
            module_name = file[:-3]
            full_module = f"{package_prefix}.{module_name}"

            try:
                module = importlib.import_module(full_module)
            except Exception:
                continue

            for attr_name in dir(module):
                cls = getattr(module, attr_name)
                if (
                    isinstance(cls, type)
                    and issubclass(cls, base_class)
                    and cls is not base_class
                ):
                    key: str = attr_name
                    try:
                        name_field = cls.model_fields.get("name")
                        if name_field and name_field.default:
                            key = name_field.default
                    except Exception:
                        key = attr_name
                    if key not in registry:
                        registry[key] = cls

    def get_role(self, role_name: str) -> Optional[BaseRole]:
        """获取角色实例。"""
        self._ensure_scanned()
        cls = self._roles.get(role_name)
        if cls is None:
            return None
        try:
            return cls()
        except Exception:
            return None

    def register_role(self, role_cls: Type[BaseRole]) -> bool:
        """注册角色类。"""
        self._ensure_scanned()
        key: str = role_cls.__name__
        try:
            name_field = role_cls.model_fields.get("name")
            if name_field and name_field.default:
                key = name_field.default
        except Exception:
            key = role_cls.__name__
        if key in self._roles:
            return False
        self._roles[key] = role_cls
        return True

    def unregister_role(self, role_name: str) -> bool:
        """注销角色。"""
        self._ensure_scanned()
        return self._roles.pop(role_name, None) is not None

    def list_roles(self) -> List[str]:
        """列出所有已注册角色名称。"""
        self._ensure_scanned()
        return sorted(self._roles.keys())

    def has_role(self, role_name: str) -> bool:
        """检查角色是否存在。"""
        self._ensure_scanned()
        return role_name in self._roles

    def role_count(self) -> int:
        self._ensure_scanned()
        return len(self._roles)

    def reset(self) -> None:
        """强制重新扫描（测试用）。"""
        self._roles.clear()
        self._scanned = False


# ========================================================================
# 技能注册表
# ========================================================================
class SkillRegistry:
    """技能注册表（单例 + 懒扫描）。"""

    _instance: Optional["SkillRegistry"] = None
    _skills: Dict[str, Type[BaseSkill]] = {}
    _scanned: bool = False

    def __new__(cls) -> "SkillRegistry":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def _ensure_scanned(self) -> None:
        if self._scanned:
            return
        self._skills.clear()
        RoleRegistry._scan_directory(
            self._skills, _SKILLS_DIR, BaseSkill, "roles_skills.predefined.skills"
        )
        self._scanned = True

    def get_skill(self, skill_name: str) -> Optional[BaseSkill]:
        """获取技能实例。"""
        self._ensure_scanned()
        cls = self._skills.get(skill_name)
        if cls is None:
            return None
        try:
            return cls()
        except Exception:
            return None

    def register_skill(self, skill_cls: Type[BaseSkill]) -> bool:
        """注册技能类。"""
        key: str = skill_cls.__name__
        try:
            name_field = skill_cls.model_fields.get("name")
            if name_field and name_field.default:
                key = name_field.default
        except Exception:
            key = skill_cls.__name__
        if key in self._skills:
            return False
        self._skills[key] = skill_cls
        return True

    def unregister_skill(self, skill_name: str) -> bool:
        """注销技能。"""
        self._ensure_scanned()
        return self._skills.pop(skill_name, None) is not None

    def list_skills(self) -> List[str]:
        """列出所有已注册技能名称。"""
        self._ensure_scanned()
        return sorted(self._skills.keys())

    def has_skill(self, skill_name: str) -> bool:
        """检查技能是否存在。"""
        self._ensure_scanned()
        return skill_name in self._skills

    def skill_count(self) -> int:
        self._ensure_scanned()
        return len(self._skills)

    def reset(self) -> None:
        """强制重新扫描（测试用）。"""
        self._skills.clear()
        self._scanned = False


# ========================================================================
# 便捷全局访问器
# ========================================================================
def get_role_registry() -> RoleRegistry:
    return RoleRegistry()


def get_skill_registry() -> SkillRegistry:
    return SkillRegistry()