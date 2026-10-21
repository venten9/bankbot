"""Database Session Middleware for aiogram 3."""

from typing import Any, Awaitable, Callable, Dict
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.session import async_session_factory


class DbSessionMiddleware(BaseMiddleware):
    """Inject an active AsyncSession into handler kwargs with automatic commit/rollback."""

    def __init__(self, session_pool: async_sessionmaker[AsyncSession] | None = None):
        super().__init__()
        self.session_pool = session_pool or async_session_factory

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        # If session is already injected (e.g. in test fixture or outer wrapper)
        if "session" in data and data["session"] is not None:
            return await handler(event, data)

        async with self.session_pool() as session:
            data["session"] = session
            try:
                result = await handler(event, data)
                await session.commit()
                return result
            except Exception:
                await session.rollback()
                raise
