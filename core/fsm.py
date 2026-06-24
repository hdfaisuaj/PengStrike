""" 
FSM 状态机 (core/fsm.py)

职责:
- 基于 transitions 库实现渗透流程状态机
- 定义所有状态: initialization, reconnaissance, vuln_scan, exploitation, privesc, lateral, collection, completed, paused, aborted, failed
- 定义流转条件: success/failure/pause hooks
- 每个状态设置 timeout 阈值 (300秒), 超时自动触发 next 或跳转 failed
- 与 StateManager 联动: 状态变更时自动落库
"""

from __future__ import annotations

import asyncio
import time
from typing import Any, Callable, Dict, Optional

from transitions import Machine


# ========================================================================
# 状态定义
# ========================================================================
PENTEST_STATES = [
    "initialization",
    "reconnaissance",
    "vuln_scan",
    "exploitation",
    "privesc",
    "lateral",
    "collection",
    "completed",
    "paused",
    "aborted",
    "failed",
]

# 每个状态的默认超时 (秒)
STATE_TIMEOUTS: Dict[str, float] = {
    "initialization": 60.0,
    "reconnaissance": 300.0,
    "vuln_scan": 300.0,
    "exploitation": 600.0,
    "privesc": 600.0,
    "lateral": 300.0,
    "collection": 300.0,
    "completed": 10.0,
    "paused": 0.0,
    "aborted": 0.0,
    "failed": 0.0,
}


# ========================================================================
# 回调类型
# ========================================================================
OnEnterCallback = Callable[[], None]
OnExitCallback = Callable[[], None]
OnTimeoutCallback = Callable[[str], None]  # 参数: 超时的 state 名称


