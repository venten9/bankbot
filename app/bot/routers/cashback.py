"""Monthly Cashback Program Router: 4-of-8 Category Picker and 1-Click Bonus Payout."""

import datetime
from decimal import Decimal
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards.inline import (
    get_cashback_menu_keyboard,
    get_cashback_selection_keyboard,
)
from app.bot.keyboards.reply import get_main_menu_keyboard
from app.bot.states.states import CashbackStates
from app.database.models import User
from app.services.cashback import CashbackService

cashback_router = Router(name="cashback_router")


@cashback_router.message(Command("cashback"))
@cashback_router.message(F.text == "🎁 Кэшбэк")
async def handle_cashback_menu(
    message: Message, session: AsyncSession, user: User, state: FSMContext
) -> None:
    """Display cashback bonus balance and active categories or selection prompt."""
    await state.clear()
    cur_month = datetime.datetime.now().strftime("%Y-%m")
    active_cats = await CashbackService.get_user_categories(session, user.id, cur_month)

    lines = [
        "🎁 <b>Программа Кэшбэка Т-Банка</b>",
        "━━━━━━━━━━━━━━━━━━━━━━━━",
        f"Накопленный кэшбэк: <b>{user.cashback_balance:.2f} ₽</b>",
        f"Период:              <b>{cur_month}</b>",
        "━━━━━━━━━━━━━━━━━━━━━━━━",
    ]

    if active_cats:
        lines.append("Ваши активные категории повышенного кэшбэка на этот месяц:")
        for cat in active_cats:
            lines.append(f"• {cat.name} — <b>{cat.percent}%</b>")
        lines.append("\nБонусы начисляются на счет автоматически при покупках.")
        await message.answer(
            text="\n".join(lines),
            reply_markup=get_cashback_menu_keyboard(has_selected=True),
        )
    else:
        lines.append(
            "Категории на текущий месяц еще не выбраны! "
            "Выберите <b>4 категории</b> из списка ниже:"
        )
        all_cats = await CashbackService.get_available_categories(session)
        await state.set_state(CashbackStates.selecting_categories)
        await state.update_data(selected_ids=[])
        await message.answer(
            text="\n".join(lines),
            reply_markup=get_cashback_selection_keyboard(all_cats, set()),
        )


@cashback_router.callback_query(F.data == "cb:select_prompt")
async def handle_select_prompt(
    query: CallbackQuery, session: AsyncSession, user: User, state: FSMContext
) -> None:
    """Trigger category selection view on inline button click."""
    all_cats = await CashbackService.get_available_categories(session)
    await state.set_state(CashbackStates.selecting_categories)
    await state.update_data(selected_ids=[])
    text = (
        "🎁 <b>Выбор категорий кэшбэка на текущий месяц</b>\n"
        "Отметьте ровно <b>4 категории</b> и нажмите «Подтвердить выбор»:"
    )
    await query.message.edit_text(text=text, reply_markup=get_cashback_selection_keyboard(all_cats, set()))
    await query.answer()


@cashback_router.callback_query(F.data.startswith("cb:toggle:"))
async def handle_toggle_category(
    query: CallbackQuery, session: AsyncSession, user: User, state: FSMContext
) -> None:
    """Toggle category selection checkbox (limit 4)."""
    cat_id = int(query.data.split(":")[-1])
    data = await state.get_data()
    selected_ids = list(data.get("selected_ids", []))

    if cat_id in selected_ids:
        selected_ids.remove(cat_id)
    else:
        if len(selected_ids) >= 4:
            await query.answer("Можно выбрать максимум 4 категории!", show_alert=True)
            return
        selected_ids.append(cat_id)

    await state.update_data(selected_ids=selected_ids)
    all_cats = await CashbackService.get_available_categories(session)

    try:
        await query.message.edit_reply_markup(
            reply_markup=get_cashback_selection_keyboard(all_cats, set(selected_ids))
        )
    except Exception:
        pass
    await query.answer()


@cashback_router.callback_query(F.data == "cb:confirm")
async def handle_confirm_categories(
    query: CallbackQuery, session: AsyncSession, user: User, state: FSMContext
) -> None:
    """Lock in the 4 chosen categories for the month."""
    data = await state.get_data()
    selected_ids = list(data.get("selected_ids", []))

    if len(selected_ids) != 4:
        await query.answer(
            f"Необходимо выбрать ровно 4 категории (сейчас выбрано: {len(selected_ids)}).",
            show_alert=True,
        )
        return

    cur_month = datetime.datetime.now().strftime("%Y-%m")
    try:
        await CashbackService.select_categories(
            session=session,
            user_id=user.id,
            category_ids=selected_ids,
            month_year=cur_month,
        )
        await session.commit()
    except ValueError as e:
        await query.answer(str(e), show_alert=True)
        await state.clear()
        return

    await state.clear()
    active_cats = await CashbackService.get_user_categories(session, user.id, cur_month)
    lines = [
        "✨ <b>Категории кэшбэка успешно зафиксированы на этот месяц!</b>",
        "━━━━━━━━━━━━━━━━━━━━━━━━",
    ]
    for cat in active_cats:
        lines.append(f"• {cat.name} — <b>{cat.percent}%</b>")
    lines.append("\nПриятных покупок с повышенным кэшбэком от Т-Банка!")

    await query.message.edit_text(text="\n".join(lines))
    await query.message.answer("Главное меню:", reply_markup=get_main_menu_keyboard())
    await query.answer("Категории зафиксированы!")


@cashback_router.callback_query(F.data == "cb:payout")
async def handle_cashback_payout(
    query: CallbackQuery, session: AsyncSession, user: User
) -> None:
    """Execute 1-click payout of accumulated bonus rubles to main debit card."""
    if user.cashback_balance < Decimal("1.00"):
        await query.answer(
            "У вас пока нет накопленного кэшбэка для вывода (минимум 1.00 ₽).",
            show_alert=True,
        )
        return

    try:
        bonus_amount = await CashbackService.payout_cashback(session, user.id)
        await session.commit()
    except Exception as e:
        await query.answer(str(e), show_alert=True)
        return

    await query.message.answer(
        f"💛 <b>Кэшбэк {bonus_amount:.2f} ₽ успешно зачислен на карту Т-Банк Black!</b>\n"
        "Тратьте с удовольствием.",
        reply_markup=get_main_menu_keyboard(),
    )
    await query.answer(f"Зачислено {bonus_amount:.2f} ₽!", show_alert=True)
