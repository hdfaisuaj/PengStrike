"""
数据库引擎层 (db/database.py)

职责:
- 使用 SQLAlchemy 2.0 AsyncEngine 同时兼容 SQLite 和 PostgreSQL
- 提供统一的 async session 工厂
- 自动建表 (init_models)
- SQLite 启用 WAL 模式避免 database is locked
"""

from __future__ import annotations

from pathlib import Path
from typing import AsyncGenerator, Optional

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase


# ========================================================================
# 全局 Base
# ========================================================================
class Base(DeclarativeBase):
    pass


# ========================================================================
# 数据库引擎封装
# ========================================================================
class Database:
    """数据库连接封装。

    使用示例:
        db = Database("sqlite+aiosqlite:///./pentest.db")
        await db.init_models()
        async with db.session() as session:
            ...
    """

    def __init__(self, db_url: str, echo: bool = False) -> None:
        self.db_url = db_url
        self.echo = echo

        # 根据 URL 选择驱动和连接参数
        connect_args: dict = {}
        if "postgresql" in db_url:
            # PostgreSQL 连接池配置
            self.engine = create_async_engine(
                db_url,
                future=True,
                echo=echo,
                pool_pre_ping=True,       # 连接前检查连接有效性
                pool_recycle=3600,        # 1小时后回收连接
                pool_size=20,             # 连接池大小
                max_overflow=30,          # 最大溢出连接数
            )
        else:
            # SQLite 连接池配置（适配SQLite场景）
            connect_args["check_same_thread"] = False
            self.engine = create_async_engine(
                db_url,
                echo=echo,
                connect_args=connect_args,
                pool_pre_ping=True,       # 连接前检查连接有效性
                pool_recycle=1800,        # 30分钟后回收连接（SQLite不宜过长）
                pool_size=5,              # SQLite不宜过大连接池
                max_overflow=10,          # 适度溢出
            )

        self._sessionmaker = async_sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

    async def init_models(self) -> None:
        """创建所有表 (Base.metadata.tables) + 兼容性迁移。"""
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        # SQLite 启用 WAL 模式避免并发写入锁定
        if "sqlite" in self.db_url:
            async with self.engine.connect() as conn:
                await conn.execute(
                    __import__("sqlalchemy").text("PRAGMA journal_mode=WAL;")
                )
                await conn.commit()

        # ★ 兼容性迁移：检查并添加缺失的列
        if "sqlite" in self.db_url:
            try:
                async with self.engine.connect() as conn:
                    # 检查 sessions 表是否有 name 列
                    result = await conn.execute(
                        __import__("sqlalchemy").text(
                            "PRAGMA table_info(sessions)"
                        )
                    )
                    cols = {row[1] for row in result.fetchall()}
                    if "name" not in cols:
                        await conn.execute(
                            __import__("sqlalchemy").text(
                                "ALTER TABLE sessions ADD COLUMN name VARCHAR(128)"
                            )
                        )
                        await conn.commit()
                        import logging
                        logging.getLogger(__name__).info("数据库迁移: 添加 sessions.name 列")
            except Exception as mig_exc:
                import logging
                logging.getLogger(__name__).warning("数据库迁移异常（可忽略）: %s", mig_exc)

    async def session(self) -> AsyncGenerator[AsyncSession, None]:
        """获取异步 session (用于 async with / 依赖注入)。"""
        async with self._sessionmaker() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise
            finally:
                await session.close()

    def get_session(self) -> AsyncSession:
        """直接创建 session 实例 (用于 StateManager 手动管理)。"""
        return self._sessionmaker()

    async def close(self) -> None:
        """关闭引擎释放连接池。"""
        await self.engine.dispose()


# ========================================================================
# 单例模式 (懒加载)
# ========================================================================
_db_instance: Optional[Database] = None


def get_database(db_url: Optional[str] = None) -> Database:
    """全局数据库单例。

    默认使用 SQLite: ~/.pengstrike/pentest.db
    """
    global _db_instance
    if _db_instance is None:
        if db_url is None:
            # 默认存到 ~/.pengstrike/
            data_dir = Path.home() / ".pengstrike"
            data_dir.mkdir(parents=True, exist_ok=True)
            db_path = data_dir / "pentest.db"
            db_url = f"sqlite+aiosqlite:///{db_path.as_posix()}"
        _db_instance = Database(db_url)
    return _db_instance