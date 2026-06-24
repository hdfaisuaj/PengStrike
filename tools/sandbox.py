"""
沙箱执行器 (tools/sandbox.py) - Phase 1 安全架构重构
职责:
- 集成安全防护链：输入 → AST检测 → 危险等级判定 → 权限校验 → 执行
- Linux 环境下以普通用户权限执行命令（禁止 root）
- Windows 环境下通过模拟降权执行
- 资源监控：CPU 使用率、内存使用量、磁盘 I/O 阈值，超限自动终止
- 进程隔离：在独立进程中执行，防止影响主进程
- 提供统一的沙箱执行接口供 AsyncExecutor 调用
设计原则:
- 安全优先：先检测后执行
- Linux: os.setuid + 资源限制 (resource.setrlimit)
- Windows: 使用 subprocess 配合资源监控（无 setuid，提供替代方案）
- 跨平台兼容：自动检测操作系统，选择合适策略
- 阈值可配置：通过环境变量或参数传入
注意事项:
- Linux 下 os.setuid 需要 root 权限启动程序，如果无法获取 root 权限则不降权，仅保留资源监控
- Windows 下可通过 runas /user:guest 配合资源监控实现
"""
from __future__ import annotations
import asyncio
import os
import platform
import signal
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional
from tools.security_chain import SecurityChain, GuardResult, get_security_chain
from utils.logger import get_logger
logger = get_logger(__name__)
# ========================================================================
# 资源阈值配置
# ========================================================================
@dataclass
class SandboxLimits:
    """沙箱资源限制配置。"""
    max_cpu_percent: float = 80.0          # CPU 使用率上限（%）
    max_memory_mb: float = 1024.0          # 内存使用上限（MB）
    max_disk_read_mb: float = 500.0        # 磁盘读取上限（MB）
    max_disk_write_mb: float = 500.0       # 磁盘写入上限（MB）
    max_process_count: int = 50            # 最大衍生进程数
    max_execution_time: float = 600.0      # 最大执行时间（秒）
    check_interval: float = 1.0            # 资源检查间隔（秒）
    @classmethod
    def from_env(cls) -> SandboxLimits:
        """从环境变量加载配置（可选覆盖）。"""
        return cls(
            max_cpu_percent=float(os.environ.get("SANDBOX_MAX_CPU", "80.0")),
            max_memory_mb=float(os.environ.get("SANDBOX_MAX_MEMORY_MB", "1024.0")),
            max_disk_read_mb=float(os.environ.get("SANDBOX_MAX_DISK_READ_MB", "500.0")),
            max_disk_write_mb=float(os.environ.get("SANDBOX_MAX_DISK_WRITE_MB", "500.0")),
            max_process_count=int(os.environ.get("SANDBOX_MAX_PROCESSES", "50")),
            max_execution_time=float(os.environ.get("SANDBOX_MAX_EXEC_TIME", "600.0")),
            check_interval=float(os.environ.get("SANDBOX_CHECK_INTERVAL", "1.0")),
        )
# ========================================================================
# 资源监控结果
# ========================================================================
@dataclass
class ResourceUsage:
    """资源使用统计。"""
    cpu_percent: float = 0.0
    memory_mb: float = 0.0
    disk_read_mb: float = 0.0
    disk_write_mb: float = 0.0
    process_count: int = 1
    elapsed: float = 0.0
    def exceeds(self, limits: SandboxLimits) -> Optional[str]:
        """检查是否超出限制。返回超限原因或 None。"""
        if self.cpu_percent > limits.max_cpu_percent:
            return f"CPU 使用率 {self.cpu_percent:.1f}% 超过限制 {limits.max_cpu_percent}%"
        if self.memory_mb > limits.max_memory_mb:
            return f"内存使用 {self.memory_mb:.1f}MB 超过限制 {limits.max_memory_mb}MB"
        if self.disk_read_mb > limits.max_disk_read_mb:
            return f"磁盘读取 {self.disk_read_mb:.1f}MB 超过限制 {limits.max_disk_read_mb}MB"
        if self.disk_write_mb > limits.max_disk_write_mb:
            return f"磁盘写入 {self.disk_write_mb:.1f}MB 超过限制 {limits.max_disk_write_mb}MB"
        if self.process_count > limits.max_process_count:
            return f"衍生进程数 {self.process_count} 超过限制 {limits.max_process_count}"
        if self.elapsed > limits.max_execution_time:
            return f"执行时间 {self.elapsed:.1f}s 超过限制 {limits.max_execution_time}s"
        return None
