from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from db.models import ExecutionStep, Session, User


@pytest.mark.unit
async def test_create_user(db_session: AsyncSession):
    user = User(username="testuser", password_hash="hashed_password", role="user")
    db_session.add(user)
    await db_session.commit()
    await db_session.refresh(user)

    assert user.id is not None
    assert user.username == "testuser"
    assert user.password_hash == "hashed_password"
    assert user.role == "user"
    assert user.is_active is True
    assert user.created_at is not None
    assert user.updated_at is not None


@pytest.mark.unit
async def test_create_session(db_session: AsyncSession):
    s = Session(target="10.0.0.1", status="initialization")
    db_session.add(s)
    await db_session.commit()
    await db_session.refresh(s)

    assert s.id is not None
    assert s.target == "10.0.0.1"
    assert s.status == "initialization"
    assert s.is_paused is False
    assert s.phase == "初始化"
    assert s.created_at is not None


@pytest.mark.unit
async def test_session_steps_relationship(db_session: AsyncSession):
    s = Session(target="192.168.1.1")
    db_session.add(s)
    await db_session.flush()

    step1 = ExecutionStep(session_id=s.id, seq=1, action_type="scan")
    step2 = ExecutionStep(session_id=s.id, seq=2, action_type="exploit")
    db_session.add_all([step1, step2])
    await db_session.commit()

    result = await db_session.execute(
        select(Session).where(Session.id == s.id).options(selectinload(Session.steps))
    )
    session = result.scalar_one()
    assert len(session.steps) == 2
    assert session.steps[0].action_type == "scan"
    assert session.steps[1].action_type == "exploit"
    assert session.steps[0].session is session


@pytest.mark.unit
async def test_user_unique_username(db_session: AsyncSession):
    user1 = User(username="unique_user", password_hash="hash1")
    db_session.add(user1)
    await db_session.commit()

    user2 = User(username="unique_user", password_hash="hash2")
    db_session.add(user2)
    with pytest.raises(Exception):
        await db_session.commit()