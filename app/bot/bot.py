"""Bot and Dispatcher Factory Configuration."""

from aiogram import Bot, Dispatcher, Router
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.middlewares.db import DbSessionMiddleware
from app.bot.middlewares.user import CurrentUserMiddleware
from app.bot.routers.card import card_router
from app.bot.routers.cashback import cashback_router
from app.bot.routers.common import common_router
from app.bot.routers.invest import invest_router
from app.bot.routers.savings import savings_router
from app.bot.routers.transfer import transfer_router
from app.config import settings
from app.database.session import async_session_factory


def detach_router(router: Router) -> None:
    """Safely detach router from any previous parent dispatcher or router."""
    parent = getattr(router, "_parent_router", None)
    if parent is not None:
        try:
            if router in parent.sub_routers:
                parent.sub_routers.remove(router)
        except (ValueError, AttributeError):
            pass
        router._parent_router = None


def create_dispatcher(
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    storage: MemoryStorage | None = None,
) -> Dispatcher:
    """Create and configure aiogram Dispatcher with outer middlewares and domain routers."""
    fsm_storage = storage or MemoryStorage()
    dp = Dispatcher(storage=fsm_storage)

    # 1. Register outer middlewares (lifecycle before filters)
    pool = session_factory or async_session_factory
    dp.update.outer_middleware(DbSessionMiddleware(pool))
    dp.update.outer_middleware(CurrentUserMiddleware())

    # 2. Collect domain routers in strict priority order
    routers: list[Router] = []
    try:
        from app.bot.routers.admin import admin_router
        routers.append(admin_router)
    except ImportError:
        pass

    routers.extend([
        common_router,
        card_router,
        transfer_router,
        savings_router,
        invest_router,
        cashback_router,
    ])

    # 3. Detach and attach routers idempotently
    for router in routers:
        detach_router(router)
        dp.include_router(router)

    return dp


def create_bot(token: str | None = None) -> Bot:
    """Create aiogram Bot instance with HTML parse mode."""
    bot_token = token or settings.BOT_TOKEN
    if not bot_token or ":" not in bot_token or not bot_token.partition(":")[0].isdigit():
        bot_token = "123456789:AAG_dummy_test_token_tbank_mock"
    return Bot(
        token=bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )

