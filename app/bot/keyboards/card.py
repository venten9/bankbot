"""Virtual Debit Card Keyboards."""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def get_card_keyboard(is_frozen: bool, details_visible: bool) -> InlineKeyboardMarkup:
    """Return inline actions for card management (CVV toggle, freeze toggle, change number)."""
    details_text = "🙈 Скрыть реквизиты" if details_visible else "👁 Показать реквизиты"
    freeze_text = "🔥 Разморозить карту" if is_frozen else "❄️ Заморозить карту"

    buttons = [
        [InlineKeyboardButton(text=details_text, callback_data="card:details:toggle")],
        [InlineKeyboardButton(text=freeze_text, callback_data="card:freeze:toggle")],
        [InlineKeyboardButton(text="✏️ Изменить номер карты", callback_data="card:change_number")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_card_cancel_keyboard() -> InlineKeyboardMarkup:
    """Return cancel inline keyboard during card number customization."""
    buttons = [
        [InlineKeyboardButton(text="❌ Отмена", callback_data="card:change:cancel")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


__all__ = [
    "get_card_keyboard",
    "get_card_cancel_keyboard",
]
