"""T-Property & Luxury Visual HTML Formatters for Telegram UI."""

import datetime
import html
from decimal import Decimal
from typing import Iterable

from app.database.models import User, UserProperty
from app.services.property import PROPERTY_CATALOG, PropertyItem, PropertyService


def _format_time_remaining(seconds: int | float) -> str:
    """Format duration in seconds into friendly Russian string."""
    secs_int = max(0, int(seconds))
    if secs_int == 0:
        return "Готово к сбору!"
    hours = secs_int // 3600
    minutes = (secs_int % 3600) // 60
    if hours > 0:
        return f"{hours} ч {minutes} мин"
    return f"{minutes} мин"


def format_property_overview(
    user: User,
    owned: list[UserProperty],
    rent_ready_sec: int = 0,
    car_buffs: tuple[int, int] = (0, 0),
) -> str:
    """Format main property dashboard card for Telegram HTML."""
    user_display = html.escape(user.full_name or user.username or f"Клиент #{user.id}", quote=False)
    total_val = sum((p.purchase_price for p in owned), Decimal("0.00"))
    total_val_str = f"{total_val:,.2f} ₽".replace(",", " ")

    lines = [
        "🏛 <b>Т-Банк Имущество & Роскошь</b>",
        "━━━━━━━━━━━━━━━━━━━━━━━━",
        f"👤 Владелец: <b>{user_display}</b>",
        f"📦 Всего объектов в собственности: <b>{len(owned)}</b>",
        f"💼 Общая стоимость активов: <b>{total_val_str}</b>\n",
    ]

    salary_bonus, cooldown_red = car_buffs
    if salary_bonus > 0 or cooldown_red > 0:
        lines.append(
            f"🏎 <b>Активные авто-бонусы:</b> +{salary_bonus}% к окладу, -{cooldown_red} мин к смене"
        )

    # Real estate summary
    real_estate_owned = [p for p in owned if p.category == "REAL_ESTATE"]
    if real_estate_owned:
        total_daily_rent = Decimal("0.00")
        for p in real_estate_owned:
            item = PropertyService.get_property_item(p.item_id)
            if item:
                total_daily_rent += item.daily_rent

        rent_str = f"{total_daily_rent:,.2f} ₽".replace(",", " ")
        lines.append(f"🏢 <b>Пассивный доход:</b> +{rent_str}/сутки")
        if rent_ready_sec <= 0:
            lines.append("   ⏱ Статус аренды: 🔔 <b>Аренда готова к сбору!</b>")
        else:
            lines.append(f"   ⏱ До сбора аренды: ⏳ ~{_format_time_remaining(rent_ready_sec)}")
        lines.append("")

    lines.extend([
        "<b>Каталог доступных категорий:</b>",
        "• 🚗 <b>Автопарк</b> — ускоряют смены и дают прибавку к зарплате",
        "• 🏢 <b>Недвижимость</b> — приносит пассивную аренду каждые 24 часа",
        "• 💎 <b>Коллекции</b> — статус, респект и подтверждение высокого достатка",
        "\n<i>Выберите категорию в меню ниже, чтобы приобрести активы.</i>",
    ])

    return "\n".join(lines)


def format_property_catalog(
    category: str,
    items: list[PropertyItem],
    owned_ids: Iterable[str] | None = None,
) -> str:
    """Format category catalog listing for Telegram HTML."""
    owned_set = set(owned_ids or [])
    cat_names = {
        "CAR": ("🚗", "Автопарк Т-Банка", "Автомобили дают постоянный процентный бонус к зарплате и сокращают длительность смены."),
        "REAL_ESTATE": ("🏢", "Недвижимость и Аренда", "Недвижимость генерирует ежедневный пассивный доход. Арендная плата накапливается каждые 24 часа."),
        "LUXURY": ("💎", "Коллекции и Роскошь", "Эксклюзивные предметы роскоши для настоящих ценителей комфорта и элитного статуса."),
    }
    icon, title, desc = cat_names.get(category.upper(), ("🏛", "Каталог имущества", ""))

    lines = [
        f"{icon} <b>{html.escape(title, quote=False)}</b>",
        "━━━━━━━━━━━━━━━━━━━━━━━━",
        f"<i>{html.escape(desc, quote=False)}</i>\n",
    ]

    for idx, item in enumerate(items, 1):
        price_str = f"{item.price:,.2f} ₽".replace(",", " ")
        is_owned = item.item_id in owned_set
        status_tag = " [✅ <b>Куплено</b>]" if is_owned else ""

        lines.append(f"<b>{idx}. {item.icon} {html.escape(item.name, quote=False)}</b>{status_tag}")
        lines.append(f"   💵 Цена: <b>{price_str}</b>")
        lines.append(f"   📝 <i>{html.escape(item.description, quote=False)}</i>")

        if item.category == "CAR":
            buff_parts = []
            if item.salary_bonus_pct > 0:
                buff_parts.append(f"+{item.salary_bonus_pct}% к зарплате")
            if item.cooldown_reduction_minutes > 0:
                buff_parts.append(f"-{item.cooldown_reduction_minutes} мин ко времени смены")
            buff_str = ", ".join(buff_parts) if buff_parts else "Без эффектов"
            lines.append(f"   ⚡️ Эффект: <b>{buff_str}</b>")
        elif item.category == "REAL_ESTATE":
            rent_str = f"{item.daily_rent:,.2f} ₽".replace(",", " ")
            lines.append(f"   💰 Доход: <b>+{rent_str} / сутки</b>")

        lines.append("")

    return "\n".join(lines)


