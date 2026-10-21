"""Admin Panel Inline Keyboards for RBAC Navigation and Management."""

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
            InlineKeyboardButton(text="🚪 Выйти из админки", callback_data="admin:exit"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_admin_user_card_keyboard(user_id: int, is_frozen: bool) -> InlineKeyboardMarkup:
    """Return user inspection action buttons with dynamic freeze/unfreeze toggle."""
    freeze_text = "☀️ Разморозить аккаунт" if is_frozen else "❄️ Заморозить аккаунт"
    freeze_action = f"admin:user:unfreeze:{user_id}" if is_frozen else f"admin:user:freeze:{user_id}"

    buttons = [
        [
            InlineKeyboardButton(text="➕ Начислить баланс", callback_data=f"admin:user:credit:{user_id}"),
            InlineKeyboardButton(text="➖ Списать баланс", callback_data=f"admin:user:debit:{user_id}"),
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
