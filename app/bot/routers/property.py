"""T-Property & Luxury Router for Telegram UI."""

import datetime
from decimal import Decimal

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.formatters.property import (
    format_my_properties,
    format_property_catalog,
    format_property_overview,
)
from app.bot.keyboards.property import (
    get_my_properties_keyboard,
    get_property_category_keyboard,
    get_property_menu_keyboard,
)
from app.database.models import Account, User
from app.services.property import PropertyService
from app.services.transfers import AccountFrozenError, InsufficientFundsError

property_router = Router(name="property")


async def _calculate_rent_ready_seconds(
    owned: list,
    now: datetime.datetime | None = None,
) -> int:
    """Helper to calculate remaining seconds until next rental collection is available."""
    real_estate = [p for p in owned if p.category == "REAL_ESTATE"]
    if not real_estate:
        return 0

    current_time = now or datetime.datetime.now()
    min_rem: int | None = None

    for p in real_estate:
        base_t = p.last_rent_collected_at or p.purchased_at
        elapsed = (current_time - base_t).total_seconds()
        if elapsed < PropertyService.RENT_COOLDOWN_SECONDS and p.last_rent_collected_at is not None:
            rem = max(1, int(PropertyService.RENT_COOLDOWN_SECONDS - elapsed))
            if min_rem is None or rem < min_rem:
                min_rem = rem
        else:
            return 0  # At least one property has ready rent

    return min_rem if min_rem is not None else 0


@property_router.message(Command("property"))
@property_router.message(F.text == "🏛 Имущество")
async def handle_property_menu(
    message: Message,
    session: AsyncSession,
    user: User,
    main_account: Account | None = None,
    state: FSMContext | None = None,
) -> None:
    """Display user's property dashboard with asset overview and category navigation."""
    if state is not None:
        await state.clear()

    owned = await PropertyService.get_user_properties(session, user.id)
    car_buffs = await PropertyService.get_user_car_buffs(session, user.id)
    rent_ready_sec = await _calculate_rent_ready_seconds(owned)

    text = format_property_overview(
        user=user,
        owned=owned,
        rent_ready_sec=rent_ready_sec,
        car_buffs=car_buffs,
    )
    has_re = any(p.category == "REAL_ESTATE" for p in owned)
    keyboard = get_property_menu_keyboard(has_real_estate=has_re)
    await message.answer(text=text, reply_markup=keyboard)


@property_router.callback_query(F.data == "prop:menu")
async def handle_property_menu_callback(
    query: CallbackQuery,
    session: AsyncSession,
    user: User,
    main_account: Account | None = None,
) -> None:
    """Refresh or return to property dashboard in-place."""
    owned = await PropertyService.get_user_properties(session, user.id)
    car_buffs = await PropertyService.get_user_car_buffs(session, user.id)
    rent_ready_sec = await _calculate_rent_ready_seconds(owned)

    text = format_property_overview(
        user=user,
        owned=owned,
        rent_ready_sec=rent_ready_sec,
        car_buffs=car_buffs,
    )
    has_re = any(p.category == "REAL_ESTATE" for p in owned)
    keyboard = get_property_menu_keyboard(has_real_estate=has_re)

    if query.message:
        try:
            await query.message.edit_text(text=text, reply_markup=keyboard)
        except Exception:
            pass
    await query.answer("Имущество обновлено!")


@property_router.callback_query(F.data.startswith("prop:cat:"))
async def handle_property_category(
    query: CallbackQuery,
    session: AsyncSession,
    user: User,
    main_account: Account | None = None,
) -> None:
    """Show catalog of properties for a specific category (CAR, REAL_ESTATE, LUXURY)."""
    if not query.data:
        await query.answer()
        return

    category = query.data.split(":", 2)[2].upper()
    items = PropertyService.get_property_catalog(category)
    owned = await PropertyService.get_user_properties(session, user.id)
    owned_ids = {p.item_id for p in owned}

    text = format_property_catalog(category, items, owned_ids)
    keyboard = get_property_category_keyboard(category, items, owned_ids)

    if query.message:
        try:
            await query.message.edit_text(text=text, reply_markup=keyboard)
        except Exception:
            pass
    await query.answer()


