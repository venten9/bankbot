"""Admin Panel Inline Keyboards for RBAC Navigation, Cheats, Grants, Market, and Cleanup."""

from typing import Any
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def get_admin_dashboard_keyboard() -> InlineKeyboardMarkup:
    """Return root admin dashboard navigation keyboard."""
    buttons = [
        [
            InlineKeyboardButton(text="🔄 Обновить метрики", callback_data="admin:metrics:refresh"),
            InlineKeyboardButton(text="👤 Поиск юзеров", callback_data="admin:user:search"),
        ],
        [
            InlineKeyboardButton(text="📢 Создать рассылку", callback_data="admin:broadcast:start"),
            InlineKeyboardButton(text="📜 Журнал аудита", callback_data="admin:audit:log"),
        ],
        [
            InlineKeyboardButton(text="⚡️ Чит-панель", callback_data="admin:cheat:menu"),
            InlineKeyboardButton(text="📈 Регулятор биржи", callback_data="admin:market:menu"),
        ],
        [
            InlineKeyboardButton(text="🧹 Очистить тестовых юзеров", callback_data="admin:cleanup:test_users"),
        ],
        [
            InlineKeyboardButton(text="🚪 Выйти из админки", callback_data="admin:exit"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


get_admin_keyboard = get_admin_dashboard_keyboard


def get_admin_user_card_keyboard(user_id: int, is_frozen: bool) -> InlineKeyboardMarkup:
    """Return user inspection action buttons with dynamic freeze/unfreeze toggle and superpowers."""
    freeze_text = "☀️ Разморозить аккаунт" if is_frozen else "❄️ Заморозить аккаунт"
    freeze_action = f"admin:user:unfreeze:{user_id}" if is_frozen else f"admin:user:freeze:{user_id}"

    buttons = [
        [
            InlineKeyboardButton(text="➕ Начислить баланс", callback_data=f"admin:user:credit:{user_id}"),
            InlineKeyboardButton(text="➖ Списать баланс", callback_data=f"admin:user:debit:{user_id}"),
        ],
        [
            InlineKeyboardButton(text="⚡️ Чит-панель", callback_data=f"admin:cheat:user:{user_id}"),
            InlineKeyboardButton(text="🎁 Выдать активы", callback_data=f"admin:grant:user:{user_id}"),
        ],
        [
            InlineKeyboardButton(text=freeze_text, callback_data=freeze_action),
            InlineKeyboardButton(text="📜 Полная выписка", callback_data=f"admin:user:ledger:{user_id}"),
        ],
        [
            InlineKeyboardButton(text="🔙 Назад к поиску", callback_data="admin:user:search"),
            InlineKeyboardButton(text="🏢 В панель управления", callback_data="admin:dashboard"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_admin_cheat_keyboard(target_user_id: int) -> InlineKeyboardMarkup:
    """Return 1-click cheat resource and timer reset buttons."""
    buttons = [
        [
            InlineKeyboardButton(text="💵 +100 000 ₽", callback_data=f"admin:cheat:rub:{target_user_id}:100000"),
            InlineKeyboardButton(text="💰 +1 000 000 ₽", callback_data=f"admin:cheat:rub:{target_user_id}:1000000"),
        ],
        [
            InlineKeyboardButton(text="🏦 +100 000 000 ₽", callback_data=f"admin:cheat:rub:{target_user_id}:100000000"),
            InlineKeyboardButton(text="🎁 +50 000 кэшбэк", callback_data=f"admin:cheat:cb:{target_user_id}:50000"),
        ],
        [
            InlineKeyboardButton(text="⏳ Сбросить все кулдауны", callback_data=f"admin:cheat:reset_cd:{target_user_id}"),
        ],
        [
            InlineKeyboardButton(text="🎁 Выдать активы и акции", callback_data=f"admin:grant:user:{target_user_id}"),
        ],
        [
            InlineKeyboardButton(text="🔙 К карточке клиента", callback_data=f"admin:user:view:{target_user_id}"),
            InlineKeyboardButton(text="🏢 В панель управления", callback_data="admin:dashboard"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_admin_grant_keyboard(target_user_id: int) -> InlineKeyboardMarkup:
    """Return 1-click asset and stock grant buttons."""
    buttons = [
        [
            InlineKeyboardButton(text="👑 Золотой Майбах", callback_data=f"admin:grant:prop:{target_user_id}:maybach"),
            InlineKeyboardButton(text="⚡️ BMW M5", callback_data=f"admin:grant:prop:{target_user_id}:bmw_m5"),
        ],
        [
            InlineKeyboardButton(text="🏰 Рублевка", callback_data=f"admin:grant:prop:{target_user_id}:rublyovka"),
            InlineKeyboardButton(text="🏙 Пентхаус в Сити", callback_data=f"admin:grant:prop:{target_user_id}:city_penthouse"),
        ],
        [
            InlineKeyboardButton(text="🛥 Яхта «Кэшбэк»", callback_data=f"admin:grant:prop:{target_user_id}:yacht"),
        ],
        [
            InlineKeyboardButton(text="📈 +1000 SHAURMA", callback_data=f"admin:grant:stock:{target_user_id}:SHAURMA"),
            InlineKeyboardButton(text="📈 +1000 USPEKH", callback_data=f"admin:grant:stock:{target_user_id}:USPEKH"),
        ],
        [
            InlineKeyboardButton(text="📈 +1000 SPICHKI", callback_data=f"admin:grant:stock:{target_user_id}:SPICHKI"),
            InlineKeyboardButton(text="📈 +1000 NANO", callback_data=f"admin:grant:stock:{target_user_id}:NANO"),
        ],
        [
            InlineKeyboardButton(text="⚡️ В чит-панель", callback_data=f"admin:cheat:user:{target_user_id}"),
            InlineKeyboardButton(text="🏢 В панель управления", callback_data="admin:dashboard"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_admin_market_keyboard(stocks: list[Any] | None = None) -> InlineKeyboardMarkup:
    """Return stock market regulator buttons (pump, dump, crash, individual stocks)."""
    buttons = [
        [
            InlineKeyboardButton(text="🚀 Памп рынка (+50%)", callback_data="admin:market:pump"),
            InlineKeyboardButton(text="📉 Дамп рынка (-50%)", callback_data="admin:market:dump"),
        ],
        [
            InlineKeyboardButton(text="💥 Крах биржи (-80%)", callback_data="admin:market:crash"),
        ],
    ]
    if stocks:
        stock_row = []
        for stock in stocks:
            ticker = getattr(stock, "ticker", str(stock))
            stock_row.append(
                InlineKeyboardButton(
                    text=f"🎯 {ticker}",
                    callback_data=f"admin:market:stock:{ticker}",
                )
            )
            if len(stock_row) == 2:
                buttons.append(stock_row)
                stock_row = []
        if stock_row:
            buttons.append(stock_row)
    else:
        buttons.append([
            InlineKeyboardButton(text="🎯 SHAURMA", callback_data="admin:market:stock:SHAURMA"),
            InlineKeyboardButton(text="🎯 USPEKH", callback_data="admin:market:stock:USPEKH"),
        ])
        buttons.append([
            InlineKeyboardButton(text="🎯 SPICHKI", callback_data="admin:market:stock:SPICHKI"),
            InlineKeyboardButton(text="🎯 NANO", callback_data="admin:market:stock:NANO"),
        ])

    buttons.append([
        InlineKeyboardButton(text="🔄 Обновить котировки", callback_data="admin:market:menu"),
        InlineKeyboardButton(text="🏢 В панель управления", callback_data="admin:dashboard"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_admin_stock_detail_keyboard(ticker: str, current_price: Any = None) -> InlineKeyboardMarkup:
    """Return manipulation controls for a specific stock ticker."""
    buttons = [
        [
            InlineKeyboardButton(text="🚀 Памп (+25%)", callback_data=f"admin:market:stock:{ticker}:pump"),
            InlineKeyboardButton(text="📉 Дамп (-25%)", callback_data=f"admin:market:stock:{ticker}:dump"),
        ],
        [
            InlineKeyboardButton(text="🎯 10 ₽", callback_data=f"admin:market:stock:{ticker}:p:10"),
            InlineKeyboardButton(text="🎯 50 ₽", callback_data=f"admin:market:stock:{ticker}:p:50"),
        ],
        [
            InlineKeyboardButton(text="🎯 100 ₽", callback_data=f"admin:market:stock:{ticker}:p:100"),
            InlineKeyboardButton(text="🎯 500 ₽", callback_data=f"admin:market:stock:{ticker}:p:500"),
        ],
        [
            InlineKeyboardButton(text="« К регулятору биржи", callback_data="admin:market:menu"),
            InlineKeyboardButton(text="🏢 В панель управления", callback_data="admin:dashboard"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_admin_cleanup_confirm_keyboard() -> InlineKeyboardMarkup:
    """Return confirmation buttons for purging test user accounts."""
    buttons = [
        [
            InlineKeyboardButton(text="⚠️ Подтвердить удаление", callback_data="admin:cleanup:confirm"),
        ],
        [
            InlineKeyboardButton(text="❌ Отмена", callback_data="admin:dashboard"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_admin_adjustment_confirm_keyboard(target_user_id: int) -> InlineKeyboardMarkup:
    """Return confirmation keyboard for balance adjustment."""
    buttons = [
        [
            InlineKeyboardButton(text="✅ Подтвердить операцию", callback_data="admin:adjust:confirm"),
        ],
        [
            InlineKeyboardButton(text="❌ Отмена", callback_data=f"admin:user:view:{target_user_id}"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_admin_broadcast_confirm_keyboard() -> InlineKeyboardMarkup:
    """Return confirmation inline buttons for broadcast dispatch."""
    buttons = [
        [
            InlineKeyboardButton(text="🚀 Начать рассылку", callback_data="admin:broadcast:confirm"),
        ],
        [
            InlineKeyboardButton(text="❌ Отменить", callback_data="admin:broadcast:cancel"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_admin_ledger_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """Return navigation keyboard for ledger statement view."""
    buttons = [
        [
            InlineKeyboardButton(text="🔙 К карточке клиента", callback_data=f"admin:user:view:{user_id}"),
            InlineKeyboardButton(text="🏢 В панель управления", callback_data="admin:dashboard"),
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_admin_cancel_keyboard(target_user_id: int | None = None) -> InlineKeyboardMarkup:
    """Return cancel button returning to user inspection or admin dashboard."""
    callback_data = f"admin:user:view:{target_user_id}" if target_user_id else "admin:dashboard"
    buttons = [
        [
            InlineKeyboardButton(text="❌ Отмена", callback_data=callback_data),
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_admin_back_keyboard() -> InlineKeyboardMarkup:
    """Return standard back to dashboard button."""
    buttons = [
        [
            InlineKeyboardButton(text="🏢 В панель управления", callback_data="admin:dashboard"),
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)
