"""
安全防护链框架 (tools/security_chain.py) - Phase 1+3 安全架构重构
职责:
- Phase 1: AST检测 + 黑名单/白名单
- Phase 3: 异常行为检测集成
- 完整防护流程：输入 → AST检测 → 异常检测 → 执行
设计原则:
- 责任链模式：每个检查环节独立，可插拔
- 可配置：支持开启/关闭各检查项
- 可扩展：支持自定义检查器
"""
from __future__ import annotations
import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional, List

from tools.ast_parser import ASTParser, DetectionResult, RiskLevel, get_ast_parser
from utils.logger import get_logger

logger = get_logger(__name__)


# ========================================================================
# 防护动作枚举
# ========================================================================
class GuardAction(Enum):
    """防护动作。"""
    ALLOW = "allow"           # 放行
    WARN = "warn"             # 警告但放行
    BLOCK = "block"           # 拦截
    REQUIRE_CONFIRM = "confirm"  # 需要用户确认


# ========================================================================
# 防护结果
# ========================================================================
@dataclass
class GuardResult:
    """防护链检查结果。"""
    action: GuardAction = GuardAction.ALLOW
    risk_level: RiskLevel = RiskLevel.SAFE
    detection_result: Optional[DetectionResult] = None
    anomaly_result: Optional[Any] = None
    blocked_by: Optional[str] = None
    message: str = ""
    details: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def allow(cls, detection: Optional[DetectionResult] = None) -> "GuardResult":
        return cls(
            action=GuardAction.ALLOW,
            risk_level=detection.risk_level if detection else RiskLevel.SAFE,
            detection_result=detection,
            message="命令安全，已放行",
        )

    @classmethod
    def warn(cls, message: str, detection: Optional[DetectionResult] = None) -> "GuardResult":
        return cls(
            action=GuardAction.WARN,
            risk_level=detection.risk_level if detection else RiskLevel.LOW,
            detection_result=detection,
            message=message,
        )

    @classmethod
    def block(cls, message: str, blocked_by: str, detection: Optional[DetectionResult] = None) -> "GuardResult":
        return cls(
            action=GuardAction.BLOCK,
            risk_level=detection.risk_level if detection else RiskLevel.HIGH,
            detection_result=detection,
            blocked_by=blocked_by,
            message=message,
        )

    @classmethod
    def require_confirm(cls, message: str, detection: Optional[DetectionResult] = None) -> "GuardResult":
        return cls(
            action=GuardAction.REQUIRE_CONFIRM,
            risk_level=detection.risk_level if detection else RiskLevel.MEDIUM,
            detection_result=detection,
            message=message,
        )

    @property
    def is_blocked(self) -> bool:
        return self.action == GuardAction.BLOCK

    @property
    def requires_confirmation(self) -> bool:
        return self.action == GuardAction.REQUIRE_CONFIRM

    @property
    def is_allowed(self) -> bool:
        return self.action in (GuardAction.ALLOW, GuardAction.WARN)


# ========================================================================
# 基础检查器接口
# ========================================================================
class BaseGuard(ABC):
    """防护检查器基类。"""

    def __init__(self, name: str, enabled: bool = True) -> None:
        self.name = name
        self.enabled = enabled
        self._next: Optional[BaseGuard] = None

    def set_next(self, guard: "BaseGuard") -> "BaseGuard":
        """设置下一个检查器。"""
        self._next = guard
        return guard

    @abstractmethod
    async def check(self, input_data: Dict[str, Any]) -> Optional[GuardResult]:
        """执行检查。返回 None 表示继续下一个检查器。"""
        pass

    async def handle(self, input_data: Dict[str, Any]) -> GuardResult:
        """处理请求（责任链模式）。"""
        if not self.enabled:
            if self._next:
                return await self._next.handle(input_data)
            return GuardResult.allow()

        result = await self.check(input_data)
        if result is not None:
            return result

        if self._next:
            return await self._next.handle(input_data)
        return GuardResult.allow()


