"""
角色 API 路由 (api/role_routes.py)
端点:
- GET    /api/roles        获取所有角色列表
- GET    /api/roles/current 获取当前角色
- POST   /api/roles/{name}/switch 切换角色
"""
from __future__ import annotations
import threading
from typing import List, Dict, Any
from fastapi import APIRouter, HTTPException, status
from roles_skills.registry import RoleRegistry

router = APIRouter(prefix="/api/roles", tags=["角色"])

# 全局状态 - 添加线程锁保护并发访问
_CURRENT_ROLE = "web_pentester"
_ROLE_LOCK = threading.Lock()

def get_current_role_name() -> str:
    """获取当前角色名称（线程安全）"""
    with _ROLE_LOCK:
        return _CURRENT_ROLE

def set_current_role_name(name: str) -> None:
    """设置当前角色名称（线程安全）"""
    global _CURRENT_ROLE
    with _ROLE_LOCK:
        _CURRENT_ROLE = name

@router.get("")
async def list_roles():
    """获取所有角色列表（含完整信息）"""
    try:
        registry = RoleRegistry()
        role_names = registry.list_roles()
        current = get_current_role_name()

        # 返回每个角色的完整信息（name, description, tools 等）
        roles_data = []
        for name in role_names:
            role_instance = registry.get_role(name)
            if role_instance:
                roles_data.append({
                    "name": getattr(role_instance, 'name', name),
                    "description": getattr(role_instance, 'description', ''),
                    "tools": getattr(role_instance, 'allowed_tools', []),
                    "color": _ROLE_COLORS.get(name, '#409eff')
                })
            else:
                # fallback: 只知道名称
                roles_data.append({
                    "name": name,
                    "description": '',
                    "tools": [],
                    "color": "#409eff"
                })

        return {
            "roles": roles_data,
            "current": current,
            "total": len(roles_data)
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"获取角色列表失败: {str(e)}"
        )

# 角色颜色映射
_ROLE_COLORS: Dict[str, str] = {
    "web_pentester": "#409eff",
    "internal_pentester": "#67c23a",
    "vuln_scanner": "#e6a23c",
    "red_team": "#f56c6c",
    "forensics": "#909399",
}

@router.get("/current")
async def get_current_role():
    """获取当前角色详情"""
    try:
        registry = RoleRegistry()
        current = get_current_role_name()
        role = registry.get_role(current)
        
        if not role:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"角色 {current} 不存在"
            )
        
        return {
            "role": role,
            "name": current
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"获取当前角色失败: {str(e)}"
        )

@router.post("/{name}/switch")
async def switch_role(name: str):
    """切换到指定角色"""
    try:
        registry = RoleRegistry()
        role = registry.get_role(name)
        
        if not role:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"角色 {name} 不存在"
            )
        
        # 线程安全地更新角色
        set_current_role_name(name)
        
        return {
            "success": True,
            "role": name,
            "role_info": role,
            "message": f"已切换到角色: {name}"
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"切换角色失败: {str(e)}"
        )
