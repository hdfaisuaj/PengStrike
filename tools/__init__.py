"""
PengStrike 工具抽象层 (tools/)
导出:
- BaseTool, ToolResult, ToolCategory, ToolMetadata, ToolParameter
- AsyncExecutor, get_executor
- ToolRegistry, get_registry
- ASTParser, RiskLevel (AST解析引擎)
- SecurityChain, GuardAction (安全防护链 - 集成Phase 1&3)

注意: 异常检测请从 security 模块导入:
  from security import AnomalyDetector, get_anomaly_detector
"""
from __future__ import annotations
from tools.base_tool import (
    BaseTool,
    BaseModel,
    ToolCategory,
    ToolMetadata,
    ToolParameter,
    ToolResult,
)
from tools.executor import AsyncExecutor, ExecutorConfig, get_executor
from tools.registry import ToolRegistry, get_registry
from tools.ast_parser import ASTParser, RiskLevel, DetectionResult, get_ast_parser
from tools.security_chain import SecurityChain, GuardAction, GuardResult, get_security_chain
__all__ = [
    # 抽象层
    "BaseTool",
    "ToolResult",
    "ToolCategory",
    "ToolMetadata",
    "ToolParameter",
    # 执行器
    "AsyncExecutor",
    "ExecutorConfig",
    "get_executor",
    # 注册表
    "ToolRegistry",
    "get_registry",
    # AST解析引擎
    "ASTParser",
    "RiskLevel",
    "DetectionResult",
    "get_ast_parser",
    # 安全防护链
    "SecurityChain",
    "GuardAction",
    "GuardResult",
    "get_security_chain",
]