# ========================================================================
# 🟢 侦察类命令白名单（最高优先级 A级：直接放行，跳过所有后续检查）
# ========================================================================
class ReconnaissanceWhitelistGuard(BaseGuard):
    """侦察类命令白名单 - 渗透测试正常流程，直接放行。"""
    def __init__(self, enabled: bool = True) -> None:
        super().__init__("recon_whitelist", enabled)
        self._recon_commands = {
            # 端口扫描
            "nmap", "masscan", "rustscan",
            # Web 扫描
            "nikto", "gobuster", "dirb", "dirsearch", "ffuf", "wpscan", "nuclei",
            # 信息收集
            "whois", "dig", "nslookup", "host", "curl", "wget", "amass",
            # 漏洞查询
            "searchsploit",
            # 系统信息
            "uname", "id", "whoami", "pwd", "ls", "cat", "head", "tail", "grep", "find",
            # 网络信息
            "netstat", "ss", "ip", "ifconfig", "arp", "route",
            # 工具类
            "python", "python3", "perl", "bash", "sh",
        }

    async def check(self, input_data: Dict[str, Any]) -> Optional[GuardResult]:
        command = input_data.get("command", "")
        if not command:
            return None
        import re
        first_word = re.split(r"[\s;|]+", command.strip())[0].lower()
        if first_word in self._recon_commands:
            logger.info("[🟢 侦察类放行] 命令: %s", first_word)
            return GuardResult.allow(input_data.get("detection_result"))
        return None


# ========================================================================
# AST 语法检查器 (Phase 1)
# ========================================================================
class ASTSyntaxGuard(BaseGuard):
    """AST 语法安全检查器。"""

    def __init__(self, parser: Optional[ASTParser] = None, enabled: bool = True) -> None:
        super().__init__("ast_syntax", enabled)
        self._parser = parser or get_ast_parser()

    async def check(self, input_data: Dict[str, Any]) -> Optional[GuardResult]:
        command = input_data.get("command", "")
        command_type = input_data.get("command_type", "shell")

        if not command:
            return None

        # 执行 AST 分析
        if command_type == "python":
            detection = self._parser.analyze_python_code(command)
        else:
            detection = self._parser.analyze_command(command)

        input_data["detection_result"] = detection

        # 根据风险等级决策
        if detection.has_critical:
            return GuardResult.block(
                f"检测到严重安全风险: {self._format_findings(detection)}",
                blocked_by=self.name,
                detection=detection,
            )

        if detection.has_high_risk:
            return GuardResult.require_confirm(
                f"检测到高风险操作，请确认执行: {self._format_findings(detection)}",
                detection=detection,
            )

        if detection.risk_level == RiskLevel.MEDIUM:
            return GuardResult.warn(
                f"存在中等风险: {self._format_findings(detection)}",
                detection=detection,
            )

        return None

    def _format_findings(self, detection: DetectionResult) -> str:
        findings = [f["description"] for f in detection.findings[:3]]
        return "; ".join(findings)


# ========================================================================
# 白名单检查器
# ========================================================================
class WhitelistGuard(BaseGuard):
    """命令白名单检查器。"""

    def __init__(
        self,
        allowed_commands: Optional[List[str]] = None,
        enabled: bool = True,
    ) -> None:
        super().__init__("whitelist", enabled)
        self._allowed = set(allowed_commands or [
            "ls", "pwd", "echo", "cat", "head", "tail", "grep",
            "find", "which", "whoami", "id", "uname", "date",
            "python", "python3", "pip", "pip3",
        ])

    async def check(self, input_data: Dict[str, Any]) -> Optional[GuardResult]:
        command = input_data.get("command", "")
        if not command:
            return None

        import re
        first_word = re.split(r"[\s;|]+", command.strip())[0].lower()

        # 白名单内直接放行
        if first_word in self._allowed:
            return GuardResult.allow(input_data.get("detection_result"))

        return None

    def add_allowed(self, command: str) -> None:
        self._allowed.add(command.lower())

    def remove_allowed(self, command: str) -> None:
        self._allowed.discard(command.lower())


