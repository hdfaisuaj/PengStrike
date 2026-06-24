"""
AST 解析引擎 (tools/ast_parser.py) - Phase 1 安全架构重构
职责:
- 基于 Python ast 模块实现命令解析
- 检测直接命令注入（; | && ` $() 等）
- 检测 Python 代码注入
- 检测变量间接引用（${VAR} $(command)）
- 集成现有危险命令黑名单
设计原则:
- 多层检测：语法分析 + 模式匹配 + 黑名单校验
- 可配置：危险模式可通过配置文件扩展
- 分级告警：根据危险程度分为不同等级
"""
from __future__ import annotations
import ast
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional, List
from utils.logger import get_logger
logger = get_logger(__name__)
# ========================================================================
# 危险等级枚举
# ========================================================================
class RiskLevel(Enum):
    """风险等级。"""
    SAFE = "safe"           # 安全
    LOW = "low"             # 低风险（需警告）
    MEDIUM = "medium"       # 中风险（需确认）
    HIGH = "high"           # 高风险（默认拦截）
    CRITICAL = "critical"   # 严重风险（强制拦截）
# ========================================================================
# 检测结果
# ========================================================================
@dataclass
class DetectionResult:
    """AST检测结果。"""
    is_safe: bool = True
    risk_level: RiskLevel = RiskLevel.SAFE
    findings: list[dict[str, Any]] = field(default_factory=list)
    command_type: str = "unknown"
    normalized_command: str = ""
    def add_finding(
        self,
        risk_level: RiskLevel,
        category: str,
        description: str,
        pattern: Optional[str] = None,
        position: Optional[tuple[int, int]] = None,
    ) -> None:
        """添加检测发现。"""
        self.findings.append({
            "risk_level": risk_level.value,
            "category": category,
            "description": description,
            "pattern": pattern,
            "position": position,
        })
        # 更新整体风险等级
        if risk_level.value > self.risk_level.value:
            self.risk_level = risk_level
        if risk_level in (RiskLevel.HIGH, RiskLevel.CRITICAL):
            self.is_safe = False
    @property
    def has_critical(self) -> bool:
        return any(f["risk_level"] == RiskLevel.CRITICAL.value for f in self.findings)
    @property
    def has_high_risk(self) -> bool:
        return any(f["risk_level"] in (RiskLevel.HIGH.value, RiskLevel.CRITICAL.value) for f in self.findings)
