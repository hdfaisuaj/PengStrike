"""
配置管理 API 路由 (api/config_routes.py)
端点:
- GET    /api/config              获取当前配置
- POST   /api/config              更新配置
- GET    /api/config/llm          获取LLM配置
- POST   /api/config/llm          更新LLM配置
- GET    /api/config/security     获取安全配置状态
"""
from __future__ import annotations
from typing import Optional, Any
from fastapi import APIRouter
from pydantic import BaseModel
from config.settings import reload_settings
import os
import json

router = APIRouter(prefix="/api/config", tags=["配置"])

# 使用与 settings.py 相同的配置文件路径
from pathlib import Path
CONFIG_FILE = str(Path(__file__).resolve().parent.parent / "config.json")

# 默认配置
DEFAULT_CONFIG = {
    "llm": {
        "base_url": "",
        "api_key": "",
        "model": "gpt-3.5-turbo",
        "temperature": 0.1,
        "max_tokens": None,
        "timeout": 60,
        "max_retries": 3,
        "fallback_base_url": "",
        "fallback_api_key": "",
        "fallback_model": "",
        "fallback_provider": ""
    },
    "security": {
        "ast_enabled": True,
        "anomaly_detection_enabled": True
    },
    "system": {
        "command_timeout": 300,
        "log_level": "INFO",
        "llm_max_tokens": 1024,
        "context_max_tokens": 4096,
        "context_reserve_recent": 4
    },
    "backend": {
        "host": "127.0.0.1",
        "port": 8000,
        "frontend_port": 5173
    }
}

def _load_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                return {**DEFAULT_CONFIG, **json.load(f)}
        except:
            pass
    return DEFAULT_CONFIG

def _save_config(config):
    with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=2, ensure_ascii=False)

class LLMConfig(BaseModel):
    base_url: str
    api_key: str
    model: str = "gpt-3.5-turbo"
    temperature: float = 0.1
    max_tokens: Optional[int] = None
    timeout: int = 60
    max_retries: int = 3
    fallback_base_url: str = ""
    fallback_api_key: str = ""
    fallback_model: str = ""
    fallback_provider: str = "openai"

@router.get("")
async def get_config():
    return _load_config()

@router.post("")
async def update_config(config: dict):
    """合并更新，只覆盖请求中携带的字段，不丢失其他段落"""
    full = _load_config()

    # 强制补齐默认段落，防止前端只提交 system/security 时把 llm 等段落写丢
    for key in DEFAULT_CONFIG:
        if key not in full or not full[key]:
            full[key] = dict(DEFAULT_CONFIG[key])

    # 合并前端提交的配置
    for key in ("llm", "security", "system", "mcp", "report", "database", "backend"):
        if key in config:
            full[key] = { **full.get(key, {}), **config[key] }

    _save_config(full)
    reload_settings()
    return {"success": True, "message": "配置已更新"}

@router.get("/llm")
async def get_llm_config():
    config = _load_config()
    return config.get("llm", DEFAULT_CONFIG["llm"])

@router.post("/llm")
async def update_llm_config(config: LLMConfig):
    full_config = _load_config()
    # 确保所有默认段落存在
    for key in DEFAULT_CONFIG:
        if key not in full_config or not full_config[key]:
            full_config[key] = dict(DEFAULT_CONFIG[key])
    full_config["llm"] = config.model_dump()
    _save_config(full_config)
    reload_settings()
    return {"success": True, "message": "LLM配置已更新"}

@router.get("/security")
async def get_security_status():
    config = _load_config()
    return config.get("security", DEFAULT_CONFIG["security"])

class KeyValueUpdate(BaseModel):
    key_path: str
    value: Any

@router.post("/set")
async def set_config_value(body: KeyValueUpdate):
    """单独更新一个配置项，不影响其他段落"""
    full = _load_config()
    parts = body.key_path.split(".")
    d = full
    for p in parts[:-1]:
        d = d.setdefault(p, {})
    d[parts[-1]] = body.value
    _save_config(full)
    reload_settings()
    return {"success": True, "message": f"{body.key_path} 已更新"}

@router.get("/frontend")
async def get_frontend_config():
    """返回前端需要知道的后端地址配置（前端启动时调用）"""
    config = _load_config()
    backend = config.get("backend", DEFAULT_CONFIG.get("backend", {}))
    return {
        "backend_host": backend.get("host", "127.0.0.1"),
        "backend_port": backend.get("port", 8000),
        "frontend_port": backend.get("frontend_port", 5173),
    }