# ========================================================================
# 黑名单检查器
# ========================================================================
class BlacklistGuard(BaseGuard):
    """命令黑名单检查器（强制拦截）。"""

    def __init__(
        self,
        blocked_commands: Optional[List[str]] = None,
        enabled: bool = True,
    ) -> None:
        super().__init__("blacklist", enabled)
        self._blocked = set(blocked_commands or [
            "rm", "rmdir", "shutdown", "reboot", "halt", "poweroff",
            "dd", "mkfs", "fdisk",
            "sudo", "su", "doas", "pkexec",
        ])

    async def check(self, input_data: Dict[str, Any]) -> Optional[GuardResult]:
        command = input_data.get("command", "")
        if not command:
            return None

        import re
        words = re.split(r"[\s;|]+", command.lower())

        for word in words:
            if word in self._blocked:
                return GuardResult.block(
                    f"命令 '{word}' 在强制黑名单中，已拦截",
                    blocked_by=self.name,
                    detection=input_data.get("detection_result"),
                )

        return None

    def add_blocked(self, command: str) -> None:
        self._blocked.add(command.lower())

    def remove_blocked(self, command: str) -> None:
        self._blocked.discard(command.lower())


# ========================================================================
# 异常行为检测检查器 (Phase 3)
# ========================================================================
class AnomalyDetectionGuard(BaseGuard):
    """异常行为检测检查器。"""

    def __init__(self, enabled: bool = True) -> None:
        super().__init__("anomaly_detection", enabled)
        # 延迟导入避免循环依赖
        from security import get_anomaly_detector, AnomalyLevel
        self._get_anomaly_detector = get_anomaly_detector
        self._AnomalyLevel = AnomalyLevel
        self._detector = get_anomaly_detector()

    async def check(self, input_data: Dict[str, Any]) -> Optional[GuardResult]:
        command = input_data.get("command", "")
        detection_result = input_data.get("detection_result")

        if not command:
            return None

        # 获取风险等级
        risk_level = detection_result.risk_level if detection_result else RiskLevel.SAFE

        # 执行异常检测
        anomaly_result = self._detector.detect(command, risk_level)
        input_data["anomaly_result"] = anomaly_result

        # ✅ 阈值调高到 0.85，仅拦截真正的高危异常
        anomaly_score = getattr(anomaly_result, 'score', 0)
        if anomaly_score < 0.85:
            logger.info("[🟡 异常检测放行] 分数: %.3f < 0.85 阈值", anomaly_score)
            return None

        # 根据异常等级决策
        if anomaly_result.anomaly_level == self._AnomalyLevel.CRITICAL:
            return GuardResult.block(
                f"[异常检测] {anomaly_result.reason}",
                blocked_by=self.name,
                detection=detection_result,
            )

        if anomaly_result.anomaly_level == self._AnomalyLevel.WARNING:
            return GuardResult.require_confirm(
                f"[异常检测] {anomaly_result.reason}",
                detection=detection_result,
            )

        return None


# ========================================================================
# 权限校验检查器
# ========================================================================
class PermissionGuard(BaseGuard):
    """权限校验检查器。"""

    def __init__(
        self,
        require_confirmation_for: Optional[List[RiskLevel]] = None,
        enabled: bool = True,
    ) -> None:
        super().__init__("permission", enabled)
        self._require_confirm = require_confirmation_for or [RiskLevel.HIGH, RiskLevel.MEDIUM]

    async def check(self, input_data: Dict[str, Any]) -> Optional[GuardResult]:
        detection = input_data.get("detection_result")
        user_confirm = input_data.get("user_confirmed", False)

        if not detection:
            return None

        # 用户已确认的放行
        if user_confirm and detection.risk_level in self._require_confirm:
            return GuardResult.allow(detection)

        # 需要确认但未确认
        if detection.risk_level in self._require_confirm and not user_confirm:
            return GuardResult.require_confirm(
                f"操作风险等级为 {detection.risk_level.value}，需要用户确认",
                detection=detection,
            )

        return None


