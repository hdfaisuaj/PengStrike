"""
中间件系统单元测试
"""

import pytest
import asyncio
import tempfile
import os
from pathlib import Path

from middleware.base import IMiddleware, MiddlewareContext
from middleware.builtin import (
    IPBlacklistMiddleware,
    ASTCommandInjectionMiddleware,
    AnomalyDetectionMiddleware,
    AuditLogMiddleware
)
from middleware.manager import MiddlewareManager


class TestMiddlewareContext:
    """测试中间件上下文"""

    def test_context_creation(self):
        ctx = MiddlewareContext(
            user_id="test_user",
            ip_address="127.0.0.1",
            command="test command"
        )
        assert ctx.user_id == "test_user"
        assert ctx.ip_address == "127.0.0.1"
        assert ctx.command == "test command"
        assert ctx.request_id is not None

    def test_context_interrupt(self):
        ctx = MiddlewareContext()
        assert not ctx.is_interrupted()
        ctx.interrupt("test reason")
        assert ctx.is_interrupted()
        assert ctx.get_interrupt_reason() == "test reason"

    def test_context_response(self):
        ctx = MiddlewareContext()
        ctx.set_response({"data": "test"})
        assert ctx.get_response() == {"data": "test"}

    def test_context_error(self):
        ctx = MiddlewareContext()
        error = ValueError("test error")
        ctx.set_error(error)
        assert ctx.get_error() == error


class TestIPBlacklistMiddleware:
    """测试IP黑名单中间件"""

    @pytest.mark.asyncio
    async def test_blacklist_block(self):
        mw = IPBlacklistMiddleware()
        mw.add_to_blacklist("192.168.1.1")

        ctx = MiddlewareContext(ip_address="192.168.1.1")
        await mw.process_request(ctx)
        assert ctx.is_interrupted()

    @pytest.mark.asyncio
    async def test_whitelist_allow(self):
        mw = IPBlacklistMiddleware()
        mw.add_to_blacklist("192.168.1.1")
        mw.configure({"whitelist": ["192.168.1.1"]})

        ctx = MiddlewareContext(ip_address="192.168.1.1")
        await mw.process_request(ctx)
        assert not ctx.is_interrupted()

    @pytest.mark.asyncio
    async def test_normal_ip_allow(self):
        mw = IPBlacklistMiddleware()
        mw.add_to_blacklist("192.168.1.1")

        ctx = MiddlewareContext(ip_address="10.0.0.1")
        await mw.process_request(ctx)
        assert not ctx.is_interrupted()


class TestASTCommandInjectionMiddleware:
    """测试AST命令注入检测中间件"""

    @pytest.mark.asyncio
    async def test_dangerous_function_block(self):
        mw = ASTCommandInjectionMiddleware()
        ctx = MiddlewareContext(command="eval('__import__(\"os\")')")
        await mw.process_request(ctx)
        assert ctx.is_interrupted()

    @pytest.mark.asyncio
    async def test_safe_command_allow(self):
        mw = ASTCommandInjectionMiddleware()
        ctx = MiddlewareContext(command="nmap -sV 127.0.0.1")
        await mw.process_request(ctx)
        assert not ctx.is_interrupted()

    @pytest.mark.asyncio
    async def test_pipe_shell_block(self):
        mw = ASTCommandInjectionMiddleware()
        ctx = MiddlewareContext(command="cat /etc/passwd | bash")
        await mw.process_request(ctx)
        assert ctx.is_interrupted()


class TestAnomalyDetectionMiddleware:
    """测试异常行为检测中间件"""

    @pytest.mark.asyncio
    async def test_rate_limit_exceeded(self):
        mw = AnomalyDetectionMiddleware()
        mw.configure({'rate_limit': 2})

        # 发送3个请求
        for i in range(3):
            ctx = MiddlewareContext(ip_address="192.168.1.1")
            await mw.process_request(ctx)

        # 第三个应该被阻断
        assert ctx.is_interrupted()

    @pytest.mark.asyncio
    async def test_anomaly_pattern_detection(self):
        mw = AnomalyDetectionMiddleware()
        ctx = MiddlewareContext(command="chmod 777 /etc/passwd")
        await mw.process_request(ctx)
        assert 'anomaly_warning' in ctx.extra


class TestAuditLogMiddleware:
    """测试审计日志中间件"""

    @pytest.mark.asyncio
    async def test_audit_log_writes(self):
        with tempfile.NamedTemporaryFile(mode='w', delete=False) as f:
            log_path = f.name

        try:
            mw = AuditLogMiddleware()
            mw.configure({'log_file': log_path})

            ctx = MiddlewareContext(user_id="test", command="test cmd")
            await mw.process_request(ctx)
            await mw.process_response(ctx)

            # 验证日志文件有内容
            assert os.path.getsize(log_path) > 0
        finally:
            os.unlink(log_path)


class TestMiddlewareManager:
    """测试中间件管理器"""

    def test_register_middleware(self):
        manager = MiddlewareManager()
        mw = IPBlacklistMiddleware()
        manager.register(mw)
        assert manager.get(mw.name) == mw

    def test_unregister_middleware(self):
        manager = MiddlewareManager()
        mw = IPBlacklistMiddleware()
        manager.register(mw)
        assert manager.unregister(mw.name) == True
        assert manager.get(mw.name) is None

    def test_list_middlewares(self):
        manager = MiddlewareManager()
        mw1 = IPBlacklistMiddleware()
        mw2 = AuditLogMiddleware()
        manager.register(mw1)
        manager.register(mw2)
        assert len(manager.list()) == 2

    def test_enable_disable(self):
        manager = MiddlewareManager()
        mw = IPBlacklistMiddleware()
        manager.register(mw)
        assert manager.disable(mw.name) == True
        assert manager.enable(mw.name) == True

    @pytest.mark.asyncio
    async def test_execute_chain(self):
        manager = MiddlewareManager()
        manager.register(IPBlacklistMiddleware())
        manager.register(AuditLogMiddleware())

        ctx = MiddlewareContext(ip_address="127.0.0.1")

        async def handler():
            return {"result": "success"}

        result = await manager.execute(ctx, handler)
        assert result == {"result": "success"}

    @pytest.mark.asyncio
    async def test_interrupted_chain(self):
        manager = MiddlewareManager()
        mw = IPBlacklistMiddleware()
        mw.add_to_blacklist("192.168.1.1")
        manager.register(mw)

        ctx = MiddlewareContext(ip_address="192.168.1.1")

        async def handler():
            return {"result": "success"}

        result = await manager.execute(ctx, handler)
        assert result['interrupted'] == True

    def test_load_yaml_config(self):
        config_content = """
middlewares:
  - class: middleware.builtin.IPBlacklistMiddleware
    name: ip_blacklist
    enabled: true
    priority: 10
    config:
      blacklist: ["192.168.1.1"]
        """
        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            f.write(config_content)
            config_path = f.name

        try:
            manager = MiddlewareManager(config_path)
            assert len(manager.list()) >= 1
        finally:
            os.unlink(config_path)

    def test_save_config(self):
        manager = MiddlewareManager()
        manager.register(IPBlacklistMiddleware())

        with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
            config_path = f.name

        try:
            manager.save_config(config_path)
            assert os.path.exists(config_path)
            assert os.path.getsize(config_path) > 0
        finally:
            os.unlink(config_path)
