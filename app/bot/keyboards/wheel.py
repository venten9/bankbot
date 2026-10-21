"""Inline Keyboard Markups for T-Bank Daily Fortune Wheel UI."""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def get_wheel_keyboard(
    can_free_spin: bool = True,
    remaining_seconds: int = 0,
) -> InlineKeyboardMarkup:
    """Return Fortune Wheel action keyboard."""
    buttons: list[list[InlineKeyboardButton]] = []

    if can_free_spin:
        buttons.append([
            InlineKeyboardButton(
                text="🎰 Крутить бесплатно!",
                callback_data="wheel:spin",
            )
        ])
    else:
        hours = remaining_seconds // 3600
        mins = (remaining_seconds % 3600) // 60
        time_str = f"{hours}ч {mins}м" if hours > 0 else f"{mins}м"
        buttons.append([
            InlineKeyboardButton(
                text=f"⏳ Бесплатный спин через {time_str}",
                callback_data="wheel:spin",
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            text="⚡️ Купить спин за 1 000 баллов",
            callback_data="wheel:spin_extra",
        )
    ])

    buttons.append([
        InlineKeyboardButton(text="🔄 Обновить", callback_data="wheel:menu"),
        InlineKeyboardButton(text="🔙 В главное меню", callback_data="main_menu"),
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_wheel_result_keyboard(can_free_spin: bool = False) -> InlineKeyboardMarkup:
    """Return navigation keyboard after spinning the Fortune Wheel."""
    buttons: list[list[InlineKeyboardButton]] = []

    if can_free_spin:
        buttons.append([
            InlineKeyboardButton(text="🎰 Крутить бесплатно!", callback_data="wheel:spin")
        ])

    buttons.append([
        InlineKeyboardButton(
            text="⚡️ Еще спин за 1 000 баллов",
            callback_data="wheel:spin_extra",
        )
    ])

    buttons.append([
        InlineKeyboardButton(text="« К Колесу Фортуны", callback_data="wheel:menu"),
        InlineKeyboardButton(text="🔙 В главное меню", callback_data="main_menu"),
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)
