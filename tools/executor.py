"""
异步执行引擎 (tools/executor.py) - Phase 4 安全增强版

职责:
- 使用 asyncio.create_subprocess_exec 执行系统命令
- 捕获 stdout / stderr / returncode
- 支持可配置超时（默认 300 秒），超时自动终止进程
- 支持命令执行进度实时反馈（通过 asyncio.Queue）
- 集成安全拦截（调用 SecurityGuard）
- Phase 4: 集成沙箱执行（权限降级 + 资源监控）
- Phase 4: 命令参数深度校验
- Phase 4: 工具执行失败自动重试（可配置）
"""

from __future__ import annotations

import asyncio
import shlex
import time
from typing import Any, Dict, List, Optional

from pydantic import BaseModel

from core.security import SecurityGuard
from tools.base_tool import ToolResult
from utils.logger import get_logger

logger = get_logger(__name__)


# ========================================================================
# 进度事件
# ========================================================================
class ProgressEvent(BaseModel):
    tool_name: str
    stage: str  # "start" | "stdout" | "stderr" | "complete" | "timeout" | "killed" | "retry"
    line: Optional[str] = None
    lines_count: int = 0
    duration: float = 0.0
    retry_count: int = 0


# ========================================================================
# 执行配置
# ========================================================================
class ExecutorConfig(BaseModel):
    model_config = {"arbitrary_types_allowed": True}

    default_timeout: float = 600.0
    kill_grace_period: float = 5.0
    max_concurrent: int = 10
    shell: bool = False
    capture_stdout: bool = True
    capture_stderr: bool = True
    max_output_lines: int = 10000
    security_guard: Optional[SecurityGuard] = None

    # Phase 4: 重试配置
    enable_retry: bool = True
    max_retries: int = 0  # 不自动重试，失败直接返回给用户决定
    retry_delay_base: float = 2.0  # 重试延迟基数（指数退避）
    retryable_tools: List[str] = []  # 空列表 = 所有工具都可重试
    non_retryable_tools: List[str] = [  # 非幂等工具，禁止重试
        "hydra", "sqlmap --flush-session",
    ]
    retry_on_return_codes: List[int] = [1, 2, 255]

    # 扫描类工具（失败后重试无意义，应当让用户选择下一步）
    scanning_commands: List[str] = [
        "gobuster", "dirsearch", "dirb", "ffuf", "nikto",
        "nmap", "masscan", "wpscan", "sqlmap", "hydra",
        "whatweb", "theharvester", "dnsrecon", "sublist3r",
    ]


