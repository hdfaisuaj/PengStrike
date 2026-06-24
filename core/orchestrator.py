"""
编排引擎 (core/orchestrator.py) - Phase 4 FSM + 角色/技能系统融合版

职责:
- 编排 ReAct 主循环 (思考 -> LLM -> 工具调用 -> 结果 -> 继续...)
- 管理 AutoPilot 开启/关闭/终止条件
- 集成 PentestFSM 状态机: 每个状态内嵌 ReAct 循环
- Phase 4: 角色/技能系统 + Jinja2 动态提示词 + 权限控制
- 超时监控: 每个状态超时自动跳转 failed
- 暂停/恢复: 外部暂停信号中断当前状态, 保存断点
"""

from __future__ import annotations

import asyncio
import re
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional

from jinja2 import Environment, BaseLoader, TemplateNotFound
from rich.console import Console

from config.settings import Settings, get_settings
from core.fsm import PentestFSM
from core.llm_client import LLMClient, SYSTEM_PROMPT
from core.security import SecurityGuard
from core.state_manager import StateManager
from utils.context_manager import ContextManager
from utils.logger import get_logger
from tools.base_tool import ToolResult

# AutoPilot 新引擎 (重构方案)
from core.auto_pilot import AutoPilotEngine

# AutoPilot WebSocket 确认（延迟导入，避免循环依赖）
_get_autopilot_confirmer = None


async def _request_autopilot_confirm(session_id: str, message: str) -> bool:
    """调用 WebSocket 确认机制，延迟导入避免循环依赖。"""
    global _get_autopilot_confirmer
    if _get_autopilot_confirmer is None:
        from api.websocket import request_autopilot_confirmation
        _get_autopilot_confirmer = request_autopilot_confirmation
    return await _get_autopilot_confirmer(session_id, message)

# AutoPilot 命令级确认（延迟导入）
_get_autopilot_cmd_confirmer = None


async def _request_autopilot_command_confirm(
    session_id: str, command: str, reason: str = "", target: str = "", state: str = ""
) -> dict:
    """请求用户确认单条命令，延迟导入避免循环依赖。"""
    global _get_autopilot_cmd_confirmer
    if _get_autopilot_cmd_confirmer is None:
        from api.websocket import request_autopilot_command_confirm
        _get_autopilot_cmd_confirmer = request_autopilot_command_confirm
    return await _get_autopilot_cmd_confirmer(session_id, command, reason, target, state)

logger = get_logger(__name__)

# ========================================================================
# 全局后台任务注册表（用于终止所有运行中的 AutoPilot）
# ========================================================================

# WS 推送函数（由 run_autopilot_session 设置，其他方法使用）
_ws_broadcast_fn = None

async def _ws_broadcast(payload: dict) -> None:
    """全局 WS 广播，供 execute_command / chat_stream 等方法调用。"""
    global _ws_broadcast_fn
    if _ws_broadcast_fn is None:
        try:
            from api.websocket import get_connection_manager
            _ws_broadcast_fn = get_connection_manager().broadcast
        except Exception:
            return
    try:
        await _ws_broadcast_fn(payload)
    except Exception:
        pass

running_autopilot_tasks: Dict[str, asyncio.Task] = {}
_running_orchestrators: Dict[str, "Orchestrator"] = {}  # session_id → Orchestrator 实例
_tool_choice_futures: Dict[str, asyncio.Future] = {}  # 工具选择等待


AUTO_PILOT_STOP_KEYWORDS = [
    "root shell", "ROOT SHELL", "提权成功", "获得 root",
    "遇到瓶颈", "需要人工介入", "人工介入",
    "无法自动绕过", "遇到无法绕过",
    "双因素", "验证码", "CAPTCHA",
]


def _smart_truncate(text: str, max_chars: int = 3000, head: int = 1000, tail: int = 1000) -> str:
    if not text or len(text) <= max_chars:
        return text
    head_text = text[:head]
    tail_text = text[-tail:]
    return f"{head_text}\n[... PengStrike 智能截断：已省略 {len(text) - head - tail} 字符，保留首尾关键信息 ...]\n{tail_text}"


