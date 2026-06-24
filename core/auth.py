"""
认证模块 (core/auth.py)

职责:
- 提供默认用户对象（已移除 JWT 认证）
- 所有依赖 get_current_user 的路由直接通过，不再校验令牌
"""

from __future__ import annotations

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel


class UserInfo(BaseModel):
    id: str = "default"
    username: str = "user"
    role: str = "admin"
    is_active: bool = True


# 默认用户单例
_DEFAULT_USER = UserInfo()


def get_current_user() -> UserInfo:
    """直接返回默认用户，不再校验 JWT。"""
    return _DEFAULT_USER


def get_optional_user() -> UserInfo:
    """直接返回默认用户。"""
    return _DEFAULT_USER
