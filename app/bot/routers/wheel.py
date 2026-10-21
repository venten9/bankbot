"""T-Bank Daily Fortune Wheel Router for Telegram UI."""

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.formatters.wheel import (
    format_wheel_dashboard,
    format_wheel_spin_result,
)
from app.bot.keyboards.wheel import (
    get_wheel_keyboard,
    get_wheel_result_keyboard,
)
from app.database.models import Account, User
from app.services.transfers import InsufficientFundsError
from app.services.wheel import WheelService

wheel_router = Router(name="wheel")


@wheel_router.message(Command("wheel"))
@wheel_router.message(F.text == "🎰 Колесо Фортуны")
async def handle_wheel_menu(
    message: Message,
    session: AsyncSession,
    user: User,
    main_account: Account | None = None,
    state: FSMContext | None = None,
) -> None:
    """Display Fortune Wheel dashboard with cooldown status and available prizes."""
    if state is not None:
        await state.clear()

    can_spin, rem_sec = await WheelService.can_spin_free(user)
    text = format_wheel_dashboard(user=user, can_spin=can_spin, seconds_remaining=rem_sec)
    keyboard = get_wheel_keyboard(can_free_spin=can_spin, remaining_seconds=rem_sec)
    await message.answer(text=text, reply_markup=keyboard)


@wheel_router.callback_query(F.data == "wheel:menu")
async def handle_wheel_menu_callback(
    query: CallbackQuery,
    session: AsyncSession,
    user: User,
    main_account: Account | None = None,
) -> None:
    """Refresh Fortune Wheel dashboard in-place."""
    can_spin, rem_sec = await WheelService.can_spin_free(user)
    text = format_wheel_dashboard(user=user, can_spin=can_spin, seconds_remaining=rem_sec)
    keyboard = get_wheel_keyboard(can_free_spin=can_spin, remaining_seconds=rem_sec)

    if query.message:
        try:
            await query.message.edit_text(text=text, reply_markup=keyboard)
        except Exception:
            pass
    await query.answer("Колесо Фортуны обновлено!")


@wheel_router.callback_query(F.data == "wheel:spin")
async def handle_wheel_spin(
    query: CallbackQuery,
    session: AsyncSession,
    user: User,
    main_account: Account | None = None,
) -> None:
    """Spin the Fortune Wheel using the daily 24h free spin."""
    try:
        result = await WheelService.spin_wheel(session, user.id, is_extra=False)
        text = format_wheel_spin_result(result, is_extra=False)
        can_spin_again, _ = await WheelService.can_spin_free(user)
        keyboard = get_wheel_result_keyboard(can_free_spin=can_spin_again)

        if query.message:
            try:
                await query.message.edit_text(text=text, reply_markup=keyboard)
            except Exception:
                pass
        await query.answer(f"🎉 Выигрыш: {result.title}!")
    except ValueError as exc:
        await query.answer(f"⏳ {exc}", show_alert=True)
    except Exception as exc:
        await query.answer(f"❌ Ошибка вращения: {exc}", show_alert=True)


@wheel_router.callback_query(F.data == "wheel:spin_extra")
async def handle_wheel_spin_extra(
    query: CallbackQuery,
    session: AsyncSession,
    user: User,
    main_account: Account | None = None,
) -> None:
    """Spin the Fortune Wheel using 1 000 bonus cashback rubles."""
    try:
        result = await WheelService.spin_wheel(session, user.id, is_extra=True)
        text = format_wheel_spin_result(result, is_extra=True)
        can_spin_free, _ = await WheelService.can_spin_free(user)
        keyboard = get_wheel_result_keyboard(can_free_spin=can_spin_free)

        if query.message:
            try:
                await query.message.edit_text(text=text, reply_markup=keyboard)
            except Exception:
                pass
        await query.answer(f"⚡️ Приз: {result.title}!")
    except InsufficientFundsError as exc:
        await query.answer(f"❌ {exc}", show_alert=True)
    except Exception as exc:
        await query.answer(f"❌ Ошибка покупки спина: {exc}", show_alert=True)
