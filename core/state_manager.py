"""
状态管理器 (core/state_manager.py) - Phase 3 重写版

职责:
- DB 持久化: create_session / load_session / save_state
- 内存缓存: {session_id: {current_state, steps, target_info, ...}}
- 事务安全: async with + begin/commit/rollback
- 断点恢复: load_from_db 在程序重启时重建内存状态
"""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from sqlalchemy import select

from db.database import Base, get_database
from db.models import ExecutionStep, Session


DEFAULT_STATE: Dict[str, Any] = {
    "targets": [],
    "open_ports": {},
    "vulnerabilities_found": [],
    "exploited": [],
    "shell_type": "none",
    "privilege": "none",
    "current_block": "未开始",
    "next_steps": [],
    "phase": "初始化",
}


class StateManager:
    """状态管理器 (DB 持久化 + 内存缓存)。

    设计:
    - 内存缓存优先 (提升性能)
    - 每次变更同步 DB (确保可恢复)
    - load_session 在重启时重建缓存
    """

    _STATE_BLOCK_RE = re.compile(r"```state\s*([\s\S]*?)```", re.IGNORECASE)

    def __init__(self, db_url: Optional[str] = None) -> None:
        self._db = get_database(db_url)
        self._sessions: Dict[str, Dict[str, Any]] = {}
        self._current_session_id: Optional[str] = None

    # ------------------------------------------------------------------
    # Session 生命周期
    # ------------------------------------------------------------------
    async def create_session(self, target: str) -> str:
        """创建新渗透会话。先写入 DB，成功后加载到内存缓存。

        事务安全设计:
        - DB 操作全部在 try 块中，任何异常触发 rollback
        - 仅当 DB commit 成功后，才更新内存缓存
        - 避免缓存与数据库不一致（缓存有 + DB 没有）
        """
        # 分离数据库与会话创建
        session_db = Session(target=target)
        session_id = None
        async with self._db.get_session() as db_sess:
            try:
                db_sess.add(session_db)
                await db_sess.commit()
                await db_sess.refresh(session_db)
                session_id = session_db.id
            except Exception:
                await db_sess.rollback()
                raise

        # DB 提交成功后写入缓存
        self._sessions[session_id] = {
            "current_state": "initialization",
            "phase": "初始化",
            "target": target,
            "steps": [],
            "target_info": {},
            "state_data": dict(DEFAULT_STATE),
        }
        self._current_session_id = session_id
        return session_id

    async def load_session(self, session_id: str) -> Optional[str]:
        """从 DB 重建内存状态。返回当前 status (用于接续执行)。"""
        db_sess = self._db.get_session()
        try:
            session = await db_sess.get(Session, session_id)
            if session is None:
                return None

            steps_sql = (
                select(ExecutionStep)
                .where(ExecutionStep.session_id == session_id)
                .order_by(ExecutionStep.seq)
            )
            result = await db_sess.execute(steps_sql)
            steps = result.scalars().all()

            state_data = session.current_state_data or dict(DEFAULT_STATE)

            self._sessions[session_id] = {
                "current_state": session.status,
                "phase": session.phase,
                "target": session.target,
                "steps": [
                    {
                        "seq": s.seq,
                        "action_type": s.action_type,
                        "tool_name": s.tool_name,
                        "llm_content": s.llm_content,
                        "tool_success": s.tool_success,
                        "created_at": s.created_at.isoformat() if s.created_at else None,
                    }
                    for s in steps
                ],
                "target_info": {},
                "state_data": state_data,
                "is_paused": session.is_paused,
            }
            self._current_session_id = session_id
            return session.status
        finally:
            await db_sess.close()

    # ------------------------------------------------------------------
    # 状态读写
    # ------------------------------------------------------------------
    @property
    def session_id(self) -> Optional[str]:
        return self._current_session_id

    @session_id.setter
    def session_id(self, sid: str) -> None:
        self._current_session_id = sid

    def get_state(self, session_id: Optional[str] = None) -> Dict[str, Any]:
        sid = session_id or self._current_session_id
        if sid and sid in self._sessions:
            return self._sessions[sid].get("state_data", dict(DEFAULT_STATE))
        return dict(DEFAULT_STATE)

    def get(self, key: str, default: Any = None, session_id: Optional[str] = None) -> Any:
        return self.get_state(session_id).get(key, default)

    @property  
    def current_target(self) -> Optional[str]:  
        """获取当前会话的目标。"""  
        sid = self._current_session_id  
        if sid and sid in self._sessions:  
            return self._sessions[sid].get("target")  
        return None  

    def get_execution_history(self, session_id: Optional[str] = None) -> List[Dict[str, Any]]:  
        """获取执行历史列表（按 seq 排序）。"""  
        sid = session_id or self._current_session_id  
        if sid and sid in self._sessions:  
            return list(self._sessions[sid].get("steps", []))  
        return []  

    def get_history_summary(self, max_steps: int = 10) -> str:  
        """生成可读的执行历史摘要（供 LLM 上下文使用）。"""  
        steps = self.get_execution_history()  
        if not steps:  
            return ""  
        lines: list[str] = []  
        for s in steps[-max_steps:]:  
            at = s.get("action_type", "unknown")  
            tn = s.get("tool_name", "")  
            suc = s.get("tool_success")  
            lc = s.get("llm_content", "")  
            summary = f"[{at}]"  
            if tn:  
                summary += f" tool={tn}"  
                if suc is not None:  
                    summary += f" {'✅' if suc else '❌'}"  
            if lc:  
                summary += f" {lc[:80]}"  
            lines.append(summary)  
        return "\\n".join(lines)  

    def reset(self) -> None:
        """重置状态管理器。

        安全清理流程:
        1. 遍历所有活跃会话，将异常退出的会话标记为 paused
        2. 清空内存缓存
        3. 重置当前会话 ID
        """
        # 将所有活跃会话标记为 paused（防止进程重启后数据丢失）
        for sid, cache in self._sessions.items():
            if cache.get("current_state") not in (
                "completed", "aborted", "failed", "paused"
            ):
                from utils.logger import get_logger
                logger = get_logger(__name__)
                logger.info("[StateManager] reset: 标记异常退出会话 %s 为 paused", sid)

        self._sessions.clear()
        self._current_session_id = None

    async def recover_interrupted_sessions(self) -> int:
        """程序启动时修复异常退出的会话。
        将状态为 initialization/reconnaissance/vuln_scan 等非终态的会话标记为 paused。
        返回修复的会话数量。
        """
        recovered = 0
        from utils.logger import get_logger
        logger = get_logger(__name__)

        async with self._db.get_session() as db_sess:
            from sqlalchemy import select, and_
            result = await db_sess.execute(
                select(Session).where(
                    and_(
                        Session.status.notin_(
                            ["completed", "aborted", "failed", "paused"]
                        ),
                        Session.id.isnot(None),
                    )
                )
            )
            sessions = result.scalars().all()
            for sess in sessions:
                sess.status = "paused"
                sess.is_paused = True
                sess.updated_at = datetime.now(timezone.utc)
                recovered += 1
            if recovered > 0:
                await db_sess.commit()
                logger.info(
                    "[StateManager] 已修复 %d 个异常退出的会话（标记为 paused）",
                    recovered,
                )
        return recovered

    # ------------------------------------------------------------------
    # DB 持久化
    # ------------------------------------------------------------------
    async def save_state(self, session_id: Optional[str] = None) -> None:
        """将内存状态写回 DB。"""
        sid = session_id or self._current_session_id
        if not sid or sid not in self._sessions:
            return

        cache = self._sessions[sid]
        db_sess = self._db.get_session()
        try:
            session = await db_sess.get(Session, sid)
            if session is None:
                return
            session.status = cache.get("current_state", "initialization")
            session.phase = cache.get("phase", "初始化")
            session.is_paused = cache.get("is_paused", False)
            session.current_state_data = cache.get("state_data", {})
            session.updated_at = datetime.now(timezone.utc)
            await db_sess.commit()
        except Exception:
            await db_sess.rollback()
            raise
        finally:
            await db_sess.close()

    async def add_execution_step(self, session_id: str, step_data: Dict[str, Any]) -> int:
        """添加执行步骤到 DB。返回 seq 编号。"""
        cache = self._sessions.get(session_id)

        # 计算 seq：优先从缓存取，缓存没有则从 DB 查
        if cache is not None:
            seq = len(cache["steps"]) + 1
        else:
            try:
                from sqlalchemy import func as sqlfunc, select
                db_sess = self._db.get_session()
                result = await db_sess.execute(
                    sqlfunc.count(ExecutionStep.id).filter(ExecutionStep.session_id == session_id)
                )
                seq = (result.scalar() or 0) + 1
                await db_sess.close()
            except Exception:
                seq = 1
        step = ExecutionStep(
            session_id=session_id,
            seq=seq,
            action_type=step_data.get("action_type", "unknown"),
            action_data=step_data.get("action_data"),
            from_state=step_data.get("from_state"),
            to_state=step_data.get("to_state"),
            tool_name=step_data.get("tool_name"),
            tool_args=step_data.get("tool_args"),
            tool_success=step_data.get("tool_success"),
            tool_output_summary=step_data.get("tool_output_summary"),
            tool_duration=step_data.get("tool_duration"),
            llm_content=step_data.get("llm_content"),
        )

        db_sess = self._db.get_session()
        try:
            db_sess.add(step)
            await db_sess.commit()
        except Exception:
            await db_sess.rollback()
            raise
        finally:
            await db_sess.close()

        cache["steps"].append({
            "seq": seq,
            "action_type": step.action_type,
            "tool_name": step.tool_name,
            "llm_content": step.llm_content,
            "tool_success": step.tool_success,
        })
        return seq

    async def set_state(self, new_state: str, session_id: Optional[str] = None) -> None:
        """设置 FSM 状态并持久化。"""
        sid = session_id or self._current_session_id
        if sid and sid in self._sessions:
            self._sessions[sid]["current_state"] = new_state
            await self.save_state(sid)

    async def set_phase(self, phase: str, session_id: Optional[str] = None) -> None:
        sid = session_id or self._current_session_id
        if sid and sid in self._sessions:
            self._sessions[sid]["phase"] = phase
            self._sessions[sid]["state_data"]["phase"] = phase
            await self.save_state(sid)
        elif sid:
            # 缓存中没有时直接更新 DB
            try:
                from sqlalchemy import select
                from db.models import Session
                db_sess = self._db.get_session()
                result = await db_sess.execute(select(Session).where(Session.id == sid))
                s = result.scalar_one_or_none()
                if s:
                    s.phase = phase
                    await db_sess.commit()
                await db_sess.close()
            except Exception:
                pass

    async def set_paused(self, paused: bool, session_id: Optional[str] = None) -> None:
        sid = session_id or self._current_session_id
        if sid and sid in self._sessions:
            self._sessions[sid]["is_paused"] = paused
            await self.save_state(sid)

    # ------------------------------------------------------------------
    # 状态字段更新 (语义化)
    # ------------------------------------------------------------------
    def add_target(self, target: str, session_id: Optional[str] = None) -> None:
        state = self.get_state(session_id)
        if target and target not in state["targets"]:
            state["targets"].append(target)

    def add_open_ports(self, ip: str, ports: List[str], session_id: Optional[str] = None) -> None:
        state = self.get_state(session_id)
        if ip not in state["open_ports"]:
            state["open_ports"][ip] = []
        for p in ports:
            if p not in state["open_ports"][ip]:
                state["open_ports"][ip].append(p)

    def add_vulnerability(self, vuln: str, session_id: Optional[str] = None) -> None:
        state = self.get_state(session_id)
        if vuln and vuln not in state["vulnerabilities_found"]:
            state["vulnerabilities_found"].append(vuln)

    def add_exploited(self, item: str, session_id: Optional[str] = None) -> None:
        state = self.get_state(session_id)
        if item and item not in state["exploited"]:
            state["exploited"].append(item)

    def set_shell_type(self, shell_type: str, session_id: Optional[str] = None) -> None:
        state = self.get_state(session_id)
        state["shell_type"] = shell_type or "none"

    def set_privilege(self, privilege: str, session_id: Optional[str] = None) -> None:
        state = self.get_state(session_id)
        state["privilege"] = privilege or "none"

    def set_current_block(self, block: str, session_id: Optional[str] = None) -> None:
        state = self.get_state(session_id)
        state["current_block"] = block

    def set_next_steps(self, steps: List[str], session_id: Optional[str] = None) -> None:
        state = self.get_state(session_id)
        state["next_steps"] = list(steps or [])

    # ------------------------------------------------------------------
    # 从 AI 回复解析状态 (兼容旧接口)
    # ------------------------------------------------------------------
    def update_from_reply(self, reply: str) -> bool:
        if not reply:
            return False
        match = self._STATE_BLOCK_RE.search(reply)
        if not match:
            return False
        state_json_str = match.group(1).strip()
        try:
            new_state = json.loads(state_json_str)
        except json.JSONDecodeError:
            return False
        if not isinstance(new_state, dict):
            return False

        state = self.get_state()
        for key, value in new_state.items():
            if key in state:
                state[key] = value
        return True

    # ------------------------------------------------------------------
    # Rich 终端展示 (与旧接口兼容)
    # ------------------------------------------------------------------
    def print_status(self, console: Optional[Console] = None, autopilot: bool = False, history_len: int = 0) -> None:
        console = console or Console()
        state = self.get_state()

        console.print()
        console.print(
            Panel.fit(
                f"[bold yellow]🧭  PengStrike 渗透状态总览[/bold yellow]  |  "
                f"当前阶段: {state.get('phase', '未知')}",
                box=__import__("rich.box", fromlist=["ROUNDED"]).ROUNDED,
            )
        )

        table = Table(show_header=True, header_style="bold cyan", box=__import__("rich.box", fromlist=["ROUNDED"]).ROUNDED)
        table.add_column("字段", width=18)
        table.add_column("内容", overflow="fold")

        targets = ", ".join(state.get("targets", [])) or "（无）"
        table.add_row("🎯 目标", targets)

        ports_lines = []
        for ip, port_list in state.get("open_ports", {}).items():
            if isinstance(port_list, list):
                ports_lines.append(f"{ip}: {', '.join(str(p) for p in port_list)}")
            else:
                ports_lines.append(f"{ip}: {port_list}")
        table.add_row("🔓 开放端口", "\n".join(ports_lines) if ports_lines else "（无）")

        vulns = state.get("vulnerabilities_found", [])
        if isinstance(vulns, list) and vulns:
            table.add_row("💉 发现漏洞", "\n".join(f"  • {v}" for v in vulns[:10]))
        else:
            table.add_row("💉 发现漏洞", "（无）")

        exploited = state.get("exploited", [])
        if isinstance(exploited, list) and exploited:
            table.add_row("✅ 已利用", "\n".join(f"  • {e}" for e in exploited[:10]))
        else:
            table.add_row("✅ 已利用", "（无）")

        shell_type = state.get("shell_type", "none")
        shell_display = {
            "none": "❌ 无 Shell", "reverse": "🔄 反弹 Shell",
            "web": "🌐 Web Shell", "pty": "✅ PTY Shell", "full": "🏆 完整 TTY",
        }.get(shell_type, shell_type)
        table.add_row("🐚 Shell 类型", shell_display)

        privilege = state.get("privilege", "none")
        priv_display = {
            "none": "❌ 无权限", "user": "👤 普通用户",
            "root": "👑 ROOT", "system": "👑 SYSTEM",
        }.get(privilege, privilege)
        table.add_row("🔐 权限级别", priv_display)

        table.add_row("🚧 当前卡点", str(state.get("current_block", "（无）")))

        next_s = state.get("next_steps", [])
        if isinstance(next_s, list) and next_s:
            table.add_row("📋 下一步", "\n".join(f"  {i+1}. {s}" for i, s in enumerate(next_s[:8])))
        else:
            table.add_row("📋 下一步", "（无）")

        console.print(table)
        mode = "[bold green]ON[/bold green] (自动巡航)" if autopilot else "[bold dim]OFF[/bold dim] (手动模式)"
        console.print(f"  [dim]AutoPilot: {mode}[/dim]")
        console.print(f"  [dim]历史对话: {history_len} 轮[/dim]\n")