@property_router.callback_query(F.data.startswith("prop:buy:"))
async def handle_property_buy(
    query: CallbackQuery,
    session: AsyncSession,
    user: User,
    main_account: Account | None = None,
) -> None:
    """Purchase a property item with atomic balance debit and duplicate prevention."""
    if not query.data:
        await query.answer()
        return

    item_id = query.data.split(":", 2)[2]
    try:
        prop = await PropertyService.buy_property(session, user.id, item_id)
        item = PropertyService.get_property_item(item_id)
        name = item.name if item else item_id
        await query.answer(f"🎉 Поздравляем с покупкой «{name}»!", show_alert=True)

        # Refresh the current category view after successful purchase
        if item:
            items = PropertyService.get_property_catalog(item.category)
            owned = await PropertyService.get_user_properties(session, user.id)
            owned_ids = {p.item_id for p in owned}
            text = format_property_catalog(item.category, items, owned_ids)
            keyboard = get_property_category_keyboard(item.category, items, owned_ids)
            if query.message:
                try:
                    await query.message.edit_text(text=text, reply_markup=keyboard)
                except Exception:
                    pass
    except InsufficientFundsError as exc:
        await query.answer(f"❌ {exc}", show_alert=True)
    except AccountFrozenError as exc:
        await query.answer(f"❄️ {exc}", show_alert=True)
    except ValueError as exc:
        await query.answer(f"⚠️ {exc}", show_alert=True)
    except Exception as exc:
        await query.answer(f"❌ Ошибка покупки: {exc}", show_alert=True)


@property_router.callback_query(F.data == "prop:rent:claim")
async def handle_property_rent_claim(
    query: CallbackQuery,
    session: AsyncSession,
    user: User,
    main_account: Account | None = None,
) -> None:
    """Collect daily rental income across all owned real estate."""
    try:
        total_rent, next_wait, msg = await PropertyService.collect_rental_income(session, user.id)
        if total_rent > Decimal("0.00"):
            await query.answer(f"💰 Собрано {total_rent:,.2f} ₽ аренды!", show_alert=True)
        else:
            await query.answer(msg, show_alert=True)

        # Refresh property overview
        owned = await PropertyService.get_user_properties(session, user.id)
        car_buffs = await PropertyService.get_user_car_buffs(session, user.id)
        text = format_property_overview(
            user=user,
            owned=owned,
            rent_ready_sec=next_wait,
            car_buffs=car_buffs,
        )
        has_re = any(p.category == "REAL_ESTATE" for p in owned)
        keyboard = get_property_menu_keyboard(has_real_estate=has_re)

        if query.message:
            try:
                await query.message.edit_text(text=text, reply_markup=keyboard)
            except Exception:
                pass
    except Exception as exc:
        await query.answer(f"❌ Ошибка сбора аренды: {exc}", show_alert=True)


@property_router.callback_query(F.data == "prop:my")
async def handle_property_my(
    query: CallbackQuery,
    session: AsyncSession,
    user: User,
    main_account: Account | None = None,
) -> None:
    """Display user's owned assets summary and active buffs."""
    owned = await PropertyService.get_user_properties(session, user.id)
    car_buffs = await PropertyService.get_user_car_buffs(session, user.id)
    text = format_my_properties(user=user, owned=owned, car_buffs=car_buffs)
    has_re = any(p.category == "REAL_ESTATE" for p in owned)
    keyboard = get_my_properties_keyboard(has_real_estate=has_re)

    if query.message:
        try:
            await query.message.edit_text(text=text, reply_markup=keyboard)
        except Exception:
            pass
    await query.answer()