# ========================================================================
# 安全防护链 (增强版 - 集成 Phase 1 & 3)
# ========================================================================
class SecurityChain:
    """安全防护链 - 统一的安全检查入口。
    ✅ 三级分级策略（优先级从高到低）:
    1. 🟢 A级 侦察类白名单：直接放行，跳过所有检查
    2. 🔴 C级 破坏类黑名单：直接拦截，绝不放行
    3. AST 语法检测：注入检测
    4. 异常行为检测：阈值 0.85，仅拦截真正高危
    5. 🟡 B级 利用类确认：需要用户弹窗确认
    """

    def __init__(
        self,
        enable_recon_whitelist: bool = True,   # 🟢 侦察类白名单（默认开启）
        enable_blacklist: bool = True,
        enable_whitelist: bool = False,
        enable_ast: bool = True,
        enable_anomaly: bool = True,
        enable_permission: bool = True,
    ) -> None:
        # 创建检查器链
        self._guards: List[BaseGuard] = []

        # 1. 🟢 侦察类白名单（最先检查，匹配直接放行）
        if enable_recon_whitelist:
            self._guards.append(ReconnaissanceWhitelistGuard(enabled=True))

        # 2. 🔴 黑名单（第二优先级，匹配直接拦截）
        if enable_blacklist:
            self._guards.append(BlacklistGuard(enabled=True))

        # 3. 白名单（快速放行）
        if enable_whitelist:
            self._guards.append(WhitelistGuard(enabled=True))

        # 4. AST 检测（核心安全检测 - Phase 1）
        if enable_ast:
            self._guards.append(ASTSyntaxGuard(enabled=True))

        # 5. 异常行为检测（阈值调高到 0.85）
        if enable_anomaly:
            self._guards.append(AnomalyDetectionGuard(enabled=True))

        # 6. 🟡 权限校验（最后确认）
        if enable_permission:
            self._guards.append(PermissionGuard(enabled=True))

        # 构建责任链
        self._build_chain()

        logger.info("安全防护链初始化完成，包含 %d 个检查器", len(self._guards))
        for guard in self._guards:
            logger.info("  - %s: %s", guard.name, "启用" if guard.enabled else "禁用")

    def _build_chain(self) -> None:
        """构建责任链。"""
        if not self._guards:
            return
        for i in range(len(self._guards) - 1):
            self._guards[i].set_next(self._guards[i + 1])

    async def validate(
        self,
        command: str,
        command_type: str = "shell",
        user_id: str = "default",
        user_confirmed: bool = False,
        context: Optional[Dict[str, Any]] = None,
    ) -> GuardResult:
        """执行安全校验。

        参数:
            command: 待检查的命令
            command_type: 命令类型 (shell/python)
            user_id: 用户ID
            user_confirmed: 用户是否已确认
            context: 额外上下文

        返回:
            GuardResult 校验结果
        """
        input_data = {
            "command": command,
            "command_type": command_type,
            "user_id": user_id,
            "user_confirmed": user_confirmed,
            **(context or {}),
        }

        if not self._guards:
            return GuardResult.allow()

        return await self._guards[0].handle(input_data)

    def add_guard(self, guard: BaseGuard, position: Optional[int] = None) -> None:
        """添加自定义检查器。"""
        if position is None or position >= len(self._guards):
            self._guards.append(guard)
        else:
            self._guards.insert(position, guard)
        self._build_chain()
        logger.info("已添加检查器: %s", guard.name)

    def remove_guard(self, name: str) -> bool:
        """移除检查器。"""
        for i, guard in enumerate(self._guards):
            if guard.name == name:
                del self._guards[i]
                self._build_chain()
                logger.info("已移除检查器: %s", name)
                return True
        return False

    def enable_guard(self, name: str) -> bool:
        """启用检查器。"""
        for guard in self._guards:
            if guard.name == name:
                guard.enabled = True
                logger.info("已启用检查器: %s", name)
                return True
        return False

    def disable_guard(self, name: str) -> bool:
        """禁用检查器。"""
        for guard in self._guards:
            if guard.name == name:
                guard.enabled = False
                logger.info("已禁用检查器: %s", name)
                return True
        return False


# 全局单例
_security_chain: Optional[SecurityChain] = None


def get_security_chain() -> SecurityChain:
    """获取安全防护链单例。"""
    global _security_chain
    if _security_chain is None:
        _security_chain = SecurityChain()
    return _security_chain
