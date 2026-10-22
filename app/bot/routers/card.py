"""Virtual Debit Card Router: Card View, Details Toggle, and Freeze/Unfreeze."""

import html
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.formatters.cards import format_card_view
from app.bot.keyboards.inline import get_card_cancel_keyboard, get_card_keyboard
from app.bot.states.states import CardState
from app.database.models import User
from app.services.banking import BankingService
from app.services.card import CardService


card_router = Router(name="card_router")


@card_router.message(Command("card"))
@card_router.message(F.text == "💳 Моя карта")
async def handle_card_menu(
    message: Message, session: AsyncSession, user: User
) -> None:
    """Display user's T-Bank Black card with masked details and action buttons."""
    overview = await BankingService.get_user_overview(session, user.id)
    card_data = overview["card"]
    if not card_data:
        await message.answer("У вас еще нет активной карты.")
        return

    text = format_card_view(
        card_data=card_data,
        unmasked=False,
        balance=overview["main_balance"],
    )
    keyboard = get_card_keyboard(
        is_frozen=card_data["is_frozen"],
        details_visible=False,
    )
    await message.answer(text=text, reply_markup=keyboard)


@card_router.callback_query(F.data == "card:details:toggle")
async def handle_toggle_details(
    query: CallbackQuery, session: AsyncSession, user: User
) -> None:
    """Toggle between masked and unmasked card credentials (PAN and CVV)."""
    overview = await BankingService.get_user_overview(session, user.id)
    card_data = overview["card"]
    if not card_data:
        await query.answer("Карта не найдена.", show_alert=True)
        return

    # Determine whether current message has masked CVC
    current_text = query.message.text or ""
    is_currently_masked = "CVC: •••" in current_text
    new_unmasked = is_currently_masked  # toggle

    new_text = format_card_view(
        card_data=card_data,
        unmasked=new_unmasked,
        balance=overview["main_balance"],
    )
    new_keyboard = get_card_keyboard(
        is_frozen=card_data["is_frozen"],
        details_visible=new_unmasked,
    )

    await query.message.edit_text(text=new_text, reply_markup=new_keyboard)
    notice = "Реквизиты показаны." if new_unmasked else "Реквизиты скрыты."
    await query.answer(notice)


@card_router.callback_query(F.data == "card:freeze:toggle")
async def handle_toggle_freeze(
    query: CallbackQuery, session: AsyncSession, user: User
) -> None:
    """Toggle card freeze / unfreeze status with immediate database persistence."""
    overview = await BankingService.get_user_overview(session, user.id)
    card_data = overview["card"]
    if not card_data:
        await query.answer("Карта не найдена.", show_alert=True)
        return

    currently_frozen = card_data["is_frozen"]
    if currently_frozen:
        await CardService.unfreeze_card(session, card_id=card_data["id"], user_id=user.id)
        alert_msg = "🔥 Карта успешно разморожена! Операции доступны."
    else:
        await CardService.freeze_card(session, card_id=card_data["id"], user_id=user.id)
        alert_msg = "❄️ Карта заморожена. Расходные операции заблокированы."

    await session.commit()

    # Re-fetch updated overview
    updated_overview = await BankingService.get_user_overview(session, user.id)
    current_text = query.message.text or ""
    details_visible = "CVC: •••" not in current_text

    new_text = format_card_view(
        card_data=updated_overview["card"],
        unmasked=details_visible,
        balance=updated_overview["main_balance"],
    )
    new_keyboard = get_card_keyboard(
        is_frozen=updated_overview["card"]["is_frozen"],
        details_visible=details_visible,
    )

    await query.message.edit_text(text=new_text, reply_markup=new_keyboard)
    await query.answer(alert_msg, show_alert=True)


@card_router.callback_query(F.data == "card:change_number")
async def handle_change_number_prompt(
    query: CallbackQuery, state: FSMContext, session: AsyncSession, user: User
) -> None:
    """Prompt user to input custom 16-digit card number."""
    overview = await BankingService.get_user_overview(session, user.id)
    if not overview.get("card"):
        await query.answer("У вас еще нет активной карты.", show_alert=True)
        return

    await state.set_state(CardState.waiting_for_custom_number)
    prompt_text = (
        "Введите желаемый 16-значный номер карты (например: <code>2200 7077 7777 7777</code>):"
    )
    if query.message:
        try:
            await query.message.edit_text(text=prompt_text, reply_markup=get_card_cancel_keyboard())
        except Exception:
            await query.message.answer(text=prompt_text, reply_markup=get_card_cancel_keyboard())
    await query.answer()


@card_router.callback_query(F.data == "card:change:cancel")
async def handle_change_number_cancel(
    query: CallbackQuery, state: FSMContext, session: AsyncSession, user: User
) -> None:
    """Cancel card number customization and restore card view widget."""
    await state.clear()
    overview = await BankingService.get_user_overview(session, user.id)
    card_data = overview.get("card")
    if not card_data:
        await query.answer("Карта не найдена.", show_alert=True)
        return

    text = format_card_view(
        card_data=card_data,
        unmasked=False,
        balance=overview["main_balance"],
    )
    keyboard = get_card_keyboard(
        is_frozen=card_data["is_frozen"],
        details_visible=False,
    )
    if query.message:
        try:
            await query.message.edit_text(text=text, reply_markup=keyboard)
        except Exception:
            await query.message.answer(text=text, reply_markup=keyboard)
    await query.answer("Изменение номера карты отменено.")


@card_router.message(CardState.waiting_for_custom_number)
async def process_custom_card_number(
    message: Message, state: FSMContext, session: AsyncSession, user: User
) -> None:
    """Handle custom 16-digit card number submission and validation."""
    text_val = (message.text or "").strip()
    if text_val in ("/cancel", "❌ Отмена", "Отмена"):
        await state.clear()
        overview = await BankingService.get_user_overview(session, user.id)
        card_data = overview.get("card")
        if card_data:
            text = format_card_view(
                card_data=card_data,
                unmasked=False,
                balance=overview["main_balance"],
            )
            keyboard = get_card_keyboard(
                is_frozen=card_data["is_frozen"],
                details_visible=False,
            )
            await message.answer(text=text, reply_markup=keyboard)
        else:
            await message.answer("❌ Изменение номера карты отменено.")
        return

    try:
        await CardService.change_card_number(session, user.id, text_val)
        await session.commit()
    except ValueError as e:
        escaped_err = html.escape(str(e), quote=False)
        await message.answer(
            f"❌ Ошибка: {escaped_err}\n\nПопробуйте ещё раз или нажмите «❌ Отмена»:",
            reply_markup=get_card_cancel_keyboard(),
        )
        return

    await state.clear()
    overview = await BankingService.get_user_overview(session, user.id)
    card_data = overview.get("card")
    card_text = format_card_view(
        card_data=card_data,
        unmasked=False,
        balance=overview["main_balance"],
    )
    success_text = f"✅ <b>Номер карты успешно изменен!</b>\n\n{card_text}"
    keyboard = get_card_keyboard(
        is_frozen=card_data["is_frozen"],
        details_visible=False,
    )
    await message.answer(text=success_text, reply_markup=keyboard)