# ========================================================================
# 危险命令黑名单
# ========================================================================
DANGEROUS_COMMANDS = {
    # 系统控制
    "rm": RiskLevel.CRITICAL,
    "rmdir": RiskLevel.HIGH,
    "shutdown": RiskLevel.CRITICAL,
    "reboot": RiskLevel.CRITICAL,
    "halt": RiskLevel.CRITICAL,
    "poweroff": RiskLevel.CRITICAL,
    "init": RiskLevel.CRITICAL,
    "systemctl": RiskLevel.HIGH,
    "service": RiskLevel.HIGH,
    # 文件系统
    "dd": RiskLevel.CRITICAL,
    "mkfs": RiskLevel.CRITICAL,
    "fdisk": RiskLevel.CRITICAL,
    "parted": RiskLevel.HIGH,
    "mount": RiskLevel.HIGH,
    "umount": RiskLevel.HIGH,
    "chmod": RiskLevel.HIGH,
    "chown": RiskLevel.HIGH,
    "chgrp": RiskLevel.HIGH,
    # 网络
    "nc": RiskLevel.HIGH,
    "netcat": RiskLevel.HIGH,
    "ncat": RiskLevel.HIGH,
    "curl": RiskLevel.MEDIUM,
    "wget": RiskLevel.MEDIUM,
    "ssh": RiskLevel.HIGH,
    "scp": RiskLevel.HIGH,
    "sftp": RiskLevel.HIGH,
    "telnet": RiskLevel.HIGH,
    # 权限提升
    "sudo": RiskLevel.CRITICAL,
    "su": RiskLevel.CRITICAL,
    "doas": RiskLevel.CRITICAL,
    "pkexec": RiskLevel.CRITICAL,
    # Shell
    "bash": RiskLevel.HIGH,
    "sh": RiskLevel.HIGH,
    "zsh": RiskLevel.HIGH,
    "fish": RiskLevel.HIGH,
    "csh": RiskLevel.HIGH,
    "ksh": RiskLevel.HIGH,
    # 进程控制
    "kill": RiskLevel.MEDIUM,
    "killall": RiskLevel.HIGH,
    "pkill": RiskLevel.HIGH,
    # 敏感文件访问
    "cat": RiskLevel.LOW,
    "less": RiskLevel.LOW,
    "more": RiskLevel.LOW,
    "head": RiskLevel.LOW,
    "tail": RiskLevel.LOW,
    "vim": RiskLevel.MEDIUM,
    "vi": RiskLevel.MEDIUM,
    "nano": RiskLevel.MEDIUM,
    # 包管理
    "apt": RiskLevel.HIGH,
    "apt-get": RiskLevel.HIGH,
    "yum": RiskLevel.HIGH,
    "dnf": RiskLevel.HIGH,
    "pip": RiskLevel.MEDIUM,
    "pip3": RiskLevel.MEDIUM,
    "npm": RiskLevel.MEDIUM,
    # 其他危险操作
    ">:": RiskLevel.CRITICAL,
    ">>": RiskLevel.MEDIUM,
    ">": RiskLevel.MEDIUM,
    "<": RiskLevel.LOW,
}
# ========================================================================
# 危险模式正则
# ========================================================================
DANGEROUS_PATTERNS = [
    # 命令注入分隔符（渗透测试工具中常用，降为 LOW）
    (r";", RiskLevel.LOW, "command_separator", "分号命令分隔符，可能执行多条命令"),
    (r"\|", RiskLevel.LOW, "pipe", "管道符，串联执行命令"),
    (r"&&", RiskLevel.LOW, "logical_and", "逻辑与，条件执行后续命令"),
    (r"\|\|", RiskLevel.LOW, "logical_or", "逻辑或，条件执行后续命令"),
    (r"`", RiskLevel.CRITICAL, "backtick", "反引号命令替换"),
    (r"\$\(", RiskLevel.CRITICAL, "command_substitution", "$() 命令替换"),
    (r"\$\{", RiskLevel.HIGH, "variable_substitution", "${} 变量间接引用"),
    # 重定向（渗透测试中常用，降为 LOW）
    (r">>", RiskLevel.LOW, "append_redirect", "追加重定向"),
    (r">", RiskLevel.LOW, "output_redirect", "输出重定向"),
    (r"<", RiskLevel.LOW, "input_redirect", "输入重定向"),
    # Shell 扩展
    (r"\*", RiskLevel.LOW, "glob", "通配符扩展"),
    (r"\?", RiskLevel.LOW, "wildcard", "单字符通配符"),
    (r"\[", RiskLevel.LOW, "char_class", "字符类通配符"),
    # 代码执行
    (r"eval", RiskLevel.CRITICAL, "eval", "eval 代码执行"),
    (r"exec", RiskLevel.CRITICAL, "exec", "exec 代码执行"),
    (r"__import__", RiskLevel.CRITICAL, "import_hook", "__import__ 动态导入"),
    (r"os\.system", RiskLevel.CRITICAL, "os_system", "os.system 系统命令执行"),
    (r"subprocess", RiskLevel.HIGH, "subprocess", "subprocess 模块调用"),
    (r"globals\(\)", RiskLevel.HIGH, "globals_access", "globals() 全局变量访问"),
    (r"locals\(\)", RiskLevel.HIGH, "locals_access", "locals() 局部变量访问"),
]
# ========================================================================
# AST 节点访问器
# ========================================================================
class SecurityVisitor(ast.NodeVisitor):
    """安全检查 AST 访问器。"""
    def __init__(self) -> None:
        self.result = DetectionResult()
        self._in_call = False
    def visit_Call(self, node: ast.Call) -> None:
        """检测函数调用。"""
        self._in_call = True
        try:
            # 检测危险函数调用
            if isinstance(node.func, ast.Name):
                func_name = node.func.id
                dangerous_funcs = {
                    "eval": RiskLevel.CRITICAL,
                    "exec": RiskLevel.CRITICAL,
                    "compile": RiskLevel.HIGH,
                    "__import__": RiskLevel.CRITICAL,
                    "open": RiskLevel.MEDIUM,
                }
                if func_name in dangerous_funcs:
                    self.result.add_finding(
                        dangerous_funcs[func_name],
                        "dangerous_function",
                        f"调用危险函数: {func_name}",
                        pattern=func_name,
                        position=(node.lineno, node.col_offset),
                    )
            elif isinstance(node.func, ast.Attribute):
                # 检测方法调用
                attr_chain = self._get_attribute_chain(node.func)
                dangerous_attrs = [
                    ("os.system", RiskLevel.CRITICAL),
                    ("os.popen", RiskLevel.CRITICAL),
                    ("subprocess.run", RiskLevel.HIGH),
                    ("subprocess.call", RiskLevel.HIGH),
                    ("subprocess.Popen", RiskLevel.HIGH),
                    ("exec", RiskLevel.CRITICAL),
                    ("eval", RiskLevel.CRITICAL),
                ]
                for pattern, risk in dangerous_attrs:
                    if pattern in attr_chain:
                        self.result.add_finding(
                            risk,
                            "dangerous_method",
                            f"调用危险方法: {attr_chain}",
                            pattern=pattern,
                            position=(node.lineno, node.col_offset),
                        )
        finally:
            self._in_call = False
        self.generic_visit(node)
    def visit_Import(self, node: ast.Import) -> None:
        """检测导入。"""
        for alias in node.names:
            dangerous_modules = ["os", "subprocess", "sys", "ctypes", "importlib"]
            if alias.name in dangerous_modules:
                self.result.add_finding(
                    RiskLevel.HIGH,
                    "sensitive_import",
                    f"导入敏感模块: {alias.name}",
                    pattern=alias.name,
                    position=(node.lineno, node.col_offset),
                )
        self.generic_visit(node)
    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        """检测从模块导入。"""
        if node.module in ["os", "subprocess", "sys", "ctypes", "importlib"]:
            self.result.add_finding(
                RiskLevel.HIGH,
                "sensitive_import",
                f"从敏感模块导入: {node.module}",
                pattern=node.module,
                position=(node.lineno, node.col_offset),
            )
        self.generic_visit(node)
    def _get_attribute_chain(self, node: ast.Attribute) -> str:
        """获取属性调用链。"""
        parts = []
        current = node
        while isinstance(current, ast.Attribute):
            parts.append(current.attr)
            current = current.value
        if isinstance(current, ast.Name):
            parts.append(current.id)
        return ".".join(reversed(parts))
