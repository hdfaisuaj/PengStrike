"""
ORM 模型定义 (db/models.py)

模型:
- User: 用户认证
- Session: 渗透会话 (顶层聚合根)
- ExecutionStep: 每一步执行记录
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db.database import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _new_id() -> str:
    return uuid.uuid4().hex[:12]


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    role: Mapped[str] = mapped_column(String(32), default="user")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    target: Mapped[str] = mapped_column(String(512), nullable=False, index=True)
    name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(32), default="initialization", index=True)
    is_paused: Mapped[bool] = mapped_column(Boolean, default=False)
    phase: Mapped[str] = mapped_column(String(32), default="初始化", index=True)
    current_state_data: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, onupdate=_utcnow)
    steps: Mapped[List["ExecutionStep"]] = relationship(
        back_populates="session", cascade="all, delete-orphan", order_by="ExecutionStep.seq"
    )


class ExecutionStep(Base):
    __tablename__ = "execution_steps"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    session_id: Mapped[str] = mapped_column(
        String(32), ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    action_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    action_data: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)
    from_state: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    to_state: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    tool_name: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    tool_args: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)
    tool_success: Mapped[Optional[bool]] = mapped_column(Boolean, nullable=True)
    tool_output_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    tool_duration: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    llm_content: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utcnow, index=True)
    session: Mapped["Session"] = relationship(back_populates="steps")