"""Main Application Entry Point: Database Initialization and Async Polling."""

import asyncio
import logging
import sys

from app.bot.bot import create_bot, create_dispatcher
from app.config import settings
from app.database.session import async_session_factory, init_db
from app.services.cashback import CashbackService
from app.services.investments import InvestmentService


async def main() -> None:
    """Initialize database, seed financial catalogs, and begin Telegram bot polling."""
    logging.basicConfig(
        level=logging.DEBUG if settings.DEBUG else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    logger = logging.getLogger("tbank")
    logger.info("Initializing T-Bank Neobank Telegram Bot...")

    # 1. Initialize SQLite WAL database schemas
    await init_db()
    logger.info("Database tables verified.")

    # 2. Seed parody stocks and cashback categories
    async with async_session_factory() as session:
        await InvestmentService.init_stocks(session)
        await CashbackService.init_cashback_categories(session)
        await session.commit()
    logger.info("Market stocks and cashback categories initialized.")

    # 3. Create Bot and Dispatcher
    bot = create_bot()
    dp = create_dispatcher()

    logger.info("Dispatcher configured. Commencing async polling loop...")
    try:
        await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())
    finally:
        await bot.session.close()
        logger.info("Bot session cleanly shut down.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logging.getLogger("tbank").info("Process terminated by user.")
