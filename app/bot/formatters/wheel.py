"""T-Bank Daily Fortune Wheel Visual HTML Formatters for Telegram UI."""

import html
from app.database.models import User
from app.services.wheel import EXTRA_SPIN_CASHBACK_COST, WHEEL_SECTORS, WheelResult


def format_countdown(seconds: int | float) -> str:
    """Format duration in seconds into HH:MM:SS representation."""
    secs_int = max(0, int(seconds))
    hours = secs_int // 3600
    minutes = (secs_int % 3600) // 60
    secs = secs_int % 60
    return f"{hours:02d}:{minutes:02d}:{secs:02d}"


def format_wheel_dashboard(
    user: User,
    can_spin: bool,
    seconds_remaining: int = 0,
) -> str:
    """Format Fortune Wheel dashboard card for Telegram HTML."""
    user_display = html.escape(user.full_name or user.username or f"Клиент #{user.id}", quote=False)
    cashback_bal_str = f"{user.cashback_balance:,.2f} ₽".replace(",", " ")
    extra_cost_str = f"{EXTRA_SPIN_CASHBACK_COST:,.0f} ₽".replace(",", " ")

    lines = [
        "🎰 <b>Колесо Фортуны Т-Банка</b>",
        "━━━━━━━━━━━━━━━━━━━━━━━━",
        f"👤 Игрок: <b>{user_display}</b>",
        f"🎁 Баланс кэшбэка: <b>{cashback_bal_str}</b>\n",
    ]

    if can_spin:
        lines.append("🟢 <b>Бесплатный ежедневный спин: ДОСТУПЕН!</b>")
        lines.append("<i>Испытайте удачу прямо сейчас без списания баллов!</i>\n")
    else:
        cd_str = format_countdown(seconds_remaining)
        lines.append(f"⏳ <b>Бесплатный спин через:</b> <code>{cd_str}</code>")
        lines.append(f"<i>Или приобретите дополнительный спин за {extra_cost_str} кэшбэка.</i>\n")

    lines.extend([
        "<b>Секторы и призовой фонд:</b>",
        "• 💵 <b>5 000 ₽</b> — 25% (на карту)",
        "• 💰 <b>25 000 ₽</b> — 15% (на карту)",
        "• 💎 <b>100 000 ₽ Джекпот</b> — 5% (на карту)",
        "• 🌯 <b>10 акций</b> ПАО «Шаурма на вокзале» — 15%",
        "• 📈 <b>5 акций</b> ООО «Успешный успех» — 15%",
        "• ⚡️ <b>Энергетик Т-Драйв</b> — 10% (сброс таймера смены)",
        "• 🎁 <b>+2 000 ₽ кэшбэка</b> — 10% (в копилку кэшбэка)",
        "• 🏦 <b>Налог на роскошь</b> — 5% (комиссия 500 ₽ или 0 ₽)",
        "\n<i>Нажмите кнопку ниже, чтобы запустить вращение!</i>",
    ])

    return "\n".join(lines)


def format_wheel_spin_result(
    result: WheelResult,
    is_extra: bool = False,
) -> str:
    """Format result announcement after spinning Fortune Wheel."""
    spin_type_badge = "⚡️ [Платный спин]" if is_extra else "🎰 [Ежедневный спин]"
    bal_str = f"{result.balance:,.2f} ₽".replace(",", " ")
    cashback_bal_str = f"{result.cashback_balance:,.2f} ₽".replace(",", " ")

    lines = [
        f"🎡 <b>Колесо Фортуны остановилось!</b> {spin_type_badge}",
        "━━━━━━━━━━━━━━━━━━━━━━━━",
        f"🎯 Выпавший сектор: <b>{html.escape(result.title, quote=False)}</b>\n",
        result.description,
        "\n━━━━━━━━━━━━━━━━━━━━━━━━",
        f"💳 Баланс карты: <b>{bal_str}</b>",
        f"🎁 Баланс кэшбэка: <b>{cashback_bal_str}</b>",
    ]

    return "\n".join(lines)