# ========================================================================
# AST 解析引擎
# ========================================================================
class ASTParser:
    """AST 解析引擎 - 命令安全检测核心。"""
    def __init__(self, custom_patterns: Optional[list[tuple[str, RiskLevel, str, str]]] = None) -> None:
        self._patterns = DANGEROUS_PATTERNS.copy()
        if custom_patterns:
            self._patterns.extend(custom_patterns)
        self._compiled_patterns = [
            (re.compile(pattern, re.IGNORECASE), risk, cat, desc)
            for pattern, risk, cat, desc in self._patterns
        ]
        self._dangerous_commands = DANGEROUS_COMMANDS.copy()
        logger.info("AST 解析引擎初始化完成，加载 %d 个危险模式, %d 个危险命令",
                   len(self._patterns), len(self._dangerous_commands))
    def analyze_command(self, command: str) -> DetectionResult:
        """分析 shell 命令的安全性。
        参数:
            command: 待检测的命令字符串
        返回:
            DetectionResult 检测结果
        """
        result = DetectionResult(normalized_command=command.strip())
        result.command_type = "shell"
        if not command or not command.strip():
            return result
        # 1. 模式匹配检测
        self._pattern_matching(command, result)
        # 2. 危险命令黑名单检测
        self._command_blacklist_check(command, result)
        # 3. Python 代码注入检测
        self._python_code_injection_check(command, result)
        return result
    def analyze_python_code(self, code: str) -> DetectionResult:
        """分析 Python 代码的安全性。
        参数:
            code: 待检测的 Python 代码
        返回:
            DetectionResult 检测结果
        """
        result = DetectionResult(normalized_command=code.strip())
        result.command_type = "python"
        if not code or not code.strip():
            return result
        try:
            # 尝试解析为 AST
            tree = ast.parse(code, mode="exec")
            visitor = SecurityVisitor()
            visitor.visit(tree)
            result = visitor.result
            result.normalized_command = code.strip()
            result.command_type = "python"
        except SyntaxError as e:
            # 语法错误可能是 shell 命令或不完整代码
            logger.debug("Python 语法解析失败，尝试作为 shell 命令分析: %s", e)
            return self.analyze_command(code)
        except Exception as e:
            logger.warning("AST 解析异常: %s", e)
            result.add_finding(
                RiskLevel.MEDIUM,
                "parse_error",
                f"代码解析异常: {e}",
            )
        return result
    def _pattern_matching(self, command: str, result: DetectionResult) -> None:
        """正则模式匹配检测。"""
        for pattern, risk, category, description in self._compiled_patterns:
            for match in pattern.finditer(command):
                result.add_finding(
                    risk,
                    category,
                    description,
                    pattern=pattern.pattern,
                    position=(match.start(), match.end()),
                )
    def _command_blacklist_check(self, command: str, result: DetectionResult) -> None:
        """危险命令黑名单检测。"""
        # 提取命令词
        words = re.split(r"[;\s|&]+", command.strip())
        first_word = words[0].lower() if words else ""
        # 检查第一个命令
        if first_word in self._dangerous_commands:
            risk = self._dangerous_commands[first_word]
            result.add_finding(
                risk,
                "dangerous_command",
                f"检测到危险命令: {first_word}",
                pattern=first_word,
            )
        # 检查管道/分号后的命令
        segments = re.split(r"[;|]+", command)
        for segment in segments[1:]:
            segment = segment.strip()
            if not segment:
                continue
            seg_words = re.split(r"[\s&]+", segment)
            seg_first = seg_words[0].lower() if seg_words else ""
            if seg_first in self._dangerous_commands:
                risk = self._dangerous_commands[seg_first]
                result.add_finding(
                    risk,
                    "dangerous_command",
                    f"检测到链式危险命令: {seg_first}",
                    pattern=seg_first,
                )
    def _python_code_injection_check(self, command: str, result: DetectionResult) -> None:
        """检测 Python 代码注入尝试。"""
        # 检测常见的 Python 注入模式
        python_indicators = [
            (r"python\s+-c", RiskLevel.HIGH, "python_one_liner", "Python 单行代码执行"),
            (r"python3\s+-c", RiskLevel.HIGH, "python_one_liner", "Python3 单行代码执行"),
            (r"__import__", RiskLevel.CRITICAL, "dynamic_import", "动态导入注入"),
            (r"eval\s*\(", RiskLevel.CRITICAL, "eval_injection", "eval 注入"),
            (r"exec\s*\(", RiskLevel.CRITICAL, "exec_injection", "exec 注入"),
        ]
        for pattern, risk, category, description in python_indicators:
            if re.search(pattern, command, re.IGNORECASE):
                result.add_finding(
                    risk,
                    category,
                    description,
                    pattern=pattern,
                )
    def add_dangerous_command(self, command: str, risk_level: RiskLevel) -> None:
        """添加自定义危险命令。"""
        self._dangerous_commands[command.lower()] = risk_level
    def add_dangerous_pattern(self, pattern: str, risk_level: RiskLevel, category: str, description: str) -> None:
        """添加自定义危险模式。"""
        compiled = re.compile(pattern, re.IGNORECASE)
        self._compiled_patterns.append((compiled, risk_level, category, description))
        self._patterns.append((pattern, risk_level, category, description))
# ========================================================================
# 单例工厂
# ========================================================================
_default_parser: Optional[ASTParser] = None
def get_ast_parser() -> ASTParser:
    global _default_parser
    if _default_parser is None:
        _default_parser = ASTParser()
    return _default_parser
