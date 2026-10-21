"""18% APR Savings Safe Router: Overview, Daily Accrual Display, Deposit, Withdraw, and Payout."""

from decimal import Decimal
from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from app.bot.keyboards.inline import get_savings_keyboard
from app.bot.keyboards.reply import get_cancel_reply_keyboard, get_main_menu_keyboard
from app.bot.states.states import SavingsStates
from app.database.models import User
from app.services.banking import BankingService
from app.services.savings import SavingsService
from app.services.transfers import AccountFrozenError, InsufficientFundsError

savings_router = Router(name="savings_router")


def format_savings_text(savings) -> str:
    """Format savings account overview with balance, APR, and daily yield."""
    pending = SavingsService.calculate_accrued_interest(savings)
    total_accrued = savings.accrued_interest + pending
    daily_yield = (savings.balance * Decimal("0.18") / Decimal("365")).quantize(Decimal("0.01"))

    return (
        "💰 <b>Накопительный счет «Сейф» (18% годовых)</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        f"Баланс сейфа:         <b>{savings.balance:.2f} ₽</b>\n"
        f"Накопленные проценты: <b>{total_accrued:.2f} ₽</b>\n"
        f"Ежедневный доход:     <b>~{daily_yield:.2f} ₽ / день</b>\n"
        f"Ставка:               <b>18.00% годовых</b>\n"
        "━━━━━━━━━━━━━━━━━━━━━━━━\n"
        "<i>Проценты рассчитываются каждый день по формуле Actual/365. "
        "Вы можете в любой момент пополнить счет, снять средства или забрать начисленные проценты!</i>"
    )


@savings_router.message(Command("savings"))
@savings_router.message(F.text == "💰 Сейф 18%")
async def handle_savings_menu(
    message: Message, session: AsyncSession, user: User, state: FSMContext
) -> None:
    """Display 18% APR savings safe overview with interactive inline buttons."""
    await state.clear()
    savings = await SavingsService.get_or_create_savings(session, user.id)
    text = format_savings_text(savings)
    await message.answer(text=text, reply_markup=get_savings_keyboard())


@savings_router.callback_query(F.data == "savings:refresh")
async def handle_savings_refresh(
    query: CallbackQuery, session: AsyncSession, user: User
) -> None:
    """Refresh the savings account overview with current interest calculations."""
    savings = await SavingsService.get_or_create_savings(session, user.id)
    text = format_savings_text(savings)
    try:
        await query.message.edit_text(text=text, reply_markup=get_savings_keyboard())
    except Exception:
        pass
    await query.answer("Данные обновлены")


@savings_router.callback_query(F.data == "savings:deposit")
async def handle_savings_deposit_prompt(
    query: CallbackQuery, session: AsyncSession, user: User, state: FSMContext
) -> None:
    """Prompt user for deposit amount into savings safe."""
    main_acc = await BankingService.get_user_main_account(session, user.id)
    if not main_acc or main_acc.balance < Decimal("1.00"):
        cur_bal = main_acc.balance if main_acc else Decimal("0.00")
        await query.answer("Недостаточно средств на карте.", show_alert=True)
        await query.message.answer(
            f"❌ Недостаточно средств на карте Т-Банк Black. Доступно: <b>{cur_bal:.2f} ₽</b>.",
            reply_markup=get_main_menu_keyboard(),
        )
        return

    await state.set_state(SavingsStates.waiting_for_deposit_amount)
    await query.message.answer(
        f"📥 <b>Пополнение сейфа под 18% годовых</b>\n\n"
        f"Доступно на карте: <b>{main_acc.balance:.2f} ₽</b>\n"
        "Введите сумму для перевода в сейф (от 1.00 ₽):",
        reply_markup=get_cancel_reply_keyboard(),
    )
    await query.answer()


