"""Reply Keyboard Markups for T-Bank Main Navigation."""

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup


def get_main_menu_keyboard() -> ReplyKeyboardMarkup:
    """Return the persistent main menu keyboard for T-Bank clients."""
    keyboard = [
        [
            KeyboardButton(text="💳 Моя карта"),
            KeyboardButton(text="💸 Переводы"),
        ],
        [
            KeyboardButton(text="💰 Сейф 18%"),
            KeyboardButton(text="📈 Инвестиции"),
        ],
        [
            KeyboardButton(text="🎁 Кэшбэк"),
            KeyboardButton(text="📊 Аналитика"),
        ],
        [
            KeyboardButton(text="💬 Олег"),
        ],
    ]
    return ReplyKeyboardMarkup(
        keyboard=keyboard,
        resize_keyboard=True,
        input_field_placeholder="Выберите операцию в меню Т-Банка",
    )


def get_cancel_reply_keyboard() -> ReplyKeyboardMarkup:
    """Return a simple cancel keyboard for active dialogue states."""
    keyboard = [
        [
            KeyboardButton(text="❌ Отмена"),
        ]
    ]
    return ReplyKeyboardMarkup(
        keyboard=keyboard,
        resize_keyboard=True,
        one_time_keyboard=True,
    )
