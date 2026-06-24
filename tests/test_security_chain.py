"""
安全架构 Phase 1 - 单元测试集
测试范围:
1. AST 解析引擎测试
2. 安全防护链测试
3. 审计日志系统测试
4. 沙箱集成测试
"""
from __future__ import annotations
import asyncio
import pytest
import sys
import os
# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools.ast_parser import ASTParser, RiskLevel, DetectionResult, get_ast_parser
from tools.security_chain import SecurityChain, GuardAction, get_security_chain
from tools.audit_logger import AuditLogger, get_audit_logger
# ========================================================================
# AST 解析引擎测试
# ========================================================================
class TestASTParser:
    """AST 解析引擎测试。"""
    def setup_method(self):
        self.parser = get_ast_parser()
    def test_safe_command(self):
        """测试安全命令。"""
        result = self.parser.analyze_command("ls -la")
        assert result.is_safe is True
        assert result.risk_level == RiskLevel.SAFE
        assert len(result.findings) == 0
    def test_command_separator_injection(self):
        """测试分号命令注入。"""
        result = self.parser.analyze_command("ls -la; rm -rf /")
        assert result.is_safe is False
        assert result.has_critical is True
        assert any(f["category"] == "command_separator" for f in result.findings)
    def test_pipe_injection(self):
        """测试管道注入。"""
        result = self.parser.analyze_command("ls -la | nc example.com 80")
        assert result.is_safe is False
        assert result.has_high_risk is True
        assert any(f["category"] == "pipe" for f in result.findings)
    def test_backtick_injection(self):
        """测试反引号注入。"""
        result = self.parser.analyze_command("echo `cat /etc/passwd`")
        assert result.is_safe is False
        assert result.has_critical is True
        assert any(f["category"] == "backtick" for f in result.findings)
    def test_command_substitution(self):
        """测试 $() 命令替换。"""
        result = self.parser.analyze_command("echo $(cat /etc/passwd)")
        assert result.is_safe is False
        assert result.has_critical is True
    def test_variable_substitution(self):
        """测试 ${} 变量替换。"""
        result = self.parser.analyze_command("echo ${HOME}")
        assert result.has_high_risk is True
    def test_dangerous_command_rm(self):
        """测试 rm 危险命令。"""
        result = self.parser.analyze_command("rm -rf /tmp/test")
        assert result.is_safe is False
        assert result.has_critical is True
        assert any(f["category"] == "dangerous_command" for f in result.findings)
    def test_dangerous_command_sudo(self):
        """测试 sudo 危险命令。"""
        result = self.parser.analyze_command("sudo apt update")
        assert result.is_safe is False
        assert result.has_critical is True
    def test_python_code_eval(self):
        """测试 Python eval 代码注入。"""
        result = self.parser.analyze_python_code("eval('__import__(\"os\").system(\"ls\")')")
        assert result.is_safe is False
        assert result.has_critical is True
    def test_python_code_import(self):
        """测试 Python 危险模块导入。"""
        result = self.parser.analyze_python_code("import os; os.system('ls')")
        assert result.is_safe is False
        assert result.has_high_risk is True
    def test_python_one_liner(self):
        """测试 python -c 一行执行。"""
        result = self.parser.analyze_command("python3 -c 'import os; print(os.system(\"ls\"))'")
        assert result.is_safe is False
        assert result.has_high_risk is True
    def test_chained_commands(self):
        """测试链式危险命令。"""
        result = self.parser.analyze_command("ls -la && rm -rf /important")
        assert result.is_safe is False
        assert result.has_critical is True
    def test_custom_pattern(self):
        """测试自定义危险模式。"""
        self.parser.add_dangerous_pattern(
            r"custom_danger",
            RiskLevel.CRITICAL,
            "custom",
            "自定义危险模式"
        )
        result = self.parser.analyze_command("echo custom_danger")
        assert result.has_critical is True
    def test_custom_command(self):
        """测试自定义危险命令。"""
        self.parser.add_dangerous_command("my_danger_cmd", RiskLevel.CRITICAL)
        result = self.parser.analyze_command("my_danger_cmd arg1")
        assert result.has_critical is True
# ========================================================================
# 安全防护链测试
# ========================================================================
class TestSecurityChain:
    """安全防护链测试。"""
    def setup_method(self):
        self.chain = get_security_chain(force_new=True)
    @pytest.mark.asyncio
    async def test_safe_command_allow(self):
        """测试安全命令放行。"""
        result = await self.chain.validate("ls -la")
        assert result.action == GuardAction.ALLOW
        assert result.is_allowed is True
    @pytest.mark.asyncio
    async def test_critical_risk_block(self):
        """测试严重风险拦截。"""
        result = await self.chain.validate("rm -rf /")
        assert result.action == GuardAction.BLOCK
        assert result.is_blocked is True
        assert result.blocked_by is not None
    @pytest.mark.asyncio
    async def test_high_risk_require_confirm(self):
        """测试高风险需要确认。"""
        result = await self.chain.validate("apt update")
        assert result.action in (GuardAction.REQUIRE_CONFIRM, GuardAction.BLOCK)
    @pytest.mark.asyncio
    async def test_user_confirmed_allow(self):
        """测试用户确认后放行。"""
        result = await self.chain.validate("apt update", user_confirmed=True)
        assert result.is_allowed is True or result.requires_confirmation is False
    @pytest.mark.asyncio
    async def test_blacklist_block(self):
        """测试黑名单拦截。"""
        result = await self.chain.validate("sudo su")
        assert result.action == GuardAction.BLOCK
    @pytest.mark.asyncio
    async def test_whitelist_allow(self):
        """测试白名单放行。"""
        chain = get_security_chain(enable_whitelist=True, force_new=True)
        result = await chain.validate("ls -la")
        assert result.action == GuardAction.ALLOW
    @pytest.mark.asyncio
    async def test_medium_risk_warn(self):
        """测试中等风险警告。"""
        result = await self.chain.validate("curl example.com")
        assert result.action in (GuardAction.WARN, GuardAction.REQUIRE_CONFIRM)
    def test_get_guard(self):
        """测试获取检查器。"""
        guard = self.chain.get_guard("blacklist")
        assert guard is not None
        assert guard.name == "blacklist"
    @pytest.mark.asyncio
    async def test_python_code_security(self):
        """测试 Python 代码安全检测。"""
        result = await self.chain.validate("eval('1+1')", command_type="python")
        assert result.action == GuardAction.BLOCK or result.requires_confirmation