@savings_router.message(SavingsStates.waiting_for_deposit_amount)
async def process_savings_deposit(
    message: Message, session: AsyncSession, user: User, state: FSMContext
) -> None:
    """Execute deposit from Main card account to Savings safe."""
    text = (message.text or "").strip()
    if text in ["❌ Отмена", "/cancel"]:
        await state.clear()
        await message.answer("Пополнение сейфа отменено.", reply_markup=get_main_menu_keyboard())
        return

    try:
        clean_amt = text.replace(",", ".").replace("₽", "").replace(" ", "").strip()
        amount = Decimal(clean_amt).quantize(Decimal("0.01"))
        if amount <= Decimal("0.00"):
            await message.answer("Сумма должна быть больше 0. Введите сумму:")
            return
    except Exception:
        await message.answer("Некорректная сумма. Введите число (например, 1000):")
        return

    try:
        await SavingsService.deposit_to_savings(session, user.id, amount)
        await session.commit()
    except InsufficientFundsError as e:
        await message.answer(f"❌ {e}")
        return
    except AccountFrozenError as e:
        await message.answer(f"❌ {e}", reply_markup=get_main_menu_keyboard())
        await state.clear()
        return

    savings = await SavingsService.get_or_create_savings(session, user.id)
    await state.clear()
    await message.answer(
        f"✅ <b>Сейф успешно пополнен на {amount:.2f} ₽!</b>\n"
        f"Новый баланс сейфа: <b>{savings.balance:.2f} ₽</b>.",
        reply_markup=get_main_menu_keyboard(),
    )


@savings_router.callback_query(F.data == "savings:withdraw")
async def handle_savings_withdraw_prompt(
    query: CallbackQuery, session: AsyncSession, user: User, state: FSMContext
) -> None:
    """Prompt user for withdrawal amount from savings safe back to main card."""
    savings = await SavingsService.get_or_create_savings(session, user.id)
    if savings.balance < Decimal("1.00"):
        await query.answer("Сейф пуст.", show_alert=True)
        await query.message.answer(
            f"❌ На накопительном счете нет средств для вывода. Баланс: <b>{savings.balance:.2f} ₽</b>.",
            reply_markup=get_main_menu_keyboard(),
        )
        return

    await state.set_state(SavingsStates.waiting_for_withdraw_amount)
    await query.message.answer(
        f"📤 <b>Вывод средств из сейфа на карту</b>\n\n"
        f"Баланс в сейфе: <b>{savings.balance:.2f} ₽</b>\n"
        "Введите сумму для перевода на карту:",
        reply_markup=get_cancel_reply_keyboard(),
    )
    await query.answer()


@savings_router.message(SavingsStates.waiting_for_withdraw_amount)
async def process_savings_withdraw(
    message: Message, session: AsyncSession, user: User, state: FSMContext
) -> None:
    """Execute withdrawal from Savings safe to Main card account."""
    text = (message.text or "").strip()
    if text in ["❌ Отмена", "/cancel"]:
        await state.clear()
        await message.answer("Вывод средств отменен.", reply_markup=get_main_menu_keyboard())
        return

    try:
        clean_amt = text.replace(",", ".").replace("₽", "").replace(" ", "").strip()
        amount = Decimal(clean_amt).quantize(Decimal("0.01"))
        if amount <= Decimal("0.00"):
            await message.answer("Сумма должна быть больше 0. Введите сумму:")
            return
    except Exception:
        await message.answer("Некорректная сумма. Введите число (например, 500):")
        return

    try:
        await SavingsService.withdraw_from_savings(session, user.id, amount)
        await session.commit()
    except InsufficientFundsError as e:
        await message.answer(f"❌ {e}")
        return

    savings = await SavingsService.get_or_create_savings(session, user.id)
    main_acc = await BankingService.get_user_main_account(session, user.id)
    await state.clear()
    await message.answer(
        f"✅ <b>Успешно выведено {amount:.2f} ₽ на карту Т-Банк Black!</b>\n"
        f"Баланс сейфа: <b>{savings.balance:.2f} ₽</b>\n"
        f"Баланс карты: <b>{main_acc.balance:.2f} ₽</b>.",
        reply_markup=get_main_menu_keyboard(),
    )


@savings_router.callback_query(F.data == "savings:payout")
async def handle_savings_payout(
    query: CallbackQuery, session: AsyncSession, user: User
) -> None:
    """Execute 1-click payout of accumulated interest to savings balance."""
    payout = await SavingsService.payout_interest(session, user.id)
    await session.commit()

    if payout > Decimal("0.00"):
        await query.answer(f"🎉 Выплачено {payout:.2f} ₽ процентов!", show_alert=True)
    else:
        await query.answer("Проценты еще не накопились (минимум 0.01 ₽).", show_alert=True)

    savings = await SavingsService.get_or_create_savings(session, user.id)
    text = format_savings_text(savings)
    try:
        await query.message.edit_text(text=text, reply_markup=get_savings_keyboard())
    except Exception:
        pass
