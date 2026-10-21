"""Inline Keyboard Markups for T-Property & Luxury UI."""

from typing import Iterable
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.services.property import PropertyItem


def get_property_menu_keyboard(has_real_estate: bool = False) -> InlineKeyboardMarkup:
    """Return main T-Property menu keyboard."""
    buttons: list[list[InlineKeyboardButton]] = [
        [
            InlineKeyboardButton(text="🚗 Автопарк", callback_data="prop:cat:CAR"),
            InlineKeyboardButton(text="🏢 Недвижимость", callback_data="prop:cat:REAL_ESTATE"),
        ],
        [
            InlineKeyboardButton(text="💎 Коллекции", callback_data="prop:cat:LUXURY"),
            InlineKeyboardButton(text="🎒 Мое имущество", callback_data="prop:my"),
        ],
    ]

    if has_real_estate:
        buttons.append([
            InlineKeyboardButton(text="💰 Собрать аренду", callback_data="prop:rent:claim"),
        ])

    buttons.append([
        InlineKeyboardButton(text="🔄 Обновить", callback_data="prop:menu"),
        InlineKeyboardButton(text="🔙 В главное меню", callback_data="main_menu"),
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_property_category_keyboard(
    category: str,
    items: list[PropertyItem],
    owned_ids: Iterable[str] | None = None,
) -> InlineKeyboardMarkup:
    """Return catalog listing keyboard for a property category."""
    owned_set = set(owned_ids or [])
    buttons: list[list[InlineKeyboardButton]] = []

    for item in items:
        if item.item_id in owned_set:
            buttons.append([
                InlineKeyboardButton(
                    text=f"✅ {item.icon} {item.name} (Куплено)",
                    callback_data="prop:my",
                )
            ])
        else:
            # Compact direct buy callback: prop:buy:<item_id> (<= 64 bytes)
            price_str = f"{item.price:,.0f} ₽".replace(",", " ")
            buttons.append([
                InlineKeyboardButton(
                    text=f"🛒 Купить {item.icon} ({price_str})",
                    callback_data=f"prop:buy:{item.item_id}",
                )
            ])

    if category == "REAL_ESTATE" and any(it.item_id in owned_set for it in items):
        buttons.append([
            InlineKeyboardButton(text="💰 Собрать аренду", callback_data="prop:rent:claim"),
        ])

    buttons.append([
        InlineKeyboardButton(text="« К категориям", callback_data="prop:menu"),
        InlineKeyboardButton(text="🎒 Мое имущество", callback_data="prop:my"),
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_my_properties_keyboard(has_real_estate: bool = False) -> InlineKeyboardMarkup:
    """Return navigation keyboard for user's owned assets summary."""
    buttons: list[list[InlineKeyboardButton]] = []

    if has_real_estate:
        buttons.append([
            InlineKeyboardButton(text="💰 Собрать аренду", callback_data="prop:rent:claim"),
        ])

    buttons.append([
        InlineKeyboardButton(text="🛍 Каталог имущества", callback_data="prop:menu"),
        InlineKeyboardButton(text="🔙 В главное меню", callback_data="main_menu"),
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_property_item_keyboard(
    item_id: str,
    category: str,
    is_owned: bool = False,
) -> InlineKeyboardMarkup:
    """Return action keyboard for an individual property item."""
    buttons: list[list[InlineKeyboardButton]] = []
    if is_owned:
        buttons.append([
            InlineKeyboardButton(text="✅ В вашей собственности", callback_data="prop:my")
        ])
    else:
        buttons.append([
            InlineKeyboardButton(text="🛒 Купить", callback_data=f"prop:buy:{item_id}")
        ])

    buttons.append([
        InlineKeyboardButton(text="« Назад в каталог", callback_data=f"prop:cat:{category}"),
        InlineKeyboardButton(text="« Меню имущества", callback_data="prop:menu"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)