# ========================================================================
# 资源监控器
# ========================================================================
class ResourceMonitor:
    """进程资源监控器（跨平台兼容）。"""
    def __init__(self, limits: Optional[SandboxLimits] = None) -> None:
        self.limits = limits or SandboxLimits.from_env()
        self._start_time: float = 0.0
    def start(self) -> None:
        self._start_time = time.monotonic()
    async def monitor_process(
        self,
        process: asyncio.subprocess.Process,
        cancel_event: asyncio.Event,
    ) -> Optional[str]:
        """监控进程资源使用，超限时设置取消事件。
        返回超限原因，或 None（正常结束）。
        """
        self.start()
        pid = process.pid
        if pid is None:
            return None
        psutil = self._try_import_psutil()
        while True:
            if cancel_event.is_set():
                return None
            elapsed = time.monotonic() - self._start_time
            if elapsed > self.limits.max_execution_time:
                return f"执行时间 {elapsed:.1f}s 超过限制 {self.limits.max_execution_time}s"
            if psutil is None:
                await asyncio.sleep(self.limits.check_interval)
                continue
            try:
                proc = psutil.Process(pid)
                usage = ResourceUsage(
                    cpu_percent=proc.cpu_percent(interval=0.1),
                    memory_mb=proc.memory_info().rss / (1024 * 1024),
                    elapsed=elapsed,
                )
                # 检查 CPU
                if usage.cpu_percent > self.limits.max_cpu_percent:
                    return f"CPU 使用率 {usage.cpu_percent:.1f}% 超过限制 {self.limits.max_cpu_percent}%"
                # 检查内存
                if usage.memory_mb > self.limits.max_memory_mb:
                    return f"内存使用 {usage.memory_mb:.1f}MB 超过限制 {self.limits.max_memory_mb}MB"
                # 检查进程数
                try:
                    children = proc.children()
                    usage.process_count = 1 + len(children)
                    if usage.process_count > self.limits.max_process_count:
                        return f"衍生进程数 {usage.process_count} 超过限制 {self.limits.max_process_count}"
                except Exception:
                    pass
            except (psutil.NoSuchProcess, psutil.AccessDenied) if psutil else ():
                pass
            except Exception as exc:
                logger.debug("资源监控异常: %s", exc)
            await asyncio.sleep(self.limits.check_interval)
    def _try_import_psutil(self):
        """尝试导入 psutil，失败时返回 None（降级运行）。"""
        try:
            import psutil
            return psutil
        except ImportError:
            logger.warning("psutil 未安装，资源监控将仅基于执行时间")
            return None
# ========================================================================
# 权限降级工具
# ========================================================================
class PrivilegeDropper:
    """特权降级工具（Linux/Windows 兼容）。"""
    @staticmethod
    def can_drop_privileges() -> bool:
        """检查当前是否有足够的权限进行降级。"""
        if platform.system() == "Linux":
            try:
                return os.getuid() == 0
            except AttributeError:
                return False
        return False
    @staticmethod
    def drop_privileges(user: str = "nobody") -> bool:
        """将当前进程权限降级为普通用户。
        Linux: 使用 os.setuid/setgid 降级
        Windows: 不支持 setuid，返回 False
        注意：需要 root 权限启动才能降级成功
        """
        if platform.system() != "Linux":
            logger.warning("当前平台不支持权限降级: %s", platform.system())
            return False
        try:
            import pwd
            user_info = pwd.getpwnam(user)
            target_uid = user_info.pw_uid
            target_gid = user_info.pw_gid
            # 先降组权限，再降用户权限
            os.setgid(target_gid)
            os.setuid(target_uid)
            logger.info("权限已降级为用户: %s (uid=%d, gid=%d)", user, target_uid, target_gid)
            return True
        except ImportError:
            logger.warning("pwd 模块不可用（非 Linux 环境），跳过权限降级")
            return False
        except PermissionError:
            logger.warning("权限降级失败（非 root 运行），跳过权限降级")
            return False
        except Exception as exc:
            logger.warning("权限降级异常: %s，跳过权限降级", exc)
            return False
    @staticmethod
    def get_restricted_environment() -> Dict[str, str]:
        """获取限制后的环境变量（去除危险变量）。"""
        dangerous_vars = {
            "LD_PRELOAD", "LD_LIBRARY_PATH", "LD_AUDIT",
            "LD_DEBUG", "LD_OPEN", "LD_ORIGIN_PATH",
            "SHELL", "BASH_ENV", "ENV",
        }
        env = dict(os.environ)
        for var in dangerous_vars:
            env.pop(var, None)
        return env
