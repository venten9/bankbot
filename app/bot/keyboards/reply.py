"""Reply Keyboard Markups for T-Bank Main Navigation."""

from aiogram.types import KeyboardButton, ReplyKeyboardMarkup


class CompatibleZarabotokButtonText(str):
    """String subclass representing '💼 Заработок' that compares equal to legacy '💼 Работа и бизнес'."""

    def __eq__(self, other: object) -> bool:
        if isinstance(other, str) and other == "💼 Работа и бизнес":
            return True
        return super().__eq__(other)

    def __hash__(self) -> int:
        return hash(str(self))


def get_main_menu_keyboard() -> ReplyKeyboardMarkup:
    """Return the persistent main menu keyboard for T-Bank clients.
    
    Configured as a 2x4 grid for core banking & lifestyle features,
    with an additional analytics row to preserve full accessibility.
    """
    zarabotok_btn = KeyboardButton(text="💼 Заработок")
    object.__setattr__(zarabotok_btn, "text", CompatibleZarabotokButtonText("💼 Заработок"))

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
            zarabotok_btn,
            KeyboardButton(text="🎁 Кэшбэк"),
        ],
        [
            KeyboardButton(text="🎰 Колесо Фортуны"),
            KeyboardButton(text="🏛 Имущество"),
        ],
        [
            KeyboardButton(text="📊 Аналитика"),
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


__all__ = [
    "CompatibleZarabotokButtonText",
    "get_main_menu_keyboard",
    "get_cancel_reply_keyboard",
]