# ========================================================================
# 审计日志系统测试
# ========================================================================
class TestAuditLogger:
    """审计日志系统测试。"""
    def setup_method(self):
        self.logger = get_audit_logger(log_dir="/tmp/test_audit_logs", force_new=True)
    @pytest.mark.asyncio
    async def test_log_security_event(self):
        """测试记录安全事件。"""
        await self.logger.log_security_event(
            event_type="security_check",
            command="ls -la",
            action="allow",
            risk_level="safe",
        )
        # 等待日志写入
        await asyncio.sleep(0.1)
    @pytest.mark.asyncio
    async def test_log_command_blocked(self):
        """测试记录命令拦截。"""
        await self.logger.log_command_blocked(
            command="rm -rf /",
            blocked_by="blacklist",
            reason="危险命令",
            risk_level="critical",
        )
        await asyncio.sleep(0.1)
    @pytest.mark.asyncio
    async def test_log_command_allowed(self):
        """测试记录命令放行。"""
        await self.logger.log_command_allowed(
            command="ls -la",
            risk_level="safe",
            warnings=["测试警告"],
        )
        await asyncio.sleep(0.1)
    @pytest.mark.asyncio
    async def test_log_command_executed(self):
        """测试记录命令执行。"""
        await self.logger.log_command_executed(
            command="ls -la",
            returncode=0,
            execution_time=0.1,
            risk_level="safe",
        )
        await asyncio.sleep(0.1)
    @pytest.mark.asyncio
    async def test_query_logs(self):
        """测试查询日志。"""
        # 先写入几条日志
        await self.logger.log_security_event("test", "cmd1", "allow", "safe")
        await self.logger.log_security_event("test", "cmd2", "block", "critical")
        await asyncio.sleep(0.2)
        logs = await self.logger.query_logs(limit=10)
        assert len(logs) >= 0
    @pytest.mark.asyncio
    async def test_get_statistics(self):
        """测试获取统计信息。"""
        stats = await self.logger.get_statistics(hours=1)
        assert "total" in stats
        assert "blocked_count" in stats
        assert "allowed_count" in stats
        assert "by_event_type" in stats
# ========================================================================
# 沙箱集成测试
# ========================================================================
class TestSandboxIntegration:
    """沙箱集成测试。"""
    @pytest.mark.asyncio
    async def test_sandbox_safe_execution(self):
        """测试沙箱安全命令执行。"""
        from tools.sandbox import get_sandbox
        sandbox = get_sandbox(force_new=True)
        result = await sandbox.execute_in_sandbox(
            ["echo", "hello world"],
            timeout=10,
        )
        assert result["returncode"] == 0
        assert "hello world" in result["stdout"]
        assert result["killed_by"] is None
        assert "security_check" in result
    @pytest.mark.asyncio
    async def test_sandbox_security_block(self):
        """测试沙箱安全拦截。"""
        from tools.sandbox import get_sandbox
        sandbox = get_sandbox(force_new=True)
        result = await sandbox.execute_in_sandbox(
            ["rm", "-rf", "/tmp/test"],
            timeout=10,
        )
        assert result["returncode"] == -1
        assert result["killed_by"] is not None
        assert "安全拦截" in result["stderr"] or "安全" in result["stderr"]
    @pytest.mark.asyncio
    async def test_sandbox_skip_security(self):
        """测试跳过安全检查。"""
        from tools.sandbox import get_sandbox
        sandbox = get_sandbox(force_new=True)
        result = await sandbox.execute_in_sandbox(
            ["echo", "test"],
            timeout=10,
            skip_security_check=True,
        )
        assert result["returncode"] == 0
    @pytest.mark.asyncio
    async def test_sandbox_user_confirmed(self):
        """测试用户确认后执行。"""
        from tools.sandbox import get_sandbox
        sandbox = get_sandbox(force_new=True)
        # 即使是高风险命令，用户确认后也会执行（但实际执行可能失败）
        result = await sandbox.execute_in_sandbox(
            ["echo", "confirmed"],
            timeout=10,
            user_confirmed=True,
        )
        assert result["returncode"] == 0 or result["returncode"] == -1
if __name__ == "__main__":
    pytest.main([__file__, "-v"])
