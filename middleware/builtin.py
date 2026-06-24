"""
内置中间件实现
包含: IP黑名单、AST命令注入检测、异常行为检测、审计日志
"""
import re
import ast
import logging
from typing import Any, Dict, List, Optional, Set
from datetime import datetime
from pathlib import Path
import json

from .base import IMiddleware, MiddlewareContext

logger = logging.getLogger(__name__)


class IPBlacklistMiddleware(IMiddleware):
    """IP黑名单中间件"""

    def __init__(self, priority: int = 10):
        super().__init__("ip_blacklist", priority)
        self.blacklist: Set[str] = set()
        self.whitelist: Set[str] = set()

    def configure(self, config: Dict[str, Any]) -> None:
        super().configure(config)
        self.blacklist = set(config.get('blacklist', []))
        self.whitelist = set(config.get('whitelist', []))

    def add_to_blacklist(self, ip: str) -> None:
        """添加IP到黑名单"""
        self.blacklist.add(ip)

    def remove_from_blacklist(self, ip: str) -> None:
        """从黑名单移除IP"""
        self.blacklist.discard(ip)

    async def process_request(self, context: MiddlewareContext) -> None:
        ip = context.ip_address
        if ip in self.whitelist:
            return
        if ip in self.blacklist:
            context.interrupt(f"IP {ip} is in blacklist")
            logger.warning(f"Blocked request from blacklisted IP: {ip}")

    async def process_response(self, context: MiddlewareContext) -> None:
        pass


class ASTCommandInjectionMiddleware(IMiddleware):
    """AST命令注入检测中间件"""

    DANGEROUS_FUNCTIONS = {
        'eval', 'exec', 'compile', '__import__', 'os.system', 'os.popen',
        'subprocess.call', 'subprocess.run', 'subprocess.Popen', 'subprocess.check_output'
    }
    DANGEROUS_MODULES = {'os', 'subprocess', 'sys', 'pty'}

    def __init__(self, priority: int = 20):
        super().__init__("ast_command_injection", priority)

    def _check_ast_safety(self, code: str) -> tuple[bool, Optional[str]]:
        """使用AST分析检测命令注入"""
        try:
            tree = ast.parse(code)
            for node in ast.walk(tree):
                # 检查危险函数调用
                if isinstance(node, ast.Call):
                    func_name = self._get_func_name(node.func)
                    if func_name in self.DANGEROUS_FUNCTIONS:
                        return False, f"Dangerous function detected: {func_name}"
                # 检查危险模块导入
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    for alias in node.names:
                        if alias.name in self.DANGEROUS_MODULES:
                            return False, f"Dangerous module import: {alias.name}"
                # 检查字符串格式化的命令执行
                if isinstance(node, ast.JoinedStr):
                    for value in node.values:
                        if isinstance(value, ast.FormattedValue):
                            return False, "Potential command injection via f-string detected"
            return True, None
        except SyntaxError:
            # 语法错误可能是命令行而非Python代码，使用正则补充检测
            return self._regex_check(code)

    def _regex_check(self, command: str) -> tuple[bool, Optional[str]]:
        """正则表达式补充检测"""
        dangerous_patterns = [
            (r';\s*(rm|chmod|chown|dd|mkfs)', "Dangerous system command"),
            (r'\|\s*(bash|sh|zsh|nc|curl|wget)\s', "Pipe to shell detected"),
            (r'`.*`', "Command substitution detected"),
            (r'\$\(.*\)', "Command substitution detected"),
            (r'(sudo|su)\s+', "Privilege escalation attempt"),
            (r'>\s*/dev/[a-z]+', "Direct device access"),
            (r'/etc/(passwd|shadow|hosts)', "Sensitive file access"),
        ]
        for pattern, reason in dangerous_patterns:
            if re.search(pattern, command, re.IGNORECASE):
                return False, reason
        return True, None

    def _get_func_name(self, node: ast.AST) -> str:
        """获取函数名"""
        if isinstance(node, ast.Name):
            return node.id
        elif isinstance(node, ast.Attribute):
            return f"{self._get_func_name(node.value)}.{node.attr}"
        return ""

    async def process_request(self, context: MiddlewareContext) -> None:
        command = context.command or ""
        if not command:
            return
        is_safe, reason = self._check_ast_safety(command)
        if not is_safe:
            context.interrupt(f"Command injection detected: {reason}")
            logger.warning(f"Command injection blocked: {reason} - Command: {command[:100]}")

    async def process_response(self, context: MiddlewareContext) -> None:
        pass


class AnomalyDetectionMiddleware(IMiddleware):
    """异常行为检测中间件"""

    def __init__(self, priority: int = 30):
        super().__init__("anomaly_detection", priority)
        self.request_counts: Dict[str, List[datetime]] = {}
        self.rate_limit = 60  # 请求/分钟
        self.command_frequency: Dict[str, int] = {}

    def configure(self, config: Dict[str, Any]) -> None:
        super().configure(config)
        self.rate_limit = config.get('rate_limit', 60)

    def _clean_old_requests(self, ip: str, now: datetime) -> None:
        """清理1分钟前的请求记录"""
        cutoff = now.timestamp() - 60
        self.request_counts[ip] = [
            t for t in self.request_counts.get(ip, [])
            if t.timestamp() > cutoff
        ]

    def _check_rate_limit(self, ip: str) -> bool:
        """检查速率限制"""
        now = datetime.now()
        self._clean_old_requests(ip, now)
        count = len(self.request_counts.get(ip, []))
        if count >= self.rate_limit:
            return False
        self.request_counts.setdefault(ip, []).append(now)
        return True

    def _detect_unusual_patterns(self, command: str) -> Optional[str]:
        """检测异常命令模式"""
        unusual_patterns = [
            (r'(/bin/|/usr/bin/)?(base64|nc|curl|wget)\s+.*http', "Data exfiltration pattern"),
            (r'(chmod|chown)\s+777', "Insecure permission change"),
            (r'(/tmp|/dev/shm)/.*\.(sh|py|elf)', "Suspicious file execution"),
            (r'--reverse|--bind', "Reverse shell pattern"),
        ]
        for pattern, reason in unusual_patterns:
            if re.search(pattern, command, re.IGNORECASE):
                return reason
        return None

    async def process_request(self, context: MiddlewareContext) -> None:
        ip = context.ip_address
        command = context.command or ""

        # 速率限制检查
        if ip and not self._check_rate_limit(ip):
            context.interrupt(f"Rate limit exceeded: {self.rate_limit}/min")
            return

        # 异常模式检测
        if command:
            anomaly = self._detect_unusual_patterns(command)
            if anomaly:
                logger.warning(f"Anomaly detected: {anomaly} - Command: {command[:100]}")
                # 记录但不阻断，可以配置为阻断
                context.extra['anomaly_warning'] = anomaly

    async def process_response(self, context: MiddlewareContext) -> None:
        pass