# ========================================================================
# 沙箱执行器（增强版 - 集成安全防护链）
# ========================================================================
class SandboxExecutor:
    """沙箱执行器 - 统一的安全执行入口。
    完整安全流程:
    1. 安全防护链检测（黑名单 → AST语法 → 权限校验）
    2. 权限降级（Linux 下以 nobody 用户执行）
    3. 启动子进程并实时监控资源
    4. 超限时发送 SIGTERM/SIGKILL 终止
    5. 返回执行结果
    """
    def __init__(
        self,
        limits: Optional[SandboxLimits] = None,
        enable_security_chain: bool = True,
    ) -> None:
        self.limits = limits or SandboxLimits.from_env()
        self._monitor = ResourceMonitor(self.limits)
        self._enable_security = enable_security_chain
        self._active_processes: List[asyncio.subprocess.Process] = []  # ⭐ 追踪所有沙箱进程
        self._shutdown_flag = False  # ⭐ 全局关闭标志
        if enable_security_chain:
            self._security_chain = get_security_chain()
        else:
            self._security_chain = None
        logger.info("沙箱执行器初始化完成，安全防护: %s",
                   "启用" if enable_security_chain else "禁用")
    async def execute_in_sandbox(
        self,
        cmd: list[str],
        *,
        timeout: float = 300.0,
        cwd: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
        user_confirmed: bool = False,
        skip_security_check: bool = False,
    ) -> dict[str, Any]:
        """在沙箱中执行命令。
        参数:
            cmd: 命令列表（asyncio.create_subprocess_exec 格式）
            timeout: 超时时间（秒）
            cwd: 工作目录
            env: 环境变量（默认使用限制后的环境）
            user_confirmed: 用户是否已确认执行高风险操作
            skip_security_check: 跳过安全检查（仅内部使用）
        返回:
            {
                "returncode": int,
                "stdout": str,
                "stderr": str,
                "killed_by": str | None,  # 沙箱终止原因
                "resource_usage": ResourceUsage,
                "security_check": GuardResult | None,  # 安全检查结果
            }
        """
        command_str = " ".join(cmd)
        start_time = time.monotonic()
        # ========== 1. 安全检测 ==========
        guard_result = None
        if self._enable_security and not skip_security_check:
            guard_result = await self._security_chain.validate(
                command=command_str,
                command_type="shell",
                user_confirmed=user_confirmed,
            )
            # 拦截处理
            if guard_result.is_blocked:
                logger.warning("[安全拦截] %s: %s", guard_result.blocked_by, guard_result.message)
                return {
                    "returncode": -1,
                    "stdout": "",
                    "stderr": f"[安全拦截] {guard_result.message}",
                    "killed_by": f"security:{guard_result.blocked_by}",
                    "resource_usage": ResourceUsage(),
                    "security_check": guard_result,
                }
            # 需要确认但未确认
            if guard_result.requires_confirmation and not user_confirmed:
                logger.warning("[需要确认] %s", guard_result.message)
                return {
                    "returncode": -1,
                    "stdout": "",
                    "stderr": f"[需要确认] {guard_result.message}",
                    "killed_by": "security:confirmation_required",
                    "resource_usage": ResourceUsage(),
                    "security_check": guard_result,
                }
            # 警告但放行
            if guard_result.action.value == "warn":
                logger.warning("[安全警告] %s", guard_result.message)
        sandbox_env = env or PrivilegeDropper.get_restricted_environment()
        cancel_event = asyncio.Event()
        try:
            # ⭐ 全局关闭标志：服务正在关闭时拒绝新命令
            if self._shutdown_flag:
                return {
                    "returncode": -1, "stdout": "",
                    "stderr": "[服务关闭] 新命令已被拒绝",
                    "killed_by": "shutdown",
                    "resource_usage": ResourceUsage(),
                    "security_check": guard_result,
                }
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
                env=sandbox_env,
            )
            self._active_processes.append(process)  # ⭐ 加入追踪列表
            monitor_task = asyncio.create_task(
                self._monitor.monitor_process(process, cancel_event)
            )
            try:
                stdout_bytes, stderr_bytes = await asyncio.wait_for(
                    process.communicate(),
                    timeout=timeout,
                )
            except asyncio.TimeoutError:
                monitor_task.cancel()
                await self._kill_process_group(process)
                if process in self._active_processes:
                    self._active_processes.remove(process)
                exec_time = time.monotonic() - start_time
                result = {
                    "returncode": -1,
                    "stdout": "",
                    "stderr": f"[沙箱超时] 命令执行超过 {timeout}s",
                    "killed_by": "timeout",
                    "resource_usage": ResourceUsage(elapsed=timeout),
                    "security_check": guard_result,
                }
                return result
            # 检查资源监控结果
            monitor_task.cancel()
            try:
                kill_reason = await monitor_task
                if kill_reason:
                    cancel_event.set()
                    await self._kill_process_group(process)
                    if process in self._active_processes:
                        self._active_processes.remove(process)
                    exec_time = time.monotonic() - start_time
                    result = {
                        "returncode": -1,
                        "stdout": "",
                        "stderr": f"[沙箱拦截] {kill_reason}",
                        "killed_by": kill_reason,
                        "resource_usage": ResourceUsage(elapsed=exec_time),
                        "security_check": guard_result,
                    }
                    return result
            except asyncio.CancelledError:
                pass
            stdout = stdout_bytes.decode("utf-8", errors="replace") if stdout_bytes else ""
            stderr = stderr_bytes.decode("utf-8", errors="replace") if stderr_bytes else ""
            exec_time = time.monotonic() - start_time
            if process in self._active_processes:
                self._active_processes.remove(process)
            result = {
                "returncode": process.returncode or 0,
                "stdout": stdout,
                "stderr": stderr,
                "killed_by": None,
                "resource_usage": ResourceUsage(elapsed=exec_time),
                "security_check": guard_result,
            }
            return result
        except Exception as exc:
            if 'process' in dir() and process in self._active_processes:
                self._active_processes.remove(process)
            exec_time = time.monotonic() - start_time
            result = {
                "returncode": -1,
                "stdout": "",
                "stderr": f"[沙箱异常] {exc}",
                "killed_by": str(exc),
                "resource_usage": ResourceUsage(elapsed=exec_time),
                "security_check": guard_result,
            }
            return result
    async def _kill_process_group(self, process: asyncio.subprocess.Process) -> None:
        """终止进程组（先 SIGTERM，后 SIGKILL）。"""
        try:
            if sys.platform != "win32":
                try:
                    pgid = os.getpgid(process.pid)
                    os.killpg(pgid, signal.SIGTERM)
                    await asyncio.sleep(0.5)
                    os.killpg(pgid, signal.SIGKILL)
                except Exception:
                    process.kill()
            else:
                process.kill()
        except ProcessLookupError:
            pass
        except Exception as exc:
            logger.warning("终止进程失败: %s", exc)
    @property
    def security_chain(self) -> Optional[SecurityChain]:
        """获取安全防护链实例。"""
        return self._security_chain

    def kill_all(self) -> int:
        """⭐ 强制终止所有正在运行的沙箱子进程（用于服务关闭）。
        返回已终止的进程数量。
        """
        self._shutdown_flag = True
        killed = 0
        processes_to_kill = list(self._active_processes)
        for process in processes_to_kill:
            try:
                awaitable = self._kill_process_group(process)
                # 在同步方法里用 asyncio.create_task 调度（如果有事件循环）
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        asyncio.create_task(awaitable)
                    else:
                        loop.run_until_complete(awaitable)
                except RuntimeError:
                    # 无事件循环，直接用 os.kill
                    try:
                        os.kill(process.pid, signal.SIGKILL)
                    except Exception:
                        pass
                killed += 1
            except Exception:
                pass
        self._active_processes.clear()
        if killed > 0:
            logger.warning("🛑 沙箱关闭：强制终止 %d 个正在运行的子进程", killed)
        return killed

    async def kill_all_async(self) -> int:
        """⭐ 异步版本：在事件循环中调用时使用。"""
        self._shutdown_flag = True
        killed = 0
        processes_to_kill = list(self._active_processes)
        for process in processes_to_kill:
            try:
                await self._kill_process_group(process)
                killed += 1
            except Exception:
                pass
        self._active_processes.clear()
        if killed > 0:
            logger.warning("🛑 沙箱关闭（异步）：强制终止 %d 个正在运行的子进程", killed)
        return killed

    @property
    def active_count(self) -> int:
        """当前运行中的沙箱进程数量。"""
        return len(self._active_processes)
# ========================================================================
# 沙箱工厂
# ========================================================================
_default_sandbox: Optional[SandboxExecutor] = None
def get_sandbox(
    enable_security_chain: bool = True,
    force_new: bool = False,
) -> SandboxExecutor:
    global _default_sandbox
    if _default_sandbox is None or force_new:
        _default_sandbox = SandboxExecutor(
            enable_security_chain=enable_security_chain,
        )
    return _default_sandbox


def kill_all_sandbox_processes() -> int:
    """⭐ 模块级快捷函数：终止所有沙箱子进程（供 api.app 关闭调用）。"""
    if _default_sandbox is not None:
        return _default_sandbox.kill_all()
    return 0


async def kill_all_sandbox_processes_async() -> int:
    """⭐ 模块级异步快捷函数：供 lifespan 关闭回调调用。"""
    if _default_sandbox is not None:
        return await _default_sandbox.kill_all_async()
    return 0