class Orchestrator:
    """PengStrike 编排核心 (Phase 4: FSM + 角色/技能/权限融合)."""

    def __init__(
        self,
        llm_client: Optional[LLMClient] = None,
        state_manager: Optional[StateManager] = None,
        security: Optional[SecurityGuard] = None,
        settings: Optional[Settings] = None,
        console: Optional[Console] = None,
        role_name: str = "web_pentester",
    ) -> None:
        self.settings = settings or get_settings()
        self.console = console or Console()
        self.llm_client = llm_client or LLMClient(self.settings, self.console)
        self.state_manager = state_manager or StateManager()
        self.security = security or SecurityGuard()

        # Phase 3: FSM 状态机
        self.fsm = PentestFSM(
            on_enter_cb=self._on_fsm_enter,
            on_exit_cb=self._on_fsm_exit,
            on_timeout_cb=self._on_fsm_timeout,
            initial_state="initialization",
        )

        # Phase 3: 上下文管理器
        self.context_manager = ContextManager(
            model=self.settings.llm_model,
            max_tokens=self.settings.llm_max_tokens or 8192,
        )

        # Phase 4: 角色/技能系统
        self._role_name: str = role_name
        self._role: Optional[BaseRole] = None
        self._jinja_env = Environment()

        # Phase 5: 插件管理器
        self._plugin_manager: Optional[Any] = None
        try:
            from plugins.manager import PluginManager
            self._plugin_manager = PluginManager()
            logger.info("[Orchestrator] 插件管理器已就绪")
        except Exception as exc:
            logger.debug("[Orchestrator] 插件管理器初始化跳过: %s", exc)

        # 强制角色加载：优先使用指定角色，否则使用默认角色
        role = self._role_registry.get_role(role_name)
        if role is None:
            self.console.print(f"[bold yellow]⚠️ 角色 '{role_name}' 未找到，尝试加载默认角色...[/bold yellow]")
            # 尝试使用第一个可用角色作为兜底
            available_roles = self._role_registry.list_roles()
            if available_roles:
                fallback = available_roles[0]
                role = self._role_registry.get_role(fallback)
                if role:
                    self._role = role
                    self._role_name = fallback
                    self.console.print(f"[bold green]✅ 使用兜底角色: {role.name} - {role.description}[/bold green]")
            if self._role is None:
                raise RuntimeError(
                    f"❌ 角色 '{role_name}' 不存在，且无可用兜底角色。"
                    f"可用角色: {', '.join(available_roles) if available_roles else '无'}"
                )
        else:
            self._role = role
            self._role_name = role_name
            self.console.print(f"[bold green]✅ 角色已加载: {role.name} - {role.description}[/bold green]")

        # AutoPilot 状态
        self.autopilot: bool = False
        self.autopilot_max_steps: int = self.settings.autopilot_max_steps
        self.autopilot_current_step: int = 0
        self.autopilot_paused: bool = False
        self._last_tool_time: float = time.time()  # ★ 最后一次工具执行时间（用于超时通知）
        self.manual_command_queue: asyncio.Queue = asyncio.Queue()  # ★ 手动插入命令队列
        self._found_credentials: list[dict] = []  # ★ 已发现的凭证列表

        # Phase 3: 异步任务跟踪
        self._fsm_timeout_task: Optional[asyncio.Task] = None
        self._db_initialized: bool = False

    # ------------------------------------------------------------------
    # Phase 4: 角色管理
    # ------------------------------------------------------------------
    def switch_role(self, role_name: str) -> bool:
        """切换当前角色。"""
        old_role = self._role.name if self._role else None
        role = self._role_registry.get_role(role_name)
        if role is None:
            self.console.print(f"[bold red]❌ 角色 '{role_name}' 不存在[/bold red]")
            return False

        self._role = role
        self._role_name = role_name
        self.console.print(f"[bold green]🔄 角色已切换: {role.name}[/bold green]")
        logger.info("role_switch: old_role=old_role, new_role=role_name")

        rendered = self._render_system_prompt()
        if rendered:
            self._update_llm_system_prompt(rendered)
        return True

    def get_current_role(self) -> Optional[BaseRole]:
        return self._role

    def list_roles(self) -> List[str]:
        return self._role_registry.list_roles()

    def list_skills(self) -> List[str]:
        return self._skill_registry.list_skills()

    def _render_system_prompt(self) -> Optional[str]:
        if not self._role:
            return None

        try:
            template = self._jinja_env.from_string(self._role.system_prompt_template)
            role_prompt = template.render(
                role=self._role,
                session={
                    "target": self.state_manager.current_target or "",
                    "history": self.state_manager.get_history_summary() or "",
                },
                state={"current_phase": self.fsm.state},
            )
            # 分层合并：核心规则 + 角色人设
            return f"{SYSTEM_PROMPT}\n\n# 角色人设\n{role_prompt}"
        except Exception as exc:
            logger.warning("[Jinja2] 模板渲染失败: %s, 使用原始模板", exc)
            return f"{SYSTEM_PROMPT}\n\n# 角色人设\n{self._role.system_prompt_template}"

    def _update_llm_system_prompt(self, prompt: str) -> None:
        """替换 LLM client 中的 system prompt。"""
        if hasattr(self.llm_client, "system_prompt") and prompt:
            self.llm_client.system_prompt = prompt
            if self.llm_client.messages and self.llm_client.messages[0].get("role") == "system":
                self.llm_client.messages[0]["content"] = prompt
            else:
                self.llm_client.messages.insert(0, {"role": "system", "content": prompt})

    # ------------------------------------------------------------------
    # Phase 4: 工具调用（带权限检查）
    # ------------------------------------------------------------------
    async def call_tool(self, tool_name: str, **kwargs) -> ToolResult:
        """调用工具 — 带角色权限检查。"""
        sid = self.state_manager.session_id

        if self._role and not self._role.can_use_tool(tool_name):
            logger.info("tool_call: tool_name=tool_name, params=kwargs, success=False, session_id=sid")
            return ToolResult(
                success=False,
                error=f"角色 '{self._role.name}' 无权调用工具 '{tool_name}'",
                tool_name=tool_name,
            )

        from tools.registry import get_registry
        registry = get_registry()
        tool = registry.get_tool(tool_name)
        if tool is None:
            logger.info("tool_call: tool_name=tool_name, params=kwargs, success=False, session_id=sid")
            return ToolResult(
                success=False,
                error=f"工具 '{tool_name}' 不存在",
                tool_name=tool_name,
            )

        start_time = time.monotonic()
        result = await tool.execute(**kwargs)
        logger.info("tool_call tool=%s success=%s", tool_name, result.success)
        return result

    # ------------------------------------------------------------------
    # Phase 4: 技能调用（带权限检查）
    # ------------------------------------------------------------------
    async def call_skill(self, skill_name: str, **kwargs) -> ToolResult:
        """调用技能 — 带角色权限检查。"""
        sid = self.state_manager.session_id

        if self._role and not self._role.can_use_skill(skill_name):
            logger.info("skill_call: skill_name=skill_name, params=kwargs, success=False, session_id=sid")
            return ToolResult(
                success=False,
                error=f"角色 '{self._role.name}' 无权使用技能 '{skill_name}'",
                tool_name=skill_name,
            )

        skill = self._skill_registry.get_skill(skill_name)
        if skill is None:
            logger.info("skill_call: skill_name=skill_name, params=kwargs, success=False, session_id=sid")
            return ToolResult(
                success=False,
                error=f"技能 '{skill_name}' 不存在",
                tool_name=skill_name,
            )

        try:
            result = await skill.run(orchestrator=self, **kwargs)
            logger.info("skill_call: skill_name=skill_name, params=kwargs, success=result.success, session_id=sid")
            return result
        except Exception as exc:
            logger.info("skill_call: skill_name=skill_name, params=kwargs, success=False, session_id=sid")
            return ToolResult(
                success=False,
                error=f"技能 '{skill_name}' 执行异常: {exc}",
                tool_name=skill_name,
            )

    # ------------------------------------------------------------------
    # FSM 回调
    # ------------------------------------------------------------------
    def _on_fsm_enter(self) -> None:
        """进入新状态时的回调。"""
        state = self.fsm.state
        logger.info("[FSM] 进入状态: %s", state)
        self.console.print(f"[bold cyan]🌀 [FSM] 进入状态: {state}[/bold cyan]")
        sid = self.state_manager.session_id
        if sid:
            logger.info("audit: state_change (removed)")
            # ★ 同步 status + phase 到数据库（Dashboard 时间线依赖此字段）
            phase_map = {
                "initialization": "初始化",
                "reconnaissance": "信息收集",
                "vuln_scan": "漏洞扫描",
                "exploitation": "漏洞利用",
                "privesc": "权限提升",
                "lateral": "横向移动",
                "collection": "凭证收集",
                "completed": "已完成",
                "failed": "已失败",
                "aborted": "已中止",
                "paused": "已暂停",
            }
            phase_name = phase_map.get(state, state)
            # 同步更新内存缓存（立即生效，无竞态）
            if sid in self.state_manager._sessions:
                self.state_manager._sessions[sid]["current_state"] = state
                self.state_manager._sessions[sid]["phase"] = phase_name
            # 异步持久化到 DB
            import asyncio
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self.state_manager.save_state(sid))
            except RuntimeError:
                pass

    def _on_fsm_exit(self) -> None:
        state = self.fsm.state
        logger.info("[FSM] 离开状态: %s", state)
        sid = self.state_manager.session_id
        if sid:
            logger.info("audit: state_change (removed)")

    def _on_fsm_timeout(self, state_name: str) -> None:
        logger.warning("[FSM] 状态超时: %s", state_name)
        self.console.print(f"[bold red]⏰ [FSM] 状态 '{state_name}' 超时，自动跳转至 failed[/bold red]")
        sid = self.state_manager.session_id
        if sid:
            logger.error("audit: error (removed)")

    # ------------------------------------------------------------------
    # DB 初始化 (懒加载)
    # ------------------------------------------------------------------
    async def _ensure_db(self) -> None:
        if not self._db_initialized:
            from db.database import get_database
            db = get_database()
            await db.init_models()
            self._db_initialized = True

    # ------------------------------------------------------------------
    # Session 管理 (FSM 启动入口)
    # ------------------------------------------------------------------
    async def start_session(self, target: str) -> str:
        """创建新会话并启动 FSM。"""
        await self._ensure_db()
        session_id = await self.state_manager.create_session(target)

        # 审计日志: 会话生命周期
        logger.info("audit: session_lifecycle: created (session_id=%s, target=%s)", session_id, target)

        await self.state_manager.add_execution_step(session_id, {
            "action_type": "state_transition",
            "from_state": "none",
            "to_state": "initialization",
            "llm_content": f"新会话启动, 目标: {target}",
        })

        self.console.print(f"[bold green]✅ 新会话已创建: {session_id} (目标: {target})[/bold green]")
        self.console.print(f"[dim]  FSM 初始状态: {self.fsm.state}[/dim]")

        if self._role:
            self.console.print(f"[dim]  当前角色: {self._role.name}[/dim]")
            rendered = self._render_system_prompt()
            if rendered:
                self._update_llm_system_prompt(rendered)
            logger.info("role_switch: old_role=None, new_role=self._role.name, session_id=session_id")

        return session_id

    async def resume_session(self, session_id: str) -> bool:
        """从 DB 恢复已有会话。"""
        await self._ensure_db()
        status = await self.state_manager.load_session(session_id)
        if status is None:
            self.console.print(f"[bold red]❌ 会话不存在: {session_id}[/bold red]")
            return False

        logger.info("audit: session_lifecycle: resumed (session_id=%s)", session_id)

        # 恢复 FSM 状态
        if status in PentestFSM.PENTEST_STATES:
            self.fsm.jump_to(status)
        else:
            self.fsm.jump_to("reconnaissance")

        is_paused = self.state_manager.get("is_paused", False, session_id)
        if is_paused:
            self.console.print(f"[bold yellow]⏸️  会话 {session_id} 处于暂停状态，已恢复至 {status}[/bold yellow]")
        else:
            self.console.print(f"[bold green]✅ 会话 {session_id} 已恢复 (状态: {status})[/bold green]")

        return True

    # ------------------------------------------------------------------
    # 对外控制接口
    # ------------------------------------------------------------------
    def toggle_autopilot(self, mode: bool) -> None:
        self.autopilot = mode
        self.autopilot_current_step = 0
        self._last_tool_time = time.time()  # ★ 重置超时计时器
        self.autopilot_paused = False
        if mode:
            self.console.print("[bold green]🤖 AutoPilot 已开启：我将自动执行非高危命令并形成 ReAct 闭环。[/bold green]")
            self.console.print("[dim]   如需中断，按 Ctrl+C 将暂停 AutoPilot 并切回手动模式。[/dim]\n")
        else:
            self.console.print("[bold yellow]🛑 AutoPilot 已关闭，回到手动确认模式。[/bold yellow]\n")
        sid = self.state_manager.session_id
        if sid:
            logger.info("audit: state_change (removed)")

    def print_status(self) -> None:
        self.state_manager.print_status(
            console=self.console,
            autopilot=self.autopilot,
            history_len=len(self.llm_client.messages),
        )
        self.console.print(f"[dim]  {self.fsm.print_state()}[/dim]")
        if self._role:
            self.console.print(f"[dim]  当前角色: [bold]{self._role.name}[/bold] — {self._role.description}[/dim]")

    def clear_context(self) -> None:
        self.llm_client.clear()
        self.state_manager.reset()
        self.autopilot = False
        self.autopilot_current_step = 0
        self.autopilot_paused = False
        self.console.print("[dim]🔄 上下文已清空[/dim]\n")

    def fsm_pause(self) -> None:
        """外部暂停信号。"""
        self.fsm.on_pause()
        self.fsm.pause()
        self.autopilot = False
        self.console.print(f"[bold yellow]⏸️  FSM 已暂停 (来自: {self.fsm.paused_state})[/bold yellow]")
        sid = self.state_manager.session_id
        if sid:
            logger.info("audit: session_lifecycle: paused (session_id=%s)", sid)

    def fsm_resume(self) -> None:
        if self.fsm.state == "paused":
            self.fsm.resume()
            self.fsm.on_resume()
            self.console.print(f"[bold green]▶️  FSM 已恢复 (当前状态: {self.fsm.state})[/bold green]")
            sid = self.state_manager.session_id
            if sid:
                logger.info("audit: session_lifecycle: resumed (session_id=%s)", sid)

    def fsm_abort(self) -> None:
        self.fsm.abort()
        self.autopilot = False
        self.console.print("[bold red]⛔ FSM 已中止[/bold red]")
        sid = self.state_manager.session_id
        if sid:
            logger.info("audit: session_lifecycle: aborted (session_id=%s)", sid)

    # ------------------------------------------------------------------
    # 命令执行 (安全拦截 + 超时 + Ctrl+C)
    # ------------------------------------------------------------------
    async def execute_command(self, command: str, force: bool = False) -> str:
        """在 Kali 本地执行命令（异步版：用 asyncio.to_thread 避免阻塞事件循环）。
        
        Args:
            command: 要执行的命令
            force: 是否强制跳过安全检查（用户确认执行时跳过）
        """
        start_time = time.monotonic()
        sid = self.state_manager.session_id

        # 安全检查：用户强制执行时跳过
        if not force:
            dangerous, reason = self.security.is_dangerous(command)
            if dangerous:
                logger.warning("[DANGEROUS] 危险命令拦截: %s (%s)", command, reason)
                self.console.print(f"[bold red]🚫 危险命令已拦截:[/bold red] {command} ({reason})")
                logger.warning("command_blocked: command=command, reason=reason, session_id=sid")
                return "Permission denied: Dangerous command blocked. Please request manual approval."

            # 白名单 + 敏感文件检查
            allowed, allow_reason = self.security.is_allowed(command)
            if not allowed:
                self.console.print(f"[bold red]🚫 命令被拦截: {allow_reason}[/bold red]")
                logger.warning("command_blocked: command=command, reason=allow_reason, session_id=sid")
                return f"Permission denied: {allow_reason}"

        logger.info("[EXEC] %s", command)
        self.console.print(f"[bold magenta]🛠️ [AI 正在执行命令]:[/bold magenta] {command}")

        cmd_lower = command.lower()
        if ("nmap -p-" in cmd_lower or "gobuster dir" in cmd_lower or "dirb " in cmd_lower):
            self.console.print("[dim]⏱️  此命令可能需要数分钟，请耐心等待（如需中断按 Ctrl+C）...[/dim]")

        try:
            # ★ 异步：扔到线程池执行，事件循环不阻塞
            result = await asyncio.wait_for(
                asyncio.to_thread(
                    subprocess.run, command,
                    shell=True, capture_output=True,
                    text=True, timeout=self.settings.command_timeout,
                ),
                timeout=self.settings.command_timeout + 10,
            )
            raw_output = (result.stdout or "") + (result.stderr or "")
            output = raw_output.strip() or "(no output)"
            logger.info("[RESULT rc=%d len=%d]", result.returncode, len(output))

            lines = output.splitlines()
            if len(lines) > 30:
                self.console.print("[dim]  ── 前 30 行预览 ──[/dim]")
                for line in lines[:30]:
                    self.console.print(f"[dim]  | {line}[/dim]")
                self.console.print(f"[dim]  └─ 还有 {len(lines) - 30} 行 ...[/dim]")

            logger.info("audit: command_exec (removed)")
            return _smart_truncate(output)

        except asyncio.TimeoutError:
            self.autopilot_paused = True
            self.autopilot = False
            logger.error("[TIMEOUT] 命令执行超时 (%ss): %s", self.settings.command_timeout, command)
            self.console.print(f"\n[bold red]⏰ 命令执行超时 (超过 {self.settings.command_timeout} 秒)，已强制终止。[/bold red]")
            self.console.print(f"[dim]  建议更换更快速的命令。[/dim]")
            return f"[命令执行超时 (超过 {self.settings.command_timeout} 秒)，已强制终止。建议更换更快速的命令，或分步执行。]"

        except subprocess.TimeoutExpired:
            self.autopilot_paused = True
            self.autopilot = False
            logger.error("[TIMEOUT] 命令执行超时 (%ss): %s", self.settings.command_timeout, command)
            self.console.print(f"\n[bold red]⏰ 命令执行超时 (超过 {self.settings.command_timeout} 秒)，已强制终止。[/bold red]")
            self.console.print(f"[dim]  建议更换更快速的命令。[/dim]")
            return f"[命令执行超时 (超过 {self.settings.command_timeout} 秒)，已强制终止。建议更换更快速的命令，或分步执行。]"

        except KeyboardInterrupt:
            self.autopilot_paused = True
            self.autopilot = False
            logger.error("[Ctrl+C] 用户手动中断了命令: %s", command)
            self.console.print(f"\n[bold yellow]✋ 用户中断 (Ctrl+C): 命令已终止[/bold yellow]")
            return "[用户手动中断了当前命令 (Ctrl+C)。当前 AutoPilot 已暂停，请等待用户下一步指示。]"

        except Exception as exc:
            logger.exception("[ERROR] 命令执行异常: %s", command)
            self.console.print(f"[bold red]❌ 命令执行异常: {exc}[/bold red]")
            return f"ERROR: {str(exc)}"

    # ------------------------------------------------------------------
    # AutoPilot 终止判定
    # ------------------------------------------------------------------
    def _should_stop_autopilot(self, text: str, has_tool_calls: bool) -> bool:
        if has_tool_calls:
            return False

        text_lower = (text or "").lower()
        for kw in AUTO_PILOT_STOP_KEYWORDS:
            if kw.lower() in text_lower:
                self.console.print(f"[bold yellow]🛑 [AutoPilot] 检测到终止关键词 '{kw}'，退出自动巡航。[/bold yellow]")
                return True

        if self.autopilot_current_step >= self.autopilot_max_steps:
            self.console.print(f"[bold yellow]🛑 [AutoPilot] 已执行 {self.autopilot_max_steps} 步，自动停止。[/bold yellow]")
            return True

        return False

    # ------------------------------------------------------------------
    # 工具调用派发
    # ------------------------------------------------------------------
    async def _handle_tool_calls(self, tool_call_dicts: list[dict]) -> bool:
        """执行工具调用。返回 True 表示继续循环，False 表示应中止循环（用户取消）。"""
        for tc in tool_call_dicts:
            # 中止检查：如果 FSM 已中止，不再执行新的工具调用
            if self.fsm.is_aborted():
                logger.warning("[AutoPilot] FSM 已中止，跳过工具调用: %s", tc["function"]["name"])
                break

            fn_name = tc["function"]["name"]
            args_json = tc["function"].get("arguments_json", {})
            # ★ arguments_json 已移除（见 llm_client.py），从 arguments 字符串反序列化
            if not args_json:
                raw_args = tc["function"].get("arguments", "{}")
                if isinstance(raw_args, str) and raw_args.strip():
                    try:
                        import json
                        args_json = json.loads(raw_args)
                    except Exception:
                        args_json = {}
                elif isinstance(raw_args, dict):
                    args_json = raw_args
            command = ""

            if fn_name == "execute_kali_command":
                command_val = args_json.get("command", tc["function"]["arguments"])
                command = str(command_val)

                # ★ AutoPilot 模式：命令直接执行（简化确认），阶段切换由外层确认
                user_confirmed_execute = False
                if self.autopilot and self.state_manager.session_id:
                    sid = self.state_manager.session_id

                    # 推送"AI准备执行"到实时日志
                    await _ws_broadcast({
                        "type": "autopilot_command_pending",
                        "session_id": sid,
                        "command": command,
                        "state": self.fsm.state,
                        "timestamp": time.time(),
                    })

                    # ★ 简化：不再请求每条命令确认，用户可通过"停止"按钮中止
                    user_confirmed_execute = True  # 用户确认执行，跳过安全检查

                    # ★ 工具替代选择：AI 选了慢工具时，询问用户是否用更快的替代
                    cmd_lower_check = command.lower().strip()
                    tool_alternatives = {
                        "nikto": "wapiti",
                        "gobuster": "dirsearch",
                        "dirb": "dirsearch",
                        "skipfish": "wapiti",
                        "wpscan": "wapiti",
                        "whatweb": "curl -I",
                    }
                    chosen_tool = cmd_lower_check.split()[0] if cmd_lower_check.split() else ""
                    better_tool = tool_alternatives.get(chosen_tool, "")
                    if better_tool:
                        # 广播工具选择请求
                        choice_future = asyncio.get_running_loop().create_future()
                        _tool_choice_futures[sid] = choice_future
                        await _ws_broadcast({
                            "type": "autopilot_tool_choice",
                            "session_id": sid,
                            "original_command": command,
                            "chosen_tool": chosen_tool,
                            "suggested_tool": better_tool,
                            "reason": f"AI 选择了 {chosen_tool}，但 {better_tool} 更快，是否更换？",
                            "timestamp": time.time(),
                        })
                        try:
                            choice = await asyncio.wait_for(choice_future, timeout=15.0)
                            if choice and choice.get("use_alternative"):
                                # 替换命令中的工具名
                                new_first = better_tool
                                parts = command.split(" ", 1)
                                if len(parts) > 1:
                                    command = f"{new_first} {parts[1]}"
                                else:
                                    command = new_first
                                await _ws_broadcast({
                                    "type": "autopilot_notification",
                                    "session_id": sid,
                                    "level": "info",
                                    "title": "工具已替换",
                                    "message": f"已将 {chosen_tool} 替换为 {better_tool}",
                                    "timestamp": time.time(),
                                })
                        except asyncio.TimeoutError:
                            pass  # 超时用原命令
                        finally:
                            _tool_choice_futures.pop(sid, None)

                # 执行命令（确认后或非 AutoPilot 模式直接执行）
                # ★ 用户确认执行时 force=True，跳过安全检查
                result = await self.execute_command(command, force=user_confirmed_execute)

                # ★ 更新最后工具执行时间
                self._last_tool_time = time.time()

                # ★ 记录执行步骤（同步等待，确保保存到 DB）
                if self.state_manager.session_id:
                    is_success = not (result.startswith("Permission denied") or result.startswith("ERROR:"))
                    await self.state_manager.add_execution_step(
                        self.state_manager.session_id,
                        {
                            "action_type": "tool_execution",
                            "tool_name": "execute_kali_command",
                            "tool_args": {"command": command},
                            "tool_success": is_success,
                            "tool_output_summary": result[:500] if result else "",
                            "tool_duration": 0,
                            "from_state": self.fsm.state if hasattr(self.fsm, 'state') else "",
                            "to_state": self.fsm.state if hasattr(self.fsm, 'state') else "",
                        }
                    )

                # ★ AutoPilot 模式：推送命令执行结果到实时日志
                if self.autopilot and self.state_manager.session_id:
                    asyncio.ensure_future(_ws_broadcast({
                        "type": "autopilot_exec_result",
                        "session_id": self.state_manager.session_id,
                        "command": command,
                        "result": (result[:2000] + "...") if len(result) > 2000 else result,
                        "state": self.fsm.state,
                        "timestamp": time.time(),
                    }))

                    # ★ 失败即通知：命令执行失败时推送给前端
                    if not is_success:
                        asyncio.ensure_future(_ws_broadcast({
                            "type": "autopilot_notification",
                            "session_id": self.state_manager.session_id,
                            "level": "error",
                            "title": "命令执行失败",
                            "message": f"命令执行失败: {command[:100]}{'...' if len(command) > 100 else ''}",
                            "detail": result[:300],
                            "timestamp": time.time(),
                        }))

                    # ★ 交互式工具检测：msfconsole/ftp/nc 等需要人工干预
                    interactive_tools = ["msfconsole", "msfvenom", "ftp", "sftp", "telnet", "nc -lv", "ncat -lv", "socat"]
                    cmd_lower = command.lower().strip()
                    is_interactive = any(cmd_lower.startswith(t) for t in interactive_tools)
                    if is_interactive:
                        asyncio.ensure_future(_ws_broadcast({
                            "type": "autopilot_notification",
                            "session_id": self.state_manager.session_id,
                            "level": "warning",
                            "title": "交互式工具已启动",
                            "message": f"需要手动操作: {command[:100]}",
                            "detail": "此工具需要人工交互，请查看 Kali 终端并手动操作。完成后回复 '继续' 让 AI 继续。",
                            "timestamp": time.time(),
                        }))
            else:
                result = f"ERROR: Unknown tool {fn_name}"

            # ★ 自动查 CVE：扫描命令结果中的 CVE 编号
            if result and self.state_manager.session_id:
                cve_ids = re.findall(r'CVE-\d{4}-\d{4,}', result)
                if cve_ids:
                    sid = self.state_manager.session_id
                    for cve_id in cve_ids[:3]:
                        try:
                            cve_result = await self.execute_command(f"searchsploit --cve {cve_id} 2>/dev/null | head -10", force=True)
                            if cve_result and "Exploit Title" not in cve_result:
                                continue
                            asyncio.ensure_future(_ws_broadcast({
                                "type": "autopilot_notification",
                                "session_id": sid,
                                "level": "info",
                                "title": f"发现 {cve_id}",
                                "message": cve_result[:300] if cve_result else f"发现 {cve_id}，详情请查看报告",
                                "timestamp": time.time(),
                            }))
                            result += f"\n\n[{cve_id} 详情]\n{cve_result[:800]}"
                        except Exception:
                            pass

                # ★ 凭证自动检测：扫描命令结果中的密码/Token/密钥
            if result and hasattr(self, '_found_credentials'):
                found = set()
                # 过滤关键词：排除工具输出中的常见误报
                skip_values = {'yes','no','true','false','on','off','none','null','undefined',
                               'password','pass','pwd','secret','PASSWORD]','PASSWORD'}
                # SSH 私钥
                if 'BEGIN' in result and 'PRIVATE KEY' in result:
                    found.add(("SSH Private Key", "SSH 私钥泄露"))
                # Hydra/Medusa 破解结果
                hydra_match = re.search(r'login:\s*(\S+)\s*password:\s*(\S+)', result, re.IGNORECASE)
                if hydra_match:
                    found.add((f"Credential: {hydra_match.group(1)}:{hydra_match.group(2)}", "暴力破解凭证"))
                # 密码字段（只在工具输出 < 5000 字符时才检测，避免大文件误报）
                if len(result) < 5000:
                    for pat in [r'password[=:\s]+(\S+)', r'pass[=:\s]+(\S+)', r'pwd[=:\s]+(\S+)']:
                        for m in re.finditer(pat, result, re.IGNORECASE):
                            val = m.group(1).strip().strip('"\'').strip(']')
                            if val and len(val) >= 4 and val.lower() not in skip_values and not val.startswith('{'):
                                found.add((f"Password: {val}", "发现密码"))
                                break
                # API Key
                for m in re.finditer(r'(sk-[a-zA-Z0-9]{20,}|api[_-]key[=:\s]+(\S+)|token[=:\s]+(\S+))', result, re.IGNORECASE):
                    val = m.group(0)
                    if val and len(val) > 8:
                        found.add((f"Key/Token: {val[:50]}", "发现 API Key 或 Token"))
                        break
                for cred_text, cred_type in found:
                    self._found_credentials.append({"type": cred_type, "value": cred_text, "source": command[:100]})
                if found:
                    cred_summary = "\n".join([f"  - {c[0]}" for c in found])
                    asyncio.ensure_future(_ws_broadcast({
                        "type": "autopilot_notification",
                        "session_id": self.state_manager.session_id,
                        "level": "info",
                        "title": f"发现 {len(found)} 个凭证",
                        "message": f"已自动保存凭证:\n{cred_summary}",
                        "timestamp": time.time(),
                    }))

            self.llm_client.append_tool_result(
                tool_call_id=tc["id"] or "",
                name=fn_name,
                result=result,
                command=str(command),
            )

        return True  # 默认继续循环

    # ------------------------------------------------------------------
    # 核心: FSM + ReAct 流式编排
    # ------------------------------------------------------------------
    async def chat_stream(self, user_message: str) -> str:
        self.llm_client.append_user(user_message)

        if self.autopilot_paused:
            self.autopilot_paused = False
            self.autopilot = False
            self.console.print("[bold yellow]🛑 AutoPilot 已暂停（Ctrl+C 中断），回到手动模式。[/bold yellow]")

        if self._role:
            rendered = self._render_system_prompt()
            if rendered:
                self._update_llm_system_prompt(rendered)

        while True:
            # ★ 长时间无进展通知：超过 5 分钟没有新步骤则推送提醒
            if self.autopilot and self.state_manager.session_id and time.time() - self._last_tool_time > 300:
                self._last_tool_time = time.time()  # 重置计时
                asyncio.ensure_future(_ws_broadcast({
                    "type": "autopilot_notification",
                    "session_id": self.state_manager.session_id,
                    "level": "warning",
                    "title": "AutoPilot 长时间无进展",
                    "message": "已超过 5 分钟没有新的命令执行，可能卡住了。请检查是否要继续等待或手动干预。",
                    "timestamp": time.time(),
                }))

            # ★ 检查手动插入的命令队列
            while not self.manual_command_queue.empty():
                try:
                    manual_msg = self.manual_command_queue.get_nowait()
                    logger.info("[手动命令] AutoPilot 处理手动指令: %s", manual_msg[:100])
                    self.llm_client.append_user(manual_msg)
                    # 推送手动命令到前端日志
                    if self.state_manager.session_id:
                        asyncio.ensure_future(_ws_broadcast({
                            "type": "autopilot_manual_command",
                            "session_id": self.state_manager.session_id,
                            "command": manual_msg,
                            "timestamp": time.time(),
                        }))
                    # 不 break，让循环重新进入 LLM 调用处理新消息
                except asyncio.QueueEmpty:
                    break
            if self.autopilot_paused:
                self.autopilot_paused = False
                self.autopilot = False
                return "[AutoPilot 已暂停，等待用户下一步指示]"

            # 中止检查
            if self.fsm.is_aborted():
                logger.warning("[AutoPilot] FSM 已中止，退出 chat_stream 循环")
                return "[AutoPilot 已被终止]"

            # 1) 调用 LLM（同步转异步，不阻塞事件循环）
            try:
                full_content, tool_call_dicts = await asyncio.to_thread(
                    self.llm_client.stream_chat,
                    autopilot=self.autopilot,
                )
            except KeyboardInterrupt:
                logger.error("[Ctrl+C] 用户中断了 LLM API 调用")
                self.console.print(f"\n[bold yellow]✋ 用户中断 (Ctrl+C): 正在停止 AI 推理...[/bold yellow]")
                self.autopilot = False
                self.autopilot_paused = True
                if self.llm_client.messages and self.llm_client.messages[-1].get("role") == "user":
                    self.llm_client.messages.pop()
                return "[用户中断了 LLM 调用 (Ctrl+C)。AutoPilot 已暂停。]"
            except RuntimeError:
                if self.llm_client.messages and self.llm_client.messages[-1].get("role") == "user":
                    self.llm_client.messages.pop()
                raise

            has_tool_calls = len(tool_call_dicts) > 0

            # ★ 每步推 AI 分析文本到前端对话窗口（中间结果也实时显示）
            if full_content and self.state_manager.session_id:
                asyncio.ensure_future(_ws_broadcast({
                    "type": "autopilot_ai_response",
                    "session_id": self.state_manager.session_id,
                    "content": full_content,
                    "state": self.fsm.state,
                    "timestamp": time.time(),
                }))
                # ★ 记录 AI 分析步骤
                if not has_tool_calls:
                    await self.state_manager.add_execution_step(
                        self.state_manager.session_id,
                        {
                            "action_type": "ai_analysis",
                            "llm_content": full_content[:500],
                            "from_state": self.fsm.state if hasattr(self.fsm, 'state') else "",
                            "to_state": self.fsm.state if hasattr(self.fsm, 'state') else "",
                        }
                    )

            # 2) AutoPilot 退出判定
            if self.autopilot and self._should_stop_autopilot(full_content, has_tool_calls):
                self.autopilot = False
                self.llm_client.append_assistant(full_content if full_content else None, None)
                self.state_manager.update_from_reply(full_content)
                if full_content:
                    logger.info("ai_output: ai_output=full_content[:500], session_id=self.state_manager.session_id")
                if full_content and ("ROOT SHELL" in full_content or "提权成功" in full_content):
                    self.fsm.next()
                return full_content

            # 3) 处理工具调用
            if has_tool_calls:
                self.llm_client.append_assistant(
                    full_content if full_content else None,
                    tool_call_dicts,
                )
                should_continue = await self._handle_tool_calls(tool_call_dicts)
                if not should_continue:
                    # 用户中止了 AutoPilot，退出 chat_stream 循环
                    break
                self.state_manager.update_from_reply(full_content)

                if self.autopilot:
                    self.autopilot_current_step += 1
                    self.console.print(
                        f"[bold cyan]🔄 [AutoPilot 第 {self.autopilot_current_step} 步] 正在根据结果继续思考...[/bold cyan]\n"
                    )
                    continue

                try:
                    self.console.print("[bold blue]🤖 AI (分析结果):[/bold blue] ", end="")
                    final_reply, _ = await asyncio.to_thread(
                        self.llm_client.stream_chat,
                        autopilot=self.autopilot,
                        enable_tools=False,
                    )
                    self.llm_client.append_assistant(final_reply if final_reply else None, None)
                    self.state_manager.update_from_reply(final_reply)
                    if final_reply:
                        logger.info("ai_output: ai_output=final_reply[:500], session_id=self.state_manager.session_id")
                    return final_reply
                except Exception as exc:
                    self.console.print(f"[bold red]❌ 二次分析失败: {exc}[/bold red]")
                    logger.exception("二次分析失败: %s", exc)
                    return full_content or "(无输出)"
            else:
                self.llm_client.append_assistant(full_content if full_content else None, None)
                self.state_manager.update_from_reply(full_content)
                if full_content:
                    logger.info("ai_output: ai_output=full_content[:500], session_id=self.state_manager.session_id")
                return full_content

    # ------------------------------------------------------------------
    # 兼容旧接口
    # ------------------------------------------------------------------
    async def chat(self, user_message: str) -> str:
        return await self.chat_stream(user_message)

    # ------------------------------------------------------------------
    # Phase 3: FSM 全自动执行 (异步)
    # ------------------------------------------------------------------
    async def run_autopilot_session(self, target: str, existing_session_id: str = None) -> None:
        """创建或恢复会话并以 FSM 驱动全自动渗透。
        
        Args:
            target: 目标 IP/域名
            existing_session_id: 如果提供，则恢复已有会话而不是新建
        """
        if existing_session_id:
            # 恢复已有会话
            ok = await self.resume_session(existing_session_id)
            if not ok:
                logger.error("[AutoPilot] 恢复会话失败: %s，将创建新会话", existing_session_id)
                session_id = await self.start_session(target)
            else:
                session_id = existing_session_id
                logger.info("[AutoPilot] 恢复已有会话: %s", session_id)
        else:
            session_id = await self.start_session(target)
        
        self.state_manager.session_id = session_id

        # 注册到全局任务表
        current_task = asyncio.current_task()
        if current_task:
            running_autopilot_tasks[session_id] = current_task

        _running_orchestrators[session_id] = self  # ★ 注册到全局字典
        self.toggle_autopilot(True)

        self.console.print(f"\n[bold cyan]🚀 [AutoPilot FSM] 开始全自动渗透: {target}[/bold cyan]")

        # WebSocket 推送辅助函数 + 同步写日志
        async def _push_ws(typ: str, msg: str, **extra) -> None:
            """推送 AutoPilot 进展到前端，并写入后端日志（供实时日志拉取）"""
            # ① 写后端日志（前端 /api/system/logs 会拉到）
            logger.info("[AutoPilot] %s", msg)
            # ② WebSocket 推送（前端可实时收到）
            try:
                from api.websocket import get_connection_manager
                manager = get_connection_manager()
                payload = {
                    "type": "autopilot_progress",
                    "session_id": session_id,
                    "state": self.fsm.state,
                    "message": msg,
                    "timestamp": time.time(),
                }
                payload.update(extra)
                await manager.broadcast(payload)
            except Exception as exc:
                logger.debug("[AutoPilot] WebSocket 推送失败（非致命）: %s", exc)

        await _push_ws("started", f"AutoPilot 启动，目标: {target}")

        try:
            while self.fsm.state not in ("completed", "aborted", "failed"):
                if self.fsm.is_aborted():
                    break

                if self.fsm.state == "paused":
                    await asyncio.sleep(1)
                    continue

                # 超时检查
                timeout_state = self.fsm.check_timeout()
                if timeout_state:
                    self._on_fsm_timeout(timeout_state)
                    await _push_ws("timeout", f"阶段超时: {timeout_state}")
                    break

                # 根据当前状态执行对应的 ReAct 循环
                current_state = self.fsm.state
                await _push_ws("phase_enter", f"进入 {current_state} 阶段", phase=current_state)

                # ★ 新增：请求用户确认是否继续执行当前阶段
                confirm_msg = f"AutoPilot 即将执行【{current_state}】阶段，是否继续？"
                try:
                    user_confirmed = await _request_autopilot_confirm(session_id, confirm_msg)
                except Exception as confirm_exc:
                    logger.warning("[AutoPilot] 确认请求异常（默认中止）: %s", confirm_exc)
                    user_confirmed = False

                if not user_confirmed:
                    await _push_ws("user_abort", f"用户中止了 {current_state} 阶段", phase=current_state)
                    logger.info("[AutoPilot] 用户中止: session_id=%s, state=%s", session_id, current_state)
                    break

                if current_state == "initialization":
                    await self._phase_initialization(target)
                elif current_state == "reconnaissance":
                    await self._phase_reconnaissance(target)
                elif current_state == "vuln_scan":
                    await self._phase_vuln_scan(target)
                elif current_state == "exploitation":
                    await self._phase_exploitation(target)
                elif current_state == "privesc":
                    await self._phase_privesc(target)
                elif current_state == "lateral":
                    await self._phase_lateral(target)
                elif current_state == "collection":
                    await self._phase_collection(target)

                await _push_ws("phase_exit", f"离开 {current_state} 阶段", phase=current_state, next_state=self.fsm.state)

        except asyncio.CancelledError:
            logger.warning("[AutoPilot] 任务被取消: session_id=%s, state=%s", session_id, self.fsm.state)
            await _push_ws("cancelled", f"AutoPilot 被取消，状态: {self.fsm.state}")
            self.fsm.abort()
            self.autopilot = False
        except Exception as exc:
            logger.exception("[AutoPilot] 异常: %s", exc)
            await _push_ws("error", f"AutoPilot 异常: {exc}")
        finally:
            running_autopilot_tasks.pop(session_id, None)
            _running_orchestrators.pop(session_id, None)  # ★ 取消注册
            final_msg = f"AutoPilot 结束，最终状态: {self.fsm.state}"
            # ★ 如果有发现的凭证，追加到结束消息
            if self._found_credentials:
                cred_file = f"credentials_{session_id[:8]}.txt"
                try:
                    with open(cred_file, "w") as f:
                        for c in self._found_credentials:
                            f.write(f"[{c['type']}] {c['value']} (来源: {c['source']})\n")
                    final_msg += f" | 发现 {len(self._found_credentials)} 个凭证，已保存到 {cred_file}"
                except Exception:
                    pass
            self.console.print(f"\n[bold green]🏁 [AutoPilot FSM] 结束, 最终状态: {self.fsm.state}[/bold green]")
            await _push_ws("finished", final_msg, final_state=self.fsm.state)

    @staticmethod
    async def cancel_autopilot(session_id: str) -> bool:
        """取消指定会话的 AutoPilot 任务。返回是否成功取消。"""
        task = running_autopilot_tasks.pop(session_id, None)
        if task is None or task.done():
            return False
        task.cancel()
        try:
            await asyncio.wait_for(task, timeout=5.0)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
        logger.info("autopilot_task_cancelled: session_id=%s", session_id)
        return True

    @staticmethod
    async def push_manual_command(session_id: str, message: str) -> bool:
        """插入手动命令到运行中的 AutoPilot 会话。"""
        orchestrator = _running_orchestrators.get(session_id)
        if orchestrator is None:
            logger.warning("[手动命令] 找不到运行中的 AutoPilot: session_id=%s", session_id)
            return False
        await orchestrator.manual_command_queue.put(message)
        logger.info("[手动命令] 已入队: session_id=%s, msg=%s", session_id, message[:100])
        return True

    @staticmethod
    async def cancel_all_autopilots() -> int:
        """取消所有运行中的 AutoPilot 任务。返回取消数。"""
        count = 0
        for sid, task in list(running_autopilot_tasks.items()):
            if not task.done():
                task.cancel()
                count += 1
            running_autopilot_tasks.pop(sid, None)
        if count:
            logger.info("autopilot_all_cancelled: count=%d", count)
        return count

    # ------------------------------------------------------------------
    # 新 AutoPilot 引擎接口 (重构方案)
    # ------------------------------------------------------------------

    async def run_new_autopilot(self, target: str) -> Dict[str, Any]:
        """
        运行新 AutoPilot 引擎 (5 阶段流程)。
        返回最终结果字典。
        """
        self.console.print(f"[bold cyan]🚀 [Orchestrator] 启动新 AutoPilot 引擎: {target}[/bold cyan]")

        engine = AutoPilotEngine(
            session_id=self.state_manager.session_id or "default",
            target=target,
            llm_client=self.llm_client,
            state_manager=self.state_manager,
            settings=self.settings,
            console=self.console,
            security=self.security,
        )
        # ★ 注入 WebSocket 管理器（避免循环导入）
        try:
            from api.websocket import _manager
            engine._ws_manager = _manager
        except Exception:
            pass

        try:
            result = await engine.run()
            self.console.print(f"[bold green]✅ 新 AutoPilot 完成: {result.get('status', 'unknown')}[/bold green]")
            return result
        except Exception as exc:
            self.console.print(f"[bold red]❌ 新 AutoPilot 异常: {exc}[/bold red]")
            logger.exception("[Orchestrator] run_new_autopilot 异常")
            return {"status": "error", "error": str(exc)}

    # ------------------------------------------------------------------
    # FSM 各阶段 ReAct 循环 (内部实现)
    # ------------------------------------------------------------------
    async def _phase_initialization(self, target: str) -> None:
        self.console.print(f"[cyan]📋 [FSM] 初始化阶段: 目标 {target}[/cyan]")
        self.fsm.start_timeout_monitor()
        # 直接跳转到侦察
        self.fsm.next()

    async def _phase_reconnaissance(self, target: str) -> None:
        self.console.print(f"[cyan]🔍 [FSM] 侦察阶段: {target}[/cyan]")
        self.fsm.start_timeout_monitor()
        msg = f"对目标 {target} 执行信息收集。先进行主机发现和端口扫描。"
        result = await self.chat_stream(msg)
        # 推送 AI 分析到前端对话窗口
        if result:
            await _ws_broadcast({
                "type": "autopilot_ai_response",
                "session_id": self.state_manager.session_id,
                "content": result,
                "state": self.fsm.state,
                "timestamp": time.time(),
            })
        # 简化: 一轮后进入漏洞扫描
        self.fsm.next()

    async def _phase_vuln_scan(self, target: str) -> None:
        self.console.print(f"[cyan]🔎 [FSM] 漏洞扫描阶段: {target}[/cyan]")
        self.fsm.start_timeout_monitor()
        msg = "对发现的端口和服务进行漏洞扫描，尝试识别已知漏洞。"
        result = await self.chat_stream(msg)
        if result:
            await _ws_broadcast({
                "type": "autopilot_ai_response",
                "session_id": self.state_manager.session_id,
                "content": result,
                "state": self.fsm.state,
                "timestamp": time.time(),
            })
        self.fsm.next()

    async def _phase_exploitation(self, target: str) -> None:
        self.console.print(f"[cyan]⚡ [FSM] 漏洞利用阶段: {target}[/cyan]")
        self.fsm.start_timeout_monitor()
        msg = "基于发现的漏洞尝试利用。"
        result = await self.chat_stream(msg)
        if result:
            await _ws_broadcast({
                "type": "autopilot_ai_response",
                "session_id": self.state_manager.session_id,
                "content": result,
                "state": self.fsm.state,
                "timestamp": time.time(),
            })
        self.fsm.next()

    async def _phase_privesc(self, target: str) -> None:
        self.console.print(f"[cyan]⬆️  [FSM] 提权阶段: {target}[/cyan]")
        self.fsm.start_timeout_monitor()
        msg = "尝试权限提升以获得 ROOT/SYSTEM 权限。"
        result = await self.chat_stream(msg)
        if result:
            await _ws_broadcast({
                "type": "autopilot_ai_response",
                "session_id": self.state_manager.session_id,
                "content": result,
                "state": self.fsm.state,
                "timestamp": time.time(),
            })
        self.fsm.next()

    async def _phase_lateral(self, target: str) -> None:
        self.console.print(f"[cyan]🔄 [FSM] 横向移动阶段: {target}[/cyan]")
        self.fsm.start_timeout_monitor()
        msg = "尝试在已获取访问权限的内网目标之间进行横向移动，寻找更多主机和凭据。"
        result = await self.chat_stream(msg)
        if result:
            await _ws_broadcast({
                "type": "autopilot_ai_response",
                "session_id": self.state_manager.session_id,
                "content": result,
                "state": self.fsm.state,
                "timestamp": time.time(),
            })
        self.fsm.next()

    async def _phase_collection(self, target: str) -> None:
        self.console.print(f"[cyan]📦 [FSM] 凭证收集阶段: {target}[/cyan]")
        self.fsm.start_timeout_monitor()
        msg = "收集已获取目标系统中的敏感信息、凭证、密码哈希和关键数据。"
        result = await self.chat_stream(msg)
        if result:
            await _ws_broadcast({
                "type": "autopilot_ai_response",
                "session_id": self.state_manager.session_id,
                "content": result,
                "state": self.fsm.state,
                "timestamp": time.time(),
            })
        self.fsm.next()