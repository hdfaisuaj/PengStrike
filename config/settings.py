"""
配置中心 (config/settings.py)
职责:
- 统一管理所有配置
- 读取 config.json 作为主配置文件
- 支持环境变量覆盖关键字段
- 提供单例入口 get_settings()
- 提供 reload_settings() 清空单例，使下次 get_settings() 重新读取
"""
from __future__ import annotations
import json
import sys
import os
from pathlib import Path
from typing import Any, Dict, Optional

PROJECT_ROOT: Path = Path(__file__).resolve().parent.parent
CONFIG_JSON_PATH: Path = PROJECT_ROOT / "config.json"

# config.json 中字段到 Settings 字段的映射
_LLM_KEY_MAP = {
    "base_url": "llm_base_url",
    "api_key": "llm_api_key",
    "model": "llm_model",
    "temperature": "llm_temperature",
    "max_tokens": "llm_max_tokens",
    "provider": "llm_provider",
    "azure_api_version": "llm_azure_api_version",
}
_SYSTEM_KEY_MAP = {
    "auto_pilot_max_steps": "autopilot_max_steps",
    "command_timeout": "command_timeout",
    "log_level": "log_level",
    "llm_max_tokens": "llm_max_tokens",
    "dangerous_interrupt": "dangerous_interrupt",
    "context_max_tokens": "context_max_tokens",
    "context_reserve_recent": "context_reserve_recent",
    "cors_allowed_origins": "cors_allowed_origins",
}

# CORS 允许来源的配置，支持字符串或数组
def _parse_cors_origins(value: Any) -> List[str]:
    """解析 CORS 允许来源的配置"""
    if value is None:
        return ["*"]
    if isinstance(value, str):
        if value == "*" or not value.strip():
            return ["*"]
        return [o.strip() for o in value.split(",")]
    if isinstance(value, list):
        return value
    return ["*"]


# 配置验证规则：字段名 -> (类型, 验证函数, 错误信息)
_CONFIG_VALIDATORS = {
    # LLM 段
    "llm_base_url": (str, lambda v: True, ""),
    "llm_api_key": (str, lambda v: True, ""),
    "llm_model": (str, lambda v: True, ""),
    "llm_provider": (str, lambda v: v in ("openai", "azure", "anthropic", "ollama", "custom"), 
                     "provider 必须是 openai/azure/anthropic/ollama/custom"),
    "llm_temperature": (float, lambda v: 0 <= v <= 2, "temperature 必须在 0-2 之间"),
    "llm_max_tokens": (int, lambda v: v is None or v > 0, "max_tokens 必须大于 0"),
    # System 段
    "autopilot_max_steps": (int, lambda v: v > 0, "auto_pilot_max_steps 必须大于 0"),
    "command_timeout": (int, lambda v: v > 0, "command_timeout 必须大于 0"),
    "log_level": (str, lambda v: v.upper() in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"),
                  "log_level 必须是 DEBUG/INFO/WARNING/ERROR/CRITICAL"),
    "dangerous_interrupt": (bool, lambda v: True, ""),
    "context_max_tokens": (int, lambda v: v > 0, "context_max_tokens 必须大于 0"),
    # Backend 段
    "backend_port": (int, lambda v: 1 <= v <= 65535, "port 必须在 1-65535 之间"),
    "backend_frontend_port": (int, lambda v: 1 <= v <= 65535, "frontend_port 必须在 1-65535 之间"),
}


def _validate_field(field_name: str, value: Any, source: str) -> Optional[str]:
    """验证单个配置字段"""
    if field_name not in _CONFIG_VALIDATORS:
        return None  # 未知字段，跳过验证
    
    expected_type, validator, error_msg = _CONFIG_VALIDATORS[field_name]
    
    # 如果值为 None，跳过验证（使用默认值）
    if value is None:
        return None
    
    # 类型检查
    if not isinstance(value, expected_type):
        return f"[{source}] {field_name} 类型错误: 期望 {expected_type.__name__}, 实际 {type(value).__name__} ({error_msg})"
    
    # 值范围检查
    if error_msg and not validator(value):
        return f"[{source}] {field_name} 无效: {error_msg}"
    
    return None


def _validate_config(config_json: Dict[str, Any], section_names: List[str]) -> List[str]:
    """验证配置文件中各段落的字段"""
    warnings = []
    for section in section_names:
        section_data = config_json.get(section, {})
        if not isinstance(section_data, dict):
            warnings.append(f"[config.json] '{section}' 段应为对象类型，实际为 {type(section_data).__name__}")
            continue
        for field_name in section_data:
            warning = _validate_field(field_name, section_data[field_name], f"config.json.{section}")
            if warning:
                warnings.append(warning)
    return warnings
_MCP_KEY_MAP = {
    "enabled": "mcp_enabled",
    "host": "mcp_host",
    "port": "mcp_port",
}
_REPORT_KEY_MAP = {
    "output_dir": "report_output_dir",
}
_DATABASE_KEY_MAP = {
    "url": "database_url",
}


