"""Routers package exports."""

from app.bot.routers.card import card_router
from app.bot.routers.cashback import cashback_router
from app.bot.routers.common import common_router
from app.bot.routers.invest import invest_router
from app.bot.routers.savings import savings_router
from app.bot.routers.transfer import transfer_router

__all__ = [
    "card_router",
    "cashback_router",
    "common_router",
    "invest_router",
    "savings_router",
    "transfer_router",
]
