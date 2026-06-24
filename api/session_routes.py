"""
会话管理 API 路由 (api/session_routes.py)

端点:
- GET    /api/sessions              列出所有会话
- POST   /api/sessions              创建新会话
- GET    /api/sessions/{id}         获取会话详情
- DELETE /api/sessions/{id}         删除会话
- POST   /api/sessions/{id}/start   启动会话 (AutoPilot)
- POST   /api/sessions/{id}/pause   暂停会话
- POST   /api/sessions/{id}/resume  恢复会话
- GET    /api/sessions/{id}/steps   获取会话执行步骤
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

import asyncio
from pathlib import Path
from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import delete, select
from sqlalchemy.orm import selectinload

from db.database import get_database
from db.models import ExecutionStep, Session
from pydantic import BaseModel
from utils.time_utils import format_beijing_time

router = APIRouter(prefix="/api/sessions", tags=["会话"])


class CreateSessionRequest(BaseModel):
    target: str
    name: Optional[str] = None


class SessionResponse(BaseModel):
    id: str
    name: str = ""
    target: str
    status: str
    is_paused: bool
    phase: str
    createdAt: str = ""
    updatedAt: str = ""
    toolCount: int = 0
    steps_count: int = 0


@router.get("")
async def list_sessions(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
):
    db = get_database()
    async with db.get_session() as session:
        total_result = await session.execute(
            select(Session)
        )
        total = len(total_result.scalars().all())

        result = await session.execute(
            select(Session)
            .order_by(Session.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
            .options(selectinload(Session.steps))
        )
        sessions = result.scalars().all()
    items = []
    for s in sessions:
        items.append(SessionResponse(
            id=s.id,
            name=s.name or f"会话 {s.id[:8]}",
            target=s.target,
            status=s.status,
            is_paused=s.is_paused,
            phase=s.phase,
            createdAt=format_beijing_time(s.created_at),
            updatedAt=format_beijing_time(s.updated_at),
            toolCount=len(s.steps),  # 前端需要toolCount字段
            steps_count=len(s.steps),
        ))
    # 前端期望直接返回数组
    return items


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_session(
    req: CreateSessionRequest,
):
    db = get_database()
    async with db.get_session() as session:
        session_name = req.name or req.target
        new_session = Session(target=req.target)
        new_session.name = session_name
        session.add(new_session)
        await session.commit()
        await session.refresh(new_session)
    return SessionResponse(
        id=new_session.id,
        name=new_session.name or f"会话 {new_session.id[:8]}",
        target=new_session.target,
        status=new_session.status,
        is_paused=new_session.is_paused,
        phase=new_session.phase,
        createdAt=format_beijing_time(new_session.created_at),
        updatedAt=format_beijing_time(new_session.updated_at),
        toolCount=0,
        steps_count=0,
    )


@router.get("/{session_id}")
async def get_session(
    session_id: str,
):
    db = get_database()
    async with db.get_session() as session:
        result = await session.execute(
            select(Session)
            .where(Session.id == session_id)
            .options(selectinload(Session.steps))
        )
        s = result.scalar_one_or_none()
        if s is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="会话不存在")
    return SessionResponse(
        id=s.id,
        name=s.name or f"会话 {s.id[:8]}",
        target=s.target,
        status=s.status,
        is_paused=s.is_paused,
        phase=s.phase,
        createdAt=format_beijing_time(s.created_at),
        updatedAt=format_beijing_time(s.updated_at),
        toolCount=len(s.steps),
        steps_count=len(s.steps),
    )


@router.put("/{session_id}")
async def update_session(
    session_id: str,
    body: Dict[str, Any],
):
    """更新会话（目前仅支持修改 target）"""
    db = get_database()
    async with db.get_session() as session:
        result = await session.execute(
            select(Session).where(Session.id == session_id)
        )
        s = result.scalar_one_or_none()
        if s is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="会话不存在")
        if "target" in body:
            s.target = body["target"]
        if "name" in body:
            # 前端有时会把 name 带过来，忽略就好
            pass
        await session.commit()
        await session.refresh(s)
    return SessionResponse(
        id=s.id,
        name=s.name or f"会话 {s.id[:8]}",
        target=s.target,
        status=s.status,
        is_paused=s.is_paused,
        phase=s.phase,
        createdAt=format_beijing_time(s.created_at),
        updatedAt=format_beijing_time(s.updated_at),
        toolCount=0,
        steps_count=0,
    )


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(
    session_id: str,
):
    db = get_database()
    async with db.get_session() as session:
        result = await session.execute(
            select(Session).where(Session.id == session_id)
        )
        s = result.scalar_one_or_none()
        if s is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="会话不存在")
        await session.delete(s)
        await session.commit()


@router.post("/{session_id}/start")
async def start_session(
    session_id: str,
):
    db = get_database()
    try:
        from core.orchestrator import Orchestrator
        orch = Orchestrator()
        target = (await _get_session_target(session_id)) or "unknown"
        # 使用新 AutoPilot 引擎 (5 阶段流程)
        orch.toggle_autopilot(True)
        orch.state_manager.session_id = session_id
        asyncio.create_task(orch.run_new_autopilot(target))
        # 更新数据库状态为 active
        async with db.get_session() as session:
            result = await session.execute(
                select(Session).where(Session.id == session_id)
            )
            s = result.scalar_one_or_none()
            if s:
                s.status = "active"
                s.is_paused = False
                await session.commit()
            return {"status": "active"}
        return {"status": "failed"}
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"启动会话失败: {exc}",
        )


@router.post("/{session_id}/pause")
async def pause_session(
    session_id: str,
):
    """暂停 AutoPilot（数据库标记 + 实际控制运行中的引擎）"""
    db = get_database()
    async with db.get_session() as session:
        result = await session.execute(
            select(Session).where(Session.id == session_id)
        )
        s = result.scalar_one_or_none()
        if s is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="会话不存在")
        s.is_paused = True
        s.status = "paused"
        await session.commit()

    # ★ 实际控制运行中的 AutoPilotEngine
    from core.auto_pilot import _running_engines
    engine = _running_engines.get(session_id)
    if engine:
        engine.pause()
        logger.info("[pause_session] 已暂停引擎: session_id=%s", session_id)
    else:
        logger.warning("[pause_session] 未找到运行中的引擎: session_id=%s", session_id)

    return {"status": "paused"}


@router.post("/{session_id}/resume")
async def resume_session(
    session_id: str,
):
    """恢复 AutoPilot（数据库标记 + 实际控制运行中的引擎）"""
    db = get_database()
    async with db.get_session() as session:
        result = await session.execute(
            select(Session).where(Session.id == session_id)
        )
        s = result.scalar_one_or_none()
        if s is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="会话不存在")
        s.is_paused = False
        s.status = "active"
        await session.commit()

    # ★ 实际控制运行中的 AutoPilotEngine
    from core.auto_pilot import _running_engines
    engine = _running_engines.get(session_id)
    if engine:
        engine.resume()
        logger.info("[resume_session] 已恢复引擎: session_id=%s", session_id)
    else:
        logger.warning("[resume_session] 未找到运行中的引擎: session_id=%s", session_id)

    return {"status": "active"}


@router.post("/{session_id}/abort")
async def abort_session(
    session_id: str,
):
    """强制中止会话：取消 AutoPilot 任务 + 杀死子进程 + 标记 DB。"""
    db = get_database()
    async with db.get_session() as session:
        result = await session.execute(
            select(Session).where(Session.id == session_id)
        )
        s = result.scalar_one_or_none()
        if s is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="会话不存在")
        s.status = "aborted"
        s.is_paused = False
        await session.commit()

    # 取消 AutoPilot 任务
    from core.orchestrator import Orchestrator
    await Orchestrator.cancel_autopilot(session_id)

    # 终止所有子进程
    from tools.executor import get_executor
    killed = get_executor().kill_all()

    logger.info("会话已中止: session_id=%s, killed_processes=%d", session_id, killed)
    return {"status": "aborted", "killed_processes": killed}


class StepResponse(BaseModel):
    id: str
    seq: int
    action_type: str
    tool_name: Optional[str] = None
    tool_success: Optional[bool] = None
    tool_output_summary: Optional[str] = None
    tool_duration: Optional[float] = None
    llm_content: Optional[str] = None
    from_state: Optional[str] = None
    to_state: Optional[str] = None
    created_at: str = ""


@router.get("/{session_id}/steps")
async def get_session_steps(
    session_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
):
    db = get_database()
    async with db.get_session() as session:
        total_result = await session.execute(
            select(ExecutionStep).where(ExecutionStep.session_id == session_id)
        )
        total = len(total_result.scalars().all())

        result = await session.execute(
            select(ExecutionStep)
            .where(ExecutionStep.session_id == session_id)
            .order_by(ExecutionStep.seq)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        steps = result.scalars().all()
    items = []
    for st in steps:
        items.append(StepResponse(
            id=st.id,
            seq=st.seq,
            action_type=st.action_type,
            tool_name=st.tool_name,
            tool_success=st.tool_success,
            tool_output_summary=(st.tool_output_summary or "")[:500],
            tool_duration=st.tool_duration,
            llm_content=(st.llm_content or "")[:500],
            from_state=st.from_state,
            to_state=st.to_state,
            created_at=format_beijing_time(st.created_at),
        ))
    return {"total": total, "page": page, "page_size": page_size, "items": items}


async def _get_session_target(session_id: str) -> Optional[str]:
    db = get_database()
    async with db.get_session() as session:
        result = await session.execute(
            select(Session).where(Session.id == session_id)
        )
        s = result.scalar_one_or_none()
        return s.target if s else None


# ========================================================================
# 报告生成
# ========================================================================
class ReportResponse(BaseModel):
    status: str
    path: str = ""
    error: str = ""


@router.get("/{session_id}/report")
async def generate_session_report(session_id: str):
    """生成并返回 HTML 渗透测试报告。"""
    try:
        from reports.manager import ReportManager
        manager = ReportManager(output_dir="reports_output")
        path = await manager.generate_html(session_id)
        if path is None:
            return ReportResponse(status="error", error="报告生成失败，会话数据可能不完整")
        # 读取文件内容并返回 HTML
        from fastapi.responses import HTMLResponse
        content = Path(path).read_text(encoding="utf-8")
        return HTMLResponse(content=content)
    except Exception as exc:
        logger.error("[Report] 报告生成异常: %s", exc)
        return ReportResponse(status="error", error=str(exc))


@router.get("/{session_id}/credentials")
async def get_session_credentials(session_id: str):
    """获取会话中 AutoPilot 发现的凭证列表。"""
    try:
        data_dir = getattr(__import__("config.settings", fromlist=["Settings"]).get_settings(), "data_dir", "./data")
        db_path = Path(data_dir) / "autopilot_progress.db"
        if not db_path.exists():
            return {"credentials": []}
        import sqlite3, json
        with sqlite3.connect(str(db_path)) as conn:
            row = conn.execute(
                "SELECT found_credentials FROM progress WHERE session_id = ?", (session_id,)
            ).fetchone()
        if row and row[0]:
            creds = json.loads(row[0])
            return {"credentials": creds}
        return {"credentials": []}
    except Exception as exc:
        logger.warning("获取凭证失败: %s", exc)
        return {"credentials": []}


@router.get("/{session_id}/progress")
async def get_session_progress(session_id: str):
    """检查会话是否有已保存的 AutoPilot 进度。"""
    try:
        data_dir = getattr(__import__("config.settings", fromlist=["Settings"]).get_settings(), "data_dir", "./data")
        db_path = Path(data_dir) / "autopilot_progress.db"
        if not db_path.exists():
            return {"has_progress": False, "current_stage": 0}
        import sqlite3
        with sqlite3.connect(str(db_path)) as conn:
            row = conn.execute(
                "SELECT current_stage FROM progress WHERE session_id = ?", (session_id,)
            ).fetchone()
        if row:
            return {"has_progress": True, "current_stage": row[0]}
        return {"has_progress": False, "current_stage": 0}
    except Exception as exc:
        logger.warning("检查进度异常: %s", exc)
        return {"has_progress": False, "current_stage": 0}
