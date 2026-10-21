"""Inline Keyboards for Interactive T-Bank Operations."""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from app.database.models import CashbackCategory, Stock


def get_card_keyboard(is_frozen: bool, details_visible: bool) -> InlineKeyboardMarkup:
    """Return inline actions for card management (CVV toggle, freeze toggle)."""
    details_text = "🙈 Скрыть реквизиты" if details_visible else "👁 Показать реквизиты"
    freeze_text = "🔥 Разморозить карту" if is_frozen else "❄️ Заморозить карту"

    buttons = [
        [InlineKeyboardButton(text=details_text, callback_data="card:details:toggle")],
        [InlineKeyboardButton(text=freeze_text, callback_data="card:freeze:toggle")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_transfer_confirm_keyboard() -> InlineKeyboardMarkup:
    """Return confirmation inline buttons for outgoing transfer."""
    buttons = [
        [
            InlineKeyboardButton(text="✅ Подтвердить перевод", callback_data="trf:confirm"),
            InlineKeyboardButton(text="❌ Отмена", callback_data="trf:cancel"),
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_savings_keyboard() -> InlineKeyboardMarkup:
    """Return quick actions for 18% APR savings safe."""
    buttons = [
        [
            InlineKeyboardButton(text="📥 Пополнить сейф", callback_data="savings:deposit"),
            InlineKeyboardButton(text="📤 Снять со счета", callback_data="savings:withdraw"),
        ],
        [
            InlineKeyboardButton(text="💰 Выплатить проценты", callback_data="savings:payout"),
        ],
        [
            InlineKeyboardButton(text="🔄 Обновить", callback_data="savings:refresh"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_invest_catalog_keyboard(stocks: list[Stock]) -> InlineKeyboardMarkup:
    """Return parody stock market catalog with real-time price buttons."""
    buttons = []
    # 2 stocks per row
    row = []
    for stock in stocks:
        btn_text = f"{stock.ticker} · {stock.current_price:.2f} ₽"
        row.append(InlineKeyboardButton(text=btn_text, callback_data=f"stock:view:{stock.ticker}"))
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    buttons.append([
        InlineKeyboardButton(text="💼 Мой портфель", callback_data="invest:portfolio"),
        InlineKeyboardButton(text="🔄 Обновить цены", callback_data="invest:refresh"),
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_stock_detail_keyboard(ticker: str) -> InlineKeyboardMarkup:
    """Return trading actions for an inspected stock."""
    buttons = [
        [
            InlineKeyboardButton(text="🛒 Купить", callback_data=f"stock:buy:{ticker}"),
            InlineKeyboardButton(text="💰 Продать", callback_data=f"stock:sell:{ticker}"),
        ],
        [
            InlineKeyboardButton(text="« К списку акций", callback_data="invest:catalog"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_trade_confirm_keyboard(action: str, ticker: str, shares: int) -> InlineKeyboardMarkup:
    """Return confirmation buttons for buying or selling shares."""
    action_text = "Купить" if action == "BUY" else "Продать"
    buttons = [
        [
            InlineKeyboardButton(
                text=f"✅ {action_text} {shares} шт.",
                callback_data=f"invest:confirm:{action}:{ticker}:{shares}",
            ),
            InlineKeyboardButton(text="❌ Отмена", callback_data="invest:cancel"),
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_cashback_selection_keyboard(
    categories: list[CashbackCategory], selected_ids: set[int]
) -> InlineKeyboardMarkup:
    """Return monthly 4-of-8 category toggle checkboxes."""
    buttons = []
    for cat in categories:
        is_checked = cat.id in selected_ids
        checkbox = "✅" if is_checked else "⬜"
        btn_text = f"{checkbox} {cat.name} ({cat.percent}%)"
        buttons.append([
            InlineKeyboardButton(text=btn_text, callback_data=f"cb:toggle:{cat.id}")
        ])

    buttons.append([
        InlineKeyboardButton(
            text=f"✨ Подтвердить выбор ({len(selected_ids)}/4)",
            callback_data="cb:confirm",
        )
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_cashback_menu_keyboard(has_selected: bool) -> InlineKeyboardMarkup:
    """Return main cashback menu actions."""
    buttons = [
        [InlineKeyboardButton(text="💰 Вывести кэшбэк на карту", callback_data="cb:payout")]
    ]
    if not has_selected:
        buttons.append([
            InlineKeyboardButton(text="🎁 Выбрать категории на месяц", callback_data="cb:select_prompt")
        ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


def get_cancel_inline_keyboard(action_tag: str = "cancel") -> InlineKeyboardMarkup:
    """Return generic cancel inline button."""
    buttons = [
        [InlineKeyboardButton(text="❌ Отмена", callback_data=f"action:{action_tag}")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=buttons)
