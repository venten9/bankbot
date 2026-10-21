"""Async Database Engine, Session Maker, and Connection Lifecycle."""

from contextlib import asynccontextmanager
from typing import AsyncGenerator
from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.config import settings
from app.database.models import Base


def setup_sqlite_pragmas(target_engine: AsyncEngine) -> None:
    """Attach SQLite WAL mode, foreign keys, and busy timeout pragmas to the engine."""
    @event.listens_for(target_engine.sync_engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA foreign_keys=ON;")
        cursor.execute("PRAGMA busy_timeout=5000;")
        cursor.close()


# Default application async engine
engine: AsyncEngine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,
    connect_args={"check_same_thread": False},
)

# Attach PRAGMAs to default engine
setup_sqlite_pragmas(engine)

# Async session factory
async_session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


async def init_db(engine_override: AsyncEngine | None = None) -> None:
    """Initialize database schemas, creating all tables if not already present."""
    active_engine = engine_override or engine
    async with active_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


@asynccontextmanager
async def get_db_session(
    session_pool: async_sessionmaker[AsyncSession] | None = None,
) -> AsyncGenerator[AsyncSession, None]:
    """Context manager providing an AsyncSession with auto-commit and rollback on exception."""
    pool = session_pool or async_session_factory
    async with pool() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Generator-based session provider for dependencies."""
    async with get_db_session() as session:
        yield session
