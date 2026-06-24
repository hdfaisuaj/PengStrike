"""
上下文管理器 (utils/context_manager.py)

职责:
- 基于 tiktoken 计算 token 数
- 滑动窗口压缩: 保留 System Prompt + 最近 N 条，中间部分摘要
- 摘要时保留关键细节 (IP、端口、漏洞名、http://、exploit、password)
- 提供统一的压缩入口供 Orchestrator 调用
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

import tiktoken


# ========================================================================
# 关键信息保护模式: 摘要时不丢失这些内容
# ========================================================================
_CRITICAL_PATTERNS = [
    re.compile(r"\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b"),  # IP 地址
    re.compile(r"\b\d{1,5}\b"),                                # 端口号
    re.compile(r"https?://\S+"),                                # URL
    re.compile(r"\bexploit\b", re.I),                          # exploit
    re.compile(r"\bpassword\b", re.I),                         # password
    re.compile(r"\bCVE-\d{4}-\d{4,7}\b", re.I),               # CVE 编号
    re.compile(r"\b(ROOT|SYSTEM|admin|root)\b", re.I),         # 权限关键词
    re.compile(r"\b(shell|reverse|bind)\b", re.I),             # shell 类型
]


# ========================================================================
# 上下文管理器
# ========================================================================
class ContextManager:
    """LLM 上下文管理器 (滑动窗口 + 智能摘要)。

    使用示例:
        cm = ContextManager(model="gpt-4", max_tokens=8192)
        compressed = cm.compress_context(messages)
    """

    def __init__(
        self,
        model: str = "gpt-4",
        max_tokens: int = 8192,
        reserve_recent: int = 4,
    ) -> None:
        try:
            self._encoding = tiktoken.encoding_for_model(model)
        except KeyError:
            self._encoding = tiktoken.get_encoding("cl100k_base")

        self.max_tokens = max_tokens
        self.reserve_recent = reserve_recent
        self._summary_prompt = (
            "请用 1-2 句话总结以下渗透测试中间过程，保留所有 IP 地址、端口号、漏洞编号(CVE)、"
            "已获取的权限级别和 shell 类型。不要丢失关键操作步骤。"
        )

    # ------------------------------------------------------------------
    # Token 计数
    # ------------------------------------------------------------------
    def num_tokens_from_messages(self, messages: List[Dict[str, Any]]) -> int:
        total = 0
        for msg in messages:
            total += 4
            for key, value in msg.items():
                if isinstance(value, str):
                    total += len(self._encoding.encode(value))
                elif isinstance(value, (dict, list)):
                    total += len(self._encoding.encode(json.dumps(value, ensure_ascii=False)))
                elif value is not None:
                    total += len(self._encoding.encode(str(value)))
        total += 2
        return total

    def num_tokens(self, text: str) -> int:
        return len(self._encoding.encode(text))

    # ------------------------------------------------------------------
    # 关键信息提取
    # ------------------------------------------------------------------
    def _extract_critical_info(self, text: str) -> str:
        fragments: List[str] = []
        for pat in _CRITICAL_PATTERNS:
            matches = pat.findall(text)
            for m in matches:
                m = m.strip()
                if m and m not in fragments:
                    fragments.append(m)
        return ", ".join(fragments[:30])

    def _contains_critical_content(self, text: str) -> bool:
        for pat in _CRITICAL_PATTERNS:
            if pat.search(text):
                return True
        return False

    # ------------------------------------------------------------------
    # 摘要生成
    # ------------------------------------------------------------------
    def _summarize_middle(
        self, middle_messages: List[Dict[str, Any]]
    ) -> Dict[str, str]:
        combined_parts: List[str] = []
        critical_parts: List[str] = []

        for msg in middle_messages:
            content = msg.get("content", "")
            if isinstance(content, str) and content.strip():
                combined_parts.append(content[:200])
                if self._contains_critical_content(content):
                    info = self._extract_critical_info(content)
                    if info:
                        critical_parts.append(info)

        summary = "; ".join(combined_parts[:10])
        if critical_parts:
            summary += f"\n[关键信息] {' | '.join(critical_parts[:5])}"

        return {"role": "system", "content": f"[历史摘要] {summary}"}

    # ------------------------------------------------------------------
    # 主压缩入口
    # ------------------------------------------------------------------
    def compress_context(
        self, messages: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        if not messages:
            return messages

        total_tokens = self.num_tokens_from_messages(messages)
        if total_tokens <= self.max_tokens:
            return messages

        system = messages[0] if messages[0].get("role") == "system" else None
        non_system = messages[1:] if system else list(messages)

        if len(non_system) <= self.reserve_recent + 1:
            return self._drop_oldest(messages)

        recent = non_system[-self.reserve_recent:]
        middle = non_system[:-self.reserve_recent]

        mid_recent = []
        mid_old = []
        for m in reversed(middle):
            if m.get("role") in ("tool", "assistant") and len(mid_recent) < 2:
                mid_recent.insert(0, m)
            else:
                mid_old.insert(0, m)

        if mid_old:
            summary_msg = self._summarize_middle(mid_old)
        else:
            summary_msg = None

        compressed: List[Dict[str, Any]] = []
        if system:
            compressed.append(system)
        if summary_msg:
            compressed.append(summary_msg)
        compressed.extend(mid_recent)
        compressed.extend(recent)

        while (
            len(compressed) > 2
            and self.num_tokens_from_messages(compressed) > self.max_tokens
        ):
            compressed.pop(1)

        return compressed

    def _drop_oldest(
        self, messages: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        result = list(messages)
        while (
            len(result) > 1
            and self.num_tokens_from_messages(result) > self.max_tokens
        ):
            for i, m in enumerate(result):
                if m.get("role") != "system":
                    result.pop(i)
                    break
            else:
                break
        return result