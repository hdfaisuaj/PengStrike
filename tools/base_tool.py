"""
工具抽象层 (tools/base_tool.py)

核心组件:
- ToolResult: 工具执行结果的标准化数据结构
- BaseTool: 所有渗透测试工具的抽象基类
- ToolCategory: 工具分类枚举
- ToolMetadata: 工具元信息

设计原则:
- 所有工具必须继承 BaseTool 并实现 run() 和 parse_output()
- parse_output() 返回 Pydantic 模型（结构化数据），失败时返回 None（降级为纯文本）
- run() 为异步方法，工具执行在独立事件循环中进行
"""

from __future__ import annotations

import re
import time
from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, ClassVar, Dict, List, Optional, Type

from pydantic import BaseModel, Field, field_validator


# ========================================================================
# 工具分类枚举
# ========================================================================
class ToolCategory(str, Enum):
    """工具分类枚举，与 MITRE ATT&CK 战术阶段对应。"""

    RECONNAISSANCE = "recon"       # 信息收集
    VULN_SCAN = "vuln_scan"       # 漏洞扫描
    EXPLOITATION = "exploit"      # 漏洞利用
    PRIVILEGE_ESCALATION = "privesc"  # 提权
    LATERAL_MOVEMENT = "lateral"  # 横向移动
    DATA_COLLECTION = "collection" # 凭证收集
    OBFUSCATION = "obfuscation"   # 混淆/持久化
    UTILITY = "utility"           # 通用工具


# ========================================================================
# 工具执行结果
# ========================================================================
class ToolResult(BaseModel):
    """标准化工具执行结果。

    字段说明:
    - success: 执行是否成功（工具存在、参数合法、命令正常返回）
    - output: 原始文本输出（供日志/调试用）
    - structured_data: 结构化数据（Pydantic 模型实例转 dict，解析失败时为 None）
    - error: 错误信息（成功时为 None）
    - duration: 执行耗时（秒）
    - stdout/stderr: 分离的输出流（可选）
    - tool_name: 来源工具名（用于追踪）
    """

    success: bool = False
    output: str = ""
    structured_data: Optional[Dict[str, Any]] = None
    error: Optional[str] = None
    duration: float = 0.0
    stdout: Optional[str] = Field(default=None, description="标准输出")
    stderr: Optional[str] = Field(default=None, description="标准错误")
    tool_name: Optional[str] = Field(default=None, description="工具名称")
    return_code: Optional[int] = Field(default=None, description="进程返回码")

    @field_validator("duration")
    @classmethod
    def duration_non_negative(cls, v: float) -> float:
        if v < 0:
            return 0.0
        return round(v, 3)

    @classmethod
    def from_exception(cls, exc: Exception, tool_name: str = "") -> "ToolResult":
        """从异常创建失败结果（工厂方法）。"""
        return cls(
            success=False,
            error=f"{type(exc).__name__}: {exc}",
            duration=0.0,
            tool_name=tool_name,
        )

    @classmethod
    def from_text(
        cls,
        output: str,
        stdout: str = "",
        stderr: str = "",
        return_code: int = 0,
        duration: float = 0.0,
        tool_name: str = "",
    ) -> "ToolResult":
        """从纯文本创建结果（工厂方法，用于无法解析结构化数据时）。"""
        success = return_code == 0
        return cls(
            success=success,
            output=output,
            stdout=stdout,
            stderr=stderr,
            return_code=return_code,
            duration=duration,
            tool_name=tool_name,
            structured_data=None,
            error=None if success else f"Exit code: {return_code}",
        )


# ========================================================================
# 工具参数定义（JSON Schema 风格）
# ========================================================================
class ToolParameter(BaseModel):
    """单个参数的元信息定义（对应 JSON Schema 的 parameter）。"""

    name: str
    type: str = "string"  # string / integer / boolean / array / number
    description: str = ""
    required: bool = False
    default: Optional[Any] = None
    enum: Optional[List[str]] = None  # 允许的枚举值
    pattern: Optional[str] = None     # 正则校验


# ========================================================================
# 工具元信息
# ========================================================================
class ToolMetadata(BaseModel):
    """工具元信息（不含执行逻辑，纯描述性数据）。"""

    name: str
    description: str
    category: ToolCategory
    tags: List[str] = Field(default_factory=list)
    version: Optional[str] = None
    author: Optional[str] = None
    references: List[str] = Field(default_factory=list)  # 参考链接
    requires_root: bool = False
    supported_platforms: List[str] = Field(default_factory=lambda: ["linux", "darwin", "windows"])
    parameters: List[ToolParameter] = Field(default_factory=list)
    timeout_default: float = 300.0  # 默认超时（秒）

    def get_param_schema(self) -> Dict[str, Any]:
        """导出为 JSON Schema 格式（供 LLM 工具调用使用）。"""
        required_names = {p.name for p in self.parameters if p.required}
        properties: Dict[str, Any] = {}
        for p in self.parameters:
            prop: Dict[str, Any] = {"type": p.type, "description": p.description}
            if p.enum:
                prop["enum"] = p.enum
            if p.default is not None:
                prop["default"] = p.default
            if p.pattern:
                prop["pattern"] = p.pattern
            properties[p.name] = prop

        return {
            "type": "object",
            "properties": properties,
            "required": sorted(list(required_names)),
        }


