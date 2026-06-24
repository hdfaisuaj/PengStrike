"""
完整集成测试 (tests/test_full_integration.py)
测试覆盖:
- Phase 1+3 完整安全防护链
- 各模块间协作
- 不破坏现有功能
- 端到端安全校验流程
"""
import sys
import asyncio
import pytest

sys.path.insert(0, ".")
from tools import get_security_chain, GuardAction


class TestFullSecurityChain:
    """测试完整安全防护链。"""

    def setup_method(self):
        self.chain = get_security_chain()

    def test_security_chain_initialization(self):
        """测试防护链初始化。"""
        assert self.chain is not None
        assert len(self.chain._guards) == 5  # blacklist + whitelist + ast + anomaly + permission
        guard_names = [g.name for g in self.chain._guards]
        assert "blacklist" in guard_names
        assert "whitelist" in guard_names
        assert "ast_syntax" in guard_names
        assert "anomaly_detection" in guard_names
        assert "permission" in guard_names

    @pytest.mark.asyncio
    async def test_safe_command_allow(self):
        """测试安全命令放行。"""
        result = await self.chain.validate("ls -la", user_id="admin")
        assert result.action == GuardAction.ALLOW
        assert result.is_allowed

    @pytest.mark.asyncio
    async def test_blacklist_block(self):
        """测试黑名单拦截。"""
        result = await self.chain.validate("rm -rf /tmp", user_id="admin")
        assert result.action == GuardAction.BLOCK
        assert result.blocked_by == "blacklist"

    @pytest.mark.asyncio
    async def test_ast_injection_detection(self):
        """测试AST注入检测。"""
        result = await self.chain.validate(
            "ls -la; cat /etc/passwd",
            user_id="admin"
        )
        # 分号注入应该被检测
        assert result.action in (GuardAction.BLOCK, GuardAction.REQUIRE_CONFIRM)

    @pytest.mark.asyncio
    async def test_anomaly_detection_complex_injection(self):
        """测试异常检测复杂注入。"""
        result = await self.chain.validate(
            "ls -la; cat /etc/passwd | grep root; id; whoami; echo $(id)",
            user_id="admin"
        )
        # 复杂注入应该触发异常检测或AST检测
        assert result.action in (GuardAction.BLOCK, GuardAction.REQUIRE_CONFIRM)

    @pytest.mark.asyncio
    async def test_user_confirmed_override(self):
        """测试用户确认覆盖。"""
        # 先不确认
        result1 = await self.chain.validate(
            "curl example.com",
            user_id="default",
            user_confirmed=False
        )
        # 用户确认后
        result2 = await self.chain.validate(
            "curl example.com",
            user_id="default",
            user_confirmed=True
        )
        # 确认后应该放行
        assert result2.action == GuardAction.ALLOW

    @pytest.mark.asyncio
    async def test_result_contains_check_results(self):
        """测试结果包含检查器的输出。"""
        result = await self.chain.validate("ls -la", user_id="admin")
        # 应该包含AST检测结果
        assert result.detection_result is not None
        # 应该包含异常检测结果
        assert result.anomaly_result is not None


class TestBackwardCompatibility:
    """测试向后兼容性。"""

    @pytest.mark.asyncio
    async def test_old_api_still_works(self):
        """测试旧API仍然可用。"""
        chain = get_security_chain()
        # 只传旧参数也能工作
        result = await chain.validate("ls -la")
        assert result.action == GuardAction.ALLOW

    @pytest.mark.asyncio
    async def test_disable_modules(self):
        """测试禁用模块。"""
        # 只启用Phase 1
        from tools.security_chain import SecurityChain
        chain_phase1 = SecurityChain(
            enable_whitelist=False,
            enable_anomaly=False,
            enable_permission=False
        )
        assert len(chain_phase1._guards) == 2  # blacklist + ast
        result = await chain_phase1.validate("ls -la")
        assert result.action == GuardAction.ALLOW


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
