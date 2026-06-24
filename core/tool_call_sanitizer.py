"""
LLM Tool Call 安全清洗模块 (core/tool_call_sanitizer.py)

职责:
- 清洗 LLM 返回的 tool call，防止非法 token 污染
- 验证工具名是否在白名单内
- 清洗 arguments，防止注入攻击
- 提供统一的清洗接口给 LLMClient 使用
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any, Dict, List, Optional

from utils.logger import get_logger

logger = get_logger(__name__)

# 注册表白名单：只允许这些工具名
ALLOWED_TOOL_NAMES = {
    "execute_kali_command",
    "save_exploit",
    "execute_command",  # 可能的别名
    "run_command",       # 可能的别名
}

# 非法 token 模式：需要清洗掉的 token
ILLEGAL_TOKEN_PATTERNS = [
    r'<\|[^|]*\|>',          # 匹配 <|...|> 格式的 token
    r'\[CHANNEL\].*?\[/CHANNEL\]',  # 匹配 [CHANNEL]...[/CHANNEL]
    r'\[CONSTRAIN\].*?\[/CONSTRAIN\]',  # 匹配 [CONSTRAIN]...[/CONSTRAIN]
    r'<channel>.*?</channel>',  # 匹配 <channel>...</channel>
    r'<constrain>.*?</constrain>',  # 匹配 <constrain>...</constrain>
]

# 编译正则表达式以提高性能
_ILLEGAL_TOKEN_REGEX = [
    re.compile(pattern, re.IGNORECASE | re.DOTALL)
    for pattern in ILLEGAL_TOKEN_PATTERNS
]


class ToolCallSanitizer:
    """Tool Call 安全清洗器"""
    
    def __init__(self, allowed_tools: Optional[List[str]] = None) -> None:
        self.allowed_tools = allowed_tools or list(ALLOWED_TOOL_NAMES)
        self.allowed_tools_set = set(self.allowed_tools)
    
    def sanitize_tool_name(self, name: str) -> Optional[str]:
        """
        清洗工具名，移除非法 token，验证是否在白名单内
        
        Args:
            name: 原始工具名
            
        Returns:
            清洗后的工具名，如果不在白名单内则返回 None
        """
        if not name or not isinstance(name, str):
            logger.warning("[ToolCall清洗] 工具名为空或不是字符串: %r", name)
            return None
        
        # 1. 移除非法 token
        sanitized = name
        for regex in _ILLEGAL_TOKEN_REGEX:
            sanitized = regex.sub('', sanitized)
        
        # 2. 移除可能的参数污染（例如：execute_kali_command{"command": "..."}）
        # 工具名应该只包含字母、数字、下划线
        match = re.match(r'^([a-zA-Z0-9_]+)', sanitized)
        if match:
            sanitized = match.group(1)
        
        # 3. 验证是否在白名单内
        if sanitized not in self.allowed_tools_set:
            logger.warning(
                "[ToolCall清洗] 工具名不在白名单内: original=%r, sanitized=%r, allowed=%s",
                name, sanitized, self.allowed_tools
            )
            return None
        
        # 4. 检查是否有变化（有变化说明被污染了）
        if sanitized != name:
            logger.info(
                "[ToolCall清洗] 工具名已清洗: original=%r, sanitized=%r",
                name, sanitized
            )
        
        return sanitized
    
    def sanitize_arguments(self, arguments: Any) -> str:
        """
        清洗工具参数，确保是合法的 JSON 字符串
        
        Args:
            arguments: 原始参数（可能是 str, dict, 或其他类型）
            
        Returns:
            清洗后的 JSON 字符串
        """
        if arguments is None:
            return "{}"
        
        # 如果已经是字符串，先尝试解析再重新序列化（确保格式正确）
        if isinstance(arguments, str):
            try:
                # 尝试解析 JSON
                parsed = json.loads(arguments)
                # 重新序列化为标准 JSON 字符串
                return json.dumps(parsed, ensure_ascii=False)
            except json.JSONDecodeError:
                # 不是合法 JSON，尝试提取关键信息
                logger.warning("[ToolCall清洗] arguments 不是合法 JSON: %r", arguments[:100])
                # 尝试提取 command 字段
                cmd_match = re.search(r'"command"\s*:\s*"([^"]*)"', arguments)
                if cmd_match:
                    return json.dumps({"command": cmd_match.group(1)}, ensure_ascii=False)
                # 无法提取，返回空对象
                return "{}"
        
        # 如果是 dict，直接序列化为 JSON 字符串
        if isinstance(arguments, dict):
            try:
                return json.dumps(arguments, ensure_ascii=False)
            except Exception as e:
                logger.error("[ToolCall清洗] 序列化 arguments 失败: %s", e)
                return "{}"
        
        # 其他类型，尝试转换为字符串
        try:
            return json.dumps({"value": str(arguments)}, ensure_ascii=False)
        except Exception as e:
            logger.error("[ToolCall清洗] 转换 arguments 失败: %s", e)
            return "{}"
    
    def sanitize_tool_call(self, tool_call: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        清洗单个 tool call
        
        Args:
            tool_call: 原始 tool call 字典
            
        Returns:
            清洗后的 tool call，如果非法则返回 None
        """
        # 1. 验证基本结构
        if not isinstance(tool_call, dict):
            logger.warning("[ToolCall清洗] tool_call 不是字典: %r", type(tool_call))
            return None
        
        # 2. 获取工具名
        function = tool_call.get("function")
        if not function or not isinstance(function, dict):
            logger.warning("[ToolCall清洗] tool_call 缺少 function 字段: %r", tool_call)
            return None
        
        original_name = function.get("name", "")
        
        # 3. 清洗工具名
        sanitized_name = self.sanitize_tool_name(original_name)
        if not sanitized_name:
            return None
        
        # 4. 清洗参数
        sanitized_arguments = self.sanitize_arguments(function.get("arguments"))
        
        # 5. 构建清洗后的 tool call
        sanitized_call = {
            "id": tool_call.get("id") or f"call_{uuid.uuid4().hex[:8]}",
            "type": "function",
            "function": {
                "name": sanitized_name,
                "arguments": sanitized_arguments,
            },
        }
        
        return sanitized_call
    
    def sanitize_tool_calls(self, tool_calls: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        清洗 tool calls 列表
        
        Args:
            tool_calls: 原始 tool calls 列表
            
        Returns:
            清洗后的 tool calls 列表（过滤掉非法的）
        """
        if not tool_calls or not isinstance(tool_calls, list):
            return []
        
        sanitized_calls = []
        for i, tc in enumerate(tool_calls):
            sanitized = self.sanitize_tool_call(tc)
            if sanitized:
                sanitized_calls.append(sanitized)
            else:
                logger.warning("[ToolCall清洗] 第 %d 个 tool call 被过滤掉", i)
        
        return sanitized_calls


# 全局单例
_default_sanitizer: Optional[ToolCallSanitizer] = None


def get_sanitizer() -> ToolCallSanitizer:
    """获取全局 ToolCallSanitizer 实例"""
    global _default_sanitizer
    if _default_sanitizer is None:
        _default_sanitizer = ToolCallSanitizer()
    return _default_sanitizer


def sanitize_tool_calls(tool_calls: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    便捷函数：清洗 tool calls 列表
    
    Args:
        tool_calls: 原始 tool calls 列表
        
    Returns:
        清洗后的 tool calls 列表
    """
    return get_sanitizer().sanitize_tool_calls(tool_calls)


def sanitize_tool_name(name: str) -> Optional[str]:
    """
    便捷函数：清洗工具名
    
    Args:
        name: 原始工具名
        
    Returns:
        清洗后的工具名，如果非法则返回 None
    """
    return get_sanitizer().sanitize_tool_name(name)