class Settings:
    """PengStrike 全局配置（无 JWT 字段）。"""
    def __init__(self, **kwargs: Any) -> None:
        self.llm_base_url: str = kwargs.get("llm_base_url", "")
        self.llm_api_key: str = kwargs.get("llm_api_key", "")
        self.llm_model: str = kwargs.get("llm_model", "gpt-3.5-turbo")
        self.autopilot_max_steps: int = kwargs.get("autopilot_max_steps", 20)
        self.command_timeout: int = kwargs.get("command_timeout", 300)
        self.llm_temperature: float = kwargs.get("llm_temperature", 0.1)
        self.llm_max_tokens: Optional[int] = kwargs.get("llm_max_tokens", None)
        self.log_level: str = kwargs.get("log_level", "INFO")
        self.log_file: Optional[str] = kwargs.get("log_file", None)
        self.dangerous_interrupt: bool = kwargs.get("dangerous_interrupt", True)
        self.database_url: Optional[str] = kwargs.get("database_url", None)
        self.context_max_tokens: int = kwargs.get("context_max_tokens", 8192)
        self.context_reserve_recent: int = kwargs.get("context_reserve_recent", 4)
        self.mcp_enabled: bool = kwargs.get("mcp_enabled", False)
        self.mcp_host: str = kwargs.get("mcp_host", "127.0.0.1")
        self.mcp_port: int = kwargs.get("mcp_port", 8911)
        self.report_output_dir: Optional[str] = kwargs.get("report_output_dir", None)
        self.llm_provider: str = kwargs.get("llm_provider", "openai")
        self.llm_azure_api_version: str = kwargs.get("llm_azure_api_version", "2024-02-15-preview")
        self.cors_allowed_origins: Any = kwargs.get("cors_allowed_origins", "*")
        # 多模型备份配置 (重构方案: 至少3个备份模型)
        self.multi_models: list[dict] = kwargs.get("multi_models", [])
        self.multi_model_auto_switch: bool = kwargs.get("multi_model_auto_switch", True)
        self.multi_model_switch_429_count: int = kwargs.get("multi_model_switch_429_count", 2)
        self.multi_model_switch_422_count: int = kwargs.get("multi_model_switch_422_count", 1)
        self.multi_model_switch_timeout_count: int = kwargs.get("multi_model_switch_timeout_count", 2)
        self.stage_timeout: int = kwargs.get("stage_timeout", 600)
        
    def get_cors_origins(self) -> List[str]:
        """获取 CORS 允许来源的列表"""
        return _parse_cors_origins(self.cors_allowed_origins)