# ========================================================================
# FSM 封装
# ========================================================================
class PentestFSM:
    """渗透测试流程状态机。

    流转规则:
    - initialization -> reconnaissance (on success)
    - reconnaissance -> vuln_scan (发现端口/服务)
    - reconnaissance -> failed (超时或 LLM 连续失败)
    - vuln_scan -> exploitation (发现可利用漏洞)
    - exploitation -> privesc (获得 shell)
    - privesc -> lateral (获得 root/system)
    - lateral -> collection (获取凭证)
    - collection -> completed (目标达成)
    - 任意状态 -> paused (外部 pause 信号)
    - paused -> 原状态 (resume)
    - 任意状态 -> aborted (外部 abort 信号)
    - 任意状态 -> failed (on_failure 全局捕获)
    """

    # 类属性: 所有合法状态集合 (供外部校验使用, 如 orchestrator.resume_session)
    PENTEST_STATES: set = {
        "initialization", "reconnaissance", "vuln_scan", "exploitation",
        "privesc", "lateral", "collection", "completed",
        "paused", "aborted", "failed",
    }

    def __init__(
        self,
        on_enter_cb: Optional[OnEnterCallback] = None,
        on_exit_cb: Optional[OnExitCallback] = None,
        on_timeout_cb: Optional[OnTimeoutCallback] = None,
        initial_state: str = "initialization",
    ) -> None:
        self._on_enter_cb = on_enter_cb
        self._on_exit_cb = on_exit_cb
        self._on_timeout_cb = on_timeout_cb

        # transitions 库的 Machine
        self._machine = Machine(
            model=self,
            states=PENTEST_STATES,
            initial=initial_state,
            auto_transitions=False,
            ignore_invalid_triggers=True,
            before_state_change=self._before_change,
            after_state_change=self._after_change,
        )

        # 正向流转
        self._machine.add_transition("next", "initialization", "reconnaissance")
        self._machine.add_transition("next", "reconnaissance", "vuln_scan")
        self._machine.add_transition("next", "vuln_scan", "exploitation")
        self._machine.add_transition("next", "exploitation", "privesc")
        self._machine.add_transition("next", "privesc", "lateral")
        self._machine.add_transition("next", "lateral", "collection")
        self._machine.add_transition("next", "collection", "completed")

        # 回退 / 重试
        self._machine.add_transition("retry", "*", "reconnaissance")

        # 失败全局捕获
        self._machine.add_transition("fail", "*", "failed")

        # 暂停/恢复
        self._machine.add_transition("pause", "*", "paused")
        self._machine.add_transition("resume", "paused", "=")  # = 表示回到前一状态

        # 中止
        self._machine.add_transition("abort", "*", "aborted")

        # 自定义跳转 (供特定场景使用)
        self._machine.add_transition("jump", "*", "=", conditions=["_jump_allowed"])

        # 超时/状态追踪
        self._state_start_time: float = time.monotonic()
        self._timeout_task: Optional[asyncio.Task] = None
        self._jump_target: Optional[str] = None

    # ------------------------------------------------------------------
    # 跳转辅助
    # ------------------------------------------------------------------
    def _jump_allowed(self) -> bool:
        return self._jump_target is not None

    def jump_to(self, target_state: str) -> None:
        """跳转到指定状态 (设置目标后触发 jump)。"""
        if target_state not in PENTEST_STATES:
            raise ValueError(f"无效状态: {target_state}")
        self._jump_target = target_state
        self.jump()
        self._jump_target = None

    # ------------------------------------------------------------------
    # Hook
    # ------------------------------------------------------------------
    def _before_change(self) -> None:
        """进入状态变更时：先取消当前超时任务，再调用退出回调。"""
        if self._on_exit_cb:
            self._on_exit_cb()

    def _after_change(self) -> None:
        """状态变更后：重置计时，调用进入回调。"""
        self._state_start_time = time.monotonic()
        if self._on_enter_cb:
            self._on_enter_cb()

    # ------------------------------------------------------------------
    # 超时管理
    # ------------------------------------------------------------------
    def start_timeout_monitor(self) -> None:
        """启动超时监控 (在进入新状态时调用)。"""
        self._state_start_time = time.monotonic()
        # 取消已有超时任务，避免回调使用旧状态
        self._cancel_timeout_task()

    def _cancel_timeout_task(self) -> None:
        """取消已有的超时监控任务。"""
        if self._timeout_task is not None and not self._timeout_task.done():
            self._timeout_task.cancel()
            self._timeout_task = None

    def check_timeout(self) -> Optional[str]:
        """检查当前状态是否超时。返回超时的 state 名称或 None。"""
        elapsed = time.monotonic() - self._state_start_time
        timeout = STATE_TIMEOUTS.get(self.state, 300.0)
        if timeout > 0 and elapsed > timeout:
            return self.state
        return None

    async def monitor_timeout(self) -> None:
        """异步超时监控 (在新状态 enter 时启动协程)。

        设计说明:
        - 每次状态切换时由 _after_change 取消旧超时任务
        - 超时触发前重新检查当前状态，防止状态已变更后使用旧状态作为回调参数
        - 避免 sleep 整段 timeout 后才检查（改为按秒轮询，响应更快）
        """
        current_state_at_start = self.state
        timeout = STATE_TIMEOUTS.get(current_state_at_start, 300.0)
        if timeout <= 0:
            return

        try:
            while self.state not in ("completed", "aborted", "failed", "paused"):
                # 每 1 秒轮询一次，及时响应状态变更
                await asyncio.sleep(1)

                # 如果状态在超时任务等待期间已被外部变更，静默退出
                if self.state != current_state_at_start:
                    return

                elapsed = time.monotonic() - self._state_start_time
                if elapsed > timeout:
                    # 再次确认状态未变（双重校验，防止竞态）
                    if self.state == current_state_at_start and self.state != "aborted":
                        if self._on_timeout_cb:
                            self._on_timeout_cb(self.state)
                        self.fail()
                    return
        except asyncio.CancelledError:
            # 超时任务被取消（状态切换时正常行为），静默退出
            pass

    # ------------------------------------------------------------------
    # 暂停/恢复状态保存 (与 StateManager 联动)
    # ------------------------------------------------------------------
    @property
    def paused_state(self) -> Optional[str]:
        return getattr(self, "_paused_from", None)

    def on_pause(self) -> None:
        """进入 paused 前保存当前状态。"""
        self._paused_from = self.state

    def on_resume(self) -> None:
        """从 paused 恢复后清除记录。"""
        self._paused_from = None

    # ------------------------------------------------------------------
    # 中止管理
    # ------------------------------------------------------------------
    def is_aborted(self) -> bool:
        """当前状态是否为已中止。"""
        return self.state == "aborted"

    def abort(self) -> None:
        """完整中止 FSM：
        1. 取消内部超时监控任务
        2. 跳转到 aborted 状态
        3. 重置状态追踪
        """
        self._cancel_timeout_task()
        if self.state != "aborted":
            try:
                self.trigger('abort')
            except Exception:
                pass
            self.state = "aborted"

    # ------------------------------------------------------------------
    # 打印状态
    # ------------------------------------------------------------------
    def print_state(self) -> str:
        elapsed = time.monotonic() - self._state_start_time
        timeout = STATE_TIMEOUTS.get(self.state, 300.0)
        timeout_str = f"{timeout:.0f}s" if timeout > 0 else "∞"
        return (
            f"[FSM] 当前状态: {self.state}  "
            f"(已停留 {elapsed:.0f}s / 超时 {timeout_str})"
        )

    # ------------------------------------------------------------------
    # 断点续跑：进度保存/加载 (SQLite)
    # ------------------------------------------------------------------

    def save_progress(self, db_path: str = "./data/fsm_progress.db") -> None:
        """保存当前 FSM 状态到 SQLite（断点续跑）。"""
        import sqlite3
        from pathlib import Path
        try:
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
            with sqlite3.connect(db_path) as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS fsm_progress (
                        session_id TEXT PRIMARY KEY,
                        current_state TEXT NOT NULL,
                        state_start_time REAL NOT NULL,
                        updated_at REAL NOT NULL
                    )
                """)
                # ★ 修复：检查 _session_id 是否设置，未设置则用 session 对象的 id（如果可用）
                _sid = getattr(self, "_session_id", None)
                if _sid is None:
                    # 尝试从 FSM 的 trigger 参数中获取 session_id
                    _sid = getattr(self, "_last_session_id", None)
                if _sid is None:
                    # 仍然没有，记录警告并使用 "unknown"（而不是 "default"）
                    import logging
                    _logger = logging.getLogger(__name__)
                    _logger.warning(
                        "[FSM] save_progress() 未设置 session_id，使用 'unknown'。请调用 set_session_id() 设置。"
                    )
                    _sid = "unknown"

                conn.execute("""
                    INSERT INTO fsm_progress (session_id, current_state, state_start_time, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(session_id) DO UPDATE SET
                        current_state=excluded.current_state,
                        state_start_time=excluded.state_start_time,
                        updated_at=excluded.updated_at
                """, (
                    _sid,
                    self.state,
                    self._state_start_time,
                    __import__("time").time(),
                ))
                conn.commit()
        except Exception as exc:
            print(f"[FSM] 保存进度失败: {exc}")

    def load_progress(self, db_path: str = "./data/fsm_progress.db") -> bool:
        """从 SQLite 加载 FSM 状态（断点续跑）。返回是否成功加载。"""
        import sqlite3
        # ★ 修复：检查 _session_id 是否设置
        _sid = getattr(self, "_session_id", None)
        if _sid is None:
            _sid = getattr(self, "_last_session_id", None)
        if _sid is None:
            import logging
            _logger = logging.getLogger(__name__)
            _logger.warning(
                "[FSM] load_progress() 未设置 session_id，使用 'unknown'。请调用 set_session_id() 设置。"
            )
            _sid = "unknown"

        try:
            with sqlite3.connect(db_path) as conn:
                row = conn.execute(
                    "SELECT current_state, state_start_time FROM fsm_progress WHERE session_id = ?",
                    (_sid,)
                ).fetchone()
            if row is None:
                return False
            # 恢复状态（使用 transitions 库的 set_state 方法）
            self._machine.set_state(row[0])
            self._state_start_time = row[1]
            return True
        except Exception as exc:
            print(f"[FSM] 加载进度失败: {exc}")
            return False

    def set_session_id(self, session_id: str) -> None:
        """设置 session_id（供进度保存/加载使用）。"""
        self._session_id = session_id