def format_my_properties(
    user: User,
    owned: list[UserProperty],
    car_buffs: tuple[int, int] = (0, 0),
) -> str:
    """Format user's owned assets summary for Telegram HTML."""
    user_display = html.escape(user.full_name or user.username or f"Клиент #{user.id}", quote=False)
    lines = [
        "🎒 <b>Мое имущество & Активы</b>",
        "━━━━━━━━━━━━━━━━━━━━━━━━",
        f"👤 Владелец: <b>{user_display}</b>\n",
    ]

    if not owned:
        lines.append(
            "<i>У вас пока нет приобретенного имущества.\n"
            "Загляните в каталог «Автопарк», «Недвижимость» или «Коллекции», "
            "чтобы инвестировать заработанные средства!</i>"
        )
        return "\n".join(lines)

    total_spent = sum((p.purchase_price for p in owned), Decimal("0.00"))
    total_spent_str = f"{total_spent:,.2f} ₽".replace(",", " ")

    cars = [p for p in owned if p.category == "CAR"]
    real_estate = [p for p in owned if p.category == "REAL_ESTATE"]
    luxury = [p for p in owned if p.category == "LUXURY"]

    if cars:
        lines.append("🚗 <b>Гараж:</b>")
        for p in cars:
            item = PropertyService.get_property_item(p.item_id)
            name = item.name if item else p.item_id
            icon = item.icon if item else "🚗"
            lines.append(f"• {icon} {html.escape(name, quote=False)}")
        salary_bonus, cooldown_red = car_buffs
        lines.append(f"   <i>Действующие баффы: +{salary_bonus}% зарплаты, -{cooldown_red} мин смены</i>\n")

    if real_estate:
        lines.append("🏢 <b>Недвижимость:</b>")
        daily_total = Decimal("0.00")
        for p in real_estate:
            item = PropertyService.get_property_item(p.item_id)
            name = item.name if item else p.item_id
            icon = item.icon if item else "🏢"
            daily = item.daily_rent if item else Decimal("0.00")
            daily_total += daily
            daily_str = f"{daily:,.0f} ₽".replace(",", " ")
            lines.append(f"• {icon} {html.escape(name, quote=False)} (доход: +{daily_str}/сут)")
        daily_total_str = f"{daily_total:,.2f} ₽".replace(",", " ")
        lines.append(f"   <i>Суммарный пассивный доход: +{daily_total_str} в день</i>\n")

    if luxury:
        lines.append("💎 <b>Коллекции и Роскошь:</b>")
        for p in luxury:
            item = PropertyService.get_property_item(p.item_id)
            name = item.name if item else p.item_id
            icon = item.icon if item else "💎"
            lines.append(f"• {icon} {html.escape(name, quote=False)}")
        lines.append("")

    lines.append(f"💼 Суммарные инвестиции в имущество: <b>{total_spent_str}</b>")
    return "\n".join(lines)


def format_property_item_detail(item: PropertyItem, is_owned: bool = False) -> str:
    """Format individual property item detail view."""
    price_str = f"{item.price:,.2f} ₽".replace(",", " ")
    status_tag = "\n✅ <b>Вы уже владеете этим предметом!</b>" if is_owned else ""

    lines = [
        f"{item.icon} <b>{html.escape(item.name, quote=False)}</b>{status_tag}",
        "━━━━━━━━━━━━━━━━━━━━━━━━",
        f"💵 Стоимость: <b>{price_str}</b>",
        f"📝 <i>{html.escape(item.description, quote=False)}</i>\n",
    ]

    if item.category == "CAR":
        lines.append(f"⚡️ Бонус к зарплате: <b>+{item.salary_bonus_pct}%</b>")
        lines.append(f"⏱ Сокращение смены: <b>-{item.cooldown_reduction_minutes} мин</b>")
    elif item.category == "REAL_ESTATE":
        rent_str = f"{item.daily_rent:,.2f} ₽".replace(",", " ")
        lines.append(f"💰 Пассивный доход: <b>+{rent_str} / сутки</b>")
    elif item.category == "LUXURY":
        lines.append("💎 Статус: <b>Элитный коллекционный актив</b>")

    return "\n".join(lines)