def _build_settings() -> Settings:
    """
    构建配置对象：
    1. 先设置默认值
    2. 读取 config.json（主配置文件）
    3. 环境变量覆盖关键字段（优先级最高）
    """
    data: Dict[str, Any] = {
        "llm_base_url": "",
        "llm_api_key": "",
        "llm_model": "gpt-3.5-turbo",
        "autopilot_max_steps": 20,
        "command_timeout": 300,
        "llm_temperature": 0.1,
        "llm_max_tokens": None,
        "log_level": "INFO",
        "log_file": None,
        "dangerous_interrupt": True,
        "database_url": None,
        "context_max_tokens": 8192,
        "context_reserve_recent": 4,
        "mcp_enabled": False,
        "mcp_host": "127.0.0.1",
        "mcp_port": 8911,
        "report_output_dir": None,
        "llm_provider": "openai",
        "llm_azure_api_version": "2024-02-15-preview",
        "cors_allowed_origins": "*",
        # 多模型备份配置
        "multi_models": [],
        "multi_model_auto_switch": True,
        "multi_model_switch_429_count": 2,
        "multi_model_switch_422_count": 1,
        "multi_model_switch_timeout_count": 2,
        "stage_timeout": 600,
    }

    # 第1步：读取 config.json
    if CONFIG_JSON_PATH.exists():
        try:
            with open(CONFIG_JSON_PATH, "r", encoding="utf-8") as f:
                config_json = json.load(f)

            # llm 段
            llm_cfg = config_json.get("llm", {})
            for json_key, settings_key in _LLM_KEY_MAP.items():
                if json_key in llm_cfg and llm_cfg[json_key] is not None:
                    data[settings_key] = llm_cfg[json_key]

            # system 段
            system_cfg = config_json.get("system", {})
            for json_key, settings_key in _SYSTEM_KEY_MAP.items():
                if json_key in system_cfg and system_cfg[json_key] is not None:
                    # 特殊处理 cors_allowed_origins，支持数组或字符串
                    if settings_key == "cors_allowed_origins":
                        data[settings_key] = system_cfg[json_key]
                    else:
                        data[settings_key] = system_cfg[json_key]

            # mcp 段
            mcp_cfg = config_json.get("mcp", {})
            for json_key, settings_key in _MCP_KEY_MAP.items():
                if json_key in mcp_cfg and mcp_cfg[json_key] is not None:
                    data[settings_key] = mcp_cfg[json_key]

            # report 段
            report_cfg = config_json.get("report", {})
            for json_key, settings_key in _REPORT_KEY_MAP.items():
                if json_key in report_cfg and report_cfg[json_key] is not None:
                    data[settings_key] = report_cfg[json_key]

            # database 段
            db_cfg = config_json.get("database", {})
            for json_key, settings_key in _DATABASE_KEY_MAP.items():
                if json_key in db_cfg and db_cfg[json_key] is not None:
                    data[settings_key] = db_cfg[json_key]

            # multi_models 段 (多模型备份配置)
            multi_models_cfg = config_json.get("multi_models", [])
            if isinstance(multi_models_cfg, list):
                data["multi_models"] = multi_models_cfg
            
            # multi_model settings
            if "multi_model_auto_switch" in config_json:
                data["multi_model_auto_switch"] = config_json["multi_model_auto_switch"]
            if "multi_model_switch_429_count" in config_json:
                data["multi_model_switch_429_count"] = config_json["multi_model_switch_429_count"]
            if "multi_model_switch_422_count" in config_json:
                data["multi_model_switch_422_count"] = config_json["multi_model_switch_422_count"]
            if "multi_model_switch_timeout_count" in config_json:
                data["multi_model_switch_timeout_count"] = config_json["multi_model_switch_timeout_count"]

        except Exception as e:
            print(f"[settings] 读取 config.json 失败: {e}", file=sys.stderr)

        # 第1.5步：验证配置（只报 warning，不阻塞启动）
        try:
            section_names = ["llm", "system", "mcp", "database", "report"]
            warnings = _validate_config(config_json, section_names)
            for w in warnings:
                print(f"[settings] ⚠️ 配置警告: {w}", file=sys.stderr)
            # 关键字段检查：LLM 配置缺失时给出明确提示
            if not data.get("llm_base_url"):
                print("[settings] ⚠️ LLM base_url 未配置，AI 对话功能将不可用", file=sys.stderr)
        except Exception as ve:
            print(f"[settings] 配置验证过程异常: {ve}", file=sys.stderr)

    # 第2步：环境变量覆盖（优先级最高）
    _apply_env_overrides(data)

    # 第3步：环境变量覆盖后再次检查关键配置（避免环境变量覆盖后漏报）
    if not data.get("llm_api_key"):
        print("[settings] ⚠️ LLM api_key 未配置（config.json 未设置且 PENGSTRIKE_LLM_API_KEY 环境变量未设置），AI 对话功能将不可用", file=sys.stderr)
    if not data.get("llm_base_url"):
        print("[settings] ⚠️ LLM base_url 未配置，AI 对话功能将不可用", file=sys.stderr)

    return Settings(**data)


def _apply_env_overrides(data: Dict[str, Any]) -> None:
    """环境变量覆盖（可选，方便部署时动态调整）。"""
    env_map = {
        "LLM_BASE_URL": "llm_base_url",
        "LLM_API_KEY": "llm_api_key",
        "LLM_MODEL": "llm_model",
        "LLM_PROVIDER": "llm_provider",
        "LLM_TEMPERATURE": ("llm_temperature", float),
        "LLM_MAX_TOKENS": ("llm_max_tokens", lambda x: int(x) if x.strip() else None),
        "AUTOPILOT_MAX_STEPS": ("autopilot_max_steps", int),
        "COMMAND_TIMEOUT": ("command_timeout", int),
        "LOG_LEVEL": "log_level",
        "DATABASE_URL": "database_url",
        "CORS_ALLOWED_ORIGINS": "cors_allowed_origins",
        "PENGSTRIKE_LLM_API_KEY": "llm_api_key",
        "PENGSTRIKE_LLM_BASE_URL": "llm_base_url",
        "MCP_ENABLED": ("mcp_enabled", lambda x: x.lower() == "true"),
    }
    for env_key, target in env_map.items():
        val = os.getenv(env_key)
        if not val:
            continue
        if isinstance(target, tuple):
            field_name, converter = target
            try:
                data[field_name] = converter(val)
            except Exception:
                pass
        else:
            data[target] = val


_settings_singleton: Optional[Settings] = None


def get_settings() -> Settings:
    global _settings_singleton
    if _settings_singleton is None:
        try:
            _settings_singleton = _build_settings()
        except Exception as exc:
            error_msg = (
                f"\n{'='*60}\n"
                f"❌ 配置加载失败: {exc}\n\n"
                f"请检查 config.json 文件 ({CONFIG_JSON_PATH}) 是否正确配置。\n"
                f"{'='*60}\n"
            )
            print(error_msg, file=sys.stderr)
            raise RuntimeError(f"配置加载失败: {exc}") from exc
    return _settings_singleton


def reload_settings() -> None:
    """清空配置单例，下次 get_settings() 会重新从文件读取。"""
    global _settings_singleton
    _settings_singleton = None
