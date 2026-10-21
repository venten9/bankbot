"""Current User Resolution Middleware for aiogram 3."""

from typing import Any, Awaitable, Callable, Dict
from aiogram import BaseMiddleware
from aiogram.types import TelegramObject
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.banking import BankingService


class CurrentUserMiddleware(BaseMiddleware):
    """Ensure Telegram user is registered in T-Bank database and inject into handler kwargs."""

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        tg_user = data.get("event_from_user")
        if not tg_user and hasattr(event, "from_user"):
            tg_user = getattr(event, "from_user")

        session: AsyncSession | None = data.get("session")

        if tg_user and session:
            # Check if user already provided in data
            if "user" not in data or data["user"] is None:
                full_name = tg_user.full_name or "Т-Банк Клиент"
                user, main_acc, card = await BankingService.get_or_create_user(
                    session=session,
                    telegram_id=tg_user.id,
                    username=tg_user.username,
                    full_name=full_name,
                )
                data["user"] = user
                data["current_user"] = user
                data["main_account"] = main_acc
                data["card"] = card
            elif "current_user" not in data:
                data["current_user"] = data["user"]

        return await handler(event, data)