# ========================================================================
# 抽象基类
# ========================================================================
class BaseTool(ABC):
    """渗透测试工具抽象基类。

    执行链路（单一入口，无分叉）:
        外部 → tool.execute(**params)
            ├─ validate_params()      必填参数 + 命令注入字符检查
            ├─ await run(**params)    子类构造 cmd 并调用 self._run_cmd()
            │   └─ _run_cmd(cmd, ...)  → get_executor() → 高危命令检查 → executor.execute()
            ├─ parse_output(output)   尝试结构化解析（失败返回 None）
            └─ 统一填充 error 字段

    子类仅需:
    1. 定义 metadata: ToolMetadata
    2. 实现 async run(**kwargs) -> ToolResult
       内部调用 self._run_cmd(cmd_list, timeout=...) 即可
    3. 实现 parse_output(raw: str) -> dict | None
    """

    # 子类必须设置类变量（用于自动扫描 / 注册表识别）
    metadata: ClassVar[ToolMetadata]

    # shell 注入黑名单（参数级检查）
    INJECTION_PATTERNS: ClassVar[List[str]] = [
        ";", "|", "&", "$(", "${", "||", "&&", "|&",
        # 扩展: 重定向到敏感设备、进程替换
        "> /dev/sda", "< /dev/sda", "/dev/tcp/", "/dev/udp/",
    ]

    def __init__(self, **extra_params: Any) -> None:
        """构造函数。"""
        self._extra = extra_params
        self._executor_cache = None  # 懒加载执行器

    # ------------------------------------------------------------------
    # 执行器（内部使用，不对外暴露）
    # ------------------------------------------------------------------
    def _get_executor(self):
        """懒加载全局执行器（单例）。"""
        if self._executor_cache is None:
            from tools.executor import get_executor
            self._executor_cache = get_executor()
        return self._executor_cache

    # ------------------------------------------------------------------
    # 抽象方法（子类必须实现）
    # ------------------------------------------------------------------
    @abstractmethod
    async def run(self, **kwargs) -> ToolResult:
        """子类在此构造命令列表并调用 self._run_cmd(cmd, timeout=...)。

        示例:
            async def run(self, **kwargs):
                cmd = ["ping", "-c", str(kwargs.get("count", 4)), kwargs.get("target")]
                return await self._run_cmd(cmd, timeout=30.0)
        """
        ...

    @abstractmethod
    def parse_output(self, raw_output: str) -> Optional[Dict[str, Any]]:
        """将工具原始输出解析为结构化字典。解析失败返回 None。"""
        ...

    # ------------------------------------------------------------------
    # 统一命令执行辅助方法（子类在 run() 中调用）
    # ------------------------------------------------------------------
    async def _run_cmd(
        self,
        cmd: List[str],
        *,
        timeout: Optional[float] = None,
        cwd: Optional[str] = None,
    ) -> ToolResult:
        """统一命令执行方法（子类在 run() 中调用此处即可）。

        流程:
        1. 对 cmd 列表中每个字符串项做注入字符 + 高危关键词检查
        2. 懒加载全局 AsyncExecutor
        3. 调用 executor.execute() 执行并返回 ToolResult
        4. 若执行成功且有 stdout，尝试 parse_output() 填充 structured_data

        参数:
            cmd: 命令+参数的字符串列表（按 asyncio.create_subprocess_exec 的格式）
            timeout: 超时秒数（None 时使用 metadata.timeout_default）
            cwd: 工作目录
        """
        # 参数保护
        if not cmd:
            return ToolResult(
                success=False,
                output="",
                error="空命令（cmd 为空列表）",
                tool_name=self.get_name(),
            )

        # 对 cmd 列表每个字符串做安全检查（避免 shell 元字符注入到 argv 中）
        cmd_flat = " ".join(str(c) for c in cmd)
        safe, reason = self._check_cmd_for_danger(cmd_flat)
        if not safe:
            return ToolResult(
                success=False,
                output="",
                error=f"命令被拦截: {reason}",
                tool_name=self.get_name(),
            )

        # 超时（优先使用调用方指定，其次 metadata 默认）
        effective_timeout = timeout if timeout is not None else self.metadata.timeout_default

        # 懒加载执行器（单例，全局共享）
        executor = self._get_executor()

        try:
            result = await executor.execute(
                self.get_name(),
                list(cmd),  # 传 list，防止调用方后续修改影响缓存
                timeout=effective_timeout,
                cwd=cwd,
            )
        except Exception as exc:
            return ToolResult.from_exception(exc, tool_name=self.get_name())

        # 结构化解析（仅在有输出时尝试，解析失败静默降级为纯文本）
        if result.success and result.output:
            try:
                parsed = self.parse_output(result.output)
                if parsed is not None:
                    result.structured_data = parsed
            except Exception:
                # parse_output 抛异常不影响主流程
                pass

        return result

    # ------------------------------------------------------------------
    # 命令级安全检查（基于真实命令字符串）
    # ------------------------------------------------------------------
    def _check_cmd_for_danger(self, command_str: str) -> tuple[bool, Optional[str]]:
        """对真实命令字符串进行安全检查。

        检查内容:
        1. INJECTION_PATTERNS 中的 shell 元字符（; | & $ ( ${ ...）
        2. core.security.DANGEROUS_KEYWORDS 中的高危关键词（rm -rf、mkfs 等）

        Returns:
            (True, None)   — 安全，允许执行
            (False, 原因)  — 拦截
        """
        if not command_str:
            return True, None

        # 1. 注入字符
        for pattern in self.INJECTION_PATTERNS:
            if pattern in command_str:
                return False, f"参数/命令中包含禁止字符: {repr(pattern)}"

        # 2. 高危关键词
        from core.security import DANGEROUS_KEYWORDS

        cmd_lower = command_str.lower()
        for keyword in DANGEROUS_KEYWORDS:
            if keyword.lower() in cmd_lower:
                return False, f"匹配危险关键词: {keyword}"

        return True, None

    # ------------------------------------------------------------------
    # 参数校验（子类可覆盖）
    # ------------------------------------------------------------------
    def validate_params(self, **params) -> tuple[bool, Optional[str]]:
        """参数校验。返回 (是否有效, 错误信息)。

        默认实现: 1. 必填参数存在  2. 参数值不包含 INJECTION_PATTERNS
        子类可覆盖以添加业务规则（如 ports 必须为数字范围、timeout 必须正数等）。
        """
        # 1. 必填参数
        schema = self.metadata.get_param_schema()
        required = schema.get("required", [])
        for name in required:
            if name not in params or params[name] is None:
                return False, f"缺少必填参数: {name}"

        # 2. 命令注入字符（参数值层面）
        injection_safe, reason = self._check_params_for_injection(**params)
        if not injection_safe:
            return False, reason

        return True, None

    def _check_params_for_injection(self, **params) -> tuple[bool, Optional[str]]:
        """参数值递归检查注入字符。"""
        for key, value in params.items():
            # 跳过框架内部参数
            if key in ("_executor", "_timeout", "_cwd"):
                continue
            found = self._find_injection_in_value(value)
            if found is not None:
                r = repr(value)
                ctx = r[:80] + ("..." if len(r) > 80 else "")
                return False, f"参数 '{key}' 包含禁止字符 {repr(found)}: {ctx}"
        return True, None

    def _find_injection_in_value(self, value: Any) -> Optional[str]:
        """递归扫描一个值（str/dict/list/tuple/set），返回首个发现的注入模式。"""
        if value is None:
            return None
        if isinstance(value, str):
            for pattern in self.INJECTION_PATTERNS:
                if pattern in value:
                    return pattern
            return None
        if isinstance(value, bool):  # bool 是 int 的子类，先判断
            return None
        if isinstance(value, (int, float)):
            return None
        if isinstance(value, dict):
            for k, v in value.items():
                fk = self._find_injection_in_value(k)
                if fk:
                    return fk
                fv = self._find_injection_in_value(v)
                if fv:
                    return fv
            return None
        if isinstance(value, (list, tuple, set)):
            for item in value:
                found = self._find_injection_in_value(item)
                if found:
                    return found
            return None
        return None

    # ------------------------------------------------------------------
    # 辅助方法
    # ------------------------------------------------------------------
    def get_metadata(self) -> ToolMetadata:
        return self.metadata

    def get_name(self) -> str:
        return self.metadata.name

    async def execute(self, **kwargs) -> ToolResult:
        """工具执行统一入口（外部与 ToolRegistry 均通过此处调用）。

        单一入口的价值:
        - 所有工具走同一条链路，不会出现 '有 executor 走 A 路，没 executor 走 B 路'
        - 安全拦截、结构化解析、error 填充逻辑只在一处维护
        """
        # Step 1: 参数校验（必填 + 注入字符）
        valid, err = self.validate_params(**kwargs)
        if not valid:
            return ToolResult(
                success=False,
                output="",
                error=f"参数校验失败: {err}",
                tool_name=self.get_name(),
            )

        # Step 2: 调用子类 run() —— 子类内部通过 self._run_cmd() 执行真实命令
        try:
            result = await self.run(**kwargs)
        except Exception as exc:
            return ToolResult.from_exception(exc, tool_name=self.get_name())

        # Step 3: 统一填充 error 字段（success=False 且 error 为空时）
        if not result.success and not result.error:
            if result.return_code is not None:
                result.error = f"命令执行失败 (exit code: {result.return_code})"
            else:
                result.error = "命令执行失败（无返回码）"

        # Step 4: 确保 tool_name 存在（便于追踪）
        if not result.tool_name:
            result.tool_name = self.get_name()

        return result

    def __repr__(self) -> str:
        return f"<{self.__class__.__name__}({self.metadata.name})>"