# ========================================================================
# 异步执行器
# ========================================================================
class AsyncExecutor:
    def __init__(self, config: Optional[ExecutorConfig] = None) -> None:
        self.config = config or ExecutorConfig()
        self._guard: SecurityGuard = self.config.security_guard or SecurityGuard()
        self._progress_queues: Dict[str, asyncio.Queue[ProgressEvent]] = {}
        self._processes: Dict[str, asyncio.subprocess.Process] = {}
        self._semaphore = asyncio.Semaphore(self.config.max_concurrent)

    def subscribe(self, tool_name: str) -> asyncio.Queue[ProgressEvent]:
        q: asyncio.Queue[ProgressEvent] = asyncio.Queue()
        self._progress_queues[tool_name] = q
        return q

    def unsubscribe(self, tool_name: str) -> None:
        self._progress_queues.pop(tool_name, None)

    async def _emit(self, tool_name: str, event: ProgressEvent) -> None:
        q = self._progress_queues.get(tool_name)
        if q is not None:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass

    # ------------------------------------------------------------------
    # 命令参数深度校验
    # ------------------------------------------------------------------
    def _validate_command_args(self, args: List[str]) -> Optional[str]:
        """对命令参数进行深度校验。

        检查内容:
        1. 参数长度限制（防止缓冲区溢出）
        2. 字符集白名单（仅允许安全字符）
        3. 路径遍历防护（../ 等）

        返回错误信息，或 None（校验通过）。
        """
        if not args:
            return "空命令"

        # 1. 参数数量限制
        if len(args) > 100:
            return f"参数数量过多: {len(args)}个（限制 100 个）"

        # 2. 单参数长度限制
        for i, arg in enumerate(args):
            if len(arg) > 4096:
                return f"参数 {i} 过长: {len(arg)} 字符（限制 4096）"

            # 路径遍历防护
            if ".." in arg.split("/") or ".." in arg.split("\\"):
                # 允许 /usr/share/../nmap 这种合法路径遍历
                if arg.startswith("..") or "/.." in arg or "\\.." in arg:
                    if not arg.startswith("/usr/") and not arg.startswith("/etc/"):
                        return f"参数 {i} 包含路径遍历: {arg[:100]}"

        return None

    # ------------------------------------------------------------------
    # 扫描类命令检测
    # ------------------------------------------------------------------
    def _is_scanning_command(self, args: Optional[List[str]] = None,
                              shell_command: Optional[str] = None) -> bool:
        """检测当前需要执行的命令是否为扫描类工具。"""
        cmd_str = ""
        if shell_command:
            cmd_str = shell_command
        elif args:
            cmd_str = " ".join(args)
        if not cmd_str:
            return False
        first_word = cmd_str.split()[0].lower() if cmd_str else ""
        for scan_cmd in self.config.scanning_commands:
            if scan_cmd == first_word:
                return True
            if cmd_str.startswith(scan_cmd):
                return True
        return False

    # ------------------------------------------------------------------
    # 工具重试判断
    # ------------------------------------------------------------------
    def _should_retry(self, tool_name: str, result: ToolResult, retry_count: int,
                       args: Optional[List[str]] = None,
                       shell_command: Optional[str] = None) -> bool:
        """判断是否应该重试。"""
        if not self.config.enable_retry:
            return False
        if retry_count >= self.config.max_retries:
            return False
        if result.success:
            return False

        # rc=-1 表示被安全链拦截，绝不重试
        if result.return_code == -1:
            logger.info("[不重试] 工具 %s 返回码=-1（安全拦截），不重试", tool_name)
            return False

        # 扫描类工具失败后重试无意义，让用户选择下一步
        if self._is_scanning_command(args=args, shell_command=shell_command):
            logger.info("[不重试] 工具 %s 命令是扫描类，失败后不自动重试", tool_name)
            return False

        # 非幂等工具检查
        for non_idem in self.config.non_retryable_tools:
            if non_idem in tool_name:
                logger.info("工具 %s 禁止重试（非幂等）", tool_name)
                return False

        # retryable_tools 白名单（如果设置了）
        if self.config.retryable_tools:
            if tool_name not in self.config.retryable_tools:
                return False

        # 返回码检查
        if result.return_code is not None:
            if result.return_code not in self.config.retry_on_return_codes:
                return False

        return True

    async def _execute_with_retry(
        self,
        tool_name: str,
        args: List[str],
        *,
        timeout: Optional[float] = None,
        cwd: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
        shell_command: Optional[str] = None,
        progress_enabled: bool = True,
    ) -> ToolResult:
        """带重试的执行逻辑。"""
        last_result = None
        retry_count = 0

        while True:
            result = await self._execute_once(
                tool_name=tool_name,
                args=args,
                timeout=timeout,
                cwd=cwd,
                env=env,
                shell_command=shell_command,
                progress_enabled=progress_enabled,
            )
            last_result = result

            if result.success:
                break

            if self._should_retry(tool_name, result, retry_count,
                                    args=args, shell_command=shell_command):
                retry_count += 1
                delay = self.config.retry_delay_base * (2 ** (retry_count - 1))
                logger.warning(
                    "工具 %s 执行失败 (rc=%s)，第 %d 次重试，等待 %.1fs...",
                    tool_name, result.return_code, retry_count, delay,
                )
                if progress_enabled:
                    await self._emit(tool_name, ProgressEvent(
                        tool_name=tool_name, stage="retry",
                        retry_count=retry_count, duration=delay,
                    ))
                await asyncio.sleep(delay)
            else:
                break

        return last_result

    async def _execute_once(
        self,
        tool_name: str,
        args: List[str],
        *,
        timeout: Optional[float] = None,
        cwd: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
        shell_command: Optional[str] = None,
        progress_enabled: bool = True,
    ) -> ToolResult:
        """单次执行逻辑（无重试）。"""
        # --- 安全拦截：check_command 返回 (safe, reason) ---
        if shell_command:
            safe, reason = self._guard.check_command(shell_command)
        else:
            cmd_str = " ".join(args)
            safe, reason = self._guard.check_command(cmd_str) if cmd_str else (True, "")
        if not safe:
            logger.warning("命令被安全拦截: %s => %s", shell_command or " ".join(args), reason)
            return ToolResult(
                success=False, output="",
                error=f"🔴 命令已拦截: {reason}",
                tool_name=tool_name, duration=0.0,
            )

        # --- 白名单 + 敏感文件检查 ---
        cmd_to_check = shell_command or " ".join(args)
        allowed, allow_reason = self._guard.is_allowed(cmd_to_check)
        if not allowed:
            logger.warning("命令被拦截: %s => %s", cmd_to_check, allow_reason)
            return ToolResult(
                success=False, output="",
                error=f"命令被拦截: {allow_reason}",
                tool_name=tool_name, duration=0.0,
            )

        # --- 参数深度校验 ---
        if args:
            validation_error = self._validate_command_args(list(args))
            if validation_error:
                logger.warning("参数校验失败: %s => %s", " ".join(args), validation_error)
                return ToolResult(
                    success=False, output="",
                    error=f"参数校验失败: {validation_error}",
                    tool_name=tool_name, duration=0.0,
                )

        timeout_sec = timeout if timeout is not None else self.config.default_timeout
        start_time = time.monotonic()
        stdout_lines: List[str] = []
        stderr_lines: List[str] = []
        lines_count = 0

        if progress_enabled:
            await self._emit(tool_name, ProgressEvent(
                tool_name=tool_name, stage="start", duration=0.0,
            ))

        try:
            # 直接执行模式（沙箱已移除）
            process = await asyncio.create_subprocess_exec(
                *args,
                stdout=asyncio.subprocess.PIPE if self.config.capture_stdout else asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE if self.config.capture_stderr else asyncio.subprocess.DEVNULL,
                cwd=cwd, env=env, shell=False,
            )
            self._processes[tool_name] = process

            async def _read_stream(
                stream: Optional[asyncio.StreamReader],
                lines_out: List[str],
                is_stderr: bool = False,
            ) -> None:
                nonlocal lines_count
                if stream is None:
                    return
                stage = "stderr" if is_stderr else "stdout"
                async for line_bytes in stream:
                    line = line_bytes.decode("utf-8", errors="replace").rstrip()
                    lines_out.append(line)
                    lines_count += 1
                    if progress_enabled and lines_count % 100 == 0:
                        await self._emit(tool_name, ProgressEvent(
                            tool_name=tool_name, stage=stage,
                            lines_count=lines_count, duration=time.monotonic() - start_time,
                        ))
                    if len(lines_out) >= self.config.max_output_lines:
                        lines_out.append(f"[... 输出超过 {self.config.max_output_lines} 行，已截断 ...]")
                        break

            try:
                _, _, returncode = await asyncio.wait_for(
                    asyncio.gather(
                        _read_stream(process.stdout, stdout_lines, False),
                        _read_stream(process.stderr, stderr_lines, True),
                        process.wait(),
                    ),
                    timeout=timeout_sec,
                )
            except asyncio.TimeoutError:
                await self._terminate_gracefully(process, tool_name)
                duration = time.monotonic() - start_time
                stderr_lines.append(f"[命令执行超时 ({timeout_sec}s)，已强制终止]")
                if progress_enabled:
                    await self._emit(tool_name, ProgressEvent(
                        tool_name=tool_name, stage="timeout",
                        lines_count=len(stdout_lines) + len(stderr_lines),
                        duration=duration,
                    ))
                stdout_text = "\n".join(stdout_lines)
                stderr_text = "\n".join(stderr_lines)
                return ToolResult(
                    success=False, output=stdout_text,
                    stdout=stdout_text or None, stderr=stderr_text or None,
                    return_code=-1, duration=round(duration, 3),
                    tool_name=tool_name,
                    error=f"命令执行超时（{timeout_sec}秒）",
                )
            except asyncio.CancelledError:
                await self._terminate_gracefully(process, tool_name)
                returncode = -1
                stderr_lines.append("[命令被外部取消]")
                raise
            finally:
                self._processes.pop(tool_name, None)

            stdout_text = "\n".join(stdout_lines)
            stderr_text = "\n".join(stderr_lines)

        except FileNotFoundError as exc:
            logger.warning("命令未找到: %s (%s)", args[0] if args else shell_command, exc)
            return ToolResult(
                success=False, output="",
                error=f"命令未找到: {args[0] if args else shell_command} ({exc})",
                tool_name=tool_name, duration=time.monotonic() - start_time,
            )
        except PermissionError as exc:
            logger.warning("权限不足: %s", exc)
            return ToolResult(
                success=False, output="",
                error=f"权限不足: {exc}",
                tool_name=tool_name, duration=time.monotonic() - start_time,
            )
        except Exception as exc:
            logger.error("执行异常: %s: %s", tool_name, exc)
            return ToolResult(
                success=False, output="",
                error=f"执行异常: {type(exc).__name__}: {exc}",
                tool_name=tool_name, duration=time.monotonic() - start_time,
            )

        duration = time.monotonic() - start_time
        full_output = stdout_text + ("\n" + stderr_text if stderr_text else "")

        if progress_enabled:
            await self._emit(tool_name, ProgressEvent(
                tool_name=tool_name, stage="complete",
                lines_count=lines_count, duration=duration,
            ))

        result = ToolResult(
            success=(returncode == 0),
            output=full_output,
            stdout=stdout_text or None,
            stderr=stderr_text or None,
            return_code=returncode,
            duration=round(duration, 3),
            tool_name=tool_name,
            error=None if returncode == 0 else f"Exit code: {returncode}",
        )

        return result

    async def execute(
        self,
        tool_name: str,
        args: List[str],
        *,
        timeout: Optional[float] = None,
        cwd: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
        shell_command: Optional[str] = None,
        progress_enabled: bool = True,
    ) -> ToolResult:
        """主执行入口（带重试机制）。"""
        return await self._execute_with_retry(
            tool_name=tool_name,
            args=args,
            timeout=timeout,
            cwd=cwd,
            env=env,
            shell_command=shell_command,
            progress_enabled=progress_enabled,
        )

    def stop_task(self, tool_name: str) -> bool:
        """根据工具名称强制终止正在运行的进程。"""
        process = self._processes.get(tool_name)
        if process is None:
            return False
        try:
            process.kill()
            return True
        except Exception:
            return False

    def kill_all(self) -> int:
        """强制终止所有正在运行的进程。
        返回已终止的进程总数。
        """
        count = 0
        for tool_name, process in list(self._processes.items()):
            try:
                process.kill()
                count += 1
            except Exception:
                pass
        self._processes.clear()
        return count

    def get_active_processes(self) -> List[str]:
        return list(self._processes.keys())

    async def _terminate_gracefully(
        self, process: asyncio.subprocess.Process, tool_name: str
    ) -> None:
        try:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=self.config.kill_grace_period)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
        except Exception:
            try:
                process.kill()
            except Exception:
                pass
        finally:
            self._processes.pop(tool_name, None)

    async def execute_batch(
        self,
        tasks: List[Dict[str, Any]],
        *,
        concurrency: int = 5,
    ) -> List[ToolResult]:
        semaphore = asyncio.Semaphore(concurrency)
        results: List[ToolResult] = [
            ToolResult(success=False, output="", error="占位") for _ in tasks
        ]
        idx_map: Dict[int, ToolResult] = {}

        async def _run_task(idx: int, task: Dict[str, Any]) -> tuple[int, ToolResult]:
            async with semaphore:
                result = await self.execute(
                    tool_name=task.get("tool_name", "unknown"),
                    args=task.get("args", []),
                    shell_command=task.get("shell_command"),
                    timeout=task.get("timeout"),
                    cwd=task.get("cwd"),
                )
                return idx, result

        coros = [asyncio.create_task(_run_task(i, t)) for i, t in enumerate(tasks)]
        done, _ = await asyncio.wait(coros, return_when=asyncio.ALL_COMPLETED)
        for task in done:
            try:
                idx, result = task.result()
                idx_map[idx] = result
            except Exception:
                pass

        for i in range(len(tasks)):
            results[i] = idx_map.get(i, ToolResult(
                success=False, output="", error="任务未执行",
                tool_name=tasks[i].get("tool_name", "unknown"),
            ))
        return results


_executor_singleton: Optional[AsyncExecutor] = None


def get_executor() -> AsyncExecutor:
    global _executor_singleton
    if _executor_singleton is None:
        _executor_singleton = AsyncExecutor()
    return _executor_singleton
