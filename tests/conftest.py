from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, Optional

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from db.database import Base
from db.models import ExecutionStep, Session, User

PROJECT_ROOT = Path(__file__).resolve().parent.parent
_IN_MEMORY_URL = "sqlite+aiosqlite:///:memory:"


def _override_db_to_memory():
    from db import database as db_module
    db_module._db_instance = None
    db_instance = db_module.Database(_IN_MEMORY_URL)
    db_module._db_instance = db_instance
    return db_instance


@pytest_asyncio.fixture(scope="function")
async def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    await loop.shutdown_asyncgens()
    loop.close()


@pytest_asyncio.fixture(scope="function")
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    engine = create_async_engine(
        _IN_MEMORY_URL,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )
    async with session_factory() as session:
        yield session

    await engine.dispose()


@pytest_asyncio.fixture(scope="function")
async def app():
    _override_db_to_memory()
    db = __import__("db.database", fromlist=["get_database"]).get_database()
    await db.init_models()

    from core.auth import hash_password
    from sqlalchemy import select
    async with db.get_session() as session:
        result = await session.execute(select(User).where(User.username == "admin"))
        if result.scalar_one_or_none() is None:
            admin = User(
                username="admin",
                password_hash=hash_password("pentest123"),
                role="admin",
            )
            session.add(admin)
            await session.commit()

    from api.app import app as _app
    return _app


@pytest_asyncio.fixture(scope="function")
async def async_client(app) -> AsyncGenerator[httpx.AsyncClient, None]:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        yield client


@pytest_asyncio.fixture(scope="function")
async def auth_headers(async_client: httpx.AsyncClient) -> Dict[str, str]:
    resp = await async_client.post("/api/auth/login", json={
        "username": "admin",
        "password": "pentest123",
    })
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest_asyncio.fixture(scope="function")
async def sample_session(db_session: AsyncSession) -> Session:
    s = Session(target="192.168.1.1", status="active")
    db_session.add(s)
    await db_session.commit()
    await db_session.refresh(s)
    return s